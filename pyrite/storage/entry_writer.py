"""The one function that publishes an entry file's bytes (ADR-0042 decisions 10, 12; #955).

Every write of an existing entry file ends here: ``KBService._update`` computes
the new text with ``file_operations.apply`` and publishes it with
``publish_entry_text``, which compares the file with the bytes the change was
computed on and replaces it under the momentary lock
(``atomic_write_text(expect=...)``, the lock in the repository's git dir,
§10a A1).

**A KB outside git (D1 (b), maintainer 2026-10-15).** There is no git dir to
lock in, so the file is written with compare-before-replace and no lock: the
bytes are compared with the base once, here, then replaced. Two processes
can both pass that compare and the last one wins; the result says
``locked: false`` so a caller knows. Nothing else changes: ``pyrite init``
does not run ``git init`` and ``index health`` does not warn.

``tests/test_entry_file_writers_are_one_function.py`` pins the ratchet: no
other module in ``pyrite/`` or an extension writes an entry file's bytes,
except the writers it lists, and that list only shrinks. Since #955 P4 the
model has no ``save`` and no emitter: a create's text is built by
``repository.new_entry_text`` (``apply`` onto a skeleton) and published by
``create_entry_text``; rename, ``schema migrate`` and the extensions'
migrations send operations and publish here. ``ids pin`` is the one writer
still outside: it keeps its own no-follow write
(``id_pin_service._rename``), pinned by that test's
``ENTRY_FILE_WRITERS_NOT_YET_CONVERTED``.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import secrets
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..models.base import _timestamp_instant
from ..utils.atomic_write import ABSENT, FileChanged, _fsync_directory, atomic_write_text
from ..utils.file_lock import LOCK_TIMEOUT, NotInGitRepository

__all__ = [
    "FileChanged",
    "create_entry_text",
    "move_entry_text",
    "publish_entry_text",
    "publish_exclusive",
    "raw_scalar",
    "updated_at_stamp",
]

logger = logging.getLogger(__name__)

#: The tail of a timestamp as YAML spells it: the spaces it allows before the
#: zone, then ``Z`` or an offset with a one- or two-digit hour and optional
#: minutes, ``+5``, ``+05``, ``+5:30``, ``+0530``, ``+05:30`` (#990; the cold
#: read of #991: a one-digit hour was stamped as naive UTC, and the space
#: before the zone was dropped). A stamp keeps every zone form the loader
#: accepts, spacing included.
_STAMP_TAIL = re.compile(r"(?P<space>[ \t]*)(?P<tz>Z|[+-]\d\d?(?::?\d\d)?)$")


def _zone_offset(tz: str) -> timezone:
    """The ``timezone`` an offset spells: ``+5`` and ``+05`` are five hours,
    ``+5:30``, ``+0530`` and ``+05:30`` five and a half."""
    sign = 1 if tz[0] == "+" else -1
    digits = tz[1:]
    if ":" in digits:
        hours_text, minutes_text = digits.split(":", 1)
    elif len(digits) > 2:
        hours_text, minutes_text = digits[:-2], digits[-2:]
    else:
        hours_text, minutes_text = digits, "0"
    return timezone(sign * timedelta(hours=int(hours_text), minutes=int(minutes_text)))


def raw_scalar(yaml_text: str, key: str) -> str | None:
    """The spelling of top-level ``key``'s scalar in ``yaml_text`` as written
    (a trailing comment stripped; a CRLF line read as its LF twin, #990), or
    None when the key is not a plain ``key: value`` line (quoted key, flow
    mapping, block scalar)."""
    found = re.search(
        rf"^{re.escape(key)}:[ \t]*([^\s#][^#\r\n]*?)[ \t]*(?:#[^\r\n]*)?\r?$", yaml_text, re.M
    )
    return found.group(1) if found else None


def updated_at_stamp(old: Any, raw: str | None = None) -> Any:
    """The new ``updated_at`` in the file's own form (#955, acceptance 4), or
    None when the file's value is not an instant Pyrite can read. The
    bookkeeping of ADR-0042 decision 7: a writer stamps the key only where
    the file carries it, never invents it.

    The stamp keeps the spelling of the value it replaces (#971 fix round
    1): the ``T`` or space separator, a ``Z``, an offset (the time is
    converted to it) or none, the fraction's digits, a date-only value.
    ``old`` is the loaded value (a ``str`` when the file quotes it, else
    ruamel's timestamp or a ``date``); ``raw`` is the scalar as the file
    spells it (``raw_scalar``), for a bare value. A quoted value stays a
    quoted string; a bare one is ``TimestampText``, which ``apply`` writes
    bare in that spelling. A zone is kept as spelled, with the spaces the
    file puts before it: ``Z``, ``+5``, ``+02``, ``+5:30``, ``+0530``,
    ``+05:30`` (#990, #991), the time converted to it.
    """
    from .file_operations import TimestampText

    if _timestamp_instant(old) is None:
        return None
    now = datetime.now(UTC).replace(microsecond=0)
    if isinstance(old, str):
        spelled = old
    elif raw:
        spelled = raw
    elif isinstance(old, datetime):
        # No source text: the loader's record of the spelling (ruamel keeps
        # the separator and the zone form on its TimeStamp).
        info = getattr(old, "_yaml", None) or {}
        tz = info.get("tz") or ""
        spelled = old.replace(tzinfo=None).isoformat(sep="T" if info.get("t") else " ") + tz
    else:
        spelled = old.isoformat()
    if ":" not in spelled:
        return now.date() if not isinstance(old, str) else now.strftime("%Y-%m-%d")
    sep = "T" if "T" in spelled else " "
    tail = _STAMP_TAIL.search(spelled)
    space, tz = (tail.group("space"), tail.group("tz")) if tail else ("", "")
    core = spelled[: tail.start()] if tail else spelled
    digits = len(core.rsplit(".", 1)[1]) if "." in core else 0
    shown = now if tz in ("", "Z") else now.astimezone(_zone_offset(tz))
    text = shown.strftime(f"%Y-%m-%d{sep}%H:%M:%S")
    if digits:
        text += "." + "0" * digits
    text += space + tz
    return text if isinstance(old, str) else TimestampText(text)


def create_entry_text(path: str | os.PathLike[str], text: str) -> bool:
    """Write ``text`` to a path that must not exist yet; return whether the
    write ran under the lock. Raises ``FileExistsError`` when something is
    already there. Used by a create and by the templated move, the one move
    an update makes (ADR-0042 decisions 5 and 13).

    In a git work tree the absence check and the publish run under the
    momentary lock (``expect=ABSENT``). Outside one there is no lock (D1 (b)),
    so the exclusivity comes from the filesystem call that publishes
    (``publish_exclusive``: ``os.link``, else ``O_CREAT | O_EXCL``), never
    from an ``exists()`` check before it: two creates racing on one path give
    one file and one refusal in every KB kind (#391; the #974 cold read found
    the check-then-replace form let both succeed).
    """
    try:
        atomic_write_text(path, text, expect=ABSENT)
        return True
    except NotInGitRepository:
        _create_exclusive_unlocked(Path(path), text)
        return False
    except FileChanged as e:
        raise FileExistsError(str(e.path)) from e


def _create_exclusive_unlocked(target: Path, text: str) -> None:
    """The no-lock create: the bytes go to a temp file beside ``target``
    (fsynced, the mode a new file gets under the umask) and are published
    with ``publish_exclusive``; the temp file is removed on any failure."""
    target = Path(os.path.realpath(target))
    tmp = _tmp_beside(target, text.encode("utf-8"))
    try:
        publish_exclusive(tmp, target)
    except BaseException:
        _remove(tmp)
        raise
    _fsync_directory(target.parent)


def _tmp_beside(target: Path, data: bytes) -> str:
    """``data`` in a new temp file beside ``target``, fsynced, with the mode a
    new file gets under the umask; the caller publishes or removes it."""
    tmp = str(target.parent / f".{target.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        _remove(tmp)
        raise
    os.close(fd)
    return tmp


def _remove(tmp: str) -> None:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass


def _still_holds(src: Path, expect: bytes) -> None:
    try:
        on_disk: bytes | None = src.read_bytes()
    except FileNotFoundError:
        on_disk = None
    if on_disk != expect:
        raise FileChanged(src, on_disk)


def move_entry_text(
    src: str | os.PathLike[str],
    expect: bytes,
    dst: str | os.PathLike[str],
    text: str,
    *,
    lock_timeout: float = LOCK_TIMEOUT,
) -> bool:
    """Move an entry file: publish ``text`` at ``dst``, which must not exist,
    and remove ``src``, which must still hold ``expect``, as one write under
    one lock for both paths (``file_lock(src, dst)``); return whether it ran
    under the lock. The one move an entry file makes: rename, and
    software-kb's ``migrate-standards`` (#978 review, item 3).

    Raises ``FileChanged`` (nothing written, both paths as they were) when
    ``src`` no longer holds ``expect``, and ``FileExistsError`` when ``dst``
    is taken. Outside a git work tree (D1 (b)) the compare, the exclusive
    publish and the removal run without a lock, in that order. A removal of
    ``src`` that fails after ``dst`` is published raises with both files in
    place.
    """
    from ..utils.file_lock import file_lock

    src_path = Path(os.path.realpath(src))
    dst_path = Path(os.path.realpath(dst))
    tmp = _tmp_beside(dst_path, text.encode("utf-8"))
    try:
        try:
            with file_lock(src_path, dst_path, timeout=lock_timeout):
                _still_holds(src_path, expect)
                publish_exclusive(tmp, dst_path)
                os.unlink(src_path)
            locked = True
        except NotInGitRepository:
            _still_holds(src_path, expect)
            publish_exclusive(tmp, dst_path)
            os.unlink(src_path)
            locked = False
    except BaseException:
        _remove(tmp)
        raise
    _fsync_directory(dst_path.parent)
    if src_path.parent != dst_path.parent:
        _fsync_directory(src_path.parent)
    return locked


def publish_exclusive(tmp: str, path: Path) -> None:
    """Publish ``tmp`` to ``path``, refusing if ``path`` already exists.

    The contract: publish exactly the new file at ``path`` and leave nothing
    behind that the caller cannot name, or raise and leave ``path`` as it
    was. Mechanisms, in order:

    1. ``os.link(tmp, path)``: atomic, never exposes a partial file.
       ``FileExistsError`` stops here. Any other error (no hard links on
       FAT/exFAT, EACCES on some network shares, FUSE-specific errnos) goes
       on to step 2, which is still exclusive (#391 round 3; #643 review: an
       errno allow-list here failed every create on those).
    2. ``os.open(path, O_CREAT | O_EXCL)`` claims the name, then
       ``os.replace(tmp, path)`` fills it: the replace only targets a path
       this call just created. If anything after the claim fails, the
       placeholder is removed while it is still ours (#477).
       ``FileExistsError`` stops here; so does any error other than
       "unsupported" (ENOSPC, EDQUOT, EIO...), with ``path`` untouched.
    3. Only if O_EXCL itself is unsupported (ENOTSUP, ENOSYS, EOPNOTSUPP): a
       plain ``os.replace``, guarded only by the caller's own check.

    ``tmp`` belongs to the caller until a mechanism consumes it: on success
    it is gone (moved by ``os.replace``, or unlinked after step 1; if that
    unlink fails twice the publish still succeeds and a warning names the
    leftover); on a raise it is still there and the caller removes it. The
    same mechanism ``Entry._publish_exclusive`` used for the model's save;
    it lives here now, with the one publish of a create (C2, #974 cold read).
    """
    unsupported_errnos = {errno.ENOTSUP, errno.ENOSYS}
    unsupported_errnos.add(getattr(errno, "EOPNOTSUPP", errno.ENOTSUP))

    try:
        os.link(tmp, path)
    except FileExistsError:
        raise
    except OSError:
        pass  # step 2 is still exclusive, and re-raises real I/O errors
    else:
        # Published. The cleanup sits outside the try above so an unlink
        # error is never mistaken for a link failure; one retry for a
        # transient sharing error, then a warning (a second hard link blocks
        # `ids pin` and keeps today's bytes past the next update, #752).
        try:
            os.unlink(tmp)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                logger.warning(
                    "published %s, but could not remove its temp name %s, a second "
                    "hard link to the same file; delete it by hand",
                    path,
                    tmp,
                    exc_info=True,
                )
        return

    try:
        claim_fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno not in unsupported_errnos:
            raise
        # Neither atomic mechanism works here; the historical best effort.
        os.replace(tmp, path)
        return

    # From here the placeholder at `path` is ours: everything up to a
    # successful replace runs inside one cleanup scope (#643 review item 3).
    claim_stat = None
    try:
        try:
            claim_stat = os.fstat(claim_fd)
        finally:
            os.close(claim_fd)
        os.replace(tmp, path)
    except BaseException:
        _remove_claim(path, claim_stat)
        raise


def _remove_claim(path: Path, claim_stat: Any) -> None:
    """Remove the O_EXCL placeholder at ``path`` if it is still ours: while it
    names the same file as ``claim_stat``, or (without one) while it is
    empty, since no entry file ever is. Best effort; the caller is raising."""
    try:
        current = os.stat(path)
        ours = (
            os.path.samestat(claim_stat, current)
            if claim_stat is not None
            else current.st_size == 0
        )
        if ours:
            os.unlink(path)
    except OSError:
        pass


def publish_entry_text(
    path: str | os.PathLike[str],
    text: str,
    *,
    expect: bytes,
    lock_timeout: float = LOCK_TIMEOUT,
) -> bool:
    """Replace ``path`` with ``text`` if it still holds ``expect``; return whether
    the replace ran under the momentary lock.

    Raises ``FileChanged`` (nothing written) when the file no longer holds
    ``expect``: the caller re-reads and re-applies. In a git work tree the
    compare and the replace run under the lock, waited for at most
    ``lock_timeout`` seconds (``LockTimeout`` after that); outside one they
    run without it (D1 (b)) and the result is ``False``.
    """
    try:
        atomic_write_text(path, text, expect=expect, lock_timeout=lock_timeout)
        return True
    except NotInGitRepository:
        target = Path(path)
        try:
            on_disk: bytes | None = target.read_bytes()
        except FileNotFoundError:
            on_disk = None
        if on_disk != expect:
            raise FileChanged(target, on_disk) from None
        atomic_write_text(path, text)
        return False

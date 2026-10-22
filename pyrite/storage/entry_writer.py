"""The one function that publishes an entry file's bytes (ADR-0042 decisions 10, 12; #955).

Every write of an existing entry file ends here: ``KBService._update`` computes
the new text with ``file_operations.apply`` and publishes it with
``publish_entry_text``, which compares the file with the bytes the change was
computed on and replaces it under the momentary lock
(``KBFiles.write_bytes(expect=...)``, the lock in the repository's git dir,
§10a A1).

**Inside the KB root.** Every function here takes the KB's ``root``, and
the write itself is ``KBFiles``' (``pyrite/utils/kb_files.py``): the folder
is opened beneath the root without following a link and every step goes
through its descriptor, so the path approved is the path written, even when
a folder is swapped for a link meanwhile. A path that leaves the root is
refused (``KBFileOutsideRootError``, ``OUTSIDE_KB_ROOT``); so is a write to a
hard-linked file (``HardLinked``), in a KB under git or not, and a move of a
file that is itself a link (``KBFileIsLinkError``). Nothing is written
before the refusal.

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
migrations send operations and publish here. ``ids pin`` publishes with
``KBFiles.write_bytes``, the same write ``publish_entry_text`` makes, with
its compare (``id_pin_service._write_pin``); that test's
``ENTRY_FILE_WRITERS_NOT_YET_CONVERTED`` is empty.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from ..models.base import _timestamp_instant
from ..utils.atomic_write import ABSENT, FileChanged
from ..utils.file_lock import LOCK_TIMEOUT
from ..utils.kb_files import KBFiles

__all__ = [
    "FileChanged",
    "create_entry_text",
    "move_entry_text",
    "publish_entry_text",
    "raw_scalar",
    "remove_entry_file",
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


def create_entry_text(
    path: str | os.PathLike[str], text: str, *, root: str | os.PathLike[str]
) -> bool:
    """Write ``text`` to a path that must not exist yet; return whether the
    write ran under the lock. Raises ``FileExistsError`` when something is
    already there. Used by a create and by the templated move, the one move
    an update makes (ADR-0042 decisions 5 and 13). ``path`` must resolve
    inside the KB ``root`` (a dangling link to outside is refused).

    The exclusivity comes from the call that publishes (a hard link, else
    ``O_CREAT | O_EXCL``), under the momentary lock in a git work tree and
    without one outside it (D1 (b)), never from an ``exists()`` check before
    it: two creates racing on one path give one file and one refusal in every
    KB kind (#391; the #974 cold read found the check-then-replace form let
    both succeed).
    """
    return KBFiles(root).write_bytes(path, text.encode("utf-8"), expect=ABSENT)


def move_entry_text(
    src: str | os.PathLike[str],
    expect: bytes,
    dst: str | os.PathLike[str],
    text: str,
    *,
    root: str | os.PathLike[str],
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

    Inside ``root`` only (``KBFiles.move_bytes``): a source that is a
    symbolic link is refused, never followed to its target and removed
    there; so is a hard-linked source, and either path leaving the root.
    ``dst``'s folder is made after those checks, beneath the root.
    """
    return KBFiles(root).move_bytes(
        src, expect, dst, text.encode("utf-8"), lock_timeout=lock_timeout
    )


def publish_entry_text(
    path: str | os.PathLike[str],
    text: str,
    *,
    expect: bytes,
    root: str | os.PathLike[str],
    lock_timeout: float = LOCK_TIMEOUT,
) -> bool:
    """Replace ``path`` with ``text`` if it still holds ``expect``; return whether
    the replace ran under the momentary lock.

    ``path`` must resolve inside the KB ``root`` and have one link: a
    hard-linked file is refused under git and outside it alike (outside it
    the replace used to be written in place, through every name).

    Raises ``FileChanged`` (nothing written) when the file no longer holds
    ``expect``: the caller re-reads and re-applies. In a git work tree the
    compare and the replace run under the lock, waited for at most
    ``lock_timeout`` seconds (``LockTimeout`` after that); outside one they
    run without it (D1 (b)) and the result is ``False``.
    """
    return KBFiles(root).write_bytes(
        path, text.encode("utf-8"), expect=expect, lock_timeout=lock_timeout
    )


def remove_entry_file(
    path: str | os.PathLike[str], *, root: str | os.PathLike[str], missing_ok: bool = True
) -> None:
    """Remove the entry file named ``path``: the name itself (a link is
    removed as a link), only when it and the folder it is named in resolve
    inside the KB ``root``, through the folder opened beneath the root
    (``KBFiles.unlink``). A delete through a folder link to outside, or of a
    link to a file outside, is refused before anything is removed."""
    KBFiles(root).unlink(path, missing_ok=missing_ok)

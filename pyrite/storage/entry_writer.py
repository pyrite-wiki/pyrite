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
``create_entry_text``; rename, ``schema migrate``, ``ids pin`` and the
extensions' migrations send operations and publish here.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..models.base import _timestamp_instant
from ..utils.atomic_write import ABSENT, FileChanged, atomic_write_text
from ..utils.file_lock import LOCK_TIMEOUT, NotInGitRepository

__all__ = [
    "FileChanged",
    "create_entry_text",
    "publish_entry_text",
    "raw_scalar",
    "updated_at_stamp",
]

#: A zone as YAML or ISO 8601 spell it after the time: ``+02``, ``+0530``,
#: ``+05:30`` (#990); ``Z`` is checked apart.
_STAMP_TZ = re.compile(r"[+-]\d\d(?::?\d\d)?$")


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
    bare in that spelling. A zone is kept as spelled: ``Z``, ``+02``,
    ``+0530`` or ``+05:30`` (#990), the time converted to it.
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
    offset = _STAMP_TZ.search(spelled)
    tz = "Z" if spelled.endswith("Z") else (offset.group(0) if offset else "")
    core = spelled[: -len(tz)] if tz else spelled
    digits = len(core.rsplit(".", 1)[1]) if "." in core else 0
    shown = now
    if tz and tz != "Z":
        # `+02`, `+0530` or `+05:30`: the digits after the sign, hours first.
        sign = 1 if tz[0] == "+" else -1
        digits_only = tz[1:].replace(":", "")
        hours, minutes = int(digits_only[:2]), int(digits_only[2:] or 0)
        shown = now.astimezone(timezone(sign * timedelta(hours=hours, minutes=minutes)))
    text = shown.strftime(f"%Y-%m-%d{sep}%H:%M:%S")
    if digits:
        text += "." + "0" * digits
    text += tz
    return text if isinstance(old, str) else TimestampText(text)


def create_entry_text(path: str | os.PathLike[str], text: str) -> bool:
    """Write ``text`` to a path that must not exist yet (``expect=ABSENT``);
    return whether the write ran under the lock. Raises ``FileExistsError``
    when something is already there. Used by the templated move, the one
    move an update makes (ADR-0042 decision 5)."""
    try:
        atomic_write_text(path, text, expect=ABSENT)
        return True
    except NotInGitRepository:
        target = Path(path)
        if target.exists():
            raise FileExistsError(str(target)) from None
        atomic_write_text(path, text)
        return False
    except FileChanged as e:
        raise FileExistsError(str(e.path)) from e


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

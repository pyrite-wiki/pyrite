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

``tests/test_update_walks_every_surface.py`` pins the ratchet: no other
module in ``pyrite/`` or an extension writes an entry file's bytes, except
the writers it lists, and that list only shrinks.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..utils.atomic_write import ABSENT, FileChanged, atomic_write_text
from ..utils.file_lock import NotInGitRepository

__all__ = ["FileChanged", "create_entry_text", "publish_entry_text"]


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


def publish_entry_text(path: str | os.PathLike[str], text: str, *, expect: bytes) -> bool:
    """Replace ``path`` with ``text`` if it still holds ``expect``; return whether
    the replace ran under the momentary lock.

    Raises ``FileChanged`` (nothing written) when the file no longer holds
    ``expect``: the caller re-reads and re-applies. In a git work tree the
    compare and the replace run under the lock; outside one they run without
    it (D1 (b)) and the result is ``False``.
    """
    try:
        atomic_write_text(path, text, expect=expect)
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

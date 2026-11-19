"""Where a KB file resolves.

Every file Pyrite reads or writes as part of a KB resolves inside that KB's
resolved root. A link whose target is inside the same KB is fine; one that
leaves the root is skipped, with a warning that names it (once per file per
process).

These are pure checks. Opening, walking, writing, moving and deleting a KB
file is done only by ``pyrite/utils/kb_files.py``, which decides at the moment
of use.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_warned: set[tuple[str, str]] = set()


def resolves_inside(root: Path | str, path: Path | str) -> bool:
    """True when ``path``, links followed, lies inside ``root``, links followed.

    Resolved against resolved: a root that is itself reached through a link
    (a macOS temp dir, a mounted volume, a differently-cased path on a
    case-insensitive filesystem) compares equal to its own files. A path that
    cannot be resolved (a link loop, a permission error) is not inside.
    """
    try:
        real_root = os.path.realpath(root)
        real = os.path.realpath(path)
        if os.path.commonpath([os.path.normcase(real_root), os.path.normcase(real)]) == (
            os.path.normcase(real_root)
        ):
            return True
        # A case-insensitive filesystem spells the same directory two ways:
        # compare by identity, walking up from the path to the root.
        if not os.path.isdir(real_root):
            return False
        parent = real
        while True:
            if os.path.exists(parent) and os.path.samefile(parent, real_root):
                return True
            up = os.path.dirname(parent)
            if up == parent:
                return False
            parent = up
    except (OSError, ValueError, RuntimeError):
        return False


def inside_or_warn(root: Path | str, path: Path | str, what: str = "file") -> bool:
    """``resolves_inside``, logging a warning that names ``path`` when it is
    not -- once per file per process, however often it is listed or looked up."""
    if resolves_inside(root, path):
        return True
    key = (str(root), str(path))
    if key not in _warned:
        _warned.add(key)
        logger.warning("Skipping %s %s: it resolves outside the KB root %s", what, path, root)
    return False

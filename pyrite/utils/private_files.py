"""Files that hold secrets: owner-only from the first byte.

A file made with the process umask and ``chmod``-ed afterwards is readable by
other local users in between. These helpers hand the mode to the call that
creates the file, so there is no in-between.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700


def ensure_private_dir(path: Path) -> None:
    """Create `path` (and any missing parents) as directories only their owner
    can enter. A directory that already exists is left exactly as it is: it may
    be a project or home directory Pyrite does not own, and loosening or
    tightening it is not Pyrite's call. The files inside are what stay private.
    """
    path = Path(path)
    missing = []
    probe = path
    while not probe.exists() and probe != probe.parent:
        missing.append(probe)
        probe = probe.parent
    for directory in reversed(missing):
        try:
            os.mkdir(directory, PRIVATE_DIR_MODE)
        except FileExistsError:
            pass


def tighten_file(path: Path) -> None:
    """Make an existing regular file this user owns owner-only (no group or
    other access, no execute): a file that holds data, not a program."""
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
        return
    if not st.st_mode & stat.S_IWUSR:
        return  # read-only on purpose: not ours to make writable
    if stat.S_IMODE(st.st_mode) != PRIVATE_FILE_MODE:
        try:
            os.chmod(path, PRIVATE_FILE_MODE)
        except OSError:
            pass


def create_private_file(path: Path, *, tighten: bool = True) -> None:
    """Create `path` empty with mode 0600 if it does not exist (never a window
    at the umask's mode). One that exists is tightened to 0600, unless
    ``tighten=False`` leaves the mode its owner chose."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        os.close(os.open(path, flags, PRIVATE_FILE_MODE))
    except FileExistsError:
        if tighten:
            tighten_file(path)


def write_private_text(path: Path, text: str) -> None:
    """Write `text` to `path` via a 0600 sibling and an atomic rename."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        os.unlink(tmp)  # a leftover from an interrupted write keeps its old mode
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

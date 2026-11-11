"""An entry write goes through the folder its held lock names, or not at all.

``KBFiles.write_bytes`` and ``move_bytes`` open the entry's folder (a
descriptor, beneath the KB root, never following a link) and then take the
momentary lock, whose key names the folder by inode. The property: inside the
locked step, every folder descriptor the write goes through is the directory
the held lock key names (``(st_dev, st_ino)``); otherwise the write is
refused (``KBFolderChanged``) and nothing is written. One case per entry
write path: a create, an update, both ends of a move.

And the lock, taken again when the directory changed while it was being
taken, waits between attempts.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import pyrite.utils.file_lock as file_lock_module
import pyrite.utils.kb_files as kb_files_module
from pyrite.utils.atomic_write import ABSENT
from pyrite.utils.file_lock import LockTimeout, file_lock
from pyrite.utils.kb_files import KBFiles, KBFolderChanged

OLD = b"---\nid: a\n---\nold\n"
NEW = b"---\nid: a\n---\nnew\n"


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    (root / "sub").mkdir(parents=True)
    (root / "dst").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True, timeout=60)
    (root / "sub" / "a.md").write_bytes(OLD)
    return root


def _swap_before_locking(monkeypatch, folder: Path) -> None:
    """Replace ``folder`` with a copy just before the lock is taken: the
    write's descriptor is already open on the original."""
    real = kb_files_module.file_lock

    def swapping(*paths, **kwargs):
        moved = folder.with_name(folder.name + "-old")
        folder.rename(moved)
        folder.mkdir()
        for child in moved.iterdir():
            # The entries, not the write's own temp file (made before the lock).
            if not child.name.startswith("."):
                (folder / child.name).write_bytes(child.read_bytes())
        return real(*paths, **kwargs)

    monkeypatch.setattr(kb_files_module, "file_lock", swapping)


def _contents(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def _expected_after_swap(before: dict[str, bytes], folder: str) -> dict[str, bytes]:
    """The tree after the swap alone: the folder's files, also under -old."""
    after = dict(before)
    for name, data in before.items():
        if name.startswith(folder + "/"):
            after[folder + "-old/" + name[len(folder) + 1 :]] = data
    return after


WRITES = {
    "update": ("sub", lambda files, root: files.write_bytes(root / "sub/a.md", NEW, expect=OLD)),
    "create": ("sub", lambda files, root: files.write_bytes(root / "sub/b.md", NEW, expect=ABSENT)),
    "move (source folder)": (
        "sub",
        lambda files, root: files.move_bytes(root / "sub/a.md", OLD, root / "dst/a.md", NEW),
    ),
    "move (target folder)": (
        "dst",
        lambda files, root: files.move_bytes(root / "sub/a.md", OLD, root / "dst/a.md", NEW),
    ),
}


@pytest.mark.parametrize("write", list(WRITES))
def test_a_folder_replaced_before_the_lock_is_refused_and_nothing_written(kb, monkeypatch, write):
    folder, call = WRITES[write]
    before = _contents(kb)
    _swap_before_locking(monkeypatch, kb / folder)
    with pytest.raises(KBFolderChanged):
        call(KBFiles(kb), kb)
    assert _contents(kb) == _expected_after_swap(before, folder)
    assert not list(kb.rglob("*.tmp")), "a temp file was left behind"


@pytest.mark.control(reason="with no folder replaced, each write lands as before")
@pytest.mark.parametrize("write", list(WRITES))
def test_each_write_lands_when_nothing_moves(kb, write):
    _, call = WRITES[write]
    assert call(KBFiles(kb), kb) is True  # it ran under the lock
    assert NEW in _contents(kb).values()


def test_the_lock_waits_between_attempts_when_the_directory_keeps_changing(kb, monkeypatch):
    """A directory replaced while the lock is taken makes ``file_lock`` take
    it again for the new key. That retry spun: with a key that never settles,
    hundreds of thousands of attempts in a fraction of a second."""
    calls = {"n": 0}

    def ever_changing(path):
        calls["n"] += 1
        return f"0:{calls['n']}/a.md"

    monkeypatch.setattr(file_lock_module, "lock_key", ever_changing)
    with pytest.raises(LockTimeout):
        with file_lock(kb / "sub" / "a.md", timeout=0.3):
            pass
    # Two lock_key calls an attempt; a wait of at least the poll interval
    # between attempts bounds them well below a spin.
    assert calls["n"] < 2 * (0.3 / 0.001), calls


def test_file_lock_yields_the_keys_it_holds(kb):
    with file_lock(kb / "sub" / "a.md", timeout=1) as keys:
        st = os.stat(kb / "sub")
        assert keys == [f"{st.st_dev}:{st.st_ino}/a.md"]

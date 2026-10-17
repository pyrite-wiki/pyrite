"""What the one publish of an entry file promises (#955, B6 P4).

``tests/test_entry_save_atomic.py`` pinned these for ``Entry.save``, the
model's own whole-file write, which is gone: every entry file is now
published by ``pyrite/storage/entry_writer.py`` over ``atomic_write_text``.
The same promises, on the one path:

- a failed write leaves the previous content intact and no temp file behind;
- a new file's mode follows the umask, an existing file keeps its mode;
- a create (``create_entry_text``, ``expect=ABSENT``) never overwrites, and
  a second create of the same path is refused with nothing changed;
- a publish against stale bytes writes nothing (``FileChanged``).

Each runs inside a git work tree (the lock lives in its git dir) and, where
the promise does not depend on the lock, outside one too (D1 (b)).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from pyrite.storage.entry_writer import FileChanged, create_entry_text, publish_entry_text

pytestmark = pytest.mark.control(
    reason="the promises test_entry_save_atomic.py pinned for Entry.save, carried onto the one write path; each already held for atomic_write_text"
)

TEXT = "---\nid: note\ntitle: t\n---\n\nb\n"


@pytest.fixture(params=["git", "no-git"])
def folder(tmp_path: Path, request) -> Path:
    if request.param == "git":
        subprocess.run(
            ["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True, timeout=30
        )
    return tmp_path


def test_a_failed_write_leaves_previous_content_intact(folder: Path, monkeypatch):
    path = folder / "note.md"
    create_entry_text(path, TEXT)
    before = path.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        publish_entry_text(path, TEXT.replace("b\n", "second\n"), expect=before)

    assert path.read_bytes() == before, "a failed publish must not corrupt the file"
    assert sorted(p.name for p in folder.iterdir() if p.name != ".git") == ["note.md"], (
        "no temp file left behind"
    )


def test_a_new_files_mode_follows_the_umask(folder: Path):
    old = os.umask(0o022)
    try:
        path = folder / "note.md"
        create_entry_text(path, TEXT)
        assert oct(path.stat().st_mode & 0o777) == oct(0o644)
    finally:
        os.umask(old)


def test_an_existing_files_mode_is_kept(folder: Path):
    path = folder / "note.md"
    create_entry_text(path, TEXT)
    os.chmod(path, 0o600)

    publish_entry_text(path, TEXT.replace("b\n", "b2\n"), expect=path.read_bytes())

    assert oct(path.stat().st_mode & 0o777) == oct(0o600)
    assert path.read_text(encoding="utf-8").endswith("b2\n")


def test_a_create_refuses_an_existing_target_and_leaves_it_as_it_was(folder: Path):
    path = folder / "note.md"
    create_entry_text(path, TEXT)
    before = path.read_bytes()

    with pytest.raises(FileExistsError):
        create_entry_text(path, TEXT.replace("b\n", "REPLACED\n"))

    assert path.read_bytes() == before, "a create must never overwrite"
    assert sorted(p.name for p in folder.iterdir() if p.name != ".git") == ["note.md"]


def test_a_publish_against_stale_bytes_writes_nothing(folder: Path):
    path = folder / "note.md"
    create_entry_text(path, TEXT)
    stale = path.read_bytes()
    publish_entry_text(path, TEXT.replace("b\n", "first\n"), expect=stale)
    current = path.read_bytes()

    with pytest.raises(FileChanged) as info:
        publish_entry_text(path, TEXT.replace("b\n", "second\n"), expect=stale)

    assert path.read_bytes() == current
    assert info.value.on_disk == current

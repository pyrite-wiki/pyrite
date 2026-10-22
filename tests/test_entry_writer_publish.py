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

import errno
import os
import subprocess
from pathlib import Path

import pytest

from pyrite.storage.entry_writer import (
    FileChanged,
    create_entry_text,
    publish_entry_text,
)
from pyrite.utils.kb_files import KBFiles

TEXT = "---\nid: note\ntitle: t\n---\n\nb\n"


@pytest.fixture(params=["git", "no-git"])
def folder(tmp_path: Path, request) -> Path:
    if request.param == "git":
        subprocess.run(
            ["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True, timeout=30
        )
    return tmp_path


@pytest.mark.control(
    reason="Existing publication safety behavior; this case must pass without the link-result fix"
)
def test_a_failed_write_leaves_previous_content_intact(folder: Path, monkeypatch):
    path = folder / "note.md"
    create_entry_text(path, TEXT, root=(path).parent)
    before = path.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "rename", boom)  # the one call that publishes a replace
    with pytest.raises(OSError):
        publish_entry_text(path, TEXT.replace("b\n", "second\n"), expect=before, root=(path).parent)

    assert path.read_bytes() == before, "a failed publish must not corrupt the file"
    assert sorted(p.name for p in folder.iterdir() if p.name != ".git") == ["note.md"], (
        "no temp file left behind"
    )


@pytest.mark.control(
    reason="Existing publication safety behavior; this case must pass without the link-result fix"
)
def test_a_new_files_mode_follows_the_umask(folder: Path):
    old = os.umask(0o022)
    try:
        path = folder / "note.md"
        create_entry_text(path, TEXT, root=(path).parent)
        assert oct(path.stat().st_mode & 0o777) == oct(0o644)
    finally:
        os.umask(old)


@pytest.mark.control(
    reason="Existing publication safety behavior; this case must pass without the link-result fix"
)
def test_an_existing_files_mode_is_kept(folder: Path):
    path = folder / "note.md"
    create_entry_text(path, TEXT, root=(path).parent)
    os.chmod(path, 0o600)

    publish_entry_text(
        path, TEXT.replace("b\n", "b2\n"), expect=path.read_bytes(), root=(path).parent
    )

    assert oct(path.stat().st_mode & 0o777) == oct(0o600)
    assert path.read_text(encoding="utf-8").endswith("b2\n")


@pytest.mark.control(
    reason="Existing publication safety behavior; this case must pass without the link-result fix"
)
def test_a_create_refuses_an_existing_target_and_leaves_it_as_it_was(folder: Path):
    path = folder / "note.md"
    create_entry_text(path, TEXT, root=(path).parent)
    before = path.read_bytes()

    with pytest.raises(FileExistsError):
        create_entry_text(path, TEXT.replace("b\n", "REPLACED\n"), root=(path).parent)

    assert path.read_bytes() == before, "a create must never overwrite"
    assert sorted(p.name for p in folder.iterdir() if p.name != ".git") == ["note.md"]


@pytest.mark.control(
    reason="Existing publication safety behavior; this case must pass without the link-result fix"
)
def test_a_publish_against_stale_bytes_writes_nothing(folder: Path):
    path = folder / "note.md"
    create_entry_text(path, TEXT, root=(path).parent)
    stale = path.read_bytes()
    publish_entry_text(path, TEXT.replace("b\n", "first\n"), expect=stale, root=(path).parent)
    current = path.read_bytes()

    with pytest.raises(FileChanged) as info:
        publish_entry_text(path, TEXT.replace("b\n", "second\n"), expect=stale, root=(path).parent)

    assert path.read_bytes() == current
    assert info.value.on_disk == current


def _tmp_with(target: Path, text: str = "new\n") -> str:
    tmp = target.parent / f".{target.name}.tmp"
    tmp.write_text(text, encoding="utf-8")
    return str(tmp)


def publish_exclusive(tmp: str, target: Path) -> None:
    """The exclusive publish of a create, ``Folder.link_exclusive`` (what
    ``entry_writer.publish_exclusive`` was before every KB write went through
    ``KBFiles``): ``tmp`` beside ``target`` gets ``target``'s name."""
    with KBFiles(target.parent).folder(target, create=False) as (folder, name):
        folder.link_exclusive(os.path.basename(tmp), folder, name)


def test_a_link_that_was_made_but_reported_io_error_is_a_success(tmp_path: Path, monkeypatch):
    """A hard link can be created and still report EIO. The publish used to go
    on to O_EXCL, find the target (our own bytes) and raise FileExistsError
    while our file sat at the target, breaking "raise and leave the target as
    it was" (#914)."""
    target = tmp_path / "note.md"
    tmp = _tmp_with(target)
    real_link = os.link

    def link_then_fail(src, dst, *args, **kwargs):
        real_link(src, dst, *args, **kwargs)
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(os, "link", link_then_fail)

    publish_exclusive(tmp, target)

    assert target.read_text(encoding="utf-8") == "new\n"
    assert not os.path.exists(tmp), "the temp name is gone on success"


def test_a_link_that_was_made_but_reported_eexist_is_a_success(tmp_path: Path, monkeypatch):
    """An NFS retransmit of a link that succeeded reports EEXIST on the second
    call; the target is still ours (#914)."""
    target = tmp_path / "note.md"
    tmp = _tmp_with(target)
    real_link = os.link

    def link_then_eexist(src, dst, *args, **kwargs):
        real_link(src, dst, *args, **kwargs)
        raise FileExistsError(errno.EEXIST, "File exists")

    monkeypatch.setattr(os, "link", link_then_eexist)

    publish_exclusive(tmp, target)

    assert target.read_text(encoding="utf-8") == "new\n"
    assert not os.path.exists(tmp)


@pytest.mark.control(
    reason="Existing publication safety behavior; this case must pass without the link-result fix"
)
def test_a_link_that_was_not_made_still_falls_back_to_o_excl(tmp_path: Path, monkeypatch):
    """The new same-file check must not turn a real link failure into a
    success or skip the O_EXCL fallback."""
    target = tmp_path / "note.md"
    tmp = _tmp_with(target)

    def link_fails(src, dst, *args, **kwargs):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(os, "link", link_fails)

    publish_exclusive(tmp, target)

    assert target.read_text(encoding="utf-8") == "new\n"
    assert not os.path.exists(tmp)


@pytest.mark.control(
    reason="Existing publication safety behavior; this case must pass without the link-result fix"
)
def test_a_link_error_with_another_writers_file_still_refuses(tmp_path: Path, monkeypatch):
    target = tmp_path / "note.md"
    target.write_text("theirs\n", encoding="utf-8")
    tmp = _tmp_with(target)

    def link_fails(src, dst, *args, **kwargs):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(os, "link", link_fails)

    with pytest.raises(FileExistsError):
        publish_exclusive(tmp, target)

    assert target.read_text(encoding="utf-8") == "theirs\n"
    assert os.path.exists(tmp), "on a raise the caller still owns the temp file"


@pytest.mark.control(
    reason="Existing publication safety behavior; this case must pass without the link-result fix"
)
def test_an_fstat_failure_removes_our_placeholder_and_raises(tmp_path: Path, monkeypatch):
    """The placeholder branch used to pass whether it raised or succeeded;
    assert the raise and that our empty placeholder is gone (#914)."""
    target = tmp_path / "note.md"
    tmp = _tmp_with(target)

    def link_fails(src, dst, *args, **kwargs):
        raise OSError(errno.EIO, "Input/output error")

    def fstat_fails(fd):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(os, "link", link_fails)
    monkeypatch.setattr(os, "fstat", fstat_fails)

    with pytest.raises(OSError):
        publish_exclusive(tmp, target)

    assert not target.exists(), "our empty placeholder is removed"
    assert os.path.exists(tmp)


@pytest.mark.control(
    reason="Existing publication safety behavior; this case must pass without the link-result fix"
)
def test_a_failed_fstat_leaves_a_file_another_writer_put_there(tmp_path: Path, monkeypatch):
    """Between the O_EXCL claim and the failed fstat another writer can replace
    the placeholder; cleanup must not delete their file (#914). The fd the
    failed fstat runs on points at a different file, as it would after the
    replacement."""
    target = tmp_path / "note.md"
    tmp = _tmp_with(target)
    other = tmp_path / "other.md"
    other.write_text("their fd\n", encoding="utf-8")
    real_open = os.open

    def link_fails(src, dst, *args, **kwargs):
        raise OSError(errno.EIO, "Input/output error")

    def open_with_another_writers_file(path_arg, flags, *args, **kwargs):
        if flags & os.O_EXCL:
            target.write_text("theirs\n", encoding="utf-8")
            return real_open(other, os.O_RDWR)
        return real_open(path_arg, flags, *args, **kwargs)

    def fstat_fails(fd):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(os, "link", link_fails)
    monkeypatch.setattr(os, "open", open_with_another_writers_file)
    monkeypatch.setattr(os, "fstat", fstat_fails)

    with pytest.raises(OSError):
        publish_exclusive(tmp, target)

    assert target.read_text(encoding="utf-8") == "theirs\n"

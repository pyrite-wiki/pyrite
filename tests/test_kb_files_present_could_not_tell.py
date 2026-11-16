"""``KBFiles.present`` answers exists, absent, or "could not tell" (#1542).

v0.26.3 (f8cbbe1d) made ``present`` raise any ``OSError`` other than the four
errnos ``Path.exists`` calls absent, so a folder the process cannot search
could not be read as "absent" by index health. Every other caller treats
``present`` as a yes/no probe and had no handler: one KB whose root the
process cannot search stopped every command, and an id longer than the
filesystem's name limit was a traceback instead of NOT_FOUND
(``tests/test_an_id_that_cannot_exist_is_not_found.py``).

The property, the same on every Python version:

- exists: True; a path that resolves outside the root: True, with no stat;
- absent: False, and a name the filesystem cannot hold (too long) is
  absent, because no file can have it;
- could not tell: one typed Pyrite error, ``KBPresenceUnknownError``. No raw
  ``OSError`` leaves ``present``.

The unsearchable-folder cases need a user the folder's mode stops; as root
they skip with a reason that shows in the report (``-rs``), never pass.

Medium: real files, real modes, the real ``load_config`` and CLI; nothing
mocks the filesystem.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from pyrite.exceptions import PyriteError
from pyrite.utils.kb_files import KBFiles, KBPresenceUnknownError


def _skip_if_mode_does_not_stop_us(folder: Path) -> None:
    if os.access(folder / "anything", os.F_OK) or os.access(folder, os.X_OK):
        pytest.skip(
            f"running as a user (uid {os.getuid()}) whose access is not stopped by "
            f"mode {folder.stat().st_mode & 0o777:o}: the could-not-tell case is not entered"
        )


# -- the probe itself --------------------------------------------------------


@pytest.mark.control(reason="the exists answer the fix keeps")
def test_an_existing_file_is_present(tmp_path):
    (tmp_path / "a.md").write_text("a\n", encoding="utf-8")
    assert KBFiles(tmp_path).present(tmp_path / "a.md") is True


@pytest.mark.control(reason="the absent answer the fix keeps")
def test_a_missing_file_is_absent(tmp_path):
    assert KBFiles(tmp_path).present(tmp_path / "gone.md") is False


@pytest.mark.control(reason="ENOTDIR was absent before and after")
def test_a_path_through_a_file_is_absent(tmp_path):
    """ENOTDIR: ``a.md/b.md`` names nothing when ``a.md`` is a file."""
    (tmp_path / "a.md").write_text("a\n", encoding="utf-8")
    assert KBFiles(tmp_path).present(tmp_path / "a.md" / "b.md") is False


@pytest.mark.control(reason="ELOOP was absent before and after")
def test_a_symlink_loop_inside_the_kb_is_absent(tmp_path):
    """ELOOP: two links inside the KB that point at each other."""
    (tmp_path / "x").symlink_to(tmp_path / "y")
    (tmp_path / "y").symlink_to(tmp_path / "x")
    assert KBFiles(tmp_path).present(tmp_path / "x" / "f.md") is False


@pytest.mark.parametrize("length", [300, 5000], ids=["name-too-long", "path-too-long"])
def test_a_name_the_filesystem_cannot_hold_is_absent(tmp_path, length):
    """ENAMETOOLONG: no file can have the name, so it is not there. The
    folder on the way exists, or the OS may answer ENOENT first."""
    assert KBFiles(tmp_path).present(tmp_path / ("a" * length + ".md")) is False


@pytest.mark.control(reason="the outside-root answer the fix keeps")
def test_a_path_outside_the_root_is_present_whatever_lies_there(tmp_path):
    """Outside the root, "there" whatever is outside: the read or write that
    follows refuses it (OUTSIDE_KB_ROOT), never "absent" because a file
    outside is missing, and never "could not tell" because a folder outside
    cannot be searched."""
    kb = tmp_path / "kb"
    kb.mkdir()
    shut = tmp_path / "shut"
    shut.mkdir()
    (kb / "out").symlink_to(tmp_path / "gone.md")  # a dangling link out
    shut.chmod(0o000)
    try:
        assert KBFiles(kb).present(tmp_path / "gone.md") is True
        assert KBFiles(kb).present(kb / "out") is True
        assert KBFiles(kb).present(shut / "x.md") is True
    finally:
        shut.chmod(0o755)


@pytest.mark.parametrize("mode", [0o600, 0o000], ids=["no-search-bit", "no-access"])
def test_an_unsearchable_folder_is_could_not_tell_as_one_typed_error(tmp_path, mode):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "d.md").write_text("d\n", encoding="utf-8")
    sub.chmod(mode)
    try:
        _skip_if_mode_does_not_stop_us(sub)
        with pytest.raises(KBPresenceUnknownError) as caught:
            KBFiles(tmp_path).present(sub / "d.md")
        err = caught.value
        assert isinstance(err, PyriteError) and not isinstance(err, OSError)
        # One answer whatever the cause: the text names no path and no errno.
        assert str(sub) not in str(err) and str(tmp_path) not in str(err)
        assert str(err) == KBPresenceUnknownError().args[0]
        assert isinstance(err.__cause__, PermissionError)  # kept for local reports
    finally:
        sub.chmod(0o755)


# -- config load: one unreadable KB does not stop the others ------------------


@pytest.fixture
def two_kbs(cli_world, tmp_path):
    """``good`` (with a kb.yaml and one entry) and ``locked``, both registered."""
    good, locked = tmp_path / "good", tmp_path / "locked"
    world = cli_world(
        kbs=[
            KBConfig(name="good", path=good, kb_type="generic"),
            KBConfig(name="locked", path=locked, kb_type="generic"),
        ]
    )
    (good / "kb.yaml").write_text("name: good\ntypes: {}\n", encoding="utf-8")
    (good / "hello.md").write_text(
        "---\nid: hello\ntype: note\ntitle: Hello\n---\nfoo body\n", encoding="utf-8"
    )
    world.index()
    return world, good, locked


def _locked(locked: Path, mode: int):
    locked.chmod(mode)
    _skip_if_mode_does_not_stop_us(locked)


@pytest.mark.parametrize("mode", [0o600, 0o000], ids=["no-search-bit", "no-access"])
def test_load_config_keeps_every_kb_and_marks_the_unreadable_one(two_kbs, mode):
    from pyrite.config import load_config

    world, good, locked = two_kbs
    try:
        _locked(locked, mode)
        with world.pointed():
            config = load_config()
        by_name = {kb.name: kb for kb in config.knowledge_bases}
        assert set(by_name) == {"good", "locked"}
        assert by_name["good"].unreadable is None
        assert by_name["locked"].unreadable == KBPresenceUnknownError().args[0]
    finally:
        locked.chmod(0o755)


def test_a_kb_yaml_the_process_cannot_read_marks_its_kb_not_the_load(two_kbs):
    """The same symptom one step later: the root is searchable, ``kb.yaml``
    is there, and reading it is refused. The load goes on."""
    from pyrite.config import load_config

    world, good, locked = two_kbs
    kb_yaml = locked / "kb.yaml"
    kb_yaml.write_text("name: locked\n", encoding="utf-8")
    kb_yaml.chmod(0o000)
    try:
        if os.access(kb_yaml, os.R_OK):
            pytest.skip(f"running as a user (uid {os.getuid()}) who can read a mode-000 file")
        with world.pointed():
            config = load_config()
        locked_kb = config.get_kb("locked")
        assert locked_kb is not None and locked_kb.unreadable
        assert config.get_kb("good").unreadable is None
    finally:
        kb_yaml.chmod(0o644)


def test_commands_on_another_kb_work_and_the_unreadable_kb_is_named(two_kbs):
    world, good, locked = two_kbs
    try:
        _locked(locked, 0o600)

        search = world.cli("search", "foo", "-k", "good", "--format", "json")
        assert search.exit_code == 0, search.output
        assert [r["id"] for r in json.loads(search.output)["results"]] == ["hello"]

        got = world.cli("get", "hello", "-k", "good", "--format", "json")
        assert got.exit_code == 0, got.output

        listed = world.cli("kb", "list", "--format", "json")
        assert listed.exit_code == 0, listed.output
        kbs = {kb["name"]: kb for kb in json.loads(listed.output)["kbs"]}
        assert set(kbs) == {"good", "locked"}
        assert kbs["locked"]["unreadable"] == KBPresenceUnknownError().args[0]
        assert "unreadable" not in kbs["good"]

        health = world.cli("index", "health", "--format", "json")
        assert not isinstance(health.exception, OSError), repr(health.exception)
        data = json.loads(health.stdout)
        assert data["status"] == "unhealthy"
        assert {"kb": "locked", "error": KBPresenceUnknownError().args[0]} in data["unreadable_kbs"]
    finally:
        locked.chmod(0o755)

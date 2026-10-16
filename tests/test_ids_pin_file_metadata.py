"""Pin restores the temporary inode's owner/group or reports a refusal."""

import json
import os
import stat
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services import id_pin_service as pin


@pytest.mark.parametrize(
    "same_group",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.control(
                reason="Matching temporary-file ownership needs no restoration"
            ),
        ),
    ],
)
def test_replace_compares_the_temporary_file_group(same_group):
    original = SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=123, st_gid=456)
    temporary = SimpleNamespace(st_uid=123, st_gid=456 if same_group else 789)
    with (
        patch.object(pin.os, "stat", return_value=original),
        patch.object(pin.os, "open", return_value=10),
        patch.object(pin.os, "fstat", return_value=temporary),
        patch.object(pin.os, "fchmod", create=True),
        patch.object(pin.os, "fchown", create=True) as chown,
        patch.object(pin.os, "getuid", return_value=123, create=True),
        patch.object(pin.os, "getgid", return_value=456, create=True),
        patch.object(pin.os, "O_NOFOLLOW", 0, create=True),
        patch.object(pin.os, "write", side_effect=lambda fd, data: len(data)),
        patch.object(pin.os, "fsync"),
        patch.object(pin.os, "close"),
        patch.object(pin, "_rename"),
    ):
        pin._replace_through(9, "note.md", b"pinned")
    assert chown.call_count == (0 if same_group else 1)
    if not same_group:
        chown.assert_called_once_with(10, 123, 456)


def test_cli_reports_group_restore_failure_without_changing_file(tmp_path):
    path = tmp_path / "kb"
    path.mkdir()
    file = path / "note.md"
    before = b"---\ntitle: Note\ntype: note\n---\nBody.\n"
    file.write_bytes(before)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="notes", path=path)],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    original = SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=123, st_gid=456)
    temporary = SimpleNamespace(st_uid=123, st_gid=789)

    def replace(*args):
        with (
            patch.object(pin.os, "stat", return_value=original),
            patch.object(pin.os, "open", return_value=10),
            patch.object(pin.os, "fstat", return_value=temporary),
            patch.object(pin.os, "fchmod", create=True),
            patch.object(
                pin.os, "fchown", side_effect=PermissionError("not a group member"), create=True
            ),
            patch.object(pin.os, "getuid", return_value=123, create=True),
            patch.object(pin.os, "getgid", return_value=789, create=True),
            patch.object(pin.os, "O_NOFOLLOW", 0, create=True),
            patch.object(pin.os, "close"),
            patch.object(pin.os, "unlink"),
        ):
            pin._replace_through(9, "note.md", b"pinned")

    with (
        patch("pyrite.cli.context.load_config", return_value=config),
        patch.object(pin, "_write_pin", side_effect=replace),
    ):
        result = CliRunner().invoke(app, ["ids", "pin", "-k", "notes", "--format", "json"])
    assert result.exit_code == 3, result.output
    refusal = json.loads(result.stdout)["refused"][0]
    assert "group 456" in refusal["reason"]
    assert "by hand" in refusal["reason"]
    assert file.read_bytes() == before


def test_documented_metadata_and_race_limits():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "docs/pinning-entry-ids.md").read_text()
    for term in ["extended attributes", "ACLs", "hard link", "rename"]:
        assert term in text and term in pin._write_pin.__doc__


@pytest.mark.skipif(not hasattr(os, "fchown"), reason="POSIX metadata operation")
@pytest.mark.parametrize(
    "different_group",
    [
        True,
        pytest.param(
            False, marks=pytest.mark.control(reason="Real same-group pin remains supported")
        ),
    ],
)
def test_cli_pin_keeps_real_mode_owner_and_group(tmp_path, different_group):
    path = tmp_path / "kb"
    path.mkdir()
    file = path / "note.md"
    file.write_text("---\ntitle: Note\ntype: note\n---\nBody.\n")
    file.chmod(0o640)
    before = file.stat()
    if different_group:
        groups = [group for group in os.getgroups() if group != before.st_gid]
        if not groups and os.geteuid() != 0:
            pytest.skip("User has no second group for a setgid-folder fixture")
        folder_group = groups[0] if groups else before.st_gid + 1
        os.chown(path, -1, folder_group)
        path.chmod(0o2770)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="notes", path=path)],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    with patch("pyrite.cli.context.load_config", return_value=config):
        result = CliRunner().invoke(app, ["ids", "pin", "-k", "notes", "--format", "json"])
    assert result.exit_code == 0, result.output
    after = file.stat()
    assert (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) == (
        before.st_uid,
        before.st_gid,
        0o640,
    )

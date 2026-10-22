"""Pin restores the temporary inode's owner/group or reports a refusal.

The temp file's group is compared with the original's, not the process's
(a setgid folder gives a new file the folder's group):
``test_cli_pin_keeps_real_mode_owner_and_group`` holds that on a real
setgid folder, through ``KBFiles.write_temp``.
"""

import json
import os
import stat
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings, save_config
from pyrite.services import id_pin_service as pin


def test_documented_metadata_and_race_limits():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "docs/pinning-entry-ids.md").read_text()
    for term in ["extended attributes", "ACLs", "hard link", "rename"]:
        assert term in text and term in pin._write_pin.__doc__


@pytest.mark.skipif(not hasattr(os, "fchown"), reason="POSIX metadata operation")
@pytest.mark.parametrize("fmt", ["json", "text"])
def test_cli_reports_actual_fchown_permission_error(tmp_path, monkeypatch, fmt):
    """The temp file cannot take the original's group: pin refuses, naming
    the group, in either output format, and never writes the file in place."""
    path = tmp_path / "kb"
    path.mkdir()
    file = path / "note.md"
    before = b"---\ntitle: Note\ntype: note\n---\nBody.\n"
    file.write_bytes(before)
    original = file.stat()
    groups = [group for group in os.getgroups() if group != original.st_gid]
    if not groups and os.geteuid() != 0:
        pytest.skip("User has no second group for a setgid-folder fixture")
    folder_group = groups[0] if groups else original.st_gid + 1
    os.chown(path, -1, folder_group)
    path.chmod(0o2770)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="notes", path=path)],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    calls = []

    def refuse_chown(fd, uid, gid):
        calls.append((uid, gid))
        assert os.fstat(fd).st_gid == folder_group
        raise PermissionError("synthetic fchown refusal")

    monkeypatch.setattr(os, "fchown", refuse_chown)
    save_config(config)
    result = CliRunner().invoke(app, ["ids", "pin", "-k", "notes", "--format", fmt])
    assert calls == [(original.st_uid, original.st_gid)]
    assert result.exit_code == 3, result.output
    if fmt == "json":
        refused = json.loads(result.stdout)["refused"]
        assert len(refused) == 1
        assert refused[0]["path"] == "note.md"
        assert f"group {original.st_gid}" in refused[0]["reason"]
        assert "by hand" in refused[0]["reason"]
    else:
        assert (
            f"refused note.md: cannot preserve owner {original.st_uid} and group "
            f"{original.st_gid}" in result.output
        )
        assert "by hand" in result.output
    assert file.read_bytes() == before
    assert not list(path.glob(".*.tmp"))


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
    save_config(config)
    result = CliRunner().invoke(app, ["ids", "pin", "-k", "notes", "--format", "json"])
    assert result.exit_code == 0, result.output
    after = file.stat()
    assert (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) == (
        before.st_uid,
        before.st_gid,
        0o640,
    )

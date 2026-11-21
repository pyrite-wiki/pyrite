"""Tests for #21: `pyrite db backup` writing into the current directory.

With no `--output`, the command built a bare relative filename
(`pyrite-backup-<timestamp>.db`), which resolves against the process's cwd.
The repo root had accumulated 125 of them (58 MB), gitignored so nobody
noticed. A backup belongs beside the database it backs up.
"""

import sqlite3
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import PyriteConfig, Settings

runner = CliRunner()


@pytest.fixture
def db_env(tmp_path):
    """A data dir holding an index.db, and a separate cwd to run from."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    index_path = data_dir / "index.db"
    conn = sqlite3.connect(str(index_path))
    conn.execute("CREATE TABLE entry (id TEXT)")
    conn.execute("INSERT INTO entry VALUES ('x')")
    conn.commit()
    conn.close()

    workdir = tmp_path / "somewhere-else"
    workdir.mkdir()

    config = PyriteConfig(knowledge_bases=[], settings=Settings(index_path=index_path))
    return {"config": config, "data_dir": data_dir, "workdir": workdir, "index": index_path}


def _run(db_env, monkeypatch, *args):
    """Invoke the CLI from a cwd that is NOT the data dir."""
    cwd = db_env["workdir"]
    monkeypatch.chdir(cwd)
    with patch("pyrite.cli.db_commands.load_config", return_value=db_env["config"]):
        result = runner.invoke(app, ["db", "backup", *args])
    return result, cwd


class TestBackupDoesNotWriteToCwd:
    def test_backup_leaves_the_cwd_untouched(self, db_env, monkeypatch):
        """The bug: 125 backup files accumulated in the repo root."""
        result, cwd = _run(db_env, monkeypatch)

        assert result.exit_code == 0, result.output
        assert list(cwd.iterdir()) == [], f"backup written into cwd: {list(cwd.iterdir())}"

    def test_backup_lands_in_the_data_dir_backups_folder(self, db_env, monkeypatch):
        result, _ = _run(db_env, monkeypatch)

        assert result.exit_code == 0, result.output
        backups = list((db_env["data_dir"] / "backups").glob("pyrite-backup-*.db"))
        assert len(backups) == 1, backups

    def test_the_backup_is_a_readable_copy_of_the_index(self, db_env, monkeypatch):
        _run(db_env, monkeypatch)

        backup = next((db_env["data_dir"] / "backups").glob("pyrite-backup-*.db"))
        conn = sqlite3.connect(str(backup))
        assert conn.execute("SELECT id FROM entry").fetchall() == [("x",)]
        conn.close()

    def test_the_printed_path_is_where_the_file_actually_is(self, db_env, monkeypatch):
        """An operator has to be able to find it from the output alone."""
        from pathlib import Path

        result, _ = _run(db_env, monkeypatch)

        printed = result.output.split("Backup created:", 1)[1].strip()
        assert Path(printed).exists(), result.output


class TestExplicitOutputStillWins:
    def test_output_flag_places_the_file_exactly_there(self, db_env, tmp_path):
        target = tmp_path / "elsewhere" / "my-backup.db"

        with patch("pyrite.cli.db_commands.load_config", return_value=db_env["config"]):
            result = runner.invoke(app, ["db", "backup", "--output", str(target)])

        assert result.exit_code == 0, result.output
        assert target.exists()

    def test_a_relative_output_is_still_relative_to_cwd(self, db_env, monkeypatch):
        """`-o` is an explicit instruction; it keeps its usual meaning."""
        result, cwd = _run(db_env, monkeypatch, "--output", "here.db")

        assert result.exit_code == 0, result.output
        assert (cwd / "here.db").exists()


class TestBackupFoldersAreOwnerOnly:
    """A folder `pyrite db backup` creates for its file is its owner's alone
    (0700), as the changelog says of every folder Pyrite creates for its
    private files; a folder that already exists keeps its mode."""

    def test_a_folder_it_creates_is_0700(self, db_env, monkeypatch):
        target = db_env["workdir"] / "made" / "here" / "b.db"
        result, _ = _run(db_env, monkeypatch, "--output", str(target))
        assert result.exit_code == 0, result.output
        for folder in (target.parent, target.parent.parent):
            assert folder.stat().st_mode & 0o777 == 0o700, folder

    def test_the_default_backups_folder_is_0700(self, db_env, monkeypatch):
        result, _ = _run(db_env, monkeypatch)
        assert result.exit_code == 0, result.output
        assert (db_env["data_dir"] / "backups").stat().st_mode & 0o777 == 0o700

    @pytest.mark.control(reason="an existing folder is not Pyrite's to change")
    def test_an_existing_folder_keeps_its_mode(self, db_env, monkeypatch):
        folder = db_env["workdir"] / "mine"
        folder.mkdir(mode=0o755)
        folder.chmod(0o755)
        result, _ = _run(db_env, monkeypatch, "--output", str(folder / "b.db"))
        assert result.exit_code == 0, result.output
        assert folder.stat().st_mode & 0o777 == 0o755

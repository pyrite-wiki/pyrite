"""Files that hold secrets are owner-only from creation, in an owner-only directory.

Property: the index database (password hashes, tokens) and its -wal/-shm files,
and the GitHub token file, are 0600 inside a 0700 directory, with no moment at
which another local user could read them -- checked under a permissive umask --
and an existing file is tightened when opened.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from pyrite.config import GitHubAuth
from pyrite.storage.database import PyriteDB

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def open_umask():
    old = os.umask(0o000)
    yield
    os.umask(old)


def _db_files(db_path: Path) -> list[Path]:
    return [
        db_path,
        db_path.with_name(db_path.name + "-wal"),
        db_path.with_name(db_path.name + "-shm"),
    ]


@pytest.mark.control(reason="round 1 already created the index 0600; kept as a guard")
def test_a_new_index_is_0600_in_a_new_0700_directory(tmp_path, open_umask):
    db_path = tmp_path / "data" / "index.db"
    db = PyriteDB(db_path)
    try:
        db.session.execute(__import__("sqlalchemy").text("select 1"))
        assert _mode(db_path.parent) == 0o700
        for f in _db_files(db_path):
            assert f.exists(), f
            assert _mode(f) == 0o600, (f, oct(_mode(f)))
    finally:
        db.close()


@pytest.mark.control(reason="round 1 already created the index 0600; kept as a guard")
def test_the_index_is_private_before_sqlite_first_opens_it(tmp_path, open_umask, monkeypatch):
    """No window: at the moment the engine is created the file already exists
    and is 0600, so nothing is ever readable at the umask's mode."""
    from pyrite.storage import connection

    seen: list[int] = []
    real = connection.index_file_problem
    db_path = tmp_path / "data" / "index.db"

    def spy(*a, **kw):
        seen.append(_mode(db_path))  # called just before the engine opens the file
        return real(*a, **kw)

    monkeypatch.setattr(connection, "index_file_problem", spy)
    PyriteDB(db_path).close()
    assert seen == [0o600]


def test_an_existing_directory_is_left_as_it_is(tmp_path):
    """The index may sit in a shared project directory: Pyrite does not
    change the mode of a directory it did not create. The files are private."""
    data = tmp_path / "shared"
    data.mkdir()
    os.chmod(data, 0o755)
    db_path = data / "index.db"
    PyriteDB(db_path).close()
    assert _mode(data) == 0o755
    assert _mode(db_path) == 0o600


@pytest.mark.control(reason="round 1 already created the index 0600; kept as a guard")
def test_an_existing_loose_index_is_tightened_on_open(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db_path = data / "index.db"
    PyriteDB(db_path).close()
    for f in _db_files(db_path):
        if f.exists():
            os.chmod(f, 0o644)
    db = PyriteDB(db_path)
    try:
        for f in _db_files(db_path):
            if f.exists():
                assert _mode(f) == 0o600, (f, oct(_mode(f)))
    finally:
        db.close()


def test_the_github_token_file_is_never_wider_than_0600(tmp_path, monkeypatch, open_umask):
    from pyrite import github_auth

    config_dir = tmp_path / "cfg"
    monkeypatch.setattr(github_auth, "trusted_config_dir", lambda: config_dir)
    seen: list[int] = []
    real_replace = os.replace

    def replace(src, dst, *a, **kw):
        seen.append(_mode(Path(src)))  # the file as written, before it takes its name
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", replace)
    github_auth.save_github_auth(GitHubAuth(access_token="tok"))

    path = github_auth.get_auth_file_path()
    assert _mode(path) == 0o600
    assert _mode(config_dir) == 0o700
    assert seen and all(m == 0o600 for m in seen)
    assert github_auth.load_github_auth().access_token == "tok"


def test_a_loose_token_file_is_tightened_when_read_and_its_directory_is_left(tmp_path, monkeypatch):
    from pyrite import github_auth

    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    monkeypatch.setattr(github_auth, "trusted_config_dir", lambda: config_dir)
    github_auth.save_github_auth(GitHubAuth(access_token="tok"))
    path = github_auth.get_auth_file_path()
    os.chmod(path, 0o644)
    os.chmod(config_dir, 0o755)
    github_auth.load_github_auth()
    assert _mode(path) == 0o600
    assert _mode(config_dir) == 0o755


def test_an_index_backup_is_0600(tmp_path, open_umask):
    from pyrite.services.index_backup import backup_index

    db_path = tmp_path / "data" / "index.db"
    PyriteDB(db_path).close()
    out = tmp_path / "backup.db"
    backup_index(db_path, out)
    assert _mode(out) == 0o600


def test_a_backup_written_over_an_existing_loose_file_ends_0600(tmp_path):
    from pyrite.services.index_backup import backup_index

    db_path = tmp_path / "data" / "index.db"
    PyriteDB(db_path).close()
    out = tmp_path / "backup.db"
    out.write_bytes(b"")
    os.chmod(out, 0o644)
    backup_index(db_path, out)
    assert _mode(out) == 0o600


def test_db_restore_leaves_the_index_0600(cli_world, tmp_path):
    world = cli_world()
    db_path = world.config.settings.index_path
    backup = tmp_path / "b.db"
    assert world.cli("db", "backup", "--output", str(backup)).exit_code == 0
    os.chmod(backup, 0o644)  # a backup that was copied around
    os.chmod(db_path, 0o644)
    result = world.cli("db", "restore", str(backup), "--force")
    assert result.exit_code == 0, result.output
    assert _mode(db_path) == 0o600


def test_a_new_config_yaml_is_created_0600(tmp_path, monkeypatch, open_umask):
    from pyrite import config as config_module

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_dir / "config.yaml")
    monkeypatch.setenv("PYRITE_DATA_DIR", str(config_dir))
    config = config_module.load_config()
    config_module.save_config(config)
    path = config_dir / "config.yaml"
    assert _mode(path) == 0o600
    os.chmod(path, 0o640)  # an owner's choice for an existing file is kept
    config_module.save_config(config)
    assert _mode(path) == 0o640

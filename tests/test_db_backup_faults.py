"""`pyrite db backup` tells a fault of the index from a fault of the backup's
destination, and a lock from either (#1260 round 2).

`REBUILD_SUGGESTION` tells a user to run `pyrite db backup` before a rebuild,
so this is the first command run after a refusal: its own refusals must not
send a user with a healthy index to move it aside. The properties:

- a destination that cannot be written never says the index is unreadable,
  and never advises a rebuild;
- a lock on either file is retryable (`StorageBusyError`), and the copy does
  not wait on it for ever;
- corruption advice is given only for a corrupt index (a corruption code or
  an ``integrity_check`` verdict);
- a file already at the output path is never deleted by a failed backup.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyrite import config as config_module
from pyrite.cli import app
from pyrite.storage.database import PyriteDB

runner = CliRunner()

needs_permissions = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores file permissions",
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A config whose index is a healthy PyriteDB file."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    kb_path = tmp_path / "kb"
    for d in (config_dir, data_dir, kb_path):
        d.mkdir()
    config_file = config_dir / "config.yaml"
    config_file.write_text(
        json.dumps(
            {
                "knowledge_bases": [{"name": "t", "path": str(kb_path), "kb_type": "generic"}],
                "settings": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("PYRITE_DATA_DIR", str(data_dir))
    monkeypatch.delenv("PYRITE_FORMAT", raising=False)
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)
    index = data_dir / "index.db"
    PyriteDB(index).close()
    return {"index": index, "tmp": tmp_path}


def _refusal(result) -> dict:
    assert result.exit_code == 1, (result.exit_code, result.stdout)
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "STORAGE_ERROR", payload
    return payload


def _says_rebuild(payload: dict) -> bool:
    text = f"{payload.get('error', '')} {payload.get('suggestion', '')}".lower()
    return "index build" in text or "move the unreadable" in text


def _names_the_destination(payload: dict) -> None:
    """A StorageError's message is the class's fixed public sentence (ADR-0037
    §3), so the suggestion is what tells the user which file is at fault."""
    suggestion = payload.get("suggestion") or ""
    assert not _says_rebuild(payload), payload
    assert "--output" in suggestion, payload
    assert "index was not changed" in suggestion, payload


@needs_permissions
def test_a_read_only_output_directory_is_the_destination_not_the_index(home):
    out_dir = home["tmp"] / "read-only"
    out_dir.mkdir()
    out_dir.chmod(0o555)
    try:
        result = runner.invoke(app, ["db", "backup", "--output", str(out_dir / "b.db")])
    finally:
        out_dir.chmod(0o755)
    payload = _refusal(result)
    _names_the_destination(payload)
    assert payload["retryable"] is False


@needs_permissions
def test_a_read_only_output_file_is_the_destination_and_is_kept(home):
    out = home["tmp"] / "old-backup.db"
    keep = sqlite3.connect(out)
    keep.execute("CREATE TABLE kept (x)")
    keep.commit()
    keep.close()
    before = out.read_bytes()
    out.chmod(0o444)
    try:
        result = runner.invoke(app, ["db", "backup", "--output", str(out)])
    finally:
        out.chmod(0o644)
    _names_the_destination(_refusal(result))
    assert out.read_bytes() == before


def test_a_file_at_the_output_that_is_not_a_database_is_named_and_kept(home):
    """SQLite answers the copy onto a non-database file with SQLITE_NOTADB, a
    corruption code: it is the destination's, and the index is healthy."""
    out = home["tmp"] / "notes.txt"
    out.write_text("my notes, not a database\n" * 200)
    before = out.read_bytes()
    result = runner.invoke(app, ["db", "backup", "--output", str(out)])
    _names_the_destination(_refusal(result))
    assert out.read_bytes() == before


def _hold(path: Path) -> sqlite3.Connection:
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    return holder


def _rollback_journal(path: Path) -> None:
    """In WAL mode a writer does not block a reader; the lock is met on a
    rollback-journal file (a copy made by `db backup`, or an old index)."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.close()


def test_a_locked_index_is_retryable_from_the_cli(home, monkeypatch):
    from pyrite.storage import connection

    monkeypatch.setattr(connection, "SQLITE_BUSY_TIMEOUT_MS", 50)
    _rollback_journal(home["index"])
    holder = _hold(home["index"])
    try:
        result = runner.invoke(app, ["db", "backup", "--output", str(home["tmp"] / "b.db")])
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    payload = _refusal(result)
    assert payload["retryable"] is True, payload
    assert "wait" in payload["suggestion"].lower(), payload
    assert not _says_rebuild(payload), payload


def test_a_locked_destination_is_retryable_and_the_copy_does_not_wait_for_ever(home, monkeypatch):
    """Python's ``Connection.backup`` retries SQLITE_BUSY with no limit; the
    command must stop and say so."""
    from pyrite.exceptions import StorageError
    from pyrite.services.index_backup import backup_index
    from pyrite.storage import connection

    monkeypatch.setattr(connection, "SQLITE_BUSY_TIMEOUT_MS", 50)
    out = home["tmp"] / "busy.db"
    seed = sqlite3.connect(out)
    seed.execute("CREATE TABLE other (x)")
    seed.commit()
    seed.close()
    holder = _hold(out)
    outcome: dict[str, BaseException | None] = {}

    def run() -> None:
        try:
            backup_index(home["index"], out)
            outcome["error"] = None
        except BaseException as exc:  # noqa: BLE001 - reported below
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=10)
    finished_under_the_lock = "error" in outcome
    holder.execute("ROLLBACK")
    holder.close()
    worker.join(timeout=10)
    assert finished_under_the_lock, "backup_index waited on the destination's lock for ever"
    err = outcome["error"]
    assert isinstance(err, StorageError), repr(err)
    assert type(err).__name__ == "StorageBusyError", (type(err).__name__, str(err))
    assert err.retryable is True
    assert "index build" not in (err.suggestion or "")
    assert out.exists(), "the file already at the output path was deleted"


@pytest.mark.control(reason="corruption advice for a corrupt index held before round 2 too")
def test_a_corrupt_index_is_still_refused_with_the_rebuild_advice(home):
    """The control: real corruption keeps its advice."""
    home["index"].write_bytes(b"garbage" * 1000)
    for side in ("-wal", "-shm"):
        Path(f"{home['index']}{side}").unlink(missing_ok=True)
    result = runner.invoke(app, ["db", "backup", "--output", str(home["tmp"] / "b.db")])
    payload = _refusal(result)
    assert _says_rebuild(payload), payload
    assert not (home["tmp"] / "b.db").exists()

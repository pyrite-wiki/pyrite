"""Storage failures keep the classifier's advice through the real CLI."""

import errno
import functools
import json
import sqlite3

import pytest

from pyrite.config import KBConfig
from pyrite.storage.errors import BUSY_SUGGESTION, DISK_SUGGESTION, storage_error_from
from tests._world import build_world


@pytest.mark.parametrize(
    "error",
    [
        sqlite3.OperationalError("disk I/O error"),
        sqlite3.OperationalError("unable to open database file"),
        sqlite3.OperationalError("database or disk is full"),
        PermissionError("index is not writable"),
        OSError(errno.ENOSPC, "No space left on device"),
    ],
    ids=["io", "open", "full", "permission", "no-space"],
)
@pytest.mark.parametrize(
    "suggestion",
    [
        None,
        pytest.param(
            "Try the query again",
            marks=pytest.mark.control(
                reason="disk advice already overrides explicit caller advice"
            ),
        ),
    ],
)
def test_disk_advice_does_not_depend_on_caller_advice(error, suggestion):
    result = storage_error_from(error, suggestion=suggestion)

    assert result.suggestion == DISK_SUGGESTION
    assert result.retryable is False


def test_a_disk_fault_uses_the_callers_disk_advice_without_other_advice():
    result = storage_error_from(
        sqlite3.OperationalError("disk I/O error"), disk_suggestion="Check the backup destination"
    )

    assert result.suggestion == "Check the backup destination"


@pytest.mark.control(reason="lock and ordinary-error advice must keep their existing behavior")
@pytest.mark.parametrize("suggestion", [None, "Check the query"])
def test_other_storage_errors_keep_their_advice(suggestion):
    busy = storage_error_from(sqlite3.OperationalError("database is locked"), suggestion=suggestion)
    other = storage_error_from(
        sqlite3.OperationalError("no such table: example"), suggestion=suggestion
    )

    assert busy.suggestion == BUSY_SUGGESTION
    assert busy.retryable is True
    assert other.suggestion == suggestion
    assert other.retryable is False


class DiskFaultCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=(), /):
        if " MATCH " in sql.upper():
            raise sqlite3.OperationalError("disk I/O error")
        return super().execute(sql, parameters)


class DiskFaultConnection(sqlite3.Connection):
    def cursor(self, factory=DiskFaultCursor):
        return super().cursor(factory)


def test_search_reports_disk_advice_when_the_driver_cannot_read(tmp_path, monkeypatch):
    world = build_world(tmp_path, kbs=[KBConfig(name="notes", path=tmp_path / "kb")], rest=False)
    try:
        world.add_entry("notes", "---\nid: needle\ntype: note\ntitle: Needle\n---\n\nA note.\n")
        # Fail the database driver's read, leaving CLI and service wiring intact.
        monkeypatch.setattr(
            sqlite3.dbapi2,
            "connect",
            functools.partial(sqlite3.dbapi2.connect, factory=DiskFaultConnection),
        )

        result = world.cli("search", "needle", "--format", "json")

        assert result.exit_code == 1, result.output
        error = json.loads(result.stdout)
        assert error["error_code"] == "STORAGE_ERROR", error
        assert error["suggestion"] == DISK_SUGGESTION, error
        assert error["retryable"] is False, error
    finally:
        world.close()

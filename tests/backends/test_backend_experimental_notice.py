"""The Postgres backend says, once per process, that it is experimental (#718).

The seam is ``PostgresBackend.__init__``: every route to a Postgres-backed
index builds the backend there, so the notice needs no per-command change.
Constructing the backend needs no database (the session is optional), so these
tests run everywhere, not only under the Postgres CI job.
"""

import logging
import subprocess
import sys

import pytest

from pyrite.storage.backends import postgres_backend as pg
from pyrite.storage.backends.postgres_backend import PostgresBackend

ACK_ENV = "PYRITE_POSTGRES_EXPERIMENTAL_ACK"
URL = "https://github.com/pyrite-wiki/pyrite/issues/718"


@pytest.fixture(autouse=True)
def _fresh_process_state(monkeypatch):
    """Each test is a new process as far as the once-only latch goes."""
    monkeypatch.delenv(ACK_ENV, raising=False)
    monkeypatch.setattr(pg, "_experimental_notice_shown", False, raising=False)


def test_notice_on_stderr_once_nothing_on_stdout(capfd):
    PostgresBackend()
    PostgresBackend()  # a second connection / request in the same process
    out, err = capfd.readouterr()
    assert out == ""
    assert err.count("experimental") == 1
    assert "may not upgrade" in err
    assert URL in err


def test_notice_reaches_the_server_log(caplog):
    with caplog.at_level(logging.WARNING, logger="pyrite.storage.backends.postgres_backend"):
        PostgresBackend()
        PostgresBackend()
    records = [r for r in caplog.records if "experimental" in r.getMessage()]
    assert len(records) == 1
    assert URL in records[0].getMessage()


@pytest.mark.control(
    reason="silent before the change too; it fails once the notice exists and the ack check is removed"
)
@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_ack_setting_silences_it(monkeypatch, capfd, caplog, value):
    monkeypatch.setenv(ACK_ENV, value)
    with caplog.at_level(logging.DEBUG):
        PostgresBackend()
    out, err = capfd.readouterr()
    assert out == "" and err == ""
    assert not [r for r in caplog.records if "experimental" in r.getMessage()]


def test_logging_to_stderr_does_not_double_the_line(capfd):
    from pyrite.logging import configure_logging

    configure_logging(level="WARNING")
    try:
        PostgresBackend()
    finally:
        logging.getLogger("pyrite").handlers.clear()
    _, err = capfd.readouterr()
    assert err.count("experimental") == 1


def _run(code, env_extra=None):
    import os

    env = {k: v for k, v in os.environ.items() if k != ACK_ENV}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120
    )


def test_fresh_process_stdout_is_still_valid_json():
    """The real thing: a process that builds the backend and prints JSON."""
    code = (
        "import json\n"
        "from pyrite.storage.backends.postgres_backend import PostgresBackend\n"
        "PostgresBackend(); PostgresBackend()\n"
        "print(json.dumps({'ok': True}))\n"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stderr
    import json

    assert json.loads(proc.stdout) == {"ok": True}
    assert proc.stderr.count("experimental") == 1

    quiet = _run(code, {ACK_ENV: "1"})
    assert json.loads(quiet.stdout) == {"ok": True}
    assert quiet.stderr == ""


@pytest.mark.control(
    reason="SQLite users saw nothing before; this pins that they still see nothing"
)
def test_sqlite_users_see_nothing(tmp_path, capfd, caplog):
    from pyrite.storage.database import PyriteDB

    with caplog.at_level(logging.DEBUG):
        db = PyriteDB(tmp_path / "index.db")
        db.close()
    out, err = capfd.readouterr()
    assert "experimental" not in out + err
    assert not [r for r in caplog.records if "Postgres" in r.getMessage()]
    assert not getattr(pg, "_experimental_notice_shown", False)


def test_quiet_logging_does_not_swallow_the_notice(capfd):
    """``-q`` raises the log level above WARNING; the notice still reaches stderr."""
    from pyrite.logging import configure_logging

    configure_logging(level="ERROR")
    try:
        PostgresBackend()
    finally:
        logging.getLogger("pyrite").handlers.clear()
    _, err = capfd.readouterr()
    assert err.count("experimental") == 1

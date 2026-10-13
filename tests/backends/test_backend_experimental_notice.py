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
    """Each test is a new process as far as the once-only latch goes.

    Also restores the ``pyrite`` logger exactly (handlers, level, propagate):
    ``configure_logging`` replaces the package's ``NullHandler`` and sets
    ``propagate=False``, which would hide ``pyrite.*`` from ``caplog`` for the
    rest of the xdist worker (pattern: tests/test_logging_configuration.py).
    """
    package_logger = logging.getLogger("pyrite")
    saved = (package_logger.handlers[:], package_logger.level, package_logger.propagate)
    monkeypatch.delenv(ACK_ENV, raising=False)
    monkeypatch.setattr(pg, "_experimental_notice_shown", False, raising=False)
    yield
    package_logger.handlers, level, propagate = saved[0], saved[1], saved[2]
    package_logger.setLevel(level)
    package_logger.propagate = propagate


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
    PostgresBackend()
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
    PostgresBackend()
    _, err = capfd.readouterr()
    assert err.count("experimental") == 1


@pytest.mark.control(
    reason="logger-state guard for this file; red when the fixture restore is removed"
)
def test_these_tests_leave_the_pyrite_logger_as_they_found_it():
    """Guard for the fixture: handlers, level and propagate survive a reconfigure."""
    from pyrite.logging import configure_logging

    package_logger = logging.getLogger("pyrite")
    before = (package_logger.handlers[:], package_logger.level, package_logger.propagate)
    configure_logging(level="ERROR")
    assert package_logger.propagate is False  # the leak the fixture must undo
    # The fixture's teardown runs after this test; its effect is asserted by the
    # next test via the module-level record below.
    _LEAK_CHECK.append(before)


_LEAK_CHECK: list = []


@pytest.mark.control(
    reason="logger-state guard for this file; red when the fixture restore is removed"
)
def test_zz_logger_state_was_restored_by_the_previous_test():
    if not _LEAK_CHECK:
        pytest.skip("ordering: run with the whole file")
    package_logger = logging.getLogger("pyrite")
    assert (
        package_logger.handlers[:],
        package_logger.level,
        package_logger.propagate,
    ) == _LEAK_CHECK[-1]
    assert any(isinstance(h, logging.NullHandler) for h in package_logger.handlers)


class _BoomStream:
    def write(self, _):
        raise BrokenPipeError("stderr write fails")

    def flush(self):
        raise BrokenPipeError("stderr flush fails")


def _closed_stream():
    import io

    s = io.StringIO()
    s.close()
    return s


@pytest.mark.control(
    reason="passes on dev, which has no notice; red against the unguarded first-round notice (print to a broken stderr / file=None)"
)
@pytest.mark.parametrize(
    "make_stderr",
    [lambda: None, _BoomStream, _closed_stream],
    ids=["stderr-is-None", "write-raises", "closed"],
)
def test_a_broken_stderr_never_stops_construction_or_reaches_stdout(
    monkeypatch, capsys, make_stderr
):
    import sys

    monkeypatch.setattr(sys, "stderr", make_stderr())
    backend = PostgresBackend()  # must not raise
    assert backend is not None
    assert capsys.readouterr().out == ""


@pytest.mark.control(
    reason="passes on dev, which has no notice; red against the unguarded first-round notice (print to a broken stderr / file=None)"
)
def test_a_failing_log_handler_never_stops_construction(monkeypatch, capfd):
    def boom(*a, **k):
        raise RuntimeError("logging is broken")

    monkeypatch.setattr(pg.logger, "warning", boom)
    assert PostgresBackend() is not None
    out, _ = capfd.readouterr()
    assert out == ""


@pytest.mark.control(
    reason="passes on dev, which has no notice; red against the unguarded first-round notice (print to a broken stderr / file=None)"
)
def test_fd2_closed_at_startup_prints_nothing_to_stdout():
    """``2>&-``: Python starts with sys.stderr None; the line must not go to stdout."""
    import subprocess

    code = (
        "from pyrite.storage.backends.postgres_backend import PostgresBackend\n"
        "PostgresBackend()\n"
        "print('ok')\n"
    )
    import os

    env = {k: v for k, v in os.environ.items() if k != ACK_ENV}
    proc = subprocess.run(
        f'"{sys.executable}" -c "{code}" 2>&-',
        shell=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.stdout == "ok\n", proc.stdout

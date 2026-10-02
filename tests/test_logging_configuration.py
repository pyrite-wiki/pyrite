"""Regression tests for CLI-owned logging configuration."""

import json
import logging
import subprocess
import sys

from pyrite.cli import main

#: What pytest's own argv was when this module was collected; `main()` rewrites
#: `sys.argv`, so a test that calls it in-process must put it back.
_ARGV_AT_COLLECTION = list(sys.argv)


def test_bare_import_does_not_configure_application_logging():
    """Library imports must not install an emitting handler or touch root."""
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import logging, pyrite, pyrite.logging; "
            "print([type(h).__name__ for h in logging.getLogger('pyrite').handlers]); "
            "print([type(h).__name__ for h in logging.root.handlers])",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert probe.stdout.splitlines() == ["['NullHandler']", "[]"]
    assert probe.stderr == ""


def test_cli_warning_is_formatted_on_stderr_and_json_stdout_stays_parseable():
    """CLI startup owns logging while machine-readable stdout remains clean."""
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, logging; from unittest.mock import patch; from pyrite.cli import main; "
            "probe=lambda: (logging.getLogger('pyrite.cli.test').warning('configuration warning'), "
            "print(json.dumps({'ok': True}))); "
            "patch('pyrite.cli.app', side_effect=probe).start(); main()",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(probe.stdout) == {"ok": True}
    assert "[WARNING] pyrite.cli.test: configuration warning" in probe.stderr
    assert "Traceback" not in probe.stderr


def test_repeated_cli_startup_does_not_accumulate_handlers(monkeypatch):
    """Repeated in-process CLI invocations replace rather than stack handlers."""
    package_logger = logging.getLogger("pyrite")
    original_handlers = package_logger.handlers[:]
    original_level = package_logger.level
    original_propagate = package_logger.propagate
    original_root_handlers = logging.root.handlers[:]
    try:
        from unittest.mock import patch

        monkeypatch.setattr(sys, "argv", list(sys.argv))  # main() strips -v from it
        with patch("pyrite.cli.app"):
            main()
            main()

        assert len(package_logger.handlers) == 1
        assert not isinstance(package_logger.handlers[0], logging.NullHandler)
        assert logging.root.handlers == original_root_handlers
    finally:
        package_logger.handlers = original_handlers
        package_logger.setLevel(original_level)
        package_logger.propagate = original_propagate


def test_zz_in_process_main_leaves_the_runners_argv_alone():
    """`main()` strips `-v` from `sys.argv`; under `pytest -v` that stripped
    pytest's own argv for the rest of the worker. Runs last in the module."""
    assert sys.argv == _ARGV_AT_COLLECTION

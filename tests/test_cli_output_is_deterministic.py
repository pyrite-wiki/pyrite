"""CLI output is the same text on a laptop and in CI (#884).

CI sets GITHUB_ACTIONS, which makes Typer render help and usage errors as
ANSI boxes; a laptop did not. conftest pins one rendering for the run. These
tests enter that through the real entry point and, in a subprocess, under the
hostile CI-like environment.
"""

import os
import subprocess
import sys

from typer.testing import CliRunner

from pyrite.cli import app

_ANSI = "\x1b"


def _assert_plain(text: str) -> None:
    # Rich draws the usage-error panel's box characters even without a
    # terminal; that is the same text everywhere. What differs is ANSI and width.
    assert _ANSI not in text, repr(text[:200])
    assert max(len(line) for line in text.splitlines()) <= 80, text


def test_usage_error_and_help_are_plain_in_process():
    # A usage error is one JSON line (ADR-0046, amendment 2026-10-15): no ANSI,
    # and its width is the message's, not the terminal's. Help is still wrapped.
    error = CliRunner().invoke(app, ["get", "--bogus"])
    assert _ANSI not in error.output, repr(error.output[:200])
    assert "No such option: --bogus" in error.output
    _assert_plain(CliRunner().invoke(app, ["task", "list", "--help"]).output)


def test_plain_under_hostile_ci_environment(tmp_path):
    """Run the whole conftest path with the CI variables set on the outside."""
    test = tmp_path / "test_inner.py"
    test.write_text(
        "from typer.testing import CliRunner\n"
        "from pyrite.cli import app\n"
        "def test_it():\n"
        "    out = CliRunner().invoke(app, ['get', '--bogus']).output\n"
        "    assert '\\x1b' not in out, repr(out[:200])\n"
        "    assert 'No such option: --bogus' in out\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "GITHUB_ACTIONS": "true",
        "FORCE_COLOR": "1",
        "PY_COLORS": "1",
        "COLUMNS": "200",
        "TERM": "xterm-256color",
        "PYTEST_ADDOPTS": "",
    }
    env.pop("NO_COLOR", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-o",
            "addopts=",
            "--rootdir",
            os.getcwd(),
            "-c",
            "pyproject.toml",
            "-p",
            "tests.conftest",
            str(test),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_terminal_colour_opt_in_renders_boxes(terminal_colour):
    """A test that checks styling on purpose can still get it."""
    import importlib
    import typer.rich_utils as ru

    assert ru.FORCE_TERMINAL is True

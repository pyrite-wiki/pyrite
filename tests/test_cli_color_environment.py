"""Run a newly added raw CLI assertion under an inherited forced-colour setting."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "forced",
    [
        pytest.param(
            False, marks=pytest.mark.control(reason="ordinary non-terminal output already works")
        ),
        True,
    ],
)
def test_new_plain_cli_assertion_ignores_ambient_color(tmp_path, forced):
    (tmp_path / "conftest.py").write_text(
        (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "test_cli.py").write_text(
        """
import typer
import io
from rich.console import Console
from typer.testing import CliRunner
from pyrite.utils.errors import cli_error
stream = io.StringIO()
console = Console(file=stream)

def test_module_level_console_is_plain():
    console.print('0.25.6')
    assert '0.25.6' in stream.getvalue()
    assert '\\x1b' not in stream.getvalue()

def test_new_raw_assertion():
    app = typer.Typer()
    @app.command()
    def run():
        cli_error('missing KB', 'rich', error_code='KB_NOT_FOUND')
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 1
    assert '[KB_NOT_FOUND]' in result.stdout
    assert '\\x1b' not in result.stdout

def test_explicit_color_remains_available(monkeypatch):
    from rich.console import Console
    import io
    monkeypatch.setenv('FORCE_COLOR', '1')
    stream = io.StringIO()
    Console(file=stream).print('[bold red]colour[/bold red]')
    assert '\\x1b' in stream.getvalue()
""",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PYTEST_ADDOPTS": "",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "TERM": "xterm-256color",
    }
    env.pop("FORCE_COLOR", None)
    env.pop("NO_COLOR", None)
    env.pop("TTY_COMPATIBLE", None)
    if forced:
        env["FORCE_COLOR"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", str(tmp_path / "test_cli.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

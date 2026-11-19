"""A successful setup must remain reportable on real terminal encodings (#841.2)."""

import io

import pytest
from rich.console import Console

from pyrite.cli.mcp_setup_command import _print_report


@pytest.mark.parametrize(
    ("encoding", "value"),
    [
        pytest.param(
            encoding,
            value,
            marks=(
                pytest.mark.control(reason="already encodable text remains unchanged")
                if (encoding, value) in {("utf-8", "日本語"), ("utf-8", "café"), ("cp1252", "café")}
                else ()
            ),
        )
        for encoding in ("utf-8", "ascii", "cp1252")
        for value in ("bad\ud800argument", "日本語", "café")
    ],
)
def test_changed_client_report_escapes_unencodable_values(encoding, value):
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding=encoding, errors="strict")
    console = Console(file=stream, color_system=None, force_terminal=False, width=200)
    report = {
        "new_entry": {"tools": None, "tier": "write", "command": ["pyrite", "mcp"]},
        "clients": [
            {
                "label": "Client",
                "status": "changed",
                "location": "config.json",
                "discarded": [value],
                "notes": [],
            }
        ],
        "notes": [],
    }
    _print_report(console, report)
    stream.flush()
    text = buffer.getvalue().decode(encoding)
    assert "Client: changed" in text
    assert "discarded:" in text
    assert value.encode(encoding, errors="backslashreplace").decode(encoding) in text
    assert "Restart the client" in text


@pytest.mark.parametrize("encoding", ["ascii", "cp1252"])
def test_no_client_command_reports_manual_setup_on_strict_terminal(tmp_path, encoding):
    import os
    from pathlib import Path
    import subprocess
    import sys

    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "日本語"
    env = dict(os.environ)
    for key in ("CLAUDE_CONFIG_DIR", "PYRITE_CONFIG_DIR", "PYRITE_DATA_DIR"):
        env.pop(key, None)
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        APPDATA=str(home / "appdata"),
        XDG_CONFIG_HOME=str(home / ".config"),
        PATH=str(tmp_path / "empty-bin"),
        PYRITE_CONFIG_DIR=str(config),
        PYTHONIOENCODING=encoding + ":strict",
        NO_COLOR="1",
        TERM="dumb",
        COLUMNS="300",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pyrite.cli import main; main()",
            "mcp-setup",
            "--format",
            "rich",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        timeout=60,
    )
    output = result.stdout.decode(encoding)
    errors = result.stderr.decode(encoding)
    assert result.returncode == 1, errors
    assert "Traceback" not in errors, errors
    assert "No MCP client found" in output
    assert "claude mcp add" in output
    assert '"mcpServers"' in output
    assert "日本語".encode(encoding, errors="backslashreplace").decode(encoding) in output

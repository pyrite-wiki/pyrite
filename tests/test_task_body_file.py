"""Task create/update --body-file and --stdin (#1687)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyrite.cli.task_commands import _resolve_task_body, task_app
from pyrite.exceptions import ValidationError

TEXT = "Outline with `backticks` and $dollar\nSecond line\n'quotes' \"double\"\n"

def test_body_file_writes_same_as_body(tmp_path: Path):
    f = tmp_path / "body.txt"
    f.write_text(TEXT)
    assert _resolve_task_body(None, f, False) == TEXT
    assert _resolve_task_body(TEXT, None, False) == TEXT

def test_stdin_writes_same_as_body_file(tmp_path: Path, monkeypatch):
    f = tmp_path / "body.txt"
    f.write_text(TEXT)
    monkeypatch.setattr("pyrite.cli.task_commands._read_stdin", lambda: TEXT)
    assert _resolve_task_body(None, None, True) == TEXT
    assert _resolve_task_body(None, f, False) == TEXT

def test_body_file_dash_means_stdin(monkeypatch):
    monkeypatch.setattr("pyrite.cli.task_commands._read_stdin", lambda: TEXT)
    assert _resolve_task_body(None, Path("-"), False) == TEXT

@pytest.mark.parametrize(
    "body,body_file,stdin_flag",
    [
        ("x", Path("f.txt"), False),
        ("x", None, True),
        (None, Path("f.txt"), True),
    ],
)
def test_two_sources_refused(body, body_file, stdin_flag):
    with pytest.raises(ValidationError, match="only one body source"):
        _resolve_task_body(body, body_file, stdin_flag)

def test_frontmatter_refused_names_pyrite_create():
    with pytest.raises(ValidationError, match="pyrite create"):
        _resolve_task_body("---\ntitle: x\n---\nbody\n", None, False)

def test_missing_file_refused(tmp_path: Path):
    with pytest.raises(Exception):
        _resolve_task_body(None, tmp_path / "nope.txt", False)

def test_directory_refused(tmp_path: Path):
    with pytest.raises(Exception):
        _resolve_task_body(None, tmp_path, False)

def test_empty_file(tmp_path: Path):
    f = tmp_path / "empty.txt"
    f.write_text("")
    assert _resolve_task_body(None, f, False) == ""

def test_non_utf8_refused(tmp_path: Path):
    f = tmp_path / "bad.txt"
    f.write_bytes(b"\xff\xfe bad")
    with pytest.raises(ValidationError, match="not valid UTF-8"):
        _resolve_task_body(None, f, False)

def test_two_sources_exit_2_via_cli():
    runner = CliRunner()
    result = runner.invoke(
        task_app,
        ["create", "T", "-k", "test-kb", "--body", "x", "--body-file", "f.txt"],
    )
    assert result.exit_code == 2

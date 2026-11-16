"""Paths remain copyable in narrow terminals and captured CLI output (#1291)."""

import ast
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest


@pytest.fixture(params=[40, 80])
def narrow_terminal(request, monkeypatch):
    monkeypatch.setenv("COLUMNS", str(request.param))
    monkeypatch.setenv("NO_COLOR", "1")


def test_export_site_output_path_stays_on_label_line(cli_world, tmp_path, narrow_terminal):
    world = cli_world()
    world.add_entry("test-kb", "---\nid: note\ntitle: Note\ntype: note\n---\nBody\n")
    output = tmp_path / ("long-folder-" * 8) / "[draft] site output"
    assert len(str(output)) > 120

    result = world.cli("export", "site", "-k", "test-kb", "-o", str(output))

    assert result.exit_code == 0, result.output
    assert f"  Output: {output}" in result.output.splitlines()
    assert list(output.rglob("*.md")), "The real site exporter must write content"


def test_index_sync_malformed_path_stays_on_bullet_line(cli_world, narrow_terminal):
    world = cli_world()
    malformed = world.kb_path("test-kb") / ("long-folder-" * 8) / "[draft] bad note.md"
    malformed.parent.mkdir(parents=True)
    malformed.write_text("---\ntitle: [unclosed\n---\nBody\n", encoding="utf-8")
    assert len(str(malformed)) > 120

    result = world.cli("index", "sync", "-k", "test-kb")

    assert result.exit_code == 0, result.output
    assert "Malformed: 1 file(s) skipped" in result.output
    relative = malformed.relative_to(world.kb_path("test-kb"))
    assert f"    • {relative}" in result.output.splitlines()
    assert str(world.kb_path("test-kb")) not in result.output


def test_kb_commit_file_path_stays_literal(cli_world, narrow_terminal):
    world = cli_world()
    kb_path = world.kb_path("test-kb")
    for args in (
        ("init",),
        ("config", "user.name", "Synthetic Test"),
        ("config", "user.email", "test@example.invalid"),
    ):
        subprocess.run(["git", *args], cwd=kb_path, check=True, capture_output=True, timeout=30)
    filename = (Path("long-folder-" * 9) / "[draft] changed note.md").as_posix()
    assert len(filename) > 120
    content = "---\nid: note\ntitle: Note\ntype: note\n---\nBody\n"
    world.add_entry("test-kb", content, filename=filename)

    result = world.cli("kb", "commit", "-k", "test-kb", "-m", "Synthetic path test")

    assert result.exit_code == 0, result.output
    assert f"  {filename}" in result.output.splitlines()
    committed = subprocess.run(
        ["git", "show", f"HEAD:{filename}"],
        cwd=kb_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert committed.stdout == content


def test_db_restore_backup_path_stays_literal(cli_world, tmp_path, narrow_terminal):
    world = cli_world()
    backup = tmp_path / ("long-folder-" * 8) / "[old] restore copy.db"
    backup.parent.mkdir(parents=True)
    with closing(sqlite3.connect(backup)) as connection:
        connection.execute("CREATE TABLE proof (value TEXT)")
        connection.execute("INSERT INTO proof VALUES ('restored')")
        connection.commit()
    # Restore owns the database file; close the world's handle before replacing it.
    world.db.close()

    result = world.cli("db", "restore", str(backup), "--force")

    assert result.exit_code == 0, result.output
    assert f"Restored database from {backup}" in result.output.splitlines()
    with closing(sqlite3.connect(world.config.settings.index_path)) as connection:
        assert connection.execute("SELECT value FROM proof").fetchone() == ("restored",)


@pytest.mark.control(reason="Preserve literal helper output and surrounding label styles")
@pytest.mark.parametrize("path", [Path("[x]"), "C:\\", Path("folder with spaces"), "/a/b[/dim]/c"])
def test_path_line_preserves_literal_characters_and_label_styles(path, narrow_terminal):
    from pyrite.cli.output import cli_console, path_line

    console = cli_console()
    line = path_line("[bold]Path:[/bold] ", path, " [dim](saved)[/dim]", path_style="cyan")
    expected = f"Path: {path} (saved)"

    assert line.plain == expected
    with console.capture() as captured:
        console.print(line)
    assert captured.get() == expected + "\n"
    assert line.get_style_at_offset(console, 0).bold
    assert line.get_style_at_offset(console, len("Path: ")).color.name == "cyan"
    assert line.get_style_at_offset(console, len(expected) - 1).dim


@pytest.mark.control(reason="Preserve the literal Windows root path while fixing wrapping")
def test_admin_kb_path_preserves_trailing_backslash(cli_world):
    from pyrite.admin_cli import app
    from pyrite.config import save_config

    world = cli_world()
    # Only list this synthetic config value; never read or index the drive.
    path = Path("C:\\").absolute()
    world.config.knowledge_bases[0].path = path
    with world.pointed():
        save_config(world.config)

    result = world.cli("kb", "list", app=app)

    assert result.exit_code == 0, result.output
    assert f"    Path: {path}" in result.output.splitlines()


def test_cli_consoles_share_the_path_wrapping_policy():
    root = Path(__file__).resolve().parents[1]
    sources = [*root.glob("pyrite/cli/**/*.py"), *root.glob("pyrite/*_cli.py")]
    sources += [root / "pyrite/utils/errors.py", *root.glob("extensions/*/src/**/*.py")]
    stray = []
    for path in sources:
        if path == root / "pyrite/cli/output.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        console_names = {"Console"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "rich.console":
                console_names.update(
                    alias.asname or alias.name for alias in node.names if alias.name == "Console"
                )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in console_names
                or isinstance(node.func, ast.Attribute)
                and node.func.attr == "Console"
            ):
                stray.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not stray, "Use cli_console() so paths stay on one line: " + ", ".join(stray)


@pytest.mark.control(reason="Soft wrapping must preserve Rich's table layout at terminal width")
def test_tables_still_follow_terminal_width(narrow_terminal):
    from rich.console import Console
    from rich.table import Table

    from pyrite.cli.export_commands import console as shared

    table = Table("KB", "Path")
    table.add_row("notes", "/long-folder" * 12)
    original = Console(width=shared.width)
    with original.capture() as expected:
        original.print(table)
    with shared.capture() as actual:
        shared.print(table)
    assert actual.get() == expected.get()

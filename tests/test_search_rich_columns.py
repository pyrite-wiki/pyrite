"""Regression tests for search --format rich ID column and markup (#1519).

Verifies that:
- `pyrite search --format rich` includes an ID column.
- Long IDs (30+ chars) are preserved without truncation even at COLUMNS=80.
- Raw `<mark>` tags from FTS5 snippets are replaced with Rich bold markup in rich output.
- `pyrite search --files --format rich` retains the ID column with matching settings.
- `pyrite search --format json` preserves raw `<mark>` tags untouched.
- Brackets like `[[key-store]]` and `array[/end]` in snippets or titles are escaped and rendered as written without raising MarkupError.
"""

from __future__ import annotations

import json


def test_search_rich_preserves_id_and_replaces_mark_tags(cli_world, console_width, monkeypatch):
    """Rich search output shows the full ID, keeps matched word, and replaces <mark> with bold."""
    console_width(80)
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("TERM", "xterm-256color")

    world = cli_world()
    expected_id = "long-search-entry-identifier-30"
    world.add_entry(
        "test-kb",
        text=(
            "---\n"
            f"id: {expected_id}\n"
            "type: note\n"
            "title: Search Feature Design\n"
            "---\n"
            "\n"
            "In-memory key/value store implementation details for cache.\n"
        ),
    )

    result = world.cli("search", "store", "-k", "test-kb", "--format", "rich")
    assert result.exit_code == 0, result.output

    # ID column exists and the full 30+ character ID is visible
    assert "ID" in result.output
    assert expected_id in result.output
    lines_with_id = [line for line in result.output.splitlines() if expected_id in line]
    assert len(lines_with_id) == 1

    # Raw <mark> and </mark> tags are not present in rich output, and matched term is present
    assert "<mark>" not in result.output
    assert "</mark>" not in result.output
    assert "store" in result.output


def test_search_rich_escapes_brackets_in_snippet_and_title(cli_world):
    """Snippet and title with wikilinks and brackets survive Rich markup parsing."""
    world = cli_world()
    world.add_entry(
        "test-kb",
        text=(
            "---\n"
            "id: bracket-markup-test-entry\n"
            "type: note\n"
            "title: Notice with [brackets] in title\n"
            "---\n"
            "\n"
            "See [[key-store]] and array[/end] next to the search term store.\n"
        ),
    )

    result = world.cli("search", "store", "-k", "test-kb", "--format", "rich")
    assert result.exit_code == 0, result.output
    assert "[[key-store]]" in result.output
    assert "array[/end]" in result.output
    assert "[brackets]" in result.output
    assert "store" in result.output


def test_search_rich_escapes_backslash_before_highlight(cli_world):
    """Backslash preceding highlight term does not escape markup or crash Rich."""
    world = cli_world()
    world.add_entry(
        "test-kb",
        text=(
            "---\n"
            "id: backslash-highlight-entry\n"
            "type: note\n"
            "title: Windows Path Note\n"
            "---\n"
            "\n"
            "Configuration path is C:\\store location.\n"
        ),
    )

    result = world.cli("search", "store", "-k", "test-kb", "--format", "rich")
    assert result.exit_code == 0, result.output
    assert r"C:\store" in result.output


def test_search_files_rich_preserves_id_at_80_columns(cli_world, console_width, monkeypatch):
    """File-search fallback preserves full ID without truncation at 80 columns."""
    console_width(80)
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("TERM", "xterm-256color")

    world = cli_world()
    expected_id = "long-files-fallback-identifier-30"
    world.add_entry(
        "test-kb",
        text=(
            "---\n"
            f"id: {expected_id}\n"
            "type: note\n"
            "title: Fallback Search Entry\n"
            "---\n"
            "\n"
            "Direct file scan search test content.\n"
        ),
    )

    result = world.cli("search", "Fallback", "-k", "test-kb", "--files", "--format", "rich")
    assert result.exit_code == 0, result.output

    assert "ID" in result.output
    assert expected_id in result.output
    lines_with_id = [line for line in result.output.splitlines() if expected_id in line]
    assert len(lines_with_id) == 1


def test_search_json_preserves_raw_mark_snippet(cli_world):
    """JSON search preserves raw <mark> tags in snippets."""
    world = cli_world()
    world.add_entry(
        "test-kb",
        text=(
            "---\n"
            "id: mark-json-preservation-note-30\n"
            "type: note\n"
            "title: JSON Snippet Test\n"
            "---\n"
            "\n"
            "Testing raw mark tags in snippet result matching.\n"
        ),
    )

    result = world.cli("search", "matching", "-k", "test-kb", "--format", "json")
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert data["count"] >= 1
    snippet = data["results"][0]["snippet"]
    assert "<mark>" in snippet
    assert "</mark>" in snippet

"""`--type` takes more than one value and `--exclude-type` exists on search (#627).

Reproduced from the 2026-10-19 groom of #627: Typer kept only the last value
of a single-valued `--type` option, so `--type note --type event` silently
searched only `event`, and `--exclude-type` did not exist. Both flags ride
SearchService's filter set, which every leg honours (#56), so the flags are
proved at the service too, and on the `--files` fallback, which filters the
same types it prints.
"""

from __future__ import annotations

import json

import pytest

from pyrite.services.search_service import SearchService


def _seed(world):
    """One note and one event that mention widget; one event without."""
    world.add_entry(
        "test-kb",
        text=(
            "---\n"
            "id: widget-note\n"
            "type: note\n"
            "title: Widget design note\n"
            "---\n"
            "\n"
            "The widget is drafted here.\n"
        ),
    )
    world.add_entry(
        "test-kb",
        text=(
            "---\n"
            "id: ev-20261019-widget-review\n"
            "type: event\n"
            "date: '2026-10-19'\n"
            "title: Widget review\n"
            "---\n"
            "\n"
            "The widget review happened and it passed, though the widget had bugs.\n"
        ),
    )


def test_a_repeated_type_survives(cli_world):
    """`--type note --type event` searches both types, not just the last."""
    world = cli_world()
    _seed(world)
    result = world.cli(
        "search", "widget", "-k", "test-kb", "--type", "note", "--type", "event", "--format", "json"
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    types = {r["entry_type"] for r in data["results"]}
    assert types == {"note", "event"}, types


def test_an_excluded_type_leaves_the_results(cli_world):
    """`--exclude-type event` keeps the note and drops every event row."""
    world = cli_world()
    _seed(world)
    result = world.cli(
        "search", "widget", "-k", "test-kb", "--exclude-type", "event", "--format", "json"
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    types = {r["entry_type"] for r in data["results"]}
    assert "event" not in types, data["results"]
    assert "note" in types, data["results"]


def test_the_files_fallback_honours_the_same_flags(cli_world):
    """`--files` lists only the rows the type filters accept."""
    world = cli_world()
    _seed(world)
    kept = world.cli(
        "search",
        "widget",
        "-k",
        "test-kb",
        "--files",
        "--exclude-type",
        "event",
        "--format",
        "json",
    )
    mixed = world.cli(
        "search",
        "widget",
        "-k",
        "test-kb",
        "--files",
        "--type",
        "note",
        "--type",
        "event",
        "--format",
        "json",
    )
    assert kept.exit_code == 0, kept.output
    assert "Widget design note" in kept.output
    assert "Widget review" not in kept.output
    assert mixed.exit_code == 0, mixed.output
    assert "Widget review" in mixed.output


def test_the_service_legs_typed_filter_together(cli_world):
    """SearchService.search takes the multi/exclusion filters on the keyword leg."""
    world = cli_world()
    _seed(world)
    svc = SearchService(world.db, settings=world.config.settings)
    both = svc.search("widget", kb_name="test-kb", entry_types=["note", "event"])
    assert {r["entry_type"] for r in both} == {"note", "event"}

    contradicted = svc.search(
        "widget", kb_name="test-kb", entry_types=["note"], exclude_types=["note"]
    )
    assert contradicted == [], contradicted

    excluded = svc.search("widget", kb_name="test-kb", exclude_types=["note"])
    assert {r["entry_type"] for r in excluded} == {"event"}, excluded

    legacy = svc.search("widget", kb_name="test-kb", entry_type="note")
    assert {r["entry_type"] for r in legacy} == {"note"}, legacy


@pytest.mark.control(reason="control: keyword search without -k already spans readable KBs")
def test_keyword_search_without_a_kb_keeps_both_types(cli_world):
    world = cli_world()
    _seed(world)
    result = world.cli("search", "widget", "--format", "json")
    assert result.exit_code == 0, result.output
    assert {r["entry_type"] for r in json.loads(result.stdout)["results"]} == {"note", "event"}


def test_an_unknown_excluded_type_without_a_kb_changes_nothing(cli_world):
    world = cli_world()
    _seed(world)
    result = world.cli("search", "widget", "--exclude-type", "nosuchtype", "--format", "json")
    assert result.exit_code == 0, result.output
    assert {r["entry_type"] for r in json.loads(result.stdout)["results"]} == {"note", "event"}

"""Typed updates use the same model rules as creates before touching a file."""

import json

import pytest

from pyrite.config import KBConfig
from pyrite.utils.yaml import load_yaml
from tests._world import build_world


@pytest.fixture
def world(tmp_path):
    result = build_world(tmp_path, kbs=[KBConfig(name="events", path=tmp_path / "kb")])
    result.add_entry(
        "events",
        "---\nid: launch\ntype: event\ntitle: Launch\ndate: 2026-10-01\nimportance: 5\n---\n\nBody.\n",
    )
    try:
        yield result
    finally:
        result.close()


def update(world, surface, field, value):
    if surface == "cli":
        result = world.cli("update", "launch", "-k", "events", "-f", f"{field}={value}")
        return result.exit_code == 0, json.loads(result.stdout)
    if surface == "mcp":
        result = world.mcp()._dispatch_tool(
            "kb_update", {"kb_name": "events", "entry_id": "launch", field: value}
        )
        return "error" not in result, result
    if surface == "put":
        result = world.client.put(
            "/api/entries/launch", json={"kb": "events", "metadata": {field: value}}
        )
    else:
        result = world.client.patch(
            "/api/entries/launch", json={"kb": "events", "field": field, "value": value}
        )
    return result.status_code == 200, result.json()


@pytest.mark.parametrize("surface", ["cli", "mcp", "put", "patch"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("date", "nextweek"),
        ("date", "1990-13-45"),
        ("date", ""),
        ("date", 20261001),
        ("importance", 99),
        ("importance", 0),
        ("importance", "high"),
    ],
)
def test_bad_typed_updates_are_refused_without_changing_the_file(world, surface, field, value):
    path = world.kb_path("events") / "launch.md"
    before = path.read_bytes()
    succeeded, body = update(world, surface, field, value)

    assert not succeeded, body
    code = body["detail"]["code"] if "detail" in body else body["error_code"]
    assert code == "VALIDATION_FAILED", body
    assert path.read_bytes() == before
    row = world.db.get_entry("launch", "events")
    assert row["date"] == "2026-10-01" and row["importance"] == 5


@pytest.mark.parametrize("surface", ["cli", "mcp", "put", "patch"])
def test_unrelated_edits_warn_about_a_handwritten_date_and_allow_repair(world, surface):
    path = world.kb_path("events") / "launch.md"
    path.write_text(path.read_text().replace("2026-10-01", "June 1990"))
    world.index()

    succeeded, body = update(world, surface, "title", "Renamed")
    assert succeeded, body
    assert any(w["rule"] == "model_validation" for w in body["warnings"]), body
    assert "date: June 1990" in path.read_text()

    before = path.read_bytes()
    stat = path.stat()
    succeeded, body = update(world, surface, "title", "Renamed")
    assert succeeded, body
    assert body["warnings"], body
    assert path.read_bytes() == before and path.stat().st_mtime_ns == stat.st_mtime_ns

    succeeded, body = update(world, surface, "date", "1990-06-01")
    assert succeeded, body
    assert not any(w["rule"] == "model_validation" for w in body.get("warnings", [])), body
    fields = load_yaml(path.read_text().split("---", 2)[1])
    assert str(fields["date"]) == "1990-06-01"


@pytest.mark.parametrize(
    ("field", "before", "after"), [("date", "June 1990", "nextweek"), ("importance", "99", "98")]
)
def test_changing_one_invalid_value_to_another_is_refused(world, field, before, after):
    path = world.kb_path("events") / "launch.md"
    original = "2026-10-01" if field == "date" else "5"
    path.write_text(path.read_text().replace(f"{field}: {original}", f"{field}: {before}"))
    world.index()
    contents = path.read_bytes()
    succeeded, body = update(world, "mcp", field, after)
    assert not succeeded, body
    assert body["error_code"] == "VALIDATION_FAILED"
    assert path.read_bytes() == contents


def test_a_plugin_event_uses_its_inherited_model_validation(world):
    path = world.kb_path("events") / "launch.md"
    path.write_text(path.read_text().replace("type: event", "type: investigation_event"))
    world.index()
    contents = path.read_bytes()
    succeeded, body = update(world, "mcp", "date", "nextweek")
    assert not succeeded, body
    assert body["error_code"] == "VALIDATION_FAILED"
    assert path.read_bytes() == contents


@pytest.mark.control(reason="create remains the strict oracle for an invalid event date")
def test_create_still_refuses_bad_dates(world):
    result = world.mcp()._dispatch_tool(
        "kb_create",
        {"kb_name": "events", "entry_type": "event", "title": "Invalid", "date": "nextweek"},
    )
    assert result["error_code"] == "VALIDATION_FAILED", result


@pytest.mark.control(reason="valid date edits still succeed, including YAML's date scalars")
@pytest.mark.parametrize("surface", ["cli", "mcp", "put", "patch"])
def test_valid_date_edits_remain_valid(world, surface):
    succeeded, body = update(world, surface, "date", "2026-10-02")
    assert succeeded, body
    assert not body.get("warnings"), body

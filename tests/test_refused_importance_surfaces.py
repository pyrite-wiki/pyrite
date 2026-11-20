"""Pin refused importance warnings and QA findings on every requested surface."""

from __future__ import annotations

import json

import pytest

from pyrite.config import KBConfig
from tests.test_refused_importance_never_silent import _note
from tests._world import build_world

KB = "k"
ENTRY = "n"
VALUES = [
    "7.5",
    "high",
    ".nan",
    pytest.param(
        None,
        marks=pytest.mark.control(reason="negative control: a missing key was not warned"),
    ),
    pytest.param(
        "5.0",
        marks=pytest.mark.control(reason="negative control: an accepted value was not warned"),
    ),
]


@pytest.fixture
def make_surface_world(tmp_path):
    worlds = []

    def make(value: str | None):
        world = build_world(
            tmp_path / f"world-{len(worlds)}",
            kbs=[KBConfig(name=KB, path=tmp_path / f"kb-{len(worlds)}")],
            rest=True,
        )
        worlds.append(world)
        world.add_entry(KB, _note(value))
        return world

    yield make
    for world in worlds:
        world.close()


def _warnings(entry: dict) -> list[str]:
    return entry.get("entry_warnings", [])


def _assert_warning(entry: dict, value: str | None) -> None:
    warnings = _warnings(entry)
    if value in {"7.5", "high", ".nan"}:
        assert len(warnings) == 1 and "whole number 1-10" in warnings[0], entry
    else:
        assert not warnings, entry


@pytest.mark.parametrize("value", VALUES)
def test_get_keeps_importance_warning_on_rest_mcp_and_cli(make_surface_world, value):
    world = make_surface_world(value)

    rest_response = world.client.get(f"/api/entries/{ENTRY}", params={"kb": KB})
    assert rest_response.status_code == 200, rest_response.text
    rest = rest_response.json()
    assert rest["id"] == ENTRY
    _assert_warning(rest, value)

    mcp = world.mcp()._dispatch_tool("kb_get", {"kb_name": KB, "entry_id": ENTRY})
    assert "error_code" not in mcp, mcp
    _assert_warning(mcp["entry"], value)

    cli = world.cli(
        "get",
        ENTRY,
        "-k",
        KB,
        "--fields",
        "importance",
        "--format",
        "json",
    )
    assert cli.exit_code == 0, cli.output
    _assert_warning(json.loads(cli.stdout), value)


@pytest.mark.parametrize("value", VALUES)
def test_single_entry_qa_reports_importance_range_on_rest_and_mcp(make_surface_world, value):
    world = make_surface_world(value)

    rest_response = world.client.get(f"/api/qa/validate/{ENTRY}", params={"kb": KB})
    assert rest_response.status_code == 200, rest_response.text
    rest = rest_response.json()
    mcp = world.mcp()._dispatch_tool("kb_qa_validate", {"kb_name": KB, "entry_id": ENTRY})
    assert "error_code" not in mcp, mcp

    for result in (rest, mcp):
        findings = [issue for issue in result["issues"] if issue["field"] == "importance"]
        if value in {"7.5", "high", ".nan"}:
            assert len(findings) == 1, result
            assert findings[0]["rule"] == "importance_range", result
        else:
            assert findings == [], result

"""An optional epic filter resolves only among KBs the caller can read."""

import json

import pytest

from pyrite.config import KBConfig
from tests._world import build_world


@pytest.fixture
def world(tmp_path):
    result = build_world(
        tmp_path,
        kbs=[KBConfig(name=n, path=tmp_path / n, kb_type="software") for n in ("alpha", "beta")],
        rest=False,
    )
    for kb in ("alpha", "beta"):
        result.add_entry(
            kb,
            "---\nid: epic\ntype: backlog_item\ntitle: Epic\nkind: epic\n"
            "links:\n  - target: child\n    relation: has_subtask\n---\n\nAn epic.\n",
        )
        result.add_entry(
            kb, "---\nid: child\ntype: backlog_item\ntitle: Child\nkind: feature\n---\n\nWork.\n"
        )
    try:
        yield result
    finally:
        result.close()


@pytest.mark.parametrize("kb", [None, "", " "])
def test_epic_resolves_in_the_only_readable_kb(world, kb):
    args = {"epic": "epic"}
    if kb is not None:
        args["kb_name"] = kb
    result = world.mcp()._dispatch_tool("sw_backlog", args, readable_kbs={"alpha"})

    assert "error" not in result, result
    assert [(i["kb_name"], i["id"]) for i in result["items"]] == [("alpha", "child")]


@pytest.mark.parametrize(("epic", "code"), [("epic", "AMBIGUOUS"), ("absent", "ENTRY_NOT_FOUND")])
def test_epic_filter_refuses_an_ambiguous_or_missing_id(world, epic, code):
    result = world.mcp()._dispatch_tool("sw_backlog", {"epic": epic})

    assert result["error_code"] == code, result
    if code == "AMBIGUOUS":
        assert result["candidate_kbs"] == ["alpha", "beta"]


@pytest.mark.control(reason="listing without an epic and with a named KB keeps working")
def test_no_epic_and_named_kb_keep_their_scope(world):
    server = world.mcp()
    all_items = server._dispatch_tool("sw_backlog", {}, readable_kbs={"alpha"})
    assert {i["id"] for i in all_items["items"]} == {"epic", "child"}
    named = server._dispatch_tool("sw_backlog", {"epic": "epic", "kb_name": "beta"})
    assert [(i["kb_name"], i["id"]) for i in named["items"]] == [("beta", "child")]


@pytest.mark.parametrize("fmt", ["json", "rich"])
@pytest.mark.parametrize(
    ("epic", "kb", "code"),
    [("epic", None, "AMBIGUOUS"), ("absent", None, "NOT_FOUND"), ("absent", "alpha", "NOT_FOUND")],
)
def test_cli_epic_refusals_are_formatted(world, fmt, epic, kb, code):
    args = ["sw", "backlog", "--epic", epic, "--format", fmt]
    if kb:
        args += ["-k", kb]
    result = world.cli(*args)

    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    if fmt == "json":
        refusal = json.loads(result.stdout)
        assert refusal["error_code"] == code
        if code == "AMBIGUOUS":
            assert refusal["candidate_kbs"] == ["alpha", "beta"]
    else:
        assert epic in result.output


def test_an_epic_in_an_unreadable_kb_is_not_disclosed(world):
    world.add_entry(
        "beta", "---\nid: private-epic\ntype: backlog_item\ntitle: Private\nkind: epic\n---\n"
    )
    result = world.mcp()._dispatch_tool(
        "sw_backlog", {"epic": "private-epic"}, readable_kbs={"alpha"}
    )

    assert result["error_code"] == "ENTRY_NOT_FOUND", result
    assert "beta" not in json.dumps(result)


def test_epic_scope_also_limits_grouped_items(world):
    result = world.mcp()._dispatch_tool(
        "sw_backlog", {"epic": "epic", "group_by": "epic"}, readable_kbs={"alpha"}
    )
    items = [item for group in result["groups"] for item in group["items"]]
    assert [(item["kb_name"], item["id"]) for item in items] == [("alpha", "child")]


@pytest.mark.control(reason="a named KB with a valid epic already lists its child")
def test_cli_with_a_named_epic_keeps_working(world):
    result = world.cli("sw", "backlog", "--epic", "epic", "-k", "alpha")
    assert result.exit_code == 0, result.output
    assert [(item["kb_name"], item["id"]) for item in json.loads(result.stdout)] == [
        ("alpha", "child")
    ]

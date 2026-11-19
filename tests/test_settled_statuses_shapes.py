"""A wrong-shaped `settled_statuses` never stops a command, is never read as
letters or keys, and is reported by `qa validate` (#934, fix round 2)."""

from __future__ import annotations

import json

import pytest

from pyrite.schema import KBSchema
from pyrite.schema.core_types import settled_statuses

WRONG = {
    "bare-string": "shipped",
    "number": 5,
    "boolean": True,
    "mapping": {"a": 1},
    "non-string-item": ["shipped", 3],
    "nested-list": [["shipped"]],
    "blank-item": ["shipped", " "],
}
YAML_WRONG = {
    "bare-string": "shipped",
    "number": "5",
    "boolean": "true",
    "mapping": "{a: 1}",
    "non-string-item": "[shipped, 3]",
    "nested-list": "[[shipped]]",
    "blank-item": "[shipped, ' ']",
}


def _schema(value):
    return KBSchema.from_dict({"name": "k", "types": {"ticket": {"settled_statuses": value}}})


@pytest.mark.parametrize("shape", sorted(WRONG))
def test_a_wrong_shape_does_not_raise_out_of_the_schema_load(shape):
    _schema(WRONG[shape])


@pytest.mark.parametrize("shape", sorted(WRONG))
def test_a_wrong_shape_is_not_declared_and_never_read_as_letters_or_keys(shape):
    assert settled_statuses("ticket", _schema(WRONG[shape])) == frozenset()


def test_a_list_of_names_is_read_lowercased_and_stripped():
    assert settled_statuses("ticket", _schema([" Shipped", "WONT_DO"])) == {"shipped", "wont_do"}


def test_null_and_absent_are_not_declared():
    assert settled_statuses("backlog_item", _schema(None)) == settled_statuses("backlog_item")
    absent = KBSchema.from_dict({"name": "k", "types": {"ticket": {}}})
    assert settled_statuses("ticket", absent) == frozenset()
    assert settled_statuses("backlog_item", absent) >= {"done", "retired"}


def test_an_empty_list_in_kb_yaml_clears_what_a_plugin_declares():
    """kb.yaml overrides a plugin, and [] is a declaration: this KB's backlog has
    no settled statuses."""
    assert settled_statuses("backlog_item") >= {"retired"}
    assert settled_statuses("backlog_item", _schema_for("backlog_item", [])) == frozenset()


def _schema_for(type_name, value):
    return KBSchema.from_dict({"name": "k", "types": {type_name: {"settled_statuses": value}}})


@pytest.mark.parametrize("shape", sorted(WRONG))
def test_a_wrong_plugin_value_is_not_declared(shape, monkeypatch):
    from pyrite.plugins import get_registry

    registry = get_registry()
    real = registry.get_all_type_metadata
    monkeypatch.setattr(
        registry,
        "get_all_type_metadata",
        lambda: {**real(), "ticket": {"settled_statuses": WRONG[shape]}},
    )

    assert settled_statuses("ticket") == frozenset()


def test_the_core_task_declaration_is_a_list_of_names():
    assert settled_statuses("task") == {"done", "cancelled"}


@pytest.mark.parametrize("shape", sorted(WRONG))
def test_cli_a_wrong_shape_stops_nothing_and_qa_validate_names_the_key(cli_world, shape):
    world = cli_world()
    (world.kb_path("test-kb") / "kb.yaml").write_text(
        f"name: test-kb\ntypes:\n  note:\n    description: notes\n  ticket:\n    settled_statuses: {YAML_WRONG[shape]}\n",
        encoding="utf-8",
    )

    made = world.cli("create", "-k", "test-kb", "-t", "note", "--title", "Kestrel")
    assert made.exit_code == 0, made.output
    dups = world.cli("qa", "duplicates", "-t", "note", "--format", "json")
    assert dups.exit_code == 0, dups.output
    world.index()
    result = world.cli("qa", "validate", "test-kb", "--format", "json")

    issues = json.loads(result.stdout)["issues"]
    found = [i for i in issues if i.get("field") == "types.ticket.settled_statuses"]
    assert len(found) == 1, issues
    assert found[0]["rule"] == "config_error"
    assert found[0]["severity"] == "error"
    assert found[0]["entry_id"] == ""
    assert found[0]["kb_name"] == "test-kb"
    assert (
        "kb.yaml types.ticket.settled_statuses must be a list of status names"
        in found[0]["message"]
    )


def test_qa_validate_reports_a_settled_status_outside_the_declared_vocabulary(cli_world):
    world = cli_world()
    (world.kb_path("test-kb") / "kb.yaml").write_text(
        "name: test-kb\ntypes:\n  ticket:\n    fields:\n      status:\n"
        "        type: select\n        options: [open, shipped]\n"
        "    settled_statuses: [shipped, retired]\n",
        encoding="utf-8",
    )
    world.index()

    issues = json.loads(world.cli("qa", "validate", "test-kb", "--format", "json").stdout)["issues"]

    found = [i for i in issues if i.get("field") == "types.ticket.settled_statuses"]
    assert len(found) == 1, issues
    assert found[0]["rule"] == "config_error"
    assert (
        "'retired'" in found[0]["message"]
        and "shipped" not in found[0]["message"].split("not in")[0]
    )


def test_qa_validate_is_quiet_for_a_well_formed_declaration(cli_world):
    world = cli_world()
    (world.kb_path("test-kb") / "kb.yaml").write_text(
        "name: test-kb\ntypes:\n  ticket:\n    fields:\n      status:\n"
        "        type: select\n        options: [open, shipped]\n"
        "    settled_statuses: [shipped]\n",
        encoding="utf-8",
    )
    world.index()

    issues = json.loads(world.cli("qa", "validate", "test-kb", "--format", "json").stdout)["issues"]

    assert not [i for i in issues if i.get("field") == "types.ticket.settled_statuses"]


# --- the layer below stands (#934, fix round 3) --------------------------------
# What each layer declares, written by hand (never read from the code under test):
#   (a) a type with a core lower layer: core `task`
#   (b) a type with a plugin lower layer: software-kb `backlog_item`
#   (c) a type with none: `ticket`
LOWER_LAYER = {
    "task": frozenset({"done", "cancelled"}),
    "backlog_item": frozenset({"done", "retired", "superseded", "wont_do"}),
    "ticket": frozenset(),
}


@pytest.mark.parametrize("type_name", sorted(LOWER_LAYER))
@pytest.mark.parametrize("shape", sorted(WRONG))
def test_a_wrong_shape_in_kb_yaml_leaves_the_lower_layer_in_force(type_name, shape):
    schema = _schema_for(type_name, WRONG[shape])

    assert settled_statuses(type_name, schema) == LOWER_LAYER[type_name]


@pytest.mark.parametrize("type_name", sorted(LOWER_LAYER))
def test_an_empty_list_clears_and_null_or_absent_change_nothing(type_name):
    assert settled_statuses(type_name, _schema_for(type_name, [])) == frozenset()
    assert settled_statuses(type_name, _schema_for(type_name, None)) == LOWER_LAYER[type_name]
    absent = KBSchema.from_dict({"name": "k", "types": {type_name: {}}})
    assert settled_statuses(type_name, absent) == LOWER_LAYER[type_name]


@pytest.mark.parametrize("type_name", ["task", "backlog_item"])
@pytest.mark.parametrize("shape", sorted(WRONG))
def test_cli_a_wrong_shape_keeps_a_settled_entry_out_of_the_scan(cli_world, type_name, shape):
    """Through the real command: kb.yaml carries the wrong shape for the type, and
    an entry in a lower-layer settled status still stays out of the default scan."""
    world = cli_world()
    folder = world.kb_path("test-kb")
    (folder / "kb.yaml").write_text(
        f"name: test-kb\ntypes:\n  {type_name}:\n    settled_statuses: {YAML_WRONG[shape]}\n",
        encoding="utf-8",
    )
    settled = {"task": "done", "backlog_item": "retired"}[type_name]
    live = {"task": "open", "backlog_item": "proposed"}[type_name]
    for entry_id, status in (("same-a", settled), ("same-b", live), ("same-c", live)):
        (folder / f"{entry_id}.md").write_text(
            f"---\nid: {entry_id}\ntype: {type_name}\ntitle: Same Title\nstatus: {status}\n---\nBody.\n",
            encoding="utf-8",
        )
    world.index()

    result = world.cli("qa", "duplicates", "-t", type_name, "--format", "json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    ids = {side["id"] for pair in payload["pairs"] for side in (pair["a"], pair["b"])}
    assert ids == {"same-b", "same-c"}, payload

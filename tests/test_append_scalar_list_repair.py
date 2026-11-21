"""A refused append names an executable repair and never edits the file (#1760)."""

import json
import shlex

import pytest
import yaml

from pyrite.config import KBConfig
from tests._world import build_world


@pytest.fixture(params=["cli", "mcp", "rest"])
def surface(request):
    return request.param


def _world(tmp_path, declaration="list"):
    root = tmp_path / "kb"
    root.mkdir()
    schema = {"name": "lists", "types": {"note": {"fields": {}}}}
    if declaration:
        schema["types"]["note"]["fields"]["labels"] = {"type": declaration}
    (root / "kb.yaml").write_text(yaml.safe_dump(schema), encoding="utf-8")
    return build_world(tmp_path, kbs=[KBConfig(name="lists", path=root)])


def _seed(world, value="firmware", *, nested=False, absent=False):
    fields = {} if absent else {"labels": value}
    if nested:
        fields = {"metadata": fields}
    text = yaml.safe_dump({"id": "b1", "title": "Board", "type": "note", **fields})
    return world.add_entry("lists", f"---\n{text}---\n\nKeep this body.\n")


def _append(world, surface, field="labels"):
    if surface == "cli":
        result = world.cli(
            "update", "b1", "-k", "lists", "--append", f"{field}=second", "--format", "json"
        )
        return result.exit_code == 0, json.loads(result.stdout), result.exit_code
    if surface == "mcp":
        result = world.mcp(tier="write")._dispatch_tool(
            "kb_update", {"kb_name": "lists", "entry_id": "b1", "append": {field: "second"}}
        )
        return not result.get("error"), result, None
    result = world.client.patch(
        "/api/entries/b1", json={"kb": "lists", "field": field, "value": "second", "op": "append"}
    )
    return result.status_code == 200, result.json(), result.status_code


@pytest.mark.parametrize("declaration", ["list", "multi-select", "tags"])
@pytest.mark.parametrize("nested", [False, True])
def test_a_scalar_append_names_the_repair_without_writing(tmp_path, surface, declaration, nested):
    world = _world(tmp_path, declaration)
    try:
        path = _seed(world, nested=nested)
        before = path.read_bytes()
        ok, result, status = _append(world, surface)
        assert not ok, result
        if surface == "cli":
            assert status == 1
        message = json.dumps(result)
        assert "set it as a list first" in message, result
        assert "firmware" in message and "labels=" in message, result
        assert path.read_bytes() == before
    finally:
        world.close()


@pytest.mark.parametrize("value", ["firmware", "City A, Inc.", 'owner\'s "board" $(echo x)'])
def test_the_suggested_command_preserves_the_value_and_allows_an_append(tmp_path, value):
    world = _world(tmp_path)
    try:
        path = _seed(world, value)
        ok, result, _ = _append(world, "cli")
        assert not ok, result
        command = shlex.split(result["suggestion"])
        assert command[0] == "pyrite"
        repair = world.cli(*command[1:], "--format", "json")
        assert repair.exit_code == 0, repair.output
        assert _append(world, "cli")[0]
        stored = yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])
        assert stored["labels"] == [value, "second"]
    finally:
        world.close()


@pytest.mark.control(reason="undeclared and text fields keep their generic storage refusal")
@pytest.mark.parametrize("declaration", [None, "text"])
def test_a_scalar_without_a_list_declaration_keeps_the_generic_refusal(
    tmp_path, surface, declaration
):
    world = _world(tmp_path, declaration)
    try:
        path = _seed(world)
        before = path.read_bytes()
        ok, result, _ = _append(world, surface)
        assert not ok, result
        message = json.dumps(result)
        assert "not a list; cannot append" in message, result
        assert "set it as a list first" not in message
        assert path.read_bytes() == before
    finally:
        world.close()


@pytest.mark.control(reason="list and absent fields already accept append")
@pytest.mark.parametrize("absent", [False, True])
def test_existing_and_absent_lists_still_accept_append(tmp_path, surface, absent):
    world = _world(tmp_path)
    try:
        path = _seed(world, ["firmware"], absent=absent)
        ok, result, _ = _append(world, surface)
        assert ok, result
        stored = yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])
        assert stored["labels"] == (["second"] if absent else ["firmware", "second"])
    finally:
        world.close()


@pytest.mark.control(
    reason="an explicit set followed by append already repairs the scalar in one write"
)
def test_set_then_append_in_one_request_still_works(tmp_path):
    world = _world(tmp_path)
    try:
        path = _seed(world)
        result = world.cli(
            "update",
            "b1",
            "-k",
            "lists",
            "-f",
            'labels=["firmware"]',
            "--append",
            "labels=second",
            "--format",
            "json",
        )
        assert result.exit_code == 0, result.output
        stored = yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])
        assert stored["labels"] == ["firmware", "second"]
    finally:
        world.close()

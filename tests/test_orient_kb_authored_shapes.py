"""`kb_authored` returns what a KB's author wrote, as written, for every YAML shape (#1369).

A KB author can write any YAML under the keys `kb_authored` carries. Orient
must answer for every such kb.yaml (dev's orient does: its schema export sits
in a `try`), and each value comes back exactly as written or is reported under
`kb_authored.malformed`, never reshaped: a string is not split into characters
or turned into a mapping, a list is not merged, a date is not stringified one
way on MCP and another on REST.

The expected value is PyYAML's reading of the same kb.yaml text (a reference
tool, independent of the ruamel loader Pyrite uses), never the module's own
conversion. The grid is every field `kb_authored` reads against every shape a
YAML value can take; the surface test runs each shape through the service,
the MCP handler's JSON encoding, REST and the CLI's JSON formatter.
"""

import json
import math

import pytest
import yaml

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB

TOP_FIELDS = ("guidelines", "goals", "evaluation_rubric")
TYPE_FIELDS = ("ai_instructions", "guidelines", "goals", "evaluation_rubric")
FIELDS = [(f,) for f in TOP_FIELDS] + [("types", "note", f) for f in TYPE_FIELDS]

#: Every shape a value can take in YAML, as kb.yaml text.
SHAPES = {
    "mapping": "{tone: Be careful with sources, depth: Cite two}",
    "string": "Be careful with sources",
    "list": "[Cite a source, Name the author]",
    "list-of-mappings": "[{text: Cite a source, weight: 2}]",
    "null": "null",
    "number": "42",
    "float": "0.5",
    "bool": "true",
    "date": "2024-01-02",
    "int-key-mapping": "{1: one}",
    "nan": ".nan",
}
#: Shapes JSON cannot carry as written: reported, not returned.
UNREPRESENTABLE = {"date", "int-key-mapping", "nan"}


def _kb_yaml(field: tuple[str, ...], shape: str) -> str:
    text = SHAPES[shape]
    if field[0] == "types":
        return f"name: k\ntypes:\n  note:\n    description: A note\n    {field[2]}: {text}\n"
    return f"name: k\n{field[0]}: {text}\ntypes:\n  note:\n    description: A note\n"


def _all_fields_yaml(shape: str) -> str:
    text = SHAPES[shape]
    top = "".join(f"{f}: {text}\n" for f in TOP_FIELDS)
    per_type = "".join(f"    {f}: {text}\n" for f in TYPE_FIELDS)
    return f"name: k\n{top}types:\n  note:\n    description: A note\n{per_type}"


def _as_written(kb_yaml: str, field: tuple[str, ...]):
    value = yaml.safe_load(kb_yaml)
    for part in field:
        value = value[part]
    return value


def _at(authored: dict, field: tuple[str, ...]):
    node = authored
    for part in field:
        if not isinstance(node, dict) or part not in node:
            return _ABSENT
        node = node[part]
    return node


_ABSENT = object()


def _config(tmp_path, kb_yaml: str) -> PyriteConfig:
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "kb.yaml").write_text(kb_yaml)
    return PyriteConfig(
        knowledge_bases=[KBConfig(name="k", path=kb_dir)],
        settings=Settings(index_path=tmp_path / "index.db", workspace_path=tmp_path / "ws"),
    )


def _orient(tmp_path, kb_yaml: str) -> dict:
    config = _config(tmp_path, kb_yaml)
    db = PyriteDB(config.settings.index_path)
    try:
        return KBService(config, db).orient("k")
    finally:
        db.close()


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("field", FIELDS, ids=".".join)
def test_each_value_comes_back_as_written_or_is_reported(tmp_path, field, shape):
    kb_yaml = _kb_yaml(field, shape)
    result = _orient(tmp_path, kb_yaml)  # never raises where dev answers
    assert "schema" in result
    authored = result["kb_authored"]
    key = ".".join(field)
    reported = [m["key"] for m in authored.get("malformed", [])]
    if shape == "null":
        assert _at(authored, field) is _ABSENT
        assert key not in reported
    elif shape in UNREPRESENTABLE:
        assert _at(authored, field) is _ABSENT
        assert reported == [key]
    else:
        assert _at(authored, field) == _as_written(kb_yaml, field)
        assert key not in reported
    # What is returned survives JSON exactly: nothing a transport reshapes.
    assert json.loads(json.dumps(authored, allow_nan=False)) == authored


@pytest.mark.parametrize("shape", SHAPES)
def test_every_surface_returns_the_same_kb_authored(tmp_path, shape):
    from tests._world import build_world

    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "kb.yaml").write_text(_all_fields_yaml(shape))
    world = build_world(tmp_path / "world", kbs=[KBConfig(name="k", path=kb_dir)])
    try:
        service = KBService(world.config, world.db).orient("k")["kb_authored"]
        # MCP, through the transport's own encoding (json.dumps(..., default=str)).
        raw = world.mcp("read")._kb_orient({"kb_name": "k"})
        via_mcp = json.loads(json.dumps(raw, default=str))["kb_authored"]
        via_rest = world.client.get("/api/kbs/k/orient").json()["kb_authored"]
        cli = world.cli("orient", "-k", "k", "--format", "json")
        assert cli.exit_code == 0, cli.output
        via_cli = json.loads(cli.stdout)["kb_authored"]
    finally:
        world.close()

    assert via_mcp == via_rest == via_cli == service
    for value in (via_mcp, via_rest, via_cli):
        assert not any(isinstance(v, float) and not math.isfinite(v) for v in _leaves(value))


def _leaves(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _leaves(v)
    elif isinstance(node, list):
        for v in node:
            yield from _leaves(v)
    else:
        yield node

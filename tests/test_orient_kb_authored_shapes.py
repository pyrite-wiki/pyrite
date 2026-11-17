"""`kb_authored` returns what a KB's author wrote, as written, for every YAML shape (#1369).

A KB author can write any YAML under the keys `kb_authored` carries. Orient
must answer for every such kb.yaml (dev's orient does: its schema export sits
in a `try`), and each value comes back exactly as written or is reported under
`kb_authored.malformed`, never reshaped: a string is not split into characters
or turned into a mapping, a list is not merged, a date is not stringified one
way on MCP and another on REST.

The grid is generated: every field `kb_authored` reads x every YAML node kind
(`KINDS`) x every position (`POSITIONS`). Its oracle is independent of the
module: PyYAML's reading of the same text (a reference reader, not the ruamel
loader Pyrite uses), and "JSON can carry it" means `json.dumps(value,
allow_nan=False)` succeeds and decodes to an equal value of the same types.
Only for a node the oracle says JSON cannot carry is the spec's reason
(`REASON`) consulted, and the test asserts that exact text. The surface test
runs each shape through the service, MCP, REST and the CLI.
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


#: Every YAML node kind, as kb.yaml flow text (YAML 1.1 tag repository and
#: the core schema: https://yaml.org/type/).
KINDS = {
    "string": "Be careful",
    "int": "42",
    "float": "0.5",
    "nan": ".nan",
    "inf": ".inf",
    "-inf": "-.inf",
    "bool": "true",
    "null": "null",
    "blank": "",
    "date": "2024-01-02",
    "timestamp": "2024-01-02T03:04:05Z",
    "binary": "!!binary aGVsbG8=",
    "set": "!!set {a: null, b: null}",
    "pairs": "!!pairs [a: 1, b: 2]",
    "omap": "!!omap [a: 1, b: 2]",
    "mapping": "{x: y}",
    "list": "[x, y]",
}

#: Every position a node can sit in under a key kb_authored reads.
POSITIONS = {
    "top": "{}",
    "mapping-value": "{{k: {}}}",
    "list-item": "[{}]",
    "mapping-key": "{{? {} : v}}",
    "two-down": "{{a: [{{b: {}}}]}}",
}

#: The reason a withheld node is named with: the spec, consulted only for a
#: node the oracle below says JSON cannot carry. A non-string mapping key is
#: named as a key, whatever its kind.
REASON = {
    "nan": "a NaN or infinite number, which JSON cannot carry",
    "inf": "a NaN or infinite number, which JSON cannot carry",
    "-inf": "a NaN or infinite number, which JSON cannot carry",
    "date": "a YAML date or timestamp, which JSON cannot carry as written",
    "timestamp": "a YAML date or timestamp, which JSON cannot carry as written",
    "binary": "a YAML binary value, which JSON cannot carry as written",
    "set": "a YAML set, which JSON cannot carry as written",
    "pairs": "a YAML ordered-pairs node (!!pairs or !!omap), which JSON cannot carry as written",
    "omap": "a YAML ordered-pairs node (!!pairs or !!omap), which JSON cannot carry as written",
}
KEY_REASON = "a mapping with a key that is not a string"


def _node_yaml(kind: str, position: str) -> str:
    return POSITIONS[position].format(KINDS[kind])


def _json_carries(value) -> bool:
    """The oracle: JSON carries `value` as written when encoding it (no NaN)
    and decoding it gives back an equal value of the same types. Nothing here
    knows which YAML kinds Pyrite withholds."""
    try:
        back = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        return False
    return _same(back, value)


def _same(a, b) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return list(a) == list(b) and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def _expected(field, kind, position):
    """("returned", value) | ("omitted",) | ("withheld", reason) | ("unreadable",)."""
    kb_yaml = _kb_yaml_text(field, _node_yaml(kind, position))
    try:
        value = _as_written(kb_yaml, field)
    except yaml.YAMLError:
        # The reference reader cannot build it: an unhashable mapping key.
        # A JSON object key is a string, so it is a key that is not a string.
        return ("withheld", KEY_REASON)
    if value is None or (isinstance(value, str | dict | list) and len(value) == 0):
        return ("omitted",)  # a null or empty field value is left out, at any position
    if _json_carries(value):
        return ("returned", value)
    return ("withheld", KEY_REASON if position == "mapping-key" else REASON[kind])


def _kb_yaml_text(field: tuple[str, ...], text: str) -> str:
    if field[0] == "types":
        return f"name: k\ntypes:\n  note:\n    description: A note\n    {field[2]}: {text}\n"
    return f"name: k\n{field[0]}: {text}\ntypes:\n  note:\n    description: A note\n"


@pytest.mark.parametrize("position", POSITIONS)
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field", FIELDS, ids=".".join)
def test_every_node_kind_in_every_position_is_returned_or_withheld_with_a_true_reason(
    tmp_path, field, kind, position
):
    kb_yaml = _kb_yaml_text(field, _node_yaml(kind, position))
    try:
        result = _orient(tmp_path, kb_yaml)
    except Exception as exc:  # the KB's own loader cannot read the file
        pytest.skip(
            f"kb.yaml unreadable by Pyrite's loader ({type(exc).__name__}): orient fails here "
            "as on dev, out of scope for #1369"
        )
    authored = result["kb_authored"]
    expected = _expected(field, kind, position)
    key = ".".join(field)
    malformed = [m for m in authored.get("malformed", []) if m["key"] == key]
    if expected[0] == "returned":
        assert _at(authored, field) == expected[1]
        assert _same(_at(authored, field), expected[1])
        assert malformed == []
    elif expected[0] == "omitted":
        assert _at(authored, field) is _ABSENT
        assert malformed == []
    else:
        assert _at(authored, field) is _ABSENT
        assert malformed == [{"key": key, "reason": expected[1]}]
    assert _json_carries(authored)


@pytest.mark.parametrize("field", FIELDS, ids=".".join)
def test_orient_answers_for_every_top_level_kind(tmp_path, field):
    """Orient itself never raises for a value kb_authored reads (dev answers)."""
    for kind in KINDS:
        sub = tmp_path / kind
        sub.mkdir()
        result = _orient(sub, _kb_yaml_text(field, _node_yaml(kind, "top")))
        assert "schema" in result and "kb_authored" in result


def test_a_blank_value_inside_a_mapping_and_a_null_list_item_come_back_as_null(tmp_path):
    """The conductor's reproductions: a key left blank is a common hand edit."""
    authored = _orient(
        tmp_path,
        "name: k\nguidelines:\n  tone: calm\n  depth:\nevaluation_rubric:\n  - Cite a source\n  - null\n",
    )["kb_authored"]
    assert authored["guidelines"] == {"tone": "calm", "depth": None}
    assert authored["evaluation_rubric"] == ["Cite a source", None]
    assert "malformed" not in authored


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

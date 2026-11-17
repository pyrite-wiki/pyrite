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

Tags (#1568): `!!str` on a scalar is the string it says; any other explicit
tag (a local `!note`, a URI tag, `!!str` on a list or mapping) is withheld
whole under one reason that is true of the value and never prints the tag.
A number that overflows a double (`1.0e+400`) is withheld as too large. The
oracle disagrees with Pyrite's loader on a few of these; each disagreement is
a written exception below, never a change to production code to match it.
"""

import json
import math

import pytest
import yaml

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import KBConfigInvalidError
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.utils.yaml import read_kb_yaml

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
    "tagged-string": "!!str 12",
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
    return _orient_config(_config(tmp_path, kb_yaml))


def _orient_config(config: PyriteConfig) -> dict:
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
    # Tags (#1568). The tag text is the KB author's: no reason may print it.
    "str-tag-int": "!!str 12",
    "str-tag-word": "!!str hello",
    "local-tag-scalar": "!note hello",
    "local-tag-mapping": "!note {a: b}",
    "local-tag-list": "!note [a, b]",
    "uri-tag": "!<tag:example.com,2026:x> hi",
    "str-tag-on-list": "!!str [a]",
    # Overflow: a finite-looking number that is infinite as a double.
    "overflow": "1.0e+400",
    "-overflow": "-1.0e+400",
    "overflow-no-dot": "1e400",
    # A recursive alias (#1602 is the follow-up): the anchor sits on the
    # field's own value, so it can be written at top, mapping-value and
    # list-item only.
    "recursive-map": "&a {self: *a}",
    "recursive-list": "&a [x, *a]",
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
TAG_REASON = "a value with a YAML tag, which JSON cannot carry as written"
TOO_LARGE_REASON = "a number too large for JSON (it reads as infinite)"
#: The catch-all: no shape the grid lists may be named with it.
OTHER_REASON = "a YAML value of a kind JSON cannot carry as written"
REASON.update(
    {
        "overflow": TOO_LARGE_REASON,
        "-overflow": TOO_LARGE_REASON,
        "overflow-no-dot": TOO_LARGE_REASON,
        "local-tag-scalar": TAG_REASON,
        "local-tag-mapping": TAG_REASON,
        "local-tag-list": TAG_REASON,
        "uri-tag": TAG_REASON,
        "str-tag-on-list": TAG_REASON,
    }
)
#: Text a reason must never echo: the KB author's tag, not Pyrite's.
AUTHORS_TAG_TEXT = ("note", "example.com")

#: Kinds whose tag is on a scalar, and on a collection. At the mapping-key
#: position a collection is a key that is not a string whatever its tag (the
#: loader drops the tag when it builds the key), while a tagged scalar key is
#: named by its tag: the tag is the first thing wrong with it (#1568).
TAGGED_SCALAR_KINDS = {"local-tag-scalar", "uri-tag"}
TAGGED_COLLECTION_KINDS = {"local-tag-mapping", "local-tag-list", "str-tag-on-list"}

#: What Pyrite's loader reads for a recursive alias: null where the alias
#: recurses (ruamel does this; entry frontmatter reads the same). PyYAML builds
#: a real cycle instead, which no JSON carries; #1602 tracks refusing it.
#: This records what the loader does today. It is not evidence that null is
#: the right answer for a recursive alias; #1602 owns that question.
RECURSIVE_AS_PYRITE_READS = {"recursive-map": {"self": None}, "recursive-list": ["x", None]}
RECURSIVE_POSITIONS = ("top", "mapping-value", "list-item")


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
    """("returned", value) | ("omitted",) | ("withheld", reason).

    Three disagreements between the reference reader (PyYAML) and Pyrite's
    loader (ruamel) are decided here by hand, each from the spec, not from the
    code under test; nothing in production bends to match the oracle:
    - a tag PyYAML cannot construct (a local tag, a URI tag, `!!str` on a
      list) raises `ConstructorError`; the answer is the tag reason;
    - `1e400` (no dot) PyYAML reads as the string "1e400"; ruamel reads an
      infinity, which is too large for JSON;
    - a recursive alias PyYAML builds as a real cycle; ruamel reads null where
      it recurses (`RECURSIVE_AS_PYRITE_READS`).
    """
    if kind in RECURSIVE_AS_PYRITE_READS:
        v = RECURSIVE_AS_PYRITE_READS[kind]
        return ("returned", {"top": v, "mapping-value": {"k": v}, "list-item": [v]}[position])
    if kind in TAGGED_SCALAR_KINDS:
        return ("withheld", TAG_REASON)
    if kind in TAGGED_COLLECTION_KINDS:
        return ("withheld", KEY_REASON if position == "mapping-key" else TAG_REASON)
    if kind == "overflow-no-dot":
        return ("withheld", KEY_REASON if position == "mapping-key" else TOO_LARGE_REASON)
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


def _cells():
    for field in FIELDS:
        for kind in KINDS:
            for position in POSITIONS:
                if kind in RECURSIVE_AS_PYRITE_READS and position not in RECURSIVE_POSITIONS:
                    continue  # the anchor cannot be written there (as a key it is unhashable)
                yield pytest.param(field, kind, position, id=f"{'.'.join(field)}-{kind}-{position}")


@pytest.mark.parametrize(("field", "kind", "position"), list(_cells()))
def test_every_node_kind_in_every_position_is_returned_or_withheld_with_a_true_reason(
    tmp_path, field, kind, position
):
    config = _config(tmp_path, _kb_yaml_text(field, _node_yaml(kind, position)))
    try:
        # The KB's own loader, outside orient: `KBConfig.kb_schema` is
        # `KBSchema.from_dict(read_kb_yaml(kb path))`, so the typed refusal of
        # `read_kb_yaml` (#1547) is the loader failing.
        read_kb_yaml(config.knowledge_bases[0].path)
    except KBConfigInvalidError as exc:
        pytest.skip(
            f"kb.yaml unreadable by Pyrite's loader ({type(exc).__name__}: {exc}): "
            "orient fails here as on dev, out of scope for #1369"
        )
    # No `try`: anything orient itself raises fails the test.
    authored = _orient_config(config)["kb_authored"]
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
    for item in authored.get("malformed", []):
        assert item["reason"] != OTHER_REASON  # no listed shape is the catch-all
        assert not any(t in item["reason"] for t in AUTHORS_TAG_TEXT)  # Pyrite's text only
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
    if shape == "tagged-string":
        # The acceptance line of #1568: the string, on all four surfaces.
        assert service["guidelines"] == "12" and service["types"]["note"]["goals"] == "12"
        assert "malformed" not in service
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


def test_an_empty_str_tagged_value_is_omitted_like_an_empty_string(tmp_path):
    """`!!str ''` is the string `''` (#1568), and an empty field value is left out."""
    authored = _orient(
        tmp_path, "name: k\nguidelines: !!str ''\ngoals: !!str 12\nevaluation_rubric: ''\n"
    )["kb_authored"]
    assert authored["goals"] == "12"
    assert "guidelines" not in authored and "evaluation_rubric" not in authored
    assert "malformed" not in authored


# --- Two things that become one, or less than was written (#1568, round 1) ---
#
# Every answer below is written by hand from the property: a value under a
# kb_authored key comes back whole as written, or is withheld whole with one
# `malformed` entry whose reason is true of what happened.

COLLISION_REASON = "a mapping with two keys that are the same string"

#: A mapping where two keys are the same string once a `!!str` key is read as
#: the string it says. The loader does not refuse these as duplicate keys (a
#: tagged key is not equal to a plain one), so dropping one value would be
#: silent. Each is the whole value at a position, as flow text. (The long form
#: `!<tag:yaml.org,2002:str> a` is resolved to a plain string by the loader,
#: which then refuses the duplicate itself: not a case here.)
COLLISIONS = {
    "tagged-then-plain": "{!!str tone: first, tone: second}",
    "plain-then-tagged": "{tone: first, !!str tone: second}",
    "tagged-number-and-quoted-number": "{!!str 1: x, '1': y}",
    "tagged-and-tagged": "{!!str a: 1, !!str a: 2}",
    "tagged-and-tagged-quoted": "{!!str 1: x, !!str '1': y}",
    "merged-tagged-key-and-own-key": "{<<: {!!str a: 1}, a: 2}",
}

#: A merge key (`<<`) whose source carries a tag: the loaded value still holds
#: its sources, each with its tag, so the tag is not dropped silently.
TAGGED_MERGES = {
    "local-tag-source": "{<<: !note {a: 1}, b: 2}",
    "str-tag-source": "{<<: !!str {a: 1}, b: 2}",
    "uri-tag-source": "{<<: !<tag:example.com,2026:x> {a: 1}, b: 2}",
    "tagged-source-in-a-list": "{<<: [!note {a: 1}, {c: 2}], b: 2}",
    "tag-on-the-sources-own-merge": "{<<: {<<: !note {a: 1}, c: 1}, b: 2}",
}
GRID_POSITIONS = ("top", "mapping-value", "list-item", "two-down")
TWO_FIELDS = [("guidelines",), ("types", "note", "goals")]


def _cells_for(cases):
    for field in TWO_FIELDS:
        for name in cases:
            for position in GRID_POSITIONS:
                yield pytest.param(field, name, position, id=f"{'.'.join(field)}-{name}-{position}")


def _withheld_whole(tmp_path, field, text, position, reason):
    config = _config(tmp_path, _kb_yaml_text(field, POSITIONS[position].format(text)))
    read_kb_yaml(config.knowledge_bases[0].path)  # the loader reads it: no skip, no excuse
    authored = _orient_config(config)["kb_authored"]
    key = ".".join(field)
    assert _at(authored, field) is _ABSENT
    assert authored.get("malformed") == [{"key": key, "reason": reason}]


@pytest.mark.parametrize(("field", "name", "position"), list(_cells_for(COLLISIONS)))
def test_two_keys_that_are_the_same_string_withhold_the_whole_value(
    tmp_path, field, name, position
):
    _withheld_whole(tmp_path, field, COLLISIONS[name], position, COLLISION_REASON)


@pytest.mark.parametrize(("field", "name", "position"), list(_cells_for(TAGGED_MERGES)))
def test_a_tagged_merge_source_is_withheld_like_any_tagged_mapping(tmp_path, field, name, position):
    _withheld_whole(tmp_path, field, TAGGED_MERGES[name], position, TAG_REASON)


def test_a_tagged_key_and_an_int_key_stay_the_key_reason(tmp_path):
    """`!!str 1` and the integer key `1` print alike; the int key is the first
    thing wrong, so this was and stays the key reason."""
    _withheld_whole(tmp_path, ("guidelines",), "{!!str 1: x, 1: y}", "top", KEY_REASON)


@pytest.mark.control(
    reason="the own key overriding a merged one is YAML's defined merge, as on dev"
)
def test_an_own_key_overriding_a_merged_key_is_the_one_value(tmp_path):
    authored = _orient(tmp_path, "name: k\nguidelines: {<<: {a: 1, c: 3}, a: 2}\n")["kb_authored"]
    assert authored["guidelines"] == {"a": 2, "c": 3}
    assert "malformed" not in authored


def test_a_tag_on_a_list_of_merge_sources_is_a_limit_not_a_withhold(tmp_path):
    """LIMIT, not a property: the loader flattens `<<: !note [{a: 1}]` into
    plain source mappings and the list's tag is gone from the loaded value, so
    the value comes back with the tag dropped, as on dev. Fixing it means the
    loader; a follow-up issue owns it. If this starts failing, the loader keeps
    the tag now: withhold it and move this case into TAGGED_MERGES."""
    for n, text in enumerate(("{<<: !note [{a: 1}], b: 2}", "{<<: !!seq [{a: 1}], b: 2}")):
        sub = tmp_path / str(n)
        sub.mkdir()
        authored = _orient(sub, f"name: k\nguidelines: {text}\n")["kb_authored"]
        assert authored["guidelines"] == {"b": 2, "a": 1}
        assert "malformed" not in authored


#: The core tags the loader resolves before orient sees the value. They are
#: YAML's own types, read as YAML defines them, and the result is returned: a
#: tag is withheld only when the loader keeps it. Written by hand (YAML 1.1
#: tag repository).
RESOLVED_CORE_TAGS = {
    "!!int '12'": 12,
    "!!float 12": 12.0,
    "!!bool yes": True,
    "!<tag:yaml.org,2002:str> 12": "12",
    "!!map {a: b}": {"a": "b"},
    "!!seq [a, b]": ["a", "b"],
    "!<tag:yaml.org,2002:map> {a: b}": {"a": "b"},
    "!<tag:yaml.org,2002:seq> [a, b]": ["a", "b"],
}


@pytest.mark.control(reason="resolved by the loader before orient: the same on dev")
@pytest.mark.parametrize("text", list(RESOLVED_CORE_TAGS))
def test_a_core_tag_the_loader_resolves_is_read_as_yaml_defines_it(tmp_path, text):
    authored = _orient(tmp_path, f"name: k\nguidelines: {text}\n")["kb_authored"]
    assert _same(authored["guidelines"], RESOLVED_CORE_TAGS[text])
    assert "malformed" not in authored

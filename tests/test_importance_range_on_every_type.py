"""A write stores importance as sent, a whole number 1-10, or refuses it (#1537, #1588).

The property: ``importance`` is a base field, a whole number from 1 to 10 on every
entry. For any value, by any surface, a create, ``pyrite add``, a bulk create or
import and an update that sets it either store it as it was sent or refuse it with
``VALIDATION_FAILED``, the same ``errors`` row naming ``importance``, and no file
written or changed -- on every entry type and on every surface (the CLI's
``-f importance=``, MCP ``kb_create`` / ``kb_update`` / ``kb_bulk_create``, REST
``POST`` / ``PUT`` / ``PATCH /api/entries``). A value the file already holds, at the
top level or under ``metadata:``, is judged by the same rule and is the file's: an
update of another field succeeds and reports it ("violation already on disk; kept",
design principle 2), with the refusal's own rule.

Before #1537 only ``EventEntry`` carried the rule, so a ``person`` stored
``importance: 99`` and ``pyrite qa validate`` then reported what Pyrite had just
written. The rule now sits on ``Entry.validation_errors`` (one place, design
principle 8), and the one write gate (``KBService._validate_write``) applies it
to every type. Before #1588 a create judged the model's ``int()`` of the value
(10.7 stored as 10, ``true`` as 1, ``high`` as 5) and an update of a file spelling
``metadata: {importance: ...}`` judged it the same way; the model now keeps the
value as given for the rule (``Entry._importance_given``) and ``Entry.importance``
stays an int for every reader.

The types are not listed by hand: the core registry (``ENTRY_TYPE_REGISTRY``),
every plugin-declared class (``get_registry().get_all_entry_types()``), and a
type a ``kb.yaml`` declares alone (``GenericEntry``). A type added to any of
them is covered by ``test_every_entry_class_carries_the_rule`` and
``test_every_entry_class_judges_the_value_its_frontmatter_gives`` without a new
line here.

Event rows. These are controls (``_EVENT_CONTROL``): they passed before #1537
and must still pass -- the ``core-event`` rows of the create, update, on-disk
and add tests for 0, 11, -5, 99, the in-range values and an absent value, and
the ``EventEntry`` class rows (and its subclasses) for 0, 11, -5, 99. These are
not controls: the ``event`` cells of the value-kind block below (``KINDS``, run
for a note and an event) pin the answers on both types.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from pyrite.models.core_types import ENTRY_TYPE_REGISTRY

KB = "imp"
YAML_KB = "imp-yaml"
OUT_OF_RANGE = [0, 11, -5, 99]
IN_RANGE = [1, 10, "7"]
KEPT_MESSAGE = "violation already on disk"
KB_YAML = """\
name: imp-yaml
kb_type: generic
types:
  widget:
    description: A type only this kb.yaml declares
    subdirectory: widgets
    fields:
      colour:
        type: text
"""

_EVENT_CONTROL = pytest.mark.control(
    reason="EventEntry already carried the rule before #1537; this row must pass before and after"
)


_ACCEPTS = pytest.mark.control(
    reason="over-refusal guard: in-range and absent values were accepted before #1537 and must stay accepted"
)


def _kb(etype: str) -> str:
    """The KB a type is written to: the kb.yaml-only type lives in its own KB."""
    return YAML_KB if etype == "widget" else KB


def _plugin_types() -> dict[str, type]:
    from pyrite.plugins import get_registry

    return get_registry().get_all_entry_types()


def _surface_types() -> list:
    """Core types, one plugin type, one kb.yaml-only type; derived, not listed."""
    out: list = []
    for name in sorted(ENTRY_TYPE_REGISTRY):
        marks = [_EVENT_CONTROL] if name == "event" else []
        out.append(pytest.param(name, id=f"core-{name}", marks=marks))
    plugin = sorted(
        n for n, c in _plugin_types().items() if c.__module__.startswith("pyrite_zettelkasten")
    )
    out.append(pytest.param(plugin[0], id=f"plugin-{plugin[0]}"))
    out.append(pytest.param("widget", id="kbyaml-widget"))
    return out


TYPES = _surface_types()


@pytest.fixture
def world(cli_world, tmp_path):
    w = cli_world(
        kbs=[
            KBConfig(name=KB, path=tmp_path / "imp-kb", kb_type="generic"),
            KBConfig(name=YAML_KB, path=tmp_path / "imp-yaml-kb", kb_type="generic"),
        ]
    )
    (w.kb_path(YAML_KB) / "kb.yaml").write_text(KB_YAML)
    return w


def _snapshot(world) -> dict[str, bytes]:
    out = {}
    for name in (KB, YAML_KB):
        root = world.kb_path(name)
        for p in sorted(root.rglob("*")):
            if p.is_file():
                out[f"{name}/{p.relative_to(root)}"] = p.read_bytes()
    return out


def _extra(etype: str) -> dict:
    return {"date": "2026-01-02"} if etype == "event" else {}


def _file_text(etype: str, entry_id: str, importance=None, extra_lines: str = "") -> str:
    lines = ["---", f"id: {entry_id}", f"type: {etype}", f"title: {entry_id}"]
    if etype == "event":
        lines.append("date: 2026-01-02")
    if importance is not None:
        lines.append(f"importance: {json.dumps(importance)}")
    lines += [extra_lines] if extra_lines else []
    lines += ["---", "", "Body.", ""]
    return "\n".join(lines)


# -- one result shape for both surfaces --------------------------------------


def _json(output: str) -> dict:
    start = output.index("{")
    return json.loads(output[start:])


def _cli_create(world, etype, importance):
    args = ["create", "-k", _kb(etype), "-t", etype, "--title", "T1", "-b", "x"]
    for k, v in _extra(etype).items():
        args += ["-f", f"{k}={v}"]
    if importance is not None:
        args += ["-f", f"importance={importance}"]
    res = world.cli(*args, "--format", "json")
    return res.exit_code, _json(res.output)


def _cli_update(world, kb, entry_id, **fields):
    args = ["update", entry_id, "-k", kb]
    for k, v in fields.items():
        args += ["-b", v] if k == "body" else ["-f", f"{k}={v}"]
    res = world.cli(*args, "--format", "json")
    return res.exit_code, _json(res.output)


def _mcp_create(world, etype, importance):
    server = world.mcp()
    payload = {
        "kb_name": _kb(etype),
        "entry_type": etype,
        "title": "T1",
        "body": "x",
        **_extra(etype),
    }
    if importance is not None:
        payload["importance"] = importance
    res = server._dispatch_tool("kb_create", payload)
    return (0 if res.get("created") else 1), res


def _mcp_update(world, kb, entry_id, **fields):
    server = world.mcp()
    res = server._dispatch_tool("kb_update", {"kb_name": kb, "entry_id": entry_id, **fields})
    return (0 if res.get("updated") else 1), res


SURFACES = {
    "cli": (_cli_create, _cli_update),
    "mcp": (_mcp_create, _mcp_update),
}
surface = pytest.mark.parametrize("surf", sorted(SURFACES))
types = pytest.mark.parametrize("etype", TYPES)


def _assert_refused(code, res):
    assert code != 0, res
    assert res.get("error_code") == "VALIDATION_FAILED", res
    rows = res.get("errors") or []
    assert any(r.get("field") == "importance" for r in rows), res


# -- create --------------------------------------------------------------------


@types
@surface
@pytest.mark.parametrize("value", OUT_OF_RANGE)
def test_a_create_outside_1_to_10_is_refused_and_writes_nothing(world, etype, surf, value):
    before = _snapshot(world)
    code, res = SURFACES[surf][0](world, etype, value)
    _assert_refused(code, res)
    assert _snapshot(world) == before


@_ACCEPTS
@types
@surface
@pytest.mark.parametrize("value", [*IN_RANGE, None], ids=lambda v: f"value-{v}")
def test_a_create_in_range_or_absent_is_accepted(world, etype, surf, value):
    code, res = SURFACES[surf][0](world, etype, value)
    assert code == 0, res
    assert res.get("created") is True, res


# -- update that sets it ---------------------------------------------------------


@types
@surface
@pytest.mark.parametrize("value", OUT_OF_RANGE)
def test_an_update_setting_it_outside_1_to_10_is_refused_and_changes_nothing(
    world, etype, surf, value
):
    world.add_entry(_kb(etype), _file_text(etype, "seed", importance=5))
    before = _snapshot(world)
    code, res = SURFACES[surf][1](world, _kb(etype), "seed", importance=value)
    _assert_refused(code, res)
    assert _snapshot(world) == before


@_ACCEPTS
@types
@surface
@pytest.mark.parametrize("value", IN_RANGE)
def test_an_update_setting_it_in_range_is_accepted(world, etype, surf, value):
    world.add_entry(_kb(etype), _file_text(etype, "seed", importance=5))
    code, res = SURFACES[surf][1](world, _kb(etype), "seed", importance=value)
    assert code == 0, res


# -- a value already on disk is reported, not refused ------------------------------


@types
@surface
@pytest.mark.parametrize("on_disk", [99, "high"], ids=["99", "high"])
def test_an_update_of_another_field_keeps_and_reports_a_value_already_on_disk(
    world, etype, surf, on_disk
):
    path = world.add_entry(_kb(etype), _file_text(etype, "seed", importance=on_disk))
    code, res = SURFACES[surf][1](world, _kb(etype), "seed", body="changed body")
    assert code == 0, res
    warnings = res.get("warnings") or []
    kept = [w for w in warnings if KEPT_MESSAGE in json.dumps(w)]
    assert kept, res
    assert any("importance" in json.dumps(w) for w in kept), res
    text = path.read_text()
    assert f"importance: {json.dumps(on_disk)}" in text or f"importance: {on_disk}" in text
    assert "changed body" in text


# -- every entry class carries the rule (a new type cannot go around it) ----------


def _every_class() -> list:
    from pyrite.models.generic import GenericEntry

    classes = {f"core-{n}": c for n, c in ENTRY_TYPE_REGISTRY.items()}
    classes.update({f"plugin-{n}": c for n, c in _plugin_types().items()})
    classes["generic"] = GenericEntry
    from pyrite.models.core_types import EventEntry

    return [
        pytest.param(c, id=i, marks=[_EVENT_CONTROL] if issubclass(c, EventEntry) else [])
        for i, c in sorted(classes.items())
    ]


@pytest.mark.parametrize("cls", _every_class())
@pytest.mark.parametrize("value", [0, 11, -5, 99])
def test_every_entry_class_carries_the_rule(cls, value):
    entry = cls(id="x", title="X", importance=value)
    rows = [r for r in entry.validation_errors() if r.get("field") == "importance"]
    assert rows, f"{cls.__name__} accepted importance={value}"
    assert rows[0]["rule"] == "range"
    assert rows[0]["message"] == f"Importance must be 1-10, got: {value}"
    assert rows[0]["expected"] == "1-10"


@_ACCEPTS
@pytest.mark.parametrize("cls", _every_class())
def test_every_entry_class_accepts_the_bounds_and_the_default(cls):
    for value in (1, 10):
        rows = [
            r
            for r in cls(id="x", title="X", importance=value).validation_errors()
            if r.get("field") == "importance"
        ]
        assert not rows
    assert not [
        r for r in cls(id="x", title="X").validation_errors() if r.get("field") == "importance"
    ]


# -- pyrite add of a hand-written file ---------------------------------------------


def test_add_of_a_file_holding_importance_99_is_refused_for_a_non_event(world, tmp_path):
    src = tmp_path / "jane.md"
    src.write_text(_file_text("person", "jane", importance=99))
    before = _snapshot(world)
    res = world.cli("add", str(src), "-k", KB, "--format", "json")
    assert res.exit_code != 0, res.output
    body = _json(res.output)
    assert body.get("error_code") == "VALIDATION_FAILED", body
    assert any(r.get("field") == "importance" for r in body.get("errors", [])), body
    assert _snapshot(world) == before


@_EVENT_CONTROL
def test_add_of_an_event_file_holding_importance_99_is_refused(world, tmp_path):
    src = tmp_path / "e.md"
    src.write_text(_file_text("event", "2026-01-02--e", importance=99))
    before = _snapshot(world)
    res = world.cli("add", str(src), "-k", KB, "--format", "json")
    assert res.exit_code != 0, res.output
    assert _snapshot(world) == before


# -- a plugin-scoped KB answers as every other KB does ---------------------------------


@pytest.mark.parametrize("surf", sorted(SURFACES))
def test_a_journalism_kb_refuses_importance_99_with_the_models_answer(cli_world, tmp_path, surf):
    """The journalism plugin used to refuse this itself (`SCHEMA_VIOLATION`, a row
    with no `rule`); the model answers first now: `VALIDATION_FAILED`, the same
    row an event has."""
    world = cli_world(
        kbs=[KBConfig(name="ji", path=tmp_path / "ji", kb_type="journalism-investigation")]
    )
    before = {p: p.read_bytes() for p in world.kb_path("ji").rglob("*") if p.is_file()}
    if surf == "cli":
        res = world.cli(
            "create", "-k", "ji", "-t", "person", "--title", "P", "-b", "x",
            "-f", "importance=99", "--format", "json",
        )  # fmt: skip
        code, body = res.exit_code, _json(res.output)
    else:
        body = world.mcp()._dispatch_tool(
            "kb_create",
            {"kb_name": "ji", "entry_type": "person", "title": "P", "body": "x", "importance": 99},
        )
        code = 0 if body.get("created") else 1
    _assert_refused(code, body)
    assert any(r.get("rule") == "range" for r in body["errors"]), body
    assert {p: p.read_bytes() for p in world.kb_path("ji").rglob("*") if p.is_file()} == before


# -- every kind of value a file, JSON or `-f` text can put in `importance` ---------------
#
# The property, for any value: the check answers with a row or with nothing and
# never raises; a value this write did not set never stops the write; a write
# that is accepted stores a whole number in 1-10 as it was sent. One row per kind,
# each with the verdict the property gives it by hand (not read off the code), so
# a new kind of value cannot be added without saying what it must do.
#
#   file:       "good" (a whole number in 1-10, or plain ASCII digit text for one),
#               "absent" (blank or null: a guest in a person's file does not call a
#               blank key a violation), "bad" (everything else: kept and reported
#               when already on disk, refused by `pyrite add`)
#   cli_set:    the verdict of the `-f importance=<text>` text ("null" and "" are
#               strings on the command line). The command line's `-f` parser reads
#               numeric text as a number for every field before any rule sees it
#               (`+5`, `1_0`, `１０`, ` 5 ` arrive as 5 or 10): that parser is out of
#               scope for #1588 (the groom's "Out of scope"), so those cells are
#               "good" and store the number.
#   mcp_set:    the verdict of the JSON value
#
# A create answers as an update that sets the same value on the same surface does
# (#1588, decision (a)): no create column. Whole floats (`5.0`, `10.0`, `1e1`) are
# with the maintainer (#1588); their rows keep the verdicts they had before #1588.
_I = 123456789012345678901234567890
KINDS = {
    # kind: (yaml, cli text, json value, file, cli_set, mcp_set)
    "in_range_7": ("7", "7", 7, "good", "good", "good"),
    "out_99": ("99", "99", 99, "bad", "bad", "bad"),
    "zero": ("0", "0", 0, "bad", "bad", "bad"),
    "big_30_digits": (str(_I), str(_I), _I, "bad", "bad", "bad"),
    "float_5.0": ("5.0", "5.0", 5.0, "good", "good", "good"),
    "float_10.0": ("10.0", "10.0", 10.0, "good", "good", "good"),
    # importance is a whole number (#1588 decision): 5.5 is refused, not stored as given
    "float_5.5": ("5.5", "5.5", 5.5, "bad", "bad", "bad"),
    # the edges of the range that are not whole: compared as numbers, not truncated first
    "float_10.000001": ("10.000001", "10.000001", 10.000001, "bad", "bad", "bad"),
    "float_10.5": ("10.5", "10.5", 10.5, "bad", "bad", "bad"),
    "float_10.7": ("10.7", "10.7", 10.7, "bad", "bad", "bad"),
    "float_10.9": ("10.9", "10.9", 10.9, "bad", "bad", "bad"),
    "float_3.9": ("3.9", "3.9", 3.9, "bad", "bad", "bad"),
    "float_0.5": ("0.5", "0.5", 0.5, "bad", "bad", "bad"),
    "float_0.99": ("0.99", "0.99", 0.99, "bad", "bad", "bad"),
    "float_-0.5": ("-0.5", "-0.5", -0.5, "bad", "bad", "bad"),
    "float_1e1": ("1e1", "1e1", 1e1, "good", "good", "good"),
    # a string is plain ASCII digit text only; the command line reads numeric text as a number
    "str_10.9": ('"10.9"', "10.9", "10.9", "bad", "bad", "bad"),
    "str_10.7": ('"10.7"', "10.7", "10.7", "bad", "bad", "bad"),
    "str_true": ('"true"', "true", "true", "bad", "bad", "bad"),
    "str_7": ('"7"', "7", "7", "good", "good", "good"),
    "str_1e1": ('"1e1"', "1e1", "1e1", "bad", "good", "bad"),
    "inf": (".inf", ".inf", float("inf"), "bad", "bad", "bad"),
    "-inf": ("-.inf", "-.inf", float("-inf"), "bad", "bad", "bad"),
    "nan": (".nan", ".nan", float("nan"), "bad", "bad", "bad"),
    "true": ("true", "true", True, "bad", "bad", "bad"),
    "false": ("false", "false", False, "bad", "bad", "bad"),
    "null": ("null", "null", None, "absent", "bad", "absent"),
    "blank": ("", "", None, "absent", "bad", "absent"),
    "empty_string": ('""', "", "", "bad", "bad", "bad"),
    "str_5": ('"5"', "5", "5", "good", "good", "good"),
    # padded text is not plain digits (#1588 decision); the CLI parser strips it first
    "str_spaces_5": ('" 5 "', " 5 ", " 5 ", "bad", "good", "bad"),
    "str_padded_newline": ('" 5\\n"', " 5\n", " 5\n", "bad", "good", "bad"),
    "str_05": ('"05"', "05", "05", "good", "good", "good"),
    "str_fullwidth_10": ('"１０"', "１０", "１０", "bad", "good", "bad"),
    "str_1_0": ('"1_0"', "1_0", "1_0", "bad", "good", "bad"),
    "str_plus_5": ('"+5"', "+5", "+5", "bad", "good", "bad"),
    # bytes are not a number, whatever they spell (b"5"); JSON cannot carry bytes, so the
    # MCP cell sends the YAML text itself (a string that is not digits)
    "binary_5": ("!!binary NQ==", "!!binary NQ==", "!!binary NQ==", "bad", "bad", "bad"),
    # a YAML `!!str` scalar is text and is judged as its text ("7", accepted on a write)
    "str_tag_7": ("!!str 7", "7", "7", "good", "good", "good"),
    "high": ("high", "high", "high", "bad", "bad", "bad"),
    "abc": ('"abc"', "abc", "abc", "bad", "bad", "bad"),
    "list": ("[5]", "[5]", [5], "bad", "bad", "bad"),
    "mapping": ("{a: 1}", "{a: 1}", {"a": 1}, "bad", "bad", "bad"),
    "date": ("2026-01-02", "2026-01-02", "2026-01-02", "bad", "bad", "bad"),
}
ETYPES = ["note", "event"]
kinds = pytest.mark.parametrize("kind", sorted(KINDS))
etypes = pytest.mark.parametrize("etype", ETYPES)
# The two places a file can spell importance (#1025): its own key, or under `metadata:`.
SPELLINGS = ["top", "nested"]
spellings = pytest.mark.parametrize("spelling", SPELLINGS)

# YAML nodes with no JSON form (`!!binary`, a `!!str`-tagged scalar). Not about importance:
# the same on dev before #1588, for any key.
#  - Under `metadata:` the file cannot be read at all (the metadata column is JSON): an
#    update of it answers VALIDATION_FAILED, "a value Pyrite cannot read", and writes
#    nothing (#1035's answer for an unmodellable file).
#  - `pyrite add` cannot write such a node back (its writer refuses a value it cannot
#    represent), so it refuses the file whatever the importance rule says.
_NO_JSON_FORM = {"binary_5", "str_tag_7"}
_UNREADABLE = "a value Pyrite cannot read"


# An update that leaves a 30-digit importance on disk writes the file, then the index
# upsert raises OverflowError (SQLite INTEGER), so the command answers NO_OUTCOME (CLI) or
# INTERNAL (MCP) and the index has no row for the entry. Same on dev; the storage defect is
# #1584 and not part of this change (the check answers with a row). The cell is expected
# to fail only that way: `_IndexOverflowError` is raised only for that answer, with the file
# rewritten, and `raises=` makes any other failure a real one.
class _IndexOverflowError(Exception):
    pass


_INDEX_OVERFLOW = pytest.mark.xfail(
    strict=True,
    raises=_IndexOverflowError,
    reason="#1584: the index upsert overflows SQLite INTEGER on a 30-digit value on disk",
)


def _raw_file(etype: str, entry_id: str, raw: str | None, spelling: str = "top") -> str:
    lines = ["---", f"id: {entry_id}", f"type: {etype}", f"title: {entry_id}"]
    if etype == "event":
        lines.append("date: 2026-01-02")
    if raw is not None:
        value = f" {raw}" if raw else ""
        if spelling == "nested":
            lines += ["metadata:", "  importance:" + value]
        else:
            lines.append("importance:" + value)
    return "\n".join([*lines, "---", "", "Body.", ""])


def _importance_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.lstrip().startswith("importance:")]


def _stored_value(path: Path):
    from pyrite.utils.yaml import load_yaml

    fm = load_yaml(path.read_text().split("---", 2)[1]) or {}
    if "importance" in fm:
        return fm["importance"]
    nested = fm.get("metadata")
    return nested.get("importance") if isinstance(nested, dict) else None


def _stored_is_in_range(path: Path) -> bool:
    """What the file now holds is a whole number in 1-10, by hand from the decision:
    an int, plain ASCII digit text, or a whole float (whole floats are undecided and
    keep the answer they had before #1588)."""
    value = _stored_value(path)
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 1 <= value <= 10
    if isinstance(value, float):
        return math.isfinite(value) and value == int(value) and 1 <= value <= 10
    if isinstance(value, str):
        return value != "" and all(c in "0123456789" for c in value) and 1 <= int(value) <= 10
    return False


def _md_files(world) -> list[Path]:
    return sorted(world.kb_path(KB).rglob("*.md"))


def _kept(res: dict) -> bool:
    return any(
        KEPT_MESSAGE in json.dumps(w) and "importance" in json.dumps(w)
        for w in res.get("warnings") or []
    )


def _surface_call(world, surf, op, etype, **kw):
    """(exit, body) of one operation on one surface; never lets an exception escape."""
    if surf == "cli":
        if op == "update":
            args = ["update", "seed", "-k", KB]
            args += ["-b", "changed"] if "body" in kw else ["-f", f"importance={kw['value']}"]
        else:
            args = ["create", "-k", KB, "-t", etype, "--title", "T1", "-b", "x"]
            args += ["-f", "date=2026-01-02"] if etype == "event" else []
            args += ["-f", f"importance={kw['value']}"]
        res = world.cli(*args, "--format", "json")
        return res.exit_code, _json(res.output)
    server = world.mcp()
    if op == "update":
        payload = {"kb_name": KB, "entry_id": "seed"}
        payload.update({"body": "changed"} if "body" in kw else {"importance": kw["value"]})
        res = server._dispatch_tool("kb_update", payload)
    else:
        payload = {"kb_name": KB, "entry_type": etype, "title": "T1", "body": "x"}
        payload.update({"date": "2026-01-02"} if etype == "event" else {})
        payload["importance"] = kw["value"]
        res = server._dispatch_tool("kb_create", payload)
    return (0 if res.get("created") or res.get("updated") else 1), res


def _no_crash(code, res):
    assert res.get("error_code") not in ("NO_OUTCOME", "INTERNAL"), res


def _importance_rows(res: dict) -> list[dict]:
    return [r for r in res.get("errors") or [] if r.get("field") == "importance"]


@etypes
@surface
@spellings
@pytest.mark.parametrize(
    "kind",
    [
        pytest.param(k, marks=[_INDEX_OVERFLOW] if k == "big_30_digits" else [])
        for k in sorted(KINDS)
    ],
)
def test_a_value_already_on_disk_never_stops_an_update_of_another_field(
    world, etype, surf, spelling, kind
):
    yaml_text, _cli, _mcp, file, *_ = KINDS[kind]
    path = world.add_entry(KB, _raw_file(etype, "seed", yaml_text, spelling))
    before_text = path.read_text()
    code, res = _surface_call(world, surf, "update", etype, body="changed")
    if kind in _NO_JSON_FORM and spelling == "nested":
        assert res.get("error_code") == "VALIDATION_FAILED", res
        assert _UNREADABLE in res["error"], res
        assert path.read_text() == before_text
        return
    if kind == "big_30_digits" and res.get("error_code") in ("NO_OUTCOME", "INTERNAL"):
        assert "changed" in path.read_text(), "the known failure comes after the file is written"
        raise _IndexOverflowError(res)
    _no_crash(code, res)
    assert code == 0, res
    assert (file == "bad") == _kept(res), res  # kept and reported only when it is a violation
    if file == "bad":
        # the kept row carries the refusal's own rule, not a generic one (#1588 acceptance 5)
        kept = [w for w in res["warnings"] if w.get("field") == "importance"]
        assert [w["rule"] for w in kept] == ["range"], res
    text = path.read_text()
    assert "changed" in text
    # the importance line itself is not touched by a write that did not set it
    assert _importance_lines(text) == _importance_lines(before_text)


@etypes
@surface
@spellings
@kinds
def test_a_set_importance_is_refused_or_stored_as_sent(world, etype, surf, spelling, kind):
    _y, cli, mcp, _f, cli_set, mcp_set = KINDS[kind]
    path = world.add_entry(KB, _raw_file(etype, "seed", "5", spelling))
    before = path.read_bytes()
    verdict = cli_set if surf == "cli" else mcp_set
    code, res = _surface_call(world, surf, "update", etype, value=cli if surf == "cli" else mcp)
    _no_crash(code, res)
    if verdict == "bad":
        _assert_refused(code, res)
        assert path.read_bytes() == before
    else:
        assert code == 0, res
        if verdict == "good":
            assert _stored_is_in_range(path), path.read_text()


@etypes
@surface
@kinds
def test_a_create_answers_as_an_update_of_the_same_value_does(world, etype, surf, kind):
    """#1588 decision (a): a create refuses what an update refuses; it never stores the
    model's reading of the value (10.7 as 10, true as 1, high as the default 5)."""
    _y, cli, mcp, _f, cli_set, mcp_set = KINDS[kind]
    verdict = cli_set if surf == "cli" else mcp_set
    before = _snapshot(world)
    code, res = _surface_call(world, surf, "create", etype, value=cli if surf == "cli" else mcp)
    _no_crash(code, res)
    if verdict == "bad":
        _assert_refused(code, res)
        assert _snapshot(world) == before
    else:
        assert code == 0, res
        (path,) = _md_files(world)
        if verdict == "good":
            assert _stored_is_in_range(path), path.read_text()


@surface
@pytest.mark.parametrize(
    "kind", ["float_10.7", "float_5.5", "true", "high", "str_fullwidth_10", "out_99"]
)
def test_a_create_refuses_with_the_same_row_an_update_gives(world, surf, kind):
    _y, cli, mcp, _f, cli_set, mcp_set = KINDS[kind]
    value = cli if surf == "cli" else mcp
    if (cli_set if surf == "cli" else mcp_set) != "bad":
        pytest.skip("the CLI parser reads this text as a number (out of scope)")
    world.add_entry(KB, _raw_file("note", "seed", "5"))
    _ucode, updated = _surface_call(world, surf, "update", "note", value=value)
    _ccode, created = _surface_call(world, surf, "create", "note", value=value)
    assert _importance_rows(created) == _importance_rows(updated) != [], (created, updated)


@etypes
@spellings
@kinds
def test_add_refuses_a_file_holding_a_bad_value_and_accepts_the_rest(
    world, tmp_path, etype, spelling, kind
):
    yaml_text, _cli, _mcp, file, *_ = KINDS[kind]
    src = tmp_path / "src.md"
    src.write_text(_raw_file(etype, "seed", yaml_text, spelling))
    before = _snapshot(world)
    res = world.cli("add", str(src), "-k", KB, "--format", "json")
    body = _json(res.output)
    _no_crash(res.exit_code, body)
    if kind in _NO_JSON_FORM and file != "bad":
        # refused by the writer, not by the importance rule (which accepts it)
        assert res.exit_code != 0 and body.get("error_code") == "VALIDATION_FAILED", body
        assert not _importance_rows(body), body
        assert _snapshot(world) == before
    elif file == "bad":
        _assert_refused(res.exit_code, body)
        assert _snapshot(world) == before
    else:
        assert res.exit_code == 0, body


@etypes
@spellings
@kinds
def test_the_check_never_raises_and_says_row_or_nothing(etype, spelling, kind):
    from pyrite.models.core_types import get_entry_class
    from pyrite.schema import validate_importance

    yaml_text, _cli, mcp, file, *_ = KINDS[kind]
    entry = get_entry_class(etype).from_markdown(_raw_file(etype, "seed", yaml_text, spelling))
    rows = [r for r in entry.validation_errors() if r.get("field") == "importance"]
    assert bool(rows) == (file == "bad"), rows
    assert validate_importance(mcp) is (file == "good")
    # every reader still sees an int (#1588: option C, a raw Entry.importance, is not built)
    assert type(entry.importance) is int


# -- every entry class judges what its frontmatter gave, not its int() of it ---------------


@pytest.mark.parametrize("cls", _every_class())
@pytest.mark.parametrize("value", [10.7, 5.5, True, "high", "１０", b"5"], ids=repr)
def test_every_entry_class_judges_the_value_its_frontmatter_gives(cls, value):
    """Every create path builds through some ``from_frontmatter`` (``build_entry``,
    ``pyrite add``, an update's reload). The value it was given is what the rule
    judges; the attribute is the model's int for every reader."""
    meta = {"id": "x", "title": "X", "type": "event", "date": "2026-01-02", "importance": value}
    entry = cls.from_frontmatter(meta, "")
    rows = [r for r in entry.validation_errors() if r.get("field") == "importance"]
    assert rows, f"{cls.__name__} accepted importance={value!r}"
    assert rows[0]["rule"] == "range"
    assert type(entry.importance) is int


def test_an_assignment_is_judged_as_assigned():
    """An in-process write assigns the attribute; the value read from the file is then
    no longer the one to judge, in either direction."""
    from pyrite.models.core_types import NoteEntry

    entry = NoteEntry.from_frontmatter({"id": "x", "title": "X", "importance": "high"}, "")
    assert [r for r in entry.validation_errors() if r["field"] == "importance"]
    entry.importance = 7
    assert not [r for r in entry.validation_errors() if r["field"] == "importance"]
    entry = NoteEntry.from_frontmatter({"id": "x", "title": "X", "importance": 7}, "")
    entry.importance = 10.7
    assert [r for r in entry.validation_errors() if r["field"] == "importance"]


# -- bulk create and import go through the same rule ----------------------------------------


@pytest.mark.parametrize("value", [10.7, True, "high", "1_0", 5.5])
def test_a_bulk_create_and_an_import_refuse_what_a_create_refuses(world, tmp_path, value):
    before = _snapshot(world)
    res = world.mcp()._dispatch_tool(
        "kb_bulk_create",
        {"kb_name": KB, "entries": [{"entry_type": "note", "title": "B1", "importance": value}]},
    )
    (row,) = res["results"]
    assert row.get("error_code") == "VALIDATION_FAILED", res
    assert _snapshot(world) == before

    src = tmp_path / "import.json"
    src.write_text(json.dumps([{"entry_type": "note", "title": "I1", "importance": value}]))
    out = world.cli("import", str(src), "-k", KB)
    assert out.exit_code != 0, out.output
    assert "VALIDATION_FAILED" in out.output, out.output
    assert _snapshot(world) == before


@_ACCEPTS
def test_a_bulk_create_of_a_whole_number_is_stored_as_sent(world):
    res = world.mcp()._dispatch_tool(
        "kb_bulk_create",
        {"kb_name": KB, "entries": [{"entry_type": "note", "title": "B1", "importance": 7}]},
    )
    assert res["results"][0].get("created") is not False, res
    (path,) = _md_files(world)
    assert _stored_value(path) == 7


# -- update --unset clears it (the issue's item 3) -------------------------------------------


@_ACCEPTS
@spellings
def test_update_unset_clears_importance(world, spelling):
    path = world.add_entry(KB, _raw_file("note", "seed", "5", spelling))
    res = world.cli("update", "seed", "-k", KB, "--unset", "importance", "--format", "json")
    assert res.exit_code == 0, res.output
    assert _importance_lines(path.read_text()) == []


# -- REST answers as the CLI and MCP do -------------------------------------------------------


@pytest.fixture
def rest(tmp_path):
    from tests._world import build_world

    w = build_world(
        tmp_path / "rest",
        kbs=[KBConfig(name=KB, path=tmp_path / "rest-kb", kb_type="generic")],
        rest=True,
    )
    try:
        yield w
    finally:
        w.close()


def _rest_snapshot(w) -> dict[str, bytes]:
    root = w.kb_path(KB)
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def _rest_refused(response) -> dict:
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "VALIDATION_FAILED", detail
    rows = [r for r in detail.get("errors") or [] if r.get("field") == "importance"]
    assert rows and rows[0]["rule"] == "range", detail
    return detail


REST_BAD = [True, 10.7, 5.5, "１０", 99]


@pytest.mark.parametrize("value", REST_BAD, ids=repr)
def test_a_rest_create_refuses_a_bad_importance_with_the_row(rest, value):
    before = _rest_snapshot(rest)
    response = rest.client.post(
        "/api/entries", json={"kb": KB, "title": "R1", "body": "x", "importance": value}
    )
    _rest_refused(response)
    assert _rest_snapshot(rest) == before


@pytest.mark.parametrize("verb", ["PUT", "PATCH"])
@pytest.mark.parametrize("value", REST_BAD, ids=repr)
def test_a_rest_update_refuses_a_bad_importance_with_the_row(rest, verb, value):
    rest.add_entry(KB, _raw_file("note", "seed", "5"))
    before = _rest_snapshot(rest)
    body = (
        {"kb": KB, "importance": value}
        if verb == "PUT"
        else {"kb": KB, "field": "importance", "value": value}
    )
    response = rest.client.request(verb, "/api/entries/seed", json=body)
    _rest_refused(response)
    assert _rest_snapshot(rest) == before


@_ACCEPTS
def test_a_rest_create_and_update_store_a_whole_number_as_sent(rest):
    response = rest.client.post(
        "/api/entries", json={"kb": KB, "title": "R1", "body": "x", "importance": 7}
    )
    assert response.status_code in (200, 201), response.text
    (path,) = sorted(rest.kb_path(KB).rglob("*.md"))
    assert _stored_value(path) == 7
    entry_id = response.json()["id"]
    response = rest.client.put(f"/api/entries/{entry_id}", json={"kb": KB, "importance": 3})
    assert response.status_code == 200, response.text
    assert _stored_value(path) == 3


# -- a write that sets null or blank: one answer on every type -----------------------------
#
# Null is absent (no row), so over MCP an update with `"importance": null` is accepted
# on a note and on an event alike, and the file then holds no number (on 0.26.3 an event
# refused it). On the command line `-f importance=` and `-f importance=null` are text,
# not null: a string that is not a number, refused on every type.


@etypes
def test_an_mcp_update_setting_importance_null_is_accepted_on_every_type(world, etype):
    from pyrite.utils.yaml import load_yaml

    path = world.add_entry(KB, _raw_file(etype, "seed", "5"))
    code, res = _surface_call(world, "mcp", "update", etype, value=None)
    assert code == 0, res
    assert load_yaml(path.read_text().split("---", 2)[1]).get("importance") is None


@etypes
@pytest.mark.parametrize("text", ["", "null"], ids=["blank", "null-text"])
def test_a_cli_update_setting_importance_to_blank_or_null_text_is_refused(world, etype, text):
    path = world.add_entry(KB, _raw_file(etype, "seed", "5"))
    before = path.read_bytes()
    code, res = _surface_call(world, "cli", "update", etype, value=text)
    _assert_refused(code, res)
    assert path.read_bytes() == before


# -- a CSV import judges the cell's text with the same rule ----------------------------------
#
# A CSV cell is text, sent as given (#1588): its verdict is the KINDS row whose JSON value is
# that text (`mcp_set`), so the cells are derived from the table, not listed. One exception,
# CSV's own: an empty cell is no value at all, so it is absent, as before (the default 5 is
# written, as for every create without importance).
CSV_KINDS = sorted(k for k, row in KINDS.items() if isinstance(row[2], str))


def _csv(rows: list[dict]) -> bytes:
    import csv
    import io

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=["title", "body", "importance"])
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def _rest_import(w, name: str, payload: bytes, fmt: str) -> dict:
    response = w.client.post(
        "/api/entries/import",
        params={"kb": KB, "format": fmt},
        files={"file": (name, payload, "text/plain")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _imported_file(w, entry_id: str) -> Path:
    (path,) = [p for p in w.kb_path(KB).rglob("*.md") if p.stem == entry_id]
    return path


@pytest.mark.parametrize("kind", CSV_KINDS)
def test_a_csv_import_judges_the_cell_as_given_and_keeps_its_siblings(rest, kind):
    """A refused row is reported beside its title with the importance row; the good row
    beside it is imported (the per-item answer every import gives)."""
    cell = KINDS[kind][2]
    verdict = "absent" if cell == "" else KINDS[kind][5]
    body = _rest_import(
        rest,
        "x.csv",
        _csv(
            [
                {"title": "Sibling", "body": "x", "importance": "7"},
                {"title": "Cell", "body": "x", "importance": cell},
            ]
        ),
        "csv",
    )
    assert _stored_value(_imported_file(rest, "sibling")) == 7
    if verdict == "bad":
        assert body["imported"] == 1 and body["errors"] == 1, body
        (refused,) = body["error_details"]
        assert refused["title"] == "Cell" and refused["error_code"] == "VALIDATION_FAILED", body
        assert [r["rule"] for r in refused["errors"] if r["field"] == "importance"] == ["range"]
        assert not [p for p in rest.kb_path(KB).rglob("*.md") if p.stem == "cell"]
    else:
        assert body["imported"] == 2 and body["errors"] == 0, body
        path = _imported_file(rest, "cell")
        if verdict == "good":
            assert _stored_is_in_range(path), path.read_text()
        else:
            assert _stored_value(path) == 5  # an empty cell: no value, the create default


@pytest.mark.parametrize("value", ["10.7", "true", "high", "+5", " 5 "])
def test_a_csv_import_and_a_json_import_answer_a_bad_row_alike(rest, value):
    rows = [
        {"title": "Sibling", "body": "x", "importance": "7"},
        {"title": "Cell", "body": "x", "importance": value},
    ]
    from_csv = _rest_import(rest, "x.csv", _csv(rows), "csv")
    for row in rows:
        row["title"] += " J"
    from_json = _rest_import(rest, "x.json", json.dumps(rows).encode(), "json")

    def shape(body):
        return (
            body["imported"],
            body["errors"],
            [(d["error_code"], d["errors"]) for d in body["error_details"]],
        )

    assert shape(from_csv) == shape(from_json), (from_csv, from_json)

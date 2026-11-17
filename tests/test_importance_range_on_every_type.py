"""A write never stores an importance outside 1-10, on any type, by any surface (#1537).

The property: ``importance`` is a base field, 1 to 10 on every entry. A create
or an update that sets it outside that range is refused with
``VALIDATION_FAILED``, an ``errors`` row naming ``importance``, and no file
written or changed -- on every entry type and on every surface (the CLI's
``-f importance=`` and MCP ``kb_create`` / ``kb_update``). A value the file
already holds is the file's: an update of another field succeeds and reports
it ("violation already on disk; kept", design principle 2).

Before #1537 only ``EventEntry`` carried the rule, so a ``person`` stored
``importance: 99`` and ``pyrite qa validate`` then reported what Pyrite had just
written. The rule now sits on ``Entry.validation_errors`` (one place, design
principle 8), and the one write gate (``KBService._validate_write``) applies it
to every type.

The types are not listed by hand: the core registry (``ENTRY_TYPE_REGISTRY``),
every plugin-declared class (``get_registry().get_all_entry_types()``), and a
type a ``kb.yaml`` declares alone (``GenericEntry``). A type added to any of
them is covered by ``test_every_entry_class_carries_the_rule`` without a new
line here. The ``event`` rows are controls: they passed before the fix and
must still pass.
"""

from __future__ import annotations

import json
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
# that is accepted never leaves an importance outside 1-10 in the file. One row
# per kind, each with the verdict the property gives it by hand (not read off the
# code), so a new kind of value cannot be added without saying what it must do.
#
#   file:       "good" (a number in 1-10, or numeric text), "absent" (blank or null:
#               a guest in a person's file does not call a blank key a violation),
#               "bad" (everything else: kept and reported when already on disk,
#               refused by `pyrite add`)
#   cli_set:    the verdict of the `-f importance=<text>` text as text ("null" and
#               "" are strings on the command line)
#   mcp_set:    the verdict of the JSON value
#   create_*:   "refused" for a number out of range, "safe" for anything else: a
#               create turns a non-number into the default or into 1 or 0 in the
#               model before the check (a known limit, not part of this fix), so
#               the property is "refused, or what is stored is in 1-10"
_I = 123456789012345678901234567890
KINDS = {
    # kind: (yaml, cli text, json value, file, cli_set, mcp_set, create_cli, create_mcp)
    "in_range_7": ("7", "7", 7, "good", "good", "good", "safe", "safe"),
    "out_99": ("99", "99", 99, "bad", "bad", "bad", "refused", "refused"),
    "zero": ("0", "0", 0, "bad", "bad", "bad", "refused", "refused"),
    "big_30_digits": (str(_I), str(_I), _I, "bad", "bad", "bad", "refused", "refused"),
    "float_5.0": ("5.0", "5.0", 5.0, "good", "good", "good", "safe", "safe"),
    "float_10.0": ("10.0", "10.0", 10.0, "good", "good", "good", "safe", "safe"),
    "float_5.5": ("5.5", "5.5", 5.5, "good", "good", "good", "safe", "safe"),
    "inf": (".inf", ".inf", float("inf"), "bad", "bad", "bad", "safe", "safe"),
    "-inf": ("-.inf", "-.inf", float("-inf"), "bad", "bad", "bad", "safe", "safe"),
    "nan": (".nan", ".nan", float("nan"), "bad", "bad", "bad", "safe", "safe"),
    "true": ("true", "true", True, "bad", "bad", "bad", "safe", "safe"),
    "false": ("false", "false", False, "bad", "bad", "bad", "refused", "refused"),
    "null": ("null", "null", None, "absent", "bad", "absent", "safe", "safe"),
    "blank": ("", "", None, "absent", "bad", "absent", "safe", "safe"),
    "empty_string": ('""', "", "", "bad", "bad", "bad", "safe", "safe"),
    "str_5": ('"5"', "5", "5", "good", "good", "good", "safe", "safe"),
    "str_spaces_5": ('" 5 "', " 5 ", " 5 ", "good", "good", "good", "safe", "safe"),
    "str_05": ('"05"', "05", "05", "good", "good", "good", "safe", "safe"),
    "high": ("high", "high", "high", "bad", "bad", "bad", "safe", "safe"),
    "abc": ('"abc"', "abc", "abc", "bad", "bad", "bad", "safe", "safe"),
    "list": ("[5]", "[5]", [5], "bad", "bad", "bad", "safe", "safe"),
    "mapping": ("{a: 1}", "{a: 1}", {"a": 1}, "bad", "bad", "bad", "safe", "safe"),
    "date": ("2026-01-02", "2026-01-02", "2026-01-02", "bad", "bad", "bad", "safe", "safe"),
}
ETYPES = ["note", "event"]
kinds = pytest.mark.parametrize("kind", sorted(KINDS))
etypes = pytest.mark.parametrize("etype", ETYPES)
# An update that leaves a 30-digit importance on disk writes the file, then the index
# upsert raises OverflowError (pyrite/storage/crud.py -> SQLite INTEGER), so the command
# answers NO_OUTCOME / INTERNAL. Same on dev; outside this change (the check is a row).
_INDEX_OVERFLOW = pytest.mark.xfail(
    strict=True, reason="index upsert overflows SQLite INTEGER on a 30-digit value on disk"
)


def _raw_file(etype: str, entry_id: str, raw: str | None) -> str:
    lines = ["---", f"id: {entry_id}", f"type: {etype}", f"title: {entry_id}"]
    if etype == "event":
        lines.append("date: 2026-01-02")
    if raw is not None:
        lines.append("importance:" + (f" {raw}" if raw else ""))
    return "\n".join([*lines, "---", "", "Body.", ""])


def _stored_is_in_range(path: Path) -> bool:
    """What the file now holds is a number (or numeric text) in 1-10."""
    from pyrite.utils.yaml import load_yaml

    value = load_yaml(path.read_text().split("---", 2)[1]).get("importance")
    if isinstance(value, bool) or value is None:
        return False
    try:
        return 1 <= float(str(value).strip()) <= 10
    except ValueError:
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


@etypes
@surface
@pytest.mark.parametrize(
    "kind",
    [
        pytest.param(k, marks=[_INDEX_OVERFLOW] if k == "big_30_digits" else [])
        for k in sorted(KINDS)
    ],
)
def test_a_value_already_on_disk_never_stops_an_update_of_another_field(world, etype, surf, kind):
    yaml_text, _cli, _mcp, file, *_ = KINDS[kind]
    path = world.add_entry(KB, _raw_file(etype, "seed", yaml_text))
    before_text = path.read_text()
    code, res = _surface_call(world, surf, "update", etype, body="changed")
    _no_crash(code, res)
    assert code == 0, res
    assert (file == "bad") == _kept(res), res  # kept and reported only when it is a violation
    text = path.read_text()
    assert "changed" in text
    # the importance line itself is not touched by a write that did not set it
    assert [ln for ln in text.splitlines() if ln.startswith("importance:")] == [
        ln for ln in before_text.splitlines() if ln.startswith("importance:")
    ]


@etypes
@surface
@kinds
def test_a_set_importance_is_refused_or_in_1_to_10(world, etype, surf, kind):
    _y, cli, mcp, _f, cli_set, mcp_set, *_ = KINDS[kind]
    path = world.add_entry(KB, _raw_file(etype, "seed", "5"))
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
            assert _stored_is_in_range(path)


@etypes
@surface
@kinds
def test_a_created_importance_is_refused_or_stored_in_1_to_10(world, etype, surf, kind):
    _y, cli, mcp, _f, _cs, _ms, create_cli, create_mcp = KINDS[kind]
    verdict = create_cli if surf == "cli" else create_mcp
    before = _snapshot(world)
    code, res = _surface_call(world, surf, "create", etype, value=cli if surf == "cli" else mcp)
    _no_crash(code, res)
    if verdict == "refused":
        _assert_refused(code, res)
        assert _snapshot(world) == before
    elif code == 0:
        (path,) = _md_files(world)
        assert _stored_is_in_range(path), path.read_text()


@etypes
@kinds
def test_add_refuses_a_file_holding_a_bad_value_and_accepts_the_rest(world, tmp_path, etype, kind):
    yaml_text, _cli, _mcp, file, *_ = KINDS[kind]
    src = tmp_path / "src.md"
    src.write_text(_raw_file(etype, "seed", yaml_text))
    before = _snapshot(world)
    res = world.cli("add", str(src), "-k", KB, "--format", "json")
    body = _json(res.output)
    _no_crash(res.exit_code, body)
    if file == "bad":
        _assert_refused(res.exit_code, body)
        assert _snapshot(world) == before
    else:
        assert res.exit_code == 0, body


@etypes
@kinds
def test_the_check_never_raises_and_says_row_or_nothing(etype, kind):
    from pyrite.models.core_types import get_entry_class
    from pyrite.schema import validate_importance

    yaml_text, _cli, mcp, file, *_ = KINDS[kind]
    entry = get_entry_class(etype).from_markdown(_raw_file(etype, "seed", yaml_text))
    rows = [r for r in entry.validation_errors() if r.get("field") == "importance"]
    assert bool(rows) == (file == "bad"), rows
    assert validate_importance(mcp) is (file == "good")

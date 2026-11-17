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
    return [pytest.param(c, id=i) for i, c in sorted(classes.items())]


@pytest.mark.parametrize("cls", _every_class())
@pytest.mark.parametrize("value", [0, 11, -5, 99])
def test_every_entry_class_carries_the_rule(cls, value):
    entry = cls(id="x", title="X", importance=value)
    rows = [r for r in entry.validation_errors() if r.get("field") == "importance"]
    assert rows, f"{cls.__name__} accepted importance={value}"
    assert rows[0]["rule"] == "range"
    assert rows[0]["message"] == f"Importance must be 1-10, got: {value}"
    assert rows[0]["expected"] == "1-10"


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

"""A field the KB's schema declares as a list holds a list on every surface (#1502).

The property (the maintainer's decision on #1502, 2026-11-16, option A): a
scalar sent to a field whose ``KBSchema.type_fields`` type is ``list``,
``multi-select`` or ``tags`` is stored as a one-item list, by one coercion in
the service, whichever surface sent it (CLI ``create``/``update -f``, MCP
``kb_create``/``kb_bulk_create``/``kb_update``, REST ``POST``/``PUT``/
``PATCH``). A JSON array is stored as sent. A value is never split on commas.
An empty value stays empty. ``qa validate`` reports a declared list field
whose file value is not a list, and nothing rewrites that file unless asked.

The declared list fields are enumerated from ``KBSchema.type_fields`` over the
core types and every shipped preset's types (``_shipped_list_fields``), so a
list field declared later gets its cells here without a hand-kept table.
Expected values are written by hand from the property: the file's YAML, read
with ``yaml.safe_load``, holds ``[value]``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from pyrite.config import KBConfig
from pyrite.plugins import get_registry
from tests._world import World, build_world

KB = "lists"
LIST_TYPES = ("list", "multi-select", "tags")

# --------------------------------------------------------------------------
# The enumeration: every declared list field, from `KBSchema.type_fields`
# --------------------------------------------------------------------------


def _presets() -> dict[str, dict[str, Any]]:
    """Every shipped preset: the built-in templates and the plugins' presets."""
    from pyrite.cli.init_command import BUILTIN_TEMPLATES

    presets: dict[str, dict[str, Any]] = {}
    try:
        presets.update(get_registry().get_all_kb_presets())
    except AttributeError:  # pragma: no cover - a registry without presets
        pass
    presets.update(BUILTIN_TEMPLATES)
    return presets


def _shipped_list_fields() -> list[tuple[str, str, str]]:
    """``(preset, entry_type, field)`` for every list-typed field a shipped
    preset's KB declares through ``type_fields`` (core < plugin < kb.yaml),
    one row per ``(entry_type, field)``: the first preset, in name order,
    that declares it. Managed fields are left out (a create refuses them
    before any coercion, #1010)."""
    from pyrite.schema import CORE_TYPES
    from pyrite.schema.kb_schema import KBSchema
    from pyrite.services.kb_service import KBService

    rows: dict[tuple[str, str], str] = {}
    for name, preset in sorted(_presets().items()):
        schema = KBSchema.from_dict(preset)
        for entry_type in sorted(set(CORE_TYPES) | set(schema.types)):
            managed = KBService._field_sets(entry_type, None)[2]
            for field, declared in schema.type_fields(entry_type).items():
                if declared.field_type in LIST_TYPES and field not in managed:
                    rows.setdefault((entry_type, field), name)
    return [(preset, entry_type, field) for (entry_type, field), preset in sorted(rows.items())]


SHIPPED = _shipped_list_fields()


def test_the_enumeration_finds_the_core_list_fields():
    """The walk is not vacuous: the two core list fields are in it."""
    found = {(t, f) for _p, t, f in SHIPPED}
    assert {("person", "affiliations"), ("event", "actors")} <= found


# --------------------------------------------------------------------------
# The world: one KB, built from a preset's kb.yaml or a kb.yaml given here
# --------------------------------------------------------------------------


def _kb_yaml_from_preset(preset: str) -> str:
    data = {k: v for k, v in _presets()[preset].items() if k != "directories"}
    data["name"] = KB
    return yaml.safe_dump(data, sort_keys=False)


def _world(tmp_path: Path, kb_yaml: str | None, *, rest: bool = True) -> World:
    kb_path = tmp_path / "kb"
    kb_path.mkdir(parents=True, exist_ok=True)
    if kb_yaml is not None:
        (kb_path / "kb.yaml").write_text(kb_yaml, encoding="utf-8")
    return build_world(
        tmp_path, kbs=[KBConfig(name=KB, path=kb_path, kb_type="generic")], rest=rest
    )


def _required(world: World, entry_type: str) -> dict[str, Any]:
    """Placeholder values for the type's required fields: kb.yaml's, and an
    event's `date`, which the event model itself requires."""
    schema = world.config.get_kb(KB).kb_schema
    ts = schema.types.get(entry_type)
    names = ["date"] if entry_type == "event" else []
    names += [*(ts.required if ts else [])]
    names += [n for n, f in (ts.fields.items() if ts else []) if f.required]
    return {n: ("2024-03-01" if n == "date" else "x") for n in names if n != "title"}


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), text
    return yaml.safe_load(text.split("---", 2)[1]) or {}


def _file_of(world: World, entry_id: str) -> Path:
    found = list(world.kb_path(KB).rglob(f"{entry_id}.md"))
    assert len(found) == 1, (entry_id, found)
    return found[0]


def _stored(world: World, entry_id: str, field: str, aliases: tuple[str, ...] = ()) -> Any:
    """The field's value as the file holds it, at whichever one spelling the
    file uses (its own name, an alias, or either under `metadata:`)."""
    fm = _frontmatter(_file_of(world, entry_id))
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    spelled = [src[name] for name in (field, *aliases) for src in (fm, meta) if name in src]
    assert len(spelled) == 1, (field, fm)
    return spelled[0]


def _aliases(entry_type: str, field: str) -> tuple[str, ...]:
    from pyrite.schema.field_aliases import field_aliases

    return tuple(a for a, t in field_aliases(entry_type).items() if t == field)


# --------------------------------------------------------------------------
# The surfaces: create and update, each sending one field value
# --------------------------------------------------------------------------


def _cli_value(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def _check_cli(result) -> dict[str, Any]:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _mcp_ok(result: dict[str, Any]) -> dict[str, Any]:
    assert not result.get("error") and not result.get("isError"), result
    return result


def create_cli(world, entry_type, title, fields):
    args = ["create", "-k", KB, "-t", entry_type, "--title", title, "-b", "x", "--format", "json"]
    for k, v in fields.items():
        args += ["-f", f"{k}={_cli_value(v)}"]
    return _check_cli(world.cli(*args))["entry_id"]


def create_mcp(world, entry_type, title, fields):
    server = world.mcp(tier="write")
    args = {"kb_name": KB, "entry_type": entry_type, "title": title, "body": "x", **fields}
    return _mcp_ok(server._dispatch_tool("kb_create", args))["entry_id"]


def create_mcp_bulk(world, entry_type, title, fields):
    server = world.mcp(tier="write")
    item = {"entry_type": entry_type, "title": title, "body": "x", **fields}
    result = _mcp_ok(server._dispatch_tool("kb_bulk_create", {"kb_name": KB, "entries": [item]}))
    (row,) = result["results"]
    assert row.get("created"), result
    return row["entry_id"]


def create_rest(world, entry_type, title, fields):
    # REST create's free fields travel in the `metadata` envelope (the model
    # has no free top-level keys, `extra="forbid"`).
    body = {"kb": KB, "entry_type": entry_type, "title": title, "body": "x", "metadata": fields}
    res = world.client.post("/api/entries", json=body)
    assert res.status_code in (200, 201), res.text
    return res.json()["id"] if "id" in res.json() else res.json()["entry_id"]


def update_cli(world, entry_id, field, value):
    _check_cli(
        world.cli(
            "update", entry_id, "-k", KB, "-f", f"{field}={_cli_value(value)}", "--format", "json"
        )
    )


def update_mcp(world, entry_id, field, value):
    server = world.mcp(tier="write")
    _mcp_ok(server._dispatch_tool("kb_update", {"kb_name": KB, "entry_id": entry_id, field: value}))


def update_rest_put(world, entry_id, field, value):
    res = world.client.put(f"/api/entries/{entry_id}", json={"kb": KB, "metadata": {field: value}})
    assert res.status_code == 200, res.text


def update_rest_patch(world, entry_id, field, value):
    res = world.client.patch(
        f"/api/entries/{entry_id}", json={"kb": KB, "field": field, "value": value}
    )
    assert res.status_code == 200, res.text


CREATES: dict[str, Callable[..., str]] = {
    "cli.create": create_cli,
    "mcp.kb_create": create_mcp,
    "mcp.kb_bulk_create": create_mcp_bulk,
    "rest.post": create_rest,
}
UPDATES: dict[str, Callable[..., None]] = {
    "cli.update": update_cli,
    "mcp.kb_update": update_mcp,
    "rest.put": update_rest_put,
    "rest.patch": update_rest_patch,
}
SURFACES = sorted([*CREATES, *UPDATES])


def _seed(world: World, entry_type: str, entry_id: str) -> None:
    """An entry file without the field, for an update to set it on."""
    extra = "".join(f"{k}: '{v}'\n" for k, v in _required(world, entry_type).items())
    world.add_entry(
        KB, f"---\nid: {entry_id}\ntitle: Seeded\ntype: {entry_type}\n{extra}---\n\nText.\n"
    )


def _write(world: World, surface: str, entry_type: str, field: str, value: Any) -> str:
    """Send ``{field: value}`` for a new entry (a create surface) or an
    existing one (an update surface); return the entry id."""
    if surface in CREATES:
        fields = {**_required(world, entry_type), field: value}
        return CREATES[surface](world, entry_type, "Sent Value", fields)
    _seed(world, entry_type, "seeded")
    UPDATES[surface](world, "seeded", field, value)
    return "seeded"


# --------------------------------------------------------------------------
# The writes
# --------------------------------------------------------------------------


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize(("preset", "entry_type", "field"), SHIPPED)
def test_a_scalar_sent_to_a_declared_list_field_is_stored_as_one_item(
    tmp_path: Path, preset: str, entry_type: str, field: str, surface: str
):
    world = _world(tmp_path, _kb_yaml_from_preset(preset), rest=surface.startswith("rest"))
    try:
        entry_id = _write(world, surface, entry_type, field, "City A")
        assert _stored(world, entry_id, field, _aliases(entry_type, field)) == ["City A"]
    finally:
        world.close()


@pytest.mark.parametrize("surface", SURFACES)
def test_a_value_with_a_comma_is_never_split(tmp_path: Path, surface: str):
    world = _world(tmp_path, None, rest=surface.startswith("rest"))
    try:
        entry_id = _write(world, surface, "person", "affiliations", "City A, Inc.")
        assert _stored(world, entry_id, "affiliations") == ["City A, Inc."]
    finally:
        world.close()


@pytest.mark.control(reason="a JSON array was already stored as sent on every surface")
@pytest.mark.parametrize("surface", SURFACES)
def test_a_json_array_is_stored_as_sent(tmp_path: Path, surface: str):
    world = _world(tmp_path, None, rest=surface.startswith("rest"))
    try:
        entry_id = _write(world, surface, "person", "affiliations", ["City A, Inc.", "Two"])
        assert _stored(world, entry_id, "affiliations") == ["City A, Inc.", "Two"]
    finally:
        world.close()


def _empty_cases() -> list[tuple[str, Any]]:
    # The CLI cannot send null (`-f k=null` is the text "null"); `""` it can.
    return [(s, v) for s in SURFACES for v in ("", None) if not (v is None and s.startswith("cli"))]


@pytest.mark.control(reason="an empty value was never wrapped; the coercion must not wrap it")
@pytest.mark.parametrize(("surface", "value"), _empty_cases())
def test_empty_values_stay_empty(tmp_path: Path, surface: str, value: Any):
    world = _world(tmp_path, None, rest=surface.startswith("rest"))
    try:
        entry_id = _write(world, surface, "person", "affiliations", value)
        fm = _frontmatter(_file_of(world, entry_id))
        meta = fm.get("metadata") or {}
        stored = fm.get("affiliations", meta.get("affiliations"))
        assert stored in (None, "", []), stored
    finally:
        world.close()


@pytest.mark.parametrize("surface", ["mcp.kb_create", "mcp.kb_update", "rest.put"])
def test_a_non_string_scalar_is_one_item(tmp_path: Path, surface: str):
    world = _world(tmp_path, None, rest=surface.startswith("rest"))
    try:
        entry_id = _write(world, surface, "person", "affiliations", 5)
        assert _stored(world, entry_id, "affiliations") == [5]
    finally:
        world.close()


# The spellings of one field: an alias and the `metadata:` envelope.


@pytest.mark.parametrize("surface", ["cli.create", "mcp.kb_create", "mcp.kb_update", "cli.update"])
def test_an_alias_spelling_is_coerced(tmp_path: Path, surface: str):
    """`participants` is the alias of `actors` on an event."""
    world = _world(tmp_path, None, rest=False)
    try:
        entry_id = _write(world, surface, "event", "participants", "person-a")
        assert _stored(world, entry_id, "actors", ("participants",)) == ["person-a"]
    finally:
        world.close()


@pytest.mark.parametrize("surface", ["mcp.kb_create", "mcp.kb_update"])
def test_the_metadata_envelope_spelling_is_coerced(tmp_path: Path, surface: str):
    world = _world(tmp_path, None, rest=False)
    try:
        entry_id = _write(world, surface, "person", "metadata", {"affiliations": "City A"})
        assert _stored(world, entry_id, "affiliations") == ["City A"]
    finally:
        world.close()


# The declaration layers nothing ships yet: kb.yaml list types and a plugin.

LAYERED_KB_YAML = f"""\
name: {KB}
kb_type: generic
types:
  dossier:
    fields:
      places: {{type: list}}
      codes: {{type: multi-select}}
      labels: {{type: tags}}
"""


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("field", ["places", "codes", "labels"])
def test_a_kb_yaml_declared_list_field_is_stored_as_one_item(
    tmp_path: Path, field: str, surface: str
):
    world = _world(tmp_path, LAYERED_KB_YAML, rest=surface.startswith("rest"))
    try:
        entry_id = _write(world, surface, "dossier", field, "City A")
        assert _stored(world, entry_id, field) == ["City A"]
    finally:
        world.close()


class _ListFieldPlugin:
    """A plugin declaring a list field through `get_field_schemas()` (none ships yet)."""

    name = "declares_list_field_1502"

    def get_field_schemas(self) -> dict[str, dict[str, dict]]:
        return {"topic": {"codes_1502": {"type": "list"}}}


@pytest.fixture
def list_plugin():
    reg = get_registry()
    reg.discover()
    reg.register(_ListFieldPlugin())
    yield
    reg._plugins.pop(_ListFieldPlugin.name, None)
    reg._conformance_cache.pop(_ListFieldPlugin.name, None)
    reg._aliases_changed()


@pytest.mark.parametrize("surface", SURFACES)
def test_a_plugin_declared_list_field_is_stored_as_one_item(
    tmp_path: Path, surface: str, list_plugin
):
    world = _world(tmp_path, None, rest=surface.startswith("rest"))
    try:
        entry_id = _write(world, surface, "topic", "codes_1502", "City A")
        assert _stored(world, entry_id, "codes_1502") == ["City A"]
    finally:
        world.close()


@pytest.mark.control(reason="a field kb.yaml types as text was never coerced; it must stay so")
@pytest.mark.parametrize("surface", ["cli.create", "mcp.kb_create", "mcp.kb_update"])
def test_a_kb_yaml_that_redeclares_a_core_list_as_text_wins(tmp_path: Path, surface: str):
    kb_yaml = (
        f"name: {KB}\nkb_type: generic\ntypes:\n  person:\n    fields:\n"
        "      affiliations: {type: text}\n"
    )
    world = _world(tmp_path, kb_yaml, rest=False)
    try:
        entry_id = _write(world, surface, "person", "affiliations", "City A")
        assert _stored(world, entry_id, "affiliations") == "City A"
    finally:
        world.close()


# --------------------------------------------------------------------------
# QA reports a scalar already on disk; nothing rewrites it
# --------------------------------------------------------------------------

SCALAR_ON_DISK = (
    "---\nid: person-a\ntitle: Person A\ntype: person\naffiliations: City A\n---\n\nText.\n"
)


def _qa_findings(world: World, field: str) -> list[dict[str, Any]]:
    # An error-severity finding makes `qa validate` exit non-zero; the
    # report is still on stdout.
    out = json.loads(world.cli("qa", "validate", KB, "--format", "json").stdout)
    return [
        i
        for i in out["issues"]
        if i.get("entry_id") == "person-a"
        and i.get("rule") == "schema_violation"
        and i.get("field") == field
    ]


@pytest.mark.parametrize("enforce", [False, True])
@pytest.mark.parametrize(("preset", "entry_type", "field"), SHIPPED)
def test_qa_reports_a_scalar_in_a_declared_list_field(
    tmp_path: Path, preset: str, entry_type: str, field: str, enforce: bool
):
    data = yaml.safe_load(_kb_yaml_from_preset(preset))
    data.setdefault("validation", {})
    data["validation"] = {**(data["validation"] or {}), "enforce": enforce}
    world = _world(tmp_path, yaml.safe_dump(data), rest=False)
    try:
        extra = "".join(f"{k}: '{v}'\n" for k, v in _required(world, entry_type).items())
        world.add_entry(
            KB,
            f"---\nid: person-a\ntitle: A\ntype: {entry_type}\n{extra}{field}: City A\n---\n\nT.\n",
        )
        findings = _qa_findings(world, field)
        assert len(findings) == 1, findings
        (finding,) = findings
        assert finding["severity"] == ("error" if enforce else "warning")
        assert "expected list" in finding["message"], finding
    finally:
        world.close()


@pytest.mark.control(reason="a list on disk was never a finding")
def test_qa_does_not_report_a_list_in_a_declared_list_field(tmp_path: Path):
    world = _world(tmp_path, f"name: {KB}\nkb_type: generic\n", rest=False)
    try:
        world.add_entry(
            KB, SCALAR_ON_DISK.replace("affiliations: City A", "affiliations: [City A]")
        )
        assert _qa_findings(world, "affiliations") == []
    finally:
        world.close()


@pytest.mark.control(reason="an update of another field never rewrote the scalar (ADR-0042)")
@pytest.mark.parametrize("surface", sorted(UPDATES))
def test_an_unrelated_update_leaves_the_scalar_byte_identical(tmp_path: Path, surface: str):
    world = _world(tmp_path, f"name: {KB}\nkb_type: generic\n", rest=surface.startswith("rest"))
    try:
        path = world.add_entry(KB, SCALAR_ON_DISK)
        UPDATES[surface](world, "person-a", "role", "Director")
        after = path.read_text(encoding="utf-8")
        assert "\naffiliations: City A\n" in after, after
        assert after.replace("role: Director\n", "") == SCALAR_ON_DISK, after
    finally:
        world.close()


@pytest.mark.control(reason="a read or a sync never wrote a file")
def test_sync_and_read_do_not_rewrite_a_scalar(tmp_path: Path):
    world = _world(tmp_path, f"name: {KB}\nkb_type: generic\n", rest=True)
    try:
        path = world.add_entry(KB, SCALAR_ON_DISK)
        before = path.read_bytes()
        assert world.cli("index", "sync").exit_code == 0
        _check_cli(world.cli("get", "person-a", "-k", KB, "--format", "json"))
        _mcp_ok(world.mcp()._dispatch_tool("kb_get", {"kb_name": KB, "entry_id": "person-a"}))
        assert world.client.get(f"/api/entries/person-a?kb={KB}").status_code == 200
        _check_cli(world.cli("qa", "validate", KB, "--format", "json"))
        assert path.read_bytes() == before
    finally:
        world.close()


@pytest.mark.control(reason="a whole file given to `add` keeps its own frontmatter (not coerced)")
def test_add_of_a_whole_file_is_not_coerced(tmp_path: Path):
    world = _world(tmp_path, f"name: {KB}\nkb_type: generic\n", rest=False)
    try:
        src = tmp_path / "given.md"
        src.write_text(SCALAR_ON_DISK, encoding="utf-8")
        _check_cli(world.cli("add", str(src), "-k", KB, "--format", "json"))
        assert _stored(world, "person-a", "affiliations") == "City A"
    finally:
        world.close()


def test_a_write_warns_about_a_scalar_already_on_disk(tmp_path: Path):
    """The write path runs the same check: an update of another field keeps
    the scalar and says so (a warning, never a refusal, even under enforce)."""
    kb_yaml = f"name: {KB}\nkb_type: generic\nvalidation:\n  enforce: true\n"
    world = _world(tmp_path, kb_yaml, rest=False)
    try:
        world.add_entry(KB, SCALAR_ON_DISK)
        out = _check_cli(
            world.cli("update", "person-a", "-k", KB, "-f", "role=Director", "--format", "json")
        )
        warned = [w for w in out.get("warnings", []) if w.get("field") == "affiliations"]
        assert warned and warned[0].get("expected") == "list", out
    finally:
        world.close()


# --------------------------------------------------------------------------
# The CLI: no comma split, and a repeated -f key is refused, not half-kept
# --------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["create", "update"])
def test_a_repeated_field_flag_is_refused(tmp_path: Path, command: str):
    world = _world(tmp_path, None, rest=False)
    try:
        if command == "create":
            args = ["create", "-k", KB, "-t", "person", "--title", "Person C", "-b", "x"]
        else:
            world.add_entry(KB, SCALAR_ON_DISK)
            args = ["update", "person-a", "-k", KB]
        result = world.cli(
            *args, "-f", "affiliations=one", "-f", "affiliations=two", "--format", "json"
        )
        assert result.exit_code != 0, result.output
        out = json.loads(result.stdout)
        assert out["error_code"] == "VALIDATION_FAILED", out
        assert '["' in out["error"] or "JSON" in out["error"], out
        if command == "create":
            assert not list(world.kb_path(KB).rglob("person-c.md"))
        else:
            assert _frontmatter(_file_of(world, "person-a"))["affiliations"] == "City A"
    finally:
        world.close()


def test_append_with_a_comma_adds_one_item(tmp_path: Path):
    world = _world(tmp_path, None, rest=False)
    try:
        world.add_entry(KB, SCALAR_ON_DISK.replace("affiliations: City A", "affiliations: [a]"))
        _check_cli(
            world.cli(
                "update",
                "person-a",
                "-k",
                KB,
                "--append",
                "affiliations=City A, Inc.",
                "--format",
                "json",
            )
        )
        assert _stored(world, "person-a", "affiliations") == ["a", "City A, Inc."]
    finally:
        world.close()

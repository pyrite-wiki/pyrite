"""A field-based create writes every field the type's schema declares (#86).

The maintainer's decision on #86 (2026-10-02, narrowed 2026-10-20): a new
file carries every field the type's *schema* declares -- ``kb.yaml``, the
plugin's ``get_field_schemas()`` and the core ``CORE_TYPES`` -- not the Python
class's dataclass fields; the caller's value where one was sent (an empty value
counts as given), the empty spelling otherwise, never a managed field and never
a second spelling of one field. A file-based create (``pyrite add``) writes the
file's keys and nothing else.

Oracles: the declared fields and their empty spellings are written out by hand
below, from the declarations (the kb.yaml this test writes, ``CORE_TYPES``), not
from the merge under test. "Exactly" is checked against what the type's own
class writes for the same request (``_frontmatter_for_file`` of an entry built
in memory), which this change does not touch: the file holds those keys plus
the declared ones, and nothing else.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

import pyrite.models  # noqa: F401  (registers core types)
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.models.core_types import ENTRY_TYPE_REGISTRY, get_entry_class
from pyrite.models.factory import build_entry
from pyrite.schema.field_schema import FieldSchema
from pyrite.plugins import get_registry
from pyrite.schema.reserved import RESERVED_FIELD_NAMES
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.utils.frontmatter import load_frontmatter

KB = "declared"

#: One field of each kb.yaml field type, with the spelling a create writes when
#: the caller sends nothing (the 10-02 groom, confirmed 10-20): '' for text and
#: select, [] for a list, a multi-select or tags, null for a number, a date, a
#: datetime, a checkbox or a reference; the declared default when there is one.
KB_YAML_FIELDS = """\
      x_text: {type: text}
      x_select: {type: select, options: [a, b]}
      x_multi: {type: multi-select, options: [a, b]}
      x_list: {type: list}
      x_tags: {type: tags}
      x_number: {type: number}
      x_date: {type: date}
      x_datetime: {type: datetime}
      x_checkbox: {type: checkbox}
      x_ref: {type: object-ref}
      x_default: {type: select, options: [low, high], default: low}
"""
KB_YAML_EMPTIES: dict[str, Any] = {
    "x_text": "",
    "x_select": "",
    "x_multi": [],
    "x_list": [],
    "x_tags": [],
    "x_number": None,
    "x_date": None,
    "x_datetime": None,
    "x_checkbox": None,
    "x_ref": None,
    "x_default": "low",
    # named in `optional:` with no field definition: a text field
    "x_bare": "",
}

#: What CORE_TYPES declares for each core type, less the base Entry fields
#: (tags, links, importance: written when set, as before), with its empty
#: spelling: `str` is text, `list[...]` a list, `dict` an object (null).
CORE_EMPTIES: dict[str, dict[str, Any]] = {
    "note": {},
    "topic": {},
    "person": {"role": "", "affiliations": []},
    "organization": {"org_type": "", "jurisdiction": "", "founded": ""},
    "event": {"date": "", "location": "", "status": "", "actors": []},
    "document": {"date": "", "author": "", "document_type": "", "url": ""},
    "relationship": {"source_entity": "", "target_entity": "", "relationship_type": ""},
    "timeline": {"date_range": ""},
    "collection": {
        "source_type": "",
        "description": "",
        "icon": "",
        "view_config": None,
        "folder_path": "",
    },
}


def _all_types() -> list[str]:
    reg = get_registry()
    reg.discover()
    names = set(ENTRY_TYPE_REGISTRY)
    for plugin in reg._plugins.values():
        try:
            names.update(plugin.get_entry_types())
        except Exception:  # a plugin that fails to list types is not this test's concern
            pass
    return sorted(names)


#: What a type refuses to create without (its own validator), sent in every case.
NEEDS: dict[str, dict[str, Any]] = {
    t: {"date": "2026-01-01"}
    for t in ("event", "investigation_event", "legal_action", "transaction")
}


def _value_of(values: dict[str, Any], name: str) -> Any:
    """A field's value at either spelling (ADR-0051: `metadata.k` spells `k`)."""
    nested = values.get("metadata") or {}
    return values[name] if name in values else nested[name]


def _managed(entry_type: str) -> frozenset[str]:
    """The fields a create refuses: the service's set for the type, read from
    the one place it is computed (``KBService._field_sets``)."""
    return KBService._field_sets(entry_type, None)[2]


def _config(tmp_path: Path, kb_yaml: str) -> PyriteConfig:
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(kb_yaml, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config


def _with_service(config: PyriteConfig, fn):
    db = PyriteDB(config.settings.index_path)
    try:
        return fn(KBService(config, db))
    finally:
        db.close()


def _file_values(config: PyriteConfig, entry_id: str) -> tuple[Path, dict[str, Any]]:
    found = sorted(config.knowledge_bases[0].path.rglob(f"{entry_id}.md"))
    assert len(found) == 1, found
    parsed = load_frontmatter(found[0].read_text(encoding="utf-8"))
    assert parsed is not None
    return found[0], {str(k): v for k, v in parsed[0].items()}


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, str):
        return str(value)
    return value


def _kb_yaml_declaring(entry_type: str, *, extra_fields: str = "") -> str:
    managed = sorted(_managed(entry_type) - RESERVED_FIELD_NAMES)
    managed_fields = "".join(f"      {name}: {{type: text}}\n" for name in managed)
    return (
        f"name: {KB}\nkb_type: generic\ntypes:\n  {entry_type}:\n"
        f"    optional: [x_bare]\n    fields:\n"
        f"{KB_YAML_FIELDS}{managed_fields}{extra_fields}"
    )


# --------------------------------------------------------------------------
# Every registered type: the file carries exactly its schema's declared fields
# --------------------------------------------------------------------------


@pytest.mark.parametrize("entry_type", _all_types())
def test_a_title_only_create_writes_exactly_the_declared_fields(tmp_path: Path, entry_type: str):
    """kb.yaml declares one field of every field type (and, for a type with
    managed fields, each managed field too); a core type also has its
    CORE_TYPES fields. A create with only a title writes each declared field
    the class did not write, in its empty spelling, no managed field, and no
    key that is neither the class's nor declared."""
    config = _config(tmp_path, _kb_yaml_declaring(entry_type))
    needs = NEEDS.get(entry_type, {})
    spec = {"entry_type": entry_type, "title": "Probe", "body": "b", **needs}
    written = _with_service(config, lambda svc: svc.create(KB, dict(spec)))
    _path, got = _file_values(config, written.entry.id)

    # What the type's own class writes for this request (unchanged by #86).
    built = build_entry(entry_type, title="Probe", body="b", **needs)
    class_keys = set(built._frontmatter_for_file())
    declared = {**CORE_EMPTIES.get(entry_type, {}), **KB_YAML_EMPTIES}

    # A field is spelled at its name or under `metadata:` (ADR-0051); a typed
    # class writes one it has no attribute for under `metadata:`, sent or not.
    top = {k: v for k, v in got.items() if k != "metadata"}
    nested = got.get("metadata") or {}
    twice = sorted(set(top) & set(nested))
    assert not twice, f"{entry_type}: spelled twice: {twice}"
    spelled = {**nested, **top}

    missing = sorted(set(declared) - set(spelled))
    assert not missing, f"{entry_type}: declared fields not written: {missing}\nfile: {got}"
    for name, empty in declared.items():
        if name not in class_keys:
            value = _plain(spelled[name])
            assert value == empty, f"{entry_type}.{name}: {value!r} != {empty!r}"
    extra = sorted(set(spelled) - class_keys - set(declared))
    assert not extra, f"{entry_type}: keys neither the class's nor declared: {extra}"
    leaked = sorted((set(spelled) - class_keys) & _managed(entry_type))
    assert not leaked, f"{entry_type}: a create wrote managed fields: {leaked}"


# --------------------------------------------------------------------------
# The reported case: a software KB from `pyrite init -t software`
# --------------------------------------------------------------------------


@pytest.fixture
def software_kb(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    from pyrite.cli import app as cli_app

    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "PYRITE_CONFIG_DIR", "PYRITE_DATA_DIR"):
        monkeypatch.setenv(var, str(home))
    runner = CliRunner()
    kb_path = tmp_path / "sw"
    result = runner.invoke(
        cli_app, ["init", "-t", "software", "-p", str(kb_path), "-n", "sw1", "--no-examples"]
    )
    assert result.exit_code == 0, result.output

    def cli(*args: str):
        out = runner.invoke(cli_app, list(args))
        assert out.exit_code == 0, out.output
        return out.output

    return kb_path, cli


def _front(path: Path) -> dict[str, Any]:
    parsed = load_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    return {str(k): v for k, v in parsed[0].items()}


def test_cli_create_backlog_item_writes_kind_effort_and_assignee(software_kb):
    import json

    kb_path, cli = software_kb
    cli("create", "-k", "sw1", "-t", "backlog_item", "--title", "Item one", "--tags", "a")
    fm = _front(kb_path / "backlog" / "item-one.md")
    assert fm["kind"] == "" and fm["effort"] == "" and fm["assignee"] == "", fm
    assert fm["status"] == "proposed" and fm["priority"] == "medium", fm

    items = json.loads(cli("sw", "backlog", "-k", "sw1", "--format", "json"))
    items = items.get("items", items) if isinstance(items, dict) else items
    (item,) = [i for i in items if i.get("id") == "item-one"]
    assert item["kind"] == "" and item["effort"] == ""


def test_cli_create_with_an_empty_value_writes_it(software_kb):
    """`-f kind=` is a value sent empty: it counts as given (#720), and the
    class's `if self.kind:` used to drop it."""
    kb_path, cli = software_kb
    cli("create", "-k", "sw1", "-t", "backlog_item", "--title", "Item two", "-f", "kind=")
    fm = _front(kb_path / "backlog" / "item-two.md")
    assert "kind" in fm and fm["kind"] == "", fm


def test_mcp_kb_create_with_an_empty_value_writes_it(tmp_path: Path):
    from pyrite.server.mcp_server import PyriteMCPServer

    config = _config(
        tmp_path,
        f"name: {KB}\nkb_type: generic\ntypes:\n  backlog_item:\n    optional: [kind, effort]\n",
    )
    server = PyriteMCPServer(config, tier="write")
    try:
        result = server._dispatch_tool(
            "kb_create",
            {"kb_name": KB, "entry_type": "backlog_item", "title": "Via MCP", "kind": ""},
        )
    finally:
        server.close()
    assert result.get("created"), result
    _path, got = _file_values(config, result["entry_id"])
    assert got["kind"] == "" and got["effort"] == "", got


@pytest.mark.control(
    reason="holds today for a file without the empties; after #86 it must still hold for one with them"
)
def test_a_noop_update_of_a_created_file_changes_no_byte(tmp_path: Path):
    config = _config(tmp_path, _kb_yaml_declaring("backlog_item"))
    written = _with_service(
        config, lambda svc: svc.create(KB, {"entry_type": "backlog_item", "title": "Same"})
    )
    path, _got = _file_values(config, written.entry.id)
    before = path.read_bytes()
    _with_service(config, lambda svc: svc.update(written.entry.id, KB, {"title": "Same"}))
    assert path.read_bytes() == before


# --------------------------------------------------------------------------
# Regimes
# --------------------------------------------------------------------------


@pytest.mark.control(
    reason="a file-based create already adds no key; #86 must keep it so (ADR-0042 decision 13)"
)
def test_a_file_based_create_adds_no_key(tmp_path: Path):
    config = _config(tmp_path, _kb_yaml_declaring("person"))
    src = tmp_path / "incoming.md"
    src.write_text("---\nid: hand\ntitle: Hand\ntype: person\n---\n\nMine.\n", encoding="utf-8")
    _with_service(config, lambda svc: svc.add_entry_from_file(KB, src))
    _path, got = _file_values(config, "hand")
    assert not (set(got) & (set(KB_YAML_EMPTIES) | set(CORE_EMPTIES["person"]))), got


@pytest.mark.control(reason="nothing declared, nothing added: true before and after")
def test_a_type_with_no_schema_fields_gains_nothing(tmp_path: Path):
    config = _config(tmp_path, f"name: {KB}\nkb_type: generic\n")
    written = _with_service(
        config, lambda svc: svc.create(KB, {"entry_type": "topic", "title": "Bare"})
    )
    _path, got = _file_values(config, written.entry.id)
    assert set(got) == set(build_entry("topic", title="Bare")._frontmatter_for_file())


def test_kb_yaml_redeclaring_a_core_field_wins_and_writes_it_once(tmp_path: Path):
    """kb.yaml's declaration wins over the core one (its default applies), and
    the field is written once. A field the model holds as an attribute keeps
    the model's type when it is left empty: `affiliations` is a list on
    `PersonEntry`, so kb.yaml calling it text writes `[]`, never `''` (a value
    of the wrong type for the class that reads it)."""
    config = _config(
        tmp_path,
        f"name: {KB}\nkb_type: generic\ntypes:\n  person:\n    fields:\n"
        "      role: {type: select, options: [member, lead], default: member}\n"
        "      affiliations: {type: text}\n",
    )
    written = _with_service(
        config, lambda svc: svc.create(KB, {"entry_type": "person", "title": "P"})
    )
    path, got = _file_values(config, written.entry.id)
    assert got["role"] == "member" and got["affiliations"] == [], got
    text = path.read_text(encoding="utf-8")
    assert text.count("role") == 1 and text.count("affiliations") == 1, text


@pytest.mark.control(
    reason="a sent value is written today; the empties must not replace or double it"
)
def test_a_value_sent_is_written_not_the_empty(tmp_path: Path):
    config = _config(tmp_path, _kb_yaml_declaring("note"))
    written = _with_service(
        config,
        lambda svc: svc.create(
            KB, {"entry_type": "note", "title": "Sent", "x_text": "mine", "x_multi": ["a"]}
        ),
    )
    path, got = _file_values(config, written.entry.id)
    assert _value_of(got, "x_text") == "mine" and _value_of(got, "x_multi") == ["a"], got
    text = path.read_text(encoding="utf-8")
    assert text.count("x_text") == 1 and text.count("x_multi") == 1, text


def test_an_aliased_field_is_written_at_one_spelling(tmp_path: Path):
    """An event's `actors` has the alias `participants`; the create writes the
    field once, whatever spelling the class or caller used."""
    config = _config(tmp_path, f"name: {KB}\nkb_type: generic\n")
    for title, extra in (("No actors", {}), ("Some actors", {"participants": ["[[jane]]"]})):
        written = _with_service(
            config,
            lambda svc, t=title, e=extra: svc.create(
                KB, {"entry_type": "event", "title": t, "date": "2026-01-01", **e}
            ),
        )
        _path, got = _file_values(config, written.entry.id)
        assert ("actors" in got) != ("participants" in got), got


@pytest.mark.control(
    reason="a missing required field is refused today; the empties must not satisfy it"
)
def test_a_required_field_not_sent_is_still_refused(tmp_path: Path):
    from pyrite.exceptions import ValidationError

    config = _config(
        tmp_path,
        f"name: {KB}\nkb_type: generic\ntypes:\n  note:\n    fields:\n"
        "      must: {type: text, required: true}\nvalidation:\n  enforce: true\n",
    )
    with pytest.raises(ValidationError):
        _with_service(config, lambda svc: svc.create(KB, {"entry_type": "note", "title": "R"}))
    assert not list((config.knowledge_bases[0].path).rglob("r.md"))


def test_an_empty_select_is_accepted_under_enforcement(tmp_path: Path):
    config = _config(
        tmp_path,
        f"name: {KB}\nkb_type: generic\ntypes:\n  note:\n    fields:\n"
        "      lane: {type: select, options: [a, b]}\n"
        "validation:\n  enforce: true\n  enforce_enums: true\n",
    )
    written = _with_service(
        config, lambda svc: svc.create(KB, {"entry_type": "note", "title": "E"})
    )
    _path, got = _file_values(config, written.entry.id)
    assert _value_of(got, "lane") == ""


class _FieldsPlugin:
    """A plugin that declares fields through `get_field_schemas()` (no shipped
    plugin does yet; #232 theme 2 makes them)."""

    name = "declares_fields_86"

    def __init__(self, schemas: dict[str, dict[str, dict]], hooks: dict | None = None):
        self._schemas = schemas
        self._hooks = hooks or {}

    def get_field_schemas(self) -> dict[str, dict[str, dict]]:
        return self._schemas

    def get_hooks(self) -> dict:
        return self._hooks


@pytest.fixture
def plugin_fields():
    """Register a `_FieldsPlugin` for one test; unregistered after it."""
    reg = get_registry()
    reg.discover()

    def register(schemas: dict[str, dict[str, dict]], hooks: dict | None = None) -> None:
        reg.register(_FieldsPlugin(schemas, hooks))

    yield register
    reg._plugins.pop(_FieldsPlugin.name, None)
    reg._conformance_cache.pop(_FieldsPlugin.name, None)
    reg._aliases_changed()


def test_a_plugin_declared_field_is_written(tmp_path: Path, plugin_fields):
    """The plugin layer (`get_field_schemas()`), read through the same merge."""
    plugin_fields({"topic": {"from_plugin": {"type": "multi-select", "options": ["x"]}}})
    config = _config(tmp_path, f"name: {KB}\nkb_type: generic\n")
    written = _with_service(
        config, lambda svc: svc.create(KB, {"entry_type": "topic", "title": "Plug"})
    )
    _path, got = _file_values(config, written.entry.id)
    assert _value_of(got, "from_plugin") == []


def test_bulk_create_writes_the_declared_fields(tmp_path: Path):
    config = _config(tmp_path, _kb_yaml_declaring("note"))
    results = _with_service(
        config,
        lambda svc: svc.bulk_create_entries(
            KB, [{"entry_type": "note", "title": "B1"}, {"entry_type": "note", "title": "B2"}]
        ),
    )
    assert all(r["created"] for r in results), results
    for item in results:
        _path, got = _file_values(config, item["entry_id"])
        assert {k: _plain(_value_of(got, k)) for k in KB_YAML_EMPTIES} == KB_YAML_EMPTIES


# --------------------------------------------------------------------------
# The merge itself
# --------------------------------------------------------------------------


def test_type_fields_merges_core_then_plugin_then_kb_yaml(plugin_fields):
    from pyrite.schema import KBSchema

    plugin_fields(
        {"person": {"role": {"type": "select", "options": ["a"]}, "p": {"type": "number"}}}
    )
    schema = KBSchema.from_dict(
        {
            "types": {
                "person": {
                    "optional": ["role", "bare", "metadata", "tags"],
                    "fields": {"p": {"type": "date"}},
                }
            }
        }
    )
    fields = schema.type_fields("person")
    assert fields["affiliations"].field_type == "list"  # core
    assert fields["role"].field_type == "select"  # plugin over core; a bare name adds nothing
    assert fields["p"].field_type == "date"  # kb.yaml over plugin
    assert fields["bare"].field_type == "text"  # a bare kb.yaml name
    assert get_entry_class("person")  # the class is not consulted for the field list
    assert "research_status" not in fields
    # reserved names are base fields, not declared ones, in every layer
    assert not {"metadata", "tags", "title"} & set(fields)


def test_a_declared_alias_is_written_at_its_target(tmp_path: Path):
    """kb.yaml declares `participants`, an event's alias of `actors` (which
    CORE_TYPES declares): the file gets the field once, at `actors`."""
    config = _config(
        tmp_path,
        f"name: {KB}\nkb_type: generic\ntypes:\n  event:\n    fields:\n"
        "      participants: {type: list}\n",
    )
    written = _with_service(
        config,
        lambda svc: svc.create(KB, {"entry_type": "event", "title": "A", "date": "2026-01-01"}),
    )
    _path, got = _file_values(config, written.entry.id)
    assert got.get("actors") == [] and "participants" not in got, got


# --------------------------------------------------------------------------
# One entry: what a create validates, hooks, indexes, returns and writes
# --------------------------------------------------------------------------

#: `role` is a model attribute of `person`; `x_level` is not (a typed class
#: keeps it under `metadata:`). Both declare a default.
PERSON_DEFAULTS = (
    f"name: {KB}\nkb_type: generic\ntypes:\n  person:\n    fields:\n"
    "      role: {type: text, default: member}\n"
    "      x_level: {type: select, options: [low, high], default: low}\n"
)

SURFACES = ["service", "cli", "mcp", "rest"]


def _create_via(surface: str, config: PyriteConfig, entry_type: str, title: str, fields):
    """Create through one surface; return the entry id. CLI and REST run in
    the test world (`tests/_world.py`: the config read from disk, the app as
    `pyrite serve` builds it). REST takes the fields in its `metadata`
    envelope (its body has a fixed field list)."""
    import json

    from tests._world import build_world

    if surface == "service":
        spec = {"entry_type": entry_type, "title": title, **fields}
        return _with_service(config, lambda svc: svc.create(KB, spec)).entry.id
    if surface == "mcp":
        from pyrite.server.mcp_server import PyriteMCPServer

        server = PyriteMCPServer(config, tier="write")
        try:
            result = server._dispatch_tool(
                "kb_create", {"kb_name": KB, "entry_type": entry_type, "title": title, **fields}
            )
        finally:
            server.close()
        assert result.get("created"), result
        return result["entry_id"]
    if surface == "rest":
        pytest.importorskip("fastapi", reason="fastapi not installed")
    root = Path(config.settings.index_path).parent / f"world-{surface}"
    world = build_world(root, config=config, rest=surface == "rest")
    try:
        if surface == "cli":
            args = ["create", "-k", KB, "-t", entry_type, "--title", title, "--format", "json"]
            for key, value in fields.items():
                args += ["-f", f"{key}={value}"]
            result = world.cli(*args)
            assert result.exit_code == 0, result.output
            return json.loads(result.output[result.output.index("{") :])["entry_id"]
        body = {"kb": KB, "entry_type": entry_type, "title": title, "metadata": dict(fields)}
        res = world.client.post("/api/entries", json=body)
        assert res.status_code in (200, 201), res.text
        return res.json()["id"]
    finally:
        world.close()


@pytest.mark.parametrize("surface", SURFACES)
def test_a_field_sent_empty_is_the_callers_not_the_default(tmp_path: Path, surface: str):
    """A field the caller sent, even empty, is the caller's value (ADR-0042
    #720): a declared default never replaces it, on an attribute or not."""
    config = _config(tmp_path, PERSON_DEFAULTS)
    entry_id = _create_via(surface, config, "person", "Sent", {"role": "", "x_level": ""})
    _path, got = _file_values(config, entry_id)
    assert _value_of(got, "role") == "" and _value_of(got, "x_level") == "", got


@pytest.mark.parametrize("surface", SURFACES)
def test_a_field_not_sent_gets_its_declared_default(tmp_path: Path, surface: str):
    config = _config(tmp_path, PERSON_DEFAULTS)
    entry_id = _create_via(surface, config, "person", "Unsent", {})
    _path, got = _file_values(config, entry_id)
    assert _value_of(got, "role") == "member" and _value_of(got, "x_level") == "low", got


def test_an_added_default_is_validated_like_a_sent_value(tmp_path: Path):
    """A declared default outside its own options is refused under
    `enforce_enums` whether the caller sent it or the create added it: the
    entry validated is the entry written."""
    from pyrite.exceptions import ValidationError

    config = _config(
        tmp_path,
        f"name: {KB}\nkb_type: generic\ntypes:\n  note:\n    fields:\n"
        "      lane: {type: select, options: [low, high], default: medium}\n"
        "validation:\n  enforce_enums: true\n",
    )
    for title, extra in (("Sent", {"lane": "medium"}), ("Added", {})):
        with pytest.raises(ValidationError, match="lane"):
            _with_service(
                config,
                lambda svc, t=title, e=extra: svc.create(
                    KB, {"entry_type": "note", "title": t, **e}
                ),
            )
    assert not list(config.knowledge_bases[0].path.rglob("*.md"))


def test_a_before_save_hook_sees_the_declared_fields(tmp_path: Path, plugin_fields):
    seen: dict[str, Any] = {}

    def hook(entry, _ctx):
        seen["x_level"] = entry.metadata.get("x_level")
        seen["role"] = entry.role
        return entry

    plugin_fields({}, hooks={"before_save": [hook]})
    config = _config(tmp_path, PERSON_DEFAULTS)
    _with_service(config, lambda svc: svc.create(KB, {"entry_type": "person", "title": "H"}))
    assert seen == {"x_level": "low", "role": "member"}


def test_the_entry_a_create_returns_carries_the_declared_fields(tmp_path: Path):
    config = _config(tmp_path, _kb_yaml_declaring("note"))
    written = _with_service(
        config, lambda svc: svc.create(KB, {"entry_type": "note", "title": "Returned"})
    )
    returned = written.entry.to_frontmatter()
    assert {k: _plain(_value_of(returned, k)) for k in KB_YAML_EMPTIES} == KB_YAML_EMPTIES


@pytest.mark.control(
    reason="dev adds no declared field, so its rows agree trivially; guards this PR's "
    "IndexManager reading declared_frontmatter (person's row lacks role/affiliations without it)"
)
@pytest.mark.parametrize("entry_type", ["note", "person", "backlog_item"])
def test_the_row_a_create_indexes_is_the_row_a_rebuild_indexes(tmp_path: Path, entry_type):
    """The index row a create writes equals the row a full rebuild writes
    from the file: nothing the file holds is missing from the row until an
    `index_all`."""
    config = _config(tmp_path, _kb_yaml_declaring(entry_type))
    written = _with_service(
        config, lambda svc: svc.create(KB, {"entry_type": entry_type, "title": "Row"})
    )
    db = PyriteDB(config.settings.index_path)
    try:
        at_create = db.get_entry(written.entry.id, KB)
    finally:
        db.close()
    rebuilt_config = PyriteConfig(
        knowledge_bases=config.knowledge_bases,
        settings=Settings(index_path=tmp_path / "rebuilt.db", auto_embed=False),
    )
    db = PyriteDB(rebuilt_config.settings.index_path)
    try:
        IndexManager(db, rebuilt_config).index_all()
        rebuilt = db.get_entry(written.entry.id, KB)
    finally:
        db.close()
    volatile = {"indexed_at", "created_at", "updated_at", "file_mtime", "content_hash"}
    a = {k: v for k, v in at_create.items() if k not in volatile}
    b = {k: v for k, v in rebuilt.items() if k not in volatile}
    assert a == b


@pytest.mark.control(
    reason="dev adds no declared field, so nothing reads the name; guards this PR's "
    "reserved-name skip (without it every create of the type raises NotAFieldError)"
)
def test_a_reserved_name_kb_yaml_lists_is_not_a_declared_field(tmp_path: Path):
    """`optional: [metadata, tags]` names base fields, not fields of the type:
    the create neither fails (`NotAFieldError`) nor writes them empty."""
    config = _config(
        tmp_path, f"name: {KB}\nkb_type: generic\ntypes:\n  note:\n    optional: [metadata, tags]\n"
    )
    written = _with_service(
        config, lambda svc: svc.create(KB, {"entry_type": "note", "title": "Reserved"})
    )
    _path, got = _file_values(config, written.entry.id)
    assert "metadata" not in got and "tags" not in got, got


def test_a_markdown_import_is_a_field_based_create(tmp_path: Path):
    """`pyrite import x.md` parses the file into fields and builds the entry
    from them (it already drops managed keys), so it writes the declared
    fields like any field-based create; `pyrite add` is the path that keeps
    a file's own keys."""
    from tests._world import build_world

    config = _config(tmp_path, _kb_yaml_declaring("note"))
    src = tmp_path / "in.md"
    src.write_text("---\ntitle: Imported\ntype: note\n---\n\nBody.\n", encoding="utf-8")
    world = build_world(tmp_path / "world", config=config, rest=False)
    try:
        result = world.cli("import", str(src), "--kb", KB)
    finally:
        world.close()
    assert result.exit_code == 0, result.output
    _path, got = _file_values(config, "imported")
    assert _value_of(got, "x_text") == "", got


def test_an_update_fills_a_created_empty_field_unquoted(tmp_path: Path):
    """A field a create wrote as `""` and an update then fills reads as a
    hand-written one: `kind: bug`, not `kind: "bug"`."""
    config = _config(
        tmp_path, f"name: {KB}\nkb_type: generic\ntypes:\n  backlog_item:\n    optional: [kind]\n"
    )
    written = _with_service(
        config, lambda svc: svc.create(KB, {"entry_type": "backlog_item", "title": "Fill"})
    )
    path, _got = _file_values(config, written.entry.id)
    assert 'kind: ""' in path.read_text(encoding="utf-8")
    _with_service(config, lambda svc: svc.update(written.entry.id, KB, {"kind": "bug"}))
    assert "\nkind: bug\n" in path.read_text(encoding="utf-8")


def test_an_empty_added_field_passes_its_own_rule_under_enforce(tmp_path: Path):
    """A number or checkbox left out is written null, and null is no value:
    a title-only create is neither refused under `validation.enforce` nor
    warned about the fields it added."""
    config = _config(
        tmp_path,
        f"name: {KB}\nkb_type: generic\ntypes:\n  note:\n    fields:\n"
        "      score: {type: number, min: 0}\n      done: {type: checkbox}\n"
        "      when: {type: date}\n"
        "validation:\n  enforce: true\n",
    )
    written = _with_service(
        config, lambda svc: svc.create(KB, {"entry_type": "note", "title": "Enforced"})
    )
    assert not [w for w in written.warnings if w.get("field") in {"score", "done", "when"}]
    _path, got = _file_values(config, written.entry.id)
    assert [_value_of(got, k) for k in ("score", "done", "when")] == [None, None, None]


# --------------------------------------------------------------------------
# An added empty passes its field's declared rule, whatever the type
# --------------------------------------------------------------------------


def _core_model_fields_left_empty() -> list[tuple[str, str]]:
    """(core type, field) for each field the type's model holds that a
    title-only create leaves at an empty value: the fields whose empty the
    create adds when kb.yaml declares them. From the model classes."""
    import dataclasses

    from pyrite.schema import CORE_TYPES

    def is_empty(value: Any) -> bool:  # written here, not imported: the oracle is the rule
        return value is None or value == "" or value == []

    cases = []
    for entry_type in sorted(CORE_TYPES):
        cls = get_entry_class(entry_type)
        needs = NEEDS.get(entry_type, {})
        built = build_entry(entry_type, title="Probe", **needs)
        skip = RESERVED_FIELD_NAMES | _managed(entry_type) | set(needs)
        for f in dataclasses.fields(cls):
            if f.init and not f.name.startswith("_") and f.name not in skip:
                if is_empty(getattr(built, f.name)):
                    cases.append((entry_type, f.name))
    return cases


#: The FieldSchema types whose check accepted `''` before #86 (probed on dev:
#: the rest refused it). A field the class already wrote as `''` on dev passes
#: these there too, so those cases are controls.
_ACCEPTED_EMPTY_TEXT_ON_DEV = {"text", "select", "list", "tags", "object-ref"}


def _empty_type_cases() -> list[Any]:
    cases = []
    for entry_type, field in _core_model_fields_left_empty():
        needs = NEEDS.get(entry_type, {})
        written_on_dev = field in build_entry(entry_type, title="Probe", **needs).to_frontmatter()
        for field_type in sorted(FieldSchema.VALID_TYPES):
            marks = []
            if written_on_dev and field_type in _ACCEPTED_EMPTY_TEXT_ON_DEV:
                marks = [
                    pytest.mark.control(
                        reason="the class wrote this field as '' on dev too, and this type "
                        "accepted '' there; pins that it still passes"
                    )
                ]
            cases.append(pytest.param(entry_type, field, field_type, marks=marks))
    return cases


@pytest.mark.parametrize(("entry_type", "field", "field_type"), _empty_type_cases())
def test_an_added_empty_passes_its_declared_type_under_enforce(
    tmp_path: Path, entry_type: str, field: str, field_type: str
):
    """kb.yaml retypes a model-held field the caller does not send. The empty
    value the create adds (the model's: `''`, `[]` or null) is no value to
    every optional type check, so a title-only create that succeeds on dev
    succeeds here, under `validation.enforce`, with no warning on the field."""
    config = _config(
        tmp_path,
        f"name: {KB}\nkb_type: generic\ntypes:\n  {entry_type}:\n    fields:\n"
        f"      {field}: {{type: {field_type}, options: [a, b]}}\n"
        "validation:\n  enforce: true\n",
    )
    spec = {"entry_type": entry_type, "title": "Probe", **NEEDS.get(entry_type, {})}
    written = _with_service(config, lambda svc: svc.create(KB, spec))
    assert not [w for w in written.warnings if w.get("field") == field], written.warnings
    _path, got = _file_values(config, written.entry.id)
    # An alias attribute (an event's `participants`) is written at its target.
    from pyrite.schema.field_aliases import field_at

    key, _rest = field_at(get_entry_class(entry_type), entry_type, (field,))
    assert key in got or key in (got.get("metadata") or {}), got


@pytest.mark.control(
    reason="dev does not write `founded`, so no rule sees it; guards the rules loop reading an empty as no value (red without it)"
)
def test_an_empty_value_passes_a_validation_rule_under_enforce(tmp_path: Path):
    """A `validation.rules` check (`format: ISO8601`, `range`) reads an empty
    value as no value too: `founded: ''` added by the create passes."""
    config = _config(
        tmp_path,
        f"name: {KB}\nkb_type: generic\ntypes:\n  organization:\n    optional: [founded]\n"
        "validation:\n  enforce: true\n  rules:\n"
        "    - {field: founded, format: ISO8601}\n    - {field: founded, range: [1, 9]}\n",
    )
    written = _with_service(
        config, lambda svc: svc.create(KB, {"entry_type": "organization", "title": "Rule"})
    )
    assert not [w for w in written.warnings if w.get("field") == "founded"], written.warnings
    _path, got = _file_values(config, written.entry.id)
    assert got["founded"] == ""


@pytest.mark.control(
    reason="a required field sent empty was refused on dev too; pins the half of the rule that must not loosen"
)
@pytest.mark.parametrize("empty", [None, "", []])
def test_a_required_field_left_empty_is_still_refused(tmp_path: Path, empty):
    """Empty is no value: a `required` field sent empty is refused, the rule's
    other half."""
    from pyrite.exceptions import ValidationError

    config = _config(
        tmp_path,
        f"name: {KB}\nkb_type: generic\ntypes:\n  person:\n    fields:\n"
        "      role: {type: number, required: true}\nvalidation:\n  enforce: true\n",
    )
    spec = {"entry_type": "person", "title": "Req", "role": empty}
    with pytest.raises(ValidationError, match="role"):
        _with_service(config, lambda svc: svc.create(KB, spec))


@pytest.mark.control(
    reason="dev writes none of these fields, so qa sees none; guards qa validate following the rule for the empties this PR writes (red without it)"
)
def test_qa_validate_reports_no_type_error_for_an_added_empty(tmp_path: Path):
    """`qa validate` runs the same `KBSchema.validate_entry` on the file: the
    empties a create wrote are no type error there either."""
    from pyrite.services.qa_service import QAService

    config = _config(
        tmp_path,
        f"name: {KB}\nkb_type: generic\ntypes:\n  document:\n    fields:\n"
        "      date: {type: date}\n      author: {type: number}\n"
        "      url: {type: checkbox}\n",
    )
    written = _with_service(
        config, lambda svc: svc.create(KB, {"entry_type": "document", "title": "QA"})
    )
    db = PyriteDB(config.settings.index_path)
    try:
        result = QAService(config, db).validate_entry(written.entry.id, KB, readable_kbs=None)
    finally:
        db.close()
    flagged = [i for i in result["issues"] if i.get("field") in {"date", "author", "url"}]
    assert not flagged, result["issues"]


@pytest.mark.parametrize(
    "empty",
    [
        pytest.param(
            None, marks=pytest.mark.control(reason="null was skipped by the table on dev too")
        ),
        "",
        [],
    ],
)
def test_schema_validate_reads_an_empty_protocol_field_as_no_value(tmp_path: Path, empty):
    """`schema validate`'s protocol type table (`priority` is an int) reads an
    empty value as no value, as the write path does."""
    from pyrite.cli.schema_commands import validate_entry

    fm = {"id": "x", "title": "X", "type": "note", "priority": empty}
    findings = validate_entry(tmp_path / "x.md", fm, None)
    assert not [f for f in findings if f["check"] == "protocol_field_type"], findings

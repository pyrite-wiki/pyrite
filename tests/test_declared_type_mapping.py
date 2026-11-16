"""A create stores the type the caller named (#468).

The property, on every create surface: the stored type is the type the caller
named, unless a plugin active for the KB's ``kb_type`` *declares* that the core
type maps to one of its own (``get_type_mappings``). Nothing is inferred from
subclassing. One resolver answers the write path and orient/``kb_schema``.

Before #468, ``KBService._resolve_entry_type`` picked the most-derived
subclass of the named core class among the KB's plugin types, so ``note`` in
a ``kb_type: software`` KB was written as an ADR (``adrs/plain-note.md``,
``type: adr``, ``adr_number: 0``), and a REST daily note there became an ADR.

Expected values below are written by hand from the property, never computed
from the registry or the resolver.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from typer.testing import CliRunner

from pyrite.cli import app as cli_app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.models.collection import CollectionEntry
from pyrite.models.core_types import NoteEntry
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.utils.yaml import load_yaml

REPO = Path(__file__).resolve().parents[1]
runner = CliRunner()

#: Pyrite's own kb/kb.yaml: the software KB a web user of this repo writes to.
SOFTWARE_KB_YAML = (REPO / "kb" / "kb.yaml").read_text()
#: A journalism KB with no vocabulary of its own: every type is allowed.
JOURNALISM_KB_YAML = "name: inv\n"
PROBE_KB_YAML = "name: probe\n"


# ---------------------------------------------------------------------------
# A probe plugin: declares a mapping for the two core types whose surfaces fix
# the type (the daily note names `note`, collection create names `collection`),
# so those surfaces show they go through the same resolver.
# ---------------------------------------------------------------------------


@dataclass
class ProbeNoteEntry(NoteEntry):
    @property
    def entry_type(self) -> str:
        return "probe_note"


@dataclass
class ProbeCollectionEntry(CollectionEntry):
    @property
    def entry_type(self) -> str:
        return "probe_collection"


class ProbeMappingPlugin:
    name = "probe_mapping"

    def __init__(self, mappings=None, kb_types=("probe-map",), name=None):
        from pyrite.plugins.capabilities import Capability

        self.capabilities = {Capability.SCHEMA, Capability.SURFACE}
        self._mappings = (
            {"note": "probe_note", "collection": "probe_collection"}
            if mappings is None
            else mappings
        )
        self._kb_types = list(kb_types)
        if name:
            self.name = name

    def get_entry_types(self):
        return {"probe_note": ProbeNoteEntry, "probe_collection": ProbeCollectionEntry}

    def get_kb_types(self):
        return self._kb_types

    def get_type_mappings(self):
        return dict(self._mappings)


@pytest.fixture
def probe_registry():
    """The real plugins plus the probe plugin, as the global registry."""
    import pyrite.plugins.registry as reg_module
    from pyrite.plugins.registry import PluginRegistry

    registry = PluginRegistry()
    registry.discover()
    registry.register(ProbeMappingPlugin())
    old = reg_module._registry
    reg_module._registry = registry
    try:
        yield registry
    finally:
        reg_module._registry = old
        registry._aliases_changed()


# ---------------------------------------------------------------------------
# Environments and surfaces
# ---------------------------------------------------------------------------


_WORLDS: list = []


@pytest.fixture(autouse=True)
def _close_worlds():
    yield
    while _WORLDS:
        _WORLDS.pop().close()


def _kb_env(tmp_path: Path, name: str, kb_type: str, kb_yaml: str) -> dict:
    """One KB in a world (tests/_world.py): the production wiring on every surface."""
    from tests._world import build_world

    kb_path = tmp_path / name
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(kb_yaml)
    world = build_world(
        tmp_path / f"{name}-world",
        kbs=[KBConfig(name=name, path=kb_path, kb_type=kb_type)],
        settings={"auto_embed": False},
    )
    _WORLDS.append(world)
    return {
        "name": name,
        "world": world,
        "config": world.config,
        "kb_path": kb_path,
        "tmp_path": tmp_path,
    }


def _stored_type(env: dict, title: str) -> str:
    """The `type:` of the one file written for ``title`` -- the file is the truth."""
    found = []
    for path in env["kb_path"].rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        meta = load_yaml(text.split("---", 2)[1]) or {}
        if meta.get("title") == title:
            found.append((path, meta))
    assert len(found) == 1, (
        title,
        [p for p, _ in found],
        sorted(str(p) for p in env["kb_path"].rglob("*.md")),
    )
    return found[0][1]["type"]


def _cli(env: dict, args: list[str]):
    return env["world"].cli(*args)


def _rest(env: dict):
    return env["world"].client, lambda: None


def _mcp(env: dict, tool: str, args: dict) -> dict:
    return env["world"].mcp()._dispatch_tool(tool, args)


def _frontmatter_file(env: dict, entry_type: str, title: str, extra: dict) -> Path:
    src = env["tmp_path"] / f"src-{abs(hash((entry_type, title)))}.md"
    lines = "".join(f"{k}: {v}\n" for k, v in extra.items())
    src.write_text(f"---\ntype: {entry_type}\ntitle: {title}\n{lines}---\nbody\n", encoding="utf-8")
    return src


def create_cli(env, entry_type, title, extra):
    fields = [a for k, v in extra.items() for a in ("-f", f"{k}={v}")]
    r = _cli(
        env,
        [
            "create",
            "-k",
            env["name"],
            "-t",
            entry_type,
            "--title",
            title,
            "-b",
            "body",
            "--allow-undeclared",
            *fields,
        ],
    )
    assert r.exit_code == 0, r.output


def create_import_json(env, entry_type, title, extra):
    src = env["tmp_path"] / f"import-{abs(hash(title))}.json"
    src.write_text(json.dumps([{"entry_type": entry_type, "title": title, "body": "b", **extra}]))
    r = _cli(env, ["import", str(src), "-k", env["name"], "--allow-undeclared"])
    assert r.exit_code == 0, r.output


def create_import_markdown(env, entry_type, title, extra):
    src = _frontmatter_file(env, entry_type, title, extra)
    r = _cli(
        env, ["import", str(src), "-k", env["name"], "--format", "markdown", "--allow-undeclared"]
    )
    assert r.exit_code == 0, r.output


def create_add_file(env, entry_type, title, extra):
    src = _frontmatter_file(env, entry_type, title, extra)
    r = _cli(env, ["add", str(src), "-k", env["name"], "--allow-undeclared"])
    assert r.exit_code == 0, r.output


def create_mcp_kb_create(env, entry_type, title, extra):
    res = _mcp(
        env,
        "kb_create",
        {
            "kb_name": env["name"],
            "entry_type": entry_type,
            "title": title,
            "body": "b",
            "allow_undeclared": True,
            **extra,
        },
    )
    assert res.get("created") or res.get("entry_id"), res


def create_mcp_kb_bulk_create(env, entry_type, title, extra):
    res = _mcp(
        env,
        "kb_bulk_create",
        {
            "kb_name": env["name"],
            "allow_undeclared": True,
            "entries": [{"entry_type": entry_type, "title": title, "body": "b", **extra}],
        },
    )
    assert res.get("created") == 1, res


def create_rest_post(env, entry_type, title, extra):
    client, close = _rest(env)
    try:
        resp = client.post(
            "/api/entries",
            json={
                "kb": env["name"],
                "entry_type": entry_type,
                "title": title,
                "body": "b",
                "allow_undeclared": True,
                **extra,
            },
        )
        assert resp.status_code == 200, resp.text
    finally:
        close()


#: Surfaces where the caller names the type.
NAMED_SURFACES = {
    "cli_create": create_cli,
    "import_json": create_import_json,
    "import_markdown": create_import_markdown,
    "add_file": create_add_file,
    "mcp_kb_create": create_mcp_kb_create,
    "mcp_kb_bulk_create": create_mcp_kb_bulk_create,
    "rest_post": create_rest_post,
}


def create_rest_daily(env) -> str:
    client, close = _rest(env)
    try:
        resp = client.post("/api/daily/2026-11-16", params={"kb": env["name"]})
        assert resp.status_code == 200, resp.text
    finally:
        close()
    return "Daily Note - 2026-11-16"


def create_rest_collection(env) -> str:
    client, close = _rest(env)
    try:
        resp = client.post(
            "/api/collections",
            json={
                "kb": env["name"],
                "title": "Open work",
                "query": "status:open",
            },
        )
        assert resp.status_code == 200, resp.text
    finally:
        close()
    return "Open work"


# ---------------------------------------------------------------------------
# The property, per surface
# ---------------------------------------------------------------------------


def _surfaces(control_on: set[str], reason: str):
    """The named surfaces; those in ``control_on`` held the property before
    #468 and are marked ``control`` (they pin it, they do not prove the fix)."""
    return [
        pytest.param(s, marks=pytest.mark.control(reason=reason)) if s in control_on else s
        for s in sorted(NAMED_SURFACES)
    ]


@pytest.mark.parametrize(
    "surface",
    _surfaces({"add_file"}, "add FILE kept the named type by switching resolution off"),
)
def test_a_note_in_a_software_kb_is_stored_as_a_note(tmp_path, surface):
    """`note` named in a `kb_type: software` KB is stored as `note`, not as the
    software plugin's most-derived NoteEntry subclass (`adr`)."""
    env = _kb_env(tmp_path, "sw", "software", SOFTWARE_KB_YAML)
    NAMED_SURFACES[surface](env, "note", "Plain note", {})
    assert _stored_type(env, "Plain note") == "note"


@pytest.mark.parametrize(
    "surface",
    _surfaces(
        set(NAMED_SURFACES) - {"add_file"},
        "inference picked the same type; the declaration must keep it",
    ),
)
@pytest.mark.parametrize(
    ("named", "stored"),
    [("event", "investigation_event"), ("document", "document_source")],
)
def test_a_declared_mapping_still_resolves(tmp_path, surface, named, stored):
    """journalism-investigation declares `event -> investigation_event` and
    `document -> document_source`; on every surface those are stored."""
    env = _kb_env(tmp_path, "inv", "journalism-investigation", JOURNALISM_KB_YAML)
    extra = {"date": "2026-01-02"} if named == "event" else {}
    NAMED_SURFACES[surface](env, named, f"Mapped {named}", extra)
    assert _stored_type(env, f"Mapped {named}") == stored


def test_a_rest_daily_note_in_a_software_kb_is_stored_as_a_note(tmp_path):
    env = _kb_env(tmp_path, "sw", "software", SOFTWARE_KB_YAML)
    title = create_rest_daily(env)
    assert _stored_type(env, title) == "note"


@pytest.mark.control(reason="collection has no subclass in software-kb; pins the surface")
def test_a_rest_collection_in_a_software_kb_is_stored_as_a_collection(tmp_path):
    env = _kb_env(tmp_path, "sw", "software", SOFTWARE_KB_YAML)
    title = create_rest_collection(env)
    assert _stored_type(env, title) == "collection"


@pytest.mark.control(reason="the probe types subclass note/collection, so inference agreed")
def test_the_daily_and_collection_surfaces_take_a_declared_mapping(tmp_path, probe_registry):
    """The two surfaces that fix their type go through the same resolver."""
    env = _kb_env(tmp_path, "probe", "probe-map", PROBE_KB_YAML)
    assert _stored_type(env, create_rest_daily(env)) == "probe_note"
    assert _stored_type(env, create_rest_collection(env)) == "probe_collection"


# ---------------------------------------------------------------------------
# Nothing is inferred from subclassing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kb_type", "named"),
    [
        ("software", "note"),  # adr, backlog_item, ... subclass NoteEntry
        ("software", "document"),  # design_doc subclasses DocumentEntry
        ("zettelkasten", "note"),  # zettel, literature_note
        ("encyclopedia", "note"),  # article
        ("social", "note"),  # writeup
        ("social", "person"),  # user_profile subclasses PersonEntry
    ],
)
def test_a_subclass_with_no_declaration_is_not_chosen(tmp_path, kb_type, named):
    from pyrite.services.kb_service import KBService

    env = _kb_env(tmp_path, "k", kb_type, "name: k\n")
    svc = KBService(env["config"], env["world"].db)
    entry = svc.create("k", {"entry_type": named, "title": "Named"}).entry
    assert entry.entry_type == named
    assert _stored_type(env, "Named") == named


def test_a_kb_with_no_kb_type_context_applies_no_mapping(tmp_path):
    """An empty kb_type means no KB type in hand: no plugin's mapping applies.
    Before #468 it matched every plugin, so `note` became some plugin's
    most-derived NoteEntry subclass (an `adr`)."""
    from pyrite.services.kb_service import KBService

    kb_path = tmp_path / "bare"
    kb_path.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="bare", path=kb_path, kb_type="")],
        settings=Settings(index_path=tmp_path / "bare.db", auto_embed=False),
    )
    db = PyriteDB(tmp_path / "bare.db")
    try:
        entry = KBService(config, db).create("bare", {"entry_type": "note", "title": "Bare"}).entry
    finally:
        db.close()
    assert entry.entry_type == "note"
    written = list(kb_path.rglob("*.md"))
    assert len(written) == 1 and "type: note" in written[0].read_text(), written


# ---------------------------------------------------------------------------
# Regimes: declarations refused at load, and an undeclared target
# ---------------------------------------------------------------------------


def _fresh_registry(*plugins):
    from pyrite.plugins.registry import PluginRegistry

    registry = PluginRegistry()
    registry._discovered = True  # only these plugins, no entry points
    for p in plugins:
        registry.register(p)
    return registry


def test_two_plugins_mapping_one_core_type_are_refused_at_load():
    from pyrite.exceptions import PluginError

    first = ProbeMappingPlugin({"note": "probe_note"}, kb_types=["shared"], name="first_p")
    second = ProbeMappingPlugin({"note": "probe_note"}, kb_types=["shared", "x"], name="second_p")
    with pytest.raises(PluginError) as caught:
        _fresh_registry(first, second)
    message = str(caught.value)
    assert "first_p" in message and "second_p" in message and "note" in message


def test_two_plugins_mapping_one_core_type_for_disjoint_kb_types_both_load():
    a = ProbeMappingPlugin({"note": "probe_note"}, kb_types=["a"], name="a_p")
    b = ProbeMappingPlugin({"note": "probe_note"}, kb_types=["b"], name="b_p")
    registry = _fresh_registry(a, b)
    assert registry.type_mappings_for_kb("a") == {"note": "probe_note"}
    assert registry.type_mappings_for_kb("b") == {"note": "probe_note"}
    assert registry.type_mappings_for_kb("c") == {}


class _EverywherePlugin(ProbeMappingPlugin):
    """A plugin with no get_kb_types at all: active in every KB."""

    get_kb_types = property(lambda self: (_ for _ in ()).throw(AttributeError))


@pytest.mark.parametrize("everywhere", ["empty", "absent"])
def test_a_mapping_from_a_plugin_active_for_every_kb_type_is_refused_at_load(everywhere):
    """A plugin with no (or an empty) get_kb_types is active in every KB; its
    mapping would retype a core type everywhere, the global rewrite #468 is
    about. Refused at load (N4)."""
    from pyrite.exceptions import PluginError

    if everywhere == "empty":
        plugin = ProbeMappingPlugin({"note": "probe_note"}, kb_types=[], name="all_p")
    else:
        plugin = _EverywherePlugin({"note": "probe_note"}, name="all_p")
        assert not hasattr(plugin, "get_kb_types")
    with pytest.raises(PluginError, match="all_p"):
        _fresh_registry(plugin)


def _conflicted_registry():
    """Two loaded plugins that map `note` for one KB type, loaded the way
    ordinary discovery loads them (non-strict)."""
    from pyrite.plugins.registry import PluginRegistry

    registry = PluginRegistry()
    registry._plugins = {
        "first_p": ProbeMappingPlugin({"note": "probe_note"}, kb_types=["s"], name="first_p"),
        "second_p": ProbeMappingPlugin(
            {"note": "probe_note", "collection": "probe_collection"},
            kb_types=["s"],
            name="second_p",
        ),
    }
    registry._load_type_mappings(strict=False)
    registry._discovered = True
    return registry


def _mapping_failures(registry) -> list[tuple[str, str]]:
    return [
        (name, fault.text(local=True))
        for name, method, fault in registry.aggregate_failures
        if method == "get_type_mappings"
    ]


def test_discovery_refuses_a_conflict_and_neither_mapping_applies(capsys):
    """Non-strict discovery cannot raise for one bad pair without taking every
    other plugin down: it refuses the conflicting mapping (neither plugin's
    applies, so the named type is stored), names both, and the CLI prints the
    refusal on stderr when it mounts plugin commands (N3)."""

    from pyrite.cli import _LazyPluginTyper
    from pyrite.exceptions import HeldFault

    registry = _conflicted_registry()
    assert registry.type_mappings_for_kb("s") == {"collection": "probe_collection"}
    assert all(
        isinstance(fault, HeldFault)
        for _n, method, fault in registry.aggregate_failures
        if method == "get_type_mappings"
    )
    failures = _mapping_failures(registry)
    assert sorted(n for n, _t in failures) == ["first_p", "second_p"], failures
    assert all("first_p" in t and "second_p" in t for _n, t in failures), failures

    with patch("pyrite.plugins.get_registry", return_value=registry):
        _LazyPluginTyper()._mount_plugin_cli()
    err = capsys.readouterr().err
    assert "plugin 'first_p' type mappings refused" in err, err
    assert "plugin 'second_p' type mappings refused" in err, err


def test_register_raises_only_for_a_refusal_involving_the_plugin_registered():
    """N1: a conflict between plugins already loaded is recorded, not raised at
    the next unrelated register; a register that is refused leaves the recorded
    failures as they were (no row twice)."""
    from pyrite.exceptions import PluginError

    registry = _conflicted_registry()
    before = _mapping_failures(registry)

    registry.register(ProbeMappingPlugin({"note": "probe_note"}, kb_types=["t"], name="other_p"))
    assert registry.type_mappings_for_kb("t") == {"note": "probe_note"}
    assert _mapping_failures(registry) == before

    with pytest.raises(PluginError, match="third_p"):
        registry.register(
            ProbeMappingPlugin({"collection": "probe_collection"}, kb_types=["s"], name="third_p")
        )
    assert "third_p" not in registry._plugins
    assert _mapping_failures(registry) == before
    assert registry.type_mappings_for_kb("s") == {"collection": "probe_collection"}


def test_a_failing_get_type_mappings_is_held_not_embedded():
    """N2: the plugin's own exception is kept as a HeldFault (its text decided
    at display); the message Pyrite writes names only its class."""
    from pyrite.plugins.registry import PluginRegistry

    class Boom(ProbeMappingPlugin):
        def get_type_mappings(self):
            raise RuntimeError("secret-path /home/someone")

    registry = PluginRegistry()
    registry._plugins = {"boom_p": Boom(name="boom_p", kb_types=["s"])}
    registry._load_type_mappings(strict=False)
    rows = [(n, f) for n, m, f in registry.aggregate_failures if m == "get_type_mappings"]
    assert len(rows) == 1 and rows[0][0] == "boom_p", rows
    assert rows[0][1].type_name == "RuntimeError"
    assert rows[0][1].matches(lambda e: "secret-path" in str(e))


@pytest.mark.parametrize(
    ("mappings", "fragment"),
    [
        ({"not_a_core_type": "probe_note"}, "not_a_core_type"),  # key must be a core type
        ({"note": "adr"}, "adr"),  # target must be the plugin's own type
    ],
)
def test_a_mapping_that_is_not_core_to_own_is_refused_at_load(mappings, fragment):
    from pyrite.exceptions import PluginError

    with pytest.raises(PluginError, match=fragment):
        _fresh_registry(ProbeMappingPlugin(mappings, kb_types=["a"], name="bad_p"))


def test_a_mapping_whose_target_the_kb_does_not_declare_is_neither_shown_nor_applied(tmp_path):
    """The KB declares `event` but not its mapped target: the pair is dropped by
    the resolver, so the schema shows no `event` mapping and `event` is stored
    as the declared `event` it was named (round 1: shown == applied)."""
    kb_yaml = "name: inv\ntypes:\n  event:\n    description: An event\n"
    env = _kb_env(tmp_path, "inv", "journalism-investigation", kb_yaml)
    assert _shown_mappings(env) == {}
    res = _mcp(
        env,
        "kb_create",
        {"kb_name": "inv", "entry_type": "event", "title": "E", "date": "2026-01-02"},
    )
    assert res.get("created"), res
    assert _stored_type(env, "E") == "event"


def test_the_stored_type_check_refuses_an_undeclared_target_the_resolver_returns(
    tmp_path, monkeypatch
):
    """Defence in depth: if the resolver ever returned a pair whose target the
    KB does not declare, `_prepare` refuses it (UNDECLARED_TYPE naming the
    target) rather than writing an undeclared type."""
    import pyrite.schema.type_mapping as type_mapping

    kb_yaml = "name: inv\ntypes:\n  event:\n    description: An event\n"
    env = _kb_env(tmp_path, "inv", "journalism-investigation", kb_yaml)
    monkeypatch.setattr(
        type_mapping,
        "resolve_type_mappings",
        lambda kb_type, kb_schema: {"event": "investigation_event"},
    )
    res = _mcp(
        env,
        "kb_create",
        {"kb_name": "inv", "entry_type": "event", "title": "E", "date": "2026-01-02"},
    )
    assert res.get("error_code") == "UNDECLARED_TYPE", res
    assert "investigation_event" in res["error"], res
    assert list(env["kb_path"].rglob("*.md")) == []


@pytest.mark.control(reason="inference picked investigation_event too; pins the declared path")
def test_a_mapping_to_a_declared_target_is_written(tmp_path):
    kb_yaml = (
        "name: inv\ntypes:\n  event:\n    description: An event\n"
        "  investigation_event:\n    description: An investigation event\n"
    )
    env = _kb_env(tmp_path, "inv", "journalism-investigation", kb_yaml)
    res = _mcp(
        env,
        "kb_create",
        {"kb_name": "inv", "entry_type": "event", "title": "E", "date": "2026-01-02"},
    )
    assert res.get("created"), res
    assert _stored_type(env, "E") == "investigation_event"


# ---------------------------------------------------------------------------
# orient / kb_schema show the declared mappings
# ---------------------------------------------------------------------------


def test_kb_schema_and_orient_show_the_declared_mappings(tmp_path):
    env = _kb_env(tmp_path, "inv", "journalism-investigation", JOURNALISM_KB_YAML)
    expected = {"event": "investigation_event", "document": "document_source"}
    schema = _mcp(env, "kb_schema", {"kb_name": "inv"})
    assert schema["type_mappings"] == expected, schema.get("type_mappings")
    orient = _mcp(env, "kb_orient", {"kb_name": "inv"})
    assert orient["schema"]["type_mappings"] == expected, orient.get("schema")
    client, close = _rest(env)
    try:
        rest = client.get("/api/kbs/inv/schema").json()
    finally:
        close()
    assert rest["type_mappings"] == expected


def test_kb_schema_shows_the_mappings_when_the_kb_declares_core_types(tmp_path):
    """A KB that declares core types (here `person`, `event`, `document`): the
    schema export still reads the KB's kb_type for its mappings (80c3181)."""
    kb_yaml = (
        "name: inv\ntypes:\n  person:\n    description: A person\n"
        "  event:\n    description: An event\n"
        "  investigation_event:\n    description: An event\n"
        "  document:\n    description: A document\n"
        "  document_source:\n    description: A source\n"
    )
    env = _kb_env(tmp_path, "inv", "journalism-investigation", kb_yaml)
    expected = {"event": "investigation_event", "document": "document_source"}
    assert _mcp(env, "kb_schema", {"kb_name": "inv"})["type_mappings"] == expected


def _shown_mappings(env: dict) -> dict:
    """`type_mappings` as every surface that shows it reports it; asserts they agree.

    MCP kb_schema and kb_orient, REST schema and orient, CLI `orient --format
    json`, and `pyrite-admin schema`."""
    from pyrite import admin_cli

    name = env["name"]
    world = env["world"]
    shown = {
        "mcp kb_schema": _mcp(env, "kb_schema", {"kb_name": name})["type_mappings"],
        "mcp kb_orient": _mcp(env, "kb_orient", {"kb_name": name})["schema"]["type_mappings"],
        "rest schema": world.client.get(f"/api/kbs/{name}/schema").json()["type_mappings"],
        "rest orient": world.client.get(f"/api/kbs/{name}/orient").json()["schema"][
            "type_mappings"
        ],
    }
    cli = world.cli("orient", "-k", name, "--format", "json")
    assert cli.exit_code == 0, cli.output
    shown["cli orient"] = json.loads(cli.stdout)["schema"]["type_mappings"]
    admin = world.cli("schema", name, app=admin_cli.app)
    assert admin.exit_code == 0, admin.output
    shown["pyrite-admin schema"] = json.loads(admin.stdout)["type_mappings"]
    values = list(shown.values())
    assert all(v == values[0] for v in values), shown
    return values[0]


def _assert_creates_agree_with_what_is_shown(env: dict, shown: dict) -> None:
    """For each core type a plugin here could map: a shown pair is what a create
    stores; an unshown name is stored as named, or refused UNDECLARED_TYPE when
    the KB's vocabulary does not include it -- never silently retyped."""
    declared = env["config"].get_kb(env["name"]).kb_schema.declared_types()
    for named in ("event", "document", "note"):
        title = f"Agree {named}"
        res = _mcp(
            env,
            "kb_create",
            {"kb_name": env["name"], "entry_type": named, "title": title, "date": "2026-01-02"},
        )
        if named in shown:
            assert res.get("created"), (named, res)
            assert _stored_type(env, title) == shown[named]
        elif declared and named not in declared:
            assert res.get("error_code") == "UNDECLARED_TYPE", (named, res)
        else:
            assert res.get("created"), (named, res)
            assert _stored_type(env, title) == named


def _preset_env(tmp_path: Path, name: str, kb_type: str) -> dict:
    """A KB registered the way a user registers one: `pyrite kb add DIR --type
    <preset>`, so its kb.yaml is the plugin preset's, not a test's.

    The CLI's own view (orient and create, in the world where `kb add` ran)
    is checked here; the returned world is opened over the same directory and
    kb.yaml for the MCP and REST surfaces."""
    from tests._world import build_world

    setup = build_world(tmp_path / "setup", rest=False)
    _WORLDS.append(setup)
    kb_path = tmp_path / name
    kb_path.mkdir()
    added = setup.cli("kb", "add", str(kb_path), "--name", name, "--type", kb_type)
    assert added.exit_code == 0, added.output
    orient = setup.cli("orient", "-k", name, "--format", "json")
    assert orient.exit_code == 0, orient.output
    cli_shown = json.loads(orient.stdout)["schema"]["type_mappings"]
    created = setup.cli(
        "create", "-k", name, "-t", "event", "--title", "Via kb add", "-f", "date=2026-01-02"
    )
    if "event" in cli_shown:
        assert created.exit_code == 0, created.output
    else:
        assert created.exit_code != 0 and "UNDECLARED_TYPE" in created.output, created.output
    world = build_world(
        tmp_path / f"{name}-world",
        kbs=[KBConfig(name=name, path=kb_path, kb_type=kb_type)],
        settings={"auto_embed": False},
    )
    _WORLDS.append(world)
    return {
        "name": name,
        "world": world,
        "config": world.config,
        "kb_path": kb_path,
        "tmp_path": tmp_path,
        "cli_shown": cli_shown,
    }


@pytest.mark.parametrize("kb_type", ["journalism-investigation", "known-entities"])
def test_a_preset_kb_shows_exactly_the_mappings_a_create_applies(tmp_path, kb_type):
    """Round 1's property. The journalism presets declare `investigation_event`
    and `document_source` (journalism-investigation) or neither (known-entities),
    but never `event` or `document`: a create refuses those names, so no surface
    may advertise them as mappings."""
    env = _preset_env(tmp_path, "inv", kb_type)
    declared = env["config"].get_kb("inv").kb_schema.declared_types()
    assert declared and "event" not in declared and "document" not in declared, declared
    shown = _shown_mappings(env)
    assert shown == env["cli_shown"] == {}, (shown, env["cli_shown"])
    _assert_creates_agree_with_what_is_shown(env, shown)


@pytest.mark.parametrize(
    ("kb_yaml", "expected"),
    [
        (JOURNALISM_KB_YAML, {"event": "investigation_event", "document": "document_source"}),
        (
            "name: inv\ntypes:\n  event: {}\n  investigation_event: {}\n  document: {}\n",
            {"event": "investigation_event"},
        ),
        ("name: inv\ntypes:\n  investigation_event: {}\n  document_source: {}\n", {}),
    ],
    ids=["no-vocabulary", "event-pair-declared", "targets-only"],
)
def test_every_surface_shows_what_a_create_applies(tmp_path, kb_yaml, expected):
    env = _kb_env(tmp_path, "inv", "journalism-investigation", kb_yaml)
    shown = _shown_mappings(env)
    assert shown == expected, shown
    _assert_creates_agree_with_what_is_shown(env, shown)


def test_kb_schema_shows_no_mapping_in_a_software_kb(tmp_path):
    env = _kb_env(tmp_path, "sw", "software", SOFTWARE_KB_YAML)
    assert _mcp(env, "kb_schema", {"kb_name": "sw"})["type_mappings"] == {}


# ---------------------------------------------------------------------------
# Guards: one rule, one place
# ---------------------------------------------------------------------------

#: issubclass calls that do NOT pick an entry type, each with its reason.
ISSUBCLASS_ALLOWED = {
    # Structural-protocol conformance of a class already chosen (ADR-0014).
    "pyrite/models/protocols.py",
}


def test_no_code_picks_a_type_by_issubclass():
    """A type is never chosen by subclassing over registry types (#468): every
    `issubclass` call in pyrite/ and the extensions' sources is on a reasoned
    allowlist."""
    roots = [REPO / "pyrite", *sorted((REPO / "extensions").glob("*/src"))]
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "issubclass"
                ):
                    rel = path.relative_to(REPO).as_posix()
                    if rel not in ISSUBCLASS_ALLOWED:
                        offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], offenders


def test_the_schema_export_cannot_be_asked_without_the_kbs_kb_type():
    """N5: `to_agent_schema` takes the registered kb_type as a required keyword,
    so no caller can show mappings for a kb_type the write path does not use."""
    import inspect

    from pyrite.schema.kb_schema import KBSchema

    param = inspect.signature(KBSchema.to_agent_schema).parameters["kb_type"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


def test_no_create_path_can_switch_resolution_off():
    """The `resolve_type=False` call-site exception is gone: `add FILE` takes
    the same rule as every other surface (principle 8)."""
    import inspect

    from pyrite.services.kb_service import KBService

    for method in ("_prepare", "_prepare_and_save"):
        assert "resolve_type" not in inspect.signature(getattr(KBService, method)).parameters

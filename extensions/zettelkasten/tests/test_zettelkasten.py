"""Tests for the Zettelkasten extension."""

import pytest
from pyrite_zettelkasten.entry_types import LiteratureNoteEntry, ZettelEntry
from pyrite_zettelkasten.plugin import ZettelkastenPlugin
from pyrite_zettelkasten.preset import ZETTELKASTEN_PRESET
from pyrite_zettelkasten.validators import validate_zettel

from pyrite.plugins.registry import PluginRegistry

# =========================================================================
# Plugin registration
# =========================================================================


class TestPluginRegistration:
    def test_plugin_has_name(self):
        plugin = ZettelkastenPlugin()
        assert plugin.name == "zettelkasten"

    def test_register_with_registry(self):
        registry = PluginRegistry()
        plugin = ZettelkastenPlugin()
        registry.register(plugin)
        assert "zettelkasten" in registry.list_plugins()

    def test_entry_types_registered(self):
        registry = PluginRegistry()
        registry.register(ZettelkastenPlugin())
        types = registry.get_all_entry_types()
        assert "zettel" in types
        assert "literature_note" in types
        assert types["zettel"] is ZettelEntry
        assert types["literature_note"] is LiteratureNoteEntry

    def test_relationship_types_registered(self):
        registry = PluginRegistry()
        registry.register(ZettelkastenPlugin())
        rels = registry.get_all_relationship_types()
        assert "elaborates" in rels
        assert "branches_from" in rels
        assert "synthesizes" in rels
        assert rels["elaborates"]["inverse"] == "elaborated_by"
        assert rels["branches_from"]["inverse"] == "has_branch"

    def test_validators_registered(self):
        registry = PluginRegistry()
        registry.register(ZettelkastenPlugin())
        validators = registry.get_all_validators()
        assert validate_zettel in validators

    def test_cli_commands_registered(self):
        registry = PluginRegistry()
        registry.register(ZettelkastenPlugin())
        commands = registry.get_all_cli_commands()
        cmd_names = [name for name, _ in commands]
        assert "zettel" in cmd_names

    def test_zettel_graph_depth_schema_is_bounded(self):
        tool = ZettelkastenPlugin().get_mcp_tools("read")["zettel_graph"]
        depth = tool["inputSchema"]["properties"]["depth"]
        assert depth["minimum"] == 1
        assert depth["maximum"] == 3

    def test_mcp_tools_registered(self):
        registry = PluginRegistry()
        registry.register(ZettelkastenPlugin())
        tools = registry.get_all_mcp_tools("read")
        assert "zettel_inbox" in tools
        assert "zettel_graph" in tools

    def test_kb_presets_registered(self):
        registry = PluginRegistry()
        registry.register(ZettelkastenPlugin())
        presets = registry.get_all_kb_presets()
        assert "zettelkasten" in presets

    def test_kb_types_registered(self):
        registry = PluginRegistry()
        registry.register(ZettelkastenPlugin())
        kb_types = registry.get_all_kb_types()
        assert "zettelkasten" in kb_types


# =========================================================================
# Entry types
# =========================================================================


class TestZettelEntry:
    def test_default_values(self):
        entry = ZettelEntry(id="test", title="Test")
        assert entry.entry_type == "zettel"
        assert entry.zettel_type == "fleeting"
        assert entry.maturity == "seed"
        assert entry.source_ref == ""
        assert entry.processing_stage == ""

    def test_to_frontmatter(self):
        entry = ZettelEntry(
            id="test",
            title="Test",
            zettel_type="permanent",
            maturity="evergreen",
            source_ref="ref-123",
            processing_stage="connect",
        )
        fm = entry.to_frontmatter()
        assert fm["type"] == "zettel"
        assert fm["zettel_type"] == "permanent"
        assert fm["maturity"] == "evergreen"
        assert fm["source_ref"] == "ref-123"
        assert fm["processing_stage"] == "connect"

    def test_to_frontmatter_field_presence(self):
        # 44f81f3: meaningful enum defaults are written so readers/agents
        # don't have to know the default; genuinely empty fields stay omitted.
        entry = ZettelEntry(id="test", title="Test")
        fm = entry.to_frontmatter()
        assert fm["zettel_type"] == "fleeting"  # default written
        assert fm["maturity"] == "seed"  # default written
        assert "source_ref" not in fm  # empty string omitted
        assert "processing_stage" not in fm  # empty string omitted

    def test_from_frontmatter(self):
        meta = {
            "id": "test",
            "title": "Test Note",
            "type": "zettel",
            "zettel_type": "permanent",
            "maturity": "sapling",
            "source_ref": "src-1",
            "processing_stage": "review",
            "tags": ["test"],
        }
        entry = ZettelEntry.from_frontmatter(meta, "Body text")
        assert entry.id == "test"
        assert entry.title == "Test Note"
        assert entry.zettel_type == "permanent"
        assert entry.maturity == "sapling"
        assert entry.source_ref == "src-1"
        assert entry.processing_stage == "review"
        assert entry.body == "Body text"
        assert entry.tags == ["test"]

    def test_from_frontmatter_generates_id(self):
        meta = {"title": "My Great Note", "type": "zettel"}
        entry = ZettelEntry.from_frontmatter(meta, "")
        assert entry.id == "my-great-note"

    def test_roundtrip_markdown(self):
        entry = ZettelEntry(
            id="test-note",
            title="Test Note",
            body="Some content",
            zettel_type="permanent",
            maturity="sapling",
            tags=["knowledge", "test"],
        )
        md = entry.to_markdown()
        assert "zettel_type: permanent" in md
        assert "maturity: sapling" in md
        assert "Some content" in md


class TestLiteratureNoteEntry:
    def test_default_values(self):
        entry = LiteratureNoteEntry(id="test", title="Test")
        assert entry.entry_type == "literature_note"
        assert entry.source_work == ""
        assert entry.author == ""
        assert entry.page_refs == []

    def test_to_frontmatter(self):
        entry = LiteratureNoteEntry(
            id="test",
            title="Notes on Book X",
            source_work="Book X",
            author="Author Y",
            page_refs=["p.42", "p.99"],
        )
        fm = entry.to_frontmatter()
        assert fm["type"] == "literature_note"
        assert fm["source_work"] == "Book X"
        assert fm["author"] == "Author Y"
        assert fm["page_refs"] == ["p.42", "p.99"]

    def test_from_frontmatter(self):
        meta = {
            "id": "test",
            "title": "Notes on Book X",
            "type": "literature_note",
            "source_work": "Book X",
            "author": "Author Y",
            "page_refs": ["p.42"],
        }
        entry = LiteratureNoteEntry.from_frontmatter(meta, "Notes here")
        assert entry.source_work == "Book X"
        assert entry.author == "Author Y"
        assert entry.page_refs == ["p.42"]
        assert entry.body == "Notes here"


# =========================================================================
# Validators
# =========================================================================


class TestValidators:
    def test_fleeting_requires_processing_stage(self):
        errors = validate_zettel("zettel", {"zettel_type": "fleeting"}, {})
        assert any(e["rule"] == "required_for_fleeting" for e in errors)

    def test_fleeting_with_stage_ok(self):
        errors = validate_zettel(
            "zettel", {"zettel_type": "fleeting", "processing_stage": "capture"}, {}
        )
        assert not any(e["rule"] == "required_for_fleeting" for e in errors)

    def test_literature_note_requires_source_work(self):
        errors = validate_zettel("literature_note", {}, {})
        assert any(e["field"] == "source_work" for e in errors)

    def test_literature_note_with_source_ok(self):
        errors = validate_zettel("literature_note", {"source_work": "Book X"}, {})
        assert not any(e["field"] == "source_work" for e in errors)

    def test_permanent_warns_no_links(self):
        errors = validate_zettel("zettel", {"zettel_type": "permanent"}, {})
        warnings = [e for e in errors if e.get("severity") == "warning"]
        assert any(e["rule"] == "permanent_should_link" for e in warnings)

    def test_permanent_with_links_no_warning(self):
        errors = validate_zettel(
            "zettel",
            {"zettel_type": "permanent", "links": [{"target": "x", "relation": "related"}]},
            {},
        )
        warnings = [e for e in errors if e.get("severity") == "warning"]
        assert not any(e["rule"] == "permanent_should_link" for e in warnings)

    def test_hub_requires_3_links(self):
        errors = validate_zettel(
            "zettel",
            {"zettel_type": "hub", "links": [{"target": "a"}, {"target": "b"}]},
            {},
        )
        non_warnings = [e for e in errors if e.get("severity") != "warning"]
        assert any(e["rule"] == "hub_min_links" for e in non_warnings)

    def test_hub_with_3_links_ok(self):
        errors = validate_zettel(
            "zettel",
            {
                "zettel_type": "hub",
                "links": [{"target": "a"}, {"target": "b"}, {"target": "c"}],
            },
            {},
        )
        assert not any(e["rule"] == "hub_min_links" for e in errors)

    def test_invalid_zettel_type(self):
        errors = validate_zettel("zettel", {"zettel_type": "invalid"}, {})
        assert any(e["field"] == "zettel_type" for e in errors)

    def test_invalid_maturity(self):
        errors = validate_zettel(
            "zettel",
            {"zettel_type": "permanent", "maturity": "invalid", "links": [{"target": "x"}]},
            {},
        )
        assert any(e["field"] == "maturity" for e in errors)

    def test_invalid_processing_stage(self):
        errors = validate_zettel(
            "zettel",
            {"zettel_type": "fleeting", "processing_stage": "invalid"},
            {},
        )
        assert any(e["field"] == "processing_stage" for e in errors)

    def test_ignores_unrelated_types(self):
        errors = validate_zettel("note", {"title": "regular note"}, {})
        assert errors == []

    def test_ignores_event_type(self):
        errors = validate_zettel("event", {"title": "something"}, {})
        assert errors == []


# =========================================================================
# Preset
# =========================================================================


class TestPreset:
    def test_preset_structure(self):
        p = ZETTELKASTEN_PRESET
        assert p["name"] == "my-zettelkasten"
        assert "zettel" in p["types"]
        assert "literature_note" in p["types"]
        assert p["policies"]["private"] is True
        assert p["policies"]["single_author"] is True
        assert p["validation"]["enforce"] is True

    def test_preset_zettel_type(self):
        zt = ZETTELKASTEN_PRESET["types"]["zettel"]
        assert "title" in zt["required"]
        assert "zettel_type" in zt["optional"]
        assert "maturity" in zt["optional"]

    def test_preset_literature_type(self):
        lt = ZETTELKASTEN_PRESET["types"]["literature_note"]
        assert "source_work" in lt["required"]

    def test_preset_directories(self):
        assert "zettels" in ZETTELKASTEN_PRESET["directories"]
        assert "literature" in ZETTELKASTEN_PRESET["directories"]


# =========================================================================
# Entry type resolution via core
# =========================================================================


class TestCoreIntegration:
    """Test that the plugin integrates correctly with pyrite core when registered."""

    def test_entry_class_resolution(self):
        """Plugin entry types resolve via get_entry_class when registered."""
        from pyrite.plugins.registry import PluginRegistry

        # Use a fresh registry
        registry = PluginRegistry()
        registry.register(ZettelkastenPlugin())

        # Temporarily replace global registry
        import pyrite.plugins.registry as reg_module

        old = reg_module._registry
        reg_module._registry = registry

        try:
            from pyrite.models.core_types import get_entry_class

            cls = get_entry_class("zettel")
            assert cls is ZettelEntry

            cls = get_entry_class("literature_note")
            assert cls is LiteratureNoteEntry

            # Core types still work
            from pyrite.models.core_types import NoteEntry

            cls = get_entry_class("note")
            assert cls is NoteEntry
        finally:
            reg_module._registry = old

    def test_entry_from_frontmatter_resolution(self):
        """Plugin entry types resolve via entry_from_frontmatter when registered."""
        import pyrite.plugins.registry as reg_module
        from pyrite.plugins.registry import PluginRegistry

        registry = PluginRegistry()
        registry.register(ZettelkastenPlugin())

        old = reg_module._registry
        reg_module._registry = registry

        try:
            from pyrite.models.core_types import entry_from_frontmatter

            entry = entry_from_frontmatter(
                {"type": "zettel", "title": "Test", "zettel_type": "permanent"},
                "Body",
            )
            assert isinstance(entry, ZettelEntry)
            assert entry.zettel_type == "permanent"

            entry = entry_from_frontmatter(
                {"type": "literature_note", "title": "Test", "source_work": "Book"},
                "Notes",
            )
            assert isinstance(entry, LiteratureNoteEntry)
            assert entry.source_work == "Book"
        finally:
            reg_module._registry = old

    def test_relationship_types_merged(self):
        """Plugin relationship types merge into schema."""
        import pyrite.plugins.registry as reg_module
        from pyrite.plugins.registry import PluginRegistry

        registry = PluginRegistry()
        registry.register(ZettelkastenPlugin())

        old = reg_module._registry
        reg_module._registry = registry

        try:
            from pyrite.schema import get_all_relationship_types, get_inverse_relation

            all_rels = get_all_relationship_types()
            assert "elaborates" in all_rels
            assert "branches_from" in all_rels

            assert get_inverse_relation("elaborates") == "elaborated_by"
            assert get_inverse_relation("branches_from") == "has_branch"
            # Core types still work
            assert get_inverse_relation("owns") == "owned_by"
        finally:
            reg_module._registry = old


class _RecordingDB:
    """Records the storage call `_mcp_inbox` makes, and answers it."""

    def __init__(self, rows=None):
        self.calls: list[dict] = []
        self._rows = rows or []

    def list_entries(self, **kwargs):
        self.calls.append(kwargs)
        return list(self._rows)

    def close(self):
        pass


def _inbox_plugin(rows=None):
    plugin = ZettelkastenPlugin()
    db = _RecordingDB(rows)
    plugin._get_db = lambda: (db, False)
    return plugin, db


class TestInboxNarrowsToTheReadableSet:
    """`zettel_inbox` hands the caller's readable set to the storage query (#223).

    `kb_name` is optional, so a call that omits it spans every KB, and the MCP
    chokepoint refuses such a call for a scoped caller rather than serving the
    index. The narrowing belongs in `list_entries(kb_names=...)` -- the storage
    layer's empty-set rule (`1 = 0`, never "no filter") is asserted with the
    backend, not here. What these tests pin is that this handler passes the set
    through untouched and does not filter the page afterwards, which would
    return a short page under the storage limit.
    """

    def test_no_kb_name_is_narrowed_by_the_readable_set(self):
        plugin, db = _inbox_plugin()
        plugin._mcp_inbox({}, readable_kbs={"public-kb"})
        assert db.calls[0]["kb_names"] == {"public-kb"}
        assert db.calls[0]["kb_name"] is None

    def test_no_kb_name_and_no_set_is_unscoped(self):
        plugin, db = _inbox_plugin()
        plugin._mcp_inbox({})
        assert db.calls[0]["kb_names"] is None

    def test_an_empty_readable_set_is_passed_as_empty_not_dropped(self):
        plugin, db = _inbox_plugin()
        plugin._mcp_inbox({}, readable_kbs=set())
        assert db.calls[0]["kb_names"] == set()

    def test_a_named_kb_is_still_passed_through(self):
        plugin, db = _inbox_plugin()
        plugin._mcp_inbox({"kb_name": "public-kb"}, readable_kbs={"public-kb"})
        assert db.calls[0]["kb_name"] == "public-kb"

    def test_the_page_is_not_filtered_after_the_query(self):
        """A short page is the symptom of post-filtering, so the handler
        returns exactly the rows the narrowed query gave it."""
        row = {
            "id": "z1",
            "title": "Fleeting note",
            "kb_name": "public-kb",
            "metadata": {"zettel_type": "fleeting", "processing_stage": "capture"},
        }
        plugin, db = _inbox_plugin(rows=[row])
        out = plugin._mcp_inbox({}, readable_kbs={"public-kb"})
        assert out["count"] == 1
        assert out["inbox"][0]["id"] == "z1"
        assert db.calls[0]["kb_names"] == {"public-kb"}


# =========================================================================
# `zettel new` goes through the write pipeline (#391)
# =========================================================================


class TestZettelNewGoesThroughPipeline:
    """`zettel new` used to call `repo.save(entry)` directly (cli.py:69-70
    before this fix): no exists check (an existing id was silently
    overwritten), no validators, no hooks, and nothing indexed the entry
    until a separate `pyrite index sync`. These tests pin the pipeline.
    """

    @pytest.fixture
    def env(self, tmp_path):
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        kb_path = tmp_path / "zettel-kb"
        kb_path.mkdir()
        (kb_path / "kb.yaml").write_text(
            "name: zettel-kb\n"
            "kb_type: zettelkasten\n"
            "validation:\n"
            "  enforce: true\n"
            "types:\n"
            "  zettel:\n"
            "    optional: [zettel_type, maturity, source_ref, processing_stage]\n"
            "    subdirectory: zettels/\n"
        )
        db_path = tmp_path / "index.db"
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="zettel-kb", path=kb_path, kb_type="zettelkasten")],
            settings=Settings(index_path=db_path, auto_embed=False),
        )
        db = PyriteDB(db_path)
        IndexManager(db, config).index_all()
        db.close()
        return {"config": config, "kb_path": kb_path, "db_path": db_path}

    def _file(self, env, entry_id):
        found = list(env["kb_path"].rglob(f"{entry_id}.md"))
        assert len(found) <= 1, found
        return found[0] if found else None

    def _run(self, env, args):
        from unittest.mock import patch

        from pyrite_zettelkasten.cli import zettel_app
        from typer.testing import CliRunner

        runner = CliRunner()
        with patch("pyrite_zettelkasten.cli.load_config", return_value=env["config"]):
            return runner.invoke(zettel_app, args)

    def test_new_zettel_is_indexed_immediately(self, env):
        result = self._run(env, ["new", "My First Zettel", "--kb", "zettel-kb"])
        assert result.exit_code == 0, result.output

        from pyrite.storage.database import PyriteDB

        db = PyriteDB(env["db_path"])
        try:
            row = db.get_entry("my-first-zettel", "zettel-kb")
            assert row is not None, "a new zettel should be indexed without `index sync`"
        finally:
            db.close()
        assert self._file(env, "my-first-zettel") is not None

    def test_new_zettel_runs_before_and_after_save_hooks(self, env):
        from pyrite.plugins.registry import get_registry

        before_calls = []
        after_calls = []

        def before_save(entry, ctx):
            before_calls.append(entry.id)

        def after_save(entry, ctx):
            after_calls.append(entry.id)

        class ProbePlugin:
            name = "probe_hook_plugin_zettel"

            def get_hooks(self):
                return {"before_save": [before_save], "after_save": [after_save]}

        reg = get_registry()
        reg.register(ProbePlugin())
        try:
            result = self._run(env, ["new", "Hooked Zettel", "--kb", "zettel-kb"])
            assert result.exit_code == 0, result.output
            assert before_calls == ["hooked-zettel"], before_calls
            assert after_calls == ["hooked-zettel"], after_calls
        finally:
            del reg._plugins["probe_hook_plugin_zettel"]

    def test_existing_id_is_refused_with_entry_exists_and_file_untouched(self, env):
        first = self._run(env, ["new", "Same Title", "--kb", "zettel-kb"])
        assert first.exit_code == 0, first.output
        original = self._file(env, "same-title").read_bytes()

        result = self._run(env, ["new", "Same Title", "--kb", "zettel-kb"])
        assert result.exit_code != 0, result.output
        assert "ENTRY_EXISTS" in result.output, result.output
        assert self._file(env, "same-title").read_bytes() == original

    def test_create_failure_prints_str_e_not_the_masked_public_message(self, env):
        """#506 item 3: this is the operator's own terminal, so there is no
        transport boundary to protect -- masking a StorageError/PluginError/
        ConfigError's public_message here (#501's fixed sentence, meant for
        REST/MCP) hides real detail for no security gain. Local CLI output
        should always show str(e)."""
        from unittest.mock import patch

        from pyrite.exceptions import StorageError
        from pyrite.services.kb_service import KBService

        real_message = "Failed to materialize query result: real driver detail (dsn=x)"
        with patch.object(KBService, "create_entry", side_effect=StorageError(real_message)):
            result = self._run(env, ["new", "Doomed Zettel", "--kb", "zettel-kb"])

        assert result.exit_code != 0, result.output
        # Rich may wrap the line at the terminal width the runner reports,
        # so compare with whitespace collapsed rather than a raw substring.
        normalized = " ".join(result.output.split())
        assert real_message in normalized, result.output
        assert "check the server log" not in normalized, result.output

    def test_no_kb_found_carries_an_error_code(self, tmp_path):
        """#391 cold read item 9: 'No KB found' was a plain message with no
        error_code, unlike every other refusal in this pipeline."""
        from unittest.mock import patch

        from pyrite_zettelkasten.cli import zettel_app
        from typer.testing import CliRunner

        from pyrite.config import PyriteConfig, Settings

        empty_config = PyriteConfig(
            knowledge_bases=[], settings=Settings(index_path=tmp_path / "test.db")
        )
        runner = CliRunner()
        with patch("pyrite_zettelkasten.cli.load_config", return_value=empty_config):
            result = runner.invoke(zettel_app, ["new", "Orphan Note"])

        assert result.exit_code != 0, result.output
        assert "KB_NOT_FOUND" in result.output, result.output

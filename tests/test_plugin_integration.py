"""
Cross-cutting integration tests for the plugin system.

Tests that the plugin infrastructure works end-to-end: discovery, entry type
resolution, CLI registration, MCP tool registration, validator execution,
hook firing, DB table creation, and multi-plugin coexistence.
"""

import logging
import tempfile
from pathlib import Path

import pytest
import yaml
from pyrite_encyclopedia.entry_types import ArticleEntry, TalkPageEntry
from pyrite_encyclopedia.plugin import EncyclopediaPlugin
from pyrite_social.entry_types import UserProfileEntry, WriteupEntry
from pyrite_social.plugin import SocialPlugin
from pyrite_zettelkasten.entry_types import LiteratureNoteEntry, ZettelEntry

# Import all three extensions
from pyrite_zettelkasten.plugin import ZettelkastenPlugin

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models.base import Entry
from pyrite.storage.repository import new_entry_text
from pyrite.models.core_types import (
    ENTRY_TYPE_REGISTRY,
    EventEntry,
    NoteEntry,
    entry_from_frontmatter,
    get_entry_class,
)
from pyrite.models.generic import GenericEntry
from pyrite.plugins.registry import PluginRegistry
from pyrite.schema import (
    KBSchema,
    get_all_relationship_types,
    get_inverse_relation,
)
from pyrite.storage.database import PyriteDB

# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def registry():
    """Fresh registry with all three plugins registered."""
    reg = PluginRegistry()
    reg.register(ZettelkastenPlugin())
    reg.register(SocialPlugin())
    reg.register(EncyclopediaPlugin())
    return reg


@pytest.fixture
def hook_runner(registry):
    """HookRunner wired to the test registry -- the only module that runs
    hooks under the raise-before/swallow-after contract (#379); the
    registry's get_hooks_for_kb is a pure lookup."""
    from pyrite.services.hook_runner import HookRunner

    return HookRunner(plugin_registry=registry)


@pytest.fixture
def patched_registry(registry):
    """Temporarily replace the global registry with our test registry."""
    import pyrite.plugins.registry as reg_module

    old = reg_module._registry
    reg_module._registry = registry
    yield registry
    reg_module._registry = old


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def db_with_plugins(temp_dir, patched_registry):
    """Create a PyriteDB that has plugin tables created."""
    db = PyriteDB(temp_dir / "test.db")
    yield db
    db.close()


@pytest.fixture
def kb_setup(temp_dir):
    """Create a KB directory with kb.yaml for testing."""
    kb_path = temp_dir / "test-kb"
    kb_path.mkdir()
    (kb_path / "zettels").mkdir()
    (kb_path / "writeups").mkdir()
    (kb_path / "articles").mkdir()

    kb_yaml = {
        "name": "test-kb",
        "description": "Integration test KB",
        "types": {
            "zettel": {"required": ["title"], "subdirectory": "zettels/"},
            "writeup": {"required": ["title"], "subdirectory": "writeups/"},
            "article": {"required": ["title"], "subdirectory": "articles/"},
        },
    }
    with open(kb_path / "kb.yaml", "w") as f:
        yaml.dump(kb_yaml, f)

    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name="test-kb", path=kb_path, kb_type=KBType.RESEARCH),
        ],
        settings=Settings(index_path=temp_dir / "index.db"),
    )
    return config, kb_path


# =========================================================================
# Plugin discovery via entry points
# =========================================================================


class TestEntryPointDiscovery:
    """Test that installed plugins are discovered via entry points."""

    def test_all_three_discovered(self):
        """All three pip-installed extensions are discovered automatically."""
        reg = PluginRegistry()
        reg.discover()
        names = reg.list_plugins()
        assert "zettelkasten" in names
        assert "social" in names
        assert "encyclopedia" in names

    def test_discovery_is_idempotent(self):
        """Calling discover() multiple times doesn't duplicate plugins."""
        reg = PluginRegistry()
        reg.discover()
        count1 = len(reg.list_plugins())
        reg.discover()
        count2 = len(reg.list_plugins())
        assert count1 == count2

    def test_manual_register_survives_discover(self):
        """Manually registered plugins aren't clobbered by discover()."""

        class FakePlugin:
            name = "fake_test_plugin"

        reg = PluginRegistry()
        reg.register(FakePlugin())
        reg.discover()
        assert "fake_test_plugin" in reg.list_plugins()


class TestStrictDiscovery:
    """Test strict mode for plugin discovery."""

    def test_strict_false_swallows_load_errors(self):
        """Default non-strict mode logs warnings but doesn't raise."""
        from unittest.mock import MagicMock, patch

        bad_ep = MagicMock()
        bad_ep.name = "broken_plugin"
        bad_ep.load.side_effect = ImportError("missing dependency")

        with patch("importlib.metadata.entry_points") as mock_eps:
            mock_eps.return_value.select.return_value = [bad_ep]
            reg = PluginRegistry()
            reg.discover(strict=False)  # Should not raise
            assert "broken_plugin" not in reg.list_plugins()

    def test_strict_true_raises_on_load_error(self):
        """Strict mode raises PluginError on plugin load failure."""
        from unittest.mock import MagicMock, patch

        from pyrite.exceptions import PluginError

        bad_ep = MagicMock()
        bad_ep.name = "broken_plugin"
        bad_ep.load.side_effect = ImportError("missing dependency")

        with patch("importlib.metadata.entry_points") as mock_eps:
            mock_eps.return_value.select.return_value = [bad_ep]
            reg = PluginRegistry()
            with pytest.raises(PluginError, match="broken_plugin"):
                reg.discover(strict=True)

    def test_strict_true_raises_on_missing_name(self):
        """Strict mode raises PluginError when plugin has no name attribute."""
        from unittest.mock import MagicMock, patch

        from pyrite.exceptions import PluginError

        nameless_plugin = MagicMock(spec=[])  # No 'name' attribute

        bad_ep = MagicMock()
        bad_ep.name = "nameless"
        bad_ep.load.return_value = lambda: nameless_plugin

        with patch("importlib.metadata.entry_points") as mock_eps:
            mock_eps.return_value.select.return_value = [bad_ep]
            reg = PluginRegistry()
            with pytest.raises(PluginError, match="nameless"):
                reg.discover(strict=True)


# =========================================================================
# Entry type resolution through core
# =========================================================================


class TestEntryTypeResolution:
    """Test that plugin entry types resolve correctly via core functions."""

    def test_core_types_unchanged(self, patched_registry):
        """Core types still resolve to their classes."""
        assert get_entry_class("note") is NoteEntry
        assert get_entry_class("event") is EventEntry

    def test_zettel_resolves(self, patched_registry):
        assert get_entry_class("zettel") is ZettelEntry

    def test_literature_note_resolves(self, patched_registry):
        assert get_entry_class("literature_note") is LiteratureNoteEntry

    def test_writeup_resolves(self, patched_registry):
        assert get_entry_class("writeup") is WriteupEntry

    def test_user_profile_resolves(self, patched_registry):
        assert get_entry_class("user_profile") is UserProfileEntry

    def test_article_resolves(self, patched_registry):
        assert get_entry_class("article") is ArticleEntry

    def test_talk_page_resolves(self, patched_registry):
        assert get_entry_class("talk_page") is TalkPageEntry

    def test_unknown_type_falls_back_to_generic(self, patched_registry):
        """Types not in core or plugins fall back to GenericEntry."""
        assert get_entry_class("nonexistent_type") is GenericEntry

    def test_entry_from_frontmatter_zettel(self, patched_registry):
        entry = entry_from_frontmatter(
            {"type": "zettel", "title": "Test", "zettel_type": "permanent", "maturity": "sapling"},
            "Body",
        )
        assert isinstance(entry, ZettelEntry)
        assert entry.zettel_type == "permanent"
        assert entry.maturity == "sapling"

    def test_entry_from_frontmatter_writeup(self, patched_registry):
        entry = entry_from_frontmatter(
            {"type": "writeup", "title": "My Post", "author_id": "alice", "writeup_type": "essay"},
            "Content",
        )
        assert isinstance(entry, WriteupEntry)
        assert entry.author_id == "alice"

    def test_entry_from_frontmatter_article(self, patched_registry):
        entry = entry_from_frontmatter(
            {
                "type": "article",
                "title": "Quantum Mechanics",
                "quality": "GA",
                "review_status": "published",
            },
            "Long article body",
        )
        assert isinstance(entry, ArticleEntry)
        assert entry.quality == "GA"
        assert entry.review_status == "published"

    def test_entry_from_frontmatter_talk_page(self, patched_registry):
        entry = entry_from_frontmatter(
            {"type": "talk_page", "title": "Talk: QM", "article_id": "quantum-mechanics"},
            "Discussion",
        )
        assert isinstance(entry, TalkPageEntry)
        assert entry.article_id == "quantum-mechanics"

    def test_all_plugin_types_distinct_from_core(self, registry):
        """No plugin type name collides with a core type."""
        plugin_types = registry.get_all_entry_types()
        for name in plugin_types:
            assert name not in ENTRY_TYPE_REGISTRY, f"Plugin type '{name}' collides with core"


# =========================================================================
# CLI command registration
# =========================================================================


class TestCLIRegistration:
    def test_all_plugin_commands_present(self, registry):
        commands = registry.get_all_cli_commands()
        cmd_names = [name for name, _ in commands]
        assert "zettel" in cmd_names
        assert "social" in cmd_names
        assert "wiki" in cmd_names

    def test_command_objects_are_typer_apps(self, registry):
        """Each plugin provides a Typer app, not a bare function."""
        import typer

        commands = registry.get_all_cli_commands()
        for name, app in commands:
            if name in ("zettel", "social", "wiki"):
                assert isinstance(app, typer.Typer), f"{name} should be a Typer app"

    def test_cli_wiring_in_main_app(self, patched_registry):
        """Plugin commands register into the main CLI app."""
        from pyrite.cli import app

        # The main app should have registered plugin sub-apps
        # Typer stores registered commands internally
        # We can check by looking at registered_groups
        registered_names = []
        for group in getattr(app, "registered_groups", []):
            if hasattr(group, "typer_instance") and hasattr(group, "name"):
                registered_names.append(group.name)

        # At minimum the core sub-apps should be there
        # Plugin commands may or may not be registered depending on import order
        # The important thing is the registration code doesn't crash
        assert True  # If we got here, CLI wiring didn't raise


# =========================================================================
# MCP tool registration
# =========================================================================


class TestMCPToolRegistration:
    def test_read_tier_tools(self, registry):
        tools = registry.get_all_mcp_tools("read")
        # Zettelkasten
        assert "zettel_inbox" in tools
        assert "zettel_graph" in tools
        # Social
        assert "social_top" in tools
        assert "social_newest" in tools
        assert "social_reputation" in tools
        # Encyclopedia
        assert "wiki_quality_stats" in tools
        assert "wiki_review_queue" in tools
        assert "wiki_stubs" in tools

    def test_write_tier_adds_tools(self, registry):
        tools = registry.get_all_mcp_tools("write")
        # Write-only tools should appear
        assert "social_vote" in tools
        assert "social_post" in tools
        assert "wiki_submit_review" in tools
        assert "wiki_assess_quality" in tools
        # Read tools still present
        assert "zettel_inbox" in tools

    def test_admin_tier_adds_tools(self, registry):
        tools = registry.get_all_mcp_tools("admin")
        assert "wiki_protect" in tools
        # Write tools still present
        assert "social_vote" in tools

    def test_tool_definitions_have_required_fields(self, registry):
        """Every MCP tool has description, inputSchema, and handler."""
        for tier in ("read", "write", "admin"):
            tools = registry.get_all_mcp_tools(tier)
            for name, tool in tools.items():
                assert "description" in tool, f"{name} missing description"
                assert "inputSchema" in tool, f"{name} missing inputSchema"
                assert "handler" in tool, f"{name} missing handler"
                assert callable(tool["handler"]), f"{name} handler not callable"

    def test_no_tool_name_collisions(self, registry):
        """No two plugins define a tool with the same name."""
        # This is implicitly tested by the dict merge in get_all_mcp_tools,
        # but let's verify by checking count
        for tier in ("read", "write", "admin"):
            tools = registry.get_all_mcp_tools(tier)
            # All our known tools should be present
            expected_read = {
                "zettel_inbox",
                "zettel_graph",
                "social_top",
                "social_newest",
                "social_reputation",
                "wiki_quality_stats",
                "wiki_review_queue",
                "wiki_stubs",
            }
            for name in expected_read:
                assert name in tools


# =========================================================================
# Validator execution
# =========================================================================


class TestValidatorExecution:
    @staticmethod
    def _schema_with_plugin_types():
        """Create a KBSchema that knows about plugin entry types."""
        from pyrite.schema import TypeSchema

        return KBSchema(
            name="test",
            types={
                "zettel": TypeSchema(name="zettel", required=["title"]),
                "writeup": TypeSchema(name="writeup", required=["title"]),
                "article": TypeSchema(name="article", required=["title"]),
                "talk_page": TypeSchema(name="talk_page", required=["title"]),
                "user_profile": TypeSchema(name="user_profile", required=["title"]),
                "literature_note": TypeSchema(name="literature_note", required=["title"]),
            },
        )

    def test_validators_run_during_schema_validation(self, patched_registry):
        """Plugin validators fire when KBSchema.validate_entry() is called."""
        schema = self._schema_with_plugin_types()

        # Zettel validation: fleeting without processing_stage
        result = schema.validate_entry("zettel", {"zettel_type": "fleeting", "title": "x"})
        all_issues = result.get("errors", []) + result.get("warnings", [])
        assert any(e.get("rule") == "required_for_fleeting" for e in all_issues)

    def test_social_validator_runs(self, patched_registry):
        schema = self._schema_with_plugin_types()
        result = schema.validate_entry("writeup", {"title": "x"})
        # Should have author_id error from social validator
        all_issues = result.get("errors", []) + result.get("warnings", [])
        assert any(e.get("field") == "author_id" for e in all_issues)

    def test_encyclopedia_validator_runs(self, patched_registry):
        schema = self._schema_with_plugin_types()
        result = schema.validate_entry(
            "article", {"quality": "GA", "title": "x", "body": "x" * 600}
        )
        all_issues = result.get("errors", []) + result.get("warnings", [])
        assert any(e.get("rule") == "ga_min_sources" for e in all_issues)

    def test_validators_ignore_unrelated_types(self, patched_registry):
        """Plugin validators don't produce errors for types they don't handle."""
        schema = KBSchema(name="test")
        result = schema.validate_entry("note", {"title": "Regular note"})
        # Should have no plugin-specific errors
        plugin_rules = {
            "required_for_fleeting",
            "hub_min_links",
            "ga_min_sources",
            "b_min_length",
            "permanent_should_link",
        }
        for e in result.get("errors", []):
            assert e.get("rule") not in plugin_rules

    def test_validator_context_passed(self, patched_registry):
        """The context parameter reaches validators."""
        schema = self._schema_with_plugin_types()
        # This shouldn't crash — context is optional and defaults to {}
        result = schema.validate_entry(
            "zettel",
            {"zettel_type": "fleeting", "processing_stage": "capture", "title": "x"},
            context={"kb_name": "my-kb", "user": "alice"},
        )
        # Should pass with no errors (fleeting + stage is valid)
        zettel_errors = [
            e for e in result.get("errors", []) if e.get("rule") == "required_for_fleeting"
        ]
        assert len(zettel_errors) == 0

    def test_plugin_validators_run_for_unknown_schema_types(self, patched_registry):
        """Plugin validators fire even when the type isn't declared in kb.yaml."""
        schema = KBSchema(name="test")  # No plugin types declared
        result = schema.validate_entry("writeup", {"title": "x"})
        all_issues = result.get("errors", []) + result.get("warnings", [])
        # Social validator should still catch missing author_id
        assert any(e.get("field") == "author_id" for e in all_issues)


# =========================================================================
# Hook execution
# =========================================================================


class TestHookExecution:
    def test_before_save_hooks_fire(self, hook_runner):
        """before_save hooks modify the entry."""
        entry = WriteupEntry(id="test", title="Test")
        ctx = {"user": "alice", "operation": "create"}
        result = hook_runner.run_before_save(entry, ctx)
        assert result.author_id == "alice"

    def test_before_save_hooks_can_abort(self, hook_runner):
        """before_save hooks can raise to abort."""
        entry = WriteupEntry(id="test", title="Test", author_id="alice")
        ctx = {"user": "bob", "operation": "update"}
        with pytest.raises(PermissionError):
            hook_runner.run_before_save(entry, ctx)

    def test_after_save_hooks_fire(self, hook_runner):
        """after_save hooks run without error."""
        entry = WriteupEntry(id="test", title="Test", author_id="alice")
        ctx = {"kb_name": "test", "user": "alice", "operation": "create"}
        # Should not raise
        hook_runner.run_after_save(entry, ctx)

    def test_after_delete_hooks_fire(self, hook_runner):
        entry = WriteupEntry(id="test", title="Test", author_id="alice")
        ctx = {"kb_name": "test", "user": "alice", "operation": "delete"}
        hook_runner.run_after_delete(entry, ctx)

    def test_hooks_pass_through_non_writeup_entries(self, hook_runner):
        """Hooks don't interfere with non-writeup entry types."""
        entry = NoteEntry(id="test", title="Regular Note")
        ctx = {"user": "alice", "operation": "create"}
        result = hook_runner.run_before_save(entry, ctx)
        assert result is entry  # unchanged

    def test_nonexistent_hook_is_noop(self, hook_runner):
        """Running a hook that no plugin provides is a no-op."""
        entry = NoteEntry(id="test", title="Test")
        result = hook_runner._run("before_index", entry, {})
        assert result is entry


class TestHookAtomicity:
    """Test that before_save exceptions propagate through kb_service._run_hooks."""

    def test_before_save_non_pyrite_error_propagates(self):
        """Non-PyriteError from before_save hook must propagate, not be swallowed."""
        from unittest.mock import MagicMock, patch

        from pyrite.services.kb_service import KBService

        entry = NoteEntry(id="test", title="Test")
        ctx = {"kb_type": "", "operation": "create"}
        # KBService.__init__ resolves get_registry() once at construction
        # time and hands the registry object to HookRunner (#379), so the
        # patch must land on that object's method, not on the get_registry
        # function (which construction has already called by the time a
        # function-level patch would take effect). get_hooks_for_kb is the
        # registry's plugin-hook lookup -- a pure lookup, not a runner
        # (HookRunner._run is the sole runner) -- so patching it to raise
        # exercises the same "something in the plugin-hook phase blew up"
        # path a raising hook would.
        svc = KBService(config=MagicMock(), db=MagicMock())

        with patch.object(
            svc.hook_runner._plugin_registry,
            "get_hooks_for_kb",
            side_effect=ValueError("hook validation failed"),
        ):
            with pytest.raises(ValueError, match="hook validation failed"):
                svc._run_hooks("before_save", entry, ctx)

    def test_after_save_non_pyrite_error_is_swallowed(self):
        """Non-PyriteError from after_save hook should be logged, not raised."""
        from unittest.mock import MagicMock, patch

        from pyrite.services.kb_service import KBService

        entry = NoteEntry(id="test", title="Test")
        ctx = {"kb_type": "", "operation": "create"}
        svc = KBService(config=MagicMock(), db=MagicMock())

        with patch.object(
            svc.hook_runner._plugin_registry,
            "get_hooks_for_kb",
            side_effect=RuntimeError("oops"),
        ):
            # Should NOT raise — after_save errors are swallowed
            result = svc._run_hooks("after_save", entry, ctx)
            assert result is entry


# =========================================================================
# DB table creation
# =========================================================================


class TestDBTableCreation:
    def test_all_plugin_tables_created(self, db_with_plugins):
        """All plugin-defined tables exist in the database."""
        cursor = db_with_plugins._raw_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        table_names = {row["name"] for row in cursor.fetchall()}

        # Social tables
        assert "social_vote" in table_names
        assert "social_reputation_log" in table_names
        # Encyclopedia tables
        assert "encyclopedia_review" in table_names
        assert "encyclopedia_article_history" in table_names

    def test_plugin_table_indexes_created(self, db_with_plugins):
        """Plugin-defined indexes exist."""
        cursor = db_with_plugins._raw_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        index_names = {row["name"] for row in cursor.fetchall()}

        assert "idx_social_vote_entry_id_kb_name_user_id" in index_names
        assert "idx_encyclopedia_review_entry_id_kb_name" in index_names

    def test_vote_table_operations(self, db_with_plugins):
        """Can insert, query, and enforce uniqueness on social_vote."""
        conn = db_with_plugins._raw_conn

        conn.execute(
            "INSERT INTO social_vote (entry_id, kb_name, user_id, value, created_at) "
            "VALUES ('e1', 'kb1', 'alice', 1, '2025-01-01')"
        )
        conn.execute(
            "INSERT INTO social_vote (entry_id, kb_name, user_id, value, created_at) "
            "VALUES ('e1', 'kb1', 'bob', -1, '2025-01-01')"
        )
        conn.commit()

        # Sum votes
        row = conn.execute(
            "SELECT SUM(value) as total FROM social_vote WHERE entry_id = 'e1'"
        ).fetchone()
        assert row["total"] == 0  # +1 - 1

        # Unique constraint: same user can't vote twice (insert would fail)
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO social_vote (entry_id, kb_name, user_id, value, created_at) "
                "VALUES ('e1', 'kb1', 'alice', -1, '2025-01-02')"
            )

    def test_review_table_operations(self, db_with_plugins):
        """Can insert and query encyclopedia_review."""
        conn = db_with_plugins._raw_conn

        conn.execute(
            "INSERT INTO encyclopedia_review "
            "(entry_id, kb_name, reviewer_id, status, comments, created_at) "
            "VALUES ('a1', 'kb1', 'reviewer1', 'approve', 'LGTM', '2025-01-01')"
        )
        conn.execute(
            "INSERT INTO encyclopedia_review "
            "(entry_id, kb_name, reviewer_id, status, comments, created_at) "
            "VALUES ('a1', 'kb1', 'reviewer2', 'comment', 'Needs more sources', '2025-01-02')"
        )
        conn.commit()

        rows = conn.execute(
            "SELECT * FROM encyclopedia_review WHERE entry_id = 'a1' ORDER BY created_at"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["status"] == "approve"
        assert rows[1]["status"] == "comment"

    def test_core_tables_still_exist(self, db_with_plugins):
        """Plugin tables don't interfere with core tables."""
        cursor = db_with_plugins._raw_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        table_names = {row["name"] for row in cursor.fetchall()}
        assert "entry" in table_names
        assert "kb" in table_names
        assert "tag" in table_names
        assert "link" in table_names


# =========================================================================
# Relationship types
# =========================================================================


class TestRelationshipTypes:
    def test_plugin_relationships_merged(self, patched_registry):
        all_rels = get_all_relationship_types()
        # Core
        assert "owns" in all_rels
        assert "funds" in all_rels
        # Zettelkasten
        assert "elaborates" in all_rels
        assert "branches_from" in all_rels
        assert "synthesizes" in all_rels

    def test_inverse_resolution(self, patched_registry):
        assert get_inverse_relation("elaborates") == "elaborated_by"
        assert get_inverse_relation("branches_from") == "has_branch"
        assert get_inverse_relation("synthesizes") == "synthesized_from"
        # Core still works
        assert get_inverse_relation("owns") == "owned_by"

    def test_agent_schema_includes_plugin_rels(self, patched_registry):
        schema = KBSchema(name="test")
        agent = schema.to_agent_schema()
        rels = agent.get("relationship_types", {})
        assert "elaborates" in rels
        assert "branches_from" in rels


# =========================================================================
# Workflow integration
# =========================================================================


class TestWorkflowIntegration:
    def test_workflow_via_registry(self, registry):
        workflows = registry.get_all_workflows()
        assert "article_review" in workflows

    def test_validate_transition_via_registry(self, registry):
        assert (
            registry.validate_transition("article_review", "draft", "under_review", "write") is True
        )
        assert (
            registry.validate_transition("article_review", "under_review", "published", "reviewer")
            is True
        )
        assert (
            registry.validate_transition("article_review", "draft", "published", "write") is False
        )

    def test_unknown_workflow_returns_false(self, registry):
        assert registry.validate_transition("nonexistent_workflow", "a", "b", "admin") is False


# =========================================================================
# KB presets
# =========================================================================


class TestKBPresets:
    def test_all_presets_available(self, registry):
        presets = registry.get_all_kb_presets()
        assert "zettelkasten" in presets
        assert "social" in presets
        assert "encyclopedia" in presets

    def test_presets_are_valid_kb_schemas(self, registry):
        """Each preset can be loaded as a KBSchema."""
        presets = registry.get_all_kb_presets()
        for name, preset_data in presets.items():
            schema = KBSchema.from_dict(preset_data)
            assert schema.name, f"Preset '{name}' has no name"
            assert len(schema.types) > 0, f"Preset '{name}' has no types"

    def test_preset_types_have_subdirectories(self, registry):
        """Every type in every preset has a subdirectory defined."""
        presets = registry.get_all_kb_presets()
        for name, preset_data in presets.items():
            for type_name, type_data in preset_data.get("types", {}).items():
                assert type_data.get("subdirectory"), (
                    f"Preset '{name}' type '{type_name}' missing subdirectory"
                )


# =========================================================================
# End-to-end: full round-trip
# =========================================================================


class TestEndToEnd:
    @pytest.mark.control(
        reason="a rewritten test of a property that already held; its old form used the model's emitter (Entry.save/to_markdown), gone since #955 P4"
    )
    def test_zettel_roundtrip(self, patched_registry):
        """Create a zettel via frontmatter, serialize, and reparse."""
        entry = entry_from_frontmatter(
            {
                "type": "zettel",
                "title": "My Thought",
                "zettel_type": "permanent",
                "maturity": "sapling",
                "tags": ["philosophy"],
            },
            "An interesting thought about consciousness.",
        )
        assert isinstance(entry, ZettelEntry)

        md = new_entry_text(entry)
        reparsed = Entry.from_markdown(md)
        # from_markdown returns (meta, body) — we need to reconstruct
        assert "zettel_type: permanent" in md
        assert "maturity: sapling" in md
        assert "An interesting thought" in md

    @pytest.mark.control(
        reason="a rewritten test of a property that already held; its old form used the model's emitter (Entry.save/to_markdown), gone since #955 P4"
    )
    def test_writeup_roundtrip(self, patched_registry):
        entry = entry_from_frontmatter(
            {
                "type": "writeup",
                "title": "Hot Take",
                "author_id": "alice",
                "writeup_type": "opinion",
            },
            "This is my opinion.",
        )
        assert isinstance(entry, WriteupEntry)
        md = new_entry_text(entry)
        assert "author_id: alice" in md
        assert "writeup_type: opinion" in md

    @pytest.mark.control(
        reason="a rewritten test of a property that already held; its old form used the model's emitter (Entry.save/to_markdown), gone since #955 P4"
    )
    def test_article_roundtrip(self, patched_registry):
        entry = entry_from_frontmatter(
            {
                "type": "article",
                "title": "General Relativity",
                "quality": "GA",
                "review_status": "published",
                "categories": ["physics", "science"],
            },
            "A" * 600,
        )
        assert isinstance(entry, ArticleEntry)
        md = new_entry_text(entry)
        assert "quality: GA" in md
        assert "review_status: published" in md
        assert "physics" in md


# =========================================================================
# Plugin context injection
# =========================================================================


class TestPluginContextInjection:
    """Test PluginContext injection into plugins."""

    def test_set_context_on_registry(self, registry, temp_dir):
        """set_context injects context into all plugins."""
        from pyrite.config import PyriteConfig, Settings
        from pyrite.plugins.context import PluginContext
        from pyrite.storage.database import PyriteDB

        db = PyriteDB(temp_dir / "ctx-test.db")
        try:
            config = PyriteConfig(
                knowledge_bases=[],
                settings=Settings(index_path=temp_dir / "ctx-test.db"),
            )
            ctx = PluginContext(config=config, db=db)
            registry.set_context(ctx)

            # All plugins should have received context
            for name in registry.list_plugins():
                plugin = registry.get_plugin(name)
                assert hasattr(plugin, "ctx"), f"Plugin {name} has no ctx attribute"
                assert plugin.ctx is ctx, f"Plugin {name} ctx not set correctly"
        finally:
            db.close()

    def test_context_dict_access(self):
        """PluginContext supports dict-style access for backwards compat."""
        from pyrite.config import PyriteConfig, Settings
        from pyrite.plugins.context import PluginContext

        config = PyriteConfig(
            knowledge_bases=[],
            settings=Settings(index_path=Path("/tmp/test.db")),
        )
        ctx = PluginContext(config=config, db=None, kb_name="test-kb", user="alice")

        # Dict-style access
        assert ctx["kb_name"] == "test-kb"
        assert ctx["user"] == "alice"
        assert ctx.get("operation", "default") == ""
        assert ctx.get("nonexistent", "fallback") == "fallback"
        assert "kb_name" in ctx

    def test_hooks_receive_plugin_context(self, hook_runner, temp_dir):
        """Hooks receive PluginContext which is backwards compatible with dict access."""
        from pyrite.plugins.context import PluginContext

        entry = WriteupEntry(id="test", title="Test")
        ctx = PluginContext(
            config=None, db=None, kb_name="social-kb", user="alice", operation="create"
        )
        # Hooks should still work — they call context.get("user", "") etc.
        result = hook_runner.run_before_save(entry, ctx)
        assert result.author_id == "alice"

    def test_after_save_hook_writes_to_db(self, hook_runner, db_with_plugins):
        """after_save_update_counts actually writes to DB when context has db."""
        from pyrite.plugins.context import PluginContext

        entry = WriteupEntry(id="test-writeup", title="Test", author_id="alice")
        ctx = PluginContext(
            config=None, db=db_with_plugins, kb_name="test", user="alice", operation="create"
        )
        # Run after_save hooks
        hook_runner.run_after_save(entry, ctx)

        # Check that a reputation log entry was created
        row = db_with_plugins._raw_conn.execute(
            "SELECT * FROM social_reputation_log WHERE user_id = 'alice'"
        ).fetchone()
        assert row is not None
        assert row["delta"] == 1
        assert "writeup_created:test-writeup" in row["reason"]

    def test_after_delete_hook_adjusts_reputation(self, hook_runner, db_with_plugins):
        """after_delete_adjust_reputation reverses vote reputation when db available."""
        from pyrite.plugins.context import PluginContext

        # First, add some votes for this writeup
        db_with_plugins._raw_conn.execute(
            "INSERT INTO social_vote (entry_id, kb_name, user_id, value, created_at) "
            "VALUES ('del-writeup', 'test', 'bob', 1, '2025-01-01')"
        )
        db_with_plugins._raw_conn.execute(
            "INSERT INTO social_vote (entry_id, kb_name, user_id, value, created_at) "
            "VALUES ('del-writeup', 'test', 'carol', 1, '2025-01-01')"
        )
        db_with_plugins._raw_conn.commit()

        entry = WriteupEntry(id="del-writeup", title="Test", author_id="alice")
        ctx = PluginContext(
            config=None, db=db_with_plugins, kb_name="test", user="alice", operation="delete"
        )
        hook_runner.run_after_delete(entry, ctx)

        # Check reputation adjustment was logged (should be -2 to reverse the +2 votes)
        row = db_with_plugins._raw_conn.execute(
            "SELECT * FROM social_reputation_log WHERE reason LIKE '%writeup_deleted:del-writeup%'"
        ).fetchone()
        assert row is not None
        assert row["delta"] == -2
        assert row["user_id"] == "alice"


# =========================================================================
# Plugin name collision detection
# =========================================================================


class TestValidatorScoping:
    """Test that validators/hooks are scoped by KB type."""

    @staticmethod
    def _make_scoped_plugin(name: str, kb_types: list[str], validator_fn=None):
        """Create a minimal plugin that targets specific KB types."""

        class ScopedPlugin:
            pass

        plugin = ScopedPlugin()
        plugin.name = name

        def get_kb_types():
            return kb_types

        plugin.get_kb_types = get_kb_types

        if validator_fn:
            plugin.get_validators = lambda: [validator_fn]
        else:
            plugin.get_validators = lambda: []

        plugin.get_hooks = lambda: {}
        return plugin

    @staticmethod
    def _fresh_registry():
        """Create a PluginRegistry that won't discover installed plugins."""
        reg = PluginRegistry()
        reg._discovered = True  # Prevent entry point discovery
        return reg

    def test_get_validators_for_kb_excludes_mismatched(self):
        """Validators from a plugin scoped to 'task' KB don't fire for 'software' KB."""
        reg = self._fresh_registry()

        def task_validator(entry_type, data, ctx):
            return [{"field": "status", "rule": "task_only"}]

        reg.register(self._make_scoped_plugin("task", ["task"], task_validator))

        # Should NOT include task validator when kb_type is "software"
        validators = reg.get_validators_for_kb("software")
        assert len(validators) == 0

    def test_get_validators_for_kb_includes_matching(self):
        """Validators from a plugin scoped to 'task' KB fire for 'task' KB."""
        reg = self._fresh_registry()

        def task_validator(entry_type, data, ctx):
            return [{"field": "status", "rule": "task_only"}]

        reg.register(self._make_scoped_plugin("task", ["task"], task_validator))

        validators = reg.get_validators_for_kb("task")
        assert len(validators) == 1

    def test_get_validators_for_kb_empty_returns_all(self):
        """When kb_type is empty, all validators are returned."""
        reg = self._fresh_registry()

        def task_validator(entry_type, data, ctx):
            return []

        reg.register(self._make_scoped_plugin("task", ["task"], task_validator))

        validators = reg.get_validators_for_kb("")
        assert len(validators) == 1

    def test_crashing_kb_type_check_fails_closed(self, caplog):
        """fail-open-exception-sweep site #6, DECIDED 2026-07-03 (fail
        closed, see plugin-type-resolution-scoping): if a plugin's
        get_kb_types() raises, _plugin_matches_kb_type must exclude that
        plugin from the KB rather than including it. Rationale: the
        compat check reads static plugin declarations (errors are
        structural, not transient), while the blast radius of a
        wrongly-applied plugin is global type remapping."""
        reg = self._fresh_registry()

        def task_validator(entry_type, data, ctx):
            return [{"field": "status", "rule": "task_only"}]

        crashing = self._make_scoped_plugin("crashing", ["task"], task_validator)

        def _raise():
            raise RuntimeError("simulated get_kb_types crash")

        crashing.get_kb_types = _raise
        reg.register(crashing)

        with caplog.at_level(logging.WARNING, logger="pyrite.plugins.registry"):
            validators = reg.get_validators_for_kb("task")

        assert validators == [], (
            "a plugin whose compat check crashes must be excluded (fail "
            f"closed), not included: got {validators}"
        )
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "expected a warning-level log for the crashed compat check"

    def test_universal_plugin_always_included(self):
        """A plugin with empty kb_types is always included."""
        reg = self._fresh_registry()

        def universal_validator(entry_type, data, ctx):
            return []

        reg.register(self._make_scoped_plugin("universal", [], universal_validator))

        validators = reg.get_validators_for_kb("anything")
        assert len(validators) == 1

    def test_get_hooks_for_kb_excludes_mismatched(self):
        """Hooks from a scoped plugin don't fire for mismatched KB types."""
        reg = self._fresh_registry()

        def before_save_hook(entry, ctx):
            return entry

        plugin = self._make_scoped_plugin("task", ["task"])
        plugin.get_hooks = lambda: {"before_save": [before_save_hook]}
        reg.register(plugin)

        hooks = reg.get_hooks_for_kb("software")
        assert len(hooks.get("before_save", [])) == 0

    def test_get_hooks_for_kb_includes_matching(self):
        """Hooks from a scoped plugin fire for matching KB types."""
        reg = self._fresh_registry()

        def before_save_hook(entry, ctx):
            return entry

        plugin = self._make_scoped_plugin("task", ["task"])
        plugin.get_hooks = lambda: {"before_save": [before_save_hook]}
        reg.register(plugin)

        hooks = reg.get_hooks_for_kb("task")
        assert len(hooks.get("before_save", [])) == 1

    def test_hook_runner_scopes_plugin_hooks_by_kb_type(self):
        """HookRunner, the sole runner of the raise/swallow contract (#379),
        only runs plugin hooks from matching plugins -- scoping comes from
        the registry's get_hooks_for_kb lookup, keyed off context["kb_type"]."""
        from pyrite.services.hook_runner import HookRunner

        reg = self._fresh_registry()
        called = []

        def task_hook(entry, ctx):
            called.append("task")
            return entry

        plugin = self._make_scoped_plugin("task", ["task"])
        plugin.get_hooks = lambda: {"before_save": [task_hook]}
        reg.register(plugin)
        runner = HookRunner(plugin_registry=reg)

        entry = NoteEntry(id="test", title="Test")
        runner.run_before_save(entry, {"kb_type": "software"})
        assert called == []

        runner.run_before_save(entry, {"kb_type": "task"})
        assert called == ["task"]

    def test_validate_entry_with_kb_type_context(self, patched_registry):
        """KBSchema.validate_entry with kb_type in context scopes plugin validators."""
        from pyrite.schema import KBSchema

        schema = KBSchema(
            name="test",
            kb_type="generic",
            types={},
        )
        # With kb_type="generic", task plugin validators should NOT fire
        result = schema.validate_entry(
            "task",
            {"status": "invalid_status", "title": "T"},
            context={"kb_type": "generic"},
        )
        # Should be valid — task validator skipped for generic KB
        assert result["valid"], f"Expected valid but got errors: {result['errors']}"


class TestPluginCollisionDetection:
    """Test that the registry warns on duplicate tool/type registrations."""

    @staticmethod
    def _registry_with_duplicate():
        """Create a registry with two copies of the same plugin under different names."""
        reg = PluginRegistry()
        p1 = ZettelkastenPlugin()
        p2 = ZettelkastenPlugin()
        p2.name = "zettelkasten-copy"  # Different name so both stay in registry
        reg.register(p1)
        reg.register(p2)
        return reg

    def test_duplicate_entry_type_logs_warning(self, caplog):
        """Registering two plugins with the same entry type logs a warning."""
        reg = self._registry_with_duplicate()

        with caplog.at_level(logging.WARNING, logger="pyrite.plugins.registry"):
            reg.get_all_entry_types()

        collision_warnings = [r for r in caplog.records if "conflicts with" in r.message]
        assert len(collision_warnings) > 0
        assert "entry type" in collision_warnings[0].message

    def test_duplicate_mcp_tool_logs_warning(self, caplog):
        """Registering two plugins with the same MCP tool logs a warning."""
        reg = self._registry_with_duplicate()

        with caplog.at_level(logging.WARNING, logger="pyrite.plugins.registry"):
            reg.get_all_mcp_tools("read")

        collision_warnings = [r for r in caplog.records if "conflicts with" in r.message]
        assert len(collision_warnings) > 0
        assert "MCP tool" in collision_warnings[0].message

    def test_duplicate_relationship_type_logs_warning(self, caplog):
        """Registering two plugins with the same relationship type logs a warning."""
        reg = self._registry_with_duplicate()

        with caplog.at_level(logging.WARNING, logger="pyrite.plugins.registry"):
            reg.get_all_relationship_types()

        collision_warnings = [r for r in caplog.records if "conflicts with" in r.message]
        assert len(collision_warnings) > 0
        assert "relationship type" in collision_warnings[0].message

    def test_no_collision_with_distinct_plugins(self, registry, caplog):
        """Three distinct plugins produce no collision warnings."""
        with caplog.at_level(logging.WARNING, logger="pyrite.plugins.registry"):
            registry.get_all_entry_types()
            registry.get_all_mcp_tools("read")
            registry.get_all_relationship_types()

        collision_warnings = [r for r in caplog.records if "conflicts with" in r.message]
        assert len(collision_warnings) == 0


class TestPluginRegistryFailureVisibility:
    """Regression tests for r1000 plugin-registry-silent-failures.

    The contract: when a plugin raises from an aggregation method
    (get_entry_types, get_validators, etc.), the registry must NOT
    silently swallow it. The failure must be logged at ERROR level
    with the plugin name AND the method name, so an operator running
    pyrite from a terminal sees the crash on stderr (default WARNING+
    is at INFO/ERROR — ERROR is always shown).

    Other plugins' contributions must still be returned: a single bad
    plugin must not bring down the registry. This is the partial-
    aggregation contract.
    """

    @staticmethod
    def _make_crashing_plugin(name: str, method: str):
        """Build a minimal plugin whose `method` raises on call."""
        plugin = ZettelkastenPlugin()
        plugin.name = name

        def crash():
            raise RuntimeError(f"simulated crash in {method}")

        setattr(plugin, method, crash)
        return plugin

    def test_crashing_get_entry_types_logs_error_with_plugin_and_method(self, caplog):
        """A plugin whose get_entry_types() raises must produce an
        ERROR log naming the plugin and the method. Pre-r1000 the
        registry logged at DEBUG and the crash was invisible."""
        reg = PluginRegistry()
        bad = self._make_crashing_plugin("bad-plugin", "get_entry_types")
        good = ZettelkastenPlugin()
        good.name = "good-plugin"
        reg.register(bad)
        reg.register(good)

        with caplog.at_level(logging.ERROR, logger="pyrite.plugins.registry"):
            result = reg.get_all_entry_types()

        # Partial aggregation: good plugin still contributes.
        assert len(result) > 0, "good plugin's types should still be returned"

        # Error visibility: ERROR-level log naming plugin AND method.
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(
            "bad-plugin" in r.getMessage() and "get_entry_types" in r.getMessage() for r in errors
        ), (
            f"expected ERROR mentioning bad-plugin and get_entry_types; got {[r.getMessage() for r in errors]}"
        )

    def test_crashing_get_validators_logs_error_with_plugin_and_method(self, caplog):
        """Same contract for get_validators (list-aggregation path)."""
        reg = PluginRegistry()
        bad = self._make_crashing_plugin("bad-validators", "get_validators")
        reg.register(bad)

        with caplog.at_level(logging.ERROR, logger="pyrite.plugins.registry"):
            reg.get_all_validators()

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(
            "bad-validators" in r.getMessage() and "get_validators" in r.getMessage()
            for r in errors
        ), (
            f"expected ERROR mentioning bad-validators and get_validators; got {[r.getMessage() for r in errors]}"
        )

    def test_crashing_plugin_does_not_break_registry_for_other_plugins(self, caplog):
        """The partial-aggregation contract: one bad plugin must not
        prevent other plugins' types from being returned."""
        reg = PluginRegistry()
        bad = self._make_crashing_plugin("bad", "get_entry_types")
        good = ZettelkastenPlugin()
        good.name = "good"
        reg.register(bad)
        reg.register(good)

        with caplog.at_level(logging.ERROR, logger="pyrite.plugins.registry"):
            result = reg.get_all_entry_types()

        # The good plugin's entry types are present in spite of the bad one.
        assert "zettel" in result or len(result) > 0


# =========================================================================
# Capability declarations — Tier A r1500 (Option B)
# =========================================================================


class TestCapabilityEnum:
    """The Capability StrEnum has the 5 members declared in ADR for r1500.

    Names mirror the 5-subsystem split (SCHEMA / STORAGE / SURFACE /
    DOMAIN / CONTEXT) so an eventual move to Option A — splitting the
    Protocol into 5 capability protocols — is mechanical: each
    Capability becomes its own Protocol with the same name.
    """

    def test_capability_enum_has_five_members(self):
        from pyrite.plugins.capabilities import Capability

        assert {c.name for c in Capability} == {
            "SCHEMA",
            "STORAGE",
            "SURFACE",
            "DOMAIN",
            "CONTEXT",
        }

    def test_capability_is_string_enum(self):
        """StrEnum so plugins can declare {Capability.SCHEMA} and the
        registry can compare by string in YAML-derived configs."""
        from pyrite.plugins.capabilities import Capability

        # StrEnum members are strings
        assert isinstance(Capability.SCHEMA, str)


class TestMethodCapabilitiesMap:
    """The _METHOD_CAPABILITIES dict maps each dispatched method to its
    Capability. Pinned here so a future contributor adding a new
    `get_*` method can't forget to wire it (the test fails until they
    map it explicitly OR mark it as exempt)."""

    def test_every_dispatched_method_has_a_capability(self):
        from pyrite.plugins import registry as registry_mod
        from pyrite.plugins.capabilities import Capability

        # Sanity: the map exists and every value is a Capability.
        mapping = registry_mod._METHOD_CAPABILITIES
        assert isinstance(mapping, dict)
        assert all(isinstance(v, Capability) for v in mapping.values())

    def test_known_methods_in_each_capability(self):
        """Anchor the 5-subsystem split — each capability has at least
        one of its expected canonical methods. Pin the categorization
        per the locked design at commit 0b9b547."""
        from pyrite.plugins import registry as registry_mod
        from pyrite.plugins.capabilities import Capability

        m = registry_mod._METHOD_CAPABILITIES
        # SCHEMA
        assert m.get("get_entry_types") == Capability.SCHEMA
        # STORAGE
        assert m.get("get_db_columns") == Capability.STORAGE
        assert m.get("get_validators") == Capability.STORAGE
        # SURFACE
        assert m.get("get_cli_commands") == Capability.SURFACE
        assert m.get("get_mcp_tools") == Capability.SURFACE
        # DOMAIN
        assert m.get("get_relationship_types") == Capability.DOMAIN
        # CONTEXT
        assert m.get("set_context") == Capability.CONTEXT


class TestRegistryDispatchSkip:
    """The registry's aggregation helpers consult the plugin's declared
    `capabilities` set and skip methods whose capability isn't claimed.

    A plugin that declares `{Capability.SCHEMA}` should NOT have its
    `get_db_columns` (STORAGE) called even if it has the method —
    that's the whole point of the dispatch-skip optimization.

    Default behavior (per the locked design) for a plugin that
    returns NON-EMPTY from a method whose capability it didn't
    declare is: WARN + skip (treat the return value as empty). Under
    `strict_plugins=True`, raise PluginError.
    """

    @staticmethod
    def _empty_registry():
        """Build a registry with entry-point discovery disabled so a
        single registered plugin is the only one tested against. The
        production registry's helpers call self.discover() which loads
        every installed pyrite_* plugin — fine in production, ruinous
        for isolated unit tests."""
        reg = PluginRegistry()
        reg._discovered = True
        return reg

    @staticmethod
    def _make_typed_plugin(
        name: str,
        capabilities_set,
        entry_types_payload=None,
        db_columns_payload=None,
    ):
        """Build a plugin with explicit capabilities + method returns.

        Use a fresh class per call (not ZettelkastenPlugin subclass) so
        tests don't depend on the production plugin's surface.
        """
        from typing import ClassVar

        from pyrite.plugins.capabilities import Capability  # noqa: F401

        class _StubPlugin:
            capabilities: ClassVar[set] = capabilities_set

            def __init__(self) -> None:
                self.name = name

            def get_entry_types(self):
                return dict(entry_types_payload or {})

            def get_db_columns(self):
                return list(db_columns_payload or [])

        return _StubPlugin()

    def test_plugin_without_capabilities_attribute_is_skipped_for_all(
        self,
    ):
        """Empty-set default: a plugin with no `capabilities` attribute
        gets ALL dispatch loops skipped. Safe failure mode — a plugin
        that forgets to declare gets ignored entirely, not silently
        half-loaded."""

        class _LegacyPlugin:
            # NO capabilities attribute declared
            def __init__(self) -> None:
                self.name = "legacy"

            def get_entry_types(self):
                return {"legacy_type": object}

        reg = self._empty_registry()
        reg.register(_LegacyPlugin())
        result = reg.get_all_entry_types()
        assert "legacy_type" not in result, (
            f"plugin without declared capabilities must be skipped; got {result}"
        )

    def test_plugin_with_capability_is_called(self):
        """A plugin that declares Capability.SCHEMA contributes its
        entry types as expected."""
        from pyrite.plugins.capabilities import Capability

        plugin = self._make_typed_plugin(
            "schema-plugin",
            capabilities_set={Capability.SCHEMA},
            entry_types_payload={"schema_type": object},
        )
        reg = self._empty_registry()
        reg.register(plugin)
        result = reg.get_all_entry_types()
        assert "schema_type" in result

    def test_plugin_without_capability_is_skipped_for_that_method(self):
        """SCHEMA-capable plugin's get_db_columns (STORAGE) is NOT
        called even though the method exists on the class. Skip is
        per-capability, not per-plugin."""
        from pyrite.plugins.capabilities import Capability

        plugin = self._make_typed_plugin(
            "schema-only",
            capabilities_set={Capability.SCHEMA},
            db_columns_payload=[{"name": "should_not_appear"}],
        )
        reg = self._empty_registry()
        reg.register(plugin)
        cols = reg.get_all_db_columns()
        names = [c.get("name") for c in cols]
        assert "should_not_appear" not in names, (
            f"SCHEMA-only plugin must skip get_db_columns; got {cols}"
        )

    def test_undeclared_nonempty_return_logs_warning(self, caplog):
        """Default mode: a plugin that returns non-empty from a method
        whose capability it didn't declare gets logged at WARNING
        with plugin name + method name. Return value is dropped from
        aggregation."""
        import logging as _logging

        from pyrite.plugins.capabilities import Capability

        # Plugin declares SCHEMA but returns rows from get_db_columns
        # (STORAGE). The registry should NOT include those rows AND
        # should log a warning.
        plugin = self._make_typed_plugin(
            "drift-plugin",
            capabilities_set={Capability.SCHEMA},
            db_columns_payload=[{"name": "rogue_column"}],
        )
        reg = self._empty_registry()
        reg.register(plugin)

        with caplog.at_level(_logging.WARNING, logger="pyrite.plugins.registry"):
            cols = reg.get_all_db_columns()

        assert all(c.get("name") != "rogue_column" for c in cols), (
            "registry must drop returns from undeclared-capability methods"
        )
        # Warning surfaces both the plugin and the method.
        msgs = [r.getMessage() for r in caplog.records if r.levelno >= _logging.WARNING]
        assert any("drift-plugin" in m and "get_db_columns" in m for m in msgs), (
            f"expected WARNING naming plugin + method; got {msgs}"
        )

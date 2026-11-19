"""Tests for the Social KB extension."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from pyrite_social.cli import social_app
from pyrite_social.entry_types import WRITEUP_TYPES, UserProfileEntry, WriteupEntry
from pyrite_social.hooks import before_save_author_check
from pyrite_social.plugin import SocialPlugin
from pyrite_social.preset import SOCIAL_PRESET
from pyrite_social.tables import SOCIAL_TABLES
from pyrite_social.validators import validate_social
from typer.testing import CliRunner

from pyrite.plugins.registry import PluginRegistry
from pyrite.services.hook_runner import HookRunner
from tests.cli_help import plain

# =========================================================================
# Plugin registration
# =========================================================================


class TestReadableSetFiltering:
    """`social_top` and `social_newest` narrow to the caller's readable KBs.

    `kb_name` is optional on both, so with it omitted they read the whole
    index. The MCP chokepoint refuses an optional-KB tool that cannot filter
    for a scoped caller -- safe, but a product regression -- and these two
    take the readable set now, so a scoped caller gets a filtered page
    instead of a refusal (#223).
    """

    @pytest.fixture
    def plugin(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "index.db")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE entry (id TEXT, kb_name TEXT, title TEXT, entry_type TEXT,"
            " metadata TEXT, created_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE social_vote (entry_id TEXT, kb_name TEXT, value INTEGER, created_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO entry (id, kb_name, title, entry_type, metadata, created_at)"
            " VALUES (?, ?, ?, 'writeup', NULL, ?)",
            [
                ("pub-1", "public-kb", "Public writeup", "2026-01-01"),
                ("priv-1", "private-kb", "Private writeup", "2026-01-02"),
            ],
        )
        conn.commit()

        plugin = SocialPlugin()
        plugin._get_db = lambda: (SimpleNamespace(_raw_conn=conn), False)
        return plugin

    def test_newest_narrows_to_the_readable_set(self, plugin):
        out = plugin._mcp_newest({}, readable_kbs={"public-kb"})
        assert [w["id"] for w in out["newest"]] == ["pub-1"]

    def test_top_narrows_to_the_readable_set(self, plugin):
        out = plugin._mcp_top({}, readable_kbs={"public-kb"})
        assert [w["id"] for w in out["top"]] == ["pub-1"]

    def test_an_unscoped_caller_still_spans_every_kb(self, plugin):
        out = plugin._mcp_newest({})
        assert {w["id"] for w in out["newest"]} == {"pub-1", "priv-1"}

    def test_an_empty_readable_set_returns_nothing_rather_than_everything(self, plugin):
        assert plugin._mcp_newest({}, readable_kbs=set()) == {"count": 0, "newest": []}
        assert plugin._mcp_top({}, readable_kbs=set()) == {"count": 0, "top": []}

    def test_a_named_kb_binds_to_that_kb(self, plugin):
        out = plugin._mcp_newest(
            {"kb_name": "private-kb"}, readable_kbs={"public-kb", "private-kb"}
        )
        assert [w["id"] for w in out["newest"]] == ["priv-1"]


class TestPluginRegistration:
    def test_plugin_has_name(self):
        plugin = SocialPlugin()
        assert plugin.name == "social"

    def test_register_with_registry(self):
        registry = PluginRegistry()
        registry.register(SocialPlugin())
        assert "social" in registry.list_plugins()

    def test_entry_types_registered(self):
        registry = PluginRegistry()
        registry.register(SocialPlugin())
        types = registry.get_all_entry_types()
        assert "writeup" in types
        assert "user_profile" in types
        assert types["writeup"] is WriteupEntry
        assert types["user_profile"] is UserProfileEntry

    def test_validators_registered(self):
        registry = PluginRegistry()
        registry.register(SocialPlugin())
        validators = registry.get_all_validators()
        assert validate_social in validators

    def test_cli_commands_registered(self):
        registry = PluginRegistry()
        registry.register(SocialPlugin())
        commands = registry.get_all_cli_commands()
        cmd_names = [name for name, _ in commands]
        assert "social" in cmd_names

    def test_mcp_read_tools(self):
        registry = PluginRegistry()
        registry.register(SocialPlugin())
        tools = registry.get_all_mcp_tools("read")
        assert "social_top" in tools
        assert "social_newest" in tools
        assert "social_reputation" in tools
        assert "social_vote" not in tools  # write-only
        assert "social_post" not in tools  # write-only

    def test_mcp_write_tools(self):
        registry = PluginRegistry()
        registry.register(SocialPlugin())
        tools = registry.get_all_mcp_tools("write")
        assert "social_top" in tools
        assert "social_vote" in tools
        assert "social_post" in tools

    def test_db_tables_registered(self):
        registry = PluginRegistry()
        registry.register(SocialPlugin())
        tables = registry.get_all_db_tables()
        table_names = [t["name"] for t in tables]
        assert "social_vote" in table_names
        assert "social_reputation_log" in table_names

    def test_hooks_registered(self):
        registry = PluginRegistry()
        registry.register(SocialPlugin())
        hooks = registry.get_all_hooks()
        assert "before_save" in hooks
        assert "after_save" in hooks
        assert "after_delete" in hooks
        assert before_save_author_check in hooks["before_save"]

    def test_kb_presets_registered(self):
        registry = PluginRegistry()
        registry.register(SocialPlugin())
        presets = registry.get_all_kb_presets()
        assert "social" in presets

    def test_kb_types_registered(self):
        registry = PluginRegistry()
        registry.register(SocialPlugin())
        kb_types = registry.get_all_kb_types()
        assert "social" in kb_types


# =========================================================================
# Entry types
# =========================================================================


class TestWriteupEntry:
    def test_default_values(self):
        entry = WriteupEntry(id="test", title="Test")
        assert entry.entry_type == "writeup"
        assert entry.author_id == ""
        assert entry.writeup_type == "essay"
        assert entry.allow_voting is True

    def test_to_frontmatter(self):
        entry = WriteupEntry(
            id="test",
            title="My Essay",
            author_id="alice",
            writeup_type="opinion",
            allow_voting=False,
        )
        fm = entry.to_frontmatter()
        assert fm["type"] == "writeup"
        assert fm["author_id"] == "alice"
        assert fm["writeup_type"] == "opinion"
        assert fm["allow_voting"] is False

    def test_to_frontmatter_field_presence(self):
        # 44f81f3: meaningful enum defaults are written so readers/agents
        # don't have to know the default; allow_voting stays omitted at default.
        entry = WriteupEntry(id="test", title="Test", author_id="bob")
        fm = entry.to_frontmatter()
        assert fm["writeup_type"] == "essay"  # default written
        assert "allow_voting" not in fm  # default True omitted
        assert fm["author_id"] == "bob"

    def test_from_frontmatter(self):
        meta = {
            "id": "my-essay",
            "title": "My Essay",
            "type": "writeup",
            "author_id": "alice",
            "writeup_type": "review",
            "allow_voting": False,
            "tags": ["review"],
        }
        entry = WriteupEntry.from_frontmatter(meta, "Great review")
        assert entry.id == "my-essay"
        assert entry.title == "My Essay"
        assert entry.author_id == "alice"
        assert entry.writeup_type == "review"
        assert entry.allow_voting is False
        assert entry.body == "Great review"
        assert entry.tags == ["review"]

    def test_from_frontmatter_defaults(self):
        meta = {"title": "Quick Post"}
        entry = WriteupEntry.from_frontmatter(meta, "")
        assert entry.author_id == ""
        assert entry.writeup_type == "essay"
        assert entry.allow_voting is True


class TestUserProfileEntry:
    def test_default_values(self):
        entry = UserProfileEntry(id="alice", title="Alice")
        assert entry.entry_type == "user_profile"
        assert entry.reputation == 0
        assert entry.join_date == ""
        assert entry.writeup_count == 0

    def test_to_frontmatter(self):
        entry = UserProfileEntry(
            id="alice",
            title="Alice",
            reputation=42,
            join_date="2025-01-01",
            writeup_count=10,
        )
        fm = entry.to_frontmatter()
        assert fm["type"] == "user_profile"
        assert fm["reputation"] == 42
        assert fm["join_date"] == "2025-01-01"
        assert fm["writeup_count"] == 10

    def test_from_frontmatter(self):
        meta = {
            "id": "alice",
            "title": "Alice",
            "type": "user_profile",
            "reputation": 42,
            "join_date": "2025-01-01",
            "writeup_count": 10,
        }
        entry = UserProfileEntry.from_frontmatter(meta, "Alice's bio")
        assert entry.reputation == 42
        assert entry.join_date == "2025-01-01"
        assert entry.writeup_count == 10
        assert entry.body == "Alice's bio"


# =========================================================================
# Validators
# =========================================================================


class TestValidators:
    def test_writeup_requires_author_id(self):
        errors = validate_social("writeup", {}, {})
        assert any(e["field"] == "author_id" for e in errors)

    def test_writeup_with_author_ok(self):
        errors = validate_social("writeup", {"author_id": "alice"}, {})
        assert not any(e["field"] == "author_id" for e in errors)

    def test_writeup_invalid_type(self):
        errors = validate_social("writeup", {"author_id": "alice", "writeup_type": "invalid"}, {})
        assert any(e["field"] == "writeup_type" for e in errors)

    def test_writeup_valid_types(self):
        for wt in WRITEUP_TYPES:
            errors = validate_social("writeup", {"author_id": "alice", "writeup_type": wt}, {})
            assert not any(e["field"] == "writeup_type" for e in errors)

    def test_ignores_other_types(self):
        errors = validate_social("note", {"title": "regular note"}, {})
        assert errors == []


# =========================================================================
# Hooks
# =========================================================================


class TestHooks:
    def test_before_save_sets_author_on_create(self):
        entry = WriteupEntry(id="test", title="Test")
        ctx = {"user": "alice", "operation": "create"}
        result = before_save_author_check(entry, ctx)
        assert result.author_id == "alice"

    def test_before_save_preserves_existing_author(self):
        entry = WriteupEntry(id="test", title="Test", author_id="bob")
        ctx = {"user": "alice", "operation": "create"}
        result = before_save_author_check(entry, ctx)
        assert result.author_id == "bob"  # bob was already set

    def test_before_save_allows_author_update(self):
        entry = WriteupEntry(id="test", title="Test", author_id="alice")
        ctx = {"user": "alice", "operation": "update"}
        result = before_save_author_check(entry, ctx)
        assert result.author_id == "alice"

    def test_before_save_blocks_non_author_update(self):
        entry = WriteupEntry(id="test", title="Test", author_id="alice")
        ctx = {"user": "bob", "operation": "update"}
        with pytest.raises(PermissionError, match="bob.*cannot edit.*alice"):
            before_save_author_check(entry, ctx)

    def test_before_save_ignores_non_writeups(self):
        from pyrite.models.core_types import NoteEntry

        entry = NoteEntry(id="test", title="Test")
        ctx = {"user": "alice", "operation": "create"}
        result = before_save_author_check(entry, ctx)
        assert result is entry  # unchanged

    def test_before_save_allows_update_without_user(self):
        """If no user in context, skip the check (e.g., system operations)."""
        entry = WriteupEntry(id="test", title="Test", author_id="alice")
        ctx = {"user": "", "operation": "update"}
        result = before_save_author_check(entry, ctx)
        assert result is entry

    def test_hooks_run_via_registry(self):
        """Test that hooks fire through HookRunner, which owns the
        raise/swallow contract for both core and plugin hooks (#379); the
        registry's run_hooks_for_kb is a pure lookup, not a runner."""
        registry = PluginRegistry()
        registry.register(SocialPlugin())
        runner = HookRunner(plugin_registry=registry)

        entry = WriteupEntry(id="test", title="Test")
        ctx = {"user": "alice", "operation": "create"}
        result = runner.run_before_save(entry, ctx)
        assert result.author_id == "alice"

    def test_hooks_abort_on_permission_error(self):
        """Test that before_save hooks can abort via HookRunner."""
        registry = PluginRegistry()
        registry.register(SocialPlugin())
        runner = HookRunner(plugin_registry=registry)

        entry = WriteupEntry(id="test", title="Test", author_id="alice")
        ctx = {"user": "bob", "operation": "update"}
        with pytest.raises(PermissionError):
            runner.run_before_save(entry, ctx)


# =========================================================================
# Custom DB tables
# =========================================================================


class TestDBTables:
    def test_vote_table_definition(self):
        vote_table = next(t for t in SOCIAL_TABLES if t["name"] == "social_vote")
        col_names = [c["name"] for c in vote_table["columns"]]
        assert "id" in col_names
        assert "entry_id" in col_names
        assert "kb_name" in col_names
        assert "user_id" in col_names
        assert "value" in col_names
        assert "created_at" in col_names

    def test_vote_table_has_unique_constraint(self):
        vote_table = next(t for t in SOCIAL_TABLES if t["name"] == "social_vote")
        unique_indexes = [i for i in vote_table["indexes"] if i.get("unique")]
        assert len(unique_indexes) == 1
        assert set(unique_indexes[0]["columns"]) == {"entry_id", "kb_name", "user_id"}

    def test_reputation_log_table_definition(self):
        rep_table = next(t for t in SOCIAL_TABLES if t["name"] == "social_reputation_log")
        col_names = [c["name"] for c in rep_table["columns"]]
        assert "user_id" in col_names
        assert "delta" in col_names
        assert "reason" in col_names

    def test_tables_created_in_sqlite(self):
        """Test that the table definitions produce valid SQL."""
        import tempfile

        from pyrite.storage.database import PyriteDB

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"

            # Manually register plugin before creating DB
            import pyrite.plugins.registry as reg_module

            old = reg_module._registry
            registry = PluginRegistry()
            registry.register(SocialPlugin())
            reg_module._registry = registry

            try:
                db = PyriteDB(db_path)

                # Verify tables exist
                cursor = db._raw_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                table_names = [row["name"] for row in cursor.fetchall()]
                assert "social_vote" in table_names
                assert "social_reputation_log" in table_names

                # Verify we can insert and query
                db._raw_conn.execute(
                    "INSERT INTO social_vote (entry_id, kb_name, user_id, value, created_at) "
                    "VALUES ('e1', 'kb1', 'alice', 1, '2025-01-01')"
                )
                db._raw_conn.commit()

                row = db._raw_conn.execute(
                    "SELECT * FROM social_vote WHERE entry_id = 'e1'"
                ).fetchone()
                assert row["user_id"] == "alice"
                assert row["value"] == 1

                # Verify unique constraint
                with pytest.raises(sqlite3.IntegrityError):
                    db._raw_conn.execute(
                        "INSERT INTO social_vote (entry_id, kb_name, user_id, value, created_at) "
                        "VALUES ('e1', 'kb1', 'alice', -1, '2025-01-02')"
                    )

                db.close()
            finally:
                reg_module._registry = old


# =========================================================================
# Preset
# =========================================================================


class TestPreset:
    def test_preset_structure(self):
        p = SOCIAL_PRESET
        assert p["name"] == "my-community"
        assert "writeup" in p["types"]
        assert "user_profile" in p["types"]
        assert p["policies"]["public"] is True
        assert p["policies"]["author_edit_only"] is True
        assert p["policies"]["voting_enabled"] is True

    def test_preset_directories(self):
        assert "writeups" in SOCIAL_PRESET["directories"]
        assert "users" in SOCIAL_PRESET["directories"]


# =========================================================================
# Core integration
# =========================================================================


class TestCoreIntegration:
    def test_entry_class_resolution(self):
        import pyrite.plugins.registry as reg_module

        registry = PluginRegistry()
        registry.register(SocialPlugin())
        old = reg_module._registry
        reg_module._registry = registry

        try:
            from pyrite.models.core_types import get_entry_class

            assert get_entry_class("writeup") is WriteupEntry
            assert get_entry_class("user_profile") is UserProfileEntry
        finally:
            reg_module._registry = old

    def test_entry_from_frontmatter_resolution(self):
        import pyrite.plugins.registry as reg_module

        registry = PluginRegistry()
        registry.register(SocialPlugin())
        old = reg_module._registry
        reg_module._registry = registry

        try:
            from pyrite.models.core_types import entry_from_frontmatter

            entry = entry_from_frontmatter(
                {"type": "writeup", "title": "Test", "author_id": "alice"},
                "Body",
            )
            assert isinstance(entry, WriteupEntry)
            assert entry.author_id == "alice"
        finally:
            reg_module._registry = old

    def test_multiple_plugins_coexist(self):
        """Both zettelkasten and social plugins can be registered together."""
        from pyrite_zettelkasten.entry_types import ZettelEntry
        from pyrite_zettelkasten.plugin import ZettelkastenPlugin

        registry = PluginRegistry()
        registry.register(ZettelkastenPlugin())
        registry.register(SocialPlugin())

        types = registry.get_all_entry_types()
        assert "zettel" in types
        assert "writeup" in types
        assert types["zettel"] is ZettelEntry
        assert types["writeup"] is WriteupEntry

        # Both provide validators
        validators = registry.get_all_validators()
        assert len(validators) >= 2

        # Both provide CLI commands
        commands = registry.get_all_cli_commands()
        cmd_names = [name for name, _ in commands]
        assert "zettel" in cmd_names
        assert "social" in cmd_names

        # Both provide presets
        presets = registry.get_all_kb_presets()
        assert "zettelkasten" in presets
        assert "social" in presets


# =========================================================================
# _mcp_post goes through the write pipeline (#391)
# =========================================================================


class TestMcpPostGoesThroughPipeline:
    """`social_post` used to call `KBRepository.save` + `IndexManager.index_entry`
    directly (plugin.py:336-372 before this fix), skipping `KBService._prepare`:
    no exists check (an existing id was silently overwritten), no hooks, and
    error responses had no `error_code`. These tests pin the pipeline instead.
    """

    @pytest.fixture
    def env(self, tmp_path):
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        kb_path = tmp_path / "social-kb"
        kb_path.mkdir()
        (kb_path / "kb.yaml").write_text(
            "name: social-kb\n"
            "kb_type: social\n"
            "validation:\n"
            "  enforce: true\n"
            "types:\n"
            "  writeup:\n"
            "    optional: [writeup_type, allow_voting, author_id]\n"
            "    subdirectory: writeups/\n"
        )
        db_path = tmp_path / "index.db"
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="social-kb", path=kb_path, kb_type="social")],
            settings=Settings(index_path=db_path, auto_embed=False),
        )
        db = PyriteDB(db_path)
        IndexManager(db, config).index_all()
        return {"config": config, "kb_path": kb_path, "db": db}

    @pytest.fixture
    def plugin(self, env):
        from pyrite.plugins.context import PluginContext

        p = SocialPlugin()
        p.set_context(PluginContext(config=env["config"], db=env["db"], kb_name="social-kb"))
        return p

    def _file(self, env, entry_id):
        found = list(env["kb_path"].rglob(f"{entry_id}.md"))
        assert len(found) <= 1, found
        return found[0] if found else None

    @pytest.mark.control(
        reason="the old direct IndexManager.index_entry call already indexed "
        "immediately; this pins that the pipeline keeps doing so, not the bug"
    )
    def test_new_post_is_indexed_immediately(self, env, plugin):
        result = plugin._mcp_post(
            {
                "kb_name": "social-kb",
                "title": "My First Post",
                "body": "Hello world",
                "author_id": "alice",
            }
        )
        assert result["created"] is True, result
        row = env["db"].get_entry("my-first-post", "social-kb")
        assert row is not None, "new post should be indexed without a separate sync"
        assert self._file(env, "my-first-post") is not None

    def test_new_post_runs_before_and_after_save_hooks(self, env, plugin, monkeypatch):
        from pyrite.plugins.registry import get_registry

        before_calls = []
        after_calls = []

        def before_save(entry, ctx):
            before_calls.append(entry.id)

        def after_save(entry, ctx):
            after_calls.append(entry.id)

        class ProbePlugin:
            name = "probe_hook_plugin"

            def get_hooks(self):
                return {"before_save": [before_save], "after_save": [after_save]}

        reg = get_registry()
        reg.register(ProbePlugin())
        try:
            result = plugin._mcp_post(
                {
                    "kb_name": "social-kb",
                    "title": "Hooked Post",
                    "body": "Body",
                    "author_id": "alice",
                }
            )
            assert result["created"] is True, result
            assert before_calls == ["hooked-post"], before_calls
            assert after_calls == ["hooked-post"], after_calls
        finally:
            del reg._plugins["probe_hook_plugin"]

    def test_a_storage_error_reports_its_public_message_not_the_raw_detail(
        self, env, plugin, monkeypatch
    ):
        """ADR-0037 theme 2 round 2 (conductor cold read of 5d65caa7, item 1):
        _mcp_post's except PyriteError clause put str(e) straight into the
        "error" field, bypassing the public_message every other transport
        already respects. A StorageError's str(exc) can carry server-side
        detail (a real path, a driver's own text)."""
        from pyrite.exceptions import StorageError
        from pyrite.services.kb_service import KBService

        def _boom(self, *a, **kw):
            raise StorageError(
                "Failed to write /real/secret/server/path/writeup.md: disk quota exceeded"
            )

        monkeypatch.setattr(KBService, "create_entry", _boom)
        result = plugin._mcp_post(
            {
                "kb_name": "social-kb",
                "title": "Boom",
                "body": "body",
                "author_id": "alice",
            }
        )
        assert "/real/secret/server/path" not in result["error"], result
        assert result["error"] == (
            "A storage operation failed. An administrator needs to check the "
            "server log for the underlying error."
        ), result
        assert result["error_code"] == "STORAGE_ERROR", result

    def test_existing_id_is_refused_with_entry_exists_and_file_untouched(self, env, plugin):
        first = plugin._mcp_post(
            {
                "kb_name": "social-kb",
                "title": "Same Title",
                "body": "ORIGINAL",
                "author_id": "alice",
            }
        )
        assert first["created"] is True, first
        original = self._file(env, "same-title").read_bytes()

        result = plugin._mcp_post(
            {
                "kb_name": "social-kb",
                "title": "Same Title",
                "body": "REPLACED",
                "author_id": "bob",
            }
        )
        assert result.get("created") is not True, result
        assert result.get("error_code") == "ENTRY_EXISTS", result
        assert self._file(env, "same-title").read_bytes() == original


# CLI numeric limit regression tests (#1561)


@pytest.mark.parametrize("command", ["newest", "top"])
@pytest.mark.parametrize("limit", ["0", "-1"])
def test_social_limit_rejects_nonpositive_values(command, limit):
    result = CliRunner().invoke(
        social_app,
        [command, "--limit", limit],
    )

    assert result.exit_code == 2, result.output
    assert "--limit" in plain(result.output)


@pytest.mark.control(reason="Positive limits already work before the fix; preserve valid input.")
@pytest.mark.parametrize("command", ["newest", "top"])
def test_social_limit_accepts_one(command):
    result = CliRunner().invoke(
        social_app,
        [command, "--limit", "1"],
    )

    assert result.exit_code == 0, result.output

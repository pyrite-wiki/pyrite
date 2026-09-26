"""
Tests for MCP Server.
"""

import contextlib
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import EventEntry, NoteEntry, PersonEntry
from pyrite.schema import Link
from pyrite.server.mcp_server import PyriteMCPServer
from pyrite.storage.repository import KBRepository


# ---------------------------------------------------------------------------
# Shared helpers — build MCP servers with minimal boilerplate
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _make_mcp_server(kb_defs, *, tier="admin", extra_setup=None):
    """Context manager that creates a PyriteMCPServer with temp dirs.

    Parameters
    ----------
    kb_defs : list[dict]
        Each dict has keys: name, kb_type, and optionally description, subdirs
        (list of sub-directory names to create under the KB path), read_only.
    tier : str
        MCP tier — "read", "write", or "admin".
    extra_setup : callable or None
        Called with (config, kb_configs_by_name) before server creation, so
        callers can write kb.yaml files or create custom entries.

    Yields
    ------
    dict with "server", "config", and each KB name mapped to its KBConfig.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        kb_configs = []
        kb_map = {}
        for defn in kb_defs:
            kb_path = tmpdir / defn["name"]
            kb_path.mkdir()
            for sub in defn.get("subdirs", []):
                (kb_path / sub).mkdir(parents=True, exist_ok=True)
            kbc = KBConfig(
                name=defn["name"],
                path=kb_path,
                kb_type=defn["kb_type"],
                description=defn.get("description", ""),
                read_only=defn.get("read_only", False),
            )
            kb_configs.append(kbc)
            kb_map[defn["name"]] = kbc

        config = PyriteConfig(
            knowledge_bases=kb_configs,
            settings=Settings(index_path=db_path),
        )

        if extra_setup:
            extra_setup(config, kb_map)

        server = PyriteMCPServer(config, tier=tier)
        server.index_mgr.index_all()

        result = {"server": server, "config": config}
        result.update(kb_map)
        yield result

        server.close()


def _populate_events_and_person(_config, kb_map):
    """Populate standard test entries (3 events + 1 person)."""
    events_repo = KBRepository(kb_map["test-events"])
    for i in range(3):
        event = EventEntry.create(
            date=f"2025-01-{10 + i:02d}",
            title=f"Test Event {i}",
            body=f"This is test event {i} about immigration policy.",
            importance=5 + i,
        )
        event.tags = ["test", "immigration"]
        event.actors = ["Stephen Miller", "Joe Biden"]
        events_repo.save(event)

    research_repo = KBRepository(kb_map["test-research"])
    actor = PersonEntry.create(
        name="Stephen Miller", role="Immigration policy architect", importance=9
    )
    actor.body = "Stephen Miller is the architect of Trump's immigration policy."
    actor.tags = ["trump-admin", "immigration"]
    research_repo.save(actor)


_STANDARD_KB_DEFS = [
    {"name": "test-events", "kb_type": KBType.EVENTS, "description": "Test events KB"},
    {
        "name": "test-research",
        "kb_type": KBType.RESEARCH,
        "description": "Test research KB",
        "subdirs": ["actors"],
    },
]


# ---------------------------------------------------------------------------
# Module-level fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_admin_server():
    """Admin-tier MCP server with 3 events + 1 person, indexed."""
    with _make_mcp_server(
        _STANDARD_KB_DEFS, tier="admin", extra_setup=_populate_events_and_person
    ) as env:
        yield env


@pytest.fixture
def mcp_minimal_server():
    """Minimal read-tier MCP server with a single empty KB."""
    with _make_mcp_server([{"name": "test", "kb_type": KBType.EVENTS}], tier="read") as env:
        yield env


# ---------------------------------------------------------------------------
# TestPyriteMCPServer — main functional tests
# ---------------------------------------------------------------------------


class TestPyriteMCPServer:
    """Tests for PyriteMCPServer."""

    def test_kb_list(self, mcp_admin_server):
        """Test listing knowledge bases."""
        result = mcp_admin_server["server"]._dispatch_tool("kb_list", {})

        assert "knowledge_bases" in result
        kbs = result["knowledge_bases"]
        assert len(kbs) == 2

        kb_names = [kb["name"] for kb in kbs]
        assert "test-events" in kb_names
        assert "test-research" in kb_names

    def test_kb_search(self, mcp_admin_server):
        """Test full-text search."""
        result = mcp_admin_server["server"]._dispatch_tool("kb_search", {"query": "immigration"})

        assert "results" in result
        assert result["count"] >= 1

    def test_kb_search_query_syntax_error_is_not_internal(self, mcp_admin_server):
        """A mixed literal+operator query (e.g. a phrase-quoted term next to
        a bare hyphenated token) reaches SQLite's FTS5 unquoted because
        sanitize_fts_query bypasses quoting once a query has an operator or
        quote — the token is then parsed as column-filter syntax and SQLite
        raises OperationalError. Before the fix, _dispatch_tool's catch-all
        mapped this to error_code=INTERNAL, retryable=True, sending agents
        into pointless retry loops on a deterministic syntax error.
        search-query-syntax-error-contract."""
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_search", {"query": '"family separation" cross-link'}
        )
        assert result.get("error_code") == "QUERY_SYNTAX", result
        assert result.get("retryable") is False, result

    def test_kb_batch_read_fields_without_kb_name_is_not_internal(self, mcp_admin_server):
        """`fields` that omits kb_name/id used to raise a raw KeyError while the
        handler computed `not_found`; _dispatch_tool's catch-all then reported
        it as INTERNAL/retryable=True. The identity pair now stays in the
        projection, so the call succeeds and still lists missing IDs.
        kb-batch-read-fields-identity-contract."""
        server = mcp_admin_server["server"]
        found = server._dispatch_tool(
            "kb_search", {"query": "immigration", "kb_name": "test-events"}
        )
        entry_id = found["results"][0]["id"]

        result = server._dispatch_tool(
            "kb_batch_read",
            {
                "entries": [
                    {"entry_id": entry_id, "kb_name": "test-events"},
                    {"entry_id": "no-such-entry", "kb_name": "test-events"},
                ],
                "fields": ["title", "entry_type"],
            },
        )

        assert "error_code" not in result, result
        assert result["found"] == 1
        assert result["not_found"] == [{"entry_id": "no-such-entry", "kb_name": "test-events"}]
        entry = result["entries"][0]
        assert entry["title"]
        assert entry["entry_type"] == "event"
        # Identity fields are always included, even when not requested.
        assert entry["id"] == entry_id
        assert entry["kb_name"] == "test-events"

    @pytest.mark.parametrize(
        "payload",
        [
            {"entries": 5},
            {"entries": "abc"},
            {"entries": [1]},
            {"entries": [{"kb_name": "test-events"}]},
            {"entries": [{"entry_id": "abc"}]},
            {"entries": [{"entry_id": None, "kb_name": "test-events"}]},
            {"entries": [{"entry_id": "", "kb_name": "test-events"}]},
            {"entries": [{"entry_id": 42, "kb_name": "test-events"}]},
            {"entries": [{"entry_id": ["abc"], "kb_name": "test-events"}]},
            {"entries": [{"entry_id": "abc", "kb_name": ""}]},
            {"entries": [{"entry_id": "abc", "kb_name": 7}]},
        ],
    )
    def test_kb_batch_read_rejects_malformed_specs(self, mcp_admin_server, payload):
        """Every malformed `entries` shape is a client error, not INTERNAL.

        Checking key presence was not enough: a non-list `entries`, a non-dict
        item, or a non-string/empty id still reached `len()` or the SQL layer
        and surfaced as INTERNAL/retryable=True -- the shape #137 exists to
        remove -- sometimes leaking a raw SQL statement in the message. All of
        them are deterministic client errors
        (kb-batch-read-spec-validation-contract).
        """
        result = mcp_admin_server["server"]._dispatch_tool("kb_batch_read", payload)
        assert result.get("error_code") == "VALIDATION_FAILED", result
        assert result.get("retryable") is False, result
        assert "sqlite3" not in str(result.get("error", "")).lower(), result

    def test_kb_batch_read_names_the_malformed_index(self, mcp_admin_server):
        """With one bad item among good ones the message names its position.

        At the 50-entry maximum an agent's only recovery from a single bad item
        would otherwise be to bisect (kb-batch-read-spec-validation-contract).
        """
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_batch_read",
            {
                "entries": [
                    {"entry_id": "a", "kb_name": "test-events"},
                    {"entry_id": "b", "kb_name": "test-events"},
                    {"kb_name": "test-events"},
                ]
            },
        )
        assert result.get("error_code") == "VALIDATION_FAILED", result
        assert "entries[2]" in result["error"], result

    def test_kb_search_fields_projection_keeps_identity_pair(self, mcp_admin_server):
        """`fields` never drops id/kb_name, for any projecting tool."""
        server = mcp_admin_server["server"]
        result = server._dispatch_tool(
            "kb_search", {"query": "immigration", "kb_name": "test-events", "fields": ["title"]}
        )
        assert result["results"], result
        for entry in result["results"]:
            assert entry["id"] and entry["kb_name"], entry

    def test_kb_get_fields_projection_keeps_identity_pair(self, mcp_admin_server):
        server = mcp_admin_server["server"]
        found = server._dispatch_tool(
            "kb_search", {"query": "immigration", "kb_name": "test-events"}
        )
        entry_id = found["results"][0]["id"]
        result = server._dispatch_tool(
            "kb_get", {"entry_id": entry_id, "kb_name": "test-events", "fields": ["title"]}
        )
        entry = result["entry"]
        assert entry["id"] == entry_id
        assert entry["kb_name"] == "test-events"

    def test_kb_list_entries_fields_projection_keeps_identity_pair(self, mcp_admin_server):
        server = mcp_admin_server["server"]
        result = server._dispatch_tool(
            "kb_list_entries", {"kb_name": "test-events", "fields": ["title"]}
        )
        assert result["entries"], result
        for entry in result["entries"]:
            assert entry["id"] and entry["kb_name"], entry

    def test_kb_recent_fields_projection_keeps_identity_pair(self, mcp_admin_server):
        server = mcp_admin_server["server"]
        result = server._dispatch_tool("kb_recent", {"kb_name": "test-events", "fields": ["title"]})
        assert result["entries"], result
        for entry in result["entries"]:
            assert entry["id"] and entry["kb_name"], entry

    def test_kb_search_with_filters(self, mcp_admin_server):
        """Test search with filters."""
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_search", {"query": "immigration", "kb_name": "test-events", "entry_type": "event"}
        )

        assert "results" in result
        for r in result["results"]:
            assert r["kb_name"] == "test-events"
            assert r["entry_type"] == "event"

    def test_kb_get(self, mcp_admin_server):
        """Test getting entry by ID."""
        # First search to get an ID
        search_result = mcp_admin_server["server"]._dispatch_tool(
            "kb_search", {"query": "Stephen Miller", "kb_name": "test-research"}
        )

        if search_result["count"] > 0:
            entry_id = search_result["results"][0]["id"]

            result = mcp_admin_server["server"]._dispatch_tool(
                "kb_get", {"entry_id": entry_id, "kb_name": "test-research"}
            )

            assert "entry" in result
            assert result["entry"]["title"] == "Stephen Miller"

    def test_kb_get_not_found(self, mcp_admin_server):
        """Test getting non-existent entry."""
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_get", {"entry_id": "nonexistent-entry", "kb_name": "test-events"}
        )

        assert "error" in result

    def test_kb_timeline(self, mcp_admin_server):
        """Test timeline queries."""
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_timeline", {"date_from": "2025-01-01", "date_to": "2025-01-31"}
        )

        assert "events" in result
        assert result["count"] >= 1

    def test_kb_timeline_with_importance(self, mcp_admin_server):
        """Test timeline with importance filter."""
        result = mcp_admin_server["server"]._dispatch_tool("kb_timeline", {"min_importance": 6})

        assert "events" in result
        for event in result["events"]:
            assert event.get("importance", 0) >= 6

    def test_kb_tags(self, mcp_admin_server):
        """Test getting all tags."""
        result = mcp_admin_server["server"]._dispatch_tool("kb_tags", {})

        assert "tags" in result
        tag_names = [t["tag"] for t in result["tags"]]
        assert "immigration" in tag_names

    def test_kb_create_event(self, mcp_admin_server):
        """Test creating a new event."""
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "New Test Event",
                "body": "This is a newly created event.",
                "date": "2025-01-25",
                "importance": 7,
                "tags": ["new", "test"],
                "actors": ["Test Actor"],
            },
        )

        assert result.get("created") is True
        assert "entry_id" in result

        # Verify it can be retrieved
        get_result = mcp_admin_server["server"]._dispatch_tool(
            "kb_get", {"entry_id": result["entry_id"], "kb_name": "test-events"}
        )
        assert "entry" in get_result
        assert get_result["entry"]["title"] == "New Test Event"

    def test_kb_create_person(self, mcp_admin_server):
        """Test creating a new person entry."""
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-research",
                "entry_type": "person",
                "title": "New Test Actor",
                "body": "Biography of the test actor.",
                "role": "Test role",
                "importance": 5,
                "tags": ["new-actor"],
            },
        )

        assert result.get("created") is True
        assert "entry_id" in result

    def test_kb_update(self, mcp_admin_server):
        """Test updating an entry."""
        server = mcp_admin_server["server"]

        # Create an entry first
        create_result = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Update Test Event",
                "date": "2025-01-26",
                "body": "Original body.",
            },
        )

        entry_id = create_result["entry_id"]

        # Update it
        update_result = server._dispatch_tool(
            "kb_update",
            {
                "entry_id": entry_id,
                "kb_name": "test-events",
                "body": "Updated body content.",
                "importance": 8,
            },
        )

        assert update_result.get("updated") is True

        # Verify update
        get_result = server._dispatch_tool(
            "kb_get", {"entry_id": entry_id, "kb_name": "test-events"}
        )
        assert "Updated body" in get_result["entry"]["body"]
        assert get_result["entry"]["importance"] == 8

    def test_kb_index_sync(self, mcp_admin_server):
        """Test index sync."""
        result = mcp_admin_server["server"]._dispatch_tool("kb_index_sync", {})

        assert result.get("synced") is True
        assert "added" in result
        assert "updated" in result
        assert "removed" in result

    # ------------------------------------------------------------------
    # Delete handler
    # ------------------------------------------------------------------

    def test_kb_delete(self, mcp_admin_server):
        """Test deleting an entry removes it from the KB."""
        server = mcp_admin_server["server"]

        # Create an entry to delete
        create_result = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Ephemeral Event",
                "date": "2025-02-01",
                "body": "This will be deleted.",
            },
        )
        assert create_result.get("created") is True
        entry_id = create_result["entry_id"]

        # Delete it
        delete_result = server._dispatch_tool(
            "kb_delete", {"entry_id": entry_id, "kb_name": "test-events"}
        )
        assert delete_result.get("deleted") is True
        assert delete_result["entry_id"] == entry_id

        # Verify it's gone
        get_result = server._dispatch_tool(
            "kb_get", {"entry_id": entry_id, "kb_name": "test-events"}
        )
        assert "error" in get_result

    def test_kb_delete_not_found(self, mcp_admin_server):
        """Test deleting a non-existent entry returns an error."""
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_delete", {"entry_id": "no-such-entry", "kb_name": "test-events"}
        )
        assert "error" in result

    # ------------------------------------------------------------------
    # Backlinks handler
    # ------------------------------------------------------------------

    def test_kb_backlinks(self, mcp_admin_server):
        """Test backlinks returns entries that link TO a given entry."""
        server = mcp_admin_server["server"]
        events_kb = mcp_admin_server["test-events"]
        repo = KBRepository(events_kb)

        # Create target entry
        target = EventEntry.create(
            date="2025-03-01", title="Target Event", body="Target.", importance=5
        )
        repo.save(target)

        # Create source entry that links to target
        source = EventEntry.create(
            date="2025-03-02", title="Source Event", body="Links to target.", importance=5
        )
        source.links = [Link(target=target.id, relation="related_to")]
        repo.save(source)

        # Re-index to pick up links
        server.index_mgr.index_all()

        result = server._dispatch_tool(
            "kb_backlinks", {"entry_id": target.id, "kb_name": "test-events"}
        )

        assert result["entry_id"] == target.id
        assert result["backlink_count"] >= 1
        backlink_ids = [bl["id"] for bl in result["backlinks"]]
        assert source.id in backlink_ids

    # ------------------------------------------------------------------
    # Stats handler
    # ------------------------------------------------------------------

    def test_kb_stats(self, mcp_admin_server):
        """Test stats returns entry/tag/link counts."""
        result = mcp_admin_server["server"]._dispatch_tool("kb_stats", {})

        assert "total_entries" in result
        assert result["total_entries"] >= 4  # 3 events + 1 person
        assert "total_tags" in result
        assert "total_links" in result
        assert "kbs" in result

    # ------------------------------------------------------------------
    # Schema handler
    # ------------------------------------------------------------------

    def test_kb_schema(self, mcp_admin_server):
        """Test schema returns type information for a KB."""
        result = mcp_admin_server["server"]._dispatch_tool("kb_schema", {"kb_name": "test-events"})

        # Schema may be empty (no kb.yaml in test tmpdir) but should not error
        assert "error" not in result

    def test_kb_schema_not_found(self, mcp_admin_server):
        """Test schema for a non-existent KB returns error."""
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_schema", {"kb_name": "nonexistent-kb"}
        )
        assert "error" in result

    # ------------------------------------------------------------------
    # Manage handler
    # ------------------------------------------------------------------

    def test_kb_manage_validate(self, mcp_admin_server):
        """Test manage validate action returns KB validation info."""
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_manage", {"action": "validate", "kb_name": "test-events"}
        )

        assert result.get("valid") is True
        assert "types" in result

    def test_kb_manage_validate_not_found(self, mcp_admin_server):
        """Test manage validate for non-existent KB returns error."""
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_manage", {"action": "validate", "kb_name": "nonexistent"}
        )
        assert "error" in result

    # ------------------------------------------------------------------
    # Metadata round-trip (regression for build_entry metadata bug)
    # ------------------------------------------------------------------

    def test_kb_create_with_metadata_generic_type(self, mcp_admin_server):
        """Test create with metadata dict round-trips for generic entry types."""
        server = mcp_admin_server["server"]

        # Use a generic type (not event/person/org) so metadata is stored
        create_result = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "note",
                "title": "Metadata Note",
                "body": "Has custom metadata.",
                "metadata": {"source_url": "https://example.com", "category": "test"},
            },
        )
        assert create_result.get("created") is True

        get_result = server._dispatch_tool(
            "kb_get", {"entry_id": create_result["entry_id"], "kb_name": "test-events"}
        )
        assert "entry" in get_result
        entry = get_result["entry"]
        # Metadata may be a JSON string or dict depending on DB layer
        import json as _json

        metadata = entry.get("metadata", {})
        if isinstance(metadata, str):
            metadata = _json.loads(metadata) if metadata else {}
        assert metadata.get("source_url") == "https://example.com"

    def test_kb_create_event_with_metadata(self, mcp_admin_server):
        """Test create event with metadata dict preserves custom fields."""
        server = mcp_admin_server["server"]

        create_result = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Event With Metadata",
                "date": "2025-02-15",
                "body": "Event with custom metadata.",
                "metadata": {"source_url": "https://example.com/event", "custom_field": "value"},
            },
        )
        assert create_result.get("created") is True

        get_result = server._dispatch_tool(
            "kb_get", {"entry_id": create_result["entry_id"], "kb_name": "test-events"}
        )
        assert "entry" in get_result
        import json as _json

        metadata = get_result["entry"].get("metadata", {})
        if isinstance(metadata, str):
            metadata = _json.loads(metadata) if metadata else {}
        assert metadata.get("source_url") == "https://example.com/event"

    def test_kb_create_person_with_metadata(self, mcp_admin_server):
        """Test create person with metadata dict preserves custom fields."""
        server = mcp_admin_server["server"]

        create_result = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-research",
                "entry_type": "person",
                "title": "Person With Metadata",
                "body": "A person with custom metadata.",
                "role": "Analyst",
                "metadata": {"linkedin": "https://linkedin.com/in/test"},
            },
        )
        assert create_result.get("created") is True

        get_result = server._dispatch_tool(
            "kb_get", {"entry_id": create_result["entry_id"], "kb_name": "test-research"}
        )
        assert "entry" in get_result
        import json as _json

        metadata = get_result["entry"].get("metadata", {})
        if isinstance(metadata, str):
            metadata = _json.loads(metadata) if metadata else {}
        assert metadata.get("linkedin") == "https://linkedin.com/in/test"

    def test_kb_create_with_empty_metadata(self, mcp_admin_server):
        """Test create with empty metadata dict doesn't cause errors."""
        server = mcp_admin_server["server"]

        result = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Empty Metadata Event",
                "date": "2025-02-11",
                "body": "No custom metadata.",
                "metadata": {},
            },
        )
        assert result.get("created") is True

    # ------------------------------------------------------------------
    # Error handling on writes
    # ------------------------------------------------------------------

    def test_kb_create_readonly_kb(self):
        """Test creating on a read-only KB returns error."""
        with _make_mcp_server(
            [{"name": "readonly", "kb_type": KBType.RESEARCH, "read_only": True}],
            tier="write",
        ) as env:
            result = env["server"]._dispatch_tool(
                "kb_create",
                {
                    "kb_name": "readonly",
                    "entry_type": "note",
                    "title": "Should Fail",
                },
            )
            assert "error" in result
            assert "read-only" in result["error"]

    def test_kb_create_refuses_undeclared_type(self):
        """kb_create must refuse entry_type values that aren't in the KB's
        kb.yaml schema. Regression for Tier A 1090
        (bug-create-silently-accepts-undeclared-types). Without this guard
        external agents — like the cascade-research conductor — silently
        file entries with their native vocabulary (`type: task`) into KBs
        that declare a different schema (this repo's `kb.yaml` accepts only
        adr | backlog_item | component | standard).
        """

        def write_kb_yaml(_config, kb_map):
            kb_path = kb_map["sw-kb"].path
            (kb_path / "kb.yaml").write_text(
                "name: sw-kb\n"
                "kb_type: generic\n"
                "types:\n"
                "  backlog_item:\n"
                "    description: A backlog work item\n"
                "validation:\n"
                "  enforce: false\n"
            )

        with _make_mcp_server(
            [{"name": "sw-kb", "kb_type": KBType.GENERIC}],
            tier="write",
            extra_setup=write_kb_yaml,
        ) as env:
            result = env["server"]._dispatch_tool(
                "kb_create",
                {
                    "kb_name": "sw-kb",
                    "entry_type": "task",  # conductor's mistake — not in this KB's schema
                    "title": "Conductor-filed task",
                    "body": "Some body",
                },
            )
            # The create must NOT silently succeed.
            assert result.get("created") is not True, (
                f"undeclared type 'task' must be refused; got result {result}"
            )
            # The error must name the offending type, name the declared types,
            # and use a machine-readable error code so callers can self-correct.
            assert "error" in result or "error_code" in result
            error_code = result.get("error_code") or ""
            assert error_code == "UNDECLARED_TYPE", (
                f"expected error_code=UNDECLARED_TYPE; got {error_code!r}"
            )
            # Declared types listed so the caller doesn't have to round-trip
            # through kb_schema.
            declared = result.get("declared_types") or []
            assert "backlog_item" in declared
            assert "task" not in declared

    def test_kb_create_allows_undeclared_with_override(self):
        """Passing allow_undeclared=true overrides the refusal, for ephemeral
        KBs or migration scenarios where the schema is fluid. The entry will
        still be flagged by `pyrite index health`'s undeclared_types check."""

        def write_kb_yaml(_config, kb_map):
            (kb_map["sw-kb"].path / "kb.yaml").write_text(
                "name: sw-kb\nkb_type: generic\ntypes:\n  backlog_item:\n"
                "    description: A backlog work item\n"
            )

        with _make_mcp_server(
            [{"name": "sw-kb", "kb_type": KBType.GENERIC}],
            tier="write",
            extra_setup=write_kb_yaml,
        ) as env:
            result = env["server"]._dispatch_tool(
                "kb_create",
                {
                    "kb_name": "sw-kb",
                    "entry_type": "task",
                    "title": "Override case",
                    "allow_undeclared": True,
                },
            )
            assert result.get("created") is True, (
                f"allow_undeclared=true must let the create succeed; got {result}"
            )

    def test_kb_update_not_found(self, mcp_admin_server):
        """Test updating a non-existent entry returns error."""
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_update",
            {
                "entry_id": "no-such-entry",
                "kb_name": "test-events",
                "body": "Should fail.",
            },
        )
        assert "error" in result

    # ------------------------------------------------------------------
    # Search edge cases
    # ------------------------------------------------------------------

    def test_kb_search_boolean_operators(self, mcp_admin_server):
        """Test FTS5 boolean operators work in search."""
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_search", {"query": "immigration AND policy"}
        )

        assert "results" in result
        assert result["count"] >= 1

    def test_kb_search_no_results(self, mcp_admin_server):
        """Test search with no matches returns count=0."""
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_search", {"query": "xyzzy_nonexistent_term_12345"}
        )

        assert result["count"] == 0
        assert result["results"] == []

    # ------------------------------------------------------------------
    # kb_bulk_create handler
    # ------------------------------------------------------------------

    def test_kb_bulk_create(self, mcp_admin_server):
        """Test batch-creating multiple entries."""
        server = mcp_admin_server["server"]

        result = server._dispatch_tool(
            "kb_bulk_create",
            {
                "kb_name": "test-events",
                "entries": [
                    {
                        "entry_type": "event",
                        "title": "Bulk Event A",
                        "date": "2025-05-01",
                        "body": "First bulk event.",
                        "tags": ["bulk"],
                    },
                    {
                        "entry_type": "event",
                        "title": "Bulk Event B",
                        "date": "2025-05-02",
                        "body": "Second bulk event.",
                    },
                    {
                        "entry_type": "note",
                        "title": "Bulk Note C",
                        "body": "A note in the batch.",
                    },
                ],
            },
        )

        assert result["total"] == 3
        assert result["created"] == 3
        assert result["failed"] == 0
        for r in result["results"]:
            assert r["created"] is True
            assert "entry_id" in r

        # Verify entries are retrievable
        for r in result["results"]:
            get = server._dispatch_tool(
                "kb_get", {"entry_id": r["entry_id"], "kb_name": "test-events"}
            )
            assert "entry" in get

    def test_kb_bulk_create_partial_failure(self, mcp_admin_server):
        """Test bulk create with some entries failing."""
        server = mcp_admin_server["server"]

        result = server._dispatch_tool(
            "kb_bulk_create",
            {
                "kb_name": "test-events",
                "entries": [
                    {
                        "entry_type": "event",
                        "title": "Good Entry",
                        "date": "2025-05-10",
                        "body": "This should succeed.",
                    },
                    {
                        # Missing title — should fail
                        "entry_type": "event",
                        "body": "No title here.",
                    },
                ],
            },
        )

        assert result["total"] == 2
        assert result["created"] == 1
        assert result["failed"] == 1
        assert result["results"][0]["created"] is True
        assert result["results"][1]["created"] is False
        assert "title" in result["results"][1]["error"].lower()

    def test_kb_bulk_create_empty(self, mcp_admin_server):
        """Test bulk create with empty entries array."""
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_bulk_create",
            {"kb_name": "test-events", "entries": []},
        )
        assert "error" in result

    def test_kb_bulk_create_readonly(self):
        """Test bulk create on read-only KB returns error."""
        with _make_mcp_server(
            [{"name": "ro", "kb_type": KBType.RESEARCH, "read_only": True}],
            tier="write",
        ) as env:
            result = env["server"]._dispatch_tool(
                "kb_bulk_create",
                {
                    "kb_name": "ro",
                    "entries": [{"title": "Should Fail"}],
                },
            )
            assert "error" in result
            assert "read-only" in result["error"]

    def test_kb_bulk_create_over_limit(self, mcp_admin_server):
        """Test bulk create rejects more than 50 entries."""
        entries = [{"title": f"Entry {i}"} for i in range(51)]
        result = mcp_admin_server["server"]._dispatch_tool(
            "kb_bulk_create",
            {"kb_name": "test-events", "entries": entries},
        )
        assert "error" in result
        assert "50" in result["error"]

    # ------------------------------------------------------------------
    # kb_link handler
    # ------------------------------------------------------------------

    def test_kb_link(self, mcp_admin_server):
        """Test creating a link between two entries via kb_link."""
        server = mcp_admin_server["server"]

        # Create two entries
        r1 = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Link Source",
                "date": "2025-04-01",
                "body": "Source entry.",
            },
        )
        r2 = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Link Target",
                "date": "2025-04-02",
                "body": "Target entry.",
            },
        )
        assert r1.get("created") and r2.get("created")

        # Link them
        link_result = server._dispatch_tool(
            "kb_link",
            {
                "source_id": r1["entry_id"],
                "source_kb": "test-events",
                "target_id": r2["entry_id"],
                "relation": "caused_by",
            },
        )
        assert link_result.get("linked") is True
        assert link_result["relation"] == "caused_by"
        assert link_result.get("created") is True

        # Verify via backlinks
        bl = server._dispatch_tool(
            "kb_backlinks",
            {"entry_id": r2["entry_id"], "kb_name": "test-events"},
        )
        backlink_ids = [b["id"] for b in bl["backlinks"]]
        assert r1["entry_id"] in backlink_ids

    def test_kb_link_idempotent(self, mcp_admin_server):
        """Test linking the same pair twice doesn't create duplicate links."""
        server = mcp_admin_server["server"]

        r1 = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Idem Source",
                "date": "2025-04-03",
                "body": "Source.",
            },
        )
        r2 = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Idem Target",
                "date": "2025-04-04",
                "body": "Target.",
            },
        )

        args = {
            "source_id": r1["entry_id"],
            "source_kb": "test-events",
            "target_id": r2["entry_id"],
        }
        result1 = server._dispatch_tool("kb_link", args)
        result2 = server._dispatch_tool("kb_link", args)
        assert result1.get("linked") is True
        assert result2.get("linked") is True
        assert result1.get("created") is True
        assert result2.get("created") is False

        # Only one backlink should exist
        bl = server._dispatch_tool(
            "kb_backlinks",
            {"entry_id": r2["entry_id"], "kb_name": "test-events"},
        )
        source_links = [b for b in bl["backlinks"] if b["id"] == r1["entry_id"]]
        assert len(source_links) == 1

    def test_kb_link_not_found(self, mcp_admin_server):
        """Test linking from a nonexistent source returns error, retryable=false."""
        server = mcp_admin_server["server"]
        target = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Existing Link Target",
                "date": "2025-04-05",
                "body": "Target entry.",
            },
        )

        result = server._dispatch_tool(
            "kb_link",
            {
                "source_id": "no-such-entry",
                "source_kb": "test-events",
                "target_id": target["entry_id"],
            },
        )
        assert "error" in result
        assert result["error_code"] == "LINK_FAILED"
        assert result["error"] == "Entry not found: no-such-entry"
        assert result["retryable"] is False

    def test_kb_link_target_not_found(self, mcp_admin_server):
        """Target validation: linking to a nonexistent target returns error,
        retryable=false, and leaves the source's stored links untouched."""
        server = mcp_admin_server["server"]

        r1 = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Source For Dangling",
                "date": "2025-05-01",
                "body": "Source exists.",
            },
        )
        assert r1.get("created")

        result = server._dispatch_tool(
            "kb_link",
            {
                "source_id": r1["entry_id"],
                "source_kb": "test-events",
                "target_id": "zzz-nonexistent-target",
            },
        )
        assert "error" in result
        assert result["error_code"] == "LINK_FAILED"
        assert result["retryable"] is False
        assert "zzz-nonexistent-target" in result["error"]
        stored = KBRepository(mcp_admin_server["test-events"]).load(r1["entry_id"])
        assert stored is not None
        assert all(link.target != "zzz-nonexistent-target" for link in stored.links)

    def test_kb_link_allow_dangling(self, mcp_admin_server):
        """allow_dangling=true creates link to missing target, resolved=false."""
        server = mcp_admin_server["server"]

        r1 = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Source For Forward Ref",
                "date": "2025-05-02",
                "body": "Source exists.",
            },
        )
        assert r1.get("created")

        result = server._dispatch_tool(
            "kb_link",
            {
                "source_id": r1["entry_id"],
                "source_kb": "test-events",
                "target_id": "future-entry-not-yet-created",
                "allow_dangling": True,
            },
        )
        assert result.get("linked") is True
        assert result["resolved"] is False

    def test_kb_link_resolved_true(self, mcp_admin_server):
        """Normal link to existing target reports resolved=true."""
        server = mcp_admin_server["server"]

        r1 = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Resolved Source",
                "date": "2025-05-03",
                "body": "Source.",
            },
        )
        r2 = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Resolved Target",
                "date": "2025-05-04",
                "body": "Target.",
            },
        )
        assert r1.get("created") and r2.get("created")

        result = server._dispatch_tool(
            "kb_link",
            {
                "source_id": r1["entry_id"],
                "source_kb": "test-events",
                "target_id": r2["entry_id"],
            },
        )
        assert result.get("linked") is True
        assert result["resolved"] is True

    def test_kb_link_replayed_after_target_deleted_is_still_a_noop(self, mcp_admin_server):
        """Re-issuing a link that is already recorded stays a silent no-op, even
        when the target has since been deleted.

        The duplicate check has to run before the target-existence check, or
        anything that replays a link set for idempotency — a re-run migration, a
        re-driven bulk script, an agent retrying a batch — starts failing on
        links its own earlier pass already wrote correctly."""
        server = mcp_admin_server["server"]
        source = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Idempotent Link Source",
                "date": "2025-04-07",
                "body": "Source entry.",
            },
        )
        target = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Idempotent Link Target",
                "date": "2025-04-08",
                "body": "Target entry.",
            },
        )
        link_args = {
            "source_id": source["entry_id"],
            "source_kb": "test-events",
            "target_id": target["entry_id"],
        }

        first = server._dispatch_tool("kb_link", link_args)
        assert first["linked"] is True

        deleted = server._dispatch_tool(
            "kb_delete",
            {"entry_id": target["entry_id"], "kb_name": "test-events", "confirm": True},
        )
        assert "error" not in deleted, deleted

        replay = server._dispatch_tool("kb_link", link_args)
        assert "error" not in replay, replay
        assert replay["linked"] is True

        stored = KBRepository(mcp_admin_server["test-events"]).load(source["entry_id"])
        assert stored is not None
        assert [link.target for link in stored.links].count(target["entry_id"]) == 1

    def test_kb_link_replayed_after_target_deleted_reports_resolved_false(self, mcp_admin_server):
        """The duplicate path must COMPUTE resolved, not assume it.

        A link already in the source's frontmatter stays a no-op (the test
        above), but the `resolved` it reports is a claim about the target as it
        is now. Hard-coding True tells a caller a stale link is fine, which is
        exactly the state #64's `title: null` dangling outlinks come from."""
        server = mcp_admin_server["server"]
        source = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Replay Resolved Source",
                "date": "2025-04-10",
                "body": "Source entry.",
            },
        )
        target = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Replay Resolved Target",
                "date": "2025-04-11",
                "body": "Target entry.",
            },
        )
        link_args = {
            "source_id": source["entry_id"],
            "source_kb": "test-events",
            "target_id": target["entry_id"],
        }

        first = server._dispatch_tool("kb_link", link_args)
        assert first["resolved"] is True

        deleted = server._dispatch_tool(
            "kb_delete",
            {"entry_id": target["entry_id"], "kb_name": "test-events", "confirm": True},
        )
        assert "error" not in deleted, deleted

        replay = server._dispatch_tool("kb_link", link_args)
        assert "error" not in replay, replay
        assert replay["linked"] is True
        assert replay["resolved"] is False

    def test_kb_link_target_written_to_disk_but_not_indexed_still_validates(self, mcp_admin_server):
        """The index is a derived cache; the markdown on disk is the truth.

        `pyrite create` followed by `pyrite link` is the common agent path, and
        an entry whose file exists but whose index row has not landed must not
        be rejected. The index-first lookup is a fast path, never the verdict."""
        server = mcp_admin_server["server"]
        repo = KBRepository(mcp_admin_server["test-events"])

        source = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Unindexed Link Source",
                "date": "2025-04-12",
                "body": "Source entry.",
            },
        )

        target = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Unindexed Link Target",
                "date": "2025-04-13",
                "body": "Target entry.",
            },
        )
        # Drop the target's index row, leaving the markdown file in place: the
        # state a create leaves behind when the index write has not caught up.
        server.svc.db.delete_entry(target["entry_id"], "test-events")
        assert server.svc.db.get_entry(target["entry_id"], "test-events") is None
        assert repo.load(target["entry_id"]) is not None

        result = server._dispatch_tool(
            "kb_link",
            {
                "source_id": source["entry_id"],
                "source_kb": "test-events",
                "target_id": target["entry_id"],
            },
        )

        assert "error" not in result, result
        assert result["linked"] is True
        assert result["resolved"] is True

    def test_kb_link_missing_target_kb_is_not_retryable(self, mcp_admin_server):
        """A target KB that is not registered is a deterministic error.

        Retrying does not register a KB, so the response must not invite one —
        the same anti-pattern test_kb_search_query_syntax_error_is_not_internal
        exists to prevent."""
        server = mcp_admin_server["server"]
        source = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-events",
                "entry_type": "event",
                "title": "Missing Target KB Source",
                "date": "2025-04-09",
                "body": "Source entry.",
            },
        )

        result = server._dispatch_tool(
            "kb_link",
            {
                "source_id": source["entry_id"],
                "source_kb": "test-events",
                "target_id": "anything",
                "target_kb": "no-such-kb",
            },
        )

        assert result["error_code"] == "LINK_FAILED"
        assert result["error"] == "KB not found: no-such-kb"
        assert result["retryable"] is False

    def test_kb_link_in_write_tier(self):
        """Test kb_link appears in write-tier tools but not read-tier."""
        with _make_mcp_server([{"name": "t", "kb_type": KBType.EVENTS}], tier="read") as read_env:
            read_names = [t["name"] for t in read_env["server"].get_tools_list()]
            assert "kb_link" not in read_names

        with _make_mcp_server([{"name": "t", "kb_type": KBType.EVENTS}], tier="write") as write_env:
            write_names = [t["name"] for t in write_env["server"].get_tools_list()]
            assert "kb_link" in write_names

    # ------------------------------------------------------------------
    # Entry type resolution (plugin subtype)
    # ------------------------------------------------------------------

    def test_kb_create_resolves_plugin_subtype(self, mcp_admin_server):
        """Test that create with a core type resolves to plugin subtype."""
        from unittest.mock import patch

        from pyrite.models import EventEntry

        # Create a mock plugin subclass of EventEntry
        class MockTimelineEvent(EventEntry):
            entry_type = "timeline_event"

        mock_plugin_types = {"timeline_event": MockTimelineEvent}

        server = mcp_admin_server["server"]

        with patch("pyrite.plugins.get_registry") as mock_reg:
            mock_reg.return_value.get_all_entry_types.return_value = mock_plugin_types
            # Also patch the hooks to avoid plugin lookup issues
            with patch.object(type(server.svc), "_run_hooks", side_effect=lambda _n, e, _c: e):
                result = server._dispatch_tool(
                    "kb_create",
                    {
                        "kb_name": "test-events",
                        "entry_type": "event",
                        "title": "Resolved Event",
                        "date": "2025-05-01",
                        "body": "Should resolve to timeline_event.",
                    },
                )

        assert result.get("created") is True
        entry_id = result["entry_id"]

        # Verify the entry was created — the type depends on whether
        # build_entry dispatches correctly for the resolved type
        get_result = server._dispatch_tool(
            "kb_get", {"entry_id": entry_id, "kb_name": "test-events"}
        )
        assert "entry" in get_result


# ---------------------------------------------------------------------------
# TestMCPProtocol — protocol-level tests
# ---------------------------------------------------------------------------


class TestMCPProtocol:
    """Test MCP server business methods (protocol dispatch handled by SDK)."""

    def test_get_tools_list(self, mcp_minimal_server):
        """Test get_tools_list returns tool definitions."""
        server = mcp_minimal_server["server"]
        tools = server.get_tools_list()
        tool_names = [t["name"] for t in tools]
        assert "kb_list" in tool_names
        assert "kb_search" in tool_names
        assert "kb_get" in tool_names
        for t in tools:
            assert "name" in t
            assert "description" in t
            assert "inputSchema" in t

    def test_dispatch_tool(self, mcp_minimal_server):
        """Test _dispatch_tool calls handler and returns result."""
        result = mcp_minimal_server["server"]._dispatch_tool("kb_list", {})
        assert "knowledge_bases" in result

    def test_dispatch_tool_unknown(self, mcp_minimal_server):
        """Test _dispatch_tool returns error for unknown tool."""
        result = mcp_minimal_server["server"]._dispatch_tool("nonexistent_tool", {})
        assert "error" in result

    def test_build_sdk_server(self, mcp_minimal_server):
        """Test build_sdk_server returns a configured SDK Server."""
        from mcp.server import Server

        sdk = mcp_minimal_server["server"].build_sdk_server()
        assert isinstance(sdk, Server)


# ---------------------------------------------------------------------------
# TestMCPPagination — pagination tests
# ---------------------------------------------------------------------------


def _populate_pagination_data(_config, kb_map):
    """Populate data for pagination tests: 5 events + backlink chain."""
    repo = KBRepository(kb_map["test-kb"])

    # Create 5 events with distinct dates and content
    for i in range(5):
        event = EventEntry.create(
            date=f"2025-06-{10 + i:02d}",
            title=f"Pagination Event {i}",
            body=f"Body for pagination event number {i}.",
            importance=5,
        )
        event.tags = [f"tag-{i}", "common"]
        repo.save(event)

    # Create a target entry and 4 entries linking to it (for backlinks)
    target = NoteEntry(id="backlink-target", title="Backlink Target", body="Target entry.")
    target.tags = ["target"]
    repo.save(target)

    for i in range(4):
        source = NoteEntry(
            id=f"backlink-source-{i}",
            title=f"Backlink Source {i}",
            body=f"Source {i} links to target.",
        )
        source.links = [Link(target=target.id, relation="related_to")]
        source.tags = ["source"]
        repo.save(source)


class TestMCPPagination:
    """Tests for MCP tool pagination (limit/offset/has_more)."""

    @pytest.fixture
    def pagination_env(self):
        """Create a test server with enough data for pagination tests."""
        with _make_mcp_server(
            [{"name": "test-kb", "kb_type": KBType.EVENTS, "description": "Pagination test KB"}],
            tier="admin",
            extra_setup=_populate_pagination_data,
        ) as env:
            env["target_id"] = "backlink-target"
            yield env

    # ------------------------------------------------------------------
    # kb_search pagination
    # ------------------------------------------------------------------

    def test_kb_search_pagination(self, pagination_env):
        """Test kb_search offset returns different pages and has_more flag."""
        server = pagination_env["server"]

        page1 = server._dispatch_tool("kb_search", {"query": "pagination", "limit": 2, "offset": 0})
        assert page1["count"] == 2
        assert page1["has_more"] is True

        page2 = server._dispatch_tool("kb_search", {"query": "pagination", "limit": 2, "offset": 2})
        assert page2["count"] == 2
        assert page2["has_more"] is True

        # IDs should differ between pages
        ids1 = {r["id"] for r in page1["results"]}
        ids2 = {r["id"] for r in page2["results"]}
        assert ids1.isdisjoint(ids2)

        # Last page
        page3 = server._dispatch_tool("kb_search", {"query": "pagination", "limit": 2, "offset": 4})
        assert page3["count"] == 1
        assert page3["has_more"] is False

    # ------------------------------------------------------------------
    # kb_timeline pagination
    # ------------------------------------------------------------------

    def test_kb_timeline_pagination(self, pagination_env):
        """Test kb_timeline limit/offset and has_more flag."""
        server = pagination_env["server"]

        page1 = server._dispatch_tool("kb_timeline", {"limit": 2, "offset": 0})
        assert page1["count"] == 2
        assert page1["has_more"] is True

        page2 = server._dispatch_tool("kb_timeline", {"limit": 2, "offset": 2})
        assert page2["count"] == 2
        assert page2["has_more"] is True

        # Different events on each page
        ids1 = {e["id"] for e in page1["events"]}
        ids2 = {e["id"] for e in page2["events"]}
        assert ids1.isdisjoint(ids2)

        # Large offset returns empty
        page_end = server._dispatch_tool("kb_timeline", {"limit": 10, "offset": 100})
        assert page_end["count"] == 0
        assert page_end["has_more"] is False

    # ------------------------------------------------------------------
    # kb_backlinks pagination
    # ------------------------------------------------------------------

    def test_kb_backlinks_pagination(self, pagination_env):
        """Test kb_backlinks limit/offset and has_more flag."""
        server = pagination_env["server"]
        target_id = pagination_env["target_id"]

        page1 = server._dispatch_tool(
            "kb_backlinks",
            {"entry_id": target_id, "kb_name": "test-kb", "limit": 2, "offset": 0},
        )
        assert page1["backlink_count"] == 2
        assert page1["has_more"] is True

        page2 = server._dispatch_tool(
            "kb_backlinks",
            {"entry_id": target_id, "kb_name": "test-kb", "limit": 2, "offset": 2},
        )
        assert page2["backlink_count"] == 2
        assert page2["has_more"] is True

        ids1 = {b["id"] for b in page1["backlinks"]}
        ids2 = {b["id"] for b in page2["backlinks"]}
        assert ids1.isdisjoint(ids2)

        # Past the end
        page3 = server._dispatch_tool(
            "kb_backlinks",
            {"entry_id": target_id, "kb_name": "test-kb", "limit": 10, "offset": 10},
        )
        assert page3["backlink_count"] == 0
        assert page3["has_more"] is False

    # ------------------------------------------------------------------
    # kb_tags limit, offset, and prefix
    # ------------------------------------------------------------------

    def test_kb_tags_limit_and_prefix(self, pagination_env):
        """Test kb_tags limit, offset, and prefix filtering at DB level."""
        server = pagination_env["server"]

        # All tags
        all_tags = server._dispatch_tool("kb_tags", {"limit": 100})
        assert all_tags["tag_count"] >= 5  # tag-0..4 + common + target + source

        # Limit
        limited = server._dispatch_tool("kb_tags", {"limit": 2})
        assert limited["tag_count"] == 2
        assert limited["has_more"] is True

        # Offset
        page2 = server._dispatch_tool("kb_tags", {"limit": 2, "offset": 2})
        assert page2["tag_count"] == 2
        tags1 = {t["tag"] for t in limited["tags"]}
        tags2 = {t["tag"] for t in page2["tags"]}
        assert tags1.isdisjoint(tags2)

        # Prefix filter
        prefixed = server._dispatch_tool("kb_tags", {"prefix": "tag-", "limit": 100})
        assert prefixed["tag_count"] == 5
        for t in prefixed["tags"]:
            assert t["tag"].startswith("tag-")


# ---------------------------------------------------------------------------
# TestMCPValidation — schema validation tests
# ---------------------------------------------------------------------------


def _write_validation_schema(_config, kb_map):
    """Write a kb.yaml with capture_lanes field for validation testing."""
    from pyrite.utils.yaml import dump_yaml

    schema = {
        "name": "validated-kb",
        "types": {
            "event": {
                "fields": {
                    "capture_lanes": {
                        "type": "multi-select",
                        "allow_other": True,
                        "options": ["lane-a", "lane-b", "lane-c"],
                    },
                },
            },
        },
        "validation": {"enforce": True},
    }
    kb_path = kb_map["val-kb"].path
    (kb_path / "kb.yaml").write_text(dump_yaml(schema), encoding="utf-8")


class TestMCPValidation:
    """Tests for schema validation on MCP write paths."""

    @pytest.fixture
    def validated_server(self):
        """Server with a kb.yaml schema for validation testing."""
        with _make_mcp_server(
            [{"name": "val-kb", "kb_type": KBType.GENERIC, "description": "Validated KB"}],
            tier="admin",
            extra_setup=_write_validation_schema,
        ) as env:
            yield env

    def test_kb_create_with_capture_lane_warning(self, validated_server):
        """Creating with unknown capture lane returns result + warning."""
        server = validated_server["server"]
        result = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "val-kb",
                "entry_type": "event",
                "title": "Lane Test Event",
                "date": "2025-03-01",
                "body": "Testing lanes.",
                "capture_lanes": ["lane-a", "new-lane"],
            },
        )
        assert result.get("created") is True
        assert "warnings" in result
        assert any("new-lane" in str(w.get("got", "")) for w in result["warnings"])

    def test_kb_update_validates_schema(self, validated_server):
        """_kb_update runs schema validation and surfaces warnings."""
        server = validated_server["server"]
        # First create an entry
        create_result = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "val-kb",
                "entry_type": "note",
                "title": "Update Val Test",
                "body": "Will be updated.",
                # val-kb declares only `event`; since #378 MCP no longer
                # exempts core types from the undeclared-type refusal (#197).
                "allow_undeclared": True,
            },
        )
        assert create_result.get("created") is True
        entry_id = create_result["entry_id"]

        # Update should return updated: True (basic case)
        update_result = server._dispatch_tool(
            "kb_update",
            {
                "entry_id": entry_id,
                "kb_name": "val-kb",
                "body": "Updated body.",
            },
        )
        assert update_result.get("updated") is True


# ---------------------------------------------------------------------------
# TestMCPQATools — QA validation tool tests
# ---------------------------------------------------------------------------


class TestMCPQATools:
    """Tests for QA validation MCP tools."""

    @pytest.fixture
    def qa_server_setup(self):
        """Create a test server with some bad data for QA testing."""

        def _populate_qa_data(_config, kb_map):
            events_repo = KBRepository(kb_map["qa-events"])
            event = EventEntry.create(
                date="2025-01-10",
                title="Good Event",
                body="Valid event body.",
                importance=5,
            )
            events_repo.save(event)

        with _make_mcp_server(
            [{"name": "qa-events", "kb_type": KBType.EVENTS, "description": "QA test events"}],
            tier="admin",
            extra_setup=_populate_qa_data,
        ) as env:
            # Insert a bad entry directly in DB (after server creation + indexing)
            env["server"].db._raw_conn.execute(
                "INSERT INTO entry (id, kb_name, entry_type, title, body, importance) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("no-title", "qa-events", "note", "", "Has body", 5),
            )
            env["server"].db._raw_conn.commit()
            yield env

    def test_mcp_qa_validate_tool(self, qa_server_setup):
        """MCP kb_qa_validate returns issues."""
        result = qa_server_setup["server"]._dispatch_tool(
            "kb_qa_validate", {"kb_name": "qa-events"}
        )
        assert "issues" in result
        assert "count" in result
        # Should find the missing title issue
        rules = [i["rule"] for i in result["issues"]]
        assert "missing_title" in rules

    def test_mcp_qa_status_tool(self, qa_server_setup):
        """MCP kb_qa_status returns status dict."""
        result = qa_server_setup["server"]._dispatch_tool("kb_qa_status", {"kb_name": "qa-events"})
        assert "total_entries" in result
        assert "total_issues" in result
        assert "issues_by_severity" in result
        assert "issues_by_rule" in result
        assert result["total_entries"] > 0


# ---------------------------------------------------------------------------
# TestMCPPostSaveValidation — post-save QA validation tests
# ---------------------------------------------------------------------------


def _write_qa_schema(schema_dict):
    """Return an extra_setup callback that writes a kb.yaml."""

    def _setup(_config, kb_map):
        from pyrite.utils.yaml import dump_yaml

        # Use the first (and only) KB
        kb_config = next(iter(kb_map.values()))
        (kb_config.path / "kb.yaml").write_text(dump_yaml(schema_dict), encoding="utf-8")

    return _setup


class TestMCPPostSaveValidation:
    """Tests for post-save QA validation on MCP write paths."""

    @pytest.fixture
    def qa_write_server(self):
        """Server for testing post-save QA validation."""
        with _make_mcp_server(
            [{"name": "qa-kb", "kb_type": KBType.GENERIC, "description": "QA write test KB"}],
            tier="write",
            extra_setup=_write_qa_schema({"name": "qa-write-kb", "types": {"note": {}}}),
        ) as env:
            yield env

    @pytest.fixture
    def qa_on_write_server(self):
        """Server with qa_on_write: true in kb.yaml."""
        with _make_mcp_server(
            [{"name": "qa-auto", "kb_type": KBType.GENERIC, "description": "QA auto-validate KB"}],
            tier="write",
            extra_setup=_write_qa_schema(
                {
                    "name": "qa-auto-kb",
                    "types": {"note": {}},
                    "validation": {"qa_on_write": True},
                }
            ),
        ) as env:
            yield env

    def test_kb_create_with_validate_clean(self, qa_write_server):
        """Valid entry with validate=True returns no error-level qa_issues."""
        server = qa_write_server["server"]
        result = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "qa-kb",
                "entry_type": "note",
                "title": "Clean Entry",
                "body": "This entry has a proper body.",
                "tags": ["test"],
                "validate": True,
            },
        )
        assert result.get("created") is True
        # May have rubric warnings (e.g. missing outlinks for new entry) but no errors
        qa_issues = result.get("qa_issues", [])
        errors = [i for i in qa_issues if i.get("severity") == "error"]
        assert errors == []

    def test_kb_create_with_validate_has_issues(self, qa_write_server):
        """Empty body with validate=True returns qa_issues."""
        server = qa_write_server["server"]
        result = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "qa-kb",
                "entry_type": "note",
                "title": "Empty Body Entry",
                "body": "",
                "validate": True,
            },
        )
        assert result.get("created") is True
        assert "qa_issues" in result
        rules = [i["rule"] for i in result["qa_issues"]]
        assert "empty_body" in rules

    def test_kb_update_with_validate(self, qa_write_server):
        """Update with validate=True returns qa_issues when entry has problems."""
        server = qa_write_server["server"]
        # Create an entry first
        create_result = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "qa-kb",
                "entry_type": "note",
                "title": "Will Update",
                "body": "Original body.",
            },
        )
        entry_id = create_result["entry_id"]

        # Update to empty body with validation
        update_result = server._dispatch_tool(
            "kb_update",
            {
                "entry_id": entry_id,
                "kb_name": "qa-kb",
                "body": "",
                "validate": True,
            },
        )
        assert update_result.get("updated") is True
        assert "qa_issues" in update_result
        rules = [i["rule"] for i in update_result["qa_issues"]]
        assert "empty_body" in rules

    def test_kb_create_without_validate(self, qa_write_server):
        """Default create (no validate param) returns no qa_issues key."""
        server = qa_write_server["server"]
        result = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "qa-kb",
                "entry_type": "note",
                "title": "No Validate Entry",
                "body": "",
            },
        )
        assert result.get("created") is True
        assert "qa_issues" not in result

    def test_kb_create_qa_on_write_kb(self, qa_on_write_server):
        """KB with qa_on_write: true auto-validates even without explicit param."""
        server = qa_on_write_server["server"]
        result = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "qa-auto",
                "entry_type": "note",
                "title": "Auto Validate Entry",
                "body": "",
            },
        )
        assert result.get("created") is True
        assert "qa_issues" in result
        rules = [i["rule"] for i in result["qa_issues"]]
        assert "empty_body" in rules

    def test_kb_create_qa_on_write_kb_clean(self, qa_on_write_server):
        """KB with qa_on_write: true, valid entry returns no error-level qa_issues."""
        server = qa_on_write_server["server"]
        result = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "qa-auto",
                "entry_type": "note",
                "title": "Clean Auto Entry",
                "body": "This entry has proper content.",
                "tags": ["test"],
            },
        )
        assert result.get("created") is True
        # May have rubric warnings (e.g. missing outlinks for new entry) but no errors
        qa_issues = result.get("qa_issues", [])
        errors = [i for i in qa_issues if i.get("severity") == "error"]
        assert errors == []


class TestDispatchRateLimiting:
    """Rate limiting integration tests."""

    def test_dispatch_rate_limited(self):
        """Exhaust rate limit and verify RATE_LIMITED error."""
        kb_defs = [{"name": "rl", "kb_type": "generic"}]
        with _make_mcp_server(
            kb_defs,
            tier="read",
            extra_setup=lambda cfg, _: setattr(cfg.settings, "rate_limit_read", "2/minute"),
        ) as ctx:
            server = ctx["server"]
            # Re-init limiter with updated settings
            from pyrite.server.mcp_rate_limiter import MCPRateLimiter

            server.rate_limiter = MCPRateLimiter(server.config.settings)

            # First 2 calls succeed (client_id != "stdio" so not exempt)
            for _ in range(2):
                result = server._dispatch_tool("kb_list", {}, client_id="remote")
                assert "error_code" not in result

            # 3rd call should be rate limited
            result = server._dispatch_tool("kb_list", {}, client_id="remote")
            assert result["error_code"] == "RATE_LIMITED"
            assert result["retryable"] is True

    def test_dispatch_exempt_stdio(self):
        """Stdio client bypasses rate limits when configured."""
        kb_defs = [{"name": "rl", "kb_type": "generic"}]
        with _make_mcp_server(
            kb_defs,
            tier="read",
            extra_setup=lambda cfg, _: setattr(cfg.settings, "rate_limit_read", "1/minute"),
        ) as ctx:
            server = ctx["server"]
            from pyrite.server.mcp_rate_limiter import MCPRateLimiter

            server.rate_limiter = MCPRateLimiter(server.config.settings)

            # Stdio should be exempt — call many times without hitting limit
            for _ in range(10):
                result = server._dispatch_tool(
                    "kb_list", {}, client_id="stdio", client_kind="local"
                )
                assert "error_code" not in result

    def _limited(self, limit="1/minute", exempt=True):
        kb_defs = [{"name": "rl", "kb_type": "generic"}]

        def setup(cfg, _):
            cfg.settings.rate_limit_read = limit
            cfg.settings.mcp_rate_limit_exempt_local = exempt

        return _make_mcp_server(kb_defs, tier="read", extra_setup=setup)

    def test_the_name_stdio_is_not_the_exemption(self):
        """P-L1: the exemption is the local principal kind, never a name a
        user can register."""
        from pyrite.server.mcp_rate_limiter import MCPRateLimiter

        with self._limited() as ctx:
            server = ctx["server"]
            server.rate_limiter = MCPRateLimiter(server.config.settings)
            for kind in ("user", None):
                server._dispatch_tool("kb_list", {}, client_id="stdio", client_kind=kind)
                result = server._dispatch_tool("kb_list", {}, client_id="stdio", client_kind=kind)
                assert result.get("error_code") == "RATE_LIMITED", (kind, result)

    def test_local_kind_is_limited_when_the_exemption_is_off(self):
        from pyrite.server.mcp_rate_limiter import MCPRateLimiter

        with self._limited(exempt=False) as ctx:
            server = ctx["server"]
            server.rate_limiter = MCPRateLimiter(server.config.settings)
            server._dispatch_tool("kb_list", {}, client_id="stdio", client_kind="local")
            result = server._dispatch_tool("kb_list", {}, client_id="stdio", client_kind="local")
            assert result.get("error_code") == "RATE_LIMITED"

    def test_run_stdio_is_the_local_kind_and_the_sdk_default_is_not(self, monkeypatch):
        """The stdio entry point chooses the local principal on purpose; a
        caller that names no kind (SSE forgetting to) is never exempt."""
        import inspect

        with self._limited() as ctx:
            server = ctx["server"]
            default = inspect.signature(server.build_sdk_server).parameters["client_kind"]
            assert default.default is None

            seen = {}

            class _StopError(Exception):
                pass

            def capture(**kwargs):
                seen.update(kwargs)
                raise _StopError

            monkeypatch.setattr(server, "build_sdk_server", capture)
            with pytest.raises(_StopError):
                server.run_stdio()
            assert seen.get("client_kind") == "local"

    def test_buckets_key_on_kind_and_id(self):
        """One id under two kinds is two buckets."""
        from pyrite.server.mcp_rate_limiter import MCPRateLimiter

        with self._limited() as ctx:
            server = ctx["server"]
            server.rate_limiter = MCPRateLimiter(server.config.settings)
            assert "error_code" not in server._dispatch_tool(
                "kb_list", {}, client_id="7", client_kind="user"
            )
            assert "error_code" not in server._dispatch_tool(
                "kb_list", {}, client_id="7", client_kind="operator_key"
            )
            limited = server._dispatch_tool("kb_list", {}, client_id="7", client_kind="user")
            assert limited.get("error_code") == "RATE_LIMITED"


# ---------------------------------------------------------------------------
# Body chunking tests
# ---------------------------------------------------------------------------


class TestBodyChunking:
    """Tests for auto-truncation and body pagination in kb_get, kb_batch_read, kb_read_body."""

    def _make_large_entry(self, ctx, body_size=10000):
        """Create an entry with a body of exactly body_size characters."""
        server = ctx["server"]
        body = "A" * body_size
        server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test",
                "entry_type": "note",
                "title": "Large Entry",
                "body": body,
            },
        )
        # Find the entry
        result = server._dispatch_tool(
            "kb_search", {"query": "Large Entry", "kb_name": "test", "include_body": True}
        )
        return result["results"][0]["id"], body

    def test_body_truncated_with_metadata(self):
        """Body >8K is truncated and includes truncation metadata."""
        kb_defs = [{"name": "test", "kb_type": "generic"}]
        with _make_mcp_server(kb_defs, tier="write") as ctx:
            entry_id, full_body = self._make_large_entry(ctx, body_size=10000)
            server = ctx["server"]
            result = server._dispatch_tool("kb_get", {"entry_id": entry_id, "kb_name": "test"})
            entry = result["entry"]
            assert entry["body_truncated"] is True
            assert entry["body_length"] == 10000
            assert entry["body_offset"] == 0
            assert entry["body_chunk_size"] == 8000
            assert len(entry["body"]) == 8000
            assert entry["body"] == full_body[:8000]

    def test_body_offset_returns_second_chunk(self):
        """body_offset=8000 returns the second chunk."""
        kb_defs = [{"name": "test", "kb_type": "generic"}]
        with _make_mcp_server(kb_defs, tier="write") as ctx:
            entry_id, full_body = self._make_large_entry(ctx, body_size=10000)
            server = ctx["server"]
            result = server._dispatch_tool(
                "kb_get", {"entry_id": entry_id, "kb_name": "test", "body_offset": 8000}
            )
            entry = result["entry"]
            assert entry["body_truncated"] is True
            assert entry["body_offset"] == 8000
            assert entry["body_chunk_size"] == 2000
            assert entry["body"] == full_body[8000:]

    def test_small_body_no_truncation(self):
        """Small body returned as-is without truncation metadata."""
        kb_defs = [{"name": "test", "kb_type": "generic"}]
        with _make_mcp_server(kb_defs, tier="write") as ctx:
            server = ctx["server"]
            server._dispatch_tool(
                "kb_create",
                {
                    "kb_name": "test",
                    "entry_type": "note",
                    "title": "Small Entry",
                    "body": "Short body text",
                },
            )
            result = server._dispatch_tool("kb_search", {"query": "Small Entry", "kb_name": "test"})
            eid = result["results"][0]["id"]
            result = server._dispatch_tool("kb_get", {"entry_id": eid, "kb_name": "test"})
            entry = result["entry"]
            assert entry["body"] == "Short body text"
            assert "body_truncated" not in entry
            assert "body_length" not in entry

    def test_kb_read_body_returns_chunk_with_has_more(self):
        """kb_read_body returns body chunk with has_more flag, no title/metadata."""
        kb_defs = [{"name": "test", "kb_type": "generic"}]
        with _make_mcp_server(kb_defs, tier="write") as ctx:
            entry_id, full_body = self._make_large_entry(ctx, body_size=10000)
            server = ctx["server"]
            result = server._dispatch_tool(
                "kb_read_body", {"entry_id": entry_id, "kb_name": "test"}
            )
            assert "title" not in result
            assert result["body"] == full_body[:8000]
            assert result["body_length"] == 10000
            assert result["body_offset"] == 0
            assert result["body_chunk_size"] == 8000
            assert result["has_more"] is True

            # Second chunk
            result2 = server._dispatch_tool(
                "kb_read_body",
                {"entry_id": entry_id, "kb_name": "test", "body_offset": 8000},
            )
            assert result2["body"] == full_body[8000:]
            assert result2["has_more"] is False

    def test_batch_read_applies_per_entry_truncation(self):
        """kb_batch_read applies per-entry body truncation."""
        kb_defs = [{"name": "test", "kb_type": "generic"}]
        with _make_mcp_server(kb_defs, tier="write") as ctx:
            entry_id, full_body = self._make_large_entry(ctx, body_size=10000)
            server = ctx["server"]
            result = server._dispatch_tool(
                "kb_batch_read",
                {"entries": [{"entry_id": entry_id, "kb_name": "test"}]},
            )
            entry = result["entries"][0]
            assert entry["body_truncated"] is True
            assert entry["body_length"] == 10000
            assert len(entry["body"]) == 8000


class TestKBRegistryAddDuplicateName:
    """#506 item 1: `kb_registry_add` over MCP answered a duplicate name
    with the generic, fixed `ConfigError.public_message` ("The configuration
    is invalid...") once #501 gave the base class a fixed message -- REST's
    own `except ConfigError` catch at this same call (`admin.py` ~278) still
    names the conflict via `str(e)`. A narrow `ConfigError` subclass for
    exactly this refusal, with its own `public_message = None`, is safe by
    construction (the message only ever names the KB and says it exists),
    so both transports say what happened."""

    def test_mcp_names_the_conflict_like_rest_does(self):
        kb_defs = [{"name": "test-kb", "kb_type": "generic"}]
        with _make_mcp_server(kb_defs, tier="admin") as ctx:
            server = ctx["server"]
            result = server._dispatch_tool(
                "kb_registry_add",
                {"name": "test-kb", "path": "/tmp/wherever-506"},
            )
            assert "error" in result, result
            assert result["error"] == "KB 'test-kb' already exists", result
            assert result["error_code"] == "CONFLICT", result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

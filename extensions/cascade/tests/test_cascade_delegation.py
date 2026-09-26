"""Tests for Cascade MCP tool delegation to JI query functions."""

import pytest
from pyrite_cascade.plugin import CascadePlugin

from pyrite.services.access_policy import UNSCOPED
from pyrite.storage.database import PyriteDB


@pytest.fixture
def db(tmp_path):
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()
    db = PyriteDB(tmp_path / "index.db")
    db.register_kb("test", "cascade-timeline", str(kb_path))
    yield db
    db.close()


@pytest.fixture
def plugin(db):
    """CascadePlugin with injected DB context."""

    class FakeCtx:
        pass

    ctx = FakeCtx()
    ctx.db = db
    p = CascadePlugin()
    p.set_context(ctx)
    return p


def _insert_entry(
    db, entry_id, title, entry_type, kb_name="test", importance=5, date="", metadata=None
):
    """Helper to insert a test entry directly into the DB."""
    db.upsert_entry(
        {
            "id": entry_id,
            "kb_name": kb_name,
            "title": title,
            "body": "",
            "entry_type": entry_type,
            "importance": importance,
            "date": date,
            "metadata": metadata or {},
            "tags": [],
        }
    )


class TestMcpNetworkDelegation:
    """_mcp_network delegates to query_network and returns the same structure."""

    def test_returns_center_outlinks_backlinks(self, db, plugin):
        _insert_entry(db, "actor-1", "Actor One", "actor", kb_name="test")
        result = plugin._mcp_network({"entry_id": "actor-1", "kb_name": "test"})

        assert "center" in result
        assert result["center"]["id"] == "actor-1"
        assert result["center"]["title"] == "Actor One"
        assert "outlinks" in result
        assert "backlinks" in result

    def test_missing_entry_returns_error(self, db, plugin):
        result = plugin._mcp_network({"entry_id": "nonexistent", "kb_name": "test"})
        assert "error" in result
        assert "nonexistent" in result["error"]

    def test_returns_same_as_ji_query_network(self, db, plugin):
        """Cascade _mcp_network should return identical result to JI query_network."""
        from pyrite_journalism_investigation.queries import query_network

        _insert_entry(db, "actor-2", "Actor Two", "actor", kb_name="test")

        cascade_result = plugin._mcp_network({"entry_id": "actor-2", "kb_name": "test"})
        ji_result = query_network(db, "test", "actor-2", readable_kbs=UNSCOPED)

        assert cascade_result == ji_result


class TestMcpTimelineCaptureLaneFilter:
    """cascade_timeline still filters by capture_lane correctly."""

    def test_filter_by_capture_lane(self, db, plugin):
        _insert_entry(
            db,
            "te-1",
            "Financial Event",
            "timeline_event",
            kb_name="test",
            date="2000-01-01",
            importance=7,
            metadata={"capture_lanes": ["financial"], "actors": []},
        )
        _insert_entry(
            db,
            "te-2",
            "Political Event",
            "timeline_event",
            kb_name="test",
            date="2000-06-01",
            importance=7,
            metadata={"capture_lanes": ["political"], "actors": []},
        )

        result = plugin._mcp_timeline({"kb_name": "test", "capture_lane": "financial"})
        assert result["count"] == 1
        assert result["events"][0]["id"] == "te-1"

    def test_no_capture_lane_returns_all(self, db, plugin):
        _insert_entry(
            db,
            "te-3",
            "Event A",
            "timeline_event",
            kb_name="test",
            date="2000-01-01",
            importance=5,
            metadata={"capture_lanes": ["financial"], "actors": []},
        )
        _insert_entry(
            db,
            "te-4",
            "Event B",
            "timeline_event",
            kb_name="test",
            date="2000-06-01",
            importance=5,
            metadata={"capture_lanes": ["political"], "actors": []},
        )

        result = plugin._mcp_timeline({"kb_name": "test"})
        assert result["count"] == 2


class TestMcpActorsFilters:
    """cascade_actors still filters by era correctly."""

    def test_filter_by_era(self, db, plugin):
        _insert_entry(
            db,
            "a-1",
            "Yeltsin-era Actor",
            "actor",
            kb_name="test",
            importance=8,
            metadata={"era": "yeltsin", "capture_lanes": []},
        )
        _insert_entry(
            db,
            "a-2",
            "Putin-era Actor",
            "actor",
            kb_name="test",
            importance=7,
            metadata={"era": "putin", "capture_lanes": []},
        )

        result = plugin._mcp_actors({"kb_name": "test", "era": "yeltsin"})
        assert result["count"] == 1
        assert result["actors"][0]["id"] == "a-1"

    def test_filter_by_min_importance(self, db, plugin):
        _insert_entry(
            db,
            "a-3",
            "Important Actor",
            "actor",
            kb_name="test",
            importance=9,
            metadata={},
        )
        _insert_entry(
            db,
            "a-4",
            "Minor Actor",
            "actor",
            kb_name="test",
            importance=3,
            metadata={},
        )

        result = plugin._mcp_actors({"kb_name": "test", "min_importance": 7})
        assert result["count"] == 1
        assert result["actors"][0]["id"] == "a-3"


class TestParseMetaDelegation:
    """MCP handlers use parse_meta from JI utils instead of inline JSON parsing."""

    def test_actors_handles_string_metadata(self, db, plugin):
        """Actors handler correctly parses string metadata (via parse_meta)."""
        _insert_entry(
            db,
            "a-5",
            "String Meta Actor",
            "actor",
            kb_name="test",
            importance=6,
            metadata={"era": "soviet", "capture_lanes": ["institutional"]},
        )

        result = plugin._mcp_actors({"kb_name": "test"})
        actor = next(a for a in result["actors"] if a["id"] == "a-5")
        assert actor["era"] == "soviet"
        assert actor["capture_lanes"] == ["institutional"]

    def test_timeline_handles_string_metadata(self, db, plugin):
        """Timeline handler correctly parses string metadata (via parse_meta)."""
        _insert_entry(
            db,
            "te-5",
            "String Meta Event",
            "timeline_event",
            kb_name="test",
            date="1999-01-01",
            importance=5,
            metadata={"capture_lanes": ["financial"], "actors": ["actor-x"]},
        )

        result = plugin._mcp_timeline({"kb_name": "test"})
        event = next(e for e in result["events"] if e["id"] == "te-5")
        assert event["actors"] == ["actor-x"]

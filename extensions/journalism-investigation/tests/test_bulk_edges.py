"""Tests for bulk edge creation."""

import pytest
from pyrite_journalism_investigation.bulk import create_edge_batch, validate_edge_batch

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB


@pytest.fixture
def svc(tmp_path):
    """A KBService over one investigation KB: the batch writes go through it."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="test", path=kb_path, kb_type="journalism-investigation")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    db.register_kb("test", "journalism-investigation", str(kb_path))
    yield KBService(config, db)
    db.close()


@pytest.fixture
def db(svc):
    return svc.db


# =========================================================================
# validate_edge_batch tests
# =========================================================================


class TestValidateEdgeBatch:
    def test_all_valid_edges_pass(self):
        """All valid edges return zero errors."""
        edges = [
            {"type": "ownership", "fields": {"owner": "[[alice]]", "asset": "[[corp-a]]"}},
            {"type": "membership", "fields": {"person": "[[bob]]", "organization": "[[org-x]]"}},
            {"type": "funding", "fields": {"funder": "[[donor]]", "recipient": "[[ngo]]"}},
        ]
        result = validate_edge_batch(edges)
        assert result["valid"] == 3
        assert result["invalid"] == 0
        assert result["errors"] == []

    def test_missing_required_field_detected(self):
        """Missing required field produces an error with the field name."""
        edges = [
            {"type": "ownership", "fields": {"owner": "[[alice]]"}},  # missing asset
        ]
        result = validate_edge_batch(edges)
        assert result["valid"] == 0
        assert result["invalid"] == 1
        assert len(result["errors"]) == 1
        assert result["errors"][0]["index"] == 0
        assert result["errors"][0]["errors"] == [
            {"field": "asset", "rule": "required", "message": "Missing required field: asset"}
        ]

    def test_invalid_edge_type_rejected(self):
        """Unknown edge type is rejected."""
        edges = [
            {"type": "bribery", "fields": {"from": "[[x]]", "to": "[[y]]"}},
        ]
        result = validate_edge_batch(edges)
        assert result["valid"] == 0
        assert result["invalid"] == 1
        row = result["errors"][0]["errors"][0]
        assert (row["field"], row["rule"]) == ("type", "enum")
        assert row["got"] == "bribery"
        assert row["expected"] == ["funding", "membership", "ownership"]

    def test_mixed_valid_and_invalid(self):
        """Mix of valid and invalid edges counted correctly."""
        edges = [
            {"type": "ownership", "fields": {"owner": "[[a]]", "asset": "[[b]]"}},
            {"type": "membership", "fields": {}},  # missing both required fields
        ]
        result = validate_edge_batch(edges)
        assert result["valid"] == 1
        assert result["invalid"] == 1

    @pytest.mark.parametrize("edge", ["ownership", 3, None, ["ownership"]])
    def test_an_edge_that_is_not_an_object_is_refused_as_a_row(self, edge):
        """A non-object edge is one invalid edge, refused as a row naming the edge."""
        result = validate_edge_batch([edge])
        assert (result["valid"], result["invalid"]) == (0, 1)
        assert result["errors"][0]["errors"] == [
            {
                "field": "edge",
                "rule": "type",
                "message": "An edge must be an object",
                "expected": "object",
            }
        ]

    def test_fields_that_are_not_an_object_are_refused_as_a_row(self):
        result = validate_edge_batch([{"type": "ownership", "fields": ["owner"]}])
        assert (result["valid"], result["invalid"]) == (0, 1)
        assert result["errors"][0]["errors"] == [
            {
                "field": "fields",
                "rule": "type",
                "message": "'fields' must be an object",
                "expected": "object",
            }
        ]

    def test_a_reserved_field_name_is_refused_as_a_row_naming_it(self):
        """A key that belongs to the entry (title, id, ...) is refused in fields."""
        edge = {"type": "ownership", "fields": {"owner": "[[a]]", "asset": "[[b]]", "title": "t"}}
        result = validate_edge_batch([edge])
        assert result["errors"][0]["errors"] == [
            {
                "field": "title",
                "rule": "reserved",
                "message": "Reserved field name: title (set it on the edge, not in fields)",
            }
        ]

    def test_empty_batch(self):
        """Empty batch returns zero counts."""
        result = validate_edge_batch([])
        assert result["valid"] == 0
        assert result["invalid"] == 0
        assert result["errors"] == []


# =========================================================================
# create_edge_batch tests
# =========================================================================


class TestCreateEdgeBatch:
    def test_create_single_ownership_edge(self, svc, db):
        """Single ownership edge is created in the DB."""
        edges = [
            {"type": "ownership", "fields": {"owner": "[[alice]]", "asset": "[[corp-a]]"}},
        ]
        result = create_edge_batch(svc, "test", edges)
        assert result["created"] == 1
        assert result["skipped"] == 0
        assert result["errors"] == 0
        assert len(result["entries"]) == 1
        assert result["entries"][0]["status"] == "created"

        # Verify it's actually in the DB
        entry = db.get_entry(result["entries"][0]["id"], "test")
        assert entry is not None
        assert entry["entry_type"] == "ownership"

    def test_create_batch_mixed_types(self, svc, db):
        """Batch of ownership + membership + funding all created."""
        edges = [
            {"type": "ownership", "fields": {"owner": "[[alice]]", "asset": "[[corp-a]]"}},
            {"type": "membership", "fields": {"person": "[[bob]]", "organization": "[[org-x]]"}},
            {"type": "funding", "fields": {"funder": "[[donor]]", "recipient": "[[ngo]]"}},
        ]
        result = create_edge_batch(svc, "test", edges)
        assert result["created"] == 3
        assert result["skipped"] == 0
        assert result["errors"] == 0

        # Verify types
        types = {e["type"] for e in result["entries"]}
        assert types == {"ownership", "membership", "funding"}

    @pytest.mark.control(
        reason="a dry run or an empty batch wrote nothing before this change either"
    )
    def test_dry_run_returns_preview_without_creating(self, svc, db):
        """Dry run shows what would be created but does not persist."""
        edges = [
            {"type": "ownership", "fields": {"owner": "[[alice]]", "asset": "[[corp-a]]"}},
        ]
        result = create_edge_batch(svc, "test", edges, dry_run=True)
        assert result["created"] == 0
        assert len(result["entries"]) == 1
        assert result["entries"][0]["status"] == "would_create"

        # Nothing in DB
        entry = db.get_entry(result["entries"][0]["id"], "test")
        assert entry is None

    def test_skip_duplicate_edges(self, svc, db):
        """Duplicate edges (same ID) are skipped, not re-created."""
        edges = [
            {"type": "ownership", "fields": {"owner": "[[alice]]", "asset": "[[corp-a]]"}},
        ]
        # First creation
        result1 = create_edge_batch(svc, "test", edges)
        assert result1["created"] == 1

        # Second creation — same edge, should be skipped
        result2 = create_edge_batch(svc, "test", edges)
        assert result2["created"] == 0
        assert result2["skipped"] == 1
        assert result2["entries"][0]["status"] == "skipped"

    def test_auto_generate_titles_from_fields(self, svc, db):
        """Titles are auto-generated from entity names if not provided."""
        edges = [
            {"type": "ownership", "fields": {"owner": "[[alice]]", "asset": "[[corp-a]]"}},
            {"type": "membership", "fields": {"person": "[[bob]]", "organization": "[[org-x]]"}},
            {"type": "funding", "fields": {"funder": "[[donor]]", "recipient": "[[ngo]]"}},
        ]
        result = create_edge_batch(svc, "test", edges)
        titles = {e["title"] for e in result["entries"]}
        assert "alice owns corp-a" in titles
        assert "bob member of org-x" in titles
        assert "donor funds ngo" in titles

    def test_auto_generate_ids_from_titles(self, svc, db):
        """Entry IDs are generated from titles."""
        edges = [
            {"type": "ownership", "fields": {"owner": "[[alice]]", "asset": "[[corp-a]]"}},
        ]
        result = create_edge_batch(svc, "test", edges)
        entry_id = result["entries"][0]["id"]
        assert entry_id == "alice-owns-corp-a"

    @pytest.mark.control(
        reason="a dry run or an empty batch wrote nothing before this change either"
    )
    def test_empty_batch_returns_zero_counts(self, svc, db):
        """Empty batch returns all-zero counts."""
        result = create_edge_batch(svc, "test", [])
        assert result["created"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == 0
        assert result["entries"] == []

    def test_partial_failure_valid_created_invalid_reported(self, svc, db):
        """Valid edges are created; invalid ones are reported as errors."""
        edges = [
            {"type": "ownership", "fields": {"owner": "[[alice]]", "asset": "[[corp-a]]"}},
            {"type": "ownership", "fields": {"owner": "[[bob]]"}},  # missing asset
        ]
        result = create_edge_batch(svc, "test", edges)
        assert result["created"] == 1
        assert result["errors"] == 1
        statuses = {e["status"] for e in result["entries"]}
        assert "created" in statuses
        assert "error" in statuses

        # The refused record carries the row, not the sentence the row came from.
        refused = next(e for e in result["entries"] if e["status"] == "error")
        assert refused["errors"] == [
            {"field": "asset", "rule": "required", "message": "Missing required field: asset"}
        ]

    def test_a_non_object_edge_is_an_error_row_and_the_batch_goes_on(self, svc, db):
        edges = ["ownership", {"type": "ownership", "fields": {"owner": "[[a]]", "asset": "[[b]]"}}]
        result = create_edge_batch(svc, "test", edges)
        assert (result["created"], result["errors"]) == (1, 1)
        refused = result["entries"][0]
        assert refused["status"] == "error"
        assert [(row["field"], row["rule"]) for row in refused["errors"]] == [("edge", "type")]

    def test_custom_title_used_when_provided(self, svc, db):
        """If title is explicitly provided, it is used instead of auto-generated."""
        edges = [
            {
                "type": "ownership",
                "fields": {"owner": "[[alice]]", "asset": "[[corp-a]]"},
                "title": "Alice's stake in Corp A",
            },
        ]
        result = create_edge_batch(svc, "test", edges)
        assert result["entries"][0]["title"] == "Alice's stake in Corp A"

    def test_optional_fields_stored_in_metadata(self, svc, db):
        """Optional fields like percentage, role, amount are stored."""
        edges = [
            {
                "type": "ownership",
                "fields": {
                    "owner": "[[alice]]",
                    "asset": "[[corp-a]]",
                    "percentage": 51,
                    "beneficial": True,
                },
            },
        ]
        result = create_edge_batch(svc, "test", edges)
        entry = db.get_entry(result["entries"][0]["id"], "test")
        assert entry is not None
        meta = entry.get("metadata", {})
        if isinstance(meta, str):
            import json

            meta = json.loads(meta)
        # OwnershipEntry types percentage as a string, as its file round-trip does.
        assert meta.get("percentage") == "51"
        assert meta.get("beneficial") is True

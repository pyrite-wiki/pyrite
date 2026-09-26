"""Tests for cross-KB entity deduplication."""

import pytest
from pyrite_journalism_investigation.dedup import (
    create_same_as_links,
    find_duplicates,
    merge_entity_view,
)

from pyrite.services.access_policy import UNSCOPED
from pyrite.storage.database import PyriteDB


@pytest.fixture
def multi_kb_db(tmp_path):
    kb1_path = tmp_path / "kb1"
    kb1_path.mkdir()
    kb2_path = tmp_path / "kb2"
    kb2_path.mkdir()
    db = PyriteDB(tmp_path / "index.db")
    db.register_kb("kb1", "journalism-investigation", str(kb1_path))
    db.register_kb("kb2", "journalism-investigation", str(kb2_path))
    yield db
    db.close()


class TestFindDuplicatesExactTitle:
    """Exact title match across KBs."""

    def test_exact_title_match(self, multi_kb_db):
        multi_kb_db.upsert_entry(
            {
                "id": "john-smith-1",
                "kb_name": "kb1",
                "title": "John Smith",
                "entry_type": "person",
                "body": "A person in kb1",
            }
        )
        multi_kb_db.upsert_entry(
            {
                "id": "john-smith-2",
                "kb_name": "kb2",
                "title": "John Smith",
                "entry_type": "person",
                "body": "A person in kb2",
            }
        )
        groups = find_duplicates(multi_kb_db)
        assert len(groups) >= 1
        group = groups[0]
        assert group["canonical"]["title"] == "John Smith"
        assert len(group["duplicates"]) >= 1
        dup = group["duplicates"][0]
        assert dup["match_type"] == "exact"
        assert dup["confidence"] == 1.0

    def test_case_insensitive_title_match(self, multi_kb_db):
        multi_kb_db.upsert_entry(
            {
                "id": "acme-1",
                "kb_name": "kb1",
                "title": "ACME Corporation",
                "entry_type": "organization",
                "body": "",
            }
        )
        multi_kb_db.upsert_entry(
            {
                "id": "acme-2",
                "kb_name": "kb2",
                "title": "acme corporation",
                "entry_type": "organization",
                "body": "",
            }
        )
        groups = find_duplicates(multi_kb_db)
        assert len(groups) >= 1
        # Find the ACME group
        acme_groups = [g for g in groups if g["canonical"]["title"].lower() == "acme corporation"]
        assert len(acme_groups) == 1
        assert acme_groups[0]["duplicates"][0]["match_type"] == "exact"


class TestFindDuplicatesAlias:
    """Alias overlap detection."""

    def test_alias_overlap(self, multi_kb_db):
        multi_kb_db.upsert_entry(
            {
                "id": "vladimir-putin",
                "kb_name": "kb1",
                "title": "Vladimir Putin",
                "entry_type": "person",
                "body": "",
                "metadata": {"aliases": ["VVP", "Putin"]},
            }
        )
        multi_kb_db.upsert_entry(
            {
                "id": "vvp-person",
                "kb_name": "kb2",
                "title": "VVP",
                "entry_type": "person",
                "body": "",
                "metadata": {"aliases": ["Vladimir Vladimirovich Putin"]},
            }
        )
        groups = find_duplicates(multi_kb_db)
        assert len(groups) >= 1
        # Find the group containing these two
        found = False
        for g in groups:
            ids = {g["canonical"]["id"]} | {d["id"] for d in g["duplicates"]}
            if "vladimir-putin" in ids and "vvp-person" in ids:
                found = True
                dup = [d for d in g["duplicates"] if d["id"] in ("vladimir-putin", "vvp-person")][0]
                assert dup["match_type"] == "alias"
                assert dup["confidence"] == 0.95
        assert found


class TestFindDuplicatesFuzzy:
    """Fuzzy matching above/below threshold."""

    def test_fuzzy_match_above_threshold(self, multi_kb_db):
        multi_kb_db.upsert_entry(
            {
                "id": "acme-corp",
                "kb_name": "kb1",
                "title": "ACME Corporation Ltd",
                "entry_type": "organization",
                "body": "",
            }
        )
        multi_kb_db.upsert_entry(
            {
                "id": "acme-corp-2",
                "kb_name": "kb2",
                "title": "ACME Corporation Limited",
                "entry_type": "organization",
                "body": "",
            }
        )
        groups = find_duplicates(multi_kb_db, threshold=0.80)
        assert len(groups) >= 1
        found = False
        for g in groups:
            ids = {g["canonical"]["id"]} | {d["id"] for d in g["duplicates"]}
            if "acme-corp" in ids and "acme-corp-2" in ids:
                found = True
                dup = [d for d in g["duplicates"] if d["id"] in ("acme-corp", "acme-corp-2")][0]
                assert dup["match_type"] == "fuzzy"
                assert 0.80 <= dup["confidence"] < 1.0
        assert found

    def test_below_threshold_not_detected(self, multi_kb_db):
        multi_kb_db.upsert_entry(
            {
                "id": "alpha-inc",
                "kb_name": "kb1",
                "title": "Alpha Industries Inc",
                "entry_type": "organization",
                "body": "",
            }
        )
        multi_kb_db.upsert_entry(
            {
                "id": "beta-llc",
                "kb_name": "kb2",
                "title": "Beta Holdings LLC",
                "entry_type": "organization",
                "body": "",
            }
        )
        groups = find_duplicates(multi_kb_db, threshold=0.85)
        # These titles are very different — should not be grouped
        for g in groups:
            ids = {g["canonical"]["id"]} | {d["id"] for d in g["duplicates"]}
            assert not ("alpha-inc" in ids and "beta-llc" in ids)


class TestFindDuplicatesFiltering:
    """Filtering by entry type."""

    def test_filter_by_entry_type(self, multi_kb_db):
        multi_kb_db.upsert_entry(
            {
                "id": "john-person",
                "kb_name": "kb1",
                "title": "John Smith",
                "entry_type": "person",
                "body": "",
            }
        )
        multi_kb_db.upsert_entry(
            {
                "id": "john-person-2",
                "kb_name": "kb2",
                "title": "John Smith",
                "entry_type": "person",
                "body": "",
            }
        )
        multi_kb_db.upsert_entry(
            {
                "id": "john-event",
                "kb_name": "kb1",
                "title": "John Smith Event",
                "entry_type": "investigation_event",
                "body": "",
            }
        )
        # Only look at persons
        groups = find_duplicates(multi_kb_db, entry_types=["person"])
        for g in groups:
            assert g["canonical"]["id"] != "john-event"
            for d in g["duplicates"]:
                assert d["id"] != "john-event"

    def test_no_duplicates_returns_empty(self, multi_kb_db):
        multi_kb_db.upsert_entry(
            {
                "id": "unique-person",
                "kb_name": "kb1",
                "title": "Unique Person Alpha",
                "entry_type": "person",
                "body": "",
            }
        )
        multi_kb_db.upsert_entry(
            {
                "id": "different-person",
                "kb_name": "kb2",
                "title": "Completely Different Name",
                "entry_type": "person",
                "body": "",
            }
        )
        groups = find_duplicates(multi_kb_db)
        # These are too different to match
        for g in groups:
            ids = {g["canonical"]["id"]} | {d["id"] for d in g["duplicates"]}
            assert not ("unique-person" in ids and "different-person" in ids)


class TestCreateSameAsLinks:
    """Create same_as links between canonical and duplicate entries."""

    def test_create_links(self, multi_kb_db):
        multi_kb_db.upsert_entry(
            {
                "id": "person-a",
                "kb_name": "kb1",
                "title": "Jane Doe",
                "entry_type": "person",
                "body": "",
            }
        )
        multi_kb_db.upsert_entry(
            {
                "id": "person-b",
                "kb_name": "kb2",
                "title": "Jane Doe",
                "entry_type": "person",
                "body": "",
            }
        )
        result = create_same_as_links(
            multi_kb_db,
            "person-a",
            "kb1",
            [{"id": "person-b", "kb_name": "kb2"}],
        )
        assert result["linked"] == 1
        assert result["skipped"] == 0
        assert len(result["links"]) == 1
        link = result["links"][0]
        assert link["from_id"] == "person-a"
        assert link["from_kb"] == "kb1"
        assert link["to_id"] == "person-b"
        assert link["to_kb"] == "kb2"

        # Verify the link actually exists in the DB
        outlinks = multi_kb_db.get_outlinks("person-a", "kb1", readable_kbs=UNSCOPED)
        same_as_links = [l for l in outlinks if l["relation"] == "same_as"]
        assert len(same_as_links) == 1
        assert same_as_links[0]["id"] == "person-b"

    def test_dry_run_returns_preview(self, multi_kb_db):
        multi_kb_db.upsert_entry(
            {
                "id": "org-a",
                "kb_name": "kb1",
                "title": "Big Corp",
                "entry_type": "organization",
                "body": "",
            }
        )
        multi_kb_db.upsert_entry(
            {
                "id": "org-b",
                "kb_name": "kb2",
                "title": "Big Corp",
                "entry_type": "organization",
                "body": "",
            }
        )
        result = create_same_as_links(
            multi_kb_db,
            "org-a",
            "kb1",
            [{"id": "org-b", "kb_name": "kb2"}],
            dry_run=True,
        )
        assert result["linked"] == 1
        assert len(result["links"]) == 1

        # Verify NO link was actually created
        outlinks = multi_kb_db.get_outlinks("org-a", "kb1", readable_kbs=UNSCOPED)
        same_as_links = [l for l in outlinks if l["relation"] == "same_as"]
        assert len(same_as_links) == 0


class TestMergeEntityView:
    """Merge entity view across KBs via same_as links."""

    @staticmethod
    def _seed(multi_kb_db):
        multi_kb_db.upsert_entry(
            {
                "id": "entity-a",
                "kb_name": "kb1",
                "title": "Acme Corp",
                "entry_type": "organization",
                "body": "",
                "metadata": {"aliases": ["ACME"]},
                "tags": ["company", "target"],
            }
        )
        multi_kb_db.upsert_entry(
            {
                "id": "entity-b",
                "kb_name": "kb2",
                "title": "ACME Corporation",
                "entry_type": "organization",
                "body": "",
                "metadata": {"aliases": ["Acme Co"]},
                "tags": ["fraud", "company"],
            }
        )
        # Create same_as link
        create_same_as_links(
            multi_kb_db,
            "entity-a",
            "kb1",
            [{"id": "entity-b", "kb_name": "kb2"}],
        )

    def test_merge_view(self, multi_kb_db):
        self._seed(multi_kb_db)
        view = merge_entity_view(multi_kb_db, "entity-a", "kb1", readable_kbs=UNSCOPED)
        assert view["canonical"]["id"] == "entity-a"
        assert view["canonical"]["kb_name"] == "kb1"
        assert len(view["appearances"]) == 2
        appearance_ids = {a["id"] for a in view["appearances"]}
        assert "entity-a" in appearance_ids
        assert "entity-b" in appearance_ids
        # Merged aliases from both
        assert "ACME" in view["merged_aliases"]
        assert "Acme Co" in view["merged_aliases"]
        # Merged tags from both
        assert "company" in view["merged_tags"]
        assert "target" in view["merged_tags"]
        assert "fraud" in view["merged_tags"]

    def test_merge_view_stays_within_the_readable_kbs(self, multi_kb_db):
        """The same_as walk crosses KBs; an appearance in a KB the caller
        cannot read is neither followed nor merged (P-R4)."""
        self._seed(multi_kb_db)
        view = merge_entity_view(multi_kb_db, "entity-a", "kb1", readable_kbs={"kb1"})
        assert {a["id"] for a in view["appearances"]} == {"entity-a"}
        assert "Acme Co" not in view["merged_aliases"]
        assert "fraud" not in view["merged_tags"]

    def test_merge_view_from_an_unreadable_start_is_empty(self, multi_kb_db):
        self._seed(multi_kb_db)
        view = merge_entity_view(multi_kb_db, "entity-b", "kb2", readable_kbs={"kb1"})
        assert view["appearances"] == [] and view["canonical"]["title"] == ""

"""Tests for incremental link sync optimization."""

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.services.access_policy import UNSCOPED


@pytest.fixture
def db_with_entry(tmp_path):
    """DB with a single entry for link testing."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()
    kb = KBConfig(name="test", path=kb_path, kb_type="standard")
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    db.register_kb("test", "standard", str(kb_path))

    # Create source and target entries
    db.upsert_entry(
        {
            "id": "source",
            "kb_name": "test",
            "title": "Source Entry",
            "entry_type": "note",
        }
    )
    for i in range(5):
        db.upsert_entry(
            {
                "id": f"target-{i}",
                "kb_name": "test",
                "title": f"Target {i}",
                "entry_type": "note",
            }
        )

    yield db
    db.close()


class TestIncrementalLinkSync:
    """Test that _sync_links correctly handles add/remove/update diffs."""

    def test_add_new_link(self, db_with_entry):
        """Adding a link to an entry with no links."""
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [{"target": "target-0", "relation": "related_to", "kb": "test"}],
            }
        )
        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 1
        assert outlinks[0]["id"] == "target-0"

    def test_add_second_link_preserves_first(self, db_with_entry):
        """Adding a second link keeps the first."""
        # First save with 1 link
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [{"target": "target-0", "relation": "related_to", "kb": "test"}],
            }
        )
        # Second save with 2 links
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [
                    {"target": "target-0", "relation": "related_to", "kb": "test"},
                    {"target": "target-1", "relation": "related_to", "kb": "test"},
                ],
            }
        )
        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 2
        targets = {l["id"] for l in outlinks}
        assert targets == {"target-0", "target-1"}

    def test_remove_link(self, db_with_entry):
        """Removing a link from the list removes it from DB."""
        # Save with 2 links
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [
                    {"target": "target-0", "relation": "related_to", "kb": "test"},
                    {"target": "target-1", "relation": "related_to", "kb": "test"},
                ],
            }
        )
        # Save with 1 link (removed target-1)
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [
                    {"target": "target-0", "relation": "related_to", "kb": "test"},
                ],
            }
        )
        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 1
        assert outlinks[0]["id"] == "target-0"

    def test_change_relation(self, db_with_entry):
        """Changing a link's relation updates it."""
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [{"target": "target-0", "relation": "related_to", "kb": "test"}],
            }
        )
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [{"target": "target-0", "relation": "depends_on", "kb": "test"}],
            }
        )
        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 1
        assert outlinks[0]["relation"] == "depends_on"

    def test_empty_links_clears_all(self, db_with_entry):
        """Empty link list removes all links."""
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [
                    {"target": "target-0", "relation": "related_to", "kb": "test"},
                    {"target": "target-1", "relation": "related_to", "kb": "test"},
                ],
            }
        )
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [],
            }
        )
        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 0

    def test_no_change_is_idempotent(self, db_with_entry):
        """Re-saving identical links doesn't create duplicates."""
        links = [
            {"target": "target-0", "relation": "related_to", "kb": "test"},
            {"target": "target-1", "relation": "depends_on", "kb": "test"},
        ]
        for _ in range(3):
            db_with_entry.upsert_entry(
                {
                    "id": "source",
                    "kb_name": "test",
                    "title": "Source Entry",
                    "entry_type": "note",
                    "links": links,
                }
            )
        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 2

    def test_many_links_add_one(self, db_with_entry):
        """Adding 1 link to an entry with many existing links."""
        # Create 50 target entries
        for i in range(5, 55):
            db_with_entry.upsert_entry(
                {
                    "id": f"target-{i}",
                    "kb_name": "test",
                    "title": f"Target {i}",
                    "entry_type": "note",
                }
            )

        # Save with 50 links
        links = [
            {"target": f"target-{i}", "relation": "related_to", "kb": "test"} for i in range(50)
        ]
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": links,
            }
        )

        # Add 1 more link
        links.append({"target": "target-50", "relation": "related_to", "kb": "test"})
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": links,
            }
        )

        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 51

    def test_inverse_relation_set(self, db_with_entry):
        """Inverse relation should be computed for new links."""
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [{"target": "target-0", "relation": "depends_on", "kb": "test"}],
            }
        )
        backlinks = db_with_entry.get_backlinks("target-0", "test", readable_kbs=UNSCOPED)
        assert len(backlinks) == 1
        # Backlink should use inverse relation
        assert backlinks[0]["relation"] in ("depended_on_by", "blocks", "dependency_of")

    def test_note_preserved_on_unchanged_link(self, db_with_entry):
        """Link notes should be preserved when link is unchanged."""
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [
                    {
                        "target": "target-0",
                        "relation": "related_to",
                        "kb": "test",
                        "note": "important",
                    },
                    {"target": "target-1", "relation": "related_to", "kb": "test"},
                ],
            }
        )
        # Re-save with same links
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [
                    {
                        "target": "target-0",
                        "relation": "related_to",
                        "kb": "test",
                        "note": "important",
                    },
                    {"target": "target-1", "relation": "related_to", "kb": "test"},
                ],
            }
        )
        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        noted = [l for l in outlinks if l.get("note") == "important"]
        assert len(noted) == 1

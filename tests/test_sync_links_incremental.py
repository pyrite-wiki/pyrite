"""Tests for incremental link sync optimization."""

import datetime
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.models import Link
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

    def _save_links(self, db, links):
        db.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": links,
            }
        )
        return db.get_outlinks("source", "test", readable_kbs=UNSCOPED)

    @pytest.mark.parametrize(
        "target",
        [
            True,
            False,
            2024,
            1.5,
            datetime.date(2024, 1, 1),
        ],
        ids=["bool_true", "bool_false", "int", "float", "date"],
    )
    def test_non_string_target_does_not_accumulate(self, db_with_entry, target):
        """A hand file's `target` is whatever YAML read, but the column is
        TEXT (#927). Re-indexing must not add a row per pass: before the fix
        the raw value never matched the stored text, so each sync inserted
        another copy."""
        links = [{"target": target, "relation": "related_to", "kb": "test"}]
        self._save_links(db_with_entry, links)
        for _ in range(3):
            outlinks = self._save_links(db_with_entry, links)
            assert len(outlinks) == 1

    def test_int_target_and_its_string_twin_are_one_link(self, db_with_entry):
        """`target: 2024` and `target: '2024'` are the same stored target, so
        they are one link, and re-indexing stays at one rather than growing
        2, 3, 4 (#927)."""
        links = [
            {"target": 2024, "relation": "related_to", "kb": "test"},
            {"target": "2024", "relation": "related_to", "kb": "test"},
        ]
        outlinks = self._save_links(db_with_entry, links)
        assert [link["id"] for link in outlinks] == ["2024"]
        for _ in range(3):
            assert len(self._save_links(db_with_entry, links)) == 1

    def test_relation_change_on_non_string_target_keeps_one_row(self, db_with_entry):
        """Changing the relation of a non-string target replaces the row
        rather than adding beside it."""
        self._save_links(db_with_entry, [{"target": 2024, "relation": "related_to", "kb": "test"}])
        outlinks = self._save_links(
            db_with_entry, [{"target": 2024, "relation": "depends_on", "kb": "test"}]
        )
        assert len(outlinks) == 1
        assert outlinks[0]["relation"] == "depends_on"

    def test_removing_non_string_link_removes_the_row(self, db_with_entry):
        """A non-string target is still removable, spares included."""
        links = [
            {"target": 2024, "relation": "related_to", "kb": "test"},
            {"target": "2024", "relation": "related_to", "kb": "test"},
        ]
        self._save_links(db_with_entry, links)
        assert self._save_links(db_with_entry, []) == []

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


# ---------------------------------------------------------------------------
# #927: the link key, through `pyrite index sync` over a hand-written file.
#
# The tests above drive the storage API, which hands _sync_links the link
# dict directly. A hand file is different: YAML decides the type of `target`,
# so `target: 2024` arrives as an int and `target: '2024'` as a quoted string,
# while the column is TEXT. The storage API can therefore look healthy while
# every `index sync` grows the link rows.
# ---------------------------------------------------------------------------


@pytest.fixture
def hand_file_kb(tmp_path):
    """A KB on disk with one hand-written entry, indexed the way `pyrite index
    sync` and `pyrite update` do: through IndexManager over real files."""
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    kb = KBConfig(name="test", path=kb_path, kb_type="standard")
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(tmp_path / "index.db")
    db.register_kb("test", "standard", str(kb_path))
    yield db, kb_path, config
    db.close()


def _write_hand_file(kb_path: Path, links_yaml: str) -> None:
    (kb_path / "h.md").write_text(
        f"---\ntitle: H\nlinks:\n{links_yaml}---\n\nBody.\n", encoding="utf-8"
    )


class TestHandFileLinkTargets:
    """Re-indexing a hand file whose links did not change must leave its link
    rows alone (#927)."""

    def _link_rows(self, db, entry_id="h"):
        return db.session.query(Link).filter_by(source_id=entry_id, source_kb="test").all()

    @pytest.mark.parametrize(
        ("label", "target_yaml"),
        [
            ("int", "target: 2024\n"),
            ("quoted_int_twin", "target: '2024'\n"),
            ("float", "target: 1.5\n"),
            ("bool", "target: true\n"),
            ("date", "target: 2024-01-01\n"),
        ],
    )
    def test_resync_does_not_grow_or_rewrite_the_row(self, hand_file_kb, label, target_yaml):
        """One non-string target: the count was already stable before the fix,
        but only because each sync deleted the row and inserted a new one, so
        the row identity changed every pass. The fix matches it in place."""
        db, kb_path, config = hand_file_kb
        _write_hand_file(kb_path, f"- {target_yaml}  relation: related_to\n")

        IndexManager(db, config).index_kb("test")
        first = self._link_rows(db)
        assert len(first) == 1
        first_id = first[0].id

        IndexManager(db, config).index_kb("test")
        after = self._link_rows(db)
        assert len(after) == 1
        assert after[0].id == first_id, f"{label}: row was rewritten instead of matched"

    def test_int_target_and_quoted_twin_do_not_accumulate(self, hand_file_kb):
        """The reported case: `target: 2024` beside `target: '2024'`. Both are
        stored as '2024', so they are one row, and re-indexing stays at one
        instead of 2, 3, 4, 5 (#927)."""
        db, kb_path, config = hand_file_kb
        _write_hand_file(
            kb_path,
            "- target: 2024\n  relation: related_to\n- target: '2024'\n  relation: related_to\n",
        )

        counts = []
        for _ in range(3):
            IndexManager(db, config).index_kb("test")
            counts.append(len(self._link_rows(db)))

        assert counts == [1, 1, 1], f"link rows grew across re-index: {counts}"

    @pytest.mark.parametrize(
        ("label", "links_yaml"),
        [
            ("bool_relation", "- target: fresh\n  relation: true\n"),
            ("int_kb", "- target: fresh\n  relation: related_to\n  kb: 2024\n"),
            ("float_relation", "- target: fresh\n  relation: 1.5\n"),
            ("int_note", "- target: fresh\n  relation: related_to\n  note: 5\n"),
        ],
        ids=lambda v: v if isinstance(v, str) else "",
    )
    def test_a_non_string_relation_kb_or_note_also_matches_in_place(
        self, hand_file_kb, label, links_yaml
    ):
        """The other two key parts and the note are YAML-typed too. Before they
        were coerced, `relation: true` and `kb: 2024` churned the row on every
        sync and `note: 5` was rewritten every pass (#927 review)."""
        db, kb_path, config = hand_file_kb
        _write_hand_file(kb_path, links_yaml)

        IndexManager(db, config).index_kb("test")
        first = self._link_rows(db)
        assert len(first) == 1, label
        first_id = first[0].id
        first_note = first[0].note

        IndexManager(db, config).index_kb("test")
        after = self._link_rows(db)
        assert len(after) == 1, f"{label}: row count changed"
        assert after[0].id == first_id, f"{label}: row was rewritten instead of matched"
        assert after[0].note == first_note, f"{label}: note was rewritten"

    def test_int_relation_and_its_quoted_twin_collapse_to_one_link(self, hand_file_kb):
        """`relation: 1` beside `relation: '1'` is one key once stored, so one
        row, the same way two spellings of a target collapse (#927 review)."""
        db, kb_path, config = hand_file_kb
        _write_hand_file(
            kb_path,
            "- target: fresh\n  relation: 1\n- target: fresh\n  relation: '1'\n",
        )

        IndexManager(db, config).index_kb("test")
        assert len(self._link_rows(db)) == 1
        first_id = self._link_rows(db)[0].id

        IndexManager(db, config).index_kb("test")
        rows = self._link_rows(db)
        assert len(rows) == 1, f"link rows grew across re-index: {len(rows)}"
        assert rows[0].id == first_id

    def test_removing_the_link_removes_every_row(self, hand_file_kb):
        """The spares a duplicate key leaves behind go when the link does."""
        db, kb_path, config = hand_file_kb
        _write_hand_file(
            kb_path,
            "- target: 2024\n  relation: related_to\n- target: '2024'\n  relation: related_to\n",
        )
        IndexManager(db, config).index_kb("test")
        assert self._link_rows(db)

        _write_hand_file(kb_path, "")
        IndexManager(db, config).index_kb("test")
        assert self._link_rows(db) == []

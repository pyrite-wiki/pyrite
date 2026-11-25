"""Tests for typed object references (entry_ref table)."""

import tempfile
from pathlib import Path

import pytest

from pyrite.storage.database import PyriteDB


class TestObjectRefs:
    """Tests for object-ref field indexing and reverse lookup."""

    @pytest.fixture
    def db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            db = PyriteDB(db_path)
            yield db
            db.close()

    def test_entry_ref_table_exists(self, db):
        """entry_ref table should be created."""
        row = db._raw_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='entry_ref'"
        ).fetchone()
        assert row is not None

    def test_sync_entry_refs(self, db):
        """Upserting entry with _refs populates entry_ref table."""
        db.register_kb("test-kb", "generic", "/tmp/test", "")
        entry_data = {
            "id": "meeting-1",
            "kb_name": "test-kb",
            "entry_type": "meeting",
            "title": "Sprint Review",
            "body": "Discussion notes",
            "tags": [],
            "sources": [],
            "links": [],
            "_refs": [
                {"target_id": "alice", "field_name": "organizer", "target_type": "person"},
                {"target_id": "bob", "field_name": "attendees", "target_type": "person"},
            ],
        }
        db.upsert_entry(entry_data)

        rows = db._raw_conn.execute(
            "SELECT * FROM entry_ref WHERE source_id = 'meeting-1' AND source_kb = 'test-kb'"
        ).fetchall()
        assert len(rows) == 2

    def test_refs_to_reverse_lookup(self, db):
        """get_refs_to returns entries referencing a target."""
        db.register_kb("test-kb", "generic", "/tmp/test", "")

        # Create target entry
        db.upsert_entry(
            {
                "id": "alice",
                "kb_name": "test-kb",
                "entry_type": "person",
                "title": "Alice",
                "body": "",
                "tags": [],
                "sources": [],
                "links": [],
            }
        )

        # Create source entry with ref to alice
        db.upsert_entry(
            {
                "id": "meeting-1",
                "kb_name": "test-kb",
                "entry_type": "meeting",
                "title": "Sprint Review",
                "body": "",
                "tags": [],
                "sources": [],
                "links": [],
                "_refs": [
                    {"target_id": "alice", "field_name": "organizer", "target_type": "person"}
                ],
            }
        )

        refs = db.get_refs_to("alice", "test-kb")
        assert len(refs) == 1
        assert refs[0]["id"] == "meeting-1"
        assert refs[0]["field_name"] == "organizer"

    def test_refs_from_lookup(self, db):
        """get_refs_from returns entries referenced by a source."""
        db.register_kb("test-kb", "generic", "/tmp/test", "")

        db.upsert_entry(
            {
                "id": "alice",
                "kb_name": "test-kb",
                "entry_type": "person",
                "title": "Alice",
                "body": "",
                "tags": [],
                "sources": [],
                "links": [],
            }
        )
        db.upsert_entry(
            {
                "id": "meeting-1",
                "kb_name": "test-kb",
                "entry_type": "meeting",
                "title": "Sprint Review",
                "body": "",
                "tags": [],
                "sources": [],
                "links": [],
                "_refs": [
                    {"target_id": "alice", "field_name": "organizer", "target_type": "person"}
                ],
            }
        )

        refs = db.get_refs_from("meeting-1", "test-kb")
        assert len(refs) == 1
        assert refs[0]["id"] == "alice"
        assert refs[0]["field_name"] == "organizer"

    def test_refs_replaced_on_reindex(self, db):
        """Re-upserting entry replaces old refs."""
        db.register_kb("test-kb", "generic", "/tmp/test", "")
        db.upsert_entry(
            {
                "id": "meeting-1",
                "kb_name": "test-kb",
                "entry_type": "meeting",
                "title": "Sprint",
                "body": "",
                "tags": [],
                "sources": [],
                "links": [],
                "_refs": [
                    {"target_id": "alice", "field_name": "organizer", "target_type": "person"}
                ],
            }
        )

        # Re-upsert with different refs
        db.upsert_entry(
            {
                "id": "meeting-1",
                "kb_name": "test-kb",
                "entry_type": "meeting",
                "title": "Sprint",
                "body": "",
                "tags": [],
                "sources": [],
                "links": [],
                "_refs": [{"target_id": "bob", "field_name": "organizer", "target_type": "person"}],
            }
        )

        rows = db._raw_conn.execute(
            "SELECT target_id FROM entry_ref WHERE source_id = 'meeting-1'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["target_id"] == "bob"

    def test_refs_deleted_with_entry(self, db):
        """Deleting entry cascades to entry_ref."""
        db.register_kb("test-kb", "generic", "/tmp/test", "")
        db.upsert_entry(
            {
                "id": "meeting-1",
                "kb_name": "test-kb",
                "entry_type": "meeting",
                "title": "Sprint",
                "body": "",
                "tags": [],
                "sources": [],
                "links": [],
                "_refs": [
                    {"target_id": "alice", "field_name": "organizer", "target_type": "person"}
                ],
            }
        )
        db.delete_entry("meeting-1", "test-kb")

        rows = db._raw_conn.execute(
            "SELECT * FROM entry_ref WHERE source_id = 'meeting-1'"
        ).fetchall()
        assert len(rows) == 0

    def test_no_refs_when_none_provided(self, db):
        """Entry without _refs doesn't create entry_ref rows."""
        db.register_kb("test-kb", "generic", "/tmp/test", "")
        db.upsert_entry(
            {
                "id": "note-1",
                "kb_name": "test-kb",
                "entry_type": "note",
                "title": "Just a note",
                "body": "",
                "tags": [],
                "sources": [],
                "links": [],
            }
        )

        rows = db._raw_conn.execute("SELECT * FROM entry_ref WHERE source_id = 'note-1'").fetchall()
        assert len(rows) == 0


class TestReadBackWriteKeepsDerivedRows:
    """A write back from a read keeps the entry's derived rows (#1733).

    The dedup merge writes back a ``get_entry`` dict: it carries no
    ``_refs`` / ``_blocks`` / ``_edge_endpoints`` and no ``_kb_root``, so it
    cannot say what the entry's derived rows are. Dropping them until the
    next index silently loses them (design principle 2); links already keep
    theirs on such a write (#1723). A write made from the file carries
    ``_kb_root`` and replaces the rows, an absent key then meaning "none
    now"; a write that carries a key replaces from the key.
    """

    KB = "test-kb"
    REF = {"target_id": "alice", "field_name": "organizer", "target_type": "person"}
    BLOCK = {
        "block_id": "b1",
        "heading": "H",
        "content": "Text",
        "position": 0,
        "block_type": "paragraph",
    }
    ENDPOINT = {
        "role": "source",
        "field_name": "employee",
        "endpoint_id": "alice",
        "endpoint_kb": "test-kb",
        "edge_type": "employment",
    }

    @pytest.fixture
    def db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PyriteDB(Path(tmpdir) / "test.db")
            db.register_kb(self.KB, "generic", "/tmp/test", "")
            yield db
            db.close()

    def _write_from_file(self, db, entry_id, **derived):
        """A file-made row: carries ``_kb_root`` and the derived keys given."""
        db.upsert_entry(
            {
                "id": entry_id,
                "kb_name": self.KB,
                "entry_type": "meeting",
                "title": "Sprint Review",
                "body": "Discussion notes",
                "tags": [],
                "sources": [],
                "links": [],
                "_kb_root": "/tmp/test",
                **derived,
            }
        )

    def _write_back_from_read(self, db, entry_id):
        """The dedup shape: the ``get_entry`` dict, written back as it came."""
        row = db.get_entry(entry_id, self.KB)
        assert row is not None and "_refs" not in row and "_kb_root" not in row
        row["title"] = "Sprint Review (merged)"
        db.upsert_entry(row)

    def _counts(self, db, entry_id):
        refs = db._raw_conn.execute(
            "SELECT COUNT(*) FROM entry_ref WHERE source_id = ? AND source_kb = ?",
            (entry_id, self.KB),
        ).fetchone()[0]
        blocks = db._raw_conn.execute(
            'SELECT COUNT(*) FROM "block" WHERE entry_id = ? AND kb_name = ?',
            (entry_id, self.KB),
        ).fetchone()[0]
        endpoints = db._raw_conn.execute(
            "SELECT COUNT(*) FROM edge_endpoint WHERE edge_entry_id = ? AND edge_entry_kb = ?",
            (entry_id, self.KB),
        ).fetchone()[0]
        return refs, blocks, endpoints

    @pytest.mark.parametrize(
        ("derived", "want"),
        [
            ({"_refs": [REF]}, (1, 0, 0)),
            ({"_blocks": [BLOCK]}, (0, 1, 0)),
            ({"_edge_endpoints": [ENDPOINT]}, (0, 0, 1)),
            ({"_refs": [REF], "_blocks": [BLOCK], "_edge_endpoints": [ENDPOINT]}, (1, 1, 1)),
            ({}, (0, 0, 0)),
        ],
    )
    def test_read_back_write_keeps_each_row_shape(self, db, derived, want):
        self._write_from_file(db, "meeting-1", **derived)
        assert self._counts(db, "meeting-1") == want
        self._write_back_from_read(db, "meeting-1")
        assert self._counts(db, "meeting-1") == want

    def test_file_made_write_with_none_clears_the_rows(self, db):
        """A file-made row whose entry no longer has refs clears them: the
        key is absent (``_entry_to_dict`` sets it only when non-empty) but
        ``_kb_root`` says the write read the file."""
        self._write_from_file(
            db, "meeting-1", _refs=[self.REF], _blocks=[self.BLOCK], _edge_endpoints=[self.ENDPOINT]
        )
        assert self._counts(db, "meeting-1") == (1, 1, 1)
        self._write_from_file(db, "meeting-1")
        assert self._counts(db, "meeting-1") == (0, 0, 0)

    def test_a_write_carrying_the_key_replaces_from_the_key(self, db):
        """No ``_kb_root``, but the row itself carries ``_refs``: the rows
        are replaced from it, here emptied."""
        self._write_from_file(db, "meeting-1", _refs=[self.REF])
        row = db.get_entry("meeting-1", self.KB)
        row["_refs"] = []
        db.upsert_entry(row)
        assert self._counts(db, "meeting-1") == (0, 0, 0)

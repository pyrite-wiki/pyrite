"""Tests for OverlaySearchBackend and WorktreeDB."""

import tempfile
from pathlib import Path

import pytest

from pyrite.storage.backends.overlay_backend import OverlaySearchBackend, WorktreeDB
from pyrite.storage.database import PyriteDB


def _make_db(path: Path) -> PyriteDB:
    """Create a PyriteDB at the given path."""
    return PyriteDB(path)


def _ensure_kb(db: PyriteDB, kb_name: str) -> None:
    """Register a KB if not already present."""
    db._raw_conn.execute(
        "INSERT OR IGNORE INTO kb (name, path, kb_type) VALUES (?, ?, ?)",
        (kb_name, f"/test/{kb_name}", "generic"),
    )
    db._raw_conn.commit()


def _insert_entry(db: PyriteDB, entry_id: str, kb_name: str, title: str, **extra):
    """Insert an entry directly into the backend."""
    _ensure_kb(db, kb_name)
    entry_data = {
        "id": entry_id,
        "kb_name": kb_name,
        "entry_type": extra.get("entry_type", "note"),
        "title": title,
        "body": extra.get("body", ""),
        "summary": "",
        "file_path": extra.get("file_path", f"/test/{entry_id}.md"),
        "date": extra.get("date"),
        "importance": extra.get("importance", 5),
        "status": extra.get("status"),
        "location": None,
        "assignee": None,
        "assigned_at": None,
        "priority": None,
        "due_date": None,
        "start_date": None,
        "end_date": None,
        "coordinates": None,
        "lifecycle": "active",
        "metadata": extra.get("metadata", "{}"),
        "tags": extra.get("tags", []),
        "sources": [],
        "links": [],
        "entry_refs": [],
        "blocks": [],
        "edge_endpoints": [],
        "created_at": extra.get("created_at", "2026-01-01T00:00:00"),
        "updated_at": extra.get("updated_at", "2026-01-01T00:00:00"),
        "created_by": None,
        "modified_by": None,
    }
    db._backend.upsert_entry(entry_data)


class TestOverlayGetEntry:
    def test_diff_wins_on_collision(self, tmp_path):
        main_db = _make_db(tmp_path / "main.db")
        diff_db = _make_db(tmp_path / "diff.db")
        try:
            _insert_entry(main_db, "e1", "test", "Main Version")
            _insert_entry(diff_db, "e1", "test", "Diff Version")

            overlay = OverlaySearchBackend(main_db._backend, diff_db._backend)
            entry = overlay.get_entry("e1", "test")
            assert entry is not None
            assert entry["title"] == "Diff Version"
        finally:
            main_db.close()
            diff_db.close()

    def test_falls_back_to_main(self, tmp_path):
        main_db = _make_db(tmp_path / "main.db")
        diff_db = _make_db(tmp_path / "diff.db")
        try:
            _insert_entry(main_db, "e1", "test", "Main Only")

            overlay = OverlaySearchBackend(main_db._backend, diff_db._backend)
            entry = overlay.get_entry("e1", "test")
            assert entry is not None
            assert entry["title"] == "Main Only"
        finally:
            main_db.close()
            diff_db.close()

    def test_diff_only_entry(self, tmp_path):
        main_db = _make_db(tmp_path / "main.db")
        diff_db = _make_db(tmp_path / "diff.db")
        try:
            _insert_entry(diff_db, "new-e", "test", "New in Diff")

            overlay = OverlaySearchBackend(main_db._backend, diff_db._backend)
            entry = overlay.get_entry("new-e", "test")
            assert entry is not None
            assert entry["title"] == "New in Diff"
        finally:
            main_db.close()
            diff_db.close()

    def test_not_found(self, tmp_path):
        main_db = _make_db(tmp_path / "main.db")
        diff_db = _make_db(tmp_path / "diff.db")
        try:
            overlay = OverlaySearchBackend(main_db._backend, diff_db._backend)
            assert overlay.get_entry("nonexistent", "test") is None
        finally:
            main_db.close()
            diff_db.close()


class TestOverlayListEntries:
    def test_merge_with_diff_override(self, tmp_path):
        main_db = _make_db(tmp_path / "main.db")
        diff_db = _make_db(tmp_path / "diff.db")
        try:
            _insert_entry(main_db, "e1", "test", "Main E1")
            _insert_entry(main_db, "e2", "test", "Main E2")
            _insert_entry(diff_db, "e1", "test", "Diff E1")  # override
            _insert_entry(diff_db, "e3", "test", "Diff E3")  # new

            overlay = OverlaySearchBackend(main_db._backend, diff_db._backend)
            results = overlay.list_entries(kb_name="test", limit=10000)

            titles = {r["title"] for r in results}
            assert "Diff E1" in titles  # diff wins
            assert "Main E1" not in titles  # overridden
            assert "Main E2" in titles  # from main
            assert "Diff E3" in titles  # new in diff
            assert len(results) == 3
        finally:
            main_db.close()
            diff_db.close()


class TestOverlayCountEntries:
    def test_count_includes_new_diff_entries(self, tmp_path):
        main_db = _make_db(tmp_path / "main.db")
        diff_db = _make_db(tmp_path / "diff.db")
        try:
            main_db._raw_conn.execute(
                "INSERT OR IGNORE INTO kb (name, path, kb_type) VALUES (?, ?, ?)",
                ("test", "/test", "generic"),
            )
            main_db._raw_conn.commit()
            diff_db._raw_conn.execute(
                "INSERT OR IGNORE INTO kb (name, path, kb_type) VALUES (?, ?, ?)",
                ("test", "/test", "generic"),
            )
            diff_db._raw_conn.commit()

            _insert_entry(main_db, "e1", "test", "Main E1")
            _insert_entry(main_db, "e2", "test", "Main E2")
            _insert_entry(diff_db, "e3", "test", "Diff E3")  # new

            overlay = OverlaySearchBackend(main_db._backend, diff_db._backend)
            count = overlay.count_entries(kb_name="test")
            assert count == 3
        finally:
            main_db.close()
            diff_db.close()


class TestOverlaySearch:
    def test_search_merges_results(self, tmp_path):
        main_db = _make_db(tmp_path / "main.db")
        diff_db = _make_db(tmp_path / "diff.db")
        try:
            main_db._raw_conn.execute(
                "INSERT OR IGNORE INTO kb (name, path, kb_type) VALUES (?, ?, ?)",
                ("test", "/test", "generic"),
            )
            main_db._raw_conn.commit()
            diff_db._raw_conn.execute(
                "INSERT OR IGNORE INTO kb (name, path, kb_type) VALUES (?, ?, ?)",
                ("test", "/test", "generic"),
            )
            diff_db._raw_conn.commit()

            _insert_entry(
                main_db, "e1", "test", "Research on climate", body="Climate research entry"
            )
            _insert_entry(diff_db, "e2", "test", "Research on energy", body="Energy research entry")

            overlay = OverlaySearchBackend(main_db._backend, diff_db._backend)
            results = overlay.search("research", kb_name="test")
            ids = {r["id"] for r in results}
            assert "e1" in ids
            assert "e2" in ids
        finally:
            main_db.close()
            diff_db.close()


class TestOverlayWrite:
    def test_upsert_goes_to_diff_only(self, tmp_path):
        main_db = _make_db(tmp_path / "main.db")
        diff_db = _make_db(tmp_path / "diff.db")
        try:
            _ensure_kb(diff_db, "test")
            overlay = OverlaySearchBackend(main_db._backend, diff_db._backend)
            overlay.upsert_entry(
                {
                    "id": "new",
                    "kb_name": "test",
                    "entry_type": "note",
                    "title": "Written via overlay",
                    "body": "",
                    "summary": "",
                    "file_path": "/test/new.md",
                    "date": None,
                    "importance": 5,
                    "status": None,
                    "location": None,
                    "assignee": None,
                    "assigned_at": None,
                    "priority": None,
                    "due_date": None,
                    "start_date": None,
                    "end_date": None,
                    "coordinates": None,
                    "lifecycle": "active",
                    "metadata": "{}",
                    "tags": [],
                    "sources": [],
                    "links": [],
                    "entry_refs": [],
                    "blocks": [],
                    "edge_endpoints": [],
                    "created_at": "2026-01-01",
                    "updated_at": "2026-01-01",
                    "created_by": None,
                    "modified_by": None,
                }
            )

            # Should be in diff
            assert diff_db._backend.get_entry("new", "test") is not None
            # Should NOT be in main
            assert main_db._backend.get_entry("new", "test") is None
            # Should be visible through overlay
            assert overlay.get_entry("new", "test") is not None
        finally:
            main_db.close()
            diff_db.close()


class TestOverlayLinks:
    @pytest.mark.parametrize("direction", ["backlinks", "outlinks"])
    @pytest.mark.parametrize(
        "relations",
        [("supports", "contradicts"), ("custom_one", "custom_two")],
    )
    @pytest.mark.parametrize(
        ("main_relations", "diff_relations", "expected"),
        [
            pytest.param(
                (0, 1),
                (),
                (0, 1),
                marks=pytest.mark.control(reason="An empty overlay already preserves main rows"),
                id="main-only",
            ),
            pytest.param((0,), (1,), (0, 1), id="split-between-stores"),
            pytest.param((), (0, 1), (0, 1), id="both-in-overlay"),
            pytest.param(
                (0,),
                (0,),
                (0,),
                marks=pytest.mark.control(reason="The same link already uses the overlay row"),
                id="same-link-diff-wins",
            ),
            pytest.param(
                (0,),
                (),
                (0,),
                marks=pytest.mark.control(
                    reason="Overlay reads do not hide links removed from diff"
                ),
                id="removed-from-overlay",
            ),
        ],
    )
    def test_relation_identity(
        self, tmp_path, direction, relations, main_relations, diff_relations, expected
    ):
        main_db = _make_db(tmp_path / "main.db")
        diff_db = _make_db(tmp_path / "diff.db")
        try:
            for db, title in [(main_db, "Main"), (diff_db, "Overlay")]:
                _insert_entry(db, "source", "test", f"{title} source")
                _insert_entry(db, "target", "test", f"{title} target")

            for db, indices, note in [
                (main_db, main_relations, "main"),
                (diff_db, diff_relations, "overlay"),
            ]:
                db._backend._sync_links(
                    "source",
                    "test",
                    [{"target": "target", "relation": relations[i], "note": note} for i in indices],
                )
                db.session.commit()

            if not diff_relations:
                # Model a removal in the diff: it has the edited entry but no link.
                diff_db._backend._sync_links(
                    "source", "test", [{"target": "target", "relation": relations[0]}]
                )
                diff_db.session.commit()
                diff_db._backend._sync_links("source", "test", [])
                diff_db.session.commit()

            overlay = OverlaySearchBackend(main_db._backend, diff_db._backend)
            if direction == "backlinks":
                rows = overlay.get_backlinks("target", "test", readable_kbs=None)
                relation_field = "forward_relation"
                other_end = "source"
            else:
                rows = overlay.get_outlinks("source", "test", readable_kbs=None)
                relation_field = "relation"
                other_end = "target"

            assert len(rows) == len(expected)
            assert {row[relation_field] for row in rows} == {relations[i] for i in expected}
            assert {(row["id"], row["kb_name"]) for row in rows} == {(other_end, "test")}
            for row in rows:
                index = relations.index(row[relation_field])
                assert row["note"] == ("overlay" if index in diff_relations else "main")
        finally:
            main_db.close()
            diff_db.close()


class TestOverlayMergeHelper:
    def test_empty_diff_returns_main(self):
        main = [{"id": "a", "kb_name": "t", "title": "A"}]
        result = OverlaySearchBackend._merge_entry_lists(main, [])
        assert result == main

    def test_diff_wins_on_id(self):
        main = [{"id": "a", "kb_name": "t", "title": "Main A"}]
        diff = [{"id": "a", "kb_name": "t", "title": "Diff A"}]
        result = OverlaySearchBackend._merge_entry_lists(main, diff)
        assert len(result) == 1
        assert result[0]["title"] == "Diff A"

    def test_new_diff_entries_appended(self):
        main = [{"id": "a", "kb_name": "t", "title": "A"}]
        diff = [{"id": "b", "kb_name": "t", "title": "B"}]
        result = OverlaySearchBackend._merge_entry_lists(main, diff)
        assert len(result) == 2
        ids = {r["id"] for r in result}
        assert ids == {"a", "b"}


class TestWorktreeDB:
    def test_get_entry_uses_overlay(self, tmp_path):
        main_db = _make_db(tmp_path / "main.db")
        diff_db = _make_db(tmp_path / "diff.db")
        try:
            _insert_entry(main_db, "e1", "test", "Main")
            _insert_entry(diff_db, "e1", "test", "Diff")

            wt_db = WorktreeDB(main_db, diff_db)
            entry = wt_db.get_entry("e1", "test")
            assert entry["title"] == "Diff"
        finally:
            main_db.close()
            diff_db.close()

    def test_getattr_delegates_to_main(self, tmp_path):
        main_db = _make_db(tmp_path / "main.db")
        diff_db = _make_db(tmp_path / "diff.db")
        try:
            wt_db = WorktreeDB(main_db, diff_db)
            # db_path is on the main DB
            assert wt_db.db_path == main_db.db_path
        finally:
            main_db.close()
            diff_db.close()

    def test_vec_available_from_main(self, tmp_path):
        main_db = _make_db(tmp_path / "main.db")
        diff_db = _make_db(tmp_path / "diff.db")
        try:
            wt_db = WorktreeDB(main_db, diff_db)
            assert wt_db.vec_available == main_db.vec_available
        finally:
            main_db.close()
            diff_db.close()

"""Tests for unified database connection model: transaction(), execute_sql(), deprecation."""

import tempfile
import warnings
from pathlib import Path

import pytest

from pyrite.storage.database import PyriteDB


@pytest.fixture
def db():
    """Fresh PyriteDB for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = PyriteDB(Path(tmpdir) / "test.db")
        yield db
        db.close()


class TestTransaction:
    """Test the transaction() context manager."""

    def test_transaction_commits_on_success(self, db):
        """Successful block commits automatically."""
        from pyrite.storage.models import Setting

        with db.transaction() as session:
            session.add(Setting(key="tx-test", value="hello"))

        result = db.get_setting("tx-test")
        assert result == "hello"

    def test_transaction_rollback_on_error(self, db):
        """Exception inside block triggers rollback."""
        from pyrite.storage.models import Setting

        with pytest.raises(ValueError):
            with db.transaction() as session:
                session.add(Setting(key="fail-key", value="fail-val"))
                session.flush()
                raise ValueError("boom")

        result = db.get_setting("fail-key")
        assert result is None

    def test_transaction_yields_session(self, db):
        """transaction() yields the ORM session."""
        with db.transaction() as session:
            assert session is db.session


class TestExecuteSQL:
    """Test the execute_sql() raw SQL helper."""

    def test_execute_sql_read(self, db):
        """execute_sql returns list of dicts for SELECT."""
        db.set_setting("sql-test", "world")
        rows = db.execute_sql("SELECT key, value FROM setting WHERE key = :k", {"k": "sql-test"})
        assert len(rows) == 1
        assert rows[0]["key"] == "sql-test"
        assert rows[0]["value"] == "world"

    def test_execute_sql_empty_result(self, db):
        """execute_sql returns empty list for no matches."""
        rows = db.execute_sql("SELECT key FROM setting WHERE key = :k", {"k": "nope"})
        assert rows == []

    def test_execute_sql_no_params(self, db):
        """execute_sql works without params."""
        db.set_setting("a", "1")
        db.set_setting("b", "2")
        rows = db.execute_sql("SELECT key FROM setting ORDER BY key")
        keys = [r["key"] for r in rows]
        assert "a" in keys
        assert "b" in keys


class TestConnDeprecation:
    """Test that db.conn raises DeprecationWarning."""

    def test_conn_property_warns(self, db):
        """Accessing db.conn emits DeprecationWarning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = db.conn
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "deprecated" in str(w[0].message).lower()
            assert "removed in 0.27" in str(w[0].message)

    def test_conn_still_works(self, db):
        """db.conn still returns a usable connection for backward compat."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            conn = db.conn
            row = conn.execute("SELECT 1 as val").fetchone()
            assert row["val"] == 1


class TestSingleConnection:
    """Test that ORM writes are visible to raw SQL reads."""

    def test_orm_write_visible_to_raw_read(self, db):
        """ORM writes (via session) should be visible to _raw_conn reads."""
        db.register_kb("test-kb", "generic", "/tmp/test")
        db.upsert_entry(
            {
                "id": "e1",
                "kb_name": "test-kb",
                "entry_type": "note",
                "title": "Test Note",
                "body": "Some body text",
                "tags": ["alpha", "beta"],
            }
        )

        # Read via raw connection (used by search queries)
        row = db._raw_conn.execute(
            "SELECT title FROM entry WHERE id = ? AND kb_name = ?", ("e1", "test-kb")
        ).fetchone()
        assert row is not None
        assert row["title"] == "Test Note"

    def test_fts5_works_after_orm_write(self, db):
        """FTS5 triggers should fire after ORM writes."""
        db.register_kb("test-kb", "generic", "/tmp/test")
        db.upsert_entry(
            {
                "id": "fts-test",
                "kb_name": "test-kb",
                "entry_type": "note",
                "title": "Quantum Computing Research",
                "body": "Quantum entanglement experiments",
                "tags": [],
            }
        )

        # FTS5 search via raw connection
        results = db.search("quantum", kb_name="test-kb")
        assert len(results) >= 1
        assert any(r["id"] == "fts-test" for r in results)

"""Tests for PyriteDB.merge_registered_kbs — the consolidated DB-registered
KB merge, replacing three near-identical copies (cli/context.py,
server/mcp_server.py, server/api.py) that each ran the same raw SQL and
handled failure differently: CLI logged at debug, MCP and REST swallowed
it entirely with a bare `except Exception: pass`.

fail-open-exception-sweep site #1: a failed SELECT here means
DB-registered KBs silently vanish from whichever surface hit the error —
the dual-registry class again. The merge must log at warning+ on failure
(not silently disappear the KBs), matching the "fix" prescription: narrow
the except to expected types, log at warning+ with a WHY comment.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from sqlalchemy.exc import OperationalError

from pyrite.config import PyriteConfig, Settings
from pyrite.storage.database import PyriteDB


@pytest.fixture
def db_with_user_kb():
    """A PyriteDB with one KB registered via the DB (source='user'),
    plus a YAML config that knows nothing about it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"
        kb_path = tmpdir / "some-kb"
        kb_path.mkdir()

        db = PyriteDB(db_path)
        db.register_kb(
            name="some-kb",
            kb_type="generic",
            path=str(kb_path),
            description="DB-registered KB",
            source="user",
        )

        config = PyriteConfig(knowledge_bases=[], settings=Settings(index_path=db_path))
        yield {"db": db, "config": config, "kb_path": kb_path}
        db.close()


class TestMergeRegisteredKBs:
    def test_a_row_with_no_source_is_a_user_row(self, db_with_user_kb):
        """A `kb` row whose `source` is NULL (written before the column had a
        default, or by hand) is a user row everywhere: the registry reads it
        as `source or "user"` and the resolver finds it, so the merge that
        gives a CLI its registry-only KBs must take it too (#965 round 2)."""
        db = db_with_user_kb["db"]
        config = db_with_user_kb["config"]
        kb_path = db_with_user_kb["kb_path"].parent / "unsourced-kb"
        kb_path.mkdir()
        db._raw_conn.execute(
            "INSERT INTO kb (name, kb_type, path, source) VALUES (?, ?, ?, NULL)",
            ("unsourced-kb", "generic", str(kb_path)),
        )
        db._raw_conn.commit()

        assert db.merge_registered_kbs(config) == 2
        assert config.get_kb("unsourced-kb") is not None

    def test_merges_db_registered_kb_into_config(self, db_with_user_kb):
        db = db_with_user_kb["db"]
        config = db_with_user_kb["config"]

        db.merge_registered_kbs(config)

        assert config.get_kb("some-kb") is not None
        assert "some-kb" in [kb.name for kb in config.all_kbs()]

    def test_merges_default_role_from_db(self, db_with_user_kb):
        """default_role is a real DB column (per-KB public/private access
        control: the access policy's anonymous rule reads it) but
        the raw SQL SELECT here never fetched it, so a DB-registered KB's
        default_role was silently dropped on every merge -- a KB added via
        `pyrite kb add` with default_role='read' could never appear in the
        public sitemap, unlike an equivalent config.yaml KB."""
        db = db_with_user_kb["db"]
        config = db_with_user_kb["config"]
        db.update_kb_default_role("some-kb", "read")

        db.merge_registered_kbs(config)

        kb = config.get_kb("some-kb")
        assert kb is not None
        assert kb.default_role == "read", (
            f"expected default_role='read' to survive the merge, got {kb.default_role!r}"
        )

    def test_no_db_registered_kbs_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / "index.db"
            db = PyriteDB(db_path)
            config = PyriteConfig(knowledge_bases=[], settings=Settings(index_path=db_path))

            db.merge_registered_kbs(config)

            assert config.all_kbs() == []
            db.close()

    def test_query_failure_logs_warning_not_silent(self, db_with_user_kb, caplog):
        """The bug this fixes: a failed SELECT must be visible (warning+
        log with a WHY comment), not a bare `except Exception: pass` that
        makes DB-registered KBs disappear without a trace."""
        import logging

        db = db_with_user_kb["db"]
        config = db_with_user_kb["config"]

        with patch.object(
            db.session,
            "execute",
            side_effect=OperationalError("SELECT ...", {}, Exception("simulated DB failure")),
        ):
            with caplog.at_level(logging.WARNING, logger="pyrite.storage.kb_ops"):
                db.merge_registered_kbs(config)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, (
            f"expected a warning-level log; got {[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )
        assert warnings[0].exc_info is not None, (
            "expected the original exception attached via exc_info"
        )

    def test_query_failure_does_not_raise(self, db_with_user_kb):
        """Startup must not crash if the kb table doesn't exist yet (first
        run) or the query otherwise fails -- degrade gracefully, but
        loudly (see test_query_failure_logs_warning_not_silent)."""
        db = db_with_user_kb["db"]
        config = db_with_user_kb["config"]

        with patch.object(
            db.session,
            "execute",
            side_effect=OperationalError("SELECT ...", {}, Exception("simulated DB failure")),
        ):
            db.merge_registered_kbs(config)  # must not raise

        assert config.all_kbs() == []

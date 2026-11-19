"""The server side of ADR-0035: a write records debt, a sync drains it (#13).

`POST /api/entries` is the surface #13 was reported on. These tests run
against a `TestClient` (in-process ASGI), which is the right surface for
"does the endpoint enqueue and does sync drain" — the *cold-process, no
model loaded* half of the regime cannot be observed in-process, because the
suite's autouse fixture neutralises write-time embedding here; that half is
covered out of process in `tests/test_writes_never_block_on_embedding.py`.

Every write service the app can build is checked, not just the DI default:
a change-set write builds its own `KBService` on
`services.write_target`'s target (`get_kb_service` on a `landing: review` KB). #13's shape was exactly "one construction
site was wired and the others were not", so a test that only exercises
`get_kb_service` would re-create the bug in a new place.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

pytestmark = pytest.mark.api


@pytest.fixture
def app_ctx(tmp_path):
    """An app over a one-KB config, with `auto_embed` on."""
    from pyrite.server.api import create_app, get_config, get_db

    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="t", path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "i.db", auto_embed=True),
    )
    db = PyriteDB(config.settings.index_path)

    app = create_app(config=config)
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    try:
        yield client, config, db
    finally:
        db.close()


def _create(client, title="Kestrel Notes"):
    resp = client.post(
        "/api/entries",
        json={"kb": "t", "title": title, "entry_type": "note", "body": "about falcons"},
    )
    assert resp.status_code in (200, 201), (resp.status_code, resp.text)
    return resp


def _queue(db):
    try:
        return [tuple(r) for r in db._raw_conn.execute("SELECT entry_id, status FROM embed_queue")]
    except Exception:
        return []


class TestPostEntriesRecordsTheDebt:
    def test_the_write_enqueues_and_embed_status_reports_it(self, app_ctx):
        client, _config, db = app_ctx
        assert client.get("/api/index/embed-status").json()["pending"] == 0

        _create(client)

        assert [row[1] for row in _queue(db)] == ["pending"], _queue(db)
        status = client.get("/api/index/embed-status").json()
        assert status["pending"] == 1, status

    def test_the_entry_is_keyword_searchable_straight_away(self, app_ctx):
        client, _config, _db = app_ctx
        _create(client)

        resp = client.get("/api/search", params={"q": "Kestrel", "kb": "t", "mode": "keyword"})
        assert resp.status_code == 200, resp.text
        ids = [r["id"] for r in resp.json()["results"]]
        assert "kestrel-notes" in ids, resp.json()


class TestAutoEmbedOffEnqueuesNothing:
    """ADR-0035 §4 on the server surface."""

    def test_nothing_is_queued_when_the_switch_is_off(self, tmp_path):
        from pyrite.server.api import create_app, get_config, get_db

        kb_path = tmp_path / "kb"
        kb_path.mkdir()
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="t", path=kb_path, kb_type="generic")],
            settings=Settings(index_path=tmp_path / "i.db", auto_embed=False),
        )
        db = PyriteDB(config.settings.index_path)
        app = create_app(config=config)
        app.dependency_overrides[get_config] = lambda: config
        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)

        _create(client)

        assert _queue(db) == []
        assert client.get("/api/index/embed-status").json()["total"] == 0
        db.close()


class TestSyncDrainsTheQueue:
    """ADR-0035 §2: `POST /api/index/sync` drains before it returns."""

    def test_sync_wait_drains_pending_rows(self, app_ctx, monkeypatch):
        client, _config, db = app_ctx
        _create(client)
        assert client.get("/api/index/embed-status").json()["pending"] == 1

        # No model anywhere in this test: the drain's embedding service is
        # stubbed, so what is asserted is the wiring, not the model.
        import pyrite.services.embedding_worker as ew

        monkeypatch.setattr(
            ew.EmbeddingWorker,
            "_get_embedding_svc",
            lambda self: MagicMock(**{"embed_entry.return_value": True}),
        )

        resp = client.post("/api/index/sync?wait=true")
        assert resp.status_code == 200, resp.text

        status = client.get("/api/index/embed-status").json()
        assert status["pending"] == 0, status

    def test_sync_without_a_model_leaves_the_debt_visible(self, app_ctx, monkeypatch):
        """Offline: sync must not lose the rows or fail the request."""
        client, _config, _db = app_ctx
        _create(client)

        import pyrite.services.embedding_worker as ew

        monkeypatch.setattr(ew.EmbeddingWorker, "_get_embedding_svc", lambda self: None)

        resp = client.post("/api/index/sync?wait=true")
        assert resp.status_code == 200, resp.text
        assert client.get("/api/index/embed-status").json()["pending"] == 1


class TestEveryWriteServiceTheAppBuildsEnqueues:
    """#13's shape was a construction site nobody wired. Cover them all."""

    def test_the_resolvers_main_db_service_enqueues_too(self, tmp_path):
        """`WorktreeResolver` on the main DB -- an admin or unauthenticated write.

        This deliberately says *main DB*, not the change-set target
        (`services.write_target`). An earlier version of this test claimed to
        cover the worktree write service while its
        body called `get_read_service`, which with `auth_user=None` hands back
        a plain `PyriteDB` -- so the one test aimed at the worktree path never
        constructed a `WorktreeDB` at all. The genuine worktree shape, where
        writes and `_raw_conn` point at different databases, is covered in
        `tests/test_embed_queue_debt_is_honest.py`.
        """
        from pyrite.server.worktree_resolver import WorktreeResolver

        kb_path = tmp_path / "kb"
        kb_path.mkdir()
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="t", path=kb_path, kb_type="generic")],
            settings=Settings(index_path=tmp_path / "i.db", auto_embed=True),
        )
        db = PyriteDB(config.settings.index_path)
        resolver = WorktreeResolver(config, db, {})

        svc = resolver.get_read_service("t", None)
        assert svc.db is db, "precondition: this is the plain main-DB service"
        svc.create_entry("t", "kestrel-notes", "Kestrel Notes", "note", "about falcons")

        assert [row[0] for row in _queue(db)] == ["kestrel-notes"], _queue(db)
        db.close()

    def test_the_mcp_servers_kb_service_enqueues_too(self, tmp_path):
        """`mcp_server.py` constructs a KBService of its own (ADR-0035 §3:
        the server, MCP and one-shot CLI paths get the same answer)."""
        from pyrite.services.kb_service import KBService

        kb_path = tmp_path / "kb"
        kb_path.mkdir()
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="t", path=kb_path, kb_type="generic")],
            settings=Settings(index_path=tmp_path / "i.db", auto_embed=True),
        )
        db = PyriteDB(config.settings.index_path)
        svc = KBService(config, db)  # the exact call mcp_server.py:182 makes

        svc.create_entry("t", "kestrel-notes", "Kestrel Notes", "note", "about falcons")

        assert [row[0] for row in _queue(db)] == ["kestrel-notes"], _queue(db)
        db.close()

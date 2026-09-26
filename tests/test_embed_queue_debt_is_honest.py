"""The embed queue must never report zero debt for work it did not do.

ADR-0035 moved embedding off the write path. The whole safety argument for
that is *"the cost moved, and the debt is visible"* -- `embed_queue` plus
`GET /api/index/embed-status` are what a user has instead of a write that
blocked until the work was done. A queue that says zero when nothing was
embedded is worse than the bug it replaced: the old synchronous path at least
failed loudly and left a warning in the log.

The cold read of the first pass on #13 found three ways the queue could reach
zero without the embedding happening, all reachable on a default install:

1. **A stock server never drains at all.** `prewarm_embeddings` defaults to
   False, and the startup drain sat inside that branch.
2. **An update freezes its stale vector.** `clear_embedded` retired any row
   whose entry had *a* vector, with no notion of whether it was current, and
   `embed_all(force=False)` then skips exactly those entries.
3. **A worktree write queues into the wrong database.** `WorktreeDB` routes
   writes to the diff DB but forwards `_raw_conn` to main, so the row lands in
   main's queue for an entry only the diff DB has; the drain then "succeeds"
   on an entry it cannot find.

Each class gets a test here that fails without its fix. None of them needs a
real model: what is under test is bookkeeping, so the embedding service is
stubbed and the assertions are about queue rows and which entries got vectors.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.services.access_policy import UNSCOPED


def _config(tmp_path, **settings):
    kb_path = tmp_path / "kb"
    kb_path.mkdir(parents=True, exist_ok=True)
    return PyriteConfig(
        knowledge_bases=[KBConfig(name="t", path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "i.db", **settings),
    )


def _svc(tmp_path, **settings):
    config = _config(tmp_path, **settings)
    return KBService(config, PyriteDB(config.settings.index_path))


def _worktree_db(main_cfg, main_db, diff_dir):
    """The DB shape `WorktreeResolver.get_write_service` hands to a KBService.

    Writes go to a per-user *diff* database; `_raw_conn` -- where `embed_queue`
    lives -- is forwarded to main. Both sides must have the KB registered or
    the entry insert trips a foreign key.
    """
    from pyrite.config import KBType
    from pyrite.storage.backends.overlay_backend import WorktreeDB

    diff_dir.mkdir(parents=True, exist_ok=True)
    diff_db = PyriteDB(diff_dir / "d.db")
    kb_path = str(main_cfg.knowledge_bases[0].path)
    for db in (main_db, diff_db):
        db.register_kb("t", KBType.GENERIC, kb_path)
    return WorktreeDB(main_db, diff_db)


def queue_rows(db):
    try:
        return [
            tuple(r)
            for r in db._raw_conn.execute("SELECT entry_id, kb_name, status FROM embed_queue")
        ]
    except Exception:
        return []


# ===========================================================================
# BLOCKER 1 -- a stock server must drain
# ===========================================================================


@pytest.mark.api
class TestADefaultServerDrainsItsQueue:
    """`prewarm_embeddings` defaults to **False** (`config.py`).

    So gating the startup drain on it means the default server -- `auto_embed:
    true`, `prewarm_embeddings: false` -- enqueues forever and the only drain
    left is `POST /api/index/sync?wait=true`, which is admin-tier. Semantic
    search on a stock install would return `[]` permanently. Before ADR-0035
    that same install embedded on write, so this would be a straight
    functional loss, not a deferral.
    """

    def test_prewarm_is_off_by_default(self):
        """The premise. If this ever flips, re-read the test below."""
        assert Settings(index_path="x").prewarm_embeddings is False

    def test_startup_drains_even_with_prewarm_off(self, tmp_path, monkeypatch):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from pyrite.server.api import create_app, get_config, get_db

        config = _config(tmp_path, auto_embed=True, prewarm_embeddings=False)
        db = PyriteDB(config.settings.index_path)
        KBService(config, db).create_entry("t", "kestrel", "Kestrel", "note", "falcons")
        assert queue_rows(db), "precondition: the write should have queued"

        import pyrite.services.embedding_worker as ew

        monkeypatch.setattr(
            ew.EmbeddingWorker,
            "_get_embedding_svc",
            lambda self: MagicMock(**{"embed_entry.return_value": True}),
        )

        app = create_app(config=config)
        app.dependency_overrides[get_config] = lambda: config
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app):  # entering the context runs startup events
            pass

        assert queue_rows(db) == [], (
            "a server with prewarm_embeddings=False (the default) never drained "
            "its embed queue; on a stock install nothing would ever be embedded"
        )

    def test_startup_drain_does_not_load_a_model_when_there_is_no_debt(self, tmp_path, monkeypatch):
        """Draining unconditionally must stay cheap on the empty-queue path.

        This is what makes it safe to drop the prewarm gate: with nothing
        pending, the drain must not construct an embedding service (and so
        must not import torch) just to discover there is no work.
        """
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from pyrite.server.api import create_app, get_config, get_db

        config = _config(tmp_path, auto_embed=True, prewarm_embeddings=False)
        db = PyriteDB(config.settings.index_path)

        import pyrite.services.embedding_worker as ew

        built = []
        monkeypatch.setattr(
            ew.EmbeddingWorker,
            "_get_embedding_svc",
            lambda self: built.append(1) or MagicMock(),
        )

        app = create_app(config=config)
        app.dependency_overrides[get_config] = lambda: config
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app):
            pass

        assert built == [], "the empty-queue drain built an embedding service anyway"


# ===========================================================================
# BLOCKER 2 -- an update must re-embed, not keep a stale vector
# ===========================================================================


class TestUpdatingAnEntryRefreshesItsEmbedding:
    """The old synchronous path re-embedded on every update.

    `embed_entry` -> `upsert_embedding` DELETEs and re-INSERTs the vector
    unconditionally, so an update always replaced the old one. Under ADR-0035
    the update enqueues instead -- and if that row is retired by
    `clear_embedded` (which only asks "does *a* vector exist?") before
    anything re-embeds, the entry keeps a vector encoding its **old body**,
    `embed-status` reads zero, and only `pyrite index embed --force` recovers
    it. Silently stale is the worst of the three outcomes: nothing anywhere
    says the semantic index is lying.
    """

    def test_settling_the_queue_re_embeds_an_updated_entry(self, tmp_path, monkeypatch):
        from pyrite.cli.index_commands import _settle_embed_queue
        from pyrite.services.embedding_worker import EmbeddingWorker

        svc = _svc(tmp_path, auto_embed=True)
        if not svc.db.vec_available:
            pytest.skip("sqlite-vec unavailable; no vectors to go stale")

        svc.create_entry("t", "kestrel", "Kestrel", "note", "the original body")

        embedded = []

        def fake_embed_entry(entry_id, kb_name):
            embedded.append((entry_id, svc.db.get_entry(entry_id, kb_name)["body"]))
            svc.db.backend.upsert_embedding(entry_id, kb_name, [0.01] * 384)
            return True

        monkeypatch.setattr(
            EmbeddingWorker,
            "_get_embedding_svc",
            lambda self: MagicMock(**{"embed_entry.side_effect": fake_embed_entry}),
        )

        _settle_embed_queue(svc.db)
        assert embedded == [("kestrel", "the original body")], embedded

        # Now the update. This is the step the old synchronous path handled.
        svc.update_entry("kestrel", "t", body="a substantially revised body")
        assert queue_rows(svc.db), "precondition: the update should have queued"

        _settle_embed_queue(svc.db)

        assert queue_rows(svc.db) == [], "the queue kept a row it never embedded"
        assert embedded[-1] == ("kestrel", "a substantially revised body"), (
            "settling the queue retired the update's row without re-embedding it: "
            f"the vector still encodes the old body. embed_entry calls: {embedded}"
        )

    def test_no_api_retires_a_row_merely_because_some_vector_exists(self, tmp_path):
        """The narrow unit: a queued row means 'this entry changed'.

        The first pass had a `clear_embedded()` that deleted any pending row
        whose entry had *a* vector, with no notion of currency -- which is
        precisely wrong for the update case, where a vector exists and is
        stale. It is gone: the only way a row leaves the queue is
        `process_batch` actually re-embedding the entry. This test pins that,
        so the shortcut cannot come back without a red.
        """
        from pyrite.services.embedding_worker import EmbeddingWorker

        svc = _svc(tmp_path, auto_embed=True)
        if not svc.db.vec_available:
            pytest.skip("sqlite-vec unavailable")

        worker = EmbeddingWorker(svc.db)
        # A name tripwire, not the guarantee: it catches a literal revert
        # cheaply, but a reimplementation under another name sails past it.
        # The behavioural assertion below is what actually holds the line.
        assert not hasattr(worker, "clear_embedded"), (
            "clear_embedded is back; it cannot tell a current vector from a "
            "stale one, which is how an update's embedding got frozen"
        )

        svc.create_entry("t", "kestrel", "Kestrel", "note", "original")
        svc.db.backend.upsert_embedding("kestrel", "t", [0.01] * 384)
        svc.update_entry("kestrel", "t", body="revised")

        # A vector exists, and the row is still owed. Only an embed clears it.
        assert len(queue_rows(svc.db)) == 1
        worker._embedding_svc = MagicMock(**{"embed_entry.return_value": False})
        worker.drain()
        assert len(queue_rows(svc.db)) == 1, "a row left the queue without being embedded"

    @pytest.mark.parametrize("surface", ["cli", "server"])
    def test_both_drain_paths_re_embed_an_update(self, tmp_path, monkeypatch, surface):
        """Two drain implementations with different correctness is the smell.

        The server drained with `drain()` alone (correct on updates) while the
        CLI called `clear_embedded()` first (wrong). Parametrising one
        assertion over both surfaces is how they are held together: whatever
        the shared implementation is, a KB must not mean different things
        depending on which command touched it last.
        """
        from pyrite.services.embedding_worker import EmbeddingWorker

        if surface == "cli":
            from pyrite.cli.index_commands import _settle_embed_queue as settle
        else:
            from pyrite.server.api import _drain_embed_queue as settle

        svc = _svc(tmp_path, auto_embed=True)
        if not svc.db.vec_available:
            pytest.skip("sqlite-vec unavailable")

        bodies: list[str] = []

        def fake_embed_entry(entry_id, kb_name):
            bodies.append(svc.db.get_entry(entry_id, kb_name)["body"])
            svc.db.backend.upsert_embedding(entry_id, kb_name, [0.01] * 384)
            return True

        monkeypatch.setattr(
            EmbeddingWorker,
            "_get_embedding_svc",
            lambda self: MagicMock(**{"embed_entry.side_effect": fake_embed_entry}),
        )

        svc.create_entry("t", "kestrel", "Kestrel", "note", "the original body")
        settle(svc.db)
        svc.update_entry("kestrel", "t", body="a substantially revised body")
        settle(svc.db)

        assert queue_rows(svc.db) == []
        assert bodies == ["the original body", "a substantially revised body"], (
            f"the {surface} drain path did not re-embed the update: {bodies}"
        )


# ===========================================================================
# BLOCKER 3 -- a worktree write must not queue into the wrong database
# ===========================================================================


class TestAWriteNeverQueuesIntoADatabaseThatCannotSeeIt:
    """`WorktreeDB` routes writes to the diff DB, `_raw_conn` to main.

    So `EmbeddingWorker(self.db)` inside a worktree write service creates the
    row in **main's** `embed_queue`, naming an entry only the **diff** DB
    holds. The drain then calls `embed_entry` against main, `get_entry`
    returns None, `embed_entry` returns False *without raising* -- and
    `process_batch` treats "no exception" as success, deletes the row and
    counts it embedded. Debt invented in the wrong place, then erased as
    though paid.
    """

    def test_process_batch_does_not_count_a_falsey_embed_as_success(self, tmp_path):
        """The mechanism, isolated. `embed_entry` returning False is a failure.

        Pre-existing, but ADR-0035 is what promotes the queue from vestigial
        to load-bearing, so it has to be honest now.
        """
        from pyrite.services.embedding_worker import EmbeddingWorker

        svc = _svc(tmp_path, auto_embed=True)
        # A real entry, not a bare enqueue of an id that was never created:
        # an absent entry is *void* debt and is now retired on sight
        # (TestADeletedEntrysDebtIsVoid). What this test pins is the other
        # case -- the entry exists and the embed refused -- which stays queued.
        svc.create_entry("t", "ghost", "Ghost", "note", "a body")
        worker = EmbeddingWorker(svc.db, max_attempts=3)
        worker._embedding_svc = MagicMock(**{"embed_entry.return_value": False})

        processed = worker.process_batch()

        assert processed == 0, "a False return was counted as a successful embed"
        row = svc.db._raw_conn.execute(
            "SELECT status, attempts FROM embed_queue WHERE entry_id = 'ghost'"
        ).fetchone()
        assert row is not None, "the row was deleted although nothing was embedded"
        assert (row[0], row[1]) == ("pending", 1), tuple(row)

    def test_the_guard_refuses_a_db_whose_writes_and_queue_disagree(self, tmp_path):
        """The predicate itself, on both DB shapes.

        Stated separately from the behavioural test below because that one
        cannot be red at the branch's merge base: there `_auto_embed` embedded
        synchronously and never queued at all, so no orphan row could exist.
        This one is red wherever the guard is absent, which is what a reviewer
        re-running `verify-red.sh` needs.
        """
        main_cfg = _config(tmp_path / "main", auto_embed=True)
        main_db = PyriteDB(main_cfg.settings.index_path)

        plain = KBService(main_cfg, main_db)
        assert plain._queue_can_see_our_writes() is True, (
            "an ordinary PyriteDB writes and queues on the same connection"
        )

        overlay = KBService(main_cfg, _worktree_db(main_cfg, main_db, tmp_path / "diff"))
        assert overlay._queue_can_see_our_writes() is False, (
            "an overlay DB routes writes to the diff database while "
            "embed_queue lives on main; queueing there files debt against an "
            "entry the drain cannot reach"
        )

    def test_a_worktree_write_service_does_not_queue_into_mains_database(self, tmp_path):
        """The behaviour the guard buys, through a real `create_entry`.

        Builds the exact DB shape `WorktreeResolver.get_write_service`
        produces -- a `WorktreeDB` whose writes go to a diff DB and whose
        `_raw_conn` is main's -- and asserts the write leaves no row in main's
        queue naming an entry main cannot see.
        """
        main_cfg = _config(tmp_path / "main", auto_embed=True)
        main_db = PyriteDB(main_cfg.settings.index_path)
        wt_db = _worktree_db(main_cfg, main_db, tmp_path / "diff")
        assert wt_db._raw_conn is main_db._raw_conn, (
            "precondition for this test: WorktreeDB forwards _raw_conn to main"
        )

        svc = KBService(main_cfg, wt_db)
        svc.create_entry("t", "kestrel", "Kestrel", "note", "falcons")

        orphans = [row for row in queue_rows(main_db) if main_db.get_entry(row[0], row[1]) is None]
        assert orphans == [], (
            "a worktree write queued into main's embed_queue an entry main "
            f"cannot see; the drain would 'succeed' on it and delete it: {orphans}"
        )

    def test_the_write_still_succeeds_and_stays_searchable(self, tmp_path):
        """Refusing to queue must never cost the user their write.

        Skipping the queue on an overlay DB is a routing decision, not an
        error -- a worktree entry embeds from the main index once its branch
        merges -- so it is logged at debug and the write is unaffected: the
        entry comes back from the service and is searchable through the
        overlay it was written to.
        """
        main_cfg = _config(tmp_path / "main", auto_embed=True)
        main_db = PyriteDB(main_cfg.settings.index_path)
        wt_db = _worktree_db(main_cfg, main_db, tmp_path / "diff")
        svc = KBService(main_cfg, wt_db)

        entry = svc.create_entry("t", "kestrel", "Kestrel", "note", "falcons")

        assert entry.id == "kestrel"
        assert svc.get_entry("kestrel", "t", readable_kbs=UNSCOPED) is not None, (
            "the write was refused a queue row and lost the entry with it"
        )


class TestAFailureToQueueIsExplained:
    """`_get_embedding_worker`'s except branch had no test and no `exc_info`."""

    def test_the_cause_is_logged_not_just_the_symptom(self, tmp_path, caplog, monkeypatch):
        """A disk-full or locked-DB error must reach the operator with a cause.

        Its sibling handler eleven lines down passes `exc_info=True`; this one
        did not, so the operator got a cause-free sentence and no traceback.
        """
        import logging

        import pyrite.services.embedding_worker as ew

        svc = _svc(tmp_path, auto_embed=True)
        svc._embedding_worker = None
        monkeypatch.setattr(
            ew, "EmbeddingWorker", MagicMock(side_effect=RuntimeError("database is locked"))
        )

        with caplog.at_level(logging.WARNING):
            svc._auto_embed("kestrel", "t")  # must not raise

        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert records, "a queue that could not be created was not reported at all"
        assert any(r.exc_info for r in records), (
            "logged without exc_info=True: the operator sees a cause-free "
            "sentence and cannot tell a locked DB from a full disk"
        )
        assert any("database is locked" in (r.exc_text or "") or r.exc_info for r in records)


# ===========================================================================
# A DELETED ENTRY'S DEBT IS VOID, NOT OWED
#
# Found by the second cold read. `delete_entry` and `rename_entry` do not
# clean up `embed_queue`, and `process_batch` selects ORDER BY queued_at ASC
# while `drain` stops on the first batch that embeds nothing -- so rows for
# entries that no longer exist sit at the HEAD of the queue and starve every
# live row behind them.
# ===========================================================================


class TestADeletedEntrysDebtIsVoid:
    """A row whose entry is gone can never succeed, so it must not be kept.

    The first pass counted a falsey `embed_entry` as success and deleted the
    row -- silently wrong, but self-healing. The second pass raised
    `EmbedRefusedError` for every falsey return, which is honest about work
    not done but keeps a row nothing can ever pay. Both fail; the queue needs
    the third answer: retire the row *because the debt is void*.
    """

    def test_a_deleted_entrys_row_is_retired_not_retried(self, tmp_path):
        from pyrite.services.embedding_worker import EmbeddingWorker

        svc = _svc(tmp_path, auto_embed=True)
        svc.create_entry("t", "gone", "Gone", "note", "x")
        assert ("gone", "t", "pending") in queue_rows(svc.db)

        svc.delete_entry("gone", "t")
        assert svc.db.get_entry("gone", "t") is None

        worker = EmbeddingWorker(svc.db)
        worker._get_embedding_svc = lambda: MagicMock(embed_entry=MagicMock(return_value=False))
        worker.process_batch(batch_size=10)

        assert queue_rows(svc.db) == [], (
            "the row for a deleted entry is still queued; nothing can ever "
            "embed it, so it will block the queue head forever"
        )

    def test_deleted_entries_at_the_head_do_not_starve_live_ones(self, tmp_path):
        """The reviewer's scenario: 10 dead rows ahead of live work.

        `drain` breaks on the first batch returning 0. If retiring a void row
        does not count as progress, a batch of nothing but deleted entries
        reads as "no progress" and every newer row starves.
        """
        from pyrite.services.embedding_worker import EmbeddingWorker

        svc = _svc(tmp_path, auto_embed=True)
        for i in range(10):
            svc.create_entry("t", f"dead{i}", "D", "note", "x")
            svc.delete_entry(f"dead{i}", "t")
        for i in range(5):
            svc.create_entry("t", f"live{i}", "L", "note", "x")

        embedded: list[str] = []

        def _embed(entry_id, kb_name):
            if svc.db.get_entry(entry_id, kb_name) is None:
                return False
            embedded.append(entry_id)
            return True

        worker = EmbeddingWorker(svc.db)
        worker._get_embedding_svc = lambda: MagicMock(embed_entry=MagicMock(side_effect=_embed))

        worker.drain(batch_size=10)

        assert sorted(embedded) == [f"live{i}" for i in range(5)], (
            f"one drain embedded {sorted(embedded)}; the five live entries "
            "starved behind ten rows for entries that no longer exist"
        )
        assert queue_rows(svc.db) == [], "queue not empty after a full drain"

    def test_a_real_refusal_is_still_retried_not_retired(self, tmp_path):
        """The guard must not swallow the case it was built to preserve.

        An entry that still exists but could not embed (no model, empty body)
        is unpaid debt: the row stays, attempts increment, `embed-status`
        keeps reporting it.
        """
        from pyrite.services.embedding_worker import EmbeddingWorker

        svc = _svc(tmp_path, auto_embed=True)
        svc.create_entry("t", "here", "Here", "note", "x")

        worker = EmbeddingWorker(svc.db)
        worker._get_embedding_svc = lambda: MagicMock(embed_entry=MagicMock(return_value=False))
        worker.process_batch(batch_size=10)

        rows = queue_rows(svc.db)
        assert rows and rows[0][0] == "here", (
            "a still-present entry that refused was retired; that is the "
            "silent data loss the second pass existed to stop"
        )

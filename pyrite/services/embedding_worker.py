"""
Embedding queue — a SQLite table, not a background thread.

Decouples write latency from embedding computation (ADR-0035): a write with
``auto_embed: true`` records one ``pending`` row here and returns, instead of
loading a ~90 MB model inside the request (#13). Nothing in this module starts
a thread; ``process_batch``/``drain`` are synchronous methods somebody with
time to spare must call.

``settle_embed_queue(db)`` is that single entry point, shared by every surface
— ``pyrite-server`` on startup and at the end of ``POST /api/index/sync``, and
``pyrite index embed``/``sync``/``build``. One implementation on purpose: two
drains that differ in correctness is how a KB comes to mean different things
depending on which command touched it last.

The embed_queue table is app-state (not in SearchBackend) — it manages
internal processing state, not knowledge-index data.

Usage:
    settle_embed_queue(db)              # the normal way to pay the debt

    worker = EmbeddingWorker(db)        # the pieces, for finer control
    worker.enqueue("entry-id", "kb-name")
    processed = worker.process_batch(batch_size=10)
    status = worker.get_status()
"""

import logging
from datetime import UTC, datetime

from ..storage.database import PyriteDB

logger = logging.getLogger(__name__)


class EmbedRefusedError(Exception):
    """`embed_entry` declined an entry without raising, and it may yet succeed.

    Its ``False`` return covers several conditions -- an empty body,
    sqlite-vec not loaded, the model unreachable. Raising turns that into the
    same retry-and-record path as any other failure, so the row stays queued
    and `embed-status` keeps reporting it instead of the queue quietly
    deleting work it never did.

    **Not** raised when the entry no longer exists: see `_entry_is_gone`. A
    row whose entry has been deleted or renamed can never succeed, and
    keeping it would block the queue head forever (rows are drained
    ``ORDER BY queued_at ASC``, and `drain` stops on the first batch that
    embeds nothing). That debt is void, not owed.
    """


def settle_embed_queue(db: PyriteDB, *, label: str = "") -> int:
    """Pay off whatever ADR-0035 writes left in `embed_queue`. Never raises.

    **The one drain implementation.** The server (startup, `POST
    /api/index/sync`) and the CLI (`pyrite index embed`/`sync`/`build`) both
    call this, because two drain paths that differ in correctness is how a KB
    comes to mean different things depending on which command touched it last
    -- which is exactly what the cold read found: the server drained correctly
    while the CLI retired rows against stale vectors.

    Cheap when there is nothing owed: ``has_pending`` is one indexed COUNT, so
    the common case never constructs an ``EmbeddingService`` and never imports
    torch. That is what makes draining on *every* server startup affordable
    rather than only when ``prewarm_embeddings`` happens to be on -- it
    defaults to False, so gating on it meant a stock install never embedded
    anything at all.

    Returns the number of entries embedded.
    """
    return sum(settle_embed_queue_by_kb(db, label=label).values())


def settle_embed_queue_by_kb(db: PyriteDB, *, label: str = "") -> dict[str, int]:
    """`settle_embed_queue`, returning the entries embedded per KB (``{kb: n}``).

    The count is what the worker reports it wrote, not an inference from the
    vector table: `upsert_embedding` reuses an entry's rowid, so a re-embedded
    edit is invisible to a before/after diff, and backends do not share a rowid
    space. `pyrite index embed` needs the per-KB split to credit the drain to
    the KB in scope (#584). Never raises.
    """
    try:
        worker = EmbeddingWorker(db)
        if not worker.has_pending():
            return {}
        embedded = worker.drain()
        by_kb = dict(worker.embedded_by_kb)
        if embedded:
            logger.info(
                "Embedded %d queued entr%s%s",
                embedded,
                "y" if embedded == 1 else "ies",
                f" ({label})" if label else "",
            )
        remaining = worker.get_status()
        if remaining["pending"] or remaining["failed"]:
            logger.warning(
                "Embed queue still owes %d pending / %d failed entries; "
                "run `pyrite index embed` once the model is reachable",
                remaining["pending"],
                remaining["failed"],
            )
        return by_kb
    except Exception:
        logger.warning("Embed queue drain failed; entries stay queued", exc_info=True)
        return {}


class EmbeddingWorker:
    """SQLite-backed embedding queue. Despite the name, not a thread.

    Constructing one is a ``CREATE TABLE IF NOT EXISTS`` -- no thread is
    spawned and no torch is imported. ``process_batch``/``drain`` are plain
    synchronous methods somebody else must call; see `settle_embed_queue`
    above for the single entry point every surface uses.
    """

    def __init__(self, db: PyriteDB, max_attempts: int = 3):
        self.db = db
        self.max_attempts = max_attempts
        self._embedding_svc = None
        #: Entries embedded by the last `drain`, per KB (see `settle_embed_queue_by_kb`).
        self.embedded_by_kb: dict[str, int] = {}
        self._ensure_table()

    def _ensure_table(self):
        """Create the embed_queue table if it doesn't exist."""
        self.db._raw_conn.execute("""
            CREATE TABLE IF NOT EXISTS embed_queue (
                entry_id TEXT NOT NULL,
                kb_name TEXT NOT NULL,
                queued_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (entry_id, kb_name)
            )
        """)
        self.db._raw_conn.commit()

    def enqueue(self, entry_id: str, kb_name: str) -> None:
        """Add an entry to the embedding queue. Idempotent — skips if already queued."""
        now = datetime.now(UTC).isoformat()
        self.db._raw_conn.execute(
            """
            INSERT OR IGNORE INTO embed_queue (entry_id, kb_name, queued_at, status, attempts)
            VALUES (?, ?, ?, 'pending', 0)
            """,
            (entry_id, kb_name, now),
        )
        self.db._raw_conn.commit()

    def process_batch(self, batch_size: int = 10) -> int:
        """Process up to batch_size pending entries. Returns count of successfully embedded.

        A row is deleted only when ``embed_entry`` returns **truthy**. It
        returns ``False`` without raising for several ordinary reasons -- the
        entry is not in this database, the body is empty, sqlite-vec is not
        loaded -- and treating "did not raise" as success deleted the row and
        counted an embedding that never happened. Under ADR-0035 the queue is
        the only record that the work is owed, so a false success is a silent
        data loss rather than a cosmetic miscount.
        """
        rows = self.db._raw_conn.execute(
            """
            SELECT entry_id, kb_name, attempts FROM embed_queue
            WHERE status = 'pending' AND attempts < ?
            ORDER BY queued_at ASC
            LIMIT ?
            """,
            (self.max_attempts, batch_size),
        ).fetchall()

        if not rows:
            return 0

        svc = self._get_embedding_svc()
        if svc is None:
            logger.debug("Embedding service not available, skipping batch")
            return 0

        if not svc.prewarm(log_failure=False):
            logger.warning(
                "Could not load the embedding model %r; leaving %d queued entries pending "
                "without spending attempts. Check the configured model and Hugging Face "
                "cache/network access, then rerun pyrite index embed.",
                svc.model_name,
                len(rows),
            )
            return 0

        success_count = 0
        retired_count = 0
        for row in rows:
            entry_id, kb_name, attempts = row[0], row[1], row[2]
            try:
                embedded = svc.embed_entry(entry_id, kb_name)
                if not embedded and self._entry_is_gone(entry_id, kb_name):
                    # The debt is void, not owed: nothing will ever embed an
                    # entry that no longer exists. Retire the row rather than
                    # retrying it forever at the head of the queue.
                    logger.debug(
                        "Retiring embed_queue row for %s/%s: the entry no longer exists",
                        kb_name,
                        entry_id,
                    )
                    self.db._raw_conn.execute(
                        "DELETE FROM embed_queue WHERE entry_id = ? AND kb_name = ?",
                        (entry_id, kb_name),
                    )
                    retired_count += 1
                    continue
                if not embedded:
                    raise EmbedRefusedError(
                        f"embed_entry returned {embedded!r} for {kb_name}/{entry_id} "
                        "(empty body, or sqlite-vec unavailable)"
                    )
                # Mark as done — delete from queue
                self.db._raw_conn.execute(
                    "DELETE FROM embed_queue WHERE entry_id = ? AND kb_name = ?",
                    (entry_id, kb_name),
                )
                success_count += 1
                self.embedded_by_kb[kb_name] = self.embedded_by_kb.get(kb_name, 0) + 1
            except Exception as e:
                new_attempts = attempts + 1
                new_status = "failed" if new_attempts >= self.max_attempts else "pending"
                self.db._raw_conn.execute(
                    """
                    UPDATE embed_queue
                    SET attempts = ?, status = ?, error = ?
                    WHERE entry_id = ? AND kb_name = ?
                    """,
                    (new_attempts, new_status, str(e), entry_id, kb_name),
                )
                logger.warning(
                    "Embed failed for %s (attempt %d/%d): %s",
                    entry_id,
                    new_attempts,
                    self.max_attempts,
                    e,
                )

        self.db._raw_conn.commit()
        # Retiring a void row IS progress, even though nothing was embedded:
        # `drain` stops on a batch that returns 0, so a batch of nothing but
        # deleted entries must not read as "no progress" or every live row
        # behind them starves (rows come out ORDER BY queued_at ASC).
        return success_count + retired_count

    def drain(self, batch_size: int = 10, max_batches: int = 1000) -> int:
        """Process pending rows until the queue stops making progress.

        Under ADR-0035 a write only *enqueues*; draining is what turns that
        debt back into embeddings, and it happens on paths that already have a
        caller willing to wait -- server startup prewarm, ``POST
        /api/index/sync``, ``pyrite index embed``/``sync``/``build``. No thread
        is started here, on purpose: #102's unjoined daemon thread holding its
        own index.db connection is the hazard this design exists not to copy.

        Terminates on the first batch that embeds nothing, so the offline case
        (no model, every row failing) costs one batch rather than spinning --
        ``process_batch`` has already recorded the attempt and, at
        ``max_attempts``, flipped the row to ``failed``. ``max_batches`` is a
        belt-and-braces stop for a queue that somehow grows as fast as it
        drains.

        Returns the number of entries successfully embedded.
        """
        total = 0
        self.embedded_by_kb = {}
        for _ in range(max_batches):
            processed = self.process_batch(batch_size=batch_size)
            if processed == 0:
                break
            total += processed
        return total

    def _entry_is_gone(self, entry_id: str, kb_name: str) -> bool:
        """Is this row's entry absent from the index the queue reads?

        Distinguishes a **void** debt from an unpaid one. `embed_entry`
        returns ``False`` for several unrelated reasons, and only this one is
        permanent: an entry that has been deleted or renamed can never be
        embedded, so retrying it forever blocks every newer row behind it.

        `delete_entry` and `rename_entry` do not clean up `embed_queue`, so
        these rows are produced by the most ordinary operations there are --
        deleting a draft, renaming a note. Checking here rather than adding
        dequeue calls to both keeps the queue's invariant in the queue: a row
        leaves when the work is done *or* when the work becomes void.

        Fails **closed** (returns False, so the row is retried and recorded)
        if the lookup itself errors -- a locked or unreadable database must
        not be mistaken for a deleted entry.
        """
        try:
            return self.db.get_entry(entry_id, kb_name) is None
        except Exception:
            logger.debug(
                "Could not confirm whether %s/%s still exists; treating the debt as owed",
                kb_name,
                entry_id,
                exc_info=True,
            )
            return False

    def has_pending(self) -> bool:
        """Is there any debt at all? One indexed COUNT, no embedding stack.

        Lets a caller skip the drain -- and so avoid constructing an
        ``EmbeddingService``, which imports torch -- when there is nothing to
        do. That is what makes it affordable to drain on *every* server
        startup rather than only when ``prewarm_embeddings`` is on.
        """
        try:
            row = self.db._raw_conn.execute(
                "SELECT 1 FROM embed_queue WHERE status = 'pending' LIMIT 1"
            ).fetchone()
            return row is not None
        except Exception:
            logger.debug("embed_queue unreadable", exc_info=True)
            return False

    def get_status(self) -> dict:
        """Get queue status: counts by status."""
        rows = self.db._raw_conn.execute(
            "SELECT status, COUNT(*) FROM embed_queue GROUP BY status"
        ).fetchall()
        counts = {r[0]: r[1] for r in rows}
        return {
            "pending": counts.get("pending", 0),
            "processing": counts.get("processing", 0),
            "failed": counts.get("failed", 0),
            "total": sum(counts.values()),
        }

    def _get_embedding_svc(self):
        """Get or lazy-load embedding service."""
        if self._embedding_svc is not None:
            return self._embedding_svc
        try:
            from .embedding_service import EmbeddingService, is_available

            if is_available() and self.db.vec_available:
                self._embedding_svc = EmbeddingService(self.db)
                return self._embedding_svc
        except Exception:
            logger.warning("Embedding service initialization failed in worker", exc_info=True)
        return None

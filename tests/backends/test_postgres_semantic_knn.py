"""Postgres semantic search must not depend on which plan the planner picks (#1401).

``entry.embedding`` carried an HNSW index until #1822 dropped it (#1821 had
already made it unusable by this query). When the planner served
``ORDER BY embedding <=> :vec LIMIT n`` from it, pgvector returns at most
``hnsw.ef_search`` candidates (40 by default) and applied the ``WHERE`` only
afterwards. A selective filter could then reject every candidate, and the
result was empty without saying ``capped``. Whether that plan runs is a cost
choice that follows table statistics, and autoanalyze refreshes those on its
own schedule. That is why
``TestSemanticKnnBudget::test_rows_the_ceiling_hides_are_returned_or_the_result_says_capped[postgres]``
failed on some runs and not others.

These tests take the chance out. ``SET LOCAL enable_sort = off`` makes any
index-ordered plan win, so an index path runs whenever one is available (today
none is; the cells keep guarding the sort key should a vector index come back).
The property must hold anyway: a row that matches the filter is returned, or
the result says it was capped.

Which plans exist depends on the Postgres version. Up to PG16 the index can
serve only an ORDER BY it matches whole, so a ``(kb_name, id)`` tiebreaker
alone keeps it out. PG17 also allows "index scan on the distance key, then an
Incremental Sort on the rest", and on PG17 the first two tests fail unless the
distance key is an expression no index matches. CI runs PG16; run these
against a PG17 pgvector image to see that guard work.
"""

from __future__ import annotations

import math

import pytest
from sqlalchemy import text

from .conftest import _require_pg, _setup_pg_backend, _teardown_postgres

# More rows nearer to the query than the one matching row than the HNSW scan's
# 40-candidate window holds. 200 keeps the fixture fast; the property does not
# depend on the count.
_NEAR_ROWS = 200


def _near_vector(nudge: int = 0) -> list[float]:
    vec = [0.05] * 384
    vec[nudge % 384] += 0.001
    return vec


def _entry(entry_id: str, entry_type: str = "note") -> dict:
    return {
        "id": entry_id,
        "kb_name": "test",
        "entry_type": entry_type,
        "title": f"Title {entry_id}",
        "body": f"Body for {entry_id}",
        "summary": "",
        "tags": [],
        "sources": [],
        "links": [],
        "metadata": {},
    }


@pytest.fixture
def pg_backend():
    _require_pg()
    be, engine, session = _setup_pg_backend(("test", "generic", "/tmp/test", "Test KB"))
    try:
        yield be
    finally:
        _teardown_postgres(engine, session)


def _prefer_index_order(backend, ef_search: int = 40) -> None:
    """Make an index-ordered plan win whenever one exists, for this transaction.

    The window is pinned to pgvector's default so the test does not depend on
    the server's configuration.
    """
    backend._session.execute(text("SET LOCAL enable_sort = off"))
    backend._session.execute(text(f"SET LOCAL hnsw.ef_search = {int(ef_search)}"))


def _far_from(query: list[float]) -> list[float]:
    """A vector with cosine similarity of about 0.6 to ``query``: above the
    similarity floor, and farther away than every near row."""
    norm = math.sqrt(sum(v * v for v in query))
    far = [0.6 * v / norm for v in query]
    far[383] += 0.8
    return far


def test_a_filtered_match_beyond_the_index_window_is_returned_or_capped(pg_backend):
    """Every row nearer than the match is filtered out. The match is returned,
    or the result says it was capped. An empty, uncapped result here would
    report "nothing matches" when one row does."""
    for i in range(_NEAR_ROWS):
        pg_backend.upsert_entry(_entry(f"e{i}"))
        pg_backend.upsert_embedding(f"e{i}", "test", _near_vector(i))
    query = _near_vector()
    pg_backend.upsert_entry(_entry("far", entry_type="rare"))
    pg_backend.upsert_embedding("far", "test", _far_from(query))

    _prefer_index_order(pg_backend)
    rows = pg_backend.search_semantic(query, kb_name="test", limit=1, entry_type="rare")

    assert [r["id"] for r in rows] == ["far"] or getattr(rows, "capped", False)


def test_the_limit_is_filled_from_every_matching_row_not_an_index_window(pg_backend):
    """More than one match beyond the window: ``limit=3`` over three matching
    rows returns all three, nearest first, not the few the window happened to
    hold."""
    for i in range(_NEAR_ROWS):
        pg_backend.upsert_entry(_entry(f"e{i}"))
        pg_backend.upsert_embedding(f"e{i}", "test", _near_vector(i))
    query = _near_vector()
    far = _far_from(query)
    # Cosine similarity to the query of about 0.626, 0.620 and 0.595: each one
    # a little farther than the last, all above the floor.
    for n, scale in (("m1", 0.0), ("m2", 0.2), ("m3", 0.4)):
        vec = list(far)
        vec[200] += scale
        pg_backend.upsert_entry(_entry(n, entry_type="rare"))
        pg_backend.upsert_embedding(n, "test", vec)

    _prefer_index_order(pg_backend)
    rows = pg_backend.search_semantic(query, kb_name="test", limit=3, entry_type="rare")

    assert [r["id"] for r in rows] == ["m1", "m2", "m3"] or getattr(rows, "capped", False)


def test_equal_distances_give_the_same_rows_whatever_the_insertion_order(pg_backend):
    """Rows at the same distance are ordered by (kb_name, id), so ``limit``
    keeps the same rows on every run. Without a tiebreaker the row kept is
    whichever the plan reaches first: insertion order through the HNSW index
    (the default plan on a small table), heap order through a sort. Neither is
    a property of the data. ``b`` goes in first here, so a backend that keeps
    the first row it reaches answers ``b``."""
    vec = _near_vector()
    for entry_id in ("b", "a"):
        pg_backend.upsert_entry(_entry(entry_id))
        pg_backend.upsert_embedding(entry_id, "test", vec)

    rows = pg_backend.search_semantic(vec, kb_name="test", limit=1)

    assert [r["id"] for r in rows] == ["a"]

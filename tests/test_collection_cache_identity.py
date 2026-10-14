"""Real database isolation and the documented collection cache TTL."""

import pytest
from pyrite.services import collection_query as cq
from pyrite.storage.database import PyriteDB


@pytest.fixture
def databases(tmp_path):
    cq.clear_cache()
    dbs = [PyriteDB(tmp_path / f"{i}.db") for i in range(2)]
    for i, db in enumerate(dbs):
        db.register_kb("test", "generic", str(tmp_path))
        db.upsert_entry(
            {"id": f"entry-{i}", "kb_name": "test", "entry_type": "note", "title": f"Title {i}"}
        )
    yield dbs
    for db in dbs:
        db.close()
    cq.clear_cache()


def test_equal_queries_never_reuse_another_database_rows(databases):
    q = cq.CollectionQuery(kb_name="test")
    first, _ = cq.evaluate_query_cached(q, databases[0], readable_kbs=None)
    second, _ = cq.evaluate_query_cached(q, databases[1], readable_kbs=None)
    assert [e["id"] for e in first] == ["entry-0"]
    assert [e["id"] for e in second] == ["entry-1"]


@pytest.mark.control(reason="The existing TTL deliberately allows staleness until expiration")
def test_write_becomes_visible_at_ttl_boundary(databases, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(cq.time, "time", lambda: clock[0])
    db = databases[0]
    q = cq.CollectionQuery(kb_name="test")
    cq.evaluate_query_cached(q, db, readable_kbs=None)
    db.upsert_entry({"id": "new", "kb_name": "test", "entry_type": "note", "title": "New"})
    clock[0] += cq.CACHE_TTL - 1
    assert cq.evaluate_query_cached(q, db, readable_kbs=None)[1] == 1
    clock[0] += 1
    assert cq.evaluate_query_cached(q, db, readable_kbs=None)[1] == 2

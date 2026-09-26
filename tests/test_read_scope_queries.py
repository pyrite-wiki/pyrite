"""Queries, lookups and aggregates stay inside the caller's read scope.

However a KB is named -- a request field, the collection query language's
own ``kb:`` selector, a stored collection's query or ``entry_filter``, a
``kb:`` or shortname prefix inside a wikilink target -- a KB the caller may
not read answers exactly like a KB that does not exist (P-R2, P-R5). A
lookup that names no KB is bounded by the readable set (P-R4). An unscoped
caller (an operator key here) sees what it always saw.

Surfaces: REST through ``TestClient`` on the characterization world with a
real session cookie; MCP through the real ``_dispatch_tool`` chokepoint.
"""

from __future__ import annotations

import pytest

from pyrite.services import collection_query
from pyrite.services.kb_service import KBService
from tests.auth_seed import SESSION_COOKIE, seed_user, sign_in
from tests.characterization.world import (
    ADMIN_KEY,
    MISSING,
    PRIVATE,
    PRIVATE_ENTRY,
    READABLE,
    READABLE_ENTRY,
    build_world,
)

ADMIN = {"X-API-Key": ADMIN_KEY}


@pytest.fixture(scope="module")
def w(tmp_path_factory):
    world = build_world(tmp_path_factory, label="read-scope-queries")
    world.config.get_kb(PRIVATE).shortname = "priv"
    world.config.get_kb(READABLE).shortname = "pub"
    svc = KBService(world.config, world.db)
    svc.create_entry(
        PRIVATE, "private-linker", "Private linker", "note", "see [[secret-missing-target]]"
    )
    svc.create_entry(
        PRIVATE,
        "private-spy",
        "Private spy dossier",
        "note",
        "watching [[readable-kb:readable-missing]]\n\n## Secret heading\n\nthe code is 1234\n",
    )
    svc.create_entry(
        PRIVATE,
        "private-aliased",
        "Private aliased",
        "note",
        "x",
        metadata={"aliases": ["Hidden moniker"]},
    )
    # Readable entries whose titles *look* prefixed: an unknown prefix is
    # part of the target, so these resolve by title -- and an unreadable
    # KB's prefix must behave exactly like a missing KB's.
    svc.create_entry(READABLE, "colon-private", f"{PRIVATE}:odd", "note", "x")
    svc.create_entry(READABLE, "colon-missing", f"{MISSING}:odd", "note", "x")
    svc.create_entry(
        READABLE,
        "readable-pointer",
        "Readable pointer",
        "note",
        "see [[private-kb:private-note]] and [[private-kb:no-such-entry]] and [[missing-kb:ghost]]",
    )
    svc.create_entry(
        kb_name=READABLE,
        entry_id="sneaky-collection",
        entry_type="collection",
        title="Sneaky",
        body="",
        metadata={"source_type": "query", "query": f"kb:{PRIVATE}"},
    )
    svc.create_entry(
        kb_name=READABLE,
        entry_id="sneaky-filter",
        entry_type="collection",
        title="Sneaky filter",
        body="",
        metadata={"source_type": "query", "entry_filter": {"kb_name": PRIVATE}},
    )
    svc.create_entry(
        kb_name=READABLE,
        entry_id="honest-collection",
        entry_type="collection",
        title="Honest",
        body="",
        metadata={"source_type": "query", "query": "type:note"},
    )
    world.index_worker.wait_for_idle(timeout=10)
    yield world
    world.close()


@pytest.fixture(autouse=True)
def _fresh_query_cache():
    collection_query.clear_cache()
    yield
    collection_query.clear_cache()


def _local(w):
    return {SESSION_COOKIE: w.principals["local_user"].rest_cookies[SESSION_COOKIE]}


def _get(w, url, *, scoped=True, **kw):
    w.release_idle_connections()
    if scoped:
        return w.client.get(url, cookies=_local(w), **kw)
    return w.client.get(url, headers=ADMIN, **kw)


def _post(w, url, *, scoped=True, **kw):
    w.release_idle_connections()
    if scoped:
        return w.client.post(url, cookies=_local(w), **kw)
    return w.client.post(url, headers=ADMIN, **kw)


def _ids(resp) -> set[str]:
    return {e["id"] for e in resp.json()["entries"]}


# -- collection query preview (S2) -------------------------------------------


def test_preview_kb_selector_in_query_text_is_scoped(w):
    r = _post(w, "/api/collections/query-preview", json={"query": f"kb:{PRIVATE}"})
    assert r.status_code == 200
    assert r.json()["entries"] == [] and r.json()["total"] == 0
    assert "Private note" not in r.text


def test_preview_private_kb_selector_answers_like_a_missing_kb(w):
    priv = _post(w, "/api/collections/query-preview", json={"query": f"kb:{PRIVATE}"})
    miss = _post(w, "/api/collections/query-preview", json={"query": f"kb:{MISSING}"})
    assert priv.status_code == miss.status_code
    assert priv.text.replace(PRIVATE, "K") == miss.text.replace(MISSING, "K")


@pytest.mark.control(reason="a readable kb: selector keeps working for a scoped caller")
def test_preview_readable_kb_selector_still_works(w):
    r = _post(w, "/api/collections/query-preview", json={"query": f"kb:{READABLE}"})
    assert READABLE_ENTRY in _ids(r)


@pytest.mark.control(reason="unscoped callers see what they saw before")
def test_preview_unscoped_caller_unchanged(w):
    r = _post(w, "/api/collections/query-preview", json={"query": f"kb:{PRIVATE}"}, scoped=False)
    assert PRIVATE_ENTRY in _ids(r)


# -- stored query collections (S2) -------------------------------------------


@pytest.mark.parametrize("collection", ["sneaky-collection", "sneaky-filter"])
def test_stored_query_naming_private_kb_is_scoped(w, collection):
    r = _get(w, f"/api/collections/{collection}/entries", params={"kb": READABLE})
    assert r.status_code == 200
    assert r.json()["entries"] == [] and r.json()["total"] == 0
    assert "Private note" not in r.text


@pytest.mark.control(reason="unscoped callers see what they saw before")
@pytest.mark.parametrize("collection", ["sneaky-collection", "sneaky-filter"])
def test_stored_query_unscoped_caller_unchanged(w, collection):
    r = _get(w, f"/api/collections/{collection}/entries", params={"kb": READABLE}, scoped=False)
    assert PRIVATE_ENTRY in _ids(r)


@pytest.mark.control(reason="a stored query with no kb: keeps its collection's KB")
def test_stored_query_without_kb_selector_still_works(w):
    r = _get(w, "/api/collections/honest-collection/entries", params={"kb": READABLE})
    assert READABLE_ENTRY in _ids(r)
    assert PRIVATE_ENTRY not in _ids(r)


def test_stored_query_cache_does_not_serve_unscoped_rows_to_scoped_caller(w):
    # The unscoped caller warms the cache first; the scoped caller asks next.
    admin = _get(
        w, "/api/collections/sneaky-collection/entries", params={"kb": READABLE}, scoped=False
    )
    assert PRIVATE_ENTRY in _ids(admin)
    local = _get(w, "/api/collections/sneaky-collection/entries", params={"kb": READABLE})
    assert local.json()["entries"] == []


def test_stored_query_cache_does_not_serve_scoped_rows_to_unscoped_caller(w):
    local = _get(w, "/api/collections/sneaky-collection/entries", params={"kb": READABLE})
    assert local.json()["entries"] == []
    admin = _get(
        w, "/api/collections/sneaky-collection/entries", params={"kb": READABLE}, scoped=False
    )
    assert PRIVATE_ENTRY in _ids(admin)


def test_query_cache_key_carries_the_scope(w):
    """A query that names no KB spans the readable set; the cache must not
    hand one scope's rows to another."""
    q = collection_query.parse_query("type:note")
    everything, _ = collection_query.evaluate_query_cached(q, w.db, readable_kbs=None)
    assert any(r["kb_name"] == PRIVATE for r in everything)
    scoped, total = collection_query.evaluate_query_cached(q, w.db, readable_kbs={READABLE})
    assert scoped and all(r["kb_name"] == READABLE for r in scoped)
    assert total == len(scoped)


# -- titles, resolve, resolve-batch, wanted (S3) ------------------------------


@pytest.mark.parametrize(
    "params", [{}, {"q": "%"}, {"q": "Private"}], ids=["all", "like-wild", "q"]
)
def test_titles_without_kb_are_scoped(w, params):
    r = _get(w, "/api/entries/titles", params=params)
    assert r.status_code == 200
    assert "Private note" not in r.text and "private-spy" not in r.text


@pytest.mark.control(reason="readable titles still listed; unscoped unchanged")
def test_titles_controls(w):
    assert "Readable note" in _get(w, "/api/entries/titles").text
    assert "Private note" in _get(w, "/api/entries/titles", scoped=False).text


@pytest.mark.parametrize(
    "params",
    [
        {"target": "Private note"},
        {"target": PRIVATE_ENTRY},
        {"target": "Private%"},
        {"target": f"{PRIVATE}:{PRIVATE_ENTRY}", "kb": READABLE},
        {"target": f"{PRIVATE}:{PRIVATE_ENTRY}"},
        {"target": f"priv:{PRIVATE_ENTRY}", "kb": READABLE},
        {"target": "private-spy#Secret heading"},
        {"target": "Hidden moniker"},
        {"target": f"{PRIVATE}:private-spy#Secret heading", "kb": READABLE},
    ],
    ids=[
        "title",
        "id",
        "like",
        "prefix-over-kb",
        "prefix",
        "shortname",
        "heading",
        "alias",
        "prefix-heading",
    ],
)
def test_resolve_does_not_reach_private_kb(w, params):
    r = _get(w, "/api/entries/resolve", params=params)
    assert r.status_code == 200
    entry = r.json()["entry"]
    # "Private%" may match a readable entry titled "private-kb:odd"; what
    # matters is that nothing in the private KB is reached.
    assert entry is None or entry["kb_name"] != PRIVATE
    assert "Private" not in r.text and "1234" not in r.text


def test_resolve_private_prefix_answers_like_missing_prefix(w):
    priv = _get(
        w, "/api/entries/resolve", params={"target": f"{PRIVATE}:{PRIVATE_ENTRY}", "kb": READABLE}
    )
    miss = _get(
        w, "/api/entries/resolve", params={"target": f"{MISSING}:{PRIVATE_ENTRY}", "kb": READABLE}
    )
    assert priv.status_code == miss.status_code and priv.text == miss.text


def test_unreadable_prefix_is_treated_as_an_unknown_prefix(w):
    """A missing KB's prefix is not a prefix, so `missing-kb:odd` resolves
    by title to the readable entry with that title. An unreadable KB's
    prefix must do the same, not strip itself and look inside the KB."""
    priv = _get(w, "/api/entries/resolve", params={"target": f"{PRIVATE}:odd"})
    miss = _get(w, "/api/entries/resolve", params={"target": f"{MISSING}:odd"})
    assert miss.json()["resolved"] is True
    assert priv.json()["resolved"] is True
    assert priv.json()["entry"]["id"] == "colon-private"


@pytest.mark.control(reason="readable prefixes still resolve; unscoped unchanged")
def test_resolve_controls(w):
    for target in (f"{READABLE}:{READABLE_ENTRY}", f"pub:{READABLE_ENTRY}", "Readable note"):
        assert _get(w, "/api/entries/resolve", params={"target": target}).json()["resolved"] is True
    for target in (
        f"{PRIVATE}:{PRIVATE_ENTRY}",
        f"priv:{PRIVATE_ENTRY}",
        "Private note",
        "Hidden moniker",
    ):
        r = _get(w, "/api/entries/resolve", params={"target": target}, scoped=False)
        assert r.json()["resolved"] is True


def test_resolve_batch_without_kb_is_scoped(w):
    targets = [PRIVATE_ENTRY, f"{PRIVATE}:{PRIVATE_ENTRY}", f"priv:{PRIVATE_ENTRY}", READABLE_ENTRY]
    r = _post(w, "/api/entries/resolve-batch", json={"targets": targets})
    assert r.json()["resolved"] == {
        PRIVATE_ENTRY: False,
        f"{PRIVATE}:{PRIVATE_ENTRY}": False,
        f"priv:{PRIVATE_ENTRY}": False,
        READABLE_ENTRY: True,
    }


def test_resolve_batch_prefix_overrides_named_readable_kb(w):
    r = _post(
        w,
        "/api/entries/resolve-batch",
        json={"targets": [f"{PRIVATE}:{PRIVATE_ENTRY}"], "kb": READABLE},
    )
    assert r.json()["resolved"] == {f"{PRIVATE}:{PRIVATE_ENTRY}": False}


@pytest.mark.control(reason="unscoped callers see what they saw before")
def test_resolve_batch_unscoped_unchanged(w):
    targets = [PRIVATE_ENTRY, f"{PRIVATE}:{PRIVATE_ENTRY}", f"priv:{PRIVATE_ENTRY}"]
    r = _post(w, "/api/entries/resolve-batch", json={"targets": targets}, scoped=False)
    assert all(r.json()["resolved"].values())


def _wanted(w, **kw):
    return {
        (p["target_kb"], p["target_id"]): p
        for p in _get(w, "/api/entries/wanted", **kw).json()["pages"]
    }


def test_wanted_without_kb_is_scoped(w):
    pages = _wanted(w)
    text = repr(pages)
    assert "secret-missing-target" not in text
    assert "private-linker" not in text and "private-spy" not in text


def test_wanted_named_readable_kb_hides_private_sources(w):
    pages = _wanted(w, params={"kb": READABLE})
    assert "private-spy" not in repr(pages)


def test_wanted_private_target_answers_like_a_missing_one(w):
    pages = _wanted(w)
    existing = pages.get((PRIVATE, PRIVATE_ENTRY))
    absent = pages.get((PRIVATE, "no-such-entry"))
    assert existing is not None and absent is not None
    assert {k: v for k, v in existing.items() if k != "target_id"} == {
        k: v for k, v in absent.items() if k != "target_id"
    }


@pytest.mark.control(reason="unscoped callers see what they saw before")
def test_wanted_unscoped_unchanged(w):
    pages = _wanted(w, scoped=False)
    assert (PRIVATE, "secret-missing-target") in pages
    assert (PRIVATE, PRIVATE_ENTRY) not in pages
    assert (PRIVATE, "no-such-entry") in pages


# -- repo sync with an empty name (N6) ---------------------------------------


@pytest.fixture(scope="module")
def private_repo(w):
    w.db.register_repo(name="acme/private-repo", local_path="/nonexistent/acme/private-repo")
    repo = w.db.get_repo(name="acme/private-repo")
    w.db.link_kb_to_repo(PRIVATE, repo["id"], "")
    seed_user(w.db, "n6-writer", role="write")
    return {SESSION_COOKIE: sign_in(w.app, "n6-writer")}


@pytest.mark.parametrize("who", ["writer", "admin_key"])
def test_repo_sync_empty_name_never_syncs_all(w, private_repo, who):
    kw = {"cookies": private_repo} if who == "writer" else {"headers": ADMIN}
    w.release_idle_connections()
    r = w.client.post("/api/repos//sync", **kw)
    w.release_idle_connections()
    unknown = w.client.post("/api/repos/no-such-repo/sync", **kw)
    assert "private-repo" not in r.text
    assert (r.status_code, r.json()) == (unknown.status_code, unknown.json())


# -- social_reputation (MCP aggregate) ---------------------------------------


@pytest.fixture(scope="module")
def reputation(w):
    svc = KBService(w.config, w.db)
    svc.create_entry(
        PRIVATE, "alice-private", "Alice private", "note", "x", metadata={"author_id": "alice"}
    )
    svc.create_entry(
        READABLE, "alice-public", "Alice public", "note", "x", metadata={"author_id": "alice"}
    )
    w.index_worker.wait_for_idle(timeout=10)
    conn = w.db._raw_conn
    conn.executemany(
        "INSERT INTO social_vote (entry_id, kb_name, user_id, value, created_at) VALUES (?, ?, ?, ?, '2026-01-01')",
        [
            ("alice-private", PRIVATE, "bob", 1),
            ("alice-private", PRIVATE, "carol", 1),
            ("alice-public", READABLE, "bob", 1),
        ],
    )
    conn.executemany(
        "INSERT INTO social_reputation_log (user_id, delta, reason, kb_name, created_at) VALUES ('alice', ?, ?, ?, '2026-01-01')",
        [(10, "private", PRIVATE), (100, "legacy", None), (1000, "public", READABLE)],
    )
    conn.commit()


def test_social_reputation_scoped_caller_counts_only_readable_kbs(w, reputation):
    readable = set(w.principals["local_user"].readable_kbs)
    assert PRIVATE not in readable
    r = w.dispatch_tool("social_reputation", {"user_id": "alice"}, readable_kbs=readable)
    assert r["from_votes"] == 1
    assert r["from_adjustments"] == 1000
    assert r["reputation"] == 1001


@pytest.mark.control(reason="unscoped callers see what they saw before")
def test_social_reputation_unscoped_unchanged(w, reputation):
    r = w.dispatch_tool("social_reputation", {"user_id": "alice"}, readable_kbs=None)
    assert (r["from_votes"], r["from_adjustments"]) == (3, 1110)


def test_social_writeup_hook_records_kb_name(w):
    from pyrite.plugins.context import PluginContext
    from pyrite_social.entry_types import WriteupEntry
    from pyrite_social.hooks import after_save_update_counts

    entry = WriteupEntry(id="hooked-writeup", title="Hooked", author_id="dora")
    ctx = PluginContext(config=None, db=w.db, kb_name=READABLE, user="dora", operation="create")
    after_save_update_counts(entry, ctx)
    row = w.db._raw_conn.execute(
        "SELECT kb_name, entry_id FROM social_reputation_log WHERE user_id = 'dora'"
    ).fetchone()
    assert row["kb_name"] == READABLE
    assert row["entry_id"] == "hooked-writeup"

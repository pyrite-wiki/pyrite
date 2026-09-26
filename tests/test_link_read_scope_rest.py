"""REST reads learn nothing about a KB the caller cannot read (P-R4, P-R5).

Private #55 (entry without a KB), #56 (entry links), #57 (graph), #58 (QA
broken links), plus the paths the same grep found: the JSON export, the
daily note, QA orphans and status, and the entry read through a user's
worktree overlay. #518 (graph edges dropped for a scoped caller) is fixed
by the same change. Surface: `TestClient` against the real app.

The scoped caller is `local_user` (reads READABLE and READ_ONLY, not
PRIVATE). `admin_key` is unscoped: every test marked `control` pins that it
sees exactly what it saw before.
"""

from __future__ import annotations

import json

import pytest

from pyrite.services.kb_service import KBService
from pyrite.storage.backends.overlay_backend import WorktreeDB
from pyrite.storage.database import PyriteDB
from tests.characterization.world import MISSING, build_world
from tests.link_scope_seed import (
    MISSING_TARGET,
    PRIVATE,
    PRIVATE_ENTRY,
    PRIVATE_SPY,
    PRIVATE_SPY_TITLE,
    READ_ONLY,
    READABLE,
    READABLE_ENTRY,
    READABLE_POINTER,
    SHADOWED,
    seed_links,
)

DAILY_DATE = "2026-01-02"
DAILY_ID = f"daily-{DAILY_DATE}"
WANTED_DAILY_DATE = "2026-01-03"


@pytest.fixture(scope="module")
def w(tmp_path_factory):
    world = build_world(tmp_path_factory, label="link-scope-rest")
    seed_links(world)
    svc = KBService(world.config, world.db)
    svc.create_entry(READABLE, DAILY_ID, f"Daily Note - {DAILY_DATE}", "note", "today")
    svc.create_entry(
        PRIVATE,
        "private-diary-watcher",
        "Private diary watcher",
        "note",
        f"[[{READABLE}:{DAILY_ID}]] and [[{READABLE}:daily-{WANTED_DAILY_DATE}]]",
    )
    world.index_worker.wait_for_idle(timeout=10)
    try:
        yield world
    finally:
        world.close()


def _get(w, principal, url, **params):
    p = w.principals[principal]
    w.release_idle_connections()
    return w.client.get(url, params=params, headers=p.rest_headers, cookies=p.rest_cookies)


def _local(w, url, **params):
    return _get(w, "local_user", url, **params)


def _admin(w, url, **params):
    return _get(w, "admin_key", url, **params)


def _row(rows, target_id):
    [row] = [r for r in rows if r["id"] == target_id]
    return {k: v for k, v in row.items() if k != "id"}


# -- GET /entries/{id} without a KB (#55) ------------------------------------


def test_n1_entry_without_kb_conceals_private_entry(w):
    private = _local(w, f"/api/entries/{PRIVATE_ENTRY}")
    missing = _local(w, "/api/entries/definitely-not-there")
    assert private.status_code == missing.status_code == 404
    assert PRIVATE not in private.text
    assert private.text.replace(PRIVATE_ENTRY, "X") == missing.text.replace(
        "definitely-not-there", "X"
    )


def test_entry_without_kb_is_not_shadowed_by_a_private_twin(w):
    r = _local(w, f"/api/entries/{SHADOWED}")
    assert r.status_code == 200
    assert r.json()["kb_name"] == READ_ONLY


@pytest.mark.control(reason="unscoped caller keeps config order: the private twin answers")
def test_entry_without_kb_unscoped_unchanged(w):
    assert _admin(w, f"/api/entries/{SHADOWED}").json()["kb_name"] == PRIVATE


# -- entry links (#56) -------------------------------------------------------


@pytest.mark.parametrize("with_links", ["true", "false"])
def test_n2_readable_entry_links_do_not_reveal_private_rows(w, with_links):
    r = _local(w, f"/api/entries/{READABLE_ENTRY}", kb=READABLE, with_links=with_links)
    assert PRIVATE_SPY not in r.text and PRIVATE_SPY_TITLE not in r.text
    r2 = _local(w, f"/api/entries/{READABLE_POINTER}", kb=READABLE, with_links=with_links)
    assert "Private note" not in r2.text


def test_outlink_to_private_target_is_byte_identical_to_a_missing_one(w):
    rows = _local(w, f"/api/entries/{READABLE_POINTER}", kb=READABLE).json()["outlinks"]
    assert json.dumps(_row(rows, PRIVATE_ENTRY), sort_keys=True) == json.dumps(
        _row(rows, MISSING_TARGET), sort_keys=True
    )


@pytest.mark.control(reason="unscoped caller still sees private backlinks and titles")
def test_entry_links_unscoped_unchanged(w):
    r = _admin(w, f"/api/entries/{READABLE_ENTRY}", kb=READABLE)
    assert PRIVATE_SPY in {b["id"] for b in r.json()["backlinks"]}
    r2 = _admin(w, f"/api/entries/{READABLE_POINTER}", kb=READABLE)
    assert "Private note" in r2.text


# -- the worktree overlay (regime: overlay backend) --------------------------


@pytest.fixture
def overlay_reads(w, tmp_path):
    """Route REST entry reads through a real `WorktreeDB` overlay.

    `get_entry` swaps in the resolver's read service whenever a session
    user names a KB; a KB outside a git repo normally falls back to the
    main index, so the overlay path is entered here by handing it a resolver
    whose read service is backed by `WorktreeDB` (main index + an empty
    per-user diff), which is exactly what a user with a worktree reads.
    """
    from pyrite.server.api import get_worktree_resolver

    diff = PyriteDB(tmp_path / "diff.db")
    used: list[bool] = []

    class _Resolver:
        def get_read_service(self, kb_name, auth_user):
            used.append(True)
            return KBService(w.config, WorktreeDB(w.db, diff))

    w.app.dependency_overrides[get_worktree_resolver] = lambda: _Resolver()
    try:
        yield used
    finally:
        w.app.dependency_overrides.pop(get_worktree_resolver, None)
        diff.close()


def test_overlay_entry_links_are_scoped(w, overlay_reads):
    r = _local(w, f"/api/entries/{READABLE_ENTRY}", kb=READABLE)
    assert overlay_reads, "the overlay read service was not used"
    assert r.status_code == 200
    assert PRIVATE_SPY not in r.text
    rows = _local(w, f"/api/entries/{READABLE_POINTER}", kb=READABLE).json()["outlinks"]
    assert _row(rows, PRIVATE_ENTRY) == _row(rows, MISSING_TARGET)


# -- export and daily notes --------------------------------------------------


def test_json_export_carries_no_private_links(w):
    r = _local(w, "/api/entries/export", kb=READABLE, format="json")
    assert r.status_code == 200
    assert PRIVATE_SPY not in r.text and "Private note" not in r.text
    assert "private-diary-watcher" not in r.text


def test_daily_note_carries_no_private_backlinks(w):
    r = _local(w, f"/api/daily/{DAILY_DATE}", kb=READABLE)
    assert r.status_code == 200
    assert "private-diary-watcher" not in r.text


def test_created_daily_note_carries_no_private_backlinks(w):
    """A private entry linked the date before the note existed; a scoped
    writer then creates it (POST is write-tier, so its scope is asked of the
    policy in the handler rather than declared)."""
    from tests.auth_seed import SESSION_COOKIE, seed_user, sign_in

    from pyrite.services.auth_service import AuthService

    row = seed_user(w.db, "link-scope-writer", role="write")
    AuthService(w.db, w.config.settings.auth).grant_kb_permission(
        row["id"], READABLE, "write", granted_by=row["id"]
    )
    cookie = {SESSION_COOKIE: sign_in(w.app, "link-scope-writer")}
    w.release_idle_connections()
    r = w.client.post(f"/api/daily/{WANTED_DAILY_DATE}", params={"kb": READABLE}, cookies=cookie)
    assert r.status_code == 200, r.text
    assert "private-diary-watcher" not in r.text


# -- graph (#57, #518) -------------------------------------------------------


def test_n4_graph_centred_on_private_entry_answers_like_a_missing_kb(w):
    private = _local(w, "/api/graph", center=PRIVATE_SPY, center_kb=PRIVATE)
    missing_entry = _local(w, "/api/graph", center="nope", center_kb=PRIVATE)
    missing_kb = _local(w, "/api/graph", center=PRIVATE_SPY, center_kb=MISSING)
    assert private.status_code == missing_entry.status_code == missing_kb.status_code == 404
    assert private.text == missing_entry.text
    assert private.text.replace(PRIVATE, "K") == missing_kb.text.replace(MISSING, "K")
    assert private.json()["detail"]["code"] == "KB_NOT_FOUND"


def test_graph_counts_only_readable_links(w):
    full = _local(w, "/api/graph").json()
    assert {n["kb_name"] for n in full["nodes"]} <= {READABLE, READ_ONLY}
    assert READABLE_ENTRY not in {n["id"] for n in full["nodes"]}
    for n in full["nodes"]:
        touching = [
            e
            for e in full["edges"]
            if (e["source_id"], e["source_kb"]) == (n["id"], n["kb_name"])
            or (e["target_id"], e["target_kb"]) == (n["id"], n["kb_name"])
        ]
        assert n["link_count"] == len(touching), n


@pytest.mark.control(
    reason="held before the fix by the endpoint's filter after the query, which this "
    "change removes; it now guards the walk's own scoping"
)
def test_graph_walk_from_readable_centre_stays_readable(w):
    r = _local(w, "/api/graph", center=READABLE_ENTRY, center_kb=READABLE)
    assert r.status_code == 200
    assert PRIVATE_SPY not in r.text and PRIVATE_SPY_TITLE not in r.text


def test_n7_graph_keeps_readable_edges_for_scoped_caller(w):
    """#518: the scoped filter compared keys edges do not have, so every
    edge was dropped for a scoped caller."""
    r = _local(w, "/api/graph", kb=READABLE)
    assert any(e["source_id"] == "readable-a" for e in r.json()["edges"])


@pytest.mark.control(reason="unscoped graph unchanged: private nodes, counts and a missing KB")
def test_graph_unscoped_unchanged(w):
    r = _admin(w, "/api/graph", center=READABLE_ENTRY, center_kb=READABLE)
    assert PRIVATE_SPY in {n["id"] for n in r.json()["nodes"]}
    r2 = _admin(w, "/api/graph", center=PRIVATE_SPY, center_kb=MISSING)
    assert r2.status_code == 200 and r2.json() == {"nodes": [], "edges": []}


# -- QA (#58, orphans, status) -----------------------------------------------


def test_n5_qa_validate_entry_is_not_an_existence_oracle(w):
    r = _local(w, f"/api/qa/validate/{READABLE_POINTER}", kb=READABLE)
    assert r.status_code == 200
    assert (MISSING_TARGET in r.text) == (PRIVATE_ENTRY in r.text)
    broken = [i["message"] for i in r.json()["issues"] if i["rule"] == "broken_link"]
    [private] = [m for m in broken if PRIVATE_ENTRY in m]
    [missing] = [m for m in broken if MISSING_TARGET in m]
    assert private.replace(PRIVATE_ENTRY, "X") == missing.replace(MISSING_TARGET, "X")


def test_qa_validate_kb_reports_private_and_missing_targets_alike(w):
    r = _local(w, "/api/qa/validate", kb=READABLE)
    broken = [i["message"] for i in r.json()["issues"] if i["rule"] == "broken_link"]
    assert any(PRIVATE_ENTRY in m for m in broken)
    assert any(MISSING_TARGET in m for m in broken)


def test_qa_orphan_status_ignores_private_linkers(w):
    r = _local(w, "/api/qa/validate", kb=READABLE)
    orphans = {i["entry_id"] for i in r.json()["issues"] if i["rule"] == "orphan_entry"}
    # Only the private spy links to it: for this caller it is an orphan.
    assert READABLE_ENTRY in orphans


def test_qa_validate_all_and_status_are_scoped(w):
    r = _local(w, "/api/qa/validate")
    assert "Private note" not in r.text
    ids = {i["entry_id"] for kb in r.json()["kbs"] for i in kb["issues"]}
    assert READABLE_ENTRY in ids  # orphan, as above
    scoped = _local(w, "/api/qa/status", kb=READABLE).json()["issues_by_rule"]
    unscoped = _admin(w, "/api/qa/status", kb=READABLE).json()["issues_by_rule"]
    # For the scoped caller the private target is broken (one more than the
    # unscoped count), and every readable entry linked only from the
    # private KB -- the readable note and the daily note(s) -- is an orphan.
    assert scoped["broken_link"] == unscoped["broken_link"] + 1
    assert scoped["orphan_entry"] >= unscoped.get("orphan_entry", 0) + 2


@pytest.mark.control(
    reason="a consistency guard: before the fix both were unscoped alike; it pins that "
    "the all-KBs status passes the readable set on to the checks, as validate does"
)
def test_qa_status_without_kb_counts_like_validate(w):
    """The all-KBs status is the sum of the scoped all-KBs validation."""
    status = _local(w, "/api/qa/status").json()["issues_by_rule"]
    swept = _local(w, "/api/qa/validate").json()["kbs"]
    broken = sum(1 for kb in swept for i in kb["issues"] if i["rule"] == "broken_link")
    orphans = sum(1 for kb in swept for i in kb["issues"] if i["rule"] == "orphan_entry")
    assert status["broken_link"] == broken
    assert status["orphan_entry"] == orphans


@pytest.mark.control(reason="unscoped QA unchanged: private target is not broken, note not orphan")
def test_qa_unscoped_unchanged(w):
    r = _admin(w, "/api/qa/validate", kb=READABLE)
    broken = [i["message"] for i in r.json()["issues"] if i["rule"] == "broken_link"]
    assert not any(PRIVATE_ENTRY in m for m in broken)
    orphans = {i["entry_id"] for i in r.json()["issues"] if i["rule"] == "orphan_entry"}
    assert READABLE_ENTRY not in orphans

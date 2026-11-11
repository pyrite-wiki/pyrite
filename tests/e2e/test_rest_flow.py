"""REST over a real socket: create a KB, write into it, find it — no restart.

Regression for PR #4. `KBRegistryService.add_kb()` wrote the KB to the `kb`
table but never refreshed `PyriteConfig._db_kb_cache`, which `create_app()`
populates exactly once on first DB access. So the KB existed in the database
and was invisible to every name lookup — including entry creation — for the
rest of the process's life. A `POST /api/kbs` followed by `POST /api/entries`
returned 404 KB_NOT_FOUND until the pod was restarted.

Nothing in the unit suite could see this: it is a property of one long-lived
process serving several requests, and a TestClient test that builds a fresh app
per test never has a stale cache to be wrong about.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def test_health_is_served(live_server):
    resp = live_server.client.get("/health")
    assert resp.status_code == 200, live_server.output()
    assert resp.json()["status"] == "ok"


def test_seeded_entry_is_searchable(live_server):
    resp = live_server.client.get("/api/search", params={"q": "Babbage", "kb": "smoke"})
    assert resp.status_code == 200, resp.text
    titles = [r["title"] for r in resp.json()["results"]]
    assert "Analytical Engine" in titles, titles


def test_kb_created_over_rest_accepts_a_write_without_a_restart(live_server, seeded_data_dir):
    """PR #4's bug class, end to end.

    Three requests against ONE server process:
      1. POST /api/kbs         -> the KB is registered in the DB
      2. POST /api/entries     -> must NOT 404; this is where the stale
                                  in-memory registry cache used to bite
      3. GET  /api/search      -> the entry is findable

    If this fails with 404 KB_NOT_FOUND on step 2, the registry cache is stale
    again -- the fourth occurrence of that class (issues #1, #2, 37a37c9, #4).
    """
    # Under the server's kb_roots (see the live_server fixture).
    kb_path = seeded_data_dir / "created-over-rest"

    created = live_server.client.post(
        "/api/kbs",
        json={
            "name": "rest-made",
            "path": str(kb_path),
            "kb_type": "generic",
            "description": "created through the API, written to immediately",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["created"] is True

    entry = live_server.client.post(
        "/api/entries",
        json={
            "kb": "rest-made",
            "entry_type": "note",
            "title": "Written without a restart",
            "body": "The registry cache must see a KB the same process just created.",
            "tags": ["smoke"],
        },
    )
    assert entry.status_code == 200, (
        f"POST /api/entries into a KB this process just created returned "
        f"{entry.status_code}: {entry.text}\n"
        f"A 404 KB_NOT_FOUND here is PR #4's stale registry cache, back again."
    )

    live_server.client.post("/api/index/sync?wait=true")

    found = live_server.client.get("/api/search", params={"q": "restart", "kb": "rest-made"})
    assert found.status_code == 200, found.text
    titles = [r["title"] for r in found.json()["results"]]
    assert "Written without a restart" in titles, titles

    # The other half of the same invariant: all_kbs() sees it too. Asserted
    # here rather than in its own test because the server fixture is
    # module-scoped and xdist may not give two tests the same worker.
    listed = live_server.client.get("/api/kbs")
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    kbs = payload["kbs"] if isinstance(payload, dict) else payload
    assert {"smoke", "rest-made"} <= {kb["name"] for kb in kbs}

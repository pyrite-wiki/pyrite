"""A shortname wikilink works over REST once the web resolves the prefix (#952).

The web builds `[[pb:note]]` as a link to the entry page and a call to
`/api/entries/resolve` (and `/api/entries/{id}`) naming the KB. REST takes
the full name only (a shortname is link syntax, refused with the full name
suggested), so the flow is: `/api/kbs` lists each KB's `shortname`, the web
maps the prefix to the full name (`kbStore.resolveName`), and the request
names the full name. This pins the two halves REST owns: the listing carries
the shortname, and the full name resolves what the shortname refuses.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")


@pytest.fixture
def client(make_client):
    client, config, db = make_client(api_key="", kb_name="pub")
    config.knowledge_bases[0].shortname = "pb"
    db._raw_conn.execute(
        "INSERT OR IGNORE INTO kb (name, kb_type, path) VALUES (?, ?, ?)",
        ("pub", "generic", str(config.knowledge_bases[0].path)),
    )
    db._raw_conn.execute(
        """INSERT OR REPLACE INTO entry (id, kb_name, entry_type, title, body, date, importance)
           VALUES ('note', 'pub', 'note', 'A note', 'body', '2024-01-01', 5)"""
    )
    db._raw_conn.commit()
    return client


def test_the_kb_listing_carries_the_shortname(client):
    kbs = {kb["name"]: kb for kb in client.get("/api/kbs").json()["kbs"]}
    assert kbs["pub"]["shortname"] == "pb"


def test_the_full_name_resolves_what_the_prefix_names(client):
    resolved = client.get("/api/entries/resolve", params={"target": "note", "kb": "pub"}).json()
    assert resolved["resolved"] is True and resolved["entry"]["kb_name"] == "pub"
    entry = client.get("/api/entries/note", params={"kb": "pub"})
    assert entry.status_code == 200 and entry.json()["id"] == "note"


def test_the_prefix_itself_is_refused_with_the_full_name(client):
    """What the web would get without resolving: the refusal that names the
    full name, so a stale link's error still says which KB was meant."""
    for path, params in (
        ("/api/entries/resolve", {"target": "note", "kb": "pb"}),
        ("/api/entries/note", {"kb": "pb"}),
    ):
        resp = client.get(path, params=params)
        detail = resp.json()["detail"]
        assert resp.status_code == 404, detail
        assert detail["code"] == "KB_NOT_FOUND"
        assert detail["did_you_mean"] == ["pub"]
        assert "[[pb:" in detail["hint"]

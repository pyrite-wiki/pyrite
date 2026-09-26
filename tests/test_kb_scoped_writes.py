"""Every REST write checks the caller's per-KB role on the KB it changes.

A global `requires_tier("write")` is never enough for a KB-scoped write, and
`requires_kb_tier` on a route whose request names no KB used to fall back to
the *global* role. Three routes relied on one or the other:

- `POST /api/clip` checked only the global tier, then wrote into `req.kb`.
  Its KB-not-found 404 ran before any KB check, so it was also an oracle.
- `DELETE /api/reviews/{review_id}` names no KB, so `requires_kb_tier`
  checked the global role and any write-tier user could delete a review on
  a private KB by id.
- `/api/starred` rows had no owner: any write-tier user could unstar or
  reorder everyone's stars, and star an entry in a KB they cannot read.

The rule these tests pin: a KB the caller cannot read answers **404, exactly
like one that does not exist**; a KB the caller can read but not write
answers 403. Stars belong to the user who made them.

Driven through `TestClient` on a real app with auth enabled and real session
cookies -- the surface where the per-KB role chain actually runs.
"""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app
from pyrite.services.auth_service import AuthService
from pyrite.services.clipper import ClipperService, ClipResult
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_user

PRIVATE = "p"  # default_role none: invisible without a grant
READONLY = "k"  # default_role read: readable, not writable, by a non-admin
OPEN = "w"  # no default_role: a user's global role applies


def _token(db, config, username, password="password123") -> str:
    """A session token for a seeded user (the app's own requests read the
    same database file)."""
    return AuthService(db, config.settings.auth).login(username, password)[1]


@pytest.fixture
def stub_clip(monkeypatch):
    """Stub only the network fetch; the clip route itself runs for real."""

    async def fake_clip(self, url, title=None):
        return ClipResult(title=title or "Clipped", body="clipped body", source_url=url)

    monkeypatch.setattr(ClipperService, "clip_url", fake_clip)


@pytest.fixture
def env(stub_clip):
    """Auth on. `admin` (first user), and `alice` and `bob` with global role
    `write` and no grants. One entry in each KB, indexed."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for name in (PRIVATE, READONLY, OPEN):
            (tmp / name).mkdir()
        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name=PRIVATE, path=tmp / PRIVATE, kb_type="generic", default_role="none"),
                KBConfig(
                    name=READONLY, path=tmp / READONLY, kb_type="generic", default_role="read"
                ),
                KBConfig(name=OPEN, path=tmp / OPEN, kb_type="generic"),
            ],
            settings=Settings(
                index_path=tmp / "index.db",
                auth=AuthConfig(enabled=True, allow_registration=True),
            ),
        )
        app = create_app(config=config)
        db = PyriteDB(config.settings.index_path)
        # Operator-path seeding: admin is the first user;
        # alice and bob get global "write" (covering every KB, as a registrant
        # used to), matching what this test's assertions rely on.
        seed_user(db, "admin", "password123", role="admin")
        seed_user(db, "alice", "password123", role="write")
        seed_user(db, "bob", "password123", role="write")
        tokens = {
            name: _token(db, config, name, "password123") for name in ("admin", "alice", "bob")
        }
        auth = AuthService(db, config.settings.auth)
        users = {u["username"]: u["id"] for u in auth.list_users()}
        for kb in (PRIVATE, READONLY, OPEN):
            db.register_kb(kb, "generic", str(tmp / kb))
            db.upsert_entry(
                {
                    "id": f"entry-{kb}",
                    "kb_name": kb,
                    "entry_type": "note",
                    "title": f"Entry in {kb}",
                    "body": "body",
                    "file_path": str(tmp / kb / f"entry-{kb}.md"),
                }
            )
        try:
            yield {"app": app, "tokens": tokens, "db": db, "tmp": tmp, "users": users}
        finally:
            db.close()


def _as(env, user) -> TestClient:
    c = TestClient(env["app"])
    c.cookies.set("pyrite_session", env["tokens"][user])
    return c


def _clip(client, kb, title="Clipped page"):
    return client.post("/api/clip", json={"url": "https://example.com/a", "kb": kb, "title": title})


# ---------------------------------------------------------------------------
# POST /api/clip
# ---------------------------------------------------------------------------


class TestClip:
    def test_private_kb_answers_404_and_writes_nothing(self, env):
        r = _clip(_as(env, "alice"), PRIVATE, title="Should not exist")
        assert r.status_code == 404, r.text
        assert not list((env["tmp"] / PRIVATE).rglob("*.md")), "a file was written"
        rows = env["db"].execute_sql(
            "SELECT id FROM entry WHERE kb_name = :kb AND title = :t",
            {"kb": PRIVATE, "t": "Should not exist"},
        )
        assert rows == []

    def test_private_kb_answers_exactly_like_a_missing_one(self, env):
        client = _as(env, "alice")
        private = _clip(client, PRIVATE)
        missing = _clip(client, "no-such-kb")
        assert private.status_code == missing.status_code == 404
        assert private.json()["detail"]["code"] == missing.json()["detail"]["code"]
        assert private.json()["detail"]["message"].replace(PRIVATE, "X") == missing.json()[
            "detail"
        ]["message"].replace("no-such-kb", "X")

    def test_readable_but_not_writable_kb_answers_403(self, env):
        r = _clip(_as(env, "alice"), READONLY)
        assert r.status_code == 403, r.text
        assert not list((env["tmp"] / READONLY).rglob("*.md"))

    def test_writable_kb_still_clips(self, env):
        r = _clip(_as(env, "alice"), OPEN)
        assert r.status_code == 200, r.text
        assert r.json()["kb_name"] == OPEN

    def test_admin_can_clip_into_private_kb(self, env):
        assert _clip(_as(env, "admin"), PRIVATE).status_code == 200


# ---------------------------------------------------------------------------
# DELETE /api/reviews/{review_id}
# ---------------------------------------------------------------------------


def _review(env, kb) -> int:
    return env["db"].create_review(
        entry_id=f"entry-{kb}",
        kb_name=kb,
        content_hash="0" * 40,
        reviewer="admin",
        reviewer_type="user",
        result="pass",
    )["id"]


def _review_exists(env, review_id) -> bool:
    return bool(env["db"].execute_sql("SELECT id FROM review WHERE id = :i", {"i": review_id}))


class TestDeleteReview:
    def test_review_on_private_kb_answers_404_and_survives(self, env):
        rid = _review(env, PRIVATE)
        r = _as(env, "alice").delete(f"/api/reviews/{rid}")
        assert r.status_code == 404, r.text
        assert _review_exists(env, rid)

    def test_review_on_private_kb_answers_exactly_like_a_missing_review(self, env):
        rid = _review(env, PRIVATE)
        client = _as(env, "alice")
        private = client.delete(f"/api/reviews/{rid}")
        missing = client.delete("/api/reviews/999999")
        assert private.status_code == missing.status_code == 404
        assert private.json()["detail"]["code"] == missing.json()["detail"]["code"]
        assert private.json()["detail"]["message"].replace(str(rid), "N") == missing.json()[
            "detail"
        ]["message"].replace("999999", "N")

    def test_review_on_readable_kb_answers_403_and_survives(self, env):
        rid = _review(env, READONLY)
        r = _as(env, "alice").delete(f"/api/reviews/{rid}")
        assert r.status_code == 403, r.text
        assert _review_exists(env, rid)

    def test_review_on_writable_kb_is_deleted(self, env):
        rid = _review(env, OPEN)
        r = _as(env, "alice").delete(f"/api/reviews/{rid}")
        assert r.status_code == 200, r.text
        assert not _review_exists(env, rid)

    def test_a_kb_query_param_cannot_redirect_the_check(self, env):
        """The row's own KB is the one checked -- naming a writable KB in the
        query buys nothing."""
        rid = _review(env, PRIVATE)
        r = _as(env, "alice").delete(f"/api/reviews/{rid}", params={"kb": OPEN})
        assert r.status_code == 404, r.text
        assert _review_exists(env, rid)


# ---------------------------------------------------------------------------
# /api/starred -- per-user rows
# ---------------------------------------------------------------------------


def _starred(client) -> list[tuple[str, str]]:
    r = client.get("/api/starred")
    assert r.status_code == 200, r.text
    return [(s["entry_id"], s["kb_name"]) for s in r.json()["starred"]]


class TestStarred:
    def test_star_in_private_kb_answers_404_and_stores_nothing(self, env):
        r = _as(env, "alice").post("/api/starred", json={"entry_id": "entry-p", "kb_name": PRIVATE})
        assert r.status_code == 404, r.text
        assert env["db"].execute_sql("SELECT * FROM starred_entry") == []

    def test_stars_belong_to_the_user_who_made_them(self, env):
        alice, bob = _as(env, "alice"), _as(env, "bob")
        assert alice.post("/api/starred", json={"entry_id": "entry-w", "kb_name": OPEN}).is_success
        assert _starred(alice) == [("entry-w", OPEN)]
        assert _starred(bob) == []

    def test_another_user_cannot_unstar_mine(self, env):
        alice, bob = _as(env, "alice"), _as(env, "bob")
        alice.post("/api/starred", json={"entry_id": "entry-w", "kb_name": OPEN})
        r = bob.delete("/api/starred/entry-w", params={"kb": OPEN})
        assert r.status_code == 404, r.text
        assert _starred(alice) == [("entry-w", OPEN)]

    def test_another_user_cannot_reorder_mine(self, env):
        alice, bob = _as(env, "alice"), _as(env, "bob")
        alice.post("/api/starred", json={"entry_id": "entry-w", "kb_name": OPEN})
        alice.post("/api/starred", json={"entry_id": "entry-k", "kb_name": READONLY})
        before = alice.get("/api/starred").json()["starred"]
        r = bob.put(
            "/api/starred/reorder",
            json={
                "entries": [
                    {"entry_id": "entry-w", "kb_name": OPEN, "sort_order": 99},
                    {"entry_id": "entry-k", "kb_name": READONLY, "sort_order": -5},
                ]
            },
        )
        assert r.status_code == 200, r.text
        assert alice.get("/api/starred").json()["starred"] == before

    def test_two_users_may_star_the_same_entry(self, env):
        alice, bob = _as(env, "alice"), _as(env, "bob")
        assert alice.post("/api/starred", json={"entry_id": "entry-w", "kb_name": OPEN}).is_success
        assert bob.post("/api/starred", json={"entry_id": "entry-w", "kb_name": OPEN}).is_success
        bob.delete("/api/starred/entry-w", params={"kb": OPEN})
        assert _starred(alice) == [("entry-w", OPEN)]
        assert _starred(bob) == []

    def test_starring_twice_is_still_idempotent(self, env):
        alice = _as(env, "alice")
        for _ in range(2):
            assert alice.post(
                "/api/starred", json={"entry_id": "entry-w", "kb_name": OPEN}
            ).is_success
        assert _starred(alice) == [("entry-w", OPEN)]


# ---------------------------------------------------------------------------
# Migration v24: an index built before stars were per-user
# ---------------------------------------------------------------------------


def test_existing_stars_survive_the_migration_as_the_instance_list(tmp_path):
    """A database whose starred_entry predates user_id keeps its stars, as the
    instance's own list (user 0), and gains the per-user unique constraint."""
    import sqlite3

    path = tmp_path / "index.db"
    PyriteDB(path).close()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        DROP TABLE starred_entry;
        CREATE TABLE starred_entry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id VARCHAR NOT NULL,
            kb_name VARCHAR NOT NULL,
            sort_order INTEGER,
            created_at VARCHAR NOT NULL,
            CONSTRAINT uq_starred_entry UNIQUE (entry_id, kb_name)
        );
        INSERT INTO starred_entry (entry_id, kb_name, sort_order, created_at)
            VALUES ('a', 'kb1', 1, '2026-01-01'), ('b', 'kb1', 2, '2026-01-02');
        DELETE FROM schema_version WHERE version >= 24;
        """
    )
    conn.commit()
    conn.close()

    db = PyriteDB(path)
    try:
        rows = db.execute_sql(
            "SELECT user_id, entry_id, kb_name, sort_order FROM starred_entry ORDER BY id"
        )
        assert rows == [
            {"user_id": 0, "entry_id": "a", "kb_name": "kb1", "sort_order": 1},
            {"user_id": 0, "entry_id": "b", "kb_name": "kb1", "sort_order": 2},
        ]
        # A second user may now star the same entry.
        db.execute_write_sql(
            "INSERT INTO starred_entry (user_id, entry_id, kb_name, sort_order, created_at) "
            "VALUES (7, 'a', 'kb1', 1, '2026-01-03')"
        )
        assert db.get_schema_version() >= 24
    finally:
        db.close()


def test_anonymous_visitor_has_no_star_list(tmp_path):
    """An anonymous visitor -- even with a write anonymous tier -- cannot star:
    sharing the instance's list would put every visitor's stars in one list."""
    (tmp_path / OPEN).mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=OPEN, path=tmp_path / OPEN, kb_type="generic")],
        settings=Settings(
            index_path=tmp_path / "index.db",
            auth=AuthConfig(enabled=True, allow_registration=True, anonymous_tier="write"),
        ),
    )
    client = TestClient(create_app(config=config))
    r = client.post("/api/starred", json={"entry_id": "x", "kb_name": OPEN})
    assert r.status_code == 401, r.text
    assert client.get("/api/starred").json() == {"count": 0, "starred": []}


def test_a_failed_v24_rebuild_leaves_the_old_table_intact(tmp_path):
    """The rebuild is one transaction: a failure after the old table is dropped
    (here, the last index cannot be created because a view holds its name)
    rolls everything back -- the old table, its rows, no leftover copy, and
    v24 not recorded -- so the next start can simply try again."""
    import sqlite3

    from pyrite.storage.migrations import MigrationError, MigrationManager

    path = tmp_path / "index.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE starred_entry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id VARCHAR NOT NULL,
            kb_name VARCHAR NOT NULL,
            sort_order INTEGER,
            created_at VARCHAR NOT NULL,
            CONSTRAINT uq_starred_entry UNIQUE (entry_id, kb_name)
        );
        INSERT INTO starred_entry (entry_id, kb_name, sort_order, created_at)
            VALUES ('a', 'kb1', 1, '2026-01-01');
        CREATE VIEW idx_starred_entry_sort AS SELECT 1;
        """
    )
    conn.commit()
    mgr = MigrationManager(conn)
    for version in range(1, 24):
        conn.execute(
            "INSERT INTO schema_version (version, description, applied_at) VALUES (?, 'x', 'x')",
            (version,),
        )
    conn.commit()

    with pytest.raises(MigrationError):
        mgr.migrate()

    columns = [row[1] for row in conn.execute("PRAGMA table_info(starred_entry)")]
    assert "user_id" not in columns, "the old table was replaced"
    assert conn.execute("SELECT entry_id, kb_name FROM starred_entry").fetchall() == [("a", "kb1")]
    assert (
        conn.execute("SELECT name FROM sqlite_master WHERE name = 'starred_entry_v24'").fetchone()
        is None
    ), "a half-built copy was left behind"
    assert mgr.get_current_version() == 23
    conn.close()


# ---------------------------------------------------------------------------
# The row form's no-identity floor: an operator key below the tier learns
# nothing about which rows exist
# ---------------------------------------------------------------------------


def test_read_tier_operator_key_gets_the_same_refusal_for_missing_and_existing_reviews(
    tmp_path,
):
    """A read-tier API key has the same role on every KB, so the row form
    refuses it on tier *before* looking the review up. Without that floor, a
    missing id answered 404 (the resolver) and an existing id 403 (the per-KB
    check) -- an existence oracle for review ids."""
    import hashlib

    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="kb", path=kb_path, kb_type="generic")],
        settings=Settings(
            index_path=tmp_path / "index.db",
            api_keys=[
                {
                    "key_hash": hashlib.sha256(b"read-key").hexdigest(),
                    "role": "read",
                    "label": "Reader",
                }
            ],
        ),
    )
    app = create_app(config=config)
    db = PyriteDB(config.settings.index_path)
    try:
        db.register_kb("kb", "generic", str(kb_path))
        db.upsert_entry(
            {
                "id": "entry-kb",
                "kb_name": "kb",
                "entry_type": "note",
                "title": "Entry",
                "body": "body",
                "file_path": str(kb_path / "entry-kb.md"),
            }
        )
        rid = db.create_review(
            entry_id="entry-kb",
            kb_name="kb",
            content_hash="0" * 40,
            reviewer="admin",
            reviewer_type="user",
            result="pass",
        )["id"]
        client = TestClient(app, headers={"X-API-Key": "read-key"})
        existing = client.delete(f"/api/reviews/{rid}")
        missing = client.delete("/api/reviews/999999")
        assert existing.status_code == 403, existing.text
        assert missing.status_code == 403, missing.text
        assert existing.json() == missing.json(), "the refusal differs by whether the row exists"
        assert db.get_review(rid) is not None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# A KB named in a body that is not declared JSON is still checked
# ---------------------------------------------------------------------------


def test_json_body_without_content_type_is_checked_by_the_guard(env):
    """`_resolve_kb_names` reads the body whatever its Content-Type, so
    the private KB named in an undeclared body is refused by the guard itself
    -- the same 404 as a declared one -- instead of depending on FastAPI's
    `strict_content_type` to keep the body from the handler."""
    import json

    client = _as(env, "alice")
    r = client.post(
        "/api/entries",
        content=json.dumps({"kb": PRIVATE, "title": "Smuggled", "body": "x"}).encode(),
    )
    assert "content-type" not in {k.lower() for k in r.request.headers}
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "KB_NOT_FOUND", r.text
    assert not list((env["tmp"] / PRIVATE).rglob("*.md")), "a file was written"
    assert (
        env["db"].execute_sql(
            "SELECT id FROM entry WHERE kb_name = :kb AND title = 'Smuggled'", {"kb": PRIVATE}
        )
        == []
    )

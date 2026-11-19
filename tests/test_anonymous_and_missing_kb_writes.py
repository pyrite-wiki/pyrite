"""Per-KB write checks for the anonymous visitor, and private == missing on writes.

Two holes with one root cause -- the per-KB role rule was not the one rule:

1. **The anonymous visitor.** `resolve_effective_kb_role` returned the global
   role whenever there was no signed-in user. For an anonymous visitor that is
   the instance-wide `anonymous_tier`, so with `anonymous_tier: write` every KB
   was writable -- a private (`default_role: none`) one, and a public read-only
   (`default_role: read`) one. Reads already used the right rule
   (`effective_kb_role_for_user(config, db, None, kb)` via `readable_kbs`);
   writes now use it too. An operator API key (no user, *not* anonymous) is
   still unscoped and keeps its global role.
2. **Private versus missing.** A KB the caller cannot read must answer every
   write route exactly like a KB that does not exist, or the answer tells an
   outsider which private KB names exist.

Also: `anonymous_tier` is validated at config load. `admin` is refused.

Driven through `TestClient` on a real app with auth enabled; the anonymous
visitor sends no credential at all.
"""

import os
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings, _apply_env_overrides
from pyrite.server.api import create_app
from pyrite.services.auth_service import AuthService
from pyrite.services.clipper import ClipperService, ClipResult
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_user

PRIVATE = "p"  # default_role none
READONLY = "k"  # default_role read
OPEN = "w"  # no default_role: the caller's global role (anonymous: anonymous_tier)
MISSING = "no-such-kb"


def _token(db, config, username, password="password123") -> str:
    """A session token for a seeded user (the app's own requests read the
    same database file)."""
    return AuthService(db, config.settings.auth).login(username, password)[1]


@pytest.fixture
def stub_clip(monkeypatch):
    async def fake_clip(self, url, title=None):
        return ClipResult(title=title or "Clipped", body="clipped body", source_url=url)

    monkeypatch.setattr(ClipperService, "clip_url", fake_clip)


def _build(tmp: Path, anonymous_tier: str | None):
    for name in (PRIVATE, READONLY, OPEN):
        (tmp / name).mkdir()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name=PRIVATE, path=tmp / PRIVATE, kb_type="generic", default_role="none"),
            KBConfig(name=READONLY, path=tmp / READONLY, kb_type="generic", default_role="read"),
            KBConfig(name=OPEN, path=tmp / OPEN, kb_type="generic"),
        ],
        settings=Settings(
            index_path=tmp / "index.db",
            auth=AuthConfig(enabled=True, allow_registration=True, anonymous_tier=anonymous_tier),
        ),
    )
    app = create_app(config=config)
    db = PyriteDB(config.settings.index_path)
    # Seed via the operator path: registration is closed
    # until an admin exists, and a self-registered user no longer reads every
    # KB. admin is the first user seeded; alice gets global "write" (covering
    # every KB the way a registrant used to), matching what this test needs.
    seed_user(db, "admin", "password123", role="admin")
    seed_user(db, "alice", "password123", role="write")
    tokens = {
        "admin": _token(db, config, "admin", "password123"),
        "alice": _token(db, config, "alice", "password123"),
    }
    auth = AuthService(db, config.settings.auth)
    reviews = {}
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
        reviews[kb] = db.create_review(
            entry_id=f"entry-{kb}",
            kb_name=kb,
            content_hash="0" * 40,
            reviewer="admin",
            reviewer_type="user",
            result="pass",
        )["id"]
    return {"app": app, "db": db, "tokens": tokens, "reviews": reviews, "tmp": tmp}


@pytest.fixture(params=["read", "write"])
def anon_env(request, stub_clip):
    with tempfile.TemporaryDirectory() as d:
        env = _build(Path(d), request.param)
        env["tier"] = request.param
        try:
            yield env
        finally:
            env["db"].close()


def _anon(env) -> TestClient:
    return TestClient(env["app"])


def _clip(client, kb):
    return client.post(
        "/api/clip", json={"url": "https://example.com/a", "kb": kb, "title": "Clipped page"}
    )


def _create(client, kb):
    return client.post("/api/entries", json={"kb": kb, "title": "Anon entry", "body": "x"})


def _review_exists(env, rid) -> bool:
    return bool(env["db"].execute_sql("SELECT id FROM review WHERE id = :i", {"i": rid}))


# ---------------------------------------------------------------------------
# 1. The anonymous visitor, at anonymous_tier read and write
# ---------------------------------------------------------------------------


class TestAnonymousVisitor:
    def test_clip_into_private_kb_is_404(self, anon_env):
        r = _clip(_anon(anon_env), PRIVATE)
        assert r.status_code == 404, r.text
        assert not list((anon_env["tmp"] / PRIVATE).rglob("*.md"))

    def test_review_delete_on_private_kb_is_404(self, anon_env):
        rid = anon_env["reviews"][PRIVATE]
        r = _anon(anon_env).delete(f"/api/reviews/{rid}")
        assert r.status_code == 404, r.text
        assert _review_exists(anon_env, rid)

    def test_clip_into_public_read_only_kb_is_403(self, anon_env):
        r = _clip(_anon(anon_env), READONLY)
        assert r.status_code == 403, r.text
        assert not list((anon_env["tmp"] / READONLY).rglob("*.md"))

    def test_review_delete_on_public_read_only_kb_is_403(self, anon_env):
        rid = anon_env["reviews"][READONLY]
        r = _anon(anon_env).delete(f"/api/reviews/{rid}")
        assert r.status_code == 403, r.text
        assert _review_exists(anon_env, rid)

    def test_create_entry_in_private_kb_is_404(self, anon_env):
        r = _create(_anon(anon_env), PRIVATE)
        assert r.status_code == 404, r.text

    def test_create_entry_in_public_read_only_kb_is_403(self, anon_env):
        r = _create(_anon(anon_env), READONLY)
        assert r.status_code == 403, r.text

    def test_kb_without_default_role_follows_anonymous_tier(self, anon_env):
        """Unchanged behaviour: no default_role, so anonymous_tier decides."""
        r = _create(_anon(anon_env), OPEN)
        if anon_env["tier"] == "write":
            assert r.status_code in (200, 201), r.text
        else:
            assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# 2. A private KB answers every write route exactly like a missing one
# ---------------------------------------------------------------------------

WRITE_CALLS = {
    "POST /api/entries": lambda c, kb: c.post(
        "/api/entries", json={"kb": kb, "title": "T", "body": "x"}
    ),
    "PUT /api/entries/{id}": lambda c, kb: c.put(
        "/api/entries/entry-x", json={"kb": kb, "title": "T"}
    ),
    "PATCH /api/entries/{id}": lambda c, kb: c.patch(
        "/api/entries/entry-x", json={"kb": kb, "field": "status", "value": "done"}
    ),
    "DELETE /api/entries/{id}": lambda c, kb: c.delete("/api/entries/entry-x", params={"kb": kb}),
    "POST /api/reviews": lambda c, kb: c.post(
        "/api/reviews",
        json={
            "entry_id": "entry-x",
            "kb_name": kb,
            "reviewer": "r",
            "reviewer_type": "user",
            "result": "pass",
        },
    ),
    "POST /api/collections": lambda c, kb: c.post(
        "/api/collections", json={"kb": kb, "title": "C", "query": "type:note"}
    ),
    "POST /api/tasks/{id}/claim": lambda c, kb: c.post(
        "/api/tasks/task-x/claim", params={"kb": kb}, json={"assignee": "me"}
    ),
    "POST /api/daily/{date}": lambda c, kb: c.post("/api/daily/2026-01-01", params={"kb": kb}),
    "POST /api/clip": _clip,
}


@pytest.fixture
def write_env(stub_clip):
    with tempfile.TemporaryDirectory() as d:
        env = _build(Path(d), "write")
        try:
            yield env
        finally:
            env["db"].close()


def _normalised(resp, kb):
    # The name as a word, not as a substring: PRIVATE is "p", and the
    # refusal's hint names `pyrite orient`.
    text = re.sub(rf"(?<![A-Za-z0-9_-]){re.escape(kb)}(?![A-Za-z0-9_-])", "<KB>", resp.text)
    return resp.status_code, text


@pytest.mark.parametrize("route", sorted(WRITE_CALLS))
@pytest.mark.parametrize("who", ["alice", "anonymous"])
def test_private_kb_answers_like_a_missing_one(write_env, route, who):
    client = TestClient(write_env["app"])
    if who != "anonymous":
        client.cookies.set("pyrite_session", write_env["tokens"][who])
    call = WRITE_CALLS[route]
    private = _normalised(call(client, PRIVATE), PRIVATE)
    missing = _normalised(call(client, MISSING), MISSING)
    assert private == missing, f"{route} as {who}: private {private} != missing {missing}"
    assert private[0] == 404, f"{route} as {who}: expected 404, got {private}"


# ---------------------------------------------------------------------------
# 3. anonymous_tier is validated
# ---------------------------------------------------------------------------


class TestAnonymousTierValidation:
    @pytest.mark.parametrize("value", ["admin", "Admin", "writer", "owner", "1"])
    def test_config_file_value_is_refused(self, value):
        with pytest.raises(ValueError, match="anonymous_tier"):
            PyriteConfig.from_dict({"settings": {"auth": {"anonymous_tier": value}}})

    def test_env_value_is_refused(self):
        config = PyriteConfig()
        with patch.dict(os.environ, {"PYRITE_AUTH_ANONYMOUS_TIER": "admin"}):
            with pytest.raises(ValueError, match="PYRITE_AUTH_ANONYMOUS_TIER"):
                _apply_env_overrides(config)

    def test_direct_construction_is_refused(self):
        with pytest.raises(ValueError, match="anonymous_tier"):
            AuthConfig(anonymous_tier="admin")

    @pytest.mark.parametrize("value", [None, "read", "write"])
    def test_valid_values_are_kept(self, value):
        config = PyriteConfig.from_dict({"settings": {"auth": {"anonymous_tier": value}}})
        assert config.settings.auth.anonymous_tier == value

    @pytest.mark.parametrize("value", ["none", "None", "NONE"])
    def test_none_string_means_no_anonymous_access(self, value):
        """The shipped compose files set PYRITE_AUTH_ANONYMOUS_TIER=none."""
        assert AuthConfig(anonymous_tier=value).anonymous_tier is None
        config = PyriteConfig()
        with patch.dict(os.environ, {"PYRITE_AUTH_ANONYMOUS_TIER": value}):
            _apply_env_overrides(config)
        assert config.settings.auth.anonymous_tier is None


# ---------------------------------------------------------------------------
# 4. anonymous_tier is a ceiling: min(anonymous_tier, KB default_role)
# ---------------------------------------------------------------------------

PUBLIC_WRITE = "pw"  # default_role write: a KB that invites writes


@pytest.fixture
def ceiling_env(request, stub_clip):
    tier = request.param
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for name in (PUBLIC_WRITE, PRIVATE):
            (tmp / name).mkdir()
        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(
                    name=PUBLIC_WRITE,
                    path=tmp / PUBLIC_WRITE,
                    kb_type="generic",
                    default_role="write",
                ),
                KBConfig(name=PRIVATE, path=tmp / PRIVATE, kb_type="generic", default_role="none"),
            ],
            settings=Settings(
                index_path=tmp / "index.db",
                auth=AuthConfig(enabled=True, anonymous_tier=tier),
            ),
        )
        yield {"app": create_app(config=config), "tmp": tmp, "config": config}


class TestAnonymousTierIsACeiling:
    @pytest.mark.parametrize("ceiling_env", ["read"], indirect=True)
    def test_read_tier_never_writes_even_where_the_kb_default_is_write(self, ceiling_env):
        client = TestClient(ceiling_env["app"])
        assert _clip(client, PUBLIC_WRITE).status_code == 403
        assert _create(client, PUBLIC_WRITE).status_code == 403
        assert not list((ceiling_env["tmp"] / PUBLIC_WRITE).rglob("*.md"))

    @pytest.mark.parametrize("ceiling_env", ["read"], indirect=True)
    def test_read_tier_still_reads_a_default_write_kb(self, ceiling_env):
        """The read side applies the same min(): read is still granted."""
        names = {kb["name"] for kb in TestClient(ceiling_env["app"]).get("/api/kbs").json()["kbs"]}
        assert PUBLIC_WRITE in names
        assert PRIVATE not in names

    @pytest.mark.parametrize("ceiling_env", ["write"], indirect=True)
    def test_write_tier_is_refused_by_a_private_kb(self, ceiling_env):
        client = TestClient(ceiling_env["app"])
        assert _clip(client, PRIVATE).status_code == 404
        assert _create(client, PRIVATE).status_code == 404

    @pytest.mark.parametrize("ceiling_env", ["write"], indirect=True)
    def test_write_tier_writes_a_default_write_kb(self, ceiling_env):
        r = _clip(TestClient(ceiling_env["app"]), PUBLIC_WRITE)
        assert r.status_code == 200, r.text


@pytest.mark.parametrize(
    ("tier", "default_role", "expected"),
    [
        ("read", "write", "read"),
        ("read", "admin", "read"),
        ("write", "admin", "write"),
        ("write", "read", "read"),
        ("write", "none", None),
        ("read", None, "read"),
        ("write", None, "write"),
        (None, "read", None),
    ],
)
def test_anonymous_role_is_the_lower_of_tier_and_kb_default(tmp_path, tier, default_role, expected):
    """The one rule, at the service: REST reads, REST writes, /ws and MCP all
    resolve the anonymous visitor through AuthService.get_kb_role."""
    db = PyriteDB(tmp_path / "index.db")
    try:
        auth = AuthService(db, AuthConfig(enabled=True, anonymous_tier=tier))
        assert auth.get_kb_role(None, "kb", default_role) == expected
    finally:
        db.close()

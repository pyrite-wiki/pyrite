"""Pulling into a repository that holds a `landing: review` KB is the operator's.

`POST /api/repos/{name}/sync` pulls upstream into a checkout, which writes
the canonical files of every KB in it. When any of them lands writes for
review (linked to the subscription, or with its folder inside the checkout),
the pull needs the operator: a global admin, an operator API key, the local
process (ADR-0044 §1). A repository whose KBs all land canonical keeps the
per-KB write rule.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig
from pyrite.services.auth_service import AuthService
from tests._world import build_world
from tests.auth_seed import seed_user

REVIEW, CANONICAL = "review-kb", "canonical-kb"
REVIEW_REPO, CANONICAL_REPO = "owner/reviewed", "owner/plain"


@pytest.fixture
def env(tmp_path: Path):
    world = build_world(
        tmp_path,
        kbs=[
            KBConfig(name=REVIEW, path=tmp_path / REVIEW, kb_type="generic", landing="review"),
            KBConfig(name=CANONICAL, path=tmp_path / CANONICAL, kb_type="generic"),
        ],
        settings={"auth": AuthConfig(enabled=True)},
    )
    db = world.db
    for repo_name, kb_name in ((REVIEW_REPO, REVIEW), (CANONICAL_REPO, CANONICAL)):
        repo = db.register_repo(repo_name, str(tmp_path / "workspace" / repo_name))
        db.link_kb_to_repo(kb_name, repo["id"], "")
    auth = AuthService(db, world.config.settings.auth)
    clients = []

    def client_for(username, *, role="write", grant=None):
        c = TestClient(world.app)
        clients.append(c)
        user = seed_user(db, username, "password123", role=role)
        if grant:
            for kb in (REVIEW, CANONICAL):
                auth.grant_kb_permission(user["id"], kb, grant, user["id"])
        c.cookies.set("pyrite_session", auth.login(username, "password123")[1])
        return c

    try:
        yield {
            "admin": client_for("root", role="admin"),
            "writer": client_for("writer", grant="write"),
            "kb_admin": client_for("kb-admin", grant="admin"),
        }
    finally:
        for c in clients:
            c.close()
        world.close()


def _sync(client, repo):
    return client.post(f"/api/repos/{repo}/sync")


@pytest.mark.parametrize("who", ["writer", "kb_admin"])
def test_a_non_operator_cannot_sync_a_repo_holding_a_review_kb(env, who):
    r = _sync(env[who], REVIEW_REPO)
    assert r.status_code == 403, r.text


@pytest.mark.control(reason="the operator syncs any repository, as before")
def test_the_operator_may_sync_it(env):
    r = _sync(env["admin"], REVIEW_REPO)
    assert r.status_code != 403, r.text


@pytest.mark.control(reason="a repository of canonical KBs keeps the per-KB write rule")
def test_a_writer_may_sync_a_repo_of_canonical_kbs(env):
    r = _sync(env["writer"], CANONICAL_REPO)
    assert r.status_code != 403, r.text


INSIDE = "inside-review-kb"
NESTED_REPO = "owner/holds-a-config-kb"


@pytest.fixture
def nested(tmp_path: Path):
    """A subscribed checkout with a ``landing: review`` KB from config.yaml
    inside it, which the subscription does not link."""
    checkout = tmp_path / "workspace" / "owner" / "holds-a-config-kb"
    world = build_world(
        tmp_path,
        kbs=[
            KBConfig(name=CANONICAL, path=tmp_path / CANONICAL, kb_type="generic"),
            KBConfig(name=INSIDE, path=checkout / "notes", kb_type="generic", landing="review"),
        ],
        settings={"auth": AuthConfig(enabled=True)},
    )
    db = world.db
    repo = db.register_repo(NESTED_REPO, str(checkout))
    db.link_kb_to_repo(CANONICAL, repo["id"], "")
    auth = AuthService(db, world.config.settings.auth)
    user = seed_user(db, "writer", "password123", role="write")
    for kb in (CANONICAL, INSIDE):
        auth.grant_kb_permission(user["id"], kb, "write", user["id"])
    client = TestClient(world.app)
    client.cookies.set("pyrite_session", auth.login("writer", "password123")[1])
    try:
        yield client
    finally:
        client.close()
        world.close()


def test_a_review_kb_inside_the_checkout_makes_the_sync_the_operators(nested):
    r = _sync(nested, NESTED_REPO)
    assert r.status_code == 403, r.text
    assert INSIDE not in r.text, "the refusal names no KB"


# -- an operator API key needs the admin role (maintainer decision 2026-11-10) --


@pytest.fixture
def keyed(tmp_path: Path):
    """Auth off, two operator API keys: one with the write role, one admin."""
    import hashlib

    def h(key):
        return hashlib.sha256(key.encode()).hexdigest()

    world = build_world(
        tmp_path,
        kbs=[KBConfig(name=REVIEW, path=tmp_path / REVIEW, kb_type="generic", landing="review")],
        settings={
            "api_keys": [
                {"key_hash": h("write-key"), "role": "write", "label": "W"},
                {"key_hash": h("admin-key"), "role": "admin", "label": "A"},
            ]
        },
    )
    repo = world.db.register_repo(REVIEW_REPO, str(tmp_path / "workspace" / REVIEW_REPO))
    world.db.link_kb_to_repo(REVIEW, repo["id"], "")
    try:
        yield world
    finally:
        world.close()


def test_a_write_role_operator_key_cannot_sync_a_repo_holding_a_review_kb(keyed):
    r = keyed.client.post(f"/api/repos/{REVIEW_REPO}/sync", headers={"X-API-Key": "write-key"})
    assert r.status_code == 403, r.text
    assert "admin" in r.text.lower()


@pytest.mark.control(reason="an admin operator key syncs it, as before")
def test_an_admin_operator_key_may_sync_it(keyed):
    r = keyed.client.post(f"/api/repos/{REVIEW_REPO}/sync", headers={"X-API-Key": "admin-key"})
    assert r.status_code != 403, r.text

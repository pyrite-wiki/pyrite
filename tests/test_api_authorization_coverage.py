"""
Authorization coverage: every mutating /api route must reject a read-tier caller.

`create_app()` mounts all endpoint routers behind a read-tier floor, and
individual routes escalate with `requires_tier` / `requires_kb_tier`. Nothing
else verifies the escalation is complete -- a new POST added without a guard
inherits only the floor and is silently callable by any read-tier key. This
test makes that a CI failure instead of an audit finding
(backlog: api-authorization-coverage-test).

The check is behavioural rather than a walk of route dependencies:

- Routes are enumerated from `app.openapi()["paths"]`, FastAPI's stable public
  view (a `.routes` walk breaks on >= 0.139's `_IncludedRouter` wrappers).
- Each mutating route is called with a read-tier key and an empty JSON body.
  Tier guards are dependencies that raise 403 before body validation is
  reported, so a guarded route answers 403 whatever the body looks like; an
  unguarded one answers 422/404/200/500 -- anything but 403.

Deliberate exceptions live in READ_TIER_ALLOWED, so exposing a mutating-method
route to read-tier callers requires editing that list in the same diff.
"""

import re

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")

from tests.test_api_tiers import _build_client, _hash_key

MUTATING_METHODS = ("post", "put", "patch", "delete")

# Path prefixes whose mutating routes are held to the read-tier refusal. All of
# /api, plus the admin user-management routes under /auth (#330): the rest of
# /auth (login, register, logout, OAuth) is deliberately public.
GUARDED_PREFIXES = ("/api/", "/auth/users")

# (METHOD, path) pairs a read-tier caller may legitimately invoke despite the
# mutating HTTP method. Each needs a reason: the route must not change stored
# state, spend quota on the operator's behalf, or reach the network.
READ_TIER_ALLOWED: dict[tuple[str, str], str] = {
    ("POST", "/api/entries/batch"): "batch read; POST only to carry an id list in the body",
    ("POST", "/api/entries/resolve-batch"): "wikilink existence lookup; read-only",
    ("POST", "/api/collections/query-preview"): "evaluates a query without saving it",
    ("POST", "/api/kbs/{kb_name}/templates/{template_name}/render"): (
        "renders a template to text; nothing is stored"
    ),
}

# Routes whose authorization lives inside the handler because a router-level
# tier dependency cannot express it. These are verified, not exempted: each is
# called with a body that passes validation and must still refuse a read-tier
# key with one of the listed statuses.
INLINE_GUARDED: dict[tuple[str, str], tuple[dict, tuple[int, ...]]] = {
    # Global admin OR per-KB admin -- after the KB is resolved: the path's
    # placeholder names no KB, so a read-tier key is refused as KB_NOT_FOUND
    # (existence before role, ADR-0037 §4; the one resolver, #952).
    ("POST", "/api/kbs/{name}/permissions"): ({"user_id": 1, "role": "read"}, (404,)),
    # Needs a logged-in user (401 for a bare API key), then enforces the
    # operator-configurable ephemeral_min_tier (403).
    ("POST", "/api/kbs/ephemeral"): ({}, (401, 403)),
}


def _fill_path(path: str) -> str:
    """Substitute a placeholder for every {param} so the route resolves."""
    return re.sub(r"\{[^}]+\}", "x", path)


@pytest.fixture(scope="module")
def read_client(tmp_path_factory):
    """Module-scoped: this file walks the whole mutating API surface with
    empty bodies, which could incidentally touch the index worker (e.g. a
    route that starts a background sync). Join it and close the DB before
    tmp_path_factory removes the directory -- same shape as GitHub #55 and
    tests-leak-open-pyritedb-connections-into-temporarydirectory-teardown.
    """
    tmpdir = tmp_path_factory.mktemp("authz_coverage")
    keys = [{"key_hash": _hash_key("read-key"), "role": "read", "label": "Reader"}]
    client, _, db, index_worker = _build_client(tmpdir, api_keys=keys)
    client.headers.update({"X-API-Key": "read-key"})
    try:
        yield client
    finally:
        index_worker.wait_for_idle(timeout=10)
        db.close()


def _mutating_routes(client) -> list[tuple[str, str]]:
    paths = client.app.openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, ops in paths.items()
        if path.startswith(GUARDED_PREFIXES)
        for method in ops
        if method in MUTATING_METHODS
    )


def test_api_surface_is_enumerable(read_client):
    """Guard against the vacuous pass: an empty enumeration proves nothing."""
    routes = _mutating_routes(read_client)
    assert len(routes) > 20, f"expected the full API surface, enumerated {len(routes)}"


def test_auth_user_management_is_enumerated(read_client):
    """The widened prefix must actually reach /auth/users*, or it proves nothing."""
    assert ("PUT", "/auth/users/{user_id}/role") in _mutating_routes(read_client)


def _user_management_routes(client) -> list[tuple[str, str]]:
    """Every route under /auth/users, whatever its method: all are admin-only."""
    paths = client.app.openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, ops in paths.items()
        if path.startswith("/auth/users")
        for method in ops
        if method in ("get", *MUTATING_METHODS)
    )


def test_user_management_rejects_read_tier_on_every_method(read_client):
    """Reads are covered too: listing users or their grants is admin-only,
    so a new unguarded GET under /auth/users fails here (#330)."""
    routes = _user_management_routes(read_client)
    assert ("GET", "/auth/users") in routes and len(routes) >= 3, routes
    unguarded = []
    for method, path in routes:
        resp = read_client.request(method, _fill_path(path).replace("/x", "/1"), json={})
        if resp.status_code != 403:
            unguarded.append(f"{method} {path} -> {resp.status_code}")
    assert not unguarded, "User-management routes callable by a read-tier key:\n  " + "\n  ".join(
        unguarded
    )


def test_read_key_is_accepted_at_read_tier(read_client):
    """Guard against the other vacuous pass: a rejected key 401s everywhere."""
    assert read_client.get("/api/kbs").status_code == 200


def test_every_mutating_route_rejects_read_tier(read_client):
    unguarded = []
    for method, path in _mutating_routes(read_client):
        if (method, path) in READ_TIER_ALLOWED or (method, path) in INLINE_GUARDED:
            continue
        resp = read_client.request(method, _fill_path(path), json={})
        if resp.status_code != 403:
            unguarded.append(f"{method} {path} -> {resp.status_code}")
    assert not unguarded, (
        "Mutating routes callable by a read-tier key (add requires_tier/"
        "requires_kb_tier, or justify in READ_TIER_ALLOWED):\n  " + "\n  ".join(unguarded)
    )


def test_inline_guarded_routes_reject_read_tier(read_client):
    failures = []
    for (method, path), (body, refusals) in INLINE_GUARDED.items():
        resp = read_client.request(method, _fill_path(path), json=body)
        if resp.status_code not in refusals:
            failures.append(f"{method} {path} -> {resp.status_code}, expected one of {refusals}")
    assert not failures, "Inline guards did not refuse a read-tier key:\n  " + "\n  ".join(failures)


def test_allowlist_has_no_stale_entries(read_client):
    """An allowlisted route that no longer exists is dead weight hiding intent."""
    live = set(_mutating_routes(read_client))
    stale = [f"{m} {p}" for (m, p) in [*READ_TIER_ALLOWED, *INLINE_GUARDED] if (m, p) not in live]
    assert not stale, f"Exception lists name routes that no longer exist: {stale}"

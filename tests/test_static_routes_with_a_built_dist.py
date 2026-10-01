"""The static-route regime with a *built* ``web/dist`` present (#538).

CI never builds the frontend for the Python suite, so a checkout with no
``web/dist`` is what CI always exercises. Any checkout with a real frontend
build -- a contributor's, or a release checkout -- mounts two more routes
(``GET /favicon.ico``, ``GET /{path:path}``), and that changed route table
broke two guards that only ever saw the no-dist table:

- ``test_every_entry_point_passes_the_policy.py`` failed the two new routes
  as unclassified (never declared public, never in the "not yet migrated"
  debt list).
- ``test_mcp_sse_session.py::test_mcp_is_not_served_without_the_sdk_owner_check``
  failed because, with ``/mcp`` correctly unmounted, ``GET /mcp/info`` fell
  through to the SPA catch-all and answered 200 with the app's own HTML
  instead of 404.

This module builds a *stub* dist (a tmp dir with a minimal ``index.html``,
via ``PYRITE_STATIC_DIR`` -- the one lever ``create_app`` already exposes
for pointing static mounting elsewhere, reused rather than adding a second)
so CI covers the built-dist route table on every run, not just a
contributor's laptop.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pyrite.server.api import create_app

_MINIMAL_INDEX_HTML = (
    '<!doctype html><html><head><meta http-equiv="content-security-policy" '
    "content=\"script-src 'sha256-stubstubstubstubstubstubstubstubstubstubstu='\">"
    "</head><body>stub spa</body></html>"
)


@pytest.fixture
def stub_dist(tmp_path, monkeypatch):
    """Point ``PYRITE_STATIC_DIR`` at a tmp dir with a minimal ``index.html``.

    A real build also emits ``_app/`` (hashed JS/CSS) and ``favicon.ico``;
    neither is needed here; ``mount_static`` mounts `/favicon.ico` and the
    SPA fallback off ``index.html`` alone.
    """
    dist = tmp_path / "stub-dist"
    dist.mkdir()
    (dist / "index.html").write_text(_MINIMAL_INDEX_HTML)
    monkeypatch.setenv("PYRITE_STATIC_DIR", str(dist))
    return dist


@pytest.mark.control(
    reason="the fix here that verify-red can see is the two PUBLIC_ENTRY_POINTS entries "
    "(a test-side file, present either way) -- the old, conditionally-mounted "
    "mount_static already mounted both routes whenever dist_dir.is_dir() (true for this "
    "stub), so this passes against the pre-fix code too; the code fix it depends on "
    "(mounting unconditionally) only matters in the NO-dist regime, covered instead by "
    "test_every_entry_point_passes_the_policy.py::test_the_lists_hold_only_what_is_still_owed"
)
def test_the_completeness_guard_passes_with_a_built_dist(stub_dist):
    """The policy guard classifies the static routes explicitly, so a built
    dist does not surface them as unclassified REST operations."""
    from tests.test_every_entry_point_passes_the_policy import (
        PUBLIC_ENTRY_POINTS,
        REST_NOT_YET_MIGRATED,
        _operations,
        check_lists_owe,
        check_rest,
    )

    ops = _operations(create_app())
    assert "GET /favicon.ico" in ops
    assert "GET /{path:path}" in ops
    check_rest(ops, PUBLIC_ENTRY_POINTS, REST_NOT_YET_MIGRATED)
    check_lists_owe(ops, PUBLIC_ENTRY_POINTS, REST_NOT_YET_MIGRATED)


def test_mcp_info_is_404_not_the_spa_when_mcp_is_unmounted(stub_dist, monkeypatch):
    """With ``/mcp`` correctly unmounted (the SDK owner-check missing), a
    built dist's SPA catch-all must not answer for it: an API-shaped path
    with nothing behind it is a 404, not the app's HTML shell."""
    import mcp.server.sse as sdk_sse

    real_init = sdk_sse.SseServerTransport.__init__

    def init_without_owners(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        del self._session_owners

    monkeypatch.setattr(sdk_sse.SseServerTransport, "__init__", init_without_owners)

    with tempfile.TemporaryDirectory() as d:
        os.environ.setdefault("PYRITE_DATA_DIR", d)
        with TestClient(create_app()) as client:
            resp = client.get("/mcp/info")
    assert resp.status_code == 404
    assert resp.headers.get("content-type", "").startswith("application/json")


def test_unknown_api_shaped_paths_are_404_not_the_spa(stub_dist):
    """``/api``, ``/mcp``, ``/auth`` and ``/ws`` prefixes answer 404 (JSON,
    as the rest of the API does) rather than the SPA shell when nothing
    mounts under them."""
    with TestClient(create_app()) as client:
        for path in ("/api/nope", "/mcp/nope", "/auth/nope", "/ws/nope"):
            resp = client.get(path)
            assert resp.status_code == 404, path
            assert resp.headers.get("content-type", "").startswith("application/json"), path


@pytest.mark.control(
    reason="a 'still works' guard: an ordinary path already reached the SPA fallback "
    "before this fix (the bug was only API-shaped prefixes falling through to it), so "
    "this passes with or without the code change"
)
def test_a_client_route_still_gets_the_spa_shell(stub_dist):
    """Client-side routing must keep working: an ordinary app path (not
    shaped like an API prefix) still gets the SPA's index.html."""
    with TestClient(create_app()) as client:
        resp = client.get("/some/client/route")
    assert resp.status_code == 200
    assert "stub spa" in resp.text


@pytest.mark.control(
    reason="a static check of web/src/routes' current contents, unrelated to any "
    "server code change -- true regardless of the fix; it is a tripwire against a "
    "future SvelteKit route colliding with a reserved prefix, not a red/green test of "
    "this PR's behaviour"
)
def test_top_level_sveltekit_routes_do_not_collide_with_api_prefixes():
    """The guard's API-shaped prefixes (api, mcp, auth, ws) must not shadow
    a real client-side route. If SvelteKit ever grows a top-level route
    starting with one of these, it would 404 instead of loading the SPA."""
    routes_dir = Path(__file__).resolve().parents[1] / "web" / "src" / "routes"
    api_shaped = ("api", "mcp", "auth", "ws")
    top_level = {
        p.name
        for p in routes_dir.iterdir()
        if p.is_dir() and not p.name.startswith(("+", "_", "."))
    }
    colliding = [name for name in top_level if name.startswith(api_shaped)]
    assert not colliding, (
        f"top-level SvelteKit route(s) {colliding} start with an API-shaped prefix "
        "the static fallback 404s -- rename the route or the prefix list"
    )


# -- the reserved prefixes themselves, and what the old fallback check did ----------

_RESERVED = ("/api", "/mcp", "/auth", "/ws")
_ALL_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _reserved_paths() -> list[str]:
    out = []
    for prefix in _RESERVED:
        out += [prefix, prefix + "/", prefix + "/nope", prefix + "/nope/deeper"]
    return out


def _app_for(tmp_path, monkeypatch, *, dist: bool, auth: bool):
    """An app with or without a built dist, auth off or on (signed in as admin)."""
    from pyrite.config import AuthConfig, PyriteConfig, Settings
    from tests.auth_seed import seed_and_sign_in

    if dist:
        d = tmp_path / "stub-dist"
        d.mkdir()
        (d / "index.html").write_text(_MINIMAL_INDEX_HTML)
    else:
        d = tmp_path / "no-dist"
        d.mkdir()
    monkeypatch.setenv("PYRITE_STATIC_DIR", str(d))
    config = PyriteConfig(
        knowledge_bases=[],
        settings=Settings(
            index_path=tmp_path / "index.db",
            auth=AuthConfig(enabled=auth, allow_registration=True),
        ),
    )
    app = create_app(config=config)
    client = TestClient(app)
    if auth:
        seed_and_sign_in(client, "admin-user", "password123", role="admin")
    return client


@pytest.mark.parametrize("auth", [False, True], ids=["auth-off", "auth-on"])
@pytest.mark.parametrize("dist", [True, False], ids=["dist", "no-dist"])
def test_a_reserved_prefix_and_everything_under_it_is_404_never_the_spa(
    tmp_path, monkeypatch, dist, auth
):
    """The prefix itself (``/api``, no slash), with a slash, and any path under
    it that no real route claims: 404 for every method, with and without a
    dist, with auth off and on -- never the SPA shell."""
    client = _app_for(tmp_path, monkeypatch, dist=dist, auth=auth)
    with client:
        for path in _reserved_paths():
            for method in _ALL_METHODS:
                resp = client.request(method, path, follow_redirects=False)
                assert resp.status_code == 404, (method, path, resp.status_code)
                assert "stub spa" not in resp.text, (method, path)
        if auth:
            # And with no credential at all: the 404 is not an auth-dependent answer.
            anon = TestClient(client.app)  # no `with`: the lifespan is the client's
            for path in _reserved_paths():
                for method in _ALL_METHODS:
                    resp = anon.request(method, path, follow_redirects=False)
                    assert resp.status_code == 404, ("anon", method, path, resp.status_code)
                    assert "stub spa" not in resp.text, ("anon", method, path)


@pytest.mark.control(
    reason="a 'real route still wins' guard: the real routes answered before this "
    "fix, so it passes with or without it"
)
@pytest.mark.parametrize("auth", [False, True], ids=["auth-off", "auth-on"])
def test_real_routes_under_the_reserved_prefixes_still_win(tmp_path, monkeypatch, auth):
    client = _app_for(tmp_path, monkeypatch, dist=True, auth=auth)
    with client:
        assert client.get("/api/kbs").status_code == 200
        assert client.get("/health").status_code == 200
        assert "stub spa" not in client.get("/api/kbs").text
        assert "stub spa" in client.get("/some/client/route").text


# Paths that only START with a name the SPA fallback used to refuse. On `dev` the
# fallback answered 404 for any path beginning "docs", "redoc", "openapi.json",
# "health", "site" or "viewer" (a string prefix, not a path segment), so a
# sibling like /docs-old or /healthz never reached the SPA shell. Kept as-is.
_OLD_FALLBACK_REFUSALS = (
    "/docs-old",
    "/docs/nope",
    "/redoc-old",
    "/openapi.json.bak",
    "/healthz",
    "/health/nope",
    "/sitemap-old",
    "/siteX",
    "/site/unknown",
    "/viewerX",
    "/viewer/x",
)


@pytest.mark.control(
    reason="pins dev's behaviour, so it passes on the merge base; it fails only on this "
    "branch's earlier draft, which dropped the fallback's prefix check"
)
@pytest.mark.parametrize("auth", [False, True], ids=["auth-off", "auth-on"])
def test_paths_the_old_fallback_check_refused_are_still_not_the_spa(tmp_path, monkeypatch, auth):
    client = _app_for(tmp_path, monkeypatch, dist=True, auth=auth)
    with client:
        for path in _OLD_FALLBACK_REFUSALS:
            resp = client.get(path, follow_redirects=False)
            assert resp.status_code == 404, (path, resp.status_code)
            assert "stub spa" not in resp.text, path


@pytest.mark.control(
    reason="pins 'same as dev' for websocket upgrades: dev already closes these with "
    "1000, so it passes without the fix; it fails only against the first draft of the "
    "404 mount, which answered a websocket scope with an HTTP response"
)
@pytest.mark.parametrize("auth", [False, True], ids=["auth-off", "auth-on"])
@pytest.mark.parametrize("dist", [True, False], ids=["dist", "no-dist"])
def test_websocket_upgrades_answer_as_they_did_on_dev(tmp_path, monkeypatch, dist, auth):
    """A handshake the request guard admits (Host ``localhost``, own Origin; with
    auth on, an admin's session cookie), so it reaches routing rather than
    the guard's 1008: the real ``/ws`` accepts; an unknown path under ``/ws`` or
    any other prefix is closed before accept with code 1000, the same as on
    ``dev``. It is not answered with an HTTP 404 response: a real ASGI server
    does not accept ``http.response.start`` on a websocket scope."""
    from starlette.testclient import WebSocketDenialResponse
    from starlette.websockets import WebSocketDisconnect

    client = _app_for(tmp_path, monkeypatch, dist=dist, auth=auth)
    headers = {"host": "localhost", "origin": "http://localhost"}
    if auth:
        headers["cookie"] = f"pyrite_session={client.cookies['pyrite_session']}"
    with client:
        with client.websocket_connect("/ws", headers=headers) as ws:
            assert ws is not None  # accepted
        for path in ("/ws/nope", "/ws/", "/api/nope", "/mcp/nope", "/auth/nope", "/wsx"):
            with pytest.raises(WebSocketDisconnect) as closed:
                try:
                    with client.websocket_connect(path, headers=headers):
                        pass
                except WebSocketDenialResponse as denial:  # pragma: no cover - the bug
                    pytest.fail(f"{path}: answered as an HTTP response ({denial.status_code})")
            assert closed.value.code == 1000, path

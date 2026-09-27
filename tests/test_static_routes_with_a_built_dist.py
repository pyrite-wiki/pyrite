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


def test_a_client_route_still_gets_the_spa_shell(stub_dist):
    """Client-side routing must keep working: an ordinary app path (not
    shaped like an API prefix) still gets the SPA's index.html."""
    with TestClient(create_app()) as client:
        resp = client.get("/some/client/route")
    assert resp.status_code == 200
    assert "stub spa" in resp.text


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

"""The SPA's ``index.html`` responses (served by ``mount_static``) must carry
a Content-Security-Policy and X-Content-Type-Options, the same as ``/site``
(private #64, P-B1). Without a CSP, a stored-content XSS anywhere in the web
app (the transclusion/tooltip/citation sinks fixed alongside this test) can
still run script, because nothing stops an injected ``<script>`` tag or
inline event handler from executing on the app's own origin.
"""

import pytest


def _make_client(tmp_path, index_html: str):
    fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from pyrite.server.static import mount_static

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text(index_html)

    app = FastAPI()
    mount_static(app, dist_dir)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def _spa_client(tmp_path):
    with _make_client(tmp_path, "<html><body>spa</body></html>") as client:
        yield client


class TestSpaSecurityHeaders:
    def test_root_route_carries_csp_with_script_src_self(self, _spa_client):
        resp = _spa_client.get("/")
        assert resp.status_code == 200
        csp = resp.headers.get("content-security-policy", "")
        assert "script-src 'self'" in csp, csp

    def test_root_route_carries_frame_ancestors_self(self, _spa_client):
        resp = _spa_client.get("/")
        csp = resp.headers.get("content-security-policy", "")
        assert "frame-ancestors 'self'" in csp, csp

    def test_root_route_carries_nosniff(self, _spa_client):
        resp = _spa_client.get("/")
        assert resp.headers.get("x-content-type-options") == "nosniff"

    def test_spa_fallback_route_also_carries_csp(self, _spa_client):
        # Any client-side route (e.g. /entries/foo) falls back to index.html.
        resp = _spa_client.get("/entries/foo")
        assert resp.status_code == 200
        csp = resp.headers.get("content-security-policy", "")
        assert "script-src 'self'" in csp, csp
        assert resp.headers.get("x-content-type-options") == "nosniff"


class TestSpaCspHonoursBuildHash:
    """SvelteKit's ``adapter-static`` (``kit.csp.mode: 'hash'``) hashes its own
    inline bootstrap script into a ``<meta http-equiv>`` CSP tag on every
    prerendered page, because a nonce is unsafe (and rejected by browsers)
    for prerendered output, and the hash changes on every build (the
    bootstrap embeds a per-build-randomised ``__sveltekit_<id>`` variable
    name). A header CSP and a meta CSP combine with AND semantics: a header
    ``script-src 'self'`` with no hash blocks that inline script even though
    the meta tag allows it, so the app would boot to a blank page. The header
    must carry the same hash(es) the build emitted.
    """

    def test_header_csp_includes_the_build_s_own_script_hash(self, tmp_path):
        built_index = (
            "<html><head>"
            '<meta http-equiv="content-security-policy" '
            "content=\"script-src 'self' 'sha256-abc123DEFghiJKLmnoPQRstuvWXyz012345678900='\">"
            "</head><body>"
            "<script>var __sveltekit_xyz = {};</script>"
            "</body></html>"
        )
        with _make_client(tmp_path, built_index) as client:
            resp = client.get("/")
        csp = resp.headers.get("content-security-policy", "")
        assert "'sha256-abc123DEFghiJKLmnoPQRstuvWXyz012345678900='" in csp, csp
        # The built-in baseline directives are still present alongside it.
        assert "frame-ancestors 'self'" in csp, csp

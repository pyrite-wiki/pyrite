"""The SPA's ``index.html`` responses (served by ``mount_static``) must carry
a Content-Security-Policy and X-Content-Type-Options, the same as ``/site``
(private #64, P-B1). Without a CSP, a stored-content XSS anywhere in the web
app (the transclusion/tooltip/citation sinks fixed alongside this test) can
still run script, because nothing stops an injected ``<script>`` tag or
inline event handler from executing on the app's own origin.
"""

import pytest


def _make_client(
    tmp_path,
    index_html: str,
    extra_files: dict[str, str] | None = None,
    site_csp_extra: str = "",
):
    fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from pyrite.server.static import mount_static

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text(index_html)
    for rel_path, content in (extra_files or {}).items():
        p = dist_dir / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    app = FastAPI()
    if site_csp_extra:
        from pyrite.config import PyriteConfig, Settings

        app.state.pyrite_config = PyriteConfig(
            knowledge_bases=[],
            settings=Settings(index_path=tmp_path / "index.db", site_csp_extra=site_csp_extra),
        )
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

    def test_explicit_index_html_request_also_carries_csp(self, _spa_client):
        # A direct GET /index.html (a crawler, or an attacker probing for the
        # unguarded path) hits the `file_path.is_file()` branch in
        # spa_fallback, not the "no route matched" branch -- it must carry
        # the same headers, or it can be framed and the CSP is theatre.
        resp = _spa_client.get("/index.html")
        assert resp.status_code == 200
        csp = resp.headers.get("content-security-policy", "")
        assert "frame-ancestors 'self'" in csp, csp
        assert resp.headers.get("x-content-type-options") == "nosniff"

    def test_img_src_allows_https_for_entry_body_and_logo_images(self, _spa_client):
        # An entry body can embed `![](https://example.com/x.png)`, and an
        # operator's branding `logo_url` is commonly an external https URL.
        # An image cannot execute script, so allowing any https source (not
        # just the app's own origin and data: URIs) is not a script-src
        # weakening -- but the prior `img-src 'self' data:` blocked both.
        resp = _spa_client.get("/")
        csp = resp.headers.get("content-security-policy", "")
        assert "img-src 'self' data: https:" in csp, csp

    def test_site_csp_extra_is_merged_into_the_spa_policy(self, tmp_path):
        with _make_client(
            tmp_path,
            "<html><body>spa</body></html>",
            site_csp_extra="connect-src https://api.example.com",
        ) as client:
            resp = client.get("/")
        csp = resp.headers.get("content-security-policy", "")
        assert "https://api.example.com" in csp, csp

    def test_every_file_served_from_dist_carries_nosniff(self, tmp_path):
        # A JS/CSS asset served through the same `file_path.is_file()`
        # branch (not the /_app StaticFiles mount) must not be sniffable
        # into a different content type either.
        with _make_client(
            tmp_path,
            "<html><body>spa</body></html>",
            extra_files={"robots.txt": "User-agent: *\n"},
        ) as client:
            resp = client.get("/robots.txt")
        assert resp.status_code == 200
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

    def test_missing_build_hash_logs_a_warning_at_mount_time(self, tmp_path, caplog):
        # If a future SvelteKit build ever stops emitting the CSP meta tag
        # (a config regression, an adapter change), the header silently
        # falls back to the bare baseline with no script hash -- the app
        # boots to a blank page with no signal why. Warn loudly instead.
        import logging

        from fastapi import FastAPI

        from pyrite.server.static import mount_static

        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "index.html").write_text("<html><body>no hash here</body></html>")

        with caplog.at_level(logging.WARNING, logger="pyrite.server.static"):
            mount_static(FastAPI(), dist_dir)

        assert any(
            "hash" in rec.message.lower() and "index.html" in rec.message.lower()
            for rec in caplog.records
        ), [rec.message for rec in caplog.records]

    def test_present_build_hash_logs_no_warning_at_mount_time(self, tmp_path, caplog):
        import logging

        from fastapi import FastAPI

        from pyrite.server.static import mount_static

        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "index.html").write_text(
            "<html><head>"
            '<meta http-equiv="content-security-policy" '
            "content=\"script-src 'self' 'sha256-abc123DEFghiJKLmnoPQRstuvWXyz012345678900='\">"
            "</head><body>ok</body></html>"
        )

        with caplog.at_level(logging.WARNING, logger="pyrite.server.static"):
            mount_static(FastAPI(), dist_dir)

        assert not any("hash" in rec.message.lower() for rec in caplog.records), [
            rec.message for rec in caplog.records
        ]

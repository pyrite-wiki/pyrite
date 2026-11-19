"""Tests for the public /config/branding and /branding/{file} endpoints.

These endpoints live OUTSIDE /api (no auth required) so the login page
can fetch branding before the user is authenticated.
"""

import textwrap
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app


def _make_client(branding_dir: Path | None = None, tmpdir: Path | None = None) -> TestClient:
    """Create a TestClient with a configurable branding dir."""
    assert tmpdir is not None
    db_path = tmpdir / "index.db"
    kb_path = tmpdir / "kb"
    kb_path.mkdir(exist_ok=True)

    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="test-kb", path=kb_path, kb_type="generic")],
        settings=Settings(index_path=db_path, branding_dir=branding_dir),
    )
    app = create_app(config=config)
    return TestClient(app)


@pytest.fixture
def populated_branding_dir(tmp_path: Path) -> Path:
    d = tmp_path / "branding"
    d.mkdir()
    (d / "branding.yaml").write_text(
        textwrap.dedent(
            """
            name: "Transparency Cascade Press"
            primary_color: "#c93b3b"
            logo: "logo.png"
            wordmark: "wordmark.png"
            invert_on_dark: true
            footer_credit_url: "https://pyrite.wiki"
            meta:
              description: "Investigative journalism."
            """
        ).lstrip()
    )
    (d / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-logo")
    (d / "wordmark.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-wordmark")
    return d


# ---------------------------------------------------------------------------
# GET /config/branding
# ---------------------------------------------------------------------------


class TestBrandingConfigEndpoint:
    def test_returns_200_without_auth(self, populated_branding_dir, tmp_path):
        client = _make_client(branding_dir=populated_branding_dir, tmpdir=tmp_path)
        r = client.get("/config/branding")
        assert r.status_code == 200

    def test_returns_branded_values(self, populated_branding_dir, tmp_path):
        client = _make_client(branding_dir=populated_branding_dir, tmpdir=tmp_path)
        data = client.get("/config/branding").json()
        assert data["name"] == "Transparency Cascade Press"
        assert data["primary_color"] == "#c93b3b"
        assert data["logo_url"] == "/branding/logo.png"
        assert data["wordmark_url"] == "/branding/wordmark.png"
        assert data["invert_on_dark"] is True

    def test_returns_defaults_when_no_branding_dir(self, tmp_path):
        client = _make_client(branding_dir=None, tmpdir=tmp_path)
        data = client.get("/config/branding").json()
        assert data["name"] == "Pyrite"
        assert data["logo_url"] is None

    def test_response_is_cacheable(self, populated_branding_dir, tmp_path):
        client = _make_client(branding_dir=populated_branding_dir, tmpdir=tmp_path)
        r = client.get("/config/branding")
        # Any Cache-Control header is fine; just verify caching hasn't been
        # explicitly disabled.
        assert "no-store" not in r.headers.get("cache-control", "").lower()


# ---------------------------------------------------------------------------
# GET /branding/{filename}
# ---------------------------------------------------------------------------


class TestBrandingAssetEndpoint:
    def test_serves_logo_without_auth(self, populated_branding_dir, tmp_path):
        client = _make_client(branding_dir=populated_branding_dir, tmpdir=tmp_path)
        r = client.get("/branding/logo.png")
        assert r.status_code == 200
        assert r.content == b"\x89PNG\r\n\x1a\nfake-logo"
        # PNG content-type
        assert "image/png" in r.headers["content-type"]

    def test_serves_wordmark(self, populated_branding_dir, tmp_path):
        client = _make_client(branding_dir=populated_branding_dir, tmpdir=tmp_path)
        r = client.get("/branding/wordmark.png")
        assert r.status_code == 200
        assert r.content.endswith(b"fake-wordmark")

    def test_404_for_missing_file(self, populated_branding_dir, tmp_path):
        client = _make_client(branding_dir=populated_branding_dir, tmpdir=tmp_path)
        r = client.get("/branding/does-not-exist.png")
        assert r.status_code == 404

    def test_404_when_no_branding_dir_configured(self, tmp_path):
        client = _make_client(branding_dir=None, tmpdir=tmp_path)
        r = client.get("/branding/anything.png")
        assert r.status_code == 404

    def test_traversal_cannot_leak_adjacent_files(self, populated_branding_dir, tmp_path):
        """An adjacent file next to the branding dir must not be reachable.

        HTTP clients typically normalize ``..`` before the request reaches
        the server (so the endpoint handler never sees it). That's the
        first line of defense; the resolver itself is tested in
        test_branding_service.py::test_resolve_asset_rejects_traversal.
        This test verifies the *outcome*: no adjacent file leaks out
        regardless of what normalization occurs.
        """
        # Write a sentinel file next to the branding dir.
        sentinel = populated_branding_dir.parent / "secret.txt"
        sentinel.write_text("SHOULD-NOT-LEAK")

        client = _make_client(branding_dir=populated_branding_dir, tmpdir=tmp_path)
        for url in (
            "/branding/%2E%2E/secret.txt",
            "/branding/../secret.txt",
            "/branding//secret.txt",
        ):
            r = client.get(url)
            assert "SHOULD-NOT-LEAK" not in r.text, f"leaked via {url}"


# ---------------------------------------------------------------------------
# A broken branding.yaml on the anonymous route (#408, #445). These lived in
# the /site tests until #953 removed the renderer; /config/branding is the
# anonymous route that still builds a BrandingService per request.
# ---------------------------------------------------------------------------


def _broken_branding_app(tmp_path: Path, yaml_text: str):
    kb_path = tmp_path / "public-kb"
    kb_path.mkdir()
    branding = tmp_path / "branding"
    branding.mkdir()
    (branding / "branding.yaml").write_text(yaml_text)
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name="public-kb", path=kb_path, kb_type="generic", default_role="read"),
        ],
        settings=Settings(index_path=tmp_path / "index.db", branding_dir=branding),
    )
    return create_app(config=config), branding


class TestBrokenBrandingDoesNotLeakOnPublicRoutes:
    """#445 cold read: a broken ``branding.yaml`` must not turn the
    anonymous, always-public GET route into a path-and-parser-text leak.
    Before #408's fix the route returned a bare 500 ("Internal Server
    Error"); adding ``BrandingInvalidError`` without a row in
    ``_PYRITE_ERROR_STATUS`` made the central handler fall through to
    ``str(exc)``, which is the absolute branding.yaml path plus the
    parser's own text.
    """

    def test_config_branding_route_does_not_leak_path_or_parser_text(self, tmp_path):
        app, branding = _broken_branding_app(tmp_path, "- a list, not a mapping\n")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/config/branding")

        assert resp.status_code >= 400, resp.text
        # Naming the filename is fine (the public_message says "fix
        # branding.yaml" so an operator knows what to look at); the
        # filesystem path and the parser's own type/error text are not.
        assert str(branding) not in resp.text, (
            f"/config/branding leaked the branding directory path: {resp.text}"
        )
        assert "CommentedSeq" not in resp.text, (
            f"/config/branding leaked the raw parser/type name: {resp.text}"
        )

    def test_config_branding_route_answers_a_named_code_not_a_bare_500(self, tmp_path):
        """The route still fails closed (it cannot serve real branding), but
        with the same ``{"detail": {"code", "message", ...}}`` shape every
        other PyriteError gets from the central handler (ADR-0037 theme 2)
        -- not FastAPI's generic 'Internal Server Error' text and not a
        traceback."""
        app, _branding = _broken_branding_app(tmp_path, "- a list, not a mapping\n")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/config/branding")

        body = resp.json()["detail"]
        assert body.get("code") == "BRANDING_INVALID", body

    @pytest.mark.parametrize("bad_key", ["meta", "mcp"])
    def test_a_nested_non_mapping_answers_the_named_code(self, tmp_path, bad_key):
        app, _branding = _broken_branding_app(tmp_path, f"name: Acme\n{bad_key}: [x]\n")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/config/branding")

        assert resp.status_code == 500, resp.text
        assert resp.json()["detail"]["code"] == "BRANDING_INVALID", resp.text

    def test_name_list_no_longer_a_list_from_config_branding(self, tmp_path):
        """The exact failure mode the cold read named: name: [x] must not
        come out of /config/branding as a list -- the route stays a named
        500 (GET /config/branding fails closed; the web store already has a
        fallback for it), so this pins that it does NOT silently serialize
        the list through."""
        app, _branding = _broken_branding_app(tmp_path, "name: [x]\n")
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/config/branding")
        assert r.status_code == 500, r.text
        body = r.json()["detail"]
        assert body.get("code") == "BRANDING_INVALID", body
        assert not isinstance(body.get("name"), list), body

    def test_config_branding_central_handler_does_not_log_a_5xx_twice(self, tmp_path, caplog):
        """#445 delta cold read: the central handler (now
        ``pyrite.server.errors``, moved from ``pyrite.server.api`` by
        ADR-0037 theme 2) logged a 5xx PyriteError with ``logger.error``
        (with a traceback) AND, whenever it had a ``public_message``, an
        unconditional second ``logger.warning`` line for the very same
        exception -- doubling the handler's own log output for a broken
        branding.yaml on every request. Scoped to the
        ``pyrite.server.errors`` logger specifically: ``BrandingService``
        logging its own line (once per mtime, a separate concern) is not
        part of this count."""
        import logging

        app, _branding = _broken_branding_app(tmp_path, "- a list, not a mapping\n")
        with (
            caplog.at_level(logging.WARNING, logger="pyrite.server.errors"),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            resp = client.get("/config/branding")

        assert resp.status_code >= 500, resp.text
        handler_records = [r for r in caplog.records if r.name == "pyrite.server.errors"]
        assert len(handler_records) <= 1, (
            f"expected the central handler to log this 5xx at most once, got "
            f"{len(handler_records)}: {[r.getMessage() for r in handler_records]}"
        )

    def test_broken_branding_logged_once_not_per_request(self, tmp_path, caplog):
        """A broken file must not flood the log with BrandingService's own
        traceback on every page load: the failure is cached (keyed on the
        file's mtime and size) so repeat requests don't re-log it. The
        central handler's per-request 5xx line is a separate concern (the
        test above), so only BrandingService's logger is counted."""
        import logging

        app, _branding = _broken_branding_app(tmp_path, "- a list, not a mapping\n")
        with (
            caplog.at_level(logging.WARNING, logger="pyrite.services.branding_service"),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            for _ in range(5):
                r = client.get("/config/branding")
                assert r.status_code == 500, r.text

        branding_records = [
            rec for rec in caplog.records if rec.name == "pyrite.services.branding_service"
        ]
        # Exactly one, not "at most one": the failure must be logged (a
        # silently-dropped exception is its own bug), just not once per
        # request across all 5 calls to the same broken, unchanged file.
        assert len(branding_records) == 1, (
            f"expected the broken-branding failure logged exactly once across 5 requests, "
            f"got {len(branding_records)}: {[r.getMessage() for r in branding_records]}"
        )


class TestBrandingFailureCacheDoesNotLeakTracebackFramesOverHttp:
    def test_config_branding_logged_traceback_does_not_grow_across_requests(self, tmp_path, caplog):
        """#445 round 3 at the HTTP layer, checked where the bug actually
        shows up: ``/config/branding`` *logs* the whole (growing) traceback
        on every request via the central handler's ``exc_info=exc`` -- the
        response body itself carries only the fixed ``public_message``, so
        it can't show this. The route builds a fresh ``BrandingService``
        per request (no per-instance state to reset), so this exercises the
        module-level cache directly, repeatedly, the way real traffic
        would."""
        import logging
        import traceback

        app, _branding = _broken_branding_app(tmp_path, "- a list, not a mapping\n")
        depths = []
        with (
            caplog.at_level(logging.ERROR, logger="pyrite.server.errors"),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            for _ in range(8):
                caplog.clear()
                r = client.get("/config/branding")
                assert r.status_code == 500, r.text
                handler_records = [
                    rec for rec in caplog.records if rec.name == "pyrite.server.errors"
                ]
                assert handler_records, "expected the central handler to log this 5xx"
                exc_info = handler_records[0].exc_info
                assert exc_info is not None and exc_info[2] is not None, (
                    "expected a traceback on the logged record"
                )
                depths.append(len(traceback.extract_tb(exc_info[2])))

        # Request 0 builds a fresh BrandingService and hits a real parse
        # failure (deeper stack, through _load_raw); every request after
        # that is a cache hit and must raise a fresh exception each time,
        # so its logged traceback is a fixed, shallow depth -- not growing
        # request over request the way re-raising the same stored object
        # would (the bug: 1, 2, 3, ... frames deeper on each hit).
        cache_hit_depths = depths[1:]
        assert len(set(cache_hit_depths)) == 1, (
            f"the logged traceback's frame count grew across identical repeated "
            f"cache-hit requests to the same unchanged file (request 0, a real "
            f"parse, is expected to differ): {depths}"
        )

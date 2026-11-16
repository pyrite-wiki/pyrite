"""Non-API responses carry one set of headers, decided by one classification.

Properties of serving non-API responses, as they stand after #953 removed
the /site and /viewer rendering:

* Root: the SPA bundle's directory is resolved once, from configuration; the
  process's working directory never decides it, and a file is served in the
  form the containment check resolved.
* Headers: every non-API response -- 404s, redirects, 304s, refusals --
  carries `static.security_headers`, applied by one middleware; a route's
  only way to differ is `add_csp_extra`.
* One classification (`API_PREFIXES`, `API_PATHS`, `NON_SPA_PREFIXES`)
  decides both the routing and the headers of a path.
* Branding URLs: only http and https URLs are kept (a `mailto:` contact for
  `support_url`).
* Sockets that present no credential are capped; credentialed ones are not.
"""

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import PlainTextResponse, RedirectResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.responses import Response  # noqa: E402

from pyrite.config import KBConfig, PyriteConfig, Settings  # noqa: E402
from pyrite.exceptions import BrandingInvalidError  # noqa: E402
from pyrite.server import static as static_mod  # noqa: E402
from pyrite.server.api import create_app  # noqa: E402
from pyrite.services.branding_service import BrandingService  # noqa: E402

BUILD_HASH = "'sha256-AbCdEf0123456789+/AbCdEf0123456789+/AbCdEf01234='"
INDEX_HTML = (
    '<html><head><meta http-equiv="content-security-policy" '
    f'content="script-src {BUILD_HASH}"></head><body>spa</body></html>'
)


def csp_directives(response) -> dict[str, list[str]]:
    csp = response.headers.get("Content-Security-Policy")
    assert csp, (
        f"no Content-Security-Policy on {response.request.url.path} ({response.status_code})"
    )
    out = {}
    for part in csp.split(";"):
        tokens = part.split()
        if tokens:
            out[tokens[0]] = tokens[1:]
    return out


def assert_framing(response, *, script_src=("'self'",), extra=None):
    """The baseline: nosniff, and the directives that stop script, plugins,
    a rewritten base and framing by another origin."""
    where = f"{response.request.url.path} ({response.status_code})"
    assert response.headers.get("X-Content-Type-Options") == "nosniff", where
    d = csp_directives(response)
    assert d["default-src"] == ["'self'"], where
    assert d["script-src"] == list(script_src), where
    assert d["object-src"] == ["'none'"], where
    assert d["base-uri"] == ["'self'"], where
    assert d["frame-ancestors"] == ["'self'"], where
    for name, sources in (extra or {}).items():
        assert d[name][-len(sources) :] == sources, where
    return d


def _config(tmp_path, branding_dir=None, **settings):
    kb = tmp_path / "kb"
    kb.mkdir(exist_ok=True)
    return PyriteConfig(
        knowledge_bases=[KBConfig(name="pub", path=kb, kb_type="generic", default_role="read")],
        settings=Settings(
            index_path=tmp_path / "data" / "index.db", branding_dir=branding_dir, **settings
        ),
    )


def _app(tmp_path, branding_dir=None, **settings):
    config = _config(tmp_path, branding_dir, **settings)
    return config, create_app(config=config)


def _config(tmp_path, branding_dir=None, **settings):
    kb = tmp_path / "kb"
    kb.mkdir(exist_ok=True)
    return PyriteConfig(
        knowledge_bases=[KBConfig(name="pub", path=kb, kb_type="generic", default_role="read")],
        settings=Settings(
            index_path=tmp_path / "data" / "index.db", branding_dir=branding_dir, **settings
        ),
    )


def _app(tmp_path, branding_dir=None, **settings):
    config = _config(tmp_path, branding_dir, **settings)
    return config, create_app(config=config)


@pytest.fixture
def no_data_dir(monkeypatch):
    monkeypatch.delenv("PYRITE_DATA_DIR", raising=False)
    monkeypatch.delenv("PYRITE_STATIC_DIR", raising=False)


class TestOneRoot:
    def test_a_relative_static_dir_is_resolved_once_not_followed_through_the_cwd(
        self, tmp_path, monkeypatch, no_data_dir
    ):
        first, second = tmp_path / "first", tmp_path / "second"
        for d in (first, second):
            (d / "dist").mkdir(parents=True)
        (first / "dist" / "index.html").write_text(INDEX_HTML)
        (second / "dist").joinpath("index.html").write_text("<html>WRONG DIST</html>")
        (second / "dist").joinpath("app.css").write_text("WRONG DIST")
        monkeypatch.chdir(first)
        monkeypatch.setenv("PYRITE_STATIC_DIR", "dist")
        assert static_mod.resolve_dist_dir() == (first / "dist").resolve()
        assert static_mod.resolve_dist_dir().is_absolute()

        (tmp_path / "state").mkdir()
        _, app = _app(tmp_path / "state")
        monkeypatch.chdir(second)
        client = TestClient(app)
        r = client.get("/some/client/route")
        assert r.status_code == 200 and "spa" in r.text and "WRONG DIST" not in r.text
        # A file looked up in the bundle is looked up in the bundle resolved
        # at startup, never in whatever "dist" the working directory has now.
        assert "WRONG DIST" not in client.get("/app.css").text

    @pytest.mark.control(
        reason="dev already refused a link out of the bundle; pinned while the serving path changes"
    )
    def test_a_link_out_of_the_bundle_is_not_followed(self, tmp_path, monkeypatch, no_data_dir):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text(INDEX_HTML)
        outside = tmp_path / "outside.txt"
        outside.write_text("outside the root")
        (dist / "escape.txt").symlink_to(outside)
        monkeypatch.setenv("PYRITE_STATIC_DIR", str(dist))
        _, app = _app(tmp_path)
        r = TestClient(app).get("/escape.txt")
        assert "outside the root" not in r.text


class _World:
    """An app with every kind of non-API content the server can answer."""

    def __init__(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PYRITE_DATA_DIR", raising=False)
        dist = tmp_path / "dist"
        (dist / "_app").mkdir(parents=True)
        (dist / "index.html").write_text(INDEX_HTML)
        (dist / "_app" / "x.js").write_text("console.log(1)")
        (dist / "favicon.ico").write_bytes(b"\x00\x00")
        (dist / "app.css").write_text("body{}")
        monkeypatch.setenv("PYRITE_STATIC_DIR", str(dist))
        branding = tmp_path / "branding"
        branding.mkdir()
        (branding / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
        (branding / "thing.bin").write_bytes(b"\x00\x01")
        self.config, self.app = _app(tmp_path, branding_dir=branding)
        self.client = TestClient(self.app)


@pytest.fixture
def world(tmp_path, monkeypatch):
    return _World(tmp_path, monkeypatch)


# Every non-API entry point: (path, expected status). Each is requested
# through its own route; a route that sets nothing itself still gets the set.
SPA = ("'self'", BUILD_HASH)
ENTRY_POINTS = [
    # /branding
    ("/branding/logo.svg", 200),
    ("/branding/thing.bin", 200),
    ("/branding/missing.png", 404),
    # the SPA bundle
    ("/_app/x.js", 200),
    ("/_app/missing.js", 404),
    ("/favicon.ico", 200),
    ("/app.css", 200),
    ("/index.html", 200),
    ("/a/client/route", 200),
    # paths removed in #953: a 404, never the SPA shell
    ("/robots.txt", 404),
    ("/sitemap.xml", 404),
    ("/site/pub/e1", 404),
    ("/viewer/", 404),
    # paths the SPA reserves for the API: answered 404, not its shell
    ("/api/not-a-route", 404),
    ("/docs/not-a-page", 404),
    ("/healthz", 404),
]


_ALREADY_GUARDED = {
    "/index.html": "the SPA's page carried its policy before the middleware",
    "/a/client/route": "the SPA's page carried its policy before the middleware",
    "/api/not-a-route": "API-shaped paths are the API's; they must stay outside the set",
}
_MATRIX = [
    pytest.param(
        path,
        status,
        marks=[pytest.mark.control(reason=_ALREADY_GUARDED[path])]
        if path in _ALREADY_GUARDED
        else [],
        id=f"{path}-{status}",
    )
    for path, status in ENTRY_POINTS
]


class TestOneSetOfHeaders:
    @pytest.mark.parametrize(("path", "status"), _MATRIX)
    def test_every_entry_point_carries_the_set(self, world, path, status):
        r = world.client.get(path)
        if path.startswith("/api/"):
            # API-shaped: the API's own 404 body, not this module's.
            assert r.status_code == status
            return
        assert r.status_code == status, path
        spa = path in ("/index.html", "/a/client/route")
        d = assert_framing(r, script_src=SPA if spa else ("'self'",))
        if path.startswith("/branding/") and status == 200:
            assert "sandbox" in d

    def test_head_requests_carry_the_set(self, world):
        assert_framing(world.client.head("/branding/logo.svg"))
        assert_framing(world.client.head("/app.css"))

    def test_the_operator_extra_reaches_every_part(self, world):
        world.config.settings.site_csp_extra = "img-src https://img.example"
        for path in (
            "/branding/logo.svg",
            "/branding/missing.png",
            "/_app/x.js",
            "/_app/missing.js",
            "/favicon.ico",
            "/robots.txt",
        ):
            r = world.client.get(path)
            assert "https://img.example" in csp_directives(r)["img-src"], path
        spa = world.client.get("/a/client/route")
        assert "https://img.example" in csp_directives(spa)["img-src"]
        assert BUILD_HASH in csp_directives(spa)["script-src"]

    def test_branding_differs_from_the_baseline_only_by_sandbox(self, world):
        branding = csp_directives(world.client.get("/branding/logo.svg"))
        baseline = csp_directives(world.client.get("/app.css"))
        assert "sandbox" in branding
        assert {k: v for k, v in branding.items() if k != "sandbox"} == baseline

    def test_a_refused_host_is_answered_with_the_set(self, world):
        r = world.client.get("/a/client/route", headers={"host": "unexpected.example"})
        assert r.status_code == 421
        assert_framing(r)

    def test_unclaimed_paths_without_a_bundle_carry_the_set(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PYRITE_DATA_DIR", raising=False)
        monkeypatch.setenv("PYRITE_STATIC_DIR", str(tmp_path / "no-dist"))
        _, app = _app(tmp_path)
        r = TestClient(app).get("/nothing-here")
        assert r.status_code == 404
        assert_framing(r)

    def test_a_route_added_later_cannot_forget_the_set(self):
        app = FastAPI()
        static_mod.install_security_headers(app)
        static_mod.install_security_headers(app)  # once

        @app.get("/new-route")
        def new_route():
            return PlainTextResponse("x")

        @app.get("/moved")
        def moved():
            return RedirectResponse("/new-route", status_code=302)

        @app.get("/unchanged")
        def unchanged():
            return Response(status_code=304)

        @app.get("/api/thing")
        def api_thing():
            return {"a": 1}

        c = TestClient(app, follow_redirects=False)
        for path in ("/new-route", "/moved", "/unchanged", "/not-a-route"):
            assert_framing(c.get(path))
        assert "Content-Security-Policy" not in c.get("/api/thing").headers

    def test_a_route_difference_is_merged_not_replaced(self):
        app = FastAPI()
        static_mod.install_security_headers(app)

        @app.get("/special")
        def special(request: fastapi.Request):
            static_mod.add_csp_extra(request, "img-src https://one.example")
            static_mod.add_csp_extra(request, "sandbox")
            return PlainTextResponse("x")

        d = assert_framing(TestClient(app).get("/special"))
        assert d["img-src"][-1] == "https://one.example"
        assert "sandbox" in d

    def test_the_function_is_the_only_source_of_the_set(self):
        h = static_mod.security_headers(None)
        assert set(h) == {"Content-Security-Policy", "X-Content-Type-Options"}

    def test_the_api_and_its_docs_are_not_given_the_page_policy(self):
        for path in ("/api/x", "/api", "/mcp/sse", "/auth/login", "/ws", "/config/branding"):
            assert static_mod.is_api_path(path), path
        for path in ("/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            assert static_mod.is_api_path(path), path
        for path in ("/docs/other", "/healthz", "/apix", "/site", "/viewer", "/branding/x"):
            assert not static_mod.is_api_path(path), path
        for prefix in static_mod.API_PREFIXES:
            assert static_mod.is_api_path(prefix + "/anything")


def _route_paths(app):
    """Every top-level route and mount of the app, as request paths: a mount
    and something under it; a route with its parameters filled in."""
    paths = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path or type(route).__name__ == "APIWebSocketRoute":
            continue
        concrete = path.replace("{path:path}", "x").replace("{name}", "x")
        if "{" in concrete:
            continue
        paths.append(concrete)
        if type(route).__name__ == "Mount":
            paths.append(concrete.rstrip("/") + "/x")
    return paths


def _classification_paths(app):
    """Built from the lists the code routes and applies headers by."""
    out = []
    for prefix in static_mod.API_PREFIXES:
        for tail in ("", "/", "/x", "/%2e%2e/x", "/..%2fx", "/.%2e/x", "/x/../y"):
            out.append(prefix + tail)
    out.extend(sorted(static_mod.API_PATHS))
    for prefix in static_mod.NON_SPA_PREFIXES:
        for tail in ("", "/", "/x", "x", "/%2e%2e/x"):
            out.append(prefix + tail)
    out.extend(_route_paths(app))
    return list(dict.fromkeys(out))


SHELL = "<body>spa</body>"


def _api_or_framed(r, path):
    """The path is the API's (and then it is never the SPA shell), or the
    response carries the whole header set."""
    if static_mod.is_api_path(path):
        assert SHELL not in r.text, f"{path} answered with the SPA shell"
    else:
        assert_framing(r, script_src=SPA if SHELL in r.text else ("'self'",))


class TestEveryPathIsApiOrFramed:
    """One classification (`static.is_api_path` / `spa_may_answer`) decides both
    the routing and the headers, so no path can be 'API' for one and 'SPA'
    for the other. Parametrised from the lists the code uses: a prefix added
    to them is covered here without editing this file."""

    def test_the_paths_come_from_the_code_s_own_lists(self, world):
        paths = _classification_paths(world.app)
        assert len(paths) > 40
        for prefix in static_mod.API_PREFIXES + static_mod.NON_SPA_PREFIXES:
            assert any(p.startswith(prefix) for p in paths)

    def test_every_api_prefix_is_a_reserved_route_and_mount(self, world):
        names = {getattr(r, "name", "") for r in world.app.routes}
        for prefix in static_mod.API_PREFIXES:
            slug = prefix.replace("/", "-")
            assert f"reserved-404-bare{slug}" in names, prefix
            assert f"reserved-404{slug}" in names, prefix

    def test_every_path_is_the_apis_or_carries_the_set(self, world):
        failures = []
        for path in _classification_paths(world.app):
            r = world.client.get(path)
            try:
                _api_or_framed(r, path)
            except AssertionError as e:  # collect, so one run names them all
                failures.append(f"{path} -> {r.status_code}: {e}")
        assert not failures, "\n".join(failures)

    def test_a_config_path_is_the_apis_not_the_shell(self, world):
        for path in ("/config", "/config/", "/config/x", "/config/%2e%2e/x", "/config/..%2fx"):
            r = world.client.get(path)
            assert SHELL not in r.text, path
            assert "text/html" not in r.headers.get("content-type", ""), path
        assert world.client.get("/config/branding").status_code == 200

    def test_the_spa_answers_only_what_is_neither_api_nor_reserved(self):
        for path in ("/", "/a", "/entries/x", "/settings", "/config-like", "/apix", "/wsx"):
            assert static_mod.spa_may_answer(path), path
        for path in (
            *static_mod.API_PREFIXES,
            "/api/x",
            "/config/x",
            "/healthz",
            "/docs/x",
        ):
            assert not static_mod.spa_may_answer(path), path


class TestBrandingUrls:
    def _service(self, tmp_path, url_field, value):
        d = tmp_path / "b"
        d.mkdir(exist_ok=True)
        (d / "branding.yaml").write_text(f'{url_field}: "{value}"\n')
        return BrandingService(d)

    @pytest.mark.parametrize("field", ["site_url", "support_url", "footer_credit_url"])
    @pytest.mark.parametrize(
        "bad",
        ["other:value", "ftp://host.example/x", "//host.example/x", "/relative/path", "http://"],
    )
    def test_only_http_and_https_urls_are_kept(self, tmp_path, field, bad):
        with pytest.raises(BrandingInvalidError):
            self._service(tmp_path, field, bad).get()

    @pytest.mark.control(
        reason="a mailto: contact was accepted before; the validator must keep accepting it"
    )
    def test_support_url_may_be_a_mailto_contact(self, tmp_path):
        # deploy/branding-examples/transparency-cascade ships one.
        svc = self._service(tmp_path, "support_url", "mailto:help@example.org")
        assert svc.get().support_url == "mailto:help@example.org"

    @pytest.mark.parametrize("field", ["site_url", "footer_credit_url"])
    def test_mailto_is_only_for_the_contact(self, tmp_path, field):
        with pytest.raises(BrandingInvalidError):
            self._service(tmp_path, field, "mailto:a@b.example").get()

    @pytest.mark.control(
        reason="valid values were accepted before; the validator must not reject them"
    )
    @pytest.mark.parametrize("field", ["site_url", "support_url", "footer_credit_url"])
    @pytest.mark.parametrize("ok", ["https://example.org/x", "http://example.org", ""])
    def test_http_and_empty_urls_are_kept(self, tmp_path, field, ok):
        assert getattr(self._service(tmp_path, field, ok).get(), field) == ok

    @pytest.mark.parametrize("field", ["site_url", "support_url", "footer_credit_url"])
    def test_what_is_stored_is_what_was_validated(self, tmp_path, field):
        svc = self._service(tmp_path, field, "  https://example.org/x  ")
        assert getattr(svc.get(), field) == "https://example.org/x"


class TestUncredentialedSocketCap:
    class _WS:
        def __init__(self, on_accept=None):
            self.accepted = False
            self.closed = None
            self._on_accept = on_accept

        async def accept(self):
            self.accepted = True
            if self._on_accept:
                self._on_accept()

        async def close(self, code=1000, reason=None):
            self.closed = code

    def test_anonymous_sockets_stop_being_accepted_at_the_cap(self):
        from pyrite.server import websocket as w

        mgr = w.ConnectionManager()
        cap = w.MAX_ANONYMOUS_SOCKETS

        async def run():
            socks = []
            for _ in range(cap):
                ws = self._WS()
                assert await mgr.connect(ws, w.SocketScope(readable={"pub"}, anonymous=True))
                socks.append(ws)
            over = self._WS()
            assert not await mgr.connect(over, w.SocketScope(readable={"pub"}, anonymous=True))
            assert not over.accepted and over.closed == w.CAP_CLOSE_CODE
            # A credentialed socket is not counted against the cap.
            ok = self._WS()
            assert await mgr.connect(ok, w.SocketScope(readable={"pub"}, user_id=1))
            # Room frees up when one leaves.
            mgr.disconnect(socks[0])
            again = self._WS()
            assert await mgr.connect(again, w.SocketScope(readable={"pub"}, anonymous=True))

        asyncio.run(run())

    def test_handshakes_in_flight_when_the_cap_is_reached_are_refused(self):
        from pyrite.server import websocket as w

        mgr = w.ConnectionManager()
        scope = w.SocketScope(readable={"pub"}, anonymous=True)

        def fill():
            # Other handshakes finished while this one was being accepted.
            for _ in range(w.MAX_ANONYMOUS_SOCKETS):
                mgr._connections[object()] = w.SocketScope(readable={"pub"}, anonymous=True)

        async def run():
            ws = self._WS(on_accept=fill)
            assert not await mgr.connect(ws, scope)
            assert ws.accepted and ws.closed == w.CAP_CLOSE_CODE
            assert ws not in mgr._connections
            assert mgr.anonymous_count == w.MAX_ANONYMOUS_SOCKETS

        asyncio.run(run())

    def test_a_socket_with_auth_disabled_is_not_counted(self):
        # Known limit: only anonymous visitors are capped; the local,
        # credential-free install stays uncapped.
        from pyrite.server import websocket as w

        assert not w.SocketScope(readable=None).anonymous

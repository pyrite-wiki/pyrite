"""A server that needs no credential answers only local hosts and refuses
cross-origin writes.

With auth disabled and no API keys every caller is admin (and with
``anonymous_tier: write`` every visitor can write), so two things stand
between a web page the user happens to visit and their KBs:

- **Host**: a request must be addressed to a host this server expects
  (``localhost``, ``127.0.0.1``, ``::1``, the configured bind host when it is
  not a wildcard, and ``settings.allowed_hosts``). Anything else gets 421 and
  runs no handler. This is what a re-pointed DNS name cannot satisfy.
- **Origin**: a state-changing request (anything but GET/HEAD/OPTIONS) whose
  ``Origin`` -- or, when there is no ``Origin``, whose ``Referer`` -- names
  neither this host nor an entry in ``cors_origins`` gets 403 and changes
  nothing. Requests with neither header (CLI, curl, agents) are unaffected.

All through the real app with TestClient. TestClient addresses requests to
``testserver``, which the repo-wide conftest admits for every other test
file; this module overrides that fixture so the production defaults apply and
each request names its Host explicitly through ``base_url``.
"""

import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app
from pyrite.server.websocket import manager

EVIL = "https://evil.example"


@pytest.fixture(autouse=True)
def _allow_testclient_host():
    """Override the repo-wide fixture: production defaults, no ``testserver``."""
    return


def _config(tmp: Path, **settings) -> PyriteConfig:
    kb = tmp / "kb"
    kb.mkdir(exist_ok=True)
    auth = settings.pop("auth", AuthConfig(enabled=False))
    return PyriteConfig(
        knowledge_bases=[KBConfig(name="notes", path=kb, kb_type="generic")],
        settings=Settings(index_path=tmp / "index.db", auth=auth, **settings),
    )


@pytest.fixture
def make(tmp_path):
    def _make(addr: str = "localhost:8088", **settings) -> TestClient:
        app = create_app(config=_config(tmp_path, **settings))
        return TestClient(app, base_url=f"http://{addr}")

    return _make


def _entry_files(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "kb").rglob("*.md"))


def _import(client: TestClient, headers: dict | None = None):
    data = json.dumps([{"title": "Planted", "body": "planted body"}])
    return client.post(
        "/api/entries/import?kb=notes&format=json",
        files={"file": ("x.json", data, "application/json")},
        headers=headers or {},
    )


def _first_status(app, method: str, path: str, host: str, deadline: float = 10.0) -> int:
    """Run one request through the ASGI app and return the first response
    status, cancelling the app as soon as it is sent (a stream never ends).
    ``deadline`` is a backstop so no outcome can hang the suite."""
    import anyio

    status: dict[str, int] = {}

    async def main() -> None:
        with anyio.fail_after(deadline):
            async with anyio.create_task_group() as tg:
                sent_body = False

                async def receive():
                    nonlocal sent_body
                    if not sent_body:
                        sent_body = True
                        return {"type": "http.request", "body": b"", "more_body": False}
                    await anyio.sleep_forever()

                async def send(message):
                    if message["type"] == "http.response.start" and "code" not in status:
                        status["code"] = message["status"]
                        tg.cancel_scope.cancel()

                scope = {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": method,
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode(),
                    "root_path": "",
                    "query_string": b"",
                    "headers": [(b"host", host.encode())],
                    "client": ("127.0.0.1", 50000),
                    "server": ("127.0.0.1", 8088),
                }
                tg.start_soon(app, scope, receive, send)

    anyio.run(main)
    return status["code"]


# ---------------------------------------------------------------------------
# Host
# ---------------------------------------------------------------------------


class TestHostAllowList:
    @pytest.mark.parametrize("host", ["attacker.example:8088", "attacker.example", "0.0.0.0:8088"])
    def test_unexpected_host_is_refused(self, make, host):
        r = make(host).get("/api/kbs")
        assert r.status_code == 421, r.text

    @pytest.mark.parametrize(
        "host", ["localhost:8088", "localhost", "127.0.0.1:8088", "[::1]:8088"]
    )
    def test_local_hosts_are_admitted(self, make, host):
        r = make(host).get("/api/kbs")
        assert r.status_code == 200, r.text

    def test_host_comparison_ignores_case(self, make):
        # Sent as a raw header: httpx lowercases a Host it derives from a URL.
        r = make().get("/api/kbs", headers={"Host": "LocalHost:8088"})
        assert r.status_code == 200, r.text
        r = make(allowed_hosts=["Pyrite.LAN"]).get("/api/kbs", headers={"Host": "pyrite.lan"})
        assert r.status_code == 200, r.text

    def test_testclient_default_host_is_not_a_production_default(self, make):
        r = make("testserver").get("/api/kbs")
        assert r.status_code == 421

    def test_configured_allowed_hosts_are_admitted(self, make):
        r = make("pyrite.lan:8088", allowed_hosts=["pyrite.lan"]).get("/api/kbs")
        assert r.status_code == 200, r.text
        r = make("[fd00::5]:8088", allowed_hosts=["[fd00::5]"]).get("/api/kbs")
        assert r.status_code == 200, r.text

    def test_configured_bind_host_is_admitted(self, make):
        r = make("10.0.0.5:8088", host="10.0.0.5").get("/api/kbs")
        assert r.status_code == 200, r.text

    def test_wildcard_bind_host_admits_nothing_extra(self, make):
        r = make("0.0.0.0:8088", host="0.0.0.0").get("/api/kbs")
        assert r.status_code == 421

    def test_refused_host_runs_no_handler(self, make, tmp_path):
        r = _import(make("attacker.example:8088"))
        assert r.status_code == 421
        assert _entry_files(tmp_path) == []

    @pytest.mark.parametrize("path", ["/site/robots.txt", "/site/search", "/site/notes/x"])
    def test_public_site_is_covered(self, make, path):
        """/site is mounted beside the API, not under it; the Host rule
        still reaches it."""
        assert make("attacker.example:8088").get(path).status_code == 421
        assert make("localhost:8088").get(path).status_code != 421

    def test_non_api_routes_are_covered(self, make):
        assert make("attacker.example:8088").get("/health").status_code == 421
        assert make("localhost:8088").get("/health").status_code == 200


# ---------------------------------------------------------------------------
# When the guard applies: exactly when a request can act without a credential
# ---------------------------------------------------------------------------

KEY = "operator-key"


class TestGuardAppliesOnlyWithoutACredential:
    """Four modes. The guard is on for (a) auth disabled with no API keys --
    every request is admin -- and (b) auth enabled with anonymous_tier
    "write" -- anonymous visitors can write. Everywhere else a request that
    acts needs a credential a foreign page cannot supply, so the guard is off
    and a keyed or proxied deployment answers any hostname."""

    def test_auth_disabled_without_keys_is_guarded(self, make, tmp_path):
        assert make("attacker.example").get("/health").status_code == 421
        assert _import(make(), {"Origin": EVIL}).status_code == 403

    def test_legacy_single_api_key_mode_is_not_guarded(self, make, tmp_path):
        headers = {"X-API-Key": KEY}
        client = make("pyrite.example.org", api_key=KEY)
        assert client.get("/api/kbs", headers=headers).status_code == 200
        r = _import(client, {**headers, "Origin": "https://ui.example.org"})
        assert r.status_code == 200, r.text

    def test_api_keys_list_mode_is_not_guarded(self, make):
        import hashlib

        keys = [{"key_hash": hashlib.sha256(KEY.encode()).hexdigest(), "role": "admin"}]
        client = make("pyrite.example.org", api_keys=keys)
        headers = {"X-API-Key": KEY}
        assert client.get("/api/kbs", headers=headers).status_code == 200
        r = _import(client, {**headers, "Origin": "https://ui.example.org"})
        assert r.status_code == 200, r.text

    def test_anonymous_write_tier_is_guarded(self, make, tmp_path):
        auth = AuthConfig(enabled=True, anonymous_tier="write")
        assert make("attacker.example", auth=auth).get("/health").status_code == 421
        r = _import(make(auth=AuthConfig(enabled=True, anonymous_tier="write")), {"Origin": EVIL})
        assert r.status_code == 403
        assert _entry_files(tmp_path) == []

    def test_anonymous_write_tier_answers_its_allowed_public_name(self, make):
        auth = AuthConfig(enabled=True, anonymous_tier="write")
        client = make("wiki.example.org", auth=auth, allowed_hosts=["wiki.example.org"])
        assert client.get("/health").status_code == 200

    @pytest.mark.parametrize("tier", [None, "read"])
    def test_auth_enabled_without_anonymous_writes_is_not_guarded(self, make, tier):
        """A server that requires a credential to write answers any hostname
        its proxy sends, and cross-origin requests fall to the credential."""
        auth = AuthConfig(enabled=True, anonymous_tier=tier)
        client = make("pyrite.example.org", auth=auth)
        assert client.get("/health").status_code == 200
        r = client.post("/auth/logout", headers={"Origin": EVIL})
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Origin
# ---------------------------------------------------------------------------


class TestCrossOriginWrites:
    def test_cross_origin_multipart_import_is_refused_and_writes_nothing(self, make, tmp_path):
        r = _import(make(), {"Origin": EVIL})
        assert r.status_code == 403, r.text
        assert _entry_files(tmp_path) == []

    def test_import_without_origin_still_works(self, make, tmp_path):
        r = _import(make())
        assert r.status_code == 200, r.text
        assert _entry_files(tmp_path)

    @pytest.mark.parametrize("host", ["localhost:8088", "127.0.0.1:8088"])
    def test_same_host_origin_still_works(self, make, tmp_path, host):
        r = _import(make(host), {"Origin": f"http://{host}"})
        assert r.status_code == 200, r.text

    def test_configured_cors_origin_is_admitted_through_a_host_rewriting_proxy(self, make):
        """The Vite dev server proxies with changeOrigin: the backend sees
        Host 127.0.0.1:8088 and the browser's Origin localhost:5173."""
        r = _import(make("127.0.0.1:8088"), {"Origin": "http://localhost:5173"})
        assert r.status_code == 200, r.text

    def test_wildcard_cors_origin_does_not_admit_cross_origin_writes(self, make, tmp_path):
        r = _import(make(cors_origins=["*"]), {"Origin": EVIL})
        assert r.status_code == 403
        assert _entry_files(tmp_path) == []

    def test_null_origin_is_refused(self, make):
        assert _import(make(), {"Origin": "null"}).status_code == 403

    def test_foreign_referer_without_origin_is_refused(self, make, tmp_path):
        r = _import(make(), {"Referer": f"{EVIL}/page"})
        assert r.status_code == 403
        assert _entry_files(tmp_path) == []

    def test_same_host_referer_without_origin_still_works(self, make):
        r = _import(make(), {"Referer": "http://localhost:8088/entries"})
        assert r.status_code == 200, r.text

    def test_origin_wins_over_referer(self, make):
        r = _import(make(), {"Origin": "http://localhost:8088", "Referer": f"{EVIL}/x"})
        assert r.status_code == 200, r.text

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/api/index/sync"),
            ("POST", "/api/kbs/gc"),
            ("PUT", "/api/entries/nope?kb=notes"),
            ("PATCH", "/api/entries/nope?kb=notes"),
            ("DELETE", "/api/entries/nope?kb=notes"),
            ("POST", "/auth/logout"),
        ],
    )
    def test_every_unsafe_method_is_checked(self, make, method, path):
        r = make().request(method, path, headers={"Origin": EVIL})
        assert r.status_code == 403, (method, path, r.status_code)

    def test_safe_methods_are_not_origin_checked(self, make):
        """Reading cross-origin is CORS's job (the browser withholds the
        response); refusing it here would break nothing but gain nothing."""
        assert make().get("/api/kbs", headers={"Origin": EVIL}).status_code == 200


# ---------------------------------------------------------------------------
# Cookie-authenticated writes, in every mode (P-A3, P-B3)
# ---------------------------------------------------------------------------


class TestCookieWritesAreOriginChecked:
    """With auth enabled and no anonymous writes the Host/Origin guard above
    is off -- but a request carrying the session cookie is not a request a
    foreign page cannot cause: ``SameSite=Lax`` still attaches the cookie to
    a POST from a *same-site* page (another port on localhost, a sibling
    subdomain). So a mutating request with a session cookie is refused unless
    its Origin (or Referer) is this host or in ``cors_origins``, in every
    mode. An API key is a header a foreign page cannot set without a
    preflight, so a key-authenticated request is not cookie-bound."""

    HOST = "wiki.example.org"
    SIBLING = "https://other.example.org"  # same site, different origin

    def _client(self, make, **settings) -> TestClient:
        from tests.auth_seed import SESSION_COOKIE, app_db, seed_user, sign_in

        settings.setdefault("auth", AuthConfig(enabled=True))
        client = make(self.HOST, **settings)
        seed_user(app_db(client.app), "cookie-admin", role="admin")
        client.cookies.set(SESSION_COOKIE, sign_in(client.app, "cookie-admin"))
        return client

    @pytest.mark.control(reason="proves the cookie authenticates; holds without the fix")
    def test_the_session_cookie_authenticates_this_client(self, make, tmp_path):
        """Not vacuous: without Origin the cookie alone lets the import write."""
        r = _import(self._client(make))
        assert r.status_code == 200, r.text
        assert _entry_files(tmp_path)

    def test_same_site_foreign_origin_is_refused_and_writes_nothing(self, make, tmp_path):
        r = _import(self._client(make), {"Origin": self.SIBLING})
        assert r.status_code == 403, r.text
        assert _entry_files(tmp_path) == []

    def test_same_site_foreign_referer_is_refused(self, make, tmp_path):
        r = _import(self._client(make), {"Referer": f"{self.SIBLING}/page"})
        assert r.status_code == 403, r.text
        assert _entry_files(tmp_path) == []

    @pytest.mark.control(reason="allowed before and after the fix")
    def test_same_origin_is_allowed(self, make, tmp_path):
        r = _import(self._client(make), {"Origin": f"http://{self.HOST}"})
        assert r.status_code == 200, r.text
        assert _entry_files(tmp_path)

    @pytest.mark.control(reason="allowed before and after the fix")
    def test_configured_cors_origin_is_allowed(self, make, tmp_path):
        client = self._client(make, cors_origins=[self.SIBLING])
        r = _import(client, {"Origin": self.SIBLING})
        assert r.status_code == 200, r.text
        assert _entry_files(tmp_path)

    def test_wildcard_cors_origin_does_not_admit_a_cookie_write(self, make, tmp_path):
        r = _import(self._client(make, cors_origins=["*"]), {"Origin": self.SIBLING})
        assert r.status_code == 403, r.text

    def test_anonymous_read_tier_is_checked_too(self, make, tmp_path):
        client = self._client(make, auth=AuthConfig(enabled=True, anonymous_tier="read"))
        r = _import(client, {"Origin": self.SIBLING})
        assert r.status_code == 403, r.text
        assert _entry_files(tmp_path) == []

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("PUT", "/api/entries/nope?kb=notes"),
            ("PATCH", "/api/entries/nope?kb=notes"),
            ("DELETE", "/api/entries/nope?kb=notes"),
            ("POST", "/auth/logout"),
            ("POST", "/mcp/messages/?session_id=x"),
        ],
    )
    def test_every_unsafe_method_and_surface_is_checked(self, make, method, path):
        r = self._client(make).request(method, path, headers={"Origin": self.SIBLING})
        assert r.status_code == 403, (method, path, r.status_code)

    @pytest.mark.control(reason="reads were never origin-checked")
    def test_safe_methods_are_not_origin_checked(self, make):
        r = self._client(make).get("/api/kbs", headers={"Origin": self.SIBLING})
        assert r.status_code == 200, r.text

    @pytest.mark.control(reason="API-key requests are exempt before and after the fix")
    @pytest.mark.parametrize("with_cookie", [False, True], ids=["key-only", "key-and-cookie"])
    def test_api_key_request_is_unaffected(self, make, tmp_path, with_cookie):
        import hashlib

        keys = [{"key_hash": hashlib.sha256(KEY.encode()).hexdigest(), "role": "admin"}]
        client = self._client(make, api_keys=keys)
        if not with_cookie:
            client.cookies.clear()
        r = _import(client, {"X-API-Key": KEY, "Origin": self.SIBLING})
        assert r.status_code == 200, r.text
        assert _entry_files(tmp_path)

    def test_a_rejected_api_key_does_not_exempt_the_cookie(self, make, tmp_path):
        """An unknown key falls through to the cookie, so the request is
        cookie-authenticated after all."""
        import hashlib

        keys = [{"key_hash": hashlib.sha256(KEY.encode()).hexdigest(), "role": "admin"}]
        client = self._client(make, api_keys=keys)
        r = _import(client, {"X-API-Key": "not-a-key", "Origin": self.SIBLING})
        assert r.status_code == 403, r.text
        assert _entry_files(tmp_path) == []

    @pytest.mark.parametrize("site", ["same-site", "cross-site"])
    def test_no_origin_but_fetch_metadata_says_cross_origin_is_refused(self, make, tmp_path, site):
        """With neither Origin nor Referer, ``Sec-Fetch-Site`` is the browser
        saying where the request came from; ``same-site`` is still another
        origin."""
        r = _import(self._client(make), {"Sec-Fetch-Site": site})
        assert r.status_code == 403, r.text
        assert _entry_files(tmp_path) == []

    @pytest.mark.control(
        reason="same-origin and user-initiated requests are admitted before and after"
    )
    @pytest.mark.parametrize("site", ["same-origin", "none"])
    def test_no_origin_and_fetch_metadata_same_origin_is_admitted(self, make, tmp_path, site):
        r = _import(self._client(make), {"Sec-Fetch-Site": site})
        assert r.status_code == 200, r.text
        assert _entry_files(tmp_path)

    @pytest.mark.control(reason="no browser signal at all: admitted before and after")
    def test_no_origin_referer_or_fetch_metadata_is_admitted(self, make, tmp_path):
        r = _import(self._client(make))
        assert r.status_code == 200, r.text
        assert _entry_files(tmp_path)

    def test_an_api_key_in_the_query_does_not_exempt_the_cookie(self, make, tmp_path):
        """A foreign page can put a key in a URL, and ``/mcp`` ignores it and
        uses the cookie, so only the ``X-API-Key`` header exempts."""
        import hashlib

        keys = [{"key_hash": hashlib.sha256(KEY.encode()).hexdigest(), "role": "admin"}]
        client = self._client(make, api_keys=keys)
        data = json.dumps([{"title": "Planted", "body": "planted body"}])
        r = client.post(
            f"/api/entries/import?kb=notes&format=json&api_key={KEY}",
            files={"file": ("x.json", data, "application/json")},
            headers={"Origin": self.SIBLING},
        )
        assert r.status_code == 403, r.text
        assert _entry_files(tmp_path) == []


# ---------------------------------------------------------------------------
# /mcp and /ws
# ---------------------------------------------------------------------------


class TestMcpAndWebSocket:
    def test_mcp_sse_refuses_unexpected_host(self, make):
        # An admitted /mcp/sse request opens an event stream that never ends,
        # and TestClient waits for the app to finish. So this drives the ASGI
        # app directly and stops at the first response status, whatever it
        # is: the red run fails on the status in well under a second.
        app = make("attacker.example:8088").app
        assert _first_status(app, "GET", "/mcp/sse", "attacker.example:8088") == 421

    def test_mcp_sse_status_probe_sees_an_admitted_stream(self, make):
        """The probe above is not vacuous: on an allowed host it sees the
        stream's own 200 and returns instead of hanging."""
        app = make("localhost:8088").app
        assert _first_status(app, "GET", "/mcp/sse", "localhost:8088") == 200

    def test_mcp_messages_refuses_unexpected_host(self, make):
        r = make("attacker.example:8088").post("/mcp/messages/?session_id=x", json={})
        assert r.status_code == 421

    def test_mcp_messages_refuses_cross_origin_post(self, make):
        r = make().post("/mcp/messages/?session_id=x", json={}, headers={"Origin": EVIL})
        assert r.status_code == 403

    def test_mcp_messages_without_origin_reaches_the_transport(self, make):
        r = make().post("/mcp/messages/?session_id=x", json={})
        assert r.status_code not in (403, 421), r.status_code

    def test_websocket_refuses_unexpected_host(self, make):
        before = manager.connection_count
        with make("attacker.example:8088") as c:
            # websocket_connect ignores base_url (it joins onto ws://testserver),
            # so the Host is named in the URL itself.
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect("ws://attacker.example:8088/ws"):
                    pass
        assert manager.connection_count == before

    def test_websocket_admits_local_host(self, make):
        with make("localhost:8088") as c:
            with c.websocket_connect("ws://localhost:8088/ws"):
                assert manager.connection_count >= 1


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestAllowedHostsSetting:
    def test_defaults_to_empty(self):
        assert Settings().allowed_hosts == []

    def test_round_trips_through_config_dict(self, tmp_path):
        cfg = _config(tmp_path, allowed_hosts=["pyrite.lan", "notes.home"])
        again = PyriteConfig.from_dict(cfg.to_dict())
        assert again.settings.allowed_hosts == ["pyrite.lan", "notes.home"]

    def test_env_override(self, tmp_path, monkeypatch):
        from pyrite.config import _apply_env_overrides

        monkeypatch.setenv("PYRITE_ALLOWED_HOSTS", " pyrite.lan , notes.home ,")
        cfg = _config(tmp_path)
        _apply_env_overrides(cfg)
        assert cfg.settings.allowed_hosts == ["pyrite.lan", "notes.home"]


class TestServeCommand:
    def test_serve_host_flag_is_an_allowed_host(self, tmp_path, monkeypatch):
        """`pyrite serve --host 10.0.0.5` binds there, so the app it serves
        must answer requests addressed there."""
        import uvicorn
        from typer.testing import CliRunner

        import pyrite.cli as cli

        cfg = _config(tmp_path)
        monkeypatch.setattr(cli, "load_config", lambda: cfg)
        served = {}
        monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app, **kw))

        result = CliRunner().invoke(cli.app, ["serve", "--host", "10.0.0.5", "--port", "8123"])
        assert result.exit_code == 0, result.output
        assert served["host"] == "10.0.0.5"

        client = TestClient(served["app"], base_url="http://10.0.0.5:8123")
        assert client.get("/health").status_code == 200
        client = TestClient(served["app"], base_url="http://attacker.example:8123")
        assert client.get("/health").status_code == 421

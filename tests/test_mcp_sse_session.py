"""An MCP SSE session is a credential, and lives no longer than the one that
opened it (P-M3, P-M4, P-A2, P-L1 in kb/designs/multi-user-threat-model.md).

Three properties, each driven over the real transport -- ``create_app``'s
``/mcp/sse`` stream and ``/mcp/messages/`` relay, on the TestClient's own
event loop, so startup hooks (the credential-change listener) run as they do
under uvicorn:

1. A message posted to a session acts only for the principal that opened
   it. A POST carrying another principal's credential, or none, is answered
   exactly as an unknown session.
2. The session ends on the credential events that close ``/ws``: logout of
   the opening session, a grant or role change of its user, and the
   session's expiry. The client reconnects and is scoped afresh -- a demoted
   admin's new session has no admin tools.
3. The local rate-limit exemption is the *local* principal kind, never a
   name; buckets key on kind plus id, so no username shares one with an
   operator key.

**Why a hand-rolled ASGI driver.** A ``TestClient`` GET to ``/mcp/sse``
never returns while the stream is open, and ``httpx.ASGITransport`` buffers
the whole body. ``_SSE`` runs the app with its own ``receive``/``send`` on
the TestClient portal's loop and reads events as they are sent; POSTs and
REST calls go through ``httpx.ASGITransport`` on the same loop. Every wait
is bounded by ``anyio.fail_after``.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app
from pyrite.services.auth_service import AuthService
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_and_sign_in

PUBLIC, PRIVATE = "public-kb", "private-kb"
OPERATOR_KEY = "operator-secret-key"
READ_KEY = "read-only-operator-key"
WAIT = 10.0  # every bounded wait in this file


def _config(tmp: Path, **settings) -> PyriteConfig:
    (tmp / PUBLIC).mkdir()
    (tmp / PRIVATE).mkdir()
    return PyriteConfig(
        knowledge_bases=[
            KBConfig(name=PUBLIC, path=tmp / PUBLIC, kb_type="generic", default_role="read"),
            KBConfig(name=PRIVATE, path=tmp / PRIVATE, kb_type="generic", default_role="none"),
        ],
        settings=Settings(
            index_path=tmp / "index.db",
            api_key=OPERATOR_KEY,
            api_keys=[
                {"key_hash": hashlib.sha256(READ_KEY.encode()).hexdigest(), "role": "read"},
                {"key_hash": hashlib.sha256(OPERATOR_KEY.encode()).hexdigest(), "role": "admin"},
            ],
            auth=AuthConfig(enabled=True, allow_registration=True, max_sessions_per_user=5),
            **settings,
        ),
    )


def _cookie(token: str) -> dict[str, str]:
    return {"cookie": f"pyrite_session={token}"}


def _bearer(key: str) -> dict[str, str]:
    return {"authorization": f"Bearer {key}"}


class _SSE:
    """One open ``/mcp/sse`` stream, driven at the ASGI level."""

    def __init__(self, app, headers: dict[str, str]):
        self.app = app
        self.headers = headers
        self.status: int | None = None
        self.ended = anyio.Event()
        self._disconnect = anyio.Event()
        self._buf = ""
        self._events_send, self._events = anyio.create_memory_object_stream(100)
        self.session_url: str | None = None

    async def _receive(self):
        if not getattr(self, "_sent_body", False):
            self._sent_body = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message):
        if message["type"] == "http.response.start":
            self.status = message["status"]
        elif message["type"] == "http.response.body":
            self._buf += message.get("body", b"").decode()
            while "\r\n\r\n" in self._buf or "\n\n" in self._buf:
                sep = "\r\n\r\n" if "\r\n\r\n" in self._buf else "\n\n"
                raw, self._buf = self._buf.split(sep, 1)
                event, data = "message", ""
                for line in raw.splitlines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data += line[5:].strip()
                if data:
                    await self._events_send.send((event, data))

    async def run(self):
        raw_headers = [(k.lower().encode(), v.encode()) for k, v in self.headers.items()]
        raw_headers.append((b"host", b"testserver"))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/mcp/sse",
            "raw_path": b"/mcp/sse",
            "root_path": "",
            "query_string": b"",
            "headers": raw_headers,
            "client": ("testclient", 123),
            "server": ("testserver", 80),
        }
        try:
            await self.app(scope, self._receive, self._send)
        except Exception:
            pass
        finally:
            self.ended.set()
            self._events_send.close()

    async def next_event(self) -> tuple[str, str]:
        with anyio.fail_after(WAIT):
            return await self._events.receive()

    async def open(self, tg) -> _SSE:
        tg.start_soon(self.run)
        event, data = await self.next_event()
        assert event == "endpoint", (event, data)
        self.session_url = data
        return self

    def close(self):
        self._disconnect.set()

    async def wait_ended(self, timeout: float = WAIT) -> bool:
        with anyio.move_on_after(timeout):
            await self.ended.wait()
            return True
        return False


class _World:
    def __init__(self, app, config):
        self.app = app
        self.config = config
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self._next_id = 0

    async def post(self, sse: _SSE, method: str, params: dict | None, headers: dict):
        self._next_id += 1
        body = {"jsonrpc": "2.0", "method": method, "id": self._next_id, "params": params or {}}
        resp = await self.http.post(
            sse.session_url,
            content=json.dumps(body),
            headers={"content-type": "application/json", **headers},
        )
        return resp, self._next_id

    async def notify(self, sse: _SSE, method: str, headers: dict):
        body = {"jsonrpc": "2.0", "method": method}
        return await self.http.post(
            sse.session_url,
            content=json.dumps(body),
            headers={"content-type": "application/json", **headers},
        )

    async def rpc(self, sse: _SSE, method: str, params: dict | None, headers: dict) -> dict:
        resp, msg_id = await self.post(sse, method, params, headers)
        assert resp.status_code == 202, resp.text
        while True:
            event, data = await sse.next_event()
            msg = json.loads(data)
            if msg.get("id") == msg_id:
                return msg

    async def initialize(self, sse: _SSE, headers: dict) -> None:
        await self.rpc(
            sse,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
            headers,
        )
        resp = await self.notify(sse, "notifications/initialized", headers)
        assert resp.status_code == 202, resp.text

    async def call(self, sse: _SSE, tool: str, args: dict, headers: dict) -> dict:
        msg = await self.rpc(sse, "tools/call", {"name": tool, "arguments": args}, headers)
        return json.loads(msg["result"]["content"][0]["text"])

    async def tool_names(self, sse: _SSE, headers: dict) -> set[str]:
        msg = await self.rpc(sse, "tools/list", {}, headers)
        return {t["name"] for t in msg["result"]["tools"]}

    def auth(self, fn):
        db = PyriteDB(self.config.settings.index_path)
        try:
            return fn(AuthService(db, self.config.settings.auth))
        finally:
            db.close()


def _login(app, username: str) -> str:
    r = TestClient(app).post("/auth/login", json={"username": username, "password": "password123"})
    assert r.status_code == 200, r.text
    return r.cookies["pyrite_session"]


@pytest.fixture
def env():
    """``root`` (admin), ``alice`` and ``bob`` (read, each with a read grant
    on the private KB), and ``boss`` (admin, to be demoted)."""
    with tempfile.TemporaryDirectory() as d:
        config = _config(Path(d))
        app = create_app(config=config)
        with TestClient(app) as client:
            seed_and_sign_in(client, "root", "password123", role="admin")
            world = _World(app, config)

            def setup(auth):
                ids = {}
                for name, role in (("alice", "read"), ("bob", "read"), ("boss", "admin")):
                    ids[name] = auth.create_user(name, "password123", role=role)["id"]
                for name in ("alice", "bob"):
                    auth.grant_kb_permission(ids[name], PRIVATE, "read", ids["boss"])
                return ids

            world.ids = world.auth(setup)
            world.tokens = {n: _login(app, n) for n in ("alice", "bob", "boss")}
            yield client, world


def _run(client, scenario):
    """Run ``scenario(tg)`` on the TestClient's loop, inside a task group
    that is cancelled when the scenario returns (closing any open stream)."""

    async def main():
        async with anyio.create_task_group() as tg:
            try:
                with anyio.fail_after(60):
                    await scenario(tg)
            finally:
                tg.cancel_scope.cancel()

    client.portal.call(main)


# =============================================================================
# 1. A message acts only for the principal that opened the session (P-M3)
# =============================================================================


class TestSessionOwner:
    @pytest.mark.control(reason="the owner's own messages are served before and after the fix")
    def test_owner_message_is_served_with_the_owner_scope(self, env):
        """Control: the opener's own messages are accepted and answered."""
        client, w = env
        alice = _cookie(w.tokens["alice"])

        async def scenario(tg):
            sse = await _SSE(w.app, alice).open(tg)
            await w.initialize(sse, alice)
            names = await w.tool_names(sse, alice)
            assert "kb_list" in names

        _run(client, scenario)

    def test_another_users_message_is_refused_as_unknown_session(self, env):
        client, w = env
        alice, bob = _cookie(w.tokens["alice"]), _cookie(w.tokens["bob"])

        async def scenario(tg):
            sse = await _SSE(w.app, alice).open(tg)
            resp, _ = await w.post(sse, "ping", {}, bob)
            assert resp.status_code == 404, resp.text

        _run(client, scenario)

    def test_a_message_with_no_credential_is_refused(self, env):
        client, w = env
        alice = _cookie(w.tokens["alice"])

        async def scenario(tg):
            sse = await _SSE(w.app, alice).open(tg)
            resp, _ = await w.post(sse, "ping", {}, {})
            assert resp.status_code == 404, resp.text

        _run(client, scenario)

    def test_a_different_operator_key_cannot_post_to_a_key_session(self, env):
        """A read key cannot act through an admin key's session."""
        client, w = env

        async def scenario(tg):
            sse = await _SSE(w.app, _bearer(OPERATOR_KEY)).open(tg)
            resp, _ = await w.post(sse, "ping", {}, _bearer(READ_KEY))
            assert resp.status_code == 404, resp.text
            resp, _ = await w.post(sse, "ping", {}, _bearer(OPERATOR_KEY))
            assert resp.status_code == 202, resp.text

        _run(client, scenario)

    @pytest.mark.control(reason="ownership is per principal; guards against over-tightening")
    def test_the_owner_with_a_second_session_may_post(self, env):
        """Ownership is the principal, not the one token: the same user's
        other live session may post (the brief's property is the principal)."""
        client, w = env
        alice = _cookie(w.tokens["alice"])
        other = _cookie(_login(w.app, "alice"))

        async def scenario(tg):
            sse = await _SSE(w.app, alice).open(tg)
            resp, _ = await w.post(sse, "ping", {}, other)
            assert resp.status_code == 202, resp.text

        _run(client, scenario)


# =============================================================================
# 2. The session ends on the credential events that close /ws (P-M4, P-A2)
# =============================================================================


class TestSessionLifetime:
    def test_logout_of_the_opening_session_ends_the_stream(self, env):
        client, w = env
        token = w.tokens["alice"]

        async def scenario(tg):
            sse = await _SSE(w.app, _cookie(token)).open(tg)
            r = await w.http.post("/auth/logout", headers=_cookie(token))
            assert r.status_code == 200, r.text
            assert await sse.wait_ended(), "SSE session outlived its logged-out credential"

        _run(client, scenario)

    @pytest.mark.control(reason="an unrelated logout must not end this stream")
    def test_logout_of_another_users_session_leaves_the_stream_open(self, env):
        client, w = env
        alice = _cookie(w.tokens["alice"])

        async def scenario(tg):
            sse = await _SSE(w.app, alice).open(tg)
            r = await w.http.post("/auth/logout", headers=_cookie(w.tokens["bob"]))
            assert r.status_code == 200, r.text
            assert not await sse.wait_ended(0.5)
            await w.initialize(sse, alice)

        _run(client, scenario)

    def test_revoking_a_grant_ends_the_stream(self, env):
        client, w = env
        alice = _cookie(w.tokens["alice"])

        async def scenario(tg):
            sse = await _SSE(w.app, alice).open(tg)
            await w.initialize(sse, alice)
            await anyio.to_thread.run_sync(
                lambda: w.auth(lambda a: a.revoke_kb_permission(w.ids["alice"], PRIVATE))
            )
            assert await sse.wait_ended(), "SSE session kept a revoked grant"

        _run(client, scenario)

    def test_a_demoted_admin_loses_admin_tools(self, env):
        client, w = env
        boss = _cookie(w.tokens["boss"])

        async def scenario(tg):
            sse = await _SSE(w.app, boss).open(tg)
            await w.initialize(sse, boss)
            admin_tools = await w.tool_names(sse, boss)
            await anyio.to_thread.run_sync(
                lambda: w.auth(lambda a: a.set_role(w.ids["boss"], "read"))
            )
            assert await sse.wait_ended(), "SSE session kept its admin tier after demotion"

            again = await _SSE(w.app, boss).open(tg)
            await w.initialize(again, boss)
            read_tools = await w.tool_names(again, boss)
            assert read_tools < admin_tools

        _run(client, scenario)

    def test_session_expiry_ends_the_stream(self, env):
        client, w = env
        token = _login(w.app, "alice")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        soon = (datetime.now(UTC) + timedelta(seconds=1.5)).isoformat()
        db = PyriteDB(w.config.settings.index_path)
        try:
            db.execute_write_sql(
                "UPDATE session SET expires_at = :e WHERE token_hash = :h",
                {"e": soon, "h": token_hash},
            )
        finally:
            db.close()

        async def scenario(tg):
            sse = await _SSE(w.app, _cookie(token)).open(tg)
            assert await sse.wait_ended(), "SSE session outlived its session's expiry"

        _run(client, scenario)

    def test_a_change_during_the_handshake_ends_the_new_stream(self, env, monkeypatch):
        """A credential change that lands between resolving the credential
        and registering the session cannot be missed (the /ws epoch rule)."""
        from pyrite.server import mcp_routes
        from pyrite.services import credential_events
        from pyrite.services.credential_events import CredentialChange

        client, w = env
        alice = _cookie(w.tokens["alice"])
        real = mcp_routes._authenticate

        async def racing(request, config, db):
            ctx = await real(request, config, db)
            credential_events.publish(CredentialChange(user_id=w.ids["alice"]))
            return ctx

        monkeypatch.setattr(mcp_routes, "_authenticate", racing)

        async def scenario(tg):
            sse = _SSE(w.app, alice)
            tg.start_soon(sse.run)
            assert await sse.wait_ended(), "a session resolved before a revocation stayed open"

        _run(client, scenario)


# =============================================================================
# 3. The local exemption is a principal kind; buckets key on kind + id (P-L1)
# =============================================================================


@pytest.fixture
def limited():
    """Auth enabled with a two-call read limit, and two registered users
    whose names collide with the old exemption and an operator key's name."""
    with tempfile.TemporaryDirectory() as d:
        config = _config(Path(d), rate_limit_read="2/minute")
        app = create_app(config=config)
        with TestClient(app) as client:
            seed_and_sign_in(client, "root", "password123", role="admin")
            world = _World(app, config)
            key_name = "apikey-" + hashlib.sha256(READ_KEY.encode()).hexdigest()[:8]

            def setup(auth):
                for name in ("stdio", key_name):
                    auth.create_user(name, "password123", role="read")

            world.auth(setup)
            world.tokens = {"stdio": _login(app, "stdio"), "keyname": _login(app, key_name)}
            yield client, world


class TestRateLimitIdentity:
    def test_a_user_named_stdio_is_rate_limited(self, limited):
        client, w = limited
        me = _cookie(w.tokens["stdio"])

        async def scenario(tg):
            sse = await _SSE(w.app, me).open(tg)
            await w.initialize(sse, me)
            results = [await w.call(sse, "kb_list", {}, me) for _ in range(3)]
            assert results[-1].get("error_code") == "RATE_LIMITED", results[-1]

        _run(client, scenario)

    def test_a_user_named_like_a_key_does_not_share_its_bucket(self, limited):
        client, w = limited
        # Same tier as the user: each tier's server has its own limiter.
        me, key = _cookie(w.tokens["keyname"]), _bearer(READ_KEY)

        async def scenario(tg):
            mine = await _SSE(w.app, me).open(tg)
            await w.initialize(mine, me)
            for _ in range(3):
                await w.call(mine, "kb_list", {}, me)
            ops = await _SSE(w.app, key).open(tg)
            await w.initialize(ops, key)
            out = await w.call(ops, "kb_list", {}, key)
            assert out.get("error_code") != "RATE_LIMITED", out

        _run(client, scenario)

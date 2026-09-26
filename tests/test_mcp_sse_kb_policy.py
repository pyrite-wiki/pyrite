"""An MCP SSE session lives no longer than the KB policy that scoped it (P-W3, P-M4).

An SSE session's readable set is resolved once, at ``/mcp/sse`` connect, and
the session ends when the credential that opened it changes
(``mcp_sessions``). A KB's own policy changing -- its ``default_role``, or
the KB being removed -- is the same kind of change to what the session may
read, and ``/ws`` already closes on it. The property here is the same one on
the MCP transport: such a change ends every session whose readable set
included the KB; the client reconnects and is scoped afresh.

Driven over the real transport with ``test_mcp_sse_session``'s ASGI driver:
``create_app``'s ``/mcp/sse`` stream on the TestClient's loop, the changes
made through the admin REST routes on the same loop.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_and_sign_in
from tests.test_mcp_sse_session import (
    OPERATOR_KEY,
    _bearer,
    _cookie,
    _live_sessions,
    _login,
    _run,
    _SSE,
    _World,
)

OPEN, CLOSED, YAML = "open-kb", "closed-kb", "yaml-kb"
ADMIN = {"X-API-Key": OPERATOR_KEY}


@pytest.fixture
def env():
    """``alice`` (global read, no grants): reads OPEN and YAML through their
    ``default_role: read``, never CLOSED."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for name in (OPEN, CLOSED, YAML):
            (tmp / name).mkdir()
        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name=YAML, path=tmp / YAML, kb_type="generic", default_role="read")
            ],
            settings=Settings(
                index_path=tmp / "index.db",
                api_key=OPERATOR_KEY,
                auth=AuthConfig(enabled=True, allow_registration=True, max_sessions_per_user=5),
            ),
        )
        with PyriteDB(config.settings.index_path) as db:
            db.register_kb(YAML, "generic", str(tmp / YAML), source="config")
            db.register_kb(OPEN, "generic", str(tmp / OPEN), source="user", default_role="read")
            db.register_kb(CLOSED, "generic", str(tmp / CLOSED), source="user", default_role="none")
        app = create_app(config=config)
        with TestClient(app) as client:
            seed_and_sign_in(client, "root", "password123", role="admin")
            world = _World(app, config)
            world.ids = world.auth(
                lambda a: {"alice": a.create_user("alice", "password123", role="read")["id"]}
            )
            world.tokens = {"alice": _login(app, "alice")}
            yield client, world


async def _readable(w, sse, headers) -> set[str]:
    listed = await w.call(sse, "kb_list", {}, headers)
    return {kb["name"] for kb in listed["knowledge_bases"]}


class TestDefaultRoleChangeEndsTheSession:
    def test_closing_a_kb_the_session_read_ends_it(self, env):
        client, w = env
        alice = _cookie(w.tokens["alice"])

        async def scenario(tg):
            sse = await _SSE(w.app, alice).open(tg)
            await w.initialize(sse, alice)
            assert OPEN in await _readable(w, sse, alice)
            r = await w.http.put(
                f"/api/kbs/{OPEN}/default-role", json={"role": "none"}, headers=ADMIN
            )
            assert r.status_code == 200, r.text
            assert await sse.wait_ended(), "SSE session kept the scope of a closed KB"
            assert _live_sessions() == 0

            again = await _SSE(w.app, alice).open(tg)
            await w.initialize(again, alice)
            assert OPEN not in await _readable(w, again, alice)

        _run(client, scenario)

    @pytest.mark.control(reason="a KB outside the session's readable set does not end it")
    def test_a_change_to_a_kb_the_session_could_not_read_leaves_it_open(self, env):
        client, w = env
        alice = _cookie(w.tokens["alice"])

        async def scenario(tg):
            sse = await _SSE(w.app, alice).open(tg)
            r = await w.http.put(
                f"/api/kbs/{CLOSED}/default-role", json={"role": "none"}, headers=ADMIN
            )
            assert r.status_code == 200, r.text
            assert not await sse.wait_ended(0.5)
            await w.initialize(sse, alice)

        _run(client, scenario)

    @pytest.mark.control(reason="an unscoped operator-key session never held a KB scope")
    def test_an_operator_key_session_survives(self, env):
        client, w = env
        op = _bearer(OPERATOR_KEY)

        async def scenario(tg):
            sse = await _SSE(w.app, op).open(tg)
            r = await w.http.put(
                f"/api/kbs/{OPEN}/default-role", json={"role": "none"}, headers=ADMIN
            )
            assert r.status_code == 200, r.text
            assert not await sse.wait_ended(0.5)
            await w.initialize(sse, op)

        _run(client, scenario)


class TestKBRemovalEndsTheSession:
    def test_removing_a_kb_the_session_read_ends_it(self, env):
        client, w = env
        alice = _cookie(w.tokens["alice"])

        async def scenario(tg):
            sse = await _SSE(w.app, alice).open(tg)
            r = await w.http.delete(f"/api/kbs/{OPEN}", headers=ADMIN)
            assert r.status_code == 200, r.text
            assert await sse.wait_ended(), "SSE session kept the scope of a removed KB"
            assert _live_sessions() == 0

        _run(client, scenario)


class TestHandshakeRace:
    def test_a_kb_change_during_the_handshake_ends_the_new_stream(self, env, monkeypatch):
        """The readable set was resolved before the change landed: the
        session must not run with it."""
        from pyrite.server import mcp_routes

        client, w = env
        alice = _cookie(w.tokens["alice"])
        real = mcp_routes._authenticate

        async def racing(request, config, db):
            ctx = await real(request, config, db)
            r = await w.http.put(
                f"/api/kbs/{OPEN}/default-role", json={"role": "none"}, headers=ADMIN
            )
            assert r.status_code == 200, r.text
            return ctx

        monkeypatch.setattr(mcp_routes, "_authenticate", racing)

        async def scenario(tg):
            sse = _SSE(w.app, alice)
            tg.start_soon(sse.run)
            assert await sse.wait_ended(), "a session resolved before a KB change stayed open"
            assert _live_sessions() == 0

        _run(client, scenario)

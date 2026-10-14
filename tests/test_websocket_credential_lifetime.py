"""A live-update socket lives no longer than the credential that opened it (#411, ADR-0036).

Until #411 a socket's readable set was fixed at the handshake for the life of
the connection (#218/#323): after logout, session expiry, a role change or a
revoked KB grant, a socket kept open went on receiving events scoped to the
old credential. The property pinned here: when a session ends or a user's
role or KB access changes, the server closes that user's open sockets, and a
socket never delivers an event its current credential could not read.

Every test drives a real WebSocket client (``TestClient.websocket_connect``)
against ``create_app`` with auth enabled; REST calls and sockets share the
TestClient's loop, as they do under uvicorn.

**No receive timeouts.** A TestClient socket's ``receive`` blocks forever,
so "nothing further arrived" is proved with a marker (``kb_synced``, which
reaches every accepted socket) or a public clip: an unaffected socket's next
message is the marker, a revoked socket's next message is its close frame.
"""

import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server import websocket as ws_module
from pyrite.server.api import create_app
from pyrite.server.websocket import manager
from pyrite.services.auth_service import AuthService
from pyrite.services.clipper import ClipperService, ClipResult
from pyrite.storage.database import PyriteDB
from tests.ws_helpers import receive_json
from tests.auth_seed import seed_and_sign_in

PUBLIC, PRIVATE = "public-kb", "private-kb"
MARKER = {"type": "kb_synced", "entry_id": "", "kb_name": ""}
OPERATOR_KEY = "operator-secret-key"
# Policy Violation: the credential no longer admits this socket -- the code a
# refused handshake uses too. The web client reconnects on any close (#336).
CREDENTIAL_CLOSE_CODE = 1008


def _config(tmp: Path, *, auth=True, api_key="", anonymous_tier=None, max_sessions=5):
    (tmp / PUBLIC).mkdir()
    (tmp / PRIVATE).mkdir()
    return PyriteConfig(
        knowledge_bases=[
            KBConfig(name=PUBLIC, path=tmp / PUBLIC, kb_type="generic", default_role="read"),
            KBConfig(name=PRIVATE, path=tmp / PRIVATE, kb_type="generic", default_role="none"),
        ],
        settings=Settings(
            index_path=tmp / "index.db",
            api_key=api_key,
            auth=AuthConfig(
                enabled=auth,
                allow_registration=True,
                anonymous_tier=anonymous_tier,
                max_sessions_per_user=max_sessions,
            ),
        ),
    )


def _register(app, username) -> str:
    r = TestClient(app).post(
        "/auth/register", json={"username": username, "password": "password123"}
    )
    assert r.status_code == 200, r.text
    return r.cookies["pyrite_session"]


def _login(app, username) -> str:
    r = TestClient(app).post("/auth/login", json={"username": username, "password": "password123"})
    assert r.status_code == 200, r.text
    return r.cookies["pyrite_session"]


def _cookie(token):
    return {"cookie": f"pyrite_session={token}"}


@pytest.fixture
def stub_clip(monkeypatch):
    async def fake_clip(self, url, title=None):
        return ClipResult(title=title or "Clipped", body="clipped body", source_url=url)

    monkeypatch.setattr(ClipperService, "clip_url", fake_clip)


def _with_auth(config, fn):
    db = PyriteDB(config.settings.index_path)
    try:
        return fn(AuthService(db, config.settings.auth))
    finally:
        db.close()


@pytest.fixture
def env(stub_clip):
    """admin; ``alice`` (write, read grant on the private KB); ``bob`` (read,
    same grant) -- bob is the unaffected user throughout. An operator key is
    configured too."""
    with tempfile.TemporaryDirectory() as d:
        config = _config(Path(d), api_key=OPERATOR_KEY)
        app = create_app(config=config)
        # admin-user is seeded via the operator path;
        # alice and bob then self-register for real and get explicit grants
        # below, so global_access is not needed for what this test checks.
        admin_client = TestClient(app)
        seed_and_sign_in(admin_client, "admin-user", "password123", role="admin")
        tokens = {"admin-user": admin_client.cookies["pyrite_session"]}
        tokens.update({name: _register(app, name) for name in ("alice", "bob")})

        def setup(auth):
            users = {u["username"]: u["id"] for u in auth.list_users()}
            auth.set_role(users["alice"], "write")
            for name in ("alice", "bob"):
                auth.grant_kb_permission(users[name], PRIVATE, "read", users["admin-user"])
            return users

        users = _with_auth(config, setup)
        yield {"app": app, "config": config, "tokens": tokens, "users": users}


def _clip(client, kb, token):
    r = client.post(
        "/api/clip",
        json={"url": "https://example.com/x", "kb": kb, "title": "A clip"},
        headers=_cookie(token),
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _marker(client):
    client.portal.call(manager.broadcast, dict(MARKER))


def _assert_closed(ws, client):
    """The socket's next message is the server's close, not the marker.

    The marker goes out after the trigger, on the same loop: a socket still
    registered would receive it (and a receive with nothing coming would
    block forever, so the marker is always sent first).
    """
    _marker(client)
    with pytest.raises(WebSocketDisconnect) as exc:
        receive_json(ws)
    assert exc.value.code == CREDENTIAL_CLOSE_CODE


def _assert_open(ws, client):
    _marker(client)
    assert receive_json(ws) == MARKER


class TestLogout:
    def test_logout_closes_the_session_socket_and_no_event_follows(self, env):
        app, tokens = env["app"], env["tokens"]
        with TestClient(app) as c:
            with (
                c.websocket_connect("/ws", headers=_cookie(tokens["alice"])) as alice,
                c.websocket_connect("/ws", headers=_cookie(tokens["bob"])) as bob,
            ):
                r = c.post("/auth/logout", headers=_cookie(tokens["alice"]))
                assert r.status_code == 200
                # An event in a KB alice could read, raised after the logout:
                # bob gets it, alice's socket gets only its close.
                entry_id = _clip(c, PRIVATE, tokens["admin-user"])
                assert receive_json(bob)["entry_id"] == entry_id
                _assert_closed(alice, c)
                _assert_open(bob, c)

    def test_logout_closes_only_that_session_not_the_users_other_one(self, env):
        app, tokens = env["app"], env["tokens"]
        second = _login(app, "alice")
        with TestClient(app) as c:
            with (
                c.websocket_connect("/ws", headers=_cookie(tokens["alice"])) as first_tab,
                c.websocket_connect("/ws", headers=_cookie(second)) as other_device,
            ):
                c.post("/auth/logout", headers=_cookie(tokens["alice"]))
                _assert_closed(first_tab, c)
                _assert_open(other_device, c)

    def test_logout_all_closes_every_socket_of_the_user(self, env):
        app, tokens, users = env["app"], env["tokens"], env["users"]
        second = _login(app, "alice")
        with TestClient(app) as c:
            with (
                c.websocket_connect("/ws", headers=_cookie(tokens["alice"])) as one,
                c.websocket_connect("/ws", headers=_cookie(second)) as two,
                c.websocket_connect("/ws", headers=_cookie(tokens["bob"])) as bob,
            ):
                _with_auth(env["config"], lambda a: a.logout_all(users["alice"]))
                _assert_closed(one, c)
                _assert_closed(two, c)
                _assert_open(bob, c)

    def test_a_session_evicted_by_the_session_cap_closes_its_socket(self, stub_clip):
        with tempfile.TemporaryDirectory() as d:
            config = _config(Path(d), max_sessions=1)
            app = create_app(config=config)
            seed_and_sign_in(TestClient(app), "admin-user", "password123", role="admin")
            old = _register(app, "alice")
            with TestClient(app) as c:
                with c.websocket_connect("/ws", headers=_cookie(old)) as ws:
                    new = _login(app, "alice")  # evicts `old`
                    _assert_closed(ws, c)
                    with c.websocket_connect("/ws", headers=_cookie(new)) as fresh:
                        _assert_open(fresh, c)


class TestExpiry:
    def _expire(self, config, token):
        import hashlib

        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        db = PyriteDB(config.settings.index_path)
        try:
            db.execute_write_sql(
                "UPDATE session SET expires_at = :past WHERE token_hash = :h",
                {"past": past, "h": token_hash},
            )
        finally:
            db.close()

    def test_an_expired_session_socket_receives_no_event(self, env):
        """The per-event check: no sweep has run, and still nothing is sent."""
        app, tokens = env["app"], env["tokens"]
        with TestClient(app) as c:
            with (
                c.websocket_connect("/ws", headers=_cookie(tokens["alice"])) as alice,
                c.websocket_connect("/ws", headers=_cookie(tokens["bob"])) as bob,
            ):
                # The socket's expiry is recorded at connect; move it into the
                # past on the socket itself (the DB row is not re-read).
                for state in manager._connections.values():
                    if state.user_id == env["users"]["alice"]:
                        state.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                entry_id = _clip(c, PRIVATE, tokens["admin-user"])
                assert receive_json(bob)["entry_id"] == entry_id
                _assert_closed(alice, c)

    def test_the_expiry_sweep_closes_a_socket_whose_session_expired(self, env):
        app, tokens = env["app"], env["tokens"]
        with TestClient(app) as c:
            with (
                c.websocket_connect("/ws", headers=_cookie(tokens["alice"])) as alice,
                c.websocket_connect("/ws", headers=_cookie(tokens["bob"])) as bob,
            ):
                for state in manager._connections.values():
                    if state.user_id == env["users"]["alice"]:
                        state.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                closed = c.portal.call(_close_expired)
                assert closed == 1
                _assert_closed(alice, c)
                _assert_open(bob, c)

    def test_the_socket_takes_its_expiry_from_the_session(self, env):
        """The expiry the sweep compares is the session row's, read at connect."""
        app, tokens = env["app"], env["tokens"]
        with TestClient(app) as c:
            with c.websocket_connect("/ws", headers=_cookie(tokens["alice"])):
                (state,) = [
                    s for s in manager._connections.values() if s.user_id == env["users"]["alice"]
                ]
                ttl = timedelta(hours=env["config"].settings.auth.session_ttl_hours)
                assert state.expires_at is not None
                assert abs(state.expires_at - (datetime.now(UTC) + ttl)) < timedelta(minutes=5)

    def test_the_auth_services_expired_session_cleanup_closes_the_socket(self, env):
        app, tokens = env["app"], env["tokens"]
        with TestClient(app) as c:
            with (
                c.websocket_connect("/ws", headers=_cookie(tokens["alice"])) as alice,
                c.websocket_connect("/ws", headers=_cookie(tokens["bob"])) as bob,
            ):
                self._expire(env["config"], tokens["alice"])
                assert _with_auth(env["config"], lambda a: a._cleanup_expired()) == 1
                _assert_closed(alice, c)
                _assert_open(bob, c)

    def test_verify_session_finding_it_expired_closes_the_socket(self, env):
        app, tokens = env["app"], env["tokens"]
        with TestClient(app) as c:
            with c.websocket_connect("/ws", headers=_cookie(tokens["alice"])) as alice:
                self._expire(env["config"], tokens["alice"])
                assert (
                    _with_auth(env["config"], lambda a: a.verify_session(tokens["alice"])) is None
                )
                _assert_closed(alice, c)


async def _close_expired():
    return manager.close_expired()


class TestRoleAndGrantChanges:
    def test_role_downgrade_closes_the_users_socket(self, env):
        app, tokens, users = env["app"], env["tokens"], env["users"]
        with TestClient(app) as c:
            with (
                c.websocket_connect("/ws", headers=_cookie(tokens["alice"])) as alice,
                c.websocket_connect("/ws", headers=_cookie(tokens["bob"])) as bob,
            ):
                r = c.put(
                    f"/auth/users/{users['alice']}/role",
                    json={"role": "read"},
                    headers=_cookie(tokens["admin-user"]),
                )
                assert r.status_code == 200, r.text
                entry_id = _clip(c, PUBLIC, tokens["admin-user"])
                assert receive_json(bob)["entry_id"] == entry_id
                _assert_closed(alice, c)

    @pytest.mark.control(
        reason="nothing changed, so nothing closes: holds before and after the fix"
    )
    def test_a_refused_role_change_closes_nothing(self, env):
        """The last admin cannot be demoted; no change, no close."""
        app, tokens, users = env["app"], env["tokens"], env["users"]
        with TestClient(app) as c:
            with c.websocket_connect("/ws", headers=_cookie(tokens["admin-user"])) as admin:
                r = c.put(
                    f"/auth/users/{users['admin-user']}/role",
                    json={"role": "read"},
                    headers=_cookie(tokens["admin-user"]),
                )
                assert r.status_code == 409
                _assert_open(admin, c)

    def test_revoking_a_kb_grant_closes_the_grantees_socket(self, env):
        """A sync route: the change is handed from a worker thread to the loop."""
        app, tokens, users = env["app"], env["tokens"], env["users"]
        with TestClient(app) as c:
            with (
                c.websocket_connect("/ws", headers=_cookie(tokens["alice"])) as alice,
                c.websocket_connect("/ws", headers=_cookie(tokens["bob"])) as bob,
            ):
                r = c.post(
                    f"/api/kbs/{PRIVATE}/permissions",
                    json={"user_id": users["alice"], "revoke": True},
                    headers=_cookie(tokens["admin-user"]),
                )
                assert r.status_code == 200, r.text
                entry_id = _clip(c, PRIVATE, tokens["admin-user"])
                assert receive_json(bob)["entry_id"] == entry_id
                _assert_closed(alice, c)

    def test_granting_a_kb_closes_the_socket_so_it_reopens_with_the_new_scope(self, env):
        app, tokens, users = env["app"], env["tokens"], env["users"]
        with TestClient(app) as c:
            with c.websocket_connect("/ws", headers=_cookie(tokens["alice"])) as alice:
                r = c.post(
                    f"/api/kbs/{PUBLIC}/permissions",
                    json={"user_id": users["alice"], "role": "write"},
                    headers=_cookie(tokens["admin-user"]),
                )
                assert r.status_code == 200, r.text
                _assert_closed(alice, c)


class TestUnaffectedSockets:
    @pytest.mark.control(
        reason="an unscoped socket was never closed; pins that the fix leaves it open"
    )
    def test_operator_key_socket_survives_every_trigger(self, env):
        app, tokens, users = env["app"], env["tokens"], env["users"]
        with TestClient(app) as c:
            with c.websocket_connect(f"/ws?api_key={OPERATOR_KEY}") as op:
                c.post("/auth/logout", headers=_cookie(tokens["alice"]))
                c.put(
                    f"/auth/users/{users['bob']}/role",
                    json={"role": "write"},
                    headers=_cookie(tokens["admin-user"]),
                )
                _with_auth(env["config"], lambda a: a.logout_all(users["admin-user"]))
                _assert_open(op, c)

    @pytest.mark.control(
        reason="an anonymous socket was never closed; pins that the fix leaves it open"
    )
    def test_anonymous_socket_survives_a_users_logout(self, stub_clip):
        with tempfile.TemporaryDirectory() as d:
            app = create_app(config=_config(Path(d), anonymous_tier="read"))
            admin_client = TestClient(app)
            seed_and_sign_in(admin_client, "admin-user", "password123", role="admin")
            token = admin_client.cookies["pyrite_session"]
            with TestClient(app) as c, c.websocket_connect("/ws") as anon:
                c.post("/auth/logout", headers=_cookie(token))
                _assert_open(anon, c)

    def test_auth_disabled_socket_survives_a_credential_change(self, stub_clip):
        from pyrite.services import credential_events

        with tempfile.TemporaryDirectory() as d:
            app = create_app(config=_config(Path(d), auth=False))
            with TestClient(app) as c, c.websocket_connect("/ws") as ws:
                credential_events.publish(credential_events.CredentialChange(user_id=1))
                credential_events.publish(credential_events.CredentialChange(session_hash="x"))
                _assert_open(ws, c)


class TestHandshakeRace:
    def test_a_revocation_during_the_handshake_refuses_the_socket(self, env, monkeypatch):
        """A credential revoked after the handshake resolved it but before the
        socket is registered must not leave a live socket behind."""
        app, tokens = env["app"], env["tokens"]
        real = ws_module.resolve_socket_scope

        def resolve_then_logout(conn, config, db):
            scope = real(conn, config, db)
            # The logout lands on another thread after the lookup succeeded.
            AuthService(db, config.settings.auth).logout(tokens["alice"])
            return scope

        monkeypatch.setattr(ws_module, "resolve_socket_scope", resolve_then_logout)
        with TestClient(app) as c:
            before = manager.connection_count
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect("/ws", headers=_cookie(tokens["alice"])) as ws:
                    # Refused at the handshake, or closed right after it:
                    # either way the marker never reaches it.
                    _marker(c)
                    receive_json(ws)
            assert manager.connection_count == before


class TestListenerRegistry:
    def test_a_failing_listener_does_not_break_the_service_or_other_listeners(self):
        from pyrite.services import credential_events

        seen = []

        def boom(change):
            raise RuntimeError("listener failed")

        credential_events.subscribe(boom)
        credential_events.subscribe(seen.append)
        try:
            credential_events.publish(credential_events.CredentialChange(user_id=7))
        finally:
            credential_events.unsubscribe(boom)
            credential_events.unsubscribe(seen.append)
        assert seen == [credential_events.CredentialChange(user_id=7)]

    def test_subscribe_is_idempotent(self):
        from pyrite.services import credential_events

        seen = []
        credential_events.subscribe(seen.append)
        credential_events.subscribe(seen.append)
        try:
            credential_events.publish(credential_events.CredentialChange(user_id=1))
        finally:
            credential_events.unsubscribe(seen.append)
        assert len(seen) == 1


class TestSweepLifetime:
    def test_the_expiry_sweep_task_stops_at_shutdown(self, env):
        """Stopped by the app's own shutdown hook, not by the loop's teardown:
        a later shutdown hook already finds it done."""
        app = env["app"]
        seen = {}

        async def later_hook():
            seen["done"] = app.state.pyrite_ws_expiry_sweep.done()

        app.router.on_shutdown.append(later_hook)
        with TestClient(app):
            assert not app.state.pyrite_ws_expiry_sweep.done()
        assert seen == {"done": True}

    def test_the_sweep_closes_an_expired_socket_on_its_own(self, monkeypatch):
        """The periodic sweep, not a direct call: with a tiny interval it
        closes an expired socket nobody has broadcast to."""
        from pyrite.server.websocket import ConnectionManager, SocketScope, expiry_sweep

        m = ConnectionManager()
        monkeypatch.setattr(ws_module, "manager", m)

        async def run():
            sock = _FakeSocket()
            past = datetime.now(UTC) - timedelta(seconds=1)
            await m.connect(sock, SocketScope(readable=set(), user_id=1, expires_at=past))
            sweep = asyncio.get_running_loop().create_task(expiry_sweep(0.001))
            try:
                for _ in range(500):  # bounded: at most ~5 s
                    if sock.closed_with is not None:
                        break
                    await asyncio.sleep(0.01)
            finally:
                sweep.cancel()
            return sock

        sock = asyncio.run(run())
        assert sock.closed_with == CREDENTIAL_CLOSE_CODE
        assert m.connection_count == 0


def test_adr_0036_is_recorded():
    import yaml

    root = Path(__file__).resolve().parent.parent / "kb" / "adrs"
    (path,) = list(root.glob("0036-*.md"))
    front = yaml.safe_load(path.read_text().split("---")[1])
    assert front["adr_number"] == 36
    assert front["status"] == "accepted"  # maintainer, 2026-09-25
    assert front["type"] == "adr"


class TestHandshakeRaceSpares:
    def test_an_operator_socket_is_registered_despite_a_change_during_its_handshake(
        self, env, monkeypatch
    ):
        """Only a revocable credential is refused on a mid-handshake change."""
        from pyrite.services import credential_events

        app = env["app"]
        real = ws_module.resolve_socket_scope

        def resolve_then_change(conn, config, db):
            scope = real(conn, config, db)
            credential_events.publish(credential_events.CredentialChange(user_id=999_999))
            return scope

        monkeypatch.setattr(ws_module, "resolve_socket_scope", resolve_then_change)
        with TestClient(app) as c, c.websocket_connect(f"/ws?api_key={OPERATOR_KEY}") as op:
            _assert_open(op, c)


class _FakeSocket:
    """Records what it was sent; its first send can run a hook mid-fan-out."""

    def __init__(self, on_send=None):
        self.sent: list[str] = []
        self.closed_with: int | None = None
        self._on_send = on_send

    async def accept(self):
        pass

    async def send_text(self, text):
        if self._on_send is not None:
            hook, self._on_send = self._on_send, None
            hook()
            await asyncio.sleep(0)
        self.sent.append(text)

    async def close(self, code=1000, reason=""):
        self.closed_with = code


class TestFanOut:
    def test_a_socket_revoked_while_a_fan_out_is_under_way_is_not_sent_to(self):
        """An event whose fan-out is still awaiting earlier sends when the
        change lands must not reach the revoked socket afterwards."""
        from pyrite.server.websocket import ConnectionManager, SocketScope
        from pyrite.services.credential_events import CredentialChange

        async def run():
            m = ConnectionManager()
            victim = _FakeSocket()
            first = _FakeSocket(on_send=lambda: m.revoke(CredentialChange(user_id=2)))
            await m.connect(first, SocketScope(readable={PUBLIC}, user_id=1))
            await m.connect(victim, SocketScope(readable={PUBLIC}, user_id=2))
            await m.broadcast({"type": "entry_created", "entry_id": "e", "kb_name": PUBLIC})
            await asyncio.sleep(0)
            return first, victim

        first, victim = asyncio.run(run())
        assert len(first.sent) == 1
        assert victim.sent == []
        assert victim.closed_with == CREDENTIAL_CLOSE_CODE


class TestFixRound:
    """The #433 cold read's follow-ups."""

    def test_creating_an_ephemeral_kb_closes_the_creators_socket(self, env):
        """Its admin grant is a grant write like any other."""
        app, tokens = env["app"], env["tokens"]
        with TestClient(app) as c:
            with (
                c.websocket_connect("/ws", headers=_cookie(tokens["alice"])) as alice,
                c.websocket_connect("/ws", headers=_cookie(tokens["bob"])) as bob,
            ):
                r = c.post("/api/kbs/ephemeral", json={}, headers=_cookie(tokens["alice"]))
                assert r.status_code == 200, r.text
                _assert_closed(alice, c)
                _assert_open(bob, c)

    def test_session_cap_eviction_is_one_delete_and_announces_each_session(self, env):
        """Several excess sessions go in one DELETE statement (atomic), and
        every evicted session's socket is closed; the newest survives."""
        from sqlalchemy import event

        app, config = env["app"], env["config"]
        extra = [_login(app, "alice") for _ in range(3)]  # alice: 4 sessions
        config.settings.auth.max_sessions_per_user = 2
        oldest_three = [env["tokens"]["alice"], *extra[:2]]
        with TestClient(app) as c:
            sockets = [
                c.websocket_connect("/ws", headers=_cookie(t)).__enter__()
                for t in [*oldest_three, extra[2]]
            ]
            try:
                db = PyriteDB(config.settings.index_path)
                deletes = []

                def count(conn, cursor, statement, *args):
                    if statement.lstrip().upper().startswith("DELETE FROM SESSION"):
                        deletes.append(statement)

                event.listen(db.engine, "before_cursor_execute", count)
                try:
                    AuthService(db, config.settings.auth).login("alice", "password123")
                    remaining = db.execute_sql(
                        "SELECT COUNT(*) AS n FROM session WHERE user_id = :u",
                        {"u": env["users"]["alice"]},
                    )[0]["n"]
                finally:
                    event.remove(db.engine, "before_cursor_execute", count)
                    db.close()
                assert len(deletes) == 1
                assert remaining == 2
                for ws in sockets[:3]:
                    _assert_closed(ws, c)
                _assert_open(sockets[3], c)
            finally:
                for ws in sockets:
                    ws.__exit__(None, None, None)

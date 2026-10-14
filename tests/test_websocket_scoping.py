"""`/ws` authenticates its handshake and only sends a socket the KBs it may read (#218).

`/ws` had no credential check and `ConnectionManager.broadcast` fanned every
event out to every socket, so an unauthenticated caller learned the *names*
of private KBs and the *ids* of entries written in them, live.

**Which path these tests drive.** `POST /api/clip`, an ``async def``
handler: `broadcast_event` finds the running loop and schedules the fan-out
there. The sync routes (`POST /api/entries` and friends) and IndexWorker
threads reach the same `ConnectionManager.broadcast` through the loop
captured at startup (#326, #322); `tests/test_websocket_delivery.py` covers
that path and its scoping. Either way the tests here run on a single
`TestClient` in context, with only the network fetch stubbed, so that REST
calls and sockets share one event loop, as they do under uvicorn.

**No receive timeouts.** A TestClient socket's `receive` blocks forever, so
"did not arrive" is proved with a marker: after the event under test, an
event with no `kb_name` is broadcast on the same loop. It goes to every
accepted socket, so each socket's *next* message says whether the private
event reached it first.
"""

import logging
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app
from pyrite.server.websocket import manager, origin_allowed
from pyrite.services.auth_service import AuthService
from pyrite.services.clipper import ClipperService, ClipResult
from pyrite.storage.database import PyriteDB
from tests.ws_helpers import receive_json
from tests.auth_seed import seed_and_sign_in

PUBLIC, PRIVATE = "public-kb", "private-kb"
MARKER = {"type": "kb_synced", "entry_id": "", "kb_name": ""}
OPERATOR_KEY = "operator-secret-key"


def _config(tmp: Path, *, auth: bool, anonymous_tier=None, api_key="") -> PyriteConfig:
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
            auth=AuthConfig(enabled=auth, allow_registration=True, anonymous_tier=anonymous_tier),
        ),
    )


def _cookie(token):
    return {"cookie": f"pyrite_session={token}"}


@pytest.fixture
def stub_clip(monkeypatch):
    """Stub only the network fetch; the clip route itself runs for real."""

    async def fake_clip(self, url, title=None):
        return ClipResult(title=title or "Clipped", body="clipped body", source_url=url)

    monkeypatch.setattr(ClipperService, "clip_url", fake_clip)


@pytest.fixture
def secured(stub_clip):
    """Auth enabled, no anonymous tier. admin, a peer with a read grant on the
    private KB, and a peer without one."""
    with tempfile.TemporaryDirectory() as d:
        config = _config(Path(d), auth=True)
        app = create_app(config=config)
        # Seeded the operator's way. granted and peer hold the global read
        # role (covering every KB without a default_role), so peer is the
        # persona denied the default_role: none KB; granted gets a grant.
        tokens = {}
        admin_client = TestClient(app)
        admin_user = seed_and_sign_in(admin_client, "admin-user", "password123", role="admin")
        tokens["admin-user"] = admin_client.cookies["pyrite_session"]
        for name in ("granted", "peer"):
            c = TestClient(app)
            seed_and_sign_in(c, name, "password123", role="read")
            tokens[name] = c.cookies["pyrite_session"]
        db = PyriteDB(config.settings.index_path)
        try:
            auth = AuthService(db, config.settings.auth)
            users = {u["username"]: u["id"] for u in auth.list_users()}
            auth.grant_kb_permission(users["granted"], PRIVATE, "read", admin_user["id"])
        finally:
            db.close()
        yield {"app": app, "tokens": tokens}


def _broadcast_marker(client):
    client.portal.call(manager.broadcast, dict(MARKER))


class TestPrivateKBEventReachesOnlyGrantedSockets:
    def test_clip_into_private_kb_reaches_granted_socket_only(self, secured):
        app, tokens = secured["app"], secured["tokens"]
        with TestClient(app) as c:
            with (
                c.websocket_connect("/ws", headers=_cookie(tokens["granted"])) as granted,
                c.websocket_connect("/ws", headers=_cookie(tokens["peer"])) as peer,
            ):
                r = c.post(
                    "/api/clip",
                    json={"url": "https://example.com/x", "kb": PRIVATE, "title": "Secret clip"},
                    headers=_cookie(tokens["admin-user"]),
                )
                assert r.status_code == 200, r.text
                entry_id = r.json()["id"]

                first = receive_json(granted)
                assert first == {"type": "entry_created", "entry_id": entry_id, "kb_name": PRIVATE}

                _broadcast_marker(c)
                assert receive_json(granted) == MARKER
                # The peer's first message is the marker: the private event
                # never reached it.
                assert receive_json(peer) == MARKER

    def test_public_kb_event_reaches_both(self, secured):
        app, tokens = secured["app"], secured["tokens"]
        with TestClient(app) as c:
            with (
                c.websocket_connect("/ws", headers=_cookie(tokens["granted"])) as granted,
                c.websocket_connect("/ws", headers=_cookie(tokens["peer"])) as peer,
            ):
                r = c.post(
                    "/api/clip",
                    json={"url": "https://example.com/y", "kb": PUBLIC, "title": "Open clip"},
                    headers=_cookie(tokens["admin-user"]),
                )
                assert r.status_code == 200, r.text
                for ws in (granted, peer):
                    assert receive_json(ws)["kb_name"] == PUBLIC

    def test_kb_synced_reaches_a_scoped_socket(self, secured):
        app, tokens = secured["app"], secured["tokens"]
        with TestClient(app) as c:
            with c.websocket_connect("/ws", headers=_cookie(tokens["peer"])) as peer:
                _broadcast_marker(c)
                assert receive_json(peer) == MARKER


class TestHandshakeAuthentication:
    def test_no_credential_is_refused_and_never_registered(self, secured):
        before = manager.connection_count
        with TestClient(secured["app"]) as c:
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect("/ws"):
                    pass
        assert manager.connection_count == before

    def test_bad_session_cookie_is_refused(self, secured):
        with TestClient(secured["app"]) as c:
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect("/ws", headers=_cookie("not-a-session")):
                    pass

    @pytest.mark.parametrize(
        ("path", "headers"),
        [
            ("/ws?api_key=anything", {}),
            ("/ws", {"X-API-Key": "anything"}),
            ("/ws", {"Authorization": "Bearer anything"}),
        ],
    )
    def test_arbitrary_key_is_refused_when_no_keys_are_configured(self, secured, path, headers):
        # Auth enabled, no API keys configured: no key is valid (#331), so an
        # arbitrary key must not admit a socket as an operator.
        with TestClient(secured["app"]) as c:
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect(path, headers=headers):
                    pass

    def test_foreign_origin_with_valid_cookie_is_refused(self, secured):
        headers = {**_cookie(secured["tokens"]["peer"]), "origin": "https://evil.example"}
        with TestClient(secured["app"]) as c:
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect("/ws", headers=headers):
                    pass

    def test_same_host_origin_with_valid_cookie_is_admitted(self, secured):
        headers = {**_cookie(secured["tokens"]["peer"]), "origin": "http://testserver"}
        with TestClient(secured["app"]) as c:
            with c.websocket_connect("/ws", headers=headers) as ws:
                _broadcast_marker(c)
                assert receive_json(ws) == MARKER

    def test_configured_cors_origin_is_admitted(self, secured):
        # localhost:5173 is in the default cors_origins (the Vite dev server).
        headers = {**_cookie(secured["tokens"]["peer"]), "origin": "http://localhost:5173"}
        with TestClient(secured["app"]) as c:
            with c.websocket_connect("/ws", headers=headers) as ws:
                _broadcast_marker(c)
                assert receive_json(ws) == MARKER


def _clip(c, kb, headers=None):
    r = c.post(
        "/api/clip",
        json={"url": "https://example.com/z", "kb": kb, "title": f"clip {kb}"},
        headers=headers or {},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


class TestOtherIdentities:
    def test_auth_disabled_bare_socket_receives_private_event(self, stub_clip):
        # The default local install: no auth, no keys. Unchanged -- it still
        # receives everything, private KB included.
        with tempfile.TemporaryDirectory() as d:
            app = create_app(config=_config(Path(d), auth=False))
            with TestClient(app) as c, c.websocket_connect("/ws") as ws:
                entry_id = _clip(c, PRIVATE)
                assert receive_json(ws) == {
                    "type": "entry_created",
                    "entry_id": entry_id,
                    "kb_name": PRIVATE,
                }

    def test_operator_key_as_query_param_is_unscoped(self, stub_clip):
        with tempfile.TemporaryDirectory() as d:
            app = create_app(config=_config(Path(d), auth=False, api_key=OPERATOR_KEY))
            key = {"X-API-Key": OPERATOR_KEY}
            with TestClient(app) as c:
                with pytest.raises(WebSocketDisconnect):
                    with c.websocket_connect("/ws"):
                        pass
                with pytest.raises(WebSocketDisconnect):
                    with c.websocket_connect("/ws?api_key=wrong"):
                        pass
                with c.websocket_connect(f"/ws?api_key={OPERATOR_KEY}") as ws:
                    entry_id = _clip(c, PRIVATE, headers=key)
                    assert receive_json(ws)["entry_id"] == entry_id

    def test_anonymous_tier_admits_visitor_scoped_to_public(self, stub_clip):
        with tempfile.TemporaryDirectory() as d:
            app = create_app(config=_config(Path(d), auth=True, anonymous_tier="read"))
            admin_client = TestClient(app)
            seed_and_sign_in(admin_client, "admin-user", "password123", role="admin")
            admin_token = admin_client.cookies["pyrite_session"]
            with TestClient(app) as c, c.websocket_connect("/ws") as anon:
                _clip(c, PRIVATE, headers=_cookie(admin_token))
                public_id = _clip(c, PUBLIC, headers=_cookie(admin_token))
                # The first thing the anonymous socket sees is the public
                # clip; the private one, created first, never arrived.
                assert receive_json(anon) == {
                    "type": "entry_created",
                    "entry_id": public_id,
                    "kb_name": PUBLIC,
                }


class TestEventsWithoutAKb:
    """An event that names no KB fails closed: only GLOBAL_EVENTS reach a
    scoped socket (cold read of #323; index_progress carries admin job ids)."""

    def test_non_global_kbless_event_skips_a_scoped_socket(self, secured):
        app, tokens = secured["app"], secured["tokens"]
        with TestClient(app) as c:
            with c.websocket_connect("/ws", headers=_cookie(tokens["peer"])) as peer:
                c.portal.call(manager.broadcast, {"type": "index_progress", "job_id": "j1"})
                _broadcast_marker(c)
                assert receive_json(peer) == MARKER  # index_progress was skipped

    def test_non_global_kbless_event_reaches_an_unscoped_socket(self, secured):
        app, tokens = secured["app"], secured["tokens"]
        with TestClient(app) as c:
            with c.websocket_connect("/ws", headers=_cookie(tokens["admin-user"])) as admin:
                c.portal.call(manager.broadcast, {"type": "index_progress", "job_id": "j1"})
                assert receive_json(admin)["type"] == "index_progress"


class _Conn:
    def __init__(self, **headers):
        self.headers = headers


class TestOriginRule:
    """origin_allowed's edges, pinned directly (cold read of #323)."""

    def _cfg(self, tmp_path, origins):
        cfg = _config(tmp_path, auth=True)
        cfg.settings.cors_origins = origins
        return cfg

    def test_star_is_not_a_wildcard(self, tmp_path):
        cfg = self._cfg(tmp_path, ["*"])
        assert not origin_allowed(_Conn(origin="https://evil.example", host="pyrite.test"), cfg)

    def test_null_origin_is_refused(self, tmp_path):
        cfg = self._cfg(tmp_path, [])
        assert not origin_allowed(_Conn(origin="null", host="pyrite.test"), cfg)

    def test_host_comparison_is_case_insensitive(self, tmp_path):
        cfg = self._cfg(tmp_path, [])
        assert origin_allowed(_Conn(origin="https://PYRITE.test", host="pyrite.test"), cfg)

    def test_absent_origin_leaves_it_to_the_credential(self, tmp_path):
        cfg = self._cfg(tmp_path, [])
        assert origin_allowed(_Conn(host="pyrite.test"), cfg)

    def test_a_refused_origin_is_logged(self, secured, caplog):
        headers = {**_cookie(secured["tokens"]["peer"]), "origin": "https://evil.example"}
        with caplog.at_level(logging.WARNING, logger="pyrite.server.api"):
            with TestClient(secured["app"]) as c:
                with pytest.raises(WebSocketDisconnect):
                    with c.websocket_connect("/ws", headers=headers):
                        pass
        assert any("evil.example" in r.getMessage() for r in caplog.records)

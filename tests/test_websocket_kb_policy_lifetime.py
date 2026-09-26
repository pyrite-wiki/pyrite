"""A scoped `/ws` socket lives no longer than the KB policy that scoped it (P-W3).

A socket's readable set is resolved once, at the handshake (#218), and the
socket is closed when the credential that opened it changes (#411,
ADR-0036). A KB's own policy changing -- its `default_role`, or the KB being
removed -- published nothing, and an anonymous socket had no credential to
revoke at all: it went on receiving the events of a KB an admin had closed.

The property: a `default_role` change or a KB removal closes every scoped
socket whose readable set included that KB, anonymous sockets included. The
client reconnects and is scoped afresh.

**No receive timeouts.** As in `test_websocket_scoping.py`: after the
trigger a `kb_synced` marker is broadcast on the same loop; a socket still
registered receives it, a closed socket's next message is its close frame.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app
from pyrite.server.websocket import manager
from pyrite.storage.database import PyriteDB
from tests.auth_seed import app_db, seed_user, sign_in

OPEN, CLOSED, YAML = "open-kb", "closed-kb", "yaml-kb"
MARKER = {"type": "kb_synced", "entry_id": "", "kb_name": ""}
KEY = "operator-key"
ADMIN = {"X-API-Key": KEY}
CLOSE_CODE = 1008


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYRITE_DATA_DIR", str(tmp_path))
    for name in (OPEN, CLOSED, YAML):
        (tmp_path / name).mkdir()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name=YAML, path=tmp_path / YAML, kb_type="generic", default_role="read")
        ],
        settings=Settings(
            index_path=tmp_path / "index.db",
            api_key=KEY,
            auth=AuthConfig(enabled=True, allow_registration=True, anonymous_tier="read"),
        ),
    )
    with PyriteDB(config.settings.index_path) as db:
        db.register_kb(YAML, "generic", str(tmp_path / YAML), source="config")
        db.register_kb(OPEN, "generic", str(tmp_path / OPEN), source="user", default_role="read")
        db.register_kb(
            CLOSED, "generic", str(tmp_path / CLOSED), source="user", default_role="none"
        )
    app = create_app(config=config)
    yield app
    state_db = getattr(app.state, "pyrite_db", None)
    if state_db is not None:
        state_db.close()


def _marker(client):
    client.portal.call(manager.broadcast, dict(MARKER))


def _assert_closed(ws, client):
    _marker(client)
    with pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_json()
    assert exc.value.code == CLOSE_CODE


def _assert_open(ws, client):
    _marker(client)
    assert ws.receive_json() == MARKER


def _reader_cookie(app, client) -> dict:
    client.get("/health")  # the app's PyriteDB exists after a request
    seed_user(app_db(app), "reader", role="read")
    return {"cookie": f"pyrite_session={sign_in(app, 'reader')}"}


class TestDefaultRoleChange:
    def test_closing_a_kb_closes_the_anonymous_socket(self, env):
        with TestClient(env) as c, c.websocket_connect("/ws") as anon:
            r = c.put(f"/api/kbs/{OPEN}/default-role", json={"role": "none"}, headers=ADMIN)
            assert r.status_code == 200, r.text
            _assert_closed(anon, c)

    def test_closing_a_kb_closes_a_session_socket_that_read_it(self, env):
        with TestClient(env) as c:
            cookie = _reader_cookie(env, c)
            with c.websocket_connect("/ws", headers=cookie) as reader:
                r = c.put(f"/api/kbs/{OPEN}/default-role", json={"role": "none"}, headers=ADMIN)
                assert r.status_code == 200, r.text
                _assert_closed(reader, c)

    @pytest.mark.control(
        reason="a socket that never read the KB is not affected; holds before and after"
    )
    def test_a_change_to_a_kb_the_socket_could_not_read_leaves_it_open(self, env):
        with TestClient(env) as c, c.websocket_connect("/ws") as anon:
            r = c.put(f"/api/kbs/{CLOSED}/default-role", json={"role": "none"}, headers=ADMIN)
            assert r.status_code == 200, r.text
            _assert_open(anon, c)

    @pytest.mark.control(reason="an unscoped operator socket was never closed by a KB change")
    def test_an_operator_socket_survives(self, env):
        with TestClient(env) as c, c.websocket_connect(f"/ws?api_key={KEY}") as op:
            r = c.put(f"/api/kbs/{OPEN}/default-role", json={"role": "none"}, headers=ADMIN)
            assert r.status_code == 200, r.text
            _assert_open(op, c)

    def test_a_default_role_write_that_changes_nothing_closes_nothing(self, env):
        """read -> read is no policy change: a socket that read the KB stays."""
        with TestClient(env) as c, c.websocket_connect("/ws") as anon:
            r = c.put(f"/api/kbs/{OPEN}/default-role", json={"role": "read"}, headers=ADMIN)
            assert r.status_code == 200, r.text
            _assert_open(anon, c)

    def test_the_reconnected_socket_no_longer_gets_the_kbs_events(self, env):
        with TestClient(env) as c:
            with c.websocket_connect("/ws") as anon:
                c.put(f"/api/kbs/{OPEN}/default-role", json={"role": "none"}, headers=ADMIN)
                _assert_closed(anon, c)
            with c.websocket_connect("/ws") as again:
                c.portal.call(
                    manager.broadcast, {"type": "entry_created", "entry_id": "x", "kb_name": OPEN}
                )
                _assert_open(again, c)


class TestKBRemoval:
    def test_removing_a_kb_closes_the_anonymous_socket(self, env):
        with TestClient(env) as c, c.websocket_connect("/ws") as anon:
            r = c.delete(f"/api/kbs/{OPEN}", headers=ADMIN)
            assert r.status_code == 200, r.text
            _assert_closed(anon, c)

    def test_removing_a_kb_closes_a_session_socket_that_read_it(self, env):
        with TestClient(env) as c:
            cookie = _reader_cookie(env, c)
            with c.websocket_connect("/ws", headers=cookie) as reader:
                r = c.delete(f"/api/kbs/{OPEN}", headers=ADMIN)
                assert r.status_code == 200, r.text
                _assert_closed(reader, c)


class TestHandshakeRace:
    def test_a_kb_change_during_the_handshake_refuses_the_anonymous_socket(self, env, monkeypatch):
        """The readable set was resolved before the change landed: the socket
        must not be registered with it."""
        from pyrite.server import websocket as ws_module
        from pyrite.services import credential_events

        real = ws_module.resolve_socket_scope

        def resolve_then_change(conn, config, db):
            scope = real(conn, config, db)
            credential_events.publish(credential_events.CredentialChange(kb_name=OPEN))
            return scope

        monkeypatch.setattr(ws_module, "resolve_socket_scope", resolve_then_change)
        with TestClient(env) as c:
            before = manager.connection_count
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect("/ws") as ws:
                    _marker(c)
                    ws.receive_json()
            assert manager.connection_count == before


class TestEveryKBRemovalAnnouncesIt:
    """Service surface: every path that removes a KB publishes the change the
    `/ws` listener acts on (the registry's own is driven end to end above).
    A KB name can be registered again later, by someone else, with another
    policy; a socket scoped with the old KB must not carry over to it."""

    @pytest.fixture
    def heard(self):
        from pyrite.services import credential_events

        changes = []
        credential_events.subscribe(changes.append)
        yield changes
        credential_events.unsubscribe(changes.append)

    def test_expiring_an_ephemeral_kb(self, tmp_path, heard):
        from pyrite.services.credential_events import CredentialChange
        from pyrite.services.ephemeral_service import EphemeralKBService

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        config = PyriteConfig(
            settings=Settings(index_path=tmp_path / "index.db", workspace_path=workspace)
        )
        with PyriteDB(config.settings.index_path) as db:
            svc = EphemeralKBService(config, db)
            svc.create_ephemeral_kb("eph-kb", ttl=60)
            assert svc.force_expire_kb("eph-kb") is True
        assert CredentialChange(kb_name="eph-kb") in heard

    def test_unsubscribing_a_repo(self, tmp_path, heard):
        from unittest.mock import patch

        from pyrite.services.credential_events import CredentialChange
        from pyrite.services.repo_service import RepoService

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        config = PyriteConfig(
            settings=Settings(index_path=tmp_path / "index.db", workspace_path=workspace)
        )
        with PyriteDB(config.settings.index_path) as db:
            repo = db.register_repo("org/kb", str(tmp_path / "clone"))
            db.register_kb("repo-kb", "generic", str(tmp_path / "clone"))
            db.execute_write_sql(
                "UPDATE kb SET repo_id = :r WHERE name = 'repo-kb'", {"r": repo["id"]}
            )
            (tmp_path / "clone").mkdir()
            db.merge_registered_kbs(config)  # known through the registry cache
            assert config.get_kb("repo-kb") is not None
            db.add_workspace_repo(db.get_local_user()["id"], repo["id"])
            with patch("pyrite.services.repo_service.save_config"):
                assert RepoService(config, db).unsubscribe("org/kb")["success"] is True
        assert CredentialChange(kb_name="repo-kb") in heard
        assert config.get_kb("repo-kb") is None

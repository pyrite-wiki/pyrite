"""Invite management authenticates actual sessions, not unset request state."""

import pytest
from fastapi.testclient import TestClient
from pyrite.config import AuthConfig, PyriteConfig, Settings
from pyrite.server.api import create_app, get_db
from pyrite.services.auth_service import AuthService
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_user


@pytest.fixture
def env(tmp_path):
    config = PyriteConfig(
        settings=Settings(index_path=tmp_path / "index.db", auth=AuthConfig(enabled=True))
    )
    db = PyriteDB(config.settings.index_path)
    seed_user(db, "root", "password123", role="admin")
    seed_user(db, "reader", "password123", role="read")
    app = create_app(config=config)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    yield client, AuthService(db, config.settings.auth)
    client.close()
    db.close()


@pytest.mark.parametrize("operation", ["create", "list", "delete"])
@pytest.mark.parametrize(
    ("principal", "status"),
    [
        ("root", 200),
        ("reader", 403),
        pytest.param(
            None, 401, marks=pytest.mark.control(reason="Anonymous access was already refused")
        ),
    ],
)
def test_invite_management_session_roles(env, principal, status, operation):
    client, service = env
    existing = service.create_invite_code(created_by="root", role="write", note="existing")
    if principal:
        assert (
            client.post(
                "/auth/login", json={"username": principal, "password": "password123"}
            ).status_code
            == 200
        )
    if operation == "create":
        response = client.post("/auth/invite-codes", json={"role": "read", "note": "created"})
    elif operation == "list":
        response = client.get("/auth/invite-codes")
    else:
        response = client.delete("/auth/invite-codes/" + existing["code"])
    assert response.status_code == status, response.text
    codes = service.list_invite_codes()
    if status == 200:
        if operation == "create":
            assert any(c["note"] == "created" for c in codes)
        elif operation == "list":
            assert response.json()["codes"] == codes
        else:
            assert not codes
    else:
        assert len(codes) == 1


@pytest.mark.control(reason="Invalid and expired session cookies already refuse access")
def test_invalid_session_cannot_list_invites(env):
    client, _ = env
    client.cookies.set("pyrite_session", "invalid-token")
    assert client.get("/auth/invite-codes").status_code == 401

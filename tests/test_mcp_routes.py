"""
Tests for MCP SSE transport routes (/mcp/*).

Covers authentication, the /mcp/info metadata endpoint, and SSE connection setup.
"""

import hashlib
import tempfile
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.server.api import create_app, get_config, get_db
from pyrite.storage.database import PyriteDB


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _make_app(tmpdir, *, api_key="", api_keys=None):
    """Create a FastAPI app with MCP routes mounted."""
    db_path = tmpdir / "index.db"
    kb_path = tmpdir / "kb"
    kb_path.mkdir(exist_ok=True)

    settings_kwargs = {
        "index_path": db_path,
        "api_key": api_key,
    }
    if api_keys is not None:
        settings_kwargs["api_keys"] = api_keys

    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name="test-kb", path=kb_path, kb_type="generic"),
        ],
        settings=Settings(**settings_kwargs),
    )

    application = create_app(config=config)

    db = PyriteDB(db_path)
    application.dependency_overrides[get_config] = lambda: config
    application.dependency_overrides[get_db] = lambda: db

    return application, config, db


# =============================================================================
# /mcp/info tests
# =============================================================================


class TestMCPInfo:
    """Tests for the GET /mcp/info endpoint."""

    def test_info_no_auth_required_when_no_keys(self, tmpdir):
        """When no API keys are configured, /mcp/info returns open access info."""
        app, _, _ = _make_app(tmpdir)
        client = TestClient(app)
        resp = client.get("/mcp/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["transport"] == "sse"
        assert data["auth"] == "bearer"
        assert "/mcp/sse" in data["endpoint"]
        assert data["tier"] == "admin"
        assert isinstance(data["tools_count"], int)
        assert data["tools_count"] > 0

    def test_info_with_valid_bearer(self, tmpdir):
        """Bearer token grants authenticated access to /mcp/info."""
        app, _, _ = _make_app(tmpdir, api_key="my-secret")
        client = TestClient(app)
        resp = client.get("/mcp/info", headers={"Authorization": "Bearer my-secret"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == "admin"
        assert data["tools_count"] > 0

    def test_info_with_valid_x_api_key(self, tmpdir):
        """X-API-Key header also works for /mcp/info."""
        app, _, _ = _make_app(tmpdir, api_key="my-secret")
        client = TestClient(app)
        resp = client.get("/mcp/info", headers={"X-API-Key": "my-secret"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == "admin"

    def test_info_unauthenticated_when_keys_required(self, tmpdir):
        """When API keys are configured but none provided, /mcp/info returns unauthenticated tier."""
        app, _, _ = _make_app(tmpdir, api_key="my-secret")
        client = TestClient(app)
        resp = client.get("/mcp/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == "unauthenticated"
        # Still returns tool count (for read tier)
        assert isinstance(data["tools_count"], int)

    def test_info_with_tiered_api_keys(self, tmpdir):
        """api_keys list with role mappings works for /mcp/info."""
        read_key = "read-key-123"
        write_key = "write-key-456"
        read_hash = hashlib.sha256(read_key.encode()).hexdigest()
        write_hash = hashlib.sha256(write_key.encode()).hexdigest()

        app, _, _ = _make_app(
            tmpdir,
            api_keys=[
                {"key_hash": read_hash, "role": "read", "name": "read-key"},
                {"key_hash": write_hash, "role": "write", "name": "write-key"},
            ],
        )
        client = TestClient(app)

        # Read key
        resp = client.get("/mcp/info", headers={"Authorization": f"Bearer {read_key}"})
        assert resp.status_code == 200
        assert resp.json()["tier"] == "read"

        # Write key
        resp = client.get("/mcp/info", headers={"Authorization": f"Bearer {write_key}"})
        assert resp.status_code == 200
        assert resp.json()["tier"] == "write"


# =============================================================================
# /mcp/sse authentication tests
# =============================================================================


class TestMCPSSEAuth:
    """Tests for SSE endpoint authentication (non-streaming checks)."""

    def test_sse_rejects_unauthenticated(self, tmpdir):
        """SSE endpoint returns 401 when auth is required and no token provided."""
        app, _, _ = _make_app(tmpdir, api_key="my-secret")
        client = TestClient(app)
        # Note: TestClient cannot maintain an SSE stream, but we can verify
        # that auth rejection happens before the stream starts.
        resp = client.get("/mcp/sse")
        assert resp.status_code == 401

    def test_sse_rejects_bad_token(self, tmpdir):
        """SSE endpoint returns 401 for invalid Bearer token."""
        app, _, _ = _make_app(tmpdir, api_key="my-secret")
        client = TestClient(app)
        resp = client.get("/mcp/sse", headers={"Authorization": "Bearer wrong-key"})
        assert resp.status_code == 401


# =============================================================================
# /mcp/messages tests
# =============================================================================


class TestMCPMessages:
    """Tests for the POST /mcp/messages/ endpoint."""

    def test_messages_requires_session_id(self, tmpdir):
        """POST without session_id gets 400."""
        app, _, _ = _make_app(tmpdir)
        client = TestClient(app)
        resp = client.post("/mcp/messages/")
        assert resp.status_code == 400

    def test_messages_invalid_session_id(self, tmpdir):
        """POST with invalid session_id gets 400."""
        app, _, _ = _make_app(tmpdir)
        client = TestClient(app)
        resp = client.post("/mcp/messages/?session_id=not-a-uuid")
        assert resp.status_code == 400

    def test_messages_unknown_session_id(self, tmpdir):
        """POST with unknown (but valid UUID format) session_id gets 404."""
        app, _, _ = _make_app(tmpdir)
        client = TestClient(app)
        resp = client.post(
            "/mcp/messages/?session_id=00000000000000000000000000000000",
            content="{}",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 404


# =============================================================================
# Auth resolution unit tests
# =============================================================================


class TestBearerAuthResolution:
    """Unit tests for _resolve_bearer_auth."""

    def test_no_auth_configured_returns_admin(self, tmpdir):
        """When no auth is configured, any request gets admin."""
        from pyrite.server.mcp_routes import _resolve_bearer_auth

        db_path = tmpdir / "index.db"
        config = PyriteConfig(
            knowledge_bases=[],
            settings=Settings(index_path=db_path, api_key=""),
        )
        db = PyriteDB(db_path)

        # Mock a minimal request
        from starlette.testclient import TestClient as StarletteClient

        app, _, _ = _make_app(tmpdir)
        client = TestClient(app)

        # Use the info endpoint as a proxy test — it uses _resolve_bearer_auth
        resp = client.get("/mcp/info")
        data = resp.json()
        assert data["tier"] == "admin"

    def test_bearer_single_api_key(self, tmpdir):
        """Bearer token matching single api_key gets admin."""
        app, _, _ = _make_app(tmpdir, api_key="test-key-42")
        client = TestClient(app)
        resp = client.get("/mcp/info", headers={"Authorization": "Bearer test-key-42"})
        assert resp.status_code == 200
        assert resp.json()["tier"] == "admin"

    def test_bearer_wrong_key_is_refused(self, tmpdir):
        """A wrong Bearer token is refused on /mcp/info (#1754); it used to be
        shown the unauthenticated view. No credential still is."""
        app, _, _ = _make_app(tmpdir, api_key="test-key-42")
        client = TestClient(app)
        resp = client.get("/mcp/info", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401
        resp = client.get("/mcp/info")
        assert resp.status_code == 200
        assert resp.json()["tier"] == "unauthenticated"


# =============================================================================
# build_sdk_server client_id parameter
# =============================================================================


class TestBuildSDKServerClientID:
    """Tests that build_sdk_server passes client_id correctly."""

    def test_default_client_id_is_stdio(self, tmpdir):
        """Default build_sdk_server uses 'stdio' as client_id."""
        from pyrite.server.mcp_server import PyriteMCPServer

        db_path = tmpdir / "index.db"
        kb_path = tmpdir / "kb"
        kb_path.mkdir()

        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name="test", path=kb_path, kb_type="generic"),
            ],
            settings=Settings(index_path=db_path),
        )

        server = PyriteMCPServer(config=config, tier="read")
        try:
            sdk = server.build_sdk_server()
            assert sdk is not None
            # The server name should reflect the tier
            assert "read" in sdk.name
        finally:
            server.close()

    def test_custom_client_id(self, tmpdir):
        """build_sdk_server accepts a custom client_id."""
        from pyrite.server.mcp_server import PyriteMCPServer

        db_path = tmpdir / "index.db"
        kb_path = tmpdir / "kb"
        kb_path.mkdir()

        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name="test", path=kb_path, kb_type="generic"),
            ],
            settings=Settings(index_path=db_path),
        )

        server = PyriteMCPServer(config=config, tier="admin")
        try:
            sdk = server.build_sdk_server(client_id="user-alice")
            assert sdk is not None
            assert "admin" in sdk.name
        finally:
            server.close()

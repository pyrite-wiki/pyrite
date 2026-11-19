"""#867: an unknown link discovery mode is refused on every surface.

Six surfaces funnel through LinkDiscoveryService.discover_neighbors: CLI
`links discover`, CLI `links batch-suggest`, MCP `kb_discover_neighbors`,
MCP `kb_batch_suggest`, REST `GET /api/links/discover-neighbors`, REST
`GET /api/links/batch-suggest`. An unknown mode must be refused with the
three valid modes named -- never silently run as keyword, and never echoed
back as the mode that ran.
"""

import contextlib
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.exceptions import ValidationError
from pyrite.server.mcp_server import PyriteMCPServer
from pyrite.services.kb_service import KBService
from pyrite.services.link_discovery_service import LinkDiscoveryService
from pyrite.storage.database import PyriteDB

#: The three modes the service must accept and name in its refusal (#867).
VALID_MODES = ("keyword", "semantic", "hybrid")

runner = CliRunner()


@pytest.fixture
def mode_env():
    """One KB with one entry, enough to reach the mode check."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        kb_path = tmpdir / "kb"
        kb_path.mkdir()
        kb = KBConfig(name="rs", path=kb_path, kb_type=KBType.GENERIC)
        config = PyriteConfig(
            knowledge_bases=[kb], settings=Settings(index_path=tmpdir / "index.db")
        )
        db = PyriteDB(tmpdir / "index.db")
        svc = KBService(config, db)
        svc.create_entry("rs", "hello", "Hello World", body="x", entry_type="note", tags=["greet"])
        yield {"config": config, "db": db, "tmpdir": tmpdir}
        db.close()


@pytest.fixture
def empty_env():
    """One KB with no entries: batch_suggest must refuse a bad mode
    before the (empty) source loop (#867 review follow-up)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        kb_path = tmpdir / "kb"
        kb_path.mkdir()
        kb = KBConfig(name="empty", path=kb_path, kb_type=KBType.GENERIC)
        config = PyriteConfig(
            knowledge_bases=[kb], settings=Settings(index_path=tmpdir / "index.db")
        )
        db = PyriteDB(tmpdir / "index.db")
        yield {"config": config, "db": db, "tmpdir": tmpdir}
        db.close()


@contextlib.contextmanager
def _patch_config(env):
    with patch("pyrite.cli.load_config", return_value=env["config"]):
        with patch("pyrite.cli.context.load_config", return_value=env["config"]):
            yield


class TestUnknownModeRefused:
    def test_service_discover_neighbors_raises(self, mode_env):
        svc = LinkDiscoveryService(mode_env["config"], mode_env["db"])
        with pytest.raises(ValidationError) as exc_info:
            svc.discover_neighbors(entry_id="hello", kb_name="rs", mode="zzz", readable_kbs=None)
        for mode in VALID_MODES:
            assert mode in str(exc_info.value)

    def test_service_batch_suggest_raises(self, mode_env):
        svc = LinkDiscoveryService(mode_env["config"], mode_env["db"])
        with pytest.raises(ValidationError):
            svc.batch_suggest(source_kb="rs", target_kb="rs", mode="zzz", readable_kbs=None)

    def test_service_batch_suggest_refuses_before_empty_source_loop(self, empty_env):
        # An empty source KB never reaches discover_neighbors, so the mode
        # must be validated at the top of batch_suggest (#867 review).
        svc = LinkDiscoveryService(empty_env["config"], empty_env["db"])
        with pytest.raises(ValidationError) as exc_info:
            svc.batch_suggest(source_kb="empty", target_kb="empty", mode="zzz", readable_kbs=None)
        for mode in VALID_MODES:
            assert mode in str(exc_info.value)

    def test_valid_modes_still_run(self, mode_env):
        svc = LinkDiscoveryService(mode_env["config"], mode_env["db"])
        for mode in VALID_MODES:
            # semantic/hybrid may fall back to keyword without embeddings;
            # the point is they are accepted, not refused.
            assert isinstance(
                svc.discover_neighbors(
                    entry_id="hello", kb_name="rs", mode=mode, readable_kbs=None
                ),
                list,
            )

    def test_cli_discover_exits_nonzero(self, mode_env):
        with _patch_config(mode_env):
            result = runner.invoke(
                app,
                ["links", "discover", "hello", "-k", "rs", "--mode", "zzz", "--format", "json"],
            )
        assert result.exit_code == 1
        assert "VALIDATION_FAILED" in result.output
        for mode in VALID_MODES:
            assert mode in result.output
        # the unknown mode is never echoed back as the mode that ran
        assert '"mode": "zzz"' not in result.output

    def test_cli_batch_suggest_exits_nonzero(self, mode_env):
        with _patch_config(mode_env):
            result = runner.invoke(
                app,
                [
                    "links",
                    "batch-suggest",
                    "--source-kb",
                    "rs",
                    "--target-kb",
                    "rs",
                    "--mode",
                    "zzz",
                    "--format",
                    "json",
                ],
            )
        assert result.exit_code == 1
        assert "VALIDATION_FAILED" in result.output

    def test_mcp_discover_neighbors_refused(self, mode_env):
        server = PyriteMCPServer(mode_env["config"])
        try:
            out = server._dispatch_tool(
                "kb_discover_neighbors",
                {"entry_id": "hello", "kb_name": "rs", "mode": "zzz"},
                client_kind="local",
                readable_kbs=None,
            )
        finally:
            server.db.close()
        assert out["error_code"] == "VALIDATION_FAILED"
        for mode in VALID_MODES:
            assert mode in out["error"]

    def test_mcp_batch_suggest_refused(self, mode_env):
        server = PyriteMCPServer(mode_env["config"])
        try:
            out = server._dispatch_tool(
                "kb_batch_suggest",
                {"source_kb": "rs", "target_kb": "rs", "mode": "zzz"},
                client_kind="local",
                readable_kbs=None,
            )
        finally:
            server.db.close()
        assert out["error_code"] == "VALIDATION_FAILED"


class TestUnknownModeRefusedRest:
    def _entry_id(self, client):
        search = client.get("/api/search", params={"q": "Test Event", "kb": "test-events"})
        assert search.status_code == 200
        return search.json()["results"][0]["id"]

    def test_rest_discover_neighbors_is_400(self, rest_api_env):
        # REST answers 400 VALIDATION_FAILED for an unknown mode (maintainer
        # decision 2026-10-17): REST validation errors answer 400 per
        # docs/json-contracts.md, not the central handler's 422.
        client = rest_api_env["client"]
        response = client.get(
            "/api/links/discover-neighbors",
            params={
                "entry_id": self._entry_id(client),
                "kb": "test-events",
                "mode": "zzz",
            },
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "VALIDATION_FAILED"
        for mode in VALID_MODES:
            assert mode in detail["message"]

    def test_rest_batch_suggest_is_400(self, rest_api_env):
        client = rest_api_env["client"]
        response = client.get(
            "/api/links/batch-suggest",
            params={
                "source_kb": "test-events",
                "target_kb": "test-research",
                "mode": "zzz",
            },
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "VALIDATION_FAILED"

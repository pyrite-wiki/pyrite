"""#917: an unknown search mode is refused on every surface.

Three surfaces funnel through ``SearchService.search`` (via ``search_response``):
CLI ``search``, MCP ``kb_search``, REST ``GET /api/search``. An unknown mode
must be refused with the three valid modes named -- never silently run as
keyword, and never echoed back as the mode that ran.
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
from pyrite.services.search_service import SearchService
from pyrite.storage.database import PyriteDB

#: The three modes the service must accept and name in its refusal (#917).
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


@contextlib.contextmanager
def _patch_config(env):
    # search_commands binds load_config into its own namespace
    # (from ..config import load_config), so patch it there.
    with patch("pyrite.cli.search_commands.load_config", return_value=env["config"]):
        with patch("pyrite.cli.context.load_config", return_value=env["config"]):
            yield


class TestUnknownModeRefused:
    def test_service_search_raises(self, mode_env):
        svc = SearchService(mode_env["db"])
        with pytest.raises(ValidationError) as exc_info:
            svc.search("hello", mode="zzz")
        for mode in VALID_MODES:
            assert mode in str(exc_info.value)
        assert "zzz" in str(exc_info.value)

    def test_differently_cased_mode_refused(self, mode_env):
        # Decided behavior (#917, same as #867): the check is exact
        # membership in ("keyword", "semantic", "hybrid"), so a differently
        # cased mode like "Keyword" is refused rather than normalized.
        svc = SearchService(mode_env["db"])
        with pytest.raises(ValidationError) as exc_info:
            svc.search("hello", mode="Keyword")
        for mode in VALID_MODES:
            assert mode in str(exc_info.value)

    def test_valid_modes_still_run(self, mode_env):
        svc = SearchService(mode_env["db"])
        for mode in VALID_MODES:
            # semantic/hybrid may fall back to keyword without embeddings;
            # the point is they are accepted, not refused.
            assert isinstance(svc.search("hello", mode=mode), list)

    def test_cli_search_json_exits_nonzero(self, mode_env):
        with _patch_config(mode_env):
            result = runner.invoke(
                app,
                ["search", "hello", "-k", "rs", "--mode", "zzz", "--format", "json"],
            )
        assert result.exit_code == 1
        assert "ValidationError" in result.output
        for mode in VALID_MODES:
            assert mode in result.output
        # the unknown mode is never echoed back as the mode that ran
        assert '"mode": "zzz"' not in result.output

    def test_mcp_kb_search_refused(self, mode_env):
        server = PyriteMCPServer(mode_env["config"])
        try:
            out = server._dispatch_tool(
                "kb_search",
                {"query": "hello", "kb_name": "rs", "mode": "zzz"},
                client_kind="local",
                readable_kbs=None,
            )
        finally:
            server.db.close()
        assert out["error_code"] == "VALIDATION_FAILED"
        for mode in VALID_MODES:
            assert mode in out["error"]


class TestUnknownModeRefusedRest:
    def test_rest_search_is_400(self, rest_api_env):
        # REST answers 400 VALIDATION_FAILED for an unknown mode (maintainer
        # decision 2026-10-17): REST validation errors answer 400 per
        # docs/json-contracts.md, not the central handler's 422 for
        # ValidationError.
        client = rest_api_env["client"]
        response = client.get("/api/search", params={"q": "hello", "mode": "zzz"})
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "VALIDATION_FAILED"
        for mode in VALID_MODES:
            assert mode in detail["message"]

"""#1397: `search --limit` refuses 0 and negative values as a usage error.

`--limit -1` was silently accepted: on the index path SQLite reads
`LIMIT -1` as "no limit", and the service only surfaced a messy
`ValueError` ("limit must be a positive integer") with exit 1; on the
`--files` path it returned every match with exit 0. A limit is a
maximum count, so 0 and below are meaningless, and both paths must
refuse them the same way, before any search runs.
"""

import contextlib
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB

runner = CliRunner()


@pytest.fixture
def limit_env():
    """One KB with one entry, enough to reach the limit check."""
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
        svc.create_entry("rs", "hello", "Hello World", body="hello world", entry_type="note")
        yield {"config": config, "db": db, "tmpdir": tmpdir}
        db.close()


@contextlib.contextmanager
def _patch_config(env):
    # search_commands binds load_config into its own namespace
    # (from ..config import load_config), so patch it there.
    with patch("pyrite.cli.search_commands.load_config", return_value=env["config"]):
        with patch("pyrite.cli.context.load_config", return_value=env["config"]):
            yield


@pytest.mark.cli
class TestSearchLimitBelowOne:
    @pytest.mark.parametrize("bad_limit", ["0", "-1"])
    def test_index_path_refuses_as_usage_error(self, limit_env, bad_limit):
        with _patch_config(limit_env):
            result = runner.invoke(
                app, ["search", "hello", "-k", "rs", "--limit", bad_limit, "--format", "json"]
            )
        assert result.exit_code == 2, result.output
        assert "Invalid value for '--limit'" in result.output

    @pytest.mark.parametrize("bad_limit", ["0", "-1"])
    def test_files_path_refuses_as_usage_error(self, limit_env, bad_limit):
        with _patch_config(limit_env):
            result = runner.invoke(
                app,
                ["search", "hello", "-k", "rs", "--limit", bad_limit, "--files", "--format", "json"],
            )
        assert result.exit_code == 2, result.output
        assert "Invalid value for '--limit'" in result.output

    def test_limit_one_returns_exactly_one_result(self, limit_env):
        with _patch_config(limit_env):
            result = runner.invoke(
                app, ["search", "hello", "-k", "rs", "--limit", "1", "--format", "json"]
            )
        assert result.exit_code == 0, result.output
        import json

        assert json.loads(result.output)["count"] == 1

    def test_limit_one_files_path_runs(self, limit_env):
        with _patch_config(limit_env):
            result = runner.invoke(
                app, ["search", "hello", "-k", "rs", "--limit", "1", "--files", "--format", "json"]
            )
        assert result.exit_code == 0, result.output

"""Tests for pyrite-read CLI (read-only entry point).

Verifies that pyrite-read exposes only read operations and
does not include any write or admin commands.
"""

import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import NoteEntry
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

runner = CliRunner()


@pytest.fixture
def cli_env():
    """Environment for read CLI tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        kb_path = tmpdir / "notes"
        kb_path.mkdir()

        kb = KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)
        config = PyriteConfig(
            knowledge_bases=[kb],
            settings=Settings(index_path=db_path),
        )

        # Create sample entries
        repo = KBRepository(kb)
        for i in range(3):
            entry = NoteEntry(
                id=f"note-{i}",
                title=f"Test Note {i}",
                body=f"Body for note {i} about testing.",
                tags=["test", f"tag-{i}"],
            )
            repo.save(entry)

        # Index them
        db = PyriteDB(db_path)
        index_mgr = IndexManager(db, config)
        index_mgr.index_all()

        import pyrite.cli as cli_module
        import pyrite.read_cli as read_cli_module

        original_get_svc = cli_module._get_svc
        original_read_get_svc = read_cli_module._get_svc

        from pyrite.services.kb_service import KBService

        def mock_get_svc(*kbs, **kwargs):  # _get_svc(kb_name, output_format=...)
            svc = KBService(config, db)
            return svc, db

        cli_module._get_svc = mock_get_svc
        read_cli_module._get_svc = mock_get_svc

        yield {"config": config, "db": db, "kb": kb}

        cli_module._get_svc = original_get_svc
        read_cli_module._get_svc = original_read_get_svc
        db.close()


class TestReadCLICommands:
    """Test that read-only commands work in pyrite-read."""

    def test_get_entry(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["get", "note-0", "--kb", "test-kb"])
        assert result.exit_code == 0
        assert "Test Note 0" in result.output

    def test_search(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["search", "testing"])
        assert result.exit_code == 0

    def test_list_kbs(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["list"])
        assert result.exit_code == 0
        assert "test-kb" in result.output

    def test_tags(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["tags"])
        assert result.exit_code == 0

    def test_backlinks(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["backlinks", "note-0", "--kb", "test-kb"])
        # May have no backlinks, but command should succeed
        assert result.exit_code == 0

    def test_timeline(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["timeline"])
        assert result.exit_code == 0

    def test_config(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["config"])
        assert result.exit_code == 0


class TestReadCLIExcludesWrites:
    """Test that write and admin commands are NOT available in pyrite-read."""

    def test_no_create_command(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(
            read_app,
            ["create", "--kb", "test-kb", "--title", "Should Fail", "--type", "note"],
        )
        # Typer returns exit code 2 for unknown commands
        assert (
            result.exit_code == 2 or "No such command" in result.output or "Error" in result.output
        )

    def test_no_update_command(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["update", "note-0", "--kb", "test-kb", "--title", "New"])
        assert (
            result.exit_code == 2 or "No such command" in result.output or "Error" in result.output
        )

    def test_no_delete_command(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["delete", "note-0", "--kb", "test-kb", "--force"])
        assert (
            result.exit_code == 2 or "No such command" in result.output or "Error" in result.output
        )

    def test_no_link_command(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["link", "note-0", "note-1", "--kb", "test-kb"])
        assert (
            result.exit_code == 2 or "No such command" in result.output or "Error" in result.output
        )

    def test_no_serve_command(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["serve"])
        assert (
            result.exit_code == 2 or "No such command" in result.output or "Error" in result.output
        )

    def test_no_index_subcommand(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["index", "build"])
        assert (
            result.exit_code == 2 or "No such command" in result.output or "Error" in result.output
        )

    def test_no_kb_subcommand(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["kb", "list"])
        assert (
            result.exit_code == 2 or "No such command" in result.output or "Error" in result.output
        )

    def test_no_repo_subcommand(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["repo", "list"])
        assert (
            result.exit_code == 2 or "No such command" in result.output or "Error" in result.output
        )

    def test_no_auth_subcommand(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["auth", "status"])
        assert (
            result.exit_code == 2 or "No such command" in result.output or "Error" in result.output
        )

    def test_no_mcp_command(self, cli_env):
        from pyrite.read_cli import app as read_app

        result = runner.invoke(read_app, ["mcp"])
        assert (
            result.exit_code == 2 or "No such command" in result.output or "Error" in result.output
        )

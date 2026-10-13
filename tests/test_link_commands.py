"""Tests for link CLI commands (pyrite links check).

Uses shared fixtures and config patching.
"""

import json
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import EventEntry
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

runner = CliRunner()


@pytest.fixture
def link_env():
    """Environment for link command tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        events_path = tmpdir / "events"
        events_path.mkdir()

        events_kb = KBConfig(
            name="test-events",
            path=events_path,
            kb_type=KBType.EVENTS,
        )

        config = PyriteConfig(
            knowledge_bases=[events_kb],
            settings=Settings(index_path=db_path),
        )

        # Create sample event
        repo = KBRepository(events_kb)
        event = EventEntry.create(
            date="2025-01-10",
            title="Test Event 0",
            body="Body for event 0.",
            importance=5,
        )
        repo.save(event)

        db = PyriteDB(db_path)
        IndexManager(db, config).index_all()
        db.close()

        yield {"config": config, "tmpdir": tmpdir}


def _patch_config(env):
    """Patch load_config to return test config."""
    import contextlib
    from unittest.mock import patch

    @contextlib.contextmanager
    def _multi_patch():
        with patch("pyrite.cli.load_config", return_value=env["config"]):
            with patch("pyrite.cli.context.load_config", return_value=env["config"]):
                yield

    return _multi_patch()


@pytest.mark.cli
class TestLinksCheck:
    @pytest.mark.control(reason="control: asks for rich explicitly, so it passes either way")
    def test_check_empty(self, link_env):
        """Clean KB shows no broken links."""
        with _patch_config(link_env):
            result = runner.invoke(app, ["links", "check", "--format", "rich"])
            assert result.exit_code == 0
            assert "No broken links" in result.output

    def test_check_with_results(self, link_env):
        """Broken links appear grouped by missing target."""
        from pyrite.services.kb_service import KBService

        config = link_env["config"]
        db = PyriteDB(config.settings.index_path)
        svc = KBService(config, db)
        svc.create_entry(
            "test-events",
            "src-1",
            "Source 1",
            "note",
            body="See [[missing-page]].",
        )
        svc.create_entry(
            "test-events",
            "src-2",
            "Source 2",
            "note",
            body="Also [[missing-page]].",
        )
        IndexManager(db, config).sync_incremental("test-events")
        db.close()

        with _patch_config(link_env):
            result = runner.invoke(app, ["links", "check", "--format", "json"])
            assert result.exit_code == 1  # a broken link fails the check
            data = json.loads(result.output)
            assert data["missing_targets"] >= 1
            assert data["total_references"] >= 2
            target = next(t for t in data["targets"] if t["target_id"] == "missing-page")
            assert target["ref_count"] == 2
            assert len(target["references"]) == 2

    def test_check_json_structure(self, link_env):
        """JSON output has expected top-level keys."""
        with _patch_config(link_env):
            result = runner.invoke(app, ["links", "check", "--format", "json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "missing_targets" in data
            assert "total_references" in data
            assert "targets" in data

    @pytest.mark.control(reason="control: asks for rich explicitly, so it passes either way")
    def test_check_detail_flag(self, link_env):
        """--detail shows per-link breakdown."""
        from pyrite.services.kb_service import KBService

        config = link_env["config"]
        db = PyriteDB(config.settings.index_path)
        svc = KBService(config, db)
        svc.create_entry(
            "test-events",
            "detail-src",
            "Detail source",
            "note",
            body="See [[detail-target]].",
        )
        IndexManager(db, config).sync_incremental("test-events")
        db.close()

        with _patch_config(link_env):
            result = runner.invoke(app, ["links", "check", "--detail", "--format", "rich"])
            assert result.exit_code == 1  # a broken link fails the check
            # Detail mode shows the arrow notation
            assert "\u2190" in result.output
            assert "detail-target" in result.output
            assert "detail-src" in result.output


@pytest.mark.cli
class TestIndexHealthBrokenLinks:
    def test_index_health_includes_broken_links(self, link_env):
        """Health check JSON output includes broken_links count."""
        with _patch_config(link_env):
            result = runner.invoke(app, ["index", "health", "--format", "json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "broken_links" in data

    def test_index_health_broken_links_warning(self, link_env):
        """Health check shows warning status when broken links exist."""
        from pyrite.services.kb_service import KBService

        config = link_env["config"]
        db = PyriteDB(config.settings.index_path)
        svc = KBService(config, db)
        svc.create_entry(
            "test-events",
            "health-broken",
            "Health broken link",
            "note",
            body="See [[health-missing-target]].",
        )
        IndexManager(db, config).sync_incremental("test-events")
        db.close()

        with _patch_config(link_env):
            result = runner.invoke(app, ["index", "health", "--format", "json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["status"] == "warning"
            assert data["broken_links"] >= 1

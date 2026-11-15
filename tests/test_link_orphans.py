"""Tests for pyrite links orphans command (cross-KB orphan detection)."""

import json
import tempfile
from pathlib import Path

import pytest
from click import unstyle
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.cli.link_commands import _find_orphans
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB

runner = CliRunner()


@pytest.fixture
def orphan_env():
    """Two KBs where some entries have cross-KB links and others don't."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        kb_a_path = tmpdir / "kb-a"
        kb_a_path.mkdir()
        kb_b_path = tmpdir / "kb-b"
        kb_b_path.mkdir()

        kb_a = KBConfig(name="kb-a", path=kb_a_path, kb_type=KBType.GENERIC)
        kb_b = KBConfig(name="kb-b", path=kb_b_path, kb_type=KBType.GENERIC)

        config = PyriteConfig(
            knowledge_bases=[kb_a, kb_b],
            settings=Settings(index_path=db_path),
        )

        db = PyriteDB(db_path)
        svc = KBService(config, db)

        # High-importance entry WITH cross-KB link (not orphan)
        svc.create_entry(
            "kb-a",
            "linked-concept",
            "Linked Concept",
            body="This concept about trust is well-connected.",
            entry_type="concept",
            tags=["trust"],
        )
        svc.update_entry("linked-concept", "kb-a", importance=9)
        svc.create_entry(
            "kb-b",
            "linked-target",
            "Linked Target",
            body="Trust and coordination in organizations.",
            entry_type="concept",
            tags=["trust"],
        )
        svc.add_link(
            "linked-concept", "kb-a", "linked-target", target_kb="kb-b", relation="related_to"
        )

        # High-importance entry WITHOUT cross-KB link (orphan candidate)
        svc.create_entry(
            "kb-a",
            "orphan-concept",
            "Orphan Concept",
            body="Feedback loops and systems thinking patterns.",
            entry_type="concept",
            tags=["systems", "feedback"],
        )
        svc.update_entry("orphan-concept", "kb-a", importance=8)
        # Create a matching entry in kb-b that COULD be linked
        svc.create_entry(
            "kb-b",
            "systems-match",
            "System Dynamics",
            body="System dynamics models feedback loops and delays.",
            entry_type="concept",
            tags=["systems", "feedback"],
        )

        # Low-importance entry (should be filtered out)
        svc.create_entry(
            "kb-a",
            "low-importance",
            "Low Importance Note",
            body="Just a casual note about cooking.",
            entry_type="note",
            tags=["cooking"],
        )

        # High-importance entry with FTS-column-colliding vocabulary and special characters (#304)
        svc.create_entry(
            "kb-a",
            "fts-orphan-concept",
            "Regulatory capture and legalism: cross-link 18:1 v1.2.3",
            body="Analysis of regulatory capture, legalism, and cross-link patterns.",
            entry_type="concept",
            tags=["capture", "legalism", "cross-link", "18:1", "1.2.3"],
        )
        svc.update_entry("fts-orphan-concept", "kb-a", importance=8)
        svc.create_entry(
            "kb-b",
            "fts-orphan-match",
            "Legalism and Cross-Link Patterns",
            body="Cross-KB matching concept discussing capture, legalism, and cross-link.",
            entry_type="concept",
            tags=["capture", "legalism", "cross-link"],
        )

        yield {"config": config, "db": db, "svc": svc}
        db.close()


class TestFindOrphans:
    def test_finds_orphan_entries(self, orphan_env):
        results = _find_orphans(
            kb_name="kb-a",
            min_importance=7,
            limit=10,
            config=orphan_env["config"],
            db=orphan_env["db"],
        )
        ids = [r["id"] for r in results]
        # orphan-concept should appear (high importance, no cross-KB links, has potential matches)
        assert "orphan-concept" in ids

    def test_linked_entries_ranked_lower(self, orphan_env):
        results = _find_orphans(
            kb_name="kb-a",
            min_importance=7,
            limit=10,
            config=orphan_env["config"],
            db=orphan_env["db"],
        )
        ids = [r["id"] for r in results]
        if "linked-concept" in ids and "orphan-concept" in ids:
            # orphan should be ranked higher (more orphan-ness)
            assert ids.index("orphan-concept") < ids.index("linked-concept")

    def test_filters_by_importance(self, orphan_env):
        results = _find_orphans(
            kb_name="kb-a",
            min_importance=7,
            limit=10,
            config=orphan_env["config"],
            db=orphan_env["db"],
        )
        ids = [r["id"] for r in results]
        assert "low-importance" not in ids

    def test_results_have_required_fields(self, orphan_env):
        results = _find_orphans(
            kb_name="kb-a",
            min_importance=5,
            limit=10,
            config=orphan_env["config"],
            db=orphan_env["db"],
        )
        if results:
            r = results[0]
            assert "id" in r
            assert "title" in r
            assert "importance" in r
            assert "cross_kb_links" in r
            assert "potential_matches" in r
            assert "orphan_score" in r

    def test_respects_limit(self, orphan_env):
        results = _find_orphans(
            kb_name="kb-a",
            min_importance=1,
            limit=1,
            config=orphan_env["config"],
            db=orphan_env["db"],
        )
        assert len(results) <= 1

    def test_find_orphans_fts_quoting_regression(self, orphan_env):
        """Regression test for #304: entries with FTS-column-colliding vocabulary
        (capture, legalism) or special characters (cross-link, 18:1, 1.2.3) in titles
        or tags must be quoted so discover_neighbors / find_orphans does not raise
        OperationalError / QuerySyntaxError.
        """
        results = _find_orphans(
            kb_name="kb-a",
            min_importance=7,
            limit=10,
            config=orphan_env["config"],
            db=orphan_env["db"],
        )
        ids = [r["id"] for r in results]
        assert "fts-orphan-concept" in ids


class TestOrphansCLI:
    def test_cli_json_output(self, orphan_env, monkeypatch):
        orig_close = orphan_env["db"].close
        monkeypatch.setattr(
            "pyrite.cli.link_commands.get_config_and_db",
            lambda: (orphan_env["config"], orphan_env["db"]),
        )
        orphan_env["db"].close = lambda: None
        try:
            result = runner.invoke(app, ["links", "orphans", "--kb", "kb-a", "--format", "json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "orphans" in data
            assert data["kb_name"] == "kb-a"
        finally:
            orphan_env["db"].close = orig_close

    def test_cli_orphans_fts_quoting_regression(self, orphan_env, monkeypatch):
        """Regression test for #304: CLI links orphans succeeds without OperationalError
        when entry vocabulary contains FTS column names and special characters.
        """
        orig_close = orphan_env["db"].close
        monkeypatch.setattr(
            "pyrite.cli.link_commands.get_config_and_db",
            lambda: (orphan_env["config"], orphan_env["db"]),
        )
        orphan_env["db"].close = lambda: None
        try:
            result = runner.invoke(app, ["links", "orphans", "--kb", "kb-a", "--format", "json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "orphans" in data
            orphan_ids = [o["id"] for o in data["orphans"]]
            assert "fts-orphan-concept" in orphan_ids
        finally:
            orphan_env["db"].close = orig_close

    @pytest.mark.cli
    @pytest.mark.parametrize("bad_importance", ["-1", "0", "11"])
    def test_cli_min_importance_out_of_range_is_usage_error(self, cli_world, bad_importance):
        """Out-of-range --min-importance (< 1 or > 10) is refused as USAGE_ERROR (#1474)."""
        world = cli_world()
        result = world.cli(
            "links",
            "orphans",
            "--kb",
            "test-kb",
            "--min-importance",
            bad_importance,
            "--format",
            "json",
        )
        output = unstyle(result.output)

        assert result.exit_code == 2, result.output
        assert "Traceback" not in output
        document = json.loads(next(line for line in output.splitlines() if line.startswith("{")))
        assert document["outcome"] == "error"
        assert document["error_code"] == "USAGE_ERROR"
        assert "--min-importance" in document["error"]
        assert bad_importance in document["error"]

    @pytest.mark.cli
    @pytest.mark.control(
        reason="inclusive 1..10 bounds and default 5 already ran before #1474 and must stay accepted"
    )
    @pytest.mark.parametrize(
        ("extra_args", "expected_min", "expected_ids"),
        [
            (["--min-importance", "1"], 1, {"low-orphan", "high-orphan"}),
            ([], 5, {"high-orphan"}),
            (["--min-importance", "10"], 10, set()),
        ],
    )
    def test_cli_min_importance_inclusive_bounds_and_default(
        self, cli_world, tmp_path, extra_args, expected_min, expected_ids
    ):
        """Inclusive bounds 1 and 10 and default 5 run cleanly (#1474)."""
        world = cli_world(
            kbs=[
                KBConfig(name="kb-a", path=tmp_path / "kb-a", kb_type=KBType.GENERIC),
                KBConfig(name="kb-b", path=tmp_path / "kb-b", kb_type=KBType.GENERIC),
            ]
        )
        world.add_entry(
            "kb-a",
            "---\nid: low-orphan\ntype: concept\ntitle: Low Orphan\nimportance: 2\n"
            "tags: [systems]\n---\nSystems feedback dynamics.\n",
        )
        world.add_entry(
            "kb-a",
            "---\nid: high-orphan\ntype: concept\ntitle: High Orphan\nimportance: 8\n"
            "tags: [systems]\n---\nSystems feedback dynamics.\n",
        )
        world.add_entry(
            "kb-b",
            "---\nid: peer-match\ntype: concept\ntitle: Peer Match\nimportance: 7\n"
            "tags: [systems]\n---\nSystems feedback dynamics.\n",
        )

        result = world.cli("links", "orphans", "--kb", "kb-a", *extra_args, "--format", "json")

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["outcome"] == "done"
        assert data["min_importance"] == expected_min
        assert {o["id"] for o in data["orphans"]} == expected_ids

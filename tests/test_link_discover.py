"""Tests for pyrite links discover command.

Tests the cross-KB semantic neighbor discovery feature.
"""

import json
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.cli.link_commands import _discover_neighbors
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.server.tool_schemas import READ_TOOLS
from pyrite.storage.database import PyriteDB

runner = CliRunner()


@pytest.fixture
def discover_env():
    """Environment with two KBs for cross-KB discovery tests."""
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

        # Create source entry in kb-a
        svc.create_entry(
            "kb-a",
            "trust-mechanisms",
            "Trust as a Coordination Mechanism",
            body="Trust enables decentralized coordination without hierarchy. "
            "Organizations that build trust can operate with less oversight.",
            entry_type="concept",
            tags=["trust", "coordination", "governance"],
        )

        # Create related entries in kb-b (should be discoverable)
        svc.create_entry(
            "kb-b",
            "psychological-safety",
            "Psychological Safety in Teams",
            body="Psychological safety enables team members to take risks "
            "without fear. It is a form of interpersonal trust that supports coordination.",
            entry_type="concept",
            tags=["trust", "teams", "safety"],
        )
        svc.create_entry(
            "kb-b",
            "decentralized-orgs",
            "Decentralized Organization Patterns",
            body="Decentralized organizations rely on trust and shared norms "
            "instead of hierarchical authority for coordination.",
            entry_type="concept",
            tags=["organizations", "decentralization", "governance"],
        )
        svc.create_entry(
            "kb-b",
            "unrelated-entry",
            "Cooking Pasta Recipes",
            body="Boil water, add salt, cook pasta for 8 minutes. Drain and serve with sauce.",
            entry_type="note",
            tags=["cooking", "recipes"],
        )

        # Create an entry in kb-a that is already linked to trust-mechanisms
        svc.create_entry(
            "kb-b",
            "already-linked",
            "Already Linked Entry",
            body="This entry about trust and coordination is already linked.",
            entry_type="concept",
            tags=["trust"],
        )
        # Create the link
        svc.add_link(
            source_id="trust-mechanisms",
            source_kb="kb-a",
            target_id="already-linked",
            relation="related_to",
            target_kb="kb-b",
        )

        yield {
            "config": config,
            "db": db,
            "svc": svc,
            "tmpdir": tmpdir,
        }
        db.close()


class TestDiscoverNeighbors:
    """Test the _discover_neighbors function directly."""

    def test_default_search_includes_source_kb(self, discover_env):
        """Without a target KB, neighbors can come from the source KB too."""
        discover_env["svc"].create_entry(
            "kb-a",
            "shared-trust",
            "Shared Trust and Coordination",
            body="Trust supports coordination across teams.",
            entry_type="concept",
            tags=["trust", "coordination"],
        )

        def discover(target_kb):
            return _discover_neighbors(
                entry_id="trust-mechanisms",
                kb_name="kb-a",
                target_kb=target_kb,
                limit=10,
                mode="keyword",
                exclude_linked=True,
                config=discover_env["config"],
                db=discover_env["db"],
            )

        all_results = discover(None)
        assert ("kb-a", "shared-trust") in {
            (result["kb_name"], result["id"]) for result in all_results
        }
        assert ("kb-a", "trust-mechanisms") not in {
            (result["kb_name"], result["id"]) for result in all_results
        }
        assert {result["kb_name"] for result in discover("kb-a")} == {"kb-a"}
        assert {result["kb_name"] for result in discover("kb-b")} == {"kb-b"}

    def test_tool_description_mentions_same_kb_results(self):
        description = READ_TOOLS["kb_discover_neighbors"]["description"]
        assert "including the source KB" in description
        assert "target_kb" in description

    def test_finds_related_entries_in_other_kb(self, discover_env):
        """Keyword-based discovery finds entries with shared vocabulary."""
        results = _discover_neighbors(
            entry_id="trust-mechanisms",
            kb_name="kb-a",
            target_kb="kb-b",
            limit=10,
            mode="keyword",
            exclude_linked=True,
            config=discover_env["config"],
            db=discover_env["db"],
        )
        ids = [r["id"] for r in results]
        # Should find the trust/coordination related entries
        assert len(results) > 0
        # Should not include the unrelated cooking entry at the top
        if "unrelated-entry" in ids:
            # Cooking entry should be ranked lower than trust-related entries
            trust_ids = {"psychological-safety", "decentralized-orgs"}
            trust_positions = [i for i, r in enumerate(results) if r["id"] in trust_ids]
            cooking_pos = ids.index("unrelated-entry")
            assert all(p < cooking_pos for p in trust_positions)

    def test_excludes_already_linked(self, discover_env):
        """Entries with existing links to the source should be excluded."""
        results = _discover_neighbors(
            entry_id="trust-mechanisms",
            kb_name="kb-a",
            target_kb="kb-b",
            limit=10,
            mode="keyword",
            exclude_linked=True,
            config=discover_env["config"],
            db=discover_env["db"],
        )
        ids = [r["id"] for r in results]
        assert "already-linked" not in ids

    def test_includes_linked_when_not_excluded(self, discover_env):
        """When exclude_linked=False, linked entries should appear."""
        results = _discover_neighbors(
            entry_id="trust-mechanisms",
            kb_name="kb-a",
            target_kb="kb-b",
            limit=10,
            mode="keyword",
            exclude_linked=False,
            config=discover_env["config"],
            db=discover_env["db"],
        )
        ids = [r["id"] for r in results]
        assert "already-linked" in ids

    def test_excludes_source_entry(self, discover_env):
        """Source entry should never appear in results."""
        results = _discover_neighbors(
            entry_id="trust-mechanisms",
            kb_name="kb-a",
            target_kb=None,  # search all KBs
            limit=10,
            mode="keyword",
            exclude_linked=True,
            config=discover_env["config"],
            db=discover_env["db"],
        )
        ids = [r["id"] for r in results]
        assert "trust-mechanisms" not in ids

    def test_returns_empty_for_nonexistent_entry(self, discover_env):
        results = _discover_neighbors(
            entry_id="nonexistent",
            kb_name="kb-a",
            target_kb="kb-b",
            limit=10,
            mode="keyword",
            exclude_linked=True,
            config=discover_env["config"],
            db=discover_env["db"],
        )
        assert results == []

    def test_results_have_required_fields(self, discover_env):
        results = _discover_neighbors(
            entry_id="trust-mechanisms",
            kb_name="kb-a",
            target_kb="kb-b",
            limit=10,
            mode="keyword",
            exclude_linked=True,
            config=discover_env["config"],
            db=discover_env["db"],
        )
        if results:
            r = results[0]
            assert "id" in r
            assert "kb_name" in r
            assert "title" in r
            assert "entry_type" in r
            assert "score" in r

    def test_respects_limit(self, discover_env):
        results = _discover_neighbors(
            entry_id="trust-mechanisms",
            kb_name="kb-a",
            target_kb="kb-b",
            limit=1,
            mode="keyword",
            exclude_linked=True,
            config=discover_env["config"],
            db=discover_env["db"],
        )
        assert len(results) <= 1


class TestDiscoverCLI:
    """Test the CLI command integration."""

    def test_cli_discover_json_output(self, discover_env, monkeypatch):
        monkeypatch.setattr(
            "pyrite.cli.link_commands.get_config_and_db",
            lambda: (discover_env["config"], discover_env["db"]),
        )
        # Prevent db.close() from being called by the function
        discover_env["db"].close = lambda: None

        result = runner.invoke(
            app,
            [
                "links",
                "discover",
                "trust-mechanisms",
                "-k",
                "kb-a",
                "--target-kb",
                "kb-b",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "discoveries" in data
        assert data["entry_id"] == "trust-mechanisms"

    def test_cli_discover_nonexistent_entry(self, discover_env, monkeypatch):
        monkeypatch.setattr(
            "pyrite.cli.link_commands.get_config_and_db",
            lambda: (discover_env["config"], discover_env["db"]),
        )
        discover_env["db"].close = lambda: None

        result = runner.invoke(
            app, ["links", "discover", "nonexistent", "-k", "kb-a", "--format", "json"]
        )
        assert result.exit_code == 1

    @pytest.mark.parametrize("bad_limit", ["0", "-1"])
    def test_cli_discover_limit_below_one_is_usage_error(
        self, discover_env, monkeypatch, bad_limit
    ):
        monkeypatch.setattr(
            "pyrite.cli.link_commands.get_config_and_db",
            lambda: (discover_env["config"], discover_env["db"]),
        )
        # Prevent db.close() from being called by the function
        discover_env["db"].close = lambda: None

        result = runner.invoke(
            app,
            [
                "links",
                "discover",
                "trust-mechanisms",
                "-k",
                "kb-a",
                "--limit",
                bad_limit,
            ],
        )
        assert result.exit_code == 2
        assert "Invalid value for '--limit'" in result.output

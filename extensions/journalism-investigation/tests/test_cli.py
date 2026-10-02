"""Tests for journalism-investigation CLI commands."""

import json

import pytest
from pyrite_journalism_investigation.cli import investigation_app
from typer.testing import CliRunner

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB

runner = CliRunner()


@pytest.fixture
def populated_kb(tmp_path, monkeypatch):
    """Set up a temporary KB with test data for CLI testing."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()
    kb = KBConfig(name="test", path=kb_path, kb_type="journalism-investigation")
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    svc = KBService(config, db)

    # Patch load_config to return our test config
    monkeypatch.setattr("pyrite_journalism_investigation.cli.load_config", lambda: config)

    # Create test data
    svc.create_entry(
        "test",
        "sanctions-2022",
        "Sanctions Announced",
        "investigation_event",
        body="EU sanctions",
        date="2022-02-24",
        importance=9,
    )
    svc.create_entry(
        "test",
        "wire-transfer-001",
        "Wire Transfer",
        "transaction",
        body="Transfer via Cyprus",
        date="2019-06-15",
        importance=7,
        sender="[[oligarchov]]",
        receiver="[[cyprus-corp]]",
        amount="5000000",
    )
    svc.create_entry(
        "test",
        "mansion-belgravia",
        "London Belgravia Mansion",
        "asset",
        body="Townhouse",
        asset_type="real_estate",
        jurisdiction="United Kingdom",
        importance=8,
    )
    svc.create_entry(
        "test",
        "panama-doc-4427",
        "Panama Papers Doc 4427",
        "document_source",
        body="Mossack Fonseca doc",
        reliability="high",
        classification="leaked",
        importance=9,
    )

    yield {"config": config, "db": db, "svc": svc}
    db.close()


class TestTimelineCommand:
    def test_timeline_default(self, populated_kb):
        result = runner.invoke(investigation_app, ["timeline", "-k", "test"])
        assert result.exit_code == 0
        assert "Sanctions" in result.output or "Wire Transfer" in result.output

    def test_timeline_json(self, populated_kb):
        result = runner.invoke(investigation_app, ["timeline", "-k", "test", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "events" in data
        assert data["count"] >= 1

    @pytest.mark.parametrize("force_color", [False, True])
    def test_timeline_json_preserves_long_title(self, populated_kb, monkeypatch, force_color):
        from rich.console import Console

        if force_color:
            monkeypatch.setenv("FORCE_COLOR", "1")
        else:
            monkeypatch.delenv("FORCE_COLOR", raising=False)
        # Recreate the console after setting the environment, as a CLI process would.
        monkeypatch.setattr("pyrite_journalism_investigation.cli.console", Console(width=40))
        title = "[bold]Evidence[/bold] " + "long investigation title " * 8
        populated_kb["svc"].create_entry(
            "test", "long-event", title, "investigation_event", date="2023-01-01"
        )

        result = runner.invoke(investigation_app, ["timeline", "-k", "test", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert (
            next(event for event in data["events"] if event["id"] == "long-event")["title"] == title
        )
        assert "\x1b" not in result.stdout

    def test_timeline_date_filter(self, populated_kb):
        result = runner.invoke(
            investigation_app,
            [
                "timeline",
                "-k",
                "test",
                "--from",
                "2022-01-01",
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Should only get 2022 events, not 2019
        for event in data["events"]:
            assert event["date"] >= "2022-01-01"


class TestEntitiesCommand:
    def test_entities_default(self, populated_kb):
        result = runner.invoke(investigation_app, ["entities", "-k", "test"])
        assert result.exit_code == 0
        assert "Mansion" in result.output or "Belgravia" in result.output

    def test_entities_json(self, populated_kb):
        result = runner.invoke(investigation_app, ["entities", "-k", "test", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "entities" in data
        assert data["count"] >= 1


class TestSourcesCommand:
    def test_sources_default(self, populated_kb):
        result = runner.invoke(investigation_app, ["sources", "-k", "test"])
        assert result.exit_code == 0
        assert "Panama" in result.output

    def test_sources_json(self, populated_kb):
        result = runner.invoke(investigation_app, ["sources", "-k", "test", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "sources" in data
        assert data["count"] >= 1

    def test_sources_reliability_filter(self, populated_kb):
        result = runner.invoke(
            investigation_app,
            [
                "sources",
                "-k",
                "test",
                "--reliability",
                "high",
                "--json",
            ],
        )
        data = json.loads(result.output)
        assert data["count"] >= 1
        for s in data["sources"]:
            assert s["reliability"] == "high"


class TestClaimsCommand:
    def test_claims_no_data(self, populated_kb):
        """With no claims created, should show empty results."""
        result = runner.invoke(investigation_app, ["claims", "-k", "test", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["count"] == 0


class TestNetworkCommand:
    def test_network_found(self, populated_kb):
        result = runner.invoke(
            investigation_app,
            [
                "network",
                "mansion-belgravia",
                "-k",
                "test",
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["center"]["id"] == "mansion-belgravia"

    def test_network_not_found(self, populated_kb):
        result = runner.invoke(
            investigation_app,
            [
                "network",
                "nonexistent",
                "-k",
                "test",
                "--json",
            ],
        )
        assert result.exit_code == 1  # an error answer is a failure in the exit too (#526)
        data = json.loads(result.output)
        assert "error" in data


class TestEvidenceChainCommand:
    def test_evidence_chain_not_found(self, populated_kb):
        result = runner.invoke(
            investigation_app,
            [
                "evidence-chain",
                "nonexistent",
                "-k",
                "test",
                "--json",
            ],
        )
        assert result.exit_code == 1  # an error answer is a failure in the exit too (#526)
        data = json.loads(result.output)
        assert "error" in data


class TestHelpText:
    def test_help(self):
        result = runner.invoke(investigation_app, ["--help"])
        assert result.exit_code == 0
        assert (
            "EXPERIMENTAL: Unsupported until the 0.28 alpha plugin/API contract." in result.output
        )
        assert "timeline" in result.output
        assert "entities" in result.output
        assert "sources" in result.output
        assert "claims" in result.output
        assert "network" in result.output
        assert "evidence-chain" in result.output


class TestPromoteClaimCommand:
    """#424: promote-claim's endpoint-field CLI options, wired end to end."""

    def _create_claim(self, svc):
        svc.create_entry(
            "test",
            "claim-x-owns-y",
            "X owns Y",
            "claim",
            body="Test claim body.",
            assertion="Entity X owns Entity Y",
            claim_status="corroborated",
            confidence="high",
            importance=7,
        )

    def test_promote_claim_without_endpoint_fields_is_refused(self, populated_kb):
        """No --owner/--asset: refused with a clear error, not a raw traceback.

        `--json` mode echoes the result dict and exits 0 regardless of
        "error" (matching the rest of this file's `network`/`evidence-chain`
        JSON-mode tests -- the error key, not the exit code, is the
        machine-readable signal); the non-JSON path is what exits 1, covered
        by test_promote_claim_dry_run_reports_the_same_refusal below.
        """
        self._create_claim(populated_kb["svc"])
        result = runner.invoke(
            investigation_app,
            ["promote-claim", "claim-x-owns-y", "--edge-type", "ownership", "-k", "test", "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "error" in data
        assert "owner" in data["error"] and "asset" in data["error"]

    def test_promote_claim_with_endpoint_fields_succeeds(self, populated_kb):
        """--owner/--asset supplied: promotion creates the edge entity."""
        self._create_claim(populated_kb["svc"])
        result = runner.invoke(
            investigation_app,
            [
                "promote-claim",
                "claim-x-owns-y",
                "--edge-type",
                "ownership",
                "-k",
                "test",
                "--owner",
                "[[entity-x]]",
                "--asset",
                "[[entity-y]]",
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "error" not in data
        assert "created" in data

    def test_promote_claim_dry_run_reports_the_same_refusal(self, populated_kb):
        """--dry-run without endpoint fields must also report the error (#424,
        coordinator note 1) -- it used to report success for a promotion that
        would fail for real. Non-JSON mode, where an error exits non-zero."""
        self._create_claim(populated_kb["svc"])
        result = runner.invoke(
            investigation_app,
            [
                "promote-claim",
                "claim-x-owns-y",
                "--edge-type",
                "ownership",
                "-k",
                "test",
                "--dry-run",
            ],
        )
        assert result.exit_code == 1
        assert "owner" in result.output and "asset" in result.output
        assert "Dry run" not in result.output

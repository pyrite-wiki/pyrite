"""Tests for investigation QA reporting and quality metrics."""

import json
from dataclasses import dataclass

import pytest
from pyrite_journalism_investigation.cli import investigation_app
from pyrite_journalism_investigation.plugin import JournalismInvestigationPlugin
from pyrite_journalism_investigation.qa import compute_qa_metrics
from typer.testing import CliRunner

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB

runner = CliRunner()


@pytest.fixture
def setup(tmp_path):
    """Set up a temporary KB with test data for QA reporting."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()
    kb = KBConfig(name="test", path=kb_path, kb_type="journalism-investigation")
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    svc = KBService(config, db)

    yield {"db": db, "svc": svc}
    db.close()


class TestSourceTierDistribution:
    def test_empty_kb(self, setup):
        metrics = compute_qa_metrics(setup["db"], "test")
        assert metrics["source_tiers"]["total"] == 0

    def test_source_tier_counts(self, setup):
        svc = setup["svc"]
        svc.create_entry("test", "src-1", "Source 1", "document_source", reliability="high")
        svc.create_entry("test", "src-2", "Source 2", "document_source", reliability="high")
        svc.create_entry("test", "src-3", "Source 3", "document_source", reliability="medium")
        svc.create_entry("test", "src-4", "Source 4", "document_source", reliability="low")

        metrics = compute_qa_metrics(setup["db"], "test")
        tiers = metrics["source_tiers"]
        assert tiers["total"] == 4
        assert tiers["high"] == 2
        assert tiers["medium"] == 1
        assert tiers["low"] == 1
        assert tiers["unknown"] == 0
        assert tiers["high_pct"] == 50.0


class TestClaimCoverage:
    def test_no_claims(self, setup):
        metrics = compute_qa_metrics(setup["db"], "test")
        assert metrics["claims"]["total"] == 0
        assert metrics["claims"]["coverage_pct"] == 100.0  # vacuously true

    def test_orphan_claims(self, setup):
        svc = setup["svc"]
        svc.create_entry("test", "claim-1", "Claim 1", "claim", assertion="X did Y")
        svc.create_entry(
            "test", "claim-2", "Claim 2", "claim", assertion="A paid B", evidence_refs=["[[ev-1]]"]
        )

        metrics = compute_qa_metrics(setup["db"], "test")
        assert metrics["claims"]["total"] == 2
        assert metrics["claims"]["orphans"] == 1
        assert metrics["claims"]["coverage_pct"] == 50.0

    def test_confidence_distribution(self, setup):
        svc = setup["svc"]
        svc.create_entry("test", "c-1", "C1", "claim", assertion="X", confidence="low")
        svc.create_entry("test", "c-2", "C2", "claim", assertion="Y", confidence="medium")
        svc.create_entry("test", "c-3", "C3", "claim", assertion="Z", confidence="high")

        metrics = compute_qa_metrics(setup["db"], "test")
        assert metrics["claims"]["confidence"]["low"] == 1
        assert metrics["claims"]["confidence"]["medium"] == 1
        assert metrics["claims"]["confidence"]["high"] == 1

    def test_disputed_ratio(self, setup):
        svc = setup["svc"]
        svc.create_entry("test", "c-1", "C1", "claim", assertion="X", claim_status="corroborated")
        svc.create_entry("test", "c-2", "C2", "claim", assertion="Y", claim_status="disputed")
        svc.create_entry("test", "c-3", "C3", "claim", assertion="Z", claim_status="retracted")

        metrics = compute_qa_metrics(setup["db"], "test")
        # 2 out of 3 are disputed or retracted
        assert metrics["claims"]["disputed_ratio"] == pytest.approx(66.67, abs=0.1)


class TestQualityScore:
    def test_perfect_score(self, setup):
        """All sources high, all claims covered, no disputes."""
        svc = setup["svc"]
        svc.create_entry("test", "src-1", "Source 1", "document_source", reliability="high")
        svc.create_entry(
            "test",
            "c-1",
            "Claim 1",
            "claim",
            assertion="X",
            evidence_refs=["[[ev-1]]"],
            claim_status="corroborated",
            confidence="high",
        )

        metrics = compute_qa_metrics(setup["db"], "test")
        assert metrics["quality_score"] >= 80

    def test_low_quality(self, setup):
        """All sources unknown, all claims orphans."""
        svc = setup["svc"]
        svc.create_entry("test", "src-1", "Source 1", "document_source", reliability="unknown")
        svc.create_entry("test", "c-1", "Claim 1", "claim", assertion="X")
        svc.create_entry("test", "c-2", "Claim 2", "claim", assertion="Y")

        metrics = compute_qa_metrics(setup["db"], "test")
        assert metrics["quality_score"] < 50

    def test_score_range(self, setup):
        """Score should be 0-100."""
        metrics = compute_qa_metrics(setup["db"], "test")
        assert 0 <= metrics["quality_score"] <= 100


class TestWarnings:
    def test_low_tier1_warning(self, setup):
        svc = setup["svc"]
        svc.create_entry("test", "src-1", "Source 1", "document_source", reliability="low")
        svc.create_entry("test", "src-2", "Source 2", "document_source", reliability="unknown")

        metrics = compute_qa_metrics(setup["db"], "test")
        assert any("tier-1" in w.lower() or "high" in w.lower() for w in metrics["warnings"])

    def test_high_orphan_warning(self, setup):
        svc = setup["svc"]
        svc.create_entry("test", "c-1", "C1", "claim", assertion="X")
        svc.create_entry("test", "c-2", "C2", "claim", assertion="Y")
        svc.create_entry("test", "c-3", "C3", "claim", assertion="Z", evidence_refs=["[[ev-1]]"])

        metrics = compute_qa_metrics(setup["db"], "test")
        assert any("orphan" in w.lower() for w in metrics["warnings"])


class TestMCPQAReportTool:
    def test_tool_registered(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("read")
        assert "investigation_qa_report" in tools

    def test_tool_returns_metrics(self, setup):
        svc = setup["svc"]
        svc.create_entry("test", "src-1", "Source 1", "document_source", reliability="high")

        @dataclass
        class Ctx:
            db: PyriteDB
            config: object = None

        plugin = JournalismInvestigationPlugin()
        plugin.set_context(Ctx(db=setup["db"]))
        tools = plugin.get_mcp_tools("read")
        result = tools["investigation_qa_report"]["handler"]({"kb_name": "test"})
        assert "quality_score" in result
        assert "source_tiers" in result
        assert result["source_tiers"]["high"] == 1


class TestCLIQACommand:
    def test_qa_json(self, setup, monkeypatch):
        svc = setup["svc"]
        svc.create_entry("test", "src-1", "Source 1", "document_source", reliability="high")
        monkeypatch.setattr(
            "pyrite_journalism_investigation.cli.load_config", lambda: setup["svc"].config
        )

        result = runner.invoke(investigation_app, ["qa", "-k", "test", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "quality_score" in data

    def test_qa_table(self, setup, monkeypatch):
        monkeypatch.setattr(
            "pyrite_journalism_investigation.cli.load_config", lambda: setup["svc"].config
        )
        result = runner.invoke(investigation_app, ["qa", "-k", "test"])
        assert result.exit_code == 0
        assert "Quality Score" in result.output


class TestStaleUnverifiedClaims:
    """--stale-days / stale_days count unverified claims by updated_at (#1566)."""

    def test_two_values_give_different_counts(self, setup):
        svc = setup["svc"]
        svc.create_entry("test", "c-1", "C1", "claim", assertion="X")
        svc.create_entry("test", "c-2", "C2", "claim", assertion="Y", claim_status="corroborated")

        fresh = compute_qa_metrics(setup["db"], "test", stale_days=30)
        now = compute_qa_metrics(setup["db"], "test", stale_days=0)

        assert fresh["claims"]["stale_unverified"] == 0
        assert not any("Stale" in w for w in fresh["warnings"])
        # Only the unverified claim counts; the corroborated one never does.
        assert now["claims"]["stale_unverified"] == 1
        assert "Stale unverified claims: 1 not updated in 0 days" in now["warnings"]

    def test_no_claims(self, setup):
        assert (
            compute_qa_metrics(setup["db"], "test", stale_days=0)["claims"]["stale_unverified"] == 0
        )

    @pytest.mark.parametrize(
        ("updated_at", "stale"),
        [
            ("2020-01-01T00:00:00+00:00", 1),
            ("2020-01-01T00:00:00", 1),  # naive: read as UTC
            (None, 1),  # missing: stale, as in `pyrite qa stale`
            ("not a date", 1),  # unparseable: stale, as in `pyrite qa stale`
            ("2999-01-01T00:00:00+00:00", 0),
        ],
    )
    def test_updated_at_shapes(self, updated_at, stale):
        class StubDB:
            def list_entries(self, kb_name, entry_type, limit):
                if entry_type != "claim":
                    return []
                return [{"metadata": {"assertion": "X"}, "updated_at": updated_at}]

        metrics = compute_qa_metrics(StubDB(), "test", stale_days=30)
        assert metrics["claims"]["stale_unverified"] == stale

    def test_mcp_argument_changes_the_answer(self, setup):
        setup["svc"].create_entry("test", "c-1", "C1", "claim", assertion="X")

        @dataclass
        class Ctx:
            db: PyriteDB
            config: object = None

        plugin = JournalismInvestigationPlugin()
        plugin.set_context(Ctx(db=setup["db"]))
        handler = plugin.get_mcp_tools("read")["investigation_qa_report"]["handler"]

        assert handler({"kb_name": "test"})["claims"]["stale_unverified"] == 0
        assert handler({"kb_name": "test", "stale_days": 0})["claims"]["stale_unverified"] == 1

    def test_cli_option_changes_the_answer(self, setup, monkeypatch):
        setup["svc"].create_entry("test", "c-1", "C1", "claim", assertion="X")
        monkeypatch.setattr(
            "pyrite_journalism_investigation.cli.load_config", lambda: setup["svc"].config
        )

        def stale(*extra):
            result = runner.invoke(investigation_app, ["qa", "-k", "test", "--json", *extra])
            assert result.exit_code == 0, result.output
            return json.loads(result.output)["claims"]["stale_unverified"]

        assert stale() == 0
        assert stale("--stale-days", "0") == 1

        table = runner.invoke(investigation_app, ["qa", "-k", "test", "--stale-days", "0"])
        assert table.exit_code == 0
        assert "Unverified, not updated in 0 days: 1" in table.output

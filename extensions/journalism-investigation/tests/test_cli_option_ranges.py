"""Tests verifying numeric CLI options in journalism-investigation reject out-of-range values (#1562)."""

import pytest
from pyrite_journalism_investigation.cli import investigation_app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.mark.parametrize(
    ("args", "expected_err"),
    [
        # timeline: --min-importance (0-10), --limit (min 1)
        (["timeline", "--kb", "test", "--min-importance", "-1"], "--min-importance"),
        (["timeline", "--kb", "test", "--min-importance", "11"], "--min-importance"),
        (["timeline", "--kb", "test", "--limit", "0"], "--limit"),
        (["timeline", "--kb", "test", "--limit", "-5"], "--limit"),
        # entities: --min-importance (0-10), --limit (min 1)
        (["entities", "--kb", "test", "--min-importance", "-1"], "--min-importance"),
        (["entities", "--kb", "test", "--min-importance", "11"], "--min-importance"),
        (["entities", "--kb", "test", "--limit", "0"], "--limit"),
        # sources: --limit (min 1)
        (["sources", "--kb", "test", "--limit", "0"], "--limit"),
        (["sources", "--kb", "test", "--limit", "-1"], "--limit"),
        # claims: --min-importance (0-10), --limit (min 1)
        (["claims", "--kb", "test", "--min-importance", "-1"], "--min-importance"),
        (["claims", "--kb", "test", "--min-importance", "11"], "--min-importance"),
        (["claims", "--kb", "test", "--limit", "0"], "--limit"),
        # qa: --stale-days (min 0)
        (["qa", "--kb", "test", "--stale-days", "-1"], "--stale-days"),
        # search: --limit (min 1)
        (["search", "query", "--limit", "0"], "--limit"),
        # ownership: --depth (min 1)
        (["ownership", "ent1", "--kb", "test", "--depth", "0"], "--depth"),
        (["ownership", "ent1", "--kb", "test", "--depth", "-2"], "--depth"),
        # money-flow: --hops (min 1)
        (["money-flow", "ent1", "--kb", "test", "--hops", "0"], "--hops"),
        (["money-flow", "ent1", "--kb", "test", "--hops", "-1"], "--hops"),
        # export: --min-importance (0-10)
        (["export", "--kb", "test", "--min-importance", "-1"], "--min-importance"),
        (["export", "--kb", "test", "--min-importance", "11"], "--min-importance"),
        # dedup: --threshold (0.0-1.0)
        (["dedup", "--threshold", "-0.01"], "--threshold"),
        (["dedup", "--threshold", "1.01"], "--threshold"),
    ],
)
def test_investigation_numeric_options_reject_out_of_range(args, expected_err):
    result = runner.invoke(investigation_app, args)
    assert result.exit_code == 2
    assert expected_err in result.output

"""Tests for QA assess numeric option ranges (#1564, #1541)."""

import json

import pytest
import typer

from pyrite.cli.qa_commands import qa_app
from pyrite.config import KBConfig
from pyrite.services.qa_service import QA_TIERS


@pytest.fixture
def qa_world(cli_world, tmp_path):
    return cli_world(kbs=[KBConfig(name="qa_kb", path=tmp_path / "qa_kb", kb_type="plain")])


def test_qa_assess_tier_declared_range_matches_qa_tiers():
    cmd = typer.main.get_command(qa_app)
    assess = cmd.get_command(None, "assess")
    param = next(p for p in assess.params if p.name == "tier")
    assert (param.type.min, param.type.max) == (min(QA_TIERS), max(QA_TIERS))


@pytest.mark.cli
@pytest.mark.parametrize("max_age", [-1, -5])
def test_qa_assess_rejects_negative_max_age(qa_world, max_age):
    result = qa_world.cli("qa", "assess", "qa_kb", "--max-age", str(max_age))
    assert result.exit_code == 2, result.output
    error = json.loads(result.stdout)
    assert error["error_code"] == "USAGE_ERROR"
    assert "--max-age" in error["error"]


@pytest.mark.cli
@pytest.mark.parametrize("tier", [0, 3, -1])
def test_qa_assess_rejects_out_of_range_tier(qa_world, tier):
    result = qa_world.cli("qa", "assess", "qa_kb", "--tier", str(tier))
    assert result.exit_code == 2, result.output
    error = json.loads(result.stdout)
    assert error["error_code"] == "USAGE_ERROR"
    assert "--tier" in error["error"]


@pytest.mark.cli
def test_qa_assess_accepts_valid_max_age_and_tier_1(qa_world):
    result = qa_world.cli("qa", "assess", "qa_kb", "--max-age", "0", "--tier", "1")
    assert result.exit_code == 0, result.output

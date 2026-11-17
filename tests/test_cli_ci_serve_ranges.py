"""Tests for pyrite ci and pyrite serve numeric option ranges (#1563, #1541)."""

import json
import os

if not hasattr(os, "getuid"):
    os.getuid = lambda: 0
if not hasattr(os, "O_NONBLOCK"):
    os.O_NONBLOCK = 0

import pytest
from pyrite.config import KBConfig


@pytest.fixture
def ci_world(cli_world, tmp_path):
    return cli_world(kbs=[KBConfig(name="ci_kb", path=tmp_path / "ci_kb", kb_type="plain")])


@pytest.mark.cli
@pytest.mark.parametrize("port", [0, -1, 65536])
def test_serve_rejects_out_of_range_port(ci_world, port):
    result = ci_world.cli("serve", "--port", str(port))
    assert result.exit_code == 2, result.output
    error = json.loads(result.stdout)
    assert error["error_code"] == "USAGE_ERROR"
    assert "--port" in error["error"]


@pytest.mark.cli
@pytest.mark.parametrize("port", [1, 65535])
def test_serve_accepts_port_boundaries(ci_world, port, monkeypatch):
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: None)
    result = ci_world.cli("serve", "--port", str(port))
    assert result.exit_code == 0, result.output


@pytest.mark.cli
@pytest.mark.parametrize("tier", [0, 3, -1])
def test_ci_rejects_out_of_range_tier(ci_world, tier):
    result = ci_world.cli("ci", "--tier", str(tier))
    assert result.exit_code == 2, result.output
    error = json.loads(result.stdout)
    assert error["error_code"] == "USAGE_ERROR"
    assert "--tier" in error["error"]


@pytest.mark.cli
def test_ci_accepts_tier_1(ci_world):
    result = ci_world.cli("ci", "--tier", "1")
    assert result.exit_code == 0, result.output


@pytest.mark.cli
def test_ci_tier_2_without_model_is_refused_with_ai_not_configured(ci_world):
    result = ci_world.cli("ci", "--tier", "2")
    # Tier 2 without model is refused with AI_NOT_CONFIGURED (exit 1), not a usage error (exit 2)
    assert result.exit_code == 1, result.output
    error = json.loads(result.stdout)
    assert error["error_code"] == "AI_NOT_CONFIGURED"

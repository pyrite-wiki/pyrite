"""JSON error answers must refuse the requested operation (#787)."""

import json

import pytest
from pyrite_journalism_investigation import cli
from typer.testing import CliRunner

from pyrite.config import PyriteConfig, Settings
from pyrite.storage.database import PyriteDB


@pytest.fixture
def config(tmp_path, monkeypatch):
    config = PyriteConfig(settings=Settings(index_path=tmp_path / "index.db"))
    with PyriteDB(config.settings.index_path) as db:
        db.register_kb("demo", "journalism-investigation", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: config)
    return config


@pytest.mark.parametrize(
    "args, expected",
    [
        (
            ["promote-claim", "nope", "-k", "demo", "--edge-type", "ownership"],
            "Claim not found: nope",
        ),
        (["start", "--title", "X", "-k", "no-such-kb"], "KB 'no-such-kb' not found"),
    ],
)
def test_json_error_answer_exits_one(config, args, expected):
    result = CliRunner().invoke(cli.investigation_app, [*args, "--json"])
    payload = json.loads(result.stdout)
    assert payload["error"] == expected
    assert result.exit_code == 1, result.output


@pytest.mark.control(reason="rich mode already refuses these error answers")
@pytest.mark.parametrize(
    "args",
    [
        ["promote-claim", "nope", "-k", "demo", "--edge-type", "ownership"],
        ["start", "--title", "X", "-k", "no-such-kb"],
    ],
)
def test_rich_errors_still_exit_one(config, args):
    result = CliRunner().invoke(cli.investigation_app, args)
    assert result.exit_code == 1, result.output
    assert "not found" in result.stdout


@pytest.mark.control(reason="successful JSON start already creates a row and exits zero")
def test_successful_start_keeps_json_and_side_effect(config):
    result = CliRunner().invoke(
        cli.investigation_app, ["start", "--title", "X", "-k", "demo", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["title"] == "X"
    with PyriteDB(config.settings.index_path) as db:
        assert db.get_entry(payload["created"], "demo")["title"] == "X"

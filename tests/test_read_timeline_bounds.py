"""Regression tests for pyrite-read timeline argument bounds (#1412)."""

import json

import pytest
from typer.testing import CliRunner

from pyrite.read_cli import app as read_app

runner = CliRunner()


class _FakeDB:
    def close(self):
        pass


class _TimelineService:
    def get_timeline(self, *, date_from=None, date_to=None, min_importance=1):
        events = [
            {
                "date": "2026-01-01",
                "title": "Low",
                "importance": 3,
                "kb_name": "test",
            },
            {
                "date": "2026-02-01",
                "title": "Medium",
                "importance": 5,
                "kb_name": "test",
            },
            {
                "date": "2026-03-01",
                "title": "High",
                "importance": 10,
                "kb_name": "test",
            },
        ]
        return [event for event in events if event["importance"] >= min_importance]


@pytest.fixture
def timeline_cli(monkeypatch):
    import pyrite.read_cli as read_cli

    monkeypatch.setattr(
        read_cli,
        "_get_svc",
        lambda *kbs, output_format="rich": (_TimelineService(), _FakeDB()),
    )
    return read_app


def _usage_error(output):
    return json.loads(next(line for line in output.splitlines() if line.startswith("{")))


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--limit", "-1"),
        ("--limit", "0"),
        ("--min-importance", "0"),
        ("--min-importance", "11"),
    ],
)
def test_timeline_rejects_out_of_range_arguments(timeline_cli, option, value):
    result = runner.invoke(
        timeline_cli,
        ["timeline", option, value, "--format", "json"],
    )

    assert result.exit_code == 2, result.output
    document = _usage_error(result.output)
    assert document["error_code"] == "USAGE_ERROR"
    assert option in document["error"]
    assert "Traceback" not in result.output


@pytest.mark.control(
    reason="limit=1 is a valid boundary value; it documents accepted behavior rather than the regression"
)
def test_timeline_limit_one_returns_exactly_one_event(timeline_cli):
    result = runner.invoke(
        timeline_cli,
        ["timeline", "--limit", "1", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["count"] == 1
    assert len(document["events"]) == 1


@pytest.mark.control(
    reason="importance=10 is a valid upper boundary; invalid values are covered by the regression parametrization"
)
def test_timeline_min_importance_ten_is_valid(timeline_cli):
    result = runner.invoke(
        timeline_cli,
        ["timeline", "--min-importance", "10", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["count"] == 1
    assert document["events"][0]["importance"] == 10


@pytest.mark.control(
    reason="the CLI intentionally has no arbitrary upper limit; the REST API's separate cap does not apply"
)
def test_timeline_limit_has_no_arbitrary_upper_bound(timeline_cli):
    result = runner.invoke(
        timeline_cli,
        ["timeline", "--limit", "501", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["count"] == 3

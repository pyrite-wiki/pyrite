"""A refused importance is never shown silently (#1761, maintainer decision C).

A file whose ``importance`` the one rule refuses (7.5, 0, 11, "high", NaN)
keeps its legacy reading for readers (the index holds a whole number). The
property: ``qa validate`` reports it as ``importance_range`` with the
whole-number 1-10 message and no rename advice, and every reader flags it:
``get`` and ``search`` JSON carry a warning on that entry, CSV writes a note
to stderr. An accepted spelling (5.0, "05") gets no finding and no warning.
"""

from __future__ import annotations

import json

import pytest

from pyrite.config import KBConfig
from tests._world import build_world

REFUSED = ["7.5", "0", "11", "high", ".nan"]
ACCEPTED = ["5.0", '"05"']


def _note(value: str) -> str:
    return f"---\nid: n\ntype: note\ntitle: Zebra marker\nimportance: {value}\n---\n\nzebra body.\n"


@pytest.fixture
def make_world(tmp_path):
    worlds = []

    def make(value: str):
        w = build_world(tmp_path, kbs=[KBConfig(name="k", path=tmp_path / "k")], rest=False)
        worlds.append(w)
        w.add_entry("k", _note(value))
        w.index("k")
        return w

    yield make
    for w in worlds:
        w.close()


def _range_findings(world):
    result = world.cli("qa", "validate", "k")
    assert result.exit_code == 0, result.output
    issues = json.loads(result.stdout)["issues"]
    return [i for i in issues if i["field"] == "importance"]


@pytest.mark.parametrize("value", REFUSED)
def test_qa_reports_refused_importance_as_range_without_rename_advice(make_world, value):
    findings = _range_findings(make_world(value))
    assert [f["rule"] for f in findings] == ["importance_range"], findings
    message = findings[0]["message"]
    assert "whole number" in message and "1-10" in message
    assert "rename" not in message.lower()


@pytest.mark.parametrize("value", ACCEPTED)
def test_qa_accepted_importance_has_no_finding(make_world, value):
    assert _range_findings(make_world(value)) == []


@pytest.mark.parametrize("value", REFUSED)
def test_get_json_warns_on_refused_importance(make_world, value):
    result = make_world(value).cli("get", "n", "-k", "k")
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert any("importance" in w and "1-10" in w for w in body["warnings"]), body


@pytest.mark.parametrize("value", ACCEPTED)
def test_get_json_accepted_importance_has_no_warning(make_world, value):
    body = json.loads(make_world(value).cli("get", "n", "-k", "k").stdout)
    assert not body.get("warnings")


@pytest.mark.parametrize("value", REFUSED)
def test_search_json_warns_on_that_entry(make_world, value):
    result = make_world(value).cli("search", "zebra", "-k", "k")
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)["results"]
    assert len(rows) == 1
    assert any("importance" in w and "1-10" in w for w in rows[0]["warnings"]), rows[0]


@pytest.mark.parametrize("value", ACCEPTED)
def test_search_json_accepted_importance_has_no_warning(make_world, value):
    rows = json.loads(make_world(value).cli("search", "zebra", "-k", "k").stdout)["results"]
    assert len(rows) == 1 and not rows[0].get("warnings")


@pytest.mark.parametrize("value", REFUSED)
def test_csv_writes_a_stderr_note(make_world, value):
    world = make_world(value)
    for args in (("get", "n", "-k", "k"), ("search", "zebra", "-k", "k")):
        result = world.cli(*args, "--format", "csv")
        assert result.exit_code == 0, result.output
        assert "importance" in result.stderr and "1-10" in result.stderr, args


@pytest.mark.parametrize("value", ACCEPTED)
def test_csv_accepted_importance_writes_no_note(make_world, value):
    result = make_world(value).cli("get", "n", "-k", "k", "--format", "csv")
    assert "importance" not in result.stderr


@pytest.mark.control(reason="readers keep the legacy reading: a whole number, unchanged")
def test_readers_keep_the_legacy_reading(make_world):
    body = json.loads(make_world("7.5").cli("get", "n", "-k", "k").stdout)
    assert body["importance"] == 7 and isinstance(body["importance"], int)

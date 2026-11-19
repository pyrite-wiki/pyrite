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


def _note(value: str | None) -> str:
    """A note; ``None`` leaves the ``importance`` key out of the file."""
    line = "" if value is None else f"importance: {value}\n"
    return f"---\nid: n\ntype: note\ntitle: Zebra marker\n{line}---\n\nzebra body.\n"


@pytest.fixture
def make_world(tmp_path):
    worlds = []

    def make(value: str | None):
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


@pytest.mark.control(reason="negative control: an accepted spelling was never flagged")
@pytest.mark.parametrize("value", ACCEPTED)
def test_qa_accepted_importance_has_no_finding(make_world, value):
    assert _range_findings(make_world(value)) == []


@pytest.mark.parametrize("value", REFUSED)
def test_get_json_warns_on_refused_importance(make_world, value):
    result = make_world(value).cli("get", "n", "-k", "k")
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert any("importance" in w and "1-10" in w for w in body["entry_warnings"]), body


@pytest.mark.control(reason="negative control: an accepted spelling was never flagged")
@pytest.mark.parametrize("value", ACCEPTED)
def test_get_json_accepted_importance_has_no_warning(make_world, value):
    body = json.loads(make_world(value).cli("get", "n", "-k", "k").stdout)
    assert not body.get("entry_warnings")


@pytest.mark.parametrize("value", REFUSED)
def test_search_json_warns_on_that_entry(make_world, value):
    result = make_world(value).cli("search", "zebra", "-k", "k")
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)["results"]
    assert len(rows) == 1
    assert any("importance" in w and "1-10" in w for w in rows[0]["entry_warnings"]), rows[0]


@pytest.mark.control(reason="negative control: an accepted spelling was never flagged")
@pytest.mark.parametrize("value", ACCEPTED)
def test_search_json_accepted_importance_has_no_warning(make_world, value):
    rows = json.loads(make_world(value).cli("search", "zebra", "-k", "k").stdout)["results"]
    assert len(rows) == 1 and not rows[0].get("entry_warnings")


@pytest.mark.parametrize("value", REFUSED)
def test_csv_writes_a_stderr_note(make_world, value):
    world = make_world(value)
    for args in (("get", "n", "-k", "k"), ("search", "zebra", "-k", "k")):
        result = world.cli(*args, "--format", "csv")
        assert result.exit_code == 0, result.output
        assert "importance" in result.stderr and "1-10" in result.stderr, args


@pytest.mark.control(reason="negative control: an accepted spelling was never flagged")
@pytest.mark.parametrize("value", ACCEPTED)
def test_csv_accepted_importance_writes_no_note(make_world, value):
    result = make_world(value).cli("get", "n", "-k", "k", "--format", "csv")
    assert "importance" not in result.stderr


@pytest.mark.control(reason="readers keep the legacy reading: a whole number, unchanged")
def test_readers_keep_the_legacy_reading(make_world):
    body = json.loads(make_world("7.5").cli("get", "n", "-k", "k").stdout)
    assert body["importance"] == 7 and isinstance(body["importance"], int)


def test_a_file_linked_out_of_the_kb_root_shows_no_importance_sentence(make_world, tmp_path):
    """A row whose file now resolves outside its KB's root is not the KB's to
    read: no sentence names what a file outside the root holds."""
    world = make_world("5")
    outside = tmp_path / "outside.md"
    outside.write_text(_note("7.5"))
    entry = world.kb_path("k") / "n.md"
    entry.unlink()
    entry.symlink_to(outside)

    from pyrite.services.importance_reading import file_importance

    from pyrite.services.importance_reading import ABSENT

    assert file_importance(world.kb_path("k"), entry) is ABSENT
    search = world.cli("search", "zebra", "-k", "k")
    assert search.exit_code == 0, search.output
    for row in json.loads(search.stdout)["results"]:
        assert not row.get("entry_warnings"), row
    qa = world.cli("qa", "validate", "k")
    assert "7.5" not in qa.stdout


def _entry_findings(world):
    result = world.cli("qa", "validate", "k", "--entry", "n")
    assert result.exit_code == 0, result.output
    return [i for i in json.loads(result.stdout)["issues"] if i["field"] == "importance"]


@pytest.mark.parametrize("value", REFUSED)
def test_qa_single_entry_reports_refused_importance_as_range(make_world, value):
    findings = _entry_findings(make_world(value))
    assert [f["rule"] for f in findings] == ["importance_range"], findings
    message = findings[0]["message"]
    assert "whole number" in message and "1-10" in message
    assert "rename" not in message.lower()


@pytest.mark.parametrize("value", ACCEPTED)
@pytest.mark.control(reason="negative control: an accepted spelling was never flagged")
def test_qa_single_entry_accepted_importance_has_no_finding(make_world, value):
    assert _entry_findings(make_world(value)) == []


@pytest.mark.control(reason="negative control: a missing key was never flagged")
def test_a_missing_importance_key_says_nothing_on_every_surface(make_world):
    world = make_world(None)
    assert _range_findings(world) == []
    assert _entry_findings(world) == []
    got = json.loads(world.cli("get", "n", "-k", "k").stdout)
    assert not got.get("entry_warnings") and "warnings" not in got
    rows = json.loads(world.cli("search", "zebra", "-k", "k").stdout)["results"]
    assert len(rows) == 1 and not rows[0].get("entry_warnings")
    for args in (("get", "n", "-k", "k"), ("search", "zebra", "-k", "k")):
        result = world.cli(*args, "--format", "csv")
        assert result.exit_code == 0 and "1-10" not in result.stderr, args


@pytest.mark.parametrize("value", ["{level: high}", "[high, low]"])
def test_a_mapping_or_list_importance_keeps_the_rename_advice(make_world, value):
    """Not a scalar the rule refuses: a key used for something else."""
    world = make_world(value)
    for args in (("qa", "validate", "k"), ("qa", "validate", "k", "--entry", "n")):
        result = world.cli(*args)
        assert result.exit_code == 0, result.output
        issues = [i for i in json.loads(result.stdout)["issues"] if i["field"] == "importance"]
        assert [i["rule"] for i in issues] == ["reserved_key_collision"], (args, issues)
        assert "rename" in issues[0]["message"].lower()

"""A QA entry filter resolves its KB instead of validating unrelated entries."""

import json

import pytest

from pyrite.config import KBConfig
from tests._world import build_world


@pytest.fixture
def world(tmp_path):
    result = build_world(
        tmp_path,
        kbs=[KBConfig(name=name, path=tmp_path / name) for name in ("alpha", "beta")],
        rest=False,
    )
    for kb, entry_id in [("alpha", "target"), ("alpha", "other"), ("beta", "other")]:
        result.add_entry(kb, f"---\nid: {entry_id}\ntype: note\ntitle: {entry_id}\n---\n\nBody.\n")
    try:
        yield result
    finally:
        result.close()


def test_entry_filter_without_kb_only_validates_the_named_entry(world):
    result = world.cli("qa", "validate", "--entry", "target")

    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["count"] > 0
    assert {(i["kb_name"], i["entry_id"]) for i in body["issues"]} == {("alpha", "target")}


@pytest.mark.parametrize(("entry_id", "code"), [("other", "AMBIGUOUS"), ("absent", "NOT_FOUND")])
def test_unqualified_entry_filter_refuses_ambiguous_or_missing_ids(world, entry_id, code):
    result = world.cli("qa", "validate", "--entry", entry_id)

    assert result.exit_code == 1, result.output
    body = json.loads(result.stdout)
    assert body["error_code"] == code, body
    if code == "AMBIGUOUS":
        assert body["candidate_kbs"] == ["alpha", "beta"]


@pytest.mark.control(reason="an explicit KB and validation without an entry keep their scopes")
def test_named_kb_and_all_kbs_still_work(world):
    named = world.cli("qa", "validate", "beta", "--entry", "other")
    assert named.exit_code == 0, named.output
    assert {(i["kb_name"], i["entry_id"]) for i in json.loads(named.stdout)["issues"]} == {
        ("beta", "other")
    }
    all_kbs = world.cli("qa", "validate")
    assert all_kbs.exit_code == 0, all_kbs.output
    assert {i["kb_name"] for i in json.loads(all_kbs.stdout)["issues"]} == {"alpha", "beta"}


@pytest.mark.control(reason="QA still diagnoses a missing entry when its KB is explicitly named")
def test_named_kb_keeps_the_qa_missing_entry_diagnostic(world):
    result = world.cli("qa", "validate", "alpha", "--entry", "absent")

    assert result.exit_code == 1, result.output
    body = json.loads(result.stdout)
    assert body["issues"][0]["rule"] == "entry_not_found"

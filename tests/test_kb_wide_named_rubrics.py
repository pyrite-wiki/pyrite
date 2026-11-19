"""Whole-KB validation runs the same named rubric as entry validation (#956)."""

import json

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings, save_config
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.qa_service import QAService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager


@pytest.fixture
def world(tmp_path):
    opened = []

    def build(items, *, role=None, tagged=True, rubric_action=None):
        folder = tmp_path / "kb"
        folder.mkdir()
        kb_data = {"name": "notes", "evaluation_rubric": items}
        if rubric_action is not None:
            kb_data["rubric_action"] = rubric_action
        (folder / "kb.yaml").write_text(json.dumps(kb_data), encoding="utf-8")
        role_line = f"role: {role}\n" if role else ""
        (folder / "a.md").write_text(
            "---\nid: a\ntype: note\ntitle: Harbour field survey\n"
            + ("tags: [survey]\n" if tagged else "")
            + role_line
            + "---\nA stub note.\n",
            encoding="utf-8",
        )
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="notes", path=folder, kb_type="generic")],
            settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
        )
        db = PyriteDB(config.settings.index_path)
        opened.append(db)
        IndexManager(db, config).index_all()
        return QAService(config, db), config

    yield build
    for db in opened:
        db.close()


HAS_ROLE = {"text": "Has a role", "checker": "has_field", "params": {"field": "role"}}


def test_kb_wide_named_failure_matches_single_entry_and_cli(world):
    qa, config = world([HAS_ROLE])
    folder = config.get_kb("notes").path
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    single = qa.validate_entry("a", "notes", readable_kbs=UNSCOPED)
    expected = [i for i in single["issues"] if i.get("rubric_item") == "Has a role"]
    assert len(expected) == 1
    all_entries = qa.validate_kb("notes", readable_kbs=UNSCOPED)
    assert [i for i in all_entries["issues"] if i.get("rubric_item") == "Has a role"] == expected
    save_config(config)
    result = CliRunner().invoke(app, ["qa", "validate", "notes", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert "Has a role" in result.stdout
    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before


@pytest.mark.parametrize("role", ["researcher", None])
def test_named_pass_and_covered_items_do_not_invent_issues(world, role):
    item = dict(HAS_ROLE)
    if role is None:
        item["covered_by"] = "separate_checker"
    qa, _ = world([item], role=role)
    result = qa.validate_kb("notes", readable_kbs=UNSCOPED)
    assert not [i for i in result["issues"] if i.get("rubric_item") == "Has a role"]


@pytest.mark.parametrize(
    ("item", "rule"),
    [
        ({"text": "Unknown", "checker": "not_a_real_checker"}, "config_error"),
        (
            {"text": "Broken", "checker": "body_has_pattern", "params": {"pattern": "("}},
            "checker_failed",
        ),
    ],
)
def test_unchecked_or_broken_named_items_are_reported(world, item, rule):
    qa, _ = world([item])
    result = qa.validate_kb("notes", readable_kbs=UNSCOPED)
    assert any(i["rule"] == rule for i in result["issues"])


def test_bulk_and_named_checks_do_not_duplicate_the_same_finding(world):
    qa, _ = world([], tagged=False)
    result = qa.validate_kb("notes", readable_kbs=UNSCOPED)
    assert len([i for i in result["issues"] if i.get("field") == "tags"]) == 1


@pytest.mark.parametrize(
    ("setting", "item_action", "expected_exit"),
    [
        (None, None, 1),
        ("fail", None, 1),
        ("warn", None, 0),
        ("fail", "warn", 0),
        ("warn", "fail", 1),
    ],
)
def test_ci_respects_named_rubric_warn_or_fail(world, setting, item_action, expected_exit):
    item = dict(HAS_ROLE)
    if item_action:
        item["action"] = item_action
    qa, config = world([item], rubric_action=setting)
    save_config(config)
    result = CliRunner().invoke(app, ["ci", "--kb", "notes", "--format", "json"])
    assert result.exit_code == expected_exit, result.output
    data = json.loads(result.stdout)
    named = [issue for issue in data["kbs"][0]["issues"] if issue.get("field") == "metadata.role"]
    assert len(named) == 1, data
    assert named[0]["message"] == "Entry 'a' is missing 'role' in metadata"
    assert named[0]["rubric_action"] == (item_action or setting or "fail")
    if expected_exit == 0:
        assert data["result"] == "pass"
    else:
        assert data["result"] == "fail"


def test_ci_text_output_reports_findings_and_exits_zero_on_warn(world):
    qa, config = world([HAS_ROLE], rubric_action="warn")
    save_config(config)
    result = CliRunner().invoke(app, ["ci", "--kb", "notes"])
    assert result.exit_code == 0, result.output
    assert "WARNING:" in result.output
    assert "Entry 'a' is missing 'role'" in result.output
    assert "1 warnings" in result.output


def test_ci_broken_check_still_fails_even_with_warn_action(world):
    broken_item = {"text": "Broken", "checker": "body_has_pattern", "params": {"pattern": "("}}
    qa, config = world([broken_item], rubric_action="warn")
    save_config(config)
    result = CliRunner().invoke(app, ["ci", "--kb", "notes"])
    assert result.exit_code == 1, result.output
    assert "checker_failed" in result.output or "failed on this entry" in result.output


def test_warn_does_not_downgrade_preexisting_bulk_findings(world):
    qa, config = world([HAS_ROLE], tagged=False, rubric_action="warn")
    save_config(config)
    result = CliRunner().invoke(app, ["ci", "--kb", "notes", "--format", "json"])
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    tags = [issue for issue in data["kbs"][0]["issues"] if issue.get("field") == "tags"]
    assert len(tags) == 1, tags
    assert tags[0].get("rubric_action") != "warn"


@pytest.mark.parametrize("key", ["rubric_action", "evaluation_rubric[0].action"])
@pytest.mark.parametrize("value", ["ignore", None, 0, True, "WARN"])
def test_invalid_rubric_action_is_a_named_config_error(world, key, value):
    qa, config = world([HAS_ROLE])
    folder = config.get_kb("notes").path
    data = json.loads((folder / "kb.yaml").read_text())
    if key == "rubric_action":
        data["rubric_action"] = value
    else:
        data["evaluation_rubric"][0]["action"] = value
    (folder / "kb.yaml").write_text(json.dumps(data))
    config.get_kb("notes").invalidate_schema_cache()
    save_config(config)
    result = CliRunner().invoke(app, ["ci", "--kb", "notes", "--format", "json"])
    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    errors = [issue for issue in report["kbs"][0]["issues"] if issue["rule"] == "config_error"]
    assert any(
        key in issue["message"] and "warn" in issue["message"] and "fail" in issue["message"]
        for issue in errors
    ), errors

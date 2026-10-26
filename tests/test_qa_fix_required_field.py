"""#958: `qa fix` offered a missing-field fix no validator could produce.

`QAFixService` matched `"missing"` in an issue's message and knew one default,
`importance: 5`. The KB schema's required-field errors say `required`
(`kb_schema.py`), and the index always carries the model's own `importance`
default, so no issue could name a field that fix handled. The kind is gone: a
required field is the author's to fill, not a value `qa fix` may invent.
"""

import pytest

from pyrite.services.access_policy import UNSCOPED
from pyrite.services.qa_fix_service import QAFixService
from pyrite.services.qa_service import QAService


def test_the_missing_field_fix_kind_is_gone():
    assert "schema_violation" not in QAFixService.FIXABLE_RULES
    assert "schema_violation" not in QAFixService.FIX_KINDS


def test_the_shape_the_removed_branch_matched_is_not_fixed(cli_world):
    """The branch's one shape: a message that says "missing". `qa fix` no
    longer writes a value the author did not choose, whatever the issue's
    text (#958)."""
    world = cli_world()
    issue = {
        "entry_id": "loose",
        "kb_name": "test-kb",
        "rule": "schema_violation",
        "field": "importance",
        "severity": "error",
        "message": "missing field importance",
    }

    report = QAFixService(world.config, world.db).fix_kb(
        "test-kb", dry_run=True, validate_kb_fn=lambda kb: {"issues": [issue]}
    )

    assert report["fixed"] == [], report
    assert [manual["reason"] for manual in report["manual"]] == ["not_auto_fixable"], report


def test_a_required_field_issue_is_manual_and_writes_nothing(cli_world):
    world = cli_world()
    kb_path = world.kb_path("test-kb")
    (kb_path / "kb.yaml").write_text(
        "name: test-kb\ntypes:\n  note:\n    required: [title, reviewer]\n",
        encoding="utf-8",
    )
    note = kb_path / "loose.md"
    note.write_text(
        "---\nid: loose\ntitle: Loose\ntype: note\n---\n\nBody.\n",
        encoding="utf-8",
    )
    world.index("test-kb")
    before = note.read_text(encoding="utf-8")

    qa = QAService(world.config, world.db)
    issues = qa.validate_kb("test-kb", readable_kbs=UNSCOPED)["issues"]
    assert [
        (issue["entry_id"], issue["field"], issue["severity"])
        for issue in issues
        if issue["rule"] == "schema_violation"
    ] == [("loose", "reviewer", "error")]

    report = qa.fix_kb(
        "test-kb", dry_run=False, fix_rules=["schema_violation"], readable_kbs=UNSCOPED
    )

    assert report["fixed"] == [], report
    assert [(issue["entry_id"], issue["field"], issue["reason"]) for issue in report["manual"]] == [
        ("loose", "reviewer", "no_safe_default")
    ], report["manual"]
    assert note.read_text(encoding="utf-8") == before


def _validate(world):
    kb_path = world.kb_path("test-kb")
    (kb_path / "kb.yaml").write_text(
        "name: test-kb\ntypes:\n  note:\n    required: [title, reviewer]\n    fields:\n"
        "      kind:\n        type: select\n        options: [a, b]\n"
        "      tone:\n        type: select\n        options: [x, y]\n        allow_other: true\n",
        encoding="utf-8",
    )
    (kb_path / "loose.md").write_text(
        "---\nid: loose\ntitle: Loose\ntype: note\nkind: zzz\ntone: odd\n---\n\nBody.\n",
        encoding="utf-8",
    )
    world.index("test-kb")
    qa = QAService(world.config, world.db)
    return qa.validate_kb("test-kb", readable_kbs=UNSCOPED)["issues"]


def test_schema_errors_and_warnings_name_their_rule_in_the_same_key(cli_world):
    """`qa validate` says which rule a schema issue broke in `schema_rule`, on
    errors and warnings alike, so no caller matches words in the message (#958)."""
    issues = [i for i in _validate(cli_world()) if i["rule"] == "schema_violation"]

    assert {i["severity"] for i in issues} >= {"error"}, issues
    for issue in issues:
        assert issue["schema_rule"], issue
    by_field = {(i["field"], i["severity"]): i["schema_rule"] for i in issues}
    assert by_field[("reviewer", "error")] == "required", by_field
    warnings = [i for i in issues if i["severity"] == "warning"]
    assert [i["field"] for i in warnings] == ["tone"], issues
    assert warnings[0]["schema_rule"], warnings


@pytest.mark.control(reason="before the key existed no issue carried it; this pins its absence")
def test_a_validator_that_names_no_rule_adds_no_schema_rule_key(cli_world, monkeypatch):
    from pyrite.schema.kb_schema import KBSchema

    world = cli_world()
    kb_path = world.kb_path("test-kb")
    (kb_path / "kb.yaml").write_text(
        "name: test-kb\ntypes:\n  note:\n    required: [title]\n", encoding="utf-8"
    )
    (kb_path / "loose.md").write_text(
        "---\nid: loose\ntitle: Loose\ntype: note\n---\n\nBody.\n", encoding="utf-8"
    )
    world.index("test-kb")
    monkeypatch.setattr(
        KBSchema,
        "validate_entry",
        lambda self, *a, **k: {
            "valid": False,
            "errors": [{"field": "x", "expected": "a", "got": "b"}],
            "warnings": [{"field": "y", "rule": ""}],
        },
    )

    issues = QAService(world.config, world.db).validate_kb("test-kb", readable_kbs=UNSCOPED)[
        "issues"
    ]

    schema_issues = [i for i in issues if i["rule"] == "schema_violation"]
    assert {(i["field"], i["severity"]) for i in schema_issues} == {
        ("x", "error"),
        ("y", "warning"),
    }
    assert all("schema_rule" not in i for i in schema_issues), schema_issues

"""#958: `qa fix` offered a missing-field fix no validator could produce.

`QAFixService` matched `"missing"` in an issue's message and knew one default,
`importance: 5`. The KB schema's required-field errors say `required`
(`kb_schema.py`), and the index always carries the model's own `importance`
default, so no issue could name a field that fix handled. The kind is gone: a
required field is the author's to fill, not a value `qa fix` may invent.
"""

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

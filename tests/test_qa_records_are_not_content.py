"""A QA record is not content (#1752).

`qa assess` writes one `qa_assessment` entry per assessed entry, and those are
real indexed entries. The property: running `qa assess` changes nothing a
content reader reports. For the same content entries, `qa validate`,
`qa status`, `qa gaps` and `export site` give the same answer before and
after. One predicate (`pyrite/services/qa_records.py`) decides what a QA record
is, and every reader that enumerates entries in the two QA services goes
through it, or is listed below as not a content reader, with the reason.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
KB = "test-kb"

# Three content entries: `alpha` links to `beta`; `gamma` has no links, so
# `qa validate` reports it as an orphan. Each has a body and a tag.
_ENTRIES = {
    "alpha": "---\nid: alpha\ntitle: Alpha Subject\ntype: note\ntags: [one]\n---\nSee [[beta]] for the rest.\n",
    "beta": "---\nid: beta\ntitle: Beta Subject\ntype: note\ntags: [one]\n---\nBeta body.\n",
    "gamma": "---\nid: gamma\ntitle: Gamma Subject\ntype: note\ntags: [two]\n---\nGamma body.\n",
}


@pytest.fixture
def world(cli_world):
    w = cli_world()
    for text in _ENTRIES.values():
        w.add_entry(KB, text)
    return w


def _json(world, *args):
    result = world.cli(*args, "--format", "json")
    assert result.exit_code in (0, 1), result.output
    return json.loads(result.stdout)


def _validate(world):
    return _json(world, "qa", "validate", KB)["issues"]


def _assess(world):
    result = world.cli("qa", "assess", KB, "--format", "json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["assessed"] == 3, report
    return report


def _tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text(errors="replace")
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.mark.control(reason="records stay entries (option A); this holds before and after the fix")
def test_assess_writes_records_into_the_kb(world):
    """The records exist as entries (option A keeps them searchable)."""
    _assess(world)
    rows = world.db.execute_sql(
        "SELECT id FROM entry WHERE kb_name = :kb AND entry_type = 'qa_assessment'", {"kb": KB}
    )
    assert len(rows) == 3


def test_validate_issue_list_is_identical_after_assess(world):
    before = _validate(world)
    assert any(i["entry_id"] == "gamma" and i["rule"] == "orphan_entry" for i in before)

    _assess(world)
    after = _validate(world)

    key = lambda i: (i["entry_id"], i["rule"], i["field"] or "", i["message"])  # noqa: E731
    assert sorted(map(key, after)) == sorted(map(key, before))
    assert not [i for i in after if i["entry_id"].startswith("qa-")]


def test_validate_all_kbs_is_identical_after_assess(world):
    before = _json(world, "qa", "validate")["issues"]
    _assess(world)
    after = _json(world, "qa", "validate")["issues"]
    assert sorted(i["message"] for i in after) == sorted(i["message"] for i in before)


def test_status_counts_content_only(world):
    before = _json(world, "qa", "status", KB)
    assert before["total_entries"] == 3

    _assess(world)
    after = _json(world, "qa", "status", KB)

    assert after["total_entries"] == 3
    assert after["total_issues"] == before["total_issues"]
    assert after["issues_by_rule"] == before["issues_by_rule"]
    assert after["issues_by_severity"] == before["issues_by_severity"]
    # Coverage is about records, and still reads them.
    assert after["coverage"]["assessed"] == 3
    assert after["coverage"]["total"] == 3


def test_status_without_kb_counts_content_only(world):
    before = _json(world, "qa", "status")
    _assess(world)
    after = _json(world, "qa", "status")
    assert after["total_entries"] == before["total_entries"]
    assert after["total_issues"] == before["total_issues"]


def test_gaps_are_identical_after_assess(world):
    before = _json(world, "qa", "gaps", "--kb", KB)
    _assess(world)
    after = _json(world, "qa", "gaps", "--kb", KB)
    assert after == before


def test_export_site_has_no_qa_assessment_output(world, tmp_path):
    out_before = tmp_path / "site-before"
    out_after = tmp_path / "site-after"
    result = world.cli("export", "site", "-k", KB, "-o", str(out_before))
    assert result.exit_code == 0, result.output

    _assess(world)
    result = world.cli("export", "site", "-k", KB, "-o", str(out_after))
    assert result.exit_code == 0, result.output

    after = _tree(out_after)
    assert not [p for p in after if "qa_assessment" in p or "qa-" in Path(p).name]
    assert not [p for p, text in after.items() if "qa_assessment" in text]
    assert after == _tree(out_before)


@pytest.mark.control(reason="existing skip behaviour that the fix must keep")
def test_assess_still_skips_recently_assessed_entries(world):
    _assess(world)
    result = world.cli("qa", "assess", KB, "--format", "json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["assessed"] == 0
    # 3 content entries skipped as recent, plus the 3 records as records.
    assert report["skipped"] == 6


def test_the_predicate_is_one_set():
    # Imported here so the rest of the file collects, and fails on behaviour, without the fix.
    from pyrite.services import qa_records

    assert qa_records.QA_RECORD_TYPES == frozenset({"qa_assessment"})
    assert qa_records.is_qa_record("qa_assessment")
    assert not qa_records.is_qa_record("note")
    assert qa_records.content_only("e") == "e.entry_type NOT IN ('qa_assessment')"
    assert qa_records.records_only("") == "entry_type IN ('qa_assessment')"


# -- the classifier ---------------------------------------------------------

_SERVICES = ("pyrite/services/qa_service.py", "pyrite/services/qa_analytics_service.py")
_FROM_ENTRY = re.compile(r"\b(?:FROM|JOIN)\s+entry\b", re.IGNORECASE)
_HELPERS = ("content_only", "records_only")

#: Readers of `entry` in the QA services that are NOT content readers, keyed
#: ``file::function``, each with the reason. A reader not here must go through
#: the shared predicate; a new unclassified one fails the test.
NOT_A_CONTENT_READER: dict[str, str] = {
    "qa_service.py::validate_entry": "one entry the caller named; a record id asked for by name is its own question",
    "qa_service.py::_evaluate_llm_rubric": "one entry the caller named (`assess_entry` tier 2)",
    "qa_service.py::find_duplicates": "selects one entry_type the caller names",
    "qa_service.py::_check_task_references": "selects tasks, and resolves ids a task names",
    "qa_service.py::_check_rubric_type_metadata": "selects person and document entries by type",
    "qa_service.py::_check_events_missing_dates": "selects event entries by type",
    "qa_service.py::assess_kb": "reads every row so it can count the records it skips (`is_qa_record`)",
    "qa_service.py::_check_broken_links": "joins `entry` to resolve a link's target; it enumerates links, not entries",
}


def _readers():
    """(file, function, source) for each `execute_sql` call that reads `entry`."""
    found = []
    for rel in _SERVICES:
        path = REPO_ROOT / rel
        source = path.read_text()
        tree = ast.parse(source)
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            func_src = ast.get_source_segment(source, func) or ""
            for call in ast.walk(func):
                if not (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "execute_sql"
                    and call.args
                ):
                    continue
                arg = call.args[0]
                text = ast.get_source_segment(source, arg) or ""
                # `sql = ...; sql += ...`, or an f-string naming a local clause:
                # the query is built in the function, so read all of it.
                names_a_local = isinstance(arg, ast.Name) or (
                    isinstance(arg, ast.JoinedStr)
                    and any(
                        isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name)
                        for v in arg.values
                    )
                )
                if names_a_local:
                    text = func_src
                if _FROM_ENTRY.search(text):
                    found.append((path.name, func.name, text))
    return found


@pytest.mark.control(
    reason="checks the classifier finds the readers; true before and after the fix"
)
def test_the_classifier_sees_readers():
    names = {f"{f}::{fn}" for f, fn, _ in _readers()}
    assert "qa_service.py::get_status" not in names  # it calls validate_kb
    assert "qa_service.py::validate_kb" in names
    assert "qa_analytics_service.py::analyze_gaps" in names
    assert len(names) >= 20


def test_every_entry_reader_is_guarded_or_classified():
    unclassified = []
    for fname, func, text in _readers():
        if f"{fname}::{func}" in NOT_A_CONTENT_READER:
            continue
        if not any(h in text for h in _HELPERS):
            unclassified.append(f"{fname}::{func}")
    assert not unclassified, (
        "These readers select from `entry` and neither go through "
        "pyrite.services.qa_records (content_only / records_only) nor appear in "
        f"NOT_A_CONTENT_READER with a reason: {sorted(set(unclassified))}"
    )


@pytest.mark.control(reason="table hygiene, independent of the fix")
def test_the_not_a_content_reader_table_has_no_stale_rows():
    names = {f"{f}::{fn}" for f, fn, _ in _readers()}
    stale = sorted(set(NOT_A_CONTENT_READER) - names)
    assert not stale, f"no such reader any more: {stale}"
    assert all(reason.strip() for reason in NOT_A_CONTENT_READER.values())


def test_no_hand_written_qa_assessment_exclusion_remains():
    for rel in _SERVICES:
        for n, line in enumerate((REPO_ROOT / rel).read_text().splitlines(), 1):
            if re.search(r"entry_type\s*(!=|=|NOT IN|IN)\s*\(?\s*'qa_assessment'", line):
                pytest.fail(f"{rel}:{n} spells the QA record type by hand: {line.strip()}")

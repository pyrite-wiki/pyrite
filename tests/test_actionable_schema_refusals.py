"""Actionable field-schema refusals, warnings, QA findings, and `-i` errors (#1753)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from pyrite.config import KBConfig, KBType
from pyrite.exceptions import SchemaViolationError
from pyrite.schema.field_schema import FieldSchema, _validate_field_value
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.kb_service import KBService
from pyrite.services.qa_service import QAService
from tests._world import build_world

_TYPE_NAME_RE = re.compile(r"\b(?:ScalarInt|ScalarFloat|str|int)\b")

_SCHEMA_KB_YAML = """\
name: typed
kb_type: generic
validation:
  enforce: true
types:
  record:
    description: Typed record for field validation
    subdirectory: records
    fields:
      amount:
        type: number
      score:
        type: number
        min: 1
        max: 10
      due_date:
        type: date
      logged_at:
        type: datetime
      blocked:
        type: checkbox
      reviewed:
        type: checkbox
      categories:
        type: multi-select
        options: [alpha, beta, gamma]
      items_list:
        type: list
      labels:
        type: tags
"""

_WARN_KB_YAML = _SCHEMA_KB_YAML.replace("enforce: true", "enforce: false")


def _ruamel_load(yaml_snippet: str):
    return YAML(typ="rt").load(yaml_snippet)


def _json_body(result) -> dict:
    return json.loads(result.stdout[result.stdout.index("{") :])


def _assert_no_internal_type_names(text: str) -> None:
    assert not _TYPE_NAME_RE.search(text), f"leaked internal type name in: {text!r}"


@pytest.fixture
def typed_world(tmp_path: Path):
    kb_path = tmp_path / "typed"
    kb_path.mkdir(parents=True)
    (kb_path / "kb.yaml").write_text(_SCHEMA_KB_YAML, encoding="utf-8")
    world = build_world(
        tmp_path,
        kbs=[KBConfig(name="typed", path=kb_path, kb_type=KBType.GENERIC)],
        settings={"index_path": tmp_path / "index.db"},
        rest=False,
    )
    try:
        yield world
    finally:
        world.close()


@pytest.fixture
def warn_world(tmp_path: Path):
    root = tmp_path / "warn_root"
    kb_path = root / "warn_typed"
    kb_path.mkdir(parents=True)
    (kb_path / "kb.yaml").write_text(_WARN_KB_YAML, encoding="utf-8")
    world = build_world(
        root,
        kbs=[KBConfig(name="warn_typed", path=kb_path, kb_type=KBType.GENERIC)],
        settings={"index_path": root / "warn_index.db"},
        rest=False,
    )
    try:
        yield world
    finally:
        world.close()


# ---------------------------------------------------------------------------
# 1. Number fields: -f amount=1,200 and -f amount='$1200'
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["1,200", "$1200"])
def test_number_field_refusal_suggests_digits_only_and_1200(typed_world, raw: str):
    res = typed_world.cli(
        "create",
        "-k",
        "typed",
        "-t",
        "record",
        "--title",
        "Invoice",
        "-f",
        f"amount={raw}",
        "--format",
        "json",
    )
    assert res.exit_code == 1, res.output
    body = _json_body(res)
    assert body["error_code"] == "SCHEMA_VIOLATION", body
    assert "numeric value" not in body["error"], body
    suggestion = body.get("suggestion") or ""
    assert "1200" in suggestion, body
    assert "digits only" in suggestion, body


# ---------------------------------------------------------------------------
# 2. --importance / -i on create and update: 7.5, 0, abc match -i 42
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", ["7.5", "0", "abc"])
def test_cli_importance_create_matches_out_of_range_42(typed_world, bad_value: str):
    ref = _json_body(
        typed_world.cli(
            "create",
            "-k",
            "typed",
            "-t",
            "record",
            "--title",
            "Ref",
            "-i",
            "42",
            "--format",
            "json",
        )
    )
    res = typed_world.cli(
        "create",
        "-k",
        "typed",
        "-t",
        "record",
        "--title",
        "Bad",
        "-i",
        bad_value,
        "--format",
        "json",
    )
    assert res.exit_code == 1, res.output
    body = _json_body(res)
    assert body["error_code"] == ref["error_code"] == "VALIDATION_FAILED", body
    assert set(body.keys()) == set(ref.keys()), body
    assert body["error"] == f"Importance must be 1-10, got: {bad_value}", body
    assert body["suggestion"] == ref["suggestion"], body
    assert "whole number 1-10" in body["suggestion"], body
    assert body["errors"][0]["field"] == ref["errors"][0]["field"] == "importance"
    assert body["errors"][0]["rule"] == ref["errors"][0]["rule"] == "range"
    assert body["errors"][0]["expected"] == ref["errors"][0]["expected"] == "1-10"


@pytest.mark.parametrize("bad_value", ["7.5", "0", "abc"])
def test_cli_importance_update_matches_out_of_range_42(typed_world, bad_value: str):
    typed_world.cli(
        "create",
        "-k",
        "typed",
        "-t",
        "record",
        "--title",
        "Seed",
        "-i",
        "5",
        "--format",
        "json",
    )
    ref_res = typed_world.cli("update", "seed", "-k", "typed", "-i", "42", "--format", "json")
    assert ref_res.exit_code == 1, ref_res.output
    ref = _json_body(ref_res)

    res = typed_world.cli("update", "seed", "-k", "typed", "-i", bad_value, "--format", "json")
    assert res.exit_code == 1, res.output
    body = _json_body(res)
    assert body["error_code"] == ref["error_code"] == "VALIDATION_FAILED", body
    assert set(body.keys()) == set(ref.keys()), body
    assert body["error"] == f"Importance must be 1-10, got: {bad_value}", body
    assert body["suggestion"] == ref["suggestion"], body
    assert "whole number 1-10" in body["suggestion"], body
    assert body["errors"][0]["field"] == ref["errors"][0]["field"] == "importance"
    assert body["errors"][0]["rule"] == ref["errors"][0]["rule"] == "range"


@pytest.mark.control(reason="valid -i integer still succeeds and writes integer importance")
def test_cli_importance_valid_integer_still_accepted(typed_world):
    res = typed_world.cli(
        "create",
        "-k",
        "typed",
        "-t",
        "record",
        "--title",
        "Good",
        "-i",
        "7",
        "--format",
        "json",
    )
    assert res.exit_code == 0, res.output
    tmpl = typed_world.cli(
        "create", "-k", "typed", "-t", "record", "--template", "-i", "7", "--format", "json"
    )
    assert tmpl.exit_code == 0, tmpl.output
    assert "importance: 7" in _json_body(tmpl)["template"]


# ---------------------------------------------------------------------------
# 3. Checkbox: -f blocked=0, on-disk blocked: 1, and ruamel/Python bad types
# ---------------------------------------------------------------------------


_BAD_CHECKBOX_VALUES = [
    pytest.param(_ruamel_load("1"), id="ruamel-scalar-int"),
    pytest.param(_ruamel_load("1.5"), id="ruamel-scalar-float"),
    pytest.param(_ruamel_load('"yes"'), id="ruamel-scalar-str"),
    pytest.param(0, id="plain-int"),
    pytest.param("yes", id="plain-str"),
    pytest.param(["true"], id="list"),
    pytest.param({"v": True}, id="dict"),
]


@pytest.mark.parametrize("bad_value", _BAD_CHECKBOX_VALUES)
def test_checkbox_rejects_non_bool_without_internal_type_names(bad_value):
    fs = FieldSchema(name="blocked", field_type="checkbox")
    errors = _validate_field_value("blocked", bad_value, fs)
    assert len(errors) == 1
    err = errors[0]
    assert err["rule"] == "field_checkbox"
    assert err["expected"] == "boolean"
    assert "use true or false" in err["message"]
    assert "use true or false" in err["suggestion"]
    rendered = json.dumps(err)
    _assert_no_internal_type_names(rendered)


def test_checkbox_cli_and_disk_file_say_use_true_or_false(typed_world):
    cli_res = typed_world.cli(
        "create",
        "-k",
        "typed",
        "-t",
        "record",
        "--title",
        "Blocked",
        "-f",
        "blocked=0",
        "--format",
        "json",
    )
    assert cli_res.exit_code == 1, cli_res.output
    body = _json_body(cli_res)
    assert body["error_code"] == "SCHEMA_VIOLATION", body
    assert "use true or false" in body["error"], body
    assert "use true or false" in (body.get("suggestion") or ""), body
    _assert_no_internal_type_names(cli_res.stdout)

    typed_world.add_entry(
        "typed",
        "---\nid: disk-blocked\ntype: record\ntitle: Disk Blocked\nblocked: 1\n---\n\nBody.\n",
    )
    qa_svc = QAService(typed_world.config, typed_world.db)
    qa_result = qa_svc.validate_kb("typed", readable_kbs=UNSCOPED)
    issues = [
        i
        for i in qa_result["issues"]
        if i["entry_id"] == "disk-blocked" and i["field"] == "blocked"
    ]
    assert len(issues) == 1, qa_result
    assert "use true or false" in issues[0]["message"], issues[0]
    assert "use true or false" in (issues[0].get("suggestion") or ""), issues[0]
    _assert_no_internal_type_names(json.dumps(issues[0]))


def test_checkbox_success_warning_for_on_disk_reviewed_zero(typed_world, warn_world):
    # Case A: enforce: true, unrelated update keeps on-disk `reviewed: 0` as a warning.
    typed_world.add_entry(
        "typed",
        "---\nid: kept-warn\ntype: record\ntitle: Kept\nreviewed: 0\n---\n\nBody.\n",
    )
    res = typed_world.cli(
        "update", "kept-warn", "-k", "typed", "-b", "Updated body", "--format", "json"
    )
    assert res.exit_code == 0, res.output
    body = _json_body(res)
    warnings = [w for w in body.get("warnings", []) if w.get("field") == "reviewed"]
    assert len(warnings) == 1, body
    assert "use true or false" in warnings[0]["message"], warnings[0]
    assert "use true or false" in (warnings[0].get("suggestion") or ""), warnings[0]
    _assert_no_internal_type_names(json.dumps(warnings[0]))

    # Case B: enforce: false, create with `reviewed: 0` succeeds with the same warning words.
    warn_res = warn_world.cli(
        "create",
        "-k",
        "warn_typed",
        "-t",
        "record",
        "--title",
        "Warn Create",
        "-f",
        "reviewed=0",
        "--format",
        "json",
    )
    assert warn_res.exit_code == 0, warn_res.output
    warn_body = _json_body(warn_res)
    c_warnings = [w for w in warn_body.get("warnings", []) if w.get("field") == "reviewed"]
    assert len(c_warnings) == 1, warn_body
    assert "use true or false" in c_warnings[0]["message"], c_warnings[0]
    assert "use true or false" in (c_warnings[0].get("suggestion") or ""), c_warnings[0]
    _assert_no_internal_type_names(json.dumps(c_warnings[0]))


# ---------------------------------------------------------------------------
# 4. All six `_validate_field_value` type branches across service & QA
# ---------------------------------------------------------------------------


_SIX_BRANCH_CASES = [
    pytest.param(
        "amount",
        "1,200",
        "field_number",
        "number",
        "digits only",
        id="number-format",
    ),
    pytest.param(
        "score",
        99,
        "field_range",
        "number from 1 to 10",
        "1 to 10",
        id="number-range",
    ),
    pytest.param(
        "due_date",
        "03/15/2026",
        "field_date",
        "YYYY-MM-DD",
        "2026-01-15",
        id="date",
    ),
    pytest.param(
        "logged_at",
        "not-a-timestamp",
        "field_datetime",
        "ISO 8601 datetime",
        "2026-01-15T09:00:00Z",
        id="datetime",
    ),
    pytest.param(
        "blocked",
        _ruamel_load("1"),
        "field_checkbox",
        "boolean",
        "use true or false",
        id="checkbox",
    ),
    pytest.param(
        "categories",
        _ruamel_load("1"),
        "field_multi_select",
        "list",
        "alpha",
        id="multi-select",
    ),
    pytest.param(
        "items_list",
        _ruamel_load("1"),
        "field_type",
        "list",
        "use a list",
        id="list",
    ),
    pytest.param(
        "labels",
        _ruamel_load("1"),
        "field_type",
        "list",
        "use a list",
        id="tags",
    ),
]


@pytest.mark.parametrize(
    ("field_name", "bad_value", "expected_rule", "expected_form", "suggestion_needle"),
    _SIX_BRANCH_CASES,
)
def test_all_six_field_branches_carry_plain_words_and_suggestions(
    typed_world,
    field_name: str,
    bad_value,
    expected_rule: str,
    expected_form: str,
    suggestion_needle: str,
):
    kb_svc = KBService(typed_world.config, typed_world.db)
    if field_name in ("categories", "items_list", "labels"):
        # Direct writes coerce scalars on list fields; an on-disk or dict value
        # hits the type check, so write a dict or test via disk/QA + direct schema.
        bad_for_write = {"not": "a list"}
    else:
        bad_for_write = bad_value

    with pytest.raises(SchemaViolationError) as caught:
        kb_svc.create("typed", {"entry_type": "record", "title": "Bad", field_name: bad_for_write})
    exc = caught.value
    assert exc.errors and exc.errors[0]["rule"] == expected_rule
    assert exc.errors[0]["expected"] == expected_form
    assert exc.suggestion and expected_form.split()[0] in exc.suggestion or exc.suggestion
    _assert_no_internal_type_names(str(exc))
    _assert_no_internal_type_names(exc.suggestion or "")

    if field_name not in ("categories", "items_list", "labels"):
        assert suggestion_needle in (exc.suggestion or ""), exc.suggestion

    # QA validation of an on-disk entry with `bad_value` carries the same clear message & suggestion.
    entry_id = f"qa-{field_name}"
    yaml_val = json.dumps(int(bad_value) if isinstance(bad_value, int) else bad_value)
    typed_world.add_entry(
        "typed",
        f"---\nid: {entry_id}\ntype: record\ntitle: QA {field_name}\n{field_name}: {yaml_val}\n---\n\nBody.\n",
    )
    qa_svc = QAService(typed_world.config, typed_world.db)
    single = qa_svc.validate_entry(entry_id, "typed", readable_kbs=UNSCOPED)
    single_hits = [
        i for i in single["issues"] if i["rule"] == "schema_violation" and i["field"] == field_name
    ]
    assert len(single_hits) == 1, single
    assert suggestion_needle in (single_hits[0].get("suggestion") or ""), single_hits[0]
    _assert_no_internal_type_names(single_hits[0]["message"])

    bulk = qa_svc.validate_kb("typed", readable_kbs=UNSCOPED)
    bulk_hits = [
        i
        for i in bulk["issues"]
        if i["entry_id"] == entry_id
        and i["rule"] == "schema_violation"
        and i["field"] == field_name
    ]
    assert len(bulk_hits) == 1, bulk
    assert bulk_hits[0]["message"] == single_hits[0]["message"]
    assert bulk_hits[0].get("suggestion") == single_hits[0].get("suggestion")

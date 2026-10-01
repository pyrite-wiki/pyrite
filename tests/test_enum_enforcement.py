"""Declared enums in kb.yaml are read, checked by one function, and enforced per KB (#555, #47).

Before: a `values:` list was dropped by the parser, rule-level `enum:` was never
read, and `list` fields were never checked, so validators reported clean over
real drift (70+ off-list `org_type` values in one research KB). `schema
validate` kept its own copy of the select check; `index health` looked only at
`status`. And an entry already off-list could not be updated at all (#47).

Now `validation.enforce_enums` (default true, independent of
`validation.enforce`) decides whether an off-list kb.yaml value is an error or
a warning; one function in `pyrite/schema/enum_check.py` makes every finding; and an
update that leaves an off-list value as it was succeeds with a warning.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import SchemaViolationError, ValidationError
from pyrite.schema import KBSchema
from pyrite.schema.field_schema import FieldSchema
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.utils.yaml import load_yaml

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------
# Fixture KB
# --------------------------------------------------------------------------

TYPES_YAML = """\
types:
  org:
    description: An organisation
    subdirectory: orgs
    fields:
      org_type:
        type: select
        values: [ngo, company]
      sectors:
        type: list
        items:
          values: [energy, health]
      codes:
        type: list
        options: [x, y]
      labels:
        type: multi-select
        options: [a, b]
      loose:
        type: select
        options: [p, q]
        allow_other: true
      score:
        type: number
        min: 0
        max: 10
"""


def kb_yaml(enforce_enums: bool | None = None, enforce: bool = False) -> str:
    lines = ["name: t", "kb_type: generic", "validation:", f"  enforce: {str(enforce).lower()}"]
    if enforce_enums is not None:
        lines.append(f"  enforce_enums: {str(enforce_enums).lower()}")
    lines += [
        "  rules:",
        "    - field: region",
        "      enum: [north, south]",
    ]
    return "\n".join(lines) + "\n" + TYPES_YAML


def schema(enforce_enums: bool | None = None, enforce: bool = False) -> KBSchema:
    return KBSchema.from_dict(load_yaml(kb_yaml(enforce_enums, enforce)))


def _make_config(tmp_path: Path, yaml_text: str, kb_type: str = "generic", name: str = "t"):
    kb = tmp_path / name
    kb.mkdir(parents=True, exist_ok=True)
    (kb / "kb.yaml").write_text(yaml_text)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=name, path=kb, kb_type=kb_type)],
        settings=Settings(index_path=tmp_path / "i.db", auto_embed=False),
    )
    return config, kb


@pytest.fixture(params=[True, False], ids=["enforce_enums_on", "enforce_enums_off"])
def mode(request):
    return request.param


@pytest.fixture
def svc_factory(tmp_path):
    made = []

    def make(enforce_enums: bool | None = None, enforce: bool = False):
        config, kb = _make_config(tmp_path, kb_yaml(enforce_enums, enforce))
        db = PyriteDB(config.settings.index_path)
        made.append(db)
        return KBService(config, db), kb, config

    yield make
    for db in made:
        db.close()


def _write_org(kb: Path, entry_id: str, **fields) -> Path:
    """Put an entry on disk directly -- the state an older Pyrite left behind."""
    (kb / "orgs").mkdir(exist_ok=True)
    lines = ["---", f"id: {entry_id}", "type: org", f"title: {entry_id}"]
    for k, v in fields.items():
        lines.append(f"{k}: {json.dumps(v)}")
    lines += ["---", "", "Body.", ""]
    path = kb / "orgs" / f"{entry_id}.md"
    path.write_text("\n".join(lines))
    return path


def _spec(entry_type: str, entry_id: str, **fields) -> dict:
    return {"entry_type": entry_type, "id": entry_id, "title": entry_id, "body": "b", **fields}


def _fm(path: Path) -> dict:
    text = path.read_text()
    return load_yaml(text.split("---", 2)[1])


# ==========================================================================
# 1-3. Reading
# ==========================================================================


class TestReading:
    def test_values_is_an_alias_of_options_for_select_multi_select_and_list(self):
        for ftype in ("select", "multi-select", "list"):
            fs = FieldSchema.from_dict("f", {"type": ftype, "values": ["a", "b"]})
            assert fs.options == ["a", "b"], ftype
            assert fs.to_dict()["options"] == ["a", "b"]
            assert "values" not in fs.to_dict()

    def test_options_wins_when_both_keys_differ_and_the_conflict_is_recorded(self):
        fs = FieldSchema.from_dict("f", {"type": "select", "options": ["a"], "values": ["b"]})
        assert fs.options == ["a"]
        assert fs.options_conflict is True
        same = FieldSchema.from_dict("f", {"type": "select", "options": ["a"], "values": ["a"]})
        assert same.options_conflict is False

    def test_items_values_and_options_constrain_list_elements(self):
        fs = FieldSchema.from_dict("f", {"type": "list", "items": {"values": ["a"]}})
        assert fs.allowed_values() == ["a"]
        fs = FieldSchema.from_dict("f", {"type": "list", "items": {"options": ["b"]}})
        assert fs.allowed_values() == ["b"]

    def test_the_agent_schema_sees_the_values_list_as_options(self):
        agent = schema().to_agent_schema()
        assert agent["types"]["org"]["fields"]["org_type"]["options"] == ["ngo", "company"]


# ==========================================================================
# 4-5. One function, one switch
# ==========================================================================


def _findings(result, field):
    return [f for f in result["errors"] + result["warnings"] if f.get("field") == field]


class TestValidateEntrySwitch:
    @pytest.mark.parametrize(
        ("field", "value", "rule"),
        [
            ("org_type", "charity", "field_select"),
            ("sectors", ["energy", "mining"], "field_list"),
            ("codes", ["z"], "field_list"),
            ("codes", "z", "field_list"),  # a scalar on a list field is one element
            ("labels", ["a", "c"], "field_multi_select"),
            ("region", "east", "rule_enum"),
            ("region", ["north", "east"], "rule_enum"),
        ],
    )
    def test_off_list_value_is_an_error_when_on_and_a_warning_when_off(
        self, mode, field, value, rule
    ):
        result = schema(enforce_enums=mode).validate_entry("org", {"title": "T", field: value})
        bucket = result["errors"] if mode else result["warnings"]
        other = result["warnings"] if mode else result["errors"]
        hits = [f for f in bucket if f["field"] == field]
        assert len(hits) == 1, result
        assert hits[0]["rule"] == rule
        assert not [f for f in other if f["field"] == field]
        assert hits[0]["severity"] == ("error" if mode else "warning")

    def test_the_switch_defaults_to_on_when_absent(self):
        result = schema(enforce_enums=None).validate_entry("org", {"title": "T", "org_type": "x"})
        assert [f["field"] for f in result["errors"]] == ["org_type"]

    def test_the_switch_is_independent_of_enforce(self):
        # enforce: false (the init default) does not turn enums off ...
        r = schema(enforce_enums=True, enforce=False).validate_entry(
            "org", {"title": "T", "org_type": "x", "score": 99}
        )
        assert [f["field"] for f in r["errors"]] == ["org_type"]
        assert [f["field"] for f in r["warnings"]] == ["score"]
        # ... and enforce_enums: false does not turn a range error into a warning.
        r = schema(enforce_enums=False, enforce=True).validate_entry(
            "org", {"title": "T", "org_type": "x", "score": 99}
        )
        assert [f["field"] for f in r["errors"]] == ["score"]
        assert [f["field"] for f in r["warnings"]] == ["org_type"]

    def test_allow_other_is_a_warning_in_both_modes(self, mode):
        r = schema(enforce_enums=mode).validate_entry("org", {"title": "T", "loose": "z"})
        assert not r["errors"]
        assert [f["field"] for f in r["warnings"]] == ["loose"]

    def test_on_list_and_absent_values_produce_no_finding(self, mode):
        r = schema(enforce_enums=mode).validate_entry(
            "org",
            {
                "title": "T",
                "org_type": "ngo",
                "sectors": ["energy"],
                "codes": ["x", "y"],
                "labels": ["a"],
                "region": "north",
            },
        )
        assert r == {"valid": True, "errors": [], "warnings": []}

    def test_an_empty_value_is_not_an_off_list_value(self):
        """A form that sends `null`/`""` for an unset select must not be refused:
        absence is `required`'s business, not the enum's."""
        for empty in (None, "", []):
            r = schema(enforce_enums=True).validate_entry(
                "org", {"title": "T", "org_type": empty, "region": empty, "codes": empty}
            )
            assert not r["errors"], (empty, r)

    def test_finding_names_field_value_allowed_list_and_origin(self):
        r = schema().validate_entry("org", {"title": "T", "sectors": ["energy", "mining"]})
        (f,) = r["errors"]
        assert f["got"] == ["mining"]
        assert f["expected"] == ["energy", "health"]
        assert f["origin"] == "field"
        (g,) = schema().validate_entry("org", {"title": "T", "region": "east"})["errors"]
        assert g["origin"] == "rule" and g["expected"] == ["north", "south"]
        assert g["got"] == "east"
        (h,) = schema().validate_entry("org", {"title": "T", "region": ["north", "east"]})["errors"]
        assert h["got"] == ["east"], "a list is checked per element"

    def test_multi_select_non_list_stays_under_enforce(self):
        for scalar in ("a", "z"):
            r = schema(enforce_enums=True, enforce=False).validate_entry(
                "org", {"title": "T", "labels": scalar}
            )
            assert not r["errors"], r
            assert [(f["field"], f["expected"]) for f in r["warnings"]] == [("labels", "list")]

    def test_one_function_makes_the_findings(self):
        from pyrite.schema.enum_check import enum_findings

        found = enum_findings(schema(), "org", {"org_type": "x", "region": "east"})
        assert {(f["field"], f["origin"]) for f in found} == {
            ("org_type", "field"),
            ("region", "rule"),
        }

    def test_schema_commands_has_no_second_copy_of_the_select_check(self):
        src = (REPO / "pyrite" / "cli" / "schema_commands.py").read_text()
        assert "field_schema.options" not in src
        assert "enum_findings" in src


# ==========================================================================
# 5, 10, 11. The write path
# ==========================================================================


class TestWritePath:
    def test_create_off_list_refused_when_on_written_with_warning_when_off(self, svc_factory, mode):
        svc, kb, _ = svc_factory(enforce_enums=mode)
        if mode:
            with pytest.raises(SchemaViolationError) as exc:
                svc.create("t", _spec("org", "acme", org_type="charity"))
            msg = str(exc.value)
            assert "org_type" in msg and "'charity'" in msg and "ngo" in msg and "company" in msg
            assert not list(kb.rglob("acme.md"))
        else:
            written = svc.create("t", _spec("org", "acme", org_type="charity"))
            assert any(w["field"] == "org_type" for w in written.warnings)

    def test_update_unrelated_field_on_off_list_entry_succeeds_with_a_warning(self, svc_factory):
        svc, kb, _ = svc_factory(enforce_enums=True)
        path = _write_org(kb, "acme", org_type="charity")
        written = svc.update("acme", "t", {"tags": ["probe"]})
        assert _fm(path)["tags"] == ["probe"]
        assert _fm(path)["org_type"] == "charity"
        (w,) = [w for w in written.warnings if w["field"] == "org_type"]
        assert w["got"] == "charity" and w["severity"] == "warning"

    def test_update_to_another_off_list_value_is_refused(self, svc_factory):
        svc, kb, _ = svc_factory(enforce_enums=True)
        path = _write_org(kb, "acme", org_type="charity")
        before = path.read_text()
        with pytest.raises(SchemaViolationError, match="'trust' is not one of"):
            svc.update("acme", "t", {"org_type": "trust"})
        assert path.read_text() == before

    def test_update_to_an_on_list_value_succeeds(self, svc_factory):
        svc, kb, _ = svc_factory(enforce_enums=True)
        path = _write_org(kb, "acme", org_type="charity")
        written = svc.update("acme", "t", {"org_type": "ngo"})
        assert _fm(path)["org_type"] == "ngo"
        assert not written.warnings

    def test_echo_of_the_full_read_counts_as_unchanged(self, svc_factory):
        svc, kb, _ = svc_factory(enforce_enums=True)
        _write_org(kb, "acme", org_type="charity", region="east")
        IndexManager(svc.db, svc.config).index_kb("t")
        read = svc.get_entry("acme", kb_name="t", readable_kbs=UNSCOPED)
        read = {**read, "title": "Acme renamed"}
        updates, _, _ = svc.split_echoed_update("acme", "t", read)
        # Whatever the echo filter leaves, the off-list values sent back as
        # they were must not refuse the write.
        updates.setdefault("org_type", "charity")
        updates.setdefault("region", "east")
        written = svc.update("acme", "t", updates)
        assert {w["field"] for w in written.warnings} >= {"org_type", "region"}

    def test_adding_an_off_list_list_element_is_refused_keeping_one_is_not(self, svc_factory):
        svc, kb, _ = svc_factory(enforce_enums=True)
        path = _write_org(kb, "acme", sectors=["energy", "mining"])
        written = svc.update("acme", "t", {"sectors": ["mining", "energy", "health"]})
        assert _fm(path)["sectors"] == ["mining", "energy", "health"]
        assert any(w["field"] == "sectors" and w["got"] == ["mining"] for w in written.warnings)
        with pytest.raises(SchemaViolationError) as exc:
            svc.update("acme", "t", {"sectors": ["mining", "fishing"]})
        assert "fishing" in str(exc.value)
        assert "mining" not in str(exc.value)

    def test_rule_enum_on_disk_value_is_kept_on_unrelated_update(self, svc_factory):
        svc, kb, _ = svc_factory(enforce_enums=True)
        _write_org(kb, "acme", region="east")
        written = svc.update("acme", "t", {"tags": ["x"]})
        assert any(w["field"] == "region" for w in written.warnings)
        with pytest.raises(SchemaViolationError, match="region"):
            svc.update("acme", "t", {"region": "west"})

    def test_off_list_update_is_a_warning_when_off(self, svc_factory):
        svc, kb, _ = svc_factory(enforce_enums=False)
        path = _write_org(kb, "acme", org_type="charity")
        written = svc.update("acme", "t", {"org_type": "trust"})
        assert _fm(path)["org_type"] == "trust"
        assert any(w["field"] == "org_type" for w in written.warnings)

    def test_enum_error_on_a_field_absent_before_is_not_excepted(self):
        """The exception is for a value the file already had: an enum error on
        a field the entry did not have before (a validator checking a default,
        say) stays an error even though it is "unchanged" (absent both times)."""
        err = {"field": "kind", "rule": "enum", "expected": ["a"], "got": None}
        kept, excepted = KBService._keep_on_disk_enum_values([err], {}, {})
        assert kept == [err] and excepted == []

    def test_untouched_non_enum_errors_are_not_excepted(self, svc_factory):
        """The exception covers enum findings only: an out-of-range number on
        disk still refuses an unrelated update when `enforce` is on."""
        svc, kb, _ = svc_factory(enforce_enums=True, enforce=True)
        _write_org(kb, "acme", score=99)
        with pytest.raises(SchemaViolationError, match="score"):
            svc.update("acme", "t", {"tags": ["x"]})


# ==========================================================================
# 9, 10, 12. #47 on a fixture copy of the live entry (plugin enum)
# ==========================================================================

LIVE_ENTRY = REPO / "kb" / "backlog" / "pyrite-remove-static-renderer.md"


@pytest.fixture
def software_kb(tmp_path):
    pytest.importorskip("pyrite_software_kb")
    kb = tmp_path / "pyrite"
    (kb / "backlog" / "done").mkdir(parents=True)
    shutil.copy(REPO / "kb" / "kb.yaml", kb / "kb.yaml")
    shutil.copy(LIVE_ENTRY, kb / "backlog" / LIVE_ENTRY.name)
    # An entry with no off-list value (its `rank: -1` draws a plugin
    # `min_value` finding, which is not an enum), and an off-list one in a
    # subdirectory.
    (kb / "backlog" / "fine.md").write_text(
        "---\nid: fine\ntype: backlog_item\ntitle: Fine\nkind: bug\nstatus: proposed\nrank: -1\n"
        "---\n\nB.\n"
    )
    (kb / "backlog" / "bad-status.md").write_text(
        "---\nid: bad-status\ntype: backlog_item\ntitle: S\nkind: bug\nstatus: completed\n"
        "---\n\nB.\n"
    )
    (kb / "backlog" / "done" / "old-chore.md").write_text(
        "---\nid: old-chore\ntype: backlog_item\ntitle: Old\nkind: chore\nstatus: done\n---\n\nB.\n"
    )
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="pyrite", path=kb, kb_type="software")],
        settings=Settings(index_path=tmp_path / "i.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    IndexManager(db, config).index_all()
    db.close()
    return config, kb


def _cli(config, *args):
    with (
        patch("pyrite.cli.context.load_config", return_value=config),
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.config.load_config", return_value=config),
    ):
        return runner.invoke(app, list(args))


def _payload(output: str) -> dict:
    return json.loads(output[output.index("{") :])


class TestIssue47:
    def test_the_fixture_still_carries_the_live_drift(self):
        assert "kind: chore" in LIVE_ENTRY.read_text()

    def test_update_tags_succeeds_and_warns_naming_kind_chore(self, software_kb):
        config, kb = software_kb
        path = kb / "backlog" / LIVE_ENTRY.name
        result = _cli(
            config, "update", "pyrite-remove-static-renderer", "-k", "pyrite", "--tags", "probe"
        )
        assert result.exit_code == 0, result.output
        out = _payload(result.output)
        assert out["updated"] is True
        (w,) = [w for w in out["warnings"] if w["field"] == "kind"]
        assert w["got"] == "chore"
        assert "probe" in _fm(path)["tags"]
        assert _fm(path)["kind"] == "chore"
        assert LIVE_ENTRY.read_text().count("probe") == 0, "the live file is never touched"

    def test_changing_kind_to_another_off_list_value_is_refused(self, software_kb):
        config, _ = software_kb
        db = PyriteDB(config.settings.index_path)
        try:
            with pytest.raises(ValidationError, match="kind"):
                KBService(config, db).update(
                    "pyrite-remove-static-renderer", "pyrite", {"kind": "refactor"}
                )
        finally:
            db.close()

    def test_create_with_a_plugin_off_list_value_is_still_refused(self, software_kb):
        config, _ = software_kb
        db = PyriteDB(config.settings.index_path)
        try:
            with pytest.raises(ValidationError, match="kind"):
                KBService(config, db).create(
                    "pyrite", _spec("backlog_item", "new-chore", kind="chore")
                )
        finally:
            db.close()

    def test_index_health_names_the_entry_field_and_value(self, software_kb):
        config, _ = software_kb
        db = PyriteDB(config.settings.index_path)
        try:
            health = IndexManager(db, config).check_health(kb_name="pyrite")
        finally:
            db.close()
        rows = {(r["id"], r["field"], r["value"]) for r in health["off_list_values"]}
        assert ("pyrite-remove-static-renderer", "kind", "chore") in rows
        assert ("old-chore", "kind", "chore") in rows  # a subdirectory changes nothing
        assert not any(r["id"] == "fine" for r in health["off_list_values"])
        assert not any(r["field"] == "status" for r in health["off_list_values"])
        assert [r["id"] for r in health["invalid_statuses"]] == ["bad-status"]
        row = next(r for r in health["off_list_values"] if r["id"] == "old-chore")
        assert row["origin"] == "plugin" and row["severity"] == "warning"
        assert "bug" in row["allowed"]

    def test_plugin_rows_roll_up_as_warning_not_unhealthy(self, software_kb):
        config, _ = software_kb
        result = _cli(config, "index", "health", "-k", "pyrite", "--format", "json")
        out = _payload(result.output)
        assert out["status"] == "warning"
        assert result.exit_code == 0
        assert any(r["value"] == "chore" for r in out["off_list_values"])


# ==========================================================================
# 8. index health on kb.yaml enums
# ==========================================================================


@pytest.fixture
def health_env(tmp_path):
    def make(enforce_enums: bool | None = None, orgs: dict | None = None, extra_kb: bool = False):
        config, kb = _make_config(tmp_path, kb_yaml(enforce_enums))
        for eid, fields in (orgs or {}).items():
            _write_org(kb, eid, **fields)
        if extra_kb:
            other_cfg, other = _make_config(tmp_path, kb_yaml(True), name="other")
            _write_org(other, "elsewhere", org_type="bogus")
            config = PyriteConfig(
                knowledge_bases=[*config.knowledge_bases, *other_cfg.knowledge_bases],
                settings=config.settings,
            )
        db = PyriteDB(config.settings.index_path)
        IndexManager(db, config).index_all()
        db.close()
        return config

    return make


def _health(config, kb_name="t"):
    db = PyriteDB(config.settings.index_path)
    try:
        return IndexManager(db, config).check_health(kb_name=kb_name)
    finally:
        db.close()


class TestIndexHealth:
    def test_custom_field_in_metadata_and_rule_enum_are_reported(self, health_env, mode):
        config = health_env(
            mode,
            {
                "acme": {"org_type": "charity", "region": "east"},
                "beta": {"sectors": ["energy", "mining"]},
            },
        )
        rows = _health(config)["off_list_values"]
        got = {(r["id"], r["field"], json.dumps(r["value"]), r["origin"]) for r in rows}
        assert got == {
            ("acme", "org_type", '"charity"', "field"),
            ("acme", "region", '"east"', "rule"),
            ("beta", "sectors", '"mining"', "field"),
        }
        for r in rows:
            assert r["kb"] == "t" and r["type"] == "org"
            assert r["severity"] == ("error" if mode else "warning")
        assert next(r for r in rows if r["field"] == "org_type")["allowed"] == ["ngo", "company"]

    def test_a_value_flagged_by_field_options_and_a_rule_is_one_row(self, tmp_path):
        yaml_text = kb_yaml(True).replace(
            "    - field: region\n",
            "    - field: org_type\n      enum: [ngo, company]\n    - field: region\n",
        )
        config, kb = _make_config(tmp_path, yaml_text)
        _write_org(kb, "acme", org_type="charity")
        db = PyriteDB(config.settings.index_path)
        IndexManager(db, config).index_all()
        db.close()
        rows = _health(config)["off_list_values"]
        assert [(r["id"], r["field"], r["value"]) for r in rows] == [
            ("acme", "org_type", "charity")
        ]

    def test_kb_yaml_status_enum_is_not_reported_twice(self, tmp_path):
        """A kb.yaml rule enum on `status` reports here, unless the plugin's
        status check already put the entry in `invalid_statuses`."""
        pytest.importorskip("pyrite_software_kb")
        yaml_text = (
            "name: sw\nkb_type: software\nvalidation:\n  rules:\n"
            "    - field: status\n      enum: [proposed, done, wontfix]\n"
            "types:\n  backlog_item:\n    description: d\n    subdirectory: backlog\n"
        )
        config, kb = _make_config(tmp_path, yaml_text, kb_type="software", name="sw")
        (kb / "backlog").mkdir()
        for eid, status in (("both", "completed"), ("rule-only", "in_progress")):
            (kb / "backlog" / f"{eid}.md").write_text(
                f"---\nid: {eid}\ntype: backlog_item\ntitle: {eid}\nkind: bug\n"
                f"status: {status}\n---\n\nB.\n"
            )
        db = PyriteDB(config.settings.index_path)
        IndexManager(db, config).index_all()
        db.close()
        health = _health(config, "sw")
        assert [r["id"] for r in health["invalid_statuses"]] == ["both"]
        assert [(r["id"], r["field"], r["origin"]) for r in health["off_list_values"]] == [
            ("rule-only", "status", "rule")
        ]

    def test_allow_other_rows_are_info(self, health_env):
        config = health_env(True, {"acme": {"loose": "z"}})
        (row,) = _health(config)["off_list_values"]
        assert row["severity"] == "info"

    def test_clean_kb_has_an_empty_key(self, health_env):
        config = health_env(True, {"acme": {"org_type": "ngo", "region": "north"}})
        assert _health(config)["off_list_values"] == []

    def test_k_scoping(self, health_env):
        config = health_env(True, {"acme": {"org_type": "ngo"}}, extra_kb=True)
        assert _health(config, "t")["off_list_values"] == []
        assert [r["id"] for r in _health(config, "other")["off_list_values"]] == ["elsewhere"]
        assert {r["kb"] for r in _health(config, None)["off_list_values"]} == {"other"}

    def test_no_plugin_validators_registered_still_checks_kb_yaml_enums(self, health_env):
        config = health_env(True, {"acme": {"org_type": "charity"}})
        with patch("pyrite.plugins.registry.PluginRegistry.get_validators_for_kb", return_value=[]):
            rows = _health(config)["off_list_values"]
        assert [r["field"] for r in rows] == ["org_type"]

    def test_typed_columns_are_read_as_the_file_wrote_them(self, tmp_path):
        """A task's `priority` column holds Pyrite's reading (5 for a file with
        none, 5 for `medium`), not the file's value (#554). A rule enum on
        `priority` must see the file: no row for an absent or on-list value,
        the file's own value for an off-list one."""
        yaml_text = (
            "name: t\nkb_type: generic\nvalidation:\n  rules:\n"
            "    - field: priority\n      enum: [low, medium, high]\n"
        )
        config, kb = _make_config(tmp_path, yaml_text)
        (kb / "tasks").mkdir()
        for eid, prio in (("none", None), ("med", "medium"), ("odd", "urgent")):
            extra = f"priority: {prio}\n" if prio else ""
            (kb / "tasks" / f"{eid}.md").write_text(
                f"---\nid: {eid}\ntype: task\ntitle: {eid}\n{extra}---\n\nB.\n"
            )
        db = PyriteDB(config.settings.index_path)
        IndexManager(db, config).index_all()
        db.close()
        rows = _health(config)["off_list_values"]
        assert [(r["id"], r["value"]) for r in rows] == [("odd", "urgent")]

    @pytest.mark.parametrize(
        ("enforce_enums", "status", "code"), [(True, "unhealthy", 1), (False, "warning", 0)]
    )
    def test_cli_roll_up_follows_the_switch(self, health_env, enforce_enums, status, code):
        config = health_env(enforce_enums, {"acme": {"org_type": "charity"}})
        result = _cli(config, "index", "health", "-k", "t", "--format", "json")
        out = _payload(result.output)
        assert out["status"] == status
        assert result.exit_code == code
        assert out["off_list_values"][0]["value"] == "charity"

    def test_cli_info_rows_do_not_change_status(self, health_env):
        config = health_env(True, {"acme": {"loose": "z"}})
        out = _payload(_cli(config, "index", "health", "-k", "t", "--format", "json").output)
        assert out["status"] == "healthy"
        assert len(out["off_list_values"]) == 1

    def test_cli_clean_kb_is_healthy_with_empty_key(self, health_env):
        config = health_env(True, {"acme": {"org_type": "ngo"}})
        out = _payload(_cli(config, "index", "health", "-k", "t", "--format", "json").output)
        assert out["status"] == "healthy"
        assert out["off_list_values"] == []

    def test_text_output_renders_ten_then_and_n_more(self, health_env):
        orgs = {f"org-{i:02d}": {"org_type": f"bad{i}"} for i in range(12)}
        config = health_env(False, orgs)
        result = _cli(config, "index", "health", "-k", "t", "--format", "rich", "--no-fail")
        assert "off-list" in result.output
        assert "bad0" in result.output
        assert "... and 2 more" in result.output


# ==========================================================================
# 6. qa validate and ci
# ==========================================================================


class TestQaAndCi:
    def test_qa_validate_reports_off_list_values_by_switch(self, health_env, mode):
        from pyrite.services.qa_service import QAService

        config = health_env(mode, {"acme": {"org_type": "charity", "region": "east"}})
        db = PyriteDB(config.settings.index_path)
        try:
            result = QAService(config, db).validate_kb("t", readable_kbs=UNSCOPED)
        finally:
            db.close()
        hits = [
            i
            for i in result["issues"]
            if i.get("rule") == "schema_violation" and i.get("field") in ("org_type", "region")
        ]
        assert {i["field"] for i in hits} == {"org_type", "region"}
        assert {i["severity"] for i in hits} == {"error" if mode else "warning"}
        assert any("charity" in i["message"] for i in hits)
        assert any("east" in i["message"] for i in hits)

    def test_ci_severity_error_passes_when_only_enum_warnings(self, health_env):
        config = health_env(False, {"acme": {"org_type": "charity"}})
        # At the default threshold the off-list value is listed, as a warning ...
        out = _payload(_cli(config, "ci", "--kb", "t", "--format", "json").output)
        enum_issues = [
            i for kb in out["kbs"] for i in kb["issues"] if "charity" in i.get("message", "")
        ]
        assert enum_issues and all(i["severity"] == "warning" for i in enum_issues)
        assert out["total_errors"] == 0
        # ... so `--severity error` passes.
        result = _cli(config, "ci", "--kb", "t", "--severity", "error", "--format", "json")
        assert result.exit_code == 0, result.output

    def test_ci_fails_when_on(self, health_env):
        config = health_env(True, {"acme": {"org_type": "charity"}})
        result = _cli(config, "ci", "--kb", "t", "--severity", "error", "--format", "json")
        assert result.exit_code == 1


# ==========================================================================
# 7. schema validate
# ==========================================================================


class TestSchemaValidate:
    def _run(self, config, kb):
        return _cli(config, "schema", "validate", str(kb))

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("org_type", "charity"),
            ("sectors", ["mining"]),
            ("labels", ["c"]),
            ("region", "east"),
        ],
    )
    def test_severity_follows_the_switch(self, tmp_path, mode, field, value):
        config, kb = _make_config(tmp_path, kb_yaml(mode))
        _write_org(kb, "acme", **{field: value})
        result = self._run(config, kb)
        assert "acme.md" in result.output
        assert field in result.output
        if mode:
            assert result.exit_code == 1, result.output
            assert "1 errors" in result.output
        else:
            assert result.exit_code == 0, result.output
            assert "0 errors" in result.output

    def test_allow_other_is_a_warning(self, tmp_path):
        config, kb = _make_config(tmp_path, kb_yaml(True))
        _write_org(kb, "acme", loose="z")
        result = self._run(config, kb)
        assert result.exit_code == 0, result.output
        assert "loose" in result.output

    def test_select_is_reported_once(self, tmp_path):
        config, kb = _make_config(tmp_path, kb_yaml(True))
        _write_org(kb, "acme", org_type="charity")
        result = self._run(config, kb)
        assert result.output.count("charity") == 1, result.output

    def test_options_values_conflict_is_a_schema_level_warning(self, tmp_path):
        yaml_text = kb_yaml(True).replace(
            "        values: [ngo, company]",
            "        values: [ngo]\n        options: [ngo, company]",
        )
        config, kb = _make_config(tmp_path, yaml_text)
        _write_org(kb, "acme", org_type="ngo")
        result = self._run(config, kb)
        assert result.exit_code == 0, result.output
        assert "org" in result.output and "org_type" in result.output
        assert "options" in result.output and "values" in result.output

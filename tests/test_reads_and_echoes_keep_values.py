"""Reads and echoes never corrupt a value (#554, #561).

#554: one task with `priority: medium` ended every task listing
(`int('medium')` in `TaskService.list_tasks`), and `qa validate` could neither
see a task's `priority` (the schema pass never selected the column) nor report
a priority that is not an integer from 1 to 10.

#561: an agent that sends a `kb_get` result back to `kb_update` with one field
changed rewrote others: `importance: high` became `5`, a task grew
`importance: 5` and `priority: '5'`, an event failed with `'str' object has no
attribute 'value'`, and `tags: Foo` was read (and written back) as characters.

Every test runs over a KB on disk with the real service, CLI or MCP handler:
the bugs live in the file -> model -> index -> read-result round trip.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.services.task_service import TaskService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

KB = "work"

#: The #554 KB's own schema: it retyped the core task's `priority` as words.
KB_YAML = """name: work
kb_type: generic
types:
  draft:
    description: A draft
    fields:
      draft_status:
        type: text
  task:
    description: A task
    required: [priority]
    fields:
      priority:
        type: select
        options: [critical, high, medium, low]
"""


def _task(tid: str, priority: object = None) -> str:
    line = "" if priority is None else f"priority: {priority}\n"
    return f"---\nid: {tid}\ntitle: Task {tid}\ntype: task\nstatus: open\n{line}---\n\nDo it.\n"


def _env(tmp_path: Path, files: dict[str, str], kb_yaml: str = KB_YAML):
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(kb_yaml, encoding="utf-8")
    for name, text in files.items():
        path = kb_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    db_path = tmp_path / "index.db"
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=db_path),
    )
    db = PyriteDB(db_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config, kb_path


def _with_db(config: PyriteConfig, fn):
    db = PyriteDB(config.settings.index_path)
    try:
        return fn(db)
    finally:
        db.close()


def _mcp(config: PyriteConfig):
    from pyrite.server.mcp_server import PyriteMCPServer

    return PyriteMCPServer(config, tier="write")


def _call(config: PyriteConfig, tool: str, args: dict) -> dict:
    server = _mcp(config)
    try:
        return server._dispatch_tool(tool, args)
    finally:
        server.close()


def _echo(config: PyriteConfig, entry_id: str, **change) -> dict:
    """What an agent does: `kb_get`, edit one field, send all of it back."""
    server = _mcp(config)
    try:
        got = server._dispatch_tool("kb_get", {"entry_id": entry_id, "kb_name": KB})["entry"]
        return server._dispatch_tool(
            "kb_update", {**got, "entry_id": entry_id, "kb_name": KB, **change}
        )
    finally:
        server.close()


# ---------------------------------------------------------------------------
# #554: one bad priority never fails a listing
# ---------------------------------------------------------------------------

MIXED = {
    "tasks/t-int.md": _task("t-int", 2),
    "tasks/t-low.md": _task("t-low", "low"),
    "tasks/t-medium.md": _task("t-medium", "medium"),
    "tasks/t-high.md": _task("t-high", "high"),
    "tasks/t-critical.md": _task("t-critical", "Critical"),
    "tasks/t-urgent.md": _task("t-urgent", "urgent"),
    "tasks/t-none.md": _task("t-none"),
    "tasks/t-quoted.md": _task("t-quoted", "'8'"),
    "tasks/t-float.md": _task("t-float", "7.0"),
    "tasks/t-float-quoted.md": _task("t-float-quoted", "'6.0'"),
}
EXPECTED = {
    "t-int": 2,
    "t-low": 3,
    "t-medium": 5,
    "t-high": 7,
    "t-critical": 9,
    "t-urgent": 5,
    "t-none": 5,
    "t-quoted": 8,
    "t-float": 7,
    "t-float-quoted": 6,
}


def test_a_kb_mixing_integer_and_word_priorities_lists_every_task(tmp_path, caplog):
    """The #554 KB: a schema retyping `priority` as words, files using them.
    Every task is listed, with the word read on the 1-10 scale."""
    with caplog.at_level(logging.WARNING):
        config, _ = _env(tmp_path, MIXED)
        tasks = _with_db(config, lambda db: TaskService(config, db).list_tasks(kb_name=KB))

    assert {t["id"]: t["priority"] for t in tasks} == EXPECTED
    # The model reads it that way too -- the reading every other caller gets.
    from pyrite.storage.repository import KBRepository

    repo = KBRepository(config.get_kb(KB))
    assert {tid: repo.load(tid).priority for tid in EXPECTED} == EXPECTED
    warned = caplog.text
    for word in ("low", "medium", "high", "Critical", "urgent"):
        assert f"'{word}'" in warned, warned
    assert "t-urgent" in warned


def test_task_list_cli_lists_every_task_when_one_has_a_word_priority(tmp_path):
    config, _ = _env(tmp_path, MIXED)
    with patch("pyrite.cli.context.load_config", return_value=config):
        result = CliRunner().invoke(app, ["task", "list", "-k", KB, "--format", "json"])
    assert result.exit_code == 0, result.output
    # stdout is the JSON document and nothing else: the word-priority warnings
    # go to logging (stderr), never into what a script parses.
    listed = json.loads(result.stdout)
    assert {t["id"]: t["priority"] for t in listed["tasks"]} == EXPECTED


@pytest.mark.control(
    reason="dev already logged to stderr; this pins that the per-load "
    "word-priority warning this PR adds never reaches --format json stdout"
)
def test_update_json_output_is_only_json_when_the_task_has_a_word_priority(tmp_path):
    """Loading a word-priority task logs a warning on every load; under
    `--format json` that warning must not reach stdout."""
    config, kb_path = _env(tmp_path, {"tasks/t.md": _task("t", "medium")})
    with patch("pyrite.cli.context.load_config", return_value=config):
        result = CliRunner().invoke(
            app, ["update", "t", "-k", KB, "-f", "title=Renamed", "--format", "json"]
        )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["updated"] is True
    assert "priority: medium" in (kb_path / "tasks" / "t.md").read_text(encoding="utf-8")


def test_an_index_row_written_before_the_fix_still_lists(tmp_path, caplog):
    """An index built before this change holds `medium` in the column until the
    file is re-indexed; the listing reads it the same way the model does."""
    config, _ = _env(tmp_path, {"tasks/t-int.md": _task("t-int", 4)})

    def run(db):
        db._raw_conn.execute("UPDATE entry SET priority = 'medium' WHERE id = 't-int'")
        db._raw_conn.commit()
        with caplog.at_level(logging.WARNING):
            return TaskService(config, db).list_tasks(kb_name=KB)

    tasks = _with_db(config, run)
    assert [(t["id"], t["priority"]) for t in tasks] == [("t-int", 5)]
    assert "'medium'" in caplog.text


@pytest.mark.control(
    reason="a word priority was always written back as the file had it; this "
    "pins that reading it as a number does not rewrite it on an unrelated update"
)
def test_an_update_keeps_a_word_priority_it_was_not_asked_to_change(tmp_path):
    config, kb_path = _env(tmp_path, {"tasks/t.md": _task("t", "medium")})
    path = kb_path / "tasks" / "t.md"
    before = path.read_text(encoding="utf-8")

    _with_db(config, lambda db: KBService(config, db).update("t", KB, {"title": "Renamed"}))

    assert path.read_text(encoding="utf-8") == before.replace("Task t", "Renamed")


# ---------------------------------------------------------------------------
# #554: qa validate reports a priority that is not an integer 1-10, and sees
# the one that is
# ---------------------------------------------------------------------------


def _qa(config: PyriteConfig, entry_id: str | None = None) -> list[dict]:
    from pyrite.services.access_policy import UNSCOPED
    from pyrite.services.qa_service import QAService

    def run(db):
        qa = QAService(config, db)
        if entry_id:
            return qa.validate_entry(entry_id, KB, readable_kbs=UNSCOPED)["issues"]
        return qa.validate_kb(KB, readable_kbs=UNSCOPED)["issues"]

    return _with_db(config, run)


NO_SCHEMA_YAML = "name: work\nkb_type: generic\n"


@pytest.mark.parametrize("kb_yaml", [KB_YAML, NO_SCHEMA_YAML], ids=["retyped", "no-schema"])
def test_qa_validate_reports_a_task_priority_that_is_not_an_integer_1_to_10(tmp_path, kb_yaml):
    files = {
        "tasks/t-ok.md": _task("t-ok", 7),
        "tasks/t-ok-quoted.md": _task("t-ok-quoted", "'3'"),
        "tasks/t-ok-float.md": _task("t-ok-float", "7.0"),
        "tasks/t-word.md": _task("t-word", "medium"),
        "tasks/t-float.md": _task("t-float", "7.5"),
        "tasks/t-big.md": _task("t-big", 42),
        "tasks/t-zero.md": _task("t-zero", 0),
        "tasks/t-none.md": _task("t-none"),
        # Not a task: another type's `priority` is that type's business.
        "notes/n.md": "---\nid: n\ntitle: N\ntype: note\npriority: high\n---\n\nB.\n",
    }
    config, _ = _env(tmp_path, files, kb_yaml)

    for entry_id in (None, "t-word"):
        found = {i["entry_id"]: i for i in _qa(config, entry_id) if i["rule"] == "task_priority"}
        want = {"t-word", "t-float", "t-big", "t-zero"} if entry_id is None else {"t-word"}
        assert set(found) == want, found
        for tid, issue in found.items():
            assert f"tasks/{tid}.md" in issue["message"], issue
            assert issue["field"] == "priority"
            assert "1 to 10" in issue["message"], issue


def test_qa_validate_sees_the_priority_a_task_keeps_as_an_attribute(tmp_path):
    """#554 comment: a schema requiring `priority` reported every task as
    `required, got None`, `priority: 5` included -- the schema pass never read
    the column TaskEntry keeps it in."""
    yaml = "name: work\nkb_type: generic\ntypes:\n  task:\n    required: [priority]\n"
    config, _ = _env(tmp_path, {"tasks/t.md": _task("t", 5)}, yaml)

    for issues in (_qa(config), _qa(config, "t")):
        required = [
            i for i in issues if i["rule"] == "schema_violation" and i["field"] == "priority"
        ]
        assert required == [], required


def test_qa_validate_reports_a_required_priority_the_task_file_lacks(tmp_path):
    """The index's `priority` column holds the model's default (5) for a task
    whose file has none; `required: [priority]` must still report it missing
    (round-2 review of #566)."""
    yaml = "name: work\nkb_type: generic\ntypes:\n  task:\n    required: [priority]\n"
    config, _ = _env(tmp_path, {"tasks/t.md": _task("t"), "tasks/u.md": _task("u", 3)}, yaml)

    for entry_id in (None, "t"):
        missing = {
            i["entry_id"]
            for i in _qa(config, entry_id)
            if i["rule"] == "schema_violation" and i["field"] == "priority"
        }
        assert missing == {"t"}, missing


def test_qa_validate_checks_a_word_priority_as_the_file_wrote_it(tmp_path):
    """The #554 schema retypes priority as words; the file says `medium`.
    The index holds Pyrite's reading (5); a select check against it reported
    `got 5`, contradicting the task_priority rule. The select sees `medium`."""
    config, _ = _env(tmp_path, {"tasks/t.md": _task("t", "medium")})

    for entry_id in (None, "t"):
        issues = _qa(config, entry_id)
        schema = [i for i in issues if i["rule"] == "schema_violation"]
        assert schema == [], schema
        assert [i["entry_id"] for i in issues if i["rule"] == "task_priority"] == ["t"]


def test_qa_validate_reports_once_a_kb_yaml_that_retypes_task_priority(tmp_path):
    """#554's first finding: the kb.yaml itself contradicts the core task
    model. qa names the kb.yaml and the field once, not only per entry."""
    files = {f"tasks/t{n}.md": _task(f"t{n}", "high") for n in range(3)}
    config, _ = _env(tmp_path, files)

    retyped = [i for i in _qa(config) if i["rule"] == "schema_retypes_core_field"]
    assert len(retyped) == 1, retyped
    issue = retyped[0]
    assert issue["field"] == "priority"
    assert "kb.yaml" in issue["message"] and "task" in issue["message"], issue
    assert "1 to 10" in issue["message"], issue


@pytest.mark.control(
    reason="dev has no schema_retypes_core_field rule, so it cannot fire there; "
    "this pins that the rule leaves a kb.yaml keeping priority a number alone"
)
@pytest.mark.parametrize(
    "field_yaml",
    ["{type: number}", "{type: select, options: [1, 2, 3, 4, 5]}", "{type: number, min: 1}"],
)
def test_qa_validate_accepts_a_kb_yaml_that_keeps_task_priority_a_number(tmp_path, field_yaml):
    yaml = (
        "name: work\nkb_type: generic\ntypes:\n  task:\n    fields:\n"
        f"      priority: {field_yaml}\n"
    )
    config, _ = _env(tmp_path, {"tasks/t.md": _task("t", 3)}, yaml)
    assert [i for i in _qa(config) if i["rule"] == "schema_retypes_core_field"] == []


@pytest.mark.control(
    reason="dev never read the typed fields, so it cannot fail there; this "
    "pins that reading them does not turn an unset attribute into a value"
)
def test_qa_validate_does_not_read_an_empty_typed_column_as_a_value(tmp_path):
    """The typed columns exist for every entry; a task with no assignee has
    `assignee = ''` there, which is not a value a kb.yaml select must allow."""
    yaml = (
        "name: work\nkb_type: generic\nvalidation:\n  enforce: true\ntypes:\n  task:\n"
        "    fields:\n      assignee:\n        type: select\n        options: [amy, mark]\n"
    )
    config, _ = _env(tmp_path, {"tasks/t.md": _task("t", 5)}, yaml)

    for issues in (_qa(config), _qa(config, "t")):
        assert [i for i in issues if i["field"] == "assignee"] == [], issues


@pytest.mark.control(
    reason="dev never read the typed fields, so it cannot fail there; this "
    "pins that reading them does not replace the value validation checks"
)
def test_qa_validate_checks_a_protocol_field_against_the_files_value(tmp_path):
    """A kb.yaml type with `protocols:` keeps each protocol field in metadata
    AND copies it, lossily, into the typed column (`[Paris, Lyon]` as a str,
    `2` as '2'). Validation must check the value the file holds, not the copy
    (round-1 review of #566)."""
    yaml = (
        "name: work\nkb_type: generic\ntypes:\n  site:\n    description: place\n"
        "    protocols: [locatable, prioritizable, temporal]\n    fields:\n"
        "      location: {type: multi-select, options: [Paris, Lyon]}\n"
        "      priority: {type: select, options: [1, 2, 3]}\n"
    )
    site = "---\nid: s1\ntitle: S1\ntype: site\nlocation: [Paris, Lyon]\npriority: 2\n---\n\nx\n"
    config, _ = _env(tmp_path, {"sites/s1.md": site}, yaml)

    for issues in (_qa(config), _qa(config, "s1")):
        assert [i for i in issues if i["rule"] == "schema_violation"] == [], issues


@pytest.mark.control(
    reason="dev never read the typed fields, so it cannot fail there; this "
    "pins that reading them does not replace the value validation checks"
)
def test_qa_validate_compares_a_task_priority_as_an_integer(tmp_path):
    """The index column is text, so a task's `priority: 7` comes back as '7';
    a schema declaring priority a select of integers must still accept it."""
    yaml = (
        "name: work\nkb_type: generic\nvalidation:\n  enforce: true\ntypes:\n  task:\n"
        "    fields:\n      priority: {type: select, options: [1, 3, 5, 7, 9]}\n"
    )
    config, _ = _env(tmp_path, {"tasks/t.md": _task("t", 7)}, yaml)

    for issues in (_qa(config), _qa(config, "t")):
        assert [i for i in issues if i["field"] == "priority"] == [], issues


# ---------------------------------------------------------------------------
# #561: an echoed read result changes only the field that changed
# ---------------------------------------------------------------------------

DRAFT = (
    "---\nid: d\ntitle: D\ntype: draft\ndraft_status: brief\nimportance: high\n"
    "tags: Foo\n---\n\nBody.\n"
)


def test_an_echo_keeps_a_word_importance_and_a_string_tag(tmp_path):
    config, kb_path = _env(tmp_path, {"drafts/d.md": DRAFT})
    path = kb_path / "drafts" / "d.md"

    res = _echo(config, "d", draft_status="ready")

    assert res.get("updated") is True, res
    assert path.read_text(encoding="utf-8") == DRAFT.replace("brief", "ready")
    # Still at the value kb_get returned: reported apart from the keys no
    # update ever writes, so the caller can tell the two apart.
    unchanged = set(res.get("unchanged", []))
    assert {"importance", "tags", "title", "body", "metadata"} <= unchanged, res
    assert not unchanged & set(res.get("ignored", [])), res
    assert {"id", "file_path", "indexed_at"} <= set(res.get("ignored", [])), res
    assert "draft_status" not in unchanged | set(res.get("ignored", []))


def test_an_echo_of_a_task_adds_no_key_the_file_lacked(tmp_path):
    text = _task("t")
    config, kb_path = _env(tmp_path, {"tasks/t.md": text})
    path = kb_path / "tasks" / "t.md"

    res = _echo(config, "t", title="Renamed")

    assert res.get("updated") is True, res
    assert path.read_text(encoding="utf-8") == text.replace("Task t", "Renamed")
    assert {"importance", "priority"} <= set(res.get("unchanged", [])), res


def test_an_echo_of_an_event_changes_only_its_title(tmp_path):
    text = "---\nid: e\ntitle: E\ntype: event\ndate: 2026-01-02\n---\n\nBody.\n"
    config, kb_path = _env(tmp_path, {"events/e.md": text})
    path = kb_path / "events" / "e.md"

    res = _echo(config, "e", title="Renamed")

    assert res.get("updated") is True, res
    assert path.read_text(encoding="utf-8") == text.replace("title: E", "title: Renamed")


@pytest.mark.control(
    reason="a lone field set always landed; this pins that setting aside echoed "
    "readings never swallows a value a caller named on its own"
)
def test_a_deliberate_mcp_set_equal_to_the_reading_still_lands(tmp_path):
    """`{importance: 5}` over `importance: high` is not an echo: nothing in the
    request came from a read result, so 5 is the caller's value."""
    config, kb_path = _env(tmp_path, {"drafts/d.md": DRAFT})
    path = kb_path / "drafts" / "d.md"

    res = _call(config, "kb_update", {"entry_id": "d", "kb_name": KB, "importance": 5})

    assert res.get("updated") is True, res
    assert "importance" not in res.get("ignored", []) + res.get("unchanged", [])
    assert path.read_text(encoding="utf-8") == DRAFT.replace("importance: high", "importance: 5")


def test_a_deliberate_event_status_lands_and_an_unknown_one_is_refused(tmp_path):
    """The event echo crash was an assignment of a str to an Enum field; a
    deliberate `status` set failed the same way."""
    text = "---\nid: e\ntitle: E\ntype: event\ndate: 2026-01-02\n---\n\nBody.\n"
    config, kb_path = _env(tmp_path, {"events/e.md": text})
    path = kb_path / "events" / "e.md"

    res = _call(config, "kb_update", {"entry_id": "e", "kb_name": KB, "status": "disputed"})
    assert res.get("updated") is True, res
    assert "status: disputed" in path.read_text(encoding="utf-8")

    before = path.read_text(encoding="utf-8")
    res = _call(config, "kb_update", {"entry_id": "e", "kb_name": KB, "status": "bogus"})
    assert res.get("updated") is not True, res
    assert res.get("error_code") != "INTERNAL", res
    assert "confirmed" in json.dumps(res), res
    assert path.read_text(encoding="utf-8") == before


def test_cli_update_sets_an_event_status_and_refuses_an_unknown_one(tmp_path):
    """`pyrite update -f status=...` reaches the same Enum conversion as MCP:
    a known status lands, an unknown one is refused before anything is written."""
    text = "---\nid: e\ntitle: E\ntype: event\ndate: 2026-01-02\n---\n\nBody.\n"
    config, kb_path = _env(tmp_path, {"events/e.md": text})
    path = kb_path / "events" / "e.md"

    def update(status: str):
        with patch("pyrite.cli.context.load_config", return_value=config):
            return CliRunner().invoke(app, ["update", "e", "-k", KB, "-f", f"status={status}"])

    result = update("disputed")
    assert result.exit_code == 0, result.output
    assert "status: disputed" in path.read_text(encoding="utf-8")

    before = path.read_text(encoding="utf-8")
    result = update("bogus")
    assert result.exit_code != 0, result.output
    assert "confirmed" in result.output, result.output
    assert "has no attribute" not in result.output, result.output
    assert path.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# #561: a string `tags:` is one tag, a bare URL source keeps its URL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["tags", "aliases"])
def test_a_string_list_field_is_read_as_one_item(tmp_path, key):
    text = f"---\nid: n\ntitle: N\ntype: note\n{key}: Foo\n---\n\nBody.\n"
    config, _ = _env(tmp_path, {"notes/n.md": text})

    from pyrite.storage.repository import KBRepository

    entry = KBRepository(config.get_kb(KB)).load("n")
    assert getattr(entry, key) == ["Foo"]
    if key == "tags":
        got = _call(config, "kb_get", {"entry_id": "n", "kb_name": KB})["entry"]
        assert got["tags"] == ["Foo"], got


def test_a_bare_url_source_is_read_with_its_url(tmp_path):
    text = "---\nid: n\ntitle: N\ntype: note\nsources:\n- https://x.org/a\n- a book\n---\n\nB.\n"
    config, kb_path = _env(tmp_path, {"notes/n.md": text})

    from pyrite.storage.repository import KBRepository

    entry = KBRepository(config.get_kb(KB)).load("n")
    assert [(s.title, s.url) for s in entry.sources] == [
        ("https://x.org/a", "https://x.org/a"),
        ("a book", ""),
    ]
    # Read losslessly, so there is nothing for reserved_key_collision to report,
    # and an unrelated update keeps the file's shorthand.
    assert [i for i in _qa(config) if i["rule"] == "reserved_key_collision"] == []
    _with_db(config, lambda db: KBService(config, db).update("n", KB, {"title": "M"}))
    assert (kb_path / "notes" / "n.md").read_text(encoding="utf-8") == text.replace(
        "title: N", "title: M"
    )

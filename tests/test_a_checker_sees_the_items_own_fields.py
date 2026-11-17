"""A rubric checker gives the item's verdict, the same in every caller (#1587).

The property: a checker named in a ``board.yaml`` gate (the five gate tools and
the load-time dry run) and in QA (``validate_entry`` and the whole-KB pass)
sees **the item's own fields wherever the index keeps them**: in the
``metadata`` bag, in their own column (``status``, ``priority``,
``assignee``, the dates, ...), in the tag table, or in the link table. One
builder makes that entry (``pyrite.services.checker_entry``), and no caller
builds it itself.

Every case runs on a real KB through the real surfaces: files on disk, the real
indexer, the MCP dispatcher's gate tools, and ``QAService``. The expected
verdicts are written by hand from the files below, never computed by the code
under test.

A column can hold Pyrite's reading rather than a line in the file: a
``backlog_item`` that writes no ``priority`` reads ``priority: medium``, as
``pyrite get`` shows it. The checker sees the same reading (the ``no-priority-line``
cells).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.rubric_checkers import NAMED_CHECKERS
from tests._world import build_world
from tests.test_gate_lookup_failure_keeps_its_error import GATE_OF_TOOL

KB = "sw"
_ROOT = Path(__file__).resolve().parent.parent

# -- the files -----------------------------------------------------------------

#: id -> the whole entry file. Each verdict below is read off these by hand.
FILES: dict[str, str] = {
    # Holds every criterion it is the holding item for.
    "full": """\
---
id: full
title: A full item
type: backlog_item
kind: bug
status: accepted
priority: high
effort: S
assignee: someone
summary: one line
tags: [alpha, beta]
---
## Context

body with a [[some-other-item]] link

```
code
```
""",
    # Fails every criterion it is the failing item for: a generic title, an
    # empty status and priority, no effort, assignee, tags, links, section or
    # code block, and no parent epic.
    "bare": """\
---
id: bare
title: todo
type: backlog_item
kind: bug
status: ""
priority: ""
---
plain words only
""",
    # XL with a has_subtask link: not oversized. A subtask_of link: not an orphan.
    "split": """\
---
id: split
title: A split item
type: backlog_item
kind: feature
status: accepted
effort: XL
links:
  - {target: epic, relation: subtask_of}
  - {target: epic, relation: has_subtask}
---
split body
""",
    # XL with no has_subtask link: oversized.
    "big": """\
---
id: big
title: A big item
type: backlog_item
kind: feature
status: accepted
effort: XL
---
big body
""",
    "epic": """\
---
id: epic
title: An epic
type: backlog_item
kind: epic
status: accepted
---
epic body
""",
    # Writes no priority line: the model reads `priority: medium`, the column
    # holds it, and `pyrite get` shows it.
    "no-priority-line": """\
---
id: no-priority-line
title: An item with no priority line
type: backlog_item
kind: bug
status: accepted
---
body
""",
    # A kb.yaml type with protocols: its `priority` and `due_date` are in the
    # column AND in the bag.
    "ticket": """\
---
id: ticket
title: A ticket
type: ticket
priority: high
due_date: "2026-12-01"
---
ticket body
""",
    # A core type whose `date` is an attribute: in the column only.
    "event": """\
---
id: event
title: An event
type: event
date: "2026-01-02"
---
event body
""",
}

KB_YAML_HEAD = """\
name: sw
types:
  ticket:
    description: A kb.yaml type that declares protocols
    protocols: [prioritizable, temporal]
"""

# -- the cases -----------------------------------------------------------------

#: case -> (criterion, the item it holds for, the item it fails for). The
#: criterion's ``text`` is the case name, so a gate's criterion result and a QA
#: finding (``rubric_item``) are found by it.
CASES: dict[str, tuple[dict, str, str]] = {
    "descriptive_title": ({"checker": "descriptive_title"}, "full", "bare"),
    "has_tags": ({"checker": "has_tags"}, "full", "bare"),
    "has_outlinks": ({"checker": "has_outlinks"}, "full", "bare"),
    "status_present": ({"checker": "status_present"}, "full", "bare"),
    "priority_present": ({"checker": "priority_present"}, "full", "bare"),
    "has_field-effort": ({"checker": "has_field", "params": {"field": "effort"}}, "full", "bare"),
    "has_field-priority": (
        {"checker": "has_field", "params": {"field": "priority"}},
        "full",
        "bare",
    ),
    "has_field-status": ({"checker": "has_field", "params": {"field": "status"}}, "full", "bare"),
    "has_field-assignee": (
        {"checker": "has_field", "params": {"field": "assignee"}},
        "full",
        "bare",
    ),
    "has_field-tags": ({"checker": "has_field", "params": {"field": "tags"}}, "full", "bare"),
    "has_any_field": (
        {"checker": "has_any_field", "params": {"fields": ["assignee", "priority"]}},
        "full",
        "bare",
    ),
    "body_has_section": (
        {"checker": "body_has_section", "params": {"heading": "Context"}},
        "full",
        "bare",
    ),
    "body_has_pattern": (
        {"checker": "body_has_pattern", "params": {"pattern": "some-other"}},
        "full",
        "bare",
    ),
    "body_has_code_block": ({"checker": "body_has_code_block"}, "full", "bare"),
    "not_oversized": ({"checker": "not_oversized"}, "split", "big"),
    "orphan_backlog_item": ({"checker": "orphan_backlog_item"}, "split", "bare"),
}

#: location -> (criterion, item): a field the item has, wherever the index
#: keeps it; it holds in every caller that can run on the item (the gate runs
#: on backlog items only).
LOCATIONS: dict[str, tuple[dict, str]] = {
    "bag-effort": ({"checker": "has_field", "params": {"field": "effort"}}, "full"),
    "column-priority": ({"checker": "has_field", "params": {"field": "priority"}}, "full"),
    "column-status": ({"checker": "has_field", "params": {"field": "status"}}, "full"),
    "column-assignee": ({"checker": "has_field", "params": {"field": "assignee"}}, "full"),
    "column-summary": ({"checker": "has_field", "params": {"field": "summary"}}, "full"),
    "column-priority-read-by-the-model": (
        {"checker": "priority_present"},
        "no-priority-line",
    ),
    "column-date-of-a-core-type": ({"checker": "has_field", "params": {"field": "date"}}, "event"),
    "tag-table": ({"checker": "has_field", "params": {"field": "tags"}}, "full"),
    "column-and-bag-priority": (
        {"checker": "has_field", "params": {"field": "priority"}},
        "ticket",
    ),
    "column-and-bag-due_date": (
        {"checker": "has_field", "params": {"field": "due_date"}},
        "ticket",
    ),
}

CALLERS = ("gate", "qa-entry", "qa-kb")

#: The cells `dev` (0d04f491) answers wrongly, read off the reproduction in the
#: plan on #1660. Every other cell is a control.
RED_ON_DEV: set[tuple[str, str, str]] = {
    *(
        ("gate", case, "holds")
        for case in (
            "has_tags",
            "has_outlinks",
            "priority_present",
            "has_field-priority",
            "has_field-status",
            "has_field-assignee",
            "has_field-tags",
            "has_any_field",
        )
    ),
    *(
        (caller, case, "holds")
        for caller in ("qa-entry", "qa-kb")
        for case in (
            "priority_present",
            "has_field-priority",
            "has_field-status",
            "has_field-assignee",
            "has_field-tags",
            "has_any_field",
            "not_oversized",
            "orphan_backlog_item",
        )
    ),
}
LOCATION_CONTROLS = {
    # The bag holds them on `dev` too.
    "bag-effort": "effort is in the bag on dev",
    "column-and-bag-priority": "a kb.yaml protocols type keeps priority in the bag too",
    "column-and-bag-due_date": "a kb.yaml protocols type keeps due_date in the bag too",
}


def _criteria(named: dict[str, dict]) -> list[dict]:
    return [{"text": text, **criterion} for text, criterion in named.items()]


def _all_criteria() -> list[dict]:
    return _criteria(
        {case: c for case, (c, _h, _f) in CASES.items()}
        | {f"at-{loc}": c for loc, (c, _i) in LOCATIONS.items()}
    )


def _board(criteria: list[dict], statuses=("in_progress",)) -> str:
    gates = {s: {"policy": "enforce", "criteria": criteria} for s in statuses}
    return json.dumps({"gates": gates})  # JSON is YAML


def _kb_yaml(criteria: list[dict]) -> str:
    return KB_YAML_HEAD + "evaluation_rubric: " + json.dumps(criteria) + "\n"


def _seed(kb_dir: Path, files: dict[str, str]) -> None:
    kb_dir.mkdir(parents=True, exist_ok=True)
    for entry_id, text in files.items():
        folder = kb_dir / ("backlog" if "backlog_item" in text else "other")
        folder.mkdir(exist_ok=True)
        (folder / f"{entry_id}.md").write_text(text)


def _world(root: Path, *, board: str | None, rubric: list[dict]):
    kb_dir = root / "kbs" / KB
    _seed(kb_dir, FILES)
    (kb_dir / "kb.yaml").write_text(_kb_yaml(rubric))
    if board is not None:
        (kb_dir / "board.yaml").write_text(board)
    return build_world(root, kbs=[KBConfig(name=KB, path=kb_dir, kb_type="software")], rest=False)


def _mcp(world, tool: str, args: dict) -> dict:
    return world.mcp(tier="admin")._dispatch_tool(tool, dict(args))


def _gate_verdicts(answer: dict) -> dict[str, bool]:
    assert "error_code" not in answer, answer
    return {c["text"]: c["passed"] for c in answer["gate"]["criteria"]}


def _qa_findings(issues: list[dict], entry_id: str) -> set[str]:
    """The rubric texts QA reports as not holding for ``entry_id``."""
    return {
        i["rubric_item"]
        for i in issues
        if i.get("entry_id") == entry_id and i.get("rule") == "rubric_violation"
    }


@pytest.fixture(scope="module")
def answers(tmp_path_factory):
    """Every caller's verdicts for every item, from one KB: {caller: {item:
    {criterion text: holds}}}, plus the built-in board's for the backlog items."""
    from pyrite.services.qa_service import QAService

    criteria = _all_criteria()
    texts = [c["text"] for c in criteria]
    world = _world(tmp_path_factory.mktemp("w"), board=None, rubric=criteria)
    try:
        backlog = [i for i, text in FILES.items() if "backlog_item" in text]
        out: dict = {"default-board": {}, "gate": {}, "qa-entry": {}, "qa-kb": {}}
        for item in backlog:
            answer = _mcp(world, "sw_check_ready", {"item_id": item, "kb_name": KB})
            out["default-board"][item] = _gate_verdicts(answer)
        (world.kb_path(KB) / "board.yaml").write_text(_board(criteria))
        for item in backlog:
            answer = _mcp(world, "sw_check_ready", {"item_id": item, "kb_name": KB})
            out["gate"][item] = _gate_verdicts(answer)
        qa = QAService(world.config, world.db)
        sweep = qa.validate_kb(KB, readable_kbs=UNSCOPED)["issues"]
        for item in FILES:
            one = qa.validate_entry(item, KB, readable_kbs=UNSCOPED)["issues"]
            failed = _qa_findings(one, item)
            out["qa-entry"][item] = {t: t not in failed for t in texts}
            failed = _qa_findings(sweep, item)
            out["qa-kb"][item] = {t: t not in failed for t in texts}
        yield out
    finally:
        world.close()


# -- the verdict, in every caller ----------------------------------------------


def _verdict_cells():
    cells = []
    for caller in CALLERS:
        for case in CASES:
            for verdict in ("holds", "fails"):
                marks = []
                if (caller, case, verdict) not in RED_ON_DEV:
                    marks.append(pytest.mark.control(reason="dev answers this cell rightly"))
                cells.append(pytest.param(caller, case, verdict, marks=marks))
    return cells


@pytest.mark.parametrize(("caller", "case", "verdict"), _verdict_cells())
def test_a_checker_gives_the_files_verdict(answers, caller, case, verdict):
    _criterion, holds, fails = CASES[case]
    item = holds if verdict == "holds" else fails
    assert answers[caller][item][case] is (verdict == "holds"), (caller, item, case)


def test_every_named_checker_has_a_case():
    """A new checker cannot be added without a holding and a failing item."""
    assert {c["checker"] for c, _h, _f in CASES.values()} == set(NAMED_CHECKERS)


def _location_cells():
    cells = []
    for loc, (_c, item) in LOCATIONS.items():
        callers = CALLERS if "backlog_item" in FILES[item] else ("qa-entry", "qa-kb")
        for caller in callers:
            marks = []
            if loc in LOCATION_CONTROLS:
                marks.append(pytest.mark.control(reason=LOCATION_CONTROLS[loc]))
            cells.append(pytest.param(loc, caller, marks=marks))
    return cells


@pytest.mark.parametrize(("location", "caller"), _location_cells())
def test_has_field_reads_a_field_wherever_the_index_stores_it(answers, location, caller):
    _criterion, item = LOCATIONS[location]
    assert answers[caller][item][f"at-{location}"] is True, (caller, item, location)


#: The built-in board's checker verdicts for each backlog item, by hand:
#: effort present, not XL without subtasks, no blockers.
DEFAULT_BOARD = {
    "full": {"Effort estimated": True, "Not oversized (XL+) without subtasks": True},
    "bare": {"Effort estimated": False, "Not oversized (XL+) without subtasks": True},
    "split": {"Effort estimated": True, "Not oversized (XL+) without subtasks": True},
    "big": {"Effort estimated": True, "Not oversized (XL+) without subtasks": False},
}


@pytest.mark.control(reason="the built-in board names only fields dev already reads")
@pytest.mark.parametrize("item", sorted(DEFAULT_BOARD))
def test_the_built_in_board_keeps_its_verdicts(answers, item):
    got = answers["default-board"][item]
    for text, holds in DEFAULT_BOARD[item].items():
        assert got[text] is holds, (item, text, got)
    assert got["No unresolved blockers"] is True


# -- every gate tool -----------------------------------------------------------

#: The gate criteria every gate tool is checked with: one per place a field
#: lives that `dev` got wrong in a gate (column, tag table, link table).
_TOOL_CRITERIA = _criteria(
    {
        "priority": {"checker": "priority_present"},
        "assignee": {"checker": "has_field", "params": {"field": "assignee"}},
        "tags": {"checker": "has_tags"},
        "outlinks": {"checker": "has_outlinks"},
    }
)

#: tool -> (the item's status before the call, the call's arguments for `item`).
TOOL_CALLS = {
    "sw_check_ready": ("accepted", lambda item: {"item_id": item, "kb_name": KB}),
    "sw_claim": ("accepted", lambda item: {"item_id": item, "kb_name": KB, "assignee": "probe"}),
    "sw_transition": (
        "accepted",
        lambda item: {"item_id": item, "kb_name": KB, "to_status": "in_progress"},
    ),
    "sw_review": (
        "review",
        lambda item: {"item_id": item, "kb_name": KB, "outcome": "approved", "reviewer": "r"},
    ),
    "sw_refine": ("accepted", lambda item: {"kb_name": KB}),
}


def _tool_item(entry_id: str, status: str, *, holds: bool) -> str:
    if holds:
        fields = "priority: high\nassignee: someone\ntags: [alpha]\n"
        body = "see [[elsewhere]]\n"
    else:
        fields = 'priority: ""\n'
        body = "plain\n"
    return (
        f"---\nid: {entry_id}\ntitle: {entry_id}\ntype: backlog_item\nkind: bug\n"
        f"status: {status}\neffort: S\n{fields}---\n{body}"
    )


def _gate_of_tool_answer(tool: str, answer: dict, item: str) -> dict[str, bool]:
    if tool == "sw_refine":
        (row,) = [i for i in answer["items"] if i["id"] == item]
        return {c["text"]: c["passed"] for c in row["gate"]["criteria"]}
    return _gate_verdicts(answer)


def test_every_gate_tool_is_listed():
    assert set(TOOL_CALLS) == set(GATE_OF_TOOL)


@pytest.mark.parametrize("holds", [True, False], ids=["holds", "fails"])
@pytest.mark.parametrize("tool", sorted(GATE_OF_TOOL))
def test_every_gate_tool_gives_the_files_verdict(tmp_path, tool, holds, request):
    if not holds:
        request.applymarker(pytest.mark.control(reason="dev fails these criteria for every item"))
    status, args = TOOL_CALLS[tool]
    kb_dir = tmp_path / "kbs" / KB
    kb_dir.mkdir(parents=True)
    (kb_dir / "board.yaml").write_text(_board(_TOOL_CRITERIA, statuses=("in_progress", "done")))
    item = "probe-item"
    (kb_dir / f"{item}.md").write_text(_tool_item(item, status, holds=holds))
    world = build_world(
        tmp_path / "w", kbs=[KBConfig(name=KB, path=kb_dir, kb_type="software")], rest=False
    )
    try:
        answer = _mcp(world, tool, args(item))
        got = _gate_of_tool_answer(tool, answer, item)
        assert got == {c["text"]: holds for c in _TOOL_CRITERIA}, (tool, answer)
        if holds:
            done = {"sw_claim": "claimed", "sw_transition": "transitioned", "sw_review": "reviewed"}
            if tool in done:
                assert answer.get(done[tool]) is True, answer
    finally:
        world.close()


# -- one builder ---------------------------------------------------------------

_BUILDER = _ROOT / "pyrite/services/checker_entry.py"
_DERIVED = {"_tag_count", "_outlink_count", "_links"}


def _sources() -> list[Path]:
    paths = list((_ROOT / "pyrite").rglob("*.py"))
    for src in (_ROOT / "extensions").glob("*/src"):
        paths.extend(src.rglob("*.py"))
    return [p for p in paths if p != _BUILDER]


def _writes_a_derived_key(tree: ast.AST) -> list[int]:
    """Lines that put a derived key into a dict: ``d["_links"] = ...``, a dict
    literal ``{"_links": ...}``, or ``dict(_links=...)``."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if (
                    isinstance(t, ast.Subscript)
                    and isinstance(t.slice, ast.Constant)
                    and t.slice.value in _DERIVED
                ):
                    lines.append(node.lineno)
        elif isinstance(node, ast.Dict):
            if any(isinstance(k, ast.Constant) and k.value in _DERIVED for k in node.keys):
                lines.append(node.lineno)
        elif isinstance(node, ast.Call):
            if any(kw.arg in _DERIVED for kw in node.keywords):
                lines.append(node.lineno)
    return lines


def test_no_caller_builds_a_checker_entry_itself():
    """Only the builder module puts ``_tag_count``, ``_outlink_count`` or
    ``_links`` into the entry a checker sees, and every caller asks it."""
    found = {}
    for path in _sources():
        lines = _writes_a_derived_key(ast.parse(path.read_text()))
        if lines:
            found[str(path.relative_to(_ROOT))] = lines
    assert found == {}, found
    assert _writes_a_derived_key(ast.parse(_BUILDER.read_text()))

    def calls_in(path: Path, function: str) -> set[str]:
        tree = ast.parse(path.read_text())
        (fn,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function]
        return {
            n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
        }

    plugin = _ROOT / "extensions/software-kb/src/pyrite_software_kb/plugin.py"
    board = _ROOT / "extensions/software-kb/src/pyrite_software_kb/board.py"
    qa = _ROOT / "pyrite/services/qa_service.py"
    assert "checker_entry" in calls_in(plugin, "_evaluate_gate")
    assert "checker_entry" in calls_in(qa, "_check_rubric_evaluation")
    assert "empty_checker_entry" in calls_in(board, "check_criterion")


def test_the_builders_field_columns_are_the_entry_tables():
    """Every column of ``entry`` that holds an item's field is projected; a
    new column needs a decision here."""
    from pyrite.services.checker_entry import FIELD_COLUMNS
    from pyrite.storage.models import Entry

    not_fields = {
        "id",
        "kb_name",
        "entry_type",
        "body",
        "file_path",
        "metadata",
        "aliases",
        "created_at",
        "updated_at",
        "indexed_at",
        "content_hash",
        "file_mtime_ns",
        "file_size",
        "created_by",
        "modified_by",
    }
    columns = {c.name for c in Entry.__table__.columns}
    assert set(FIELD_COLUMNS) == columns - not_fields

"""Field operations on the surfaces and in the in-process callers (#955, B6 P3b).

ADR-0042 decision 1: a write names an operation. After P3a a surface could
only *set* a field, so a caller that wanted to add one tag or one link had
to send the whole list, and a caller that read the list through the model
sent the model's reading back (the #730 spike's surprise 3: ``since: 2020``
and a comment on a legacy link item were lost). Now:

- ``append`` and ``remove`` add or take one item (or each of a list of
  items) from a list, and ``unset`` removes a key with its own lines, on
  ``KBService.update``, MCP ``kb_update`` (``append``/``remove``/``unset``
  arguments), REST ``PATCH`` (``op``) and the CLI (``--append``, ``--remove``,
  ``--unset``);
- ``null`` is a value, not a removal;
- none of the five in-process callers (``qa fix``'s link and tag repairs,
  ``TaskService``'s evidence writers and ``update_task``'s audit trail)
  sends a list read through the model: each sends an operation, and the
  items it did not name keep their bytes.

The oracle is bytes outside the named key's span (``file_operations_oracle``).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app as cli_app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import ValidationError
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.file_operations import Append, FrontmatterSpan, Remove, Set, Unset
from pyrite.storage.index import IndexManager
from pyrite.utils.frontmatter import Frontmatter, split_frontmatter
from tests.file_operations_oracle import op_spans, outside_identical

KB = "ops"
ID = "ops-entry"

NOTE = (
    "---\n"
    "# kept by hand\n"
    f"id: {ID}\n"
    "title: Ops   # keep\n"
    "type: note\n"
    "tags: [alpha, beta]  # trailing\n"
    "links:\n"
    "- to: foo          # legacy spelling, hand written\n"
    "  type: related\n"
    "  since: 2020\n"
    "extra: 1\n"
    "---\n"
    "\n"
    "Text.\n"
)


def _build(tmp_path: Path, text: str = NOTE, *, crlf: bool = True):
    kb_path = tmp_path / "kb"
    (kb_path / "notes").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30)
    path = kb_path / "notes" / f"{ID}.md"
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config, path


def _text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _span(text: str) -> FrontmatterSpan:
    split = split_frontmatter(text)
    assert isinstance(split, Frontmatter), split
    return FrontmatterSpan(split.yaml_start, split.close_start, split.close_end)


def assert_only_changed(before: str, after: str, ops: list[Any]) -> None:
    span = _span(before)
    spans: list[tuple[int, int]] = []
    for op in ops:
        spans += op_spans(before, span, op)
    assert outside_identical(before, after, spans), f"--- before\n{before!r}\n--- after\n{after!r}"
    assert after.count("\r\n") == after.count("\n"), "a bare LF was written into a CRLF file"


def _with_service(config: PyriteConfig, fn: Callable[[KBService], Any]) -> Any:
    db = PyriteDB(config.settings.index_path)
    try:
        return fn(KBService(config, db))
    finally:
        db.close()


# --------------------------------------------------------------------------
# The surfaces: each sends one operation for ``ID`` and raises on a refusal
# --------------------------------------------------------------------------


def _service(config, *, append=None, remove=None, unset=None, set=None):
    return _with_service(
        config,
        lambda svc: svc.update(ID, KB, dict(set or {}), append=append, remove=remove, unset=unset),
    )


def _mcp(config, *, append=None, remove=None, unset=None, set=None):
    from pyrite.server.mcp_server import PyriteMCPServer

    args: dict[str, Any] = {"kb_name": KB, "entry_id": ID, **(set or {})}
    if append is not None:
        args["append"] = append
    if remove is not None:
        args["remove"] = remove
    if unset is not None:
        args["unset"] = unset
    server = PyriteMCPServer(config, tier="write")
    try:
        result = server._dispatch_tool("kb_update", args)
    finally:
        server.close()
    if result.get("error") or result.get("isError"):
        raise ValidationError(str(result))
    return result


def _rest(config, *, append=None, remove=None, unset=None, set=None):
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from fastapi.testclient import TestClient

    from pyrite.server.api import create_app, get_config, get_db, get_index_mgr

    db = PyriteDB(config.settings.index_path)
    index_mgr = IndexManager(db, config)
    app = create_app(config)
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_index_mgr] = lambda: index_mgr
    client = TestClient(app, raise_server_exceptions=False)
    try:
        calls: list[dict[str, Any]] = []
        for key, value in (set or {}).items():
            calls.append({"kb": KB, "field": key, "value": value})
        for op, mapping in (("append", append), ("remove", remove)):
            for key, value in (mapping or {}).items():
                calls.append({"kb": KB, "field": key, "value": value, "op": op})
        for key in unset or []:
            calls.append({"kb": KB, "field": key, "op": "unset"})
        results = []
        for body in calls:
            res = client.patch(f"/api/entries/{ID}", json=body)
            if res.status_code != 200:
                raise ValidationError(res.text)
            results.append(res.json())
    finally:
        client.close()
        db.close()
    return results


def _cli(config, *, append=None, remove=None, unset=None, set=None):
    import json

    args = ["update", ID, "-k", KB, "--format", "json"]
    for key, value in (set or {}).items():
        args += ["-f", f"{key}={json.dumps(value) if value is None else value}"]
    for key, value in (append or {}).items():
        args += ["--append", f"{key}={json.dumps(value) if not isinstance(value, str) else value}"]
    for key, value in (remove or {}).items():
        args += ["--remove", f"{key}={json.dumps(value) if not isinstance(value, str) else value}"]
    for key in unset or []:
        args += ["--unset", key]
    with (
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        result = CliRunner().invoke(cli_app, args)
    if result.exit_code != 0:
        raise ValidationError(result.output)
    return result.output


SURFACES = {"service": _service, "mcp": _mcp, "rest.patch": _rest, "cli": _cli}


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_append_adds_one_item_and_leaves_the_rest_alone(tmp_path: Path, surface: str):
    config, path = _build(tmp_path)
    before = _text(path)

    SURFACES[surface](config, append={"tags": "gamma"})

    after = _text(path)
    assert "tags: [alpha, beta, gamma]  # trailing" in after, after
    assert_only_changed(before, after, [Append("tags", "gamma"), Set("updated_at", "x")])


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_remove_takes_one_item_and_leaves_the_rest_alone(tmp_path: Path, surface: str):
    config, path = _build(tmp_path)
    before = _text(path)

    SURFACES[surface](config, remove={"tags": "alpha"})

    after = _text(path)
    assert "tags: [beta]  # trailing" in after, after
    assert_only_changed(before, after, [Remove("tags", "alpha"), Set("updated_at", "x")])


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_unset_removes_the_key_with_its_own_lines(tmp_path: Path, surface: str):
    config, path = _build(tmp_path)
    before = _text(path)

    SURFACES[surface](config, unset=["extra"])

    after = _text(path)
    assert "extra" not in after, after
    assert_only_changed(before, after, [Unset("extra"), Set("updated_at", "x")])


@pytest.mark.parametrize("surface", ["service", "rest.patch"])
def test_null_is_a_value_not_a_removal(tmp_path: Path, surface: str):
    """The CLI's ``-f k=null`` is the string ``null`` by its own parser's
    rule, and MCP ``kb_update`` sets a ``null`` aside for an undeclared key
    (#455, ``ignored``); neither removes anything. The service and PATCH
    write the value."""
    config, path = _build(tmp_path)

    SURFACES[surface](config, set={"extra": None})

    after = _text(path)
    assert "\r\nextra: null\r\n" in after, after


@pytest.mark.control(
    reason="#455's rule, unchanged here: the control pins that an operation surface did not change what MCP does with a null"
)
def test_mcp_sets_a_null_aside_and_removes_nothing(tmp_path: Path):
    config, path = _build(tmp_path)
    before = path.read_bytes()

    result = _mcp(config, set={"extra": None})

    assert result.get("ignored") == ["extra"], result
    assert path.read_bytes() == before


@pytest.mark.parametrize("surface", ["service", "mcp"])
def test_append_to_links_leaves_the_legacy_item_byte_for_byte(tmp_path: Path, surface: str):
    """The doc's example: the hand-written item keeps its spelling, its
    comment and its extra key; the new item goes after it."""
    config, path = _build(tmp_path)
    before = _text(path)

    SURFACES[surface](config, append={"links": {"target": "bar", "relation": "related"}})

    after = _text(path)
    assert (
        "- to: foo          # legacy spelling, hand written\r\n  type: related\r\n  since: 2020\r\n"
        in after
    )
    assert "- target: bar\r\n  relation: related\r\n" in after, after
    assert_only_changed(
        before,
        after,
        [Append("links", {"target": "bar", "relation": "related"}), Set("updated_at", "x")],
    )


def test_a_list_value_appends_each_item(tmp_path: Path):
    config, path = _build(tmp_path)

    _service(config, append={"tags": ["gamma", "delta"]})

    assert "tags: [alpha, beta, gamma, delta]  # trailing" in _text(path)


def test_an_appended_link_is_checked_like_any_link(tmp_path: Path):
    config, path = _build(tmp_path)
    before = path.read_bytes()

    with pytest.raises(ValidationError):
        _service(config, append={"links": {"target": None, "relation": "related"}})

    assert path.read_bytes() == before


def test_a_managed_field_is_still_refused_as_an_operation_on_the_surfaces(tmp_path: Path):
    text = NOTE.replace("type: note", "type: task\nstatus: open")
    config, path = _build(tmp_path, text)
    before = path.read_bytes()

    with pytest.raises(ValidationError, match="Pyrite maintains"):
        _service(config, append={"evidence": "e1"})

    assert path.read_bytes() == before


# --------------------------------------------------------------------------
# The in-process callers: none sends a list read through the model
# --------------------------------------------------------------------------

LEGACY_ITEM = (
    "- to: foo          # legacy spelling, hand written\r\n  type: related\r\n  since: 2020\r\n"
)


def test_qa_fix_link_repair_rewrites_only_the_broken_items_target(tmp_path: Path):
    from pyrite.services.access_policy import UNSCOPED
    from pyrite.services.qa_service import QAService

    text = NOTE.replace(
        "extra: 1\n", f"- target: {ID[:-1]}x   # a typo\n  relation: related_to\nextra: 1\n"
    )
    config, path = _build(tmp_path, text)
    before = _text(path)
    db = PyriteDB(config.settings.index_path)
    try:
        result = QAService(config, db).fix_kb(
            KB, dry_run=False, fix_rules=["broken_link"], readable_kbs=UNSCOPED
        )
    finally:
        db.close()
    after = _text(path)
    assert result["fixed_count"] == 1, result
    assert LEGACY_ITEM in after, after
    assert f"- target: {ID}   # a typo\r\n  relation: related_to\r\n" in after, after
    assert_only_changed(before, after, [Set("links[1].target", "x"), Set("updated_at", "x")])


def test_qa_fix_link_repair_of_a_legacy_item_edits_its_own_key(tmp_path: Path):
    """The broken link is the hand-written one: the fix sets ``to:``, the
    key the item uses, never adds a ``target:`` beside it."""
    from pyrite.services.access_policy import UNSCOPED
    from pyrite.services.qa_service import QAService

    text = NOTE.replace("- to: foo ", f"- to: {ID[:-1]}x ")
    config, path = _build(tmp_path, text)
    before = _text(path)
    db = PyriteDB(config.settings.index_path)
    try:
        result = QAService(config, db).fix_kb(
            KB, dry_run=False, fix_rules=["broken_link"], readable_kbs=UNSCOPED
        )
    finally:
        db.close()
    after = _text(path)
    assert result["fixed_count"] == 1, result
    assert re.search(
        rf"- to: {ID} +# legacy spelling, hand written\r\n  type: related\r\n  since: 2020\r\n",
        after,
    ), after
    assert "target:" not in after
    assert_only_changed(before, after, [Set("links[0].to", "x"), Set("updated_at", "x")])


@pytest.mark.control(
    reason="P3a's whole-list set already copied the unchanged items' bytes (apply's item-wise copy), so the bytes were right before; the control pins the property while the caller changes from sending the list to sending the one item"
)
def test_qa_fix_tag_normalisation_sets_the_one_tag(tmp_path: Path):
    from pyrite.services.access_policy import UNSCOPED
    from pyrite.services.qa_service import QAService

    text = NOTE.replace("tags: [alpha, beta]", "tags: [Alpha, beta]")
    config, path = _build(tmp_path, text)
    before = _text(path)
    db = PyriteDB(config.settings.index_path)
    try:
        result = QAService(config, db).fix_kb(
            KB, dry_run=False, fix_rules=["tag_case"], readable_kbs=UNSCOPED
        )
    finally:
        db.close()
    after = _text(path)
    assert result["fixed_count"] == 1, result
    assert "tags: [alpha, beta]  # trailing" in after, after
    assert LEGACY_ITEM in after
    assert_only_changed(before, after, [Set("tags[0]", "x"), Set("updated_at", "x")])


TASK = (
    "---\n"
    "# a task kept by hand\n"
    "id: t1\n"
    "title: Task one   # keep\n"
    "type: task\n"
    "status: open\n"
    "evidence: [e0]   # keep\n"
    "links:\n"
    "- to: foo          # legacy spelling, hand written\n"
    "  type: related\n"
    "  since: 2020\n"
    "---\n"
    "\n"
    "Body.\n"
)
PARENT = "---\nid: t0\ntitle: Parent\ntype: task\nstatus: open\nevidence: [p0]\n---\n\nParent.\n"


def _task_kb(tmp_path: Path, files: dict[str, str]):
    kb_path = tmp_path / "tasks"
    (kb_path / "tasks").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30)
    for name, text in files.items():
        (kb_path / "tasks" / name).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="tasks", path=kb_path, kb_type="task")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    IndexManager(db, config).index_all()
    return config, db, kb_path / "tasks"


@pytest.mark.control(
    reason="P3a's whole-list set already copied the unchanged items' bytes (apply's item-wise copy), so the bytes were right before; the control pins the property while the caller changes from sending the list to sending the one item"
)
def test_link_qa_assessment_appends_one_evidence_item(tmp_path: Path):
    from pyrite.services.task_service import TaskService

    config, db, folder = _task_kb(tmp_path, {"t1.md": TASK})
    path = folder / "t1.md"
    before = _text(path)
    try:
        TaskService(config, db).link_qa_assessment("t1", "qa-1", "tasks")
    finally:
        db.close()
    after = _text(path)
    assert "evidence: [e0, qa-1]   # keep" in after, after
    assert LEGACY_ITEM in after
    assert_only_changed(before, after, [Append("evidence", "qa-1"), Set("updated_at", "x")])


@pytest.mark.control(
    reason="P3a's whole-list set already copied the unchanged items' bytes (apply's item-wise copy), so the bytes were right before; the control pins the property while the caller changes from sending the list to sending the one item"
)
def test_aggregate_evidence_appends_only_the_new_items(tmp_path: Path):
    from pyrite.services.task_service import TaskService

    child = TASK.replace("evidence: [e0]   # keep", "parent: t0\nevidence: [p0, c1]")
    config, db, folder = _task_kb(tmp_path, {"t1.md": child, "t0.md": PARENT})
    path = folder / "t0.md"
    before = _text(path)
    try:
        result = TaskService(config, db).aggregate_evidence_to_parent("t1", "tasks")
    finally:
        db.close()
    after = _text(path)
    assert result and result["evidence_added"] == 1, result
    assert "evidence: [p0, c1]" in after, after
    assert_only_changed(before, after, [Append("evidence", "c1"), Set("updated_at", "x")])


@pytest.mark.control(
    reason="P3a's whole-list set already copied the unchanged items' bytes (apply's item-wise copy), so the bytes were right before; the control pins the property while the caller changes from sending the list to sending the one item"
)
def test_update_task_appends_the_audit_entry(tmp_path: Path):
    from pyrite.services.task_service import TaskService

    config, db, folder = _task_kb(tmp_path, {"t1.md": TASK})
    path = folder / "t1.md"
    before = _text(path)
    try:
        TaskService(config, db).update_task(
            "t1", "tasks", status="claimed", comment="mine", by="me"
        )
    finally:
        db.close()
    after = _text(path)
    assert "status: claimed" in after and "status_change_log:" in after
    assert LEGACY_ITEM in after and "evidence: [e0]   # keep" in after
    assert_only_changed(
        before,
        after,
        [Set("status", "x"), Append("status_change_log", {"x": 1}), Set("updated_at", "x")],
    )


# --------------------------------------------------------------------------
# Fix round 1: every refusal is decided once, on the RESOLVED path of every
# operation, so no operation kind, path form or surface can skip it
# --------------------------------------------------------------------------

#: A task: `evidence` and `status_change_log` are its managed fields, `links`
#: is the list a surface may add an item to or take one from.
GUARDED = NOTE.replace("type: note", "type: task\nstatus: open\nevidence: [e0]")

OPS = ["set", "append", "remove", "unset"]


def _send(surface: str, config, op: str, path: str, value: Any = "x") -> Any:
    fn = SURFACES[surface]
    if op == "set":
        return fn(config, set={path: value})
    if op == "append":
        return fn(config, append={path: value})
    if op == "remove":
        return fn(config, remove={path: value})
    return fn(config, unset=[path])


# The grid over operation kind x path form x surface for every managed field
# is generated from the code in tests/test_metadata_is_a_spelling_not_a_field.py
# (Andon #994); the hand-written path forms that lived here are gone with it.
# What stays below are the `links` item rules and the MCP argument shapes.

_FIRST_SEGMENT_CAUGHT = pytest.mark.control(
    reason="the first-segment check refused these before #973 round 1; the test states "
    "the rule for every operation kind"
)


LINK = {"target": "bar", "relation": "related"}


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize(
    "spelling",
    [
        pytest.param(
            "links", marks=pytest.mark.control(reason="the item exemption held for `links`")
        ),
        pytest.param(
            "metadata.links",
            marks=pytest.mark.control(
                reason="red on d210d0fa, fixed by #973 round 1 (939910c); kept as the "
                "statement that `metadata.links` is a spelling of `links` under #994 too"
            ),
        ),
    ],
)
@pytest.mark.parametrize("op", ["append", "remove"])
def test_an_item_operation_on_links_lands_on_the_list_itself(
    tmp_path: Path, surface: str, spelling: str, op: str
):
    """`append links` and `append metadata.links` are one request:
    `metadata.links` is a second spelling of the field `links` (Andon #994,
    decision A), which this file spells at the top level, so the item goes
    there, checked as a link; nothing called `metadata:` appears. Review of
    #973: `append metadata.links` wrote a nested `metadata:`."""
    text = GUARDED.replace("  since: 2020\n", "  since: 2020\n- target: bar\n  relation: related\n")
    config, path = _build(tmp_path, text if op == "remove" else GUARDED)

    _send(surface, config, op, spelling, LINK)

    after = _text(path)
    assert "metadata" not in after, after
    assert ("- target: bar\r\n  relation: related\r\n" in after) == (op == "append"), after


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize(
    "op",
    [
        pytest.param("set", marks=_FIRST_SEGMENT_CAUGHT),
        "append",
        "remove",
        pytest.param("unset", marks=_FIRST_SEGMENT_CAUGHT),
    ],
)
def test_an_operation_inside_a_link_item_is_refused(tmp_path: Path, surface: str, op: str):
    """The exemption is for adding or taking a whole item of `links` or
    `sources`; `links[0].note` is Pyrite's field, edited by none of the four.
    Review of #973: `append links[0].note` wrote the file, then the index
    insert failed with an untyped sqlite error (REST 500)."""
    config, path = _build(tmp_path, GUARDED)
    before = path.read_bytes()

    with pytest.raises(ValidationError):
        _send(surface, config, op, "links[0].note")

    assert path.read_bytes() == before


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize("value", ["x", [["x"]]], ids=["a string", "a list in a list"])
def test_an_appended_link_must_be_a_link(tmp_path: Path, surface: str, value: Any):
    """An item appended to `links` is checked as a link before anything is
    written: a string or a nested list is refused as `VALIDATION_FAILED`,
    never written and then refused by the index."""
    config, path = _build(tmp_path, GUARDED)
    before = path.read_bytes()

    with pytest.raises(ValidationError):
        _send(surface, config, "append", "links", value)

    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "args",
    [
        {"unset": "tags"},
        {"append": ["tags", "x"]},
        {"remove": "tags=x"},
        pytest.param(
            {"unset": [1]},
            marks=pytest.mark.control(
                reason="an int key reached apply() and was refused there before the round"
            ),
        ),
    ],
    ids=["unset a string", "append a list", "remove a string", "unset a non-string key"],
)
def test_mcp_refuses_a_malformed_operation_argument(tmp_path: Path, args: dict[str, Any]):
    """`append` and `remove` are objects ({field: item}), `unset` a list of
    keys; a wrong shape is `VALIDATION_FAILED`, nothing written. Review of
    #973: `unset: "tags"` was iterated per character, a malformed `append`
    was `INTERNAL`, retryable."""
    from pyrite.server.mcp_server import PyriteMCPServer

    config, path = _build(tmp_path)
    before = path.read_bytes()
    server = PyriteMCPServer(config, tier="write")
    try:
        result = server._dispatch_tool("kb_update", {"kb_name": KB, "entry_id": ID, **args})
    finally:
        server.close()
    assert result.get("error_code") == "VALIDATION_FAILED", result
    assert path.read_bytes() == before


def _fix(config, rule: str) -> dict:
    from pyrite.services.access_policy import UNSCOPED
    from pyrite.services.qa_service import QAService

    db = PyriteDB(config.settings.index_path)
    try:
        return QAService(config, db).fix_kb(
            KB, dry_run=False, fix_rules=[rule], readable_kbs=UNSCOPED
        )
    finally:
        db.close()


@pytest.mark.parametrize(
    ("spelled", "fixed"),
    [("tags: Alpha", "tags: alpha"), ("tags: Alpha, beta", "tags: alpha, beta")],
    ids=["a scalar", "a comma string"],
)
def test_qa_fix_tag_normalisation_keeps_a_scalar_tags_value_a_scalar(
    tmp_path: Path, spelled: str, fixed: str
):
    """`tags: Alpha` is one tag (the model's rule, #561); the repair lowers
    it in place, as the scalar it is. Review of #973: the per-position set
    failed on a scalar, and the repair with it."""
    config, path = _build(tmp_path, NOTE.replace("tags: [alpha, beta]", spelled))
    before = _text(path)

    result = _fix(config, "tag_case")

    after = _text(path)
    assert result["fixed_count"] == 1, result
    assert f"{fixed}  # trailing" in after, after
    assert_only_changed(before, after, [Set("tags", "x"), Set("updated_at", "x")])


def test_qa_fix_link_repair_rewrites_a_string_link_item(tmp_path: Path):
    """`links: [foo]` is a link to `foo` (the model's rule); the repair sets
    the item itself, keeping it a string. Review of #973: a string item was
    skipped and the broken link left."""
    text = NOTE.replace("extra: 1\n", f"- {ID[:-1]}x   # a typo, as a bare id\nextra: 1\n")
    config, path = _build(tmp_path, text)
    before = _text(path)

    result = _fix(config, "broken_link")

    after = _text(path)
    assert result["fixed_count"] == 1, result
    assert f"- {ID}   # a typo, as a bare id\r\n" in after, after
    assert LEGACY_ITEM in after
    assert_only_changed(before, after, [Set("links[1]", "x"), Set("updated_at", "x")])

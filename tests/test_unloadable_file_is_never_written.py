"""A file its declared type cannot load is written not at all (ADR-0042 8a; #1261).

The invariant: no write lands on a file whose declared type cannot load it,
and no validator or hook ever receives an entry its type did not fully load.
It has one enforcement point: every writer loads strictly
(``KBRepository._load_entry``; ``load`` re-raises ``EntryLoadError``), so the
refusal, ``ENTRY_UNREADABLE``, reaches the caller before any operation,
validator or hook runs. The partial read (``load_for_read``) has one caller,
the single-entry read (``tests/test_refused_fields_stay_absent.py``).

Rounds 1 and 2 of #1311 failed this on shapes their tests did not range over:
a hooked type (``task``, whose ``before_save`` workflow gate passes when the
old status is missing) and a ``metadata:`` that is not a mapping. So this file
is a matrix: every shape x every write method x {hooked ``task``, unhooked
``backlog_item``}, with spy ``before_save``/``before_delete`` hooks and a spy
on validation that must never see the entry.

The write methods are not a hand-kept list of the ones somebody remembered:
``test_every_loader_and_writer_call_site_is_classified`` walks the source for
every call that loads an entry or writes an entry file, and fails on one this
file does not classify. A writer is classified by naming the write methods
below that exercise it, so a new writer cannot join without a row in the
matrix.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from pyrite.config import KBConfig

REPO = Path(__file__).resolve().parents[1]
REL = "items/x.md"  # the file under test, KB-relative: messages name this, never the absolute path


# -- shapes: a file each type's loader refuses ---------------------------------
#
# `extra` is the frontmatter lines after `type`/`id`/`title`; `title` replaces
# the title line. `field` is the refused key a message must name (None: not
# isolated to a field); `loader` a fragment of the loader's own message.
# In-tree triggers are used where they exist; the rest are injected by
# `_loaders_that_refuse` below, as a plugin's loader can refuse.


@dataclass(frozen=True)
class Shape:
    extra: str = ""
    title: str = "T"
    field: str | None = None
    loader: str = ""


SHAPES: dict[str, dict[str, Shape]] = {
    # A scalar field of a hooked type and of an unhooked one (injected: no
    # in-tree loader refuses a status value).
    "scalar": {
        "task": Shape("status: weird\n", field="status", loader="status must be one of"),
        "backlog_item": Shape("status: weird\n", field="status", loader="status must be one of"),
    },
    # The whole `metadata:` is not a mapping: real for `task` (the round-2
    # blocker), injected for `backlog_item`, whose loader keeps it.
    "metadata-whole": {
        "task": Shape("metadata: abc\n", field="metadata", loader="not a mapping"),
        "backlog_item": Shape("metadata: abc\n", field="metadata", loader="not a mapping"),
    },
    # One key under `metadata:`: real for `backlog_item` (`int()` of rank),
    # injected for `task`.
    "metadata-key": {
        "task": Shape("metadata:\n  probe: bad\n", field="metadata.probe", loader="probe"),
        "backlog_item": Shape(
            "metadata:\n  rank: .inf\n", field="metadata.rank", loader="float infinity"
        ),
    },
    # A plugin field the plugin's loader converts with `int()` (until #1263).
    "plugin-int": {
        "backlog_item": Shape("rank: .inf\n", field="rank", loader="float infinity"),
    },
    # A base field: no field alone explains it, so a read cannot be partial.
    "unisolated": {
        "task": Shape(title="[a, b]", loader="title must be a string"),
        "backlog_item": Shape(title="[a, b]", loader="title must be a string"),
    },
}
HOOKED = {"task": True, "backlog_item": False}  # task: the workflow gate (before_save)


def _text(entry_type: str, shape: Shape, entry_id: str = "x", body: str = "Body\n") -> str:
    status = "" if "status:" in shape.extra else "status: open\n"
    return (
        f"---\ntype: {entry_type}\nid: {entry_id}\ntitle: {shape.title}\n{status}"
        f"{shape.extra}---\n\n{body}"
    )


def _refusing(original, *, str_metadata: bool):
    """A ``from_frontmatter`` that raises on the injected shapes, the way a
    loader raises, and otherwise is ``original``."""

    def from_frontmatter(klass, meta, body=""):
        if meta.get("status") == "weird":
            raise ValueError("status must be one of: open, done")
        nested = meta.get("metadata")
        if isinstance(nested, dict) and nested.get("probe") == "bad":
            raise ValueError("probe: refused by the loader")
        if str_metadata and isinstance(nested, str):
            raise TypeError("'str' object is not a mapping")
        return original(klass, meta, body)

    return classmethod(from_frontmatter)


@pytest.fixture(autouse=True)
def _loaders_that_refuse(monkeypatch):
    """The injected shapes, inside each type's own loader; a file without them
    loads as before. (`task` refuses a string `metadata:` on its own.)"""
    from pyrite.models.task import TaskEntry
    from pyrite_software_kb.entry_types import BacklogItemEntry

    task_loader = _refusing(TaskEntry.from_frontmatter.__func__, str_metadata=False)
    backlog_loader = _refusing(BacklogItemEntry.from_frontmatter.__func__, str_metadata=True)
    monkeypatch.setattr(TaskEntry, "from_frontmatter", task_loader)
    monkeypatch.setattr(BacklogItemEntry, "from_frontmatter", backlog_loader)


class _SpyPlugin:
    """Seeded into the world's plugin registry (no service is patched): a
    ``before_save``, ``after_save``, ``before_delete`` and ``after_delete``
    hook and a validator, each recording the entry id it was handed."""

    name = "spy_unloadable_1261"

    def __init__(self, calls: list[tuple[str, str]]):
        self.calls = calls

    def get_hooks(self) -> dict:
        def spy(name):
            return lambda entry, context: self.calls.append((name, str(entry.id)))

        names = ("before_save", "after_save", "before_delete", "after_delete")
        return {name: [spy(name)] for name in names}

    def get_validators(self) -> list:
        def validate(entry_type, data, context):
            self.calls.append(("validate", str(data.get("id", ""))))
            return []

        return [validate]


@pytest.fixture
def spies():
    from pyrite.plugins.registry import get_registry

    calls: list[tuple[str, str]] = []
    reg = get_registry()
    reg.discover()
    reg.register(_SpyPlugin(calls))
    yield calls
    reg._plugins.pop(_SpyPlugin.name, None)
    reg._conformance_cache.pop(_SpyPlugin.name, None)


@pytest.fixture
def make_world(tmp_path):
    import contextlib

    from tests._world import build_world

    stack = contextlib.ExitStack()
    count = {"n": 0}

    def make(files: dict[str, str], kb_yaml: str | None = None):
        count["n"] += 1
        kb = tmp_path / f"sw{count['n']}"
        if kb_yaml:
            files = {"kb.yaml": kb_yaml, **files}
        for rel, text in files.items():
            (kb / rel).parent.mkdir(parents=True, exist_ok=True)
            (kb / rel).write_text(text, encoding="utf-8")
        w = build_world(
            tmp_path / f"w{count['n']}", kbs=[KBConfig(name="sw", path=kb, kb_type="software")]
        )
        stack.callback(w.close)
        stack.enter_context(w.pointed())
        return w

    with stack:
        yield make


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


# -- the write methods, each through its own entry point -----------------------


@dataclass
class Outcome:
    code: str | None  # the error code the surface carries (None: the surface has none)
    message: str
    ok: bool  # the surface reported success
    status: int | None = None  # REST's HTTP status


def _json_or(text: str) -> dict:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _cli(world, *args) -> Outcome:
    result = world.cli(*args)
    output = (result.stdout or "") + (getattr(result, "stderr", "") or "")
    data = _json_or(result.stdout) or _json_or(getattr(result, "stderr", "") or "")
    error = data.get("error") if isinstance(data.get("error"), dict) else data
    code = error.get("code") or error.get("error_code")
    if code is None:  # the rich form: `ERROR [CODE]: message`
        match = re.search(r"\[([A-Z_]+)\]", output)
        code = match.group(1) if match else None
    if result.exit_code == 0 and code is None:
        return Outcome(None, output, True)
    assert result.exit_code == 1, (result.exit_code, output)
    return Outcome(code, output, False)


def _rest(resp) -> Outcome:
    if resp.status_code < 400:
        return Outcome(None, resp.text, True, resp.status_code)
    detail = resp.json().get("detail") or {}
    return Outcome(detail.get("code"), detail.get("message", ""), False, resp.status_code)


def _mcp(res: dict) -> Outcome:
    if "error" not in res and "error_code" not in res:
        return Outcome(None, json.dumps(res, default=str), True)
    error = res.get("error")
    message = error.get("message", "") if isinstance(error, dict) else str(error)
    code = res.get("error_code") or (error.get("code") if isinstance(error, dict) else None)
    return Outcome(code, message, False)


def _service(call) -> Outcome:
    from pyrite.exceptions import PyriteError

    try:
        call()
    except PyriteError as e:
        return Outcome(e.error_code, str(e), False)
    return Outcome(None, "", True)


def _svc(world):
    from pyrite.services.kb_service import KBService
    from pyrite.storage.database import PyriteDB

    db = PyriteDB(world.config.settings.index_path)
    world.track_db(db)
    return KBService(world.config, db)


def w_rest_patch(world):
    return _rest(
        world.client.patch("/api/entries/x", json={"kb": "sw", "field": "status", "value": "done"})
    )


def w_rest_put_echo(world):
    got = world.client.get("/api/entries/x", params={"kb": "sw"})
    read = got.json() if got.status_code == 200 else {"title": "Y"}
    body = {k: read[k] for k in ("title", "body", "tags", "metadata") if read.get(k) is not None}
    return _rest(world.client.put("/api/entries/x", json={"kb": "sw", **body, "title": "Y"}))


def w_mcp_update_echo(world):
    server = world.mcp()
    got = server._dispatch_tool("kb_get", {"entry_id": "x", "kb_name": "sw"})
    read = got.get("entry") or {"title": "Y"}
    return _mcp(server._dispatch_tool("kb_update", {**read, "entry_id": "x", "kb_name": "sw"}))


def w_cli_update(world):
    return _cli(world, "update", "x", "-k", "sw", "-f", "title=Y", "--format", "json")


def w_cli_delete(world):
    return _cli(world, "delete", "x", "-k", "sw", "--force")


def w_rest_delete(world):
    return _rest(world.client.delete("/api/entries/x", params={"kb": "sw"}))


def w_mcp_delete(world):
    return _mcp(world.mcp()._dispatch_tool("kb_delete", {"entry_id": "x", "kb_name": "sw"}))


def w_cli_link(world):
    return _cli(world, "link", "x", "ok", "-k", "sw", "--format", "json")


def w_mcp_link(world):
    return _mcp(
        world.mcp()._dispatch_tool(
            "kb_link", {"source_id": "x", "source_kb": "sw", "target_id": "ok"}
        )
    )


def w_bulk_link(world):
    # The unreadable source fails its own item; the next item still lands.
    results = _svc(world).add_links(
        "sw", [{"source": "x", "target": "ok"}, {"source": "ok", "target": "ok2"}]
    )
    assert results[1].get("status") not in (None, "failed"), results
    first = results[0]
    if first.get("status") == "failed":
        return Outcome(first.get("error_code"), first.get("error", ""), False)
    return Outcome(None, json.dumps(results), True)


def w_cli_rename(world):
    return _cli(world, "rename", "x", "x2", "-k", "sw", "--format", "json")


def w_cli_task_claim(world):
    return _cli(world, "task", "claim", "x", "-k", "sw", "-a", "agent:a")


def w_mcp_task_claim(world):
    return _mcp(
        world.mcp()._dispatch_tool(
            "task_claim", {"task_id": "x", "kb_name": "sw", "assignee": "agent:a"}
        )
    )


def w_cli_task_update_audited(world):
    # `--comment` reads the old status first (task_service.update_task).
    return _cli(world, "task", "update", "x", "-k", "sw", "-s", "done", "--comment", "c")


def w_mcp_task_update(world):
    return _mcp(
        world.mcp()._dispatch_tool(
            "task_update", {"task_id": "x", "kb_name": "sw", "status": "done"}
        )
    )


def w_mcp_task_checkpoint(world):
    return _mcp(
        world.mcp()._dispatch_tool(
            "task_checkpoint", {"task_id": "x", "kb_name": "sw", "message": "m"}
        )
    )


def w_cli_task_reset(world):
    return _cli(world, "task", "reset", "x", "-k", "sw")


def w_apply_operations(world):
    # A plugin's write through `ctx.kb_service` (ADR-0042 decision 12).
    from pyrite.storage.file_operations import Set

    return _service(lambda: _svc(world).apply_operations("x", "sw", [Set("title", "Y")]))


def w_update_entry(world):
    return _service(lambda: _svc(world).update_entry("x", "sw", title="Y"))


def _qa_fix(world, issue: dict, bucket: str) -> Outcome:
    from pyrite.services.qa_fix_service import QAFixService

    svc = _svc(world)
    report = QAFixService(world.config, svc.db, kb_svc=svc).fix_kb(
        "sw", dry_run=False, validate_kb_fn=lambda _kb: {"issues": [issue]}
    )
    hit = [f for f in report[bucket] if f.get("entry_id") == "x"]
    if not hit or any(f.get("entry_id") == "x" for f in report["fixed"]):
        return Outcome(None, json.dumps(report, default=str), True)
    reason = str(hit[0].get("reason"))
    code = reason.split(":", 1)[0] if reason.startswith("ENTRY_UNREADABLE") else None
    return Outcome(code, reason, False)


def w_qa_fix_field(world):
    # A field fix is one write inside `attempt()`, which records a failure per
    # write and goes on (the run does not stop on x).
    issue = {"entry_id": "x", "kb_name": "sw", "rule": "invalid_date", "field": "date"}
    issue["message"] = "invalid date: '2026-01'"
    return _qa_fix(world, issue, "failed")


def w_qa_fix_merge(world):
    # The double-spelling merge loads the file OUTSIDE `attempt()`: a raise
    # there would end the whole run; it is a hand fix that names the file.
    issue = {"entry_id": "x", "kb_name": "sw", "rule": "field_spelled_twice", "field": "title"}
    return _qa_fix(world, issue, "manual")


WRITE_METHODS = {
    f.__name__[2:]: f
    for f in (
        w_rest_patch,
        w_rest_put_echo,
        w_mcp_update_echo,
        w_cli_update,
        w_cli_delete,
        w_rest_delete,
        w_mcp_delete,
        w_cli_link,
        w_mcp_link,
        w_bulk_link,
        w_cli_rename,
        w_cli_task_claim,
        w_mcp_task_claim,
        w_cli_task_update_audited,
        w_mcp_task_update,
        w_mcp_task_checkpoint,
        w_cli_task_reset,
        w_apply_operations,
        w_update_entry,
        w_qa_fix_field,
        w_qa_fix_merge,
    )
}

#: The surfaces whose refusal carries no code of its own: the message must
#: still say ENTRY_UNREADABLE's facts.
_NO_CODE = {"qa_fix_field"}

MATRIX = [
    pytest.param(shape, method, entry_type, id=f"{shape}-{method}-{entry_type}")
    for shape, per_type in SHAPES.items()
    for entry_type in per_type
    for method in WRITE_METHODS
]


@pytest.mark.parametrize(("shape", "method", "entry_type"), MATRIX)
def test_no_write_lands_and_no_guard_sees_the_entry(make_world, spies, shape, method, entry_type):
    spec = SHAPES[shape][entry_type]
    files = {
        REL: _text(entry_type, spec),
        "ok.md": _text(entry_type, Shape(), "ok"),
        "ok2.md": _text(entry_type, Shape(), "ok2"),
    }
    world = make_world(files)
    root = world.kb_path("sw")
    before = _snapshot(root)
    spies.clear()

    outcome = WRITE_METHODS[method](world)

    assert not outcome.ok, f"{method} reported success: {outcome.message}"
    if method not in _NO_CODE:
        assert outcome.code == "ENTRY_UNREADABLE", outcome
    if outcome.status is not None:
        assert outcome.status == 422, outcome
    message = outcome.message.replace("\n", " ")
    assert REL in message, message
    assert str(root) not in message, "the absolute path leaked"
    assert f"`{entry_type}`" in message, message
    if spec.field:
        assert spec.field in message, message
    assert spec.loader in message, message
    after = _snapshot(root)
    if method == "bulk_link":
        assert after[REL] == before[REL]
        assert set(after) == set(before)
    else:
        assert after == before, sorted(k for k in after if after.get(k) != before.get(k))
    seen = [c for c in spies if c[1] == "x"]
    assert seen == [], f"a guard was handed the entry its type did not load: {seen}"


@pytest.mark.control(reason="the spies fire on a file that loads: they can see a guard run")
@pytest.mark.parametrize("entry_type", ["task", "backlog_item"])
def test_the_spies_see_every_guard_on_a_file_that_loads(make_world, spies, entry_type):
    # Without this the matrix's "no guard saw the entry" could pass because
    # the spy plugin never runs at all.
    world = make_world({REL: _text(entry_type, Shape())})

    assert w_cli_update(world).ok
    assert w_cli_delete(world).ok

    seen = {name for name, entry_id in spies if entry_id == "x"}
    assert {"validate", "before_save", "after_save", "before_delete"} <= seen, spies


@pytest.mark.control(reason="the same methods on a file that loads: never ENTRY_UNREADABLE")
@pytest.mark.parametrize("entry_type", ["task", "backlog_item"])
def test_a_file_that_loads_is_never_refused_as_unreadable(make_world, entry_type):
    # Guards the matrix itself: the refusal is the file's, not the method's.
    # (A loadable task may still be refused by its gate, open -> done: that is
    # VALIDATION_FAILED, never ENTRY_UNREADABLE.)
    for method, write in WRITE_METHODS.items():
        world = make_world(
            {
                REL: _text(entry_type, Shape()),
                "ok.md": _text(entry_type, Shape(), "ok"),
                "ok2.md": _text(entry_type, Shape(), "ok2"),
            }
        )
        world.index("sw")
        outcome = write(world)
        assert outcome.code != "ENTRY_UNREADABLE", (method, outcome)


def test_rest_patch_never_lands_a_status_the_task_gate_would_refuse(make_world):
    # The groom's red line at 853a74da: 200 with changed:true. `open -> done`
    # is no task transition; round 2 skipped the gate on the partial model.
    world = make_world({"t.md": _text("task", SHAPES["metadata-whole"]["task"], "t")})
    path = world.kb_path("sw") / "t.md"
    before = path.read_bytes()

    resp = world.client.patch(
        "/api/entries/t", json={"kb": "sw", "field": "status", "value": "done"}
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "ENTRY_UNREADABLE"
    assert path.read_bytes() == before


# -- regimes -------------------------------------------------------------------


def test_a_rename_never_rewrites_a_linking_file_its_type_cannot_load(make_world):
    # Rename rewrites `[[old]]` in every linking file; a linker whose type
    # cannot load keeps its bytes and is listed with the reason.
    shape = SHAPES["plugin-int"]["backlog_item"]
    world = make_world(
        {
            REL: _text("backlog_item", shape, body="See [[ok]].\n"),
            "ok.md": _text("backlog_item", Shape(), "ok"),
            "fine.md": _text("backlog_item", Shape(), "fine", body="See [[ok]].\n"),
        }
    )
    root = world.kb_path("sw")
    before = (root / REL).read_bytes()

    result = world.cli("rename", "ok", "ok-renamed", "-k", "sw", "--format", "json")

    data = json.loads(result.stdout)
    assert result.exit_code == 3 and data["outcome"] == "partial", result.output  # a file skipped
    assert (root / REL).read_bytes() == before
    skipped = {s["path"]: s["reason"] for s in data["files_skipped"]}
    assert REL in skipped and "does not load" in skipped[REL], data
    assert "[[ok-renamed]]" in (root / "fine.md").read_text(encoding="utf-8")


def test_nothing_moves_in_a_templated_subdirectory(make_world, spies):
    # `backlog/{status}`: the round-1 head moved such a file into
    # `backlog/open/` (the model's default status). Every write is refused,
    # so nothing moves and no file appears.
    kb_yaml = "name: sw\ntypes:\n  backlog_item:\n    subdirectory: 'backlog/{status}'\n"
    rel = "backlog/weird/x.md"
    world = make_world({rel: _text("backlog_item", SHAPES["scalar"]["backlog_item"])}, kb_yaml)
    root = world.kb_path("sw")
    before = _snapshot(root)

    for args in (
        ("update", "x", "-k", "sw", "-f", "title=Y"),
        ("update", "x", "-k", "sw", "-f", "status=done"),
    ):
        result = world.cli(*args)
        assert result.exit_code == 1, result.output
        assert rel in result.output.replace("\n", "")

    assert _snapshot(root) == before
    assert [c for c in spies if c[1] == "x"] == []


# -- the enumeration: every loader and every entry-file writer -----------------
#
# Each call site that loads an entry (the loader seam's callers) or writes an
# entry file (the byte writers), by "path::qualified function". A writer's
# value names the WRITE_METHODS rows that drive it; a reader's says why it may
# read. A new call site fails the test below until it is classified here, and
# a writer cannot be classified without a row in the matrix.

WRITER = "writer"
READER = "reader"
SAFE = "safe"

CALL_SITES: dict[str, tuple[str, object]] = {
    # writers: the strict load refuses before anything runs
    "pyrite/services/kb_service.py::KBService._update": (
        WRITER,
        [
            "rest_patch",
            "rest_put_echo",
            "mcp_update_echo",
            "cli_update",
            "apply_operations",
            "update_entry",
            "mcp_task_update",
            "cli_task_update_audited",
            "qa_fix_field",
        ],
    ),
    "pyrite/services/kb_service.py::KBService.delete_entry": (
        WRITER,
        ["cli_delete", "rest_delete", "mcp_delete"],
    ),
    "pyrite/services/kb_service.py::KBService.add_link": (WRITER, ["cli_link", "mcp_link"]),
    "pyrite/services/kb_service.py::KBService.add_links": (WRITER, ["bulk_link"]),
    "pyrite/services/kb_service.py::KBService.claim_entry": (
        WRITER,
        ["cli_task_claim", "mcp_task_claim"],
    ),
    "pyrite/services/task_service.py::TaskService.update_task": (
        WRITER,
        ["cli_task_update_audited"],
    ),
    "pyrite/services/task_service.py::TaskService.checkpoint_task": (
        WRITER,
        ["mcp_task_checkpoint"],
    ),
    "pyrite/services/task_service.py::TaskService.reset_task": (WRITER, ["cli_task_reset"]),
    "pyrite/storage/repository.py::KBRepository.rename": (WRITER, ["cli_rename"]),
    "pyrite/services/qa_fix_service.py::QAFixService._fix_double_spelling": (
        WRITER,
        ["qa_fix_merge"],
    ),
    # the byte writers each writer above reaches
    "pyrite/storage/repository.py::KBRepository.delete": (WRITER, ["cli_delete"]),
    "pyrite/storage/repository.py::KBRepository._publish_rewrite": (
        SAFE,
        "rename's linking files: `_write_refusal` before and on every re-read "
        "(test_a_rename_never_rewrites_a_linking_file_its_type_cannot_load)",
    ),
    "pyrite/storage/repository.py::KBRepository._write_refusal": (
        SAFE,
        "the strict load rename's linking files are checked with",
    ),
    "pyrite/services/kb_service.py::KBService._templated_move": (
        SAFE,
        "runs after _update's strict load, on the fully loaded result "
        "(test_nothing_moves_in_a_templated_subdirectory)",
    ),
    "pyrite/storage/repository.py::KBRepository.save": (SAFE, "a create: the target is absent"),
    "pyrite/services/schema_service.py::SchemaService.migrate_entries": (
        SAFE,
        "an explicit repair command on raw values (ADR-0042 decisions 7, 8a point 4); no model, "
        "validator or hook",
    ),
    "extensions/software-kb/src/pyrite_software_kb/cli.py::sw_migrate_standards": (
        SAFE,
        "driven by index rows; a file its type cannot load has no row",
    ),
    # readers: never write
    "pyrite/services/kb_service.py::KBService._entry_file": (
        READER,
        "the one partial read (load_for_read), test_refused_fields_stay_absent.py",
    ),
    "pyrite/services/kb_service.py::KBService.updatable_fields": (
        WRITER,
        ["mcp_update_echo"],  # split_echoed_update's strict load, ahead of the update's
    ),
    "pyrite/services/kb_service.py::KBService.load_entry_from_disk": (
        READER,
        "export and the daily note: propagates or counts",
    ),
    "pyrite/services/qa_fix_service.py::QAFixService._load": (READER, "no callers"),
    "pyrite/services/qa_service.py::QAService._check_file_values": (
        READER,
        "qa validation reports the file",
    ),
    "pyrite/services/file_search.py::search_kb_files": (READER, "search skips the file"),
    "pyrite/cli/export_commands.py::_follow_links": (READER, "export"),
    "pyrite/cli/export_commands.py::_load_entry_from_result": (READER, "export: counts it"),
    "pyrite/cli/export_commands.py::export_collection": (READER, "export"),
    "pyrite/server/endpoints/daily.py::_load_existing_daily_note": (
        READER,
        "propagates ENTRY_UNREADABLE",
    ),
    "pyrite/storage/index.py::IndexManager.plan_reconcile": (READER, "lists it as malformed"),
    "pyrite/storage/repository.py::KBRepository.list_entries": (READER, "skips it"),
    "pyrite/storage/repository.py::KBRepository.search_files": (READER, "skips it"),
    "pyrite/storage/repository.py::KBRepository.load": (READER, "the strict seam itself"),
    "pyrite/storage/repository.py::KBRepository.load_entry_from_bytes": (READER, "the seam"),
    "pyrite/storage/repository.py::KBRepository.load_entry_from_file": (READER, "the seam"),
    "pyrite/storage/repository.py::KBRepository.load_for_read": (READER, "the partial read"),
    "pyrite/storage/repository.py::MultiKBRepository.load": (READER, "delegates to load"),
}

_LOADERS = {
    "load_entry_from_bytes",
    "load_entry_from_file",
    "_load_entry",
    "load_entry_from_disk",
    "load_for_read",
}
_BYTE_WRITERS = {"publish_entry_text", "create_entry_text", "remove_entry_file", "move_entry_text"}


def _is_repository(node: ast.expr) -> bool:
    """``repo.load``, ``target_repo.load``, ``KBRepository(c).load``: a load
    by id, as against ``json.load`` or an entry point's ``load``."""
    if isinstance(node, ast.Name):
        return "repo" in node.id.lower()
    if isinstance(node, ast.Call):
        func = node.func
        return (func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")) == (
            "KBRepository"
        )
    return False


def _call_sites() -> set[str]:
    sources = sorted((REPO / "pyrite").rglob("*.py")) + sorted(
        REPO.glob("extensions/*/src/**/*.py")
    )
    found: set[str] = set()
    for src in sources:
        rel = src.relative_to(REPO).as_posix()

        def visit(node: ast.AST, scope: list[str], rel: str = rel) -> None:
            for child in ast.iter_child_nodes(node):
                inner = scope
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    inner = [*scope, child.name]
                if isinstance(child, ast.Call):
                    func = child.func
                    name = (
                        func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                    )
                    if (
                        name in _LOADERS
                        or name in _BYTE_WRITERS
                        or (
                            name == "load"
                            and isinstance(func, ast.Attribute)
                            and _is_repository(func.value)
                        )
                    ):
                        found.add(f"{rel}::{'.'.join(scope)}")
                visit(child, inner)

        visit(ast.parse(src.read_text(encoding="utf-8")), [])
    return found


def test_every_loader_and_writer_call_site_is_classified():
    found = _call_sites()
    unclassified = sorted(found - set(CALL_SITES))
    assert not unclassified, (
        "a new call site loads an entry or writes an entry file; classify it in "
        f"CALL_SITES (a writer names its WRITE_METHODS rows): {unclassified}"
    )
    gone = sorted(set(CALL_SITES) - found)
    assert not gone, f"classified call sites that no longer exist: {gone}"
    for site, (kind, detail) in CALL_SITES.items():
        if kind == WRITER:
            assert detail and set(detail) <= set(WRITE_METHODS), (site, detail)


# -- where this meets sticky entry paths (#1355; ADR-0038 I6) --------------------
#
# A file's name is a location, so a rename rewrites the id in place and a
# create whose default path is held by a *different* id takes a fresh filename.
# Neither may reach a file its type cannot load: the rename no longer loads the
# model, and `_free_default_path` asks who holds a path with the same loader.


def _default_path(world, entry_type: str) -> str:
    """The KB-relative path a create of id ``x`` gets in an empty KB, asked of
    the service itself so the test does not restate the placement rule."""
    svc = _svc(world)
    created = svc.create_entry("sw", "x", "Probe", entry_type, body="b")
    rel = created.file_path.relative_to(world.kb_path("sw")).as_posix()
    created.file_path.unlink()
    return rel


@pytest.mark.control(
    reason="dev already refuses a create onto an existing path; this keeps the refusal across the #1355 free-path rule"
)
@pytest.mark.parametrize("entry_type", ["task", "backlog_item"])
def test_a_create_is_refused_when_an_unloadable_file_holds_its_own_id_at_the_default_path(
    make_world, entry_type
):
    # The unloadable file *is* entry `x`: the create is a write to it. Refused,
    # the file as it was, and no `x-2.md` beside it.
    world = make_world({})
    rel = _default_path(world, entry_type)
    path = world.kb_path("sw") / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_text(entry_type, SHAPES["metadata-whole"][entry_type]), encoding="utf-8")
    before = _snapshot(world.kb_path("sw"))

    outcome = _service(lambda: _svc(world).create_entry("sw", "x", "New", entry_type, body="n"))

    assert not outcome.ok, outcome
    assert _snapshot(world.kb_path("sw")) == before


@pytest.mark.control(
    reason="dev already takes a fresh filename; this pins that the unloadable file is left alone"
)
@pytest.mark.parametrize("entry_type", ["task", "backlog_item"])
def test_a_create_beside_an_unloadable_file_of_another_id_leaves_that_file_alone(
    make_world, entry_type
):
    # `x.md` holds `other`, which its type cannot load. The id is read without
    # the full loader (ADR-0038 decision 1), so `x` is free: the create takes
    # `x-2.md` (#1355) and the unloadable file keeps its bytes.
    world = make_world({})
    rel = _default_path(world, entry_type)
    path = world.kb_path("sw") / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _text(entry_type, SHAPES["metadata-whole"][entry_type], "other"), encoding="utf-8"
    )
    before = path.read_bytes()

    created = _svc(world).create_entry("sw", "x", "New", entry_type, body="n")

    assert created.file_path.name == "x-2.md"
    assert path.read_bytes() == before


@pytest.mark.control(reason="a loadable holder of another id still frees the path: <id>-2.md")
def test_a_create_still_takes_a_fresh_filename_beside_a_loadable_holder_of_another_id(make_world):
    world = make_world({})
    rel = _default_path(world, "task")
    path = world.kb_path("sw") / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_text("task", Shape(), "other"), encoding="utf-8")
    before = path.read_bytes()

    created = _svc(world).create_entry("sw", "x", "New", "task", body="n")

    assert created.file_path.name == path.with_name("x-2.md").name
    assert path.read_bytes() == before


@pytest.mark.parametrize("entry_type", ["task", "backlog_item"])
def test_a_dry_run_rename_of_an_unloadable_entry_is_refused_like_the_real_one(
    make_world, entry_type
):
    # The rename rewrites the id in place and loads no model; the strict load
    # sits before the dry-run return, or the plan promises what the run refuses.
    spec = SHAPES["metadata-whole"][entry_type]
    world = make_world({REL: _text(entry_type, spec)})
    before = _snapshot(world.kb_path("sw"))

    outcome = _cli(world, "rename", "x", "x2", "-k", "sw", "--dry-run", "--format", "json")

    assert not outcome.ok and outcome.code == "ENTRY_UNREADABLE", outcome
    assert _snapshot(world.kb_path("sw")) == before


def test_an_entry_renamed_in_place_and_then_made_unloadable_cannot_be_renamed_again(make_world):
    # a.md holds b after rename(a, b) (the file keeps its name). A hand edit
    # then breaks b: the second rename must find the file by id, load it
    # strictly, and refuse, leaving the bytes.
    world = make_world({"a.md": _text("task", Shape(), "a")})
    root = world.kb_path("sw")
    assert _cli(world, "rename", "a", "b", "-k", "sw", "--format", "json").ok
    held = root / "a.md"
    assert "id: b" in held.read_text(encoding="utf-8")
    held.write_text(held.read_text(encoding="utf-8").replace("status: open", "metadata: abc"))
    before = _snapshot(root)

    outcome = _cli(world, "rename", "b", "c", "-k", "sw", "--format", "json")

    assert not outcome.ok and outcome.code == "ENTRY_UNREADABLE", outcome
    assert _snapshot(root) == before

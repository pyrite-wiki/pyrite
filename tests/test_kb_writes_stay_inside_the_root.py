"""No KB write deletes, replaces or writes a file outside the KB root.

The property, whatever links are on the path: every write operation (create,
update, rename, delete, ``ids pin``, ``qa fix``, software-kb's
``migrate-standards``) either stays inside the KB's resolved root or is
refused with a stable code, every file left as it was. A rename of an entry
whose file is a symbolic link never removes the link's target: it refuses,
with the reason.

The grid: operation x placement x KB kind. A placement is a link kind at a
position:

- ``file-out``: the entry file is a symbolic link to a file outside the root;
- ``file-in``: the entry file is a symbolic link to a file inside the root;
- ``file-hard``: the entry file is a hard link whose other name is outside;
- ``dir-out``: the entry's folder is a symbolic link to a folder outside (the
  index still names the file under it: the folder was replaced after the
  last sync, as a checkout can do);
- ``dir-in``: the same, to a folder inside the root.

A hard link cannot be a folder, so there is no ``dir-hard``. Each cell runs
the operation through its own entry point (``KBService`` for the four entry
operations, the CLI for ``ids pin`` and ``migrate-standards``, ``QAService``
for ``qa fix``), on a KB under git and one outside it. Every cell asserts the
property by bytes: the tree outside the root is identical before and after
(every name and every byte). Each cell then asserts its own outcome.

A cell that holds without the fix is a ``control`` with its reason.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app  # at import, as the CLI registers plugin commands then
from pyrite.config import KBConfig, PyriteConfig, Settings, save_config
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

KB = "k"
OUTSIDE_CODE = "OUTSIDE_KB_ROOT"

NOTE = "---\nid: s\ntype: note\ntitle: S\n---\n\nbody\n"
UNPINNED = "---\ntype: note\ntitle: S\n---\n\nbody\n"
BAD_DATE = "---\nid: s\ntype: event\ntitle: S\ndate: '2006'\n---\n\nbody\n"
STANDARD = "---\nid: s\ntitle: S\ntype: standard\nenforced: true\n---\n\nBody.\n"


# --------------------------------------------------------------------------
# the tree
# --------------------------------------------------------------------------


def _tree(top: Path) -> dict[str, bytes | str]:
    """Every name under ``top``: a link as its target, a file as its bytes."""
    out: dict[str, bytes | str] = {}
    for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
        for name in sorted(dirnames + filenames):
            p = Path(dirpath) / name
            rel = str(p.relative_to(top))
            if p.is_symlink():
                out[rel] = "-> " + os.readlink(p)
            elif p.is_file():
                out[rel] = p.read_bytes()
            else:
                out[rel] = "<dir>"
    return out


@dataclass
class Cell:
    root: Path
    outside: Path
    config: PyriteConfig
    entry: Path  # the entry file's path as the KB names it

    def db(self) -> PyriteDB:
        return PyriteDB(self.config.settings.index_path)

    def sync(self) -> None:
        db = self.db()
        try:
            IndexManager(db, self.config).index_all()
        finally:
            db.close()


def _cell(
    tmp_path: Path,
    *,
    git: bool,
    kb_type: str = "generic",
    rel: str,
    text: str | None,
    placement: str,
) -> Cell:
    """A KB at ``tmp_path/kb`` whose entry ``rel`` is reached through
    ``placement``; ``text`` None leaves the entry absent (a create's target).
    The canary outside is there in every cell, so an outside tree is never
    empty."""
    root = tmp_path / "kb"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "canary.md").write_text(NOTE.replace("id: s", "id: canary"))
    if git:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True, timeout=30)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=root, kb_type=kb_type)],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    entry = root / rel
    folder = entry.parent
    cell = Cell(root, outside, config, entry)
    folder.mkdir(parents=True, exist_ok=True)

    if placement in ("file-out", "file-in", "file-hard"):
        if placement == "file-out":
            target = outside / entry.name
        else:
            store = root / "store"
            store.mkdir(exist_ok=True)
            target = store / (entry.stem + ".txt")  # not a .md: no second entry
        if placement == "file-hard":
            target = outside / entry.name
            if text is not None:
                target.write_text(text)
                os.link(target, entry)
        else:
            if text is not None:
                target.write_text(text)
            os.symlink(target, entry)
        cell.sync()
        return cell

    # dir-out / dir-in: the folder is real at the last sync, then replaced by
    # a link to where its files now are.
    if text is not None:
        entry.write_text(text)
    cell.sync()
    away = (outside / "moved") if placement == "dir-out" else (root / "store" / "moved")
    away.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(away))
    os.symlink(away, folder)
    return cell


PLACEMENTS = ["file-out", "file-in", "file-hard", "dir-out", "dir-in"]


# --------------------------------------------------------------------------
# the operations, each through its own entry point
# --------------------------------------------------------------------------


@dataclass
class Outcome:
    done: bool
    code: str | None = None
    message: str = ""


def _service(cell: Cell, call) -> Outcome:
    from pyrite.exceptions import PyriteError
    from pyrite.services.kb_service import KBService

    db = cell.db()
    try:
        try:
            call(KBService(cell.config, db))
        except PyriteError as e:
            return Outcome(False, getattr(e, "error_code", None), str(e))
        return Outcome(True)
    finally:
        db.close()


def op_update(cell: Cell) -> Outcome:
    return _service(cell, lambda svc: svc.update_entry("s", KB, title="Changed"))


def op_rename(cell: Cell) -> Outcome:
    return _service(cell, lambda svc: svc.rename_entry("s", "s2", KB))


def op_delete(cell: Cell) -> Outcome:
    def call(svc):
        if not svc.delete_entry("s", KB):
            from pyrite.exceptions import EntryNotFoundError

            raise EntryNotFoundError("nothing deleted")

    return _service(cell, call)


def op_create(cell: Cell) -> Outcome:
    return _service(cell, lambda svc: svc.create_entry(KB, "s", "S", "note", "body"))


def op_ids_pin(cell: Cell) -> Outcome:
    import json

    save_config(cell.config)  # the CLI reads it; the loader is never replaced
    result = CliRunner().invoke(app, ["ids", "pin", "-k", KB])
    data = json.loads(result.output)
    return Outcome(bool(data["pinned"]), None, result.output)


def op_qa_fix(cell: Cell) -> Outcome:
    from pyrite.services.access_policy import UNSCOPED
    from pyrite.services.qa_service import QAService

    db = cell.db()
    try:
        report = QAService(cell.config, db).fix_kb(KB, dry_run=False, readable_kbs=UNSCOPED)
    finally:
        db.close()
    fixed = [f for f in report["fixed"] if f.get("entry_id") == "s"]
    failed = [f for f in report["failed"] if f.get("entry_id") == "s"]
    return Outcome(bool(fixed), None, str(failed))


def op_migrate_standards(cell: Cell) -> Outcome:
    pytest.importorskip("pyrite_software_kb.cli")
    from pyrite.plugins.registry import PluginRegistry
    from pyrite_software_kb.plugin import SoftwareKBPlugin

    PluginRegistry().register(SoftwareKBPlugin())
    save_config(cell.config)  # the CLI reads it; the loader is never replaced
    result = CliRunner().invoke(app, ["sw", "migrate-standards", "--apply", "-k", KB])
    return Outcome("Migrated" in result.output, None, result.output)


#: op -> (its entry's KB-relative path, its text, the KB type)
OPS = {
    "create": (op_create, "notes/s.md", None, "generic"),
    "update": (op_update, "notes/s.md", NOTE, "generic"),
    "rename": (op_rename, "notes/s.md", NOTE, "generic"),
    "delete": (op_delete, "notes/s.md", NOTE, "generic"),
    "ids-pin": (op_ids_pin, "notes/s.md", UNPINNED, "generic"),
    "qa-fix": (op_qa_fix, "events/s.md", BAD_DATE, "generic"),
    "migrate-standards": (op_migrate_standards, "standards/s.md", STANDARD, "software"),
}

REFUSED_OUTSIDE = ("refused", OUTSIDE_CODE)
REFUSED_LINK = ("refused", "VALIDATION_FAILED")  # a rename of a link: the reason says so
REFUSED_HARD = ("refused", "STORAGE_ERROR")  # the existing hard-link refusal's code
DONE = ("done", None)
NOT_DONE = ("not-done", None)  # a batch operation reports the file and leaves it

_NOT_FOUND = "the lookup walks no folder link, so the entry is not found and nothing is written"
_SAME_KB = "a link whose target is inside the KB writes inside the KB"
_REAL_FOLDER = "the folder a link inside points to is itself in the KB, and found there"

#: (op, placement) -> (expected outcome, control reason or None, kinds it is
#: a control for). A control passes without the fix.
EXPECTED: dict[tuple[str, str], tuple[tuple[str, str | None], str | None, set[str]]] = {
    # create: the target path is a dangling link, or its folder is a link
    ("create", "file-out"): (REFUSED_OUTSIDE, None, set()),
    ("create", "file-in"): (DONE, _SAME_KB, {"git", "plain"}),
    ("create", "dir-out"): (REFUSED_OUTSIDE, None, set()),
    ("create", "dir-in"): (DONE, _SAME_KB, {"git", "plain"}),
    # update
    ("update", "file-out"): (REFUSED_OUTSIDE, None, set()),
    ("update", "file-in"): (DONE, _SAME_KB, {"git", "plain"}),
    ("update", "file-hard"): (
        REFUSED_HARD,
        "under git the compare-and-replace already refuses a hard-linked file",
        {"git"},
    ),
    ("update", "dir-out"): (("refused", "ENTRY_NOT_FOUND"), _NOT_FOUND, {"git", "plain"}),
    ("update", "dir-in"): (DONE, _REAL_FOLDER, {"git", "plain"}),
    # rename
    ("rename", "file-out"): (REFUSED_OUTSIDE, None, set()),
    ("rename", "file-in"): (REFUSED_LINK, None, set()),
    ("rename", "file-hard"): (REFUSED_HARD, None, set()),
    ("rename", "dir-out"): (("refused", "ENTRY_NOT_FOUND"), _NOT_FOUND, {"git", "plain"}),
    ("rename", "dir-in"): (DONE, _REAL_FOLDER, {"git", "plain"}),
    # delete
    ("delete", "file-out"): (REFUSED_OUTSIDE, None, set()),
    ("delete", "file-in"): (DONE, "removing a link removes the link only", {"git", "plain"}),
    ("delete", "file-hard"): (
        DONE,
        "removing a name of a hard-linked file leaves its other names as they are",
        {"git", "plain"},
    ),
    ("delete", "dir-out"): (REFUSED_OUTSIDE, None, set()),
    ("delete", "dir-in"): (DONE, _SAME_KB, {"git", "plain"}),
    # ids pin: its own no-follow writer already refuses links and hard links
    ("ids-pin", "file-out"): (NOT_DONE, "ids pin refuses a link", {"git", "plain"}),
    ("ids-pin", "file-in"): (NOT_DONE, "ids pin refuses a link", {"git", "plain"}),
    ("ids-pin", "file-hard"): (NOT_DONE, "ids pin refuses a hard link", {"git", "plain"}),
    ("ids-pin", "dir-out"): (NOT_DONE, "the walk enters no folder link", {"git", "plain"}),
    ("ids-pin", "dir-in"): (DONE, _REAL_FOLDER, {"git", "plain"}),
    # qa fix: an update per entry
    ("qa-fix", "file-out"): (NOT_DONE, None, set()),
    ("qa-fix", "file-in"): (DONE, _SAME_KB, {"git", "plain"}),
    ("qa-fix", "file-hard"): (
        NOT_DONE,
        "under git the compare-and-replace already refuses a hard-linked file",
        {"git"},
    ),
    ("qa-fix", "dir-out"): (NOT_DONE, _NOT_FOUND, {"git", "plain"}),
    ("qa-fix", "dir-in"): (DONE, _REAL_FOLDER, {"git", "plain"}),
    # migrate-standards: the file moves from standards/ to validations/
    ("migrate-standards", "file-out"): (NOT_DONE, None, set()),
    ("migrate-standards", "file-in"): (NOT_DONE, None, set()),
    ("migrate-standards", "file-hard"): (NOT_DONE, None, set()),
    ("migrate-standards", "dir-out"): (NOT_DONE, None, set()),
    ("migrate-standards", "dir-in"): (DONE, _SAME_KB, {"git", "plain"}),
}


def _grid():
    for (op, placement), (_expected, reason, controls) in EXPECTED.items():
        for kind in ("git", "plain"):
            marks = [pytest.mark.control(reason=reason)] if kind in controls else []
            yield pytest.param(op, placement, kind, id=f"{op}-{placement}-{kind}", marks=marks)


@pytest.mark.control(reason="checks the grid itself, not the code under test")
def test_the_grid_has_every_cell():
    """Every operation meets every placement, except a create over a hard
    link (a hard link is an existing file: the create is ENTRY_EXISTS)."""
    want = {(op, p) for op in OPS for p in PLACEMENTS} - {("create", "file-hard")}
    assert set(EXPECTED) == want


@pytest.mark.parametrize(("op", "placement", "kind"), list(_grid()))
def test_no_write_leaves_the_kb_root(tmp_path: Path, op: str, placement: str, kind: str):
    run, rel, text, kb_type = OPS[op]
    cell = _cell(
        tmp_path, git=kind == "git", kb_type=kb_type, rel=rel, text=text, placement=placement
    )
    outside_before = _tree(cell.outside)
    kb_before = _tree(cell.root)

    outcome = run(cell)

    # The property, by bytes: nothing outside the root changed.
    assert _tree(cell.outside) == outside_before, (op, placement, outcome)

    (state, code), _reason, _controls = EXPECTED[(op, placement)]
    if state == "done":
        assert outcome.done, outcome
        return
    assert not outcome.done, outcome
    if state == "refused":
        assert outcome.code == code, outcome
        # No refusal names an absolute path, the hard-link one included.
        assert str(tmp_path) not in outcome.message and "/private/" not in outcome.message
    # A refusal leaves the KB as it was too (git's own files aside).
    kb_after = {k: v for k, v in _tree(cell.root).items() if not k.startswith(".git")}
    assert kb_after == {k: v for k, v in kb_before.items() if not k.startswith(".git")}, outcome


def test_a_rename_of_a_link_says_why(tmp_path: Path):
    """Property 2: the refusal names the reason, and the link's target is
    still there."""
    cell = _cell(tmp_path, git=False, rel="notes/s.md", text=NOTE, placement="file-in")
    outcome = op_rename(cell)
    assert not outcome.done
    assert "symbolic link" in outcome.message, outcome
    assert (cell.root / "store" / "s.txt").read_text() == NOTE
    assert (cell.root / "notes" / "s.md").is_symlink()


def test_an_outside_refusal_names_no_path(tmp_path: Path):
    """The refusal reaches API callers: it names neither the root nor the
    path the link resolves to."""
    cell = _cell(tmp_path, git=False, rel="notes/s.md", text=NOTE, placement="file-out")
    outcome = op_update(cell)
    assert outcome.code == OUTSIDE_CODE, outcome
    assert str(tmp_path) not in outcome.message and "outside/" not in outcome.message


# --------------------------------------------------------------------------
# the writer functions themselves, where no lookup stands in front
# --------------------------------------------------------------------------


@pytest.mark.parametrize("git", [True, False], ids=["git", "plain"])
@pytest.mark.parametrize("position", ["file", "dir"])
def test_the_writer_refuses_a_path_that_leaves_the_root(tmp_path: Path, git: bool, position):
    """``publish_entry_text``, ``create_entry_text``, ``move_entry_text`` and
    ``remove_entry_file`` decide containment themselves, against the root
    they are given: a caller that skips every lookup still cannot write
    outside."""
    from pyrite.storage import entry_writer

    root = tmp_path / "kb"
    root.mkdir()
    if git:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True, timeout=30)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "s.md").write_text(NOTE)
    if position == "file":
        os.symlink(outside / "s.md", root / "s.md")
        src = root / "s.md"
        fresh = root / "n.md"
        os.symlink(outside / "n.md", fresh)  # dangling, out
    else:
        os.symlink(outside, root / "d")
        src = root / "d" / "s.md"
        fresh = root / "d" / "n.md"
    raw = NOTE.encode()
    before = _tree(outside)

    for call in (
        lambda: entry_writer.publish_entry_text(src, "x\n", expect=raw, root=root),
        lambda: entry_writer.create_entry_text(fresh, "x\n", root=root),
        lambda: entry_writer.move_entry_text(src, raw, root / "m.md", "x\n", root=root),
        lambda: entry_writer.move_entry_text(root_note(root), raw, fresh, "x\n", root=root),
        lambda: entry_writer.remove_entry_file(src, root=root),
    ):
        with pytest.raises(Exception) as refused:  # noqa: PT011 - the code decides
            call()
        assert getattr(refused.value, "error_code", None) == OUTSIDE_CODE, refused.value
        assert _tree(outside) == before
    assert not (root / "m.md").exists()


def root_note(root: Path) -> Path:
    p = root / "inside.md"
    if not p.exists():
        p.write_text(NOTE)
    return p


@pytest.mark.control(reason="a root reached through a link is its own resolved self")
def test_a_root_reached_through_a_link_still_writes(tmp_path: Path):
    from pyrite.storage import entry_writer

    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    os.symlink(real, linked)
    entry_writer.create_entry_text(linked / "a.md", NOTE, root=linked)
    entry_writer.publish_entry_text(linked / "a.md", "y\n", expect=NOTE.encode(), root=linked)
    assert (real / "a.md").read_text() == "y\n"


# --------------------------------------------------------------------------
# the code on the surfaces
# --------------------------------------------------------------------------


def test_the_cli_answers_the_outside_code(tmp_path: Path):
    """``pyrite update --format json`` carries ``OUTSIDE_KB_ROOT``."""
    import json

    cell = _cell(tmp_path, git=False, rel="notes/s.md", text=NOTE, placement="file-out")
    save_config(cell.config)  # the CLI reads it; the loader is never replaced
    result = CliRunner().invoke(
        app, ["update", "s", "-k", KB, "--title", "Changed", "--format", "json"]
    )
    assert result.exit_code != 0, result.output
    assert json.loads(result.output)["error_code"] == OUTSIDE_CODE, result.output
    assert (cell.outside / "s.md").read_text() == NOTE


# --------------------------------------------------------------------------
# the KB's own files beside its entries: kb.yaml and README.md
# --------------------------------------------------------------------------


def _linked_kb_file(tmp_path: Path, name: str, text: str) -> tuple[PyriteConfig, Path, Path]:
    root = tmp_path / "kb"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / name
    target.write_text(text)
    os.symlink(target, root / name)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=root, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    return config, root, target


def test_a_schema_write_does_not_write_through_a_kb_yaml_link_out(tmp_path: Path):
    """``SchemaService`` (REST and MCP schema writes) refuses a kb.yaml that
    links out of the KB; the file it points to keeps its bytes."""
    from pyrite.exceptions import PyriteError
    from pyrite.services.schema_service import SchemaService

    config, _root, target = _linked_kb_file(tmp_path, "kb.yaml", "name: k\n")
    with pytest.raises(PyriteError) as refused:
        SchemaService(config).add_type(KB, "ticket", {"description": "A ticket"})
    assert refused.value.error_code == OUTSIDE_CODE
    assert target.read_text() == "name: k\n"


def test_readme_write_does_not_write_through_a_readme_link_out(tmp_path: Path):
    config, _root, target = _linked_kb_file(tmp_path, "README.md", "mine\n")
    save_config(config)  # the CLI reads it; the loader is never replaced
    result = CliRunner().invoke(app, ["readme", "-k", KB, "--write"])
    assert result.exit_code != 0, result.output
    assert OUTSIDE_CODE in result.output, result.output
    assert target.read_text() == "mine\n"


# --------------------------------------------------------------------------
# the moves an update and the reconcile make, and a delete of many files
# --------------------------------------------------------------------------

TASKS_YAML = (
    "name: k\n"
    "types:\n"
    "  ticket:\n"
    '    subdirectory: "tasks/{status}"\n'
    "    fields:\n"
    "      status:\n"
    "        type: select\n"
    "        options: [active, done]\n"
)
TASK = "---\nid: t\ntype: ticket\ntitle: T\nstatus: {status}\n---\n\nbody\n"


def _tasks_kb(tmp_path: Path) -> Cell:
    root = tmp_path / "kb"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "kb.yaml").write_text(TASKS_YAML)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=root, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    return Cell(root, outside, config, root / "tasks" / "active" / "t.md")


def test_an_update_does_not_move_a_linked_file_as_its_link(tmp_path: Path):
    """A templated subdirectory moves a file when its field changes. A file
    that is a link inside the KB is written through and stays where it is:
    moving it would remove the link and leave its target behind."""
    cell = _tasks_kb(tmp_path)
    (cell.root / "store").mkdir()
    target = cell.root / "store" / "t.txt"
    target.write_text(TASK.format(status="active"))
    cell.entry.parent.mkdir(parents=True)
    os.symlink(target, cell.entry)
    cell.sync()

    outcome = _service(cell, lambda svc: svc.update_entry("t", KB, status="done"))

    assert outcome.done, outcome
    assert cell.entry.is_symlink() and "status: done" in target.read_text()
    assert not (cell.root / "tasks" / "done" / "t.md").exists()


def test_the_reconcile_does_not_move_a_link_out_of_the_kb(tmp_path: Path):
    """``reconcile_templated_subdirectories(apply=True)`` names a misplaced
    file that is a link to outside as an error and leaves both trees as they
    were (it used to hard-link the outside file into the KB)."""
    from pyrite.services.kb_service import KBService

    cell = _tasks_kb(tmp_path)
    away = cell.outside / "t.md"
    away.write_text(TASK.format(status="done"))  # misplaced: under active/
    cell.entry.parent.mkdir(parents=True)
    os.symlink(away, cell.entry)
    cell.sync()
    outside_before, kb_before = _tree(cell.outside), _tree(cell.root)

    db = cell.db()
    try:
        result = KBService(cell.config, db).reconcile_templated_subdirectories(KB, apply=True)
    finally:
        db.close()

    assert result.moved == 0 and [e for e, _ in result.errors] == ["t"], result
    assert _tree(cell.outside) == outside_before
    assert _tree(cell.root) == kb_before


def test_a_delete_that_would_leave_the_root_removes_no_file(tmp_path: Path):
    """Every removal is decided before the first: when one of the files that
    hold the id resolves outside, none is removed."""
    cell = _cell(tmp_path, git=False, rel="notes/s.md", text=NOTE, placement="file-out")
    (cell.root / "a.md").write_text(NOTE)  # a second holder, inside
    kb_before, outside_before = _tree(cell.root), _tree(cell.outside)

    outcome = op_delete(cell)

    assert outcome.code == OUTSIDE_CODE, outcome
    assert _tree(cell.root) == kb_before and _tree(cell.outside) == outside_before


# --------------------------------------------------------------------------
# the race: a folder swapped for a link after the path is decided
# --------------------------------------------------------------------------

RACE_OPS = {
    "update": op_update,
    "rename": op_rename,
    "delete": op_delete,
    "create": lambda cell: _service(
        cell, lambda svc: svc.create_entry(KB, "fresh", "Fresh", "note", "body")
    ),
}


@pytest.mark.parametrize("git", [True, False], ids=["git", "plain"])
@pytest.mark.parametrize("op", sorted(RACE_OPS))
def test_a_folder_swapped_for_a_link_after_the_decision_is_not_written_into(
    tmp_path: Path, monkeypatch, op: str, git: bool
):
    """The path the writer approves is the path it writes. ``notes/`` is a
    real folder when every check runs and when the path is resolved; it is
    swapped for a link to a copy outside the root right before the writer
    opens it. The open goes beneath the root without following a link, so it
    is refused and the outside copy keeps its bytes (the writer used to
    resolve the path again and write, or delete, outside)."""
    from pyrite.utils import kb_files

    cell = _cell(tmp_path, git=git, rel="notes/s.md", text=NOTE, placement="file-in")
    # A real notes/s.md (not the grid's link): the swap is the only link.
    entry = cell.root / "notes" / "s.md"
    entry.unlink()
    entry.write_text(NOTE)
    cell.sync()
    real_open_dir = kb_files.KBFiles._open_dir
    swapped = {}

    def swap_then_open(self, parts, **kw):
        if not swapped:
            away = cell.outside / "notes"
            shutil.move(str(cell.root / "notes"), str(away))
            os.symlink(away, cell.root / "notes")
            swapped["outside"] = _tree(cell.outside)
        return real_open_dir(self, parts, **kw)

    monkeypatch.setattr(kb_files.KBFiles, "_open_dir", swap_then_open)

    outcome = RACE_OPS[op](cell)

    assert swapped, "the writer never opened a folder"
    assert _tree(cell.outside) == swapped["outside"], (op, outcome)
    assert not outcome.done and outcome.code == OUTSIDE_CODE, outcome


# --------------------------------------------------------------------------
# the in-place fallback writes only the inode the check approved
# --------------------------------------------------------------------------


def _in_place_cell(tmp_path: Path, monkeypatch, git: bool, swap) -> tuple[Cell, Outcome, dict]:
    """An update forced onto the in-place path (the folder takes no temp
    file), with ``swap(cell)`` run between the last check and the open."""
    from pyrite.utils import kb_files

    cell = _cell(tmp_path, git=git, rel="notes/s.md", text=NOTE, placement="file-in")
    entry = cell.root / "notes" / "s.md"
    entry.unlink()
    entry.write_text(NOTE)
    cell.sync()
    monkeypatch.setattr(kb_files.Folder, "write_temp", lambda self, name, data, like, **kw: None)
    real = kb_files.Folder.write_in_place
    seen: dict = {}

    def swap_then_write(self, name, data, why, checked):
        if not seen:
            swap(cell)
            seen["outside"] = _tree(cell.outside)
        return real(self, name, data, why, checked)

    monkeypatch.setattr(kb_files.Folder, "write_in_place", swap_then_write)
    return cell, op_update(cell), seen


@pytest.mark.parametrize("git", [True, False], ids=["git", "plain"])
def test_a_hard_link_swapped_in_before_an_in_place_write_is_not_written(
    tmp_path: Path, monkeypatch, git: bool
):
    """Between the link-count check and the open, the name is replaced by a
    hard link to a file outside the root. The open's descriptor is checked
    (one link, the inode the check saw), so the outside file keeps its bytes."""

    def to_hard_link(cell: Cell) -> None:
        evil = cell.outside / "evil.md"
        evil.write_text(NOTE)
        (cell.root / "notes" / "s.md").unlink()
        os.link(evil, cell.root / "notes" / "s.md")

    cell, outcome, seen = _in_place_cell(tmp_path, monkeypatch, git, to_hard_link)
    assert seen, "the in-place write was never reached"
    assert _tree(cell.outside) == seen["outside"], outcome
    assert (cell.outside / "evil.md").read_text() == NOTE
    assert not outcome.done and outcome.code == "STORAGE_ERROR", outcome


@pytest.mark.parametrize("git", [True, False], ids=["git", "plain"])
def test_a_fifo_swapped_in_before_an_in_place_write_neither_hangs_nor_is_written(
    tmp_path: Path, monkeypatch, git: bool
):
    """A FIFO in the file's place cannot hold the write (and the lock): the
    open does not block, and nothing is written."""
    import threading

    def to_fifo(cell: Cell) -> None:
        (cell.root / "notes" / "s.md").unlink()
        os.mkfifo(cell.root / "notes" / "s.md")

    box: dict = {}
    worker = threading.Thread(
        target=lambda: box.update(r=_in_place_cell(tmp_path, monkeypatch, git, to_fifo))
    )
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive(), "the in-place write blocked on a FIFO"
    cell, outcome, seen = box["r"]
    assert seen and not outcome.done, outcome
    assert _tree(cell.outside) == seen["outside"]


@pytest.mark.parametrize("git", [True, False], ids=["git", "plain"])
def test_an_entry_with_a_250_character_file_name_is_updated(tmp_path: Path, git: bool):
    """The temp file's name keeps only a prefix of the entry's, so a name
    near the 255-byte limit is still replaced atomically, and no temp file
    is left (the base wrote such a file in place)."""
    long = "n" * 250 + ".md"
    cell = _cell(tmp_path, git=git, rel=f"notes/{long}", text=NOTE, placement="file-in")
    entry = cell.root / "notes" / long
    entry.unlink()
    entry.write_text(NOTE)
    cell.sync()
    before_ino = entry.stat().st_ino

    outcome = op_update(cell)

    assert outcome.done, outcome
    assert "title: Changed" in entry.read_text()
    assert entry.stat().st_ino != before_ino, "written in place, not replaced"
    assert not [p for p in (cell.root / "notes").iterdir() if p.name.endswith(".tmp")]


@pytest.mark.parametrize("git", [True, False], ids=["git", "plain"])
def test_a_file_replaced_before_an_in_place_write_is_read_again_not_overwritten(
    tmp_path: Path, monkeypatch, git: bool
):
    """Another regular file put at the name between the check and the open
    is not the inode the check approved: the write is refused as a change,
    the update reads the new file again, and what that file added is kept."""
    replaced = NOTE.replace("body\n", "body\nadded by hand\n")

    def to_another_file(cell: Cell) -> None:
        other = cell.root / "notes" / "other.txt"
        other.write_text(replaced)
        os.replace(other, cell.root / "notes" / "s.md")

    cell, outcome, seen = _in_place_cell(tmp_path, monkeypatch, git, to_another_file)
    assert seen and outcome.done, outcome
    text = (cell.root / "notes" / "s.md").read_text()
    assert "added by hand" in text and "title: Changed" in text, text

"""One predicate decides what the walk lists; the walk and every writer ask it (#1552).

Invariant 1 of #1552, by recording and by substitution. The predicate is
``pyrite/storage/entry_paths.unlisted_reason``:

- substituted with one that reserves one more name, the walk and the
  lookups stop listing that name, so there is no second copy of the rule;
- recorded, every call that places a new entry-file name (a create through
  ``create_entry_text``, the templated move, ``move_entry_text``, the
  reconcile's ``KBFiles.move``) has asked it about that name before the file
  appeared.

These tests patch the predicate on purpose, so they live apart from
``test_writes_land_where_the_walk_lists.py``, which drives the CLI, REST
and MCP surfaces and may patch nothing behind them
(``tests/test_tests_drive_the_surface_not_the_mock.py``). The KB and the
helpers are that file's.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.test_writes_land_where_the_walk_lists import KB, Env, _note, _tree


@pytest.fixture
def plain(tmp_path):
    e = Env(tmp_path)
    yield e
    e.close()


def test_the_walk_and_the_lookups_apply_the_one_predicate(plain, monkeypatch):
    """Teach the predicate one more reserved name: the walk and the lookup
    both stop listing it. There is no second copy of the rule."""
    from pyrite.storage import entry_paths

    (plain.root / "notes").mkdir()
    (plain.root / "notes" / "zz.md").write_text(_note("zz", "ZZ"))
    (plain.root / "notes" / "aa.md").write_text(_note("aa", "AA"))
    real = entry_paths.unlisted_reason

    def reserving_zz(root, path):
        return "readme" if Path(path).name == "zz.md" else real(root, path)

    monkeypatch.setattr(entry_paths, "unlisted_reason", reserving_zz)
    listed = sorted(p.name for p in plain.repo.list_files())
    assert listed == ["aa.md"], listed
    assert plain.repo.find_file("zz") is None
    assert plain.repo.find_file("aa") is not None


def _record_asked(monkeypatch) -> list[tuple[Path, bool]]:
    """Every path the predicate is asked about, with whether anything was at
    that path at the moment of asking: a writer that asks after it wrote is
    seen asking about a path that already exists (round 1, F4)."""
    from pyrite.storage import entry_paths

    asked: list[tuple[Path, bool]] = []
    real = entry_paths.unlisted_reason

    def recording(root, path):
        asked.append((Path(os.path.abspath(path)), os.path.lexists(path)))
        return real(root, path)

    monkeypatch.setattr(entry_paths, "unlisted_reason", recording)
    return asked


def _placed(before: dict, root: Path) -> set[Path]:
    """The new file names the write put in the KB."""
    return {
        root / rel for rel, value in _tree(root).items() if value != "<dir>" and rel not in before
    }


def _write_create(env: Env) -> None:
    env.svc.create(KB, {"entry_type": "note", "title": "Fresh", "body": "b"})


def _write_templated_move(env: Env) -> None:
    env.svc.update("m1", KB, {"phase": "done"})


def _write_reconcile(env: Env) -> None:
    p = env.repo.find_file("m1")
    text = p.read_text().replace("phase: open", "phase: closed")
    p.write_text(text)
    env.sync()
    env.svc.reconcile_templated_subdirectories(KB, apply=True)


def _write_migrate_standards(env: Env) -> None:
    from pyrite.storage.entry_writer import move_entry_text

    src = env.root / "notes" / "s1.md"
    move_entry_text(
        src, src.read_bytes(), env.root / "standards" / "s1.md", src.read_text(), root=env.root
    )


#: Every call that places a new entry-file name (the groom's enumeration:
#: create_entry_text through a create and the templated move,
#: move_entry_text, and the reconcile's KBFiles.move).
PLACING_WRITERS: dict[str, Callable[[Env], None]] = {
    "create": _write_create,
    "templated move": _write_templated_move,
    "reconcile": _write_reconcile,
    "move_entry_text": _write_migrate_standards,
}


@pytest.mark.parametrize("writer", list(PLACING_WRITERS))
def test_every_writer_asks_the_predicate_about_the_name_it_places(plain, monkeypatch, writer):
    plain.svc.create(
        KB, {"entry_type": "st", "title": "M1", "id": "m1", "body": "b", "phase": "open"}
    )
    (plain.root / "notes").mkdir(exist_ok=True)
    (plain.root / "notes" / "s1.md").write_text(_note("s1", "S1"))
    plain.sync()
    before = _tree(plain.root)
    asked = _record_asked(monkeypatch)

    PLACING_WRITERS[writer](plain)

    placed = _placed(before, plain.root)
    assert placed, "the writer placed nothing; the case does not exercise it"
    for path in placed:
        here = Path(os.path.abspath(path))
        # Asked about this name while nothing was there yet: before the write.
        assert (here, False) in asked, (path, [a for a in asked if a[0] == here])


#: Every call in the code that can place a new entry-file name, by the
#: function it is in, mapped to the PLACING_WRITERS case that runs it. Found
#: by the scan below, not kept by hand: a new call to `create_entry_text`,
#: `move_entry_text` or a `.move(` anywhere in `pyrite/` or an extension
#: fails `test_the_placing_writers_are_the_ones_in_the_code` until it is
#: mapped here (and so run above) or listed as not placing a new name.
PLACING_CALLS = {
    ("pyrite/storage/repository.py", "KBRepository.save"): "create",
    ("pyrite/services/kb_service.py", "KBService._templated_move"): "templated move",
    (
        "pyrite/services/kb_service.py",
        "KBService.reconcile_templated_subdirectories",
    ): "reconcile",
    (
        "extensions/software-kb/src/pyrite_software_kb/cli.py",
        "sw_migrate_standards",
    ): "move_entry_text",
}
_PLACING_NAMES = {"create_entry_text", "move_entry_text"}


def _placing_calls() -> set[tuple[str, str]]:
    import ast

    repo = Path(__file__).resolve().parent.parent
    roots = [repo / "pyrite", *sorted((repo / "extensions").glob("*/src"))]
    found: set[tuple[str, str]] = set()
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(repo).as_posix()
            if rel == "pyrite/storage/entry_writer.py":
                continue  # the two functions themselves
            tree = ast.parse(path.read_text(encoding="utf-8"))

            def visit(node, scope, rel=rel):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                        visit(child, [*scope, child.name], rel)
                        continue
                    if isinstance(child, ast.Call):
                        f = child.func
                        name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                        if name in _PLACING_NAMES or (
                            name == "move" and isinstance(f, ast.Attribute)
                        ):
                            found.add((rel, ".".join(scope)))
                    visit(child, scope, rel)

            visit(tree, [])
    return found


@pytest.mark.control(
    reason="a structural ratchet: it holds on dev and goes red when a new placing call appears"
)
def test_the_placing_writers_are_the_ones_in_the_code():
    found = _placing_calls()
    assert found == set(PLACING_CALLS), {
        "unmapped (add a case and map it)": sorted(found - set(PLACING_CALLS)),
        "stale": sorted(set(PLACING_CALLS) - found),
    }
    assert set(PLACING_CALLS.values()) == set(PLACING_WRITERS)

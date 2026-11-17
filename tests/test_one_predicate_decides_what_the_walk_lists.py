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
    """The predicate asks a writer's GATE makes, with whether anything was at
    that path at the moment of asking (round 1, F4; round 2, E1).

    A gate is a call to ``entry_paths.require_listed`` or
    ``entry_paths.kept_in_place``: the two functions every placing writer
    calls before it writes (``KBRepository._require_listed`` ends in the
    first). Only a ``unlisted_reason`` call made while a gate is running is
    recorded, so a look at the same path from elsewhere (the ``-N`` rule's
    ``is_listed`` in ``_free_default_path``, the walk) does not satisfy the
    test. A gate made a no-op, by replacing it or by removing the writer's
    call, records nothing; a gate asked after the write records the path as
    already there; a gate asked about another path records that path."""
    from pyrite.storage import entry_paths

    asked: list[tuple[Path, bool]] = []
    depth = {"n": 0}
    real_reason = entry_paths.unlisted_reason

    def recording(root, path):
        if depth["n"]:
            asked.append((Path(os.path.abspath(path)), os.path.lexists(path)))
        return real_reason(root, path)

    def gate(fn):
        def inside(*args, **kwargs):
            depth["n"] += 1
            try:
                return fn(*args, **kwargs)
            finally:
                depth["n"] -= 1

        return inside

    monkeypatch.setattr(entry_paths, "unlisted_reason", recording)
    for name in ("require_listed", "kept_in_place"):
        monkeypatch.setattr(entry_paths, name, gate(getattr(entry_paths, name)))
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
#: function it is in and how many such calls that function makes, mapped to
#: the PLACING_WRITERS case that runs it. Found by the scan below, not kept
#: by hand. The scan sees, in `pyrite/` and every extension's `src`:
#:
#: - a call of `create_entry_text` or `move_entry_text`, by name, as an
#:   attribute (`entry_writer.create_entry_text(...)`), or under an alias
#:   (`from ... import create_entry_text as w`);
#: - a call of an attribute named `move`, `move_bytes` or `rename`
#:   (`KBFiles.move`, `KBFiles.move_bytes`, `Path.rename`, `os.rename`,
#:   whatever the receiver);
#: - `pyrite/storage/entry_writer.py` too, outside the bodies of the two
#:   primitives themselves.
#:
#: A new such call, or a second one inside a function already listed, fails
#: `test_the_placing_writers_are_the_ones_in_the_code` until it is mapped here
#: (and so run above).
#:
#: What it does not see (round 2, table (b)):
#:
#: - `KBFiles.write_bytes(..., expect=ABSENT)` (create_entry_text's own
#:   primitive) and `os.replace`: caught instead by
#:   `tests/test_entry_file_writers_are_one_function.py`, which makes every
#:   writer of bytes be classified by hand;
#: - `Path.replace` (an attribute `replace` on anything but `os`): caught by
#:   neither. `replace` is too common a name to scan for (`str.replace`);
#:   a stated limit;
#: - a writer classified in that ratchet as "not an entry file" that does
#:   place a `*.md` under a KB: caught by neither; the classification is the
#:   judgement.
PLACING_CALLS: dict[tuple[str, str], tuple[str, int]] = {
    ("pyrite/storage/repository.py", "KBRepository.save"): ("create", 1),
    ("pyrite/services/kb_service.py", "KBService._templated_move"): ("templated move", 1),
    # The move, and its rollback to the file's own (listed) place.
    (
        "pyrite/services/kb_service.py",
        "KBService.reconcile_templated_subdirectories",
    ): ("reconcile", 2),
    (
        "extensions/software-kb/src/pyrite_software_kb/cli.py",
        "sw_migrate_standards",
    ): ("move_entry_text", 1),
}
#: Calls the scan finds that place no new entry-file name, with the reason.
NOT_PLACING: dict[tuple[str, str], tuple[str, int]] = {
    (
        "pyrite/utils/kb_files.py",
        "Folder.rename",
    ): ("the primitive: a temp name renamed onto the path the caller's gate asked about", 1),
    (
        "pyrite/utils/kb_files.py",
        "Folder.link_exclusive",
    ): ("the primitive: a temp name renamed onto the path the caller's gate asked about", 1),
    (
        "pyrite/utils/kb_files.py",
        "KBFiles.write_bytes.step",
    ): ("the primitive: a temp name renamed onto the path the caller's gate asked about", 1),
    (
        "pyrite/services/kb_service.py",
        "KBService.rename_entry",
    ): ("an id rename in place: the file keeps its name (ADR-0038 I6)", 1),
}
_PLACING_NAMES = {"create_entry_text", "move_entry_text"}
_PLACING_ATTRIBUTES = {"move", "move_bytes", "rename"}
_PRIMITIVES = ("pyrite/storage/entry_writer.py", {"create_entry_text", "move_entry_text"})


def _placing_calls() -> dict[tuple[str, str], int]:
    import ast
    from collections import Counter

    repo = Path(__file__).resolve().parent.parent
    roots = [repo / "pyrite", *sorted((repo / "extensions").glob("*/src"))]
    found: Counter = Counter()
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(repo).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = set(_PLACING_NAMES)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    names |= {a.asname for a in node.names if a.name in _PLACING_NAMES and a.asname}

            def visit(node, scope, rel=rel, names=names):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                        if rel == _PRIMITIVES[0] and not scope and child.name in _PRIMITIVES[1]:
                            continue  # the primitive's own body
                        visit(child, [*scope, child.name], rel, names)
                        continue
                    if isinstance(child, ast.Call):
                        f = child.func
                        if isinstance(f, ast.Name):
                            placing = f.id in names
                        elif isinstance(f, ast.Attribute):
                            placing = f.attr in _PLACING_NAMES | _PLACING_ATTRIBUTES
                        else:
                            placing = False
                        if placing:
                            found[(rel, ".".join(scope))] += 1
                    visit(child, scope, rel, names)

            visit(tree, [])
    return dict(found)


@pytest.mark.control(
    reason="a structural ratchet: dev makes the same calls, so it holds there; it goes red "
    "when a call of a shape the scan sees is added (see PLACING_CALLS' comment)"
)
def test_the_placing_writers_are_the_ones_in_the_code():
    found = _placing_calls()
    expected = {key: count for key, (_why, count) in {**PLACING_CALLS, **NOT_PLACING}.items()}
    assert found == expected, {
        "unmapped or a new call (add a case and map it)": sorted(
            (k, n) for k, n in found.items() if expected.get(k) != n
        ),
        "stale": sorted(k for k in expected if k not in found),
    }
    assert {case for case, _count in PLACING_CALLS.values()} == set(PLACING_WRITERS)

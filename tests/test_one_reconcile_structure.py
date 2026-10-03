"""The reconcile rule lives in one place (ADR-0038 step 2, design principle 8).

Indexing a KB's files is `IndexManager.reconcile_kb`, and nothing else. Before
it, four functions each walked the files and wrote rows by their own rule
(`index_kb`, `sync_incremental`, `sync_kb`, `index_with_attribution`), and
each got a different case wrong (#485, #486, #487, #495).

This test fails when a function anywhere in `pyrite/` or an extension's source
both walks a KB's files and writes index rows: that is a second reconcile. Go
through `reconcile_kb` (or `plan_reconcile` to read without writing) instead,
or, if the function is a write of entries it just moved or created rather than
a reconcile, add it to ALLOWED with the reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Calls that walk a KB's files.
WALKS = {"list_files", "list_all_files", "list_entries", "rglob", "walk"}
# Calls that write an index row.
WRITES = {"upsert_entry", "index_entry"}

# "<path relative to the repo>::<function>" -> why it is not a reconcile.
ALLOWED = {
    "pyrite/services/kb_service.py::reconcile_templated_subdirectories": (
        "moves misplaced files of a templated-subdirectory type and writes the "
        "moved entry's row: a write of the files it moved, not a reconcile"
    ),
}


def _calls(node: ast.AST, *, files_only: bool = False) -> set[str]:
    """Names of the functions ``node`` calls. With ``files_only``, a call on
    a database object (``db.list_entries(...)``, a query of rows, not a walk
    of files) is left out: its receiver's name ends in ``db``."""
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Attribute):
                if files_only and ast.unparse(func.value).lower().endswith("db"):
                    continue
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def second_reconciles(source: str, label: str) -> list[str]:
    """Functions in ``source`` that walk files and write rows."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if _calls(node, files_only=True) & WALKS and _calls(node) & WRITES:
                found.append(f"{label}::{node.name}")
    return found


def _sources():
    for base in [ROOT / "pyrite", *sorted((ROOT / "extensions").glob("*/src"))]:
        for path in sorted(base.rglob("*.py")):
            yield path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")


def test_no_second_reconcile():
    found = []
    for label, source in _sources():
        found += second_reconciles(source, label)
    unexpected = sorted(set(found) - set(ALLOWED))
    assert not unexpected, (
        f"{unexpected} walk KB files and write index rows: a second reconcile. "
        "Index through IndexManager.reconcile_kb (ADR-0038 step 2)."
    )


@pytest.mark.control(reason="checks the allowlist, not the change")
def test_allowlist_has_no_stale_entries():
    found = set()
    for label, source in _sources():
        found.update(second_reconciles(source, label))
    assert set(ALLOWED) <= found, f"no longer walk-and-write: {sorted(set(ALLOWED) - found)}"


def test_the_index_writes_rows_in_two_places_only():
    """Inside IndexManager, a row is written by `_write_claim` (the reconcile)
    and `index_entry` (a single write, for DocumentManager's write-through)."""
    source = (ROOT / "pyrite/storage/index.py").read_text(encoding="utf-8")
    writers = sorted(
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and "upsert_entry" in _calls(node)
    )
    assert writers == ["_write_claim", "index_entry"]


@pytest.mark.control(reason="tests the scanner on a fixed string")
def test_the_scan_catches_a_walk_and_write():
    """Negative control: the shape the old index_kb had is caught."""
    old_index_kb = (
        "def index_kb(self, kb_name):\n"
        "    for entry, path in repo.list_entries():\n"
        "        self.db.upsert_entry(self._entry_to_dict(entry, kb_name, path))\n"
    )
    assert second_reconciles(old_index_kb, "x.py") == ["x.py::index_kb"]


@pytest.mark.control(reason="tests the scanner on a fixed string")
def test_a_query_of_rows_is_not_a_walk():
    rows_only = (
        "def backfill(db, kb):\n"
        "    for row in db.list_entries(kb_name=kb):\n"
        "        db.upsert_entry(row)\n"
    )
    assert second_reconciles(rows_only, "x.py") == []

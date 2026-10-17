"""Every write of an entry file's bytes goes through one function: a ratchet (#955).

ADR-0042 decision 12 and ``kb/design.md`` principle 8: a write to an entry
file is an operation applied by ``file_operations.apply`` and published by
``pyrite/storage/entry_writer.py`` (over ``utils/atomic_write.py``). This
test walks every call in ``pyrite/`` and the extensions that can put bytes
into a file (``write_text``, ``write_bytes``, ``os.replace``, ``os.rename``,
``.save(``, ``atomic_write_text``) and sorts each into one of three lists:

- ``THE_WRITE_FUNCTION``: the one path.
- ``ENTRY_FILE_WRITERS_NOT_YET_CONVERTED``: the writers that still emit or
  write a whole entry file, each with the B6 phase that converts it. Its size
  is pinned and only shrinks: a converted writer that is still listed fails
  ``test_the_list_holds_only_what_is_still_owed``, so the room it leaves
  cannot be reused.
- ``NOT_AN_ENTRY_FILE``: writers of something else (an export, a scaffold,
  a config file, a cache), each with the reason.

A call in none of them fails: a new writer of an entry file goes through
``entry_writer``, and anything else is classified here, by hand.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ROOTS = [REPO / "pyrite", *sorted((REPO / "extensions").glob("*/src"))]

_ATTRIBUTES = {"write_text", "write_bytes", "replace", "rename", "save"}
_NAMES = {"atomic_write_text"}

#: (module path relative to the repo, enclosing function) -> why
THE_WRITE_FUNCTION: dict[tuple[str, str], str] = {
    (
        "pyrite/utils/atomic_write.py",
        "atomic_write_text",
    ): "the crash-safe replace, compare and lock",
    ("pyrite/storage/entry_writer.py", "publish_entry_text"): "the one publish of an update",
    ("pyrite/storage/entry_writer.py", "create_entry_text"): "the one publish of a templated move",
}

#: Shrinks with each B6 phase (the spike on #730, §4 and §5).
ENTRY_FILE_WRITERS_NOT_YET_CONVERTED: dict[tuple[str, str], str] = {
    (
        "pyrite/models/base.py",
        "Entry._publish_exclusive",
    ): "the model's exclusive publish; no create uses it since C2, pinned by test_entry_save_atomic.py until the last caller of Entry.save goes (P4)",
    (
        "pyrite/models/base.py",
        "Entry.save",
    ): "the whole-file emit: rename, schema migrate, ids pin and sw migrate-standards still call it (P4)",
    (
        "pyrite/storage/repository.py",
        "KBRepository.save",
    ): "the non-exclusive branch is Entry.save for rename's re-save (P4); a create publishes with create_entry_text",
    (
        "pyrite/storage/repository.py",
        "KBRepository.rename",
    ): "rename's link rewrite and re-save (P4)",
    (
        "pyrite/storage/document_manager.py",
        "DocumentManager.save_entry",
    ): "a create's call of KBRepository.save; add_link/add_links append an item through apply() since P4",
    ("pyrite/services/id_pin_service.py", "_rename"): "ids pin, under the lock (P4)",
    ("pyrite/cli/schema_commands.py", "schema_migrate"): "schema migrate (P4)",
    (
        "extensions/software-kb/src/pyrite_software_kb/cli.py",
        "sw_migrate_standards",
    ): "sw migrate-standards (P4)",
}
ENTRY_FILE_WRITERS_NOT_YET_CONVERTED_SIZE = 8

NOT_AN_ENTRY_FILE: dict[tuple[str, str], str] = {
    (
        "pyrite/services/worktree_service.py",
        "WorktreeService.ensure_worktree",
    ): "a .gitignore beside the overlay db",
    (
        "pyrite/services/kb_registry_service.py",
        "KBRegistryService._apply_preset",
    ): "a KB's kb.yaml template",
    ("pyrite/services/url_checker.py", "URLChecker.save_cache"): "the URL checker's cache",
    ("pyrite/cli/__init__.py", "generate_readme_cmd"): "a generated README, not an entry",
    ("pyrite/cli/extension_commands.py", "extension_init"): "an extension scaffold",
    ("pyrite/cli/mcp_setup_command.py", "_setup_file"): "an MCP client's config file",
    ("pyrite/utils/yaml.py", "dump_yaml_file"): "config and schema files (kb.yaml, config.yaml)",
}


def _qualname(stack: list[str]) -> str:
    return ".".join(stack) if stack else "<module>"


class _Walker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.found: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        hit = False
        if isinstance(func, ast.Attribute) and func.attr in _ATTRIBUTES:
            if func.attr in ("replace", "rename"):
                hit = isinstance(func.value, ast.Name) and func.value.id == "os"
            else:
                hit = True
        elif isinstance(func, ast.Name) and func.id in _NAMES:
            hit = True
        if hit:
            self.found.append(_qualname(self.stack))
        self.generic_visit(node)


def _writers() -> dict[tuple[str, str], int]:
    """(module, function) -> count of writer calls, over every module."""
    sites: dict[tuple[str, str], int] = {}
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            walker = _Walker()
            walker.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
            rel = str(path.relative_to(REPO))
            for function in walker.found:
                sites[(rel, function)] = sites.get((rel, function), 0) + 1
    return sites


def _is_out_of_scope(module: str) -> bool:
    """Renderers and exports write what they generate, never an entry file."""
    return module.startswith(("pyrite/renderers/", "pyrite/services/export_service.py")) or (
        module.startswith("pyrite/services/site_cache.py")
    )


@pytest.mark.control(
    reason="a ratchet over what this PR does not change: it guards the next writer"
)
def test_every_writer_of_bytes_is_classified() -> None:
    sites = _writers()
    assert sites, "the walk found no writer at all; the scan is broken"
    classified = (
        set(THE_WRITE_FUNCTION) | set(ENTRY_FILE_WRITERS_NOT_YET_CONVERTED) | set(NOT_AN_ENTRY_FILE)
    )
    unclassified = sorted(
        site for site in sites if site not in classified and not _is_out_of_scope(site[0])
    )
    assert not unclassified, (
        "a call that writes bytes to a file is in no list; an entry file is written only "
        "through pyrite/storage/entry_writer.py, anything else is classified here:\n"
        + "\n".join(f"  {m}: {f}" for m, f in unclassified)
    )


@pytest.mark.control(
    reason="a ratchet over what this PR does not change: it guards the next writer"
)
def test_the_list_holds_only_what_is_still_owed() -> None:
    sites = _writers()
    gone = sorted(site for site in ENTRY_FILE_WRITERS_NOT_YET_CONVERTED if site not in sites)
    assert not gone, f"converted, so delete the line and lower the size: {gone}"
    assert len(ENTRY_FILE_WRITERS_NOT_YET_CONVERTED) == ENTRY_FILE_WRITERS_NOT_YET_CONVERTED_SIZE, (
        "the list only shrinks: lower ENTRY_FILE_WRITERS_NOT_YET_CONVERTED_SIZE with it"
    )


def test_the_write_function_is_present() -> None:
    sites = _writers()
    missing = sorted(site for site in THE_WRITE_FUNCTION if site not in sites)
    assert not missing, missing

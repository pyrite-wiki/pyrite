"""A software KB's ``board.yaml`` is read by one function, on one path (#1538).

``kb/design.md`` principle 8, one enforcement point: the board config is read
by ``board.load_board_config``, which validates it and refuses what it cannot
load, and the plugin reaches that function through one method,
``SoftwareKBPlugin._board_config``, which resolves the KB first. A second
reader of the file, or a second caller of the loader, would be a place where
a broken file is read some other way -- the eight loaders and five
``except Exception`` blocks this replaced.

The oracle is the extensions' source as it is on disk, walked with ``ast``.
The checker knows four rules and nothing about how the loader works:

1. A string naming ``board.yaml`` (other than a docstring) is written once:
   ``board.py``'s module constant ``BOARD_FILE``.
2. ``BOARD_FILE`` is used only inside ``board.py``.
3. ``load_yaml_file`` is called, in any extension, only by
   ``board.load_board_config``.
4. ``load_board_config`` is imported or called, anywhere in ``pyrite/`` or the
   extensions, only by ``SoftwareKBPlugin._board_config``.

Planted violations go through the same checker and must each be found, so a
checker that finds nothing cannot pass.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ROOTS = [REPO / "pyrite", *sorted((REPO / "extensions").glob("*/src"))]

BOARD_MODULE = "extensions/software-kb/src/pyrite_software_kb/board.py"
PLUGIN_MODULE = "extensions/software-kb/src/pyrite_software_kb/plugin.py"
CLI_MODULE = "extensions/software-kb/src/pyrite_software_kb/cli.py"

THE_READER = (BOARD_MODULE, "load_board_config")
THE_PATH_TO_THE_READER = (PLUGIN_MODULE, "SoftwareKBPlugin._board_config")
THE_FILE_NAME = (BOARD_MODULE, "<module>")


@functools.cache
def _disk_sources() -> tuple[tuple[str, str], ...]:
    out = {}
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            out[str(path.relative_to(REPO))] = path.read_text(encoding="utf-8")
    return tuple(out.items())


def _sources() -> dict[str, str]:
    return dict(_disk_sources())


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


class _Walker(ast.NodeVisitor):
    def __init__(self, docstrings: set[int]) -> None:
        self.docstrings = docstrings
        self.stack: list[str] = []
        self.hits: list[tuple[str, str]] = []  # (rule, qualname)

    def _where(self) -> str:
        return ".".join(self.stack) if self.stack else "<module>"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if (
            isinstance(node.value, str)
            and "board.yaml" in node.value
            and id(node) not in self.docstrings
        ):
            self.hits.append(("file-name", self._where()))

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id == "BOARD_FILE" and isinstance(node.ctx, ast.Load):
            self.hits.append(("file-name-constant", self._where()))
        elif node.id == "load_board_config" and isinstance(node.ctx, ast.Load):
            self.hits.append(("loader", self._where()))

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr == "load_board_config":
            self.hits.append(("loader", self._where()))
        elif node.attr == "BOARD_FILE":
            self.hits.append(("file-name-constant", self._where()))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        for alias in node.names:
            if alias.name == "load_board_config":
                self.hits.append(("loader", self._where()))
            elif alias.name == "BOARD_FILE":
                self.hits.append(("file-name-constant", self._where()))

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "load_yaml_file":
            self.hits.append(("yaml-read", self._where()))
        self.generic_visit(node)


@functools.cache
def _hits(module: str, text: str) -> tuple[tuple[str, str], ...]:
    tree = ast.parse(text, filename=module)
    walker = _Walker(_docstring_nodes(tree))
    walker.visit(tree)
    return tuple(walker.hits)


def violations(sources: dict[str, str]) -> list[str]:
    """Each read of board.yaml, or path to its reader, the four rules forbid."""
    found = []
    for module, text in sources.items():
        in_extension = module.startswith("extensions/")
        for rule, where in _hits(module, text):
            site = (module, where)
            if rule == "file-name" and site != THE_FILE_NAME:
                found.append(f"{module}: {where} names board.yaml")
            elif rule == "file-name-constant" and module != BOARD_MODULE:
                found.append(f"{module}: {where} uses BOARD_FILE")
            elif rule == "yaml-read" and in_extension and site != THE_READER:
                found.append(f"{module}: {where} reads YAML")
            elif rule == "loader" and site != THE_PATH_TO_THE_READER:
                if site == THE_READER:  # the definition's own name, in its own module
                    continue
                found.append(f"{module}: {where} reaches load_board_config")
    return sorted(found)


def test_board_yaml_is_read_by_one_function_on_one_path():
    sources = _sources()
    assert BOARD_MODULE in sources and PLUGIN_MODULE in sources, "the walk missed the extension"
    assert not violations(sources), (
        "board.yaml is read by board.load_board_config, reached through "
        "SoftwareKBPlugin._board_config, and nowhere else (#1538):\n"
        + "\n".join(f"  {v}" for v in violations(sources))
    )


def test_the_reader_and_its_path_are_there():
    """The rules name real functions: a rename cannot leave them vacuous."""
    sources = _sources()
    for module, function in (THE_READER, THE_PATH_TO_THE_READER):
        tree = ast.parse(sources[module])
        names = {f"{cls.name}.{fn.name}" if cls else fn.name for cls, fn in _functions(tree)}
        assert function in names, (module, function)
    plugin_tree = ast.parse(sources[PLUGIN_MODULE])
    walker = _Walker(set())
    walker.visit(plugin_tree)
    assert ("loader", THE_PATH_TO_THE_READER[1]) in walker.hits


def _functions(tree):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            yield None, node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    yield node, item


#: Each a change to one module that the checker must catch.
PLANTED: dict[str, tuple[str, str]] = {
    "a second reader of the file in the plugin": (
        PLUGIN_MODULE,
        "\n\ndef _peek(kb):\n    from pyrite.utils.yaml import load_yaml_file\n\n"
        "    return load_yaml_file(kb.path / 'board.yaml', kb_root=kb.path)\n",
    ),
    "the file name spelt in the CLI": (
        CLI_MODULE,
        "\n\ndef _board_path(kb):\n    return kb.path / 'board.yaml'\n",
    ),
    "the constant used outside board.py": (
        CLI_MODULE,
        "\n\ndef _board_path(kb):\n    from .board import BOARD_FILE\n\n"
        "    return kb.path / BOARD_FILE\n",
    ),
    "a second caller of the loader in the plugin": (
        PLUGIN_MODULE,
        "\n\ndef _other(kb):\n    from .board import load_board_config\n\n"
        "    return load_board_config(kb)\n",
    ),
    "the CLI calling the loader directly": (
        CLI_MODULE,
        "\n\ndef _direct(kb):\n    from . import board\n\n    return board.load_board_config(kb)\n",
    ),
    "a second YAML read in board.py": (
        BOARD_MODULE,
        "\n\ndef _raw(kb):\n    from pyrite.utils.yaml import load_yaml_file\n\n"
        "    return load_yaml_file(kb.path / BOARD_FILE)\n",
    ),
    "a reader in another extension": (
        "extensions/zettelkasten/src/pyrite_zettelkasten/plugin.py",
        "\n\ndef _board(path):\n    import yaml\n\n"
        "    return yaml.safe_load(open(path / 'board.yaml'))\n",
    ),
}


@pytest.mark.parametrize("plant", sorted(PLANTED))
def test_the_checker_finds_a_planted_violation(plant):
    sources = _sources()
    module, extra = PLANTED[plant]
    assert module in sources, module
    planted = {**sources, module: sources[module] + extra}
    assert len(violations(planted)) > len(violations(sources)), plant

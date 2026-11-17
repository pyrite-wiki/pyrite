"""A software KB's ``board.yaml`` is read by one function, on one path (#1538).

``kb/design.md`` principle 8, one enforcement point: the board config is read
by ``board.load_board_config``, which validates it and refuses what it cannot
load, and the plugin reaches that function through one method,
``SoftwareKBPlugin._board_config``, which resolves the KB first. A second
reader of the file, or a second caller of the loader, would be a place where
a broken file is read some other way -- the eight loaders and five
``except Exception`` blocks this replaced.

The property: no code obtains a board except through the one function, and
no caller can turn a failed load into a board, defaults or otherwise. The
oracle is the extensions' source as it is on disk, walked with ``ast``. The
checker knows these rules and nothing about how the loader works:

1. A string naming ``board.yaml`` (other than a docstring) is written once:
   ``board.py``'s module constant ``BOARD_FILE``; nor is the name built by
   concatenating or formatting constants (``"board" + ".yaml"``).
2. ``BOARD_FILE`` is used only inside ``board.py``.
3. ``load_yaml_file`` is called, in any extension, only by
   ``board.load_board_config``.
4. ``load_board_config`` is imported or called, anywhere in ``pyrite/`` or the
   extensions, only by ``SoftwareKBPlugin._board_config``; and neither name is
   written as a string (``getattr(board, "load_board_config")``, ``vars()``).
5. The built-in board (``default_board_config``, ``DEFAULT_BOARD_CONFIG``) is
   reached only by ``board.py`` and ``_board_config``, and never from an
   ``except`` handler.
6. A ``try`` around a call of ``_board_config`` or ``load_board_config`` is
   only orient's, catching ``BoardConfigError`` alone, and its handler builds
   no mapping and obtains no board. In the loader, its path and the one
   converter (``_board_refusal``) every handler raises.
7. ``unnamed_gives_defaults=True`` is passed only by the three cross-KB views
   (``sw_board``, ``sw_review_queue``, ``sw_pull_next``) and the CLI's board.
8. ``_board_config`` is given the caller's read scope: ``readable_kbs`` is a
   name ``readable_kbs``, a set display (orient's own KB), or ``UNSCOPED`` in
   the CLI; never a constant such as ``None``.

Planted violations go through the same checker and each must be found by the
rule it names, so a checker that finds nothing cannot pass.

Limits, which are the behavioural tests' job (``tests/test_board_config_refuses.py``):

- A name built from a variable (``f"board.{ext}"``) or a path read by a
  helper outside the extension: ``test_a_kb_that_does_not_resolve_is_refused``
  and the views-with-no-KB tests plant a board.yaml with a marker in the
  working directory and assert it reaches no answer.
- A board obtained or swallowed one call away (a method that calls a handler
  inside ``except Exception``, as orient's recommendation does): orient skips
  that call when the board did not load, and
  ``test_orient_answers_and_says_what_is_wrong_with_the_board`` pins it.
- A handler that never calls ``_board_config`` and builds its own lanes: the
  receivers are derived from the source (``BOARD_TOOLS``), and
  ``test_the_walk_finds_the_known_loaders`` names the handlers that must be
  among them.
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
THE_CONVERTER = (BOARD_MODULE, "_board_refusal")
ORIENT = (PLUGIN_MODULE, "SoftwareKBPlugin.get_orient_supplement")
LOADING_NAMES = {"_board_config", "load_board_config"}
DEFAULT_NAMES = {"default_board_config", "DEFAULT_BOARD_CONFIG"}
CROSS_KB_VIEWS = {
    (PLUGIN_MODULE, "SoftwareKBPlugin._mcp_board"),
    (PLUGIN_MODULE, "SoftwareKBPlugin._mcp_review_queue"),
    (PLUGIN_MODULE, "SoftwareKBPlugin._mcp_pull_next"),
    (CLI_MODULE, "sw_board_cmd"),
}


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
        if isinstance(node.value, str) and id(node) not in self.docstrings:
            if "board.yaml" in node.value:
                self.hits.append(("file-name", self._where()))
            if node.value in LOADING_NAMES:
                self.hits.append(("loader-by-name", self._where()))

    def _static(self, node: ast.AST) -> str | None:
        """The text a concatenation or f-string of constants spells."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.FormattedValue):
            return self._static(node.value) or "\0"
        if isinstance(node, ast.JoinedStr):
            return "".join(self._static(v) or "\0" for v in node.values)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self._static(node.left), self._static(node.right)
            if left is None and right is None:
                return None
            return (left or "\0") + (right or "\0")
        return None

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:  # noqa: N802
        text = self._static(node) or ""
        if "board" in text and "yaml" in text:
            self.hits.append(("file-name", self._where()))
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:  # noqa: N802
        text = self._static(node) or ""
        if "board" in text and "yaml" in text:
            self.hits.append(("file-name", self._where()))
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
        if any(_calls(stmt, LOADING_NAMES) for stmt in node.body):
            for handler in node.handlers:
                caught = handler.type
                only_board_error = isinstance(caught, ast.Name) and caught.id == "BoardConfigError"
                builds = any(
                    isinstance(n, ast.Dict) for stmt in handler.body for n in ast.walk(stmt)
                ) or any(_calls(stmt, LOADING_NAMES | DEFAULT_NAMES) for stmt in handler.body)
                self.hits.append(
                    (
                        "catches-a-load" if only_board_error and not builds else "swallows-a-load",
                        self._where(),
                    )
                )
        for handler in node.handlers:
            raises = any(isinstance(n, ast.Raise) for stmt in handler.body for n in ast.walk(stmt))
            returns = any(
                isinstance(n, ast.Return) for stmt in handler.body for n in ast.walk(stmt)
            )
            if returns or not raises:
                self.hits.append(("handler-that-does-not-raise", self._where()))
            if any(_names(stmt, DEFAULT_NAMES) for stmt in handler.body):
                self.hits.append(("defaults-in-a-handler", self._where()))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id == "BOARD_FILE" and isinstance(node.ctx, ast.Load):
            self.hits.append(("file-name-constant", self._where()))
        elif node.id == "load_board_config" and isinstance(node.ctx, ast.Load):
            self.hits.append(("loader", self._where()))
        elif node.id in DEFAULT_NAMES and isinstance(node.ctx, ast.Load):
            self.hits.append(("defaults", self._where()))

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr == "load_board_config":
            self.hits.append(("loader", self._where()))
        elif node.attr == "BOARD_FILE":
            self.hits.append(("file-name-constant", self._where()))
        elif node.attr in DEFAULT_NAMES:
            self.hits.append(("defaults", self._where()))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        for alias in node.names:
            if alias.name == "load_board_config":
                self.hits.append(("loader", self._where()))
            elif alias.name == "BOARD_FILE":
                self.hits.append(("file-name-constant", self._where()))
            elif alias.name in DEFAULT_NAMES:
                self.hits.append(("defaults", self._where()))

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "load_yaml_file":
            self.hits.append(("yaml-read", self._where()))
        if name == "_board_config":
            for kw in node.keywords:
                if kw.arg == "unnamed_gives_defaults" and not (
                    isinstance(kw.value, ast.Constant) and kw.value.value is False
                ):
                    self.hits.append(("defaults-for-no-kb", self._where()))
                if kw.arg == "readable_kbs":
                    v = kw.value
                    if isinstance(v, ast.Name) and v.id == "readable_kbs":
                        continue
                    if isinstance(v, ast.Set):
                        continue
                    if isinstance(v, ast.Name) and v.id == "UNSCOPED":
                        self.hits.append(("unscoped", self._where()))
                        continue
                    self.hits.append(("scope-not-the-callers", self._where()))
            if not any(kw.arg == "readable_kbs" for kw in node.keywords):
                self.hits.append(("scope-not-the-callers", self._where()))
        self.generic_visit(node)


def _calls(node: ast.AST, names: set[str]) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if (f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)) in names:
                return True
    return False


def _names(node: ast.AST, names: set[str]) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id in names)
        or (isinstance(n, ast.Attribute) and n.attr in names)
        for n in ast.walk(node)
    )


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
            elif rule == "loader-by-name":
                found.append(f"{module}: {where} names the loader as a string")
            elif rule == "defaults" and module != BOARD_MODULE and site != THE_PATH_TO_THE_READER:
                found.append(f"{module}: {where} reaches the built-in board")
            elif rule == "defaults-in-a-handler":
                found.append(f"{module}: {where} returns the built-in board from a handler")
            elif rule == "swallows-a-load":
                found.append(f"{module}: {where} turns a failed load into an answer")
            elif rule == "catches-a-load" and site != ORIENT:
                found.append(f"{module}: {where} catches a failed load")
            elif rule == "handler-that-does-not-raise" and site in (
                THE_READER,
                THE_PATH_TO_THE_READER,
                THE_CONVERTER,
            ):
                found.append(f"{module}: {where} has a handler that does not raise")
            elif rule == "defaults-for-no-kb" and site not in CROSS_KB_VIEWS:
                found.append(f"{module}: {where} asks for the built-in board with no KB")
            elif rule == "unscoped" and module != CLI_MODULE:
                found.append(f"{module}: {where} gives _board_config no caller's scope")
            elif rule == "scope-not-the-callers":
                found.append(f"{module}: {where} gives _board_config a scope not the caller's")
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


#: Each a change to one module that the checker must catch, and the words of
#: the violation it must produce. ``extra`` is appended to the module; a
#: ``(old, new)`` pair replaces text in it.
_HANDLER_DEFAULTS = (
    "\n\ndef _h(self, db, kb_name):\n    try:\n"
    "        return self._board_config(db, kb_name, readable_kbs=readable_kbs)\n"
    "    except Exception:\n        from .board import default_board_config\n\n"
    "        return default_board_config()\n"
)
PLANTED: dict[str, tuple[str, str | tuple[str, str], str]] = {
    "a second reader of the file in the plugin": (
        PLUGIN_MODULE,
        "\n\ndef _peek(kb):\n    from pyrite.utils.yaml import load_yaml_file\n\n"
        "    return load_yaml_file(kb.path / 'board.yaml', kb_root=kb.path)\n",
        "reads YAML",
    ),
    "the file name spelt in the CLI": (
        CLI_MODULE,
        "\n\ndef _board_path(kb):\n    return kb.path / 'board.yaml'\n",
        "names board.yaml",
    ),
    "the file name built by concatenation": (
        CLI_MODULE,
        "\n\ndef _board_path(kb):\n    return kb.path / ('board' + '.yaml')\n",
        "names board.yaml",
    ),
    "the file name built by an f-string": (
        PLUGIN_MODULE,
        "\n\ndef _board_path(kb):\n    return kb.path / f\"{'board'}.yaml\"\n",
        "names board.yaml",
    ),
    "the constant used outside board.py": (
        CLI_MODULE,
        "\n\ndef _board_path(kb):\n    from .board import BOARD_FILE\n\n"
        "    return kb.path / BOARD_FILE\n",
        "uses BOARD_FILE",
    ),
    "a second caller of the loader in the plugin": (
        PLUGIN_MODULE,
        "\n\ndef _other(kb):\n    from .board import load_board_config\n\n"
        "    return load_board_config(kb)\n",
        "reaches load_board_config",
    ),
    "the CLI calling the loader directly": (
        CLI_MODULE,
        "\n\ndef _direct(kb):\n    from . import board\n\n    return board.load_board_config(kb)\n",
        "reaches load_board_config",
    ),
    "the loader reached through getattr": (
        CLI_MODULE,
        "\n\ndef _direct(kb):\n    from . import board\n\n"
        "    return getattr(board, 'load_board_config')(kb)\n",
        "names the loader as a string",
    ),
    "the loader reached through vars()": (
        PLUGIN_MODULE,
        "\n\ndef _direct(kb):\n    from . import board\n\n"
        "    return vars(board)['load_board_config'](kb)\n",
        "names the loader as a string",
    ),
    "a second YAML read in board.py": (
        BOARD_MODULE,
        "\n\ndef _raw(kb):\n    from pyrite.utils.yaml import load_yaml_file\n\n"
        "    return load_yaml_file(kb.path / BOARD_FILE)\n",
        "reads YAML",
    ),
    "a reader in another extension": (
        "extensions/zettelkasten/src/pyrite_zettelkasten/plugin.py",
        "\n\ndef _board(path):\n    import yaml\n\n"
        "    return yaml.safe_load(open(path / 'board.yaml'))\n",
        "names board.yaml",
    ),
    "a caller that turns a failed load into the built-in board": (
        PLUGIN_MODULE,
        _HANDLER_DEFAULTS,
        "turns a failed load into an answer",
    ),
    "a caller that turns a failed load into a hand-built board": (
        PLUGIN_MODULE,
        "\n\ndef _h(self, db, kb_name):\n    try:\n"
        "        return self._board_config(db, kb_name, readable_kbs=readable_kbs)\n"
        "    except BoardConfigError:\n        return {'lanes': []}\n",
        "turns a failed load into an answer",
    ),
    "a caller that catches the refusal and passes": (
        CLI_MODULE,
        "\n\ndef _h(plugin, db):\n    try:\n"
        "        plugin._board_config(db, None, readable_kbs=readable_kbs)\n"
        "    except BoardConfigError:\n        pass\n",
        "catches a failed load",
    ),
    "the loader turning a failed read into the built-in board": (
        BOARD_MODULE,
        (
            "        data = load_yaml_file(board_file, kb_root=kb_path)\n",
            "        try:\n            data = load_yaml_file(board_file, kb_root=kb_path)\n"
            "        except Exception:\n            return default_board_config()\n",
        ),
        "has a handler that does not raise",
    ),
    "the converter letting a failure through as an answer": (
        BOARD_MODULE,
        (
            '        raise BoardConfigError("is nested too deeply to load") from None\n',
            "        return\n",
        ),
        "has a handler that does not raise",
    ),
    "the built-in board reached outside the loader": (
        CLI_MODULE,
        "\n\ndef _b():\n    from .board import default_board_config\n\n"
        "    return default_board_config()\n",
        "reaches the built-in board",
    ),
    "a gate tool asking for the built-in board with no KB": (
        PLUGIN_MODULE,
        (
            "board_config = self._board_config(db, kb_name, readable_kbs=readable_kbs)\n"
            "            gate_result = self._evaluate_gate(\n"
            '                db, board_config, "in_progress", row, meta, readable_kbs=readable_kbs\n'
            '            )\n            if gate_result and not gate_result["passed"]',
            "board_config = self._board_config(\n"
            "                db, kb_name, readable_kbs=readable_kbs, unnamed_gives_defaults=True\n"
            "            )\n            gate_result = self._evaluate_gate(\n"
            '                db, board_config, "in_progress", row, meta, readable_kbs=readable_kbs\n'
            '            )\n            if gate_result and not gate_result["passed"]',
        ),
        "asks for the built-in board with no KB",
    ),
    "a handler passing no read scope": (
        PLUGIN_MODULE,
        (
            "board_config = self._board_config(db, kb_name, readable_kbs=readable_kbs)\n"
            "            priority_rank",
            "board_config = self._board_config(db, kb_name, readable_kbs=None)\n"
            "            priority_rank",
        ),
        "a scope not the caller's",
    ),
}


def _plant(sources: dict[str, str], plant: str) -> dict[str, str]:
    module, change, _ = PLANTED[plant]
    text = sources[module]
    if isinstance(change, tuple):
        old, new = change
        assert text.count(old) == 1, f"{plant}: the text to replace is not there once"
        text = text.replace(old, new)
    else:
        text += change
    return {**sources, module: text}


@pytest.mark.control(reason="the checker's own test: a planted violation is found on any tree")
@pytest.mark.parametrize("plant", sorted(PLANTED))
def test_the_checker_finds_a_planted_violation(plant):
    sources = _sources()
    before = violations(sources)
    after = violations(_plant(sources, plant))
    new = [v for v in after if v not in before]
    words = PLANTED[plant][2]
    assert any(words in v for v in new), f"{plant}: expected '{words}' among {new}"

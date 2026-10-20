"""A plugin's MCP handlers resolve KBs through the server's config (#952).

`PluginContext` hands every plugin the server's `config` and `db`
(`PluginRegistry.set_context`). A handler that called `load_config()` instead
built its services on the process's config file -- a different config from
the server's in every test world and in any deployment that passes a config
by path -- and resolved the KB it was given from the registry row, which
carries no `read_only` and no `shortname` (review of #965, blocking 1).

Pinned at the source: the only `load_config()` call a plugin module may make
is inside its context fallback (`_get_db`, `_get_config`), for the CLI, which
has no context.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN_MODULES = sorted(REPO.glob("extensions/*/src/*/plugin.py"))
FALLBACKS = {"_get_db", "_get_config"}


def _load_config_calls_outside_fallbacks(module: Path) -> list[str]:
    tree = ast.parse(module.read_text(), filename=str(module))
    found: list[str] = []

    def visit(node: ast.AST, owner: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Call):
                fn = child.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                if name in ("load_config", "_load_config") and owner not in FALLBACKS:
                    found.append(f"{module.relative_to(REPO)}:{child.lineno} in {owner}")
            visit(child, owner)

    visit(tree, None)
    return found


@pytest.mark.parametrize("module", PLUGIN_MODULES, ids=[m.parent.name for m in PLUGIN_MODULES])
def test_only_the_context_fallback_loads_a_config(module):
    assert _load_config_calls_outside_fallbacks(module) == []

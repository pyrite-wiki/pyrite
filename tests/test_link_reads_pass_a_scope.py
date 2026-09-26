"""Forgetting the read scope fails closed: it is never "see everything".

A structural guard for P-R4/P-R5 (private #39, #40, #55-#58 and batch 3b's
cold read). The read methods that span KBs take ``readable_kbs`` -- the
caller's ``ReadScope.as_set()``, or ``UNSCOPED`` spelled out by a caller
with no scope to apply. Two rules, neither of them a hand-kept list of
methods:

1. **Definitions.** Every function under ``pyrite/`` and the extensions'
   ``src/`` that has a ``readable_kbs`` parameter gives it no default, so a
   call that leaves it out raises ``TypeError`` instead of reading every KB.
   The exceptions are the MCP tool handlers, found from the live registry
   (the chokepoint passes the set only to a handler that declares it, and
   the default there is its opt-in, ADR-0037 §4), and the transport entry
   points listed in ``TRANSPORT_ENTRY_POINTS``, each with its reason.
2. **Calls.** A call to a method whose every definition requires the
   argument must pass it -- by keyword, or positionally where the
   definition takes it positionally. A name that is also defined somewhere
   *without* the parameter (``get_entry``: ``KBService`` requires it, the
   storage row read does not) cannot be told apart by name; there rule 1's
   ``TypeError`` is what fails, at the first test that reaches the call.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._surface_inventory import mcp_server, mcp_tools

REPO = Path(__file__).resolve().parents[1]
SCANNED = sorted((REPO / "pyrite").rglob("*.py")) + sorted(REPO.glob("extensions/*/src/**/*.py"))

#: Functions whose ``readable_kbs`` keeps a default, besides the MCP tool
#: handlers. (file, qualname) -> why.
TRANSPORT_ENTRY_POINTS: dict[tuple[str, str], str] = {
    ("pyrite/server/mcp_server.py", "PyriteMCPServer._dispatch_tool"): (
        "the tool chokepoint; every transport passes the per-connection set, and "
        "None there is the unscoped principal the transport resolved (ADR-0037 §4)"
    ),
    ("pyrite/server/mcp_server.py", "PyriteMCPServer._read_resource"): (
        "the resource chokepoint, called by the same per-connection closure"
    ),
    ("pyrite/server/mcp_server.py", "PyriteMCPServer._get_prompt"): (
        "the prompt chokepoint, called by the same per-connection closure"
    ),
    ("pyrite/server/mcp_server.py", "PyriteMCPServer.build_sdk_server"): (
        "builds the per-connection closure from the transport's resolved scope"
    ),
}


@dataclass(frozen=True)
class _Def:
    file: str
    qualname: str
    name: str
    has_default: bool
    positional_index: int | None  # None when keyword-only
    is_method: bool


def _defs() -> list[_Def]:
    found: list[_Def] = []

    def visit(node, prefix, rel):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.ClassDef):
                visit(ch, f"{prefix}{ch.name}.", rel)
            elif isinstance(ch, ast.FunctionDef | ast.AsyncFunctionDef):
                q = f"{prefix}{ch.name}"
                a = ch.args
                kwonly = [x.arg for x in a.kwonlyargs]
                pos = [x.arg for x in a.posonlyargs + a.args]
                is_method = isinstance(node, ast.ClassDef)
                if "readable_kbs" in kwonly:
                    default = a.kw_defaults[kwonly.index("readable_kbs")]
                    found.append(_Def(rel, q, ch.name, default is not None, None, is_method))
                elif "readable_kbs" in pos:
                    i = pos.index("readable_kbs")
                    n_defaults = len(a.defaults)
                    has_default = i >= len(pos) - n_defaults
                    # `self`/`cls` is not passed at a method call site.
                    shift = 1 if pos and pos[0] in ("self", "cls") else 0
                    found.append(_Def(rel, q, ch.name, has_default, i - shift, is_method))
                visit(ch, f"{q}.<locals>.", rel)

    for path in SCANNED:
        rel = str(path.relative_to(REPO))
        visit(ast.parse(path.read_text()), "", rel)
    return found


def _all_def_names() -> dict[tuple[str, bool], int]:
    """How many definitions each (name, is-a-method) has across the scanned
    code. `x.name(...)` can only reach a method; `name(...)` only a function,
    so a REST handler called `get_graph` does not make `svc.get_graph`
    ambiguous."""
    counts: dict[tuple[str, bool], int] = {}

    def visit(node):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.FunctionDef | ast.AsyncFunctionDef):
                key = (ch.name, isinstance(node, ast.ClassDef))
                counts[key] = counts.get(key, 0) + 1
            visit(ch)

    for path in SCANNED:
        visit(ast.parse(path.read_text()))
    return counts


def _scoped_names() -> tuple[dict[tuple[str, bool], list[_Def]], set[tuple[str, bool]]]:
    """(unambiguous scoped names -> their definitions, ambiguous ones)."""
    by_key: dict[tuple[str, bool], list[_Def]] = {}
    for d in _defs():
        if not d.has_default:
            by_key.setdefault((d.name, d.is_method), []).append(d)
    totals = _all_def_names()
    scoped = {k: ds for k, ds in by_key.items() if totals.get(k, 0) == len(ds)}
    return scoped, set(by_key) - set(scoped)


@pytest.fixture(scope="module")
def handler_qualnames() -> set[tuple[str, str]]:
    with mcp_server() as srv:
        out = set()
        for ep in mcp_tools(srv):
            fn = inspect.unwrap(ep.handler)
            src = Path(inspect.getsourcefile(fn)).resolve()
            try:
                rel = str(src.relative_to(REPO))
            except ValueError:  # an installed, non-editable plugin
                continue
            out.add((rel, fn.__qualname__))
        return out


def test_no_scoped_read_defaults_to_unscoped(handler_qualnames):
    offenders = [
        f"{d.file} {d.qualname}(): readable_kbs has a default"
        for d in _defs()
        if d.has_default
        and (d.file, d.qualname) not in handler_qualnames
        and (d.file, d.qualname) not in TRANSPORT_ENTRY_POINTS
    ]
    assert not offenders, "\n".join(offenders)


@pytest.mark.control(reason="list bookkeeping: each transport entry point still has a default")
def test_transport_entry_points_are_not_stale():
    live = {(d.file, d.qualname) for d in _defs() if d.has_default}
    assert set(TRANSPORT_ENTRY_POINTS) <= live, sorted(set(TRANSPORT_ENTRY_POINTS) - live)


def test_every_call_to_a_scoped_read_passes_the_scope():
    scoped, _ambiguous = _scoped_names()

    offenders = []
    for path in SCANNED:
        rel = str(path.relative_to(REPO))
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                key = (func.attr, True)
            elif isinstance(func, ast.Name):
                key = (func.id, False)
            else:
                continue
            if key not in scoped:
                continue
            name = key[0]
            if any(k.arg == "readable_kbs" or k.arg is None for k in node.keywords):
                continue  # passed by keyword, or forwarded in **kwargs
            indexes = {d.positional_index for d in scoped[key]}
            if None not in indexes and all(len(node.args) > i for i in indexes):
                continue
            offenders.append(f"{rel}:{node.lineno} calls {name}() without readable_kbs")
    assert not offenders, "\n".join(offenders)


#: Method names a scoped read shares with an unscoped method elsewhere, so a
#: call cannot be classified by name. Rule 1's TypeError covers them; the
#: list is pinned so a new collision is a reviewed change.
AMBIGUOUS_SCOPED_METHODS = {
    "get_entry": "KBService.get_entry vs the storage row reads (PyriteDB, backends)",
    "get_status": "QAService.get_status vs other services' get_status",
    "validate_entry": "QAService.validate_entry vs KBSchema.validate_entry",
    "validate_all": "QAService.validate_all vs KBRepository.validate_all",
}


def test_ambiguous_scoped_names_are_pinned():
    _scoped, ambiguous = _scoped_names()
    assert {name for name, is_method in ambiguous if is_method} == set(AMBIGUOUS_SCOPED_METHODS)
    assert not {name for name, is_method in ambiguous if not is_method}

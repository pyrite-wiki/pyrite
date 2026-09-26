"""Every server read that can reach across KBs passes the caller's scope.

A structural guard for P-R4/P-R5 (private #39, #40, #55-#58). The link
queries, `KBService.get_entry` and the QA checks take `readable_kbs`, and
`None` means unscoped -- so a call that forgets it is not an error, it is
the leak this batch closed, silently back. This walks `pyrite/server` and
the extension plugins and fails for any such call that does not pass
`readable_kbs=`, unless it is listed below with the reason it cannot leak.

The behavioural tests (`tests/test_link_read_scope_*.py`) show each path
answers correctly; this one keeps a *new* call site from skipping the rule.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

SCANNED = [
    *sorted((REPO / "pyrite" / "server").rglob("*.py")),
    *sorted(REPO.glob("extensions/*/src/*/plugin.py")),
    *sorted(REPO.glob("extensions/*/src/*/queries.py")),
]

#: Methods whose answer spans KBs through links, or whose KB-less lookup
#: walks every KB. `get_entry` is included only on a service receiver: the
#: storage `db.get_entry(id, kb)` reads one named row and no links.
SCOPED_METHODS = {
    "get_backlinks",
    "get_outlinks",
    "get_graph",
    "get_graph_data",
    "get_orphans",
    "validate_entry",
    "validate_kb",
    "validate_all",
    "assess_entry",
    "assess_kb",
}
SERVICE_GET_ENTRY_RECEIVERS = {"svc", "self.svc", "kb_svc", "self.kb_svc"}

#: (file relative to the repo, enclosing function, method) -> why it cannot leak.
EXEMPT: dict[tuple[str, str, str], str] = {
    ("pyrite/server/endpoints/ai_ep.py", "_get_entry", "get_entry"): (
        "named KB checked by the route; only body, title and tags are used"
    ),
    ("pyrite/server/endpoints/ai_ep.py", "ai_chat", "get_entry"): (
        "named KBs: the route-checked one, and rows the scoped search returned "
        "(each re-checked with scope.permits); only body and title are used"
    ),
    ("pyrite/server/endpoints/blocks.py", "get_entry_blocks", "get_entry"): (
        "named KB checked by the route; an existence check, nothing of the entry returned"
    ),
    ("pyrite/server/endpoints/collections.py", "create_collection", "get_entry"): (
        "named KB, write route; an existence check for the slug"
    ),
    ("pyrite/server/endpoints/collections.py", "get_collection", "get_entry"): (
        "named KB checked by the route; only the collection's own metadata is returned"
    ),
}


def _receiver(node: ast.Attribute) -> str:
    try:
        return ast.unparse(node.value)
    except Exception:  # pragma: no cover - defensive
        return ""


def _unscoped_calls():
    found = []
    for path in SCANNED:
        rel = str(path.relative_to(REPO))
        tree = ast.parse(path.read_text())
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(func):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                name = node.func.attr
                if name == "get_entry":
                    if _receiver(node.func) not in SERVICE_GET_ENTRY_RECEIVERS:
                        continue
                elif name not in SCOPED_METHODS:
                    continue
                if any(k.arg == "readable_kbs" for k in node.keywords):
                    continue
                found.append((rel, func.name, name, node.lineno))
    return found


def test_every_cross_kb_read_passes_readable_kbs():
    offenders = [
        f"{rel}:{line} {func}() calls .{name}() without readable_kbs="
        for rel, func, name, line in _unscoped_calls()
        if (rel, func, name) not in EXEMPT
    ]
    assert not offenders, "\n".join(offenders)


@pytest.mark.control(reason="list bookkeeping: every exemption still names a real call site")
def test_exemptions_are_not_stale():
    live = {(rel, func, name) for rel, func, name, _ in _unscoped_calls()}
    stale = sorted(set(EXEMPT) - live)
    assert not stale, stale

"""Derived task completion: the Parentable protocol's derived value.

ADR-0045 decision 7, ADR-0042 decision 4, kb/design.md principle 4. A pure
function over index rows, with no I/O, so every layer asks the same question
the same way: the task service (lists, reads, claims) and storage (every
query that filters or reports by status, through
``pyrite/storage/effective_status.py``).
"""

from __future__ import annotations

from typing import Any

from .task import TASK_RESOLVED_STATUSES


def no_derived() -> dict[str, Any]:
    """The derived value of a task the index does not hold."""
    return {
        "completion": {"complete": False, "basis": "status", "children": 0, "children_complete": 0},
        "effective_status": "open",
        "parent_missing": False,
    }


def derive_completion(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Compute every task's completion and effective status from one KB's rows.

    The ONE place completion is decided. Every question about a task's
    completion -- a status filter, a claim, a read -- gets its answer here:
    ``TaskService`` (``list_tasks`` and every status filter, so the drain
    check and the web worklist; ``task get``; the MCP task tools; ``task
    decompose``; ``KBService.claim_entry``'s refusal) and storage's generic
    status filters (``kb_find_by_status``, ``kb_find_overdue``,
    ``kb_find_by_assignee``, search and list ``status=``) through
    ``pyrite/storage/effective_status.py``. Nothing here writes a file.

    ``rows`` are the KB's entries: ``id``, ``status``, ``parent`` and
    optionally ``entry_type`` (absent means ``task``). Only tasks are
    children; any entry may be a parent (a ``parent`` naming a note is not
    dangling). Returns, for each task id::

        {"completion": {"complete": bool, "basis": "status" | "children",
                        "children": int, "children_complete": int},
         "effective_status": str,
         "parent_missing": bool}

    The rules (ADR-0045 decision 7; maintainer, 2026-10-03):

    - The file's own status decides when a person or an explicit operation
      set a terminal one: ``done``/``cancelled`` are complete even with open
      children (the counts show the disagreement), ``failed`` is not
      complete. ``basis`` is ``status``.
    - Otherwise a task with children is complete when every child is, so a
      grandparent is complete when its whole subtree is. ``basis`` is
      ``children``. A task with no children and a non-terminal status is not
      complete (``basis`` ``status``).
    - ``effective_status`` is what every status filter matches: the file's
      status, except that a task complete by its children counts as
      ``done``. An unset status is ``open``.
    - No KB shape makes this fail: a ``parent`` that names no entry is
      reported as ``parent_missing``; a ``parent`` cycle counts as not
      complete; any depth of chain is walked without recursion (a 1,500-deep
      chain once raised RecursionError and failed every ``task list``).
    """
    all_ids: set[str] = set()
    status: dict[str, str] = {}
    parent_of: dict[str, str] = {}
    children: dict[str, list[str]] = {}
    for row in rows:
        rid = row.get("id")
        if not rid:
            continue
        all_ids.add(rid)
        if (row.get("entry_type") or "task") != "task":
            continue
        status[rid] = str(row.get("status") or "open")
        parent = row.get("parent")
        parent = str(parent).strip() if isinstance(parent, str | int) else ""
        parent_of[rid] = parent
        if parent:
            children.setdefault(parent, []).append(rid)

    def _own_terminal(tid: str) -> bool | None:
        s = status[tid]
        if s in TASK_RESOLVED_STATUSES:
            return True
        if s == "failed":
            return False
        return None

    # Post-order walk with an explicit stack: a node is decided after all of
    # its children. A child met while still on the path (a cycle) is left
    # undecided, and an undecided child counts as not complete.
    complete: dict[str, bool] = {}
    on_path: set[str] = set()
    for root in status:
        if root in complete:
            continue
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            tid, expanded = stack.pop()
            if tid in complete:
                continue
            kids = children.get(tid, [])
            if expanded:
                own = _own_terminal(tid)
                if own is not None:
                    complete[tid] = own
                else:
                    complete[tid] = bool(kids) and all(complete.get(k, False) for k in kids)
                on_path.discard(tid)
                continue
            if tid in on_path:
                continue
            on_path.add(tid)
            stack.append((tid, True))
            stack.extend((k, False) for k in kids if k not in complete and k not in on_path)

    out: dict[str, dict[str, Any]] = {}
    for tid, own_status in status.items():
        kids = children.get(tid, [])
        basis = "children" if kids and _own_terminal(tid) is None else "status"
        is_complete = complete[tid]
        parent = parent_of[tid]
        out[tid] = {
            "completion": {
                "complete": is_complete,
                "basis": basis,
                "children": len(kids),
                "children_complete": sum(1 for k in kids if complete[k]),
            },
            "effective_status": "done" if is_complete and basis == "children" else own_status,
            "parent_missing": bool(parent) and parent not in all_ids,
        }
    return out

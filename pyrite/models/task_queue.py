"""The task queue's derived view: which tasks a worker can take now (#1686).

A pure function over index rows, beside ``task_completion.py`` and built on
it: kb/design.md principle 4 (dispatchability is derived, never stored) and
principle 8 (one classifier; the park and stale-claim views of #1680 extend
it rather than copy it). ``TaskService.list_tasks(dispatchable=True)`` is
the only caller, and the CLI ``task list``, the MCP ``task_list`` tool and
REST ``GET /tasks`` all ask it, so the three return the same tasks in the
same order with the same reason for each one left out.

Nothing here reads a file or the database. The service supplies whether a
row's file is still there (``file_present``).
"""

from __future__ import annotations

from typing import Any

from .task import TASK_RESOLVED_STATUSES

#: Why an open task is not dispatchable, in the order they are checked: a
#: task gets the first that applies, so each has exactly one.
#:
#: - ``not_open``: its effective status (ADR-0045 decision 7) is not ``open``,
#:   so a parent done by its children is ``not_open`` even if its file says
#:   ``open``.
#: - ``orphaned``: the index holds the row but the file is gone; a claim would
#:   fail at the file load.
#: - ``has_children``: its work is in its children (``parent`` or the legacy
#:   ``parent_task``); the children are listed instead.
#: - ``assigned``: it names an assignee, of any kind (#1686, question 1).
#: - ``parked``: ``parked_awaiting`` says why it waits (:func:`is_parked`).
#: - ``blocked``: a dependency is unresolved, or names no task this listing
#:   can read.
DISPATCH_REASONS: tuple[str, ...] = (
    "not_open",
    "orphaned",
    "has_children",
    "assigned",
    "parked",
    "blocked",
)

#: Characters that, alone, do not say why a task waits: a value of only
#: whitespace and quote marks is the empty value spelled oddly (`''` left by a
#: shell, or by a hand edit).
_EMPTY_SPELLING = " \t\r\n'\""


def is_parked(value: Any) -> bool:
    """Whether a ``parked_awaiting`` value parks its task.

    Only the field's value counts, never the body. ``None``, ``False``, an
    empty or whitespace string and a string of only quote marks (``''``) say
    nothing, so they do not park. A list parks when any item would. Any other
    value (a reason, a number, ``True``) parks.
    """
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip(_EMPTY_SPELLING))
    if isinstance(value, list | tuple | set):
        return any(is_parked(item) for item in value)
    if isinstance(value, dict):
        return bool(value)
    return True


def dependency_ids(value: Any) -> list[str]:
    """A ``dependencies`` value as a list of ids: a bare string is one id."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list | tuple):
        return [str(v).strip() for v in value if isinstance(v, str | int) and str(v).strip()]
    return []


def _dependency_resolved(
    dep: str, own_kb: str, derived: dict[str, dict[str, dict[str, Any]]]
) -> bool:
    """A dependency is resolved when it names a task this listing reads and
    that task's effective status resolves it. An id in the task's own KB
    first; then ``kb:id`` in a KB the listing reads. Anything else -- no such
    task, a KB the listing does not read -- is not resolved.

    This differs from ``TaskService.get_blocked_by``, which skips a
    dependency it cannot find (so ``task_blocked_by`` reports it as blocking
    nothing). That method's output is an existing contract, left as it is;
    #1714 asks for one rule."""
    target = derived.get(own_kb, {}).get(dep)
    if target is None and ":" in dep:
        kb, _, tid = dep.partition(":")
        target = derived.get(kb, {}).get(tid)
    return target is not None and target["effective_status"] in TASK_RESOLVED_STATUSES


def classify_task(row: dict[str, Any], derived: dict[str, dict[str, dict[str, Any]]]) -> str | None:
    """``None`` when ``row`` is dispatchable, else its one reason.

    ``row`` is a ``list_tasks`` row (``id``, ``kb_name``, ``assignee``,
    ``parked_awaiting``, ``derived``) plus ``dependencies`` and
    ``file_present``. ``derived`` maps each KB this listing reads to its
    :func:`~pyrite.models.task_completion.derive_completion` result, which
    is how dependencies are resolved.
    """
    own = row["derived"]
    if own["effective_status"] != "open":
        return "not_open"
    if not row.get("file_present", True):
        return "orphaned"
    if own["completion"]["children"]:
        return "has_children"
    if str(row.get("assignee") or "").strip():
        return "assigned"
    if is_parked(row.get("parked_awaiting")):
        return "parked"
    kb = row["kb_name"]
    if not all(
        _dependency_resolved(d, kb, derived) for d in dependency_ids(row.get("dependencies"))
    ):
        return "blocked"
    return None


def dispatch_order(row: dict[str, Any]) -> tuple:
    """The queue's order: priority highest first, then oldest ``created_at``
    (first in, first out within a priority), then KB and id, so the order is
    the same on every call."""
    return (-int(row["priority"]), str(row.get("created_at") or ""), row["kb_name"], row["id"])


def classify_tasks(
    rows: list[dict[str, Any]], derived: dict[str, dict[str, dict[str, Any]]]
) -> list[tuple[dict[str, Any], str | None]]:
    """Every row with its reason (``None``: dispatchable), in :func:`dispatch_order`."""
    return [(row, classify_task(row, derived)) for row in sorted(rows, key=dispatch_order)]


def count_reasons(classified: list[tuple[dict[str, Any], str | None]]) -> dict[str, int]:
    """``{"dispatchable": n, <reason>: n, ...}``, every key present, summing to
    the rows classified."""
    counts = dict.fromkeys(("dispatchable", *DISPATCH_REASONS), 0)
    for _row, reason in classified:
        counts[reason or "dispatchable"] += 1
    return counts

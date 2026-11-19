"""The task queue's derived view: which tasks a worker can take now (#1686).

A pure function over index rows, beside ``task_completion.py`` and built on
it: kb/design.md principle 4 (dispatchability is derived, never stored) and
principle 8 (one classifier; the park and stale-claim views of #1680 extend
it rather than copy it). ``TaskService.task_listing(dispatchable=True)`` is
the only caller, and the CLI ``task list``, the MCP ``task_list`` tool and
REST ``GET /tasks`` all return what it builds, so the three return the same
tasks in the same order with the same reason for each one left out.

A task this view lists is one ``task claim`` accepts now: the claim's
compare-and-swap takes only ``open`` or an unset status, and the claim loads
the file through ``KBRepository.load`` before it. So a row the index reads as
open but the claim would refuse -- an empty-string status, a file that no
longer loads -- is left out with its own reason
(``tests/test_task_dispatchable.py`` claims every listed task).

Nothing here reads a file or the database. The service supplies whether a
row's file is still there (``file_present``) and, for a row that passes every
other check, whether the claim's loader loads it (``file_loads``).
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Mapping
from typing import Any

from .task import TASK_RESOLVED_STATUSES

#: Why an open task is not dispatchable, in the order they are checked: a
#: task gets the first that applies, so each has exactly one.
#:
#: - ``not_open``: its effective status (ADR-0045 decision 7) is not ``open``,
#:   so a parent done by its children is ``not_open`` even if its file says
#:   ``open``.
#: - ``empty_status``: the file says ``status: ""``. The index and
#:   ``derive_completion`` read it as open; the claim's compare-and-swap
#:   (``status = 'open' OR status IS NULL``) refuses it.
#: - ``orphaned``: the index holds the row but the file is gone; a claim would
#:   fail at the file load.
#: - ``has_children``: its work is in its children (``parent`` or the legacy
#:   ``parent_task``); the children are listed instead.
#: - ``assigned``: it names an assignee, of any kind (#1686, question 1).
#: - ``parked``: its park state (:func:`parked_state`) is ``parked`` or
#:   ``person``; a ``due`` task is listed, marked as a recheck.
#: - ``blocked``: a dependency is unresolved, or names no task this listing
#:   can read.
#: - ``unreadable``: everything else says dispatchable, but the claim's loader
#:   (``KBRepository.load``) does not load the file: its YAML broke after it
#:   was indexed, or its type refuses it. Checked last, because it is the one
#:   reason that reads a file, so only the rows that would be listed are read.
DISPATCH_REASONS: tuple[str, ...] = (
    "not_open",
    "empty_status",
    "orphaned",
    "has_children",
    "assigned",
    "parked",
    "blocked",
    "unreadable",
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


def park_reason(value: Any) -> Any:
    """A ``parked_awaiting`` value as a listing returns it: ``""`` when it
    does not park (:func:`is_parked`), a string stripped, anything else as
    written. Every read path of ``TaskService.list_tasks`` returns this, so
    whitespace means the same on all of them (#1688)."""
    if not is_parked(value):
        return ""
    return value.strip() if isinstance(value, str) else value


#: A task's park state (#1688), one of these. ``none``: not parked.
#: ``parked``: waiting, not yet due. ``due``: waiting on the world and its
#: ``retest_after`` has come, so it is an agent's recheck. ``person``: waiting
#: on a person; it never comes back by date.
PARK_STATES: tuple[str, ...] = ("none", "parked", "due", "person")

#: The ``park_kind`` values ``task park`` writes. Absent counts as
#: ``external``; any other value is read as ``person`` (never handed to an
#: agent) and reported by ``qa validate``.
PARK_KINDS: tuple[str, ...] = ("person", "external")


def park_kind(value: Any) -> str:
    """A ``park_kind`` value as :func:`parked_state` reads it: ``external``
    when absent or empty, ``person`` or ``external`` (any case, spaces
    around), and ``person`` for anything else, so an unknown kind is never
    handed to an agent."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "external"
    text = str(value).strip().lower()
    return text if text in PARK_KINDS else "person"


def retest_date(value: Any) -> _dt.date | None:
    """A ``retest_after`` value as a date, or None when it names none.

    A YAML date, a datetime (its date), an ISO date string, or an ISO
    datetime string (its date). Anything else -- empty, ``next week``,
    ``2026-13-01`` -- names no date."""
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return _dt.date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


#: The three keys of a park, in the order `task unpark` reports them.
PARK_KEYS: tuple[str, ...] = ("parked_awaiting", "park_kind", "retest_after")


def park_fields(frontmatter: Mapping[str, Any]) -> dict[str, Any]:
    """A file's park as the views read it: each key at the top level, else
    under ``metadata:`` (the second spelling every read lifts, #1001). Keys
    absent at both are left out. ``qa validate`` reads a file through this,
    so it reports what the views act on (#1688, round 1)."""
    bag = frontmatter.get("metadata")
    nested: Mapping[str, Any] = bag if isinstance(bag, Mapping) else {}
    out: dict[str, Any] = {}
    for key in PARK_KEYS:
        if key in frontmatter:
            out[key] = frontmatter[key]
        elif key in nested:
            out[key] = nested[key]
    return out


def _has_value(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def parked_state(task: Mapping[str, Any], today: _dt.date) -> str:
    """The one park predicate (#1688): one of :data:`PARK_STATES`.

    A pure function of ``task``'s ``parked_awaiting``, ``park_kind`` and
    ``retest_after`` and the ``today`` the caller gives; it never reads the
    clock, and never reads the body. Every view reads it: ``task list
    --dispatchable`` and ``--parked``, and the stale-claim view (#1689),
    for which a parked claim (any state but ``none``) holds no lease
    (ADR-0029 section 4).

    - Parked when ``parked_awaiting`` parks (:func:`is_parked`) or
      ``retest_after`` holds anything: a file with only a date is parked
      until that date (``qa validate`` reports the missing reason).
    - A ``person`` park (:func:`park_kind`) is ``person`` on every date.
    - Otherwise ``due`` when ``retest_after`` names a date on or before
      ``today``, else ``parked`` -- no date, or one that does not parse,
      stays parked until it is unparked.

    "Due" is derived and never written back (kb/design.md principle 4).
    """
    reason = task.get("parked_awaiting")
    retest = task.get("retest_after")
    if not is_parked(reason) and not _has_value(retest):
        return "none"
    if park_kind(task.get("park_kind")) == "person":
        return "person"
    day = retest_date(retest)
    if day is not None and day <= today:
        return "due"
    return "parked"


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


def classify_task(
    row: dict[str, Any], derived: dict[str, dict[str, dict[str, Any]]], *, today: _dt.date
) -> str | None:
    """``None`` when ``row`` is dispatchable, else its one reason.

    ``row`` is a ``list_tasks`` row (``id``, ``kb_name``, ``assignee``,
    ``parked_awaiting``, ``derived``) plus ``park_kind`` and
    ``retest_after`` (read by :func:`parked_state` at ``today``),
    ``index_status`` (the index's
    own status column, which the claim compares), ``dependencies``,
    ``file_present`` and ``file_loads`` (both default true). ``derived`` maps each KB this listing reads to its
    :func:`~pyrite.models.task_completion.derive_completion` result, which
    is how dependencies are resolved.
    """
    own = row["derived"]
    if own["effective_status"] != "open":
        return "not_open"
    if row.get("index_status") == "":
        return "empty_status"
    if not row.get("file_present", True):
        return "orphaned"
    if own["completion"]["children"]:
        return "has_children"
    if str(row.get("assignee") or "").strip():
        return "assigned"
    if parked_state(row, today) in ("parked", "person"):
        return "parked"
    kb = row["kb_name"]
    if not all(
        _dependency_resolved(d, kb, derived) for d in dependency_ids(row.get("dependencies"))
    ):
        return "blocked"
    if not row.get("file_loads", True):
        return "unreadable"
    return None


def dispatch_order(row: dict[str, Any]) -> tuple:
    """The queue's order: priority highest first, then oldest ``created_at``
    (first in, first out within a priority), then KB and id, so the order is
    the same on every call."""
    return (-int(row["priority"]), str(row.get("created_at") or ""), row["kb_name"], row["id"])


def classify_tasks(
    rows: list[dict[str, Any]],
    derived: dict[str, dict[str, dict[str, Any]]],
    *,
    today: _dt.date,
) -> list[tuple[dict[str, Any], str | None]]:
    """Every row with its reason (``None``: dispatchable), in :func:`dispatch_order`."""
    return [
        (row, classify_task(row, derived, today=today)) for row in sorted(rows, key=dispatch_order)
    ]


#: Effective statuses whose work is over: a park on such a task is never a
#: recheck. Every other status is live (open, claimed, in_progress, blocked,
#: review, and a KB's own states).
_NOT_LIVE = ("done", "cancelled", "failed")


def due_recheck_order(row: dict[str, Any]) -> tuple:
    """``due_rechecks``' order: priority highest first, then the recheck date
    (oldest first), then KB and id."""
    day = retest_date(row.get("retest_after"))
    return (-int(row["priority"]), day or _dt.date.max, row["kb_name"], row["id"])


def due_rechecks(
    classified: list[tuple[dict[str, Any], str | None]], *, today: _dt.date
) -> list[dict[str, Any]]:
    """Every live task whose park is ``due`` and that the dispatchable list
    does not already give (#1688): a park whose date has come returns as a
    recheck in ANY live status -- in progress, claimed, blocked, or open but
    assigned. In :func:`due_recheck_order`."""
    rows = [
        row
        for row, reason in classified
        if reason is not None
        and row["derived"]["effective_status"] not in _NOT_LIVE
        and parked_state(row, today) == "due"
    ]
    return sorted(rows, key=due_recheck_order)


def count_reasons(classified: list[tuple[dict[str, Any], str | None]]) -> dict[str, int]:
    """``{"dispatchable": n, <reason>: n, ...}``, every key present, summing to
    the rows classified."""
    counts = dict.fromkeys(("dispatchable", *DISPATCH_REASONS), 0)
    for _row, reason in classified:
        counts[reason or "dispatchable"] += 1
    return counts

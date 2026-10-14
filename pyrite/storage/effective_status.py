"""The effective status in SQL: every storage query that filters by status.

Maintainer decision, 2026-10-03 ("effective status everywhere"): a task whose
children are all resolved counts as ``done`` for every status filter, though
its file still says ``open`` or ``in_progress``. The rollup that used to
write ``done`` into such a file is deleted (ADR-0042 decision 4), so every
query that compares ``status`` -- ``find_by_status``, ``find_by_assignee``,
``find_overdue``, search, list and count -- compares this expression
instead of the column.

The decision itself is ``derive_completion`` (``pyrite/models/task_completion.py``),
the same function the task service calls. Here it only runs over the tasks of
the KBs in scope and yields the keys of the tasks whose effective status
differs from their file: those are ``done``. Every other row keeps its
column. Applying the keys in SQL keeps ``LIMIT``/``OFFSET`` paging exact.

Keys are bound one parameter each (``kb_name || sep || id``), never inlined.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..models.task_completion import derive_completion
from ..utils.metadata import parse_metadata
from .models import Entry

#: Separates kb_name and id in a key; neither can contain it.
KEY_SEP = "\x1f"


def derived_done_keys(
    session: Any,
    kb_name: str | None = None,
    kb_names: set[str] | list[str] | None = None,
) -> list[str]:
    """Keys (``kb_name + KEY_SEP + id``) of tasks that count as ``done`` by
    their children while their file says otherwise."""
    query = session.query(Entry.kb_name, Entry.id, Entry.status, Entry.extra_data).filter(
        Entry.entry_type == "task"
    )
    if kb_name:
        query = query.filter(Entry.kb_name == kb_name)
    if kb_names is not None:
        names = list(kb_names)
        if not names:
            return []
        query = query.filter(Entry.kb_name.in_(names))
    by_kb: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for kb, entry_id, status, extra in query.all():
        meta = parse_metadata(extra)
        by_kb[kb].append(
            {"id": entry_id, "status": status or meta.get("status"), "parent": meta.get("parent")}
        )
    keys = []
    for kb, rows in by_kb.items():
        for entry_id, derived in derive_completion(rows).items():
            if (
                derived["completion"]["basis"] == "children"
                and derived["effective_status"] == "done"
            ):
                keys.append(f"{kb}{KEY_SEP}{entry_id}")
    return keys


def status_sql(prefix: str, keys: list[str], params: dict[str, Any] | list[Any]) -> str:
    """The effective status as a SQL expression over ``{prefix}status``.

    ``params`` is the query's parameter dict (named ``:es_k<n>`` binds) or
    list (``?`` binds, appended in order). With no keys it is the column.
    """
    column = f"{prefix}status"
    if not keys:
        return column
    if isinstance(params, dict):
        params["es_sep"] = KEY_SEP
        binds = []
        for i, key in enumerate(keys):
            params[f"es_k{i}"] = key
            binds.append(f":es_k{i}")
        sep = ":es_sep"
    else:
        params.append(KEY_SEP)
        params.extend(keys)
        binds = ["?"] * len(keys)
        sep = "?"
    return (
        f"(CASE WHEN {prefix}entry_type = 'task' AND "
        f"({prefix}kb_name || {sep} || {prefix}id) IN ({', '.join(binds)}) "
        f"THEN 'done' ELSE {column} END)"
    )


def status_orm(keys: list[str]) -> Any:
    """The effective status as a SQLAlchemy expression over ``Entry.status``."""
    from sqlalchemy import and_, case

    if not keys:
        return Entry.status
    key = Entry.kb_name + KEY_SEP + Entry.id
    return case((and_(Entry.entry_type == "task", key.in_(keys)), "done"), else_=Entry.status)

"""The entry a rubric checker sees: one builder for every caller (#1587).

A named checker (``rubric_checkers.NAMED_CHECKERS``, or a plugin's) is handed
one dict. A ``board.yaml`` gate (``_evaluate_gate`` and the load-time dry run)
and QA (``validate_entry`` and the whole-KB rubric pass) build it here, from
the index, so a checker gives one verdict for one item whoever calls it.

The index keeps an item's fields in four places: the ``metadata`` bag, a
column of their own (``FIELD_COLUMNS``), the tag table, and the link table.
The checkers read ``metadata`` and three derived keys. So the entry is:

- the row's columns as they are (``title``, ``body``, ``status``, ...);
- ``metadata``: a new dict, never the stored bag. It holds the bag, with each
  field column filled in where the bag has no non-empty value, and ``tags``
  (the tag names) when the item has any. A field whose bag value and column
  are both empty (``None`` or ``""``) is absent, so a kb.yaml type (which keeps
  a protocol field in both places) and a plugin class (column only) give one
  answer;
- ``_tag_count``, ``_links`` (``[{"target", "relation"}]``, every ``link``
  row the item is the source of: frontmatter links and body wikilinks alike)
  and ``_outlink_count`` (``len(_links)``).

The bag is read first because, for a kb.yaml type, it holds the value as the
file wrote it, while the column holds a copy (a ``location`` list is joined
into one string). A column can hold Pyrite's reading rather than a line of
the file: a ``backlog_item`` that writes no ``priority`` reads ``medium``, as
``pyrite get`` shows it, and the checker sees the same reading.

The two lookups read the index, so in a gate they run outside
``board.evaluating``: a failure keeps its own error and is never reported as
a problem in ``board.yaml`` (#1585).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..utils.metadata import parse_metadata

#: The ``entry`` columns that hold one of the item's fields, laid into
#: ``metadata`` for ``has_field``, ``has_any_field`` and ``priority_present``.
#: ``tests/test_a_checker_sees_the_items_own_fields.py`` pins this list against
#: the table, so a new column needs a decision here.
FIELD_COLUMNS: tuple[str, ...] = (
    "title",
    "summary",
    "date",
    "importance",
    "status",
    "location",
    "assignee",
    "assigned_at",
    "priority",
    "due_date",
    "start_date",
    "end_date",
    "coordinates",
    "fips",
    "state",
    "lifecycle",
)


def _empty(value: Any) -> bool:
    return value is None or value == ""


def _entry(row: Mapping[str, Any], tags: list[str], links: list[dict[str, Any]]) -> dict:
    entry = dict(row)
    bag = dict(parse_metadata(entry.get("metadata")))
    for key in FIELD_COLUMNS:
        if _empty(bag.get(key)):
            bag.pop(key, None)
            if not _empty(entry.get(key)):
                bag[key] = entry[key]
    if tags:
        bag["tags"] = list(tags)
    entry["metadata"] = bag
    entry["_tag_count"] = len(tags)
    entry["_links"] = links
    entry["_outlink_count"] = len(links)
    return entry


def checker_entry(db: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    """The entry a checker sees for one index ``row`` (a whole ``entry`` row,
    ``SELECT *``; a mapping or a ``sqlite3.Row``). Reads the item's tags and
    outgoing links from ``db``."""
    params = {"eid": row["id"], "kb": row["kb_name"]}
    tag_rows = db.execute_sql(
        "SELECT t.name AS name FROM entry_tag et JOIN tag t ON t.id = et.tag_id "
        "WHERE et.entry_id = :eid AND et.kb_name = :kb ORDER BY t.name",
        params,
    )
    link_rows = db.execute_sql(
        "SELECT target_id, relation FROM link WHERE source_id = :eid AND source_kb = :kb",
        params,
    )
    return _entry(
        row,
        [r["name"] for r in tag_rows],
        [{"target": r["target_id"], "relation": r["relation"]} for r in link_rows],
    )


def empty_checker_entry() -> dict[str, Any]:
    """The entry with no row: the shape a gate criterion is dry-run against
    when ``board.yaml`` loads."""
    return _entry({"id": "", "kb_name": "", "title": "", "body": "", "metadata": {}}, [], [])

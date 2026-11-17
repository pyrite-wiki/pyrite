"""One verdict over an index health report (#1119).

`pyrite index health`, `pyrite-admin index health` and QA's
``index_out_of_date`` gate all read ``IndexManager.check_health``; they
must not decide for themselves whether the report is healthy.
"""

from __future__ import annotations

from typing import Any


def health_verdict(health: dict[str, Any]) -> dict[str, Any]:
    """The one verdict over an ``IndexManager.check_health`` report (#1119).

    `pyrite index health`, `pyrite-admin index health` and QA's
    ``index_out_of_date`` gate read the same report, but each decided for
    itself: the admin command ignored ``duplicates``, ``content_changed`` and
    error-severity ``off_list_values``, and QA ignored ``orphaned_entries``, so
    a report that said unhealthy on one surface read healthy on another. Every
    consumer calls this function.

    Returns ``status`` (``healthy``, ``warning`` or ``unhealthy``),
    ``is_unhealthy`` and ``has_warning``. Warnings name a problem that does not
    stop the index from serving what it holds; unhealthy means search or a
    read would answer from something other than the files.
    """
    off_list_severities = {row.get("severity") for row in health.get("off_list_values") or []}
    is_unhealthy = bool(
        health.get("missing_files")
        or health.get("unindexed_files")
        or health.get("stale_entries")
        or health.get("content_changed")
        # Two files claiming one id: only the first path is indexed, so the
        # other is invisible to search (ADR-0038 decided question 3).
        or health.get("duplicates")
        or health.get("unreadable_files")
        # A KB whose kb.yaml could not be checked or read: what the index
        # holds for it cannot be compared with its files (#1542).
        or health.get("unreadable_kbs")
        or health.get("orphaned_entries")
        or "error" in off_list_severities
    )
    has_warning = bool(
        health.get("broken_links")
        or health.get("undeclared_types")
        or health.get("type_name_drift_total")
        or health.get("schema_errors")
        or health.get("missing_required_fields")
        or health.get("subdirectory_mismatches")
        or health.get("malformed_frontmatter")
        or health.get("invalid_statuses")
        or "warning" in off_list_severities
        or health.get("removed_extensions")
        or health.get("unmodellable_frontmatter")
        or health.get("double_spellings")
        # A link row a rebuild would point at a different KB (a shortname
        # renamed since the file was indexed, or a row from before #980).
        or health.get("links_stale_kb")
        # An in-KB link with nothing at the path it names (hand-made, or left
        # by a delete that keeps the user's link): no entry is there, so no
        # read answers from anything but the files; the user should know (#1262).
        or health.get("dangling_links")
        # An entry file an earlier version wrote where the walk does not list
        # it (a create titled README wrote `readme.md`): not indexed, so no
        # read answers from it, but its id is not findable (#1552).
        or health.get("unlisted_entry_files")
    )
    status = "unhealthy" if is_unhealthy else ("warning" if has_warning else "healthy")
    return {"status": status, "is_unhealthy": is_unhealthy, "has_warning": has_warning}

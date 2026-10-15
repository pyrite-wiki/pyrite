"""Actor extraction and migration tool for Cascade timelines.

Scans timeline events for actor strings, groups variants using alias mappings,
and creates proper actor entries in the KB.
"""

import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .aliases import extract_actor_counts_from_db

logger = logging.getLogger(__name__)


def _slugify_id(name: str) -> str:
    """Convert an actor name to a kebab-case entry ID."""
    s = name.lower().strip()
    s = re.sub(r"[''`]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s


def _compute_importance(count: int, max_count: int) -> int:
    """Map appearance count to 1-10 importance scale."""
    if max_count <= 0:
        return 5
    ratio = count / max_count
    if ratio >= 0.5:
        return 10
    elif ratio >= 0.2:
        return 8
    elif ratio >= 0.1:
        return 7
    elif ratio >= 0.05:
        return 6
    elif ratio >= 0.02:
        return 5
    elif ratio >= 0.01:
        return 4
    else:
        return 3


def _load_alias_file(alias_file: Path) -> dict[str, list[str]]:
    """Load a canonical → aliases mapping from JSON file."""
    from pyrite.cli.context import require_input_file

    # A missing alias file used to be skipped without a word (#923).
    with open(require_input_file(alias_file, "Alias file")) as f:
        return json.load(f)


def _apply_alias_mapping(
    actor_counts: Counter,
    alias_map: dict[str, list[str]],
) -> dict[str, dict[str, Any]]:
    """Group actor names using alias mappings, merging counts."""
    # Build reverse lookup: alias → canonical
    reverse: dict[str, str] = {}
    for canonical, aliases in alias_map.items():
        for alias in aliases:
            reverse[alias] = canonical

    # Group actors
    groups: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()

    for actor, count in actor_counts.most_common():
        if actor in seen:
            continue

        # Check if this actor is an alias for something
        canonical = reverse.get(actor, actor)

        if canonical not in groups:
            groups[canonical] = {
                "count": 0,
                "aliases": [],
            }

        groups[canonical]["count"] += count
        seen.add(actor)

        if actor != canonical:
            groups[canonical]["aliases"].append(actor)

        # Also merge any aliases of this canonical that appear in counts
        if canonical in alias_map:
            for alias in alias_map[canonical]:
                if alias not in seen and alias in actor_counts:
                    groups[canonical]["count"] += actor_counts[alias]
                    groups[canonical]["aliases"].append(alias)
                    seen.add(alias)

    return groups


def _build_groups_without_aliases(actor_counts: Counter) -> dict[str, dict[str, Any]]:
    """Build actor groups without alias mapping — each actor is its own group."""
    return {actor: {"count": count, "aliases": []} for actor, count in actor_counts.most_common()}


def extract_actors(
    db: Any,
    kb_name: str,
    config: Any = None,
    alias_file: Path | None = None,
    dry_run: bool = True,
    event_types: list[str] | None = None,
) -> dict[str, Any]:
    """Extract actors from events and optionally create actor entries.

    Returns a result dict with:
    - actors: {name: {count, importance, aliases, entry_id}}
    - created: number of entries created (0 if dry_run)
    - skipped: list of actor names skipped (already exist)
    """
    actor_counts = extract_actor_counts_from_db(db, kb_name, event_types)
    if not actor_counts:
        return {"actors": {}, "created": 0, "skipped": []}

    # Group using alias mapping if provided
    if alias_file:
        alias_map = _load_alias_file(alias_file)
        groups = _apply_alias_mapping(actor_counts, alias_map)
    else:
        groups = _build_groups_without_aliases(actor_counts)

    max_count = max(g["count"] for g in groups.values()) if groups else 0

    # Compute importance and entry IDs
    for name, info in groups.items():
        info["importance"] = _compute_importance(info["count"], max_count)
        info["entry_id"] = _slugify_id(name)

    result: dict[str, Any] = {"actors": groups, "created": 0, "skipped": []}

    if dry_run:
        return result

    if not config:
        return result

    # Check which actors already exist
    existing = db.list_entries(kb_name=kb_name, entry_type="actor", limit=10000)
    existing_titles = {e.get("title", "").lower() for e in existing}
    existing_ids = {e.get("id", "") for e in existing}

    from pyrite.services.kb_service import KBService

    svc = KBService(config, db)
    created = 0

    for name, info in groups.items():
        entry_id = info["entry_id"]

        # Skip if already exists (by title or ID)
        if name.lower() in existing_titles or entry_id in existing_ids:
            result["skipped"].append(name)
            continue

        # Create actor entry
        svc.create_entry(
            kb_name,
            entry_id,
            name,
            "actor",
            importance=info["importance"],
            aliases=info.get("aliases", []),
        )
        created += 1

    result["created"] = created
    return result

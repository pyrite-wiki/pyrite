"""Utility functions for known-entities KB pattern.

Provides matching logic to find known entities across KBs by title or alias,
useful for suggesting links when creating entities in investigation KBs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from pyrite.utils.frontmatter import Frontmatter, NoFrontmatter, describe, split_frontmatter

logger = logging.getLogger(__name__)


def find_matching_entities(
    db: Any,
    query: str,
    known_kb_names: list[str],
) -> list[dict]:
    """Search across specified KBs for entries matching by title or aliases.

    Args:
        db: PyriteDB instance.
        query: Search string to match against titles and aliases (case-insensitive).
        known_kb_names: List of KB names to search.

    Returns:
        List of match dicts with: id, kb_name, title, entry_type, match_type.
        match_type is "title" if the title matched, or "alias" if an alias matched.
    """
    query_lower = query.lower()
    matches: list[dict] = []

    for kb_name in known_kb_names:
        entries = db.list_entries(
            kb_name=kb_name,
            limit=1000,
        )
        for entry in entries:
            # Check title match (case-insensitive)
            if entry["title"].lower() == query_lower:
                matches.append(_make_match(entry, "title"))
                continue

            # Check alias match — aliases live in frontmatter on disk
            aliases = _extract_aliases(entry)
            for alias in aliases:
                if alias.lower() == query_lower:
                    matches.append(_make_match(entry, "alias"))
                    break

    return matches


def _make_match(entry: dict, match_type: str) -> dict:
    """Build a match result dict from an entry."""
    return {
        "id": entry["id"],
        "kb_name": entry["kb_name"],
        "title": entry["title"],
        "entry_type": entry["entry_type"],
        "match_type": match_type,
    }


def _extract_aliases(entry: dict) -> list[str]:
    """Extract aliases from an entry's file frontmatter.

    Aliases are stored in the markdown frontmatter but not currently
    persisted to the DB metadata column, so we read them from disk.
    """
    file_path = entry.get("file_path")
    if not file_path:
        return []
    path = Path(file_path)
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    split = split_frontmatter(text)
    if not isinstance(split, Frontmatter):
        if not isinstance(split, NoFrontmatter):
            logger.warning("No aliases read from %s: %s", path, describe(split))
        return []
    try:
        fm = yaml.safe_load(split.text)
    except yaml.YAMLError:
        return []
    if not isinstance(fm, dict):
        return []
    aliases = fm.get("aliases", [])
    if isinstance(aliases, list):
        return [a for a in aliases if isinstance(a, str)]
    return []

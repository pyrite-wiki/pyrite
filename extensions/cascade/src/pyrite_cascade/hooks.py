"""Before-save hooks for the Cascade plugin."""

import logging
import re
from typing import Any

from pyrite.utils.frontmatter import Frontmatter, NoFrontmatter, describe, split_frontmatter

logger = logging.getLogger(__name__)

# Regex to detect wikilinks: [[some-id]] or [[kb:some-id]]
_WIKILINK_RE = re.compile(r"^\[\[(?:[a-z0-9-]+:)?(.+?)\]\]$")

# Module-level actor lookup cache: {kb_name: {name_lower: entry_id}}
_actor_cache: dict[str, dict[str, str]] = {}


def _strip_wikilink(ref: str) -> str | None:
    """Extract entry ID from a wikilink like [[some-id]]. Returns None if not a wikilink."""
    match = _WIKILINK_RE.match(ref.strip())
    if match:
        return match.group(1).strip()
    return None


def _build_actor_lookup(db: Any, kb_name: str, config: Any = None) -> dict[str, str]:
    """Build a case-insensitive title/alias → entry_id map for all actors in the KB.

    Checks the cache first; rebuilds on cache miss. Includes title matches
    and alias matches read from actor entry files.
    """
    if kb_name in _actor_cache:
        return _actor_cache[kb_name]

    lookup: dict[str, str] = {}
    try:
        actors = db.list_entries(kb_name=kb_name, entry_type="actor", limit=10000)
        for actor in actors:
            entry_id = actor.get("id", "")
            title = actor.get("title", "")
            if not entry_id:
                continue
            if title:
                lookup[title.lower()] = entry_id
            # Also index the entry_id itself for wikilink resolution
            lookup[entry_id.lower()] = entry_id

            # Read aliases from file if we have config
            if config:
                _load_aliases_for_actor(config, kb_name, entry_id, actor, lookup)
    except Exception:
        logger.debug("Failed to build actor lookup for %s", kb_name, exc_info=True)

    _actor_cache[kb_name] = lookup
    return lookup


def _load_aliases_for_actor(
    config: Any,
    kb_name: str,
    entry_id: str,
    actor_dict: dict[str, Any],
    lookup: dict[str, str],
) -> None:
    """Load aliases from an actor entry's file and add them to the lookup."""
    try:
        file_path = actor_dict.get("file_path", "")
        if not file_path:
            return
        from pathlib import Path

        path = Path(file_path)
        if not path.exists():
            return

        import yaml

        content = path.read_text(encoding="utf-8")
        split = split_frontmatter(content)
        if not isinstance(split, Frontmatter):
            if not isinstance(split, NoFrontmatter):
                logger.warning("No aliases read from %s: %s", path, describe(split))
            return
        meta = yaml.safe_load(split.text)
        if not meta:
            return
        aliases = meta.get("aliases", []) or []
        for alias in aliases:
            if isinstance(alias, str) and alias:
                lookup[alias.lower()] = entry_id
    except Exception:
        logger.debug("Failed to load aliases for %s", entry_id, exc_info=True)


def invalidate_actor_cache(kb_name: str | None = None) -> None:
    """Clear the actor lookup cache. Call after creating/deleting actor entries."""
    if kb_name:
        _actor_cache.pop(kb_name, None)
    else:
        _actor_cache.clear()


def resolve_actor_links(entry: Any, context: Any) -> Any:
    """Before-save hook: resolve actor strings to actor_reference links.

    For timeline_event and solidarity_event entries, reads the `actors` field
    and creates actor_reference links to matching actor entries. Supports both
    plain strings ("Donald Trump") and wikilinks ("[[donald-trump]]").
    """
    entry_type = getattr(entry, "entry_type", "")
    if entry_type not in ("timeline_event", "solidarity_event", "scene"):
        return entry

    actors = getattr(entry, "actors", [])
    if not actors:
        return entry

    kb_name = context.kb_name if hasattr(context, "kb_name") else context.get("kb_name", "")
    db = context.db if hasattr(context, "db") else context.get("db")
    if not db or not kb_name:
        return entry

    config = context.config if hasattr(context, "config") else context.get("config")
    lookup = _build_actor_lookup(db, kb_name, config=config)
    if not lookup:
        return entry

    # Collect existing link targets to avoid duplicates
    existing = {(l.target, l.relation) for l in entry.links}

    for actor_str in actors:
        if not actor_str or not isinstance(actor_str, str):
            continue

        # Check if it's a wikilink
        wikilink_id = _strip_wikilink(actor_str)
        if wikilink_id:
            target_id = wikilink_id
        else:
            # Plain string — look up by title (case-insensitive)
            target_id = lookup.get(actor_str.lower())

        if not target_id:
            continue

        if (target_id, "actor_reference") not in existing:
            entry.add_link(target=target_id, relation="actor_reference")
            existing.add((target_id, "actor_reference"))

    return entry


def _on_actor_saved(entry: Any, context: Any) -> Any:
    """After-save hook: invalidate actor cache when an actor entry is saved."""
    entry_type = getattr(entry, "entry_type", "")
    if entry_type == "actor":
        kb_name = context.kb_name if hasattr(context, "kb_name") else context.get("kb_name", "")
        invalidate_actor_cache(kb_name)
    return entry

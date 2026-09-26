"""
Data loading utilities for pyrite UI.

Provides cached access to knowledge base data via the REST API or direct DB access.
"""

from typing import Any

import streamlit as st

from ..services.access_policy import UNSCOPED

# Try to import from pyrite, fall back to API calls
try:
    from pyrite.config import load_config
    from pyrite.services.kb_service import KBService
    from pyrite.storage.database import PyriteDB

    DIRECT_ACCESS = True
except ImportError:
    DIRECT_ACCESS = False


@st.cache_resource
def _get_db():
    """Get database connection (cached as resource)."""
    if not DIRECT_ACCESS:
        return None
    config = load_config()
    return PyriteDB(config.settings.index_path)


@st.cache_resource
def _get_config():
    """Get configuration (cached as resource)."""
    if not DIRECT_ACCESS:
        return None
    return load_config()


@st.cache_resource
def _get_kb_service():
    """Get the KB service (cached as resource). The UI reads through it (#380)."""
    if not DIRECT_ACCESS:
        return None
    return KBService(_get_config(), _get_db())


@st.cache_data(ttl=300)
def get_kb_list() -> list[dict[str, Any]]:
    """Get list of knowledge bases."""
    svc = _get_kb_service()
    config = _get_config()

    if not svc or not config:
        return []

    kbs = []
    for kb in config.knowledge_bases:
        stats = svc.get_kb_stats(kb.name)
        kbs.append(
            {
                "name": kb.name,
                "type": kb.kb_type,
                "path": str(kb.path),
                "entries": stats.get("entry_count", 0) if stats else 0,
                "indexed": bool(stats.get("last_indexed")) if stats else False,
            }
        )
    return kbs


@st.cache_data(ttl=300)
def get_stats() -> dict[str, Any]:
    """Get index statistics."""
    svc = _get_kb_service()
    if not svc:
        return {"total_entries": 0, "total_tags": 0, "total_links": 0}
    return svc.get_index_stats()


@st.cache_data(ttl=60)
def search(
    query: str,
    kb_name: str | None = None,
    entry_type: str | None = None,
    tags: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    mode: str = "keyword",
    expand: bool = False,
) -> list[dict[str, Any]]:
    """Full-text search with optional semantic/hybrid mode and query expansion."""
    db = _get_db()
    if not db:
        return []

    from pyrite.services.search_service import SearchService

    try:
        config = _get_config()
        settings = config.settings if config else None
        search_svc = SearchService(db, settings=settings)
        return search_svc.search(
            query=query,
            kb_name=kb_name if kb_name != "All KBs" else None,
            entry_type=entry_type,
            tags=tags,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            mode=mode,
            expand=expand,
        )
    except Exception as e:
        st.error(f"Search error: {e}")
        return []


@st.cache_data(ttl=60)
def get_timeline(
    date_from: str | None = None,
    date_to: str | None = None,
    min_importance: int = 1,
    actor: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Get timeline events."""
    svc = _get_kb_service()
    if not svc:
        return []

    results = svc.get_timeline(date_from=date_from, date_to=date_to, min_importance=min_importance)

    if actor:
        actor_lower = actor.lower()
        results = [
            r for r in results if any(actor_lower in a.lower() for a in (r.get("actors") or []))
        ]

    return results[:limit]


@st.cache_data(ttl=300)
def get_tags(kb_name: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Get tags with counts."""
    svc = _get_kb_service()
    if not svc:
        return []

    effective_kb = kb_name if kb_name and kb_name != "All KBs" else None
    return svc.get_tags(kb_name=effective_kb, limit=limit)


@st.cache_data(ttl=60)
def get_entry(entry_id: str, kb_name: str | None = None) -> dict[str, Any] | None:
    """Get entry by ID."""
    svc = _get_kb_service()
    config = _get_config()
    if not svc or not config:
        return None

    # KBService.get_entry attaches outlinks and backlinks. With no KB named,
    # the UI looks only in config.yaml's KBs (not DB-registered ones), so it
    # walks them itself rather than using get_entry's all-KB search.
    if kb_name and kb_name != "All KBs":
        return svc.get_entry(entry_id, kb_name, readable_kbs=UNSCOPED)
    for kb in config.knowledge_bases:
        result = svc.get_entry(entry_id, kb.name, readable_kbs=UNSCOPED)
        if result:
            return result
    return None


@st.cache_data(ttl=60)
def get_entry_graph(entry_id: str, kb_name: str) -> dict[str, Any]:
    """Get graph data (nodes + edges) centered on an entry."""
    svc = _get_kb_service()
    if not svc:
        return {"nodes": [], "edges": []}

    center = svc.get_entry(
        entry_id, kb_name, readable_kbs=UNSCOPED
    )  # carries outlinks and backlinks
    if not center:
        return {"nodes": [], "edges": []}

    outlinks = center["outlinks"]
    backlinks = center["backlinks"]

    nodes = {}
    edges = []

    # Center node
    nodes[(entry_id, kb_name)] = {
        "id": entry_id,
        "kb_name": kb_name,
        "title": center.get("title", entry_id),
        "entry_type": center.get("entry_type", "unknown"),
        "importance": center.get("importance"),
        "is_center": True,
    }

    # Outgoing links
    for link in outlinks:
        key = (link["id"], link["kb_name"])
        if key not in nodes:
            nodes[key] = {
                "id": link["id"],
                "kb_name": link["kb_name"],
                "title": link.get("title") or link["id"],
                "entry_type": link.get("entry_type", "unknown"),
                "importance": None,
                "is_center": False,
            }
        edges.append(
            {
                "source": entry_id,
                "target": link["id"],
                "label": link.get("relation", "related"),
            }
        )

    # Backlinks
    for link in backlinks:
        key = (link["id"], link["kb_name"])
        if key not in nodes:
            nodes[key] = {
                "id": link["id"],
                "kb_name": link["kb_name"],
                "title": link.get("title") or link["id"],
                "entry_type": link.get("entry_type", "unknown"),
                "importance": None,
                "is_center": False,
            }
        edges.append(
            {
                "source": link["id"],
                "target": entry_id,
                "label": link.get("relation", "related"),
            }
        )

    return {"nodes": list(nodes.values()), "edges": edges}


def save_entry(entry_id: str, kb_name: str, **updates) -> bool:
    """Update an entry via KBService. Returns True on success."""
    svc = _get_kb_service()
    if not svc:
        return False

    try:
        svc.update_entry(entry_id, kb_name, **updates)
        clear_cache()
        return True
    except ValueError:
        return False


def clear_cache():
    """Clear all cached data."""
    st.cache_data.clear()

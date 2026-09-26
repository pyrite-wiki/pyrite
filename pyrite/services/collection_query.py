"""Collection query DSL — parse, validate, and evaluate virtual collection queries.

Supports inline query strings like:
    "type:backlog_item status:proposed tags:enhancement,core"

And structured queries from collection metadata (entry_filter field).
"""

import hashlib
import logging
import time
from dataclasses import asdict, dataclass

from ..storage.database import PyriteDB
from ..utils.metadata import parse_metadata

logger = logging.getLogger(__name__)


@dataclass
class CollectionQuery:
    """Structured query for virtual collections."""

    entry_type: str | None = None
    tags_any: list[str] | None = None  # match ANY of these tags
    tags_all: list[str] | None = None  # match ALL of these tags
    date_from: str | None = None
    date_to: str | None = None
    kb_name: str | None = None
    status: str | None = None
    fields: dict[str, str] | None = None  # field comparisons from metadata
    sort_by: str = "title"
    sort_order: str = "asc"
    limit: int = 200
    offset: int = 0


def parse_query(query_str: str) -> CollectionQuery:
    """Parse inline query string like 'type:backlog_item status:proposed tags:core,enhancement'.

    Supported operators:
        type:<entry_type>
        tags:<comma-separated>          (treated as tags_any)
        tags_all:<comma-separated>      (AND logic)
        status:<status>
        date_from:<YYYY-MM-DD>
        date_to:<YYYY-MM-DD>
        kb:<kb_name>
        sort:<field>                    (prefix with - for desc, e.g. sort:-updated_at)
        limit:<int>
        offset:<int>
        <key>=<value>                   (arbitrary field comparison)
    """
    query = CollectionQuery()
    if not query_str or not query_str.strip():
        return query

    tokens = query_str.strip().split()
    for token in tokens:
        if ":" in token:
            key, _, value = token.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if not value:
                continue

            if key == "type":
                query.entry_type = value
            elif key == "tags":
                query.tags_any = [t.strip() for t in value.split(",") if t.strip()]
            elif key == "tags_all":
                query.tags_all = [t.strip() for t in value.split(",") if t.strip()]
            elif key == "status":
                query.status = value
            elif key == "date_from":
                query.date_from = value
            elif key == "date_to":
                query.date_to = value
            elif key == "kb":
                query.kb_name = value
            elif key == "sort":
                if value.startswith("-"):
                    query.sort_by = value[1:]
                    query.sort_order = "desc"
                else:
                    query.sort_by = value
                    query.sort_order = "asc"
            elif key == "limit":
                try:
                    query.limit = int(value)
                except ValueError:
                    logger.debug("Could not parse query parameter as number: %s", value)
            elif key == "offset":
                try:
                    query.offset = int(value)
                except ValueError:
                    logger.debug("Could not parse query parameter as number: %s", value)
            else:
                # Treat as field comparison
                if query.fields is None:
                    query.fields = {}
                query.fields[key] = value
        elif "=" in token:
            # key=value field comparison
            key, _, value = token.partition("=")
            if key and value:
                if query.fields is None:
                    query.fields = {}
                query.fields[key.strip()] = value.strip()

    return query


def query_from_dict(d: dict) -> CollectionQuery:
    """Build query from collection metadata dict (entry_filter field)."""
    query = CollectionQuery()

    if not d or not isinstance(d, dict):
        return query

    if "entry_type" in d:
        query.entry_type = d["entry_type"]
    if "type" in d:
        query.entry_type = d["type"]
    if "tags_any" in d:
        tags = d["tags_any"]
        query.tags_any = tags if isinstance(tags, list) else [tags]
    if "tags" in d:
        tags = d["tags"]
        query.tags_any = tags if isinstance(tags, list) else [t.strip() for t in tags.split(",")]
    if "tags_all" in d:
        tags = d["tags_all"]
        query.tags_all = tags if isinstance(tags, list) else [tags]
    if "date_from" in d:
        query.date_from = str(d["date_from"])
    if "date_to" in d:
        query.date_to = str(d["date_to"])
    if "kb_name" in d or "kb" in d:
        query.kb_name = d.get("kb_name") or d.get("kb")
    if "status" in d:
        query.status = d["status"]
    if "fields" in d and isinstance(d["fields"], dict):
        query.fields = d["fields"]
    if "sort_by" in d:
        query.sort_by = d["sort_by"]
    if "sort_order" in d:
        query.sort_order = d["sort_order"]
    if "limit" in d:
        try:
            query.limit = int(d["limit"])
        except (ValueError, TypeError):
            logger.debug("Could not parse query parameter as number: %s", d["limit"])
    if "offset" in d:
        try:
            query.offset = int(d["offset"])
        except (ValueError, TypeError):
            logger.debug("Could not parse query parameter as number: %s", d["offset"])

    return query


def validate_query(query: CollectionQuery) -> list[str]:
    """Validate query, return list of error messages (empty if valid)."""
    errors: list[str] = []

    if query.sort_order not in ("asc", "desc"):
        errors.append(f"Invalid sort_order: {query.sort_order!r} (must be 'asc' or 'desc')")

    allowed_sorts = {"title", "entry_type", "updated_at", "created_at", "date", "id"}
    if query.sort_by not in allowed_sorts:
        errors.append(
            f"Invalid sort_by: {query.sort_by!r} (allowed: {', '.join(sorted(allowed_sorts))})"
        )

    if query.limit < 1:
        errors.append(f"limit must be >= 1, got {query.limit}")
    if query.limit > 1000:
        errors.append(f"limit must be <= 1000, got {query.limit}")

    if query.offset < 0:
        errors.append(f"offset must be >= 0, got {query.offset}")

    # Validate date formats
    import re

    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    if query.date_from and not date_pattern.match(query.date_from):
        errors.append(f"Invalid date_from format: {query.date_from!r} (expected YYYY-MM-DD)")
    if query.date_to and not date_pattern.match(query.date_to):
        errors.append(f"Invalid date_to format: {query.date_to!r} (expected YYYY-MM-DD)")

    return errors


def evaluate_query(
    query: CollectionQuery,
    db: PyriteDB,
    *,
    readable_kbs: set[str] | list[str] | None,
) -> tuple[list[dict], int]:
    """Evaluate query against DB. Returns (entries, total_count).

    Uses db.list_entries() for the base query, then applies additional
    filters (date_from, date_to, status, tags_all, fields) in Python.

    ``readable_kbs`` is the caller's readable set (``UNSCOPED``: none). It goes
    into ``list_entries`` rather than into ``_post_filter`` because
    ``total_count`` is computed from the filtered rows: a private row
    dropped after the fact would still be counted.

    A KB the query names itself (``kb:`` in the text, ``kb``/``kb_name``
    in an ``entry_filter``) is authorized against the same set, as a named
    KB would be: one outside it answers exactly as a KB that does not
    exist does -- no rows (P-R2, P-R5).
    """
    if query.kb_name and readable_kbs is not None and query.kb_name not in readable_kbs:
        return [], 0
    # Use the first tag from tags_any for the DB-level filter (it only supports one)
    db_tag = query.tags_any[0] if query.tags_any and len(query.tags_any) == 1 else None

    # Fetch a larger set for post-filtering, then apply limit/offset after
    fetch_limit = query.limit + query.offset + 500  # over-fetch for post-filtering
    base_results = db.list_entries(
        kb_name=query.kb_name,
        kb_names=None if query.kb_name else readable_kbs,
        entry_type=query.entry_type,
        tag=db_tag,
        sort_by=query.sort_by
        if query.sort_by in {"title", "entry_type", "updated_at", "created_at", "date", "id"}
        else "title",
        sort_order=query.sort_order,
        limit=fetch_limit,
        offset=0,
    )

    # Post-filter
    filtered = _post_filter(base_results, query)

    total_count = len(filtered)
    # Apply offset and limit
    paginated = filtered[query.offset : query.offset + query.limit]

    return paginated, total_count


def _post_filter(entries: list[dict], query: CollectionQuery) -> list[dict]:
    """Apply post-filters that can't be handled by the DB query."""
    result = entries

    # tags_any with multiple tags: entry must have at least one
    if query.tags_any and len(query.tags_any) > 1:
        tag_set = set(query.tags_any)
        result = [e for e in result if tag_set.intersection(_get_tags(e))]

    # tags_all: entry must have ALL of these tags
    if query.tags_all:
        tag_set = set(query.tags_all)
        result = [e for e in result if tag_set.issubset(_get_tags(e))]

    # status filter
    if query.status:
        result = [e for e in result if _get_metadata_field(e, "status") == query.status]

    # date range filters
    if query.date_from:
        result = [e for e in result if (e.get("date") or "") >= query.date_from]
    if query.date_to:
        result = [e for e in result if (e.get("date") or "") <= query.date_to]

    # Arbitrary field comparisons
    if query.fields:
        for field_name, field_value in query.fields.items():
            result = [e for e in result if _get_metadata_field(e, field_name) == field_value]

    return result


def _get_tags(entry: dict) -> set[str]:
    """Extract tags from an entry dict."""
    tags = entry.get("tags", [])
    if isinstance(tags, str):
        return {t.strip() for t in tags.split(",") if t.strip()}
    if isinstance(tags, list):
        return set(tags)
    return set()


def _get_metadata_field(entry: dict, field_name: str) -> str | None:
    """Get a field from entry dict, checking both top-level and metadata."""
    # Check top-level first
    if field_name in entry and entry[field_name] is not None:
        return str(entry[field_name])

    # Check metadata dict
    metadata = parse_metadata(entry.get("metadata", {}))

    if field_name in metadata:
        val = metadata[field_name]
        return str(val) if val is not None else None

    return None


# =============================================================================
# Query caching
# =============================================================================

_query_cache: dict[str, tuple[float, list[dict], int]] = {}
CACHE_TTL = 60  # seconds


def _cache_key(query: CollectionQuery, *, readable_kbs: set[str] | list[str] | None) -> str:
    """A stable cache key from the query fields and the caller's scope.

    The scope is part of the key: the same query evaluated for two scopes
    has two answers, and one scope's rows must never be served to another.
    """
    d = asdict(query)
    scope = None if readable_kbs is None else sorted(readable_kbs)
    raw = str((sorted(d.items()), scope))
    return hashlib.sha256(raw.encode()).hexdigest()


def evaluate_query_cached(
    query: CollectionQuery,
    db: PyriteDB,
    ttl: int = CACHE_TTL,
    *,
    readable_kbs: set[str] | list[str] | None,
) -> tuple[list[dict], int]:
    """Cached version of evaluate_query, keyed by query and scope."""
    key = _cache_key(query, readable_kbs=readable_kbs)
    now = time.time()

    if key in _query_cache:
        cached_time, cached_entries, cached_total = _query_cache[key]
        if now - cached_time < ttl:
            return cached_entries, cached_total

    entries, total = evaluate_query(query, db, readable_kbs=readable_kbs)
    _query_cache[key] = (now, entries, total)

    # Prune expired entries periodically
    if len(_query_cache) > 100:
        expired = [k for k, (t, _, _) in _query_cache.items() if now - t >= ttl]
        for k in expired:
            del _query_cache[k]

    return entries, total


def clear_cache() -> None:
    """Clear the query cache."""
    _query_cache.clear()

"""Collection endpoints — list and browse folder-backed and query-based collections."""

import re

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from ...exceptions import EntryNotFoundError
from ...plugins.registry import get_registry
from ...services.access_policy import KB, UNSCOPED, Action, ReadScope
from ...services.kb_service import KBService
from ...utils.metadata import parse_metadata
from ..api import (
    get_kb_service,
    limiter,
    negotiate_response,
    requires_kb_tier,
)
from ..authz import authorize
from ..schemas import (
    CollectionEntriesResponse,
    CollectionListResponse,
    CollectionResponse,
    CreateCollectionRequest,
    EntryResponse,
    QueryPreviewRequest,
    QueryPreviewResponse,
)

router = APIRouter(tags=["Collections"])


def _parse_metadata(raw) -> dict:
    """Parse metadata which may be a JSON string or dict."""
    return parse_metadata(raw)


@router.get("/collections/types")
@limiter.limit("100/minute")
def get_collection_types(request: Request):
    """Get available collection types (built-in + plugin-provided)."""
    built_in = {
        "generic": {
            "description": "General-purpose collection",
            "default_view": "list",
            "fields": {},
            "ai_instructions": "",
            "icon": "folder",
        },
    }
    plugin_types = get_registry().get_all_collection_types()
    # Plugin types override built-in on collision
    merged = {**built_in, **plugin_types}
    return {"types": merged}


@router.get("/collections", response_model=CollectionListResponse)
@limiter.limit("100/minute")
def list_collections(
    request: Request,
    kb: str | None = Query(None, description="Filter by KB name"),
    svc: KBService = Depends(get_kb_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """List the collections in the KBs the caller may read.

    A collection is an entry: its id, title and tags are KB content, and
    ``total`` counts the rows returned. The readable set is pushed into
    the underlying ``list_entries`` query for that reason.
    """
    results = svc.list_collections(kb_name=kb, kb_names=None if kb else scope.as_set())

    collections = []
    for r in results:
        meta = _parse_metadata(r.get("metadata"))
        collections.append(
            CollectionResponse(
                id=r["id"],
                title=r.get("title", ""),
                description=meta.get("description", ""),
                source_type=meta.get("source_type", "folder"),
                icon=meta.get("icon", ""),
                view_config=meta.get("view_config", {}),
                entry_count=0,
                kb_name=r.get("kb_name", ""),
                folder_path=meta.get("folder_path", ""),
                query=meta.get("query", ""),
                tags=r.get("tags", []),
            )
        )

    resp_data = {
        "collections": [c.model_dump() for c in collections],
        "total": len(collections),
    }
    neg = negotiate_response(request, resp_data)
    if neg is not None:
        return neg
    return CollectionListResponse(collections=collections, total=len(collections))


@router.post(
    "/collections",
    response_model=CollectionResponse,
    dependencies=[Depends(requires_kb_tier("write"))],
)
@limiter.limit("60/minute")
def create_collection(
    request: Request,
    body: CreateCollectionRequest = Body(...),
    svc: KBService = Depends(get_kb_service),
):
    """Create a new virtual collection."""
    metadata = {
        "description": body.description or "",
        "source_type": "query",
        "query": body.query,
        "icon": body.icon or "",
        "view_config": body.view_config or {},
        "collection_type": body.collection_type,
    }

    # Generate a slug-style entry_id from the title
    slug = re.sub(r"[^a-z0-9]+", "-", body.title.lower()).strip("-")
    if not slug:
        slug = "collection"

    # Check for duplicate slug and append suffix if needed
    base_slug = slug
    counter = 1
    while True:
        try:
            # Named KB on a write route; an existence check for the slug.
            existing = svc.get_entry(slug, kb_name=body.kb, readable_kbs=UNSCOPED)
            if existing:
                counter += 1
                slug = f"{base_slug}-{counter}"
            else:
                break
        except Exception:
            break

    result = svc.create_entry(
        kb_name=body.kb,
        entry_id=slug,
        entry_type="collection",
        title=body.title,
        body="",
        metadata=metadata,
    )

    return CollectionResponse(
        id=result["id"],
        title=body.title,
        description=body.description or "",
        source_type="query",
        icon=body.icon or "",
        view_config=body.view_config or {},
        entry_count=0,
        kb_name=body.kb,
        folder_path="",
        query=body.query,
        tags=[],
    )


@router.get(
    "/collections/{collection_id}",
    response_model=CollectionResponse,
    dependencies=[Depends(authorize(Action.KB_READ, KB))],
)
@limiter.limit("100/minute")
def get_collection(
    request: Request,
    collection_id: str,
    kb: str = Query(..., description="KB name"),
    svc: KBService = Depends(get_kb_service),
):
    """Get collection metadata."""
    # Named KB, authorized by the route; only the collection metadata is used.
    result = svc.get_entry(collection_id, kb_name=kb, readable_kbs=UNSCOPED)
    if not result or result.get("entry_type") != "collection":
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Collection '{collection_id}' not found"},
        )

    meta = _parse_metadata(result.get("metadata"))
    resp = CollectionResponse(
        id=result["id"],
        title=result.get("title", ""),
        description=meta.get("description", ""),
        source_type=meta.get("source_type", "folder"),
        icon=meta.get("icon", ""),
        view_config=meta.get("view_config", {}),
        entry_count=0,
        kb_name=result.get("kb_name", ""),
        folder_path=meta.get("folder_path", ""),
        query=meta.get("query", ""),
        tags=result.get("tags", []),
    )

    neg = negotiate_response(request, resp.model_dump())
    if neg is not None:
        return neg
    return resp


@router.get(
    "/collections/{collection_id}/entries",
    response_model=CollectionEntriesResponse,
)
@limiter.limit("100/minute")
def get_collection_entries(
    request: Request,
    collection_id: str,
    kb: str = Query(..., description="KB name"),
    sort_by: str = Query("title", description="Sort column"),
    sort_order: str = Query("asc", description="Sort direction: asc or desc"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    svc: KBService = Depends(get_kb_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """List entries within a collection.

    A stored query can name a KB of its own (``kb:`` in its text); it is
    evaluated within the viewer's readable set, so one the viewer cannot
    read returns what a missing KB returns.
    """
    try:
        results, total = svc.get_collection_entries(
            collection_id,
            kb,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
            readable_kbs=scope.as_set(),
        )
    except EntryNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": str(e)},
        )

    entries = []
    for r in results:
        r.setdefault("sources", [])
        r.setdefault("tags", [])
        r.setdefault("outlinks", [])
        r.setdefault("backlinks", [])
        entries.append(EntryResponse(**r))

    resp_data = {
        "entries": [e.model_dump() for e in entries],
        "total": total,
        "collection_id": collection_id,
    }
    neg = negotiate_response(request, resp_data)
    if neg is not None:
        return neg
    return CollectionEntriesResponse(entries=entries, total=total, collection_id=collection_id)


@router.post("/collections/query-preview", response_model=QueryPreviewResponse)
@limiter.limit("60/minute")
def preview_collection_query(
    request: Request,
    body: QueryPreviewRequest = Body(...),
    svc: KBService = Depends(get_kb_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """Preview results for a collection query without saving.

    A read route despite the POST: it runs an arbitrary query and returns
    the matching entries. With no ``kb`` in the body or the query the query
    spans every KB, so the readable set is pushed into the evaluation --
    ``total`` counts the matched rows and must not count private ones.
    """
    from ...services.collection_query import parse_query, validate_query

    query = parse_query(body.query)
    if body.kb:
        query.kb_name = body.kb
    query.limit = body.limit

    errors = validate_query(query)
    if errors:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_QUERY", "message": "; ".join(errors)},
        )

    # The query's own `kb:` is authorized against the same set as the body's
    # `kb` was: an unreadable one answers like a missing one (P-R2, P-R5).
    results, total = svc.evaluate_collection_query(query, readable_kbs=scope.as_set())

    entries = []
    for r in results:
        r.setdefault("sources", [])
        r.setdefault("tags", [])
        r.setdefault("outlinks", [])
        r.setdefault("backlinks", [])
        entries.append(EntryResponse(**r))

    query_parsed = {
        "entry_type": query.entry_type,
        "tags_any": query.tags_any,
        "tags_all": query.tags_all,
        "status": query.status,
        "kb_name": query.kb_name,
        "date_from": query.date_from,
        "date_to": query.date_to,
        "sort_by": query.sort_by,
        "sort_order": query.sort_order,
        "limit": query.limit,
    }

    resp_data = {
        "entries": [e.model_dump() for e in entries],
        "total": total,
        "query_parsed": query_parsed,
    }
    neg = negotiate_response(request, resp_data)
    if neg is not None:
        return neg
    return QueryPreviewResponse(entries=entries, total=total, query_parsed=query_parsed)

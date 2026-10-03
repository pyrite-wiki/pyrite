"""Search endpoint."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ...services.access_policy import KB, Action, ReadScope
from ...services.kb_service import KBService
from ...services.read_shaping import parse_fields_param, project_fields
from ...services.search_service import SearchService
from ..api import (
    get_kb_service,
    get_search_service,
    limiter,
    negotiate_response,
)
from ..authz import authorize
from ..schemas import SearchResponse, SearchResult

router = APIRouter(tags=["Search"])


# ``response_model_exclude_none``: the happy path must not serialise
# ``"warnings": null``. One convention across every surface — MCP omits the key,
# the CLI prints nothing, REST omits it — so a caller can test ``"warnings" in
# response`` and get the right answer. See ``SearchService.search``'s docstring
# for what a search response owes its caller (#56). The web client already
# declares the nullable result fields optional (web/src/lib/api/types.ts), so
# omitting them rather than nulling them matches the contract it was written to.
@router.get("/search", response_model=SearchResponse, response_model_exclude_none=True)
@limiter.limit("100/minute")
def search(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query"),
    kb: str | None = Query(None, description="Limit to specific KB"),
    type: str | None = Query(None, description="Filter by entry type"),
    tags: str | None = Query(None, description="Comma-separated tags"),
    date_from: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int = Query(20, ge=1, le=100),
    mode: str = Query("keyword", description="Search mode: keyword, semantic, hybrid"),
    expand: bool = Query(False, description="Use AI query expansion for additional search terms"),
    include_body: bool = Query(
        False, description="Include full body text in results (default: snippet only)"
    ),
    fields: str | None = Query(
        None,
        description=(
            "Comma-separated fields to return per result. "
            "id and kb_name are always included; when fields is set the response is a "
            "projection of the stored entry, not EntryResponse."
        ),
    ),
    group_by_kb: bool = Query(
        False, description="Return top results per KB instead of global ranking"
    ),
    limit_per_kb: int = Query(
        3, ge=1, le=20, description="Max results per KB when group_by_kb=true"
    ),
    svc: KBService = Depends(get_kb_service),
    search_svc: SearchService = Depends(get_search_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """Full-text search across the knowledge bases the caller may read."""
    if svc.count_entries() == 0:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "INDEX_EMPTY",
                "message": "Search index is empty",
                "hint": "Run: pyrite-admin index build",
            },
        )

    tag_list = tags.split(",") if tags else None
    # Anything the search could not do as asked (today: a filter a backend's
    # vector leg cannot honour). Omitted from the response when empty -- see the
    # route decorator above and SearchService.search's docstring (#56).
    warnings: list[str] = []

    try:
        # When grouping by KB, fetch more results to ensure coverage across KBs
        fetch_limit = limit * 5 if group_by_kb else limit

        results = search_svc.search(
            kb_names=None if kb else scope.as_set(),
            query=q,
            kb_name=kb,
            entry_type=type,
            tags=tag_list,
            date_from=date_from,
            date_to=date_to,
            limit=fetch_limit,
            mode=mode,
            expand=expand,
            warnings=warnings,
        )

        # Group by KB: take top N per KB, interleave by best score
        if group_by_kb:
            from collections import defaultdict

            by_kb: dict[str, list] = defaultdict(list)
            for r in results:
                kb_name_val = r.get("kb_name", "")
                if len(by_kb[kb_name_val]) < limit_per_kb:
                    by_kb[kb_name_val].append(r)
            # Interleave: round-robin by best score in each group
            grouped: list = []
            remaining = dict(by_kb)
            while remaining:
                exhausted = []
                for k in sorted(
                    remaining,
                    key=lambda k: remaining[k][0].get("score", 0) if remaining[k] else 0,
                    reverse=True,
                ):
                    if remaining[k]:
                        grouped.append(remaining[k].pop(0))
                    if not remaining[k]:
                        exhausted.append(k)
                for k in exhausted:
                    del remaining[k]
            results = grouped[:limit]

        # Apply field projection or strip body. One rule for every read
        # surface (#193).
        fields_list = parse_fields_param(fields)
        if fields_list:
            results = [project_fields(record, fields_list) for record in results]
        elif not include_body:
            for r in results:
                r.pop("body", None)

        resp_data = {"query": q, "count": len(results), "results": results}
        if warnings:
            resp_data["warnings"] = warnings
        neg = negotiate_response(request, resp_data, fields=fields_list)
        if neg is not None:
            return neg
        if fields_list:
            return JSONResponse(content=resp_data)
        return SearchResponse(
            query=q,
            count=len(results),
            results=[SearchResult(**r) for r in results],
            warnings=warnings or None,
        )
    except ValueError as e:
        # A caller-input problem (e.g. a bad `limit`) -- the caller's fault,
        # 400. `sqlite3.OperationalError` used to be caught here too, but
        # SearchService._db_search already reclassifies every DB-layer
        # OperationalError: a real parse error becomes QuerySyntaxError
        # (mapped to 400 QUERY_SYNTAX), and a genuine backend failure
        # ("database is locked", disk I/O, a missing table) becomes
        # StorageError (mapped to a logged 500). Catching the raw
        # OperationalError here too would undo that distinction the moment
        # it (incorrectly) reached this far (#414 round 2).
        raise HTTPException(status_code=400, detail={"code": "SEARCH_FAILED", "message": str(e)})

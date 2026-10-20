"""Cross-KB link discovery endpoints.

mcp-rest-tool-parity: ports kb_discover_neighbors and kb_batch_suggest
from MCP-only (pyrite/server/mcp_server.py) to REST. Both share the
same LinkDiscoveryService methods the MCP handlers already call --
no service-layer duplication.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from ...exceptions import ValidationError
from ...services.access_policy import KB, Action, ReadScope
from ...services.link_discovery_service import LinkDiscoveryService, _validate_mode
from ..api import get_link_discovery_service, limiter
from ..authz import authorize
from ..errors import error_response

router = APIRouter(tags=["Links"])


@router.get("/links/discover-neighbors")
@limiter.limit("60/minute")
def discover_neighbors(
    request: Request,
    entry_id: str = Query(..., description="Entry to find neighbors for"),
    kb: str = Query(..., description="KB the entry belongs to"),
    target_kb: str | None = Query(
        None, description="Limit candidates to this KB; omit to include the source KB and others"
    ),
    limit: int = Query(10, ge=1, le=100),
    mode: str = Query("hybrid", description="Search mode: keyword, semantic, hybrid"),
    exclude_linked: bool = Query(True, description="Exclude entries already linked"),
    include_empty: bool = Query(
        False,
        description="Also return entries with no body and no summary (left out by default)",
    ),
    svc: LinkDiscoveryService = Depends(get_link_discovery_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """Find related entries across all KBs, including the source KB by default.

    Excludes the source entry and, by default, already-linked entries.
    Entries with no body and no summary are left out unless `include_empty`.

    `authorize(Action.KB_READ, KB)` refuses any KB this call names that the
    caller may not read; `scope` keeps the *candidates* to readable KBs as well.
    With `target_kb` omitted the search spans the index, so the second half
    is what stops a readable KB's entry being matched against a private
    KB's entry and handed back as a suggestion (#186).
    """
    # An unknown mode answers 400, not the central handler's 422 for
    # ValidationError (maintainer decision 2026-10-17): REST validation
    # errors answer 400 per docs/json-contracts.md. error_response builds
    # the canonical VALIDATION_FAILED body; only the status is overridden.
    try:
        _validate_mode(mode)
    except ValidationError as exc:
        _, content = error_response(exc)
        return JSONResponse(status_code=400, content=content)

    candidates = svc.discover_neighbors(
        entry_id=entry_id,
        kb_name=kb,
        target_kb=target_kb,
        limit=limit,
        mode=mode,
        exclude_linked=exclude_linked,
        readable_kbs=scope.as_set(),
        include_empty=include_empty,
    )

    return {
        "entry_id": entry_id,
        "kb_name": kb,
        "count": len(candidates),
        "discoveries": candidates,
    }


@router.get("/links/batch-suggest")
@limiter.limit("20/minute")
def batch_suggest(
    request: Request,
    source_kb: str = Query(..., description="Source KB"),
    target_kb: str = Query(..., description="Target KB to compare against"),
    limit_per_entry: int = Query(3, ge=1, le=20),
    mode: str = Query("keyword", description="Search mode: keyword, semantic, hybrid"),
    exclude_linked: bool = Query(True, description="Exclude entries already linked"),
    include_empty: bool = Query(
        False,
        description="Also return entries with no body and no summary (left out by default)",
    ),
    svc: LinkDiscoveryService = Depends(get_link_discovery_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """Batch-compare two KBs to find potential cross-KB links.

    Targets with no body and no summary are left out unless `include_empty`.
    """
    # An unknown mode answers 400, not the central handler's 422 for
    # ValidationError (maintainer decision 2026-10-17): REST validation
    # errors answer 400 per docs/json-contracts.md. error_response builds
    # the canonical VALIDATION_FAILED body; only the status is overridden.
    try:
        _validate_mode(mode)
    except ValidationError as exc:
        _, content = error_response(exc)
        return JSONResponse(status_code=400, content=content)

    pairs = svc.batch_suggest(
        source_kb=source_kb,
        target_kb=target_kb,
        limit_per_entry=limit_per_entry,
        mode=mode,
        exclude_linked=exclude_linked,
        readable_kbs=scope.as_set(),
        include_empty=include_empty,
    )

    return {
        "source_kb": source_kb,
        "target_kb": target_kb,
        "count": len(pairs),
        "pairs": pairs,
    }

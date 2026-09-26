"""Block reference endpoints — list blocks for an entry."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ...services.access_policy import KB, UNSCOPED, Action
from ...services.block_service import BlockService
from ...services.kb_service import KBService
from ..api import get_block_service, get_kb_service, limiter, negotiate_response
from ..authz import authorize
from ..schemas import BlockListResponse, BlockResponse

router = APIRouter(tags=["Blocks"])


@router.get(
    "/entries/{entry_id}/blocks",
    response_model=BlockListResponse,
    dependencies=[Depends(authorize(Action.KB_READ, KB))],
)
@limiter.limit("100/minute")
def get_entry_blocks(
    request: Request,
    entry_id: str,
    kb: str = Query(..., description="KB name"),
    heading: str | None = Query(None, description="Filter by heading"),
    block_type: str | None = Query(None, description="Filter by block type"),
    block_id: str | None = Query(None, description="Filter by block ID"),
    svc: KBService = Depends(get_kb_service),
    block_svc: BlockService = Depends(get_block_service),
):
    """Get blocks extracted from an entry."""
    # Named KB, authorized by the route; used as an existence check only.
    entry = svc.get_entry(entry_id, kb_name=kb, readable_kbs=UNSCOPED)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Entry '{entry_id}' not found"},
        )

    block_rows = block_svc.list_blocks(
        entry_id, kb, heading=heading, block_type=block_type, block_id=block_id
    )

    blocks = [
        BlockResponse(
            block_id=b.block_id,
            heading=b.heading,
            content=b.content,
            position=b.position,
            block_type=b.block_type,
        )
        for b in block_rows
    ]

    resp_data = {
        "entry_id": entry_id,
        "kb_name": kb,
        "blocks": [b.model_dump() for b in blocks],
        "total": len(blocks),
    }
    neg = negotiate_response(request, resp_data)
    if neg is not None:
        return neg
    return BlockListResponse(entry_id=entry_id, kb_name=kb, blocks=blocks, total=len(blocks))

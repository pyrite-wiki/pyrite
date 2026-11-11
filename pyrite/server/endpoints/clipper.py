"""Web Clipper endpoint — POST /api/clip to fetch URL and create entry."""

import logging

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Request

from ...exceptions import ClipperBlockedHostError, ClipperTooLargeError, ValidationError
from ...services.clipper import ClipperService
from ...services.kb_service import KBService
from ..api import get_kb_service, limiter, requires_kb_tier
from ..schemas import ClipRequest, ClipResponse
from .write_refusal import refusal_http

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Clipper"])


@router.post(
    "/clip", response_model=ClipResponse, dependencies=[Depends(requires_kb_tier("write"))]
)
@limiter.limit("20/minute")
async def clip_url(
    request: Request,
    req: ClipRequest,
    svc: KBService = Depends(get_kb_service),
):
    """Clip a URL: fetch, convert to Markdown, and create an entry.

    `requires_kb_tier("write")` has already checked the caller's role on
    `req.kb` -- and answered a KB they cannot read with the same 404 as the
    one below, so this check, running after authorization, is no oracle.
    """
    if not svc.get_kb(req.kb):
        raise HTTPException(
            status_code=404,
            detail={"code": "KB_NOT_FOUND", "message": f"KB '{req.kb}' not found"},
        )

    # Validate URL
    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_URL", "message": "URL must start with http:// or https://"},
        )

    # Clip the URL
    clipper = ClipperService()
    try:
        result = await clipper.clip_url(req.url, title=req.title)
    except ClipperBlockedHostError as e:
        # SSRF defense: blocked host/scheme. A stable code lets callers tell a
        # policy rejection from an upstream failure. The text names no
        # address: what a name resolved to is the operator's, so it goes to
        # the log only.
        logger.warning("Clip of %r refused: %s", req.url, e)
        raise HTTPException(
            status_code=400,
            detail={
                "code": e.error_code,
                "message": "That address cannot be clipped: only public http(s) pages are allowed.",
            },
        ) from e
    except ClipperTooLargeError as e:
        raise HTTPException(
            status_code=413,
            detail={"code": e.error_code, "message": str(e)},
        ) from e
    except Exception as e:
        # The exception's text can quote internal addresses, paths or parser
        # state: it goes to the log, the caller gets Pyrite's own message.
        logger.warning("Clip of %r failed", req.url, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "CLIP_FAILED",
                "message": "The page could not be fetched. Check the URL and try again.",
            },
        ) from e

    # Build entry body with source attribution
    source_header = f"> Clipped from [{result.title}]({result.source_url})\n\n"
    body = source_header + result.body

    # Create the entry through the write pipeline: the declared-type refusal
    # (with the caller's override), schema validation and the exists check are
    # the service's, as for POST /entries (#378).
    spec = {
        "entry_type": req.entry_type or "note",
        "title": result.title,
        "body": body,
        "tags": req.tags or [],
        "metadata": {"source_url": result.source_url, "clipped": True},
    }

    # The write touches files, the index and git: off the event loop.
    def _create():
        return svc.create(req.kb, spec, allow_undeclared=req.allow_undeclared).entry

    try:
        entry = await anyio.to_thread.run_sync(_create)
    except ValidationError as e:
        raise refusal_http(e) from e
    except Exception as e:
        logger.warning("Clip of %r: entry creation failed", req.url, exc_info=True)
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CREATE_FAILED",
                "message": "The page was fetched but the entry could not be created.",
            },
        ) from e

    # Broadcast WebSocket event
    from ..websocket import broadcast_event

    broadcast_event("entry_created", entry_id=entry.id, kb_name=req.kb)

    return ClipResponse(
        created=True,
        id=entry.id,
        kb_name=req.kb,
        title=result.title,
        source_url=result.source_url,
    )

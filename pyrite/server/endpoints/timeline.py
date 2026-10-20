"""Timeline endpoint."""

from fastapi import APIRouter, Depends, Query, Request

from ...services.access_policy import KB, Action, ReadScope
from ...services.kb_service import KBService
from ..api import (
    get_kb_service,
    limiter,
    negotiate_response,
)
from ..authz import authorize
from ..schemas import TimelineEvent, TimelineResponse

router = APIRouter(tags=["Timeline"])


@router.get("/timeline", response_model=TimelineResponse)
@limiter.limit("100/minute")
def get_timeline(
    request: Request,
    date_from: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    min_importance: int | None = Query(None, ge=1, le=10),
    kb: str | None = Query(None),
    sort: str = Query("asc", pattern=r"^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=500),
    svc: KBService = Depends(get_kb_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """Get timeline events from the KBs the caller may read.

    The readable set goes into the query, not into a list comprehension
    afterwards: ``count`` is the length of the returned events and
    ``limit`` is applied by SQL, so post-filtering would both under-fill
    the page and let the count reveal how many private events matched.
    """
    results = svc.get_timeline(
        date_from=date_from,
        date_to=date_to,
        min_importance=min_importance or 1,
        kb_name=kb,
        limit=limit,
        sort_order=sort,
        kb_names=None if kb else scope.as_set(),
    )

    events = [
        TimelineEvent(
            id=r["id"],
            kb_name=r["kb_name"],
            date=r["date"],
            title=r["title"],
            importance=r.get("importance", 5),
            tags=r.get("tags", []),
        )
        for r in results
    ]

    resp_data = {
        "count": len(results),
        "date_from": date_from,
        "date_to": date_to,
        "events": [e.model_dump() for e in events],
    }
    neg = negotiate_response(request, resp_data)
    if neg is not None:
        return neg
    return TimelineResponse(count=len(results), date_from=date_from, date_to=date_to, events=events)

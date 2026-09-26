"""Daily notes endpoint -- get or auto-create daily note for a given date."""

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ...exceptions import KBNotFoundError
from ...services.access_policy import KB, AccessPolicy, Action, ReadScope
from ...services.kb_service import KBService
from ..api import (
    TIER_LEVELS,
    KBRoleResolver,
    get_access_policy,
    get_kb_role_resolver,
    get_kb_service,
    limiter,
    requires_kb_tier,
)
from ..authz import authorize, read_scope
from ..schemas import DailyDatesResponse, EntryResponse

router = APIRouter(tags=["Daily Notes"])


def _daily_entry_id(date_str: str) -> str:
    """Generate a consistent entry ID for a daily note."""
    return f"daily-{date_str}"


def _default_daily_body(date_str: str) -> str:
    """Default daily note body when no template exists."""
    d = date.fromisoformat(date_str)
    return f"# {d.strftime('%A, %B %-d, %Y')}\n\n"


@router.get(
    "/daily/dates",
    response_model=DailyDatesResponse,
    dependencies=[Depends(authorize(Action.KB_READ, KB))],
)
@limiter.limit("60/minute")
def list_daily_dates(
    request: Request,
    kb: str = Query(..., description="KB name"),
    month: str | None = Query(
        None,
        pattern=r"^\d{4}-\d{2}$",
        description="Filter by month (YYYY-MM). Defaults to current month.",
    ),
    svc: KBService = Depends(get_kb_service),
):
    """List dates that have daily notes for calendar display."""
    if not svc.get_kb(kb):
        raise HTTPException(
            status_code=404,
            detail={"code": "KB_NOT_FOUND", "message": f"KB '{kb}' not found"},
        )

    if month is None:
        month = datetime.now(UTC).strftime("%Y-%m")

    dates = svc.list_daily_dates(kb, month)
    return DailyDatesResponse(dates=dates)


def _load_existing_daily_note(
    svc: KBService, entry_id: str, kb: str, readable_kbs: set[str] | None
) -> EntryResponse | None:
    """Return the daily note if it's already indexed or present on disk
    (indexing it first in the latter case). None if it doesn't exist yet.

    Its backlinks may come from any KB, so they are bounded by the caller's
    readable set (P-R4)."""
    existing = svc.get_entry(entry_id, kb, readable_kbs=readable_kbs)
    if not existing:
        loaded = svc.load_entry_from_disk(entry_id, kb)
        if loaded:
            svc.index_entry_from_disk(loaded, kb)
            existing = svc.get_entry(entry_id, kb, readable_kbs=readable_kbs)

    if not existing:
        return None
    existing.setdefault("sources", [])
    existing.setdefault("tags", [])
    existing.setdefault("outlinks", [])
    existing.setdefault("backlinks", [])
    return EntryResponse(**existing)


def _create_daily_note(
    svc: KBService, entry_id: str, kb: str, date_str: str, readable_kbs: set[str] | None
) -> EntryResponse:
    """Create a new daily note from the ``daily`` template (if present) or
    a sensible default, save and index it, and return the result."""
    parsed_date = date.fromisoformat(date_str)
    title = f"Daily Note - {parsed_date.strftime('%Y-%m-%d')}"
    body = _default_daily_body(date_str)

    try:
        from ...services.template_service import TemplateService

        tpl_svc = TemplateService(svc.config)
        rendered = tpl_svc.render_template(
            kb, "daily", variables={"title": title, "date": date_str}
        )
        body = rendered.get("body", body)
        fm_tags = rendered.get("frontmatter", {}).get("tags", [])
    except (FileNotFoundError, KeyError, KBNotFoundError):
        fm_tags = ["daily"]

    try:
        svc.create_entry(
            kb,
            entry_id,
            title,
            "note",
            body,
            tags=fm_tags if fm_tags else ["daily"],
        )
    except ValueError:
        now = datetime.now(UTC)
        return EntryResponse(
            id=entry_id,
            kb_name=kb,
            entry_type="note",
            title=title,
            body=body,
            tags=fm_tags if fm_tags else ["daily"],
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )

    # Links made to this date before the note existed now resolve to it;
    # only those from readable KBs are the caller's to see (P-R4).
    result = svc.get_entry(entry_id, kb, readable_kbs=readable_kbs)
    if result:
        result.setdefault("sources", [])
        result.setdefault("tags", [])
        result.setdefault("outlinks", [])
        result.setdefault("backlinks", [])
        return EntryResponse(**result)

    now = datetime.now(UTC)
    return EntryResponse(
        id=entry_id,
        kb_name=kb,
        entry_type="note",
        title=title,
        body=body,
        tags=fm_tags if fm_tags else ["daily"],
        file_path="",
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
    )


def _validate_date(date_str: str) -> None:
    try:
        date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_DATE",
                "message": f"Invalid date format: '{date_str}'. Expected YYYY-MM-DD.",
            },
        )


@router.get(
    "/daily/{date_str}",
    response_model=EntryResponse,
)
@limiter.limit("60/minute")
async def get_or_create_daily_note(
    request: Request,
    date_str: str,
    kb: str = Query(..., description="KB name"),
    svc: KBService = Depends(get_kb_service),
    role_of: KBRoleResolver = Depends(get_kb_role_resolver),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """Get a daily note for the given date, auto-creating it only for a
    write-tier caller.

    If a daily note already exists (entry id = ``daily-YYYY-MM-DD``), it is
    returned regardless of tier. Otherwise, a write-tier caller gets a new
    note created from the ``daily`` template (if present in the KB's
    ``_templates/`` directory) or from a sensible default. A caller
    without write access on this KB never triggers creation -- merely
    viewing/navigating must not have write side effects
    (web-daily-notes-view-side-effect) -- and gets 404 instead; the
    frontend uses `POST /daily/{date_str}` to create explicitly.
    """
    _validate_date(date_str)

    if not svc.get_kb(kb):
        raise HTTPException(
            status_code=404,
            detail={"code": "KB_NOT_FOUND", "message": f"KB '{kb}' not found"},
        )

    entry_id = _daily_entry_id(date_str)
    readable = scope.as_set()

    existing = _load_existing_daily_note(svc, entry_id, kb, readable)
    if existing:
        return existing

    effective_role = await role_of(kb)
    if effective_role is None or TIER_LEVELS.get(effective_role, -1) < TIER_LEVELS.get("write", 99):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": f"No daily note for '{date_str}' in KB '{kb}'",
            },
        )

    return _create_daily_note(svc, entry_id, kb, date_str, readable)


@router.post(
    "/daily/{date_str}",
    response_model=EntryResponse,
    dependencies=[Depends(requires_kb_tier("write"))],
)
@limiter.limit("60/minute")
def create_daily_note(
    request: Request,
    date_str: str,
    kb: str = Query(..., description="KB name"),
    svc: KBService = Depends(get_kb_service),
    policy: AccessPolicy = Depends(get_access_policy),
):
    """Explicitly create (or return, if already existing) a daily note.

    Write-tier only. This is the action a "Start today's note" button
    calls -- unlike the GET endpoint, this is never triggered by mere
    navigation/viewing.

    The write is decided by `requires_kb_tier("write")` (theme 3b); the
    note's links are bounded by the caller's `ReadScope`, asked of the
    policy here because this route has no read declaration to take it from.
    """
    readable = read_scope(request, policy).as_set()
    _validate_date(date_str)

    if not svc.get_kb(kb):
        raise HTTPException(
            status_code=404,
            detail={"code": "KB_NOT_FOUND", "message": f"KB '{kb}' not found"},
        )

    entry_id = _daily_entry_id(date_str)

    existing = _load_existing_daily_note(svc, entry_id, kb, readable)
    if existing:
        return existing

    return _create_daily_note(svc, entry_id, kb, date_str, readable)

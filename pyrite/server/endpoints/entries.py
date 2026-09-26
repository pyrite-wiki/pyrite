"""Entry CRUD endpoints including wikilink resolution."""

import io
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from ...config import PyriteConfig
from ...exceptions import (
    EntryNotFoundError,
    KBNotFoundError,
    KBReadOnlyError,
    PyriteError,
    ValidationError,
)
from ...services.access_policy import KB, Action, AnyKB, ReadScope
from ...services.block_service import BlockService
from ...services.kb_service import KBService
from ...services.read_shaping import parse_fields_param, project_fields
from ..api import (
    get_block_service,
    get_config,
    get_kb_service,
    get_worktree_resolver,
    limiter,
    negotiate_response,
    requires_kb_tier,
)
from ..authz import authorize
from ..schemas import (
    CreateEntryRequest,
    CreateResponse,
    DeleteResponse,
    EntryListResponse,
    EntryResponse,
    EntryTitle,
    EntryTitlesResponse,
    EntryTypesResponse,
    PatchEntryRequest,
    ResolveBatchRequest,
    ResolveBatchResponse,
    ResolveResponse,
    UpdateEntryRequest,
    UpdateResponse,
    WantedPage,
    WantedPagesResponse,
)
from .write_refusal import refusal_http, refuses_truncated_body

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Entries"])


@router.get("/entries", response_model=EntryListResponse)
@limiter.limit("100/minute")
def list_entries(
    request: Request,
    kb: str | None = Query(None, description="Filter by KB name"),
    entry_type: str | None = Query(None, description="Filter by entry type"),
    tag: str | None = Query(None, description="Filter by tag"),
    status: str | None = Query(None, description="Filter by status"),
    min_importance: int | None = Query(None, ge=1, le=10, description="Minimum importance (1-10)"),
    sort_by: str = Query("updated_at", description="Sort column"),
    sort_order: str = Query("desc", description="Sort direction: asc or desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    svc: KBService = Depends(get_kb_service),
    resolver=Depends(get_worktree_resolver),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """List entries with pagination, limited to KBs the caller may read."""
    kb_names = None if kb else scope.as_set()
    # Use overlay so user sees their own edits in lists
    auth_user = getattr(request.state, "auth_user", None)
    if auth_user and kb:
        try:
            svc = resolver.get_read_service(kb, auth_user)
        except ValueError:
            pass  # KB not in a git repo — fall back to main

    results = svc.list_entries(
        kb_name=kb,
        kb_names=kb_names,
        entry_type=entry_type,
        tag=tag,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
        status=status,
        min_importance=min_importance,
    )
    total = svc.count_entries(
        kb_name=kb,
        kb_names=kb_names,
        entry_type=entry_type,
        tag=tag,
        status=status,
        min_importance=min_importance,
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
        "limit": limit,
        "offset": offset,
    }
    neg = negotiate_response(request, resp_data)
    if neg is not None:
        return neg
    return EntryListResponse(entries=entries, total=total, limit=limit, offset=offset)


@router.get("/entries/types", response_model=EntryTypesResponse)
@limiter.limit("100/minute")
def list_entry_types(
    request: Request,
    kb: str | None = Query(None, description="Filter by KB name"),
    svc: KBService = Depends(get_kb_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """Get distinct entry types, limited to KBs the caller may read."""
    types = svc.get_distinct_types(kb_name=kb, kb_names=None if kb else scope.as_set())
    return EntryTypesResponse(types=types)


@router.get("/entries/type-schemas", dependencies=[Depends(authorize(Action.KB_READ, KB))])
@limiter.limit("100/minute")
def list_type_schemas(
    request: Request,
    kb: str | None = Query(None, description="KB name to get type schemas for"),
    config: "PyriteConfig" = Depends(get_config),
):
    """Return available entry types with field schemas for a KB.

    Merges type information from three sources:
    1. KB-level kb.yaml type definitions (highest priority)
    2. Plugin-registered entry types and presets
    3. Core built-in types (fallback)
    """
    from ...schema.core_types import CORE_TYPE_METADATA, CORE_TYPES

    result: dict[str, dict] = {}
    declared: list[str] = []

    # Layer 1: Core types as baseline
    for type_name, type_info in CORE_TYPES.items():
        if type_name == "collection":
            continue  # internal type, skip
        fields = {}
        meta = CORE_TYPE_METADATA.get(type_name, {})
        field_descs = meta.get("field_descriptions", {})
        for fname, ftype in type_info.get("fields", {}).items():
            if fname in ("tags", "links"):
                continue
            fields[fname] = {
                "type": _python_type_to_field_type(ftype),
                "description": field_descs.get(fname, ""),
            }
        result[type_name] = {
            "description": type_info.get("description", ""),
            "fields": fields,
            "subdirectory": type_info.get("subdirectory", ""),
        }

    # Layer 2: Plugin entry types and presets
    try:
        from ...plugins import get_registry

        registry = get_registry()

        # Plugin presets — rich type definitions
        for _preset_name, preset_data in registry.get_all_kb_presets().items():
            for type_name, type_info in preset_data.get("types", {}).items():
                if type_name not in result:
                    result[type_name] = {"description": "", "fields": {}, "subdirectory": ""}
                result[type_name]["description"] = type_info.get(
                    "description", result[type_name]["description"]
                )
                result[type_name]["subdirectory"] = type_info.get(
                    "subdirectory", result[type_name]["subdirectory"]
                )
                # Add optional fields as field definitions
                for fname in type_info.get("optional", []):
                    if fname not in result[type_name]["fields"] and fname not in (
                        "importance",
                        "tags",
                        "links",
                    ):
                        result[type_name]["fields"][fname] = {
                            "type": _guess_field_type(fname),
                            "description": "",
                        }

        # Plugin type metadata (field_descriptions, ai_instructions)
        for type_name, meta in registry.get_all_type_metadata().items():
            if type_name in result:
                for fname, desc in meta.get("field_descriptions", {}).items():
                    if fname in result[type_name]["fields"]:
                        result[type_name]["fields"][fname]["description"] = desc
    except Exception:
        logger.debug("Failed to load plugin type metadata", exc_info=True)

    # Layer 3: KB-level schema overrides (highest priority)
    if kb:
        kb_config = config.get_kb(kb)
        if kb_config:
            schema = kb_config.kb_schema
            # The same vocabulary `_refuse_undeclared_type` (#378) checks
            # against: non-empty only when the KB's kb.yaml declares any type.
            declared = sorted(schema.types.keys()) if schema and schema.types else []
            for type_name, ts in schema.types.items():
                if type_name not in result:
                    result[type_name] = {"description": "", "fields": {}, "subdirectory": ""}
                if ts.description:
                    result[type_name]["description"] = ts.description
                if ts.subdirectory:
                    result[type_name]["subdirectory"] = ts.subdirectory
                if ts.file_pattern:
                    result[type_name]["file_pattern"] = ts.file_pattern
                # Rich field schemas from kb.yaml
                for fname, fs in ts.fields.items():
                    result[type_name]["fields"][fname] = fs.to_dict()
                    if fs.description:
                        result[type_name]["fields"][fname]["description"] = fs.description
                # Optional fields listed in kb.yaml
                for fname in ts.optional:
                    if fname not in result[type_name]["fields"] and fname not in (
                        "importance",
                        "tags",
                        "links",
                    ):
                        result[type_name]["fields"][fname] = {
                            "type": _guess_field_type(fname),
                            "description": ts.field_descriptions.get(fname, ""),
                        }

    return {"types": result, "declared": declared}


def _python_type_to_field_type(type_str: str) -> str:
    """Map Python type annotations to field schema types."""
    if "list" in type_str:
        return "list"
    if type_str in ("int", "float"):
        return "number"
    if type_str == "bool":
        return "checkbox"
    return "text"


def _guess_field_type(field_name: str) -> str:
    """Guess field type from the field name convention."""
    if field_name in (
        "date",
        "opened_date",
        "closed_date",
        "acquisition_date",
        "obtained_date",
        "founded",
    ):
        return "date"
    if field_name in ("amount", "value", "importance"):
        return "number"
    if field_name in ("actors", "parties", "affiliations", "source_refs", "participants"):
        return "list"
    if field_name in ("url",):
        return "text"
    return "text"


@router.get(
    "/entries/titles",
    response_model=EntryTitlesResponse,
)
@limiter.limit("100/minute")
def list_entry_titles(
    request: Request,
    kb: str | None = Query(None, description="Filter by KB name"),
    q: str | None = Query(None, description="Filter titles by search string"),
    limit: int = Query(500, ge=1, le=5000),
    svc: KBService = Depends(get_kb_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """Lightweight listing of entry IDs and titles for wikilink autocomplete."""
    rows = svc.list_entry_titles(kb_name=kb, query=q, limit=limit, readable_kbs=scope.as_set())
    entries = []
    for r in rows:
        aliases_raw = r.get("aliases")
        if isinstance(aliases_raw, str):
            import json

            try:
                aliases = json.loads(aliases_raw) or []
            except (json.JSONDecodeError, TypeError):
                aliases = []
        elif isinstance(aliases_raw, list):
            aliases = aliases_raw
        else:
            aliases = []
        entries.append(
            EntryTitle(
                id=r["id"],
                title=r["title"],
                kb_name=r["kb_name"],
                entry_type=r["entry_type"],
                aliases=aliases,
            )
        )
    return EntryTitlesResponse(entries=entries)


@router.post(
    "/entries/resolve-batch",
    response_model=ResolveBatchResponse,
)
@limiter.limit("100/minute")
def resolve_batch(
    request: Request,
    req: ResolveBatchRequest,
    svc: KBService = Depends(get_kb_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """Batch-resolve wikilink targets. Returns which targets exist.

    Scoped like `resolve`: a `kb:` prefix inside a target is a named KB too.
    """
    resolved = svc.resolve_batch(req.targets, kb_name=req.kb, readable_kbs=scope.as_set())
    return ResolveBatchResponse(resolved=resolved)


@router.post("/entries/batch")
@limiter.limit("60/minute")
def batch_read_entries(
    request: Request,
    body: dict,
    svc: KBService = Depends(get_kb_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, AnyKB)),
):
    """Batch-read multiple entries in one call."""
    entries_spec = body.get("entries", [])
    fields_param = body.get("fields")

    if not isinstance(entries_spec, list):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "VALIDATION_FAILED",
                "message": "entries must be an array of {entry_id, kb_name} objects",
            },
        )
    if not entries_spec:
        raise HTTPException(
            status_code=400,
            detail={"code": "VALIDATION_FAILED", "message": "entries array is required"},
        )
    if len(entries_spec) > 50:
        raise HTTPException(
            status_code=400,
            detail={"code": "VALIDATION_FAILED", "message": "Maximum 50 entries per call"},
        )

    # REST parity with the MCP kb_batch_read contract (#134): a malformed spec is
    # a client error with a stable code, not a 500, and the identity pair is
    # always kept so `found` cannot contradict `not_found` when `fields` omits it.
    for index, spec in enumerate(entries_spec):
        if (
            not isinstance(spec, dict)
            or not isinstance(spec.get("entry_id"), str)
            or not spec["entry_id"]
            or not isinstance(spec.get("kb_name"), str)
            or not spec["kb_name"]
        ):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "VALIDATION_FAILED",
                    "message": (
                        f"entries[{index}] must be an object with non-empty string "
                        "entry_id and kb_name"
                    ),
                },
            )

    # `fields`, like `entries`, is caller-supplied: a wrong type must be the same
    # structured 400, not a 500 from the star-unpack below (#134 review).
    if fields_param is not None and (
        not isinstance(fields_param, list)
        or any(not isinstance(field, str) for field in fields_param)
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "VALIDATION_FAILED",
                "message": "fields must be an array of strings",
            },
        )

    ids = [(e["entry_id"], e["kb_name"]) for e in entries_spec]
    if not scope.unscoped:
        # Items in KBs the caller may not read are reported as not found.
        ids = [(eid, kb) for eid, kb in ids if scope.permits(kb)]
    results = svc.get_entries(ids)

    if fields_param:
        # Same rule as every other read surface (#193). found_ids below reads
        # the identity pair, and dropping it is what made `found` and
        # `not_found` disagree (#134).
        results = [project_fields(record, fields_param) for record in results]

    found_ids = {(r["id"], r["kb_name"]) for r in results}
    requested = [(e["entry_id"], e["kb_name"]) for e in entries_spec]
    not_found = [
        {"entry_id": eid, "kb_name": kb} for eid, kb in requested if (eid, kb) not in found_ids
    ]

    resp_data = {"entries": results, "found": len(results), "not_found": not_found}
    neg = negotiate_response(request, resp_data)
    if neg is not None:
        return neg
    return resp_data


@router.get(
    "/entries/wanted",
    response_model=WantedPagesResponse,
)
@limiter.limit("100/minute")
def list_wanted_pages(
    request: Request,
    kb: str | None = Query(None, description="Filter by KB name"),
    limit: int = Query(100, ge=1, le=500),
    svc: KBService = Depends(get_kb_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """List link targets that don't exist as entries (wanted pages)."""
    pages = svc.get_wanted_pages(kb_name=kb, limit=limit, readable_kbs=scope.as_set())
    result = []
    for p in pages:
        refs = p.get("referenced_by", "") or ""
        ref_list = [r for r in refs.split(",") if r] if isinstance(refs, str) else []
        result.append(
            WantedPage(
                target_id=p["target_id"],
                target_kb=p["target_kb"],
                ref_count=p["ref_count"],
                referenced_by=ref_list,
            )
        )
    return WantedPagesResponse(count=len(result), pages=result)


@router.get(
    "/entries/resolve",
    response_model=ResolveResponse,
)
@limiter.limit("100/minute")
def resolve_entry(
    request: Request,
    target: str = Query(..., description="Entry ID or title to resolve"),
    kb: str | None = Query(None, description="Filter by KB name"),
    svc: KBService = Depends(get_kb_service),
    block_svc: BlockService = Depends(get_block_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """Resolve a wikilink target to an entry. Tries exact ID match first, then title match.
    Supports fragment syntax: target#heading or target^block-id.

    Scoped: with no `kb` the lookup spans only readable KBs, and a `kb:` or
    shortname prefix inside the target is authorized as a named KB -- an
    unreadable one is treated as an unknown prefix, as a missing KB is."""
    # Parse fragment from target
    heading = None
    block_id = None
    entry_target = target

    if "#" in target:
        entry_target, heading = target.split("#", 1)
    elif "^" in target:
        entry_target, block_id = target.split("^", 1)

    result = svc.resolve_entry(entry_target, kb_name=kb, readable_kbs=scope.as_set())

    if result:
        block_content = None
        # If fragment specified, try to find matching block
        if heading or block_id:
            block = block_svc.find_block(
                result["id"], result["kb_name"], heading=heading, block_id=block_id
            )
            if block:
                block_content = block.content

        return ResolveResponse(
            resolved=True,
            entry=EntryTitle(
                id=result["id"],
                title=result["title"],
                kb_name=result["kb_name"],
                entry_type=result["entry_type"],
            ),
            heading=heading,
            block_id=block_id,
            block_content=block_content,
        )
    return ResolveResponse(resolved=False, entry=None)


# =============================================================================
# Import / Export (must be before /entries/{entry_id} to avoid route conflicts)
# =============================================================================


@router.get("/entries/export")
@limiter.limit("30/minute")
def export_entries(
    request: Request,
    kb: str = Query(..., description="KB to export"),
    format: str = Query("json", description="Export format: json, markdown, csv"),
    entry_type: str | None = Query(None, description="Filter by entry type"),
    tag: str | None = Query(None, description="Filter by tag"),
    limit: int = Query(10000, ge=1, le=50000),
    svc: KBService = Depends(get_kb_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """Export entries as JSON, Markdown, or CSV."""
    from ...formats import get_format_registry

    if not svc.get_kb(kb):
        raise HTTPException(
            status_code=404,
            detail={"code": "KB_NOT_FOUND", "message": f"KB '{kb}' not found"},
        )

    entries = svc.list_entries(kb_name=kb, entry_type=entry_type, limit=limit, offset=0)

    # For full export, load bodies from disk
    full_entries = []
    for e in entries:
        # Each entry carries its links: bounded by the caller's scope (P-R4).
        full = svc.get_entry(e["id"], kb_name=kb, readable_kbs=scope.as_set())
        if full:
            # Apply tag filter if specified
            if tag and tag not in full.get("tags", []):
                continue
            full_entries.append(full)

    registry = get_format_registry()
    fmt_spec = registry.get(format)
    if not fmt_spec:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "UNSUPPORTED_FORMAT",
                "message": f"Unsupported format: {format}",
            },
        )

    data = {"entries": full_entries, "total": len(full_entries)}
    content = fmt_spec.serializer(data)

    ext = fmt_spec.file_extension
    filename = f"{kb}-export.{ext}"

    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type=fmt_spec.media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/entries/import", dependencies=[Depends(requires_kb_tier("write"))])
@limiter.limit("30/minute")
async def import_entries(
    request: Request,
    file: UploadFile = File(...),
    kb: str = Query(..., description="Target KB name"),
    format: str = Query(
        None, description="Format: json, markdown, csv (auto-detected from extension if omitted)"
    ),
    allow_undeclared: bool = Query(
        False, description="Allow entry types the KB's kb.yaml does not declare"
    ),
    svc: KBService = Depends(get_kb_service),
):
    """Import entries from an uploaded file."""
    from ...formats.importers import get_importer_registry

    if not svc.get_kb(kb):
        raise HTTPException(
            status_code=404,
            detail={"code": "KB_NOT_FOUND", "message": f"KB '{kb}' not found"},
        )

    # Auto-detect format from filename
    fmt = format
    if not fmt and file.filename:
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        fmt = {"json": "json", "md": "markdown", "csv": "csv"}.get(ext)
    if not fmt:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "FORMAT_REQUIRED",
                "message": "Could not detect format. Specify format=json|markdown|csv",
            },
        )

    registry = get_importer_registry()
    importer = registry.get(fmt)
    if not importer:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "UNSUPPORTED_FORMAT",
                "message": f"Unsupported format: {fmt}. Available: {registry.available_formats()}",
            },
        )

    content = await file.read()
    try:
        parsed = importer(content)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"code": "PARSE_ERROR", "message": f"Failed to parse file: {e}"},
        )

    # Every per-record decision (the ADR-0034 truncated-body refusal, the
    # declared type, an existing id, schema and plugin validation) is the
    # service's write pipeline, the same one POST /entries uses (#378).
    results = svc.bulk_create_entries(kb, parsed, allow_undeclared=allow_undeclared)

    created = []
    errors = []
    for entry_data, r in zip(parsed, results, strict=True):
        title = entry_data.get("title", "?") if isinstance(entry_data, dict) else "?"
        if r.get("created"):
            created.append({"id": r["entry_id"], "title": title})
        else:
            errors.append({"title": title, "error": r["error"], "error_code": r["error_code"]})

    return {
        "imported": len(created),
        "errors": len(errors),
        "entries": created,
        "error_details": errors,
    }


# =============================================================================
# Entry CRUD (parametric routes must come after static routes)
# =============================================================================


@router.get("/entries/{entry_id}", response_model=EntryResponse)
@limiter.limit("100/minute")
def get_entry(
    request: Request,
    entry_id: str,
    kb: str | None = Query(None, description="KB name (optional)"),
    with_links: bool = Query(False, description="Include links"),
    fields: str | None = Query(
        None,
        description=(
            "Comma-separated fields to return. "
            "id and kb_name are always included; when fields is set the response is a "
            "projection of the stored entry, not EntryResponse."
        ),
    ),
    svc: KBService = Depends(get_kb_service),
    resolver=Depends(get_worktree_resolver),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """Get entry by ID."""
    # Use overlay so user sees their own edits
    auth_user = getattr(request.state, "auth_user", None)
    if auth_user and kb:
        try:
            svc = resolver.get_read_service(kb, auth_user)
        except ValueError:
            pass  # KB not in a git repo — fall back to main

    # The lookup and its links are bounded by the caller's scope: without
    # `kb` it walks only readable KBs, so an entry in a private one answers
    # NOT_FOUND exactly as a missing id does and cannot shadow a readable
    # twin (P-R5); its outlinks and backlinks cover readable KBs only (P-R4).
    readable = scope.as_set()
    if with_links:
        # get_entry already includes outlinks/backlinks
        result = svc.get_entry(entry_id, kb_name=kb, readable_kbs=readable)
    else:
        # For non-link requests, get entry without links
        result = svc.get_entry(entry_id, kb_name=kb, readable_kbs=readable)
        if result:
            result.setdefault("outlinks", [])
            result.setdefault("backlinks", [])

    if not result:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": f"Entry '{entry_id}' not found",
                "hint": f"Search: /api/search?q={entry_id}",
            },
        )

    result.setdefault("sources", [])
    result.setdefault("tags", [])

    # Apply field projection. One rule for every read surface (#193): the
    # identity pair always survives, and keys the entry does not have are not
    # invented.
    fields_list = parse_fields_param(fields)
    if fields_list:
        result = project_fields(result, fields_list)
        neg = negotiate_response(request, result)
        if neg is not None:
            return neg
        return JSONResponse(content=result)

    neg = negotiate_response(request, result)
    if neg is not None:
        return neg
    return EntryResponse(**result)


@router.post(
    "/entries",
    response_model=CreateResponse,
    dependencies=[Depends(requires_kb_tier("write")), Depends(refuses_truncated_body)],
)
@limiter.limit("30/minute")
def create_entry(
    request: Request,
    req: CreateEntryRequest,
    svc: KBService = Depends(get_kb_service),
    resolver=Depends(get_worktree_resolver),
):
    """Create a new entry."""
    # Route writes through worktree for authenticated users
    auth_user = getattr(request.state, "auth_user", None)
    if auth_user and auth_user.get("role") != "admin":
        try:
            svc = resolver.get_write_service(req.kb, auth_user)
        except ValueError:
            pass  # KB not in a git repo — fall back to main

    if not svc.get_kb(req.kb):
        raise HTTPException(
            status_code=404,
            detail={"code": "KB_NOT_FOUND", "message": f"KB '{req.kb}' not found"},
        )

    # Map the request to a spec; None means "not given", so factory defaults
    # apply. (A truncated body was refused on the raw request, above.)
    spec = {
        k: v
        for k, v in {
            "entry_type": req.entry_type or "note",
            "title": req.title,
            "body": req.body,
            "date": req.date,
            "importance": req.importance,
            "participants": req.participants,
            "role": req.role,
            "tags": req.tags,
            "metadata": req.metadata,
        }.items()
        if v is not None
    }

    try:
        written = svc.create(req.kb, spec, allow_undeclared=req.allow_undeclared)
    except (KBNotFoundError, EntryNotFoundError) as e:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": str(e)})
    except KBReadOnlyError as e:
        raise HTTPException(status_code=403, detail={"code": "READ_ONLY", "message": str(e)})
    except ValidationError as e:
        raise refusal_http(e)
    except (PyriteError, ValueError) as e:
        raise HTTPException(status_code=400, detail={"code": "CREATE_FAILED", "message": str(e)})
    entry = written.entry

    # Broadcast WebSocket event
    from ..websocket import broadcast_event

    broadcast_event("entry_created", entry_id=entry.id, kb_name=req.kb)

    return CreateResponse(
        created=True, id=entry.id, kb_name=req.kb, file_path="", warnings=written.warnings
    )


@router.put(
    "/entries/{entry_id}",
    response_model=UpdateResponse,
    dependencies=[Depends(requires_kb_tier("write")), Depends(refuses_truncated_body)],
)
@limiter.limit("30/minute")
def update_entry(
    request: Request,
    entry_id: str,
    req: UpdateEntryRequest,
    svc: KBService = Depends(get_kb_service),
    resolver=Depends(get_worktree_resolver),
):
    """Update an existing entry."""
    # Route writes through worktree for authenticated users
    auth_user = getattr(request.state, "auth_user", None)
    if auth_user and auth_user.get("role") != "admin":
        try:
            svc = resolver.get_write_service(req.kb, auth_user)
        except ValueError:
            pass  # KB not in a git repo — fall back to main

    updates = {}
    if req.title is not None:
        updates["title"] = req.title
    if req.body is not None:
        updates["body"] = req.body
    if req.importance is not None:
        updates["importance"] = req.importance
    if req.tags is not None:
        updates["tags"] = req.tags
    if req.metadata is not None:
        updates["metadata"] = req.metadata

    try:
        written = svc.update(entry_id, req.kb, updates)
    except (KBNotFoundError, EntryNotFoundError) as e:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": str(e)})
    except KBReadOnlyError as e:
        raise HTTPException(status_code=403, detail={"code": "READ_ONLY", "message": str(e)})
    except ValidationError as e:
        raise refusal_http(e)
    except (PyriteError, ValueError) as e:
        raise HTTPException(status_code=400, detail={"code": "UPDATE_FAILED", "message": str(e)})

    # Broadcast WebSocket event
    from ..websocket import broadcast_event

    broadcast_event("entry_updated", entry_id=entry_id, kb_name=req.kb)

    return UpdateResponse(updated=True, id=entry_id, warnings=written.warnings)


@router.patch(
    "/entries/{entry_id}",
    response_model=UpdateResponse,
    dependencies=[Depends(requires_kb_tier("write")), Depends(refuses_truncated_body)],
)
@limiter.limit("30/minute")
def patch_entry_field(
    request: Request,
    entry_id: str,
    body: PatchEntryRequest,
    svc: KBService = Depends(get_kb_service),
    resolver=Depends(get_worktree_resolver),
):
    """Update a single field on an entry (used by kanban drag-drop)."""
    auth_user = getattr(request.state, "auth_user", None)
    if auth_user and auth_user.get("role") != "admin":
        try:
            svc = resolver.get_write_service(body.kb, auth_user)
        except (ValueError, Exception):
            logger.warning("Worktree routing failed for PATCH, falling back to main")

    updates = {body.field: body.value}
    try:
        written = svc.update(entry_id, body.kb, updates)
    except (KBNotFoundError, EntryNotFoundError) as e:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": str(e)})
    except KBReadOnlyError as e:
        raise HTTPException(status_code=403, detail={"code": "READ_ONLY", "message": str(e)})
    except ValidationError as e:
        raise refusal_http(e)
    except (PyriteError, ValueError) as e:
        raise HTTPException(status_code=400, detail={"code": "UPDATE_FAILED", "message": str(e)})

    return UpdateResponse(updated=True, id=entry_id, warnings=written.warnings)


@router.delete(
    "/entries/{entry_id}",
    response_model=DeleteResponse,
    dependencies=[Depends(requires_kb_tier("write"))],
)
@limiter.limit("30/minute")
def delete_entry(
    request: Request,
    entry_id: str,
    kb: str = Query(..., description="KB name"),
    svc: KBService = Depends(get_kb_service),
    resolver=Depends(get_worktree_resolver),
):
    """Delete an entry."""
    # Route deletes through worktree for authenticated users
    auth_user = getattr(request.state, "auth_user", None)
    if auth_user and auth_user.get("role") != "admin":
        try:
            svc = resolver.get_write_service(kb, auth_user)
        except ValueError:
            pass  # KB not in a git repo — fall back to main

    try:
        deleted = svc.delete_entry(entry_id, kb)
    except (KBNotFoundError, EntryNotFoundError) as e:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": str(e)})
    except KBReadOnlyError as e:
        raise HTTPException(status_code=403, detail={"code": "READ_ONLY", "message": str(e)})
    except (ValidationError, PyriteError, ValueError) as e:
        raise HTTPException(status_code=400, detail={"code": "DELETE_FAILED", "message": str(e)})

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Entry '{entry_id}' not found"},
        )

    # Broadcast WebSocket event
    from ..websocket import broadcast_event

    broadcast_event("entry_deleted", entry_id=entry_id, kb_name=kb)

    return DeleteResponse(deleted=True, id=entry_id)

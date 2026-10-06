"""Admin endpoints: stats, index sync, AI status, KB management, plugins."""

import logging
from collections.abc import Callable

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ...exceptions import ConfigError, KBNotFoundError, KBProtectedError
from ...services.access_policy import ROLES, Action, AnyKB, ReadScope
from ...services.auth_service import AuthService
from ...services.embedding_worker import EmbeddingWorker
from ...services.ephemeral_service import EphemeralKBService, InvalidEphemeralKBNameError
from ...services.index_worker import IndexWorker
from ...services.kb_registry_service import KBRegistryService
from ...services.llm_service import LLMService
from ...services.llm_usage_service import LLMUsageService
from ...services.site_cache import SiteCacheService
from ...storage.index import IndexManager
from ..api import (
    get_auth_service,
    get_embedding_worker,
    get_ephemeral_service,
    get_index_mgr,
    get_index_worker,
    get_kb_default_role_resolver,
    get_kb_registry,
    get_llm_service,
    get_llm_usage_service,
    get_site_cache_factory,
    limiter,
    requires_tier,
)
from ..authz import authorize
from ..schemas import (
    AIStatusResponse,
    KBReindexResponse,
    SiteCacheSyncStatus,
    StatsResponse,
    SyncResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin"])


@router.get("/stats", response_model=StatsResponse)
@limiter.limit("100/minute")
def get_stats(
    request: Request,
    index_mgr: IndexManager = Depends(get_index_mgr),
    scope: ReadScope = Depends(authorize(Action.KB_READ, AnyKB)),
):
    """Get index statistics, over the KBs the caller may read."""
    stats = index_mgr.get_index_stats(kb_names=scope.as_set())
    return StatsResponse(**stats)


@router.post("/index/sync", dependencies=[Depends(requires_tier("admin"))])
@limiter.limit("30/minute")
def sync_index(
    request: Request,
    wait: bool = False,
    worker: IndexWorker = Depends(get_index_worker),
    index_mgr: IndexManager = Depends(get_index_mgr),
):
    """Sync the search index.

    By default submits a background job and returns immediately with a job_id.
    Pass ?wait=true to block until completion and return the legacy SyncResponse
    format with counts.
    """
    if wait:
        result = index_mgr.sync_incremental()

        # ADR-0035: a write enqueues instead of embedding, and this is one of
        # the two server paths that pay that debt back (the other is the
        # unconditional startup drain, which since the cold read no longer
        # rides on `prewarm_embeddings`). `wait=true` is the caller saying they will
        # block, so the drain is synchronous here; the fire-and-forget branch
        # below leaves the queue to startup or an explicit `pyrite index
        # embed`, because there is nobody left to wait for it.
        from ..api import _drain_embed_queue

        _drain_embed_queue(index_mgr.db)

        # Broadcast WebSocket event
        from ..websocket import broadcast_event

        broadcast_event("kb_synced", entry_id="", kb_name="")

        # Re-render the site cache if entries changed, *after* the sync has
        # already committed and the drain/broadcast above have run. `wait=true`
        # means the caller is already blocked on this request, so the render
        # happens synchronously here rather than being handed to the loop
        # captured at startup (#326's `bind_loop`/`_loop`) -- that hand-off
        # exists for code with no way to make the caller wait; this code has
        # exactly that. `index_mgr.config` (not `request.app.state.config`,
        # which nothing in the server ever sets) is the same config already
        # used to build `index_mgr` via `get_index_mgr`.
        #
        # A render failure does NOT fail the sync (#349 cold-read round): the
        # sync already committed its counts, drained the embed queue and
        # broadcast `kb_synced` by this point, and a 500 here would also make
        # a retry see `added=0` (nothing changed), so the cache would never
        # get another chance to render. The failure is instead reported on
        # `site_cache.error` and logged with its traceback -- narrow because
        # only building the service and `render_all` are wrapped, not the
        # sync, drain or broadcast above.
        site_cache_status = None
        if result.get("added", 0) + result.get("updated", 0) + result.get("removed", 0) > 0:
            from ...services.site_cache import SiteCacheService

            try:
                # Inside the try: the constructor reads branding.yaml, which
                # an operator may have left half-edited. On this path (#408)
                # a broken branding file is reported the same generic way as
                # any other render failure -- the sync already committed, so
                # there is no "nothing committed yet, fix and retry" framing
                # to offer; that framing is only true of the explicit
                # POST /api/site/render call below.
                stats = SiteCacheService(config=index_mgr.config, db=index_mgr.db).render_all()
            except Exception:
                logger.exception("Site cache render failed after sync")
                site_cache_status = SiteCacheSyncStatus(
                    rendered=False, error="site cache render failed; see server log"
                )
            else:
                logger.info("Site cache re-rendered after sync")
                site_cache_status = SiteCacheSyncStatus(
                    rendered=True, errors=stats.get("errors", 0)
                )

        return SyncResponse(
            synced=True,
            added=result.get("added", 0),
            updated=result.get("updated", 0),
            removed=result.get("removed", 0),
            skipped=result.get("skipped", []),
            site_cache=site_cache_status,
        )

    job_id = worker.submit_sync()
    return {"job_id": job_id, "status": "submitted"}


@router.get("/index/jobs/{job_id}", dependencies=[Depends(requires_tier("admin"))])
@limiter.limit("100/minute")
def get_index_job(request: Request, job_id: str, worker: IndexWorker = Depends(get_index_worker)):
    """Get status of an index job."""
    job = worker.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": f"Job '{job_id}' not found"}
        )
    return job


@router.get("/index/jobs", dependencies=[Depends(requires_tier("admin"))])
@limiter.limit("100/minute")
def list_index_jobs(request: Request, worker: IndexWorker = Depends(get_index_worker)):
    """List active index jobs."""
    return {"jobs": worker.get_active_jobs()}


@router.get("/ai/status", response_model=AIStatusResponse)
@limiter.limit("100/minute")
def ai_status(request: Request, llm: LLMService = Depends(get_llm_service)):
    """Return AI/LLM configuration status."""
    status = llm.status()
    return AIStatusResponse(**status)


# write tier: pings the provider with the operator's key (network + quota),
# and sits behind the same settings page as PUT /settings.
@router.post("/ai/test", dependencies=[Depends(requires_tier("write"))])
@limiter.limit("10/minute")
def ai_test_connection(request: Request, llm: LLMService = Depends(get_llm_service)):
    """Actually test the AI connection by pinging the provider."""
    return llm.test_connection()


@router.get("/usage/me")
@limiter.limit("60/minute")
def get_my_usage(
    request: Request,
    usage_svc: LLMUsageService = Depends(get_llm_usage_service),
):
    """Current user's LLM usage totals. Anonymous (auth disabled) users
    get the user_id=None bucket."""
    auth_user = getattr(request.state, "auth_user", None)
    user_id = auth_user["id"] if auth_user else None
    return usage_svc.get_usage(user_id)


@router.get("/admin/usage", dependencies=[Depends(requires_tier("admin"))])
@limiter.limit("60/minute")
def get_all_usage(
    request: Request,
    usage_svc: LLMUsageService = Depends(get_llm_usage_service),
):
    """Per-user LLM usage totals across all users (admin only)."""
    return {"users": usage_svc.get_all_usage()}


@router.post("/site/render", dependencies=[Depends(requires_tier("admin"))])
@limiter.limit("10/minute")
async def render_site_cache(
    request: Request,
    build_site_cache: Callable[[], SiteCacheService] = Depends(get_site_cache_factory),
):
    """Render all /site pages to the filesystem cache for fast serving.

    ``rendered`` means the same thing here as on the sync path's
    ``site_cache`` status (#408): the render ran to completion, independent
    of per-entry page failures, which ``errors`` counts. This is an
    explicit, nothing-already-committed operator call, so an unreadable
    ``branding.yaml`` (``SiteCacheService.__init__`` reads it) is reported
    as a 409 rather than folded into `rendered: false` -- unlike the sync
    path, there is nothing that already committed, so surfacing the mistake
    plainly is more useful than a generic 200/500.
    """
    import asyncio

    from ...exceptions import BrandingInvalidError

    try:
        svc = await asyncio.to_thread(build_site_cache)
    except BrandingInvalidError as e:
        # Logged detail names the real path; the response body carries only
        # the exception's public_message (#377 pattern; #445 cold read --
        # str(e) here previously leaked the branding.yaml path over HTTP).
        logger.warning("Site cache render refused: invalid branding.yaml: %s", e)
        raise HTTPException(
            status_code=409, detail={"code": "BRANDING_INVALID", "message": e.public_message}
        ) from None
    stats = await asyncio.to_thread(svc.render_all)
    return {"rendered": True, **stats}


@router.get("/index/embed-status")
@limiter.limit("100/minute")
def embed_status(request: Request, worker: EmbeddingWorker = Depends(get_embedding_worker)):
    """Return embedding queue status."""
    return worker.get_status()


# =========================================================================
# KB Management
# =========================================================================


@router.post("/kbs", dependencies=[Depends(requires_tier("admin"))])
@limiter.limit("30/minute")
def create_kb(
    request: Request,
    name: str = Body(...),
    path: str = Body(...),
    kb_type: str = Body("generic"),
    description: str = Body(""),
    ephemeral: bool = Body(False),
    ttl: int | None = Body(None),
    eph_svc: EphemeralKBService = Depends(get_ephemeral_service),
    registry: KBRegistryService = Depends(get_kb_registry),
):
    """Create a new knowledge base."""
    if ephemeral:
        try:
            kb = eph_svc.create_ephemeral_kb(name, ttl=ttl or 3600, description=description)
        except InvalidEphemeralKBNameError as e:
            raise HTTPException(
                status_code=400, detail={"code": "INVALID_KB_NAME", "message": str(e)}
            ) from None
        return {"created": True, "name": kb.name, "path": str(kb.path), "ephemeral": True}

    try:
        result = registry.add_kb(name=name, path=path, kb_type=kb_type, description=description)
    except ConfigError as e:
        raise HTTPException(status_code=409, detail={"code": "CONFLICT", "message": str(e)})
    return {"created": True, **result}


@router.put("/kbs/{name}", dependencies=[Depends(requires_tier("admin"))])
@limiter.limit("30/minute")
def update_kb(
    request: Request,
    name: str,
    description: str | None = Body(None),
    kb_type: str | None = Body(None),
    registry: KBRegistryService = Depends(get_kb_registry),
):
    """Update a knowledge base's metadata."""
    try:
        result = registry.update_kb(name, description=description, kb_type=kb_type)
    except KBNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": f"KB '{name}' not found"}
        )
    return {"updated": True, **result}


@router.delete("/kbs/{name}", dependencies=[Depends(requires_tier("admin"))])
@limiter.limit("30/minute")
def delete_kb(
    request: Request,
    name: str,
    registry: KBRegistryService = Depends(get_kb_registry),
):
    """Delete a knowledge base from the registry."""
    try:
        registry.remove_kb(name)
    except KBNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": f"KB '{name}' not found"}
        )
    except KBProtectedError as e:
        raise HTTPException(status_code=403, detail={"code": "PROTECTED", "message": str(e)})
    return {"deleted": True, "name": name}


@router.post("/kbs/{name}/reindex", dependencies=[Depends(requires_tier("admin"))])
@limiter.limit("10/minute")
def reindex_kb(
    request: Request,
    name: str,
    registry: KBRegistryService = Depends(get_kb_registry),
):
    """Reindex a specific knowledge base."""
    try:
        result = registry.reindex_kb(name)
    except KBNotFoundError:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": f"KB '{name}' not found"}
        )
    return KBReindexResponse(name=name, **result)


@router.post("/kbs/gc", dependencies=[Depends(requires_tier("admin"))])
@limiter.limit("30/minute")
def gc_ephemeral_kbs(
    request: Request,
    eph_svc: EphemeralKBService = Depends(get_ephemeral_service),
):
    """Garbage-collect expired ephemeral KBs."""
    removed = eph_svc.gc_ephemeral_kbs()
    return {"removed": removed, "count": len(removed)}


# =============================================================================
# Ephemeral KB Admin
# =============================================================================


@router.get("/kbs/ephemeral", dependencies=[Depends(requires_tier("admin"))])
@limiter.limit("30/minute")
def list_ephemeral_kbs(
    request: Request,
    eph_svc: EphemeralKBService = Depends(get_ephemeral_service),
):
    """List all active ephemeral KBs. Requires admin."""
    kbs = eph_svc.list_ephemeral_kbs()
    return {"ephemeral_kbs": kbs, "count": len(kbs)}


@router.delete("/kbs/ephemeral/{name}", dependencies=[Depends(requires_tier("admin"))])
@limiter.limit("30/minute")
def force_expire_ephemeral_kb(
    request: Request,
    name: str,
    eph_svc: EphemeralKBService = Depends(get_ephemeral_service),
):
    """Force-expire a specific ephemeral KB. Requires admin."""
    ok = eph_svc.force_expire_kb(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Ephemeral KB '{name}' not found")
    return {"expired": True, "name": name}


# =============================================================================
# Ephemeral KB creation (authenticated users, not admin-only)
# =============================================================================


@router.post("/kbs/ephemeral")
@limiter.limit("10/minute")
def create_ephemeral_kb(
    request: Request,
    name: str | None = Body(None, embed=True),
    eph_svc: EphemeralKBService = Depends(get_ephemeral_service),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Create an ephemeral KB for the current user."""
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        result = auth_service.create_user_ephemeral_kb(auth_user["id"], eph_svc, name=name)
    except InvalidEphemeralKBNameError as e:
        raise HTTPException(
            status_code=400, detail={"code": "INVALID_KB_NAME", "message": str(e)}
        ) from None
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

    return {"created": True, **result}


# =============================================================================
# Per-KB Permission CRUD
# =============================================================================


@router.get("/kbs/{name}/permissions")
@limiter.limit("30/minute")
def list_kb_permissions(
    request: Request,
    name: str,
    auth_service: AuthService = Depends(get_auth_service),
    resolve_default: Callable[[str], str | None] = Depends(get_kb_default_role_resolver),
):
    """List permission grants for a KB. Requires global admin or KB admin."""
    auth_user = getattr(request.state, "auth_user", None)
    global_role = getattr(request.state, "api_role", None)

    if global_role != "admin":
        if not auth_user:
            raise HTTPException(status_code=403, detail="Admin access required")
        kb_default_role = resolve_default(name)
        effective = auth_service.get_kb_role(auth_user["id"], name, kb_default_role)
        if effective != "admin":
            raise HTTPException(status_code=403, detail="Admin access required for this KB")

    grants = auth_service.list_kb_permissions(name)
    return {"kb_name": name, "permissions": grants}


@router.post("/kbs/{name}/permissions")
@limiter.limit("30/minute")
def manage_kb_permission(
    request: Request,
    name: str,
    user_id: int = Body(...),
    role: str | None = Body(None),
    revoke: bool = Body(False),
    auth_service: AuthService = Depends(get_auth_service),
    resolve_default: Callable[[str], str | None] = Depends(get_kb_default_role_resolver),
):
    """Grant or revoke a per-KB permission. Requires global admin or KB admin."""
    auth_user = getattr(request.state, "auth_user", None)
    global_role = getattr(request.state, "api_role", None)

    if global_role != "admin":
        if not auth_user:
            raise HTTPException(status_code=403, detail="Admin access required")
        kb_default_role = resolve_default(name)
        effective = auth_service.get_kb_role(auth_user["id"], name, kb_default_role)
        if effective != "admin":
            raise HTTPException(status_code=403, detail="Admin access required for this KB")

    granted_by = auth_user["id"] if auth_user else None

    if revoke:
        ok = auth_service.revoke_kb_permission(user_id, name)
        if not ok:
            raise HTTPException(status_code=404, detail="Permission not found")
        return {"revoked": True, "user_id": user_id, "kb_name": name}

    if not role:
        raise HTTPException(status_code=400, detail="role is required when not revoking")

    try:
        auth_service.grant_kb_permission(user_id, name, role, granted_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"granted": True, "user_id": user_id, "kb_name": name, "role": role}


@router.put("/kbs/{name}/default-role", dependencies=[Depends(requires_tier("admin"))])
@limiter.limit("30/minute")
def update_kb_default_role(
    request: Request,
    name: str,
    role: str | None = Body(..., embed=True),
    registry: KBRegistryService = Depends(get_kb_registry),
):
    """Update a KB's default access role. Requires admin."""
    if role is not None and role not in ("none", "read", "write"):
        raise HTTPException(status_code=400, detail="role must be 'none', 'read', 'write', or null")
    try:
        registry.update_kb(name, default_role=role)
    except KBNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True, "kb_name": name, "default_role": role}


# =============================================================================
# Plugin UI
# =============================================================================


@router.get("/plugins")
@limiter.limit("100/minute")
def list_plugins(request: Request):
    """List installed plugins with capabilities."""
    from ...plugins import get_registry

    registry = get_registry()
    plugins = []
    for name in registry.list_plugins():
        plugin = registry.get_plugin(name)
        if not plugin:
            continue
        info: dict = {
            "name": name,
            "entry_types": [],
            "kb_types": [],
            "tools": [],
            "hooks": [],
            "has_cli": False,
        }
        try:
            if hasattr(plugin, "get_entry_types"):
                types = plugin.get_entry_types()
                if types:
                    info["entry_types"] = list(types.keys())
        except Exception:
            logger.warning("Failed to get entry types for plugin %s", name, exc_info=True)
        try:
            if hasattr(plugin, "get_kb_types"):
                kb_types = plugin.get_kb_types()
                if kb_types:
                    info["kb_types"] = kb_types
        except Exception:
            logger.warning("Failed to get kb types for plugin %s", name, exc_info=True)
        try:
            if hasattr(plugin, "get_cli_commands"):
                cmds = plugin.get_cli_commands()
                if cmds:
                    info["has_cli"] = True
        except Exception:
            logger.warning("Failed to get CLI commands for plugin %s", name, exc_info=True)
        try:
            if hasattr(plugin, "get_hooks"):
                hooks = plugin.get_hooks()
                if hooks:
                    info["hooks"] = list(hooks.keys())
        except Exception:
            logger.warning("Failed to get hooks for plugin %s", name, exc_info=True)
        try:
            for tier in ROLES:
                if hasattr(plugin, "get_mcp_tools"):
                    tools = plugin.get_mcp_tools(tier)
                    if tools:
                        info["tools"].extend(list(tools.keys()))
        except Exception:
            logger.warning("Failed to get MCP tools for plugin %s", name, exc_info=True)
        plugins.append(info)
    return {"plugins": plugins, "total": len(plugins)}


@router.get("/plugins/{name}")
@limiter.limit("100/minute")
def get_plugin_detail(request: Request, name: str):
    """Get detailed plugin information."""
    from ...plugins import get_registry

    registry = get_registry()
    plugin = registry.get_plugin(name)
    if not plugin:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": f"Plugin '{name}' not found"}
        )

    info: dict = {"name": name}

    try:
        if hasattr(plugin, "get_entry_types"):
            types = plugin.get_entry_types()
            info["entry_types"] = {k: str(v) for k, v in types.items()} if types else {}
    except Exception:
        logger.warning("Failed to get entry types for plugin %s", name, exc_info=True)
        info["entry_types"] = {}

    try:
        if hasattr(plugin, "get_kb_types"):
            info["kb_types"] = plugin.get_kb_types() or []
    except Exception:
        logger.warning("Failed to get kb types for plugin %s", name, exc_info=True)
        info["kb_types"] = []

    try:
        if hasattr(plugin, "get_hooks"):
            hooks = plugin.get_hooks()
            info["hooks"] = {k: len(v) for k, v in hooks.items()} if hooks else {}
    except Exception:
        logger.warning("Failed to get hooks for plugin %s", name, exc_info=True)
        info["hooks"] = {}

    tools_all: dict = {}
    try:
        for tier in ROLES:
            if hasattr(plugin, "get_mcp_tools"):
                tools = plugin.get_mcp_tools(tier)
                if tools:
                    for tool_name, tool_def in tools.items():
                        tools_all[tool_name] = {
                            "tier": tier,
                            "description": tool_def.get("description", ""),
                        }
    except Exception:
        logger.warning("Failed to get MCP tools for plugin %s", name, exc_info=True)
    info["tools"] = tools_all

    return info

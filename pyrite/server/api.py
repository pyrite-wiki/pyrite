"""
FastAPI REST Server for pyrite

Provides HTTP API access to knowledge bases for web applications and external integrations.

All endpoints are served under the /api prefix. Endpoint implementations live in
the ``endpoints/`` subpackage; this module provides shared dependencies, the rate
limiter, and the application factory.
"""

import hashlib
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from ..config import PyriteConfig, Settings, load_config
from ..services import access_policy
from ..services.access_policy import (
    FORBIDDEN,
    ROLE_LEVELS,
    AccessPolicy,
    Action,
    Principal,
    authorize_tier,
    resolve_api_key_role,
)
from ..services.block_service import BlockService
from ..services.ephemeral_service import EphemeralKBService
from ..services.export_service import ExportService
from ..services.graph_service import GraphService
from ..services.index_worker import IndexWorker
from ..services.kb_registry_service import KBRegistryService
from ..services.kb_service import KBService
from ..services.link_discovery_service import LinkDiscoveryService
from ..services.llm_service import LLMService
from ..services.llm_usage_service import LLMUsageService
from ..services.review_service import ReviewService
from ..services.search_service import SearchService
from ..services.settings_service import SettingsService
from ..services.starred_service import StarredService
from ..services.task_service import TaskService
from ..services.version_service import VersionService
from ..storage.database import PyriteDB
from ..storage.index import IndexManager
from .authz import get_principal, not_authenticated

if TYPE_CHECKING:
    from ..services.auth_service import AuthService
    from ..services.embedding_worker import EmbeddingWorker
    from ..services.qa_service import QAService
    from ..services.site_cache import SiteCacheService
    from ..services.sitemap_service import SitemapService

logger = logging.getLogger(__name__)

# The central PyriteError -> HTTP handler lives in server/errors.py
# (ADR-0037 theme 2, §3: "one mapping table per transport, in one module
# each"). Re-exported here for existing importers
# (`from pyrite.server.api import register_pyrite_exception_handler`);
# server/errors.py is the source of truth.
from .errors import register_pyrite_exception_handler  # noqa: E402  # isort:skip


def _anonymized_key_func(request: Request) -> str:
    """Hash the client IP for rate limiting without storing the raw address.

    Uses SHA-256 truncated to 16 chars — sufficient for rate limiting,
    not reversible to the original IP address.
    """
    raw_ip = get_remote_address(request)
    return hashlib.sha256(raw_ip.encode()).hexdigest()[:16]


# =============================================================================
# Dependencies (imported by endpoint modules)
#
# Service state lives on ``app.state.pyrite_*`` attributes, initialised by
# ``create_app()``.  DI functions read from app.state so each FastAPI app
# instance is fully isolated — no cross-test contamination via module globals.
# =============================================================================


def _init_app_state(application: FastAPI, config: PyriteConfig) -> None:
    """Initialise pyrite service state on *application*.state."""
    application.state.pyrite_config = config
    application.state.pyrite_db = None
    application.state.pyrite_index_mgr = None
    application.state.pyrite_index_worker = None
    application.state.pyrite_ws_loop = None
    application.state.pyrite_kb_service = None
    application.state.pyrite_kb_registry = None
    application.state.pyrite_llm_service = None
    application.state.pyrite_diff_db_cache = {}  # (user_id, kb_name) → PyriteDB


def get_config() -> PyriteConfig:
    """Get or load configuration.

    When used inside a FastAPI app created by ``create_app()``, this is
    overridden via ``dependency_overrides`` to return the app-state config.
    Direct calls (non-DI contexts) fall back to ``load_config()``.
    """
    return load_config()


def get_db() -> PyriteDB:
    """Get or create database connection.

    When used inside a FastAPI app created by ``create_app()``, this is
    overridden via ``dependency_overrides`` to return the app-state DB.
    Direct calls (non-DI contexts) create a fresh connection.
    """
    config = load_config()
    return PyriteDB(config.settings.index_path)


def get_index_mgr() -> IndexManager:
    """Get or create index manager.

    Overridden via ``dependency_overrides`` inside FastAPI apps.
    """
    config = load_config()
    db = PyriteDB(config.settings.index_path)
    return IndexManager(db, config)


def get_index_worker() -> IndexWorker:
    """Get or create index worker.

    Overridden via ``dependency_overrides`` inside FastAPI apps.
    """
    config = load_config()
    db = PyriteDB(config.settings.index_path)
    return IndexWorker(db, config)


def get_kb_service(
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> KBService:
    """Get or create KB service via DI."""
    return KBService(config, db)


def _drain_embed_queue(db: PyriteDB, *, label: str = "") -> int:
    """Embed everything a write left in `embed_queue`. Blocking; never raises.

    Thin alias for `services.embedding_worker.settle_embed_queue`, which is
    the single drain implementation the CLI shares. Kept as a name in this
    module because the endpoints import it from here.
    """
    from ..services.embedding_worker import settle_embed_queue

    return settle_embed_queue(db, label=label)


def get_task_service(
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> TaskService:
    """Get or create TaskService via DI."""
    return TaskService(config, db)


def get_worktree_resolver(
    request: Request,
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
):
    """Get a WorktreeResolver for per-user read/write routing."""
    from .worktree_resolver import WorktreeResolver

    # Cache diff DBs on app state to avoid heavyweight re-init per request
    cache = getattr(request.app.state, "pyrite_diff_db_cache", {})
    return WorktreeResolver(config, db, cache)


def get_llm_usage_service(
    db: PyriteDB = Depends(get_db),
) -> LLMUsageService:
    """Get or create LLMUsageService via DI."""
    return LLMUsageService(db)


def get_llm_service(
    request: Request,
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> LLMService:
    """Get or create LLM service, using DB settings with config file fallback.

    Wires the per-user usage-tracking context (llm-usage-tracking-and-
    quotas) when a request is authenticated -- anonymous access (auth
    disabled) records usage rows with user_id=None rather than skipping
    tracking entirely.
    """
    provider = db.get_setting("ai.provider") or config.settings.ai_provider
    api_key = db.get_setting("ai.apiKey") or config.settings.ai_api_key
    model = db.get_setting("ai.model") or config.settings.ai_model
    base_url = db.get_setting("ai.baseUrl") or config.settings.ai_api_base
    # Default base URL for Gemini's OpenAI-compatible endpoint
    if provider == "gemini" and not base_url:
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
    settings = Settings(
        ai_provider=provider,
        ai_api_key=api_key,
        ai_model=model,
        ai_api_base=base_url,
    )
    principal = get_principal(request)
    user_id = principal.user_id if principal else None
    usage_service = LLMUsageService(db)
    return LLMService(settings, usage_service=usage_service, user_id=user_id)


def get_user_llm_context(
    request: Request,
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> dict | None:
    """Get the current user's LLM API key context, or None.

    Returns {"provider": ..., "api_key": ..., "model": ...} if the user
    has a stored BYOK key, otherwise None.
    """
    principal = get_principal(request)
    if principal is None or principal.user_id is None:
        return None
    from ..services.auth_service import AuthService

    auth_svc = AuthService(db, config.settings.auth)
    return auth_svc.get_user_api_key(principal.user_id)


def get_kb_registry(
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
    index_mgr: IndexManager = Depends(get_index_mgr),
) -> KBRegistryService:
    """Get KBRegistryService instance via DI."""
    return KBRegistryService(config, db, index_mgr)


def get_repo_service(
    request: Request,
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
):
    """Get RepoService, injecting the current user's GitHub token if available."""
    from ..services.repo_service import RepoService

    svc = RepoService(config, db)

    # Inject user's stored GitHub token if available
    principal = get_principal(request)
    if principal is not None and principal.user_id is not None:
        from ..services.auth_service import AuthService

        auth_service = AuthService(db, config.settings.auth)
        gh_token, _ = auth_service.get_github_token_for_user(principal.user_id)
        if gh_token:
            svc._github_token = gh_token
        else:
            svc._github_token = None
    else:
        svc._github_token = None

    return svc


def get_graph_service(
    db: PyriteDB = Depends(get_db),
) -> GraphService:
    """Get GraphService instance via DI."""
    return GraphService(db)


def get_export_service(
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> ExportService:
    """Get ExportService instance via DI."""
    return ExportService(config, db)


def get_ephemeral_service(
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> EphemeralKBService:
    """Get EphemeralKBService instance via DI."""
    return EphemeralKBService(config, db)


def get_review_service(
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> ReviewService:
    """Get ReviewService instance via DI."""
    return ReviewService(config, db)


def get_version_service(
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> VersionService:
    """Get VersionService instance via DI."""
    return VersionService(config, db)


def get_search_service(
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> SearchService:
    """Get SearchService instance via DI."""
    return SearchService(db, settings=config.settings)


def get_link_discovery_service(
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> LinkDiscoveryService:
    """Get LinkDiscoveryService instance via DI."""
    return LinkDiscoveryService(config, db)


def get_starred_service(
    db: PyriteDB = Depends(get_db),
    kb_service: KBService = Depends(get_kb_service),
) -> StarredService:
    """Get StarredService instance via DI."""
    return StarredService(db, kb_service)


def get_block_service(
    db: PyriteDB = Depends(get_db),
) -> BlockService:
    """Get BlockService instance via DI."""
    return BlockService(db)


def get_settings_service(
    db: PyriteDB = Depends(get_db),
) -> SettingsService:
    """Get SettingsService instance via DI."""
    return SettingsService(db)


def get_auth_service(
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> "AuthService":
    """Get AuthService instance via DI (users, sessions, grants, tokens).

    One per request, like every other provider here. The endpoints used to
    build ``AuthService(db, ...)`` inline (#380).
    """
    from ..services.auth_service import AuthService

    return AuthService(db, config.settings.auth)


def get_access_policy(
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> AccessPolicy:
    """The access policy, for this request's config and DB handle (ADR-0037 §1).

    What `authz.authorize(...)` asks; one per request, like every provider here.
    """
    return AccessPolicy(config, db)


def get_qa_service(
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
    llm_service: LLMService = Depends(get_llm_service),
) -> "QAService":
    """Get QAService instance via DI."""
    from ..services.qa_service import QAService

    return QAService(config, db, llm_service=llm_service)


def get_sitemap_service(
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> "SitemapService":
    """Get SitemapService instance via DI."""
    from ..services.sitemap_service import SitemapService

    return SitemapService(config, db)


def get_embedding_worker(
    db: PyriteDB = Depends(get_db),
) -> "EmbeddingWorker":
    """Get an EmbeddingWorker (the embed queue) via DI."""
    from ..services.embedding_worker import EmbeddingWorker

    return EmbeddingWorker(db)


def get_site_cache_factory(
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> "Callable[[], SiteCacheService]":
    """A zero-argument builder for SiteCacheService.

    A factory, not the service: the constructor reads ``branding.yaml`` and
    may raise ``BrandingInvalidError``, which ``POST /site/render`` answers
    with a 409 -- so the handler must be the one that calls it.
    """

    def build() -> "SiteCacheService":
        from ..services.site_cache import SiteCacheService

        return SiteCacheService(config, db)

    return build


def get_kb_default_role_resolver(
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> Callable[[str], str | None]:
    """A KB's ``default_role``, for a handler that decides inline.

    ``resolve_default(kb_name)`` is ``resolve_kb_default_role`` bound to
    this request's config and database (#380); #383 replaces it.
    """

    def resolve_default(kb_name: str) -> str | None:
        return resolve_kb_default_role(config, db, kb_name)

    return resolve_default


KBRoleResolver = Callable[[str], Awaitable[str | None]]


def get_kb_role_resolver(
    request: Request,
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
) -> KBRoleResolver:
    """The caller's effective role on a KB, for a handler that decides inline.

    Returns an async callable: ``await role_of(kb_name)`` is
    ``resolve_effective_kb_role`` for this request. It exists so a handler
    needs no database handle of its own (#380); the access-policy work
    (#383, ADR-0037) replaces it.
    """

    async def role_of(kb_name: str) -> str | None:
        return await resolve_effective_kb_role(request, config, db, kb_name)

    return role_of


def invalidate_llm_service():
    """Reset the cached LLM service so next request rebuilds it.

    No-op retained for import compatibility. With app-state-scoped DI,
    LLM services are rebuilt per-request from current DB settings.
    """


# The role ladder, re-exported for the handlers that still compare inline
# (`daily.py`, `repos.py`, `settings_ep.py`; ADR-0037 themes 3b/3c move them).
# It is written out once, in `services/access_policy.py`, as is
# `resolve_api_key_role`, imported above and re-exported from here.
TIER_LEVELS = ROLE_LEVELS


async def verify_api_key(
    request: Request,
    config: PyriteConfig = Depends(get_config),
    db: PyriteDB = Depends(get_db),
):
    """Verify API key, session cookie, or anonymous tier. Stores role in request.state.

    Checks in order:
    1. X-API-Key header / api_key query param (existing behaviour)
    2. Session cookie (web UI auth)
    3. Anonymous tier (configurable public access)
    4. No auth configured → admin (backwards-compatible)

    This dependency is ``async``, so its body runs on the event-loop thread.
    Everything here must therefore be non-blocking — the one synchronous DB
    call (``AuthService.verify_session``) is offloaded with
    ``run_in_threadpool`` below (#131 criterion 4). Calling it inline both
    blocked the loop for the duration of the query and put event-loop DB work
    in the same session as the threadpool handlers' — the second, distinct
    exposure of the shared-session bug, specifically on the auth-enabled path.
    """
    # 1. API key (header or query param)
    key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if key:
        role = resolve_api_key_role(key, config)
        if role is not None:
            request.state.api_role = role
            return

    # 2. Session cookie (when auth enabled)
    if config.settings.auth.enabled:
        token = request.cookies.get("pyrite_session")
        if token:
            from starlette.concurrency import run_in_threadpool

            from ..services.auth_service import AuthService

            def _verify() -> dict | None:
                # Runs on a worker thread. `db` is this request's handle, so
                # the lookup uses this request's session — no scope to open,
                # and nothing shared with a concurrent request.
                auth_service = AuthService(db, config.settings.auth)
                return auth_service.verify_session(token)

            user = await run_in_threadpool(_verify)
            if user:
                request.state.api_role = user["role"]
                request.state.auth_user = user
                return

    # 3. Anonymous tier
    if config.settings.auth.enabled and config.settings.auth.anonymous_tier:
        request.state.api_role = config.settings.auth.anonymous_tier
        # Not an operator key: per-KB roles apply (a private KB is hidden).
        request.state.anonymous = True
        return

    # 4. No auth configured → admin (existing behavior)
    if (
        not config.settings.api_key
        and not config.settings.api_keys
        and not config.settings.auth.enabled
    ):
        request.state.api_role = "admin"
        return

    raise HTTPException(status_code=401, detail="Invalid or missing API key")


def requires_tier(tier: str):
    """FastAPI dependency factory: enforce minimum tier on an endpoint.

    Usage: router = APIRouter(dependencies=[Depends(requires_tier("admin"))])
    """

    async def _check_tier(request: Request):
        _enforce_tier(get_principal(request), tier)

    return _check_tier


def _enforce_tier(principal: Principal | None, tier: str) -> None:
    """The global-role check, answered as REST always has: 401 with no
    principal, 403 naming the tier and the role when it is too low."""
    decision = authorize_tier(principal, tier)
    if decision.allowed:
        return
    if principal is None:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    raise HTTPException(
        status_code=403,
        detail=f"Insufficient permissions: requires '{tier}' tier, your role is '{principal.role}'",
    )


# The parameter names that name a knowledge base, in every location a
# request can carry one. Pinned by tests/test_read_scoping_is_structural.py,
# which fails if a handler declares a KB-bearing parameter outside this set.
#
# `source_kb` and `target_kb` are the *secondary* KBs the `links.py` routes
# name alongside their primary one. They belong here rather than in a
# per-route allowlist: the rule is that naming two KBs gets both checked, so
# the only thing a route-specific exception would buy is a request that names
# a readable KB in one parameter and serves from a private one in the other
# (#186). `center_kb` is deliberately absent -- `/api/graph` filters nodes and
# edges by the readable set after building the graph, so a KB named there that
# the caller cannot read contributes nothing to the response, and checking it
# would turn a harmless name into a 404.
KB_PARAM_NAMES = ("kb", "kb_name", "source_kb", "target_kb")


class _UnparseableBodyError(Exception):
    """The request body could not be read or parsed, so the KBs it names are
    unknown. Never treated as "names no KB": that would make a guard pass."""


async def _resolve_kb_names(request: Request) -> list[str]:
    """Every KB this request names, in every location it can name one.

    Query parameters (every name in `KB_PARAM_NAMES` -- `reviews.py` binds
    `Query(..., alias="kb_name")`, so the wire name differs from the Python
    one), path parameters, and the JSON body's `kb`/`kb_name`. A route that
    takes a *secondary* KB (`source_kb`, `target_kb`) is therefore checked
    against that one too, not only against its primary.

    **Every** value is returned, never just the first. A request that names
    two KBs used to be checked against whichever spelling the resolver
    happened to read first and served from the other -- `kb` checked,
    `kb_name` served on the reviews routes; a `kb` query param checked, the
    path's `kb_name` served on `/api/kbs/{kb_name}` and `/orient`. Callers
    require *each* value to be permitted, which removes the whole class.

    Order is preserved and duplicates removed, so the first value is still
    a sensible single name for an error message.

    Raises `_UnparseableBodyError` when a body cannot be read or parsed:
    "no KB named" is what lets a request through, so a body that could carry
    a KB and could not be read must not produce it.

    **The body is read whatever its Content-Type says**. The guard must
    see every body the handler could bind, and whether a handler binds a
    body as JSON is FastAPI's decision, not this resolver's: before 0.132
    FastAPI parsed a body with *no* Content-Type as JSON, and any version may
    draw that line differently. The KBs checked must be the KBs the handler
    acts on, so every non-form body is parsed here, and one that does not
    parse is refused.

    Only a form body is left unread: `/api/entries/import` binds
    `UploadFile = File(...)`, which FastAPI consumes as a stream (reading it
    here raises `RuntimeError("Stream consumed")`), and FastAPI never binds a
    form as JSON. Those routes name their KB in the query string.
    """
    names: list[str] = []

    def add(value: object) -> None:
        if isinstance(value, str) and value and value not in names:
            names.append(value)

    # Path first: it is the route's own identity, the one location a caller
    # cannot add or remove. Only `kb`/`kb_name`; `/plugins/{name}` and
    # `/kbs/{name}` (admin) use `name` for other things, so `name` is read
    # only where the route is a KB route -- see `_admin_kb_path_name` below.
    for param in KB_PARAM_NAMES:
        add(request.path_params.get(param))
    add(_admin_kb_path_name(request))

    # Every value of a repeated parameter, not just the one FastAPI binds.
    for param in KB_PARAM_NAMES:
        for value in request.query_params.getlist(param):
            add(value)

    if _is_form_body(request):
        return names

    try:
        body = await request.body()
    except Exception as exc:
        logger.warning("Failed to extract KB from request body", exc_info=True)
        raise _UnparseableBodyError() from exc
    if body:
        import json

        try:
            data = json.loads(body)
        except Exception as exc:
            logger.warning("Failed to extract KB from request body", exc_info=True)
            raise _UnparseableBodyError() from exc
        if isinstance(data, dict):
            for param in KB_PARAM_NAMES:
                add(data.get(param))

    return names


_FORM_MEDIA_TYPES = frozenset({"multipart/form-data", "application/x-www-form-urlencoded"})


def _is_form_body(request: Request) -> bool:
    """Is this request's body a form, which FastAPI never binds as JSON?

    Parsed the way FastAPI parses the header (`email.message`), so the two
    cannot disagree about what the media type is. Everything else -- JSON,
    no Content-Type, text, an unknown type -- is read by the resolver.
    """
    import email.message

    content_type = request.headers.get("content-type")
    if not content_type:
        return False
    message = email.message.Message()
    message["content-type"] = content_type
    return message.get_content_type() in _FORM_MEDIA_TYPES


def _admin_kb_path_name(request: Request) -> str | None:
    """The `{name}` path param, but only on routes where it names a KB.

    `admin.py` declares `/kbs/{name}` and `/kbs/{name}/permissions`; it also
    declares `/plugins/{name}`, where `name` is a plugin. Keying on the URL
    path keeps the plugin routes from being treated as KB routes.
    """
    name = request.path_params.get("name")
    if not name:
        return None
    return name if request.url.path.startswith("/api/kbs/") else None


async def _resolve_kb_name(request: Request) -> str | None:
    """The single KB this request names, for callers that genuinely need one.

    Prefers the path parameter -- the route's own identity -- over a query
    parameter, which a caller can add freely. Guards must use
    `_resolve_kb_names` and check every value instead; this exists only for
    call sites that need one name (an error message, a role lookup).
    """
    try:
        names = await _resolve_kb_names(request)
    except _UnparseableBodyError:
        return None
    return names[0] if names else None


def resolve_kb_default_role(config: PyriteConfig, db: PyriteDB, kb_name: str) -> str | None:
    """A KB's default_role: `AccessPolicy.kb_default_role`."""
    return AccessPolicy(config, db).kb_default_role(kb_name)


async def resolve_effective_kb_role(
    request: Request, config: PyriteConfig, db: PyriteDB, kb_name: str | None = None
) -> str | None:
    """Resolve the caller's effective role for a KB, without raising.

    Resolution chain:
    1. Global admins always pass (returns "admin")
    2. No user identity and not anonymous (an operator API key, or auth
       disabled) → global `request.state.api_role`
    3. A signed-in user or the anonymous visitor: explicit KB grant → KB
       default_role → user global role / anonymous tier

    Returns None only if no role could be determined at all (e.g. no
    `api_role` set on the request, which normally means auth failed
    upstream). Callers that need a hard 401/403 should still use
    `requires_tier`/`requires_kb_tier`; this helper is for call sites
    that need to check permissions inline without failing the request
    (e.g. deciding whether a GET is allowed to have a write side effect).

    Resolves a **single** KB name when none is given, preferring the path
    parameter. A caller that must cover every KB the request names --
    `requires_kb_tier` does -- resolves them with `_resolve_kb_names` and
    calls this once per name.
    """
    principal = get_principal(request)
    if principal is None:
        return None
    if not principal.per_kb:
        # The admin role, an operator API key, or auth disabled: the same
        # role on every KB, so no KB need be resolved from the request.
        return principal.role

    if kb_name is None:
        kb_name = await _resolve_kb_name(request)
    if not kb_name:
        return principal.role

    # A signed-in user, or the anonymous visitor: the one per-KB rule, the
    # same one `AccessPolicy.read_scope` uses, so an anonymous visitor's write check can
    # never be looser than their read check.
    return AccessPolicy(config, db).effective_kb_role(principal, kb_name)


def effective_kb_role_for_user(
    config: PyriteConfig, db: PyriteDB, user_id: int | None, kb_name: str, auth_service=None
) -> str | None:
    """The per-KB role rule: `AccessPolicy.user_kb_role`, where the one
    implementation lives. `user_id=None` is the anonymous visitor on an
    auth-enabled instance.
    """
    return AccessPolicy(config, db, auth_service).user_kb_role(user_id, kb_name)


def kbs_for_user_at_tier(
    config: PyriteConfig,
    db: PyriteDB,
    user_id: int | None,
    role: str | None,
    tier: str,
    *,
    scoped: bool = True,
) -> set[str] | None:
    """The KBs where the caller's effective role is at least `tier`, or None
    when the caller is not scoped (a global admin, an operator API key, auth
    disabled). See `readable_kbs_for_user` for the scoping rules; this is the
    same walk at any tier, so the read and write sets cannot drift apart.
    """
    return AccessPolicy(config, db).kbs_at_tier(principal_for(user_id, role, scoped=scoped), tier)


def principal_for(user_id: int | None, role: str | None, *, scoped: bool = True) -> Principal:
    """The `Principal` the set helpers' `(user_id, role, scoped)` arguments name:
    a user, the anonymous visitor (scoped, no user), or no identity."""
    if not scoped:
        return Principal.from_api_key(role)
    if user_id is not None:
        return Principal.user(user_id, role)
    return Principal.anonymous(role)


def readable_kbs_for_user(
    config: PyriteConfig,
    db: PyriteDB,
    user_id: int | None,
    role: str | None,
    *,
    scoped: bool = True,
) -> set[str] | None:
    """The KBs a caller may read, or None when the caller is not scoped.

    The one rule, framework-free: no `Request`, so the MCP transport can
    apply exactly what the REST routes apply. `readable_kbs()` below is a
    thin Request-reading wrapper over it; the routes themselves ask
    `AccessPolicy.read_scope` through `authz.authorize`, the same walk.
    **Do not add a second implementation** -- two copies
    drift, and a grant honoured on one surface but refused on the other is
    the bug this whole shape exists to prevent (#201).

    Not scoped (returns None): a global admin, and any caller with no user
    identity to scope by -- an operator API key, or auth disabled entirely.
    Callers that know the identity question is already settled pass
    `scoped=False` to say so.

    Scoped: `user_id` is resolved per KB through the same chain the REST
    routes use (explicit grant → KB default_role → the user's global role),
    and the KB is readable when that effective role is at least "read".
    `user_id=None` with `scoped=True` is the anonymous visitor on an
    auth-enabled instance: the same walk with no grants.
    """
    return kbs_for_user_at_tier(config, db, user_id, role, "read", scoped=scoped)


async def readable_kbs(request: Request, config: PyriteConfig, db: PyriteDB) -> set[str] | None:
    """The KBs this caller may read, or None when the caller is not scoped.

    For in-process callers that want a plain set; a route declares
    `authz.authorize(Action.KB_READ, KB | AnyKB)` and takes a `ReadScope`
    instead (ADR-0037 §2). The caller comes from `authz.get_principal`, the
    one place REST reads identity off the request, and the rule is the shared
    `readable_kbs_for_user` -- the helper the MCP transport uses too, so the
    two surfaces cannot drift (#201).

    No principal is refused (401), never read as "not scoped".
    """
    principal = get_principal(request)
    if principal is None:
        raise not_authenticated()
    return readable_kbs_for_user(
        config, db, principal.user_id, principal.role, scoped=principal.scoped
    )


def kb_not_found(kb_name: str) -> HTTPException:
    """404 for a KB the caller may not read. Not 403: its existence is private too."""
    return HTTPException(
        status_code=404,
        detail={"code": "KB_NOT_FOUND", "message": f"KB '{kb_name}' not found"},
    )


def kb_exists(config: PyriteConfig, db: PyriteDB, kb_name: str) -> bool:
    """Is `kb_name` a KB this instance knows? `AccessPolicy.kb_exists`."""
    return AccessPolicy(config, db).kb_exists(kb_name)


@dataclass(frozen=True)
class RowKB:
    """What a row resolver hands `requires_kb_tier`: the KB that owns the row
    a route changes, and the 404 the route gives for a row that does not exist.

    A route whose request names no KB -- `DELETE /api/reviews/{review_id}` --
    cannot be checked against a KB it does not know. Its resolver looks the
    row up, and the per-KB rule is applied to the row's own KB. `not_found` is
    what the caller sees when that KB is unreadable, so a row in a private KB
    answers byte-for-byte like a row that does not exist.
    """

    kb_name: str
    not_found: HTTPException


async def _enforce_kb_tier(
    request: Request,
    config: PyriteConfig,
    db: PyriteDB,
    kb_name: str,
    tier: str,
    not_found: HTTPException,
) -> None:
    """The per-KB write rule for one KB.

    Passes when the caller's effective role on `kb_name` is at least `tier`.
    Otherwise: 404 (`not_found`) when the caller may not read the KB or the KB
    does not exist -- the two must answer alike, or the answer is an oracle
    for private KB names -- and 403 when the caller can read it but not
    write it.
    """
    # The policy checks existence before role (ADR-0037 §4): a missing KB
    # answers exactly like a private one, for every caller and on every write
    # route -- not with whatever the handler behind this guard says.
    decision = AccessPolicy(config, db).authorize(
        get_principal(request), Action.at_tier(tier), access_policy.KB(kb_name)
    )
    if decision.allowed:
        return
    if decision.code == FORBIDDEN:
        raise HTTPException(
            status_code=403,
            detail=f"Insufficient permissions on KB '{kb_name}': requires '{tier}' tier",
        )
    # NOT_FOUND -- and UNAUTHENTICATED, which both callers answer with 401
    # before reaching here, and which this guard always answered as 404.
    raise not_found


def requires_kb_tier(tier: str, *, resolve_kb=None):
    """FastAPI dependency factory: enforce a minimum tier on the KB(s) a write changes.

    Resolution chain, per KB:
    1. Global admins always pass
    2. Explicit KB grant → KB default_role → user global role → anonymous tier

    A KB the caller cannot read answers 404 exactly like a KB that does not
    exist; a KB the caller can read but not reach `tier` on answers 403.

    Two forms:

    - ``requires_kb_tier("write")`` -- the KB is the one the **request names**
      (`kb`/`kb_name`/... in path, query or JSON body; every value is
      checked, so naming a writable KB beside a private one buys nothing).
      A route using this form must declare a KB-bearing parameter
      (`tests/test_kb_write_guard_is_structural.py`), and a request that
      names no KB is refused (422 `KB_REQUIRED`): the caller's *global* role
      is never enough for a KB-scoped write.
    - ``requires_kb_tier("write", resolve_kb=dep)`` -- for a route that
      changes a row by id and names no KB. `dep` is a FastAPI dependency that
      looks the row up and returns a `RowKB`; the rule is applied to the
      row's own KB, and anything the request names is ignored.
    """
    Action.at_tier(tier)  # an unknown tier fails here, when the route is declared
    if resolve_kb is not None:

        async def _identityless_floor(request: Request) -> None:
            """Refuse before the row is looked up when no KB could change the answer.

            A caller with no user identity -- an operator API key, or auth
            disabled -- has the same role on every KB, so a tier it lacks is
            refused here: before the resolver validates the id or reveals
            whether the row exists. A signed-in user may hold a per-KB grant,
            and an anonymous visitor a KB's default_role, above the global
            role, so for them the row's KB decides, below.
            """
            principal = get_principal(request)
            if principal is None or not principal.scoped:
                _enforce_tier(principal, tier)

        async def _check_row_kb_tier(
            request: Request,
            # Declared first: FastAPI solves sub-dependencies in order, so the
            # floor runs before the resolver.
            _floor: None = Depends(_identityless_floor),
            row: RowKB = Depends(resolve_kb),
            config: PyriteConfig = Depends(get_config),
            db: PyriteDB = Depends(get_db),
        ):
            await _enforce_kb_tier(request, config, db, row.kb_name, tier, row.not_found)

        return _check_row_kb_tier

    async def _check_kb_tier(
        request: Request,
        config: PyriteConfig = Depends(get_config),
        db: PyriteDB = Depends(get_db),
    ):
        principal = get_principal(request)
        if principal is None:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

        try:
            kb_names = await _resolve_kb_names(request)
        except _UnparseableBodyError:
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_BODY", "message": "Request body could not be parsed"},
            ) from None

        if not kb_names:
            # A KB-scoped write that names no KB has nothing to be authorised
            # against. It is refused -- never checked against the caller's
            # global role, which says nothing about the KB the handler would
            # then act on.
            raise HTTPException(
                status_code=422,
                detail={"code": "KB_REQUIRED", "message": "This write must name a knowledge base"},
            )

        for kb_name in kb_names:
            await _enforce_kb_tier(request, config, db, kb_name, tier, kb_not_found(kb_name))

    return _check_kb_tier


# =============================================================================
# Content Negotiation
# =============================================================================


def negotiate_response(request: Request, data: Any) -> Response | None:
    """Check Accept header and return formatted response, or None for default JSON.

    Endpoints call this after computing their result dict. If the client
    requested a non-JSON format via the Accept header, returns a Response
    with the serialized content. Returns None when JSON is acceptable so
    the endpoint can use its normal Pydantic response model.
    """
    accept = request.headers.get("accept", "application/json")

    # Skip negotiation for standard JSON requests
    if not accept or accept == "*/*" or "application/json" in accept.split(",")[0]:
        return None

    from ..formats import format_response, negotiate_format

    fmt = negotiate_format(accept)
    if fmt is None:
        return JSONResponse(
            status_code=406,
            content={
                "error": "Not Acceptable",
                "supported_formats": [
                    "application/json",
                    "text/markdown",
                    "text/csv",
                    "text/yaml",
                ],
            },
        )

    if fmt == "json":
        return None  # Use default

    content, media_type = format_response(data, fmt)
    return Response(content=content, media_type=media_type)


# =============================================================================
# Rate Limiter
# =============================================================================

limiter = Limiter(key_func=_anonymized_key_func)


# =============================================================================
# Application Factory
# =============================================================================


def create_app(config: PyriteConfig | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Optional config to use. If None, loads from default config file.
    """
    from fastapi import APIRouter

    from .endpoints import all_routers

    application = FastAPI(
        title="pyrite API",
        description="REST API for pyrite knowledge management",
        version="0.12.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Resolve config for CORS setup
    if config is None:
        config = load_config()

    # Store all service state on app.state for per-app isolation
    _init_app_state(application, config)

    # Override DI functions to read from app.state instead of module globals
    def _app_get_config() -> PyriteConfig:
        return application.state.pyrite_config

    def _app_db() -> PyriteDB:
        """The app's shared ``PyriteDB`` — engine and pool, no session.

        Plain accessor for the other app-state builders below (index manager,
        KB registry, index worker), which need the object, not a request
        scope. The *dependency* is ``_app_get_db`` underneath.
        """
        if application.state.pyrite_db is None:
            cfg = application.state.pyrite_config
            db = PyriteDB(cfg.settings.index_path)
            application.state.pyrite_db = db
            db.merge_registered_kbs(cfg)
        return application.state.pyrite_db

    def _app_get_db():
        """Per-request database session (#131).

        The ``PyriteDB`` — and so the engine and its connection pool — is still
        built once and cached on app state; what is now per-request is the
        SQLAlchemy ``Session``. ``session_scope()`` binds a fresh one for the
        duration of this request and closes it in its ``finally``, on every
        path including exceptions.

        Being a generator dependency, FastAPI opens the handle before the
        handler and closes it after the response is produced. The handler
        receives a per-request *handle* onto the same database — same engine,
        pool, raw connection and backend — whose ``db.session`` and backend
        ``self._session`` resolve to this request's own Session. That is why no
        service or endpoint signature had to change.

        The handle, rather than a thread-local or a ContextVar, is what makes
        this correct: one anyio worker thread interleaves several requests (see
        ``PyriteDB.session`` for the measurements), so neither the thread nor
        the context identifies a request.

        Both halves of the fix are required and both are here: the per-request
        session (isolation) and its close (returning the connection to the
        pool, which ``storage/connection.py`` sizes to the anyio threadpool so
        the close cannot be traded for ``QueuePool limit ... reached``).
        """
        db = _app_db()
        with db.request_handle() as handle:
            yield handle

    def _app_get_index_mgr() -> IndexManager:
        if application.state.pyrite_index_mgr is None:
            application.state.pyrite_index_mgr = IndexManager(_app_db(), _app_get_config())
        return application.state.pyrite_index_mgr

    def _app_get_kb_registry() -> KBRegistryService:
        """The startup/seeding registry, bound to the shared ``PyriteDB``.

        Not a request dependency -- see ``_request_kb_registry`` below. This
        one exists for ``seed_from_config()`` at startup, where there is no
        request and so no per-request session to bind to.
        """
        if application.state.pyrite_kb_registry is None:
            application.state.pyrite_kb_registry = KBRegistryService(
                _app_get_config(), _app_db(), _app_get_index_mgr()
            )
        return application.state.pyrite_kb_registry

    def _request_kb_registry(db: PyriteDB = Depends(_app_get_db)) -> KBRegistryService:
        """A registry bound to *this request's* handle.

        The cached app-state registry holds the shared ``PyriteDB``, so its
        ORM reads resolve to the thread-local fallback session -- which opens
        a transaction per request that nothing closes, and the connection is
        never returned to the pool. Measured before this: five requests to
        ``GET /api/kbs`` produced five checkouts and zero check-ins, and
        request 58 died with ``QueuePool limit of size 40 overflow 20
        reached``. Building it per request from the handle costs one object
        and keeps the engine, pool and backend shared.
        """
        return KBRegistryService(_app_get_config(), db, _app_get_index_mgr())

    def _app_get_index_worker() -> IndexWorker:
        if application.state.pyrite_index_worker is None:
            worker = IndexWorker(_app_db(), _app_get_config())

            # Wire WebSocket broadcast for progress updates. The callback runs
            # on an IndexWorker thread; broadcast_event hands it to the loop
            # captured at startup (#322). index_progress is operator
            # information and reaches unscoped sockets only, whatever the
            # job's KB (UNSCOPED_ONLY_EVENTS); kb_name is carried for them.
            def _ws_progress(job_id: str, current: int, total: int, kb_name: str | None):
                from .websocket import broadcast_event

                broadcast_event(
                    "index_progress",
                    job_id=job_id,
                    current=current,
                    total=total,
                    kb_name=kb_name,
                )

            worker.on_progress = _ws_progress
            application.state.pyrite_index_worker = worker
        return application.state.pyrite_index_worker

    application.dependency_overrides[get_config] = _app_get_config
    application.dependency_overrides[get_db] = _app_get_db
    application.dependency_overrides[get_index_mgr] = _app_get_index_mgr
    application.dependency_overrides[get_index_worker] = _app_get_index_worker
    application.dependency_overrides[get_kb_registry] = _request_kb_registry

    # Seed config KBs into DB registry
    try:
        registry = _app_get_kb_registry()
        seeded = registry.seed_from_config()
        if seeded:
            logger.info("Seeded %d config KB(s) into registry", seeded)
    except Exception:
        logger.warning("Failed to seed KB registry from config", exc_info=True)

    # Set up embedding service for prewarm and actually pre-warm it on startup.
    # This used to only construct the EmbeddingService and stop -- the comment
    # said "actual prewarm happens in lifespan" but no lifespan/startup hook
    # ever called .prewarm(), so /health's embeddings.ready stayed false
    # forever and the first real search/embed request always paid the full
    # cold-start cost this feature exists to avoid. Runs in a thread since
    # prewarm() is a blocking sentence-transformers model load.
    if config.settings.prewarm_embeddings:
        from ..services.embedding_service import EmbeddingService

        application.state.pyrite_embedding_svc = EmbeddingService(
            _app_db(), model_name=config.settings.embedding_model
        )

        @application.on_event("startup")
        async def _prewarm_embedding_model() -> None:
            from starlette.concurrency import run_in_threadpool

            warmed = await run_in_threadpool(application.state.pyrite_embedding_svc.prewarm)
            if warmed:
                logger.info("Embedding model pre-warmed on startup")
            else:
                logger.warning(
                    "Embedding model pre-warm failed or unavailable "
                    "(sentence-transformers not installed?)"
                )

    # ADR-0035: writes enqueue rather than embed, so anything written while
    # this process -- or a previous one -- had no model is sitting in
    # embed_queue. Draining it is what turns "eventually embedded" into
    # "embedded".
    #
    # **Deliberately outside the `prewarm_embeddings` branch above.** That
    # setting defaults to False, so gating the drain on it meant the default
    # server (`auto_embed: true`, `prewarm_embeddings: false`) enqueued
    # forever with only the admin-tier `POST /api/index/sync?wait=true` left
    # to drain it -- every `--mode semantic` returning [] on a stock install,
    # a straight functional loss against the synchronous behaviour ADR-0035
    # replaced. Affordable unconditionally because `settle_embed_queue`
    # checks `has_pending()` first: one indexed COUNT, and no EmbeddingService
    # (so no torch) when there is nothing owed, which is the usual case.
    #
    # Still no background thread (#102): this runs in the startup threadpool,
    # which the server already waits on before serving.
    @application.on_event("startup")
    async def _drain_embed_queue_on_startup() -> None:
        from starlette.concurrency import run_in_threadpool

        # `_app_db()`, not the `_app_get_db` dependency: this runs at startup,
        # outside any request, and the dependency is a generator FastAPI is
        # meant to open and close around a handler. Calling it directly returns
        # the generator object itself, and the first attribute access on it
        # fails with `'generator' object has no attribute '_raw_conn'`, so the
        # drain never runs and a stock install embeds nothing.
        db = _app_db()
        await run_in_threadpool(lambda: _drain_embed_queue(db, label="startup"))

    # Sync routes and IndexWorker threads have no running loop; they hand
    # WebSocket events to this one (#326, #322). Captured here, not at import,
    # because the loop that serves the sockets exists only once the server
    # starts; released at shutdown so a later app in the same process (the
    # manager is module-global) never hands events to a dead loop.
    # Pinned by tests/test_websocket_delivery.py (TestLoopLifetime, TestBindUnbind).
    @application.on_event("startup")
    async def _bind_websocket_loop() -> None:
        import asyncio

        from .websocket import bind_loop

        application.state.pyrite_ws_loop = asyncio.get_running_loop()
        bind_loop(application.state.pyrite_ws_loop)

    # A socket lives no longer than the credential that opened it (#411,
    # ADR-0036): AuthService announces each session end or scope change and
    # the socket manager closes the sockets it names. Subscribing is
    # idempotent and never undone -- the listener is module-level, like the
    # manager, and does nothing while no live loop is bound. The expiry sweep
    # lives exactly as long as this app's loop.
    # Pinned by tests/test_websocket_credential_lifetime.py.
    @application.on_event("startup")
    async def _start_socket_credential_lifetime() -> None:
        import asyncio

        from ..services import credential_events
        from .websocket import expiry_sweep, on_credential_change

        credential_events.subscribe(on_credential_change)
        application.state.pyrite_ws_expiry_sweep = asyncio.get_running_loop().create_task(
            expiry_sweep()
        )

    @application.on_event("shutdown")
    async def _unbind_websocket_loop() -> None:
        import asyncio

        from .websocket import unbind_loop

        sweep = getattr(application.state, "pyrite_ws_expiry_sweep", None)
        if sweep is not None:
            sweep.cancel()
            # Bounded: shutdown never waits on the sweep for long.
            await asyncio.wait({sweep}, timeout=5)
        unbind_loop(application.state.pyrite_ws_loop)

    # CORS — use configured origins; disable credentials with wildcard (spec compliance)
    origins = config.settings.cors_origins
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Host allow-list and cross-origin write refusal for the credential-free
    # (auth disabled) mode. Added after CORS so it is the outermost layer: a
    # request to an unexpected Host is refused before anything else runs.
    from .request_guard import RequestGuardMiddleware

    application.add_middleware(RequestGuardMiddleware, get_config=_app_get_config)

    # Rate limiting
    application.state.limiter = limiter
    application.add_exception_handler(
        RateLimitExceeded,
        lambda request, exc: JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit exceeded: {exc.detail}"},
            headers={"Retry-After": str(getattr(exc, "retry_after", 60))},
        ),
    )

    # Central handler for the domain exception hierarchy (server/errors.py):
    # any uncaught PyriteError is mapped to a proper HTTP status + uniform
    # {"detail": {"code","message","retryable","hint"?}} body instead of a 500.
    register_pyrite_exception_handler(application)

    # Auth router (mounted outside /api, no verify_api_key dependency)
    from .auth_endpoints import auth_router

    application.include_router(auth_router)

    # Branding router (public — the login page needs it before auth)
    from .branding_endpoints import branding_router

    application.include_router(branding_router)

    # SEO endpoints: sitemap.xml + robots.txt (public — crawlers don't auth)
    from .seo_endpoints import seo_router

    application.include_router(seo_router)

    # MCP SSE transport (mounted outside /api — handles its own Bearer auth)
    from .mcp_routes import mount_mcp_routes

    # The shared PyriteDB, not the `_app_get_db` generator dependency: the MCP
    # routes are plain Starlette handlers, so no DI runs the generator; they
    # open their own per-request handle (#131) around the auth lookup.
    mount_mcp_routes(application, _app_get_config, _app_db)

    # Collect endpoint routers under /api with auth + read-tier baseline
    api_router = APIRouter(
        prefix="/api",
        dependencies=[Depends(verify_api_key), Depends(requires_tier("read"))],
    )
    for r in all_routers:
        api_router.include_router(r)
    application.include_router(api_router)

    # Health check (not behind /api — used for infra probes, no rate limit)
    @application.get("/health", tags=["Admin"])
    def health_check():
        """Health check endpoint."""
        result: dict[str, Any] = {
            "status": "ok",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if config.settings.prewarm_embeddings:
            svc = getattr(application.state, "pyrite_embedding_svc", None)
            result["embeddings"] = {
                "ready": svc.is_warm if svc else False,
            }
        return result

    # WebSocket endpoint for multi-tab awareness
    @application.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        """Authenticate the handshake, then register the socket with its scope.

        Rejected handshakes are closed *before* ``accept`` and never reach the
        manager (#218). The readable set is fixed for the connection's life,
        and the connection lives no longer than its credential (ADR-0036).
        The resolution runs on a worker thread with its own short-lived DB
        handle -- not a ``Depends(get_db)`` session, which would stay open for
        as long as the socket does.
        """
        from starlette.concurrency import run_in_threadpool

        from .websocket import HandshakeRejectedError, manager, origin_allowed, resolve_socket_scope

        cfg = application.state.pyrite_config
        if not origin_allowed(ws, cfg):
            # Logged: behind a proxy that rewrites Host, this is the only
            # trace of why the web UI's socket never connects.
            logger.warning(
                "Refused /ws handshake: Origin %r is neither this server's Host %r "
                "nor listed in cors_origins",
                ws.headers.get("origin"),
                ws.headers.get("host"),
            )
            await ws.close(code=1008)
            return

        def _resolve():
            with _app_db().request_handle() as db:
                return resolve_socket_scope(ws, cfg, db)

        # Read before resolving: a credential change processed after this
        # point may have revoked what `_resolve` is about to find valid.
        epoch = manager.epoch
        try:
            scope = await run_in_threadpool(_resolve)
        except HandshakeRejectedError:
            logger.info("Refused /ws handshake: no credential admits this socket")
            await ws.close(code=1008)
            return

        if not await manager.connect(ws, scope, epoch):
            return
        try:
            while True:
                # Keep connection alive; clients can send pings
                await ws.receive_text()
        except WebSocketDisconnect:
            manager.disconnect(ws)

    # Mount static files if dist directory exists
    # Check env override first (for containerised deploys where the package is
    # installed as a site-package and the relative path won't resolve).
    dist_dir = (
        Path(os.environ.get("PYRITE_STATIC_DIR", ""))
        if os.environ.get("PYRITE_STATIC_DIR")
        else None
    )
    if dist_dir is None:
        dist_dir = Path(__file__).parent.parent.parent / "web" / "dist"
    # Always mount /site and /viewer routes (independent of SPA dist)
    from .static import mount_site_routes

    mount_site_routes(application)

    # Mount SPA static files if dist directory exists
    if dist_dir.is_dir():
        from .static import mount_static

        mount_static(application, dist_dir)

    return application


# =============================================================================
# Default application instance (used by uvicorn / existing imports)
# =============================================================================

app = create_app()


# =============================================================================
# Main
# =============================================================================


def main():
    """Run the API server."""
    import uvicorn

    from ..config import open_registration_warning

    config = load_config()
    if warning := open_registration_warning(config):
        logger.warning(warning)
    uvicorn.run(
        "pyrite.server.api:app",
        host=config.settings.host,
        port=config.settings.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()

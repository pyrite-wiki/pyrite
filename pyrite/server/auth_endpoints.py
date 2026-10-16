"""
Auth endpoints for web UI authentication.

Mounted at /auth (outside /api) to bypass API key verification.
Provides register, login, logout, session introspection, and OAuth flows.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..config import PyriteConfig
from ..exceptions import LastAdminError
from ..services.access_policy import ROLES
from ..services.auth_service import AuthService, RegistrationClosedError
from ..services.oauth_providers import GitHubOAuthProvider
from .api import _anonymized_key_func, get_auth_service, get_config, requires_tier, verify_api_key
from .auth_rate_limit import get_auth_rate_limiter

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["Auth"])

# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str | None = None
    invite_code: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthUserResponse(BaseModel):
    id: int
    username: str
    display_name: str | None
    role: str
    auth_provider: str = "local"
    avatar_url: str | None = None
    kb_permissions: dict[str, str] = {}


class AuthConfigResponse(BaseModel):
    enabled: bool
    allow_registration: bool
    require_invite_code: bool = False
    providers: list[str] = []
    anonymous_tier: str = "none"


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

COOKIE_NAME = "pyrite_session"


def _set_session_cookie(response: Response, token: str, ttl_hours: int, request: Request) -> None:
    secure = request.url.scheme == "https"
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
        max_age=ttl_hours * 3600,
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


# ---------------------------------------------------------------------------
# OAuth state, bound to the browser that started the flow
#
# The state row lives in the DB (AuthService.{create,verify}_oauth_state,
# oauth_state table) so it survives restarts and works across replicas. The
# binding value lives in this short-lived cookie, which only the browser that
# started the flow holds; the callback needs both. SameSite=Lax because the
# provider's redirect back is a top-level cross-site GET navigation.
# ---------------------------------------------------------------------------

OAUTH_BINDING_COOKIE = "pyrite_oauth_binding"
_OAUTH_COOKIE_PATH = "/auth/github"


def _set_oauth_binding_cookie(response: Response, binding: str, request: Request) -> None:
    response.set_cookie(
        key=OAUTH_BINDING_COOKIE,
        value=binding,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path=_OAUTH_COOKIE_PATH,
        max_age=AuthService._OAUTH_STATE_TTL_SECONDS,
    )


def _clear_oauth_binding_cookie(response: Response) -> None:
    response.delete_cookie(key=OAUTH_BINDING_COOKIE, path=_OAUTH_COOKIE_PATH)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# User management needs the caller's role. auth_router is mounted without the
# credential dependency, because /auth/login, /auth/register and /auth/config
# must stay reachable without one, so these routes resolve it themselves:
# verify_api_key sets request.state.api_role from a key or session cookie (401
# when there is neither), and requires_tier("admin") refuses anything below it
# (403). Order matters: the tier check reads what verify_api_key set.
_ADMIN_ONLY = [Depends(verify_api_key), Depends(requires_tier("admin"))]


@auth_router.get("/users", dependencies=_ADMIN_ONLY)
async def list_users(
    auth_service: AuthService = Depends(get_auth_service),
) -> dict:
    """List all users. Requires the global admin tier."""
    return {"users": auth_service.list_users()}


@auth_router.put("/users/{user_id}/role", dependencies=_ADMIN_ONLY)
async def set_user_role(
    request: Request,
    user_id: int,
    auth_service: AuthService = Depends(get_auth_service),
) -> dict:
    """Update a user's global role. Requires the global admin tier."""
    body = await request.json()
    role = body.get("role", "")
    if role not in ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role: {role}")

    global_access = body.get("global_access")
    if global_access is not None and not isinstance(global_access, bool):
        raise HTTPException(status_code=400, detail="global_access must be true or false")
    try:
        found = auth_service.set_role(user_id, role, global_access=global_access)
    except LastAdminError as e:
        raise HTTPException(status_code=409, detail={"code": "LAST_ADMIN", "message": str(e)})
    if not found:
        raise HTTPException(status_code=404, detail="User not found")
    return {"updated": True, "user_id": user_id, "role": role}


@auth_router.get("/users/{user_id}/permissions", dependencies=_ADMIN_ONLY)
async def get_user_permissions(
    user_id: int,
    auth_service: AuthService = Depends(get_auth_service),
) -> dict:
    """Get a user's per-KB permissions. Requires the global admin tier."""
    perms = auth_service.get_user_kb_permissions(user_id)
    return {"user_id": user_id, "permissions": perms}


@auth_router.get("/config")
async def get_auth_config(
    config: PyriteConfig = Depends(get_config),
) -> AuthConfigResponse:
    """Public endpoint: returns auth configuration for the frontend."""
    providers = [name for name, p in config.settings.auth.providers.items() if p.client_id]
    return AuthConfigResponse(
        enabled=config.settings.auth.enabled,
        allow_registration=config.settings.auth.allow_registration,
        require_invite_code=config.settings.auth.require_invite_code,
        providers=providers,
        anonymous_tier=config.settings.auth.anonymous_tier or "none",
    )


@auth_router.post("/register")
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    config: PyriteConfig = Depends(get_config),
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthUserResponse:
    """Create a new account.

    Refused (403) until an operator has created an admin with the CLI; a
    registered user never becomes admin. Rate-limited per client (429).

    The account creation and the auto-login are both synchronous DB work, so
    both run in the threadpool (``run_in_threadpool``, #131 criterion 4):
    called inline, on this ``async def`` handler's own event-loop thread, a
    write blocked behind another connection's write lock (an index sync,
    another login) would stall every other request the loop is holding open
    for up to the busy timeout, not only this one (#440).
    """
    if not config.settings.auth.enabled:
        raise HTTPException(status_code=400, detail="Authentication is not enabled")

    get_auth_rate_limiter(request, config.settings.auth).check_register(
        _anonymized_key_func(request)
    )

    from starlette.concurrency import run_in_threadpool

    def _register() -> dict:
        return auth_service.register(
            body.username, body.password, body.display_name, body.invite_code
        )

    try:
        user = await run_in_threadpool(_register)
    except RegistrationClosedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Auto-login after registration. Deliberately outside the try/except
    # above: a ValueError here is not a registration failure -- the account
    # was already created -- so it must not be reported as one (400) or
    # silently mapped to the wrong status; it propagates like any other
    # unhandled error, exactly as it did before this call moved off the
    # event loop.
    def _login() -> tuple[dict, str]:
        return auth_service.login(body.username, body.password)

    _, token = await run_in_threadpool(_login)
    _set_session_cookie(response, token, config.settings.auth.session_ttl_hours, request)

    return AuthUserResponse(**user)


@auth_router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    config: PyriteConfig = Depends(get_config),
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthUserResponse:
    """Authenticate and set session cookie.

    Rate-limited before the password is checked: every attempt counts for
    the client, failed attempts for the username (429 when either is spent).

    ``AuthService.login`` is synchronous DB work and runs in the threadpool
    (``run_in_threadpool``, #131 criterion 4), not inline on this handler's
    event-loop thread: a login whose write is blocked behind another
    connection's write lock would otherwise stall every other request the
    loop is holding open for up to the busy timeout, not only this one (#440).
    """
    if not config.settings.auth.enabled:
        raise HTTPException(status_code=400, detail="Authentication is not enabled")

    limiter = get_auth_rate_limiter(request, config.settings.auth)
    limiter.check_login(_anonymized_key_func(request), body.username)

    from starlette.concurrency import run_in_threadpool

    def _login() -> tuple[dict, str]:
        return auth_service.login(body.username, body.password)

    try:
        user, token = await run_in_threadpool(_login)
    except ValueError:
        limiter.record_login_failure(body.username)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    _set_session_cookie(response, token, config.settings.auth.session_ttl_hours, request)
    return AuthUserResponse(**user)


@auth_router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> dict:
    """Clear session cookie and delete server-side session."""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        auth_service.logout(token)
    _clear_session_cookie(response)
    return {"ok": True}


@auth_router.get("/me")
async def get_current_user(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthUserResponse:
    """Return current user from session cookie, or 401."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = auth_service.verify_session(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    # Include per-KB permissions
    kb_perms = auth_service.get_user_kb_permissions(user["id"])
    user["kb_permissions"] = kb_perms

    return AuthUserResponse(**user)


# ---------------------------------------------------------------------------
# GitHub OAuth endpoints
# ---------------------------------------------------------------------------


@auth_router.get("/github")
async def github_oauth_start(
    request: Request,
    config: PyriteConfig = Depends(get_config),
    auth_service: AuthService = Depends(get_auth_service),
) -> RedirectResponse:
    """Redirect user to GitHub for authorization."""
    gh_config = config.settings.auth.providers.get("github")
    if not gh_config or not gh_config.client_id:
        raise HTTPException(status_code=404, detail="GitHub OAuth is not configured")

    provider = GitHubOAuthProvider(gh_config.client_id, gh_config.client_secret)
    state, binding = auth_service.create_oauth_state()

    # Build callback URL from request
    callback_url = str(request.url_for("github_oauth_callback"))
    authorize_url = provider.get_authorize_url(callback_url, state)

    response = RedirectResponse(url=authorize_url, status_code=302)
    _set_oauth_binding_cookie(response, binding, request)
    return response


@auth_router.get("/github/callback")
async def github_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    config: PyriteConfig = Depends(get_config),
    auth_service: AuthService = Depends(get_auth_service),
) -> RedirectResponse:
    """Handle GitHub OAuth callback.

    Completes only in the browser that started the flow (the binding
    cookie), and -- for the connect flow -- only for the session user who
    started it. The binding cookie is cleared whatever the outcome.
    """
    response = await _github_oauth_callback(request, code, state, error, config, auth_service)
    _clear_oauth_binding_cookie(response)
    return response


async def _github_oauth_callback(
    request: Request,
    code: str,
    state: str,
    error: str,
    config: PyriteConfig,
    auth_service: AuthService,
) -> RedirectResponse:
    """Every synchronous DB call this handler makes -- state and session
    verification, storing a connected token, and the login itself -- runs in
    the threadpool (``run_in_threadpool``, #131 criterion 4). Called inline
    on this ``async def`` handler's event-loop thread, a write blocked
    behind another connection's write lock would stall every other request
    the loop is holding open for up to the busy timeout, not only this
    callback (#440).
    """
    from starlette.concurrency import run_in_threadpool

    if error or not code:
        logger.warning("GitHub OAuth error: %s", error or "no code")
        return RedirectResponse(url="/login?error=oauth_failed", status_code=302)

    binding = request.cookies.get(OAUTH_BINDING_COOKIE, "")
    state_data = await run_in_threadpool(auth_service.verify_oauth_state, state, binding)
    if not state_data:
        logger.warning("GitHub OAuth invalid/expired state")
        return RedirectResponse(url="/login?error=oauth_failed", status_code=302)

    gh_config = config.settings.auth.providers.get("github")
    if not gh_config or not gh_config.client_id:
        return RedirectResponse(url="/login?error=oauth_failed", status_code=302)

    # A connect callback completes only for the session user who started it.
    # Checked before the exchange, so a refused callback never spends the code.
    if state_data.get("flow") == "connect":
        connect_user_id = state_data.get("user_id")
        session_token = request.cookies.get(COOKIE_NAME)
        session_user = (
            await run_in_threadpool(auth_service.verify_session, session_token)
            if session_token
            else None
        )
        if not connect_user_id or not session_user or session_user["id"] != connect_user_id:
            logger.warning("GitHub connect callback without the initiating user's session")
            return RedirectResponse(url="/settings/kbs?error=connect_failed", status_code=302)

    provider = GitHubOAuthProvider(gh_config.client_id, gh_config.client_secret)
    callback_url = str(request.url_for("github_oauth_callback"))

    try:
        token = await provider.exchange_code(code, callback_url)
    except ValueError as e:
        logger.warning("GitHub OAuth token exchange failed: %s", e)
        return RedirectResponse(url="/login?error=oauth_failed", status_code=302)
    except Exception:
        logger.exception("GitHub OAuth unexpected error during token exchange")
        return RedirectResponse(url="/login?error=oauth_failed", status_code=302)

    # Handle "connect" flow — store token for existing user, don't create session
    if state_data.get("flow") == "connect":
        connect_user_id = state_data["user_id"]
        try:
            await run_in_threadpool(
                auth_service.store_github_token, connect_user_id, token.access_token, token.scope
            )
            return RedirectResponse(url="/settings/kbs?github=connected", status_code=302)
        except Exception:
            logger.exception("Failed to store GitHub token")
            return RedirectResponse(url="/settings/kbs?error=connect_failed", status_code=302)

    # Standard login flow
    try:
        profile = await provider.get_user_profile(token)
        user, session_token = await run_in_threadpool(auth_service.oauth_login, profile, gh_config)
    except ValueError as e:
        logger.warning("GitHub OAuth login failed: %s", e)
        return RedirectResponse(url="/login?error=oauth_failed", status_code=302)
    except Exception:
        logger.exception("GitHub OAuth unexpected error")
        return RedirectResponse(url="/login?error=oauth_failed", status_code=302)

    response = RedirectResponse(url="/", status_code=302)
    _set_session_cookie(response, session_token, config.settings.auth.session_ttl_hours, request)
    return response


# ---------------------------------------------------------------------------
# GitHub Connect (scope escalation for repo access)
# ---------------------------------------------------------------------------

CONNECT_SCOPES = "read:user read:org public_repo"


@auth_router.get("/github/connect")
async def github_connect_start(
    request: Request,
    config: PyriteConfig = Depends(get_config),
    auth_service: AuthService = Depends(get_auth_service),
) -> RedirectResponse:
    """Redirect logged-in user to GitHub with public_repo scope for repo operations."""
    gh_config = config.settings.auth.providers.get("github")
    if not gh_config or not gh_config.client_id:
        raise HTTPException(status_code=404, detail="GitHub OAuth is not configured")

    # Require authenticated user
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = auth_service.verify_session(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired")

    provider = GitHubOAuthProvider(gh_config.client_id, gh_config.client_secret)
    state, binding = auth_service.create_oauth_state(flow="connect", user_id=user["id"])

    callback_url = str(request.url_for("github_oauth_callback"))
    # Build authorize URL with elevated scopes
    from urllib.parse import urlencode

    params = {
        "client_id": gh_config.client_id,
        "redirect_uri": callback_url,
        "scope": CONNECT_SCOPES,
        "state": state,
    }
    authorize_url = f"{provider.AUTHORIZE_URL}?{urlencode(params)}"

    response = RedirectResponse(url=authorize_url, status_code=302)
    _set_oauth_binding_cookie(response, binding, request)
    return response


@auth_router.get("/github/status")
async def github_connection_status(
    request: Request,
    config: PyriteConfig = Depends(get_config),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict:
    """Check if current user has a connected GitHub token."""
    gh_config = config.settings.auth.providers.get("github")
    if not gh_config or not gh_config.client_id:
        return {"connected": False, "github_configured": False}

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return {"connected": False, "github_configured": True}

    user = auth_service.verify_session(token)
    if not user:
        return {"connected": False, "github_configured": True}

    gh_token, scopes = auth_service.get_github_token_for_user(user["id"])
    if not gh_token:
        return {"connected": False, "github_configured": True}

    # Optionally verify token is still valid
    username = None
    try:
        from ..github_auth import get_github_user_info

        info = get_github_user_info(gh_token)
        if info:
            username = info.get("login")
        else:
            # Token invalid, clear it
            auth_service.clear_github_token(user["id"])
            return {"connected": False, "github_configured": True, "reason": "token_expired"}
    except Exception:
        pass

    return {
        "connected": True,
        "github_configured": True,
        "username": username,
        "scopes": scopes,
    }


@auth_router.delete("/github/connect")
async def github_disconnect(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> dict:
    """Disconnect GitHub by removing stored token."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = auth_service.verify_session(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired")

    auth_service.clear_github_token(user["id"])
    return {"ok": True, "message": "GitHub disconnected"}


# ── User API Key Management (BYOK) ────────────────────────────────────


class StoreApiKeyRequest(BaseModel):
    provider: str  # anthropic, openai, gemini, openrouter
    api_key: str
    model: str = ""


def _require_session_auth(request: Request, auth_service: AuthService) -> dict:
    """Authenticate via session cookie and return user dict, or raise 401."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = auth_service.verify_session(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    return user


@auth_router.get("/api-keys")
async def list_user_api_keys(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> dict:
    """List the current user's configured LLM providers (no keys exposed)."""
    user = _require_session_auth(request, auth_service)
    keys = auth_service.list_user_api_keys(user["id"])
    return {"keys": keys}


@auth_router.post("/api-keys")
async def store_user_api_key(
    body: StoreApiKeyRequest,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> dict:
    """Store or update the current user's API key for a provider."""
    user = _require_session_auth(request, auth_service)
    valid_providers = ("anthropic", "openai", "gemini", "openrouter", "ollama")
    if body.provider not in valid_providers:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider: {body.provider}. Must be one of: {', '.join(valid_providers)}",
        )
    result = auth_service.store_user_api_key(user["id"], body.provider, body.api_key, body.model)
    return result


@auth_router.delete("/api-keys/{provider}")
async def delete_user_api_key(
    provider: str,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> dict:
    """Delete the current user's API key for a provider."""
    user = _require_session_auth(request, auth_service)
    deleted = auth_service.delete_user_api_key(user["id"], provider)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No API key found for provider: {provider}")
    return {"ok": True, "provider": provider}


# ── Invite Code Management (admin only) ────────────────────────────────


class CreateInviteRequest(BaseModel):
    role: str = "write"
    note: str = ""
    expires_hours: int | None = None


@auth_router.post("/invite-codes")
async def create_invite_code(
    body: CreateInviteRequest,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
):
    """Create a new invite code (admin only)."""
    user = _require_session_auth(request, auth_service)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return auth_service.create_invite_code(
        created_by=user["username"],
        role=body.role,
        note=body.note,
        expires_hours=body.expires_hours,
    )


@auth_router.get("/invite-codes")
async def list_invite_codes(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
):
    """List all invite codes (admin only)."""
    user = _require_session_auth(request, auth_service)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return {"codes": auth_service.list_invite_codes()}


@auth_router.delete("/invite-codes/{code}")
async def delete_invite_code(
    code: str,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
):
    """Delete an unused invite code (admin only)."""
    user = _require_session_auth(request, auth_service)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        auth_service.delete_invite_code(code)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

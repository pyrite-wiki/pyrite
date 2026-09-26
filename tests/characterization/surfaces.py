"""KB-or-row-bearing REST operations and MCP tools, filtered from the one
shared inventory (`tests/_surface_inventory.py`, ADR-0037).

Theme 0's acceptance is "every REST operation and every MCP tool that takes
a KB or row resource". Two structurally-detected shapes cover most of it,
reusing the exact signals the two existing structural ratchets already trust
(so this module never re-derives a third notion of "takes a KB"):

- REST: a route's dependant tree contains one of the read-scoping
  dependencies `tests/test_read_scoping_is_structural.py` walks
  (`authz.authorize(Action.KB_READ, KB | AnyKB)`, which replaced
  `requires_kb_read()` and `get_readable_kbs` in ADR-0037 theme 3a) or one of
  the write-scoping guards `tests/test_kb_write_guard_is_structural.py`
  walks (`requires_kb_tier`'s named and row forms).
- MCP: a tool's `inputSchema` declares one of `KB_ARGUMENT_NAMES`, or its
  handler opts into the cross-KB `readable_kbs` keyword
  (`PyriteMCPServer._handler_takes_readable_kbs`) -- the same test
  `tests/test_mcp_tool_registry_is_scoped.py` runs.

**A third shape exists, and it is not structurally detectable: a route or
tool that decides per-KB access INLINE**, reading `request.state.auth_user`/
`api_role` directly and calling `AuthService.get_kb_role`/
`resolve_kb_default_role` itself rather than through one of the two
dependency shapes above. `surfaces.py` used to call this whole remainder
"instance administration... [with] no per-KB principal matrix to
characterize" -- wrong for exactly this shape, and cold review (#476 blocker
4) caught it: `GET`/`POST /api/kbs/{name}/permissions` decide admin-or-KB-
admin access for a NAMED KB inline (`pyrite/server/endpoints/admin.py`'s
`list_kb_permissions`/`manage_kb_permission`), invisible to both dependency
walks, and were silently uncharacterized. `INLINE_ACCESS_DECIDING_ROUTES`
below is the explicit, hand-maintained, individually-verified list of these
-- unioned into `kb_bearing_rest_operations`'s output -- because there is no
dependency shape to detect them by; each entry names why it belongs there,
the same discipline `ALLOWLIST` uses for the opposite case.

**What's left out is `REST_ACCESS_EXCLUSIONS`/`MCP_ACCESS_EXCLUSIONS`: an
explicit, counted, per-route reason, never a blanket label.** Every
excluded route/tool was read, not assumed: `PUT`/`DELETE /api/kbs/{name}`,
reindex, `default-role`, `publish`/`commit`/`push`, and the MCP
`kb_registry_*` family all gate on `requires_tier("admin")` (REST) or the
admin TOOL tier (MCP) alone -- instance-wide, with no per-KB branch in the
handler, unlike `permissions` above -- so a `{readable, private, missing}`
axis has nothing to vary over (`private` and `readable` answer identically
for a global admin, and a migration to per-KB policy has no home to move
this check TO, because it is not one). `/api/settings*` and `/auth/users*`
name no KB at all (a settings key, a user id). `/api/admin/merge-queue*`
gates on `requires_tier("admin")` alone by design (an admin reviews every
KB's pending worktrees, private or not) -- the `kb=` query filter narrows
the listing, it does not gate it. `/site/*` and `/api/index/sync` are
already-documented public/instance-wide surfaces (`/site/*` serves a
static, pre-rendered cache with no auth dependency at all, the same
"anonymous, always-public" class ADR-0037 §5 names for SEO/branding
routes -- confirmed by reading `pyrite/server/static.py`).
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass

from fastapi.routing import APIRoute

from pyrite.server.api import KB_PARAM_NAMES  # noqa: F401 -- re-exported for goldens' use
from pyrite.server.mcp_server import KB_ARGUMENT_NAMES, NON_KB_CONTENT_TOOLS, PyriteMCPServer

# Qualified names of every dependency that scopes a route to a KB or a row's
# KB -- the union of the two existing structural ratchets' own lists.
_READ_SCOPING = {
    # ADR-0037 theme 3a: `authz.authorize(Action.KB_READ, KB | AnyKB)`, which
    # replaced `requires_kb_read()` and `get_readable_kbs` route for route.
    "pyrite.server.authz.authorize.<locals>.authorize_kb_read_named",
    "pyrite.server.authz.authorize.<locals>.authorize_kb_read_any",
}
_WRITE_SCOPING = {
    "pyrite.server.api.requires_kb_tier.<locals>._check_kb_tier",
    "pyrite.server.api.requires_kb_tier.<locals>._check_row_kb_tier",
}
KB_SCOPING_DEPENDENCIES = _READ_SCOPING | _WRITE_SCOPING

# Routes that decide per-KB access INLINE (no dependency shape catches
# them): read, verified against `pyrite/server/endpoints/admin.py`
# (#476 blocker 4). Both call `auth_service.get_kb_role(auth_user["id"],
# name, kb_default_role)` for the NAMED KB when the caller is not a global
# admin -- a real per-KB decision, so both get a real golden case.
INLINE_ACCESS_DECIDING_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/kbs/{name}/permissions"): (
        "list_kb_permissions calls AuthService.get_kb_role(user, name, "
        "default_role) inline when the caller is not a global admin -- a "
        "per-KB admin-or-not decision no FastAPI dependency expresses."
    ),
    ("POST", "/api/kbs/{name}/permissions"): (
        "manage_kb_permission (grant/revoke) makes the identical inline "
        "per-KB decision before granting or revoking a permission on the "
        "named KB."
    ),
    # The four worktree routes (#476 round-2 issue 2's route-completeness
    # sweep): each takes `kb` (query or body) and calls
    # `WorktreeService.get_worktree`/`submit`/`reset_to_main`(kb, user_id)
    # inline -- gated only by `requires_tier("read"/"write")` (instance-
    # wide, no KB_SCOPING_DEPENDENCIES shape) -- so naming a KB is itself a
    # per-(kb, caller) decision, even though `WorktreeService` keys strictly
    # off row existence (kb_name, user_id), never off the caller's ROLE on
    # that KB: naming a private KB with no worktree row answers exactly the
    # same "none"/empty shape as naming a readable or missing one (verified
    # by reading `pyrite/server/endpoints/worktree.py` directly -- no
    # leaked content either way). Characterized anyway, per this dict's own
    # rule: it names why a route belongs here, not whether today's answer
    # looks safe.
    ("GET", "/api/worktree/status"): (
        "worktree_status calls WorktreeService.get_worktree(kb, user_id) inline; "
        "only requires_tier('read') (instance-wide) gates it."
    ),
    ("GET", "/api/worktree/changes"): (
        "worktree_changes calls WorktreeService.get_worktree(kb, user_id) inline; "
        "only requires_tier('read') (instance-wide) gates it."
    ),
    ("POST", "/api/worktree/submit"): (
        "worktree_submit calls WorktreeService.submit(kb, user_id) inline; "
        "only requires_tier('write') (instance-wide) gates it."
    ),
    ("POST", "/api/worktree/reset"): (
        "worktree_reset calls WorktreeService.reset_to_main(kb, user_id) inline; "
        "only requires_tier('write') (instance-wide) gates it."
    ),
    # `entries/types`/`entries/type-schemas` (#476 round-2 issue 2): both
    # take an optional `kb` and read that KB's content (distinct entry
    # types; kb.yaml type schemas) with NO auth dependency on the route at
    # all -- not even an instance-wide tier gate, unlike every other route
    # in entries.py. Characterized here so the harness pins TODAY's actual
    # (unauthorized) behaviour rather than silently passing it over; filed
    # as its own bug (#496) rather than fixed in this test-only branch.
    ("GET", "/api/entries/types"): (
        "list_entry_types reads svc.get_distinct_types(kb_name=kb) for the named "
        "KB with NO auth dependency on the route at all (#496)."
    ),
    ("GET", "/api/entries/type-schemas"): (
        "list_type_schemas reads the named KB's kb.yaml type definitions with NO "
        "auth dependency on the route at all (#496)."
    ),
}

# Routes with NO per-KB component at all: read and verified individually,
# never a blanket "administration" label (#476 blocker 4's own complaint).
# Pinned count so growing this list is a reviewed, visible diff, not a
# silent one -- see `test_rest_matrix.py::test_surface_counts_are_pinned`.
REST_ACCESS_EXCLUSIONS: dict[tuple[str, str], str] = {
    ("PUT", "/api/kbs/{name}"): "requires_tier('admin') only; update_kb has no per-KB branch.",
    ("DELETE", "/api/kbs/{name}"): (
        "requires_tier('admin') only; remove_kb's only per-KB fact is "
        "source=='config' (KBProtectedError), not the caller's role on it."
    ),
    ("POST", "/api/kbs/{name}/reindex"): "requires_tier('admin') only; no per-KB branch.",
    ("PUT", "/api/kbs/{name}/default-role"): "requires_tier('admin') only; no per-KB branch.",
    ("POST", "/api/kbs/{kb_name}/publish"): "requires_tier('admin') only; no per-KB branch.",
    ("POST", "/api/kbs/{kb_name}/commit"): "requires_tier('admin') only; no per-KB branch.",
    ("POST", "/api/kbs/{kb_name}/push"): "requires_tier('admin') only; no per-KB branch.",
    ("POST", "/api/index/sync"): "requires_tier('admin') only; syncs every KB, not one.",
    ("GET", "/api/settings"): "names no KB at all -- instance-wide configuration.",
    ("PUT", "/api/settings"): "names no KB at all -- instance-wide configuration.",
    ("GET", "/api/settings/{key}"): "names a settings key, not a KB.",
    ("PUT", "/api/settings/{key}"): "names a settings key, not a KB.",
    ("DELETE", "/api/settings/{key}"): "names a settings key, not a KB.",
    ("GET", "/auth/users"): "names no KB at all -- instance-wide user list, _ADMIN_ONLY.",
    ("PUT", "/auth/users/{user_id}/role"): "names a user id, not a KB; _ADMIN_ONLY.",
    ("GET", "/auth/users/{user_id}/permissions"): (
        "names a user id, not a KB; the response lists that user's per-KB "
        "grants, but the ACCESS GATE is _ADMIN_ONLY regardless of which "
        "KBs appear in the answer."
    ),
    ("GET", "/api/admin/merge-queue"): (
        "requires_tier('admin') only, by design -- an admin reviews every "
        "KB's pending worktrees; the optional kb= query narrows the "
        "listing, it does not gate it."
    ),
    (
        "GET",
        "/api/admin/merge-queue/{username}/diff",
    ): "requires_tier('admin') only, same as above.",
    (
        "POST",
        "/api/admin/merge-queue/{username}/merge",
    ): "requires_tier('admin') only, same as above.",
    (
        "POST",
        "/api/admin/merge-queue/{username}/reject",
    ): "requires_tier('admin') only, same as above.",
    (
        "GET",
        "/site",
    ): "static, pre-rendered cache; no auth dependency at all (anonymous, always-public).",
    ("GET", "/site/{path:path}"): "static, pre-rendered cache; no auth dependency at all.",
    ("GET", "/site/search"): "static, pre-rendered cache; no auth dependency at all.",
    ("GET", "/site/sitemap.xml"): "static, pre-rendered cache; no auth dependency at all.",
    ("GET", "/site/robots.txt"): "static, pre-rendered cache; no auth dependency at all.",
    ("GET", "/site/_static/{name}"): "static asset serving; no auth dependency at all.",
    # ---- #476 round-2 issue 2: the remaining 41 of the 49 routes a full
    # create_app() sweep found neither characterized nor excluded. Each was
    # read (its handler body, not just its path), not assumed -- see the
    # round-2 report on #476 for the sweep itself.
    #
    # KB registry / ephemeral / admin: requires_tier('admin') only, same
    # shape as the already-excluded PUT/DELETE /api/kbs/{name} above.
    ("POST", "/api/kbs"): (
        "create_kb creates a NEW kb (or ephemeral kb); no EXISTING KB's role to "
        "check, same reasoning as kb_registry_add's exclusion."
    ),
    ("POST", "/api/kbs/gc"): (
        "gc_ephemeral_kbs sweeps every expired ephemeral KB instance-wide; "
        "requires_tier('admin') only, no per-KB branch."
    ),
    ("GET", "/api/kbs/ephemeral"): (
        "list_ephemeral_kbs lists all of them; requires_tier('admin') only, no per-KB branch."
    ),
    ("DELETE", "/api/kbs/ephemeral/{name}"): (
        "force_expire_ephemeral_kb; requires_tier('admin') only, its only "
        "per-name fact is 404-if-missing, not the caller's role on it."
    ),
    ("POST", "/api/kbs/ephemeral"): (
        "create_ephemeral_kb creates a NEW kb for the caller; only checks "
        "auth_user is present, no per-KB branch."
    ),
    ("GET", "/api/index/jobs"): "requires_tier('admin') only; job_id is an index job, not a KB.",
    ("GET", "/api/index/jobs/{job_id}"): (
        "requires_tier('admin') only; job_id is an index job, not a KB."
    ),
    ("GET", "/api/admin/usage"): (
        "requires_tier('admin') only; instance-wide per-user usage listing."
    ),
    # User/session/account-scoped: self-owned data, no KB param anywhere.
    ("GET", "/api/ai/status"): "instance AI config; no KB param, no auth dependency at all.",
    ("POST", "/api/ai/test"): (
        "requires_tier('write') only; pings the provider with the operator's own key, no KB param."
    ),
    ("GET", "/api/usage/me"): "scoped to the caller's own user_id only, no KB param.",
    ("PUT", "/api/starred/reorder"): (
        "requires_tier('write') only; reorders the caller's OWN starred list by "
        "owner identity, no kb/kb_name param anywhere."
    ),
    # Plugin/instance introspection: no KB param anywhere.
    ("GET", "/api/plugins"): "lists all installed plugins instance-wide, no KB param.",
    ("GET", "/api/plugins/{name}"): (
        "name is a plugin name, not a KB; 404 if not installed, no per-KB branch."
    ),
    ("GET", "/api/collections/types"): (
        "built-in + plugin collection type listing; no kb param anywhere."
    ),
    # Repo management: creates/discovers NEW repos, no existing KB's role to
    # check -- same shape as kb_registry_add.
    ("POST", "/api/repos/fork"): (
        "forks a NEW GitHub repo by remote_url; no dependency on the route at "
        "all, only precondition is 'GitHub connected'."
    ),
    ("POST", "/api/repos/subscribe"): (
        "subscribes to a NEW remote repo by remote_url; no dependency on the route at all."
    ),
    ("GET", "/api/github/repos"): (
        "lists the caller's OWN GitHub account's repos via their stored token; "
        "no KB param, no dependency on the route."
    ),
    # Site cache / index: instance-wide, admin, or fully public.
    ("POST", "/api/site/render"): (
        "requires_tier('admin') only; renders every /site page, no single-KB branch."
    ),
    ("GET", "/api/index/embed-status"): (
        "instance-wide embedding queue status, no KB param, no auth dependency."
    ),
    # Auth / session / GitHub OAuth / API keys / invite codes: keyed by user
    # identity or global admin, zero kb/kb_name anywhere in any of these.
    ("POST", "/auth/register"): "no KB param anywhere; creates the caller's account.",
    ("POST", "/auth/login"): "no KB param anywhere; authenticates the caller.",
    ("POST", "/auth/logout"): "no KB param anywhere; ends the caller's session.",
    ("GET", "/auth/me"): (
        "no KB param anywhere; the response body includes the caller's OWN "
        "kb_permissions map as informational output, not a gate."
    ),
    ("GET", "/auth/config"): "no KB param anywhere; instance-wide auth configuration.",
    ("GET", "/auth/github"): "no KB param anywhere; starts the caller's OAuth flow.",
    ("GET", "/auth/github/callback"): "no KB param anywhere; OAuth callback.",
    ("GET", "/auth/github/connect"): "no KB param anywhere; links the caller's GitHub account.",
    ("DELETE", "/auth/github/connect"): (
        "no KB param anywhere; unlinks the caller's GitHub account."
    ),
    ("GET", "/auth/github/status"): "no KB param anywhere; the caller's own link status.",
    ("GET", "/auth/api-keys"): "no KB param anywhere; the caller's own API keys.",
    ("POST", "/auth/api-keys"): "no KB param anywhere; creates the caller's own API key.",
    ("DELETE", "/auth/api-keys/{provider}"): (
        "no KB param anywhere; provider names an OAuth provider, not a KB."
    ),
    ("POST", "/auth/invite-codes"): "no KB param anywhere; admin-issued invite codes.",
    ("GET", "/auth/invite-codes"): "no KB param anywhere; lists invite codes.",
    ("DELETE", "/auth/invite-codes/{code}"): "no KB param anywhere; code is an invite code.",
    # Public/static/no-auth: same class as the already-excluded /site/* entries.
    ("GET", "/health"): "infra probe; no auth dependency, no KB param.",
    ("GET", "/robots.txt"): (
        "public SEO route mounted outside /api (no auth), per seo_endpoints.py's own docstring."
    ),
    ("GET", "/sitemap.xml"): (
        "public SEO route mounted outside /api (no auth), per seo_endpoints.py's own docstring."
    ),
    ("GET", "/viewer"): "static SPA file serving, include_in_schema=False, no auth dependency.",
    ("GET", "/viewer/{path:path}"): (
        "static SPA file serving, include_in_schema=False, no auth dependency."
    ),
    ("GET", "/branding/{filename}"): (
        "mounted outside /api (no auth) so the login page can fetch branding "
        "before the caller is authenticated, per branding_endpoints.py's own "
        "docstring."
    ),
    ("GET", "/config/branding"): (
        "mounted outside /api (no auth) so the login page can fetch branding "
        "before the caller is authenticated, per branding_endpoints.py's own "
        "docstring."
    ),
}
REST_ACCESS_EXCLUSIONS_COUNT = len(REST_ACCESS_EXCLUSIONS)

# The MCP kb_registry_* family: admin TOOL tier only (ADMIN_TOOLS in
# tool_schemas.py), the same "instance-wide, no per-KB branch" shape as
# their REST admin.py counterparts above -- read and verified individually
# in pyrite/server/mcp_server.py.
MCP_ACCESS_EXCLUSIONS: dict[str, str] = {
    "kb_registry_add": "admin tool tier only; registers a NEW KB, so there is no existing KB to vary over.",
    "kb_registry_remove": (
        "admin tool tier only; remove_kb's only per-KB fact is "
        "source=='config' (KBProtectedError), not the caller's role."
    ),
    "kb_registry_reindex": "admin tool tier only; no per-KB branch beyond KBNotFoundError.",
    "kb_registry_health": "admin tool tier only; no per-KB branch beyond KBNotFoundError.",
    # Already excluded from kb_bearing_mcp_tool_names's OWN output (it
    # subtracts NON_KB_CONTENT_TOOLS, mcp_server.py's own authoritative "no
    # KB content" list) -- added here too (#476 round-2 issue 2's
    # completeness sweep) so the reasoning is visible in the one place a
    # reviewer checks coverage, not only in production code.
    "kb_index_job_status": (
        "NON_KB_CONTENT_TOOLS (mcp_server.py): background job state keyed by job id, not a KB."
    ),
}
MCP_ACCESS_EXCLUSIONS_COUNT = len(MCP_ACCESS_EXCLUSIONS)

# Every route `create_app()` mounts that is NOT an `APIRoute` -- a Starlette
# `Route`/`Mount` (the `/mcp` transport) or an `APIWebSocketRoute` (`/ws`) --
# so `test_every_rest_route_is_covered`'s `APIRoute`-only walk cannot see it
# at all, and it was silently absent from both `kb_bearing_rest_operations`
# and `REST_ACCESS_EXCLUSIONS` (#498). Each is COVERED, not excluded: its
# connection-level auth is pinned directly by
# `tests/characterization/test_mcp_transport_auth.py` (`_resolve_bearer_auth`
# / `_authenticate` for `/mcp/sse` and `/mcp/info`; `/mcp/messages/` shares
# the SAME per-connection ctx the SSE handshake already resolved -- the SDK
# transport relays JSON-RPC over an established session, it does not
# authenticate a second time) and by `tests/test_websocket_scoping.py`
# (`/ws`'s own `resolve_socket_scope`, built on the identical
# `mcp_routes._resolve_credential` + `AccessPolicy`). Named here, with a
# reason, so `test_every_rest_route_is_covered`'s walk (extended to include
# these) can report them "covered by name" the same way an `APIRoute` is
# covered by appearing in `kb_bearing_rest_operations`.
#
# #498 round-2 cold read: the original walk only looked INSIDE the `/mcp`
# Mount and for `APIWebSocketRoute`s -- a `Route`/`Mount` appended anywhere
# else at the TOP LEVEL of `app.routes` (a scratch top-level `Route` added
# during review passed every test in this file) was invisible to it. The
# walk below now looks at every top-level route, recursing into every
# `Mount` wherever it appears (not just one named `/mcp`), so nothing new
# mounted at the top level -- transport or not -- can land uncovered again.
TRANSPORT_ROUTE_EXCLUSIONS: dict[str, str] = {
    "GET /mcp/sse": (
        "Starlette Route, not an APIRoute -- negotiates the long-lived SSE "
        "transport. Its connection-level auth (_resolve_bearer_auth via "
        "_authenticate) is pinned directly by test_mcp_transport_auth.py."
    ),
    "GET /mcp/info": (
        "Starlette Route, not an APIRoute -- connection metadata for "
        "frontends. Calls the identical _authenticate as /mcp/sse; pinned "
        "by test_mcp_transport_auth.py's TestClient case."
    ),
    "POST /mcp/messages/": (
        "A Starlette Mount (mcp_routes.handle_messages, wrapping "
        "sse_transport.handle_post_message), not an APIRoute. Each POST's "
        "own credential is resolved and its principal placed in the ASGI "
        "scope's `user`, where the SDK's same-owner check compares it with "
        "the principal that opened the session: a message acts only for "
        "that principal, and any other caller gets the unknown-session 404. "
        "The tier and readable/writable KB sets are resolved once, at "
        "/mcp/sse connect, and the session ends with that credential "
        "(mcp_sessions). Pinned by test_mcp_transport_auth.py's "
        "TestMCPMessagesSessionCredential and TestMCPMessagesScopeFixedAtConnect, "
        "and end to end by tests/test_mcp_sse_session.py."
    ),
    "WS /ws": (
        "APIWebSocketRoute, not an APIRoute -- its handshake auth "
        "(resolve_socket_scope, built on the same mcp_routes._resolve_credential "
        "+ AccessPolicy) is pinned by tests/test_websocket_scoping.py."
    ),
}
TRANSPORT_ROUTE_EXCLUSIONS_COUNT = len(TRANSPORT_ROUTE_EXCLUSIONS)

# Non-APIRoute top-level constructs that are NOT transport at all -- FastAPI's
# own docs/schema routes (plain Starlette `Route`s, not `APIRoute`s, so the
# REST walk misses these too). Each read, not assumed, the same discipline
# REST_ACCESS_EXCLUSIONS uses.
#
# NOT listed here: mount_static's `app.mount("/_app", StaticFiles(...))`,
# `GET /favicon.ico` and the `GET /{path:path}` SPA fallback
# (pyrite/server/static.py) -- all three mount only when `web/dist/index.html`
# exists on disk, which it does on the main checkout and on any contributor's
# tree after `npm run build`. #504 round 2: a first version of this fix
# listed `/_app` here on the reasoning "this harness's create_app() never has
# a built web/dist" -- true only by accident of which tree happened to run
# the tests, and false the moment someone ran the frontend build first
# (three completeness tests went red with no code change at all: the two
# static APIRoutes were newly uncovered REST routes, and `/_app` was a newly
# uncovered transport-completeness Mount). The real fix is in `world.py`:
# `_create_app_with_empty_static` points `PYRITE_STATIC_DIR` at an
# always-empty directory this harness itself creates, so `mount_static`'s own
# `if not index_html.exists(): return` guard makes it -- and the two static
# APIRoutes -- a no-op in `world.app` regardless of whether the real repo's
# `web/dist` exists. Nothing to exclude here because nothing is ever mounted.
NON_TRANSPORT_ROUTE_EXCLUSIONS: dict[str, str] = {
    "GET /openapi.json": (
        "FastAPI's own schema route, a plain Starlette Route (not an "
        "APIRoute) auto-added by FastAPI(...) -- no auth dependency, no KB "
        "param; serves the generated OpenAPI schema."
    ),
    "GET /docs": (
        "FastAPI's own Swagger UI route, a plain Starlette Route -- no auth "
        "dependency, no KB param."
    ),
    "GET /docs/oauth2-redirect": (
        "FastAPI's own Swagger OAuth2 redirect route, a plain Starlette "
        "Route -- no auth dependency, no KB param."
    ),
    "GET /redoc": (
        "FastAPI's own ReDoc route, a plain Starlette Route -- no auth dependency, no KB param."
    ),
}
NON_TRANSPORT_ROUTE_EXCLUSIONS_COUNT = len(NON_TRANSPORT_ROUTE_EXCLUSIONS)


def _walk_non_apiroutes(routes, prefix: str = "") -> set[str]:
    """Every non-`APIRoute` entry anywhere in `routes`, recursing into every
    `Mount` (not just one named `/mcp`) and into FastAPI's `_IncludedRouter`
    wrapper (which can itself hold a `Mount`, even though today it only ever
    holds `APIRoute`s). Named ``"{METHOD} {full_path}"`` for a `Route`,
    ``"WS {full_path}"`` for an `APIWebSocketRoute`, and ``"MOUNT {full_path}"``
    for a `Mount` whose own sub-app is not itself walked further (a raw ASGI
    app, e.g. `StaticFiles`, has no `.routes` to recurse into) -- the same
    shape `TRANSPORT_ROUTE_EXCLUSIONS`/`NON_TRANSPORT_ROUTE_EXCLUSIONS` use.
    """
    from fastapi.routing import APIRoute, APIWebSocketRoute
    from starlette.routing import Mount, Route

    out: set[str] = set()
    for route in routes:
        full_path = prefix + getattr(route, "path", "")
        if isinstance(route, APIRoute):
            continue
        if type(route).__name__ == "_IncludedRouter":
            sub = route.original_router
            out |= _walk_non_apiroutes(sub.routes, prefix + (sub.prefix or ""))
        elif isinstance(route, APIWebSocketRoute):
            out.add(f"WS {full_path}")
        elif isinstance(route, Mount):
            sub_routes = getattr(route.app, "routes", None)
            if sub_routes is not None:
                # A Mount with real sub-routes (e.g. /mcp): recurse, naming
                # each sub-route's own method(s), same as before. A nested
                # Mount with no `.methods` of its own (sse_transport's raw
                # ASGI /messages/ app) is POST-only per mcp_routes.py's own
                # docstring -- there is nothing else to introspect on it.
                for sub in sub_routes:
                    sub_full = full_path + getattr(sub, "path", "")
                    if isinstance(sub, Mount):
                        out.add(f"POST {sub_full}/")
                    else:
                        for method in sorted(getattr(sub, "methods", None) or ()):
                            if method in ("HEAD", "OPTIONS"):
                                continue
                            out.add(f"{method} {sub_full}")
            else:
                # A raw ASGI app with no route list to walk (StaticFiles and
                # similar) -- named as the Mount itself, not a method+path.
                out.add(f"MOUNT {full_path}")
        elif isinstance(route, Route):
            for method in sorted(getattr(route, "methods", None) or ()):
                if method in ("HEAD", "OPTIONS"):
                    continue
                out.add(f"{method} {full_path}")
    return out


def non_apiroute_transport_routes(app) -> set[str]:
    """Every non-`APIRoute` top-level construct of `app`, wherever it is
    mounted -- not just inside `/mcp` or a bare `APIWebSocketRoute` (#498
    round-2: a top-level `Route` added anywhere else in `app.routes` was
    invisible to the original, `/mcp`-only walk). Covers both the real
    transport routes (`TRANSPORT_ROUTE_EXCLUSIONS`) and anything that is not
    transport at all (`NON_TRANSPORT_ROUTE_EXCLUSIONS`) -- the completeness
    test diffs this single set against the union of both.
    """
    return _walk_non_apiroutes(app.routes)


def _dependency_names(dependant) -> set[str]:
    names: set[str] = set()
    for dep in dependant.dependencies:
        call = dep.call
        module = getattr(call, "__module__", "?")
        qualname = getattr(call, "__qualname__", repr(call))
        names.add(f"{module}.{qualname}")
        names |= _dependency_names(dep)
    return names


@dataclass(frozen=True)
class RestCase:
    method: str
    path: str
    route: APIRoute


def kb_bearing_rest_operations(app) -> list[RestCase]:
    """Every (method, path, route) whose dependant tree scopes it to a KB
    or a row's KB, UNIONED with `INLINE_ACCESS_DECIDING_ROUTES` (no
    dependency shape catches those). Walks the same routes
    `tests/_surface_inventory.py` enumerates."""
    from tests._surface_inventory import _walk_routes

    out = []
    for path, route in _walk_routes(app.routes):
        if not isinstance(route, APIRoute):
            continue
        scoped = bool(_dependency_names(route.dependant) & KB_SCOPING_DEPENDENCIES)
        for method in sorted(route.methods or ()):
            if method in ("HEAD", "OPTIONS"):
                continue
            if scoped or (method, path) in INLINE_ACCESS_DECIDING_ROUTES:
                out.append(RestCase(method, path, route))
    return out


def kb_bearing_mcp_tool_names(server: PyriteMCPServer) -> list[str]:
    """Every tool name that names a KB argument or opts into the cross-KB
    `readable_kbs` keyword, excluding `NON_KB_CONTENT_TOOLS`."""
    out = []
    for name, meta in server.tools.items():
        if name in NON_KB_CONTENT_TOOLS:
            continue
        schema = meta.get("inputSchema") or {}
        props = set((schema.get("properties") or {}).keys())
        if (props & set(KB_ARGUMENT_NAMES)) or server._handler_takes_readable_kbs(name):
            out.append(name)
    return sorted(out)


def handler_signature_params(handler) -> set[str]:
    """Parameter names of a tool handler, for tests that need to know
    whether a handler takes `readable_kbs`/`writable_kbs`."""
    try:
        return set(inspect.signature(handler).parameters)
    except (TypeError, ValueError):  # pragma: no cover - exotic callables
        return set()

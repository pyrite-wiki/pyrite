"""Every entry point passes the access policy -- a ratchet (ADR-0037 §5).

The policy (`pyrite/services/access_policy.py`) is the one place an access
decision is made; each surface asks it through one adapter. This test is
what makes skipping the adapter a CI failure instead of a review finding. It
walks the one shared inventory of entry points (`tests/_surface_inventory.py`,
the list `test_layer_boundaries.py` walks too):

- **every REST operation**, `/api` and `/auth` and the public routes: its
  dependant tree -- what FastAPI actually runs for it -- must contain
  **exactly one** `authz.authorize(...)` declaration. None means the route
  never asked; two means it asked two different questions and a reader
  cannot tell which one governs.
- **every MCP tool**, core and plugin, at the admin tier: its registration
  must resolve an `Action` (ADR-0037 theme 4), and a registered handler may
  be taken out of the registry only by `_dispatch_tool`, the chokepoint that
  asks (plus signature introspection, which calls nothing).
- **the entry points neither walk sees** (`/mcp`, a `Mount`; `/ws`, a
  `WebSocketRoute`) are named, so a third one cannot appear unrecorded.

**What is not yet migrated is listed, and the lists only shrink.**
`REST_NOT_YET_MIGRATED` and `MCP_NOT_YET_MIGRATED` hold the entry points
whose migration theme has not landed, each with that theme. Their sizes are
pinned (`*_SIZE`); an entry that has since been migrated, or no longer
exists, fails `test_the_lists_hold_only_what_is_still_owed`, so the room it
leaves cannot be reused. Adding an entry is not the fix for a new route:
declare `authorize(...)` on it. `PUBLIC_ENTRY_POINTS` is the other list:
routes that answer before anyone is authenticated (login, the OAuth dance,
SEO, branding, health, the static site and viewer), each with a reason; it
is pinned too, and no `/api` operation may be on it unless it is also on
`PUBLIC_API_OPERATIONS` (empty) with a reason.

**A read declaration does not settle a write.** A non-GET operation whose
only declaration is `authorize(Action.KB_READ, ...)` fails unless it is on
`REST_WRITE_TIER_NOT_YET_MIGRATED` (its write or admin tier is still decided
by `requires_tier` or an inline check: what 3b/3c owe) or on
`REST_READS_OVER_POST` (a POST that only reads). Both are pinned.

Not here yet, by the ADR's own sequencing: the grep ratchet over role
comparisons (§5.3) turns on in theme 5, and the generated principal matrix
(§5.4) is theme 0's characterization harness (`tests/characterization/`),
whose goldens every migration theme leaves unchanged.
"""

from __future__ import annotations

import ast
import functools

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute

from pyrite.server import authz
from pyrite.services.access_policy import KB, Action, AnyKB, ReadScope
from tests._surface_inventory import (
    NON_ROUTE_ENTRY_POINTS,
    REPO_ROOT,
    mcp_server,
    mcp_tools,
    rest_operations,
)

pytestmark = pytest.mark.core

_UNCHANGED_SURFACE = pytest.mark.control(
    reason="a ratchet over what this PR does not change (the MCP registry, /mcp, /ws) "
    "or list bookkeeping that holds on either side of it: it guards the next change"
)

_SELF_TEST = pytest.mark.control(
    reason="tests the guard itself (its walk, its detection, or its list bookkeeping): "
    "true on either side of the change that adds it"
)

# =============================================================================
# The lists
# =============================================================================

#: Answer before anyone is authenticated, by design. Each with its reason.
PUBLIC_ENTRY_POINTS: dict[str, str] = {
    "POST /auth/login": "signing in: there is no principal yet",
    "POST /auth/register": "creating an account: there is no principal yet",
    "GET /auth/config": "the login page reads which sign-in methods exist before signing in",
    "GET /auth/github": "starts the OAuth dance, before there is a principal",
    "GET /auth/github/callback": "the OAuth dance's return leg, before there is a principal",
    "GET /health": "infrastructure probe; no KB content",
    "GET /robots.txt": "public SEO route (seo_endpoints.py)",
    "GET /sitemap.xml": "public SEO route (seo_endpoints.py)",
    "GET /branding/{filename}": "the login page fetches branding before signing in",
    "GET /config/branding": "the login page fetches branding before signing in",
    "GET /site": "the static site: a pre-rendered public cache",
    "GET /site/{path:path}": "the static site: a pre-rendered public cache",
    "GET /site/search": "the static site: a pre-rendered public cache",
    "GET /site/sitemap.xml": "the static site: a pre-rendered public cache",
    "GET /site/robots.txt": "the static site: a pre-rendered public cache",
    "GET /site/_static/{name}": "the static site's assets",
    "GET /viewer": "the SPA's static files; the API calls it makes are guarded",
    "GET /viewer/{path:path}": "the SPA's static files; the API calls it makes are guarded",
    "GET /favicon.ico": "the SPA's static favicon; mounted unconditionally (#538)",
    "GET /{path:path}": (
        "the SPA catch-all for client-side routing; mounted unconditionally (#538) -- "
        "404s itself for /api, /mcp, /auth, /ws instead of serving the SPA shell"
    ),
}
PUBLIC_ENTRY_POINTS_SIZE = 20  # lower it with every entry removed; never raise it

#: The `/api` operations allowed in `PUBLIC_ENTRY_POINTS`, each with its reason.
#: None today: everything under `/api` sits behind `verify_api_key`, so a public
#: `/api` entry is a read route escaping the policy, not a login page.
PUBLIC_API_OPERATIONS: dict[str, str] = {}
PUBLIC_API_OPERATIONS_SIZE = 0  # never raise it

#: REST operations whose migration theme has not landed. Only shrinks.
REST_NOT_YET_MIGRATED: dict[str, str] = {
    "POST /api/clip": "3b: requires_kb_tier('write'), a per-KB write",
    "POST /api/collections": "3b: requires_kb_tier('write'), a per-KB write",
    "POST /api/daily/{date_str}": "3b: requires_kb_tier('write'), a per-KB write",
    "POST /api/entries": "3b: requires_kb_tier('write'), a per-KB write",
    "POST /api/entries/import": "3b: requires_kb_tier('write'), a per-KB write",
    "DELETE /api/entries/{entry_id}": "3b: requires_kb_tier('write'), a per-KB write",
    "PATCH /api/entries/{entry_id}": "3b: requires_kb_tier('write'), a per-KB write",
    "PUT /api/entries/{entry_id}": "3b: requires_kb_tier('write'), a per-KB write",
    "POST /api/reviews": "3b: requires_kb_tier('write'), a per-KB write",
    "DELETE /api/reviews/{review_id}": "3b: requires_kb_tier('write', resolve_kb=...), a row's KB",
    "POST /api/tasks/{task_id}/claim": "3b: requires_kb_tier('write'), a per-KB write",
    "GET /api/admin/merge-queue": "3c: requires_tier('admin'), instance administration",
    "GET /api/admin/merge-queue/{username}/diff": (
        "3c: requires_tier('admin'), instance administration"
    ),
    "POST /api/admin/merge-queue/{username}/merge": (
        "3c: requires_tier('admin'), instance administration"
    ),
    "POST /api/admin/merge-queue/{username}/reject": (
        "3c: requires_tier('admin'), instance administration"
    ),
    "GET /api/admin/usage": "3c: requires_tier('admin'), instance administration",
    "GET /api/ai/status": "3c: instance AI configuration, the router floor only",
    "POST /api/ai/test": "3c: requires_tier('write'), instance AI configuration",
    "GET /api/collections/types": (
        "3c: instance collection-type descriptors, the router floor only"
    ),
    "GET /api/github/repos": "3c: Action.SELF, the caller's own GitHub account",
    "GET /api/index/embed-status": "3c: instance embedding queue, the router floor only",
    "GET /api/index/jobs": "3c: requires_tier('admin'), instance administration",
    "GET /api/index/jobs/{job_id}": "3c: requires_tier('admin'), instance administration",
    "POST /api/index/sync": "3c: requires_tier('admin'), instance administration",
    "POST /api/kbs": "3c: requires_tier('admin'), instance administration",
    "GET /api/kbs/ephemeral": "3c: requires_tier('admin'), instance administration",
    "POST /api/kbs/ephemeral": "3c: instance; checks only that a user is signed in",
    "DELETE /api/kbs/ephemeral/{name}": "3c: requires_tier('admin'), instance administration",
    "POST /api/kbs/gc": "3c: requires_tier('admin'), instance administration",
    "POST /api/kbs/{kb_name}/commit": "3c: requires_tier('admin'), git operations (git_ops.py)",
    "POST /api/kbs/{kb_name}/publish": "3c: requires_tier('admin'), git operations (git_ops.py)",
    "POST /api/kbs/{kb_name}/push": "3c: requires_tier('admin'), git operations (git_ops.py)",
    "DELETE /api/kbs/{name}": "3c: requires_tier('admin'), instance administration",
    "PUT /api/kbs/{name}": "3c: requires_tier('admin'), instance administration",
    "PUT /api/kbs/{name}/default-role": "3c: requires_tier('admin'), instance administration",
    "GET /api/kbs/{name}/permissions": "3c: the inline grant-role check in admin.py",
    "POST /api/kbs/{name}/permissions": "3c: the inline grant-role check in admin.py",
    "POST /api/kbs/{name}/reindex": "3c: requires_tier('admin'), instance administration",
    "GET /api/plugins": "3c: instance plugin listing, the router floor only",
    "GET /api/plugins/{name}": "3c: instance plugin listing, the router floor only",
    "POST /api/repos/fork": "3c: Action.REPO_EGRESS, mapped to today's tier",
    "POST /api/repos/subscribe": "3c: Action.REPO_EGRESS, mapped to today's tier",
    "GET /api/settings": "3c: settings_ep.py, the inline secret-key role check",
    "PUT /api/settings": "3c: requires_tier('write') and settings_ep.py's inline check",
    "DELETE /api/settings/{key}": "3c: requires_tier('write') and settings_ep.py's inline check",
    "GET /api/settings/{key}": "3c: settings_ep.py, the inline secret-key role check",
    "PUT /api/settings/{key}": "3c: requires_tier('write') and settings_ep.py's inline check",
    "POST /api/site/render": "3c: requires_tier('admin'), instance administration",
    "PUT /api/starred/reorder": "3c: requires_tier('write'), Action.SELF (the caller's stars)",
    "GET /api/usage/me": "3c: Action.SELF, the caller's own usage",
    "GET /api/worktree/changes": "3c: requires_tier('read'), worktree.py",
    "POST /api/worktree/reset": "3c: requires_tier('write'), worktree.py",
    "GET /api/worktree/status": "3c: requires_tier('read'), worktree.py",
    "POST /api/worktree/submit": "3c: requires_tier('write'), worktree.py",
    "GET /auth/api-keys": "3c: Action.SELF",
    "POST /auth/api-keys": "3c: Action.SELF",
    "DELETE /auth/api-keys/{provider}": "3c: Action.SELF",
    "DELETE /auth/github/connect": "3c: Action.SELF",
    "GET /auth/github/connect": "3c: Action.SELF",
    "GET /auth/github/status": "3c: Action.SELF",
    "GET /auth/invite-codes": "3c: instance administration (invite codes)",
    "POST /auth/invite-codes": "3c: instance administration (invite codes)",
    "DELETE /auth/invite-codes/{code}": "3c: instance administration (invite codes)",
    "POST /auth/logout": "3c: Action.SELF",
    "GET /auth/me": "3c: Action.SELF",
    "GET /auth/users": "3c: _ADMIN_ONLY, Action.USER_MANAGE",
    "GET /auth/users/{user_id}/permissions": "3c: _ADMIN_ONLY, Action.USER_MANAGE",
    "PUT /auth/users/{user_id}/role": "3c: _ADMIN_ONLY, Action.USER_MANAGE",
}
REST_NOT_YET_MIGRATED_SIZE = 68  # lower it with every entry removed; never raise it

#: Non-GET operations that pass the policy only with a *read* declaration while
#: their write or admin tier is still decided by `requires_tier`, the repos
#: guard or an inline check. They count as migrated for the read half only;
#: this is what themes 3b/3c owe on them. Only shrinks.
REST_WRITE_TIER_NOT_YET_MIGRATED: dict[str, str] = {
    "DELETE /api/repos/{name:path}": (
        "3b: requires_tier('write') and _repo_kb_guard's inline admin tier per KB"
    ),
    "POST /api/repos/{name:path}/sync": (
        "3b: requires_tier('write') and _repo_kb_guard's inline write tier per KB"
    ),
    "POST /api/repos/{name:path}/pr": (
        "3b: requires_tier('write') and _repo_kb_guard's inline write tier per KB"
    ),
    "POST /api/ai/auto-tag": "3c: requires_tier('write') on the ai router (ai_ep.py)",
    "POST /api/ai/chat": "3c: requires_tier('write') on the ai router (ai_ep.py)",
    "POST /api/ai/suggest-links": "3c: requires_tier('write') on the ai router (ai_ep.py)",
    "POST /api/ai/summarize": "3c: requires_tier('write') on the ai router (ai_ep.py)",
    "POST /api/starred": "3c: requires_tier('write'), Action.SELF (starred.py)",
    "DELETE /api/starred/{entry_id}": "3c: requires_tier('write'), Action.SELF (starred.py)",
    "POST /api/kbs/{kb_name}/export": "3c: requires_tier('write') (kbs.py)",
}
REST_WRITE_TIER_NOT_YET_MIGRATED_SIZE = 10  # lower it with every entry removed; never raise it

#: Non-GET operations that only read: a POST because the request carries a
#: body, not because it changes anything. A read declaration is their whole
#: answer. Each with its reason; pinned.
REST_READS_OVER_POST: dict[str, str] = {
    "POST /api/entries/batch": "batch read of entries named in the body",
    "POST /api/entries/resolve-batch": "resolves wikilink targets named in the body",
    "POST /api/collections/query-preview": "evaluates a collection query without saving it",
    "POST /api/kbs/{kb_name}/templates/{template_name}/render": (
        "renders a template's text for the caller; writes nothing"
    ),
}
REST_READS_OVER_POST_SIZE = 4  # a new entry is a reviewed diff to this constant

#: MCP tools that do not resolve an `Action` yet: all of them until theme 4.
MCP_NOT_YET_MIGRATED: frozenset[str] = frozenset(
    {
        "cascade_actors",
        "cascade_capture_lanes",
        "cascade_network",
        "cascade_timeline",
        "investigation_bulk_edges",
        "investigation_claims",
        "investigation_create_claim",
        "investigation_create_entity",
        "investigation_create_event",
        "investigation_entities",
        "investigation_evidence_chain",
        "investigation_export_pack",
        "investigation_find_duplicates",
        "investigation_ftm_export",
        "investigation_ftm_import",
        "investigation_log_source",
        "investigation_money_flow",
        "investigation_network",
        "investigation_ownership_chain",
        "investigation_promote_claim",
        "investigation_qa_report",
        "investigation_search_all",
        "investigation_sources",
        "investigation_start",
        "investigation_status",
        "investigation_timeline",
        "kb_backlinks",
        "kb_batch_read",
        "kb_batch_suggest",
        "kb_bulk_create",
        "kb_commit",
        "kb_create",
        "kb_delete",
        "kb_discover_neighbors",
        "kb_find_by_assignee",
        "kb_find_by_location",
        "kb_find_by_status",
        "kb_find_overdue",
        "kb_get",
        "kb_index_job_status",
        "kb_index_sync",
        "kb_link",
        "kb_list",
        "kb_list_entries",
        "kb_manage",
        "kb_orient",
        "kb_push",
        "kb_qa_assess",
        "kb_qa_status",
        "kb_qa_validate",
        "kb_read_body",
        "kb_recent",
        "kb_registry_add",
        "kb_registry_health",
        "kb_registry_reindex",
        "kb_registry_remove",
        "kb_schema",
        "kb_search",
        "kb_stats",
        "kb_tags",
        "kb_timeline",
        "kb_update",
        "list_edge_types",
        "social_newest",
        "social_post",
        "social_reputation",
        "social_top",
        "social_vote",
        "solidarity_infrastructure_types",
        "solidarity_timeline",
        "sw_adrs",
        "sw_backlog",
        "sw_board",
        "sw_check_ready",
        "sw_claim",
        "sw_component",
        "sw_context_for_item",
        "sw_conventions",
        "sw_create_adr",
        "sw_create_backlog_item",
        "sw_epic_detail",
        "sw_epics",
        "sw_log",
        "sw_milestones",
        "sw_prioritize",
        "sw_pull_next",
        "sw_refine",
        "sw_review",
        "sw_review_queue",
        "sw_standards",
        "sw_submit",
        "sw_transition",
        "sw_validations",
        "task_ancestors",
        "task_blocked_by",
        "task_checkpoint",
        "task_claim",
        "task_create",
        "task_critical_path",
        "task_decompose",
        "task_list",
        "task_status",
        "task_subtree",
        "task_update",
        "wiki_assess_quality",
        "wiki_protect",
        "wiki_quality_stats",
        "wiki_review_queue",
        "wiki_stubs",
        "wiki_submit_review",
        "zettel_graph",
        "zettel_inbox",
    }
)
MCP_NOT_YET_MIGRATED_SIZE = 112  # lower it with every entry removed; never raise it

#: Where a tool handler may be read out of the registry: (file, function).
HANDLER_READ_SITES = {
    ("pyrite/server/mcp_server.py", "PyriteMCPServer._dispatch_tool"): "the chokepoint: calls it",
    ("pyrite/server/mcp_server.py", "PyriteMCPServer._handler_takes_readable_kbs"): (
        "inspect.signature only; calls nothing"
    ),
}

#: Non-route entry points, with what covers them until the theme that moves them.
NON_ROUTE_COVERAGE = {
    "/mcp": "its tools, enumerated below (theme 4 makes them resolve an Action)",
    "/ws": "theme 5: /ws asks read_scope; today tests/test_websocket_scoping.py",
}

# =============================================================================
# The walk
# =============================================================================


def _authorize_declarations(dependant) -> set:
    """The distinct `authz.authorize(...)` callables in a dependant tree.

    `authorize` is cached per (action, resource), so a declaration repeated
    on a router and as a parameter is one callable, counted once.
    """
    found = set()
    for dep in dependant.dependencies:
        call = dep.call
        if getattr(call, "__module__", "") == authz.__name__ and getattr(
            call, "__qualname__", ""
        ).startswith("authorize.<locals>."):
            found.add(call)
        found |= _authorize_declarations(dep)
    return found


_SPA_STATIC_OPERATIONS = {"GET /favicon.ico", "GET /{path:path}"}


def _operations(app=None) -> dict[str, APIRoute]:
    """name -> route for every REST operation (HEAD/OPTIONS are the framework's)."""
    from pyrite.server.api import create_app

    app = app or create_app()
    from tests._surface_inventory import _walk_routes

    out = {}
    for path, route in _walk_routes(app.routes):
        for method in sorted(route.methods or ()):
            name = f"{method} {path}"
            if method not in ("HEAD", "OPTIONS") and name not in _SPA_STATIC_OPERATIONS:
                out[name] = route
    return out


# "Exactly one" stays exactly one when 3b/3c migrate the routes in
# REST_WRITE_TIER_NOT_YET_MIGRATED: a route asks the policy ONE question, the
# strongest it needs, and never a read declaration beside a write one. A
# per-KB write declares authorize(Action.KB_WRITE, KB), which subsumes the read
# (the policy answers NOT_FOUND for an unreadable KB before FORBIDDEN for a
# readable one, ADR-0037 §4). A route that needs a global tier *and* a per-KB
# read (the ai router: write tier, read on the named KB) gets its own Action
# or Resource in the policy, decided in 3b/3c -- not two declarations -- so
# the §5.4 matrix has one declared answer per route to compare against.
def check_rest(
    operations: dict[str, APIRoute],
    public: dict[str, str],
    not_yet: dict[str, str],
) -> None:
    """Fail naming every operation that skips the policy or asks it twice."""
    skipped, doubled = [], []
    for name, route in sorted(operations.items()):
        declared = _authorize_declarations(route.dependant)
        if len(declared) > 1:
            doubled.append(f"{name}  ({len(declared)} authorize declarations)")
        elif not declared and name not in public and name not in not_yet:
            where = f"{route.endpoint.__module__}.{route.endpoint.__qualname__}"
            skipped.append(f"{name}  ({where})")
    assert not (skipped or doubled), (
        "REST operations that do not pass the access policy exactly once:\n  "
        + "\n  ".join(skipped + doubled)
        + "\nDeclare it on the route, e.g.\n"
        "  dependencies=[Depends(authorize(Action.KB_READ, KB))]\n"
        "or take the scope: scope: ReadScope = Depends(authorize(Action.KB_READ, AnyKB)).\n"
        "Adding the route to REST_NOT_YET_MIGRATED is not the fix; that list only shrinks."
    )


def check_lists_owe(
    operations: dict[str, APIRoute], public: dict[str, str], not_yet: dict[str, str]
) -> None:
    """A listed operation that is gone, or now declares `authorize`, must leave its list."""
    stale = []
    for name in sorted({**public, **not_yet}):
        if name not in operations:
            stale.append(f"{name}  (no such operation)")
        elif _authorize_declarations(operations[name].dependant):
            stale.append(f"{name}  (passes the policy now: remove it)")
    assert not stale, "stale entries:\n  " + "\n  ".join(stale)


def _is_read_declaration(call) -> bool:
    """`authorize(Action.KB_READ, ...)`: its closure is named for the action."""
    return "_kb_read_" in call.__qualname__


def _only_reads(route: APIRoute) -> bool:
    declared = _authorize_declarations(route.dependant)
    return bool(declared) and all(_is_read_declaration(c) for c in declared)


def check_write_tiers(
    operations: dict[str, APIRoute],
    owed: dict[str, str],
    reads_over_post: dict[str, str],
) -> None:
    """A non-GET operation whose only declaration is a read must be owed (its
    write tier is decided elsewhere until 3b/3c) or be a read over POST; a
    listed operation that is gone, GET, or no longer read-only must leave."""
    unlisted, stale = [], []
    for name, route in sorted(operations.items()):
        if name.startswith("GET ") or not _only_reads(route):
            continue
        if name not in owed and name not in reads_over_post:
            where = f"{route.endpoint.__module__}.{route.endpoint.__qualname__}"
            unlisted.append(f"{name}  ({where})")
    for name in sorted({**owed, **reads_over_post}):
        if name not in operations:
            stale.append(f"{name}  (no such operation)")
        elif name.startswith("GET ") or not _only_reads(operations[name]):
            stale.append(f"{name}  (no longer a non-GET with only a read declaration: remove it)")
    assert not unlisted, (
        "non-GET operations that pass the policy only with a read declaration:\n  "
        + "\n  ".join(unlisted)
        + "\nDeclare the write it makes (authorize(Action.KB_WRITE, ...)), or, if it only "
        "reads, list it in REST_READS_OVER_POST with the reason."
    )
    assert not stale, "stale write-tier entries:\n  " + "\n  ".join(stale)


def check_public(public: dict[str, str], allowed_api: dict[str, str]) -> None:
    """No `/api` operation is public unless it is on the explicit short list."""
    api = sorted(
        name
        for name in public
        if name.split(" ", 1)[1].startswith("/api") and name not in allowed_api
    )
    assert not api, (
        "/api operations in PUBLIC_ENTRY_POINTS -- everything under /api answers to the "
        "policy; declare authorize(...) instead:\n  " + "\n  ".join(api)
    )


@functools.cache
def _real_operations() -> dict[str, APIRoute]:
    return _operations()


# =============================================================================
# REST
# =============================================================================


def test_every_rest_operation_passes_the_policy_exactly_once():
    check_rest(_real_operations(), PUBLIC_ENTRY_POINTS, REST_NOT_YET_MIGRATED)


@pytest.mark.parametrize("built", [False, True], ids=["no-web-build", "web-build"])
def test_policy_guard_is_independent_of_web_build(tmp_path, monkeypatch, built):
    dist = tmp_path / "dist"
    dist.mkdir()
    if built:
        (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setenv("PYRITE_STATIC_DIR", str(dist))

    operations = _operations()
    assert set(operations) == set(_real_operations())
    check_rest(operations, PUBLIC_ENTRY_POINTS, REST_NOT_YET_MIGRATED)


@_UNCHANGED_SURFACE
def test_the_lists_hold_only_what_is_still_owed():
    check_lists_owe(_real_operations(), PUBLIC_ENTRY_POINTS, REST_NOT_YET_MIGRATED)


def test_non_get_routes_declare_their_write_or_are_owed():
    check_write_tiers(_real_operations(), REST_WRITE_TIER_NOT_YET_MIGRATED, REST_READS_OVER_POST)


def test_no_api_operation_is_public():
    check_public(PUBLIC_ENTRY_POINTS, PUBLIC_API_OPERATIONS)


@_SELF_TEST
def test_the_lists_only_shrink():
    pinned = {
        "PUBLIC_ENTRY_POINTS": (PUBLIC_ENTRY_POINTS, PUBLIC_ENTRY_POINTS_SIZE),
        "PUBLIC_API_OPERATIONS": (PUBLIC_API_OPERATIONS, PUBLIC_API_OPERATIONS_SIZE),
        "REST_WRITE_TIER_NOT_YET_MIGRATED": (
            REST_WRITE_TIER_NOT_YET_MIGRATED,
            REST_WRITE_TIER_NOT_YET_MIGRATED_SIZE,
        ),
        "REST_READS_OVER_POST": (REST_READS_OVER_POST, REST_READS_OVER_POST_SIZE),
    }
    for label, (listed, size) in pinned.items():
        assert len(listed) == size, (
            f"{label} has {len(listed)} entries, its _SIZE is {size}. Removing an entry: "
            "lower the size to match. Adding one is a reviewed change to the constant."
        )
        for name, reason in listed.items():
            assert reason.strip(), f"{label}: {name} has no reason"
    assert not set(REST_WRITE_TIER_NOT_YET_MIGRATED) & set(REST_READS_OVER_POST)
    assert len(REST_NOT_YET_MIGRATED) == REST_NOT_YET_MIGRATED_SIZE, (
        f"REST_NOT_YET_MIGRATED has {len(REST_NOT_YET_MIGRATED)} entries, "
        f"REST_NOT_YET_MIGRATED_SIZE is {REST_NOT_YET_MIGRATED_SIZE}. Removing an entry: "
        "lower the size to match. Adding one is not the fix: declare authorize(...)."
    )
    assert len(MCP_NOT_YET_MIGRATED) == MCP_NOT_YET_MIGRATED_SIZE, (
        f"MCP_NOT_YET_MIGRATED has {len(MCP_NOT_YET_MIGRATED)} entries, "
        f"MCP_NOT_YET_MIGRATED_SIZE is {MCP_NOT_YET_MIGRATED_SIZE}. Lower it; never raise it."
    )
    assert not set(PUBLIC_ENTRY_POINTS) & set(REST_NOT_YET_MIGRATED)
    for name, reason in {**PUBLIC_ENTRY_POINTS, **REST_NOT_YET_MIGRATED}.items():
        assert reason.strip(), f"{name} has no reason"


@_SELF_TEST
def test_the_walk_sees_the_whole_app():
    """The inventory found the app (not a stub), and every operation it
    served is one this test decided about."""
    ops = _real_operations()
    assert len(ops) > 130
    assert any(name.startswith("GET /api/") for name in ops)
    assert any(name.startswith("POST /auth/") for name in ops)
    inventory = {
        ep.name
        for ep in rest_operations()
        if ep.name.split()[0] != "HEAD" and ep.name not in _SPA_STATIC_OPERATIONS
    }
    assert set(ops) == inventory


# -- the guard catches what it says ----------------------------------------------


def _toy_app() -> FastAPI:
    app = FastAPI()
    named = authz.authorize(Action.KB_READ, KB)
    spanning = authz.authorize(Action.KB_READ, AnyKB)

    @app.get("/api/skips")
    def skips():
        return {}

    @app.get("/api/declares", dependencies=[Depends(named)])
    def declares():
        return {}

    @app.get("/api/declares-and-takes", dependencies=[Depends(named)])
    def declares_and_takes(scope: ReadScope = Depends(named)):
        return {}

    @app.get("/api/asks-twice", dependencies=[Depends(named)])
    def asks_twice(scope: ReadScope = Depends(spanning)):
        return {}

    def nested(scope: ReadScope = Depends(spanning)):
        return scope

    @app.get("/api/nested", dependencies=[Depends(nested)])
    def through_a_helper():
        return {}

    @app.post("/api/writes-behind-a-read", dependencies=[Depends(named)])
    def writes_behind_a_read():
        return {}

    return app


@_SELF_TEST
def test_a_non_get_route_with_only_a_read_declaration_fails_unless_listed():
    ops = {k: v for k, v in _operations(_toy_app()).items() if "asks-twice" not in k}
    with pytest.raises(AssertionError, match=r"POST /api/writes-behind-a-read  \("):
        check_write_tiers(ops, {}, {})
    check_write_tiers(ops, {"POST /api/writes-behind-a-read": "3b"}, {})
    check_write_tiers(ops, {}, {"POST /api/writes-behind-a-read": "reads a body"})
    with pytest.raises(AssertionError, match=r"GET /api/declares  \(no longer a non-GET"):
        check_write_tiers(
            ops, {"POST /api/writes-behind-a-read": "3b", "GET /api/declares": "x"}, {}
        )


@_SELF_TEST
def test_an_api_route_listed_as_public_fails_by_name():
    public = {"GET /health": "probe", "GET /api/entries": "a read route slipped in"}
    with pytest.raises(AssertionError, match=r"GET /api/entries"):
        check_public(public, PUBLIC_API_OPERATIONS)
    check_public(public, {"GET /api/entries": "listed on purpose, with a reason"})


@_SELF_TEST
def test_a_route_that_skips_the_policy_fails_by_name():
    ops = _operations(_toy_app())
    with pytest.raises(AssertionError) as err:
        check_rest(ops, {}, {})
    message = str(err.value)
    assert "GET /api/skips" in message
    assert "GET /api/asks-twice  (2 authorize declarations)" in message
    for fine in ("/api/declares ", "/api/declares-and-takes", "/api/nested"):
        assert fine not in message


@_SELF_TEST
def test_a_listed_route_passes_and_a_stale_listing_fails():
    ops = {k: v for k, v in _operations(_toy_app()).items() if "asks-twice" not in k}
    check_rest(ops, {"GET /api/skips": "public"}, {})
    check_rest(ops, {}, {"GET /api/skips": "theme 3c"})
    with pytest.raises(AssertionError, match=r"GET /api/declares  \(passes the policy now"):
        check_lists_owe(ops, {}, {"GET /api/declares": "theme 3c"})
    with pytest.raises(AssertionError, match=r"GET /api/gone  \(no such operation"):
        check_lists_owe(ops, {"GET /api/gone": "public"}, {})


# =============================================================================
# MCP
# =============================================================================


def _tool_action(meta: dict) -> Action | None:
    action = meta.get("action")
    return action if isinstance(action, Action) else None


@_UNCHANGED_SURFACE
def test_every_mcp_tool_resolves_an_action_or_is_owed():
    with mcp_server() as server:
        tools = {t.name: server.tools[t.name] for t in mcp_tools(server)}
    assert len(tools) > 50
    unresolved = sorted(
        n
        for n, meta in tools.items()
        if _tool_action(meta) is None and n not in MCP_NOT_YET_MIGRATED
    )
    stale = sorted(
        n for n in MCP_NOT_YET_MIGRATED if n not in tools or _tool_action(tools[n]) is not None
    )
    assert not unresolved, (
        "MCP tools that resolve no Action (declare one at registration, ADR-0037 theme 4):\n  "
        + "\n  ".join(unresolved)
    )
    assert not stale, (
        "MCP_NOT_YET_MIGRATED entries that are gone or resolve an Action now "
        "(remove them and lower MCP_NOT_YET_MIGRATED_SIZE):\n  " + "\n  ".join(stale)
    )


def handler_read_sites(source: str, rel: str) -> set[tuple[str, str]]:
    """(file, enclosing function) for every read of a tool's ``["handler"]``."""
    found: set[tuple[str, str]] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack: list[str] = []

        def _scoped(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node):  # noqa: N802 -- ast.NodeVisitor's naming
            self._scoped(node)

        def visit_AsyncFunctionDef(self, node):  # noqa: N802
            self._scoped(node)

        def visit_ClassDef(self, node):  # noqa: N802
            self._scoped(node)

        def visit_Subscript(self, node):
            s = node.slice
            if (
                isinstance(s, ast.Constant)
                and s.value == "handler"
                and isinstance(node.ctx, ast.Load)
            ):
                found.add((rel, ".".join(self.stack) or "<module>"))
            self.generic_visit(node)

    Visitor().visit(ast.parse(source))
    return found


def _all_handler_read_sites() -> set[tuple[str, str]]:
    sites: set[tuple[str, str]] = set()
    for path in sorted((REPO_ROOT / "pyrite").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        sites |= handler_read_sites(path.read_text(), rel)
    return sites


@_UNCHANGED_SURFACE
def test_a_tool_handler_is_taken_from_the_registry_only_by_the_chokepoint():
    sites = _all_handler_read_sites()
    extra = sorted(f"{f}::{fn}" for f, fn in sites - set(HANDLER_READ_SITES))
    gone = sorted(f"{f}::{fn}" for f, fn in set(HANDLER_READ_SITES) - sites)
    assert not extra, (
        "a tool handler is read out of the registry outside _dispatch_tool, so it can be "
        "called without asking the policy:\n  " + "\n  ".join(extra)
    )
    assert not gone, "HANDLER_READ_SITES entries that no longer exist:\n  " + "\n  ".join(gone)


@_SELF_TEST
def test_a_handler_called_outside_the_chokepoint_is_found():
    source = (
        "class PyriteMCPServer:\n"
        "    def shortcut(self, name, args):\n"
        "        return self.tools[name]['handler'](args)\n"
    )
    assert handler_read_sites(source, "pyrite/server/x.py") == {
        ("pyrite/server/x.py", "PyriteMCPServer.shortcut")
    }


# =============================================================================
# Entry points neither walk sees
# =============================================================================


@_UNCHANGED_SURFACE
def test_the_non_route_entry_points_are_the_recorded_ones():
    from pyrite.server.api import create_app

    mounted = {
        getattr(r, "path", None)
        for r in create_app().routes
        if not isinstance(r, APIRoute) and getattr(r, "path", "").startswith(("/mcp", "/ws"))
    }
    assert mounted == set(NON_ROUTE_ENTRY_POINTS) == set(NON_ROUTE_COVERAGE)

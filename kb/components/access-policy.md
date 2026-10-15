---
id: access-policy
title: Access Policy
type: component
tags:
- core
- service
- auth
- authorization
importance: 5
kind: service
path: pyrite/services/access_policy.py
owner: core
links:
- target: adr-0037
  relation: related_to
  kb: pyrite
dependencies:
- pyrite.services.auth_service
- pyrite.services.kb_registry_service
---

The one place an access decision is made (ADR-0037 §1, #383). Framework-free: it imports nothing from FastAPI, Starlette, MCP or Typer. REST (through `pyrite/server/authz.py`, and for writes and instance routes still the dependencies in `api.py`), MCP (`mcp_routes.py`) and the live-update socket (`websocket.py`) build a `Principal` and ask it; none compares roles itself.

## What it owns

- **The role ladder**: `ROLES`, `ROLE_LEVELS`, `role_at_least`, `lower_role`. Written out nowhere else under `pyrite/` (`tests/test_role_ladder_has_one_home.py`).
- **The API-key role**: `resolve_api_key_role`.
- **A KB's default role**: config first, then the index registry (`KBRegistryService.registered_default_role`), honoured only as far as `PyriteConfig.confined_default_role` allows under an untrusted config.
- **The per-KB rule**: `kb_role` (global admin, grant, KB default role with the self-registered read cap, global role only with `global_access`, anonymous ceiling). `AuthService.get_kb_role` fetches the rows and asks it.
- **The effective role and the readable/writable sets**: `AccessPolicy.effective_kb_role`, `kbs_at_tier`, `read_scope`, `write_scope` (a `ReadScope`/`WriteScope` only the policy constructs).
- **Concealment**: `authorize(p, action, KB(k) | Row(k))` checks existence before role, so an unreadable KB answers `NOT_FOUND` exactly like a missing one; `FORBIDDEN` when readable but below the rung; `UNAUTHENTICATED` with no principal.

## Vocabulary

`Principal` (user, anonymous, operator_key, local), `Action` (the ADR's closed list; theme 1 decides the KB rungs and `INSTANCE_ADMIN`), `Resource` (`KB`, `Row`, `Instance`, `User`, `AnyKB`), `Decision`. `require` raises `KBNotFoundError` for a concealed or missing KB and `PolicyDeniedError` otherwise (folds into theme 2's `AccessDenied` family).

Services do not take a principal (ADR-0037, decision 3). The API helpers in `api.py` (`resolve_kb_default_role`, `kb_exists`, `effective_kb_role_for_user`, `kbs_for_user_at_tier`, `readable_kbs_for_user`) are delegates kept for their callers.

**The KB a request names is resolved once** (`services/kb_resolver.py`, #952). `authorize(principal, action, KB|Row)` calls `resolve_kb_for(principal, name)` first -- existence (config, the registry cache, then the live `kb` table for a KB `pyrite kb add` registered -- never the row the server seeds for a config.yaml KB, which carries none of the entry's attributes), readability and the shortname rule (looked up among the caller's readable KBs only) in one function -- and a NOT_FOUND `Decision` carries the resolver's `KBNotFoundError` (with `suggestion` and `did_you_mean`) for `require` to raise. `kb_exists` is that lookup with no scope. REST's `authz.authorize(KB_READ, KB)` calls the same function with the caller's `ReadScope.as_set()`; MCP's `_kb_refusal` with the connection's readable set; the CLI through `PyriteConfig.require_kb`. `tests/test_kb_resolver.py` pins the function and `tests/test_kb_walk.py` that every REST route and MCP tool asks it.

## REST's adapter: `pyrite/server/authz.py`

- `get_principal(request)`: the one place REST turns what `verify_api_key` recorded into a `Principal`; the `api.py` helpers that need the caller (`readable_kbs`, `get_llm_service`, `get_user_llm_context`, `get_repo_service`) read it through this.
- `authorize(action, resource)`: the one dependency a route declares (ADR-0037 §2). Cached per `(action, resource)`, so a declaration repeated on a router and as a parameter is one callable, run once. Theme 3a decides reads: `authorize(Action.KB_READ, KB)` resolves every KB the request names through the one resolver (404 `KB_NOT_FOUND` from the central handler for an unknown, unreadable or shortname KB, for every caller) and returns the caller's `ReadScope`; `authorize(Action.KB_READ, AnyKB)` returns the `ReadScope` alone for routes that span KBs. No principal is a 401, never "unscoped". Writes (3b) and instance/user routes (3c) raise `NotImplementedError` at declaration until their theme lands.
- The guard: `tests/test_every_entry_point_passes_the_policy.py` (ADR-0037 §5) fails for a REST operation without exactly one `authorize(...)` and an MCP tool without an `Action`, unless it is in `PUBLIC_ENTRY_POINTS` or on the not-yet-migrated lists, which only shrink.

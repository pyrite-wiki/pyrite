---
id: auth-service
title: Auth Service
type: component
kind: service
path: pyrite/services/auth_service.py
owner: core
dependencies:
- pyrite.storage
- bcrypt
tags:
- core
- service
- auth
---

Local username/password authentication with session tokens for the web UI. Uses bcrypt for password hashing and SHA-256 hashed opaque tokens for sessions. Supports per-KB permissions via the `kb_permission` table and ephemeral KB access control.

## Key Methods

### User & Session Management

- `create_user(username, password, role)` — the operator path (`pyrite-admin user create`); how the first admin is made
- `register(username, password, display_name, invite_code)` — web sign-up; refused (`RegistrationClosedError`) until an admin exists, never makes an admin. Without an invite the user gets `read` on public KBs only (`global_access` 0); an invite code is claimed by one conditional UPDATE in the same transaction as the insert, so one code makes at most one user
- `login(username, password)` — validates credentials, creates session token
- `oauth_login(profile, provider_config)` — create/update OAuth user, returns session; a new sign-up is refused until an admin exists, and gets `global_access` only through `allowed_orgs` or an `org_tier_map` match; an unvetted sign-up also obeys `allow_registration` and `require_invite_code`
- `set_role(user_id, role, global_access=None)` — `global_access` changes only when passed explicitly
- `logout(token)` — invalidates a session
- `verify_session(token)` — returns user info if token is valid

### GitHub Token Management

- `store_github_token(user_id, token, scopes)` — store GitHub OAuth token for a user (scope escalation)
- `get_github_token_for_user(user_id)` — returns `(token, scopes)` tuple; `(None, None)` if not stored
- `clear_github_token(user_id)` — remove stored GitHub token; returns `True` if user found

### Per-KB Permissions

- `get_kb_role(user_id, kb_name, kb_default_role)` — resolve effective role via: global admin → explicit grant → KB default_role → user global role → anonymous tier
- `grant_kb_permission(user_id, kb_name, role, granted_by)` — INSERT OR REPLACE per-KB grant
- `revoke_kb_permission(user_id, kb_name)` — remove per-KB grant
- `list_kb_permissions(kb_name)` — all grants for a KB
- `get_user_kb_permissions(user_id)` — all grants for a user (used by `/auth/me`)
- `create_user_ephemeral_kb(user_id, kb_service, name)` — create ephemeral KB with policy checks (min tier, max per user), auto-grants creator admin

## Permission Resolution Chain

```
effective_role(user, kb) =
    1. Global admin → always "admin"
    2. kb_permission[user_id, kb_name]   -- explicit per-KB grant
    3. kb.default_role                   -- KB-level default (capped at read
                                            for a user without global_access)
    4. user.role                         -- user's global role fallback, only
                                            when user.global_access (operator-
                                            vetted users; not self-registered)
    5. anonymous_tier                    -- anonymous fallback (if no
                                            credential was presented)
```

## Credential Resolution (#1754)

Who the caller is comes before the chain above, from one resolver,
`pyrite/server/credentials.py`, which REST (`verify_api_key`), `/mcp`
(`mcp_routes._resolve_credential`) and the live-update socket
(`websocket.resolve_socket_scope`) all ask. It answers *verified* (an operator
key, a session user, or open access with auth off and no keys), *none* (no
credential presented), or *failed* (one was presented and did not verify).
Only *none* may become the anonymous visitor; *failed* is 401 (a refused
handshake on the socket) whatever `anonymous_tier` says. The first credential
presented decides, in the order `Authorization: Bearer`, `X-API-Key`,
`?api_key` (REST and socket only), `pyrite_session` cookie; nothing weaker is
consulted. A REST 401 caused by the cookie clears it (it is `httponly`).

## REST Endpoints

Mounted at `/auth` (outside `/api` prefix) via `auth_endpoints.py`:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/register` | POST | Create new user |
| `/auth/login` | POST | Login, returns session token |
| `/auth/logout` | POST | Invalidate session |
| `/auth/me` | GET | Session introspection (includes `kb_permissions`) |
| `/auth/config` | GET | Public auth configuration |
| `/auth/github` | GET | Start GitHub OAuth flow |
| `/auth/github/callback` | GET | GitHub OAuth callback |
| `/auth/github/connect` | GET | Scope escalation: re-auth with `public_repo` scope (requires login) |
| `/auth/github/status` | GET | GitHub connection status (connected, scopes, github_configured) |
| `/auth/github/connect` | DELETE | Disconnect GitHub (clear stored token) |

KB permission management endpoints live under `/api/kbs/{name}/permissions` (see [[rest-api]]).

## Related

- [[rest-api]] — auth endpoints at root, KB permission endpoints under /api
- [[web-frontend]] — web UI consumes auth endpoints
- [[per-kb-permissions]] — design doc for per-KB ACL system
- [[configuration-system]] — `AuthConfig` ephemeral policy fields, `KBConfig.default_role`

## 2026-11-17 · small team server: `pyrite serve` with auth, admin key + write user + read user + anonymous, REST and MCP over HTTP · claude-sonnet-5-5 · build 96bb4936 (openapi.json says 0.26.3)
**Setup:** isolated env; `pyrite init -t research` twice (KBs `harbor`, `secret`); hand-edited `default_role: none` onto `secret` in `$PYRITE_DATA_DIR/config.yaml`; `pyrite-admin user create boss|wendy|rita --role admin|write|read --password ...`; server `PYRITE_AUTH_ENABLED=true PYRITE_API_KEY=<key> pyrite serve --host 127.0.0.1 --port 18421`; `anonymous_tier` unset. Users sign in with `POST /auth/login` (cookie); the "admin key" is `PYRITE_API_KEY` via `X-API-Key`. MCP over SSE with the Python `mcp` client (`/mcp/sse`). Server stopped at the end.
**Got (summary):** the permission model held everywhere I looked. Read user: list/read/search ok, every write 403 (REST) or FORBIDDEN (MCP). Write user: all ok on `harbor`, `secret` invisible. Anonymous (tier unset): 401 on everything, no KB name or entry text anywhere. With `PYRITE_AUTH_ANONYMOUS_TIER=read`: anonymous reads `harbor`, writes 403, `secret` stays hidden. Friction below.

**Friction 1 — a wrong or mistyped credential is treated as anonymous once `anonymous_tier: read` is set.** Command: `curl -H 'X-API-Key: wrong' /api/kbs` -> 200 with the KB list; `Authorization: Bearer wrong` on `/api/search` -> 200. Expected: 401 for a credential that does not match (it did 401 with tier unset). Got: silent downgrade; the person with the typo'd admin key sees reads work, then a write says `Insufficient permissions on KB 'harbor': requires 'write' tier`, with nothing saying "your key was not recognised". Would have helped: reject a presented-but-invalid credential even when anonymous is allowed. Severity: annoyed (confusing for an operator, and hides rotated/expired keys).

**Friction 2 — the 401 never says what to do.** Anonymous, tier unset: every REST route returns `{"detail":"Invalid or missing API key"}`; `/auth/me` says `Not authenticated`; `/mcp/sse` says `Invalid or missing authentication. Provide Authorization: Bearer <token> header.` Three different wordings; none mentions `POST /auth/login`, the session cookie or `X-API-Key`. For a user-account server, "API key" points at the wrong credential. A wrong key and no key are identical (good for secrecy). Would have helped: one 401 body naming the ways in (`sign in at /auth/login, or send X-API-Key`). Severity: annoyed.

**Friction 3 — 403 for the read user names the tier, not the remedy.** `PUT /api/entries/pothole-survey` as read user: `Insufficient permissions on KB 'harbor': requires 'write' tier`. The MCP form is better: `suggestion: "This tool needs the write role. Ask the operator for access."`. REST has no suggestion field. Severity: cosmetic.

**Friction 4 — anonymous MCP stays 401 under `anonymous_tier: read`.** `GET /mcp/sse` anonymous -> 401 `Provide Authorization: Bearer <token> header.` while REST reads work. Possibly intended, but docs/configuration.md says only "what an unauthenticated request may do"; rest-api.md says "no credential for what that tier allows" and neither mentions MCP. I had to try it. Would have helped: one sentence on whether `/mcp` honours the anonymous tier. Severity: slowed.

**Friction 5 — user agents and the admin key authenticate differently on MCP, undocumented.** The admin key works as `X-API-Key`; for users I used the session cookie value as `Authorization: Bearer <cookie value>` (a guess from last time's finding; it works). Still no doc says how a user's agent gets a credential. Same as 0.26.3 Friction 1. Severity: slowed.

**Friction 6 — my first create used `kb_name` and got a 422.** `POST /api/entries {"kb_name":"harbor",...}` -> `Unknown field(s) on a create: kb_name. A create accepts: allow_undeclared, body, ..., kb, ...`. The error lists the right field, so one retry fixed it, but REST uses `kb` (and `?kb=`) while MCP uses `kb_name`. Severity: annoyed.

**Friction 7 — MCP refusals come back as `isError: false`.** `kb_create` as read user and `kb_get` on `secret` both return a normal result with `error_code` in the JSON body (`FORBIDDEN`, `KB_NOT_FOUND`), plus a client-side `Tool kb_create not listed by server, cannot validate any structured content` for tools outside the user's tier. Same as 0.26.3 Friction 3. Severity: annoyed.

**Friction 8 — small things.** (a) `search "zebra"` by MCP says `"match":"weak"` for zero results; REST says `"none"`. (b) With no `[semantic]` extra the server logs two WARNING lines at startup about 5 queued entries pending embedding and "embedding service is unavailable"; harmless but alarming. (c) Startup warning "no KB has default_role read or write, so they can read nothing" is true for registrants, but under `anonymous_tier: read` anonymous visitors do read `harbor` (no `default_role`); the sentence reads as if nothing is visible. (d) `/docs` and `/openapi.json` are open to anonymous callers (no KB names in them; `/docs` loads Swagger from a CDN). (e) Hidden KB behaves identically to a nonexistent KB for write/read users (`KB_NOT_FOUND`, empty `did_you_mean`); a read user posting to `secret` gets 404 on REST but FORBIDDEN on MCP `kb_create`; neither leaks the name. Cosmetic.

**Tool counts:** read user 29 tools, write 40, admin key 48.

### Release blockers
None. No traceback, no data loss; no leak of hidden KB names or entry text to any principal, in errors too. Friction 1 is the one I would look at before an operator relies on this.

### Re-check of 0.26.3 findings (mcp-http-team)
- F1 (how a user's agent authenticates over MCP): Still there (Bearer = session cookie value still the only route I found).
- F2 (HTTP transport is SSE, only in Gemini/OpenAI pages): Still there (did not re-read the pages; `/mcp` still 307s to nothing useful, `/mcp/sse` is the mount).
- F3 (tier by role; refusals `isError: false`): Still there; refusal text improved: FORBIDDEN now says "Ask the operator for access."
- F4 (per-KB grants REST-only, `default_role` hand-edited): Still there (I hand-edited `default_role: none` again).
- F5 (web-ui.md vs configuration.md on non-admin landing): Not tested.
- F6 (`init -t research` registers `kb_type: generic`): Still there (config shows `kb_type: generic`).
- F7 (no authorship recorded): Not tested.
- Anonymous 401 wording ("API key" to someone meant to sign in): Still there.
- Hidden-KB refusal shape (KB_NOT_FOUND, no `did_you_mean` leak): Fixed/unchanged-good, identical on REST and MCP.

### What worked well
- `pyrite-admin user create --role ... --password ...` gave three principals in three lines.
- The role model matched the docs on the first try: read cannot write, write cannot see `default_role: none` KBs, the admin key sees all, anonymous sees nothing, and `anonymous_tier: read` gave read-only exactly as described, ceiling-wise, `secret` still hidden.
- `PYRITE_AUTH_ANONYMOUS_TIER` set as an env var took effect on restart without touching config.
- 422 on unknown create fields listed every accepted field.
- MCP FORBIDDEN carried `required_tier` and a human suggestion.
- Hidden KBs are indistinguishable from missing ones, in search, get, create and update.

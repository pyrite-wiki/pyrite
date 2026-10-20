---
type: design
id: multi-user-threat-model
title: "Multi-user threat model: principals, trust boundaries, properties, and the A1–A8 audit briefs"
status: draft
date: '2026-09-26'
author: 'claude-opus-5.5 (pyrite-spike T1, 0.26 security review)'
tags: [security, multi-user, threat-model, authorization, design]
links:
- target: multi-user-security-review-threat-model-surface-audits-live-personas-and-a-structural-guard
  relation: implements
  kb: pyrite
- target: adr-0037
  relation: related
  kb: pyrite
- target: adr-0036
  relation: related
  kb: pyrite
- target: hosting-security-requirements
  relation: related
  kb: pyrite
---

# Multi-user threat model

Theme T1 of the 0.26 security review
([[multi-user-security-review-threat-model-surface-audits-live-personas-and-a-structural-guard]]).
It gives the audits A1–A8 and the live session L1 a shared vocabulary:
**who** can cross **which boundary**, to reach **what**, and the **property**
that must hold there.

**What this document is not.** It lists no findings. It contains no attack
steps and no reproductions, and it makes no claim that current code is
vulnerable. Checking the code is the audits' job. Their findings go to the
conductor only, under the item's handling rule, and they are never written
here.

**What it builds on and does not restate:**

- [[hosting-security-requirements]] covers data minimisation, legal
  compulsion, IP logging, encryption at rest and container hardening
  (REQ-1 to REQ-8). This document covers what one principal of a running
  instance can do to another principal's assets. Where the two overlap
  (REQ-5 container user, REQ-6 MCP access), the property here links to the
  REQ.
- [[adr-0037]] defines the mechanism: `Principal`, `Action`, `Resource`,
  `authorize`, concealment in the policy, the §5 guard, and the error
  contract. This document uses that vocabulary. It states what the mechanism
  must achieve at each boundary, not how the mechanism works.
- [[adr-0036]] sets the lifetime of a `/ws` socket. [[adr-0031]] sets the
  API as the product surface and repo egress as a capability.

Measured on `dev` e0a6ae11 (the groom's base; the branch diff is KB only).

## 1. Deployment modes

The same code runs with very different trust boundaries. Every property
below is stated for **auth enabled** unless it says otherwise. Each audit
must say which modes its questions apply to.

| Mode | What sets it | Who is a principal | The boundary that remains |
|---|---|---|---|
| **M0 Local, no auth** | auth off, no `api_keys` | every request is admin | the host, and `RequestGuardMiddleware` (Host and Origin checks) against a browser |
| **M1 Keys only** | auth off, `api_keys` set | operator keys by role | the key |
| **M2 Auth, closed** | auth on, `anonymous_tier` unset, registration off or invite-only | sessions, keys | the full policy |
| **M3 Auth, open** | auth on, `allow_registration: true`, `require_invite_code: false` | anyone can become a registered user | the full policy, with "registered user" as a hostile principal |
| **M4 Auth, anonymous tier** | `anonymous_tier: read` or `write` | the anonymous visitor holds a rung | the full policy, with the anonymous tier as the floor |
| **Local process** | CLI, `pyrite mcp` (stdio), `pyrite-admin` | `Principal.local()`, the operator | the OS account and the filesystem |

**P-M1.** A mode is never inferred from partial configuration in the
permissive direction. For example, "auth on with no keys configured" must
not produce a more privileged principal than "auth on with keys
configured". This is the class of the 0.25-era key-role fix, stated as a
property.

**P-M2.** A request that reaches no credential check is exactly as
privileged as the mode says the anonymous visitor is, and no more.

## 2. Assets

| # | Asset | Includes | Primary goal against it |
|---|---|---|---|
| **S1** | Private KB content | Entry bodies, titles, ids, tags, field values, links and backlinks, versions (git history), attachments; and everything **derived** from them: FTS and embedding indexes, counts and stats, facets, graph neighbours, wanted links, suggestions, QA reports, task trees, export packs, the site cache, `/ws` event payloads, error messages | read it (confidentiality) |
| **S2** | The existence of a private KB | Its name, whether it exists, its size, its type | learn it (concealment) |
| **S3** | KB integrity | Create, update, delete and move of entries; KB settings, schema, templates, collections; git commits, pushes and the merge queue | change it without a write grant |
| **S4** | Instance control | Users, global roles, KB grants, `default_role`, the KB registry, settings, invite codes, operator keys | escalate |
| **S5** | Credentials | Session cookies, password hashes, operator API keys (and their hashes in config), per-user provider API keys (`/auth/api-keys`), GitHub OAuth tokens, OAuth `state`, LLM keys in settings (`SETTINGS_SECRET`) | steal or replay them |
| **S6** | The server host | The filesystem outside KB roots, the config dir, the index DB, the process (code execution), the container user | escape to it |
| **S7** | The server's network position | What the server can reach that the requester cannot: loopback, the private network, cloud metadata, authenticated GitHub | pivot through it |
| **S8** | Availability | The event loop, DB connections, rate limits, disk | exhaust it |

Account metadata, IP logs and what a court order could reach are
[[hosting-security-requirements]] REQ-1 to REQ-3. They are not repeated
here.

## 3. Principals

The first eight are **authorised principals**, each of which may turn
hostile. The last four are **untrusted inputs**, which reach the system
without being a principal at all.

| # | Principal | ADR-0037 `Principal` | Should reach | Must not reach |
|---|---|---|---|---|
| **U0** | Anonymous visitor | `anonymous(tier)` | public KBs (`default_role: read`), the public entry points; with `anonymous_tier`, that rung on KBs whose default allows it | any private KB (S1, S2); any S4 or S5 |
| **U1** | Registered user, no grant | `user(row)`, global role | what the global role and each KB's `default_role` give; their own profile, keys and tokens (`SELF`) | other users' `SELF`; private KBs with no grant |
| **U2** | User with a read grant on KB *k* | `user` | read *k* | write *k*; anything on another private KB |
| **U3** | User with a write grant on *k* | `user` | read and write *k* | KB-admin actions on *k*; writes to any other KB, including through a body, row id or bulk item that names another KB |
| **U4** | KB admin of *k* | `user` | *k*'s grants, `default_role` and settings | other KBs' grants; instance roles; granting more than the policy lets a KB admin grant |
| **U5** | Instance admin | `user`, global admin | S4 | nothing within the instance is forbidden; S6 and S7 still are, beyond what the admin UI offers |
| **U6** | Operator API key | `from_api_key(role)` | its role's rung, instance-wide (unscoped) | beyond its role; `SELF` actions of any user |
| **U7** | Local operator | `local()` | everything the OS account can reach | nothing: the OS is the boundary. The *content* the local process handles is still untrusted (U9–U10) |
| **U8** | A browser page the user visits | none (acts through the user's browser) | nothing | any state change or read through the user's cookie, or through M0's credential-less access |
| **U9** | Content from another principal | entries, templates, `kb.yaml`, collections, frontmatter written by U1–U4 | be stored and shown as data | executing in another user's browser (stored script); executing on the server; steering a later reader's agent into tool calls it did not intend |
| **U10** | A KB from a subscribed or forked repo | files written by whoever controls the remote | the same as U9 | the same as U9, plus anything a cloned repository's own configuration can make git or Pyrite do |
| **U11** | A remote host answering an outbound fetch | the clipper's target, a clone URL, an OAuth or LLM endpoint | return data | redirecting the server to S7 targets; oversized or slow answers (S8); content that becomes U9 |

**U12, an outside contributor's pull request,** runs code in CI, not in an
instance. That boundary belongs to
[[outside-prs-screen-before-anything-runs-review-after-ci-one-daily-routine]],
and no audit below covers it.

**MCP agents.** An agent that holds a legitimate MCP credential is its
principal (U1–U7). The arguments it sends are untrusted input, because the
agent may have read U9 or U10 content (prompt injection). The property is
that **a tool call is authorised for what its arguments actually name**,
never for what the tool's name suggests.

## 4. Entry points and trust boundaries

### 4.1 The inventory

The inventory comes from `tests/_surface_inventory.py`, called in-process
with `HOME`, `PYRITE_CONFIG_DIR` and `PYRITE_DATA_DIR` set to a scratch
directory. No server or suite was run.

| Class | Count | Where |
|---|---|---|
| REST under `/api` | 111 operations | `pyrite/server/endpoints/*.py` (22 modules) |
| REST `/auth/*` | 19 | `auth_endpoints.py` |
| Anonymous-by-design REST | 3 (14 until #953 removed `/site*`, `/viewer*`, `/sitemap.xml` and `/robots.txt`) | `branding_endpoints.py` (2), `api.py` `/health` (1) |
| **REST total** (`rest_operations()`) | **133** (144 before #953) | |
| MCP tools (`mcp_tools()`, admin tier) | **112**: read tier 72, write tier +31, admin tier +9 | core `mcp_server.py` 48; extensions 64 (software-kb 23, journalism-investigation 22, encyclopedia 6, cascade 6, social 5, zettelkasten 2) |
| `NON_ROUTE_ENTRY_POINTS` | 2 | `/mcp` (a Mount: `GET /mcp/sse`, `POST /mcp/messages/`, `GET /mcp/info`), `/ws` |
| Other app routes, **not in the inventory** | 4 | FastAPI's `/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc` |
| Local entry points | 4 console scripts | `pyrite`, `pyrite-read`, `pyrite-admin`, `pyrite-server`; `pyrite mcp` (stdio) |
| Outbound request sites | 7 modules | `services/clipper.py`, `services/url_checker.py`, `services/git_service.py`, `services/llm_service.py`, `services/oauth_providers.py`, `github_auth.py`, `server/endpoints/repos.py` |
| Subprocess sites | 8 modules | `services/{git_service,kb_service,version_service,worktree_service}.py`, `storage/document_manager.py`, `cli/{__init__,extension_commands,schema_commands}.py` |
| In-process execution of KB-adjacent code | plugin hooks (`hook_runner.py`), validators, templates (`template_service.py`, `endpoints/templates.py`, `site_cache.py`) | |

**Inventory note (structural, not a finding).** The four FastAPI
documentation routes are neither an `APIRoute` nor named in
`NON_ROUTE_ENTRY_POINTS`, so the §5 guard and G1 do not see them. The
three `/mcp` sub-routes are covered only as "the Mount". A4 decides
whether the documentation routes belong in `PUBLIC_ENTRY_POINTS` with a
reason, or in `NON_ROUTE_ENTRY_POINTS`. G1 should enumerate the `/mcp`
sub-routes separately.

**MCP transports.** There are two: **stdio** (`run_stdio`, local, U7) and
**SSE**, mounted at `/mcp`: the stream is opened with `GET /mcp/sse`, and
messages are relayed with `POST /mcp/messages/?session_id=…`. "MCP over
HTTP" in this review means that SSE pair; no streamable-HTTP transport is
mounted.

### 4.2 Boundaries, properties and attacker goals

The goals, as the attacker would name them:

- **G-read**: read S1.
- **G-exist**: learn S2.
- **G-write**: change S3.
- **G-escalate**: gain S4.
- **G-cred**: obtain or replay S5.
- **G-escape**: reach S6.
- **G-pivot**: use S7.
- **G-deny**: exhaust S8.
- **G-persist**: keep access after revocation.
- **G-impersonate**: act as another principal.

The properties (P-…) are what each audit checks. Every audit finding names
the property it violates.

#### B1. REST `/api` (111 operations): any principal U0–U6

Attacker goals: G-read, G-exist, G-write, G-escalate, G-persist.

- **P-R1, one decision.** Every operation is authorised once, by the
  policy, for a declared `(Action, Resource)` (ADR-0037 §2). No handler
  compares roles itself.
- **P-R2, every KB named is checked.** Each KB the request names or implies
  is authorised for the action the operation performs on it. That covers
  the path, the query, the body, bulk and batch items, import payloads,
  a row id resolved to its KB, a worktree or collection resolved to its KB,
  and a `kb` field inside an entry body.
- **P-R3, mutating means write.** A mutating operation requires a write,
  admin or capability action on its resource. A read rung never suffices.
  The only exceptions are the reads-over-POST that 3a lists
  (`REST_READS_OVER_POST`).
- **P-R4, cross-KB reads are bounded.** An operation that spans KBs returns
  nothing derived from a KB outside the principal's `ReadScope`. This
  includes S1's derived forms: counts, facets, titles resolved for links,
  graph neighbours, wanted links, suggestions and QA totals.
- **P-R5, concealment.** For a principal that cannot read *k*, every
  answer about *k* is byte-identical to the answer about a KB that does not
  exist. That covers the status, the body, the error code, and any
  difference that is visible or measurable. It holds for rows in *k* too
  (ADR-0037 §4).
- **P-R6, decisions are current.** A decision uses the principal's grants at
  the time of the request. Nothing caches a grant beyond one request
  (ADR-0037 §2), except as ADR-0036 allows.
- **P-R7, capabilities are not rungs.** Repo egress, git push and publish,
  index sync and site render are allowed only to the principals ADR-0031
  and 3c map them to. Being able to write a KB does not by itself confer
  them.
- **P-R8, safe errors.** Error bodies carry only `public_message`. No path,
  SQL, stack trace, other KB's name or config value appears
  (ADR-0037 §3; #509).

#### B2. `/auth/*` (19 operations): U0 against U1–U6

Attacker goals: G-cred, G-impersonate, G-escalate, G-persist, G-deny.

- **P-A1, credentials are bound.** A session identifies one user. It is
  unguessable, it is sent only with the `HttpOnly`, `Secure` (when served
  over TLS) and `SameSite` flags its role requires, and it expires.
- **P-A2, revocation is total.** Logout, "log out everywhere", a password
  change, a role change, a revoked grant and a deleted user each end or
  re-scope every credential derived from the old state. This covers REST
  sessions, MCP SSE connections (P-M4) and `/ws` sockets (ADR-0036).
- **P-A3, cookie auth resists cross-site requests.** A state-changing
  request authenticated by cookie cannot be caused by U8. Under M0, the
  same holds without any cookie (`request_guard.py`).
- **P-A4, registration grants only what configuration says.** A registered
  user starts at the configured default. Invite codes are single-purpose,
  expire or are consumed as configured, and are managed only by the
  principals allowed to manage them. Usernames cannot collide with, or
  impersonate, reserved or system identities.
- **P-A5, keys are compared safely.** Operator and user keys are stored
  hashed and compared in constant time. A key's role is its configured role
  and never more (P-M1). Each credential scheme (cookie, `X-API-Key`,
  `Bearer`) is accepted or rejected consistently on every surface.
- **P-A6, the OAuth flow is bound to its initiator.** `state` is bound to
  the browser that started the flow and is single-use. The callback links a
  GitHub identity only to the user who started it. A stored token is
  readable only by its owner's `SELF` actions, and never appears in a
  response, a log or an error.
- **P-A7, user administration is admin-only.** `/auth/users*` and role
  changes are `USER_MANAGE`. A principal cannot raise its own role or grant.
- **P-A8, authentication cannot be flooded.** Rate limits apply per
  credential and per identity, and one slow login cannot stall the event
  loop for everyone (#440).

#### B3. MCP over SSE (`/mcp/sse`, `/mcp/messages/`, `/mcp/info`): U1–U6 through an agent

Attacker goals: G-read, G-exist, G-write, G-escalate, G-impersonate,
G-persist.

- **P-M3, a session is a credential.** The principal is fixed when the SSE
  stream is authenticated. A message posted to a session acts as that
  session's principal and no other. Knowing a session id does not confer
  that session's principal on another caller.
- **P-M4, session lifetime.** An SSE session lives no longer than the
  credential that opened it, as ADR-0036 requires for `/ws`.
- **P-M5, tier is a floor, not the decision.** Registering a tool at a tier
  hides it from lower tiers. Per-KB read and write scoping still applies to
  every call, in `_dispatch_tool`, for core and extension tools alike
  (REQ-6.1).
- **P-M6, arguments are untrusted.** Every KB, entry id, path, URL, query
  or template that a tool argument names is authorised and contained as if
  it came from U8 (see B1's P-R2 and B7). A tool that spans KBs and cannot
  filter fails closed.
- **P-M7, the MCP discovery surface.** `/mcp/info` and tool listings reveal
  nothing about private KBs.

#### B4. MCP over stdio, and the CLI: U7

Attacker goals: none from U7 itself, which is trusted. Through U9 and U10
content: G-escape, G-pivot.

- **P-L1, local is explicit.** The local principal is chosen on purpose
  (`Principal.local()`, ADR-0037 theme 5). Nothing on a network surface can
  obtain it, or any treatment reserved for it (exemptions, defaults).
- **P-L2, local is not root.** `pyrite-admin` and the server in Docker run
  as the least-privileged user that works (REQ-5.1; #405 context). Files
  they create are not readable by other OS users unless configured so.
- **P-L3, content stays data.** Everything in B7 and B8 applies to the CLI
  too. A KB the CLI reads, indexes, renders or syncs cannot make it execute
  code, write outside the KB, or fetch a URL the operator did not ask for.

#### B5. `/ws`: U0–U6

Attacker goals: G-read, G-exist, G-persist.

- **P-W1.** The socket is authenticated at the handshake with the same
  scheme and the same result as REST.
- **P-W2.** An event naming, or derived from, KB *k* reaches only sockets
  whose principal may read *k*. That includes KB names, entry ids and
  titles in the payload.
- **P-W3.** Socket lifetime follows ADR-0036.

#### B6. The anonymous render surfaces: `/branding/*`, `/config/branding`, `/health` (and, until #953 removed them, `/site*`, `/viewer*`, `/sitemap.xml`, `/robots.txt`); U0

Attacker goals: G-read, G-exist, G-escape (reading files through a path).

- **P-S1, public only.** Every anonymous answer includes only what the
  access policy gives the anonymous principal (`kb_role`, step 5 of
  `services/access_policy.py`, the one rule; `services/public_kbs.py` was
  the renderer's copy and went with it in #953). That covers link targets,
  backlinks and titles resolved from private KBs. A private KB's name,
  entry titles or counts never appear, even as a dangling link.
- **P-S2, contained paths.** Every served path resolves inside its root:
  the static dir or the branding dir. That holds after normalisation,
  encoding, symlinks and case folding.
- **P-S3, no stale anonymous copy.** Nothing anonymous is served from a
  cache rendered under an earlier policy (the `/site` cache this property
  governed is gone; it stays as the rule for any future one).
- **P-S4, no configuration leaks.** `/config/branding` and `/health` expose
  nothing a U0 should not know: paths, versions beyond what the operator
  chose, KB names, or whether auth or keys are configured beyond what the
  login screen needs.

#### B7. The browser: U8 and U9 against U1–U5's sessions

Attacker goals: G-impersonate, G-cred, G-write (through a victim's
session).

- **P-B1, stored content never executes.** Content written by one principal
  cannot run script in another principal's browser or in the web app's
  origin. That covers entry bodies (markdown and HTML),
  titles, tags, field values, template output, collection names, KB names,
  branding assets and SVG. Pages are served with a CSP (`site_csp_extra`
  only relaxes it by operator choice).
- **P-B2, CORS and Host.** Only configured origins can read responses, and
  requests addressed to an unexpected Host are refused while M0 or M4 is
  active (`request_guard.py`).
- **P-B3, P-A3 holds for every mutating route,** including `/mcp/messages/`
  and uploads.

#### B8. The write path: what a permitted write can reach; U3–U7, and U9–U10 as input

Attacker goals: G-escape, G-write (beyond the granted KB), G-deny.

- **P-F1, containment.** Every file a service creates, writes, moves or
  deletes on behalf of a request resolves inside the KB root it was
  authorised for, or inside a designated scratch or cache root. Names
  derived from ids, titles, types, collection folders, template names,
  export targets, worktree names, ephemeral KB names and repo names are
  sanitised to that end. NUL, overlong names, separators, drive-relative
  names, reserved device names, `..` and symlinks all stay inside, or
  are refused.
- **P-F2, delete precision.** A delete removes exactly the entry's own
  files (ADR-0038).
- **P-F3, git arguments are data.** Every value a request places on a git
  or subprocess command line (a ref, a branch, a remote, a path, a commit
  message) cannot be read as an option, and no shell interprets it.
- **P-F4, imports are writes to every KB they name** (P-R2), and are
  contained (P-F1).
- **P-F5, writes are bounded.** Body, bulk, import and upload sizes are
  bounded, so that one principal cannot exhaust disk or memory for others.

#### B9. Outbound requests: U1–U6 choose a URL, U11 answers

Attacker goals: G-pivot, G-deny, G-cred (a credential sent to the wrong
host).

- **P-F6, files at rest.** Export packs, site caches and backups are
  readable only by principals who may read every KB they contain, and are
  written with permissions no wider than the KB's own files.
- **P-O1, no pivot.** A URL chosen by a non-admin principal (clipper, link
  check, clone, subscribe) cannot make the server connect to loopback,
  link-local, private or metadata addresses. That holds after DNS
  resolution, on every redirect hop, and at connect time, not only at
  validation time.
- **P-O2, schemes.** Only the intended schemes are accepted: `https`, and
  `http` where configured, for fetches. Clone and subscribe exclude
  `file:`, `ext::` and other local or transport-helper schemes.
- **P-O3, bounded responses.** Size, time and redirect count are bounded
  (see the item
  `web-clipper-response-size-cap-and-dns-rebinding-toctou-defense-r1300-follow-ups`).
- **P-O4, credentials go where they belong.** A stored token or LLM key is
  sent only to the host it was issued for. A base URL that decides where a
  credential is sent can be changed only by U5 or U7.
- **P-O5, fetched content is U9.** Everything B7 and B8 require of stored
  content applies to it.

#### B10. KB-supplied configuration and code paths: U9 and U10

Attacker goals: G-escape (code execution), G-pivot, G-read (by
cross-reference).

- **P-K1, data does not become control.** Nothing in a KB's files, whether
  `kb.yaml`, templates, schemas, `file_pattern`, collection queries, or
  frontmatter, causes code execution on the server or CLI host. Nor does
  it cause file access outside the KB, an outbound fetch, or a change to
  another KB's settings. Plugin hooks and validators are installed code
  (U7's choice), and a KB can only select among them.
- **P-K2, a cloned repository's own configuration is inert.** Its hooks,
  attributes and filters, and its submodules, do not execute or fetch
  during clone, sync, commit, diff or index.
- **P-K3, templates are sandboxed.** Rendering a KB-supplied template
  cannot reach Python objects, the filesystem, or the environment.
- **P-K4, cross-KB references are scoped.** A link, embed, transclusion
  or query in KB *a* that names KB *b* resolves only if the **reader** can
  read *b*.

## 5. Severity scale, for A1–A8, L1, G1 and D1

| Severity | Meaning |
|---|---|
| **critical** | U0 or U8 reaches S1, S3, S4, S5 or S6 in any mode; or any principal reaches S6 code execution |
| **high** | U1–U3 reach another KB's S1 or S3, or reach S4 or S5; or G-pivot to S7 |
| **medium** | G-exist (a P-R5 or P-S1 breach revealing names or counts); G-persist within a bounded window; a derived-data leak of titles or counts only; G-deny by one principal against all |
| **low** | a property holds in effect, but only by accident of another check (defence in depth missing); information about the instance, not about any KB |

A **finding** violates a property here, shown by a failing regression test on `dev` at a named SHA: one pytest test asserting the property, red on `dev`, which the fix then keeps (maintainer, 2026-09-26). Briefs ask for that test, never for a reproduction or a request sequence. A property that holds today but is enforced
nowhere structural is **hardening**, not a finding. Hardening may become an
ordinary backlog item. In every brief, a divergence **toward refusal** (the
code refuses what the policy allows) is not a finding either: it is a G1
pin or a bug issue.

## 6. Audit briefs

Every brief shares these rules:

- **Read-only.** Run no suite. At most one scratch server on a free port,
  with a temp data dir (never `~/.pyrite` or a checkout's `kb/`), started
  only to reproduce one finding.
- **Deliverables.** Each brief delivers:
  - (a) findings, to the conductor only, each naming its principal, entry
    point, property, severity and reproduction;
  - (b) hardening items, as backlog items that name the property, not an
    exploit;
  - (c) for every question, "holds", with the evidence (file:line, the
    guard or test that enforces it), or "open".
- **Handling rule.** A finding never goes into GitHub, a public KB entry, a
  commit, a test name or a PR body before its fix is released.
- **Time box.** One conductor tick. The questions are in priority order, so
  a partial pass answers the most important ones first.
- **Read first:** this document §1–§5, ADR-0037, and the file-level lists in
  `tests/test_every_entry_point_passes_the_policy.py`
  (`REST_NOT_YET_MIGRATED`, `REST_WRITE_TIER_NOT_YET_MIGRATED`,
  `REST_READS_OVER_POST`, `PUBLIC_ENTRY_POINTS`, `MCP_NOT_YET_MIGRATED`)
  and `tests/characterization/` (the principals, and
  `REST_ACCESS_EXCLUSIONS` and `MCP_ACCESS_EXCLUSIONS`).

### A1. REST authorization, writes first (B1)

- **Scope.**
  - The 68 `REST_NOT_YET_MIGRATED`, the 10
    `REST_WRITE_TIER_NOT_YET_MIGRATED` and the 8
    `INLINE_ACCESS_DECIDING_ROUTES`.
  - Then the 3a-migrated reads, sampled.
  - Files: `server/endpoints/*.py`, `server/authz.py`, `server/api.py`
    (`verify_api_key`, `requires_tier`, `requires_kb_tier`, `RowKB`,
    `_resolve_kb_names`), `server/worktree_resolver.py`, and
    `services/access_policy.py`.
- **Properties.** P-R1–P-R8, P-M1, P-M2.
- **Questions, in order.**
  1. For every mutating operation, which KBs does it touch, and is each
     authorised for write? Answer this as a table of operation → resolved
     resources → the check that covers each resource (P-R2, P-R3). Bulk,
     batch and import bodies, row-resolved writes, the worktree resolver
     branch in `entries.py`, `daily.py`, `collections.py` and `reviews.py`
     come first.
  2. The instance, user and capability routes (`admin.py`,
     `settings_ep.py`, `worktree.py` merge queue, `git_ops.py`,
     `repos.py`, `kbs.py`, `starred.py`, `ai_ep.py`). Is each gated at the
     rung ADR-0037 3c will map it to, and can a KB admin act only on their
     own KB's grants (P-R7, U4)?
  3. Concealment. Does every operation answer an unreadable KB, or a row
     in one, exactly like a missing one (P-R5)?
  4. Cross-KB reads and derived data (P-R4). Do search, graph, timeline,
     tags, links, QA, stats and `entries/titles|resolve|wanted` leak from
     outside the `ReadScope`?
  5. Is every credential scheme handled the same way on every route
     (P-A5)?
- **A finding is** a principal from §3 that gets a different answer class
  than the policy gives for the operation's actual effect, toward access.
- **Not a finding:** a divergence toward refusal. That is a G1 pin or a
  bug issue.

### A2. MCP (B3, B4)

- **Scope.**
  - All 112 tools: 48 core, 64 extension.
  - Files: `server/mcp_server.py` (`_dispatch_tool`, registration,
    `run_stdio`, rate limiting), `server/mcp_routes.py` (`_authenticate`,
    `_resolve_credential`, `_resolve_bearer_auth`, the SSE and messages
    routes), `server/mcp_rate_limiter.py`, `plugins/scoping.py`,
    `plugins/registry.py`, and each extension's `plugin.py` tool handlers
    (`extensions/*/src/*/plugin.py`).
- **Properties.** P-M3–P-M7, P-L1, and P-R2, P-R4 and P-R5 as they apply
  to tools.
- **Questions, in order.**
  1. Session binding and lifetime on the SSE pair (P-M3, P-M4).
  2. For each of the 31 write-tier and 9 admin-tier tools, is every KB
     that the arguments name, including ids resolved to a KB, checked
     against `writable_kbs`? Extension write tools first (P-M5, P-M6).
  3. For every read tool that spans KBs, does it filter by scope or fail
     closed? Do extension finders use `kb_scope_clause` (P-R4)?
  4. Tool arguments as untrusted input: paths, URLs, templates and query
     fragments reaching B8, B9 or B10. This is the manual stand-in for
     #228.
  5. Can any network caller obtain local-only treatment (P-L1)?
  6. Do `/mcp/info` and the tool listings stay free of private data
     (P-M7)?
- **A finding is** a tool call, over SSE, by a principal from §3 that reads
  or writes beyond its scope, or keeps its scope after revocation.

### A3. CLI, local processes, and KB-supplied configuration (B4, B10)

- **Scope.**
  - The CLIs: `pyrite/cli/`, `read_cli.py` and `admin_cli.py`.
  - `ui/data.py`.
  - The `Dockerfile` and entrypoint.
  - `services/hook_runner.py`, `services/template_service.py`,
    `services/schema_service.py` and the `kb.yaml` loading in
    `config.py`.
  - `file_pattern` handling.
  - `services/repo_service.py` sync and `services/git_service.py`, as they
    act on a cloned repository.
  - The subprocess sites in §4.1.
  - Plugin loading (`plugins/registry.py`, `entry_points()`): can a KB's
    own files name a module or entry point to import (P-K1)?
- **Properties.** P-L1–P-L3, P-K1–P-K4, and REQ-5.1.
- **Questions, in order.**
  1. What does a KB from a subscribed or forked repo make the server or
     CLI execute, render, fetch or write, from its own files, during
     subscribe, sync, index and render (P-K1, P-K2)?
  2. Is template rendering sandboxed on every path: REST render, MCP, the
     site cache and the CLI (P-K3)?
  3. Do cross-KB references resolve by the reader's scope (P-K4)?
  4. What user do `pyrite-admin` and the server run as in the shipped
     container? What permissions do the config, index and key files get
     (P-L2, #405)?
  5. Does anything give a network caller the local principal (P-L1)?
- **A finding is** a KB's content (U9 or U10) that causes execution, a
  fetch, or a write outside its root. Also: a network principal that
  obtains `local`.

### A4. `/ws` and the anonymous render surfaces (B5, B6, B7's P-B1)

- **Scope.**
  - `server/websocket.py`, `server/static.py`,
    `server/branding_endpoints.py` and `services/branding_service.py`
    (`services/site_cache.py`, `services/public_kbs.py`,
    `services/sitemap_service.py`, `server/seo_endpoints.py`,
    `server/static_search_page.py` and `server/templates/` were removed
    by #953).
  - The FastAPI documentation routes, and the inventory note in §4.1.
  - The web UI's rendering of entry content (`web/src/`: the markdown
    renderer, and every `{@html}`).
- **Properties.** P-W1–P-W3, P-S1–P-S4, P-B1, P-B2.
- **Questions, in order.**
  1. Does every anonymous answer draw only on what the access policy
     gives the anonymous principal, including derived data: backlinks and
     resolved titles (P-S1)?
  2. Path containment in `static.py` and the branding routes (P-S2).
  3. Can content stored by one principal run script in another's
     browser, in the app (P-B1)? The web UI's markdown and HTML
     sanitisation, and the CSP.
  4. `/ws` payload scoping, handshake authentication and lifetime
     (P-W1–P-W3).
  5. Should the documentation routes be public, and does anything
     anonymous reveal configuration (P-S4)?
- **A finding is** private data (S1 or S2) on an anonymous surface or a
  wrongly scoped socket; a path outside its root; or stored content that
  executes.

### A5. Authentication flows (B2, B7's P-B3)

- **Scope.**
  - `server/auth_endpoints.py`, `services/auth_service.py`,
    `server/api.py` (`verify_api_key`, cookie handling),
    `server/auth_rate_limit.py` and `server/request_guard.py`.
  - `github_auth.py`, `services/oauth_providers.py`,
    `services/user_service.py` and `services/credential_events.py`.
  - The web client's auth calls in `web/src/lib/api/`.
- **Properties.** P-A1–P-A8, P-B2, P-B3, P-M1.
- **Questions, in order.**
  1. Key and role resolution in every mode of §1 (P-M1, P-A5).
  2. The cookie flags, the session entropy, expiry and revocation (P-A1,
     P-A2). Answer P-A2 as a table: event → which credentials it ends
     (REST, SSE, `/ws`).
  3. CSRF on cookie-authenticated mutating routes, with and without a
     configured `cors_origins` (P-A3, P-B3).
  4. Registration, invite codes and username rules (P-A4).
  5. OAuth `state`, the callback binding, and token storage and exposure
     (P-A6).
  6. `/auth/users*` and role changes (P-A7).
  7. Rate limits and the login stall (P-A8, #440).
- **A finding is** a credential obtained, replayed, fixed or kept past
  revocation; or a state change caused cross-site.

### A6. The write path: containment (B8)

- **Scope.**
  - `services/export_service.py`, `services/template_service.py`,
    `services/kb_service.py` (`services/site_cache.py` was removed by #953), and the storage
    layer (`storage/document_manager.py`, `find_file`).
  - A collection's `folder_path` (`endpoints/collections.py`), and
    `entries/import`.
  - `services/git_service.py`, `services/worktree_service.py`,
    `services/ephemeral_service.py`, `services/repo_service.py` (the
    clone path) and `services/version_service.py`.
- **Properties.** P-F1–P-F5, P-K2, P-R8.
- **Questions, in order.**
  1. List every join from request-derived input to a filesystem path.
     Is each contained against NUL, overlong, drive-relative, reserved,
     `..` and symlinked names (P-F1)?
  2. Delete precision after ADR-0038 step 1 (P-F2).
  3. Every subprocess argument that comes from a request or from KB
     content (P-F3).
  4. The import payloads (P-F4).
  5. Size bounds on bodies, bulk, import and uploads (P-F5).
  6. Files at rest (P-F6): who can later fetch an export pack or the site
     cache, and with what on-disk permissions they are written.
- **A finding is** a file created, read, changed or deleted outside its
  authorised root, or a request value interpreted as a command option.

### A7. Outbound requests (B9)

- **Scope.**
  - `services/clipper.py`, `services/url_checker.py`,
    `services/repo_service.py` and `server/endpoints/repos.py`
    (subscribe, fork, sync, the GitHub listing).
  - `services/git_service.py` (remotes), `services/llm_service.py`,
    `services/oauth_providers.py` and `github_auth.py`.
  - `services/settings_service.py` and `endpoints/settings_ep.py` (who can
    set a base URL).
  - Every use of a stored provider key: `services/embedding_service.py`,
    `services/query_expansion_service.py`, and the LLM client built in
    `server/api.py`.
  - Push and sync to a KB's existing git remotes: a stored GitHub token
    goes only to a host the operator configured, never one a KB's files
    chose (P-O4).
  - The open items in
    `web-clipper-response-size-cap-and-dns-rebinding-toctou-defense-r1300-follow-ups`.
- **Properties.** P-O1–P-O5, P-R7.
- **Questions, in order.**
  1. For each outbound site: who chooses the URL, and which address
     classes can it reach after resolution and redirects (P-O1)?
  2. Clone and subscribe schemes (P-O2).
  3. Response bounds (P-O3).
  4. Where each stored credential is sent, and who can change the
     destination (P-O4).
  5. Is fetched content treated as U9 (P-O5)?
- **A finding is** a non-admin principal making the server reach an S7
  address, or sending a credential to a host it chose.

### A8. CodeQL triage

- **Scope.** As defined by the spike
  `codeql-triage-and-close-the-56-open-code-scanning-alerts-on-dev`. Its
  output goes into that item. It runs after #509 merges.
- **Properties.** Map each alert to the property above that it would
  violate, if any. That map is how a true positive becomes an A1–A7
  finding, and how a false positive gets its dismissal reason.
- **A finding is** as §5 defines it. Dismissing alerts on GitHub is the
  conductor's action.

## 7. The persona world (L0) and the live session (L1)

L0 builds this world, and L1 plays it. The expectations column is L1's
**oracle**: a divergence toward access is a finding, and one toward
refusal is a bug.

### 7.1 Instance configuration

- Mode **M3** plus invite codes. Auth enabled,
  `allow_registration: true`, `require_invite_code: true`. The instance
  admin issues one invite for bob; mallory registers in a second phase
  with the invite requirement off (this exercises M3).
- `anonymous_tier` unset for the main run. An optional second run with
  `anonymous_tier: read` checks M4.
- One operator read key. `allowed_hosts` and `cors_origins` stay at their
  defaults.
- A per-worktree port and a scratch data dir (`web/e2e/ports.ts`'s
  derivation). Seeded through the real admin paths only.

### 7.2 KBs

| KB | `default_role` | Holds |
|---|---|---|
| `alpha` | none (private) | alice's private notes |
| `beta` | none (private) | bob's private notes |
| `shared` | none (private) | a collaboration KB |
| `public` | `read` | public notes (the control for U0) |

**Canaries.** Every entry's title, body, one tag and one field carries the
token `<KB>-CANARY-<n>` (for example `ALPHA-CANARY-3`), so that a leak is
greppable in any response, render or socket frame. Each KB has at least
five entries, one with an attachment, and one with a version history of
two commits.

**Cross-references.** `public` links to an `alpha` entry (a wanted or
dangling link), and `shared` links to a `beta` entry. They exist so that
P-S1 and P-K4 have something to leak. These references are part of the
seed, not attack steps.

### 7.3 Personas

| Persona | Kind (§3) | Credentials | Grants | Should reach | Must not reach |
|---|---|---|---|---|---|
| **root** | U5 instance admin | created by `pyrite-admin user create`; a session | global admin | everything within the instance | (the control persona; not played in L1) |
| **alice** | U4 on `alpha`, U3 on `shared` | a session, plus a user API key for `/mcp` | admin on `alpha`; write on `shared` | read, write and manage grants on `alpha`; read and write on `shared`; read on `public` | anything in `beta`; grants on `shared`; instance administration |
| **bob** | U4 on `beta`, U2 on `shared` | invite registration; a session and an API key | admin on `beta`; read on `shared` | read, write and manage grants on `beta`; read `shared`; read `public` | **any write to `shared`**; anything in `alpha`; instance administration |
| **mallory** | U1, no grant | open registration; a session and an API key | none | read `public`; her own `SELF` | anything in `alpha`, `beta` or `shared`, including their existence (P-R5); anything of anyone else's `SELF` |
| **anon** | U0 | none | none | `public` only | the three private KBs, including their names, in any form |
| **opkey** | U6 read key | `X-API-Key` | read, instance-wide | read on every KB (unscoped, by design) | any write, admin or capability action |

### 7.4 Scenarios L1 must cover

These are coverage goals, not steps. Each run covers the persona's row
across the UI and the three network surfaces (REST, `/mcp` SSE, `/ws`;
`/site` was removed by #953), for reads and writes.

- **Revocation (P-A2, P-M4, P-W3).** Mid-session, root revokes bob's grant
  on `shared`, and later logs mallory out everywhere. Each persona's open
  REST session, SSE session and socket is then observed.
- **Role change.** Root raises, then lowers, alice's global role. Her old
  credentials are then observed.
- **Anonymous tier (optional M4 run).** anon's reach changes by exactly
  the configured rung on `public`, and on nothing private.

L0 needs these beyond the groom's list:

- the `public` KB;
- **root** as a named persona;
- alice as KB admin on `alpha`, and bob as KB admin on `beta`;
- the canary format;
- the two cross-references;
- a user API key per persona for `/mcp`.

L0's out-of-scope rule stands: no assertions, only the world and its
credentials file.

## 8. Coverage map

| Boundary | Properties | Audit | Live | Structural |
|---|---|---|---|---|
| B1 REST `/api` | P-R1–P-R8 | A1 | L1 | 3a guard, G1 |
| B2 `/auth/*` | P-A1–P-A8 | A5 | L1 | G1 (the excluded 75) |
| B3 MCP SSE | P-M3–P-M7 | A2 | L1 | `_dispatch_tool` AST check, G1 |
| B4 stdio and CLI | P-L1–P-L3 | A3 | none | ADR-0037 theme 5 |
| B5 `/ws` | P-W1–P-W3 | A4 | L1 | `test_websocket_scoping.py` |
| B6 anonymous renders | P-S1–P-S4 | A4 | L1 (anon) | `PUBLIC_ENTRY_POINTS` reasons |
| B7 browser | P-B1–P-B3 | A4, A5 | L1 | none |
| B8 write path | P-F1–P-F5 | A6 | none | none |
| B9 outbound | P-O1–P-O5 | A7 | none | none |
| B10 KB-supplied config | P-K1–P-K4 | A3 | none | none |
| everything | as mapped | A8 (CodeQL) | none | none |

The rows with no structural guard (B7–B10) are where D1's known-gaps list
for R1 is most likely to come from.

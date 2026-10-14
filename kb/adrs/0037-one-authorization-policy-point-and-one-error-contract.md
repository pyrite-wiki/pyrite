---
id: adr-0037
type: adr
title: "One authorization policy point and one error contract"
adr_number: 37
status: accepted
date: 2026-09-25
tags: [architecture, authorization, security, api, mcp, cli, errors]
links:
- target: adr-0031
  relation: related
  kb: pyrite
- target: adr-0036
  relation: related
  kb: pyrite
- target: multi-user-security-review-threat-model-surface-audits-live-personas-and-a-structural-guard
  relation: related
  kb: pyrite
---

# ADR-0037: One authorization policy point and one error contract

> **Status note (2026-10-03).** The KB-admin rung is gone: [[adr-0043]] has
> no KB-admin and administering a KB is the operator's. `Principal.local()` is
> defined but no surface calls it yet, and four `Action`s are still undecided and
> refused. Themes 3b to 5 are not landed. The decisions below are unchanged.
> Evidence: `kb/designs/adr-audit-2026-10.md`.

> **Accepted** (maintainer, 2026-09-26; see "Accepted" below. First written as
> a spike proposal, 2026-09-25). It answers the decision #383 asks to have recorded before dispatch ("do services
> take a principal, or does scoping stay a surface duty with one shared
> helper?"). The decisions at the end are the maintainer's.

## Context

Pyrite has four surfaces that act on the same KBs: REST (`pyrite/server/endpoints/`,
`auth_endpoints.py`), MCP (`mcp_server.py`, `mcp_routes.py`), the live-update
socket (`websocket.py`), and the CLI (`pyrite/cli/`, plus the Streamlit
`pyrite/ui/data.py`). Each decides access, and each shapes its own errors. A
security fix or a parity fix has to find every copy. In practice workers
spend 150 to 500 turns on themes of 300 to 500 lines, and cold reads keep
finding one surface where a guard fails open. The cause is structural: the
rule is shared, but the decision is made in many places.

### Where the checks are today (measured on `dev` 85c7c123)

Commands are in the footnote, so the numbers can be re-run.

| Mechanism | Where | Count |
|---|---|---|
| Global tier floor | `create_app` mounts every `/api` router behind `verify_api_key` + `requires_tier("read")`; `/auth/users*` uses `_ADMIN_ONLY` (the #330 fix) | 1 floor, 1 admin list |
| Tier escalation, per route | `Depends(requires_tier(...))` | 37 in 10 files |
| Per-KB write, per route | `requires_kb_tier(tier)` (the KB named in the request) and `requires_kb_tier(tier, resolve_kb=…)` (row by id, `RowKB`) | 12 in 6 files |
| Per-KB read, named KB | `Depends(requires_kb_read())` | 44 in 19 files |
| Per-KB read, cross-KB | `Depends(get_readable_kbs)`, then the handler filters | 22 in 14 files |
| Decisions made inside handlers | reads of `request.state.api_role` / `auth_user` / `anonymous` outside `api.py`, plus inline `TIER_LEVELS` comparisons (`repos.py`, `daily.py`, `settings_ep.py`) and inline `AuthService.get_kb_role` (`admin.py` grant/revoke) | 23 reads in 8 files |
| `AuthService(db, …)` built inline outside services | endpoints, `api.py`, `mcp_routes.py`, `websocket.py` | 15 |
| MCP | one chokepoint, `_dispatch_tool`: tier-by-registration, read scoping over `KB_ARGUMENT_NAMES`, fail-closed for non-filtering cross-KB tools, per-KB write via `writable_kbs`; 33 core handlers take `readable_kbs`; 6 extensions narrow with `plugins/scoping.kb_scope_clause` | 1 chokepoint, 33 + 6 filter sites |
| MCP credential → scope | `mcp_routes._authenticate` resolves `readable_kbs` / `writable_kbs` per connection | 1 |
| `/ws` | `websocket.py` resolves the readable set at connect (ADR-0036 bounds its life) | 1 |
| CLI, `ui/data.py` | no decision: the process runs as the operator and is unscoped by assumption, never by an explicit principal | 0 |
| The role ladder written out | `TIER_LEVELS` in `api.py`, and three more literal `{"read": 0, "write": 1, "admin": 2}` copies in `auth_service.py`; `("read","write","admin")` validity lists in 5 more places | 4 ladders |

The framework-free rule already exists, and it is good. `api.py` holds
`effective_kb_role_for_user` (grant → KB `default_role` → global role or
anonymous tier), `kbs_for_user_at_tier`, and `readable_kbs_for_user`, and it
says in a docstring "do not add a second implementation". MCP and `/ws`
import them lazily from `api`. `mcp_server.py` is already a single
chokepoint. **What is missing is not a shared rule, but a single point where
every surface asks the rule a question.** The rule lives in the REST module,
it is asked in five syntaxes, and some handlers still answer it themselves.

### How reads became structural in 0.25, and why writes did not

Reads were made structural by putting one question into a shape a test can
see:

- REST: `tests/test_read_scoping_is_structural.py` walks every `APIRoute`'s
  dependant tree. It fails for an `/api` route with neither
  `requires_kb_read()` nor `get_readable_kbs`, unless the route is allowlisted
  with a reason. It also fails when a route declares a KB-bearing parameter the
  resolver (`KB_PARAM_NAMES`) does not read.
- MCP: `tests/test_mcp_tool_registry_is_scoped.py` walks the tool registry,
  core and plugin, against `_dispatch_tool`'s `KB_ARGUMENT_NAMES`.
- `/ws`: `tests/test_websocket_scoping.py`.

That worked because a read has one shape: "may this principal see KB *k*", or
"which KBs may it see". Writes have at least four shapes, and no single
dependency expresses them all:

1. The KB is named in the request (`requires_kb_tier("write")`).
2. The KB is found from a row id (`RowKB` resolvers, one route so far).
3. There is no KB: instance administration, user management, settings, invites.
4. Capabilities that are not a rung on the read/write/admin ladder: repo
   fork/push, git operations, network egress (`repo-access-is-a-capability-not-a-tier`,
   ADR-0031 §1).

So the write-side tests prove weaker things. `test_kb_write_guard_is_structural.py`
proves that *where* a `requires_kb_tier` guard is attached, it can see a KB.
It does not prove that a guard is attached. `test_api_authorization_coverage.py`
proves behaviourally that a read-tier key is refused on every mutating route.
It says nothing about per-KB grants, and nothing about shapes 3 and 4. The
0.26 definition of done asks for a guard that "enumerates every REST route and
MCP tool and fails when one lacks a covering read **and** write authorization
test". The multi-user review item says the same: "Reads were made structural
in 0.25; writes still use per-endpoint checks."

### The error side

Each surface also shapes its own refusals:

- **REST, three body shapes.**
  - The central `PyriteError` handler (`_PYRITE_ERROR_STATUS` in `api.py`)
    answers flat `{"code", "message"}`.
  - `HTTPException(detail={...})` answers `{"detail": {"code", "message", ...}}`:
    91 of the 124 constructions in `endpoints/`, the write refusals via
    `endpoints/write_refusal.refusal_http`, and `/api/repos`.
  - `HTTPException(detail="...")` answers `{"detail": "..."}`: 19 in
    `endpoints/`, and 31 of 37 in `auth_endpoints.py`/`api.py`/`mcp_routes.py`.
  - In total: 161 `HTTPException(` constructions in 22 server files. Statuses:
    404 ×56, 400 ×50, 401 ×20, 403 ×19.
- **MCP.** `_DOMAIN_ERROR_CODES` and `_refusal()` answer
  `{"error", "error_code", "retryable", "suggestion"?}`.
- **CLI.** `cli_error` / `build_error` answer the same canonical shape as MCP.
- **The codes disagree across transports for the same exception.** REST says
  `ENTRY_NOT_FOUND` / `KB_NOT_FOUND` / `KB_READ_ONLY` / `VALIDATION_ERROR`
  where MCP says `NOT_FOUND` / `READ_ONLY` / `VALIDATION_FAILED`.
- **The docs describe a REST shape that REST does not emit.**
  `docs/json-contracts.md` and the `api-design` standard say the REST handler
  emits the canonical shape.
- **Per-class `error_code` has started.** #378 put a stable `error_code` on the
  `ValidationError` family. #377 added `public_message` (an operator's message
  kept out of the response) on `ConfigSaveRefusedError`, and the REST handler
  special-cases that one class. #408 raised the same question for a
  branding-render failure.

### The concealment rule

An unreadable KB answers exactly like a missing one. That rule is
re-implemented at each site that needs it: `api.kb_not_found`,
`_enforce_kb_tier`'s `kb_exists` check (before the role, so the answers match
byte for byte), `RowKB.not_found`, and MCP's `_kb_not_found`, which is
commented as "the MCP spelling of `api.kb_not_found`". It is correct today
because reviewers keep it correct.

## Decision

### 1. One policy point: `pyrite/services/access_policy.py`

A framework-free service owns every access decision. It imports nothing from
FastAPI, MCP or Typer. It is the `access_policy.py` that #383 targets,
widened from "the rules" to "the only place a decision is made".

```python
@dataclass(frozen=True)
class Principal:
    kind: Literal["user", "anonymous", "operator_key", "local"]
    role: str | None            # global role / key role / anonymous tier
    user_id: int | None = None
    # constructors: Principal.local(), .from_api_key(role), .user(row), .anonymous(tier)

class Action(StrEnum):          # a closed vocabulary, each with its rung and/or capability
    KB_READ = "kb.read"; KB_WRITE = "kb.write"; KB_ADMIN = "kb.admin"
    INSTANCE_ADMIN = "instance.admin"; USER_MANAGE = "user.manage"
    SETTINGS_SECRET = "settings.secret"; SELF = "self"   # own profile/keys/tokens
    REPO_EGRESS = "repo.egress"                          # capability, not a rung (ADR-0031)

Resource = KB(name) | Row(kb_name) | Instance() | User(id) | AnyKB()

class Decision: Allow | Deny(code: "UNAUTHENTICATED" | "FORBIDDEN" | "NOT_FOUND")

class AccessPolicy:
    def authorize(self, p: Principal, a: Action, r: Resource) -> Decision
    def require(self, p, a, r) -> None          # raises the Deny as an exception (§3)
    def read_scope(self, p) -> ReadScope         # the cross-KB answer: an opaque set, or unscoped
    def write_scope(self, p) -> WriteScope
```

What moves into it, unchanged in behaviour:

- `TIER_LEVELS`, and the role-ladder copies in `auth_service.py`. The ladder
  exists once.
- `resolve_api_key_role`.
- `resolve_kb_default_role` and `kb_exists`, read through the KB registry
  rather than `_raw_conn` (#383's target, and two of the three `api.py` sites
  #380 allowlists).
- `effective_kb_role_for_user`, `kbs_for_user_at_tier`, `readable_kbs_for_user`.
- The per-KB comparisons now written inline in handlers.

`AuthService` keeps identity and storage: sessions, users, grants, invites,
tokens. The policy reads grants through it. The policy is the only caller
that turns a grant into a decision.

`ReadScope` is a small type that only the policy constructs, rather than a
bare `set[str] | None`. Services that span KBs take it where they take
`kb_names` today (`SearchService`, `QAService`, `LinkDiscoveryService`,
`TaskService` finders, plugin `kb_scope_clause`). "Unscoped" then has to be
constructed on purpose by the policy for a principal that is unscoped. It
cannot be what a forgotten argument defaults to.

**Services do not take a principal** (the answer to #383's question, for now).
The policy is asked at each surface's single entry point, and the
enforcement guard in §5 makes skipping it a test failure. Passing a principal
through every service method would add defence in depth, at the cost of
touching every service signature, every in-process caller (hooks, plugins,
the CLI) and every test fixture. `ReadScope` gets most of that benefit on
the read side, which is where cross-KB services live. See Open questions.

### 2. Every surface asks, and none decides

Each transport gets one adapter. The adapter builds the `Principal` and the
`Resource`, calls `authorize`/`require`, and maps a `Deny`. Nothing else in
the transport compares roles.

- **REST.** One dependency factory, `authorize(action, resource=…)`, in
  `pyrite/server/authz.py` (the #383 file). It replaces `requires_tier`,
  `requires_kb_tier` (both forms), `requires_kb_read` and `get_readable_kbs`.
  - The resource resolver is part of the declaration:
    - `KB` is resolved from the request by the existing `_resolve_kb_names`, and every named KB is checked;
    - `Row` uses a `RowKB`-style resolver;
    - `AnyKB` hands the handler a `ReadScope`;
    - `Instance` and `User` are what they say.
  - The router floor (`verify_api_key`) becomes "build the `Principal`", and
    nothing else.
  - A handler never reads `request.state.api_role`. It receives the
    `Principal`, or a scope, as a parameter.
- **MCP.** `_dispatch_tool` stays the chokepoint, and it asks the policy
  instead of comparing sets itself.
  - Each tool's action is declared at registration. The default comes from
    its registered tier and KB arguments, so existing plugin tools need no
    change; a plugin tool may declare one explicitly.
  - `mcp_routes._authenticate` builds a `Principal`, and stops resolving sets.
- **`/ws`.** It builds a `Principal` at connect, and asks `read_scope`.
  ADR-0036's lifetime rules are unchanged.
- **CLI and `ui/data.py`.** They use `Principal.local()`, explicitly. Every
  service call that takes a scope gets `policy.read_scope(Principal.local())`.
  That is unscoped today, but it is now a decision the policy makes and a
  test can pin. It is no longer an absent argument. MCP stdio uses the same
  principal.

A decision is made per call, against the principal's current grants. Caching
is allowed for the lifetime of one request or tool call. It is not allowed
beyond that unless an ADR says so (ADR-0036 is the one that does).

### 3. One error contract

- **Codes live on exception classes.** Every `PyriteError` subclass carries a
  class-level `error_code`, as the `ValidationError` family already does
  (#378).
- **Messages are public or private by construction.**
  - Every `PyriteError` has a `public_message`.
  - By default it is `str(exc)` for the refusal families whose messages are
    written to be shown: `ValidationError`, not-found, read-only, query
    errors.
  - It is a fixed per-code sentence for everything that can carry
    server-side detail: `StorageError`, `PluginError`, `ConfigError`. #377's
    `ConfigSaveRefusedError` is the model.
  - A transport emits only `public_message`. `str(exc)` goes to the log.
- **Denials are exceptions in the same hierarchy.**
  - `AccessDenied(PyriteError)` has two subclasses: `NotAuthenticated`
    (`UNAUTHENTICATED`) and `Forbidden` (`FORBIDDEN`).
  - A concealment denial raises the not-found exception itself
    (`KBNotFoundError`, or `EntryNotFoundError` for a row). There is no
    "hidden" subclass. It is the same class, and it carries the same message
    a missing KB gets, so no transport can tell the two apart even by
    accident.
- **One mapping table per transport, keyed by code, in one module each:**
  - REST: `pyrite/server/errors.py`, grown from `_PYRITE_ERROR_STATUS`.
    It maps code → status, and it is the only exception handler.
    `HTTPException` is no longer raised for a domain condition or a denial.
    It stays for protocol errors FastAPI owns (422 on request validation).
  - MCP: `_refusal`, which reads the class's code. `_DOMAIN_ERROR_CODES`
    goes away.
  - CLI: `cli_error_from(exc)`, which maps code → exit code.
- **The wire shape per transport is written down once**, in
  `docs/json-contracts.md`. What REST's shape should be is an open question
  below. Whichever is chosen, all three REST shapes collapse into one.

### 4. Concealment lives in the policy

`authorize(p, KB_READ | KB_WRITE | …, KB(k))` checks existence before role.
It returns `Deny(NOT_FOUND)` when `k` does not exist, or when `p` cannot read
it; `Deny(FORBIDDEN)` when `p` can read it but lacks the action; and
`Deny(UNAUTHENTICATED)` when there is no principal at all. `Row(kb)` follows
the same rule for the row's KB. Surfaces never write that order themselves,
so they cannot get it wrong. This is today's `_enforce_kb_tier` order, moved,
not changed.

### 5. The structural guard

`tests/test_every_entry_point_passes_the_policy.py` enumerates:

- every REST operation: `APIRoute` walk, the same one `test_read_scoping_is_structural.py` uses, including `/auth/*`;
- every MCP tool at the admin tier, core and every installed extension;
- the non-route entry points: `/mcp` (the `Mount`), and `/ws`.

It fails when:

1. **REST.** An operation's dependant tree does not contain exactly one
   `authz.authorize(...)` dependency, unless the operation is in
   `PUBLIC_ENTRY_POINTS` with a reason (login, register, the OAuth dance,
   SEO/branding, health).
2. **MCP.** A tool has no resolved `Action`. Or a tool handler is invoked
   anywhere except `_dispatch_tool`: an AST check that `["handler"](`
   appears once.
3. **Any surface.** A module under `pyrite/server`, `pyrite/cli` or
   `pyrite/ui` compares roles itself. A grep ratchet: `TIER_LEVELS`,
   `request.state.api_role`, `.get_kb_role(` and the ladder literal appear
   only in `access_policy.py` and `authz.py`.
4. **The definition-of-done half.** For every enumerated entry point, a
   generated behavioural case runs a fixed principal matrix:
   - anonymous;
   - a user with no grant;
   - read grant;
   - write grant;
   - KB admin;
   - instance admin;
   - operator key.

   Each runs against a readable KB, a private KB and a missing KB. The case
   asserts the transport's answer equals what `authorize` says for the
   declared action. Every route and tool now declares its action and
   resource, so the covering read and write test is generated from the
   declaration. Nobody hand-writes it, and nobody can forget it.

Like #380's `test_layer_boundaries.py`, the guard lands with an allowlist of
not-yet-migrated entry points that **can only shrink**. That allowlist is
what each migration theme below empties. When it is empty, the guard
subsumes `test_read_scoping_is_structural.py`,
`test_kb_write_guard_is_structural.py`, `test_mcp_tool_registry_is_scoped.py`
and `test_api_authorization_coverage.py`. Their tests fold into it. Their
allowlists and reasons carry over verbatim.

## Relationship to #380 (T6) and #383

- **#380 lands first, as groomed.** It moves inline `AuthService(db, …)`
  construction into providers, and moves storage reaches behind services. It
  deliberately leaves access policy out. It allowlists, by function,
  `api.get_llm_service`, `api.resolve_kb_default_role`, `api.kb_exists` and
  `mcp_routes._resolve_credential`, each naming #382/#383. Migration theme 1
  below removes three of those four entries (`get_llm_service` belongs to
  #382). #380 also puts the MCP finders in `TaskService`, which is where
  theme 4 hands them a `ReadScope`.
- **The two ratchets are complementary, and they share one inventory.**
  - `test_layer_boundaries.py` (#380) proves that a surface reaches data
    only through a service.
  - The policy guard (§5) proves that a surface reaches a service only after
    the policy.
  - Both walk the same list of entry points: extract it to
    `tests/_surface_inventory.py` in the first theme that needs it, so the
    two can never disagree about what a surface is.
  - Neither test's allowlist may grow.
- **#383 becomes theme 1 of this ADR.** Its acceptance (the ladder literals
  only in `access_policy.py`; no `from .api import` in `mcp_routes`/`websocket`;
  tier and #201 tests unchanged) is theme 1's acceptance, plus §1's `Principal`.
  Its "decision first" precondition is §1 here, if this ADR is accepted.
- **#382 (composition root) is independent.** The policy is a lazily built
  service like the others, and it gets its registry from whatever root exists
  when it lands. If #382 lands first, `open_runtime()` hands out the policy.
  If it lands later, the providers in `api.py` do, as #380 leaves them.
- **Can go together:** the error-contract theme (2) can run in parallel
  with #380. They share `api.py`, but not a hunk: #380 touches the
  providers, and theme 2 touches the handler at the top of the file.

## Migration

Each theme is one PR a reviewer can hold in their head. Each keeps behaviour
byte-identical except where a line below says otherwise. Order:

| # | Theme | Files | Model | After |
|---|---|---|---|---|
| 0 | **Characterization harness.** Golden `(status, body)` for the §5 principal matrix × {readable, private, missing} KB over every REST operation and MCP tool with a KB or row resource. Plus the error bodies of each `PyriteError` subclass per transport. No production change. | new `tests/characterization/` (fixtures from `tests/test_api_tiers._build_client`), `tests/_surface_inventory.py` | Sonnet | #380 |
| 1 | **Policy extraction (#383).** Create `services/access_policy.py` with `Principal`, `Action`, `Resource`, `Decision`, `authorize`/`require`/`read_scope`/`write_scope`. Move the ladder, key role, default role (via the registry), `kb_exists`, the effective-role walk. `api.py`'s helpers become one-line delegates. `mcp_routes`/`websocket` import the policy, not `api`. | `services/access_policy.py` (new), `server/api.py`, `services/auth_service.py`, `server/mcp_routes.py`, `server/websocket.py`, `services/ephemeral_service.py` | **Opus** | 0 |
| 2 | **Error contract.** `error_code` on every `PyriteError` class. `public_message` with safe defaults. `AccessDenied` family. `server/errors.py` as the single REST handler; `_refusal` reads class codes; `cli_error_from`. Docs corrected. Endpoint `HTTPException`s are *not* converted here: the mechanism lands, the sites move in 3a–3c. | `exceptions.py`, `server/api.py` (handler only), `server/errors.py` (new), `server/mcp_server.py` (`_refusal`, `_DOMAIN_ERROR_CODES`), `utils/errors.py`, `docs/json-contracts.md`, `kb/standards/api-design.md` | Sonnet | 0 (parallel with 1) |
| 3a | **REST reads through the policy.** Add `server/authz.authorize(...)`. Convert the 44 `requires_kb_read()` and 22 `get_readable_kbs` declarations. Cross-KB handlers take `ReadScope`. The §5 guard lands here, with its shrinking allowlist. | `server/authz.py` (new), `endpoints/{tags,tasks,reviews,timeline,kbs,git_ops,graph,links,ai_ep,templates,collections,search,versions,daily,blocks,entries,qa,starred,repos,admin}.py` (declarations only), new guard test | Sonnet | 1, 2 |
| 3b | **REST KB writes through the policy.** Convert `requires_kb_tier` (named and row forms). Move the in-handler per-KB decisions (`entries.py` worktree resolver branch, `daily.py`, `repos.py`) into declarations. Their `HTTPException` denials become `AccessDenied`/not-found raises. | `endpoints/{entries,reviews,clipper,tasks,collections,daily,repos}.py`, `server/authz.py` | **Opus** | 3a |
| 3c | **REST instance, user and capability routes.** `requires_tier` on `admin.py`, `settings_ep.py`, `worktree.py`, `git_ops.py`, `starred.py`, `kbs.py`; `/auth/users*` (`_ADMIN_ONLY`); the inline grant-role check in `admin.py`; `Action.SELF` for own-profile/keys/tokens; `REPO_EGRESS` mapped to today's tiers exactly (the capability split itself stays a separate decision). | `endpoints/{admin,settings_ep,worktree,git_ops,starred,kbs,ai_ep}.py`, `server/auth_endpoints.py`, `server/authz.py` | **Opus** | 3b |
| 4 | **MCP through the policy.** Tool registration resolves an `Action` (default from tier + KB args). `_dispatch_tool` calls `require`, and hands `ReadScope` to the 33 filtering handlers and to plugin handlers via `kb_scope_clause`. `mcp_routes._authenticate` returns a `Principal`. | `server/mcp_server.py` (`_dispatch_tool`, registration), `server/mcp_routes.py`, `plugins/scoping.py`, `plugins/registry.py` (action metadata), extension tests only if a signature changes | **Opus** | 1, 2, and #385(a) if the `mcp_server.py` split is in flight |
| 5 | **Local principal and the remaining surfaces.** `Principal.local()` in `cli/context.py`, stdio MCP, `ui/data.py`. `/ws` asks `read_scope`. CLI errors go through `cli_error_from`. The §5 grep ratchet (no role comparisons outside the policy) turns on. The allowlist reaches zero, and the four older structural tests fold in. | `cli/context.py`, `cli/*_commands.py` (error paths only), `ui/data.py`, `server/websocket.py`, the guard test, the four older tests | Sonnet | 3c, 4 |

**Keeping behaviour identical:**

- Theme 0's goldens are the contract. A migration PR may not change a golden
  file. An intended change, such as unifying a code under the open question
  below, is its own commit that edits the golden, with the reason in the
  message. The reviewer reads that diff, not the handler diff.
- The existing behavioural suites stay green, unedited, through themes 1–4:
  `test_api_tiers`, `test_mcp_tiers`, `test_kb_scoped_writes`,
  `test_private_kb_read_scoping`, `test_mcp_read_scoping`,
  `test_mcp_write_scoping`, `test_websocket_scoping`, `test_write_surface_parity`,
  `test_read_shaping_parity`, `test_settings_secret_authz`,
  `test_repo_kb_authorization`.
- Each of themes 1, 3b, 3c and 4 gets a `pyrite-reviewer` cold read. They
  are on the authorization path, and the private security batch's footprint
  is checked at dispatch, as #380's groom does.

## Consequences

**Gains**

- **A fix lands once.** A change to the rule changes one module. A change to
  how a surface asks changes one adapter.
- **Fail-open stops being reachable by omission.** A new route or tool that
  does not declare an action fails CI (§5). A handler that compares roles
  fails the grep ratchet. The DoD's covering test is generated, not written.
- **Concealment has one implementation.**
- **REST and MCP stop disagreeing about codes and shapes.** CLI and MCP
  already agree.
- **Capabilities that are not tiers** (ADR-0031's repo/egress axis) get a
  place in the `Action` vocabulary. Adding one changes the policy, not 20
  routes.

**Costs**

- **Seven PRs**, four of them Opus with a cold read, on the most sensitive
  path in the code, and sequenced behind #380. Realistically this is 0.26's
  second half into 0.27, alongside the security review rather than before it.
- **Declarations add a line to every route and tool.** A wrong declaration
  (the wrong action) still passes the presence check. Only the generated
  matrix of §5.4 catches it, and only when the matrix's fixture principals
  cover the case.
- **Theme 0's harness is real work.** An auth-enabled app with seven
  principals and three KBs × ~135 REST operations × ~112 MCP tools. It must
  run inside the pre-push budget (`-n 4`), so it has to be one module-scoped
  fixture, not one app per case.
- **Resolving the readable set is a walk over every KB, per request.** That
  is unchanged, and it stays cached per request/call. The policy point makes
  it easy to add a better cache later, and equally easy to add a wrong one:
  §2's caching rule is the guard.
- **REST error bodies change for some callers**, whichever shape is chosen:
  one of today's three shapes survives. The web client already reads both
  `detail` forms and a flat `message` (`web/src/lib/api/client.ts`), so the
  in-tree client is safe. Out-of-tree scripts may not be.

## Alternatives considered

- **Per-route FastAPI dependencies, generalised (the status quo, tidied).**
  - Cheapest: it is what 0.25 did for reads, and it works.
  - But it exists only on REST. MCP, `/ws` and the CLI cannot use a FastAPI
    dependency, so they keep their own decision code. The rule stays in
    `api.py`, and surfaces keep importing the REST module to reach it.
  - It is kept as REST's *adapter* (§2). It is rejected as the *policy*.
- **Decorators on handlers or service methods** (`@requires("kb.write")`).
  - On REST handlers, a decorator is invisible to FastAPI's dependant tree.
    The guard would have to trust an attribute, where today it can walk
    what actually runs.
  - On service methods, it is "services take a principal" (below), with the
    resource resolution hidden in argument positions.
- **ASGI middleware.**
  - Middleware would see every request, including a router mounted without
    its dependency (the #330 class).
  - But it runs before routing, so it would have to re-derive which route
    and which KB from raw paths and bodies, which duplicates the router.
  - It cannot see MCP tool calls inside a session, or the CLI.
  - It is kept only as a possible belt-and-braces check that
    `request.state.principal` was set. It is rejected as the decision point.
- **Services take a principal and enforce inside every method** (defence in
  depth; #383's other branch).
  - The strongest guarantee: no surface can skip it.
  - Also the largest change: every service signature, every in-process
    caller (hooks, plugins, workers, the CLI), and every service test.
  - It also raises the problem of what principal a background job or a hook
    runs as.
  - Deferred, not rejected. `ReadScope` (§1) is the first step, and the
    policy point makes the rest a mechanical follow-up if the review shows
    the guard is not enough.
- **An external policy engine** (OPA, Casbin, Oso).
  - The rule set is small: a three-rung ladder, grant → default → global,
    concealment, and a handful of capabilities.
  - An engine adds a dependency, a policy language contributors must learn,
    and a deployment story, for no expressiveness Pyrite needs.

## Accepted (maintainer, 2026-09-26)

Accepted after theme 1 landed (#500), with themes 0-3a on `dev` (#476, #500, #501, #504, #508).

## Decisions recorded (maintainer, 2026-09-25)

1. **REST error wire shape: (a).** REST keeps `{"detail": {"code", "message",
   "retryable", "hint"?}}`, which the web client already speaks.
   `docs/json-contracts.md` is corrected to match (theme 2).
2. **One code per exception, and REST's code wins.** The class code is REST's
   more specific one. For one release, MCP keeps emitting its current code in
   a `legacy_error_code` field, then switches. This goes in the release notes.
3. **Services take a principal: later.** This is revisited once the security
   review reports.
4. **Scheduling: (a).** Themes 0–3a are pulled into 0.26, so the definition of
   done's structural guard is the §5 guard with a shrinking allowlist, not a
   guard on today's mechanisms. Themes 3b–5 follow in 0.27.
5. **`REPO_EGRESS`:** theme 3c maps it to today's tiers exactly. Splitting it
   out is still ADR-0031's decision.

The ADR stays `proposed` until theme 1 (#383) lands and proves the design;
the maintainer accepts it then (2026-09-25).

---

<small>Counts, on `dev` 85c7c123, from the worktree root:
`grep -rnE 'requires_kb_read\(\)' pyrite/server` (44, excluding defs/docs),
`grep -rn 'Depends(get_readable_kbs)' pyrite/` (22),
`grep -rnE 'requires_tier\(' pyrite/server` (37), `grep -rnE 'requires_kb_tier\(' pyrite/server` (12);
`grep -rn 'HTTPException(' pyrite/server/endpoints | wc -l` (124); detail shapes and statuses by an
`ast` walk of `HTTPException(...)` calls in `endpoints/`, `auth_endpoints.py`, `api.py`, `mcp_routes.py`,
`branding_endpoints.py`; REST operations from `create_app().openapi()["paths"]` (135: 111 under `/api`,
50 of them mutating; 19 under `/auth`); MCP tools from `PyriteMCPServer(tier=…).tools`
(read 72, write 103, admin 112), with `HOME`, `PYRITE_CONFIG_DIR` and `PYRITE_DATA_DIR` set to one temp dir.
The brief's "about 59 read-check sites in 16 files" counts a subset. The REST declarations alone are
66 in 20 files, before MCP's 33 filtering handlers and the six extensions.</small>

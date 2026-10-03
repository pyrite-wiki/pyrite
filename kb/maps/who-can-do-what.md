---
id: map-who-can-do-what
title: "Who can do what? Topic map: authorization, grants, the operator, anonymous access and errors"
type: note
tags:
- map
- design
- authorization
- permissions
- grants
- operator
- access-policy
- errors
- multi-user
---

# Who can do what?

Answers "who may read or write this?", "who can add a KB?", "where is this
checked?", "what does a refusal look like?". Layer 2 under [[design]] (P7, P8).
Multi-user is experimental; local use by one operator comes first.

## The design today

1. One framework-free policy point decides every access question; each surface
   builds a `Principal` and asks, none decides. **decided, partly built**
   [[adr-0037]] (themes 0 to 3a landed; 3b to 5 not).
2. A role on a KB is the grant, else the KB's `default_role`, else the global
   role or anonymous tier. **decided** [[adr-0037]] section 1.
3. An unreadable KB, and any row in it, answers exactly like a missing one.
   **decided** [[adr-0037]] section 4.
4. One error contract: a code on each exception class, a `public_message`, one
   mapping table per transport; REST keeps `{"detail": {...}}`. **decided,
   partly built** [[adr-0037]] section 3 and its recorded decisions.
5. A scope parameter fails closed: "unscoped" is constructed on purpose, never
   the default of a forgotten argument. **decided** [[adr-0037]]; standard
   `scope-parameters-are-required-forgetting-one-fails-closed`.
6. A live-update socket lives no longer than its credential. **decided**
   [[adr-0036]].
7. A tier (read, write, admin) is a visibility floor for MCP tools; plugins
   register tools per tier. **decided** [[adr-0006]].
8. Two planes: users read and write entries per KB by grant (`read` or
   `write`); everything else (registry, policy, grants, users, settings, secrets,
   repo egress, merge, index) is the operator's, plus a small self category.
   There is no KB-admin rung. **decided** ADR-0043 decisions 1, 3, 5.
9. The operator is the local process (CLI, stdio MCP) or the holder of the
   operator credential; a plugin or hook has no principal and gets the session's
   identity injected, never from arguments. **decided** ADR-0043 decisions 2, 7.
10. Anonymous reads exactly what the policy says; `/mcp` admits no anonymous
    principal; derived data is readable only where all its sources are.
    **decided** ADR-0043 decisions 4, 8, 9.
11. The API is the only security boundary; grants, not modes. **withdrawn**
    [[adr-0031]] (parts adopted piecemeal by [[adr-0036]] and [[adr-0037]]).

## Invariants a test could check

- Every REST operation and MCP tool resolves exactly one declared action, and
  no module outside the policy compares roles ([[adr-0037]] section 5).
- For a fixed principal matrix against readable, private and missing KBs, the
  transport's answer equals `authorize`'s ([[adr-0037]] 5.4; ADR-0043 P1, P4, P8).
- An operator-plane action is never conferred by an entries grant; an
  operation with no matrix row fails (ADR-0043 P12, P17).
- The acting identity comes from the session; an unset allowlist or tier means
  none (ADR-0043 P6, P7).
- After a credential change no event reaches a socket opened with the old one
  ([[adr-0036]]).

## ADRs in reading order

[[adr-0037]] first. ADR-0043 (accepted; amends [[adr-0037]], [[adr-0006]],
[[adr-0004]]), [[adr-0036]], [[adr-0031]] (withdrawn, replaced by ADR-0043; read for the reasoning).
History only: [[adr-0006]] (tier contents are stale), [[adr-0004]]
(folder-per-author, inert at the app layer), [[adr-0024]] (its V1 permissions
are contradicted by [[adr-0037]]).

## Where the code starts

Components [[access-policy]], [[auth-service]], [[auth-endpoints]],
[[mcp-server]], [[websocket-server]], [[exception-hierarchy]], [[rest-api]].
Paths: `pyrite/services/access_policy.py`, `pyrite/server/authz.py`,
`pyrite/server/errors.py`, `pyrite/server/mcp_server.py` (`_dispatch_tool`).

## Tests that pin it

`tests/test_every_entry_point_passes_the_policy.py` (shrinking allowlist),
`tests/test_read_scoping_is_structural.py`,
`tests/test_kb_write_guard_is_structural.py`,
`tests/test_mcp_tool_registry_is_scoped.py`, `tests/test_websocket_scoping.py`,
`tests/test_api_authorization_coverage.py`,
`tests/test_websocket_credential_lifetime.py`, `tests/characterization/`
(goldens), `tests/test_api_tiers.py`, `tests/test_mcp_tiers.py`.

## Known gaps

- [[adr-0037]] themes 3b to 5 have not landed; `Principal.local()` has no
  caller; four actions are named and refused until decided; the CLI is
  unscoped by assumption ([[adr-audit-2026-10]]).
- [[adr-0004]]'s author check is inert: hooks are called with an empty user.
  ADR-0043 decision 7 addresses it (accepted; not yet in code).
- [[adr-0024]] says every KB is readable by every authenticated user and has
  no per-KB permissions; contradicted by [[adr-0037]]; ADR-0043 (accepted) and ADR-0044
  (proposed) replace it.
- Where grants are stored is open: database state in [[adr-0029]], the
  operator's file in ADR-0039 (accepted). `kb/designs/authorization-matrix.md`
  (ADR-0043's acceptance) is not authored.
- Not decided: which HTTP principals are the operator, what `self` includes,
  which MCP tools HTTP users see (ADR-0043 questions 1 to 3).

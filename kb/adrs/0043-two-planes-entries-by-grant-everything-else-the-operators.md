---
id: adr-0043
type: adr
title: "Two planes: entries by grant, everything else the operator's"
adr_number: 43
status: proposed
date: 2026-10-02
tags: [architecture, authorization, operator, principals, mcp, invariants, testing]
links:
- target: adr-0037
  relation: amends
  kb: pyrite
- target: adr-0006
  relation: amends
  kb: pyrite
- target: adr-0004
  relation: amends
  kb: pyrite
- target: adr-0031
  relation: amends
  kb: pyrite
- target: adr-0024
  relation: related
  kb: pyrite
- target: adr-0036
  relation: related
  kb: pyrite
- target: adr-0039
  relation: related
  kb: pyrite
- target: adr-0041
  relation: related
  kb: pyrite
- target: adr-0042
  relation: related
  kb: pyrite
- target: adr-0044
  relation: related
  kb: pyrite
- target: adr-0045
  relation: related
  kb: pyrite
---

# ADR-0043: Two planes: entries by grant, everything else the operator's

> **Proposed** (2026-10-02). The maintainer decided the model below on
> 2026-10-02 and accepts or rejects this text. It states the intended model and
> the required properties. It is written from a survey of the authorization
> design (the policy point of ADR-0037, the MCP tiers of ADR-0006, the grant
> and role code) and says nothing about where today's code falls short of it:
> the executable matrix in the acceptance section finds that. Multi-user stays
> experimental, and local use by one operator comes first (ADR-0041 decision
> 7). No new bespoke authorization object is built before this is accepted.

## Context

Pyrite has several surfaces acting on the same KBs: REST, HTTP MCP, stdio MCP,
the live-update socket, the CLI, and the web app that is a client of REST.
ADR-0037 gave them one policy point, `access_policy.py`, with a closed action
vocabulary. What the vocabulary should say, and who holds the actions that are
not about an entry, is written nowhere as one statement. The pieces are
ADR-0006 (the operator picks an MCP tier), ADR-0037 (`KB_READ`, `KB_WRITE`,
`KB_ADMIN`, `INSTANCE_ADMIN`, `USER_MANAGE`, `SETTINGS_SECRET`, `SELF`,
`REPO_EGRESS`), ADR-0004 (author-owned folders), ADR-0024 (all KBs readable by
authenticated users; only admins merge) and the draft ADR-0031 (grants, not
modes; repo and egress as a capability).

Questions that have no written answer: how an MCP tier, a REST role and a local
CLI user relate; whether whoever holds the files is the owner; where identity
comes from for a plugin tool or a hook; whether `/mcp` admits an anonymous
principal; how "public" relates to the anonymous tier; what a KB's owner may
grant.

The maintainer's answers, 2026-10-02, are the Decision.

## Decision

**1. Two planes.**

- **The entries plane.** Users read and write entries, per KB, by grant.
- **The operator's plane.** Everything else belongs to **the operator**, plus
  a small **self** category: a principal's own profile, keys, tokens, stars,
  and own sessions.

**2. Principals.**

| Principal | Is | Scoped per KB? |
|---|---|---|
| **The operator** | The local process (the CLI, stdio MCP, the desktop UI) and, over HTTP, a principal holding the operator credential | No: every KB, every action |
| **A user** | A session with an account | Yes: by grant, else the KB's default role |
| **Anonymous** | No credential | Yes: only what the policy says it may read; read only |

An MCP client has no kind of its own: over HTTP it is a user or the operator;
over stdio it is the operator. A plugin or a hook has no principal of its own
(decision 7). Whoever holds the files holds the KB (ADR-0041); the operating
system account and the filesystem are the boundary for the local process.

**3. The ladder and the resolution.** A grant is one of `read` or `write`, per
KB. A principal's role on a KB is: the operator, all; a user, the grant, else
the KB's `default_role`, else none; anonymous, the lesser of the anonymous tier
and the KB's `default_role`. A role is checked per call against current grants.
An unreadable KB, and any row in it, answers exactly like a missing one
(ADR-0037 decision 4). There is no KB-admin rung: administering a KB is the
operator's (decision 5).

**4. Derived data follows its sources.** Backlinks, counts, facets, QA results,
versions, diffs, job records, events, exports and rendered pages are readable
only where every KB that feeds them is readable.

**5. The operator's plane, for now.** These are the operator's and no grant
confers them:

- KB ownership and the grants on a KB; the registry and a KB's policy fields
  (ADR-0039);
- commit, push, publish and export; repository subscribe, fork and sync;
- schema changes made through Pyrite (`kb.yaml` edits through the API);
- instance settings, secrets, users, invite codes and the index (sync, jobs);
- merging a user's change set (ADR-0044) and rendering the site.

Over HTTP the operator reaches these through the operator credential; the web
app's administration pages are the operator's. The tools and routes for them
are not shown to users.

**6. Direction, not now.** Capabilities (egress, push, publish, creating a KB)
that the operator grants in a master file and that a user configures in a file
they own. It is the same mechanism for every capability, so it is built once.
Nothing built now makes it harder: a capability is never conferred by a content
grant (required property P12).

**7. Identity.** The acting identity comes from the session principal. An
argument may name a target, never the actor. **Plugin and hook identity is
injected by the dispatcher from the session principal** on each call, never
taken from arguments; for the local process it is the configured operator name.
This is what ADR-0004's author check needs (decision 5 of ADR-0042 keeps hooks
to refusing).

**8. The site asks the policy for what is public.** Anonymous surfaces
(`/site`, the sitemaps) show exactly what the policy says the anonymous
principal may read. A KB is public when the operator's file says so
(`published`, ADR-0039 decision 11).

**9. No anonymous MCP, for now.** `/mcp` admits no anonymous principal.

**10. Where a write lands** is one rule on every surface, a function of
principal and KB (ADR-0044). For now every authorised write lands on the
canonical files.

**Leaning, not decided.** Over HTTP, the MCP tool list shows write tools to
anyone who can write any KB, so a per-KB write grant works over MCP as it does
over REST; every call is still decided per KB. **Question 3.**

## What each operation family is

The seed of the executable matrix (below). One row per family; the matrix adds
the surface columns and the concrete entry points.

| Operation family | Plane |
|---|---|
| Read an entry; search, list, tags, timeline, stats; backlinks, graph, link suggestions; the live-update channel | entries (read) |
| Create, update, delete, rename, import | entries (write) |
| QA validate and status | entries (read) |
| QA assess | entries (write) |
| Commit, push, publish; export to a remote | operator |
| Repo subscribe, fork, sync, unsubscribe | operator |
| Registry, KB policy, default role, grants, ephemeral-to-durable promotion, index sync | operator |
| Users, roles, invites; instance settings and secrets | operator |
| Own profile, own keys and tokens, stars, own AI usage and key; own ephemeral KB | self |
| Merge queue; site render | operator |
| Site serving | public, by the policy |

## Required properties

Each is checkable by a test. P1 to P10 are the invariants the survey named;
P11 to P17 follow from the decisions.

- **P1.** Every entry point asks the policy exactly once for a declared action
  and resource.
- **P2.** Every KB a call names or implies, including a row, a collection or a
  change set resolved to its KB, is authorised for the action performed on it.
- **P3.** Derived data is readable only where all its sources are (decision 4).
- **P4.** An unreadable KB, and any row in it, answers identically to a missing
  one on every surface.
- **P5.** A scope parameter fails closed; "unscoped" is constructed on purpose
  (decided 2026-09-26).
- **P6.** The acting identity comes from the session; an argument may name a
  target, never the actor.
- **P7.** An unset allowlist, tier or role means none; partial configuration
  never yields a more privileged principal.
- **P8.** The same operation requires the same action on every surface that
  reaches it.
- **P9.** A tier is a visibility floor; the decision is made per call against
  current grants.
- **P10.** A decision or scope outlives one call only where an ADR says so
  (ADR-0036).
- **P11.** Where a write lands is a function of principal and KB, identical on
  every surface (ADR-0044).
- **P12.** An operator-plane action is never conferred by an entries-plane
  grant; a capability is never conferred by a content rung alone.
- **P13.** An anonymous surface shows exactly what the policy says the
  anonymous principal may read.
- **P14.** The operator principal is explicit, and unobtainable from a network
  surface without the operator credential.
- **P15.** A `self` resource of a principal with no identity is empty, never a
  shared bucket.
- **P16.** A grant names `read` or `write` and nothing else.
- **P17.** The operator plane is closed: every operation is declared entries,
  operator, self or public in the matrix, and an operation with no row fails.

## Acceptance

Doc-driven, and the matrix is the acceptance. The passage is
`docs/who-can-do-what.md`; `tests/test_doc_who_can_do_what.py` runs it against
REST, HTTP MCP, stdio MCP and the CLI.

> **Who can do what.** Pyrite has two kinds of action. Reading and writing
> entries is decided per KB, by your grant. Everything else is the operator's:
> the person who runs Pyrite and holds its config file. If you can read a KB
> you can read what is derived from it; if you cannot, it answers exactly as if
> it did not exist.

Setup: a library with KBs `notes` (published), `private` and no KB `missing`;
principals the operator, `alice` (write on `notes`), `bob` (read on `notes`),
`carol` (no grant), and anonymous.

- `alice` updates an entry in `notes`; it succeeds. `bob`'s update is refused.
- `carol` asking for `private` and anyone asking for `missing` get the same
  answer, byte for byte, on every surface.
- `alice` does not see `kb_push`, `kb_manage` or the registry tools, and a
  direct call to one is refused.
- Anonymous reads `notes` through `/site`; `private` is absent from the site
  and from the sitemap; `/mcp` refuses an anonymous connection.
- The operator's CLI and stdio MCP act on every KB with no credential.

The matrix is data (`kb/designs/authorization-matrix.md`, authored with the
implementation, seeded by the table above): one row per operation family and
surface, with the entry-point patterns, the action, the resource and where the
write lands. Three tests read it. **Completeness**: every entry point in
`tests/_surface_inventory.py` matches exactly one row, and every "not
reachable" cell is asserted absent. **Oracle**: for each reachable cell, run
the principals above against readable, private and missing KBs and assert the
surface's answer equals the policy's. **Parity**: one row and one principal
give the same decision on every surface (P8). A new route or tool with no row
fails; a row whose cell changes fails until the code does.

## Amends

All of these are accepted ADRs (ADR-0031 is a draft), amended once this is
accepted.

- **ADR-0037, section 1**, the action vocabulary (`KB_READ`, `KB_WRITE`,
  `KB_ADMIN`, `INSTANCE_ADMIN`, `USER_MANAGE`, `SETTINGS_SECRET`, `SELF`,
  `REPO_EGRESS`): the decisions are entries read, entries write, operator and
  self. `KB_ADMIN` leaves the grant vocabulary. `INSTANCE_ADMIN`,
  `USER_MANAGE`, `SETTINGS_SECRET` and `REPO_EGRESS` remain as names inside the
  operator plane, each decided as "is the principal the operator".
- **ADR-0037, section 1**, "**Services do not take a principal** (the answer to
  #383's question, for now)", and **Decisions recorded 3**, "Services take a
  principal: later": narrowed. The write pipeline and the hook and plugin
  context receive the acting identity (decision 7). Read services still take a
  scope.
- **ADR-0037, Decisions recorded 5**, "`REPO_EGRESS`: theme 3c maps it to
  today's tiers exactly. Splitting it out is still ADR-0031's decision":
  repository egress is the operator's (decision 5). Theme 3c maps it to the
  operator, not to the write tier. `/repos/subscribe` and `/repos/fork` change
  accordingly.
- **ADR-0037, section 5.4**, the principal matrix row "KB admin": removed.
- **ADR-0006**, "The server is started at a chosen tier": for the local
  process the tier is an operator-chosen tool filter, not authorization. Over
  HTTP the tools shown follow the principal, and every call is decided by the
  policy (P9). "Admin: index sync, KB manage" is the operator's tier and is not
  shown to users.
- **ADR-0004**, "App layer: hooks check `before_save` that the author matches
  the folder": the hook's acting identity is injected by the dispatcher
  (decision 7), and a hook only refuses (ADR-0042 decision 4).
- **ADR-0031 (draft), section 2**, "Grants, not modes ... a shared instance
  issues credentials without the repo/egress capability": grants are `read` and
  `write` on entries; egress, push and publish are the operator's now, and
  delegated capabilities are decision 6's direction.
- **ADR-0024**: its "Permissions model (V1)" is superseded by this ADR and
  ADR-0044.
- **ADR-0023** (the static site cache) gains decision 8.

## Consequences

**Easier**
- One statement of who can do what, and one test that finds every place the
  code disagrees with it.
- The per-KB owner role, whose only powers were over a KB's grants, is removed
  rather than designed.
- A hook and a plugin tool know who is acting without trusting an argument.

**Harder**
- Behaviour changes: repository subscribe and fork move from the write tier to
  the operator; a KB's owner no longer manages its grants (the operator does,
  in the file, ADR-0039); the web's KB-admin controls go.
- Sharing a KB with another user is the operator editing the file. Until
  capabilities exist (decision 6), a user cannot share their own KB.
- The matrix has to be kept current: it fails the build when it is not.

**Deleted**
- The `KB_ADMIN` action and the per-KB admin role; the inline per-KB admin
  checks in the grant routes; the "KB admin" principal in ADR-0037's matrix.

## Alternatives considered

- **Keep a per-KB owner role** (a grant that lets its holder manage the KB's
  other grants). Rejected: it is a bespoke authorization object whose only use
  is the creator of an ephemeral KB, and it needs a bound on what an owner may
  grant. The operator edits grants instead.
- **Capabilities now** (egress, push, publish as delegated capabilities).
  Deferred: the direction is decided (decision 6), the mechanism waits for
  multi-user to be designed.
- **Ownership declared inside the KB's own tree** (`kb.yaml` or an access file).
  Rejected: anyone who can write the KB could grant themselves access.
- **Services take a principal everywhere** (ADR-0037's deferred alternative).
  Still deferred; decision 7 takes only the write pipeline and hooks.
- **Anonymous MCP.** Rejected for now (decision 9).

## Questions for the maintainer

Ranked by what they block.

1. **Which HTTP principals are the operator.** The holder of the operator
   credential, and also an instance-admin account (an account the operator's
   file lists as an operator)? *Recommended: both*, so the web's administration
   pages need no second credential. Blocks the matrix.
2. **What is `self`.** Own profile, keys, tokens, stars and sessions. May a user
   also share their own ephemeral KB with another user, or is that the
   operator's grant until capabilities exist? *Recommended: the operator's.*
   Blocks the operator-plane list (decision 5).
3. **MCP over HTTP: which tools are shown.** Today by global role; the leaning
   is by the highest rung held on any KB. Decide the leaning? *Recommended:
   yes*; every call is decided per KB regardless, so the list is a convenience.
   Blocks nothing before multi-user.
4. **Settings split.** Instance settings are the operator's; which settings are
   per-user (`self`)? *Recommended: a user's own AI provider key and
   preferences only; everything else the operator's.* Blocks the settings row
   of the matrix.

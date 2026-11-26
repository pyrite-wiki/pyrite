---
id: epic-pyrite-publication-strategy
title: "Epic: Pyrite publication strategy — static sites + hosted investigation instance"
type: backlog_item
tags: [strategy, publication, hosting, multi-user, static-sites, journalism]
links:
- target: epic-normalization-and-data-cleanup
  relation: has_subtask
  kb: pyrite
- target: epic-journalists-pyrite-wiki-hosted-research-platform-for-investigative-journalists
  relation: supersedes
  kb: pyrite
- target: pyrite-white-labeling
  relation: has_subtask
  kb: pyrite
- target: pyrite-dynamic-sitemap
  relation: has_subtask
  kb: pyrite
- target: pyrite-entry-page-seo-meta
  relation: has_subtask
  kb: pyrite
- target: pyrite-remove-static-renderer
  relation: has_subtask
  kb: pyrite
importance: 5
kind: epic
status: retired
priority: high
effort: XL
rank: 2400
lifecycle: archived
---

## Thesis

Pyrite serves two fundamentally different audiences with two fundamentally
different tech stacks. Conflating them — as the cascade plugin did — is
how we got silent drift, baked-in site-specific assumptions, and
duplicated infrastructure.

**Consumption is static. Investigation is live.**

### Surface 1: Static publication sites

Public-facing, read-only, fast, cheap, indexable. Each site owns its
own design, narrative framing, SEO, and performance characteristics.

- `capturecascade.org` — Cascade Series reader experience (React
  viewer today; may move to Hugo to match detention-pipeline)
- `detention-industrial` static site (Hugo) — already the reference
  implementation of this pattern
- Future sites for other investigations

Pyrite's role: publish versioned, documented data exports. Site repos
are consumers of that data, not plugins inside pyrite.

### Surface 2: Hosted investigation instance

`investigate.transparencycascade.org` — a multi-user Pyrite deployment
where journalists work live. Search, entry editing, graph, MCP, AI
tools, per-user worktrees, admin merge queue.

Pyrite's role: be the platform. Authentication, authorization, editing,
collaboration, publishing.

## The shared data pipeline

```
  ┌──────────────────────┐
  │  investigate.*       │  ← journalists edit here (live Pyrite)
  │  (hosted Pyrite)     │
  └──────────┬───────────┘
             │ pyrite ji export-timeline (generic JSON)
             ▼
  ┌──────────────────────┐
  │  Pyrite data exports │  ← documented public contract
  │  (timeline.json etc.)│
  └──────────┬───────────┘
             │ site-specific adapters (in site repos)
             ▼
  ┌──────────────────────┐     ┌──────────────────────┐
  │  capturecascade.org  │     │  detention-industrial│
  │  (Hugo / React)      │     │  (Hugo)              │
  └──────────────────────┘     └──────────────────────┘
```

Sites pull pyrite exports on build. Pyrite doesn't know which sites
exist or what templates they use. Sites don't know how pyrite stores
data internally — only the public export schema.

## Problems this solves

- **No site-specific code in plugins.** Today `static_export.py` bakes
  capturecascade's field names into the cascade plugin. Future sites
  would either duplicate this or force the plugin to grow ever-more
  `--format` flags. Sites owning their own adapters is the clean seam.
- **Versioned data contract.** If JI exports are the public API for
  publishing, they get a schema, a version number, and compatibility
  promises. Much easier to reason about than "whatever cascade was
  emitting this week."
- **Hosted instance is independent of publishing.** Journalists can
  work in the hosted instance without a site existing. Sites can be
  built from any point-in-time pyrite export without needing pyrite
  running.
- **Onboarding story.** Inviting another journalist to an
  investigation becomes "sign in at investigate.*" — not "install
  pyrite, clone the KB, run the CLI."

## Subtasks

### Sub-epic A: Cascade deprecation + data-health hardening

[[epic-normalization-and-data-cleanup]] — already in flight. Produces
the JI plugin as the canonical investigation-types home, establishes
the generic timeline-export JSON as a public contract, retires the
cascade plugin.

### Sub-epic B: Hosted investigation instance

Much of this sub-epic is **already built**. This epic supersedes the
earlier
[[epic-journalists-pyrite-wiki-hosted-research-platform-for-investigative-journalists]]
— an older version of the same thinking, aimed at `journalists.pyrite.wiki`
as the first branded deploy. The current plan rebrands the first deploy
as `investigate.transparencycascade.org` and generalizes "one branded
hosted Pyrite" into "hosted Pyrite as a white-labelable product."

**Already done** (confirmed via KB search and code):

- Multi-user auth (local + GitHub OAuth)
- Role-based access control (read/write/admin)
- **Per-KB permissions** ([[per-kb-permissions]], done) — admin
  configures which users see which KBs. Decision #2's access model
  is already implemented.
- **BYOK LLM** ([[byok-per-user-encrypted-api-key-storage-and-routing]],
  completed; [[byok-ai-gap-analysis]], done) — per-user encrypted
  API keys, `LLMService.with_user_key()` routes per-request. Decision
  #5's LLM path is shipped.
- **Worktree collaboration** ([[epic-fork-system]] done, ADR-0024,
  [[worktree-write-routing]] done, [[worktree-service]] done,
  [[worktree-merge-queue]] done) — per-user git worktree,
  `OverlaySearchBackend` for diff-over-main indexing, admin merge
  queue UI. Handles decisions #2 and #3's editing model end-to-end.
- **Authenticated MCP transport**
  ([[authenticated-mcp-endpoint-sse-http-transport-with-bearer-auth]],
  completed) — SSE / Streamable HTTP with bearer tokens mapped to
  user accounts and per-KB permissions. Claude Desktop / Claude
  Code can already connect.
- Docker + Caddy deployment template (`selfhost/`)
- Usage tiers + quotas
- Web UI: search, browse, graph, timeline, tags

**Remaining tickets** (absorbed from the superseded journalists epic
where applicable):

- **`transparency-cascade-hosted-deploy`** — branded deploy for
  `investigate.transparencycascade.org`: hostname, TLS, backup,
  monitoring. Mostly a clone-and-configure of `selfhost/`.
- **`pyrite-white-labeling`** — genuinely new (decision #4):
  configurable logo, site name, nav links, footer, theme colors,
  help-text overrides. Driven by env vars or a deploy-level config
  file. Not tracked anywhere today despite being needed for both
  Transparency Cascade and any future branded deploy.
- **`invite-code-registration`** — from the superseded journalists
  epic Phase 0. Needed for journalist onboarding without a public
  signup flow.
- **`kb-seeding-packaging-script`** — from the superseded journalists
  epic Phase 0. Reproducible script that loads a curated KB set
  (cascade-timeline, cascade-research, etc.) into a fresh deploy.
- **`hosted-instance-backup-and-recovery`** — git-backed KB data is
  easy; SQLite index + embeddings are not. Need a documented plan.
- **`journalist-onboarding-flow`** — "one-screen entry creation"
  path for non-technical users.

### Sub-epic C: Static publication infrastructure

- **`document-generic-timeline-export-schema`** — turn today's
  `static_export.py` output into a versioned public JSON schema with
  compat guarantees. Landed inside [[ji-absorb-cascade-cli]] but worth
  its own ticket to track the *schema* as a public artifact, not just
  the code that emits it.
- **`capturecascade-site-repo-extraction`** — move the React viewer
  + any capturecascade-specific JSON reshaping into its own site
  repo, mirroring detention-pipeline. Deprecation target: the
  `extensions/cascade/src/pyrite_cascade/static_export.py` path after
  the JI move lands.
- **`site-build-pattern-doc`** — write up how a new site is built from
  a pyrite export (detention-pipeline as the reference), so the next
  site doesn't re-invent the pipeline.
- **`capturecascade-on-hugo-spike`** — optional: evaluate moving
  capturecascade from React to Hugo to unify with detention-pipeline.
  Tracked in memory as a rewrite plan; decide whether to pursue.

## Resolved decisions

1. **Generic export schema → today's cascade output is v1.** The bytes
   `pyrite cascade export` emits today become the documented v1
   timeline-export schema. Write a JSON Schema file alongside the code
   in JI. Semantic versioning: new fields → minor, breaking changes →
   major with migration note. The first real work is *writing it down* —
   the schema exists today only as whatever `static_export.py` happens
   to produce.

2. **Access control → curated KB set, per-user branches, merge-gated
   publication.** The hosted instance hosts a curated set of KBs (public
   + private-to-invited-journalists). The admin chooses which KBs live
   on the instance and who has access to each.
   - **Commits to a user's own branch** are visible to that user
     immediately (their working view of the KB).
   - **Commits from other journalists on the same KB** are visible once
     merged to the KB's main branch.
   - **Commits go public** (to a static publication site) only when:
     (a) the KB is marked as published, AND (b) the commit has been
     merged into main. Both conditions required.
   - Private KBs never publish to public sites regardless of merge
     state.

   **Already implemented:** per-KB permissions
   ([[per-kb-permissions]]), worktree-based per-user branches
   (ADR-0024), admin merge queue, `OverlaySearchBackend` for
   diff-over-main reads, and the `KBConfig.default_role` field —
   which serves as the "publicly visible" signal for both the
   permissions system and the [[pyrite-dynamic-sitemap]] crawler
   surface. No separate `published:` flag needed; `default_role ==
   "read"` is the authoritative signal.

3. **Sites sync live from main** (follows from #2). Static publication
   sites rebuild on every merge-to-main of a published KB. No
   separate snapshot/staging step — the merge *is* the publish gate.
   Matches detention-pipeline. Unpublished KBs (and unmerged branches
   on published ones) never reach the public site.

4. **White-labeling is a Pyrite feature.**
   `investigate.transparencycascade.org` won't be pyrite-branded — it
   ships under the Transparency Cascade identity. Build white-labeling
   as a first-class Pyrite capability (configurable logo, nav, help
   text, footer, domain-aware theming) rather than forking or hacking
   the UI for one deploy. This opens up "hosted Pyrite for other
   investigations" as a downstream possibility without locking it in
   as a commitment today.

5. **BYOK LLM chat (shipped); embeddings operator-paid via overlay.**
   The cost model splits cleanly along the overlay architecture
   already built in ADR-0024 / [[worktree-write-routing]]:

   - **LLM chat:** BYOK per user. Implemented and shipped
     ([[byok-per-user-encrypted-api-key-storage-and-routing]] completed,
     `LLMService.with_user_key()` routes per-request). Runaway-cost
     risk lives here and is handled.
   - **Embeddings on main (shared):** operator-paid. Generated once
     per canonical entry; cost scales with merges to main, not with
     per-user edit activity. Small KB (~5000 entries) is a
     one-time few-dollars cost; ongoing cost tracks editorial
     velocity, not user count. Not a runaway risk.
   - **Embeddings on diff (per-user):** operator-paid. Each user's
     diff index holds embeddings only for their unmerged edits.
     Bounded by per-user edit rate (50 entries/day = trivial).
     Same pool as main embeddings.
   - **Known V1 limitation (Path B, accepted):**
     `OverlaySearchBackend.search_semantic` queries main only
     (`overlay_backend.py:326`). A user's unmerged entries are
     findable in FTS but not in vector search until an admin merges
     them to main. UI should surface this with a hint
     ("draft entries appear in keyword search; semantic search
     available after merge") rather than extending the overlay. Close
     the gap later if real use reveals it matters.

   No BYOK embedding plumbing needed. The earlier open sub-question
   ("do embeddings use the user's key?") is resolved by the overlay:
   most embedding volume is shared main content, and per-user diff
   volume is too small to justify the complexity.

## Recommended order

1. **Finish [[epic-normalization-and-data-cleanup]]** (sub-epic A).
   Produces the JI generic-export contract that everything else depends
   on.
2. **Write `document-generic-timeline-export-schema`** (sub-epic C,
   first ticket). Locks the data contract per decision #1 — today's
   cascade output becomes the documented schema.
3. **Extract capturecascade site repo** (sub-epic C). Validates the
   site-consumes-export pattern end-to-end with a real site. Also
   validates decision #3 (live-sync from main) in a controlled setting.
4. **Promote `project_v1_multiuser` memory plan to a KB epic** and pick
   up the foundational tickets. Adds decisions #2, #4, #5 as new
   subtasks: access control, white-labeling, BYOK keys.
5. **Deploy `investigate.transparencycascade.org`** once multi-user
   foundations + access control + white-labeling are stable enough
   for non-developer users.

## Decision impact on existing work

Most of what the decisions require is **already shipped**. The genuinely
new work is small:

| Decision | Status |
|----------|--------|
| #1 generic export schema | Document today's bytes as v1 — new ticket in sub-epic C |
| #2 per-KB access control | **Shipped** ([[per-kb-permissions]], done) |
| #2/#3 KB publication signal | **Shipped** — `default_role == "read"` is the authoritative signal. Used by per-KB permissions and [[pyrite-dynamic-sitemap]]. No separate `published:` flag needed. |
| #2/#3 per-user branches, overlay reads, merge queue | **Shipped** (ADR-0024, [[worktree-write-routing]]) |
| #3 live sync from main | **Shipped** pattern (detention-pipeline); replicate for capturecascade |
| #4 white-labeling | **Shipped** ([[pyrite-white-labeling]]) |
| #5 BYOK LLM chat | **Shipped** ([[byok-per-user-encrypted-api-key-storage-and-routing]]) |
| #5 overlay-safe embedding cost model | **Shipped** (main embeddings shared, diff embeddings per-user; Path B gap accepted) |

Net new features: white-labeling (**shipped**), sitemap + SEO meta
(**shipped**), invite-code registration, KB-seeding/packaging script,
backup automation, onboarding flow. Everything architectural is
shipped; what remains is productization tickets for the actual
hosted-instance deploy.

## Out of scope

- The *content* strategy for capturecascade.org or detention-industrial
  (editorial decisions, narrative). This epic is about infrastructure.
- Retired pyrite demos or single-user workflows. The hosted instance
  is a separate deploy target from `demo.pyrite.wiki`.
- PyPI / distribution of pyrite itself. Publication strategy for the
  *platform* vs. publication strategy for the *knowledge bases* are
  different epics.

## Related

- v1 multi-user work (memory: `project_v1_multiuser`) — prerequisite
  for sub-epic B; promote to a KB epic when that work starts
- [[epic-normalization-and-data-cleanup]] — sub-epic A, already in flight
- Memory: `project_cascade_deployment` (current cascade deploy setup)
- Memory: `project_cascade_viewer_rewrite` (React → Svelte 5 rewrite plan)

---
id: adr-0031
type: adr
title: "The API Is the Product Surface; Frontends Are Scoped Clients"
adr_number: 31
status: superseded
deciders: ["markr"]
date: "2026-09-17"
tags: [architecture, api, frontend, authorization, extensibility, deployment]
links:
- target: adr-0007
  relation: refines
  kb: pyrite
- target: adr-0006
  relation: related
  kb: pyrite
- target: adr-0002
  relation: related
  kb: pyrite
- target: adr-0023
  relation: related
  kb: pyrite
- target: adr-0030
  relation: related
  kb: pyrite
- target: api-authorization-coverage-test
  relation: related
  kb: pyrite
- target: repo-access-is-a-capability-not-a-tier
  relation: related
  kb: pyrite
- target: adr-0043
  relation: superseded_by
  kb: pyrite
superseded_by: adr-0043
---

# ADR-0031: The API Is the Product Surface; Frontends Are Scoped Clients

> **Withdrawn by the maintainer, 2026-10-03.** Replaced by [[adr-0043]] (two planes:
> entries by grant, everything else the operator's). The status is `superseded`, the nearest allowed value to "withdrawn"; this draft was never accepted. The text below is kept as the
> record of the exploration.

> **Was a draft.** Circulated for iteration, not decision. Open questions at the
> end are genuinely open; several would change the shape of the decision.

## Context

ADR-0007 describes three surfaces — Claude Code plugin, MCP server, Web UI —
sharing one backend. That was right for a single-operator tool. It is now
carrying four distinct products that the word "Web UI" hides:

1. **Operator workspace.** One trusted user, full CRUD, settings, merge
   queue, and (per ADR-0030) local agent run execution. This is what exists.
2. **Public reader.** Untrusted anonymous visitors, read-only, crawlable.
   Partly exists already as the ADR-0023 `/site/` HTML cache, which was built
   precisely because the SvelteKit SPA is invisible to search engines.
3. **Shared instance.** Authenticated but *not fully trusted* users —
   the invited-peer pilot, and later a journalist federation sharing research
   KBs across organizations. Per-user BYOK AI, no run execution, and
   sharing grants that read/write/admin cannot express.
4. **Domain workspace.** A software team using the `software-kb` extension,
   whose vocabulary is ADRs, backlog items, epics, review queues and work
   logs — not entries, tags and collections.

These differ on two independent axes: **who the user is** (trust, scope,
credentials) and **what the domain is** (vocabulary, workflow, affordances).
One application cannot serve all four without conditionals that are neither
reviewable nor enforceable.

### Two facts that shape the decision

**The frontend is already mostly a library.**

```
src/lib/     14,026 LOC   api, components, editor, stores, types, utils
src/routes/   7,929 LOC   19 routes
```

64% of the web app is domain-agnostic machinery. The routes are thin. And
they sort cleanly by audience: `settings`, `merge-queue`, `tasks`, `qa`,
`register`, `login`, `changes`, `daily` are operator-only; `entries`,
`search`, `graph`, `timeline`, `tags`, `collections`, `orient`, `overview`
are reader-plausible. Roughly half the current app has no business existing
on a public instance.

**Plugins extend the backend and stop at the API.** The protocol has 18
extension points — `get_entry_types`, `get_mcp_tools`, `get_validators`,
`get_db_tables`, `get_workflows`, `get_kb_presets`, and so on — and **not
one of them concerns the frontend.** The consequence is visible in
`software-kb`: 10 entry types and 23 `sw_*` MCP tools, a complete domain
model, with no UI of its own. A `backlog_item` and a `timeline_event` render
identically in the generic entry browser. The backend has been
general-purpose since 0.18; the frontend never was.

### The rejected framing, and why

An earlier version of this decision was **deployment modes**: one
application, one API, a mode flag selecting which capabilities are reachable.
Rejected because *a mode flag is runtime state, and runtime state can be
wrong.* If `RunService` exists in the process and configuration decides
whether it is reachable, then a misconfiguration, a bug, or a compromised
settings write turns it on. This is the same objection ADR-0030 §4 makes
about harness gates, and `hosting-security-requirements` REQ-1 states the
principle directly: *"If the capability doesn't exist, it cannot be
compelled."*

A second framing — **two separately-built applications** — was also
insufficient on its own. A build split is a real *product* boundary but not a
*security* boundary: a public app that does not render a fork button does not
prevent anyone from calling `POST /repos/fork` with a write-tier credential.
The endpoint is still there.

The resolution is that these solve different problems and both are needed,
with the API carrying the guarantee.

## Decision (proposed)

**The REST API is the product surface. It carries the complete authorization
model. Frontends are clients that hold credentials, scoped by audience and by
domain. No frontend enforces anything.**

### 1. The API is the only security boundary

Every guarantee about what a principal may do is expressed and enforced in
the API, not in which routes a bundle happens to contain. A frontend omitting
a feature is a product decision; a credential lacking a grant is the
enforcement.

This makes two already-filed tickets prerequisites rather than hygiene:

- [[api-authorization-coverage-test]] — the model exists (router-wide
  `verify_api_key` + `requires_tier("read")` floor, 46 escalating guards) but
  nothing verifies it is complete. Under this ADR, an unguarded endpoint is
  not a latent bug; it is a hole in the only boundary.
- [[repo-access-is-a-capability-not-a-tier]] — read/write/admin is a content
  ladder. Repo, git, and network-egress operations are a different axis,
  currently approximated inconsistently (`repos.py` forks at write tier,
  `git_ops.py` pushes at admin, MCP exposes only `kb_commit`/`kb_push`).

**Neither the public reader nor the shared instance ships before both land.**

### 2. Grants, not modes

Deployment differences are expressed as *which grants a credential carries*,
not as which code is present. A shared instance issues credentials without
the repo/egress capability and without run execution; a local instance issues
one credential with everything.

This keeps the earlier mode-flag objection answered: a grant is data about a
principal, checked on every request, not a global toggle whose failure mode
is silent over-permission.

### 3. Frontends are scoped clients, and there will be more than two

A frontend is characterized by the audience it serves and the domain
vocabulary it speaks. The named artifacts:

| Artifact | Audience | Domain | Ships with | Run execution |
|---|---|---|---|---|
| `pyrite-core-ui` | — (library) | — | consumed by the rest | — |
| `pyrite-desktop` | single trusted user | generic KB | **pyrite itself** | yes (ADR-0030) |
| `pyrite-software` | trusted colleagues | software-kb vocabulary | `pyrite-software-kb` | operator-dependent |
| `pyrite-ji` | journalists, peers | investigation vocabulary | `pyrite-journalism-investigation` | no |

`pyrite-desktop` names the *installed application* — which carries the ACP
subprocess and local-filesystem assumptions in the name, and makes the
contrast with any hosted surface explicit rather than inferred.

**Naming collision, deliberate but not free.** `pyrite-software-kb` and
`pyrite-journalism-investigation` are existing Python distributions. Domain
UI names sit on top of them. Per §5 the UI ships *inside* those
distributions, so this is one product with two halves rather than two
artifacts sharing a prefix — but the npm/PyPI split means that is a
convention, not something the packaging enforces.

**No public-reader entry.** The ADR-0023 `/site/` cache remains the public
read surface for now; whether a client-side reader supersedes it is open
question 1. A shared journalist instance (untrusted-ish peers, per-user
BYOK, cross-org KB sharing) is a *deployment* of `pyrite-ji` under §2 grants,
not a fifth artifact.

The point of the decision is that adding a fifth should require no
architectural change.

### 4. `pyrite-core-ui`: the shared library becomes addressable

`web/` is currently `private: true`, has no `exports` map, no `src/lib`
barrel, and a `version` (0.20.0) already drifted from `pyproject.toml`
(0.24.0). It is an application, not a library.

The extraction is a package boundary over the existing 14K LOC: an explicit
public surface, a barrel, peer-dependency handling for Svelte, and a decision
about what is public versus internal.

**Sequencing recommendation:** extract `api` + `types` first as a
framework-neutral client package. A third-party frontend needs typed access
to `/api/*` far more than it needs Svelte components, and that package is
smaller, has no framework coupling, and lets non-Svelte clients participate.
Extract components only when a second real consumer exists.

### 5. UI contribution is separable for the base app, bound for domains

Symmetric with the plugin protocol's 18 backend extension points, a plugin
should be able to contribute UI: type-specific renderers, domain routes,
board configurations. `software-kb` is the proving case — 10 entry types and
23 `sw_*` tools with no UI of its own is the gap, not a design.

**The separability rule is asymmetric, and the asymmetry is about who
installs:**

- **`pyrite-desktop` UI must be separable from any extension.** It ships with
  pyrite and is the base application — its UI exists before any extension
  does, so it cannot be bound to one.
- **`pyrite-software` and `pyrite-ji` UI are bound to their extension.**
  Someone installing `pyrite-software-kb` wants the board and the ADR views,
  not a generic entry browser. Shipping the plugin without its UI delivers
  exactly what is wrong today.

Stated as a rule: **the base UI is a product; domain UIs are part of their
extension.** Extensions already declare themselves per-distribution via
`[project.entry-points."pyrite.plugins"]`, so a bound UI is the same
distribution additionally declaring a UI contribution.

**This inherits a lesson from `plugin-type-resolution-scoping`,** where
global, discovery-ordered type remapping resolved `person` differently on
different machines. Any UI contribution point must be **scoped and declared**
— by KB type, per ADR-0029 libraries — never a global registry whose winner
depends on load order.

**The hard part is version coupling, and it is unresolved.** If domain UI
ships inside a Python wheel, compiled JS travels in that wheel and must match
the `pyrite-core-ui` major the host app was built against. That is the
`mcp<2.0.0` pin problem one layer up: an unbounded compatibility claim
between separately-released artifacts. Three shapes, none chosen:

1. **Declarative contribution** — a manifest describing views over generic
   `pyrite-core-ui` components. No compiled JS in the wheel, no Svelte
   version coupling, limited expressiveness. Probably right for most domain
   UI, and the only option that keeps the wheel framework-neutral.
2. **Prebuilt against a pinned `pyrite-core-ui` major** — full
   expressiveness, real coupling, and a matrix problem the day two extensions
   pin different majors.
3. **Source shipped, host builds at install** — maximum flexibility, requires
   a Node toolchain at install time in an otherwise-Python deployment.

This is the crux of open question 4 and likely determines whether §5 is
tractable at all.

## Consequences

### Positive

- One enforcement model, testable in CI, rather than a boundary that depends
  on which bundle a user loaded.
- Adding an audience or a domain becomes a client, not a fork.
- The BHAG's four-wave go-to-market (software teams, journalism, PKM) gets a
  frontend story; today every wave would ship the same generic UI.
- The public reader stops being a separate static-rendering pipeline and
  becomes a client like any other — possibly superseding `/site/`, possibly
  not (see open questions).

### Negative

- **Multiple apps multiply the release surface**: build pipelines, e2e suites,
  deploy artifacts. The Playwright suite is currently nondeterministic and
  non-blocking in CI; multiplying it before fixing it would compound a known
  problem.
- **A published `pyrite-core-ui` is a compatibility obligation.** Once a
  second consumer exists, `lib/` changes are breaking changes. The project has
  never cut a GitHub release, has no working PyPI path (the `pyrite` name sits
  on a locked pre-2FA account), and `pyrite-web`'s version has already drifted
  four minors. Taking on npm semver obligations before Python release
  discipline exists is a real risk.
- **A plugin UI contribution point is a new extension surface** with its own
  scoping, conflict, and trust questions — the backend equivalent produced
  ADR-0002, capability declarations, and the type-resolution bug.
- The two prerequisite tickets are now blocking, which lengthens the path to
  a shared instance.

### Neutral

- ADR-0007's three-surface diagram is refined rather than replaced: Surface 3
  becomes a family of clients.
- The operator workspace is unchanged in the near term; it is simply named as
  one client among several.

## Open questions

1. **Does the public reader replace `/site/`, or coexist?** `/site/` exists
   because SPAs are not crawlable. A client-side public reader inherits that
   problem. Keeping both means two public surfaces; replacing means solving
   SSR, which ADR-0023 explicitly tried and abandoned.
2. **Where do BYOK keys live for untrusted users?** ADR-0007 §3 says "keys
   never leave the server," correct when the server is your laptop. On a
   shared instance it puts every user's key inside the legal-compulsion
   surface REQ-3.3 requires you to document. Browser-held keys are a
   different mechanism, not a configuration of that one.
3. **Is `pyrite-core-ui` published, or vendored?** A monorepo workspace
   package with no npm publish avoids the semver obligation while still
   allowing two apps. It also blocks genuine third-party frontends.
4. **Which of §5's three contribution shapes?** Declarative manifest,
   prebuilt-against-a-pinned-major, or source-built-at-install. This
   determines whether a domain UI can ship inside a Python wheel at all, and
   whether `pyrite-core-ui` takes on a compatibility obligation to
   third-party extensions rather than only to first-party apps. Declarative
   is the only shape that avoids both the Svelte version coupling and a Node
   toolchain at install time, so the real question is whether it is
   expressive enough for a kanban board and an ADR browser — which
   `pyrite-software` would answer empirically.
5. **Does the shared instance run a different backend** (fewer routers
   mounted, `RunService` never constructed), or the same backend with grants
   doing the work? §2 says grants. §1's "the API is the only boundary" is
   stronger if some capabilities are structurally absent — these are in
   tension and the tension is not resolved here.
6. **Sequencing against 0.25.** Both prerequisite tickets are high priority
   and the shared-instance pilot is the 0.25 epic. Does this ADR block the
   pilot, or does the pilot ship with the operator workspace and a manually
   scoped credential?

## Review response (2026-09-17)

> Written during a whole-project review (code health, repo hygiene, docs,
> positioning, and a walk through demo.pyrite.wiki as an anonymous visitor).
> This is input to the draft, not a decision. It agrees with §1 and §2,
> argues for a narrower §3 and §4, picks a shape for §5, and proposes a
> sequence.

### The question the draft does not ask: what is the UI *for*?

The draft sorts frontends by audience and domain. Both axes assume the UI's
job is already known. It is worth stating, because it determines which
routes deserve investment.

The positioning that the evidence supports — the newsroom origin, the
`FEEDBACK.md` sessions with four agents claiming tasks atomically across
50+ KBs, an outside contributor running Pyrite as a server for agents — is
*knowledge your agents can write to, and you can verify.* Under that
positioning, agents and the CLI are the primary **authoring** surface. The
web UI's job is **human oversight of agent work**.

Most of the current UI is built for the opposite job. Tiptap + CodeMirror
dual editing, slash commands, daily notes, quick switcher, web clipper and
gallery views are a Notion/Obsidian feature list. A solo maintainer does not
win that contest, and does not need to: files are the source of truth, so
anyone who wants a polished editor can point Obsidian or VS Code at the same
directory. That is a consequence of ADR-0001, not a gap.

The routes no competitor has are `changes`, `merge-queue`, `qa` and `tasks`.
In the demo's sidebar, those that show above the fold sit at the bottom of a
flat 12-item list. The context section of this ADR classifies exactly these
routes as "operator-only" — which is true, and is also a list of the
product's differentiators.

### Agreement: §1 and §2

The API as the only security boundary, and grants rather than modes, are
both right. The same-day review supports making
[[api-authorization-coverage-test]] a hard prerequisite rather than hygiene:
authorization applied by convention does drift, and under this ADR a drifted
endpoint is a hole in the only boundary. Build the coverage test first;
details of what it would catch today belong in the fix PR, not in a public
ADR.

### Pushback: §3 and §4 name more artifacts than the project can release

The draft's own Negative section makes the case: no GitHub release has ever
been cut, the PyPI name is unreachable, `web/package.json` has drifted four
minors, and the Playwright suite is nondeterministic and non-blocking. Four
named artifacts plus a published library multiplies every one of those
problems.

Proposed narrowing:

- **One app for now.** Audience differences are a nav filtered by grants.
  Per §1 this is a product decision, not enforcement, so it costs nothing in
  security terms.
- **Open question 3: vendored.** If a library seam is needed, make it a
  workspace package with no npm publish. Do not take on npm semver before
  Python release discipline exists.
- **Extract the typed `api` + `types` client only when a second consumer
  actually exists.** The sequencing recommendation in §4 is right about
  *order*; this adds a trigger.
- **Gate:** Playwright deterministic and blocking in CI before any new
  surface ships.

The artifact table in §3 remains useful as a map of where this could go. It
should not be a work plan yet.

### Open question 4: declarative, proven on `software-kb`

Choose shape 1. Beyond the reasons the draft gives (no compiled JS in
wheels, no Svelte version matrix, no Node toolchain at install), there is a
thesis-level reason: the roadmap's BHAG is "the schema is the program." An
agent that can write a `kb.yaml` can write a view manifest beside it. An
agent cannot ship a prebuilt Svelte bundle. Shapes 2 and 3 would make UI the
one part of a self-configured domain that still needs a human with a
toolchain.

Test of expressiveness, as the draft suggests: a backlog board and an ADR
list for `software-kb`. If the manifest can express those two, it is
sufficient for the journalism views already in the backlog (entity profile,
claims coverage, source management panel). If it cannot, that is learned
cheaply, before any compatibility promise is made. The scoping lesson from
`plugin-type-resolution-scoping` applies unchanged: manifests are declared
per KB type, never a global registry.

### Open questions 1, 5 and 6

1. **Public reader: keep `/site/`.** Do not reopen SSR. ADR-0023 tried it and
   abandoned it; nothing in this ADR changes that calculus.
5. **Shared-instance backend: a suggestion, not a position.** The mode-flag
   objection is about *runtime* state. A separate app factory (a distinct
   entry point that never imports or constructs `RunService`) is selected by
   which process is started, not by a setting that can be flipped or
   compromised. That gives structural absence for the few capabilities where
   REQ-1 demands it, while grants do the work for everything else. Worth
   testing against ADR-0030 §4 before adopting.
6. **Do not block the 0.25 pilot on the frontend split.** Ship the pilot with
   the existing app, a grant-filtered nav, and a manually scoped credential —
   *after* the two prerequisite tickets land. The API is the boundary either
   way; the split buys nothing the pilot needs.

No position offered on open question 2 (BYOK key custody); the review did not
examine it.

### Proposed sequence

> **Scoping note from the maintainer (2026-09-17):** everything in this ADR
> and in ADR-0030 is post-0.25. That settles open question 6: 0.25 is the
> shared-instance pilot on the existing app, behind the two prerequisite
> tickets. Of the steps below, only step 1 and the prerequisites compete
> with 0.25 work; steps 2–3 and the ADR-0030 cross-reading are the agenda
> for what follows it, and step 4 is 0.25 itself rather than last.

1. **Fix what a first-time visitor sees (days).** Observed on
   demo.pyrite.wiki on 2026-09-17 as an anonymous visitor:
   - search snippets render literal `<mark>` tags as text alongside the real
     highlights;
   - the "Pyrite" wordmark is invisible in light mode;
   - the sidebar shows 12 flat nav items, including QA, Changes and Settings,
     to a visitor with no credential;
   - the sidebar KB selector reads `guide (27)` while the search page reads
     "All KBs";
   - `/auth/me` returns 401 into the console on every page load.

   Existing tickets cover the KB-context authority, the sidebar regroup,
   light mode and graph scoping. Add HTML sanitization of rendered markdown
   to this batch, and the Playwright gate above.
2. **The review surface.** Make "what did agents do since I last looked" the
   home screen: a per-agent, per-commit change feed with diffs; approve and
   revert; QA warnings inline; the task board showing who claimed what;
   provenance on each entry page (commit, author, sources). Most of the
   backend exists. This is also the screen that demonstrates the pitch — a
   short recording of it is a better product-page hero than a feature grid.
3. **Type-aware views via the declarative manifest**, `software-kb` first,
   then the journalism-investigation views.
4. **Shared instance**, per open question 6 above.

If the investigator pilot has a near date, step 4 moves ahead of step 3 and
the journalism views come before `software-kb`.

### Read against ADR-0030

The sections above were written before reading ADR-0030, which is where this
ADR came from. Three things change or sharpen.

**The review surface and ADR-0030's Phase 5 UI are the same product, reached
from opposite ends.** ADR-0030 wants a surface to run, observe, interrupt and
review pipeline work. Step 2 above wants a surface to see what agents did and
accept or revert it. ADR-0030 §6 already says which half is truth:
checkpoints are the progress spine and the ACP stream is display. So the
after-the-fact half — claimed tasks, checkpoints, commits, diffs, QA — can be
built now, on data that exists, with none of ADR-0030's five prerequisites.
It also works for agents Pyrite did not launch: today's Claude Code
conductors, and an outside operator's own agents over MCP. Live run control
(Phases 1–5) later plugs into the same screen rather than arriving as a new
one. Building it first is also the cheapest test of whether Phase 5 is worth
its prerequisites.

**One grant model resolves three open items.** ADR-0030 §6 found that
`task_claim` and `task_checkpoint` are write tier, so a read-tier run cannot
report progress. [[repo-access-is-a-capability-not-a-tier]] found that repo
and egress operations do not fit the tier ladder either. §2 of this ADR says
grants, not modes. These are the same observation: read/write/admin is a
*content* ladder, and task machinery, repo egress and run execution are
orthogonal capabilities. A credential — whether a user's API key or a
per-run MCP session — is then `tier + library + capabilities`. That picks
the second of ADR-0030 §6's three exits, keeps the read-tier research run
alive as a concept, and means the shared instance of §3 is expressed as
"credentials without `repo-egress` or `run-execution`" with no special case.
It should be designed once, in the capability ticket, not three times.

**Open question 5 has a concrete subject.** The capability the mode-flag
objection is really about is `RunService`: an ACP client that spawns
subprocesses with filesystem and terminal access. That is the one thing a
hosted instance must not merely *deny* but not *contain*. The separate
app-factory suggestion above is aimed at exactly this, and ADR-0030 §1 makes
it cheap: run execution is a service with its own router, so a hosted entry
point that never imports it is a small seam, not a fork.

### Freeze list

Keep, but stop investing in: the dual editor, daily notes, the web clipper,
gallery views. None is removed; none gets roadmap time until the review
surface exists.

### Two facts that would change this

- Whether the outside contributor's deployment uses the web UI at all, or
  only REST and MCP. If the latter, that is direct evidence for the
  oversight framing and worth asking.
- The pilot date, per the sequencing note above.

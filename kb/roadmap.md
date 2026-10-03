---
id: roadmap
title: Pyrite Release Roadmap
type: note
tags:
- roadmap
- planning
---

# Pyrite Release Roadmap

## Thesis: agents write, humans verify

Agents produce knowledge faster than people can review it, so human review
attention is the constraint (ADR-0019, Theory of Constraints). Pyrite's job is
to make agent-written knowledge **verifiable**: typed entries, git provenance
down to the commit that introduced a claim, tiered write access, a review
queue, and a UI whose first purpose is oversight of agent work. It was built in
an investigative newsroom, where that property is not optional.

This replaces the earlier "self-configuring infrastructure for agent swarms"
framing (2026-09-17; the old vision doc is kept at
[[bhag-self-configuring-knowledge-infrastructure]]). Autonomous provisioning
may still happen; it is downstream of trust, not the goal.

Every milestone below is judged by one question: does it make agent-written
knowledge easier to trust, for its operator first and for invited readers next?

---

## 0.3 — CLI Foundation (done)

Core CLI, storage layer, service architecture, plugin system (15-method protocol), three-tier MCP server, SvelteKit web UI, content negotiation, collections (phases 1-3, 5), block references (phases 1-2), background embedding pipeline, REST API tier enforcement. 1040 tests.

## 0.4 — MCP Server Hardening (done)

Production-solid MCP for agent workflows. Fixed metadata passthrough bugs, added capture lane validation (`allow_other` on FieldSchema), schema validation on all write paths. 1040 tests.

## 0.5 — QA & Agent CLI (done)

QAService with 9 structural validation rules. `--format json/markdown/csv/yaml` on 11 CLI commands. `pyrite init --template`, `pyrite extension init/install/list/uninstall`. 1086 tests.

## 0.6 — Agent Coordination (done)

Task plugin (7-state workflow, atomic `task_claim` via CAS, `task_decompose`, `task_checkpoint`). Plugin KB-type scoping. Programmatic schema provisioning. QA Phase 2 (assessment entries, post-save validation). 1154 tests.

## 0.7 — Web UI Polish (done)

QA dashboard, graph betweenness centrality, block-ID transclusions, collection embedding in transclusions, WebSocket live updates, cycle detection, transclusion view options, collection nesting. 1000+ entry performance test. 1179 backend + 115 frontend tests.

## 0.8 — UI Design & UX (done)

Brand identity (gold accent, DM Serif Display, "Py" monogram). Dashboard redesign with type distribution chart. Dedicated `/search` page. Responsive sidebar. Keyboard shortcuts modal. Template picker redesign. Page transitions. 1182 backend + 115 frontend tests.

## 0.9 — Code Hardening (done)

Internal quality pass across 8 waves. Key results: `mcp_server.py` 32% smaller (tool schemas extracted), `WikilinkService` extracted from `KBService`, 24 `_raw_conn` calls eliminated, 43 bare `except: pass` replaced with logging, SQL DDL injection prevention, stale `docs/` directory deleted. 1654 backend + 115 frontend tests.

---

## 0.10 — SearchBackend Protocol + PostgresBackend (done)

**Theme:** Pluggable storage backends via a clean protocol abstraction. See [ADR-0014](adrs/0014-structural-protocols-for-extension-types.md) (structural protocols), [ADR-0015](adrs/0015-odm-layer-and-schema-migration.md) (ODM architecture), [ADR-0016](adrs/0016-lancedb-evaluation.md) (LanceDB evaluation).

### Delivered

- **SearchBackend protocol** — 13-method structural protocol in `pyrite/storage/backends/protocol.py`, 66 conformance tests
- **SQLiteBackend** — wraps existing PyriteDB + FTS5 + sqlite-vec (default, local/single-user)
- **PostgresBackend** — tsvector + pgvector, 66/66 conformance tests (~3x indexing, ~2x query overhead vs SQLite — acceptable for server deployments)
- **LanceDB evaluated and rejected** — 49-66x slower indexing, 60-280x slower queries, 25-54x larger disk. See ADR-0016.

---

## 0.11 — ODM Completion (done)

Schema versioning (`_schema_version` tracking, `since_version` field semantics, `MigrationRegistry`, `pyrite schema migrate`). DocumentManager for write-path coordination. Architecture hardening (DDL validation, MCP constants extraction). Test infrastructure (pytest-xdist, extension tests). Docs KB fixes. 1505 tests.

---

## 0.12 — Distribution (done)

PyPI publish, version bump, MANIFEST.in, publish workflow, CHANGELOG, README.

Remaining loose ends (no version bump needed):
- PyPI trusted publisher setup (XS, manual step)
- [[mcp-submission-update]] (#89, XS)

---

## 0.13 — Human & Agent UX Hardening (done)

Web UI hardening (14 items: logout, version history, type colors, page titles, dead code, loading states, accessibility, mobile responsive, collections save, first-run experience, starred entries, full accessibility audit, Playwright tests, review hardening). Agent DX (8 items: PosixPath fix, batch-read, list-entries, kb_recent, search fields, smart field routing, structured errors, file placement fix).

---

## 0.14 — Auth & Rate Limiting (done)

GitHub OAuth login, per-KB read/write/admin permissions, MCP rate limiting.

---

## 0.15 — Deployment & Demo (done)

Docker/Compose, deploy buttons (Railway/Render/Fly.io), pyrite.wiki website, demo site deployment, BYOK AI gap analysis, final UI review.

---

## 0.16 — Ecosystem & Onboarding (done)

Getting Started tutorial, plugin writing tutorial, awesome plugins page, `pyrite ci` command, personal-kb-repo-backing, one-click deploy configs, alpha banner, OpenAI/Gemini MCP integration docs.

---

## 0.17 — Cleanup & Hardening (done)

Bug fixes (entry ID collisions, priority type mismatch, date field reads, status field reads). Reliability (API singletons, plugin hook atomicity, plugin discovery strict mode). Developer experience (MCP body truncation docs, embedding prewarm, import cycle detection, KB compaction, schema validation CLI, plugin registry dedup, factory open/closed, component documentation gaps).

---

## 0.18 — Architecture & Ecosystem (done)

KBService decomposition (extracted GraphService, EphemeralKBService, QuotaService, ExportService). Schema module decomposition. SearchService/KBService overlap resolution. Dynamic subdirectory paths. Reserved field validation. Software-kb plugin. Journalism-investigation plugin. Edge entities (typed relationships as first-class entities). Entry lifecycle and search filtering. DB backup/restore. Kanban workflow for agent teams. Export system (NotebookLM + Quartz renderers).

---

## 0.20.0 — First Public Release

**Theme:** Release readiness. Version bump, CHANGELOG, documentation accuracy, lint cleanup. No new features — polish and ship.

---

## 0.21–0.24 — Shipped between milestones (reconciliation note, 2026-07-02)

Versions 0.21.0 through 0.24.0 were tagged without roadmap or CHANGELOG
entries (CHANGELOG stops at 0.20.0). Highlights recoverable from git:
Tier-A agent-CLI features (`pyrite rename` + wikilink rewrite, `qa
coverage` curation stats), the CLI error-shape consistency sweep (0
ad-hoc sites CLI-wide), `task reset` for stale claims, FTS5 term quoting
in links suggest/discover (fixed the `links orphans` crash), and the
`all_kbs()` enumeration fix for DB-registered KBs. Backfill CHANGELOG as
part of 0.25.

---

## 0.24.1 — First GitHub release (shipped 2026-09-17)

Five months of hardening. Security fixes (token disclosure, git argument
injection, unguarded routes, stored XSS, path traversal through entry ids),
the error-handling contract, MCP tools that failed on every call, create no
longer overwrites, fast commit hooks with the suite at pre-push, CI as the
gate. Installable from the tag with `pip install "pyrite[all] @ git+…@v0.24.1"`
— CLI, REST and MCP work; the web UI is not packaged yet. Release notes in
CHANGELOG. Process decisions: ADR-0032 (branch flow), ADR-0033 (where work is
tracked: bugs and requests on GitHub, roadmap here).

---

## 0.24.3 — Operational (shipped 2026-09-20)

Planned and worked as 0.24.2; **cut as 0.24.3** because `v0.24.2` was already
taken by an accidental tag — it pointed at a mid-development commit whose own
`pyproject.toml` said 0.24.1, carried an empty GitHub release, and `main` never
moved to it. A tag is immutable by convention, so the number was spent; the
content is unchanged.

**Theme:** a release should be an unremarkable event, and the tool should be
boring for its operator. No new product surface. Definition of done: the
release is cut by one script from a CI-verified commit under ADR-0032's rules,
and every GitHub issue in the milestone is closed. **Met** — 58 issues closed,
the tag cut by `scripts/release.py` on its first real use, which caught six
defects across seven dry runs that neither the tests nor CI could see.

**How this list was chosen:** GitHub issues #9–#21 (the migrated bugs) plus the
2026-09-17 review's process findings. Under ADR-0033, "what is broken" lives in
GitHub — when working toward a release, check `gh issue list --milestone` and
`gh pr list` as well as this file.

### Workstream 1 — Release process, operationalized

- ~~[[make-the-task-claim-concurrency-test-xdist-safe-then-run-pre-push-with-n-auto]]~~
  **done 2026-09-17**: `-n auto` at pre-push and in CI; suite 3m37s → ~45 s
  locally once write-time embedding was switched off for tests (it was the
  whole cost); CI Python jobs 22 min → ~2.5 min tests + 20 s uv install.
- ~~CI change classifier~~ **done 2026-09-17**: `changes` job; test/frontend/
  coverage skip for docs/KB-only pushes; `kb` job validates KB changes in
  ~30 s; coverage in its own non-required job; e2e on `main` and by hand only.
  Auto-merge for KB PRs still to enable (repo setting, with protections).
- Branch protection on per ADR-0032: `dev` and `main` require the three Python
  jobs + `frontend`, up to date, no bypass; linear history; `v*` tags protected;
  Dependabot security updates and private vulnerability reporting enabled.
- `scripts/release.py` — one command: checks CHANGELOG `[Unreleased]` and
  version, waits for CI on the SHA, fast-forwards `main`, tags, creates the
  GitHub release from the changelog, installs from the tag into a clean venv
  and runs the Quick Start. (v0.24.1 took ten manual steps.)
- ~~[[live-server-integration-tests-for-multi-request-flows-plus-regression-tests-for-the-three-outside-prs]]~~
  **done 2026-09-18**: `tests/e2e/` starts `pyrite-server` on a free port
  against a temp data dir and drives it as a client — REST (create a KB, write
  into it, find it, no restart), MCP over SSE (the advertised endpoint path,
  then a full session), MCP over stdio (`pyrite mcp --tier read`), and startup
  prewarm read from `/health`. Each of PRs #3, #4 and #5, reintroduced by hand,
  fails its test while the existing 3316 do not notice.
- Post-tag workflow: install from the tag, run the Quick Start, build the Docker
  image (unverifiable locally on 2026-09-17).
- [[ci-parity-lint-extensions-and-enforce-the-fix-needs-a-test-rule]] (medium, S).
- [[single-source-of-truth-for-the-version-asserted-by-a-test]] — finish:
  `[Unreleased]` discipline asserted by the test.
- Session setup script (worktree + venv + hooks) so ADR-0032's one-branch-per-
  session rule is one command.
- **Every interface has an end-to-end test in CI** (added 2026-09-17):
  - ~~REST and MCP over SSE~~ **done 2026-09-18** — the live-server ticket
    above. `tests/e2e/test_rest_flow.py`, `tests/e2e/test_mcp_sse.py`.
  - ~~**MCP over stdio**~~ **done 2026-09-18** — `tests/e2e/test_mcp_stdio.py`
    spawns `pyrite mcp --tier read` from the installed package and speaks
    JSON-RPC on stdin/stdout: `initialize`, `tools/list`, `kb_search`. Its
    tool list is asserted equal to the SSE one, so the two transports cannot
    drift.
  - ~~**CLI** — [[ci-run-getting-started-tutorial]]~~ **done 2026-09-18**:
    `scripts/run_tutorial.sh` extracts the fenced bash blocks from
    `docs/getting-started.md` and runs them in order in one shell session in a
    temp HOME, against the installed package. It found two real bugs on its
    first run: #43 (semantic search returns 0 on a fresh tutorial KB — nothing
    embeds on write) and #44 (`index health` reports `subdirectory_mismatches`
    on every entry of a correct KB; a trailing slash is not stripped). Both
    are why its assertions are one notch looser than the ticket asked for;
    the code names the TODO.
  - **Web** — [[playwright-e2e-suite-non-deterministic-failures-likely-shared-state-auth-config-gap]]
    (high, M): make Playwright deterministic and blocking. Root cause still
    unconfirmed; if it proves large it slips to 0.25, and that is the only
    item on this list allowed to.

  All of the above run in the `smoke` CI job, gated on the push to `dev` and
  on manual dispatch — never on a pull request, and never in `gate`'s needs
  (ADR-0032 §3a's breadth row).

### Workstream 2 — Bugs (GitHub milestone `0.24.2`)

- **#15** `pyrite update` drops undeclared frontmatter keys — data loss, and it
  blocks tagging roadmap items with `milestone:`.
- **#14** writes accept off-enum field values.
- **#13** first write on a fresh install blocks on the model download.
- **#9** web: search results never render (the flagship flow on the demo).
- **#16, #17** entry-id slugs; **#18** `index health` exit code and `-k`;
  **#21** db backup path.
- ~~One security fix from the release review~~ **done 2026-09-18**: per-KB read
  scoping — private KBs (`default_role: none`) were readable by any logged-in
  or anonymous user on read routes; now 404 / filtered. Pilot prerequisite.

### Workstream 3 — Docs and README

- [[installable-from-github-with-a-working-web-ui-package-the-built-frontend]]
  (high, M) — `uv tool install "pyrite[server,cli] @ git+…@v0.24.2"` gives a
  working UI; README Quick Start leads with it.
- [[reposition-the-readme-opening-and-pyrite-wiki-around-agent-written-human-verified-knowledge]]
  (medium, M) — the README opening matches the thesis above.
- [[docs-counts-generated-or-asserted-from-code]] (medium, S).
- [[contributor-docs-pass-contributing-security-pr-template-credits]] (high, S).

### Pull-forward pool (added 2026-09-18)

The conductor loop finishes the list above; while it runs, well-specified
work that solves real user problems may be pulled into 0.24.2 so the process
gets feedback on more shapes of work. The maintainer's terms: "As long as
features are being developed and tested and our process is improving I do
not think it hurts to pull features and bugfixes and test improvements that
are well specified forward." The rule, which the meta-conductor may apply
without asking:

1. **Well-specified** — acceptance criteria a Sonnet worker could execute
   with no conversation (the `dispatch.md` test). The god-object splits are
   the deliberate exception: Opus, one object per PR, and the acceptance is
   "behaviour unchanged, proven by the existing suite plus a boundary test".
2. **Real** — closes a filed issue, removes a known data-loss or
   non-convergence bug, or is a test that would have caught one.
3. **Cheap to hold** — footprint disjoint from anything in flight; effort
   ≤ M, or L only for the splits below.
4. **Never displaces** — pulled only when fewer than three themes are in
   flight and every definition-of-done item above is claimed or done. The
   release ships when the definition of done is met; a pool item still open
   then rides on `dev` into the next release. Pulling forward must not
   lengthen 0.24.2.

| Theme | Closes | Shape / what it exercises |
|---|---|---|
| Index sync converges and health tells the truth | #6, #7, #8, #22, #19, #47 | Sonnet; a multi-issue theme with a storage cold read |
| Web first-visit fixes | #10, #11, #12 — [[web-kb-context-single-authority]], [[web-fix-dropped-kb-seams]], [[web-light-mode-chrome-repair]] | real UI change → explorer agent + Playwright fan-out; 0.25 pilot prerequisites |
| KB registry as one source of truth | [[collapse-kb-registry-to-one-source-of-truth]] | Opus, cross-cutting; root cause of the PR #4 bug class; first real cold-read test |
| Data-loss class, continued | #46 (`update --tags` rewrites frontmatter — found while tagging this pool), [[typed-entries-silently-drop-the-references-frontmatter-field]] | siblings of #15; the PR #35 write-path tests give them a home |
| Deploy path | #20 (Railway bind host/port) | small; pairs with the packaged web UI |
| God-object splits | [[split-the-remaining-god-objects-software-kb-plugin-kb-service-index-qa-service]], [[split-mcp-server-module]], [[split-entries-endpoint]] | Opus, one object per PR, sequenced; a different set of capabilities — large-diff review, behaviour-preserving refactor, the cold read at scale |
| MCP read-tier ergonomics (hallway test #56–#68, minus #56 which is milestone) | #57 #58 #59 #62 #64 #65 #66 #67 #68 | Sonnet mostly; the read tier is the agent surface, and the report's §2.12 says what not to touch (`kb/notes/hallway-test-read-tier-mcp-2026-09-18`) |
| MCP write-tier correctness (hallway test tier 2) | #95 `kb_bulk_create` not best-effort, #96 `add_type` overwrites silently, #97 `kb_link` accepts a missing target (the mechanism behind #64) | Sonnet; each has a reproduction and acceptance in the issue |
| CLI write-path integrity (CLI hallway test, `tests/usability/cli-hallway-report-2026-09-18.md`) | **#87** `link`/`update`/`create --link`/`links bulk-create` write frontmatter that no longer parses when the body has a `\|---\|` or `---` line, exit 0 — milestone; #48 cascade validator never called; #51 `parked_awaiting` lost from the index; #54 search output shape; #52 `task status` deprecation invisible | #87 is the same serialization fault as #46/#86 seen from the link step — one theme with #69's follow-up, Opus; the rest Sonnet. Report's "if only three things": the link step, `filters_dropped` on every search response, `--dry-run` on `link` and `update` |
| Quality pool for the retro (tagged `quality`) | [[tests-leak-open-pyritedb-connections-into-temporarydirectory-teardown]], [[worktree-no-lost-commits-invariant]], [[index-rebuild-from-files-equivalence-test]], [[regression-test-links-fts-quoting]], [[cli-output-is-inconsistent-when-stdout-is-not-a-tty]] | Sonnet; pre-groomed stock so the retro's quality theme has candidates before it has evidence of its own |

Left out on purpose: the JI UI features and the review surface (0.26 — the
thesis screen deserves its own release), anything `needs-design`.

### Not in 0.24.2

**The journalism-investigation plugin's tool bugs** — #61 (`correlate` groups
nothing), #92/#93 (edges written as index-only ghosts with metadata strings the
read tools never see), #94 (`investigation_create_*` fails on a runtime-
registered KB), #98 — are milestone **0.25** (maintainer, 2026-09-18): real,
reproduced, and a plugin's problem, not the core's; they need the registry
single-source-of-truth item first anyway.

The GitHub-issues importer (ADR-0033 consequence) — not a priority; the
pyrite-dev skill checks GitHub directly instead. New UI surface of any kind.
The remaining web-UX items (0.25). Packaging beyond the git install
([[choose-a-pypi-distribution-name-and-decide-the-fate-of-pyrite-mcp]] stays
open until a name is chosen).

---

## 0.25 — The community's first release (shipped 2026-09-23)

**Theme:** the project moves to its own organization, and the contribution
path is made good enough that the people already sending patches do not have
to work around it. No new product surface.

**Definition of done:** the repository is at `pyrite-wiki/pyrite`, a release
can be cut from there, and a contributor can land a PR without hitting the
potholes this milestone names.

**How this list was chosen.** 0.24.3 merged 86 PRs, 8 of them from five
outside contributors. Of the 13 PRs open when it was cut, **11 were theirs**.
The queue has already inverted, so the release that follows is chosen from
what contributors are actually doing plus what is in their way — not from a
scope freeze written before any of them arrived. Under ADR-0033 the bugs live
in GitHub: check `gh issue list --milestone 0.25` alongside this file.

**That premise held.** On 2026-09-21, the first full day in the new org,
**21 PRs merged and 13 of them were from outside contributors** — one of whom
opened 16. Workstream 4 is entirely theirs. The release is what the community
fixed, which is what this milestone was named for.

**The Shared-Instance Pilot moves out**, unscheduled. Its definition of done
— one or two invited peers reading the corpus read-only for two weeks with
zero operator interventions — is unchanged and still wanted, but it is a
product goal and this release is about the project's own machinery. Its item
set ([[epic-shared-instance-readiness]]; the web first-visit fixes
[[web-kb-context-single-authority]], [[web-fix-dropped-kb-seams]],
[[web-sidebar-ia-regroup]], [[web-graph-default-scope-and-guards]],
[[web-light-mode-chrome-repair]]; [[oauth-state-store-persistence]]; the
hosting-security static audit; the invite doc) is held intact for whichever
release takes it up.

### Workstream 1 — Stop the contribution path from costing people evenings

**Changelog fragments (#243) landed here after all** *(shipped: #276,
2026-09-21)*. They were scheduled for 0.26 on the argument that two unproven
things in one window — a new organization and a new release path — is how a
release breaks. The org move landed first and held, and the file went on
conflicting: six times in a week, three of them on first-time contributors'
branches. So the cost of waiting exceeded the risk of moving, and the
migration was done with a dry run and every open contributor PR converted by
hand. `CHANGELOG.md` has appeared in 2 PRs since, against six conflicts in
the week before.

- **#235** no agent-facing documentation of the write path
- **#244** `.claude/` ships 3,897 lines of agent instructions with three
  audiences and no separation; `CONTRIBUTING.md:276` sends contributors to a
  skill whose own description tells them PRs are someone else's job
- **#248** release notes credit only merged-PR authors, so a contributor whose
  patch lands via someone else's PR is invisible to the script
  *(shipped: co-author trailers are read too)*

### Workstream 2 — The move to `pyrite-wiki`

The org is the enabler: PR queues, contributor permissions, and a place for
extensions to live — including ones that migrate out of core, and ones the
community writes.

**Both shipped, in the required order.**

- **#259** `scripts/release.py` hardcodes `markramm/pyrite` and step (a)
  *refuses to release* when `origin` disagrees *(shipped 2026-09-20, before
  the transfer, exactly as this item required)*
- **#182** the transfer itself, and the checklist of what has to survive it
  (rulesets, secrets, CodeQL, Discussions, labels, milestones, open PRs)
  *(shipped 2026-09-21)*

What the move actually bought, measured on the first full day in the new org:
the **merge queue** (#273/#274), which is the thing the org was the enabler
for. 7 PRs merged in the six hours before it took its first PR; 14 in the
thirteen hours after, including a six-PR batch that landed in 36 minutes. Two
`merge_group` runs started 22 seconds apart and both passed — the overlap that
a serialized "up to date" gate makes impossible.

### Workstream 3 — The loop stops poisoning its own evidence

Four failure modes where something reasonable returns a wrong answer silently
— the same family as the product bugs in Workstream 4, applied to the
development process.

- **#209** a long-lived weekly branch resurrects backlog items already merged
  to `done/` *(outlived: #275 moved the tick log to the gitignored `desk/`,
  so no weekly `kb/` branch is created any more and the mechanism cannot
  recur; two abandoned branches remain to delete)*
- **#210** the conductor's main checkout goes stale, so every verification
  grep reads pre-merge code and reports a landed fix as not landed
  *(shipped: the pre-push hook refuses a worktree with no `.venv` instead of
  falling back to the main checkout's interpreter, and the health step
  fast-forwards before reading anything)*
- **#236** a venv per worktree costs 1 GB, and disk pressure surfaces as
  `3 failed, 1088 errors` on branches that passed minutes earlier — the
  machine budget counts suite slots and memory, and nothing counts disk
  *(shipped, and the premise was wrong: `du` reports ~1 GB where removing a
  worktree venv frees ~112 MB, because APFS clones blocks. The real cost is
  worktree **count**, so the health step now reaps merged worktrees and treats
  free disk as a dispatch budget.)*
- **#242** a worktree's `.venv` can vanish mid-session; three agents lost
  theirs in one night and all three noticed by luck *(shipped with #210 — a
  vanished venv now stops the push instead of silently testing another tree)*

### Workstream 4 — The answer is wrong and nothing errors

Every one of these was found by someone using the tool, and every one of them
already has a contributor's PR against it. This is the workstream the
community chose.

**All seven shipped** — the whole workstream, by outside contributors.

- **#19** `kb remove` permanently refuses a KB whose `config.yaml` is gone
  *(shipped)*
- **#44** `index health` false-positives on a declared subdirectory's trailing
  slash — a KB built exactly as the getting-started guide instructs reports
  `warning` on every entry *(shipped: #255)*
- **#149** `GenericEntry` duplicates undeclared frontmatter keys into a
  `metadata:` block *(shipped: #175)*
- **#151** `created_at` / `updated_at` are read from frontmatter and never
  written back, so an explicit key is dropped on the next save
  *(shipped: #171, #173)*
- **#196** Pyrite never configures logging, so `logger.warning` falls through
  to the root handler *(shipped: #225)*
- **#197** `pyrite create -t note` silently creates an ADR *(shipped: #252)*
- **#231** `pyrite update -f tags=a,b` writes a bare string the reader then
  iterates character by character, silently dropping the entry out of every
  tag-keyed view *(shipped: #254)*

Not in the original list, found and fixed in the same window: read-scoping
holes on the link-discovery routes (#186) and in two MCP extension tools
(#223, two of six done), a Postgres FTS trigger that silently gave a second
instance no keyword search at all (#282), `?fields=` projections that differed
across REST, MCP and the CLI (#193), network tools that returned every
neighbour at once (#63), and block-sequence indentation lost on a round trip
(#148).

---

## The working plan (approved 2026-10-03, maintainer)

**How the work runs.** One core theme (write path, index, identity) in flight
at a time; small independent fixes run alongside. Every theme is groomed
against [[design]] (principle at stake, the user's model and the
implementation model, its cluster) before code; a spike wherever scope or
approach is uncertain; the Andon cord stops dispatch when two themes fail the
same way; a cold read for the gate, storage, the write path and security; a
retro after about five landed themes, counting churn first.

**Maintainer decisions that gate work:** accept ADR-0041 (the 0.25.7
message); accept ADR-0038 with its identity amendment (reconcile, identity);
ADR-0042 questions 1 and 2 (write path phase 2); the principle 4 sentence on
reads and derived values.

**Track A: the first hour and agent friction.**
- A1, 0.25.7 (Oct 8): #582/#647, the contributor PRs #649 and #652 (LF line
  endings; add `.gitattributes`), the 20-minute tutorial #662 as the release's
  acceptance test, #583 with a known-issues page, #595, #664. Cut line: #232,
  #43.
- A2, 0.25.8, under the CLI contract (#303): `--fields` in every format and a
  compact default (#667), `sw adrs` sorted with supersession (#666), `orient`
  recent from content dates (#670); a spike first for search scores and "no
  strong match" (#668).
- A3: contributor setup (#665, groomed and decomposed), Pyrite's own KB healthy
  on `dev` with a CI check (#669), #672, #671, #673 (reproduce first), a spike
  on making the skills findable (#674), #365.
- A4: rerun the contributor hallway test on three models, repository attached,
  after A1 and A3.

**Track B: refactoring toward 0.26, "your files, exactly".** B1 #661
follow-ups; B2 #659 cleanup, one PR per area (spike first on which migration
path is live); B3 the divergence register as expected-failure conformance tests
(ADR-0042 phase 1); B4 `ids missing` and `ids pin`, one release before the
identity switch (0.25.9); B5 the index reconcile (ADR-0038 steps 2 to 5); B6 the
write path in phases (spike first on concurrency, the per-file lock and `/` in
ids); B7 derived task completion, then the rollup hook goes; B8 the ADR-0045
protocol inventory spike, then aliases as schema data (before B6 lands).

**Track C: releases.** 0.25.7: A1, ADR-0041, the tutorial, the alpha
announcement. 0.25.8: A2, A3, B1, first B2 removals. 0.25.9: passage
embedding, B4, B3. 0.26: B5, B6, B7. 0.27 and 0.28: the authorization design,
then B8 and the alpha contracts.

**For contributors:** #664, #671, #666, #670, #672, groomed for strangers and
left unclaimed a few days.

## The release line (redesigned 2026-10-02, maintainer)

**Design.** A KB is Markdown files with YAML frontmatter on disk, usable by any
tool. Pyrite is additive to someone maintaining those files by hand (ADR-0041,
accepted 2026-10-03). A write does what was asked, loses nothing, and reports it.

**Who it is for now.** Local use by one operator, usually through a terminal
coding agent. Multi-user stays experimental until its design is right; no new
bespoke authorization objects are built before then.

**Contracts are alpha.** The plugin and API contracts are our attempt to clean
up the design around what matters most to users. They will be revised with
community needs and feedback.

| Release | Promise | Leaves |
|---|---|---|
| 0.25.7 | The first hour works; the alpha announcement | 2026-10-08 |
| 0.25.8 | The CLI contract for agents | 2026-10-15 |
| 0.25.9 | Search reads the whole entry; links work across KBs | 2026-10-22 |
| 0.26 | Your files, exactly | undated |
| 0.27 | One operator, many users | undated |
| 0.28 | Alpha plugin and API contracts | undated |

Dated releases are trains: what is done by the date ships. The GitHub
milestones carry the issues; this table carries the promise and the gate.

- **0.25.7.** `mcp-setup` does what was asked (#582), first-hour docs (#583),
  declared types (#595, #607), orient (#232), first search (#43), a save that
  deletes a body line (#636), and the journalism-investigation tools marked
  experimental and unsupported (#644). *Gate:* ADR-0041 accepted for entry
  files. *Slips first:* #232, #43.
- **0.25.8.** Formats (#547, #553, #303), exit codes (#526), ambiguity (#528),
  error spelling (#610), create (#86), the echo rule against the file (#569),
  tiers in tool descriptions (#68), `.claude/` by audience (#244). Left open
  for contributors: #54, #52, #59, #572, #200.
- **0.25.9.** Whole-entry passage embedding
  ([[embed-the-whole-entry-in-passages-by-default]]), #594, #194, #627; links
  and cross-KB reads (#62, #64, #124, #621, #622, #623, #468). Embedding lands
  before the index reconcile (maintainer, 2026-10-02).
- **0.26: your files, exactly.** First the index tells the truth: the ADR-0038
  reconcile (#6, #7, #484 to #487, #495, #51, #22), identity (#639, #494, #488
  to #490), health (#593). Then a write changes only what was asked (ADR-0042,
  accepted 2026-10-03; its rule is re-spiked on the real path first): #637, #638, #640,
  #178, #628, a stale-read rule, and explicit format and migrate commands.
  *Gate:* ADR-0038 and the ADR-0042 decisions.
- **0.27: one operator, many users.** Design first: the authorization model
  and its executable matrix (#587), the security review (#586), the registry
  in config under the operator (#539, #521, #565, #602, #382), one landing rule
  for writes, the `published` flag. Two planes: users read and write entries
  per KB by grant; everything else belongs to the operator. Capabilities
  granted by the operator and configured in a user-owned file are the
  direction, not this release.
- **0.28: alpha plugin and API contracts.** Plugins out of tree with
  journalism-investigation as the pilot, where its tools are fixed (#92, #93,
  #94, #98); identity injected by the dispatcher (#384); #385, #412, #456,
  #626.

The sections below for 0.25.7 to 0.25.9, 0.26 and 0.27 predate this line.
Where they differ, this section wins; their detail is kept for the reasoning.

---

## 0.25.7 to 0.25.9 — Agents in the terminal (next; weekly patch releases)

**Decided 2026-10-01 (maintainer).** The work a new user tests first comes before the
multi-user review: Claude Code and terminal integration, and MCP on a local install.
Three weekly patch releases, each a train: what is done by the date ships, the rest rolls.
This moves "Meet agents where they are" ahead of 0.26; it was listed under 0.27.

The backlog is sorted into three buckets: (1) terminal, Claude Code and local MCP;
(2) multi-user security and multi-user bugs (0.26); (3) everything else (0.27).

### 0.25.7 — The first hour works (target 2026-10-08)

**Done when:** someone follows the README and getting-started on a clean machine,
connects Claude Code or Claude Desktop with `pyrite mcp-setup`, and finishes the
tutorial without meeting a false statement. The release layer checks it: the tutorial
run, plus an `mcp-setup` read-back.

- `mcp-setup` writes the config each client reads, write tier by default (#582).
- First-hour docs say what happens; `KNOWN-ISSUES.md` is honest (#583).
- Quiet default output; semantic search says when its extra is missing (#584, #43).
- Local Docker is local (private tracker).
- The contributor door: #541 (#538), #548 (#244), root clutter (#585).
- The agent's first call: `kb_orient` (#66, #232); #574.
- Release, demo redeploy, pyrite.wiki.

The alpha announcement goes on this release. It claims the single-user agent workflow
and does not claim multi-user.

### 0.25.8 — The CLI contract for agents (target 2026-10-15)

**Done when:** one contract test runs across commands: JSON is valid in a pipe, a
requested effect that did not happen exits non-zero, an ambiguous id is an error.

- Exit codes and ambiguity (#526, #528); one format contract (#547, #553, #303).
- Search output for agents (#54; lean by default, decided 2026-09-30).
- Creates and schema (#86, #572, #468, #569, #51, #8).
- MCP answers (#64, #68, #62, #124, #200); #52; #527.
- The journalism-investigation MCP tools write through the pipeline (#92, #93, #94,
  #98): fixed here, not marked experimental (maintainer, 2026-10-01). #94 needs the
  `PluginContext` half of #384.
- Needs design first: #303, #200, #22, #59.

### 0.25.9 — The index tells the truth (target 2026-10-22 to 10-29)

**Done when:** after any change made outside Pyrite (an edit that keeps its mtime, a
time-preserving copy, a `git mv`, a delete, a duplicate id), one command makes the
index match the files and reports duplicates, pinned by invariant tests.

- The single reconcile of ADR-0038: #6, #7, #484, #485, #486, #487, #495.
- Storage-touching: every theme gets a cold read. It may need both weeks.
- Embed the whole entry in passages by default (added 2026-10-02, maintainer): backlog item
  `embed-the-whole-entry-in-passages-by-default`, on the evidence of the embedding spike.
  It changes the index, so it needs a groom and probably an ADR first.

## 0.26 — Multi-user you can trust (after 0.25.9; still alpha)

**Sequencing (2026-10-01):** grooming for the review starts in parallel with the 0.25.x
releases, at a low rate; the build starts after 0.25.9. Target November, no fixed date.
Tracking issues: #586 (the review), #587 (G1). The public multi-user bugs are in the
`0.26` milestone; 40 items are open in the private tracker.

**Theme:** the multi-user path gets the security review and the manual testing
it has never had. Decided 2026-09-23, after one day turned up three separate
holes:
- with auth enabled and no API keys configured, **any** key was answered
  "admin" on REST and `/mcp` (v0.21.0–v0.25.0, #331);
- `kb_delete` could remove `.md` files outside the KB;
- `/ws` broadcast private-KB names to every socket (#218).

Each was found by accident, one at a time. The automated tests had not caught
them.

**Definition of done:**
- The multi-user security review
  ([[multi-user-security-review-threat-model-surface-audits-live-personas-and-a-structural-guard]])
  is complete: a threat model, per-surface audits, and live testing with three
  personas.
- Every exploitable finding is fixed, with a regression test that fails
  without the fix.
- A **structural authorization guard** is on `dev`. It enumerates every REST
  route and MCP tool and fails when one lacks a covering read **and** write
  authorization test.
  **G1 is in 0.26** (maintainer, 2026-09-26). It compares every entry point's
  answer, for each kind of principal, with what the policy says, and it
  follows directly after security batch 3b.
- The README states the multi-user status honestly: alpha, the review date,
  and the known gaps.
- Every finding in the private security tracker, and every backlog item
  tagged `security`, has a disposition: fixed, scheduled, or accepted with a
  reason. No public `security` label is created (maintainer, 2026-09-26).
- Findings are published only after their fixes ship. Before then, only
  counts and structural gaps are public.

The review starts once the in-flight security fixes have landed, so it audits
current code rather than a moving target.

### Workstream 1 — Security (the theme)

- In flight on 2026-09-23:
  - the key-role bypass (#331);
  - `find_file` / `kb_delete` containment;
  - `/ws` authentication and scoping (#218, #323);
  - export path segments (#221, #324).
- **#330** admin user management always returns 403 (fails closed).
- **#223** the remaining extension MCP tools that span every KB.
- **The write-path audit.** Reads were made structural in 0.25; writes still
  rely on per-endpoint checks. This is the review's largest surface.
- **The 56 open CodeQL alerts:** triage them in 0.26 (audit A8). **#228**
  (CodeQL advanced setup, so MCP tool arguments count as untrusted) moves to
  0.27; until then audit A2 reviews those arguments by hand (maintainer,
  2026-09-26).
- Recorded by the 2026-09-23 cold reads, as properties for the review: every
  path built from request or KB input stays inside its root and survives
  unusual names (NUL, overlong, drive-relative, symlinks); a message to an
  MCP session acts only for the principal that opened it; each credential
  scheme gets the same answer on every surface. Details are tracked
  privately.

### Workstream S — Structure first (maintainer decision, 2026-09-25)

Most 0.26 bugs are one fix made four times. REST, MCP, CLI and the UI each
reach past the service layer, so a parity fix has to find every copy:
- the update semantics (#407), dry-run (#427), refusal reasons (#429) and the
  search contract (#431);
- 59 read-authorization checks in 16 files, with no single policy point;
- 124 hand-raised `HTTPException`s in the endpoints.

Themes on that surface took 150 to 500 worker turns. So this workstream
dispatches **ahead of everything below except work already in flight**:
1. **#380 (T6): the surfaces stop reaching past services**, with a boundary
   test that only ratchets down. It runs once #445 and #447 land, because
   they share its files. It uses Opus, because the ratchet is the design.
2. **ADR-0037: one authorization policy point and one error contract.** Every REST route, MCP tool and CLI write asks one service
   whether the caller may act, and every refusal leaves through one mapping.
   This is the mechanism behind the definition of done's structural
   authorization guard. Security fixes after batch 3 are built against it,
   not route by route.

   **Themes 0–3a are in 0.26** (maintainer, 2026-09-25). Their order:
   - 0: the characterization harness;
   - then 1 (policy extraction, #383, Opus) and 2 (the error contract),
     which can run in parallel;
   - then 3a: REST reads go through the policy, and the guard lands.

   Themes 3b–5 are 0.27. The error codes follow REST's; MCP keeps a
   `legacy_error_code` for one release.

**Check:** after #380, a parity fix touches one service plus thin adapters.
If one still takes more than about 150 worker turns, the diagnosis was
wrong; say so at the next retro.

### Workstream 0 — Write paths the investigation conductor depends on (maintainer priority, 2026-09-25)

Every write says what it did and stores what it was given, or refuses. These
block the maintainer's investigation conductor, so they dispatch ahead of the
rest of 0.26, after Workstream S.

- **CLI write path honours the schema** (#407, #397, #396, one PR): `update -f`
  stores undeclared keys where `create` puts them; `task create --field` so
  desk-schema tasks can be created in one step; `link` records a second relation
  and says "Already linked" when it wrote nothing.
- **Extension writes through the pipeline** (#391): social post and zettel new
  now; `sw new-adr` after the ADR-filename decision.
- **Web New-entry offers declared types** (#392): `allow_undeclared` becomes an
  explicit choice, not the web's default.

### Workstream 2 — Multi-user correctness the review will touch

- **#326 / #322** WebSocket events are never delivered: one root cause, no
  event loop on the worker thread.
- **#217** MCP `resources/read` is broken on every transport.
- **#234** MCP tool tiers are reported inconsistently.
- **#207** concurrent first searches each load an embedding model (#325, in
  review).

### Workstream 3 — The contribution path

- **Outside PRs are screened before any of their code runs, and reviewed after
  CI**
  ([[outside-prs-screen-before-anything-runs-review-after-ci-one-daily-routine]]).
  One daily routine. It also closes the gap where the review lane ran
  untrusted suites next to the maintainer's credentials.
- **#278** a missing changelog fragment fails the check instead of warning.
- **#320 / #321** the `--help` colour trap and `--json` wrapping at 80
  columns. Each cost first-time contributors a red gate.
- **#244** separate `.claude/` by audience; **#235** agent-facing write-path
  docs. Both carried from 0.25.

### Workstream 4 — Keep the door open

At least five `good first issue` items open at all times (eleven on
2026-09-23), each written to the standard: the error verbatim, steps,
acceptance criteria, files. Security-adjacent work never gets the label.

**Out of 0.26:**
- the review surface (a multi-release epic, kept below);
- PyPI and packaging;
- the web graph and sidebar work;
- the KB registry collapse.

These move to "Later" unless a contributor picks one up. The earlier 0.26 draft
("contribution machinery, from the new org") was overtaken: 0.25.0 was already
cut from `pyrite-wiki`. Its candidates are carried above (#244, #235, #223) or
into Later (the #209 validator message, the Postgres test-isolation sibling of
#282).

**The review surface stays its own epic.** "What did agents do since I last
looked" as the home screen
([[web-the-review-surface-what-did-agents-do-since-i-last-looked-as-the-home-screen]])
keeps its definition of done and is scheduled once it is broken down. A
trusted multi-user path is a prerequisite for showing it to anyone but the
maintainer.

---

## 0.27 — Direction (maintainer decisions, 2026-09-26; not yet groomed)

- **ADR-0037 themes 3b–5:** writes and MCP go through the one policy point.
- **CodeQL advanced setup (#228).**
- **A separate `published` flag.** Being on the anonymous `/site` becomes its
  own switch, apart from `default_role: read` for registered users.
- **One KB registry** (ADR-0039, accepted 2026-10-03 after a spike). A single source of
  truth for KB membership and per-KB policy; it removes the class behind
  private #61/#69 and the characterization-harness leaks.
- **Plugins out of tree.** The plugin contract becomes the public API.
  Journalism-investigation is the pilot, and software-kb stays in tree. An
  import-inventory spike and a proposed ADR come first.
- **Meet agents where they are** *(pulled forward to 0.25.7–0.25.9, 2026-10-01; what is
  left of it stays here).* The CLI is the core of the terminal agent
  interface: pi and Claude Code use `pyrite` commands with pipes and `--json`
  as shared memory. MCP serves Claude Desktop and Cowork agents; the web UI
  serves people. The output and error contract and the index freshness the
  CLI hallway test asked for live in the service layer, so every surface
  gets them.

## Later, unscheduled

PyPI and packaging; web graph scoping and sidebar regrouping; collapse the KB registry to one source of truth; the #209 validator message (name the stale side); Postgres test isolation beyond the conformance suite (#282's sibling).

Type-aware views via a declarative manifest (ADR-0031 open question 4);
agent run control (ADR-0030 Phase 1, Claude adapter first); the GitHub-issues
importer; write access for peers (needs tool-enforced provenance +
[[per-user-fork-directories]]); extension registry; PyPI once a name exists.

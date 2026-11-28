---
id: serial-queue-2026-09-18
title: "Serial queue — the not-started 0.24.2 work re-split for one task at a time (2026-09-18)"
type: note
tags: [process, conductor, groom, architect, 0.24.2]
created: "2026-09-18"
lifecycle: archived
---

The `pyrite-architect`'s re-split after #168 (the conductor loop ran the 16 GB machine out of memory, 2026-09-18 ~11:10Z). The maintainer's direction: **one Pyrite task at a time** — one worker *or* one review on the machine, never both — and "split the remaining pyrite work up again". Every theme below is sized for one worker pass, one suite run to verify, one review: ≤ ~300 changed lines, ≤ ~6 files, one reason to change. Baseline `origin/dev` 28fc380. Supersedes the dispatch order (not the analysis) in [[groom-2026-09-18-tick6]].

**The groom for each theme is in its ticket**, under `## Groom 2026-09-18 (serial)` — acceptance, regimes, touches, sequence, model, heavy, cold read, out of scope, size. This note is only the order. Where the ticket is a backlog item the id is given; where it is a GitHub issue the groom is a comment on the issue.

**Ahead of the queue — five finished themes awaiting review, one slot each** (not re-groomed). Suggested review order, by what each unblocks: #163 CI parity (unblocks 1 and 7) · #145 search filters (closes milestone #56; unblocks 3, 25, 26) · #140 `scripts/release.py` (the definition of done's first half; unblocks 4, 15, 18) · #161 repo error disclosure (unblocks 27) · #160 Playwright F (unblocks 9, 31).

`heavy` = needs the machine's one slot for something bigger than one suite run (browsers, an npm build, a live server with an empty model cache). Size S ≤ ~150 lines, M ≤ ~300.

> **Update 2026-09-18 evening:** ADR-0034 was accepted by the maintainer — the five `adr-0034-*` entries below are no longer blocked on it (their sequencing waits stand). #169 merged, so #134 waits only on the contributor window.

## The queue (strictly serial, dispatch order)

**Protects the machine and users**

1. `machine-wide-suite-lock-one-full-suite-at-a-time-xdist-workers-bounded-by-memory` (#168) — `scripts/suite.sh`: a kernel-released machine-wide lock, xdist workers from memory not cores, the pre-push hook behind it — opus — M ~250 — heavy: no — cold read — waits on #163 merged (`tests/test_dev_process_config.py`, `.pre-commit-config.yaml`).
2. `every-heavy-runner-takes-the-suite-lock-playwright-the-skills-and-the-conductor-health-step` (#168) — `scripts/e2e.sh` under the same lock, local Playwright workers bounded, every skill/agent prompt says the wrapper, the conductor's load/memory refusal is a command — sonnet — S ~150 — heavy: one single-spec Playwright run — waits on 1.
3. `codeql-triage-…` — tracked privately.

**What the 0.24.2 definition of done requires** (the release cut by one script; every milestone issue closed — #56 via #145, #9, #13) **and what the roadmap's 0.24.2 workstreams list**

4. `changelog-fragments-one-file-per-pr-under-changelog-d-assembled-by-the-release` — `changelog.d/` fragments assembled by `scripts/release.py` — sonnet — M ~300 plus the migrated bullets — heavy: no — waits on #140 merged (`scripts/release.py`), on 1 (`tests/test_dev_process_config.py`), **and on all five review-queue PRs having merged** (each adds an `[Unreleased]` bullet the migration must carry; outside PRs #166 #169 #171 convert theirs on rebase). Groomed by retro 5 on `dev` (4560c2d) with regimes; only the size and that last dependency are new, and they are recorded here because the item does not exist on this branch.
5. #9 **spike** — does "N results, skeleton forever" reproduce on `dev`; the two 401 URLs, served vs built bundle hash — explorer-shaped `pyrite-spike` — 45 min — heavy: yes (one server + one browser) — independent.
6. #13 + #43 **spike** — when no embedding worker is attached, does a write attach one (who owns its lifecycle, #102) or skip and record the debt; what does a fresh config say about `auto_embed` — `pyrite-spike`, opus — 45 min — heavy: no — independent.
7. #159 — the fix-needs-a-test rule recognises `web/**/*.test.ts` / `*.spec.ts` (not covered by #163 — checked) — sonnet — S ~60 — heavy: no — waits on #163 merged.
8. #49 — a route's page title survives the layout — sonnet — S ~80 — heavy: no (vitest only) — waits on 7 (so its `fix:` commit can carry a frontend test).
9. `playwright-package-g-qa-and-search-specs-against-the-seeded-world` — sonnet — S ~150 — **heavy: 5 consecutive runs of two spec files + 1 full-suite run, one slot each** — waits on 8 (both specs open with a `toHaveTitle` #49 breaks) and #160 merged (the shared ticket file).
10. #9 **fix or close** — placeholder until 5 returns; if "does not reproduce", closed on the strength of 9's rendered-result assertion — model and size from the spike — waits on 5 and 9 (`web/src/routes/search/+page.svelte`).
11. #13 **server half** — placeholder until 6 returns — opus — M — heavy: yes (live server, empty HF cache, network blocked) — cold read — waits on 6 and #145 merged (`embedding_service.py`).
12. #43 **fresh-KB half** — placeholder until 6 returns — sonnet or opus per the spike — S — waits on 11 (`kb_service.py`, `config.py`).
13. #153 — the first Playwright run after a cold start is as green as the fifth (every CI run is a cold start) — sonnet — S ~80 — **heavy: 5 cold-start runs of one spec file + 1 full-suite run** — waits on 9.
14. `playwright-package-h-the-e2e-job-blocks-on-main` — closes the Playwright ticket — sonnet — S ~60 — evidence: 3 local full-suite runs (heavy) + 3 `workflow_dispatch` runs — cold read — waits on 9, 13, #160, #163, 1, 4 (`ci.yml`, `tests/test_dev_process_config.py`).
15. `post-tag-workflow-install-from-the-tag-run-the-quick-start-build-the-docker-image` — sonnet — S ~150 — heavy: no (runs on GitHub) — waits on #140 merged and 14 (same test file).
16. `packaged-web-ui-1-the-server-finds-a-packaged-ui-and-warns-when-there-is-none` — sonnet — S ~120 — heavy: no — cold read — waits on 11 if it touched `server/api.py`, otherwise independent.
17. `packaged-web-ui-2-the-built-frontend-ships-inside-the-package` — opus — M ~200 + the generated build — **heavy: one `npm ci && npm run build`, one wheel build + clean-venv install** — cold read — waits on 16.
18. `packaged-web-ui-3-quick-start-leads-with-the-one-liner-and-the-release-checks-the-ui` — closes the packaged-UI parent — sonnet — S–M ~200 — heavy: no — cold read (`scripts/release.py`) — waits on 17, #140, 4.
19. `docs-counts-generated-or-asserted-from-code` + the remainder of `single-source-of-truth-for-the-version-asserted-by-a-test` — sonnet — M ~300 — heavy: no — waits on 4 (`[Unreleased]`) and 18 (`README.md`).
20. `reposition-the-readme-opening-…` — a draft PR for the maintainer to rewrite, not an auto-merge — opus — S ~80 — heavy: no — waits on 19 (`README.md`) and on the maintainer wanting a draft (below).

**The rest — pull-forward pool and today's follow-ups** (the roadmap's rule: never lengthens 0.24.2; still open at the release, it rides on `dev`)

21. #6 — index sync converges when two files share an id; `duplicate_ids` in health — sonnet — M ~250 — heavy: no — cold read — independent (first of four on `pyrite/storage/index.py`).
22. #7 — sync compares the stored content hash, so sync and health agree — sonnet — S ~150 — heavy: no — cold read — waits on 21 (same function).
23. #8 — `missing_files` means absent from disk; unparseable files reported once — sonnet — S ~120 — heavy: no — cold read — waits on 22.
24. #22 — `kb.yaml` `exclude:` globs honoured by build/sync/health (a new public config key) — opus — M ~280 — heavy: no — cold read — waits on 23, and on outside PR #164 landing or closing (`repository.py`).
25. #62 — `kb_timeline` gains `kb_names`, `status`, `exclude_status`, pushed into SQL — opus — M ~250 — heavy: no — cold read — waits on #145 merged (`mcp_server.py`, `tool_schemas.py`, the backends).
26. #66 — `kb_orient` as a first call: optional `kb_name`, `KB_NOT_FOUND` + suggestion, `detail: brief|full` — sonnet — S–M ~200 — heavy: no — cold read — waits on 25 (same two files).
27. `codeql-triage-…` Theme C1 — `github_auth.py` token injection decides on host equality, not `"github.com" in url` — opus — S ~80 — heavy: no — cold read — waits on 3 and #161 merged.
28. `codeql-triage-…` C2 — the 43 dismissals: the conductor's API calls from the item's table, no worker, no slot — waits on 27 and the next CodeQL run on `dev`.
29. #150 — six corrupted `kb/backlog` files cleaned; the round-trip gate's xfail list shrinks by six — sonnet — S — heavy: no — waits on 14 (the Playwright ticket is one of the six and F, G, H all edit it).
30. #149 — `GenericEntry` stops writing undeclared keys twice — sonnet — S ~120 — heavy: no — cold read — independent if it stays in `models/generic.py`; otherwise waits on #164/#171 (`models/base.py`).
31. #162 — the e2e world gets links so the graph specs can assert a populated graph — sonnet — S ~120 — **heavy: 5 runs of two spec files + 1 full-suite run** — waits on #160, 13 (`global-setup.ts`) and 14 (do not change the world while H collects evidence).

**Blocked — in the order they would run once unblocked**

32. #44 — `subdirectory_mismatches` trailing slash — sonnet — S ~60 — waits on the outside-contributor window and on 23 (`check_health`).
33. #47 — `index health` names entries whose on-disk values fail the schema; an update's error says how to repair — sonnet — S–M ~200 — cold read — waits on the window and on 32.
34. #134 — REST `/entries/batch` parity with the MCP fix — sonnet — S ~150 — cold read — waits on the window and on outside PR #169 (its `_project_fields` identity-pair rule is what REST must mirror).
35. `adr-0034-i-mcp-body-ceiling-and-batch-response-budget-configured-by-environment` — opus — S–M ~250 — cold read — waits on ADR-0034 accepted, and on #166 (#58), #169 and #145 landed or abandoned (`mcp_server.py`).
36. `adr-0034-ii-write-paths-refuse-a-body-marked-body-truncated` — opus — S ~200 — cold read — waits on 35 and #166 (#95 edits `_kb_bulk_create`).
37. `adr-0034-iii-cli-body-limit-body-offset-and-pyrite-body-limit` — sonnet — S–M ~250 — cold read — waits on 35 (shared `body_bounds.py`).
38. `adr-0034-iv-rest-body-limit-and-body-offset-on-entry-reads` — sonnet — S ~180 — cold read — waits on 35, 36 and 34 (`endpoints/entries.py`, `schemas.py`).
39. `adr-0034-v-contracts-doc-and-tool-descriptions-for-bounded-reads` — sonnet — S ~190 — waits on 35–38 and 19 (`tests/test_docs_facts.py`).
40. `codeql-triage-…` Theme D — `pyrite key new` + API-key entropy docs — sonnet — S ~150 — cold read — waits on the maintainer's hashing decision.

## Blocked on the maintainer

- **ADR-0034 (PR #170) — accept, amend or reject**, including whether 8,000 / 20,000 / 40,000 are the shipped defaults and whether the CLI default stays unbounded; themes 35–39 do not move until then.
- **`py/weak-sensitive-data-hashing`** — is sha256 of a high-entropy random API key acceptable (dismiss the alert, dispatch Theme D) or does Pyrite move to a keyed hash (a breaking `config.yaml` change with its own ADR)?
- **README opening (20)** — does the maintainer want a worker's draft to rewrite, or to write the paragraph himself?
- **Package H's "ten consecutive greens on `main`"** — `main` moves only at releases, so do `workflow_dispatch` runs against `main` count toward the ten, or does the `dev` trigger wait ten releases?
- **Raising the WIP limit** — after 1 and 2 have landed and run for a while: to what, and on what evidence (the peak-RSS-per-worker number theme 1 records)?
- **#59 (`entry_type` drift, 187 values)** — controlled vocabulary or free text, and if controlled, normalise the data or alias on read? (Carried from tick 6; blocks nothing in this queue.)
- **Housekeeping, no decision needed:** `contributor-docs-pass-…`'s one open criterion was "enable GitHub private vulnerability reporting"; `gh api repos/markramm/pyrite/private-vulnerability-reporting` now returns `{"enabled": true}` — the item can be closed. "Auto-merge for KB PRs" (roadmap, a repo setting) is still the maintainer's.

## Blocked on an outside contributor

- **#44, #47, #134** — `good first issue`, reserved until **2026-09-19 08:45Z**; unclaimed after that, they are 32–34.
- **#137** — outside PR #169 (@makiaveli1), opened 2026-09-18 11:39Z; it also moves the identity-pair rule into `_project_fields` for every `fields` tool, which is why #134 waits for it.
- **#151** — outside PRs #164 (needs rework) and #171 (same author, `parse_datetime`), both in `pyrite/models/base.py`; #148's eventual fix and possibly #149 queue behind them.
- **#58, #65, #95, #96** — outside PR #166 (needs rework); ADR-0034 declares its #58 fix correct, and themes 35–36 build on it.

## Needs a spike first

- **#9** (queue 5) — does it reproduce on `dev`, and is the cause a stale cached bundle mismatched with the API shape? Deliverable: a fix ticket, a close citing package G's assertion, or a ticket against the packaged build's hash/cache headers.
- **#13 + #43** (queue 6) — with no embedding worker attached, attach one (lifecycle owner, #102) or skip and record the debt; and what a fresh config and a zero-embedding semantic search say. Deliverable: two fix tickets with acceptance, or an ADR draft.
- **#148** (not queued; not 0.24.2) — can ruamel emit block sequences at a per-node or per-file indent, or is indent dumper-global? Deliverable: acceptance for the mechanism that exists, or "not feasible" and a maintainer decision about normalising on save.

## Count and distance

**40 entries**: 31 dispatchable now or as their dependencies merge (28 worker themes — three of them placeholders the two spikes will define — 2 spikes, 1 conductor chore) and 9 blocked (3 on the contributor window, 5 on ADR-0034, 1 on the hashing decision).

**Distance to the 0.24.2 definition of done, in one-at-a-time cycles** (a cycle = one worker pass + one review):
- *The literal definition* — "cut by one script from a CI-verified commit; every milestone issue closed": the reviews of #140 and #145, then entries 5, 6, 10, 11, 12 — **about 5 cycles plus 2 reviews** if #9 needs a fix; #9 closing on package G's assertion instead pulls 7, 8 and 9 onto the path (**about 7 cycles**).
- *Everything the roadmap's 0.24.2 workstreams list* (Playwright blocking, the packaged UI, the post-tag workflow, docs facts, the README), behind the two machine-safety themes and entry 3: entries 1–20 — **20 cycles plus the 5 queued reviews, 7 of them heavy** (2, 5, 9, 11, 13, 14's evidence, 17). Package H's last criterion ("passes on `main`") can only be observed at the release itself.

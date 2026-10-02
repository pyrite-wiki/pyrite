---
id: adr-audit-2026-10
type: design_doc
title: "ADR audit, 2026-10-02: the true standing of every ADR"
status: draft
author: pyrite-docs
date: "2026-10-02"
reviewers: []
tags: [adr, audit, documentation, process]
links:
- target: adr-0024
  relation: related
  kb: pyrite
- target: adr-0029
  relation: related
  kb: pyrite
- target: adr-0037
  relation: related
  kb: pyrite
- target: adr-0040
  relation: related
  kb: pyrite
---

# ADR audit, 2026-10-02

The maintainer's brief (2026-10-02): "review the existing ADR's. Some have been
superceded in whole or part, some are possibly misslabeled, and they are part
of the agent interface for undestanding the codebase." This entry records what
each of the 39 files in `kb/adrs/` decides and where it truly stands, read
against the ADRs' own text and against the code at `origin/dev` (0.25.6).

**Evidence marks.** **V** = verified (the ADR's own text or frontmatter, or a
`git grep` / read of the code, named in the row). **I** = inferred (the code
for the claim exists and looks consistent; I did not check each detail).
Nothing here accepts, rejects or rewrites an accepted decision. The PR that
carries this entry applied only the mechanical corrections listed under
"Corrections applied". Everything else is under "For the maintainer".

## Standings used

- **current**: label and body are true.
- **current, detail stale**: the decision stands; named sections no longer match the code or a later ADR.
- **superseded in whole / in part by X**: a later accepted ADR says so.
- **accepted, not (fully) implemented**: accepted, and code shows part of it absent.
- **proposed or draft, open**: not decided.
- **contradicted, unresolved**: two accepted texts, or an accepted text and the code, disagree and nothing resolves it.
- **rejected**: label is true.

## Summary by standing

| Standing | Count | ADRs |
|---|---|---|
| current | 20 | 0001, 0005, 0008, 0009, 0010, 0011, 0012, 0014, 0015, 0017, 0019, 0020, 0021, 0022, 0023, 0026, 0027, 0033, 0034, 0035 |
| current, detail stale (or amended, decision stands) | 6 | 0002, 0006, 0007, 0025, 0032, 0036 |
| superseded in whole | 1 | 0003 (by 0029) |
| superseded in part | 1 | 0018 (by 0024) |
| accepted, not (fully) implemented | 5 | 0004, 0013, 0028, 0037, 0040 |
| contradicted, unresolved | 2 | 0024, 0029 |
| proposed or draft, open | 3 | 0030, 0031, 0038 |
| rejected | 1 | 0016 |
| **Total** | **39** | |

Not counted as files: the proposed ADR-0039 (remote branch only) and the draft
ADR-0041 and ADR-0042 (PR #635). See "Not on dev".

## The table

| # | id | Title | Stated status | What it decides | True standing | Evidence |
|---|---|---|---|---|---|---|
| 1 | adr-0001 | Git-native markdown storage | accepted | Markdown + YAML frontmatter in git is the source of truth; SQLite is a derived index | **current**. 0041 (proposed) would build on it | I: `pyrite/storage/`, the whole KB works this way |
| 2 | adr-0002 | Plugin system via entry points | accepted | `pyrite.plugins` entry-point group; addenda: 19-method protocol, capability declarations | **current, amended by 0040** (banner and link added). The "six extensions ship" line is stale: 0040 decision 2 deletes `cascade`, which is still in `extensions/` | V: `pyrite/plugins/capabilities.py` (5 members), `_METHOD_CAPABILITIES` at `pyrite/plugins/registry.py:146`; 0040 frontmatter `amends adr-0002`; `ls extensions` shows `cascade` |
| 3 | adr-0003 | Two-tier data durability | superseded | Content tier in git, engagement tier in local SQLite | **superseded in whole by 0029**, "absorbed, not reversed". Label true. Note the superseding ADR is itself only partly implemented (see 0029) | V: 0003 banner + `superseded_by adr-0029`; 0029 section 5 "ADR-0003 is superseded" and `supersedes adr-0003` |
| 4 | adr-0004 | Folder-per-author permissions | accepted | Author folders; `before_save` hooks check the author matches the folder | **accepted, not implemented at the app layer**: hooks never know the acting user, so the check is inert. The git layer (CODEOWNERS) is outside the code | V: `KBService._hook_ctx` (`pyrite/services/kb_service.py:654-665`) passes `user=""` on every write; `extensions/social/.../hooks.py:19-40` `before_save_author_check` returns early when `user` is empty. 0040 plugin-identity decision (injected by the dispatcher) not yet in code |
| 5 | adr-0005 | SQLAlchemy ORM with Alembic | accepted | ORM + Alembic migrations; raw connection for FTS5 | **current** | V: `alembic>=1.13.0` in `pyproject.toml`; `pyrite/storage/alembic/`, `pyrite/storage/migrations.py` |
| 6 | adr-0006 | MCP three-tier tool model | accepted | read / write / admin tiers; plugins register tools per tier; server defaults to write | **current, detail stale**. The mechanism stands; the tier contents ("4 software-kb read + 2 write tools") are wrong, and tiers predate users, grants and 0037's `Action` vocabulary, which 0037 theme 4 layers on tool registration | V: `PyriteMCPServer(tier=...)` builds 72 / 103 / 112 tools (read / write / admin); CLI default `--tier write` (`pyrite/cli/__init__.py:739-743`), class default `read` (`mcp_server.py:392`); `sw_*` tools are 23 of the admin set |
| 7 | adr-0007 | AI integration: three surfaces, BYOK | accepted | One backend, three surfaces; Anthropic + OpenAI SDKs, no LiteLLM; BYOK; MCP prompts/resources; `/api/ai/*` | **current, detail stale**. Architecture holds. Section 6 lists 8 endpoints; 4 exist in `ai_ep.py` (summarize, auto-tag, suggest-links, chat) and `GET /api/ai/status` lives in `admin.py`; `/generate`, `/assist`, `/expand-query` are absent. The plugin tree is `.claude/skills/`, not `skills/` | V: `grep @router pyrite/server/endpoints/ai_ep.py`; `pyrite/services/llm_service.py` `complete/stream/embed`; MCP prompts: 4 (`research_topic`, `summarize_entry`, `find_connections`, `daily_briefing`); `.claude-plugin/plugin.json` |
| 8 | adr-0008 | Structured data and schema-as-config | accepted | Types and fields declared in kb.yaml; amendment 2026-10-01: enums read, checked once, enforced per KB | **current** | V: dated amendment in body, ruamel.yaml dependency in `pyproject.toml` |
| 9 | adr-0009 | Type metadata, AI instructions, plugin docs | accepted | `get_type_metadata`, `ai_instructions`, display hints | **current** | I: `ai_instructions` documented in `pyrite/plugins/protocol.py:223` |
| 10 | adr-0010 | Content negotiation and formats | accepted | Serializer registry (json, yaml, csv, markdown); section 7 import pipeline marked "Future" | **current** | I: `pyrite/formats/` has `json_fmt`, `yaml_fmt`, `csv_fmt`, `markdown_fmt`, `importers/` |
| 11 | adr-0011 | Collections, folder metadata, views | accepted | Collections as folders + virtual queries; five phases | **current**; phase completion not checked. Four backlog links were stale (fixed) | I: `pyrite/services/collection_query.py`; **not verified:** which of the five phases shipped |
| 12 | adr-0012 | Block references and transclusion | accepted | `^block-id` references, `![[...]]` embeds | **current** | I: `pyrite/services/block_service.py`, `pyrite/utils/markdown_blocks.py` |
| 13 | adr-0013 | Unified DB connection and transaction model | accepted | Phase 1 implemented (ORM writes, raw SQL for read-only search); Phase 2 (services off `db.conn`) and Phase 3 (plugins off `db.conn`) "future" | **accepted, partly implemented**: Phases 2 and 3 are open by the ADR's own text and still open | V: `grep -E "db\.conn\b\|\._raw_conn"` finds 70 hits in `pyrite/` outside migrations (19 in `worktree_service.py`, 15 in `index_worker.py`, 11 in `embedding_worker.py`) and 89 in `extensions/` |
| 14 | adr-0014 | Structural protocols for extension types | accepted | Types satisfy protocols by structure, not inheritance | **current**; implemented by 0017. Design-doc link fixed (the doc is under `kb/documents/`) | V: 0017 links `refines adr-0014`; `pyrite/models/protocols.py` |
| 15 | adr-0015 | ODM layer and schema migration | accepted | ODM layer, `SearchBackend` protocol, on-load schema migration | **current** | V: `SearchBackend` at `pyrite/storage/backends/protocol.py:15`, `BaseBackend` at `base_backend.py:67`, `_schema_version` in `pyrite/cli/schema_commands.py:366` |
| 16 | adr-0016 | LanceDB evaluation | rejected | No-Go; Postgres adopted as second backend | **rejected**, label true | V: no `lancedb` anywhere in `pyrite/` or `pyproject.toml`; `pyrite/storage/backends/postgres_backend.py` exists. Title carried a duplicate "ADR-0016:" prefix (fixed) |
| 17 | adr-0017 | Entry protocol mixins | accepted | Five mixins in `pyrite/models/protocols.py` | **current** | V: `Assignable`, `Temporal`, `Locatable`, `Statusable`, `Prioritizable` at `pyrite/models/protocols.py:16-128` |
| 18 | adr-0018 | Web UI KB management via git forks | accepted | Per-user shallow-clone forks, GitHub PRs, org/user hierarchy | **superseded in part by 0024** (per-user fork/clone/PR mechanics, single-instance case); stands as the future cross-instance design. Banner and link added | V: 0024 frontmatter `supersedes adr-0018` and its section "What this supersedes from ADR-0018". Repo fork code (`repo_service.fork_and_subscribe`) remains |
| 19 | adr-0019 | Pull-based kanban for agent teams | accepted | Kanban over sprints; `milestone` and `review_queue` replace `sprint` | **current** | V: `milestone` entry type (`extensions/software-kb/.../entry_types.py:384`); no `sprint` in the extension |
| 20 | adr-0020 | Kanban entity model | accepted | Milestone = entry type; lanes = config; review queue = view | **current** | V: `board.py` has `lanes` config; milestone entry type |
| 21 | adr-0021 | Definition of ready / done gates | accepted | Gates in `board.yaml`, evaluated in `sw_transition` / `sw_claim` | **current** | I: `gates` in `extensions/software-kb/.../board.py` |
| 22 | adr-0022 | Typed relationship entries (edge-entities) | accepted | Relationships that carry data are entries | **current**. Title duplicate prefix fixed | V: `OwnershipEntry` in the journalism-investigation plugin |
| 23 | adr-0023 | Static HTML site cache | accepted | Python-rendered static HTML under `/site/`, rebuilt on index sync | **current**. Predates the 2026-10-02 decision "the site asks the policy for what is public" and the proposed `published` switch (0039) | V: `pyrite/services/site_cache.py`, `pyrite/server/static.py` |
| 24 | adr-0024 | Git worktree collaboration model | accepted | Worktree per user, branch `user/{name}`, in-app admin merge queue, read all / write own | **contradicted, unresolved; amended by 0029 section 6; a replacement ADR is being drafted separately.** Affected sections listed below the table | V: see "ADR-0024: sections affected" |
| 25 | adr-0025 | Release workflow | accepted | `dev` integrates, `main` releases, tags ship, deployment tiers | **current, amended by 0032** (banner already present; `amended_by` link added). PyPI and capturecascade amendments in body | V: banner; 0032 opens "Amends ADR-0025" |
| 26 | adr-0026 | FIPS and state as promoted columns | accepted | `fips`, `state` become indexed entry columns | **current** | V: `fips = Column(String)` at `pyrite/storage/models.py:73` |
| 27 | adr-0027 | Per-entity-type state machine config (relaxed mode) | accepted | `state_machine` block per type; refines 0019/0021 | **current** | V: `TASK_WORKFLOW` and the per-type `state_machine` path at `pyrite/models/task.py:150,322` |
| 28 | adr-0028 | Backend-agnostic query DSL | accepted | One DSL, parsed to an AST, compiled per backend | **accepted, not implemented**. No parser, AST or compiler exists; the legacy FTS5 sanitizer is still the only path | V: `sanitize_fts_query` still at `pyrite/services/search_service.py:250`; no `AST`/`compile_query`/`fielded` under `pyrite/storage` or `search_service.py`; backlog item `backend-agnostic-query-dsl` is `status: proposed` |
| 29 | adr-0029 | Libraries, KB lifecycles, runtime state | accepted | Config YAML is the registry; library files; durable vs ephemeral KBs; state tables; worktrees as leased ephemerals | **contradicted, unresolved, and mostly unimplemented.** Details below the table | V: see "ADR-0029: claims against code" |
| 30 | adr-0030 | Agent-agnostic run execution (ACP) | proposed | `RunService`, ACP adapters, MCP-only tool access, enforcement in the write path | **proposed, open**. Phase 0 (spike) not started; 6 open questions, one blocks a phase gate | V: no `RunService` or `acp` under `pyrite/` |
| 31 | adr-0031 | The API is the product surface | draft | API is the only security boundary; grants not modes; scoped clients | **draft, open**. Several of its premises were adopted piecemeal by 0036/0037 (grants, scoped sockets) without this ADR being decided | V: status line in body ("DRAFT. Circulated for iteration, not decision") |
| 32 | adr-0032 | Branch flow and test progression | accepted | Feature branches, green-and-current merges to `dev`, layered gates | **current, detail stale.** Section 3b (snapshot `.dev0+sha` versions) is `proposed` inside an accepted ADR and `scripts/release.py` deliberately does the opposite; the decision-3 merge method (rebase) conflicts with the "queue is set to squash" note in the same file | V: `scripts/release.py:1360` ("pyproject.toml is deliberately NOT bumped to a `.dev0`"); `pyproject.toml` version `0.25.6`; ADR text "Open questions on 3b (proposed 2026-09-22, not yet decided)". **Not verified:** the live merge-queue method (a repo setting) |
| 33 | adr-0033 | Where work is tracked | accepted | GitHub issues for bugs/requests, KB for the roadmap; 2026-09-20 amendment: process findings in the KB | **current** | I: matches `.claude/` skills and CLAUDE.md |
| 34 | adr-0034 | Agent-facing reads are bounded by default | accepted | Lists paginated, bodies bounded | **current** | I: `pyrite/services/body_bounds.py`, `read_shaping.py` |
| 35 | adr-0035 | Writes are eventually embedded | accepted | `auto_embed` queues; it does not block the write | **current** | V: `KBService._get_embedding_worker` (`kb_service.py:382`) replaces the "nothing sets `_embedding_worker`" state the ADR describes; `.claude/skills/pyrite-dev/gotchas.md:475` |
| 36 | adr-0036 | A live-update socket lives no longer than its credential | accepted | Sockets close on credential change or expiry | **current, detail stale**. Consequences say KB-wide access changes are "not yet covered"; they now are | V: `announce_kb_policy_change` at `pyrite/services/credential_events.py:80`; `CredentialChange.kb_name` |
| 37 | adr-0037 | One authorization policy point and one error contract | accepted | `AccessPolicy`, `Principal`, `Action`, `Resource`; one error contract; structural guard; seven migration themes | **accepted, partly implemented.** Themes 0-3a landed (ADR's own "Accepted" section lists #476, #500, #501, #504, #508); 3b-5 not. `Principal.local()` is defined but no surface calls it. `Action.USER_MANAGE`, `SETTINGS_SECRET`, `SELF`, `REPO_EGRESS` exist but are "not decided in this theme" and refused. Banner said "Proposed" while status is accepted (fixed) | V: `pyrite/services/access_policy.py:206` (`local`), `:233-260` (`Action`, `_ACTION_TIERS` lists four of eight), `git grep "Principal.local()" pyrite` has no caller; `pyrite/server/errors.py:105` ("Not raised by any surface yet") |
| 38 | adr-0038 | Entry identity and the file lifecycle | proposed | `(kb, id)` identity, invariants I1-I10, migration steps 0-5 | **proposed, open, and stale as a label**: all five open questions are marked DECIDED (2026-09-25/26) and step 1 landed (#497, #505), but the ADR is still `proposed`. `previous_ids` (step 4) is absent from the code | V: `git log -- kb/adrs/0038*`; `git grep previous_ids pyrite` has no hits; `tests/test_storage_invariants.py` still carries xfails |
| 40 | adr-0040 | Extensions live out of tree; the plugin contract is the public API | accepted | `pyrite.plugin_api` facade, `PLUGIN_API_VERSION`, allowlist, conformance kit, plugins in the `pyrite-wiki` org; cascade deleted | **accepted, not implemented** (sequenced for 0.27). **id was `adr-0039` (fixed to `adr-0040`)**; body banner said "Proposed" (fixed) | V: no `pyrite/plugin_api*`, no `PLUGIN_API_VERSION`, no `plugins:` allowlist in `pyrite/config.py`; `extensions/cascade/` still present |

(There is no file numbered 39 on `dev`; see "Numbering".)

## ADR-0024: sections affected

The maintainer has decided that worktree routing is switched off for now and
that a new ADR replaces ADR-0024. Standing of each part today:

| ADR-0024 section | Standing | Evidence |
|---|---|---|
| Context, "Why git worktrees" | historical rationale; still true of git | I |
| Architecture, branch `user/{username}` | implemented | V: `WorktreeService` (`pyrite/services/worktree_service.py`: `ensure_worktree`, `submit`, `merge`, `reject`, `reset_to_main`, `delete_worktree`) |
| Read/write routing, "All users read from main" / "All subsequent writes go to the user's worktree" | **implemented in code and not switched off there**: entry create/update/delete route non-admin authenticated users to a worktree. The switch-off is a decision, not yet in code or in any ADR | V: `pyrite/server/endpoints/entries.py:753-761` (`resolver.get_write_service`), `pyrite/server/worktree_resolver.py` |
| Read/write routing, "Index per worktree" | **amended by 0029 section 6**: a diff index via overlay | V: 0029 section 6 "per-worktree diff index via OverlaySearchBackend"; `pyrite/storage/backends/overlay_backend.py` (`WorktreeDB`) |
| Submission and merge queue | implemented (REST endpoints in `pyrite/server/endpoints/worktree.py`) | V |
| **Permissions model (V1)**: "All KBs are readable by all authenticated users", "No per-KB permissions in V1" | **contradicted** by 0037 (per-KB grants, concealment of unreadable KBs, `kbs_for_user_at_tier`) and by the 2026-10-02 decisions (users read and write entries per KB by grant) | V: `pyrite/services/access_policy.py` (`effective_kb_role` walk, concealment `NOT_FOUND`) |
| Phase 3, worktree GC | not implemented; 0029 section 6 says to build it as the lease reaper | V: no gc/reap in `worktree_service.py`; no lease code in `pyrite/` |
| "Concurrent edits ... admin resolves", "Users don't see each other's unmerged work" | open: depend on the replacement ADR | I |

## ADR-0029: claims against code

| Claim | Standing | Evidence |
|---|---|---|
| Section 1: YAML config is the registry's source of truth; DB `kb` table is a derived cache; `~/.pyrite/` owns the registry | **partly true in code, and disputed by two later texts.** `config.py` still holds a `_db_kb_cache` bridged from the DB (`config.py:482-604`). The proposed 0039 says the `kb` table is the registry of record. The 2026-10-02 decision says every non-ephemeral KB is listed in config, one file per library, registry setup is the operator's | V: `pyrite/config.py:482`; `git show security/kb/adr-0039-one-kb-registry:kb/adrs/0039-one-kb-registry.md` section 1 |
| Section 2: library files, `pyrite library switch`, per-library DBs, readonly mounts | **not implemented** (0.26 per its own phasing) | V: `git grep -i "libraries\|--library\|PYRITE_LIBRARY" pyrite` has no library feature |
| Section 3: ephemeral KBs are NEVER written to the library YAML | **contradicted by code**: `EphemeralService` adds the KB to `config` and calls `save_config` | V: `pyrite/services/ephemeral_service.py:112-113` (`self.config.add_kb(kb)`, `save_config(self.config)`), docstring at `:42` ("in the registry row and in config.yaml") |
| Section 3: leases, `retention: record\|lock`, link rule enforced as QA rule | not implemented | V: `git grep "lease_expires_at\|retention" pyrite` finds nothing relevant |
| Section 4: a declared registry of state tables; `rebuild` cannot touch them | **no such registry exists** | V: `git grep -i "state_tables\|state table" pyrite` finds only an `oauth_state` migration and a comment in `storage/backends/protocol.py:6` |
| Section 5: 0003 superseded | true | V |
| Section 6: worktrees as user-leased ephemerals, exempt coordination KBs | direction only; the lease reaper is absent | V |

## Corrections applied in this PR

**Numbering and ids.** Checked for every file: file number, `adr_number`, `id`,
and the `# ADR-NNNN` heading.

- `kb/adrs/0040-extensions-live-out-of-tree-the-plugin-contract-is-the-public-api.md`:
  `id: adr-0039` changed to `id: adr-0040` (file number 40, `adr_number: 40`
  and heading `# ADR-0040` already agreed). **No file was renamed.** All other
  38 files agree on all four.
- Eight accepted ADRs have no `# ADR-NNNN` heading at all (0001-0007, 0023);
  no mismatch, so not changed.
- Two frontmatter titles carried a duplicate prefix that `pyrite sw adrs`
  printed ("ADR-0024: Git Worktree ...", also 0016 and 0022); prefix removed
  from the frontmatter `title` in 0016, 0022, 0024.

**Links that pointed at the old id `adr-0039`.** `git grep` over `kb/`,
`.claude/`, `docs/`, `pyrite/`, `tests/`, `scripts/`, `extensions/`:

- `kb/backlog/spike-plugins-out-of-tree-import-inventory-and-the-public-plugin-contract.md`:
  `[[adr-0039]]` meant the plugin ADR; now `[[adr-0040]]`.
- Left alone because they mean the registry ADR-0039: `kb/roadmap.md:672`
  ("One KB registry (ADR-0039, proposed)") and
  `kb/backlog/spike-one-kb-registry-adr-0039-proposed.md`.

**Status banners and links.**

- 0018: `> Superseded in part by [[adr-0024]]` banner and `superseded_by` link (status stays `accepted`: 0024 supersedes only the single-instance mechanics and says 0018 stays valid for federation).
- 0024: `> Amended by [[adr-0029]] (section 6)` banner and `amended_by` link.
- 0002: `> Amended by [[adr-0040]]` banner and `amended_by` link (0040 frontmatter says `amends adr-0002`).
- 0025: `amended_by adr-0032` link added to the existing banner.
- 0037: body banner said "Proposed (spike)" while frontmatter and its own "Accepted (maintainer, 2026-09-26)" section say accepted; banner now says Accepted. Decision text untouched.
- 0040: same stale "Proposed" banner corrected; its own "Decisions (maintainer, 2026-09-26: accepted with these)" section is the evidence.
- No ADR moved to `superseded`: only 0003 qualifies and it already was.

**Broken links between ADRs and KB entries fixed.**

- 0011: four `../backlog/X.md` to `../backlog/done/X.md` (dataview-queries, database-views, display-hints-for-types, block-references).
- 0013: `0005-sqlalchemy-orm-with-alembic-migrations.md` to `0005-sqlalchemy-orm-with-alembic.md`.
- 0014: `../designs/extension-type-protocols.md` to `../documents/extension-type-protocols.md`.
- 0015: `../backlog/schema-versioning.md` to `../backlog/done/schema-versioning.md`.
- Left: example wikilinks inside code samples (0011, 0012, 0022, 0027, 0038 `[[old-id]]` etc.) are illustrations, not links.

## Numbering

`kb/adrs/` has files 0001-0038 and 0040. ADR-0039 ("One KB registry: one
record per KB, one read path, one change event", `status: proposed`,
2026-09-26) exists only on `kb/adr-0039-one-kb-registry` in the `security`
remote (`kb/adrs/0039-one-kb-registry.md`, `adr_number: 39`, `id: adr-0039`).
The plugin ADR was committed as file 0040 with the id `adr-0039`, which would
have collided with the registry ADR when it lands; the id is now `adr-0040`,
so the registry ADR can land as 0039 without a clash. `pyrite sw adrs` today
shows no 39. Any ADR landing after this PR should check the number against
`pyrite sw adrs` and the remote branches.

## What an agent sees (`pyrite sw adrs -k pyrite`)

- All 39 files are listed, with the `adr_number`, title, status and date from frontmatter. 0040 is listed under 40 (was already, since the tool reads `adr_number`); `get adr-0040` now resolves to it, where before only `adr-0039` did.
- The listing is **not sorted** by number (it printed 36, 30, 24, 8, 17, ...). An agent has to sort it. That is a tool defect, not an ADR defect; no change made here.
- The listing carries no pointer to a superseding or amending ADR, so the banners in the files are the only place that information lives; an agent reading only `sw adrs` sees 0003 `superseded` and 0018, 0024, 0002, 0025 as plain `accepted`.
- Three titles showed an "ADR-NNNN:" prefix (fixed above).

## Not on dev

- **ADR-0039** (proposed, remote branch above). Would amend 0029 (section 1: the `kb` table as registry of record). Related to 0036, 0037, 0038.
- **ADR-0041** "A KB is files any tool can use; Pyrite is additive" and **ADR-0042** "A write changes what was asked and nothing else" (draft, PR #635, being revised). Not imported. Between them they would amend: **0001** (files are the source of truth, now with a round-trip guarantee for hand-edited files), **0029** (section 1 and the "one file per library" rule: 0041 says "This amends ADR-0029 section 1"), **0038** (identity derivable from the path when `id:` is absent; patch writes pin `id:`), and **0040** (the plugin contract: 0042 states "No signature changes" but cites 0040 section 6 conformance; 0041 says the contract is affected).

## Design docs other documents lean on

- `kb/designs/permissions-model.md` (`status: draft`, "brainstorm - not an ADR yet", three-layer model): predates 0037 and the grant model; not an authority. Not edited.
- `kb/designs/multi-user-threat-model.md` (`type: design`, `status: draft`): leans on 0036, 0037, 0038 and `Principal.local()` (theme 5, not landed). Authoritative only as far as 0037 themes 3b-5 land. `type: design` differs from the `design_doc` used elsewhere in `kb/designs/`; not edited.

## For the maintainer

Each item is one line; the recommendation follows the arrow.

**Looks superseded or stale, and no accepted ADR says so**

1. 0004 (folder-per-author) assumes hooks know the acting user; they do not (`user=""`) -> add an amendment once the dispatcher-injected identity decision (2026-10-02) is written into an ADR; until then mark it "accepted, app layer inert".
2. 0006 (MCP tiers) tier contents are stale and tiers sit beside 0037's `Action` -> a short amendment pointing to 0037 theme 4 and 0031's capability idea, or fold into the new MCP/CLI ADR planned for 0.27.
3. 0023 (site cache) predates "the site asks the policy for what is public" and 0039's `published` switch -> amend when 0039 is decided.
4. 0007 section 6 lists three endpoints that never shipped (`/generate`, `/assist`, `/expand-query`) -> amend to "not planned" or re-file as backlog.
5. 0036 "not yet covered: KB-wide access changes" is now covered (`announce_kb_policy_change`) -> one-line amendment (accepted text, so yours to approve).
6. 0037's last paragraph ("stays `proposed` until theme 1 lands") and its Context numbers are history under an accepted label -> leave, or add a dated "status" note.
7. 0002 addendum "Six extensions ship" is stale once cascade is deleted (0040 decision 2) -> note in the amendment when cascade is removed.

**Contradictions between accepted texts, or accepted text and code**

8. 0024 permissions model ("all KBs readable by all authenticated users, no per-KB permissions") contradicts 0037 and the grant model -> resolved by the replacement ADR; mark 0024 `superseded` when it is accepted.
9. 0024 "index per worktree" vs 0029 section 6 "diff index via overlay" -> same replacement ADR; 0029 wins today (banner added).
10. 0029 section 3 (ephemerals never written to config) vs `ephemeral_service.py:112-113` (writes them) -> decide whether the code or the ADR is right; the 2026-10-02 decision ("every non-ephemeral KB is listed in config") reads like the ADR, so the code is the likely bug (file an issue; I did not).
11. 0029 section 1 vs proposed 0039 vs the 2026-10-02 decision (one file per library; operator owns setup) -> three registry designs; accept 0039 or 0041/0042's amendment explicitly, then amend 0029 section 1. Until then an agent reading 0029 gets the wrong registry.
12. 0032 section 3b (snapshot versions) is `proposed` inside an accepted ADR while `scripts/release.py:1360` does the opposite -> decide 3b (reject, most likely) and record it; also record the actual merge method (ADR says rebase in decision 3 and squash in a note; CLAUDE.md says rebase).

**Accepted but not implemented (the ADR describes a future, and an agent reading it believes it is present)**

13. 0028 (query DSL) -> keep accepted but add "not implemented" to the status line, or move to `proposed` if the commitment lapsed; backlog item is `proposed`.
14. 0029 sections 2-4 (libraries, leases, state-table registry) -> 0.26 per its own phasing; add a pointer to the roadmap line so agents do not look for `pyrite library`.
15. 0037 themes 3b-5, `Principal.local()` with no caller, four undecided `Action`s -> already tracked in its migration table; consider a "landed" column.
16. 0040 (facade, `PLUGIN_API_VERSION`, allowlist, canary) -> 0.27 per its own sequencing.
17. 0013 Phases 2 and 3 (70 `db.conn` uses in core, 89 in extensions) -> keep, or retire the phases as no-longer-planned.

**Proposed or draft ADRs that have sat undecided**

18. 0038 (proposed, five questions decided, step 1 landed): accept it, since the code already follows it; it blocks steps 2-5 being cited as settled, and 0041/0042 both link it.
19. 0030 (proposed since 2026-09-17, no Phase 0 spike): decide go/no-go; it blocks any ACP/run-service work and 0031's "run-execution capability".
20. 0031 (draft since 2026-09-17): its grant model was adopted piecemeal by 0036/0037 while the ADR is still a draft -> accept the parts that are now fact (API as the only boundary; grants not modes) and reject or defer the rest; it blocks the capabilities work (`REPO_EGRESS`, tool actions).
21. ADR-0039 (remote branch only): blocks 0029 section 1 being amended and the "published" switch.
22. 0041/0042 (PR #635): block the "a KB is files" principle being citable.

**Housekeeping**

23. `pyrite sw adrs` is unsorted and shows no supersession links -> a small tool fix (ticket).
24. Eight early ADRs (0001-0007, 0023) have no `# ADR-NNNN` heading -> add if you want files self-identifying.
25. `kb/roadmap.md` has no section called "The release line" on `dev`; I could not check the docs against it.

## Not checked

- Section-by-section implementation of 0009, 0010, 0011 (five phases), 0012, 0019-0021 gate details, 0026-0027 beyond the named symbols, 0034 and 0035 beyond the named symbols: marked I where so.
- The live repository settings (merge-queue method, rulesets) behind 0032 and CLAUDE.md.
- Whether the web UI still has the worktree "submit changes" screens of 0024 Phase 2/3.
- The 2026-10-02 decisions that are not yet written in any file (two planes, operator-only kb.yaml and ownership, no anonymous MCP, alpha contracts, journalism tools experimental until 0.28): taken from the brief, not verified in code or KB text.
- `kb/roadmap.md` "The release line": section absent on `dev`.
- No suite or server was run.

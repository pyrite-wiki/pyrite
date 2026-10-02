---
id: alpha-supported-surface
type: design_doc
title: "The alpha's supported surface: what is supported, what is experimental, what is unsupported"
status: draft
author: pyrite-docs
date: "2026-10-02"
reviewers: []
tags: [alpha, supported-surface, release, documentation, roadmap]
links:
- target: adr-0041
  relation: related
  kb: pyrite
- target: adr-0043
  relation: related
  kb: pyrite
- target: adr-0045
  relation: related
  kb: pyrite
- target: adr-0040
  relation: related
  kb: pyrite
---

# The alpha's supported surface

> **Proposed** (2026-10-02). The maintainer decided the principle: the alpha
> supports a small surface (files, the index, search, the CLI, a small core of
> MCP tools) and marks the rest experimental. **The exact list below is a
> proposal. Approving it is the maintainer's.** The principle is in the roadmap's
> release line; this entry places every surface against it, with the evidence
> for each placement.

## What the words mean

- **Supported.** Documented. Tested through the real surface. A defect is fixed
  in the next weekly release. The contract is alpha (it may change with notice
  in a changelog fragment), but it is kept working.
- **Experimental.** It works for its author's own use. It may change or break
  without notice, it is marked as such in the README and in `--help` or the tool
  description, and fixes are best effort (or a contributor's).
- **Unsupported.** Not maintained. Slated for removal or a move out of tree.

The journalism-investigation tools are already marked experimental and
unsupported (#644). Multi-user stays experimental (README banner, "Here be
dragons").

## Evidence

Two kinds, per placement.

- **Tested through the real surface?** Counted by search over `tests/` and
  `extensions/*/tests/` (410 test files; 66 use `CliRunner`). A count of files
  that name a command or tool is a heuristic, not coverage; "none found" means
  none found by that search, and the surface may be exercised another way. Live
  process tests (`tests/e2e/`, marker `e2e`, not in the default run) start a
  real stdio MCP server, an SSE MCP session and a REST server.
- **Did the 2026-10-01 readiness audit find it working?** The audit
  (`pyrite-readiness-2026-10-01.md`) ran the README's install and the
  getting-started guide in a scratch directory on v0.25.5 and on a fresh `dev`
  checkout. "Not covered" means the audit did not run it.

Counts of commands and tools were taken on 2026-10-02 from the CLI's own Typer
app and from `PyriteMCPServer(tier=...)` (read 72, write 103, admin 112, with
all six extensions installed).

## 1. The core: files, index, search

| Surface | Proposed | Tested through the real surface | Readiness audit |
|---|---|---|---|
| A KB as Markdown with YAML frontmatter in git (ADR-0041) | supported | `tests/test_roundtrip_identity.py` and `tests/test_update_never_loses_a_key.py`; the corpus of files Pyrite did not write (ADR-0042) is not yet built | The release the README named (v0.25.5) deleted or rewrote hand-written frontmatter on `update`; fixed on `dev` (#557, #549, #561, #568). Plain Markdown with no frontmatter is skipped as malformed (#22) |
| The SQLite index, `index build`, `index sync`, `index health` | supported | `tests/test_storage_invariants.py` (the ADR-0038 state machine, with strict expected failures) | Edge cases around copied, moved or duplicated files fail today (#6, #7, #485 to #487, #494, #495); the ADR-0038 reconcile is 0.26. Workaround documented: `touch` the file or rebuild |
| Keyword search (FTS5) | supported | 13 CLI test files; `tests/e2e` REST flow | worked |
| Semantic and hybrid search (the `[semantic]` extra, local model) | supported, with the extra named in the README | `tests/test_embedding*.py` and `tests/test_semantic_search_warns_when_nothing_is_embedded.py` | worked with the extra; silent when the extra is missing (#43); 3.5 s per CLI call to load the model |
| Python 3.11 to 3.13 | supported | the CI matrix | 3.14 also worked in the audit's hallway run; classifiers stop at 3.13 |
| SQLite backend | supported | all of the above | worked |

## 2. The CLI

43 top-level commands: 24 plain commands and 19 groups with 132 subcommands
(156 leaves). `pyrite-admin` has a further 9 entries, which overlap `pyrite`'s
`kb`, `index`, `repo`, `auth`, `config` and `schema`.

| Commands | Proposed | Tested (files naming it, through `CliRunner`) | Readiness audit |
|---|---|---|---|
| `init`, `create`, `get`, `update`, `delete`, `search`, `link`, `backlinks`, `add`, `import` | supported | 3, 12, 5, 10, 2, 13, 5, 5, 3, 5 | `init`, `create`, `search`, `get`, `link`, `backlinks` worked. `update` see section 1. The guide's tutorial search returned nothing (tags are not in the full-text index) |
| `orient`, `list-entries`, `batch-read`, `recent`, `rename`, `timeline`, `tags`, `list` | supported | none found for `orient`, `list-entries`, `batch-read`, `recent`, `rename` (named in 2, 0, 0, 2, 3 files elsewhere); `timeline` 3, `tags` 11, `list` 2 | `orient` worked. `orient` as the first call was fixed in #591 |
| `kb` (12), `index` (7), `links` (7), `qa` (10), `schema` (3), `db` (2: `backup`, `restore`), `config`, `ci`, `readme` | supported | `kb` 9, `index` 9, `links` 7, `qa` 8, `schema` 6, `db` 14, `config` 35, `ci` 2, `readme` 1 | `index` and `kb` used by the audit's runs. `pyrite --help` still describes "citizen journalists" rather than the README's product |
| `mcp` (start the server) | supported | 2 | started in under a second over stdio; 29 / 40 / 48 tools per tier on v0.25.5 |
| `mcp-setup` | experimental until #582 lands (0.25.7) | none found | wrote a config file that neither Claude Desktop nor Claude Code reads, and chose the admin tier (the audit's B2). Being reworked by #582 |
| `serve` | experimental | 4 | answers 404 on a fresh clone until the web UI is built; not covered otherwise |
| `task` (10) | experimental | 7 | not covered. Pyrite's own process runs on it |
| `repo` (8), `auth` (5), `extension` (4), `export` (2), `collections` (2), `protocol` (2) | experimental | `repo` 5, `auth` 3, `extension` 2, `export` 1, `collections` none found, `protocol` none found | not covered |
| `sw` (23) | experimental (the in-tree reference extension; Pyrite's own process runs on it) | software-kb's 311 tests, through its own Typer app | not covered; `pyrite sw` is absent on the README's tag install |
| `investigation` (18), `social` (4), `wiki` (4), `zettel` (4) | experimental | the extensions' own tests (435, 50, 64, 48) | `investigation`'s write tools fail under the real MCP server (#94, #92, #93) |
| `cascade` (5) | unsupported (to be deleted, ADR-0040 decision 2) | cascade's 136 tests | not covered |

## 3. MCP

112 tools at the admin tier: 72 at read, 31 more at write (103), 9 more at
admin (112).

| Group | Tools (read / write / admin) | Proposed | Evidence |
|---|---|---|---|
| **Core entries**: `kb_orient`, `kb_search`, `kb_get`, `kb_batch_read`, `kb_read_body`, `kb_list`, `kb_list_entries`, `kb_schema`, `kb_tags`, `kb_recent`, `kb_backlinks`; `kb_create`, `kb_update`, `kb_delete`, `kb_link` | 11 / 4 / 0 = 15 | **supported** (the small core) | Each is named in 3 to 15 test files. `tests/characterization/mcp_calls.py` drives 19 tools through the dispatcher; `tests/e2e/test_mcp_stdio.py` starts a real server. The audit ran the stdio server and the read tools and `kb_create` |
| Core reads beyond the small core: `kb_timeline`, `kb_stats`, `kb_batch_suggest`, `kb_discover_neighbors`, `kb_find_by_assignee`, `_location`, `_status`, `kb_find_overdue` | 8 / 0 / 0 | experimental | named in tests; not covered by the audit |
| QA and index status: `kb_qa_status`, `kb_qa_validate`, `kb_qa_assess`, `kb_index_job_status`; `kb_bulk_create` | 3 / 2 / 0 | experimental | named in tests; not covered |
| Tasks: `task_*` | 6 / 5 / 0 = 11 | experimental | `tests/test_task_*`; Pyrite's own process runs on them |
| Administration: `kb_commit`, `kb_push`, `kb_index_sync`, `kb_manage`, `kb_registry_add`, `_health`, `_reindex`, `_remove` | 0 / 0 / 8 | experimental (the operator's, ADR-0043) | the audit saw `kb_push` and `kb_registry_remove` in the admin tier |
| Vocabulary tools in core files: `list_edge_types`, `solidarity_infrastructure_types`, `solidarity_timeline` | 3 / 0 / 0 | experimental (cascade-shaped; follow cascade's deletion) | `tests/test_mcp_list_edge_types.py` |
| journalism-investigation (`investigation_*`) | 14 / 8 / 0 = 22 | experimental, marked (#644) | write tools fail under the real server (#94, #92, #93) |
| software-kb (`sw_*`) | 15 / 8 / 0 = 23 | experimental (reference extension) | 311 extension tests |
| social, wiki, zettelkasten | 3 / 2 / 0; 3 / 2 / 1; 2 / 0 / 0 = 5, 6, 2 | experimental | extension tests |
| cascade (`cascade_*`) | 4 / 0 / 0 | unsupported | 136 extension tests |
| MCP prompts (4) and resources | | experimental | `tests/test_mcp_prompts.py`, `tests/test_mcp_resources_session.py` |

The stdio server is the operator's (ADR-0043); `--tier` is a tool filter for
that process, not authorization.

## 4. REST and the web UI

| Surface | Proposed | Evidence |
|---|---|---|
| REST: 135 operations (111 under `/api`, 19 under `/auth`; measured 2026-09-25) in 24 endpoint modules | experimental. The web UI is its client | `tests/test_api_*`, the characterization goldens, `tests/e2e/test_rest_flow.py`. Not covered by the audit beyond `GET`s |
| Web UI, single-user parts: entry browse and edit, search, graph, timeline, tags, daily, QA | experimental (**question 2**) | 11 Playwright specs in `web/e2e`; the audit built it from a clone: it needs `npm run build`, and is not in the tag install. Open: "New collection" returns 500 (#480), no delete in the UI (#117), light mode unreadable (#12), "which KB am I in" (#10) |
| Web UI, multi-user parts: login, register, changes, merge queue, settings for accounts | experimental | the multi-user issue cluster (#544, #517, #518, #516, #539, #540, #521, #565, #404, #572) belongs to 0.26 |
| `/site` static HTML cache and the sitemap | experimental | `tests/test_site_*` and the site-cache tests |
| AI endpoints (`/api/ai/*`, bring your own key) | experimental | `tests/test_ai_endpoints.py` |
| Live updates (`/ws`) | experimental | `tests/test_websocket_*` |
| Streamlit (`ui_streamlit.py`, `pyrite/ui`) | unsupported | root-level clutter in the audit |

## 5. Backends, extensions, deployment

| Surface | Proposed | Evidence |
|---|---|---|
| Postgres backend | experimental | 71 conformance tests, run in CI against a pgvector service; not covered by the audit |
| Overlay backend and worktrees (ADR-0024, ADR-0044) | experimental, routing off | the architect's probe found ordinary situations that fail (ADR-0044) |
| Extensions: software-kb | experimental, in tree (the reference) | 311 tests |
| Extensions: journalism-investigation | experimental, marked (#644) | 435 tests; the write tools fail under the real MCP server |
| Extensions: social, encyclopedia, zettelkasten | experimental | 50, 64, 48 tests; zettelkasten is also a `pyrite init` preset |
| Extensions: cascade | unsupported (deleted) | 136 tests |
| Local use: CLI plus stdio MCP | **supported** | the audit's Run A and Run B |
| `pyrite serve`, local web | experimental | see section 4 |
| Docker (`docker compose up`), one-click deploy (Render, Railway, Fly) | experimental | not covered by this audit's list; no Docker image is published |
| Hosted or shared multi-user instance | experimental | README banner; SECURITY.md |
| Install: from git; no PyPI wheel | stated plainly | the audit's distribution note |
| Claude Code plugin (`.claude-plugin/plugin.json`) | experimental | not covered |

## Counts

| | Supported | Experimental | Unsupported |
|---|---|---|---|
| CLI top-level commands (43) | 22 (`init`, `search`, `get`, `create`, `add`, `update`, `delete`, `rename`, `link`, `list-entries`, `batch-read`, `orient`, `recent`, `timeline`, `tags`, `backlinks`, `ci`, `list`, `config`, `mcp`, `import`, `readme`) and the groups `kb`, `index`, `links`, `qa`, `schema`, `db` = 28 | `mcp-setup`, `serve`, `task`, `repo`, `auth`, `extension`, `export`, `collections`, `protocol`, `sw`, `investigation`, `social`, `wiki`, `zettel` = 14 | `cascade` = 1 |
| MCP tools (112) | 15 | 93 | 4 |

(The CLI row counts top-level entries: 28 + 14 + 1 = 43. The MCP row:
15 + 93 + 4 = 112.)

## Questions for the maintainer

Ranked by what they block (the README's claims and the 0.25.7 announcement).

1. **Approve the list.** The core placements (sections 1 to 3) are the
   proposal; the README's tool table, `pyrite --help` and each tool's
   description carry "experimental" from it. *Recommended: approve as written,
   then mark the experimental tools in their descriptions in 0.25.7.* Blocks
   the announcement text.
2. **The web UI's single-user parts.** Your list names the CLI, the index,
   search and MCP as supported and the web UI's multi-user parts as candidates
   for experimental. The single-user web UI is not named. It needs a clone and
   `npm run build`. *Recommended: experimental, until it is packaged and #10,
   #12, #117 and #480 are fixed.*
3. **Tasks.** `task` and the 11 `task_*` tools are how Pyrite's own process
   runs but are not in your list. *Recommended: experimental, and say that
   Pyrite's own loops use them.*
4. **software-kb.** In tree, the worked example for plugins (ADR-0040), and
   Pyrite's own process runs on it. *Recommended: experimental, in tree.*
5. **The admin-tier MCP tools**, including `kb_index_sync`, which serves the
   index. *Recommended: experimental, with `kb_index_sync` moving to supported
   if you want the index to be operable from MCP.*

## Not verified

- The tests-through-the-real-surface column is a search, not coverage. The
  default run excludes `tests/e2e`; this entry did not run any test, server or
  suite.
- The readiness audit's results are from 2026-10-01 and v0.25.5 or a `dev`
  checkout at that date. Several findings have since been fixed or are in
  flight; this entry does not re-run them.
- REST operation counts are ADR-0037's footnote (2026-09-25). Web routes and
  specs were counted by listing, not run.
- Docker, the one-click deploys and the Claude Code plugin were not exercised
  by the audit.

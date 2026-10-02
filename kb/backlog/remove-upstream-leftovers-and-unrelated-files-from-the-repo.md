---
id: remove-upstream-leftovers-and-unrelated-files-from-the-repo
title: Remove upstream leftovers and unrelated files from the repo
type: backlog_item
tags:
- hygiene
- public-repo
importance: 5
kind: improvement
status: proposed
priority: medium
effort: S
rank: 0
---

## Handled by #585 (PR #592)

Done there: `claude_desktop_config.json` and `ui_streamlit.py` deleted; the
`ZK_STREAMLIT_PORT` line removed from `.env.example`; `MCP_SUBMISSION.md` and
`KnowledgeClaw-Spec.md` moved to `kb/designs/` (`mcp-submission.md`,
`knowledgeclaw-spec.md`; deleting the spec is still the maintainer's call).
`pyrite-mcp/` stays: `publish.yml` builds it.

**Remaining:** `KNOWN-ISSUES.md` (#583), `deploy/start.sh`, the four scraper
scripts, `pyrite/models/task_validators.py`, the `pyrite/ui/` decision,
`FEEDBACK.md` / `UPSTREAM_CHANGES.md` placement, stale branches, `adapter-node`.
The table below is the original list; rows for the files above are done.

## Problem

Tracked files that are dead, inherited from the upstream fork, or belong to a
different project:

| Path | Why |
|---|---|
| `claude_desktop_config.json` | upstream's `C:\Code\Zk\mcp_server.py` paths and `ZK_NOTES_DIR`; unreferenced |
| `ui_streamlit.py` | upstream "Zettelkasten Assistant" UI; calls routes that do not exist; streamlit is in no extra |
| `.env.example:19` | `ZK_STREAMLIT_PORT` |
| `MCP_SUBMISSION.md` | one-off draft; claims `pip install pyrite`, which ADR-0025 says is unreachable |
| `KNOWN-ISSUES.md` | single item, already promoted to the backlog |
| `deploy/start.sh` | unreferenced; Dockerfile CMD is `pyrite-server` |
| `KnowledgeClaw-Spec.md` | speculative spin-off spec; delete or move to `kb/designs/` |
| `scripts/conflict_analysis.py`, `cross_reference_appointees.py`, `import_appointees.py`, `scrape_appointee_details.py` | an unrelated scraper project; referenced nowhere. Move to its own repo. |
| `pyrite/models/task_validators.py` | imported only by `tests/test_task.py` — confirm, then remove |

Keep: `scripts/check_import_cycles.py`, `scripts/check_fix_commit_has_tests.py`
(hooks and CI use them), `AGENTS.md`, the deploy configs, `pyrite-mcp/`
(pending the packaging ticket), `benchmarks/`.

**Decision needed:** `pyrite/ui/` — about 1000 lines of Streamlit with no
declared dependency, no tests, and a `pyrite-ui` script that does not exist.
Delete, or declare a `ui` extra and test it.

Also: `FEEDBACK.md` and `UPSTREAM_CHANGES.md` (says "583 tests", "Streamlit
UI") are active but belong under `docs/` or `kb/`; update the README link.
Stale remote branches from March: `feature/structured-epics-prioritized-backlog`,
`feature/web-kb-management`, `feature/worktree-collaboration-v1`,
`spike/0.10-schema-odm-lancedb` (plus five stale local ones). Web:
`adapter-node` is installed (in runtime `dependencies`) but only
`adapter-static` is used.

## Acceptance

- [ ] Each path above is deleted, moved, or has a recorded reason to stay.
- [ ] Full suite green; README links resolve.

Source: 2026-09-17 project review (three read-only audits: docs/contributor, public-repo, code-health). These are deletions of the owner's files — the owner decides each one.

---
id: the-cli-is-the-agent-interface-one-output-error-contract-a-fresh-index-a-zero
title: 'The CLI is the core of the terminal agent interface: one contract in the service layer, a fresh index, a zero-setup local KB'
type: backlog_item
tags:
- cli
- agents
- enhancement
importance: 5
kind: feature
status: proposed
priority: high
effort: L
rank: 0
---

Maintainer direction, 2026-09-26. We meet agents and users where they are. The CLI is the core of the terminal agent interface (pi, Claude Code). MCP serves Claude Desktop and Cowork agents, and the web UI serves people. So the contract below (errors, result shapes, index freshness) lives in the service layer, where every surface gets it. The CLI and MCP are thin adapters over it (ADR-0037's single error contract). The CLI is the first surface to hold it, because terminal agents chain and parse text. Evidence comes from the CLI-only hallway test of 2026-09-26. An agent could do every task, but four things mislead it. Bugs are filed as #526 (partial failures exit 0), #527 (backlinks drop custom relations), #528 (ambiguous get) and #529 (a bad --format gives a traceback).

## Three themes (0.27)

1. **One output and error contract.**
   - Every command takes --format json|jsonl. JSON is the default when output is not a terminal.
   - Lists use one envelope (today search returns results, list returns entries, kb list returns kbs), or one record per line under jsonl.
   - A field has the same type everywhere: metadata is a string in search and an object in get.
   - create returns JSON with the new id.
   - Errors use one envelope with a stable error_code from a fixed list, on a fixed stream, never wrapped. Today create and delete print wrapped text on stdout, and one failure gets different codes depending on the command.
   - Exit codes differ per class (not found, conflict, validation), and a partial failure never exits 0.
   - Logging is WARNING on stderr when stderr is not a terminal; configure_quiet exists but is never called.
2. **Keeping the index fresh is the CLI's job.**
   - Reads index changed files before answering. Today a file arriving through git pull answers NOT_FOUND until index sync runs.
   - Embedding stays off every read path. A sync took 10.9 s, because it loaded the model to embed one entry.
   - Metadata-only commands don't import heavy modules. Every command starts in about 0.5 s; importing pyrite.cli costs 0.45 s.
3. **Record, update, what's open: three obvious commands.**
   - init --here gives a KB with no global registration, relative paths and a gitignored index.
   - --status can be set and filtered on create, update, list and search.
   - Search has a match-all query, and tags are searchable text.
   - What create accepts is exactly what index health accepts: type names, importance range, empty stdin.

Dispatch each theme as a mission, plan first. The hallway test's full friction list is in desk/notes/conductor-log-2026-W39.md (2026-09-26).

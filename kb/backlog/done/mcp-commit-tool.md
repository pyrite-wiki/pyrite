---
id: mcp-commit-tool
type: backlog_item
title: "Add kb_commit MCP Tool and REST Endpoint"
kind: feature
status: done
priority: high
effort: M
tags: [mcp, git, agents, api]
---

# Add kb_commit MCP Tool and REST Endpoint

Close the automation loop for the git decision gate by adding programmatic commit/push surfaces.

## Context

Pyrite's architecture treats git commit as a decision gate — AI agents can create/modify entries (write tier), but changes sit as uncommitted files until approved. Currently there is no programmatic way to commit; the gap must be bridged by a human running `git commit` manually.

## Scope

### MCP Tool (admin tier only)
- `kb_commit` — commits staged changes to the KB repository
  - Parameters: `message` (required), `kb` (optional, defaults to current), `paths` (optional, list of entry IDs or globs), `sign_off` (optional bool)
  - Checks that the KB is backed by a git repo
  - Stages specified paths (or all changed KB files if not specified)
  - Creates commit with the given message
  - Returns commit hash, files changed, diff summary
- `kb_push` — pushes commits to the remote (admin tier only)
  - Parameters: `kb`, `remote` (default: origin), `branch` (optional)
  - Requires configured remote

### REST Endpoint
- `POST /api/kbs/{kb}/commit` — same parameters as MCP tool
- `POST /api/kbs/{kb}/push` — same parameters as MCP tool
- Both behind admin authentication when API key is configured

### CLI
- `pyrite kb commit -k <kb> -m "message"` — commit KB changes
- `pyrite kb push -k <kb>` — push to remote

## Design Decisions

- Admin tier only — prevents write-tier agents from self-approving their changes
- Commit and push are separate operations — allows review between commit and publish
- Uses existing `GitService` for git operations
- Entry-level granularity via `paths` parameter — can commit specific entries, not just "all changes"

## Rationale

This completes the "git as approval layer" pattern. With this tool, automated pipelines can: create entries (write tier) → review changes (read tier) → commit approved changes (admin tier) → push to remote (admin tier). The tier separation ensures the commit decision is always an explicit, authorized action.

## References

- [Collaboration Services](../../components/collaboration-services.md)
- [MCP Server](../../components/mcp-server.md)

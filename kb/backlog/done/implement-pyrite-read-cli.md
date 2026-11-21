---
id: implement-pyrite-read-cli
type: backlog_item
title: "Implement pyrite-read CLI Entry Point"
kind: feature
status: done
priority: high
effort: S
tags: [cli, security, agents]
---

# Implement pyrite-read CLI Entry Point

Complete the read-only CLI tier for agent safety. The three-CLI architecture (pyrite, pyrite-read, pyrite-admin) is described in documentation and help text, but `pyrite-read` is not registered as a `[project.scripts]` entry point in `pyproject.toml`.

## Scope

- Create `pyrite/read_cli.py` with a Typer app exposing only read operations: `get`, `list`, `search`, `backlinks`, `tags`, `timeline`
- Register `pyrite-read = "pyrite.read_cli:main"` in `pyproject.toml` `[project.scripts]`
- Exclude all write operations (create, update, delete, link) and all admin operations (index, kb, repo, auth)
- Include plugin read-only CLI commands (if plugin declares them)
- Add tests verifying pyrite-read cannot perform writes

## Rationale

AI agents (Claude Code, Gemini CLI, Codex) use the CLI as their primary Pyrite interface. A read-only binary prevents accidental writes during research/exploration workflows. This completes the three-tier access model that MCP already enforces at construction time.

## References

- [CLI System](../../components/cli-system.md)

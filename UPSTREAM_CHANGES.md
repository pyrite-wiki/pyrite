# Relationship to Upstream

Pyrite began as a fork of [joshylchen/zettelkasten](https://github.com/joshylchen/zettelkasten), a single-KB AI-powered note-taking tool. It has since diverged substantially — the package was renamed to `pyrite`, the module tree from `zettelkasten`/`cascade_research` to `pyrite`, and the architecture was rebuilt around multi-KB, plugins, and agent integration.

This document preserves the history of that divergence for attribution and context.

## What Came from Upstream

The original project contributed:

- The core idea of markdown files with YAML frontmatter as source of truth
- The CEQRC workflow concept (Capture → Explain → Question → Refine → Connect)
- The initial single-KB CLI and OpenAI integration
- Basic note CRUD operations

These concepts are preserved in the zettelkasten extension (`extensions/zettelkasten/`).

## What Pyrite Replaced or Rebuilt

Essentially everything else. The scope of changes makes this closer to a rewrite than a fork: the package and module tree were renamed to `pyrite`, and the architecture was rebuilt around multiple knowledge bases, plugins, several interfaces (CLI, REST API, MCP server, web frontend) and agent integration. For what Pyrite is today, see [README.md](README.md) and `docs/`; this file keeps only the history.

## Syncing with Upstream

Given the degree of divergence, syncing is unlikely to be practical. If upstream introduces interesting ideas, cherry-picking specific concepts (not code) into the zettelkasten extension is the most realistic approach.

The upstream remote can still be added for reference:

```bash
git remote add upstream https://github.com/joshylchen/zettelkasten.git
git fetch upstream
git log upstream/main --oneline
```

## Attribution

The original Zettelkasten AI Assistant was created by Josh Chen. Pyrite is developed by Mark Ramm. Both projects are MIT licensed.

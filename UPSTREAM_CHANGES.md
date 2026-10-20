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

Essentially everything else. The scope of changes makes this closer to a rewrite than a fork:

| Area | Upstream | Pyrite |
|------|----------|--------|
| Package name | `zettelkasten` | `pyrite` |
| Knowledge bases | Single KB | Multi-KB with cross-references |
| Entry types | Single `Note` model | 8 core types + plugin-defined types + YAML-defined custom types |
| Storage | In-memory search | SQLAlchemy ORM + SQLite FTS5 + Alembic migrations |
| Search | Basic text matching | FTS5 with BM25, semantic (embeddings), hybrid mode |
| Interfaces | Single CLI | CLI (read/write/admin), REST API, MCP server, SvelteKit frontend |
| MCP | 7 tools, single tier | 15+ tools across 3 permission tiers + prompts + resources |
| AI providers | OpenAI only | OpenAI, Anthropic, stub (abstraction layer) |
| Extensibility | None | Plugin protocol with 12 extension points |
| Schema system | Hardcoded | 4-layer resolution (config YAML → plugin → core defaults → empty) |
| Collaboration | None | GitHub OAuth, multi-user, workspace repos, git version tracking |
| Validation | None | Pydantic models + YAML field schemas with 10 field types |
| Content formats | Markdown only | JSON, Markdown, CSV, YAML via content negotiation |
| Tests | Minimal | 583 tests covering CRUD, FTS5, REST, MCP, plugins, migrations, security |

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

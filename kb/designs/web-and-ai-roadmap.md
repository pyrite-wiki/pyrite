---
id: web-and-ai-roadmap
type: design_doc
title: "Pyrite Web Application & AI Integration Roadmap"
status: active
author: markr
date: "2026-02-23"
reviewers: []
tags: [web, ai, roadmap, architecture]
lifecycle: archived
---

# Pyrite Web Application & AI Integration Roadmap

Two parallel tracks delivering Pyrite's vision: a web app that feels as good to write in as Obsidian, and AI workflows that make it a genuine research platform for both humans and agents.

## Track Overview

| Track | Goal | Surfaces |
|-------|------|----------|
| **UI Track** | Obsidian-quality web editor served from `pyrite serve` | Web browser |
| **AI Track** | AI-native research workflows across all surfaces | Claude Code, MCP clients, Web UI |

Phases are organized by deliverable value, not by track. Each phase delivers usable features from both tracks.

---

## Phase 1: Foundation ✅ COMPLETE

**Delivered:** A user can run `pyrite serve`, open a browser, browse entries, and create/edit with a real Markdown editor.

### UI Track (Done)
- SvelteKit + Vite + Tailwind CSS 4 frontend
- CodeMirror 6 Markdown editor with dark/light themes
- Sidebar navigation, KB switcher, entry list with pagination
- Entry view/edit with rendered Markdown + metadata
- Dashboard with stat cards
- `/api` prefix migration, JSON request bodies
- Static file serving with SPA fallback
- `pyrite serve` command with `--dev` and `--build` flags

### Testing (Done)
- Vitest: 36 unit/component tests
- Playwright: 15 E2E tests
- Backend pytest: all passing with `/api` prefix

---

## Phase 2: Linking, Navigation & AI Foundation ✅ COMPLETE

**Delivered:** Wikilinks, quick switcher, command palette, backlinks. Plus the AI foundation layer, Claude Code plugin, and structured data foundation.

### UI Track
- **Wikilinks with autocomplete** — `[[` triggers search, renders as clickable pills
- **Quick Switcher** (Ctrl+O) — fuzzy search entries via `/api/search`
- **Command Palette** (Cmd+K) — fuzzy-matched action list with fuse.js
- **Backlinks Panel** — entries linking to current entry
- **Split Panes** — CSS Grid resizable, two entries side-by-side
- **Templates System** — user-defined Markdown templates with variables
- **Starred/Pinned Entries** — bookmarks section in sidebar
- **Slash Commands** — `/` menu in editor for quick insertion
- **Global Keyboard Shortcuts** — shortcut manager for all actions

### Core Track
- **ruamel.yaml migration** — round-trip safe YAML for clean git diffs
- **Schema-as-Config** — rich field types in kb.yaml (text, number, date, select, object-ref, etc.)
- **Field validation** — automatic validation against declared field schemas

### AI Track
- **LLM Abstraction Service** — `pyrite/services/llm_service.py` with Anthropic + OpenAI SDK support, OpenRouter/Ollama via base_url
- **Claude Code Plugin** — `.claude-plugin/plugin.json` manifest, auto-discovery
- **MCP Prompts & Resources** — pre-built prompts and browsable KB resources for Claude Desktop/Cline
- **API Security Hardening** — per-request auth, rate limiting, CORS tightening

### New Backend Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/entries/titles` | Lightweight autocomplete |
| GET | `/api/entries/resolve` | Wikilink target resolution |
| GET | `/api/ai/status` | AI provider status |

### npm Dependencies
`fuse.js`

---

## Phase 3: Graph, Daily Notes, Research Skills ✅ COMPLETE

**Delivered:** Interactive knowledge graph, daily notes workflow, real-time multi-tab awareness. Plus research and investigation skills for Claude Code, and typed object references.

### UI Track
- **Knowledge Graph** — Cytoscape.js with cose-bilkent layout, color-coded nodes, filters
- **Local Graph** — per-entry mini graph (1-2 hops) in sidebar
- **Daily Notes** — Cmd+D opens today's note, mini calendar navigation, auto-create from template
- **WebSocket Multi-Tab** — change notifications across browser tabs
- **Tag Hierarchy** — nested tags with `/` separator, tree view in sidebar
- **Callouts/Admonitions** — `> [!info]` styled blocks in editor
- **Outline/TOC Panel** — auto-generated table of contents from headings

### Core Track
- **Typed Object References** — `object-ref` field type with `entry_refs` DB table and reverse lookups
- **Relation-enriched backlinks** — backlinks API includes object-ref sources with field names
- **Graph edges from object refs** — typed relation edges for Cytoscape visualization

### AI Track
- **Research Flow Skill** — structured methodology: gather → connect → analyze → synthesize
- **Investigation Skill** — entity investigation: identify → collect → map → track → assess
- **`/research` command** — start a structured research session
- **`/investigate` command** — pull together everything known about an entity
- **`/daily` command** — open today's daily note

### New Backend Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/graph` | Graph nodes + edges for Cytoscape |
| GET | `/api/daily/{date}` | Get or auto-create daily note |
| WS | `/ws` | WebSocket change notifications |

### npm Dependencies
`cytoscape`, `cytoscape-cose-bilkent`

---

## Phase 4: WYSIWYG, Settings, Web AI Features ✅ COMPLETE

**Delivered:** Dual editor mode, settings persistence, timeline visualization, plugin UI hooks. Plus AI features in the web UI, and schema-driven forms.

### UI Track
- **Tiptap WYSIWYG** — toggle between CodeMirror source and Tiptap WYSIWYG, shared Markdown string
- **Timeline Visualization** — CSS-based visual timeline with date markers and filtering
- **Settings Page** — theme, editor preferences, daily note template, default KB
- **Plugin UI Extension Points** — plugins register sidebar items, commands, entry type renderers
- **Block References** — `![[entry#section]]` transclusion in editor
- **Schema-driven forms** — auto-generated entry forms from field schemas (select dropdowns, date pickers, entry pickers for object-refs)
- **Record layout** — form-first view for structured data objects (contacts, tasks, bookmarks)

### AI Track
- **AI Provider Settings** — BYOK configuration in settings page (provider, model, API key)
- **Summarize Entry** — button on entry view, streaming summary generation
- **Auto-Tag** — suggest tags from existing vocabulary
- **Suggest Links** — find entries that should be wikilinked
- **Pyrite Dev Workflow Skill** — TDD, backlog process, architecture patterns for contributors
- **`/review-kb` command** — audit KB health, orphaned entries, coverage gaps

### New Backend Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/plugins` | Plugin UI manifests |
| GET/PUT | `/api/settings` | User preferences |
| POST | `/api/ai/summarize` | Summarize entry |
| POST | `/api/ai/auto-tag` | Suggest tags |
| POST | `/api/ai/suggest-links` | Suggest wikilinks |

### npm Dependencies
`@tiptap/core`, `@tiptap/starter-kit`, `@tiptap/extension-link`, `@tiptap/extension-placeholder`, `@tiptap/extension-task-list`, `@tiptap/extension-task-item`, `@tiptap/pm`

---

## Phase 5: Advanced Features, Chat, Polish ✅ COMPLETE

**Delivered:** Chat with KB (RAG), version history, collections (replacing dataview/database views), import/export. Production polish.

### UI Track
- **Dataview-Style Queries** — embedded queries in entries rendering as live tables
- **Database Views** — table, kanban board, gallery modes for entry lists
- **Import/Export** — Obsidian vault import, Notion export import, CSV, Markdown zip export
- **Version History** — git-based entry history with diff view and restore

### AI Track
- **Chat Sidebar** — RAG: retrieve relevant entries → LLM conversation with citations
- **Smart Search** — query expansion + semantic search (upgrade existing)
- **Generate Entry** — "Create an entry about X" → structured entry with frontmatter

### New Backend Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/entries/{id}/versions` | Git-based version history |
| POST | `/api/import` | Import from external formats |
| GET | `/api/export` | Export in various formats |
| POST | `/api/ai/chat` | RAG chat with KB context |
| POST | `/api/ai/generate` | Generate entry from prompt |

---

## Pyrite 0.3 Milestone — COMPLETE

**Delivered 2026-02-25.** Collections (phases 1–3), block references (phase 1), semantic search for plugins, extension builder skill. Key deliverables:

- **Collections Phase 1:** `__collection.yaml` parsing, folder-backed collections, list/table views, `/collections` routes
- **Collections Phase 2:** Query DSL parser (`type:x status:y tags:z`), cached evaluation, virtual collections, `pyrite collections` CLI commands
- **Collections Phase 3:** Kanban view with drag-and-drop, gallery view with card grid, PATCH endpoint for field updates
- **Block References Phase 1:** Block table (migration v5), markdown block extraction, `[[entry#heading]]` links, `GET /api/entries/{id}/blocks`
- **Extension Builder Skill:** Claude Code skill for scaffolding new pyrite extensions from description
- **Semantic Search for Plugins:** `PluginContext.search_semantic()` with vector search fallback to FTS5

See [ADR-0011](../adrs/0011-collections-and-views.md) (collections — status: accepted) and [ADR-0012](../adrs/0012-block-references-and-transclusion.md) (block references — status: accepted).

---

## Future (Post-Phase 5)

Items in `kb/backlog/future-ideas/`:

- AI Writing Assistant in editor (select text → summarize/expand/rewrite)
- Offline support with IndexedDB + service worker
- Web Clipper (bookmarklet + in-app) ✅ Done
- Entry Aliases ✅ Done
- Canvas/Whiteboard view
- Git sync conflict resolution UI
- Semantic search for plugins (vector embeddings) ✅ Done in 0.3

---

## Architecture Decisions

| Decision | ADR | Summary |
|----------|-----|---------|
| Git-native storage | ADR-0001 | Markdown + YAML frontmatter in git repos |
| Plugin system | ADR-0002 | Entry points, protocol class, registry |
| Two-tier durability | ADR-0003 | Content (git) vs engagement (SQLite) |
| Folder-per-author | ADR-0004 | Permission model via directory structure |
| MCP three-tier tools | ADR-0006 | Read/write/admin tool tiers |
| AI integration | ADR-0007 | Three surfaces, BYOK, Anthropic+OpenAI SDKs |
| Structured data | ADR-0008 | Schema-as-config, rich field types, ruamel.yaml, object refs |

## Technology Stack

| Layer | Technology | Added In |
|-------|-----------|----------|
| Frontend | Svelte 5 + SvelteKit + Vite | Phase 1 |
| Editor (source) | CodeMirror 6 | Phase 1 |
| Editor (WYSIWYG) | Tiptap | Phase 4 |
| Graph | Cytoscape.js + cose-bilkent | Phase 3 |
| Styling | Tailwind CSS 4 | Phase 1 |
| Backend | FastAPI + SQLite + FTS5 | Pre-existing |
| LLM | Anthropic SDK + OpenAI SDK | Phase 2 |
| MCP | MCP 2024-11-05, stdio | Pre-existing |
| Real-time | WebSocket | Phase 3 |
| Testing | pytest + Vitest + Playwright | Phase 1 |

## Verification

After each phase:
1. `cd web && npm run build` produces `web/dist/`
2. `pyrite serve` starts on :8088 and serves the SPA
3. `pytest tests/` — all backend tests pass
4. `cd web && npm run test:unit` — all Vitest tests pass
5. `cd web && npm run test:e2e` — all Playwright tests pass
6. New AI endpoints return correct data (or 503 if no provider configured)
7. Claude Code skills activate correctly when invoked
8. MCP tools/prompts/resources work in Claude Desktop

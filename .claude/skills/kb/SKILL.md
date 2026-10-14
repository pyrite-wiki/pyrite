# Pyrite Knowledge Base Skill

Use when working with pyrite knowledge bases: searching, reading, creating, updating, or deleting entries. Also covers index management, KB administration, and plugin CLI sub-commands.

## Quick Reference

One entry point: `pyrite`. Rich table output. Run `pyrite index build` before first use.

```bash
pyrite --help                                   # All commands + plugin sub-apps
pyrite search "plugin system"                   # Full-text search (FTS5)
pyrite get <entry-id>                           # Retrieve entry by ID
pyrite list                                     # Table of KBs with entry counts
pyrite create --kb=pyrite --type=note --title="My Note" --body="Content"
pyrite sw adrs                                  # List ADRs (software KB plugin)
```

Plugin sub-apps: `pyrite sw` (software), `pyrite zettel` (zettelkasten), `pyrite wiki` (encyclopedia), `pyrite social`.

---

## Reading from the KB

### Search

```bash
pyrite search "migration"                       # Full-text search
pyrite search "migration" --kb=pyrite           # Specific KB
pyrite search "auth*" --type=component          # Prefix + type filter
pyrite search "storage" --tag=architecture      # Tag filter
pyrite search "query" --expand                  # AI-powered query expansion
```

FTS5 syntax: simple terms, `"exact phrase"`, `auth*` prefix, `storage -legacy` exclusion, `AND` boolean.

### Get, list, timeline, tags, backlinks

```bash
pyrite get <entry-id>                           # Search all KBs
pyrite get <entry-id> --kb=pyrite               # Specific KB
pyrite list                                     # All KBs with entry counts
pyrite timeline                                 # All events
pyrite timeline --from=2025-01-01 --to=2025-12-31 --min-importance=7 --limit=20
pyrite tags                                     # All tags with counts
pyrite tags --kb=pyrite --limit=20              # Filter by KB
pyrite backlinks <entry-id> --kb=pyrite         # Entries linking to this one
```

### Plugin read commands

```bash
pyrite sw adrs                                  # List ADRs
pyrite sw backlog                               # List backlog items
pyrite sw standards                             # List standards
pyrite sw components                            # List components
```

---

## Writing to the KB

### Create with core types

```bash
pyrite create --kb=pyrite --type=note --title="My Note" --body="Content here"
pyrite create --kb=pyrite --type=event --title="Release v1.0" --date=2026-02-22 --importance=8
pyrite create --kb=pyrite --type=person --title="Jane Smith" --tags="team,backend"
```

### Create with plugin types (use --field for extension-specific fields)

```bash
# ADR
pyrite create --kb=pyrite --type=adr --title="Use Typer for CLI" \
  --field adr_number=7 --field status=proposed --field date=2026-02-22

# Backlog item
pyrite create --kb=pyrite --type=backlog_item --title="Add caching layer" \
  --field kind=feature --field status=proposed --field priority=high --field effort=M

# Runbook
pyrite create --kb=pyrite --type=runbook --title="Deploy to Production" \
  --field runbook_kind=howto --field audience=developers

# Standard
pyrite create --kb=pyrite --type=standard --title="Error Handling" \
  --field category=python --field enforced=true

# Component
pyrite create --kb=pyrite --type=component --title="Auth Service" \
  --field kind=service --field path=pyrite/auth/ --field owner=alice
```

Entries are saved to the correct subdirectory automatically and indexed.

### Shortcuts and mutations

```bash
pyrite sw new-adr "Add Rate Limiting"           # Auto-numbered ADR (title is positional)
pyrite update <entry-id> --kb=pyrite --title="New Title"
pyrite update <entry-id> --kb=pyrite --body="Updated body" --tags="new,tags" --importance=8
pyrite delete <entry-id> --kb=pyrite             # With confirmation
pyrite delete <entry-id> --kb=pyrite --force     # Skip confirmation
```

---

## Admin Commands

### Index management

```bash
pyrite index build                              # Full rebuild (required before search works)
pyrite index build --kb=pyrite                  # Rebuild single KB
pyrite index sync                               # Incremental sync (fast)
pyrite index stats                              # Entry/tag/link counts
pyrite index health                             # Check for stale/missing/unindexed
```

After creating or updating entries, the index updates automatically. Run `index build` when setting up a new environment or after bulk file changes.

### KB management

```bash
pyrite kb list                                  # List configured KBs
pyrite kb add ./path/to/kb --name=myproject --type=software
pyrite kb remove myproject
pyrite kb discover                              # Auto-discover KBs by kb.yaml
pyrite kb validate pyrite                       # Validate KB entries
```

### Other admin

```bash
pyrite repo add ./path --name=myrepo --discover # Repository management
pyrite repo remove myrepo
pyrite auth status                              # Check GitHub auth
pyrite auth whoami                              # Current identity
pyrite auth github-login                        # OAuth flow
pyrite mcp                                      # Start MCP server (stdio, write tier)
pyrite mcp-setup                                # Register with Claude Code / Claude Desktop
```

---

## This Project's KB

The `kb/` directory is a software-type KB:

```
kb/
  kb.yaml              # KB config (name: pyrite, kb_type: software)
  adrs/                # Architecture Decision Records
  designs/             # Design documents
  standards/           # Coding standards and conventions
  components/          # Module/service documentation
  backlog/             # Feature requests, bugs, tech debt
  runbooks/            # How-to guides and procedures
```

### Entry types and fields

| Type | Key Fields | Subdirectory |
|------|-----------|--------------|
| `adr` | adr_number, status, deciders, date | `adrs/` |
| `design_doc` | status, reviewers, date, author | `designs/` |
| `standard` | category, enforced | `standards/` |
| `component` | kind, path, owner, dependencies | `components/` |
| `backlog_item` | kind, status, priority, assignee, effort | `backlog/` |
| `runbook` | runbook_kind, audience | `runbooks/` |

Core types also available: `note`, `person`, `organization`, `event`, `document`, `topic`, `relationship`, `timeline`.

### Status enums

- **ADR**: `proposed` -> `accepted` -> `deprecated` | `superseded`
- **Backlog**: `proposed` -> `accepted` -> `in_progress` -> `done` | `wont_fix`
- **Backlog priority**: `critical`, `high`, `medium`, `low`
- **Backlog effort**: `XS`, `S`, `M`, `L`, `XL`

---

## Project Processes

### Backlog Management

The backlog lives in `kb/backlog/` with three zones:

| Location | Contents |
|----------|----------|
| `kb/backlog/*.md` | Active priority items (~top 30) |
| `kb/backlog/done/` | Completed items |
| `kb/backlog/future-ideas/` | Lower-priority items for later |

**The backlog has no index file.** `pyrite sw backlog` reads frontmatter
directly and is the source of truth — there is no `BACKLOG.md` to keep in sync.

### When completing a feature:

1. Update the item's status via CLI (validates + syncs index automatically):
   ```bash
   pyrite update <item-id> -k <kb> -f status=completed
   ```
   **Never hand-edit YAML frontmatter for field updates** — the CLI validates values and keeps the index in sync. Hand-editing skips validation and leaves the index stale.
2. Move the file from `kb/backlog/` to `kb/backlog/done/` with `git mv`
3. Re-sync the index: `pyrite index sync`
4. If the work revealed new tech debt, cleanup needs, or follow-on features:
   - Create new backlog items via CLI:
     ```bash
     pyrite create -k <kb> -t backlog_item --title "..." -b "..."
     ```
   - Place in `kb/backlog/` if high priority, or `kb/backlog/future-ideas/` if low

### When promoting a future idea:

```bash
git mv kb/backlog/future-ideas/<id>.md kb/backlog/
pyrite update <id> -k <kb> -f priority=<high|medium>
pyrite index sync
```

### Backlog item file format:

```yaml
---
type: backlog_item
title: "Human-readable title"
kind: feature | improvement | bug
status: proposed | in_progress | completed
priority: high | medium | low
effort: S | M | L | XL
tags: [relevant, tags]
---

Description of the work, context, approach, and dependencies.
```

---

## Software KB Research Workflows

These workflows apply when building or improving a **software-type** knowledge base (patterns, best practices, conventions for a technology). They're distinct from the investigation skill which is for researching entities and events.

### Workflow 1: Gap Analysis

Run this before researching to identify what the KB is missing.

**Step 1 — Inventory what exists:**

```bash
pyrite search "*" --kb=<name> --limit=200 --format=json   # All entries
pyrite tags --kb=<name>                                     # Tag coverage
pyrite search "*" --kb=<name> --mode=semantic --limit=50    # Semantic clusters
```

Read a sample of entries to assess depth and quality.

**Step 2 — Identify structural gaps.** For a software KB, check coverage against these categories:

| Category | What to look for |
|----------|-----------------|
| Core concepts | Are the fundamental primitives documented? (e.g. for Svelte: runes, components, reactivity) |
| Patterns | Common usage patterns, composition techniques, state management approaches |
| Anti-patterns | What NOT to do, common mistakes, performance traps |
| Migration | How to move from older versions or competing tools |
| Integration | How this technology connects to others (build tools, testing, deployment) |
| Edge cases | Gotchas, browser quirks, SSR vs CSR differences, TypeScript nuances |
| Architecture | How to structure larger applications, module boundaries, scaling patterns |

**Step 3 — Check cross-references.** Good KB entries link to each other. Look for:

```bash
# Find orphan entries (no links in or out)
pyrite search "*" --kb=<name> --format=json | # check for entries with no wikilinks in body
```

Entries that reference concepts not yet in the KB should be noted — these are implicit "wanted pages."

**Step 4 — Produce a gap report.** Create an entry summarizing findings:

```bash
pyrite create --kb=<name> --type=note --title="Gap Analysis: <topic>" \
  --body="<gap report>" --tags="meta,gap-analysis"
```

The gap report should list: (a) missing topics with priority, (b) existing entries that need deepening, (c) missing cross-references between entries.

---

### Workflow 2: Research for Software KBs

The goal is entries that teach something an experienced developer **wouldn't already know** from reading the official docs. Generic documentation has no value — agents (and humans) already have access to docs.

**What makes a KB entry valuable:**

1. **Non-obvious behavior** — edge cases, surprising interactions, things that only surface in production
2. **Decision frameworks** — when to use X vs Y, with concrete trade-offs (not just "it depends")
3. **Patterns that combine features** — how multiple concepts work together in real applications
4. **Migration knowledge** — specific gotchas when moving between versions or approaches
5. **Performance implications** — what's actually expensive, what's cheap, when it matters

**Research process:**

1. **Start from the gap analysis** — work on the highest-priority missing topics first
2. **Go deep, not wide** — one thorough entry beats five shallow ones
3. **Use web search for novel information** — official docs, GitHub issues, release notes, RFCs, blog posts from framework authors. Look for information from the last 6 months especially.
4. **Test claims when possible** — if you can verify behavior with a code example, do so
5. **Always include "when NOT to use"** — anti-patterns are as valuable as patterns

**Research agent prompt template:**

When launching parallel research agents, give each a focused scope:

```
Research [specific topic] for the [technology] KB.

Focus on NON-OBVIOUS information that goes beyond official documentation:
- Edge cases and gotchas
- Performance implications
- When to use vs when NOT to use
- How it interacts with [related feature]
- Common mistakes in production

Create entries using: pyrite create --kb=<name> --type=standard --title="..." --body="..." --tags="..."

Every entry MUST include [[wikilinks]] to related entries that exist or should exist.
Entries that reference a topic not yet in the KB create "wanted pages" — this is good,
it maps the territory the KB should eventually cover.
```

---

### Workflow 3: Entry Best Practices

**Structure of a good software KB entry:**

```markdown
---
type: standard
title: "Descriptive Title — Not Generic"
tags: [specific, relevant, tags]
---

## Overview

One paragraph: what this is and why it matters. State the key insight upfront.

## Core Concept / Pattern

The meat. Code examples with comments explaining WHY, not just WHAT.
Include the non-obvious parts — the things you'd learn after a week of using it.

## When to Use / When NOT to Use

Concrete decision criteria. Not "it depends" — give specific scenarios.

## Gotchas

Numbered list of things that will bite you. Each with a code example if applicable.

## Related

- [[related-entry-1]] — how it connects
- [[related-entry-2]] — how it connects
```

**Entry quality checklist:**

| Criterion | Test |
|-----------|------|
| **Non-obvious** | Would a developer learn this from 10 minutes with the docs? If yes, cut it. |
| **Specific** | Does it use concrete code examples, not abstract descriptions? |
| **Actionable** | Can a developer apply this immediately? |
| **Cross-linked** | Does it `[[wikilink]]` to at least 2 other entries (existing or wanted)? |
| **Anti-patterns included** | Does it show what NOT to do, not just what to do? |
| **Trade-offs stated** | Are costs and benefits explicit, not just benefits? |

**Wikilink discipline:**

Every entry should contain `[[wikilinks]]` to related topics. If the target doesn't exist yet, that's fine — it creates a "wanted page" that maps what the KB should eventually cover. This is how the KB grows organically.

```markdown
## Good: creates a web of knowledge
$state creates deeply reactive proxies (see [[svelte-5-runes-state]]).
Unlike [[stores-vs-runes-migration-guide|the old store pattern]], runes
track dependencies at read time. Be careful with [[ssr-state-leaks|SSR
state leaks]] when using module-level state.

## Bad: isolated entry with no connections
$state creates deeply reactive proxies. Runes track dependencies at
read time. Be careful with SSR.
```

**Naming conventions for entry IDs:**

- Use lowercase kebab-case: `svelte-5-runes-state`
- Prefix with technology for multi-KB clarity: `sveltekit-form-actions`
- Be specific: `sveltekit-error-handling` not `errors`
- Group related entries with common prefixes: `svelte-5-runes-state`, `svelte-5-runes-derived`, `svelte-5-runes-effect`

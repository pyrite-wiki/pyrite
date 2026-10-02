---
id: adr-0011
type: adr
title: "Collections, Folder Metadata, and Views"
adr_number: 11
status: accepted
deciders: ["markr"]
date: "2026-02-23"
tags: [architecture, schema, views, collections, ui]
---

# ADR-0011: Collections, Folder Metadata, and Views

## Context

Pyrite stores entries as flat files in folders. Folders are currently a storage convenience — they hold files organized by type (`events/`, `people/`) but have no metadata, no identity, and no behavior. You can't search for a folder, link to it, describe it, or define how its contents should be displayed.

Meanwhile, every interesting knowledge base application (Notion, Anytype, Obsidian Dataview, Roam) has a concept of **collections** — structured groups of objects with a defined view. Notion calls them "databases." Anytype calls them "sets" and "collections." Obsidian Dataview creates them from queries. They're the primary way users organize and browse knowledge.

Pyrite needs collections, but they should emerge from the file system rather than being imposed on top of it.

### The insight: folders already are collections

A folder is a group of related files. It just lacks:

1. **Identity** — it's not an addressable object in the KB
2. **Metadata** — no title, description, tags, schema overrides
3. **View preferences** — no default sort, grouping, or display mode
4. **Queryability** — you can't search for folders or link to them

Adding a `__collection.yaml` file to any folder turns it into a first-class collection object without changing anything about how entries work.

### Virtual collections fill the other half

Not all collections map to folders. You also want:

- "All backlog items" (entries of type `backlog_item` across any folder)
- "This week's meetings" (entries matching a date query)
- "Immigration policy network" (entries matching a tag + type filter)

These are **virtual collections** — defined by a query rather than a folder path. They should work identically to folder collections in terms of views, embedding, and linking.

### Domain-specific collection types drive real workflows

Collections become powerful when they carry domain semantics:

| Domain | Collection Type | View | Behavior |
|--------|---------------|------|----------|
| **Investigative journalism** | Investigation Package | timeline + source network + findings | AI can summarize evidence chain |
| **Software development** | Backlog | kanban board by status | drag-and-drop status changes |
| **Software development** | Sprint | progress bar + burndown | filtered by sprint tag |
| **Social KB** | Post with comments | thread view + engagement | reply creates linked entry |
| **Encyclopedia** | Article with talk page | split: article + discussion | separate edit workflows |
| **Research** | Literature review | table with citation metadata | sort by relevance, date, author |
| **Project management** | Todo list | checklist with due dates | completion tracking |

These aren't separate features — they're all "a collection of entries with a specific view and optional behavior." The plugin system should define collection types the same way it defines entry types.

## Decision

### 1. `__collection.yaml` makes folders into collection objects

Any folder in a KB can contain a `__collection.yaml` file. This file:

- Gives the folder an **identity** (becomes an indexable, linkable object)
- Defines **metadata** (title, description, tags, type)
- Specifies **view preferences** (default view, sort, grouping, filters)
- Can override **schema** for entries in that folder

```yaml
# kb/investigations/jan6/__collection.yaml
type: collection
title: "January 6th Investigation"
description: "Evidence, witnesses, and timeline for the Capitol breach"
tags: [investigation, jan6, capitol]
icon: folder-search

# What's in this collection (implicit: files in this folder)
source: folder

# View preferences
view:
  default: timeline
  sort: date_desc
  group_by: entry_type
  card_fields: [date, importance, tags]

# Schema overrides for entries in this folder
schema:
  evidence_status:
    type: select
    options: [unverified, corroborated, disputed, confirmed]
  source_reliability:
    type: number
    constraints: { min: 1, max: 5 }
```

**The `__` prefix convention:** Files prefixed with `__` are collection-level metadata, not entries. The indexer skips them as entries but reads them as collection definitions. This is analogous to Python's `__init__.py` or Node's `__tests__/` — a well-understood namespace convention.

### 2. Virtual collections are entries with `source: query`

A virtual collection is a regular entry (any `.md` or `.yaml` file) whose collection definition uses a query instead of a folder:

```yaml
# kb/views/all-backlog-items.md
---
type: collection
title: "Backlog"
tags: [meta, project-management]

source:
  query:
    entry_type: backlog_item
  sort: priority_desc

view:
  default: table
  columns: [title, status, priority, effort, tags]
  group_by: status
---

The complete backlog for the Pyrite project, organized by status.
```

```yaml
# kb/views/immigration-network.md
---
type: collection
title: "Immigration Policy Network"

source:
  query:
    tags: [immigration]
    entry_type: [person, organization, event]

view:
  default: graph
  node_color_by: entry_type
---
```

Virtual collections can have a Markdown body (description, notes, analysis) just like any other entry. They're documents that happen to also define a dynamic view of other entries.

### 3. Collection types are defined in kb.yaml and by plugins

The `collection` entry type is built-in but extensible. Plugins and kb.yaml can define **collection subtypes** with specific view defaults and behaviors:

```yaml
# kb.yaml
types:
  investigation:
    extends: collection
    description: "Investigation package with evidence tracking"
    fields:
      status:
        type: select
        options: [open, active, suspended, closed, published]
      lead_researcher:
        type: object-ref
        target_type: person
    view:
      default: timeline
      available: [timeline, table, graph, kanban]
    ai_instructions: >
      When adding evidence to this investigation, always set
      evidence_status and source_reliability on the new entry.

  storyboard:
    extends: collection
    description: "Software development story board"
    fields:
      sprint:
        type: text
    view:
      default: kanban
      kanban_field: status
      available: [kanban, table, list]

  discussion:
    extends: collection
    description: "Discussion thread (talk page, comments)"
    fields:
      parent_entry:
        type: object-ref
        description: "The entry this discussion is about"
    view:
      default: thread
      sort: created_at_asc
```

### 4. Collections are embeddable in other entries

A collection reference in a Markdown body renders as an inline view:

```markdown
## Investigation Progress

Here are the key witnesses we've identified:

![[jan6/witnesses]]{ view: table, columns: [name, role, testimony_status] }

And the timeline of events:

![[jan6]]{ view: timeline, filter: { entry_type: event } }
```

The `![[collection-id]]{ options }` syntax extends the existing wikilink transclusion syntax (backlog #17) with view parameters. Without parameters, it uses the collection's default view.

This is how Notion's "linked databases" and Anytype's "inline sets" work — you embed a live, filtered view of a collection inside any page.

### 5. The `__` file convention

Files starting with `__` in a KB folder are **collection infrastructure**, not entries:

| File | Purpose |
|------|---------|
| `__collection.yaml` | Collection identity and metadata |
| `__views.yaml` | Saved view configurations (optional, for multiple named views) |
| `__schema.yaml` | Schema overrides for entries in this folder (optional, alternative to inline in `__collection.yaml`) |

The indexer:
- **Does not** index `__*` files as entries
- **Does** read `__collection.yaml` to create a collection object in the index
- **Does** apply `__schema.yaml` overrides during validation of entries in that folder

### 6. View types

Collections support these view types, implemented progressively:

| View | Renders as | Best for |
|------|-----------|----------|
| `list` | Simple title list with snippets | Quick browsing |
| `table` | Sortable/filterable columns | Structured data, backlogs |
| `kanban` | Columns grouped by a field | Status tracking, workflows |
| `gallery` | Card grid with preview | Visual browsing, people |
| `timeline` | Chronological axis | Events, investigations |
| `thread` | Linear conversation | Discussions, comments |
| `graph` | Node-link diagram | Relationships, networks |
| `calendar` | Month/week grid | Date-based entries |

Plugins can register additional view types. Each view type is a Svelte component that receives a list of entries and view configuration.

## Implementation Order

### Phase 1: Foundation (M effort)
1. Define `collection` as a built-in entry type in core types
2. Implement `__collection.yaml` parsing in `KBRepository` and `IndexManager`
3. Add `source: folder` collections — index folder metadata as collection entries
4. Collection CRUD via existing KBService (collections are just entries)
5. Basic `list` and `table` views in the web frontend

### Phase 2: Virtual Collections (M effort)
6. `source: query` parsing and execution via the index
7. Virtual collection entries (`.md` files with collection frontmatter)
8. Query DSL: `entry_type`, `tags`, `date_from/to`, `field` comparisons
9. Collection results available via API (`GET /api/collections/{id}/entries`)

### Phase 3: Rich Views (L effort)
10. `kanban` view with drag-and-drop field updates
11. `gallery` view with card layout
12. `timeline` view (extend existing timeline component)
13. `thread` view for discussion-style collections
14. View configuration saved per-user (engagement layer)

### Phase 4: Embedding and Composition (M effort)
15. `![[collection-id]]{ view options }` transclusion syntax
16. Inline collection rendering in the Markdown editor
17. Collection-in-collection nesting

### Phase 5: Plugin Collection Types (S effort)
18. `extends: collection` in kb.yaml type definitions
19. Plugin-defined view types (custom Svelte components)
20. AI instructions for collection management

## Consequences

### Positive

- **Folders become meaningful** — every folder can be a searchable, linkable, describable object
- **Zero migration** — existing folders work unchanged; `__collection.yaml` is opt-in
- **Unifies multiple backlog items** — #28 (Dataview), #29 (Database Views), #43 (Display Hints), and parts of #17 (Transclusion) all become aspects of collections
- **Domain flexibility** — investigation packages, kanban boards, discussion threads, and wiki talk pages are all "collection + view type," not separate features
- **Plugin extensible** — new collection types and view types via the existing plugin system
- **AI-friendly** — collections have descriptions and AI instructions; agents can create, populate, and query collections through the same CRUD interface as entries
- **Git-friendly** — `__collection.yaml` is a plain file in the repo, diffs cleanly, merges predictably

### Negative

- **Schema override complexity** — folder-level schema overrides add a third layer (KB → folder → entry) that validation must resolve
- **Query language design** — virtual collections need a query DSL; this is a design surface that must be kept simple enough for YAML while powerful enough to be useful
- **View state** — saved view configurations (sort, filter, column widths) are per-user engagement data, adding to the two-tier durability model
- **Indexing cost** — collections add objects to the index; virtual collections require query execution at render time

### Risks

- **Over-engineering** — the collection type system could become as complex as Notion's, which would be bad for a file-first tool. Mitigation: keep the core simple (folder + query + view), let plugins add domain complexity.
- **Performance** — virtual collections with complex queries on large KBs could be slow. Mitigation: cache collection results, add query limits, index common query patterns.

## Related

- [ADR-0008: Structured Data and Schema](0008-structured-data-and-schema.md) — field types, validation, kb.yaml schema
- [ADR-0009: Type Metadata and AI Instructions](0009-type-metadata-and-plugin-documentation.md) — display hints, AI instructions per type
- [ADR-0010: Content Negotiation](0010-content-negotiation-and-format-support.md) — format-aware rendering of collection results
- Backlog #28: [Dataview-Style Queries](../backlog/done/dataview-queries.md) — subsumed by virtual collections
- Backlog #29: [Database Views](../backlog/done/database-views.md) — subsumed by collection view types
- Backlog #43: [Display Hints for Types](../backlog/done/display-hints-for-types.md) — foundation for view configuration
- Backlog #17: [Block References and Transclusion](../backlog/done/block-references.md) — collection embedding extends transclusion

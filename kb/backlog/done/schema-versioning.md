---
id: schema-versioning
title: "Schema Versioning and Migration"
type: backlog_item
tags:
- feature
- schema
- migration
- core
kind: feature
priority: high
effort: M
status: done
links:
- odm-layer
- intent-layer-guidelines-and-goals
- pyrite-ci-command
- roadmap
---

## Problem

Schemas evolve. You add a required field to a type, tighten a controlled vocabulary, or rename a field — and now hundreds of existing entries are technically invalid. `pyrite ci` will fail on entries that were fine yesterday. There's no migration story: no way to add defaults to existing entries, no way to distinguish "required for new entries" vs "required for all entries," and no way to track which schema version an entry was created under.

This matters especially for corporate teams and long-lived KBs where schema changes are inevitable.

## Relationship to ODM Layer

Schema versioning is **decoupled from the ODM layer** and ships independently (pre-0.8). The migration pattern (Ming-style on-load migration) hooks into the existing `KBRepository` and `IndexManager` load/save paths — no `DocumentManager` or `SearchBackend` abstraction required.

The ODM layer (see [[odm-layer]]) ships post-launch (0.9+) as a backend abstraction refactor. When it lands, the schema versioning hooks move from `KBRepository` into `DocumentManager` — a straightforward relocation, not a redesign.

See [ADR-0015 addendum](../../adrs/0015-odm-layer-and-schema-migration.md) for the rationale.

## Solution

### Schema Version Tracking

KB-level and per-type versioning in kb.yaml:

```yaml
name: my-kb
kb_type: journalism
schema_version: 3  # increments when any type changes

types:
  finding:
    version: 3
    fields:
      confidence:
        type: number
        required: true
        since_version: 2  # required for entries created at v2+
      evidence:
        type: multi-ref
        required: true
        since_version: 1
      methodology:
        type: string
        required: true
        since_version: 3
```

Entries track their schema version in frontmatter:

```yaml
---
id: finding-001
type: finding
_schema_version: 2
confidence: 0.85
evidence: [doc-001, doc-002]
# no 'methodology' — predates v3
---
```

### On-Load Migration (Ming Pattern)

When `KBRepository` loads an entry, it checks `_schema_version` against the current type version. If behind, the `MigrationRegistry` applies the migration chain:

```python
@migration_registry.register(type="finding", from_version=2, to_version=3)
def finding_v2_to_v3(entry_data: dict) -> dict:
    """Add methodology field with default."""
    if "methodology" not in entry_data:
        entry_data["methodology"] = "unspecified"
    return entry_data
```

Migrations are registered by core code and extensions via the plugin protocol's `get_migrations()` method.

### Implementation

The `MigrationRegistry` and version tracking hook into existing code paths:

- **`KBRepository.load()`** — after parsing frontmatter, check `_schema_version` against current type version, apply migration chain if behind
- **`KBRepository.save()`** — stamp `_schema_version` with current type version
- **`IndexManager.index_entry()`** — index the migrated version (entry in the index is always current)
- **`KBSchema.validate_entry()`** — use `since_version` to distinguish legacy entries from new violations

No new abstraction layers. The migration registry is a standalone module (`pyrite/schema/migrations.py` or similar) that `KBRepository` calls during load.

### Migration Commands

```bash
# Show what would change
pyrite schema diff --from 2 --to 3

# Dry-run migration — forces load of every entry, reports what would change
pyrite schema migrate --kb research --dry-run

# Apply migration — forces load + save of every entry
# On-load migration does the actual work; save writes migrated files + updates index
pyrite schema migrate --kb research
# Result: "247 entries checked, 31 migrated, 0 errors"
# git diff shows exactly what changed — reviewable before commit

# Validate at specific version
pyrite ci --schema-version 2  # lenient mode for legacy entries
```

Because files in git are the source of truth, migration produces a reviewable diff. Run on a branch, review with `git diff`, merge when satisfied. This is something the original Ming/MongoDB pattern couldn't provide.

### Migration Strategies

- **Add default**: New required field gets a default value applied to existing entries
- **Backfill**: New required field gets computed from existing data (e.g., `confidence` derived from source count)
- **Soft require**: Field required for new entries, warning-only for legacy (`since_version`)
- **Rename**: Old field name maps to new name during migration
- **Vocabulary expansion**: New allowed values added — no migration needed
- **Vocabulary restriction**: Old values mapped to new values via migration table

### QA Integration

`pyrite ci` and QA validation should be schema-version-aware:
- Entries created before a requirement was added get warnings, not errors
- Migration status surfaced in `pyrite qa status`
- "Unmigrated entries" as a QA metric

## Prerequisites

- Schema-as-config (done)
- KBRepository load/save paths (done)

## Success Criteria

- kb.yaml supports `schema_version` and per-type `version` fields
- Entries track their `_schema_version` in frontmatter
- `MigrationRegistry` supports decorator-based migration registration
- Migration chain resolution (v1→v3 runs v1→v2 then v2→v3)
- Extensions register migrations via `get_migrations()` plugin protocol
- `pyrite schema migrate` forces load/save of all entries, producing reviewable git diff
- `pyrite ci` distinguishes legacy entries from new violations via `since_version`
- Migration is idempotent and safe (dry-run first)

## Launch Context

Must ship before 0.8. Without this, the first schema change after launch breaks every existing KB. The `since_version` pattern is the minimum — it lets schemas evolve without invalidating existing content. The on-load migration pattern (from Ming/Allura) means the system tolerates mixed schema versions gracefully — entries migrate when accessed, and `pyrite schema migrate` provides a clean "everything is migrated" checkpoint.

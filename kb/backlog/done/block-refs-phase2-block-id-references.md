---
id: block-refs-phase2-block-id-references
type: backlog_item
title: "Block References Phase 2: Block ID References and Resolution"
kind: feature
status: done
priority: high
effort: M
tags: [web, editor, linking, block-refs]
---

# Block References Phase 2: Block ID References and Resolution

Support `[[entry^block-id]]` syntax for referencing specific paragraphs/blocks.

## Scope

### Wikilink regex extension
- Extend regex to capture `^block-id` suffix: `[[entry^block-id]]`
- Combined syntax: `[[kb:entry#heading]]`, `[[kb:entry^block-id]]`
- Groups: (1) kb prefix, (2) target, (3) heading, (4) block-id, (5) display text

### Resolution endpoint
- Extend `GET /api/entries/resolve` to handle `#heading` and `^block-id` suffixes
- `GET /api/entries/{id}/blocks?block_id=...` returns specific block content
- Batch resolution for `resolve-batch` endpoint

### Frontend
- Autocomplete suggests block IDs after typing `^` within `[[entry^`
- Block reference links navigate to the specific block (scroll + highlight)
- Backlinks panel groups by block reference vs. entry reference

### Block ID management
- Explicit `^block-id` markers preserved during edit
- Auto-generated IDs shown in a "copy block reference" context menu action
- When a block is edited, its auto-generated ID changes — show warning in editor

## Depends on
- Block References Phase 1 (block storage and heading links)

## References

- [ADR-0012: Block References and Transclusion](../../adrs/0012-block-references-and-transclusion.md)
- Parent: [Block References and Transclusion](block-references.md) (#17)

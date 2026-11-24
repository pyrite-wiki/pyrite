---
id: block-refs-phase1-storage-and-heading-links
type: backlog_item
title: "Block References Phase 1: Block Storage and Heading Links"
kind: feature
status: done
priority: high
effort: M
tags: [web, editor, linking, block-refs]
---

# Block References Phase 1: Block Storage and Heading Links

Extract blocks (headings, paragraphs) from entries during indexing, store in a `block` table, and support `[[entry#heading]]` links.

## Scope

### Block extraction during indexing
- Parse markdown into blocks: headings, paragraphs, list items, code blocks
- Auto-generate block IDs from content hash (first 6 chars of SHA-256)
- Recognize explicit `^block-id` markers at end of paragraphs
- Store in `block` table: `entry_id, kb_name, block_id, heading, content, position, block_type`
- Re-extract on entry update (delete old blocks, insert new)

### Heading link support
- Extend wikilink regex to capture `#heading` suffix: `[[entry#heading]]`
- `GET /api/entries/{id}/blocks` endpoint — returns blocks for an entry, filterable by heading
- Frontend: `[[entry#heading]]` links navigate to the heading anchor within the entry page
- Backlinks panel shows heading-level backlinks when entry is referenced with `#heading`

### Migration
- Add `block` table via migration (schema from ADR-0012)
- `pyrite index sync` re-indexes all entries to populate blocks

## Does NOT include
- `^block-id` reference links (Phase 2)
- Transclusion / `![[...]]` rendering (Phase 3)
- Cross-KB block references (future)

## References

- [ADR-0012: Block References and Transclusion](../../adrs/0012-block-references-and-transclusion.md)
- Parent: [Block References and Transclusion](block-references.md) (#17)

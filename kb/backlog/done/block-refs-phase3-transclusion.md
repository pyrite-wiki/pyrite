---
id: block-refs-phase3-transclusion
type: backlog_item
title: "Block References Phase 3: Transclusion Rendering"
kind: feature
status: done
priority: medium
effort: L
tags: [web, editor, linking, block-refs]
---

# Block References Phase 3: Transclusion Rendering

Render `![[entry#heading]]` and `![[entry^block-id]]` as inline embedded content.

## Scope

### Transclusion syntax
- `![[entry#heading]]` — embed content under a heading (to next heading of same/higher level)
- `![[entry^block-id]]` — embed a single block/paragraph
- Leading `!` distinguishes transclusion from regular link

### TipTap editor rendering
- Custom TipTap node for transclusion blocks
- Fetches content from `/api/entries/{id}/blocks` endpoint on render
- Read-only embedded card with subtle border, source entry title/link
- Loading state while fetching, error state if block not found
- Cycle detection: max depth of 3, show warning if cycle detected

### Real-time updates
- WebSocket event `entry_updated` triggers re-fetch of transcluded content
- Only re-fetches transclusions that reference the updated entry

### Markdown rendering
- In markdown preview mode, transclusions render as blockquotes with source attribution
- Export: transclusions expand inline (full content) or render as links (configurable)

## Depends on
- Block References Phase 2 (block ID references)
- WebSocket infrastructure (done, Wave 6A)

## References

- [ADR-0012: Block References and Transclusion](../../adrs/0012-block-references-and-transclusion.md)
- Parent: [Block References and Transclusion](block-references.md) (#17)

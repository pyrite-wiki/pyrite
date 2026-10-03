---
id: type-the-service-receivers-navigation-cannot-resolve-so-every-caller-of-a-write
title: Type the service receivers navigation cannot resolve, so every caller of a write is findable
type: backlog_item
tags:
- refactor
importance: 5
kind: enhancement
status: proposed
priority: medium
effort: S
rank: 0
---

Retro 2026-10-03T16:10Z quality theme, approved by the maintainer.

The LSP hallway test (two agents, `~/.tool-feedback/pyright-lsp.md`) found that pyright's caller search silently drops callers whose receiver resolves as `Unknown`. Examples: `kb_service` in `pyrite/services/qa_fix_service.py` (:395, :447) and the four journalism `create_entry` calls (`extensions/journalism-investigation/.../plugin.py` :1129, :1166, :1203, :1239). B6 needs a complete list of write callers, and an agent using LSP gets an incomplete one with no warning.

## Acceptance
1. Each service receiver that is `Unknown` today in `pyrite/` and `extensions/` is annotated with its real type: plugin contexts, service attributes and constructor parameters. Use `TYPE_CHECKING` imports where a runtime import would cycle; the import-cycle hook must stay green.
2. A test lists every call site of `KBService.create_entry`, `update_entry`, `create`, `update` and `delete_entry` in `pyrite/` and `extensions/` (grep the AST, not text). It asserts each receiver's annotation resolves to `KBService`, so a new untyped caller fails it.
3. Parity: LSP `incomingCalls` on `create_entry` and `update_entry` returns the same production callers as the grep set. Run it once and report it.
4. No behaviour change; `scripts/test-affected --run` stays green.

## Footprint
- `pyrite/services/qa_fix_service.py`, the extension plugin modules that hold a `kb_service`, `pyrite/plugins/context.py` if the context's attribute is untyped.
- A new `tests/test_service_receivers_typed.py`.
- Model: Sonnet. Sequence after B6 P1/P2 and the frontmatter splitter; it overlaps neither.

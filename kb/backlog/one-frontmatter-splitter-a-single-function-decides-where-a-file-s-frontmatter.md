---
id: one-frontmatter-splitter-a-single-function-decides-where-a-file-s-frontmatter
title: "One frontmatter splitter: a single function decides where a file's frontmatter starts and ends"
type: backlog_item
tags:
- quality
- refactor
importance: 5
kind: enhancement
status: in_progress
priority: high
effort: S
rank: 0
assignee: agent:pyrite-worker
---

Retro 2026-10-03 quality theme (approved by the maintainer). Two pieces of code split a file into frontmatter and body differently: the regex in `_frontmatter_of` (pyrite/models/core_types.py ~451) and `KBRepository._load_entry` (pyrite/storage/repository.py ~62). `ids pin` (#700/#702) now imports the private `_frontmatter_of`, and the write path (B6) needs one answer to "where does the frontmatter end". Principle: kb/design.md 8 (each rule lives in one place).

## Acceptance
1. One public function (in pyrite/storage/ or pyrite/utils/) returns the frontmatter text, its span (offsets of the delimiters) and the body, or a typed no-frontmatter / unparseable result. Both current callers and every other splitter found by grep use it; `_frontmatter_of` is removed or becomes a thin call.
2. A table test over hand-written edge cases, each with the expected span and body: LF, CRLF, mixed endings, BOM, no final newline, `---` indented inside a block scalar, a `|+` scalar last, trailing spaces after the closing `---`, a `...` terminator, a tab before the closing `---`, empty frontmatter (`---` then `---`), no frontmatter, a body that starts with `---`. Where the two old splitters disagreed, the test records which answer is kept and why.
3. A structural test fails if new code under pyrite/ splits frontmatter with its own pattern outside the one module.
4. No file reads differently: `pyrite index build` on kb/ and the test fixture KBs gives the same ids and fields before and after (a parity check, run once and reported).

## Footprint
pyrite/models/core_types.py, pyrite/storage/repository.py, the new function's module, pyrite/services/id_pin_service.py (once #702 has landed), any other splitter found, tests/test_frontmatter_splitter.py. Sonnet. Sequence after #702 lands, before B6.

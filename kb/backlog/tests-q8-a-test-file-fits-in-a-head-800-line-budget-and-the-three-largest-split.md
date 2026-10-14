---
id: tests-q8-a-test-file-fits-in-a-head-800-line-budget-and-the-three-largest-split
title: 'Tests Q8: a test file fits in a reviewer''s head - an 800-line budget that can only shrink, and the three largest files split by behaviour'
type: backlog_item
tags:
- quality
- testing
importance: 3
kind: tech_debt
status: planned
priority: low
effort: M
rank: 0
---

> **Decision 2026-10-14 (Mark):** all eight test themes accepted as planned ("I think all the themes make sense"). Q1 and Q2 tentatively in the next release.

Source: `test-architecture-review-2026-10`, finding 5. Last in the plan: it is the cleanup the other themes make safe, and a budget without the lints before it would be gamed by splitting weak tests into more files.

## Problem

428 files, median 244 lines, p90 707; 77 files over 500 lines, 31 over 800, 20 over 1,000. The largest: `extensions/software-kb/tests/test_software_kb.py` 4,741 lines (246 functions), `tests/test_mcp_setup_reads_back.py` 3,604 (118 functions, 408 instances), `tests/test_storage.py` 2,460, `tests/test_mcp_server.py` 2,183, `tests/test_file_operations.py` 1,991. A reviewer cannot hold 4,700 lines, a cold read samples them, and a `-k` run on one behaviour still imports and collects the whole module. The testing standard's 8-section pattern for extensions (`kb/standards/testing-standards.md`) is a file-per-section pattern that `test_software_kb.py` ignores.

The pattern for a budget that can only shrink exists (`tests/experimental_known_failures.txt`; Q1 and Q3 add two more).

## Acceptance

- `tests/test_tests_fit_in_a_head.py` (name is the worker's): a test file over 800 lines fails unless listed in the file's allowlist with its current line count; the allowlist can only shrink and a listed count can only go down (a PR that grows a listed file fails with the file and the two counts).
- The three largest files are split by behaviour into files under 800 lines each, with no test renamed or dropped (the worker proves it with `--collect-only -q` before and after: same node ids modulo the module path, same count). `test_software_kb.py` splits by the 8-section pattern; `test_mcp_setup_reads_back.py` by the exit enumeration versus the read-back properties; `test_storage.py` by backend operation.
- `tests/test_cli_outcome.py` (2,075 instances from 47 functions; the walk is one function) is listed, not split: its size is parametrisation, not lines, and the item records that the budget is lines.
- Fails today: the budget test on 31 files, before the allowlist is seeded; the PR shows the seeded list.

## Regimes

- A split that changes which conftest a test sees (moving into a subdirectory): the worker keeps the three splits in the same directory as siblings (`test_software_kb_validators.py`, ...) so no fixture resolution changes; `test-affected`'s helper threshold treats sibling modules as before.
- Extension tests import through the repo root; `extensions/software-kb/tests/__init__.py` exists (check) so sibling imports of shared helpers work.

## Footprint

New: `tests/test_tests_fit_in_a_head.py` (~80 lines), ~12 new sibling files from the three splits. Existing: the three files (net zero lines, moved), `kb/standards/testing-standards.md` (+4). Model: sonnet. Sequence: last; after Q1 and Q3 so the lints already judge the split files. Must not break: node-id stability for `tests/experimental_known_failures.txt` (empty today) and `tests/experimental_surface.py`'s path mapping (a split of `test_mcp_setup_reads_back.py` must keep its surface mapping; the worker runs `tests/test_experimental_surface.py` after the split).

Checked: sizes by `wc -l`; function counts from `--collect-only`. Assumed: no test in the three files depends on module-level state set by another test in the same file (xdist already runs them split across workers, so this is nearly certain). This groom is wrong if `experimental_surface.py` maps `test_mcp_setup_reads_back.py` by file name rather than a directory or node-id prefix (then the split adds three lines there).

---
id: tests-q1-no-test-mocks-the-service-behind-the-entry-point-it-drives
title: 'Tests Q1: no test mocks the service behind the entry point it drives (a lint over tests/, and the five search files migrated)'
type: backlog_item
tags:
- quality
- testing
importance: 5
kind: tech_debt
status: planned
priority: high
effort: M
rank: 0
milestone: "0.25.9"
---

> **Decision 2026-10-14 (Mark):** all eight test themes accepted as planned ("I think all the themes make sense"). Q1 and Q2 tentatively in the next release (this item).

Source: `test-architecture-review-2026-10`, finding 1. First in the plan: it removes the class that broke three files on 2026-10-13 and it is a day of work.

## Problem

A test that drives `pyrite search` through `CliRunner` and patches `pyrite.services.search_service.SearchService` is a test of the patch. On 2026-10-13, a41f1dde (#847) changed the CLI's call from `search()` to `search_response()`; `tests/test_fields_acceptors.py:161-168,263-268`, `tests/test_fields_every_format.py:159-164,184` and `tests/test_projected_rich_width.py` all broke, and the repair was to re-point the mock and hand-write the service's response dict (`{"results": ..., "match": "strong", "match_basis": ..., "legs_run": ...}`) into each test. The mock now encodes the implementation. `tests/test_search_filters_across_modes.py` and `tests/test_plugin_semantic_search.py` (257 lines rewritten in the same commit) are the same class.

Across the tree: 195 of 428 files use mocks; 435 `patch(` sites, 413 `monkeypatch.setattr`; `load_config` is patched 147 times in 67 files; `pyrite.services.*` targets: `git_service.subprocess.run` 21, `GitService` 21+5, `KBService` 10+5+3+3+2, `SearchService` 4+2, `llm_service._import_*` 19. The same commit that broke the tests added `tests/search_verdict_seed.py`, a seeded KB that makes the search mocks unnecessary.

The pattern to copy exists: `tests/test_cli_help_assertions_are_plain.py` walks `tests/` and fails with `file:line: use tests.cli_help.plain()`. `tests/conftest.py:93-119` (`_no_network_git_clone`) shows the other half: a guard that turns a fail-open patch into a failure.

## Acceptance

- A test under `tests/test_tests_drive_the_surface_not_the_mock.py` (name is the worker's) walks every `test_*.py` under `tests/` and `extensions/*/tests`. For a file that drives an entry point (imports `CliRunner`, `TestClient`, or calls MCP dispatch), any `patch("pyrite.services...")`, `patch("pyrite.storage...")`, `patch.object(<Service class>, ...)` or `monkeypatch.setattr(<Service class>, ...)` fails the test with `file:line: <target> is the service behind <entry point>; seed the world instead (tests/search_verdict_seed.py, tests/auth_seed.py)`. Patches at the process edge are allowed by target: `subprocess`, `httpx`, `_import_anthropic`/`_import_openai`, `embedding_service.is_available`/`_load_model`, `time`, network clients. The worker enumerates the edge list from the 435 sites and writes it down in the test's docstring.
- An allowlist in the test file names every file that fails today, with one line each; a test asserts it can only shrink (model: `tests/experimental_known_failures.txt` and `scripts/experimental_ratchet.py`'s "can only shrink" check).
- The five search files above are off the allowlist: they seed a KB with `tests/search_verdict_seed.py` and drive the real `SearchService`. Each keeps every property it asserts today (the `--fields` acceptors, every output format, the console width, filters across modes, the semantic leg's failure modes).
- Fails today: the walk fails on at least the five search files and the `load_config` shape in `tests/test_fields_acceptors.py:244-245` (`KBRepository` patched behind `CliRunner`).
- No test under `pyrite.services` or `pyrite.storage` loses a case: the allowlist records the migration debt; this theme migrates search only.

## Regimes

- A file that drives a surface in one test and unit-tests a service in another: the rule is per file; the worker may split the file (Q8's budget applies) or list it.
- `extensions/*/tests` have no conftest and import through the repo root; the walk covers them from `tests/`.
- A patch target given as a string with a module alias (`patch.object(svc, "search")`): the walk resolves the alias by reading the import; an unresolvable target is reported as "unresolved, listed" and goes on the allowlist, not ignored.

## Footprint

New: `tests/test_tests_drive_the_surface_not_the_mock.py` (~200 lines, walk + allowlist + shrink check). Existing: `tests/test_fields_acceptors.py` (-150), `tests/test_fields_every_format.py` (-80), `tests/test_projected_rich_width.py` (-40), `tests/test_search_filters_across_modes.py` (small), `tests/test_plugin_semantic_search.py` (-100), `tests/search_verdict_seed.py` (+30 if a fixture is missing), `kb/standards/testing-standards.md` (+6, the rule and the edge list). Model: sonnet. Sequence: independent; lands before Q5 (the registry would otherwise parametrise over mocks). Must not break: `tests/test_search_match_verdict.py` and `tests/test_search_verdict_on_every_surface.py` already use the seed; keep their fixture names.

Checked: the 435/413/147 counts by grep on a41f1dde; the diff of a41f1dde on the four files. Assumed: the seed KB covers the rows the acceptor tests need (it has `tps/smed.md` and the verdict corpus; a missing field may need one more fixture entry). This groom is wrong if the acceptor tests need a `SearchService` failure that cannot be produced by a real index (then the edge list gains `sqlite3.OperationalError` injection at the backend, not at the service).

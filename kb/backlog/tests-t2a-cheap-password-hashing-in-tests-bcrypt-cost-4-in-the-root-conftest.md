---
id: tests-t2a-cheap-password-hashing-in-tests-bcrypt-cost-4-in-the-root-conftest
title: 'Tests T2a: cheap password hashing in tests (bcrypt cost 4 in the root conftest, one test pins cost 12)'
type: backlog_item
tags:
- quality
- test
importance: 5
kind: tech_debt
status: in_progress
priority: high
effort: S
rank: 0
---

Source: test-architecture review 2026-09-25 (theme T2, part a). Milestone 0.26, as the maintainer decided on 2026-09-24. Part b (shared read-only worlds in the scoping files) is its own item in 0.27, because it has to wait for the private security batch. The two halves are one theme, split into two PRs only because of that sequencing.

## Problem

bcrypt at cost 12 accounts for about 75% of per-test time in the largest file, `tests/test_private_kb_read_scoping.py` (433 tests, 487 s serial, about a third of the suite's CPU). In `test_mcp_write_scoping.py` it was 65 s of 88 s. bcrypt is pure CPU, so under load it degrades worst of anything in the suite: at load 9–12 a hash went from 0.19 s to 0.99 s.

`pyrite/services/auth_service.py:571` hashes with `_bcrypt.hashpw(password.encode(), _bcrypt.gensalt())`.

## Target shape

- An autouse fixture in the **root** `conftest.py`, so the extensions get it too, lowers the bcrypt cost to 4 in tests, for example by patching `pyrite.services.auth_service._bcrypt.gensalt`.
- One opt-out test pins the production cost: the hash starts with `$2b$12$`.

## Groom 2026-09-25

**Acceptance** (the review's T2 criteria that apply to part a, verbatim):
- A test with the production cost marker asserts that `_hash_password` yields cost 12. Removing the autouse patch does not break it, and changing production cost does.
- Suite-wide `hashpw` call count is reported before and after (a counting patch in a local run is fine).

Added for part a: report the wall time of `tests/test_private_kb_read_scoping.py` run serially, before and after, on the same machine under the same load. The review's target for T2 as a whole is a fall of at least 60%; part a alone should deliver most of it.

**Touches:**
- Existing: `conftest.py` (root), `tests/test_auth_service.py` (the production-cost pin).
- Plus one rule in `tests/test_test_rules.py`: the test-cost patch lives only in the root conftest (review rule 11).

**Sequence:**
- After T3, which also edits the root `conftest.py` and moves fixtures into it.
- Independent of the security batch: it touches no scoping file.

**Model:** Sonnet. **Size:** S. **Heavy:** yes (the full suite, to measure). **Cold read:** yes. It patches auth hashing in-process for the whole suite. A reviewer should confirm the patch cannot reach non-test code paths and that no test asserts timing.

**Out of scope:**
- Changing fixture scope in the scoping files. That is part b (0.27).
- Changing the production cost, or making it configurable.
- Any change to `auth_service.py`.

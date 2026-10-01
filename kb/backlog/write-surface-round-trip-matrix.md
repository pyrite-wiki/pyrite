---
id: write-surface-round-trip-matrix
title: Write-surface round-trip matrix
type: backlog_item
tags:
- quality
- refactor
- testing
importance: 5
kind: improvement
status: proposed
priority: high
effort: M
rank: 0
---

## Problem

One defect class reached the maintainer as nine issues in six days (#455, #549, #554, #555, #557, #561, #568, #569, #47): a write changed something it was not asked to change, or wrote a value that did not read back the same. Five PRs fixed the instances. No test owns the invariant they share, so the next surface or the next frontmatter shape is found by use again (retro 2026-10-01).

**The invariant.** A write changes only what it names, and what it writes reads back the same for every reader, on every write surface.

## What the existing tests already cover

- `tests/test_write_surface_parity.py` covers **create** only: six create surfaces (REST `POST /api/entries`, REST import, MCP `kb_create`, MCP `kb_bulk_create`, CLI `create`, CLI `import`) refuse six bad specs with the same error code and write nothing, plus one valid-create control. It has no update surface and no round trip.
- `tests/test_update_never_loses_a_key.py` covers **update** on three surfaces (CLI `update -f`, REST `PATCH`, MCP `kb_update`) for one frontmatter shape (a foreign `provenance:` block) and one change, asserting the file is byte-identical outside the changed line. Six further lossy shapes run through `KBService.update` only, not through each surface. An echoed read is tested on MCP only, for one shape. `add_link` is called on the service as a control that an in-place change lands; it asserts nothing about the rest of the file.
- Nearby: `tests/test_rest_put_echo.py` (REST `PUT` echo, one shape, `importance` and `tags`), `tests/test_reads_and_echoes_keep_values.py` (MCP echoes; a task's word `priority`), `tests/test_roundtrip_identity.py` (a no-op load and save through the repository, no surface), `tests/_surface_inventory.py` (enumerates REST operations and MCP tools from the real app and server).

**Not covered anywhere:** `pyrite task update` and `pyrite link` as surfaces; REST `PUT` and the echo case beyond one shape; the shapes crossed with the surfaces; any check that a new write surface joins a test.

## What to build

One parametrized test module, `tests/test_write_surface_round_trip_matrix.py`, over real surfaces (Typer `CliRunner`, FastAPI `TestClient`, the MCP dispatcher) and one KB on disk.

- **Surfaces (the minimum):** CLI `update`, CLI `task update`, CLI `link`, REST `PATCH /api/entries/{id}`, REST `PUT /api/entries/{id}`, MCP `kb_update`. One adapter per surface, in the style of the `SURFACES` dicts in the two files above.
- **Shapes:** a fixed list of hand-written frontmatter files, each a string in the test module. At least: flow-style `tags: [a, b]`; a string `tags: Foo`; `importance: high`; a foreign `provenance:` mapping and a scalar one; bare-string `links:`; a scalar `sources:`; an undeclared key with a nested mapping; quoted and unquoted dates; a block-indented sequence; a task with a word `priority`; an off-list enum value already on disk; keys in non-canonical order; no trailing newline after the body.
- **Assertion 1, echo:** read the entry through the surface's own reader (`pyrite get --format json`, `GET /api/entries/{id}`, `kb_get`), send the result back unchanged through the write surface, and the file is byte-identical. A surface with no echo form (`link`) is marked not applicable in the matrix, with the reason.
- **Assertion 2, named change:** change one named key, and the file differs from the original in that key's lines only.
- **Assertion 3, structure:** a test that fails when a write surface exists outside the matrix. Enumerate MCP tools and REST operations with `tests/_surface_inventory.py`, and CLI commands from the Typer app; every one that rewrites an existing entry file is either a matrix surface or on a named exclusion list with a one-line reason. Other writers exist today (`task status`, `task claim`, `task checkpoint`, `rename`, `links bulk-create`, `POST /api/tasks/{id}/claim`, plugin commands): each is classified.

## Acceptance

1. `tests/test_write_surface_round_trip_matrix.py` exists and runs every surface against every shape for assertions 1 and 2; cell ids name the surface and the shape.
2. Adding a write command, REST operation or MCP tool that rewrites an entry file, without adding it to the matrix or the exclusion list, makes assertion 3 fail. The PR shows this with a throwaway registration in a test.
3. Every cell that fails on `dev` is reported in the PR as a finding with surface, shape and the diff. A cell that needs a change under `pyrite/` gets its own GitHub issue and is marked `xfail(strict=True)` naming that issue; the matrix does not weaken the assertion to pass.
4. The item's first report line states which cells the two existing files already cover, so duplicated cases can be deleted from them in the same PR.
5. `scripts/verify-red.sh` shows the structural test red against a tree with one surface removed from the matrix.
6. #569 item 3: the matrix includes its two named cases (a trimmed echo that keeps `importance` and `tags` but drops `id`; a deliberate request that carries `id`) and asserts the file outcome for both. Item 3's own done-when also asks that the result say which path a request took and that `docs/agent-write-path.md` explain it. If the matrix shows that needs code, it is a finding under point 3 and #569 stays open for it; say so in the PR.

## Scope

Tests only, unless the matrix finds a defect (then point 3). Model: opus. `heavy: no` (one module; `TestClient`, no live server).

## This item is wrong if

Byte-identity on echo is not achievable for a shape by design (a legacy alias that is deliberately rewritten, the `body:` fold that a save deliberately removes). Those shapes go in the matrix as named exceptions with the test that pins the deliberate behaviour, not as silent omissions.

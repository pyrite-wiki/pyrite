---
id: tests-q5-one-surface-registry-feeds-every-cross-surface-test
title: 'Tests Q5: one surface registry (CLI, REST, MCP) feeds every cross-surface test, with a completeness check'
type: backlog_item
tags:
- quality
- testing
importance: 4
kind: improvement
status: planned
priority: medium
effort: L
rank: 0
---

> **Decision 2026-10-14 (Mark):** all eight test themes accepted as planned ("I think all the themes make sense"). Q1 and Q2 tentatively in the next release.

Source: `test-architecture-review-2026-10`, the "Shape" section. Depends on `tests-t4` (the world builder) for the seeded world, and on Q1 (otherwise the registry parametrises over mocks).

## Problem

A property that must hold on every surface is written three times, with three hand-rolled drivers, in a new file each time: `tests/test_search_verdict_on_every_surface.py`, `tests/test_write_surface_parity.py`, `tests/test_effective_status_everywhere.py` (85 instances from 4 functions), `tests/test_default_output_is_the_result.py`, `tests/test_fields_every_format.py`, `tests/test_private_kb_read_scoping.py` (433 instances). Two half-registries already exist and do not meet: `tests/outcome_walk.py` (every command of the three CLIs that declares `--format`, read from the Typer registry; `contract_commands`, `args_for`) and `tests/characterization/surfaces.py` (REST routes and MCP tools for the goldens, with `rest_calls.py` and `mcp_calls.py` as drivers). A new cross-surface property costs a file and three drivers; a new command or tool joins none of the existing matrices unless someone remembers.

## Acceptance

- `tests/surfaces.py` (name is the worker's): one registry of operations, each row naming the operation (`search`, `get`, `create`, `update`, `delete`, `list`, `link`, ...), its CLI invocation (from `outcome_walk`), its REST route and method (from `characterization/surfaces.py`), its MCP tool and argument shape, and the tier it needs. One driver per surface returns a common `Result` (status, body as JSON, exit code or HTTP status, the rendered text) against the T4 world.
- A completeness test fails when a CLI command declaring `--format`, a REST route under `/api`, or a registered MCP tool has no row, or a row names a surface that does not exist; experimental surfaces are rows marked so, and `tests/experimental_surface.py` reads the mark instead of its own path list for those.
- Three of the files above are rewritten as one parametrised test each over the registry (`test_search_verdict_on_every_surface.py`, `test_default_output_is_the_result.py`, `test_effective_status_everywhere.py`), keeping every property they assert and with the surface and operation in every assert message (Q3's rule). The others are listed in the item as follow-ups with their instance counts.
- `tests/characterization` keeps its goldens and its drivers but imports the route and tool lists from the registry, so there is one list.
- Fails today: the completeness test, on at least one surface with no row (the worker names it).

## Regimes

- A tool that exists on MCP only (orient, the setup tools) or a CLI-only command (`init`, `mcp-setup`): a row with the other surfaces `None`, and the parametrised tests skip those cells with the reason in the id.
- A surface that needs auth (REST tiers, MCP write key): the row's tier selects the principal from the T4 world; an operation run above its tier must refuse, and that is itself one registry-driven test (`characterization/test_access_deciding_routes.py` is the model).
- Rendering differences (rich tables on the CLI): the driver captures the JSON format for equality and the rendered text for Q1-style width checks only where a test asks.

## Footprint

New: `tests/surfaces.py` (~350 lines), `tests/test_surfaces_registry_is_complete.py` (~120). Existing: the three rewritten files (net -400), `tests/characterization/surfaces.py` (-80, import the lists), `tests/experimental_surface.py` (+20). Model: opus. Sequence: after T4 and Q1; the three rewrites can be three commits on one branch. Must not break: `tests/test_cli_outcome.py`'s walk (it owns `outcome_walk.py`; extend, do not move), the characterization goldens (`tests/characterization/goldens/*.json` unchanged, checked by running the matrices).

Checked: the two half-registries and the six hand-rolled matrices by reading their imports. Assumed: the T4 world builder lands first with a per-module-scoped seeded world; without it the registry tests pay a per-test world and Q5 doubles the suite's time instead of halving the files. This groom is wrong if T4 is deferred past 0.27 (then Q5 builds a minimal module-scoped world of its own under `tests/surfaces.py`, ~100 lines, and T4 later replaces it).

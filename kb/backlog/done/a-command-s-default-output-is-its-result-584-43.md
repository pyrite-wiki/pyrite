---
id: a-command-s-default-output-is-its-result-584-43
title: "A command's default output is its result (#584, #43)"
type: backlog_item
tags:
- 0.25.7
importance: 5
kind: bug
status: done
priority: high
assignee: agent:pyrite-worker-sonnet
effort: S
rank: 0
---

Theme: **a command's default output is its result** (milestone 0.25.7). Closes #584 and, with #583's later tutorial change, what is left of #43.

The spec is the groom: the `## Groom 2026-10-01` comment on #584, and the maintainer's decision under it (HTTP servers keep INFO logs; the stdio `mcp` server goes quiet). Acceptance, regimes, touches and out of scope are there and are not repeated here.

Done: `pyrite`, `pyrite-admin` and `pyrite-read` log at WARNING through one function (`pyrite/logging.py`, `configure_entry_point_logging`); `-v`/`-vv` work in any position (and a `-v` that is an option's value stays the value); `PYRITE_LOG_LEVEL` sets the level; `pyrite serve` and `pyrite-server` keep INFO and `pyrite-server` now prints its own warnings. Semantic and hybrid search say so, with the same cause in the warning and the trace, when the extra or sqlite-vec is missing. `index embed` counts the vectors it added, names other KBs it settled, and shares that wording. The riskiest-assumption result and the review rounds are on PR #590 and the #584 thread; tests are in `tests/test_default_output_is_the_result.py`.

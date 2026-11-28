---
id: extract-hookrunner-from-kb-service
title: "Extract HookRunner from KBService; move task-specific hooks to task_service"
type: backlog_item
tags: [architecture, services, kb_service, refactor, hooks, modularity]
importance: 5
kind: improvement
status: done
priority: high
effort: S
rank: 0
---

## Problem

`KBService` (`pyrite/services/kb_service.py`, 1,369 lines, 38 public methods) is
the load-bearing service. Recent work extracted `graph_service`,
`export_service`, `ephemeral_service`, and `quota_service` out of it — the
correct move, but it stopped early. What remains is still doing four jobs:

1. **CRUD** (create/get/update/delete) — its real job.
2. **Hook orchestration** — `_run_hooks`, the `_CORE_HOOKS` registry, before/after
   dispatch, and the swallow-and-log behavior the recent audit caught (lines
   1085-1095 swallow after-save errors silently).
3. **Embedding orchestration** — auto-embed on save with lazy init and another
   swallowed-exception pattern (lines 66-80).
4. **Task-specific workflow** — `_task_validate_transition` and
   `_parent_rollup` live in `kb_service.py` as module-level `_CORE_HOOKS`, but
   only fire for `entry_type == "task"`. Wrong home.

The audit identified jobs 2 and 4 as the cleanest extractions: they touch the
fewest other KBService internals and have the most concentrated debt
(silent-error swallowing, weird home).

## Solution

### HookRunner (peer service)

Extract a `HookRunner` service:

```python
class HookRunner:
    def __init__(self, plugin_registry):
        self._core_hooks: dict[str, list[Callable]] = ...
        self._registry = plugin_registry

    def register_core_hook(self, name: str, fn: Callable) -> None: ...
    def run_before_save(self, entry, context) -> Entry: ...
    def run_after_save(self, entry, context) -> Entry: ...
    def run_before_delete(self, entry, context) -> None: ...
    def run_after_delete(self, entry, context) -> None: ...
```

`KBService` holds a `HookRunner` and delegates. The swallow-vs-raise contract
(before-hooks raise, after-hooks log) lives in one place instead of being open-
coded in `KBService._run_hooks`. The audit's after-save-error-swallowing
finding becomes addressable with one logging or telemetry change in
`HookRunner.run_after_save` rather than touching `KBService`.

### Move task-specific hooks to task_service

`_task_validate_transition` and `_parent_rollup` move into `task_service.py`
and register themselves at startup via `hook_runner.register_core_hook(
"before_save", _task_validate_transition)`. They stay typed (`entry: Any`
becomes `entry: TaskEntry`, addressing the audit's type-hint finding at lines
1308 and 1335 in one swoop). The "wrong home" smell goes away.

## Acceptance criteria

- New `pyrite/services/hook_runner.py` with `HookRunner` class and the
  before/after dispatch + swallow/raise contract.
- `KBService._run_hooks` deleted; CRUD methods call `self.hook_runner.run_*`.
- `_task_validate_transition` and `_parent_rollup` moved into
  `task_service.py`, registered at task-service init.
- `entry: Any` -> `entry: TaskEntry` on both moved functions.
- Existing tests for task transitions, parent rollup, before-save validation,
  and after-save side effects all still pass; no API change visible to
  callers.
- After-save error logging is now in `HookRunner` only; if telemetry or
  bubble-up is later wanted, one site changes.

## Out of scope

- The embedding-orchestration swallow (kb_service.py:66-80). That's a separate
  fix; this ticket only addresses hook orchestration and task-specific hooks.
- Further `KBService` decomposition. After this lands, `KBService` should be
  meaningfully closer to "just CRUD."

## Related

- [[bug-postgres-backend-silent-return-empty-on-query-error]] — same family of
  silent-failure bugs the audit named; this ticket addresses the hook layer's
  version.
- ADR-0002 — the core-hooks list lives at the boundary between core and
  plugins; consolidating it makes the boundary cleaner.
- The modularity report committed alongside this ticket.

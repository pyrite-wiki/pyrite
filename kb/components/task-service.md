---
id: task-service
title: Task Service
type: component
tags:
- core
- coordination
kind: service
path: pyrite/services/task_service.py
owner: markr
dependencies: '["kb_service"]'
---

Operative task service wrapping KBService for the coordination-task extension. Provides task-specific atomic operations: claim (CAS), decompose and checkpoint. Handles task lifecycle beyond basic CRUD — dependency-aware claiming and hierarchical decomposition into subtasks.

## Derived completion

A parent task's completion is computed, never written (ADR-0042 decision 4, ADR-0045 decision 7). `derive_completion(rows)` in `pyrite/models/task_completion.py` (re-exported by `task_service.py`) is the one function that decides it; `TaskService.derived_completion(kb)` reads the KB's rows once and calls it, and `derived_for(kb, id)` returns one task's value. Every reader attaches it under `derived`, beside the file's own `status`:

```
"derived": {"completion": {"complete": bool, "basis": "status" | "children",
                           "children": int, "children_complete": int},
            "effective_status": str,
            "parent_missing": bool}
```

- A terminal status the file holds decides: `done`/`cancelled` are complete even with open children, `failed` is not. Otherwise a task with children is complete when every child is (a grandparent is complete when its whole subtree is).
- `effective_status` is the file's status, except that a task complete by its children is `done`. **Every status filter matches it** (maintainer, 2026-10-03): `list_tasks` (so `task list`, MCP `task_list`, `GET /api/tasks`, the web worklist and the investigation conductor's drain check), and every storage query that compares `status` — `find_by_status`, `find_by_assignee`, `find_overdue`, keyword and semantic search, `list_entries`/`count_entries` and the QA queue — through `pyrite/storage/effective_status.py`, which applies `derive_completion`'s answer as a SQL expression so paging stays exact. `tests/test_effective_status_everywhere.py` walks every MCP, REST and CLI `status` parameter and fails on one nobody classified.
- **A claim decides on the file.** `KBService.claim_entry` reads the status and assignee it judges from the task's file, and its write (`_update(precondition=...)`) checks them again on the bytes it replaces, so a stale index row cannot hand a held task to a second claimer. The compare-and-swap on the index row is kept as the mutex, not the record: outside a git work tree `publish_entry_text` compares and replaces with no lock, so the row's swap is what gives one winner there. A row that loses the swap is believed only when its `content_hash` is the file's (a claim in flight); otherwise it is rebuilt from the file and the swap runs once more. A refusal leaves the row saying what the file says. Known gaps: a row left `claimed` with the file's hash by a claimer that died between swap and write (or by the sync race below) refuses claims until `index sync --force`; and `index sync` can overwrite a claim made while it ran (`tests/test_task_claim_concurrency.py::TestIndexSyncRacingAClaim`, a strict xfail). Tests: `tests/test_task_claim_concurrency.py`.
- **Claims:** `KBService.claim_entry` refuses a task that is complete by its children (`_refuse_claim_of_complete_task`), with the reason; it asks the same function. `task update -s claimed` is a status edit, not a claim, and stays allowed so a person can walk a parent to an explicit `done`.
- Only tasks count as children; any entry may be a parent. A `parent` that names no entry in the KB is `parent_missing`, never an error. `parent` cycles count as not complete. The walk is iterative (as is `get_subtree`): a 1,500-deep chain once raised RecursionError and failed `task list` for the whole KB.
- Readers: `list_tasks`, `get_subtree`, `get_ancestors`, `claim_entry`, CLI `task get`/`task decompose`, MCP `task_status`/`task_decompose`. Tests: `tests/test_derived_task_completion.py` (every entry point x every tree shape, literal expectations; reads write nothing; a deep chain) and `tests/test_after_save_hooks_write_no_other_entry.py` (no after_save hook writes another entry; a planted rollup proves the check catches one).

The `_parent_rollup` after_save hook and `rollup_parent` that used to write the parent's `status: done` are deleted. Files where it already wrote `done` keep it.

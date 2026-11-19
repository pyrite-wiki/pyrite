## 2026-11-17 · Pyrite 0.26.4 candidate (96bb4936) over MCP stdio (`pyrite mcp --tier write`) as an agent's long-term memory · claude-sonnet-5-5

Setup: `pyrite init --template empty --path memkb --name agent-memory`, then a ~60-line Python `mcp` stdio client (`client.py`, `c2.py`; `mcp` came with `pyrite[server,cli]`). Invented subjects: Dana Reyes (Harbor Falls city engineer), Acme Paving LLC. Ran initialize, list tools (40 at write tier), kb_orient, create x2, update (tags list + importance), get, keyword/semantic/default search, link, task_create (due_date + links), task_claim, task_update to cancelled and through done, kb_find_overdue, kb_timeline, kb_recent, and about 20 deliberately bad inputs. Server process exits with the client; `pgrep` shows none left.

**Friction 1 — a scalar to a declared list field is stored as a scalar via `metadata` (the #1707 gap, confirmed still present as expected).**
**Command:** `kb_update {"entry_id":"dana-reyes","metadata":{"affiliations":"Acme Paving LLC"}}` and `{"metadata":{"role":42}}`.
**Expected:** a refusal (affiliations is `list[str]`, role is text) or coercion.
**Got:** `{"updated":true,...}`; file now has `affiliations: Acme Paving LLC` and `role: 42`; `kb_get` returns `"affiliations":"Acme Paving LLC","role":42`. Top-level `kb_create ... "aliases":"Bob"` on a person was coerced to `aliases: [Bob]`, so the same mistake behaves two ways depending on the route. Typed top-level params (`tags`, `importance`) are rejected by the schema.
**Would have helped:** one rule: coerce or refuse the same way on every route.
**Severity:** annoyed (data written wrong with `updated:true`)

**Friction 2 — two error shapes, and the schema-level one has no code.**
Schema failures (`importance:"high"`, `tags:"city"`, `title:12345`, `body:42`, `priority:"urgent"`, `status:"bogus"`, `priority:50`) return `isError=True` with plain text `Input validation error: 'high' is not of type 'integer'`; no `error_code`, no `retryable`. Domain refusals (importance 99, missing entry, bad due date, bad transition, unknown KB) return `isError=False` with `error`/`error_code`/`retryable`. An agent can still decide the next step from the schema text (it names the value and the type), but a client that branches on `error_code` sees nothing for the first class, and one that branches on `isError` misses the second. The server instructions mention only the second shape.
**Would have helped:** one sentence in `instructions`: "argument-type failures arrive as isError text without a code".
**Severity:** annoyed (same as 0.26.3 friction 4)

**Friction 3 — out-of-range importance is good, but the same number in `metadata` is the only route that says so differently.**
`importance:99` / `-1` / `11` (update and create) all give `VALIDATION_FAILED`, `"errors":[{"field":"importance","rule":"range","expected":"1-10"}]`, suggestion "use 1-10": decidable from the error alone. Via `metadata:{"importance":99}` the same refusal. Good. Note the JSON schema for `importance` on `kb_create`/`kb_update` has no `minimum`/`maximum` while `task_*` priority does, so the range is learned by failing.
**Would have helped:** `minimum:1, maximum:10` on `importance` like `priority`.
**Severity:** cosmetic

**Friction 4 — an undeclared entry type is still accepted silently.**
**Command:** `kb_create {"entry_type":"decision","title":"Undeclared","body":"x"}` on the empty-template KB (`types:[]`).
**Got:** `created:true`, file `memkb/undeclared.md` at the KB root with `type: decision`, no warning. The `kb_create` description says it "Validates against kb.yaml schema and returns warnings for unknown vocabulary values" and tells you to call `kb_schema` first.
**Would have helped:** a `warnings` entry ("type 'decision' is not declared in kb.yaml; stored at the root") or a refusal.
**Severity:** annoyed (Still there from 0.26.3 friction 5)

**Friction 5 — `kb_link` requires `source_kb` even when `kb_name` is the convention everywhere else.**
**Command:** `kb_link {"kb_name":"agent-memory","source_id":"dana-reyes","target_id":"acme-paving-llc"}`.
**Got:** `isError=True` `Input validation error: 'source_kb' is a required property`. Clear and one retry fixes it, but every other tool takes `kb_name`. Also `kb_link` to a missing target returns `{"error":"Entry not found: ghost","error_code":"LINK_FAILED"}`; there is no hint that `allow_dangling:true` exists (it is in the schema only).
**Would have helped:** accept `kb_name` as an alias; mention `allow_dangling` in the suggestion.
**Severity:** annoyed

**Friction 6 — `task_claim` needs `assignee` and silently ignores the name an agent would guess.**
**Command:** `task_claim {"task_id":...,"kb_name":...,"agent_id":"agent-1"}` (extra unknown key).
**Got:** `{"error":"'assignee' is required: this connection has no signed-in user to record","error_code":"VALIDATION_FAILED","suggestion":"Pass 'assignee' naming the person to record"}`. The unknown `agent_id` was dropped without comment (also `task_create {"due":...}` silently dropped, task created with no due date, no `ignored` note). The error itself is decidable. A second claim gives `{"claimed":false,"error":"... is 'claimed', not 'open'","current_status":"claimed"}`: no `error_code` and `isError=False`, so it is a third shape (a refused claim looks like a result).
**Would have helped:** echo ignored keys; give the claim refusal an `error_code` (e.g. `CONFLICT`).
**Severity:** annoyed

**Friction 7 — `kb_timeline` lists dateless tasks as events.**
`kb_timeline` returned my 3 tasks with `"date":""` and none of the notes. Same as before; a to-do is not a timeline event.
**Would have helped:** skip entries with no date.
**Severity:** annoyed

**Friction 8 — null vs empty string differs by entry type in one `kb_get`.**
Person: `"date":null,"assignee":null,"priority":null`; task: `"date":"","assignee":"","assigned_at":""`, `"location":null`. An agent testing `if not x` is fine, one testing `is None` is not.
**Severity:** cosmetic

**Friction 9 — semantic search without the extra degrades with a good message.**
`kb_search ... mode:"semantic"` gave `match:"weak"`, `legs_run:[]`, and a warning with the exact install command for this commit. Tells a human what to do; a chat agent still cannot install it (not tested further: semantic extra not installed per rules).
**Severity:** cosmetic

## Release blockers
none. No traceback, no crash, no data loss. (Friction 1 writes a scalar into a list field with `updated:true`; that is #1707, which the preamble says lands before the cut.)

## Re-check of 0.26.3 findings (feedback/2026-11-16-hallway-mcp-stdio-memory.md)
- F1 semantic dead on MCP-only, `kb_index_sync` does not embed: Not re-tested (semantic extra not installed); the no-extra message is clear and names the exact command.
- F2 semantic floor false negative: Not tested (needs embeddings).
- F3 `cancelled` missing from `task_update` enum: Fixed. Enum includes it, description states the state machine, `cancelled` worked from open and the transition error text is accurate.
- F3 "mark done takes 3 calls": Still there (claim, in_progress, done), but the error says exactly which to walk through.
- F4 two error shapes: Still there (Friction 2).
- F5 undeclared type accepted silently: Still there (Friction 4).
- F6 task_create has no due_date or links: Fixed. `due_date` and `links` work, response echoes `linked` and `failed_links`, the file has them, `kb_find_overdue` finds the past-due task.
- F6 `kb_timeline` lists dateless tasks: Still there (Friction 7).
- F7 recent gives no delta: Not re-tested beyond `kb_recent` with `fields` (works, compact). `metadata` as string vs object across tools: now an object in `kb_get` and `kb_search` rows (Fixed for those two; `kb_find_by_status` not re-tested).
- F8 `kb_update` echoes custom fields: Not re-tested; `kb_update` description now says unwritten keys "are listed", which suggests Changed.
- F9 100 tools at write tier: Changed. 40 tools listed at write tier here.
- F10 keyword search misses tags/fields: Not re-tested.
- Read-tier FORBIDDEN suggestion: Not tested this session.
Count Fixed: 3 (cancelled enum, task due_date/links, metadata object); Changed: 2; Still there: 4.

## What worked well
- Importance range errors carry a `errors[]` array with field, rule and expected value: decidable with no guessing.
- `task_update` transition error names the allowed next states and the route, and `task_claim` race loser gets `current_status`.
- `task_create` with `due_date` and `links` in one call, with `linked`/`failed_links` in the answer; `kb_find_overdue` then worked.
- Search `match`/`match_basis`/`legs_run` and the `QUERY_SYNTAX` suggestion ("quote tokens containing -") are good.
- Every write is a plain Markdown file I could `cat`; `kb_get fields:[...]` keeps answers small.

## 2026-10-23 · Desktop-style agent using Pyrite over MCP as memory across sessions · claude-sonnet-5-5
Setup: scratch HOME, KBs made with `pyrite init -t research -p kb-a -n scratch-a` and `pyrite init -t zettelkasten -p kb-b -n scratch-b --no-examples`. Everything else through a Python stdio client (mcp ClientSession) against `pyrite mcp --tier read|write|admin`. Placeholders: Person A, "budget memo" are invented.

**Tier counts (observed):** list_tools gives read 66, write 97, admin 106. Matches `pyrite mcp --help` (66, +31, +9). `kb_orient` is the only description that says "Use this first"; the server `initialize` result carries no `instructions`.

**Friction 1 — `kb_registry_health` and `kb_stats.entry_count` report 0 after MCP writes; health says `healthy:false`.**
Command: admin tier, `kb_registry_health {"name":"scratch-a"}` (three separate connections), after 3 `kb_create` calls in a prior session.
Expected: file_count 2 and entry_count 2 (the entries are searchable and `kb_orient` says total_entries 3 before I deleted one).
Got: `"healthy":false,"file_count":2,"entry_count":0,"last_indexed":"...05:53:36"` (the init time). `kb_stats` shows `entry_count:0, actual_count:2`. `pyrite index health` (CLI) says `unindexed_files: 0`, so the index is fine; the registry counter is stale.
Observed twice-plus. Concluded: looks like a bug (counter not bumped by MCP writes). An agent that trusts `healthy:false` would reindex needlessly.
Severity: annoyed

**Friction 2 — `kb_recent since="yesterday"` silently returns nothing.**
Command: `kb_recent {"since":"yesterday"}` (read and admin tier, twice).
Expected: an error naming the accepted format, or the entries.
Got: `{"entries":[],"count":0}`. The description says "ISO datetime"; a bad value is treated as "nothing newer". For a "what changed since I last looked" workflow that reads as "nothing changed" — a confident wrong answer.
Possible bug (no validation of `since`); reproduced twice.
Severity: slowed

**Friction 3 — no first-class "what changed since last session".** Closest is `kb_recent since=<ISO>`; I had to store my own last-seen timestamp. It cannot show deletes: after `kb_delete person-a`, `kb_recent since=...` lists only the updated note. Creation versus update is not distinguished (no `change` field; `created_at` must be compared by hand). `kb_timeline` is event-date, not change-time, and has no `kb_name` parameter at all (cannot scope to one KB).
Would have helped: a `kb_changes(since)` returning created/updated/deleted ids, or a documented "store the max updated_at" idiom in `kb_recent`'s description.
Severity: slowed

**Friction 4 — read-tier refusal is "Unknown tool".** `kb_create` called on `--tier read`:
`{"error":"Unknown tool: kb_create","error_code":"UNKNOWN_TOOL","retryable":false,"suggestion":"Use list_tools to see available tools"}` (isError false, the client also logged "Tool 'kb_create' not listed, no validation will be performed"). Doesn't say the tool exists at a higher tier or how to reconnect. An agent may conclude the tool is gone. Also not in docs/json-contracts.md as a code (`INVALID_TIER` is, a different thing).
Would have helped: `error_code: TIER_FORBIDDEN`-style, "kb_create needs --tier write".
Severity: annoyed

**Friction 5 — semantic search needs a CLI step an MCP-only agent cannot take.**
Command: `kb_search {"query":"who signed off the spending paper","kb_name":"scratch-a","mode":"semantic"}`
Got: 0 results, `"warnings":["semantic leg skipped: no embeddings exist ... run \`pyrite index embed\` to build them"]`. The warning is clear and honest, but no MCP tool builds embeddings (only `kb_index_sync` at admin; the string "embed" is in no tool name). Agent in Desktop is stuck unless the human runs the CLI. Did not get to test semantic ranking.
Severity: slowed

**Friction 6 — `kb_link` accepts any relation and any `is_about`-style name with no warning.** `relation:"bogus_rel"` returned `linked:true`; `is_about` is not in the schema's relationship_types, and its backlink came back as `relation:"related_to", inverse_relation:null`. `kb_create` warns on undeclared types, `kb_link` does not. Vocabulary mistakes become silent graph noise.
Severity: annoyed

**Friction 7 — `kb_delete` leaves dangling links with no mention.** `kb_delete person-a` returned `{"deleted":true,"entry_id":"person-a"}`; `pyrite index health` then reported `broken_links: 2`. Delete could say how many entries linked to it.
Severity: annoyed

**Friction 8 — parameter names differ across tools.** `kb_get`/`kb_create`/`kb_recent` use `kb_name`; `kb_registry_health` wants `name`; `kb_commit` wants `kb`. I guessed `kb_name` for both, got the schema error `Input validation error: 'name' is a required property` / `'kb' is a required property` (isError true). Only these two errors are isError:true; every domain error is a normal result with an `error` key, so a client must check both.
Severity: annoyed

**Friction 9 — validation error is bare.** `kb_create {entry_type:"event", title:"No date event"}` -> `{"error":"Event must have a date","error_code":"VALIDATION_FAILED","retryable":false}`: no `suggestion`, no field name (`date`, YYYY-MM-DD) though the tool schema says date is required for events. Compare with UNDECLARED_TYPE, which lists the valid types and the override. Also `kb_link` to a missing cross-KB target gives `LINK_FAILED` with "Entry not found: b-note" (not `ENTRY_NOT_FOUND`, no suggestion, no mention that `allow_dangling` exists), and `kb_backlinks` on a nonexistent id answers success with count 0 rather than not-found.
Severity: cosmetic

**Friction 10 — docs/json-contracts.md vs observed.** Matched: flat error shape; ENTRY_NOT_FOUND / KB_NOT_FOUND with `legacy_error_code: NOT_FOUND` (kb_get); `kb_update` missing entry -> ENTRY_NOT_FOUND + legacy `UPDATE_FAILED`; QUERY_SYNTAX. Not matched: `kb_update` missing entry has no `suggestion` (fine, optional); `kb_get` unknown KB had `did_you_mean: []` as documented. `UNKNOWN_TOOL`, `UNDECLARED_TYPE`, `LINK_FAILED` are not in the contract page's tables (grep of the first 200 lines only; I did not read the remainder). Cross-KB: a KB-scoped `kb_get {"entry_id":"zettel-in-b","kb_name":"scratch-a"}` correctly says ENTRY_NOT_FOUND; unscoped `kb_get` finds it.

**Had to figure out:** the `entry_type` per KB (note is not valid in the zettelkasten KB; UNDECLARED_TYPE error taught me it); entry ids are slugs of titles, returned by kb_create; `kb_link` needs `source_kb` (and `target_kb` for cross-KB). `docs/getting-started.md` "Connect an AI via MCP" points at `pyrite mcp-setup`; I bypassed it and spawned `pyrite mcp --tier X` directly, and the `--help` text was enough.

**Worked well:** `kb_orient` with no args lists KBs plus the operational contracts (indexing, error shape, quoting) in one call; with `detail:"brief"` it carries relationship_types. `kb_create` returned entry_id and file_path, so no slug guessing. QUERY_SYNTAX error told me exactly which token to quote. `kb_search` empty-semantic result carried `match_basis` and `warnings` instead of a bare empty list. `kb_link` is idempotent and reports `created:false` on the repeat. `kb_backlinks` shows `forward_relation` plus the inverse, cross-KB included (backlink from scratch-a appeared under scratch-b). Tier counts matched `--help` exactly. `kb_recent fields:[...]` kept responses small.

**Severity (overall):** annoyed

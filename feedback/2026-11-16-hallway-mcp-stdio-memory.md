## 2026-11-16 · Pyrite 0.26.3 over MCP stdio as a chat agent's long-term memory for side-project planning (3 sessions: write, write, read) · claude-sonnet-5-5

Setup: `pyrite init --template empty --path .../side-project` (CLI, once), then a Python `mcp` stdio client against `pyrite mcp --tier write|read|admin`, isolated HOME/PYRITE_CONFIG_DIR/PYRITE_DATA_DIR. Invented subjects: <person-a> (designer), <person-b> (backend contractor), a project "Trailmix". Stored 2 people, 1 topic, 2 decision notes, 3 tasks, 4 links, 1 bulk note; then recalled, searched, corrected, completed a task, and tried to write from the read tier.

**Friction 1 — semantic search is dead on an MCP-only install, and nothing over MCP can fix it.**
**Command:** `kb_search {"query":"where are we hosting","kb_name":"side-project","mode":"semantic"}` (write tier, ~10 s after the writes); same in default hybrid mode; then `kb_index_sync {"kb_name":"side-project"}` at admin tier and the search again.
**Expected:** hybrid is the default; writes "never wait on" embedding, so an MCP agent's own fresh entries should become semantically findable without help.
**Got:** `{"count":0,...,"match":"weak","match_basis":"semantic: the leg did not run: nothing in the KBs searched is embedded yet","legs_run":[],"warnings":["semantic leg skipped: no embeddings exist ... run `pyrite index embed` to build them"]}`. `kb_index_sync` returned `{"synced":true,"added":0,"updated":0,...}` and did not embed; semantic still empty. The only fix was the CLI (`pyrite index embed`, exit 0, 9/9 embedded), which a chat agent cannot run.
**Friction:** the warning names a CLI command to an agent that has no shell. No MCP tool (any tier, I listed all of them) embeds. The `kb_index_sync` description does not say it does not embed (getting-started says the CLI `index sync` "then embed"s, so I expected the MCP one to).
**Would have helped:** `kb_index_sync` (or a new `kb_index_embed`) that settles the embedding debt, or the warning saying "an MCP client cannot fix this; ask the user to run ...". Counts as one CLI use beyond setup.
**Severity:** slowed (keyword still works, so the agent degrades quietly; but semantic is half the product pitch).

**Friction 2 — the semantic floor made a true answer read as "the KB has no answer".**
**Command:** after embedding, `kb_search {"query":"who handles visual identity","kb_name":"side-project","mode":"semantic","fields":["id","title"]}`
**Got:** `count:0, "match":"none","match_basis":"semantic: no entry reached the similarity floor"`. Priya's entry ("Freelance designer ... logo and landing page") is exactly the answer. The `kb_search` description says `match: none` "means the KB does not contain an answer; say so, do not build an answer from these results", so a careful agent would tell the user it has no record of their designer. Hybrid `"who is doing the design work"` returned Priya first but still said `"match":"none"`, `"keyword: nothing matched; semantic: best similarity 0.25, below 0.35"`.
**Friction:** the instruction to trust `none` plus a floor that is strict on short entries is a false-negative trap for memory use. Also before embedding, the same hybrid query said `keyword_match: some_terms` because the stopword "is" matched and ranked a Postgres decision above Priya; after embedding the keyword leg said "nothing matched" for the same text. Same query, two different keyword verdicts.
**Would have helped:** drop stopwords from the keyword OR fallback; a looser floor or a "closest were ..." hint when the top result is the only plausible candidate.
**Severity:** annoyed

**Friction 3 — `task_update` error recommends a status its own schema rejects, and the state machine is not in the description.**
**Command 1:** `task_update {"task_id":"send-priya-the-brand-brief","kb_name":"side-project","status":"done"}` on a fresh task.
**Got:** `isError=False`, `{"error":"Cannot move task from 'open' to 'done'. Allowed next: claimed, cancelled. Tasks follow open → claimed → in_progress → done/failed/blocked/review; walk through ... (or use 'cancelled' to retire an obsolete task from any state).","error_code":"VALIDATION_FAILED","retryable":false}`. Message is clear and fixed me in one step.
**Command 2:** `task_update {"task_id":"register-trailmix-example-domain","kb_name":"side-project","status":"cancelled"}`
**Got:** `isError=True`, plain text `Input validation error: 'cancelled' is not one of ['open', 'claimed', 'in_progress', 'blocked', 'review', 'done', 'failed']`.
**Friction:** "mark this to-do done" is the most common memory action, and it takes three calls (`task_claim`, `task_update in_progress`, `task_update done`) for a personal to-do that has no other worker. `task_update`'s description ("Update task fields (status, assignee, priority)") does not mention transitions or `task_claim`. And the advertised escape hatch `cancelled` is not in the tool's enum, so there is no way to retire a to-do from MCP.
**Would have helped:** add `cancelled` to the enum; put the state machine in the description; a one-call "complete" for unclaimed tasks.
**Severity:** slowed (cancel: blocked)

**Friction 4 — two error shapes in one server.**
Domain errors come back with `isError=False` and an `error`/`error_code` body (`ENTRY_NOT_FOUND`, `VALIDATION_FAILED`, `QUERY_SYNTAX`, `FORBIDDEN`); argument-schema errors come back `isError=True` with a plain string and no `error_code` (Friction 3, command 2). The server instructions do say "Errors return as a result containing error, error_code, and retryable", so I knew to look in the body, but a generic client that branches on `isError` misses every domain refusal, including every tier refusal and every failed `kb_create`.
**Would have helped:** set `isError=True` on the `error` envelope too, or state in the instructions that schema failures are the exception.
**Severity:** annoyed

**Friction 5 — no memory-shaped types, and an undeclared type was accepted silently, against the tool's own description.**
**Command:** `kb_orient {"kb_name":"side-project"}` then `kb_create {"kb_name":"side-project","entry_type":"decision","title":"Use Postgres not MongoDB","body":"...","tags":["trailmix","decision"]}`
**Got:** `{"created":true,"entry_id":"use-postgres-not-mongodb","file_path":".../side-project/use-postgres-not-mongodb.md"}`, no warning, file at the KB root. The `kb_create` description says an `entry_type` the kb.yaml does not declare is "refused otherwise, core types included" unless `allow_undeclared`. `kb_orient` for this empty-template KB showed `"types":[]` and a schema of note/person/organization/event/document/topic/relationship/timeline/collection, i.e. none of decision or to-do (tasks exist as `task_*` only, and appear in `kb_orient` types after the first one). I deleted the stray entry (`kb_delete` worked, `inbound_link_count 0`).
**Friction:** I guessed `decision` because the user said "decisions"; the tool accepted a type the schema never declared and the entry would not show in typed browsing as a decision type. I fell back to `note` + tag `decision` + `metadata:{"status":"decided"}`. `kb_create` has a `participants` param for events while the schema field is `actors`.
**Would have helped:** reject (or warn on) the undeclared type as documented; a "personal memory" template, or orient text suggesting how to model decisions on `note`.
**Severity:** slowed

**Friction 6 — to-dos cannot be linked, dated or tied to a person at creation, and `kb_timeline` lists them as dateless events.**
`task_create` has no `links`, `due_date` or `parent`-free relation param, so "to-do for <person-a>" needs a second `kb_link` call from some other entry (I linked a decision to the task with `related_to`; direction is awkward, link source must be the entry that carries the link). `kb_find_overdue` therefore has nothing to find. `kb_timeline {"kb_name":"side-project"}` returned my 3 tasks as events with `"date":""`, and not the two decision notes.
**Would have helped:** `due_date` and `links` on `task_create`; tasks excluded from `kb_timeline` when dateless.
**Severity:** annoyed

**Friction 7 — "what changed since last time" gives which entries, not what changed; corrections overwrite the old value.**
**Command:** `kb_recent {"kb_name":"side-project","since":"2026-11-16T15:48:50Z","fields":["id","title","updated_at"]}` returned the 4 touched entries (good), but nothing about the delta. I corrected a launch date (`kb_update` with a new `body`, 2027-02-01 -> 2027-03-15); the old value now lives only in git history if someone commits, and nothing at write or read tier shows history (`kb_commit` is admin). I wrote "CORRECTED from ..." into the body myself.
Without `fields`, `kb_recent` and `kb_list_entries` return 25 keys per entry including absolute `file_path` and `content_hash`; `kb_find_by_status` returns `metadata` as a JSON string, while `kb_get` returns it as an object (`kb_search` too, as a string).
**Would have helped:** a per-entry changed-fields/previous-value in `kb_recent`, or a read-tier `kb_history`; a compact default projection.
**Severity:** slowed

**Friction 8 — parameter naming and typing inconsistencies an agent must memorise.**
`kb_create`/`kb_get`/`kb_update`/`kb_search`: `kb_name` and (`entry_id` | `entry_type`); `kb_link`: `source_id`, `source_kb`, `target_id`, `target_kb`; `task_*`: `task_id` + `kb_name`; `kb_search` filter is `entry_type` but results key `entry_type` while `kb_update` takes `status`/`date` as top-level and `role` is not in its schema yet worked at top level. `task_create` returns `priority:7` (int); `kb_list_entries`/`kb_find_by_status` return `"priority":"7"` (string). `kb_update` with an invented key `{"bogus":1}` returned `{"updated":true}` and wrote `bogus: 1` into the person's frontmatter (documented: "stored as a custom field"), so a typo'd parameter silently pollutes the file; the result gives no `ignored`/`custom` note for it.
**Would have helped:** echo `custom_fields_written:[...]` in the `kb_update` result.
**Severity:** cosmetic to annoyed

**Friction 9 — tool-list noise for a note-taking use.**
The write tier lists 100 tools; for this task I needed ~15. `investigation_*` (all "EXPERIMENTAL: Unsupported until the 0.28 alpha"), `sw_*`, `wiki_*`, `social_*`, `zettel_*` are all present on a KB with none of those types. `kb_orient detail:"brief"` still includes the full type schema (a few KB). Not blocking, but it costs context every session.
**Would have helped:** only list plugin tools whose types the mounted KBs declare, or a `--tools core` option.
**Severity:** annoyed

**Friction 10 — keyword search matches title+body only, not tags or fields.**
`kb_search {"query":"design","mode":"keyword"}` found nothing though Priya has tag `design` and role "Freelance designer" (documented in getting-started, not in the tool description). `"Postgres-based"` auto-quoting worked; `foo AND bar-baz` gave a clear `QUERY_SYNTAX` with a fix hint (good).
**Would have helped:** one sentence in `kb_search`'s description; or index role/tags.
**Severity:** annoyed

**Had to figure out:** the task state machine (by hitting it); that decisions must be `note`s; that semantic search needs a CLI step; `kb_orient` first (the server `instructions` and the tool's `[read] Use this first` both said so, no problem there).

**Session 3 (read tier) went well:** write tools are not listed, and calling them anyway returns `{"error":"Tool 'kb_create' requires the write tier.","error_code":"FORBIDDEN","retryable":false,"suggestion":"Start the server with pyrite mcp --tier write.","required_tier":"write"}` (for `kb_create`, `task_create`, `kb_update`), no write occurred. The suggestion is aimed at an operator, not the agent; "ask the user to reconnect with write access" would suit a chat client.

**Worked well:** `kb_orient` as the first call (operational contracts, error shape, quoting rule in one result) and the `match`/`match_basis`/`legs_run`/`warnings` fields on `kb_search`, which told me exactly why semantic returned nothing; writes are plain Markdown files I could `cat` and verify (`tasks/send-priya-the-brand-brief.md` showed `status: done`), `kb_bulk_create` reported the per-entry failure without losing its sibling, and `kb_get` with `fields` and `kb_search` with `fields` kept results small.

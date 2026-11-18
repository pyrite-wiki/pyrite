## 2026-11-17 · hallway test: software KB backlog/ADRs/components, then tasks with parents and children, plus `init -t task` and `init -t research-queue` · claude-sonnet-5-5 · Pyrite 96bb4936 (0.26.4 candidate)

Isolated HOME and Pyrite config/data directories. Semantic search not tested. Invented subjects only (Billing API, Ledger, City of Harbor Falls).

**Friction 1 - `pyrite sw` does not exist after the documented install, and the docs point at a path a user does not have.**
**Command:** `uv pip install "pyrite[server,cli] @ git+https://github.com/pyrite-wiki/pyrite@96bb4936"`, then `pyrite init -t software -p proj`, then `pyrite sw --help`
**Expected:** `sw` commands (init -t software succeeded and listed the ADR/backlog types).
**Got:** `Error: No such command 'sw'`; `pyrite extension list` shows `count: 0`. getting-started.md says `pip install -e extensions/software-kb`, which only works inside a clone. I cloned the repo and installed `extensions/software-kb` from the clone.
**Would have helped:** `init -t software` printing "install the software-kb extension for `pyrite sw`" with a git-URL install line (`pyrite-software-kb @ git+...#subdirectory=extensions/software-kb`); README's install line for a non-clone user.
**Severity:** slowed

**Friction 2 - `sw log` fails on a KB made by `init -t software` (possible regression).**
**Command:** `pyrite sw log crash-on-empty-invoice -s "investigated"` (also on a second item)
**Expected:** a work log entry (0.26.3 testers logged sessions fine).
**Got:** `Error: Invalid work_log for KB 'proj': entry_type: known_type (expected [adr, backlog_item, component, design_doc, runbook, standard, task], got 'work_log'). Add work_log under types: in this KB's kb.yaml, with subdirectory: work-logs/ and required: [title].` The message is clear, but the stock software template's kb.yaml has no work_log type, so one of the kanban verbs is dead on a fresh KB. No file was written; claim/submit/review all worked.
**Would have helped:** the template declaring `work_log`, or `sw log` creating the type.
**Severity:** slowed (the fix is a hand edit of kb.yaml)

**Friction 3 - repeated `--field` on a list field keeps only the last value (data loss), and the field is a bare string.**
**Command:** `pyrite task create "..." -k rq --field sources_wanted=budget --field sources_wanted=audit` (research-queue template, `sources_wanted` declared `type: list`)
**Got:** frontmatter `sources_wanted: audit`; `budget` is gone, no warning. Same scalar-for-list as 0.26.3's friction 8 (`dependencies: ledger` on `create -f dependencies=ledger`). The pending #1707 should cover the scalar; the silent drop of the first value should be checked by it too.
**Would have helped:** a list field accumulating repeated flags, or a refusal.
**Severity:** annoyed (silent loss of input)

**Friction 4 - `task get` returns `priority` as a string; `task create`/`update`/`list` return an int.**
**Command:** `task get ship-invoice-fix-release -k proj` -> `"priority": "8"`; `task list` -> `"priority": 8`; `task update` -> `9`.
**Why it matters:** #1503 says a field has one JSON type on every reader; here one reader disagrees. Sorting or comparing in a script breaks.
**Severity:** annoyed

**Friction 5 - a parent whose children are all resolved shows stored status `open` but effective status `done`; filters and the table use the stored one.**
**Commands:** cancelled two children and finished one, then `task get`/`task list --format json` (parent `derived.effective_status: done`, `completion.complete: true`), but `task list -s done` omits the parent and `--format rich` shows `open`. `task claim` on it says "counts as done ... mark the completed parent done with ..." (a good message). Cancelling the parent afterwards worked; the child tasks stayed cancelled/done (cancelling a parent does not cascade, and nothing says so).
**Would have helped:** a rich/`-s` view of effective status, or a `--effective` filter; one line in `task update --help` about cascade.
**Severity:** slowed

**Friction 6 - a lost claim race is `outcome: nothing`, exit 1, with a non-standard error shape.**
**Command:** second `task claim patch-handler -a agent:bob` -> `{"claimed": false, "error": "Entry 'patch-handler' is 'claimed', not 'open'", "current_status": "claimed", "outcome": "nothing"}` (no `error_code`, no `retryable`). Other refusals (`task update` bad transition, `task get nope`) use the flat `error_code` shape. An agent branching on `error_code` sees none here.
**Severity:** annoyed

**Friction 7 - flags and vocabulary disagree between `sw`, `task` and core (re-confirmed).**
- Priority: backlog items take `critical|high|medium|low`; tasks take an int 1-10 (`--priority 7`); `task create` and `create -t task -f priority=4` both work, but the two KBs' items are not comparable.
- Status words: backlog `proposed/accepted/in_progress/review/done`; tasks `open/claimed/in_progress/blocked/review/done/failed/cancelled`. `task claim` moves to `claimed`, `sw claim` moves to `in_progress`.
- KB: `task` commands require `-k` for write verbs but `task list` without `-k` lists every KB (count 10 across proj, tk, rq); `sw` infers a KB except `context-for-item` and `refine` (both: `Missing option '--kb'`, though only one KB had backlog items; `sw check-ready`, `board` infer it).
- `sw review` has `--reviewer` (no `-r`; I typed `-r` and got `No such option`); `sw transition -r` is `--reason`; `task reset -r` is `--reason`.
- `--format`: `task *`, `sw backlog`, `sw board` accept it (`sw` read verbs json/rich). `sw new-adr`, `sw claim`, `sw transition`, `sw submit`, `sw review`, `sw log`, `sw prioritize` do not (`No such option: --format`), and print prose. Core `create`/`update`/`link` return the `outcome` envelope. Tasks write verbs return `outcome` too, so the split is `sw` write verbs versus everything else.
- `sw` read verbs print compact one-line JSON; `task`/core print pretty JSON.
**Severity:** annoyed

**Friction 8 - `new-adr` unchanged from 0.26.3.** Still a TODO skeleton, `deciders: []`, no body option. Output now shows the file `adrs/adr-0001.md` (the filename matches the id, which helps). Updating via `pyrite update adr-0001 --body` replaced the whole body, headings included.
**Severity:** annoyed

**Friction 9 - smaller things.**
- `pull-next` with only `proposed` items still says "No accepted items available" without naming `sw transition <id> accepted`; `check-ready` says `ready: true` for a `proposed` item; `transition ... ready` still errors with allowed statuses (accepted -> `in_progress, wont_do, proposed, deferred`); `transition --help` still lists no statuses. The refusal text itself is good.
- `pull-next` when nothing is in the KB-less call and `refine` without `-k` returns `{"error": "This tool needs a KB: a board config is read from a KB's own root."}` with `error_code VALIDATION_FAILED` although one KB exists.
- `pyrite list` and `orient` report `kb_type: generic` for software, task and research-queue KBs created by `init -t`. Still there.
- `qa validate proj` on 7 backlog/ADR/component entries and 5 tasks: 35 issues, 21 `rubric_violation`, 9 `orphan_entry`, 5 `empty_body`; none are real problems in a fresh KB. Noise hides real issues.
- `sw milestones` and `sw epics` print `[]` with no hint on how to create one.
- `task update -s bogus` answers "Cannot move task from 'claimed' to 'bogus'", not "unknown status", but lists the allowed next states and the lifecycle: good.
**Severity:** annoyed

**Friction 10 - new templates `init -t task` and `init -t research-queue` work, with one gap.** Both created kb.yaml with a `task` type, workflow enforcement and status/priority validation; `task create/claim/update/list` worked on both; research-queue adds `target_kb`, `question`, `sources_wanted` fields plus `ai_instructions`/`guidelines` ("link follow-ups to the originating ticket"); `-p` and `-l ...:follows` both worked. Gap: `entries_indexed: 0`, `types: ["task"]` in the init JSON is fine, but nothing tells the user the next command (`task create -k <name>`). Orient on rq still `kb_type: generic`. `init -t task` creates only `kb.yaml` (no `tasks/` dir until the first task; harmless).
**Severity:** annoyed

**JSON-contract checks:** `init`, `create`, `update`, `link`-style outputs, `task create/claim/update/list/get/decompose/checkpoint/reset` and the unknown-KB/unknown-task refusals match the documented `outcome` / `error_code` / `retryable` shape (NOT_FOUND, KB_NOT_FOUND, VALIDATION_FAILED, USAGE_ERROR). Misses: Friction 4 (priority string), Friction 6 (claim refusal without `error_code`), `sw` write verbs print prose, and `sw` read-verb errors (`{"error":..., "error_code": "NOT_FOUND"}`) omit `outcome`. docs/json-contracts.md's `--format` table lists only create/add/delete/link/update/rename/import/task create, so none of the `sw`, `task get/list/claim` behaviours above can be checked against it.

## Release blockers
None that crash, lose existing data or fail to install. Candidates to look at before the cut: `sw log` failing on the stock software template (Friction 2; if 0.26.3 logged fine this is worse than 0.26.3), and `--field` repeated on a list field silently dropping earlier values (Friction 3; the #1707 fix may or may not cover it).

## Re-check of 0.26.3 findings (feedback/2026-11-16-hallway-cli-software-kb.md)
- F1 pull-next reason/status vocabulary: Still there (reason names the status, not the command; `transition --help` lists no statuses; check-ready true vs pull-next none).
- F2 `-k` spelled four ways: Still there (`qa validate proj` positional not re-run; `context-for-item` and `refine` still need `-k`).
- F3 short flags differ (`-l`, `-f`): Not re-tested.
- F4 `sw` write verbs lack `--format`: Still there (`new-adr`, `claim` refuse `--format`).
- F5 `new-adr` skeleton, no body option: Still there; Changed slightly (filename now `adr-0001.md`, matching the id).
- F6 `create --template` shows no type fields / `kb_type: generic`: `kb_type` Still there; template not re-run.
- F7 `context-for-item` empty `body_preview`, pull-next counts 0: Fixed (body_preview "Billing service"; pull-next `component_count: 1`).
- F8 list field stored as a string: Still there (awaits #1707).
- F9 smaller: qa tag noise Changed (now rubric/orphan/empty_body noise, 35 issues); `transition done` after review error not re-run; init no git: unchanged, not re-run.
Count: 1 fixed, 1 changed, 6 still there, 2 not re-tested.

## What worked well
- The whole backlog cycle (create with `-l target:affects`, `check-ready`, accept, claim, submit, review, board) runs from the CLI; refusals list the allowed next states.
- `task` state machine messages are excellent: they name the lifecycle, the allowed next states, and for a claim on a parent whose children are resolved, the exact commands to fix it.
- `task decompose` with repeated `-c` returns parent completion in the same JSON; `derived.completion`/`effective_status` on parents is a real help once you know to read it.
- `checkpoint`, `reset` (audit reason, prior_status) and `--comment/--by` on update are the right verbs for agent work.
- `init -t task` and `-t research-queue` work first time, and the research-queue schema guidance reads as intended for agents.
- Typed JSON errors with `suggestion` (`run pyrite task list -k proj`) made recovery cheap.

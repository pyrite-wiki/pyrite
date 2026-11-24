# Hallway test: upgrade 0.26.3 -> 0.26.4 candidate
Date: 2026-11-18 | Scenario: existing user upgrades (two KBs built on v0.26.3, then 96bb4936 with the same HOME/config/data) | Version: 96bb4936 (reports itself as 0.26.3) | Model: Sonnet 5.5
Notes read: docs/upgrading.md from PR #1737 head 531711e9. No 0.26.3 feedback file for this scenario.

Setup: `old/.venv` = v0.26.3, `new/.venv` = 96bb4936 (direct_url.json confirms the commit). Notes KB (generic, type `note` with number/checkbox/list fields) and software KB (template `software`, plus number/checkbox/list fields I declared). On disk: importance 7.5, 15, 0, 8.5, 11, 4.2; `score: "7"`, `estimate: "5"`, task `priority: "2"` as text; `reviewed: 1`, `blocked: 1/0`; `authors: Acme Paving LLC` and `labels: firmware` as plain strings in list fields; a `_Templates` folder with entries in each KB; a task tree (root, 2 children, a dependency); wikilinks incl. one broken. 12 files, md5 snapshotted before the upgrade.

Method: restored the 0.26.3 index, ran read commands first, then followed the notes in order. Every command was checked for file changes against the snapshot.

## Per-note verdict (did it happen, did the note say what to do)
- **_Templates skipped by the index walk (#1552):** Happened as written. Ignoring the note and syncing: "Removed: 3", the entries were gone from `get` and `list-entries`, files untouched, and `index health` named the 3 files under `unlisted_entry_files` (reason "templates"). Renaming first (I used `Templates-archive`): sync "Updated: 3", entries kept. The note's remedy works. Note: it says "Rename the folder", but the renamed entries then show as `subdirectory_mismatches` in health (warning). Not mentioned.
- **importance whole 1-10 (#1537/#1588):** Happened as written. `update -f importance=5.5` is refused (VALIDATION_FAILED, exit 1, "use 1-10"). Values already on disk (7.5, 15, 0, 8.5, 11) were kept with a warning ("violation already on disk; kept") and the file bytes were unchanged. Note says what to expect, not how to fix existing values. `qa validate` says "Rename the key (for example 'note_importance')" for fractional values, which is a good pointer. 15/0/11 get only `importance_range` warnings.
- **Numbers read as numbers (#1503):** Partly false for old data. After sync and after `index build`, `get n1` still shows `score: "7"` (text) and `reviewed: 1` (int); `get b1` shows `estimate: "5"`, `blocked: 1`. Only the task `priority: "2"` reads as number 2 (the note's "task priority is a number on kb_get" holds). `qa validate` says of `score: "7"`, "Pyrite reads it as that text", which contradicts the note's "reads as a number ... where it read as text before". `number_as_text` is reported as the note says, with the fix ("Write it as a plain number"). The checkbox `1` shows as `schema_violation` ("expected boolean, got int"), with no `number_as_text`-style hint.
- **Write-back unquotes "7" / rewrites 1/0 (#1503):** Not reproduced as worded. Updating only the title of n1, b1 and t-root changed only the title line; `"7"`, `"5"`, `"2"` and `1` stayed. The value is rewritten only when I write that field myself (`-f score=8` -> `score: 8`; `-f priority=4` -> `priority: 4`; `-f blocked=false` -> `false`). That is the safe behaviour, but the note reads as if any write-back converts; it should say "when you write that field". No data was rewritten without being asked.
- **Metadata object / aliases list in search:** Happened. `search --fields '*'` returns `metadata` as a dict.
- **Exit codes, index sync exit 3, tier errors, board.yaml, REST 422, create type, known-entities kb.yaml, orient format, typer, soupsieve, symlink delete, smaller refusals:** Not exercised by this data (no unreadable folder, no board.yaml, no REST, no known-entities KB). soupsieve 4.1.1 / typer 0.29.1 were installed on Python 3.12 with no action needed.
- **Anything changed with no note:** see F1-F4.

## Friction entries
### 1. Candidate reports its version as 0.26.3
Command: `new/.venv/bin/pyrite --version`; `uv pip list`. Expected: 0.26.4 (or a dev marker). Got: `0.26.3`; the same string for old and new venv, so a user cannot tell the upgrade happened. Would have helped: bump before the cut (likely pre-release only). Severity: annoyed.

### 2. A checkbox stored as 1 cannot be set to 0 and the error names an internal type
Command: `update b1 -k sw -f blocked=0`. Expected: either coerced or a plain error. Got: `SCHEMA_VIOLATION ... (expected boolean, got 'ScalarInt')`; nothing written (good). Success warnings also say `"got": "ScalarInt"` for n3's `reviewed: 0`. Would have helped: "use true or false", and "int" instead of the ruamel class name. Severity: slowed.

### 3. Stored-as-text numbers and `1` in checkboxes keep reading as before
Command: `get n1 -k notes`, `search --fields '*'` after sync and `index build`. Expected (per #1503 note): `score: 7`, `reviewed: true`. Got: `"7"` and `1`. Would have helped: the note should say a value already on disk is read as stored until you rewrite it, and name the one-line fix (re-set it with `update -f`). Severity: slowed.

### 4. String in a list field: append refused with no remedy
Command: `update b1 -k sw --append labels=second` where `labels: firmware`. Got: "labels is not a list; cannot append to it" (exit 1). Expected: a pointer to set it as a list first. (Not in the notes; #1707 is out of this build.) Severity: annoyed. `qa validate` raised nothing for this string-in-list-field, so nothing told me it was a problem before the failure.

### 5. Renamed template folder shows as subdirectory mismatch
Command: `index health` after renaming `_Templates`. Got: 3 `subdirectory_mismatches` and status warning (before: unhealthy). Expected: note to say that this will appear. Severity: annoyed.

### 6. `qa validate` takes `kb_name` as an argument, not `-k`
Command: `qa validate -k notes` -> USAGE_ERROR, exit 2. Other commands take `-k`/`--kb`, and `kb reindex` requires `--kb`. Same on 0.26.3. Severity: annoyed.

### 7. Software KB template: no `sw` command without the extension
Command: `pyrite sw ...` -> "No such command 'sw'" with `pyrite[server,cli]` only; `init -t software` works and registers the KB as type "generic". Tasks list through `list-entries -t task`. The scenario's "list tasks" needed that. Severity: annoyed.

## Data safety
After sync, build, searches, gets, qa validate, ci and health: zero file changes (md5 compare). Writes only happened on my explicit `update`, each touching only the named field(s) (diffs checked, importance 7.5/8.5 kept in place). A refused multi-field update wrote nothing.

## Release blockers
none. No traceback, no data rewritten unasked, index sync/search/get/list all worked on a 0.26.3 index with no migration step. (Note #1503 is overstated; see F3.)

## Re-check of 0.26.3 findings
No matching file for this scenario; not applicable.

## What worked well
- Upgrading in place: the 0.26.3 index opened with new code and `get`/`search` worked before any sync.
- The `_Templates` note was exactly right, including what `index health` shows; removed rows came back after the rename.
- "violation already on disk; kept" warnings with the file left byte-identical, and the fractional-importance message that suggests renaming the key.
- `number_as_text` in `qa validate` names the file, field and a fix.
- A refused update is atomic and says so.

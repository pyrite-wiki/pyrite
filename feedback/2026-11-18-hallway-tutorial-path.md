# Hallway test, 2026-11-17: "a wiki in 20 minutes" deterministic half (epic #1661)
Scenario: newcomer follows the planned tutorial by hand, no agents. Version: `pyrite --version` prints 0.26.3 (commit 96bb4936, the 0.26.4 candidate). Model: Sonnet 5.5. No matching 0.26.3 feedback file.
Isolated HOME and Pyrite config/data directories. Semantic search not tested (`--no-embed`).

## Timings (machine time only; excludes my reading and typing)
| Step | Time | Landed? |
|---|---|---|
| 1 install (uv venv + pip from git) | 8 s | yes |
| 2 clone demo / `kb discover --add` / `index build --no-embed` / search | 1 s / 3 s / 25 s / <1 s | payoffs 1, 2: 26 KBs, 4,383 entries; "technical debt" hit agile, anderson, devops, poppendiecks, reinertsen (also richards, schneier, weinberg) |
| 3 orient + `qa checkers -k poppendiecks` | 1 s | payoff 3 |
| 4 hand-edit ward-cunningham.md, get, sync, search | 3 s | payoff 4: `get` saw it at once; search warned "index may be stale", found 0 until `index sync -k agile` |
| 5 new KB `ward` (hand-written kb.yaml, trimmed from poppendiecks' 341 lines), discover --add, refused create | 1 s machine; kb.yaml authoring is the real cost (about 2 min of typing) | payoff 5: SCHEMA_VIOLATION naming allowed values, nothing written |
| 6 three hand entries + sync + backlinks | 2 s | payoff 6: ward entries show in backlinks of agile:technical-debt, agile:refactoring, agile:ward-cunningham, deming:russell-ackoff, deming:pdsa-cycle...; agile/deming files untouched (`git status` shows only my own edit and new dirs) |
| 7 `init -t research-queue`, 3 tickets, list | <1 s each | payoff 8 |
| 8 claim, second claim, start, done, follow-up | about 1 s | payoff 9: second claim exit 1 with `current_status` |
| 9 qa on ward | validate 1 s, assess 1 s | payoff 7 partial (see 6, 7) |
| 10 export site | <1 s | produced 10 files |
Total machine time about 3 minutes; the 20-minute budget is dominated by authoring kb.yaml, not by Pyrite.

## Friction
1. **Command:** `pyrite task claim <id> -k q --as agent:x` (guessed). **Expected:** `--as`. **Got:** USAGE_ERROR; the flag is the required `--assignee/-a`. **Would have helped:** tutorial showing the exact claim line. Severity: annoyed.
2. **Command:** `task update <id> --status in_progress` / `--status done` on an open ticket. **Got:** refused, "Allowed next: claimed, cancelled". Error message is clear. A hand tick needs claim, then in_progress, then done (three calls); the epic's "task start" is not present. Severity: annoyed.
3. **Command:** `pyrite qa assess ward` then `qa status ward`/`qa validate ward`. **Expected:** judgment of my 3 entries. **Got:** assess writes `qa-<entry>-<ts>.md` assessment records INTO the KB folder (ward/qa-*.md, git-visible, untracked files in the user's KB) and they become entries. Afterwards `qa validate` reports 6 issues (3 orphan_entry + 3 "no outgoing links" rubric_violation) about the assessment records themselves, and `qa status` says `total_entries 6`, `total_issues 6` while the same status says coverage pass 3/3 and assess said 0 issues. `qa gaps` then counts 6 entries and the export site includes a qa_assessment folder. Before assess, `qa validate ward` returned 0 issues. A newcomer sees the QA going from clean to 6 warnings by running QA. Severity: slowed (confusing; possibly a defect, files written to the KB root and fed back into QA).
4. **Command:** `qa assess ward --tier 2`. **Got:** AI_NOT_CONFIGURED with a suggestion; fine, but means payoff 7 (rubric judged) cannot be seen without a model. The rubric items are only listed by `qa checkers`. Severity: annoyed (expected, documented in the epic).
5. **Command:** the QA/export commands take the KB three different ways: `qa validate ward` and `qa assess ward` and `qa status ward` positional; `qa checkers -k`, `qa gaps -k`, `export site -k` flag; `export -k` is refused (subcommand needed, `export site`). I got USAGE_ERROR four times (`qa status -k`, `qa assess -k`, `export -k`, `export site ward`). Would have helped: accept both forms, or show the exact line in the tutorial. Severity: slowed.
6. **Command:** `pyrite kb discover . --add` run inside the demo clone after step 2. **Got:** lists all 26 KBs, every one with `"already_registered": false`, although 25 were registered (later `kb discover .` without --add still showed 0 true). Unclear whether it re-added or ignored them; `kb list` showed 27 total and no duplicates. The output is 25 KB blocks long to find the one new entry. Would have helped: `already_registered` correct and a "new: ward" summary line. Severity: annoyed.
7. **Command:** `pyrite search zebrafishquark -k agile` after hand edit, before sync. **Got:** "Warning: index may be stale ... run pyrite index sync" plus 0 results. Good message. Payoff 4 lands well; the explanation of "files decide, index catches up" is exactly what the warning says. Severity: delighted.
8. **Command:** `pyrite backlinks technical-debt -k agile` after frontmatter link `relation: develops`. **Got:** my entry listed twice (relations `related_to` and `wikilinked_by`); the frontmatter link relation `develops` shows as `related_to` in the backlink although `get` shows `develops` forward. Inverse label unexplained. Severity: annoyed. Also JSON output lists ward entries among many agile ones (technical-debt has 7+ backlinks), so a newcomer must filter to see their own; no `--kb` filter on backlinks seen. Severity: slowed.
9. **Command:** `task list -k ward-queue --format rich`. **Got:** IDs wrapped over 3 lines at 80 columns (IDs are the full title slug, 50-60 chars), hard to copy into `task claim`. List order was not by priority (3, 8, 5). Would have helped: shorter ticket ids or a `--fields id,priority` list. Severity: annoyed.
10. **Command:** `task create "<q>" --field target_kb=ward`. **Got:** ticket file has `question: ""`, title carried the question; template declares `question` and `sources_wanted` but nothing tells a newcomer to fill them (no flag, `--field question=...` presumably). Severity: annoyed.
11. **Command:** `cat ward-queue/tasks/<id>.md` after claim, in_progress, done. **Expected:** audit log of all transitions (the epic says "the ticket file holds the audit log"). **Got:** `status_change_log` has only the last transition (in_progress -> done, `by: operator`, comment); the claim and start are absent. Assignee is kept. Severity: slowed (payoff 10 audit claim is not true for a hand tick).
12. **Command:** `pyrite task get` shows `"outcome": "done"` on a read, and an open ticket; harmless but odd. Severity: annoyed.
13. `pyrite --version` prints 0.26.3 for the 0.26.4 candidate (expected before the version bump). Severity: annoyed.
14. `ward-queue` was initialised inside the demo clone (my choice of -p), making it an untracked sibling KB in the demo repo; the tutorial needs to say where to put the tracker. Severity: annoyed.

## Release blockers
None. No traceback, no data loss. Friction 3 (QA writes assessment records into the KB root and then judges them) is the closest to a defect; I would look at it before the cut but it is not worse than 0.26.3 as far as I know (not compared).

## Re-check of 0.26.3 findings
No matching feedback file was provided; not rechecked.

## What worked well
- 8-second install; 25-second index of 26 KBs; one search spans all KBs with an explicit stale-index warning and a clear "match_basis".
- Orient and `qa checkers` explain a KB's rubric in one call.
- A hand edit shows in `get` immediately and in search after one `index sync`.
- Schema refusal names the allowed values and writes nothing.
- Cross-KB wikilinks (`[[agile:technical-debt]]`) and frontmatter `kb:` links resolved with no registration and appear as backlinks in the other KBs without touching their files.
- `init -t research-queue` exists and gives a ticket schema with target_kb, question, sources_wanted; second claim refused with exit 1 and the current status.
- Error messages on invalid status transitions say the allowed next states.

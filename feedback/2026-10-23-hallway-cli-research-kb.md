## 2026-10-23 · hallway test, CLI-only: set up and maintain a reading-project KB (research template) · claude-sonnet-5-5

Setup: scratch `HOME` (`export HOME=<scratch>/home`), CLI `<scratch>` (`$P` below), KB at `<scratch>/reading-kb`, git-initialised by me. Everything below was run with the CLI only. Docs read first: `pyrite --help`, `pyrite orient`, README Quick Start, `docs/getting-started.md`, command `--help`. No test suite was run. People and orgs are placeholders (Author A/B, Editor C, Institute D); works are "Source One/Two".

Placeholder table: `<author-a>` Author A, `<author-b>` Author B, `<editor-c>` Editor C, `<org-d>` Institute D.

### What the docs said, and whether it was true
- getting-started: `pyrite init --template research --path ...` works exactly as written; it does not `git init` (true, doc says so). `pyrite kb commit` exists (true).
- getting-started: a semantic search straight after writes returns nothing and prints `warning: semantic leg skipped ... run pyrite index embed`. True, verbatim. After `pyrite index embed` (9.7 s, model fetched in a fresh HOME) semantic search worked and returned `similarity` per result.
- getting-started says "Search matches an entry's title and body, not its tags". True; `--tag` is the separate route (see Friction 9).
- `pyrite orient` with no KB returns a good contract block (indexing, error shape, quoting). `pyrite orient -k reading-kb` listed `"total_entries": 0, "types": [], "top_tags": []` right after `init` because it was empty. That was correct.

## Frictions

**Friction 1 — a hand-edited malformed-YAML file silently drops out of `ci` and `qa validate`; only `index health` names it. (looks like a bug / gap; reproduced twice)**
**Command:** I broke `people/author-b.md` with `sed -i '' 's/^tags:$/tags: [unclosed/' people/author-b.md`, then ran `$P index sync` and `$P ci --kb reading-kb 2>/dev/null` (stderr dropped to hide log noise). I repeated it after `git checkout`-ing the other deliberately broken files so only this one remained.
**Expected:** `ci` fails (or at least names) the unparseable file; it is the worst thing a hand edit can do.
**Got:** `index sync` printed `Removed: 1` then, on the second run, `Malformed: 1 file(s) skipped` with an empty bullet (`    • ` then the path on the next two lines, wrapped mid-path) and exit 0. `ci` said `11 entries validated`, with only secondary symptoms: `WARNING: source-two ... links to non-existent 'author-b'` and the same for `1992-rebuttal-debate`. No line says a file failed to parse. `qa validate reading-kb` also gave only the broken-link warnings. `$P index health` is the only command that lists `malformed_frontmatter` (with the YAML error), and it classifies it as `status: warning` and exits 0. The entry vanished from search and `get` (`get author-b` prints two WARNING log lines).
**Friction:** A reviewer reading `ci` output sees "broken link to author-b" and hunts for a missing entry that exists on disk. "12 entries" became "11 entries" with no explanation.
**Would have helped:** `ci` and `qa validate` report a `malformed_frontmatter` error per unparseable file; `index health` makes it `unhealthy`; `sync` prints the path on one line, no empty bullet.
**Severity:** slowed (data-loss-shaped: the entry is invisible until someone runs the right command)

**Friction 2 — a typo in a frontmatter key (`tpye:` for `type:`) is accepted silently and nothing in validation reports it. (reproduced twice)**
**Command:** `python3` edit of `notes/note-on-skimming.md` replacing `type: note` with `tpye: note`; then `$P get note-on-skimming -k reading-kb --format json`, `$P index sync`, `$P index health`, `$P qa validate reading-kb`, `$P ci --kb reading-kb`.
**Expected:** a warning that `type` is missing / `tpye` is an unknown key.
**Got:** `get` and `sync` each print one stderr line: `[WARNING] pyrite.models.core_types: Entry frontmatter missing 'type:' — falling back to 'note' (id=note-on-skimming, ... available_keys=[... 'tpye'])`. The entry is indexed as a note. `index health`, `qa validate`, `ci` and `kb validate` say nothing about it. Because the fallback type was `note`, which happens to be right, the typo is invisible. Had it been `tpye: person` or `tpye: event`, the entry would silently become a note, and an `event` would drop off the timeline. I did not run that variant.
**Friction:** The warning is a log line (stderr), visible only if you watch stderr; an agent that pipes `2>/dev/null` or parses stdout never sees it. The log line itself is good (it lists `available_keys`, which shows the typo).
**Would have helped:** a validator rule `missing_type` (and ideally "unknown frontmatter key close to a known one: did you mean `type`?") surfaced in `qa validate` / `ci` / `index health`.
**Severity:** annoyed

**Friction 3 — `update -f` accepts values `create` rejects; hand edits and `update` can plant a non-date in an event's `date`.**
**Command:** `$P update 1992-rebuttal-debate -k reading-kb -f date=nextweek` (exit 0, `"updated": true`, file now has `date: "nextweek"`). Compare `$P create -k reading-kb -t event --title "Bad date" --date 1990-13-45 --body x` which gives `VALIDATION_FAILED: Invalid date format: 1990-13-45 (expected YYYY-MM-DD)`. A hand edit to `date: "June 1990"` was also indexed.
**Got:** the timeline then sorted `June 1990` as a string, after 1992 (`$P timeline -k reading-kb --sort asc` printed 1985, 1992, "June 1990"). `ci`/`qa validate` do catch it afterwards (`invalid_date`, error), which is good; the write itself is not guarded.
**Would have helped:** `update` runs the same date validation as `create`.
**Severity:** annoyed (one run; not repeated)

**Friction 4 — `rename` accepts any string as the new id, including spaces and capitals, and nothing says so.**
**Command:** `$P rename note-on-skimming "Note On Skimming" -k reading-kb` (and the `--dry-run` form before it). Result: `"renamed": true`, file `notes/Note On Skimming.md`, `id: Note On Skimming`. Links were rewritten and `links check` was clean. I renamed it back.
**Expected:** a slug check (ids created via `create` are slugs: `note-on-skimming`).
**Friction:** an agent that passes a title instead of an id creates a path with spaces in a git repo and a `[[Note On Skimming]]` wikilink form that no other tool in the repo produces.
**Would have helped:** reject or warn when `new_id` is not a slug of itself; echo "id will be X" in the dry run.
**Severity:** cosmetic/annoyed

**Friction 5 — the same job (name the KB) is spelled four ways, and one is positional.**
**Commands:** `search -k`, `ci --kb` (no `-k`; `ci -k reading-kb` gives `No such option: -k`), `qa validate reading-kb` (positional; `qa validate -k reading-kb` gives `No such option: -k`), `kb validate reading-kb`, `kb health -k` (`kb health reading-kb` gives `Missing option '--kb' / '-k'`). `index health` takes `-k`, `index sync` takes no KB at all.
**Got:** I guessed `-k` on `qa validate` and `ci` and got `USAGE_ERROR` each time. The error JSON is clean and parseable (good) but its suggestion is only "Try ... --help".
**Would have helped:** accept `-k/--kb` everywhere; treat a positional KB as an alias.
**Severity:** slowed

**Friction 6 — five commands validate a KB and none says which one is "the" check.**
**Commands:** `pyrite ci --kb`, `pyrite qa validate`, `pyrite kb validate`, `pyrite index health`, `pyrite links check`. On the same broken state they disagreed: `kb validate reading-kb` returned `"all_valid": true` (it checks types and required fields only) while `ci` failed with 3 warnings; `index health` returned status `warning` and exit 0; `ci` exited 1 on warnings. Which one gates a hand-edit session? I ended up running all of them and diffing.
**Would have helped:** one paragraph in getting-started ("to check a KB after editing by hand, run `pyrite ci --kb X` and `pyrite index health -k X`") plus naming each command's scope in its first help line. getting-started never mentions validation at all.
**Severity:** slowed

**Friction 7 — `links check` JSON counts are named misleadingly.**
**Command:** `$P links check -k reading-kb --format json` on a healthy KB with ~14 wikilinks returned `"missing_targets": 0, "total_references": 0`. On the broken state it returned `"total_references": 1` (the one broken link). So `total_references` is "broken references", not references.
**Friction:** I first read `0` as "the command did not see any links". Output is `outcome: nothing` with exit 1 when broken, which the help documents, but `nothing` for "found something" is odd.
**Would have helped:** rename to `broken_references`, or report the total links checked.
**Severity:** cosmetic

**Friction 8 — the research template has no "source" type, and the persona asked for sources.**
**Command:** `$P init --template research --path ...` gives `note`, `event`, `person`, `organization`. I modelled sources as `note` with `-f source_ref="..."` (a declared optional field on `note`, and it worked, written as a top-level frontmatter key). `create -t source` would have given `UNDECLARED_TYPE` (I saw that error for `-t nonsense`, and it was excellent: it lists declared types and the `--allow-undeclared` escape).
**Would have helped:** `reading`/`source` mentioned among templates, or a `book`/`source` type in `research`. I found no template that fits a reading project: `getting-started` lists templates by name only.
**Severity:** annoyed

**Friction 9 — small CLI inconsistencies I hit by guessing.**
- `create` has `--status`, `update` does not (`Error: No such option: --status Did you mean --tags?`). I used `update ... -f status=in-progress`, which worked. The "Did you mean --tags" guess is wrong.
- `search --tags debate` is wrong (`No such option: --tags (Possible options: --state, --status, --tag)`); the right one is `--tag`. That suggestion was helpful.
- `create -k` is not marked required in `--help`, but omitting it gives `VALIDATION_FAILED: --kb is required (unless using --template)`, while `update`/`backlinks` give `USAGE_ERROR: Missing option '--kb' / '-k'`. Same mistake, two error codes. `search` with no `-k` quietly searches every KB, and `get` with no `-k` works unless the id is ambiguous.
- `index sync --format json` fails (`No such option: --format`); `sync` and `embed` print human text only, so an agent can't parse the `Malformed:` count (Friction 1).
- `timeline` defaults to newest-first; a "timeline" for a reader wants `--sort asc`. `--format markdown` for it is nice.
- `rename --help` shows `Rewrite [] / [] wikilinks` — the `[[old]]`/`[[new]]` placeholders were eaten by the help renderer.
- `pyrite kb commit -k reading-kb -m "..."` with nothing to commit prints `No commit: No changes to commit`, exit 0 (fine).
- Error codes differ for the same fact: `get source-one` (renamed away) gives `ENTRY_NOT_FOUND`, `backlinks source-one` gives `NOT_FOUND`.
**Severity:** annoyed (collectively)

**Friction 10 — export has no entry-level format; only `site` (Quartz) and `collection` (NotebookLM).**
**Command:** `$P export all` (guess) gave `No such command 'all'`. `$P export site -k reading-kb -o <scratch>/site-out` worked (`Exported 12 entries`, 17 files; wikilinks kept as `[[id]]`; `type`, `id`, `date` are dropped from the exported frontmatter, so an exported event loses its date). The output line `Output: ` is followed by the wrapped path on the next lines.
**Expected:** `export` to JSON/markdown bundle for a researcher who wants to hand the KB to someone.
**Would have helped:** a one-line mention in getting-started of what `export` is for; the `--init` flag is only in `--help`.
**Severity:** cosmetic

## What worked well
- Every error is JSON on stdout with `error_code`, `retryable`, and usually `suggestion`. The three mistakes I tried deliberately were handled best of anything in the tool:
  - Wrong KB (`-k reading-kbb`): `KB_NOT_FOUND` with `"did_you_mean": ["reading-kb"]`, exit 1.
  - Ambiguous id (`get author-a` with the same id in two KBs): `AMBIGUOUS` with `candidate_kbs` and a suggestion naming `-k`, exit 1.
  - Missing required field (`create -t event` with no date): `VALIDATION_FAILED: Event must have a date`; undeclared type: `UNDECLARED_TYPE` listing declared types; duplicate title: `ENTRY_EXISTS` with the fix.
- `rename` rewrote 5 wikilinks across 5 files in one call, reported `index_verified: true`, and `backlinks` + `links check` agreed afterwards. This is the feature I'd trust most.
- Search JSON: `match`, `match_basis`, `legs_run` and the pre-`embed` warning told me exactly why semantic search was empty and what to run. Semantic search found the right entry for a query that shared no keyword ("careful reading help concentration" -> source note first).
- `index health` JSON is thorough: it caught the missing title, the malformed file and stale entries, and exit-1-on-unhealthy is documented in its help.
- `ci` text output is readable and its exit codes behave (checked: `ci` exit 1 with warnings; never piped when I read `$?` except where I wrote `PIPESTATUS`).
- `pyrite orient` and `--help` epilogue point a new user to `orient -k` first; the operational contracts block answered "do I need to run index sync after hand edits?" without any trial.
- Files stayed plain markdown; `git diff` after each `update` showed minimal, readable changes (tags and one key), good for the hand-maintenance story.

## Observed vs concluded
- Observed: `ci` reported 11 entries and no parse error with `author-b.md` malformed; `index health` reported it under `malformed_frontmatter` with exit 0. Concluded: validation commands do not share a view of the malformed-file case. I ran these as direct invocations (stderr to /dev/null for `ci`), not through a pager; exit codes read from `$?` or `PIPESTATUS`.
- Observed: `tpye:` produces only a stderr log line. Concluded: it is not part of any validator (I did not read the code for this).

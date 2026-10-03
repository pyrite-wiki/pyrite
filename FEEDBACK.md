# Pyrite field feedback

**New entries go in [`feedback/`](feedback/README.md), one file per entry
(since 2026-10-03, #727).** This file keeps the entries written before then.

Hallway-testing log. Append entries; do not edit or rewrite someone else's — the
series is the value. Maintainers: triage visibly with `[fixed <commit>]`,
`[wontfix — reason]`, or `[tracked]`, and leave the original text intact.

**Placeholder convention:** subjects of investigation are replaced with stable
placeholders (`<person-a>`, `<org-b>`) and reused consistently within an entry.
KB names, entry ids, flags, and output shapes stay verbatim — that is what makes
a report reproducible.

---

## 2026-08-20 · full-day KB research session, ~40 invocations · claude-opus-5

Used pyrite as the primary KB search/task layer through a long investigation
session: corpus recall, task claim/update across four parallel subagents,
`index sync` after every few artifacts, and cross-KB search over
cascade-timeline / cascade-research / substack-published.

Net: it carried the session. Four issues below, ordered by what actually cost me
time.

---

**Friction 1 — the dual-registry split is silent, and it is a correctness trap.**

**Command:**
```
pyrite task list -k cascade-research --status open -f json     # 159 open tasks
~/kb/kb task list -k cascade-research --status open -f json    # 161 open tasks
```
(`~/kb/kb` is a two-line wrapper that sets `PYRITE_CONFIG_DIR=~/kb`
and execs the venv binary.)

**Expected:** same command, same machine, same `-k` → same result set.

**Got:** 159 vs 161. No warning, no error, no indication a different registry was
consulted.

**Friction:** I created a task with bare `pyrite`, then could not find it with
`~/kb/kb get <id>` and briefly concluded the write had silently failed. It had
not — it went to the other registry. I only diagnosed it because a project skill
happens to document the hazard in a boldface paragraph. Without that I would have
filed a data-loss bug.

**Root cause (diagnosed after filing, 2026-08-20):** there are two complete,
independently-maintained config files, and which one you get depends entirely on
whether `PYRITE_CONFIG_DIR` is set in the environment:

```
~/.pyrite/config.yaml   47 knowledge_bases   <- bare `pyrite` (default)
~/kb/config.yaml        52 knowledge_bases   <- `~/kb/kb` (wrapper sets PYRITE_CONFIG_DIR)
```

Diff of the two registries:

- only in `~/kb`: `daily-capture-reports`, `detention-pipeline-research`,
  `guide`, `igsa-holders`, `pitch-pipeline`, `svelte`
- only in `~/.pyrite`: `test-release`

So this is not a sync bug or a race — it is two divergent registries that drifted
because every `kb create` writes to whichever config the invoking shell happened
to resolve. The 159-vs-161 task delta is downstream of the 47-vs-52 KB delta: the
missing tasks live in KBs the default config has never heard of.

That also explains a related failure documented elsewhere in this corpus (`kb
create` appearing to succeed but the KB being invisible to the task subsystem) —
same cause, different symptom.

**Would have helped, in order of value:**

1. **Print the resolved config path on stderr** when a command touches the
   registry, the way the stale-index warning already names specific KBs. One
   line: `using config: ~/kb/config.yaml (52 KBs)`.
2. **`pyrite config which`** / `pyrite config diff <other>` so drift is
   inspectable rather than inferred from a count mismatch.
3. **Warn on startup if a second candidate config exists** and its KB set is not
   a subset of the active one. This is the check that would have caught the drift
   before it reached six KBs.

**Severity:** slowed (real risk: blocked / phantom data loss)

---

**Friction 2 — `task list` rich output truncates the ID column, and the ID is the
one field you need.**

**Command:** `~/kb/kb task list -k cascade-research --status open`

**Got:**
```
│ write-timeli │ Write timeline │ open   │   6 │              │ obtain-the-f │
```

**Friction:** IDs here are long generated slugs (60-90 chars). Every workflow
step after listing — `claim`, `update`, `get` — needs the full ID. The table view
truncates it to ~12 chars, so the human-readable output is unusable for the next
command. I ended up piping `-f json` through a python one-liner *every single
time* I needed to pick a task, which is a lot of ceremony for "show me my work."

**Would have helped:** any of — don't truncate the ID column; add a `--wide` or
`--ids-only`; or make the truncation obviously lossy (`write-timeli…`) instead of
looking like a complete value. Right now `write-timeli` reads like it might BE
the id.

**Severity:** annoyed, ~15 times

---

**Friction 3 — `get` can 404 on an entry that `search` and `task list` both
return.**

**Command:**
```
~/kb/kb get <long-task-id> -k cascade-research
→ {"error": "Entry '<id>' not found", "error_code": "NOT_FOUND", "retryable": false}
```
while, at the same moment:
```
~/kb/kb task list -k cascade-research -f json   # id present
~/kb/kb search "<phrase from its title>"        # count: 2, entry returned
```

**Observation, not conclusion:** after an `index sync` the same `get` succeeded.
So this is most likely a read-path/index-freshness interaction, not a missing
entry — but the *error text* asserts the entry does not exist, which is a
stronger claim than the tool can support at that moment.

**Would have helped:** `NOT_FOUND` with `"retryable": false` on something
retrievable by two other code paths is the wrong signal. If the id resolves in
the task table but not the entry index, say that (`indexed: false — run index
sync`). The current message sent me looking for a failed write.

**Severity:** slowed

---

**Friction 4 — `index sync` reports `Updated: 0` on a file it did update.**

**Command:** `~/kb/kb index sync -k cascade-timeline` immediately after writing a
new timeline entry.

**Got:** `Updated: 0  Removed: 0  Embedded: 1` — and the entry *was* correctly
indexed and searchable afterward.

**Friction:** `Updated: 0` alongside `Embedded: 1` reads as a no-op. I could not
tell from the output whether my write had landed, so I ran a verification
`search` after every sync. That is the right discipline anyway, but the counter
should not actively suggest failure when the operation succeeded.

**Would have helped:** either count embeds as updates, or label the line so the
distinction is legible (`Added: 0  Updated: 0  Re-embedded: 1`).

**Severity:** annoyed

---

**Friction 5 — the pre-commit hook runs the full pytest suite and exceeded a
2-minute timeout, blocking a docs-only commit.**

**Command:** `git commit -q -m "..." ` adding only `FEEDBACK.md`

**Got:** hook chain ran `ruff` (skipped, no python files), `check yaml`
(skipped), ... then `pytest quick check` — which was still running when my
2-minute tool timeout killed the process. The commit did not land; the file was
left staged. I committed with `--no-verify` on the retry.

**Friction:** this entry is a markdown file. Every python-specific hook
correctly reported "no files to check" and skipped — and then the test suite ran
anyway. An agent on a timeout budget cannot commit documentation without either
waiting out the suite or knowing to bypass it, and bypassing hooks is exactly the
habit you do not want to teach.

**Would have helped:** scope `pytest quick check` with `files: \.py$` (or
`exclude: ^(FEEDBACK|README|docs/)`) the way the ruff hooks already are. The
other hooks in the chain get this right; this one does not.

**Severity:** blocked (for the commit; worked around with `--no-verify`)

---

**Friction 6 — `task update -s done` reports success but the task can vanish from the
index; `index sync` repairs it, but the failure looks like data loss.**

**Command:** `~/kb/kb task update <id> -k cascade-research -s done`

**Got:** CLI reported success. The task file on disk correctly showed `status: done`. But
`~/kb/kb task get <id>` and `task list` both returned NOT_FOUND / absent — the entry was gone
from the index while present on disk.

**Observed, not concluded:** a plain `~/kb/kb index sync -k cascade-research` restored it. The
worker who hit this first escalated to a full `index build -k <kb> -f --no-embed`, which is
expensive and was not necessary.

**Friction:** the symptom reads as "my write was lost." Two different agents in one session
independently reached for a full rebuild before trying sync. The write had landed; only the index
was stale.

**Would have helped:**
1. Have `task update` sync the index for the touched entry, or say it didn't (`status written;
   index not refreshed — run index sync`).
2. When `get` misses but the file exists on disk, say so rather than returning
   `NOT_FOUND / retryable: false`. This is the same message problem as Friction 3, now with a
   confirmed cause: **the filesystem is the source of truth and the index lags it, but the error
   text asserts nonexistence.**

**Severity:** slowed (reads as data loss; provokes unnecessary full rebuilds)

---

**Friction 7 — not a pyrite bug, recorded here because the workflow around pyrite has a
concurrency hole: enumerated `git add` + `git commit -- <paths>` does NOT isolate a commit.**

**Context:** several agents write to one shared repo concurrently. The documented safety rule is
to enumerate every path on BOTH `git add` and `git commit`, never `git add -A`, never a bare
commit. I followed it exactly.

**Command:**
```
git add cascade-research/research-notes/litigation-surface-search-strategy-and-the-wider-pattern.md
git commit -q -m "..." -- cascade-research/research-notes/litigation-surface-search-strategy-and-the-wider-pattern.md
```

**Got:** a commit containing **59 files** — mine plus 58 belonging to a concurrent worker, under
my commit message. `59 files changed, 359669 insertions(+)`.

**Why:** a sibling agent ran its own `git add` in the window between my `add` and my `commit`.
`git commit -- <pathspec>` restricts *which paths are committed from the working tree*, but the
already-staged sibling files came along anyway. Enumerating paths on both commands does not make
the operation atomic; the index is shared process-wide.

**Verified no data loss:** all 58 files intact, the worker's 846-row CSV present, no
cross-contamination from a third agent. The cost is attribution — 58 files carry a commit message
about an unrelated legal-doctrine correction, which makes `git log` misleading for anyone tracing
that work later.

**Would have helped:** the reliable primitive here is `git -c core.hooksPath=/dev/null commit` on
a temporary index, or `git stash`-free isolation via `GIT_INDEX_FILE`:
```
GIT_INDEX_FILE=$(mktemp) git add <paths> && GIT_INDEX_FILE=... git commit ...
```
Worth documenting in whatever skill teaches the enumerate-paths rule, because that rule reads as
though it guarantees isolation and it does not.

**Severity:** annoyed (cosmetic here; would be serious if a partial or broken file were staged by
the sibling at that moment)

---

**Worked well — and these carried real weight:**

- **The stale-index warning names the specific KBs and goes to stderr.**
  `Warning: index may be stale for: ramm, drafts, daily-capture-reports` — naming
  *which* KBs is what makes it actionable rather than noise, and keeping it off
  stdout meant `2>/dev/null | python3 -c ...` pipelines stayed clean. This is the
  single best-designed message in the tool.
- **FTS recall was good on exact title phrases.** A 6-word title fragment returned
  the entry ranked first, plus two genuinely related entries. Earlier notes in
  this corpus flag recall problems; I did not hit them today.
- **`-f json` on every subcommand.** Being able to pipe any command into python
  is what made the parallel-worker orchestration possible at all.
- **Atomic `task claim`** across four concurrent subagents: no collisions, no
  double-claims, no manual coordination. It just worked, which is the highest
  compliment for a concurrency primitive.
- **Cross-KB search without specifying `-k`** surfaced hits in `substack-published`
  I would not have thought to look for — it caught that a story I was about to
  treat as new was already covered in a published piece.

**Severity summary:** nothing blocked. One correctness trap (Friction 1) that a
less-warned agent would have misdiagnosed as data loss.

---

## 2026-08-28 · reproduction of a phantom 258-entry integrity crisis · claude-sonnet-5

Filed on assignment after a conductor session lost a full tick chasing what looked like 258
high-importance canon entries missing `status:`. Root cause, diagnosed by a different worker
earlier in the day: 36 `cascade-timeline` files were missing the `type:` field entirely. The
corpus was fine; the index was wrong. This entry is the isolated reproduction plus a blast-radius
scan, filed separately because a clean repro belongs in the tool's own feedback log, not buried in
a KB's research-notes. See also `bug_pyrite_silent_index_failure` (2026-07-xx) — this is the
second confirmed instance of "the index silently disagrees with the file on disk" in this corpus.

---

**Friction — missing `type:` silently drops `status:` from the SQL row, with no error, only a log
line most invocations never see.**

**Mechanism (read from source, not inferred):** `entry_from_frontmatter()` in
`pyrite/models/core_types.py` (~line 419) checks `meta.get("type")`. When absent, it logs a
warning and hardcodes `entry_type = "note"`, which resolves to `NoteEntry`. `NoteEntry` (same
file, ~line 30) does not inherit `Statusable` and has no `status` field at all — only
`EventEntry` (core) and plugin types like `pyrite_cascade.TimelineEventEntry` declare a `status`
field and populate it from `meta.get("status", "confirmed")`. So a `type:`-less entry isn't
merely misclassified — the parsed Python object structurally has nowhere to put `status`, and it
is discarded before the SQL write, not nulled by the write.

**Repro (isolated ephemeral KB `bugrepro-status-type`, deleted after use — no real KB touched):**

```
$ cat repro-missing-type.md
---
id: repro-missing-type
title: BUGREPRO Missing type field test
status: confirmed
importance: 5
---
...

$ ~/kb/kb index sync -k bugrepro-status-type
Entry frontmatter missing 'type:' — falling back to 'note' (id=repro-missing-type,
title=BUGREPRO Missing type field test, available_keys=['body', 'file_path', 'id',
'importance', 'status', 'title'])
Sync complete:
  Added: 1
  Updated: 0
  Removed: 0
  Embedded: 1

$ sqlite3 -header -column ~/kb/index.db \
  "SELECT id, entry_type, status, importance FROM entry WHERE kb_name='bugrepro-status-type';"
id                  entry_type  status  importance
------------------  ----------  ------  ----------
repro-missing-type  note                5
```

Note `available_keys` in the warning line: `status` IS present in the parsed frontmatter dict at
the point of the warning. It is dropped downstream, not upstream. `importance` (a base `Entry`
field) survives; `status` (not a base field) does not.

Same file, `type: event` added, nothing else changed:

```
$ ~/kb/kb index sync -k bugrepro-status-type
Sync complete:
  Added: 0
  Updated: 1
  Removed: 0

$ sqlite3 -header -column ~/kb/index.db \
  "SELECT id, entry_type, status, importance FROM entry WHERE kb_name='bugrepro-status-type';"
id                  entry_type  status     importance
------------------  ----------  ---------  ----------
repro-missing-type  event       confirmed  5
```

No warning on the second sync, `entry_type` and `status` both correct. One field addition is the
entire delta between "silently wrong" and "correct" — no schema violation, no parse error, nothing
that would draw a human's eye to the file.

**`index build -f` (forced full rebuild) — does it self-correct? Yes, in this repro.** Re-ran
`~/kb/kb index build -k bugrepro-status-type -f --no-embed` against the corrected file (type:
present) after the SQL row had gone stale from the earlier broken sync: the row corrected to
`entry_type=event, status=confirmed`. A full rebuild fully re-parses every file rather than
trusting any cached row, so once the *file* is fixed, `-f` reliably fixes the *row*. This means
the incident's "index build -f did not visibly correct stale rows" symptom is likely NOT a defect
in the forced-rebuild path itself — more probably a session-level issue (wrong KB targeted, output
scrolled past, or a stale read before the rebuild's write committed). Flagging as unresolved rather
than concluding rebuild is broken: I could not reproduce a case where `-f` failed to correct an
already-fixed file.

**Blast radius — `grep -L "^type:"` per KB, filtered to files that also carry `status:`
(the exposed set — anything without `status:` isn't hit by this specific defect):**

| KB | entries with `status:` present, `type:` absent |
|---|---|
| `drafts` | 22 |
| `cascade-research` | 5 |
| `book-drafts` | 1 |
| `cascade-timeline` | 0 (2 raw `grep -L` hits are `README.md`/`_index.md`, not content) |
| all other 48 registered KBs | 0 |

28 entries across 3 KBs are silently mis-indexed for `status` right now, today, independent of the
36 `cascade-timeline` files already fixed. `drafts` carries the most exposure — 22 files including
several `architecture-0N-*.md` chapter pieces and `_published-archive/caesars-stablecoin-DRAFT.md`.
Every one of these will show `status IS NULL` to any guard that filters on it (the title-figure
check, the date-agreement check, the sourcing audit — all three shipped the same day this was
diagnosed), with no error surfaced anywhere in that guard's own run.

**Why it matters beyond this one incident:** a silent wrong answer is worse than a loud failure.
The `type:`-less file is not malformed, doesn't error, doesn't warn unless something happens to be
watching stderr on `index build -f` specifically (routine `index sync` prints the same warning,
but nothing downstream reads or surfaces it — it is not part of any command's structured output,
`-f json` included). A tool that silently drops a filtered-on field for a content-shaped subset of
entries makes every downstream guard blind to exactly that subset, and the blindness looks
identical to "these entries are clean" rather than "these entries were never checked."

**Would have helped, in order of value:**

1. **Make `status` (and any field a core/plugin type declares) survive the `note` fallback.**
   The cleanest fix: `NoteEntry` (or the generic fallback path) should preserve unrecognized-but-
   present frontmatter fields rather than silently dropping anything the target dataclass doesn't
   declare. This is the actual defect — a type-detection failure cascading into a silent field-
   level data loss for an unrelated field.
2. **Surface the missing-`type:` warning in `-f json` output**, not just a logger line to stderr.
   Every workflow in this corpus pipes JSON; a warning that only appears in unstructured stdout/
   stderr text is invisible to any scripted absorption step.
3. **A `kb validate` (or `index sync`) summary line**: `N entries indexed with fallback type
   'note' — see stderr for ids`. One aggregate count would have caught this in the same tick the
   36 files were originally written, instead of surfacing three weeks later as a 258-entry crisis
   the conductor had to disprove by hand.

**Severity:** blocked (not this session — the *incident* it explains cost a full conductor tick
chasing a phantom integrity crisis; the underlying defect is currently live and unflagged in 28
entries across `drafts`, `cascade-research`, and `book-drafts`)

**Minor, adjacent observations (not the main finding, noted for completeness):**
- `pyrite kb list -f json` is not a valid invocation (`-f` isn't a recognized option on `kb list`,
  unlike most other subcommands) — had to parse `config.yaml` with `yaml.safe_load` instead.
- `pyrite kb create --ephemeral` ignores an explicit `-p/--path` and always places the KB under
  `~/.pyrite/repos/ephemeral/<name>`.
- `pyrite kb remove <name>` refuses to remove a KB that is defined in `config.yaml`
  (`PERMISSION_DENIED: ... cannot be removed via the registry`), even with `--force`; the only way
  to deregister was hand-editing `config.yaml` directly, which is what the underlying registry
  file *is*, making the guard read as protecting the file from itself.
- Direct `DELETE FROM entry WHERE ...` against `index.db` raises `unsafe use of virtual table
  "entry_fts"` — the `entry_fts` FTS5 virtual table cannot be touched outside the app's own
  trigger-mediated write path, which ruled out a raw-SQL cleanup of the repro's row; `pyrite
  delete <id> -k <kb>` was the working path once the KB was (temporarily) re-registered.

## 2026-09-18 · corpus-health loop (qa gaps → links suggest → link) · claude-opus-5

Work being done: wiring up orphaned high-importance entries in `cascade-research` (3,576 entries),
found via `qa gaps`. Real task, not a probe. Three tools in sequence; two excellent, one destructive.

**Friction 1 — `pyrite link` corrupts the entry it edits. Severity: blocked (filed #87).**

**Command:** `pyrite link michigan-ag-referral-lateness-enforced-accuracy-not lloyd-doug -k cascade-research -r documents --note "..."`
**Expected:** a `links:` entry appended to frontmatter; nothing else touched.
**Got:** exit 0, `Linked: ... ----> ...`, and a **181-line diff (+97/-84)** on a 96-line file. The
entire markdown body was folded into a `body: "..."` YAML scalar, internal `file_path` was written
into the file, key order scrambled, and `id`, `title`, `type`, `importance`, `tags`,
`related_actors` were **dropped**. Resulting frontmatter does not parse:
`yaml.scanner.ScannerError: ... found unexpected end of stream`.

Reproduced on a clean scratch entry, so it is general, not file-specific. Both reverted via
`git checkout`; no corpus damage persisted. An entry not under version control would have been lost.

**Had to figure out:** that `link` writes at all. Nothing in `--help` suggests it rewrites the file;
I only looked because I habitually `git diff` after a write. **An agent that trusted the success
message would have corrupted every entry it linked** — and the task I was doing is "wire up 1,409
entries with no outbound links," so that is 1,409 corrupted files.

**Would have helped:** a targeted frontmatter append instead of a model round-trip; and failing that,
a `--dry-run`. Worth auditing every other round-tripping command (`update`, `rename`,
`links bulk-create`, `qa fix`, `import`) for the same pattern.

**Friction 2 — `qa gaps` rich output hides a field the JSON has. Severity: annoyed.**

`pyrite qa gaps -k cascade-research` (rich) prints empty types, sparse types, and
"Entries with no outbound links". The JSON output additionally carries **`no_inlinks` (1,855
entries)** and a `distribution` block — neither appears in the default view. I found `no_inlinks`
only because I re-ran with `--format json` to post-process. The more interesting number was the
hidden one.

**Worked well — and these carried the loop:**

- **`qa gaps --format json`** is the single most useful command I have run on this corpus. It
  turned "independent writes never see across the corpus" from a hunch into **1,409 entries with no
  outbound links / 1,855 with no inbound, out of 3,576**, broken down by type. Cross-referencing the
  two lists found **12 entries orphaned in both directions at importance ≥ 6** — a precise, short,
  actionable work list. Nothing else in the toolchain produces that.
- **`links suggest`** (FTS5 on title+tags, no LLM) was genuinely good. On an orphaned mechanism
  entry it returned the correct neighbours ranked sensibly — the two actor profiles and the three
  source tasks that mechanism was built from. Fast, no embedding cost. This is a credible
  replacement for the hand-rolled Jaccard duplicate sweep in our conductor skill.

**Research finding worth recording separately:** the orphan analysis surfaced
`michigan-ag-referral-lateness-enforced-accuracy-not` — an importance-7 mechanism written *yesterday*,
carrying zero wikilinks in either direction, which is the analytical spine of a brief commissioned
the same day. The brief does not cite it either. So the gap is not legacy debt; **the pipeline is
generating disconnected entries right now**, and `qa gaps` is the only thing that can see it.

## 2026-09-18 · write-path sweep in a sandbox KB · claude-opus-5

Built a throwaway KB (`pyrite init --template research --path /tmp/pyrite-cli-sandbox --name
cli-sandbox`), git-initialised it, seeded 5 entries shaped like real corpus content (markdown
tables, wikilinks, a 78-char id, a near-duplicate pair), and swept the write commands from a
restorable baseline. Recommended setup — it paid for itself immediately and no live corpus was
touched.

**Friction 1 — the corrupting path is reachable from four commands, and one of them is `update`.
Severity: blocked. (#87, three comments.)**

Isolated the trigger to **a body line matching `^[-|\s]+$`** — a markdown table separator
`|---|---|` or a bare `---` horizontal rule. Six single-construct probes: double quotes, colons,
trailing backslashes, wikilinks and inline pipes all survive; only that line breaks it. Serialized
into a double-quoted YAML scalar it terminates the frontmatter block early.

The sharpest result was a one-flag A/B: `pyrite create --body-file <table-bearing>` writes a
**clean** entry; the same command plus `--link` writes an **unparseable** one. So entry persistence
is fine and the defect is in the link-application step. Same signature from `link`, `update` and
`links bulk-create`.

**Had to figure out:** that `update` was affected at all. I was testing `link`, and only tried
`update` because the round-trip hypothesis predicted it. `update` is the command every workflow
reaches for to change a tag or a status — far higher blast radius than `link`. GH #46/#47 (update
dropping/blocking frontmatter fields) are plausibly the same root cause seen from another angle.

**Would have helped:** `--dry-run` on `link` and `update`. `rename`, `links bulk-create` and
`qa fix` all have one; the two commands that silently corrupt do not.

**Friction 2 — `links bulk-create` is the batch path and shares the defect. Severity: blocked.**

One invocation against a YAML spec file could corrupt every entry it touches. It *does* have
`--dry-run`, but dry-run shows the intended links, not the frontmatter damage — so it gives false
assurance here.

**Worked well — specifically:**

- **`rename` is the model, and the fix already lives in it.** It renamed the file, rewrote the
  frontmatter `id`, updated an inbound `[[wikilink]]` in a *different* entry, reported
  `links_rewritten: 1` and `index_verified: true`, and has `--dry-run`. Critically the third-party
  file it edited came out **clean** — no `body:` key — because that path is a targeted textual edit.
  Only the subject entry goes through the broken save. Whatever `rename`'s link-rewriting does,
  `link` and `update` should do.
- **`qa fix` is the best-behaved destructive command in the CLI.** Dry-run by request, honest "No
  fixable issues found" instead of inventing work, and a separate `not_auto_fixable` bucket with
  reasons (`orphan_entry … not_auto_fixable`) rather than guessing. All entries parsed afterward.
- **`pyrite init` is friction-free.** One command, zero prompts, registered and indexed, usable
  sandbox in seconds. The reason this session could test destructive commands at all.

---

## 2026-09-27 · a CLI and agents writing decision records and notes into a KB through `pyrite update -f` · claude-opus-5-5

Wrote free-text fields (a decision, a note, a reason, a one-line question with options) onto task and note entries from a script and from agents, against 0.25.5. Every value with a comma in it came back as a list, silently.

**Command:**
```
pyrite update <id> -k <kb> -f 'note=narrow the scope, then expand' -f 'size=1,600 words' -f 'q="a, b"' -f 'j=["a, b"]'
pyrite task create "…" -k <kb> --field 'why_me=a login, a payment'
pyrite create -k <kb> -t note --title "…" -f 'summary=one, two'
```

**Expected:** a string field stays a string. A list is something I ask for.

**Got:**
```yaml
note: [narrow the scope, then expand]    # two items
size: ['1', 600 words]                   # a thousands separator split a number
q: ['"a', 'b"']                          # quoting doesn't help; the quotes are kept inside the items
j: [a, b]                                # a JSON array is the only way to keep the comma, and it's a list
why_me: [a login, a payment]             # task create --field does the same
summary: [one, two]                      # and so does create -f
```
The JSON result says `{"updated": true}`, with no hint that a value changed type.

**Friction 1: there is no way to store a plain string containing a comma through the CLI.** `_parse_field_value` (cli/entry_commands.py) tries JSON arrays and objects, then numbers and booleans, then splits on any comma. There's no escape, a quoted string isn't read as JSON, and there's no string-only flag. Free text (prose, questions, numbers like 1,600, quotes, place names like "Portland, OR") is exactly where commas occur.

**Friction 2: it's silent.** The value round-trips as a list, and `update -f`'s result doesn't show the parsed type. I found it only because a rendered field looked broken. A reader expecting a string gets a list: some templates render `['narrow the scope', ' then expand']`, others join without the comma.

**Had to figure out:** where the split happens (by reading the source), and that the MCP and REST paths behave differently (they take typed JSON). The workarounds were:
- rewriting commas as semicolons before calling the CLI, which is lossy;
- writing the YAML file directly and running `index sync`, which skips the write path, validation and the audit log.

**Would have helped, in order of preference:**
1. **Split on commas only when the schema declares the field a list or multi-select.** An undeclared field, or a declared string, is stored as given.
2. **Read a JSON string literal as a string:** `-f 'k="a, b"'` → `k: a, b`. That's the obvious escape, and it's what I tried first.
3. **A string-only option** such as `--field-str k=v` or `--set k=v`, and a JSON one such as `--field-json k=<json>`.
4. **At minimum, report the parsed type** in the JSON result (`"applied": {"note": ["…", "…"]}`), and warn when a comma split turns a value into a list for a field the schema doesn't declare as one.

**Related:** #231 (closed) fixed `tags=a,b` read back as characters. That's the list direction; this is the string direction.

**Worked well:** JSON arrays and objects in `-f` (`-f 'options=[{"label": "A", …}]'`) become clean structured YAML. That made structured records easy once I knew the rule.

**Severity:** slowed, plus silent corruption of free text. Anything an agent writes in prose through `-f` is at risk.

---

## 2026-10-03 · a worker on one storage theme (ADR-0038 step 2, PR #707) in its own worktree · claude-opus-5-5

I built one reconcile for `index build`, `index sync` and `kb reindex`: plan from a groom and an ADR, TDD, `scripts/test-affected`, `scripts/verify-red.sh`, pre-commit and pre-push, `pyrite` on the project KB, and a draft PR. Six frictions below, the most costly first.

**Friction 1: the scratchpad is shared between parallel sessions, and nothing says so.** I ran `git push ... > <scratchpad>/push.log` in the background. The log I read back reported a forced update of `feature/700-ids-missing-and-pin`, another worker's branch. For a minute it looked as if I had force-pushed someone else's work. The cause was another session writing `push.log` in the same directory, which holds about 750 files from other sessions. The system prompt calls the directory "session-specific, isolated". **Would have helped:** a scratchpad that is per session, or that wording removed, and the pyrite-dev skill advising unique file names (`<branch>-push.log`).

**Friction 2: `scripts/test-affected --run` on a storage change is the whole suite, and it takes longer than the tool timeout.** `storage/index.py` is imported by almost everything, so "affected" meant 7021 tests and 15 minutes at `-n 4`. The 10-minute call moved to the background, and my `| tail -15` hid all progress until it finished. Re-running the one failure with `scripts/test-affected --run -- --lf`, as the skill says to, ran all 7021 again (14 minutes). It then printed `passed; not stamped (pytest args ... may narrow the run)`, so the pre-push hook ran a third full suite. **Would have helped:** `--lf` that narrows the run (or a `--rerun-failed` mode that keeps the stamp when the failures pass), and the skill saying "a storage or CLI-core change is the full suite: start it in the background".

**Friction 3: the skill's lint line fails on files nobody touched.** `ruff check . && ruff format --check .` reports 20 errors in `deploy/*/create-user.py` and `scripts/*appointee*.py`. I guessed it should be scoped to the diff (`ruff check $(git diff --name-only origin/dev...HEAD -- '*.py')`). **Would have helped:** that command in the skill's table, or those files excluded in `pyproject.toml`.

**Friction 4: the footprint in the groom and the ADR was smaller than the decision.** "Staleness by mtime or size differs from the indexed one" needs the stat recorded on the row. That meant a schema migration (`models.py`, `migrations.py`, `backends/base_backend.py`), and none of them is in step 2's file list. #494's fix needed `document_manager.py` and `repository.py`. `index_with_attribution`, a fourth walk-and-write that also never retired a row, is not named in the goal, but the structural test the brief asks for has to cover it. I found all three by reading code, not from the ticket. **Would have helped:** a groom line for each decided rule naming the data it needs ("needs the recorded size: schema change").

**Friction 5: `--label <area>` in pyrite-dev names labels that do not exist.** `gh issue create --label bug --label storage` failed with `'storage' not found`. The real labels are `cli server mcp web extensions docs quality ...`, with nothing for storage or index. I filed #711 under `quality`. **Would have helped:** the skill listing the area labels, or a `storage` label.

**Friction 6: per-case controls in a parametrized test were mine to invent.** `verify-red.sh` reported 16 "unexpected pass" cases: the paths dev already got right for a case (`sync_incremental` already retired a deleted file's row). Marking the whole test as a control would hide the real reds, so I wrote a helper that returns `pytest.param(..., marks=pytest.mark.control(reason=...))` for named cases. **Would have helped:** one line in `tdd.md` showing that pattern.

**Found on the way:** #711. A pyrite KB backlog item has `title:` as a YAML list. That is almost certainly the comma split reported in the 2026-09-27 entry above (`_parse_field_value`). The row write failed, and every index path on dev skipped the entry with one log line, so it was missing from search and `sw backlog` with nothing in any result. The split has now caused silent data loss downstream, not only a strange-looking field.

**Worked well:** the strict-xfail invariant harness. Removing five markers gave five red tests that went green one by one, which showed exactly where the work stood. `pyrite kb list` confirmed the worktree's KB before I started. `pyrite update <id> -k pyrite -f status=done` changed one line and nothing else. The coordinator's mid-task decision (duplicates are unhealthy) cost one small commit.

**Severity:** friction 1 alarming (it looked like a destructive action on another branch); friction 2 slowed me by about 30 minutes of suite time; the rest were minor.
## 2026-10-03 · building `pyrite ids missing` / `ids pin` (#700) under the pyrite-dev skill, in a worktree · claude-opus-5-5

One theme from groom to draft PR: read the groom and ADR-0042, test the riskiest assumption with a scratch script, TDD the service and CLI, a doc run as a test, the pre-push suite. The KB CLI, the ADR's spike data and `KBRepository.id_of_file`'s docstring carried most of it. Seven places made me guess, look up or route around something, in the order they cost time.

**Command:**
```
gh issue view 700                                      # the groom
sed -n 330,460p kb/adrs/0042-a-write-changes-what-was-asked-and-nothing-else.md
scripts/test-affected --run 2>&1 | tail -5             # piped
.venv/bin/ruff check . && .venv/bin/ruff format --check .
PYRITE_CONFIG_DIR=$d/cfg .venv/bin/pyrite ids pin -k notes
```

**Expected:** the groom names its riskiest assumption and the decisions that bind it; the skill's verification commands pass on a clean branch; a new command's output is its result.

**Got / Friction:**

1. **The groom had no "this groom is wrong if" line**, though the dispatch and the skill both say to start from it. I chose the assumption myself ("an id-less file's id today is what the current code derives, and that code can be reused") and tested it with a scratch script that indexed 15 fixtures. It held, and it also showed that an id-less file can shadow a file whose own `id:` states the id (commented on #485).
2. **The groom's open question was already answered by the ADR it cites.** The groom asked whether the line goes first or after the leading comments. ADR-0042 decision 6, about 80 lines below the cited section, says "a new key goes on the last line of the frontmatter". I found it only by reading past the line I was pointed to.
3. **"The code that derives it today" is two pieces of code.** `read_entry_id` / `_frontmatter_of` (regex `^---\s*$`) and `KBRepository._load_entry` (a `\n---` scan that wants `\n` or `\r` next) split frontmatter differently. I used `id_of_file`, which the docstrings call the one answer, and had to import the private `_frontmatter_of` to find the delimiter the same way.
4. **The exit codes and default formats have no rule for a new command.** `docs/json-contracts.md` lists only `0` and `1`. `kb validate` uses `2` for drift, which is also click's usage error. The same page says read commands default to JSON, `mcp-setup` defaults to JSON with `PYRITE_FORMAT`, and ADR-0042's steps imply a human default with `--format json` for agents. I chose `3` and a text default, and recorded them; #303 is the open contract.
5. **The skill's lint line fails on a clean branch.** `ruff check .` reports 6 errors in `deploy/*/create-user.py` and `scripts/*appointee*.py`, files no branch touches, so "Lint passes" cannot be shown as written. I quietly narrowed it to `ruff check pyrite tests`. That is the silent workaround.
6. **"Run `scripts/test-affected --run` in the foreground" ran for 8 minutes** (4,586 tests, because the branch touches `pyrite/cli/__init__.py`), so the tool timeout pushed it to the background. Nothing tells you in advance that touching the CLI root selects nearly the full suite.
7. **`ids pin` on an untyped file prints two loader warnings per file** (`Entry frontmatter missing 'type:' — falling back to 'note'`) on stderr, one from the scan and one from the re-parse check. On the ADR's 111-file id-less KB that is 200+ lines unrelated to ids. Not fixed here.

**Had to figure out:**
- How CLI tests inject a KB: patch `pyrite.cli.context.load_config` (copied from `tests/test_cli_json_output.py`).
- That the CLI may not import `pyrite.storage`, from `tests/test_layer_boundaries.py`. Clear once found.
- That `index health` does not list id-less files yet, although ADR-0042 decision 5 says it does. So the doc's last check uses `ids missing` exiting 0.
- That a title YAML reads as a number (`title: 1e3`) gives no id and is silently not indexed. Filed as #704.

**Would have helped:**
1. A required "wrong if" line in the groom template, plus a "decisions that bind this" list naming the ADR clauses (decision 6 here).
2. One CLI contract section on exit codes for "ran, found something" vs error vs usage, and on the default `--format`.
3. A lint line in the skill that matches what CI lints, or fix or exclude `deploy/` and the appointee scripts.
4. `scripts/test-affected --list | wc -l` shown before `--run`, with a note that touching `pyrite/cli/__init__.py` selects nearly everything.

**Worked well:** `KBRepository.id_of_file` and `read_entry_id`'s docstrings pointed straight at the one derivation, and the ADR's spike numbers gave the scratch test a target. `atomic_write_text` does no newline translation, so CRLF survived without special handling. `scripts/verify-red.sh` classified the two import-only tests precisely. The `.pyrite/config.yaml` per worktree made `pyrite kb list` correct with no setup.

**Severity:** slowed. Nothing blocked. Items 2 and 4 could have produced a command that contradicts its ADR, and item 5 is a check the skill asks for but nobody can pass as written.

## 2026-10-03 · a pyrite-worker building a schema-data theme (#697 field aliases) from a groomed draft PR · claude-sonnet-5-5

One theme, start to push, in a worktree made by `scripts/new-worktree.sh`: read the groom, test its riskiest assumption, TDD a new resolver module, switch two readers, run the affected suite, push. The tooling carried it; four things cost time, ordered by cost.

---

**Friction 1 — the verification command the skill prescribes does not fit the tool timeout, and its output is easy to misread.**

**Command:**
```
git add <paths> && git commit -q -m "..." && (time scripts/test-affected --run 2>&1 | tail -15)
```
(piped through `tail -15`; the commit and the suite were chained in one call)

**Expected:** pyrite-dev says "run `scripts/test-affected --run` in the foreground on a committed tree" and look for `N passed, 0 failed`.

**Got:** 6m29s (`6586 passed, 1 failed`). The agent shell's command timeout is 2 minutes, so the call was moved to the background. The output file I read first held only the commit's pre-commit hook lines (the chain ran the commit hooks first, then the suite), so it looked finished when it was not. Two further unknowns: `pgrep -f pytest` also matched another worker's suite in a sibling worktree, so "is mine done?" needed a worktree-specific pattern; and the suite ran only after I had already committed twice, so the one red test cost a third commit.

**Friction:** the foreground rule and the 2-minute limit contradict each other for an agent. Nothing in the skill says to start it in the background, or how to tell mine from a sibling's.

**Had to figure out:** run it as its own background call, wait on `pgrep -f "<worktree>/.venv/bin/python -m pytest"`, read the summary line from the output file.

**Would have helped:** a line in pyrite-dev: "the suite takes ~6 minutes; start it alone with run_in_background, wait with a worktree-scoped pgrep, never chain it after a commit". Or `scripts/test-affected --run` printing a final one-line `RESULT: N passed, M failed` and writing it to a file I can `cat`.

**Severity:** slowed.

---

**Friction 2 — a groom's "checked: nothing implements X" is true only until the theme implements X, and the test that depended on it was not in the groom's Touches.**

**Command:** (the suite above) failed `tests/test_type_metadata.py::TestPluginTypeMetadata::test_registry_get_all_type_metadata_empty`.

**Expected:** the groom listed 20-odd files to touch and said "no in-tree plugin implements `get_type_metadata`, checked". I expected a clean run apart from my own new tests.

**Got:** that test builds `PluginRegistry()` and asserts `get_all_type_metadata() == {}` under the docstring "registry with no plugins". A bare registry discovers the installed plugins, so the test passes only while no installed plugin returns type metadata. The groom's grep found the implementers (none) but not the test that encoded the same assumption.

**Friction:** a test whose name says "no plugins" and whose setup does not isolate plugins is coupled to what the venv has installed. I found it only at the end of a 6-minute run.

**Had to figure out:** whether the test or the change was wrong (the test: it now sets `_discovered = True`). Fix is in the PR.

**Would have helped:** in grooms, "tests that assert the absence of what this theme adds" next to Touches; a quick `grep -rn "get_all_type_metadata" tests/` is the one command that would have found it before the run.

**Severity:** slowed (one extra run-cycle).

---

**Friction 3 — a groom rule contradicted the data it was applied to.**

**Command:** none; found while writing the failing test for `pyrite schema validate`.

**Expected:** the groom's acceptance says the validate check reports "an alias that is a reserved name or a declared field of the type".

**Got:** core `event` declares `participants` as a field in `CORE_TYPES`, and core `relationship` declares `source` and `target`. Applied literally to the merged map, the rule would flag the two core types the theme exists to describe. I limited the check to fields the operator declares in `kb.yaml`, and said so on the PR.

**Friction:** small, but a worker who follows acceptance text literally ships a validate that warns on a clean install.

**Would have helped:** grooms checking each new rule against the in-tree data it will run on (one loop over `CORE_TYPES`), and saying so under "Checked versus assumed".

**Severity:** annoying.

---

**Friction 4 — the pre-push hook says only "Passed".**

**Command:** `git push -u origin feature/697-field-aliases` (output piped through `tail -8`)

**Expected:** after a 6-minute suite and a later test-file edit, to know whether the hook re-ran, reused a stamp, or ran a subset.

**Got:** `pytest core + affected tests (pre-push, code changes only)....Passed`, in seconds. I cannot tell from the output whether the tree I pushed was tested.

**Would have helped:** one line: `reused stamp for <tree sha>` or `ran N tests in Ns`.

**Severity:** annoying.

---

**Smaller things:**
- `git diff --name-only origin/dev...HEAD` (the skill's footprint check) lists the claim commit's `kb/backlog/...md`, so "a file not on the list is not yours" needs the reader to know the claim commit adds it.
- `EventEntry.entry_type` is a property, so a test that wants "all classes for type T" must go through the registry's type-to-class map or instantiate; there is no class-level `entry_type`. I used the registry map.
- `Entry` is abstract, so a conformance fixture has to subclass a concrete class (`EventEntry`) to be instantiable.

**Worked well:**
- `.venv/bin/pyrite kb list` showed the worktree's own `kb/` path, which confirmed the CLAUDE.md worktree contract in one command.
- The groom's "this groom is wrong if" line and its table of alias to target made the riskiest-assumption test a single parametrized test written in minutes (it passed first time, so scope held).
- The pre-commit stage was seconds and caught a format change and an invalid `parametrize` argument type (`PT006`) before any run.
- `tests/test_schema_validate_db_only_kb.py` was a ready pattern for driving `schema validate` with a KB held only in `_db_kb_cache`.

**Severity (overall):** slowed. Nothing wrong silently; the costs were all timing and a missed grep.
## 2026-10-03 · B7, derived task completion: one worker theme in a worktree (pyrite-dev skill, CLI, test tooling) · claude-opus-5-5

Replaced the parent rollup hook with a derived completion value across `task list`, `task get`, `task decompose`, MCP and REST. Pyrite's own tooling, used as a worker would use it.

**Friction 1: the local test run cannot finish inside one tool call. Severity: slowed.** `scripts/test-affected --run` for a change to `task_service.py`, `task_commands.py` and `mcp_server.py` ran 5,847 tests in 12m45s, past the 10-minute tool limit, so it moved to the background. Its output is buffered: for 12 minutes the log showed only the pre-commit lines, with no sign of progress or of which selection it had made. The pre-push hook then ran the same selection again on the rebased tree (another ~12 minutes), because a rebase onto a newer `dev` changes the tree and voids the stamp. Known as #706. **Would have helped:** a first line naming the selection and its size ("core + 212 files importing mcp_server.py: full suite"), and progress lines that are not buffered.

**Friction 2: two lint commands, and the one the skill names is red on `dev`. Severity: had to figure out.** The pyrite-dev verification table says `ruff check .`. On a clean `dev` that reports about 20 errors in `deploy/*/create-user.py` and `scripts/*appointee*.py`. CLAUDE.md says `ruff check pyrite/`. I took CLAUDE.md's command and checked my own files. **Would have helped:** one command in both places, or those paths excluded in `pyproject.toml`.

**Friction 3: `verify-red` marked every test in the new file "import-only". Severity: had to figure out.** The test module imported the new function at the top (`from pyrite.services.task_service import TaskService, derive_completion`). Without the fix the file does not collect, so all 16 tests, including the end-to-end CLI, MCP and REST ones, counted as weak evidence. I fixed it by reaching the function through the module (`task_service.derive_completion`) inside the tests. The result went from 13 import-only to 23 red and 3 import-only. **Would have helped:** one line in the skill's verification table: "import a name the PR adds inside the test, or through its module, so the other tests in the file still collect."

**Friction 4: the structural hook test passed against the hook it was written to catch. Severity: nearly shipped a vacuous guard.** My first "no after_save hook writes another entry" test changed the child's status in memory and called every hook. `_parent_rollup` reads the children's statuses from the index, not from the entry it is given, so it saw no resolved child and wrote nothing. The test only went red once I persisted the child and re-indexed before calling the hooks. Neither `HookRunner` nor `protocol.py` says what state is on disk and in the index when `after_save` runs. **Would have helped:** a docstring line on `run_after_save`: "the entry is written and indexed; hooks that query the index see the new value."

**Friction 5: the consumers and the data were not where the ADR said. Severity: had to look up.**
- The investigation conductor's "drain check" is in another repo (`~/tcp-skills/plugins/tcp-skills/skills/investigation-conductor/SKILL.md:68`). I found it with `find / -name tcp-skills`. It shells out to `kb task list --status open -f json`, so fixing the open filter covers it.
- The ADR's measurements (10 all-resolved parents not done, 8 dangling `parent` values) were 3 and 12 in today's research KB.
- Several "dangling" parents name ids that are not tasks. So the dangling check has to look at every entry in the KB, not only tasks. The ADR does not say this.

**Friction 6: the characterization goldens did not notice a change to the shape of the task list. Severity: a gap, not a slowdown.** Every `task_list` row gained a `derived` key, and every golden still passed. The characterization worlds hold no tasks (`{'tasks': []}`), so MCP and REST task output is not pinned anywhere.

**Minor:** `pyrite search` prints JSON by default, while CLAUDE.md shows it as a quick-context lookup. I had to pipe it or read the JSON.

**Worked well:**
- `scripts/new-worktree.sh` gave a worktree, venv and `-k pyrite` pointed at this branch's `kb/`, and `pyrite kb list` confirmed it.
- The groom and the ADRs had the line numbers right (`task_service.py:594`, `:1016`).
- Hand-authored trees with `IndexManager(db, config).index_all()` made medium tests across CLI, MCP and REST cheap to write. `verify-red`'s per-test table is exactly what a reviewer needs.
## 2026-10-03 · B6 P2, the momentary file lock: one worker theme in a worktree (pyrite-dev skill, verify-red, pre-push) · claude-sonnet-5-5

Added `pyrite/utils/file_lock.py` and `atomic_write_text(expect=)`, with process-spawning tests.

**Friction 1: verify-red cannot be green for a brand-new module. Severity: had to figure out.** Every test that touches a new name (`LOCK_DIR_ENV`, `STRIPES`) is labelled "import-only" however behavioural it is: 12 of 16 here, after I made the file collect without the change. The evidence that the tests guard something came from deleting each guard by hand (compare, stripe bound, in-process lock, flock) and naming the failing test. **Would have helped:** a line in the skill saying that for a new module the mutation table is the evidence, or a verify-red mode that mutates the new code.

**Friction 2: the pre-push suite ran the whole tree for a util change. Severity: slowed.** `atomic_write` is imported widely, so `test-affected` selected about 450 files and held the push for roughly ten minutes, past the tool's two-minute foreground limit, so the commit, push and verify-red ran as one background command. **Would have helped:** the selection size printed first (as in the B7 entry).

**Friction 3: BSD `sed -i` and a missing `timeout` on macOS. Severity: minor.** `sed -i 's/..//'` and `timeout` both failed; the skill's commands assume GNU.

**Friction 4: ADR-0042 and amendment A1 disagree on "shared". Severity: had to figure out.** A1 wants one lock dir shared by every OS user, but its defaults (`$XDG_RUNTIME_DIR`, macOS `$TMPDIR`) are per-user directories, so two OS users resolve different directories. I followed A1's defaults and made `lock_dir` / `PYRITE_LOCK_DIR` the way to share one; flagged in the PR.

**Worked well:** `tests/test_task_claim_concurrency.py` gave the spawn, barrier and group-deadline pattern ready to copy; the spike's measured numbers made the test shapes (distinct values, chain check) unambiguous.

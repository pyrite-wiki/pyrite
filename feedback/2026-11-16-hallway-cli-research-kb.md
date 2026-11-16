## 2026-11-16 · procurement research KB, CLI hallway test (Pyrite 0.26.3) · claude-haiku-5-5

Placeholder key for the invented subjects: `<city-a>` City of Harbor Falls, `<vendor-a>` Acme Paving LLC, `<vendor-b>` Bluewater Civil Works, `<ngo-a>` Harbor Watch Coalition, `<person-a>` Person A (procurement director), `<person-b>` Person B (council member), `<person-d>` Person D (confidential source). KB name `harbor-kb` is kept as typed. Long `-b` bodies are shortened to `...` here; everything else is verbatim. Isolated HOME/PYRITE_* dirs, no network, nothing committed or pushed. Task: 21 entries (4 orgs, 4 people, 4 sources, 7 events, 2 notes), links, timeline, search, backlinks, QA, a hand edit, and a query export.

**Friction 1 — `orient --format markdown` does not print markdown (annoyed)**
**Command:** `pyrite orient -k harbor-kb --format markdown --detail brief | head -120`
**Expected:** a markdown summary, per the flag's name.
**Got:** a Python dict repr starting `{'kb': 'harbor-kb', 'description': '', 'kb_type': 'generic', ...}`, single quotes and all. `--format json` is the form that parses. Piped to `head`, so the exit status is `head`'s, not orient's.
**Would have helped:** either real markdown for `markdown`, or a `--format` list in `--help` that says which formats are real for orient.

**Friction 2 — `qa` takes the KB two different ways (slowed)**
**Command:** `pyrite qa validate -k harbor-kb --format rich`
**Got:** `Error: No such option: -k` / `ERROR [USAGE_ERROR]: No such option: -k`, exit 2. The same error for `qa stale -k` and `qa status -k`. `pyrite qa gaps -k harbor-kb` works. `qa validate` and `qa status` take a positional `[kb_name]`; `qa stale` takes a required positional `{kb_name}`. The default format also differs: `validate` and `status` default to json, `stale` and `gaps` to rich.
**Would have helped:** one convention across `qa` (either `-k` everywhere or positional everywhere). Had to read `--help` for each subcommand to find which one.

**Friction 3 — `index sync` has no `--format` (annoyed)**
**Command:** `pyrite index sync -k harbor-kb --format rich`
**Got:** `Error: No such option: --format`, exit 2. The sync did not run; the search that followed still warned the index was stale. Rerun without the flag: `Sync complete: Added: 1 Updated: 1 Removed: 0`, exit 0.
**Would have helped:** a `--format` that matches the other commands, or a plain-text note in the help that this command prints only text.

**Friction 4 — CLI `create` accepts importance 42 and stores it (slowed)**
**Command:** `pyrite create -k harbor-kb -t note --title "Importance test" -b "Testing range." -i 42`
**Got:** `{"created": true, "entry_id": "importance-test", ... "outcome": "done"}`, exit 0. `pyrite get importance-test -k harbor-kb --format json` shows `importance = 42`. The `--importance` help says `(1-10)`. The same out-of-range value written by hand into a file was flagged by `qa validate` as a warning (`importance_range`, "must be 1-10"), so the write path and the validator disagree about severity.
**Would have helped:** reject on the CLI write path with the same error shape as the event-date refusal (Friction 14 below, which worked), or have validate say error, not warning.

**Friction 5 — list fields accept strings silently (slowed)**
**Command:** `pyrite create -k harbor-kb -t person --title "<person-a>" -b "..." -f role="Procurement Director" -f affiliations="<city-a>"`
**Got:** `get` returns `"affiliations": "<city-a>"`, a string, though `orient --detail brief` declares `affiliations: list[str]` for `person`. Same for `actors` on events (`actors="<person-a>,<vendor-b>,<vendor-a>"` came back as one string at the top level, plus a duplicate under `metadata.actors` as a list). `qa validate` reported nothing for either. I only noticed because a hand edit later turned it into a YAML list.
**Would have helped:** `-f key=value` on a `list[str]` field should split on commas or reject; and validate should flag a type mismatch. Its help says it checks "schema violations".

**Friction 6 — link relation names are free text, and backlinks shows a different relation (annoyed)**
**Command:** `pyrite create -k harbor-kb -t event --title "Contract signed with <vendor-a>" ... --link <person-a>:signatory --link <vendor-a>:party`
**Got:** `"linked": [{"target": "person-a", "relation": "signatory"}, ...]`, exit 0, accepted with no check. But `pyrite backlinks person-a -k harbor-kb --format rich` shows `Relation: related_to` and `Written as: signatory`. `get person-a --format json` shows `"relation": "related_to", "forward_relation": "signatory"`. The `relationship_types` list in `orient --detail brief` has no `signatory`, `party`, `participant` or `requester`, and no `related_to` either (though `link`'s default is `related_to`).
**Had to figure out:** which of the two relation names I had to query on. Trial and error. The rendered KB export shows `(signatory)` under Connections, so the name survives in some views and not others.
**Would have helped:** either a closed relation vocabulary enforced on write, or the backlinks Relation column showing the forward name the writer used.

**Friction 7 — "everything linked to one person" misses people named only in fields (slowed)**
**Command:** `pyrite backlinks <person-a> -k harbor-kb --format rich`
**Got:** one row, `contract-signed-with-<vendor-a>`. `pyrite get <person-a>` shows the same single backlink. The event `bid-opening-and-scoring` has `actors: ["<person-a>", "<vendor-b>", "<vendor-a>"]` and does not appear, because `actors` is a field, not a link. Likewise `pyrite search "<vendor-a>" -k harbor-kb --format rich` returned 4 hits and missed the change-order event, which is linked to `<vendor-a>` but whose body does not say the name.
**Expected:** "linked to" to mean "mentioned in a field or linked", or at least a warning that field-only mentions are not counted.
**Would have helped:** either make `actors` produce links, or document that backlinks follows only `--link` relations.

**Friction 8 — timeline mixes dated sources with events and drops the status (slowed)**
**Command:** `pyrite timeline -k harbor-kb --from 2024-01-01 --to 2024-12-31 --sort asc --format rich`
**Got:** seven rows, including `Bid tabulation 2024` and `Council minutes June 2024`, which are source records with a `date` field, next to events. The `Panel rescoring after clerk flag` row (status `alleged`) shows no status, and so does every other row, so the table cannot tell confirmed from alleged. The JSON form has no status key either.
**Expected:** timeline to be events only, or source rows marked as such; and a status column, since the event type has one.
**Worked well:** the date-range filter is exact on both ends and `--sort asc` did what it said.

**Friction 9 — QA is silent on the findings a journalist cares about (slowed, results)**
**Command:** `pyrite qa validate harbor-kb --format rich` and `pyrite qa gaps -k harbor-kb --format rich`
**Got:** validate: 17 issues, all `orphan_entry` (info) and `rubric_violation` ("has no outgoing links", warning). The two events marked `alleged` with no source link (`change-order-1-<...>` and `panel-rescoring-after-clerk-flag`) were not flagged. `qa status` reports `Assessed: 0, Unassessed: 20, Coverage: 0.0%`. Gaps listed `Entries with no inbound links (11)` and sparse types, nothing about sourcing.
**Expected:** a rule that an `alleged`, `disputed` or `rumored` event with no `sourced_from` link is a gap. That is the first question a source-based KB owes its reader.
**Would have helped:** a sourcing rule, or a documented way to add one. Validate's "rubric" warnings for every entry with no outgoing links were noise when most entries are people or orgs.

**Friction 10 — a hand edit is visible to `get` but not to `search` until sync (annoyed)**
**Command:** edited `harbor-kb/people/person-a.md` (role to "Acting Procurement Director", affiliations as a YAML list, body text added) with the Edit tool, then `pyrite get <person-a> -k harbor-kb --format json` and `pyrite search "Acting" -k harbor-kb --format rich`.
**Got:** `get` returned the new role and body immediately, but its `updated_at` and `indexed_at` were still the original timestamps. `search "Acting"` printed `Warning: index may be stale for: harbor-kb (a file on disk is newer than the index). Results could be out of date — run pyrite index sync to refresh.` and `No results found.` After `index sync` the search hit, with the body text in the snippet. A new hand-written file (`sources/council-clerk-memo.md`) was not visible until the sync either (`Added: 1 Updated: 1`).
**Worked well:** the staleness warning on search, and the sync counts. Two things were contradictory: `get` reads the file, `search` reads the index, and `get` reports timestamps from the index.
**Would have helped:** `get` to flag a stale index the way `search` does, or to report `updated_at` from the file's mtime.

**Friction 11 — export only does NotebookLM; there is no plain collection export (annoyed)**
**Command:** `pyrite export collection "q:type:event" -k harbor-kb -o ./export-events --sources redact --bundle single --title "<city-a> repaving events"`
**Got:** `Exported 7 entries`, `Files created: 1`, `export-events/_manifest.md` and `export-events/all_entries.md`. The manifest is clean and the Connections sections carry the link names. But the `Summary` and the Entry Index summary column are blank for all seven, since I never wrote summaries. `pyrite collections list -k harbor-kb` prints `No collections found.`, and the help says the only `--target` is `notebooklm`. The `site` subcommand exists but I did not try it.
**Had to figure out:** that a query string (`q:` prefix) is an accepted collection id, which the help shows only in an example for another KB.
**Would have helped:** a `--target markdown` (one file per entry, with frontmatter), and a line in `export collection --help` that a summary-less entry gives an empty index column.

**Friction 12 — `links validate` does not exist (cosmetic)**
**Command:** `pyrite links validate -k harbor-kb`
**Got:** `Error: No such command 'validate'.` exit 2. `pyrite links check -k harbor-kb --format rich` works and says `No broken links found.`
**Would have helped:** either an alias, or `links check` named in `pyrite links --help` under the same word the other commands use. A reader guessing the verb hits this first.

**Friction 13 — slug from a title with an apostrophe (cosmetic)**
**Command:** `pyrite create -k harbor-kb -t note --title "Lead: <vendor-b>'s protest letter" ...`
**Got:** `"entry_id": "lead-<vendor-b>-s-protest-letter"` (`<vendor-b>` stands for the name token), so the `s` after the apostrophe became a separate word in the id.
**Would have helped:** dropping the apostrophe (`<vendor-b>s`) instead of splitting on it.

**Friction 14 — an event with no `--date` is refused with a clear message (worked well, slowed by my slip)**
**Command:** `pyrite create -k harbor-kb -t event --title "Council approves repaving award" -b "..." --importance 8 -f status=confirmed -f actors="<person-b>,<vendor-a>"` (I forgot `--date`).
**Got:** `{"outcome": "error", "errors": [{"field": "date", "rule": "required", "message": "Event must have a date", "expected": "YYYY-MM-DD"}], "error": "Event must have a date", "error_code": "VALIDATION_FAILED", "retryable": false, "suggestion": "set date (YYYY-MM-DD)"}`, exit 1. The entry was not written. Re-running with `--date 2024-06-11` worked. This is the error shape the docs describe; the `expected` and `suggestion` fields made the fix obvious.

**Worked well**
- `pyrite init --template research --path ./<kb-dir> --name harbor-kb --format rich` printed the types and created the folders in one call.
- `create --link target:relation` wrote the entry and its links together. A dangling target was reported as `outcome: partial`, `failed_links` with the exact `finish_up` command, and exit 3. That is the right shape for an agent.
- `pyrite links check` and `pyrite qa stale --max-age 0` gave clean, sortable output; `stale` correctly exempts events as historical.
- `pyrite export collection` writes a readable manifest and one file per bundle; the connections section is the useful part.
- Deleting with `--force` returned `inbound_link_count` and `warnings`, which is a good signal before a destructive call.
- Search's "index may be stale" warning caught the hand-edit case before I trusted the empty result.

**Severity:** slowed (overall: the core write/search/link/validate path works; friction is in the seams: field types, relation names, QA rules, and the export shape)

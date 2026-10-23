## 2026-10-23 · software-kb via CLI, invented small-team project · claude-sonnet-5-5
Scratch HOME (`$PWD/home`), KB at `./proj`, binary `<scratch>`. Output was read as shown unless I say I piped it. Invented names are neutral (api-gateway, auth-service, etc.); no real people or organisations.

**Docs consulted:** `pyrite --help`, `pyrite orient` (no KB), `docs/getting-started.md` (software template section), AGENTS.md. Getting-started says the `sw` commands need `pip install -e extensions/software-kb`; the venv already had it, so I could not check what a fresh user sees without it.

**Friction 1 — `sw log` reports success and writes nothing (BUG, reproduced twice).**
**Command:** `pyrite sw log fix-order-total-rounding -k proj --summary "Found float rounding" --decisions "use Decimal"` (run twice, second with `-s "second log"`)
**Expected:** a work-log entry saved and visible in `sw context-for-item`.
**Got:** `Logged: fix-order-total-rounding-log-20261023T055443 / File: work-logs/fix-order-total-rounding-log-20261023T055443.md`, rc 0. Observed afterwards: no `work-logs/` dir under the KB, no such file anywhere under the scratch dir (`find`), `pyrite get <log-id> -k proj` is NOT_FOUND, `list-entries -t work_log` is empty, `context-for-item` shows `work_logs: []`. Reading `plugin.py` (~line 3075-3120): the tool returns a dict with `"note": "Create the markdown file with this frontmatter, body, and link to complete."` and the CLI prints "Logged:" from it. Conclusion: the CLI path drops the file-creation step. An agent following the skill would believe it recorded decisions and lose them.
**Would have helped:** write through `KBService.create_entry` like `new-adr` does, or print "not saved".
**Severity:** blocked (for the work-log feature; silent data loss)

**Friction 2 — `qa` subcommands take the KB positionally, everything else takes `-k`.**
**Command:** `pyrite qa validate -k proj` -> `Error: No such option: -k` (USAGE_ERROR). Same for `qa stale`, `qa status`, `qa gaps`, `qa assess`. Correct is `pyrite qa validate proj`. `qa checkers proj` also fails ("unexpected extra argument") because it uses `--kb`/`-k`; so `qa` has both conventions inside it.
Also defaults differ: `qa stale`/`qa checkers` default to rich tables, `qa validate`/`qa status` to json, `qa gaps` prints text with no `--format`.
**Severity:** slowed (hit it on 3 commands in a row)

**Friction 3 — orient / `kb schema show` and #232 (record requested).**
**Commands:** `pyrite orient -k proj` (piped to head, then to python to pull `schema.types.backlog_item`), `pyrite kb schema show proj > schema.json`, `pyrite create -k proj -t backlog_item --template`.
**Observed:** In this build, on a KB made with `init --template software`, both DO list fields for `backlog_item`: `kind, status, priority, assignee, effort` with select options (status 10 values, kind 10, priority 4, effort XS-XL). `kb schema show` adds `"required": ["title"]`; `orient` omits the `required` key (it has only `optional`). So the AGENTS.md claim "schema block is rubric prose with no field list" was not true here, but it may be true for the repo's own plugin-declared kb; I did not test that (told not to use Pyrite's kb/). Neither says that `kind` is needed by the MCP create tool (`sw_create_backlog_item`), nor that `status` defaults to proposed and priority to medium. The CLI itself needed only `--title`: `create -t backlog_item --title "Login page"` succeeded with kind empty, priority medium (visible in `sw backlog`). `--template` returns a bare skeleton with no fields (`id: ''`, `title: Your Title Here`), so it does not teach the schema either.
**Would have helped:** `orient` including `required`, plus defaults. `--template` listing the allowed fields.
**Severity:** annoyed

**Friction 4 — unknown frontmatter fields and unknown filter values are silently accepted.**
**Command:** `pyrite create -k proj -t backlog_item --title "Typo field" -f kind=bug -f stauts=done -f priorty=high` -> rc 0, `created: true`. File got `metadata: {stauts: done, priorty: high}` and `status: proposed`, `priority: medium`. `pyrite index health` reported nothing (`off_list_values: []`). Contrast: a bad VALUE (`-f priority=urgent`, `-f kind=feat`) is a good SCHEMA_VIOLATION naming the allowed options. And `pyrite sw backlog -k proj -s doing` returns `[]`, rc 0, no hint that "doing" is not a status.
**Would have helped:** reject or warn on unknown keys for a typed schema (suggest nearest: "stauts -> status"); `sw backlog -s` validate against the options.
**Severity:** slowed (a typo'd priority is silently lost)

**Friction 5 — "blocked" is not a status, and nothing lists blocked items.**
**Commands:** `pyrite sw transition gateway-rate-limiting blocked -k proj` -> `Error: Cannot transition from 'proposed' to 'blocked'. Allowed: planned, accepted, deferred, wont_do` (it is also not in the status list). I found the mechanism only by grepping `plugin.py`: a link with relation `blocked_by`. `pyrite link order-list-page login-endpoint -k proj -r blocked_by` worked, and `sw context-for-item order-list-page -k proj` shows `is_blocked: true`. But `sw --help`, `sw backlog --help` and `sw board` have no blocked view, and `sw pull-next` still recommended login-endpoint (right, but I cannot tell whether it skips blocked items; order-list-page was never recommended since it was lower priority). `context-for-item login-endpoint` shows `blocks: []` although the inverse should be `order-list-page`. Same for stale: `qa stale proj --max-age 0` is by days since update, ADRs exempt; nothing flags "in_progress for N days" which is what a team means by stale.
**Would have helped:** `sw backlog --blocked`, `sw stale`, document `blocked_by` in `sw` help and the software-kb docs. (`extensions/software-kb/README.md` does not exist; I looked.)
**Severity:** slowed

**Friction 6 — `pull-next` preview disagrees with `context-for-item`.**
**Command:** `pyrite sw pull-next -k proj` -> `"context_preview":{"adr_count":0,"component_count":0,"validation_count":0}` for login-endpoint, while `pyrite sw context-for-item login-endpoint -k proj` lists `adrs: [adr-0002 ... relation related_to]` (from my mistaken link, see below). Observed twice. Probably pull-next counts a different link direction/relation; I did not trace it.
**Severity:** annoyed

**Friction 7 — plain-text errors on `sw` workflow commands; JSON elsewhere.**
**Command:** `pyrite sw transition fix-order-total-rounding done -k proj >o 2>e`. Both stdout and stderr were plain `Error: Cannot transition from 'proposed' to 'done'. Allowed: planned, accepted, deferred, wont_do` (no JSON, no error_code, rc 1), though `sw claim` with a missing option printed both plain text and the JSON error shape, and `task update` gives full JSON with a good walkthrough of the lifecycle. The error for `sw transition` is itself useful (lists allowed moves). Contract in `docs/json-contracts.md`/orient says `{error, error_code, retryable}`.
**Severity:** annoyed

**Friction 8 — claim path for an agent requires accept first, and the error is oblique.**
**Command:** `pyrite sw claim fix-order-total-rounding -a agent-1 -k proj` on a proposed item -> `Error: Cannot transition from 'proposed' to 'in_progress'. Allowed: planned, accepted, ...`. `sw pull-next` said "No accepted items available" before I accepted anything, so that part was clear. Nothing says who may move proposed->accepted. Flow that worked: transition accepted, claim -a, submit, review -o approved --reviewer, which moved it to done by itself; my follow-up `transition ... done` then failed `from 'done' to 'done'` (correct but I did not expect review to close it; `sw review` output says "New status: done").
**Severity:** cosmetic

**Friction 9 — smaller things.**
- `pyrite delete login-page -k proj` with no TTY: `Delete entry ... [y/N]: Aborted!` rc 1; I guessed `--yes` (-> `No such option: --yes`), then found `--force`/`-f`. In `create`, `-f` is `--field`; in `sw backlog`, `-f` is `--format`; in `delete`, `-f` is `--force`. Same short flag, three meanings.
- `pyrite sw new-adr --help` prints a developer changelog ("Was: query the next number ... (#391)") as its help text. `new-adr` has no `--body`/`--deciders`, so every ADR is a TODO skeleton and I had to edit files or use `update` (not tried). `qa validate` does not flag TODO ADR bodies.
- `pyrite sw backlog -k proj --format rich` truncates ids (`fix-order-to`, `login-endpoi`), which are the thing you need to type next. `sw board` json shows only lane counts, no items; no way to see what is in a lane.
- `pyrite sw components` json has no `id` field (title only), unlike `sw adrs`/`sw backlog`.
- `pyrite init --template software` result and `pyrite kb list` say `"type": "generic"` for a software KB, and `orient -k proj` says `kb_type: generic`; `types: []` and `total_entries: 0` in the first orient before entries, fine.
- `pyrite task create "Write login tests" -b "unit tests" ...` echoed `"title":"Writelogintests"` (spaces stripped) because I piped to `tr -d '\n '`. My pipe, not a bug; noted so nobody chases it. The listing shows the right title.
- `pyrite link ... -r affects` accepted any free-text relation; `backlinks orders-db` then reports `relation: related_to` and `forward_relation: affects` for the same link, which read backwards to me. `link` to a missing target is a clean NOT_FOUND (rc 1).
- `qa validate proj` flags components `has no outgoing links` and `orphan` separately, though the ADRs I wrote point at them; "orphan" is both-direction but the rubric counts outgoing only. `qa gaps` says 10 entries have no outbound links, `qa validate` lists 4 orphans: consistent once explained, but I read it twice.
- `task` flow: `task claim` OK; second claim by another agent gave `{"claimed": false, "outcome": "nothing", "current_status": "claimed"}` rc 1 (clear). There is no `task done`; `task update -s done` from `claimed` is refused with a good message that spells out open -> claimed -> in_progress -> done. `sw` items and `task` entries are two separate lifecycles with two claim commands; the brief's "claim and done" matched `task`, `sw` has no `done`.

**Mistakes tried (and what the tool said):**
1. Skipped a status: `sw transition ... done` from proposed -> refused, allowed moves listed (good).
2. Out-of-range value: `create ... -f priority=urgent` -> SCHEMA_VIOLATION listing options (good); `kind=feat` same.
3. Link to a nonexistent entry: `link adr-0001 no-such-comp -k proj -r affects` -> NOT_FOUND rc 1 (good).
4. Wrong/missing KB: `sw backlog -k nope` -> KB_NOT_FOUND with suggestion (good, `did_you_mean: []`). Omitting `-k` with one KB registered just worked (`sw adrs`).
5. `-k` on qa, `--yes` on delete, `claim` without `-a`: usage errors, see above.
6. Typo'd field names: accepted silently (Friction 4).
7. Second claim of a claimed task: refused cleanly.

**Worked well:**
- `init --template software` gave a ready KB with all ten types in one command; `sw new-adr` numbered ADRs sequentially (adr-0001..3, ids and filenames) and wrote through the pipeline immediately, searchable at once.
- SCHEMA_VIOLATION messages name the field, bad value, and every allowed value.
- `sw transition` errors list the allowed next states; `task update` error spells out the whole lifecycle.
- `sw check-ready`, `context-for-item` (bundle of ADRs, deps, reviews) and `pull-next` are what an agent wants; `review -o approved` closing the item is tidy.
- `task claim` race semantics match the orient contract text.
- `kb schema show` gives the allowed values for every select field in one call.

**Severity (overall):** one data-loss bug (1), the rest slowed/annoyed.

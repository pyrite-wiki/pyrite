## 2026-11-16 · hallway test: set up a software KB (3 components, 2 ADRs, 6 backlog items) and run a working session via the CLI · claude-sonnet-5-5

Isolated HOME/PYRITE_CONFIG_DIR/PYRITE_DATA_DIR, invented 3-file Python project. Used only README/docs, `--help` and `pyrite orient`. Whole flow completed (init, create, claim, log, submit, review, done, search, health, QA); nothing blocked. Frictions below, most severe first.

**Friction 1 — a new backlog item can't be pulled, and nothing says why or how to fix it.**
**Command:** `pyrite sw pull-next` (right after creating 6 items with `pyrite create -k proj -t backlog_item ... -f priority=... -f effort=...`)
**Expected:** the critical bug.
**Got:** `{"recommendation":null,"reason":"No accepted items available","wip_status":{"current":0,"limit":5,"policy":"warn"}}`
**Friction:** Items are created as `proposed`; `pull-next` only considers `accepted`. The reason names a status but not the command that sets it. `pyrite sw board` showed 6 in "Backlog" and 0 in "Ready", and `sw check-ready` on the item said `"ready":true`, which contradicted "no accepted items". I guessed `pyrite sw transition crash-on-empty-key ready`, which failed: `Error: Cannot transition from 'accepted' to 'ready'. Allowed: in_progress, wont_do, proposed, deferred`. So the board's "Ready" lane is `accepted`, and `ready` is not a status.
**Had to figure out:** the lifecycle `proposed -> accepted -> in_progress -> review -> done` by trial. `pyrite sw transition --help` does not list statuses.
**Would have helped:** `reason` should say "run `pyrite sw transition <id> accepted`"; `transition --help` should list valid statuses; board lane names should match status names (or show the mapping). `check-ready` saying ready=true while `pull-next` says nothing is available needs one sentence of explanation.
**Severity:** slowed

**Friction 2 — `-k/--kb` is spelled four different ways across commands.**
**Commands as run:**
- `pyrite qa validate -k proj` -> `Error: No such option: -k`
- `pyrite qa status --kb proj` -> `Error: No such option: --kb` (qa takes a positional `kb_name`: `pyrite qa validate proj`)
- `pyrite sw context-for-item crash-on-empty-key` -> `Error: Missing option '--kb' / '-k'.` (every other `sw` command I used resolved the only KB without `-k`)
- `pyrite sw claim crash-on-empty-key` -> `Missing option '--assignee' / '-a'`; `pyrite sw review` requires `--reviewer`; `pyrite sw log` requires `-s`. Those are fine, just more guessing.
**Friction:** for an agent the cost is one failed call per command, learned afresh each time. The JSON error is clean and the `suggestion` ("Try ... --help") is generic, so it doesn't say "use the positional argument".
**Would have helped:** accept `-k` on `qa`; make `sw context-for-item` infer the KB like its siblings; or have the USAGE_ERROR suggestion name the accepted form.
**Severity:** annoyed

**Friction 3 — short flags mean different things in neighbouring commands.**
`pyrite create ... -l store:affects` (`-l` = `--link`, `-f` = `--field`), but `pyrite sw backlog -l 5` is `--limit` and `-f rich` is `--format`. I typed `-f rich` after a habit from `create -f key=value`; it happened to work only because the sw commands define `-f`. Mixed use in a shell history or an agent's learned pattern is a trap.
**Severity:** annoyed

**Friction 4 — `sw` commands have no `--format`/default-output consistency.**
**Commands:** `pyrite sw new-adr "..." -k proj --format json` -> `Error: No such option: --format` (also printed as a JSON error object on a second line, so stdout has both a human line and JSON). `new-adr`, `claim`, `submit`, `review`, `log`, `transition` print prose ("Claimed: ...", "Transitioned: ..."); `sw backlog`, `board`, `pull-next`, `context-for-item`, `adrs` print compact one-line JSON by default and `-f rich` for tables; `create`/`update`/`link` print pretty JSON. `docs/json-contracts.md` documents `--format` defaults, but an agent can't tell from `--help` which commands lack `--format` entirely. An agent scripting the working session can parse the read commands and has to scrape the write commands' prose (e.g. ADR id and file path from `Created ADR-0001: ...`).
**Would have helped:** `--format json` on all `sw` write verbs returning the same `{"outcome":"done","entry_id":...}` shape `create` uses.
**Severity:** slowed

**Friction 5 — `pyrite sw new-adr` creates a TODO skeleton and offers no way to write the body.**
**Command:** `pyrite sw new-adr "Use SQLite for persistence" -k proj --status accepted` -> file with `TODO: What is the issue...` in Context/Decision/Consequences, `deciders: []`, `superseded_by: ""`. Options are only `--status` and `--kb`. I used `pyrite update adr-0001 -k proj --body "..."` / `--stdin`, which replaces the whole body (the headings I wanted to keep). Note the ADR id is `adr-0001` while the filename starts `0001-`; I had to guess `adr-0001` as the `update` id (it was visible in the file frontmatter, not in the command output, which prints "ADR-0001").
**Would have helped:** `--body/--body-file/--stdin` on `new-adr`; print the entry id (`adr-0001`) in the output. Also the `status: accepted` ADR was written with no `deciders`, and neither QA nor health complained.
**Severity:** annoyed

**Friction 6 — `pyrite create --template` shows no type-specific fields.**
**Command:** `pyrite create -k proj -t backlog_item --template` -> `{"template": "---\nid: ''\ntype: backlog_item\ntitle: Your Title Here\ntags: []\n---\n\nYour content here.", ...}` (same for `component`, `adr`). It never mentions `kind`, `priority`, `effort`, `status`. I found the field names via `pyrite orient -k proj`, which in a fresh empty KB prints a long schema with `optional` lists that, for backlog_item/component, omit required-looking fields and options (I couldn't tell `priority` values from `orient`; I guessed `critical|high|medium|low` and it worked). `orient` also reported `"kb_type": "generic"` for a KB created with `-t software`, and `total_entries: 0, types: []` (correct but empty, so the schema block is the only content).
**Would have helped:** `--template` emitting the type's declared fields with allowed values; `orient` showing `kb_type: software`. (AGENTS.md already admits the field-list gap for plugin types, so this is a confirmation, not news.)
**Severity:** slowed

**Friction 7 — `context-for-item` returns empty `body_preview`, and `pull-next`'s preview counts disagree with it.**
**Commands:** `pyrite sw context-for-item persist-store-to-sqlite -k proj` -> `"components":[{"id":"store",...,"relation":"affects","body_preview":""}]`, same for the ADR, though both have bodies ("In-memory key/value store used by the API." and the ADR text). And `pyrite sw pull-next` for `crash-on-empty-key` returned `"context_preview":{"adr_count":0,"component_count":0,"validation_count":0}` while `context-for-item` on the same item listed 1 component (`store`) via the link I gave at create time.
**Friction:** the one command meant to "assemble full context" doesn't include the text an agent needs to start work, so I had to `pyrite get` each linked entry. A count of 0 in the pull-next preview would lead an agent to skip the context call.
**Severity:** slowed

**Friction 8 — a list field passed with `-f` is stored as a bare string.**
**Command:** `pyrite create -k proj -t component --title "API" ... -f dependencies=store` -> frontmatter `dependencies: store` (scalar), whereas the schema (`sw` docs say `dependencies`) is a list. No warning; `--append dependencies=store` exists on `update` and would presumably have been right, but `create --help` doesn't say so. Also this field is plain text, not a link: `pyrite backlinks store` doesn't show the `api` component (I'd have had to add `-l store:depends_on`).
**Severity:** annoyed

**Friction 9 — smaller things.**
- `pyrite sw transition crash-on-empty-key done` right after `sw review ... -o approved` (which already moved it to done) gave `Cannot transition from 'done' to 'done'. Allowed: retired, accepted`. My mistake, but `review`'s help states it, and `submit` -> `transition done` is the natural way an agent would finish; both exist, and it's not obvious which is the intended close.
- `pyrite search "sqlite" -k proj --format rich`: the ID and Type columns render as `…` at 80 columns, so the table can't be used to pick an id. JSON default was fine.
- `backlinks store` lists my `affects` links as `"relation":"related_to","forward_relation":"affects","inverse_relation":null`. The relation name an agent chose is only visible in `forward_relation`; `relation` says `related_to`.
- `pyrite search "persistence AND store OR"` gave a good QUERY_SYNTAX error with a fix; and `--mode semantic` on a fresh KB said clearly to run `pyrite index embed` with a warning on stderr plus `legs_run: []` in JSON. Both are good messages.
- `pyrite qa validate proj` emits 14 identical "has no tags" warnings for entries the CLI created with no tags by default (ADRs from `new-adr` can't take `--tags`). Noise that hides real issues in a fresh KB.
- `pyrite init` does not `git init` or register a repo; my KB sat inside the project git repo untracked. Documented in getting-started, fine.

**Had to figure out:** status lifecycle; that `qa` takes a positional KB; link syntax `-l target:relation` (from `create --help`); `sw claim -a` is required; ADR entry id form `adr-0001`; priority/kind values (guessed, none validated against a list I could see).

**Worked well:**
- `pyrite sw board`/`pull-next`/`claim`/`submit`/`review`/`log`: once the status was `accepted`, the kanban cycle worked end-to-end with sensible state transitions, WIP info and a clear error listing allowed transitions.
- `pyrite create -l target:relation` linking in one call, with `failed_links: []` reported in the result, and `index health` plus the typed JSON errors (`error_code`, `retryable`, `suggestion`) made correcting mistakes cheap.

**Severity (overall):** slowed

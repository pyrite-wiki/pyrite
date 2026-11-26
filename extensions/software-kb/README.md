# Pyrite software-kb

The software-kb extension turns a Pyrite knowledge base into a place to run a
software project: architecture decision records (ADRs), components, standards,
runbooks and a backlog with a kanban lifecycle. It adds the entry types, the
`pyrite sw` commands and the `sw_*` MCP tools for them.

This page is enough to install it, create a software KB and run the backlog from
proposal to done.

## Install

The extension needs Pyrite itself. Install that first
([main README, Install](../../README.md#install)), then add the extension.

Without a clone, pinned to the release tag:

```bash
pip install "pyrite-software-kb @ git+https://github.com/pyrite-wiki/pyrite@v0.26.4#subdirectory=extensions/software-kb"
```

From a clone of this repository:

```bash
pip install -e extensions/software-kb
```

`pyrite sw --help` lists the commands once it is installed. An extension is
never installed by `pyrite init`: if `pyrite sw` is missing, this step was
skipped.

## Quick start

Create a KB from the software template. `--path` is required:

```bash
pyrite init --template software --path my-project
```

It prints the new KB's name, path and entry types (`adr`, `design_doc`,
`standard`, `programmatic_validation`, `development_convention`, `component`,
`backlog_item`, `runbook`, `work_log`, `milestone`, `task`) and registers the KB
under the name `my-project`. Pass `-k my-project` to later commands, or work
from inside the directory.

An ADR (numbered for you, starts `proposed`):

```bash
pyrite sw new-adr "Use PostgreSQL" -k my-project --context "Need ACID transactions" --decision "Use PG 16"
```

```text
Created adr-0001 (ADR-0001): Use PostgreSQL
  File: .../my-project/adrs/0001-use-postgresql.md
  Status: proposed
```

A component:

```bash
pyrite create -k my-project -t component --title "Auth service" \
  -b "Handles login." -f kind=service -f path=src/auth -f owner=platform
```

```json
{
  "created": true,
  "entry_id": "auth-service",
  "entry_type": "component",
  "outcome": "done"
}
```

A backlog item:

```bash
pyrite create -k my-project -t backlog_item --title "Add login rate limit" \
  -b "Limit attempts." -f kind=feature -f priority=high -f effort=S
```

```json
{
  "created": true,
  "entry_id": "add-login-rate-limit",
  "entry_type": "backlog_item",
  "outcome": "done"
}
```

The new item is `proposed`, and `pull-next` offers only `accepted` items, so it
says nothing is available until you accept one:

```bash
pyrite sw pull-next -k my-project
```

```json
{"recommendation":null,"reason":"No accepted items available","wip_status":{"current":0,"limit":5,"policy":"warn"}}
```

```bash
pyrite sw transition add-login-rate-limit accepted -k my-project
```

```text
Transitioned: Add login rate limit
  proposed -> accepted
```

`pull-next` now recommends it (`"recommendation":{"id":"add-login-rate-limit",...}`).
The rest of the cycle is under [Review](#review).

## Entry types

Create any of these with `pyrite create -k <kb> -t <type> --title ... -f field=value`.
The plugin registers ten types; the template also adds the core `task` type.

| Type | Key fields | What it is |
|---|---|---|
| `adr` | `adr_number` (assigned), `status`, `deciders`, `superseded_by` | An architecture decision. Statuses: `proposed`, `accepted`, `rejected`, `deprecated`, `superseded`. |
| `design_doc` | `status`, `reviewers` | A design or specification. Statuses: `draft`, `review`, `approved`, `implemented`, `obsolete`, `active`. |
| `component` | `kind`, `path`, `owner`, `dependencies` | A module, service, library, CLI and so on. `kind` is one of `module`, `service`, `package`, `library`, `cli`, `api`, `database`, `application`, `utility`, `endpoint`, `docs`. |
| `backlog_item` | `kind`, `status`, `priority`, `effort`, `rank`, `assignee` | A unit of work; see [the lifecycle](#the-backlog-lifecycle). |
| `standard` | `category`, `enforced` | A rule the project follows. |
| `programmatic_validation` | `category`, `check_command`, `pass_criteria` | A rule a command can check. |
| `development_convention` | `category` | Guidance that takes judgment. |
| `runbook` | `runbook_kind`, `audience` | A procedure. `runbook_kind` is one of `howto`, `troubleshooting`, `setup`, `operations`, `onboarding`. |
| `milestone` | `status` (`open` or `closed`) | A group of backlog items; `pyrite sw milestones` shows progress. |
| `work_log` | `item_id`, `decisions`, `rejected`, `open_questions` | A session note on a backlog item; made by `pyrite sw log`. |

Backlog fields:

- `kind`: `feature`, `bug`, `tech_debt`, `improvement`, `spike`, `enhancement`,
  `documentation`, `docs`, `task`, `epic`, `refactor`, `chore`, `process`. An
  epic is a `backlog_item` with `kind: epic`; link its subtasks to it with
  `-r subtask_of` (inverse `has_subtask`).
- `priority`: `critical`, `high`, `medium` (default), `low`.
- `effort`: `XS`, `S`, `M`, `L`, `XL`.
- `rank`: an order within a priority band, set by `pyrite sw prioritize`;
  `0` is unranked.

Other relations the plugin declares: `implements`, `amends`, `supersedes`,
`documents`, `depends_on`, `tracks`, `session_for`, each with its inverse.
Create one with `pyrite link <from> <to> -r <relation> -k <kb>`.

## The backlog lifecycle

A new item is `proposed`. Move it with `pyrite sw transition <item> <status>`
(`-r "<reason>"` where the table says a reason is required). `sw claim`,
`sw submit` and `sw review` are shortcuts for the moves marked below.

| From | To | Reason | Command |
|---|---|---|---|
| `proposed` | `planned` | | `sw transition` |
| `proposed` | `accepted` | | `sw transition` |
| `proposed` | `deferred` | | `sw transition` |
| `proposed` | `wont_do` | required | `sw transition` |
| `planned` | `accepted` | | `sw transition` |
| `planned` | `deferred` | | `sw transition` |
| `accepted` | `in_progress` | | `sw claim <item> -a <who>` (sets the assignee) |
| `accepted` | `proposed` | required | `sw transition` |
| `accepted` | `deferred` | | `sw transition` |
| `accepted` | `wont_do` | required | `sw transition` |
| `in_progress` | `review` | | `sw submit` |
| `in_progress` | `done` | | `sw transition` |
| `in_progress` | `accepted` | required | `sw transition` (give the item back) |
| `review` | `done` | | `sw review -o approved` |
| `review` | `in_progress` | required | `sw review -o changes_requested` |
| `done` | `retired` | | `sw transition` |
| `done` | `accepted` | required | `sw transition` (reopen) |
| `deferred` | `proposed` | | `sw transition` |

```text
proposed -> planned -> accepted -> in_progress -> review -> done
```

is the main path. Any other move is refused with the moves that are allowed:

```bash
pyrite sw transition add-login-rate-limit ready -k my-project
```

```text
Error: Cannot transition from 'proposed' to 'ready'. Allowed: planned, accepted, deferred, wont_do
```

`ready` is not a status. The board's **Ready** lane is the `accepted` status.
`done`, `retired` and `wont_do` are settled: a blocker in any of them no longer
blocks. `superseded` is a valid `status` value that no move leads to; set it with
`pyrite update`.

The CLI and the MCP tools name the same moves, but only MCP `sw_claim` checks
blockers (and the board's Definition of Ready gate); see below.

## Blocked is a link, not a status

There is no `blocked` status. Say an item is blocked by linking it to the item
that blocks it. With a second item, `audit-log-export`, created and accepted as
above, block the first on it:

```bash
pyrite link add-login-rate-limit audit-log-export -r blocked_by -k my-project
```

```json
{
  "links": [
    {
      "created": true,
      "source": "add-login-rate-limit",
      "relation": "blocked_by",
      "target": "audit-log-export",
      "target_kb": "my-project",
      "resolved": true
    }
  ],
  "outcome": "done"
}
```

The inverse is `blocks`: `pyrite backlinks audit-log-export` lists
`add-login-rate-limit` with relation `blocks`. Writing the link the other way,
`pyrite link audit-log-export add-login-rate-limit -r blocks`, blocks the same
item and reads the same. A blocker is unresolved until it
is `done`, `retired` or `wont_do`; when it settles, the block lifts with no
other step.

What each command does with a blocked item, as of v0.26.4:

| Command | Blocked item |
|---|---|
| `pyrite sw pull-next` | Skips it. It lists skipped items under `blocked_items` with their unresolved blockers. When every accepted item is blocked, the reason is `All accepted items are blocked by dependencies`. |
| MCP `sw_claim` | Refuses: `claimed: false`, error `Item has unresolved dependencies`, and the unresolved blockers. |
| `pyrite sw claim` | **Does not check today.** It claims a blocked item. Check `pull-next` or `check-ready` first. |
| `pyrite sw transition` | Does not check blockers. |
| `pyrite sw check-ready <item>` | Reports `"No unresolved blockers"` as failed, with the blocker ids. |
| `pyrite sw context-for-item <item>` | Shows `dependencies`: `blocked_by` (each with its status and `resolved`), `blocks` and `is_blocked`. |

`pull-next` with one blocked and one free item:

```json
{"recommendation":{"id":"audit-log-export",...},"context_preview":{...},"wip_status":{...},"blocked_items":[{"id":"add-login-rate-limit","title":"Add login rate limit","blocked_by":["audit-log-export"]}]}
```

The CLI and MCP claims are meant to agree, but until issue #1288 lands the CLI
is the unchecked one.

## Review

`pyrite sw submit <item>` moves an `in_progress` item to `review`.
`pyrite sw review-queue` lists items in review. The reviewer records an outcome;
`--reviewer` is required:

```bash
pyrite sw review add-login-rate-limit -o approved --reviewer bob -k my-project
```

```text
Reviewed: add-login-rate-limit
  Outcome: approved
  New status: done
```

- `-o approved` moves the item to `done`.
- `-o changes_requested` moves it back to `in_progress` and needs `--feedback`;
  without it the command says `Error: Feedback is required when requesting changes`.

```bash
pyrite sw review add-login-rate-limit -o changes_requested --reviewer bob --feedback "Add tests" -k my-project
```

```text
Reviewed: add-login-rate-limit
  Outcome: changes_requested
  New status: in_progress
```

Both outcomes are kept: `pyrite sw context-for-item <item>` lists them under
`reviews` with the reviewer, result and feedback. After `changes_requested` the
item is `in_progress` again: rework it, then `sw submit` it again.

`sw review` needs the item to be in `review`; on any other status it stops with
`Error: Item is in 'accepted' status, not 'review'`. Only `-o approved` closes an
item.

## The board

`pyrite sw board` shows the items by lane with each lane's WIP limit. The lanes,
the WIP policy and the Definition of Ready and Done gates come from `board.yaml`
in the KB's root. `pyrite init -t software` writes none: with no file the
built-in board applies (lanes Backlog `proposed, planned`; Ready `accepted`; In
Progress `in_progress`, limit 5; Review `review`, limit 3; Done `done`; both gates
at `warn`).

A `board.yaml` replaces the built-in board whole; nothing is merged. Its format,
the gate checkers and the `warn` and `enforce` policies are in
[plugins: board.yaml](../../docs/plugins.md#boardyaml-lanes-wip-limits-and-gates).

A file that cannot be loaded is refused, never half-applied (since 0.26.4).
`sw board` and `sw pull-next` stop with exit 1 and a message that names
`board.yaml`:

```json
{"error": "board.yaml 'lanes' must be a list", "error_code": "VALIDATION_FAILED", "retryable": false, "suggestion": "Fix board.yaml in the KB's root, or remove it to use the defaults."}
```

A key Pyrite does not read (anything but `lanes`, `wip_policy`, `gates`) is
refused the same way. Fix the file or delete it to get the defaults back.

## Command reference

`pyrite sw <command>`; `-k <kb>` names the KB for each. Most list commands print
JSON (`--format rich` for a table where offered). The MCP column names the tool
where it differs from the command; `read` tier tools list, `write` tier tools
change the KB.

| Command | What it does | MCP tool |
|---|---|---|
| `adrs` | List ADRs | `sw_adrs` |
| `new-adr <title>` | Create the next numbered ADR (`--body-file`, `--stdin`, `--context`, `--decision`, `--consequences`, `--deciders`) | `sw_create_adr` |
| `backlog` | List backlog items (`--status`, `--priority`, `--kind`, `--epic`, `--sort` by `priority`, `rank` or `created`, `--group-by epic`, `--limit`, `--offset`); 50 by default, `--limit 0` for all | `sw_backlog` |
| `epics` | List epics with subtask progress | `sw_epics` |
| `epic <epic-id>` | Show an epic's subtasks by status | `sw_epic_detail` |
| `prioritize <ids...>` | Set rank order, highest first (or `--after`/`--before` one id) | `sw_prioritize` |
| `standards` | List all standard types | `sw_standards` |
| `validations` | List programmatic validations | `sw_validations` |
| `conventions` | List development conventions | `sw_conventions` |
| `migrate-standards` | Move `standard` entries to validation or convention types (`--apply` to write; dry run by default) | none |
| `milestones` | List milestones with progress | `sw_milestones` |
| `components` | List components | `sw_component` |
| `board` | The kanban board | `sw_board` |
| `review-queue` | Items in review, longest wait first | `sw_review_queue` |
| `claim <item> -a <who>` | `accepted` to `in_progress`, set assignee | `sw_claim` |
| `submit <item>` | `in_progress` to `review` | `sw_submit` |
| `transition <item> <status>` | Any move in the table; `-r` for a reason | `sw_transition` |
| `pull-next` | Recommend the top `accepted`, unblocked item | `sw_pull_next` |
| `review <item> -o <outcome> --reviewer <who>` | Record a review outcome | `sw_review` |
| `log <item> -s <summary>` | Log a work session (`--decisions`, `--rejected`, `--open-questions`) | `sw_log` |
| `check-ready <item>` | Check Definition of Ready without claiming | `sw_check_ready` |
| `refine` | Scan the backlog for Definition of Ready gaps | `sw_refine` |
| `context-for-item <item>` | ADRs, components, validations, conventions, reviews, work logs and dependencies for an item | `sw_context_for_item` |

There is no `sw` command to create a backlog item: use `pyrite create -t
backlog_item`, or MCP `sw_create_backlog_item`.

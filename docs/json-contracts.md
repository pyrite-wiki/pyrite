# JSON Contracts

Canonical shapes returned by the pyrite CLI (`--format json`), MCP tools,
and REST API. Written down here so agents and extension authors can rely
on a stable contract instead of re-deriving it from source or an
operator's memory — see `docs-operational-contracts-travel-with-tool`.

## Error shape

ADR-0037 theme 2 (maintainer decision, 2026-09-25): **codes live on the
exception class** (`pyrite/exceptions.py`; every `PyriteError` subclass
carries an `error_code`, and a safe `public_message` where its own text can
carry server-side detail). Each transport has its own wire shape, but each
now derives its code from the class the same way — no per-transport lookup
table keyed by exception type.

**MCP and the CLI** (MCP tool `_error`/`_refusal`, and CLI commands with a
`--format` flag via `cli_error`/`cli_error_from`) return the flat structure
below. A CLI command with no `--format` flag prints a refusal as Rich text
(`ERROR [CODE]: message`), never this JSON; the table under "`--format`
defaults" says which. Making CLI output consistent regardless of TTY is
**#303** (open); this page describes the shape you get when you do get JSON,
not a promise that every command gives you the option.

```json
{
  "error": "human-readable message",
  "error_code": "MACHINE_CODE",
  "suggestion": "optional fix hint",
  "retryable": false
}
```

- `error` — always present, human-readable.
- `error_code` — always present, machine-readable (`QUERY_SYNTAX`,
  `KB_NOT_FOUND`, `INVALID_TIER`, etc.). Match on this, not on `error`
  text.
- `suggestion` — omitted entirely when there's no applicable fix hint.
  Don't assume the key exists.
- `retryable` — always present. `true` means the same request could
  succeed on retry (e.g. a transient lock); `false` means the request
  itself needs to change first (e.g. a malformed query).

For an unqualified entry ID in multiple readable KBs, `pyrite get`,
`pyrite-read get`, and MCP `kb_get` refuse with `error_code: "AMBIGUOUS"`.
The message lists the candidate KBs and suggests supplying a KB name. The
`pyrite get` and MCP JSON errors also carry `candidate_kbs`, containing only
KBs that caller can read. Zero matches still return `NOT_FOUND`; one match
still returns the entry. A blank KB name is treated as an omitted one.

REST `GET /api/entries/{id}` without `kb` retains its first readable match
temporarily because web entry links can omit `?kb=`. REST callers that need
unambiguous identity should supply `kb` until those web links are updated.

**MCP only, for one release: `legacy_error_code`.** Where a class's code
changed on MCP because REST's more specific code won (ADR-0037 theme 2:
`ENTRY_NOT_FOUND`/`KB_NOT_FOUND` replacing `NOT_FOUND`, `KB_READ_ONLY`
replacing `READ_ONLY`, `INVALID_FRONTMATTER` replacing `VALIDATION_FAILED`
for `FrontmatterError` specifically, `CONFIG_CONFLICT`/`CONFIG_SAVE_REFUSED`
replacing `CONFIG_ERROR`, `STORAGE_ERROR`/`PLUGIN_ERROR` replacing
`REQUEST_REFUSED`, and others), the MCP tool response carries the *old*
code one more release in `legacy_error_code`, alongside the new
`error_code`. A class whose REST and MCP codes already agreed gets no
`legacy_error_code` key at all — that includes the base `ValidationError`
itself: its code is `VALIDATION_FAILED` on every transport, unchanged (see
"Write refusals" below). The CLI has no transition field, and no CLI write
command has adopted the new class-level codes yet — see "Write refusals".

Two more joined in 0.25.7 (#66), both now `KB_NOT_FOUND`: the refusal a
caller with per-KB scoping gets from any MCP tool, prompt or resource for a
KB outside its readable set (`legacy_error_code: "NOT_FOUND"`; the answer is
the same whether the KB is private or does not exist), and `kb_orient`'s
answer for a name that is not a KB (`legacy_error_code: "OPERATION_FAILED"`).
That scoped refusal carries one static `suggestion` ("Call kb_orient with no
kb_name to list the KBs you can read."), the same string for a private and
an absent name and naming no KB. `kb_orient` and `pyrite orient` add
`did_you_mean` beside the contract's keys: a list of at most three near-match
KB names the caller may read, empty when there are none (a scoped caller is
refused by the scoped refusal first, so it sees the static hint, not
`did_you_mean`). A `kb_name` that is not a string is `VALIDATION_FAILED`; `null`
is "no name".

**REST** answers a domain refusal as `{"detail": {"code", "message",
"retryable", "hint"?}}` — the web client already speaks this shape
(`web/src/lib/api/client.ts`). This is true whether the refusal reached the
central `PyriteError` handler (`server/errors.py`) or an endpoint's own
`HTTPException(detail={...})` (e.g. `write_refusal.refusal_http`): both
answer the same wrapped shape, with the same code, for the same exception
class. `hint` carries a `ValidationError`'s `suggestion` when set, and is
omitted otherwise — match the CLI/MCP shape's `suggestion` semantics, just
under a different key.

```json
{
  "detail": {
    "code": "KB_NOT_FOUND",
    "message": "human-readable message",
    "retryable": false,
    "hint": "optional fix hint"
  }
}
```

Source of truth: `pyrite/exceptions.py` (the codes), `pyrite/utils/errors.py`
(`build_error`, `cli_error`, `cli_error_from`), `pyrite/server/errors.py`
(REST's central handler), `pyrite/server/mcp_server.py` (`_refusal`).

## Write refusals (create, import, update)

Every entry write — REST `POST /api/entries`, `PUT`/`PATCH
/api/entries/{id}`, `POST /api/entries/import`, `POST /api/clip`; MCP `kb_create`,
`kb_update`, `kb_bulk_create`; CLI `pyrite create`, `pyrite update`,
`pyrite add`, `pyrite import` — goes through one pipeline in `KBService`,
so the same entry is refused with the same `error_code` on every surface.
This is the one table of codes an agent meets while writing; the
how-to (`docs/agent-write-path.md`) links here instead of repeating it. Each row
is reproduced by a command in "Reproduce the codes" below, which CI runs.

| `error_code` | Meaning | What to do |
|---|---|---|
| `KB_NOT_FOUND` | the KB name is not registered (or, for a scoped caller, not readable). Every CLI write command (`create`, `update`, `delete`, `link`) reports it. | `pyrite kb list` shows the registered names; `pyrite orient` lists the KBs you can read. |
| `NOT_FOUND` | the entry id does not exist: CLI `get`, `update`, `delete`, `link`. `pyrite backlinks` and `pyrite-read backlinks` on an id that no entry has and nothing links to answer `ENTRY_NOT_FOUND` (the class's code, today; #610 settles the spelling). An id that is only linked to (`[[ghost-page]]`) is answered with its linking entries. The intended MCP and REST code is `ENTRY_NOT_FOUND` (see "MCP only, for one release" above); today MCP `kb_get` answers `NOT_FOUND` and `kb_update` answers `UPDATE_FAILED` with `retryable: true` (#761: a bug, do not rely on it). | `pyrite search <term> -k <kb>` to find the id. |
| `UNDECLARED_TYPE` | the KB's `kb.yaml` declares types and this is not one of them. Core types (`note`, `person`, …) are **not** exempt. Override with `allow_undeclared` (MCP, REST body or import query) / `--allow-undeclared` (CLI). The error carries `declared_types` on MCP and REST. | Use a declared type (`pyrite kb schema show <kb>`), or pass the override. |
| `ENTRY_EXISTS` | the id (given, or derived from the title) already exists. Create never replaces; use update. REST answers `409`. | `update` it, or choose another title or id. |
| `SCHEMA_VIOLATION` | the KB schema or a plugin validator rejected a field: enum, required, range, format. A kb.yaml enum (`options:`/`values:`, list `items:`, rule `enum:`) is refused when `validation.enforce_enums` is on (the default); every other kb.yaml finding when `validation.enforce` is on. The message names the field, the value and the allowed list (`kind: 'chore' is not one of [...]`). An update that leaves an off-list value as it was is not refused (see **Warnings**). | Re-read the message: it names the field and the allowed values. |
| `VALIDATION_FAILED` | anything else the entry model refuses (an event without a date, a missing title), a `managed_fields` key in an update, and the ADR-0034 truncated-body refusal below. Unchanged by ADR-0037 theme 2 (2026-09-25): REST, MCP and the CLI already agreed on this code, so no `legacy_error_code`. | Fix the named field. For a truncated body, reassemble it with `kb_read_body` and retry without the marker. |
| `QUERY_SYNTAX` | (`search`, not a write, but what an agent hits right after a failed write) a token containing `-`, `:` or `.` used beside `AND`/`OR`/`NOT` or a quote, unquoted. | Quote the token yourself: `"bar-baz"`. |

All are `retryable: false`. REST reports them as
`{"detail": {"code", "message", "retryable": false, "hint"?, "declared_types"?}}`
with status `400` (`409` for `ENTRY_EXISTS`); MCP and the CLI use the error
shape above.

**Per-item results.** `kb_bulk_create`, `POST /api/entries/import` and
`pyrite import` refuse a bad item on its own and create its siblings;
results keep the input order. Each failed item carries `error_code`:

```json
{"created": false, "error": "Entry with ID 'x' already exists in KB 'k'. ...", "error_code": "ENTRY_EXISTS"}
```

REST import reports the same pair per item in `error_details`
(`{"title", "error", "error_code"}`). `pyrite import` prints
`Failed [CODE]: <title>: <message>` per refused record and exits `3` when some
records were written and some refused, `1` when none was written
(re-importing a file whose entries all exist writes nothing, so it exits `1`);
`--dry-run` prints `Would refuse [CODE]: …`, including for a record whose id
an earlier record of the same file would create, writes nothing, and exits the
way the real run would.

**Warnings.** A write that succeeds may still draw non-blocking schema
findings (an off-list select value when the KB sets
`validation.enforce_enums: false`, or an `allow_other` field). MCP
`kb_create`/`kb_update` return them as `warnings` (omitted when empty),
each `kb_bulk_create` result as `warnings`, REST `POST`/`PUT`/`PATCH
/api/entries` as `warnings: []` in the response body, and CLI `pyrite
update` and `pyrite task create --format json` as `warnings` in their JSON
(omitted when empty), and `pyrite create` / `pyrite import` as `Warning:`
lines. An update whose
entry already holds an off-list enum value it does not change -- compared
by value, so an echoed read counts; per element for a list-valued field;
never for a type error -- succeeds and
reports that value here (`severity: "warning"`, `note: "value already on
disk; ..."`) rather than being refused (#47, #555). This covers a plugin
validator's `rule: "enum"` too. Changing the field to another off-list
value, or adding an off-list list element, is still refused; create is
always strict.

**`index health` → `off_list_values`.** One row per off-list value of a
declared enum: `{kb, id, type, field, value, allowed, origin, severity}`.
`origin` is `field` or `rule` (kb.yaml) or `plugin` (a plugin validator's
`rule: "enum"` on any field but `status`, which stays in
`invalid_statuses`). `severity` is `error` for a kb.yaml enum in a KB with
`enforce_enums` on (status `unhealthy`, exit `1`), `warning` with it off
and for every plugin row (status `warning`), and `info` for an
`allow_other` field (does not change the status). Empty for a KB without
drift.

**Update fields.** An update applies the fields it names: the entry type's
own fields (its model's fields and every field its `kb.yaml` names for the
type) and, on every surface, keys the type does not declare, which are
stored as custom fields. Keys the request does not name keep the file's
value as written, including values Pyrite reads differently (#557). Fields Pyrite maintains are never set by
an update: `id`, `file_path`, `kb_name`, `links`, `sources`, `provenance`,
plus a type's own `managed_fields` (a task's `status_change_log`,
`evidence`, `agent_context`, `assigned_at`; an ADR's `adr_number`). REST
`PUT`/`PATCH` and `pyrite update --field` refuse them with
`VALIDATION_FAILED`; MCP `kb_update` sets them aside, along with
`created_at`/`updated_at`, index columns and `null`s for undeclared keys,
and lists them in the result's `ignored` field, so a read result echoed
back cannot rewrite them. In a request that echoes a read result (it
carries `id`, `file_path` or an index column), a field whose value is still
what the read returned is not a change: it is not written, and is listed
in the result's `unchanged` field (apart from `ignored`, which names keys no
update writes), so the file keeps `importance: high` rather than Pyrite's
reading `5` (#561). A request that names fields on its own writes each one
as sent, even a value equal to the reading.
A body marked `body_truncated` is refused at any depth of the request on
every update surface.

**Web client.** The web app sends `allow_undeclared: true` on create,
`POST /api/clip` and import, so its forms, which offer every core and
plugin type, keep working in a KB that declares types.

Source of truth: `pyrite/services/kb_service.py` (`_prepare`,
`bulk_create_entries`, `update`, `updatable_fields`) and the
`ValidationError` subclasses in `pyrite/exceptions.py`.

### Reproduce the codes

Each block below is run by `tests/test_doc_agent_contracts.py` in a scratch
HOME, in order; the exit code and the text it expects are the ones stated.
A refusal from a command without `--format` is Rich text; from `update`, JSON.

<!-- expect-exit: 0 -->
```bash
pyrite init -t empty -p ./notes --name notes --no-examples
mkdir typed && printf 'name: typed\ntypes:\n  ticket:\n    description: A ticket\n    fields:\n      kind:\n        type: select\n        options: [bug, feature]\n' > typed/kb.yaml
pyrite kb add ./typed --name typed
pyrite create -k notes -t note --title "Hello" --body "First."
```

`KB_NOT_FOUND`, from a command with `--format` and from one without:

<!-- expect-exit: 1 -->
<!-- expect-text: "error_code": "KB_NOT_FOUND" -->
```bash
pyrite update hello -k nokb --title x
```

<!-- expect-exit: 1 -->
<!-- expect-text: ERROR [KB_NOT_FOUND] -->
```bash
pyrite create -k nokb -t note --title x
```

`NOT_FOUND`:

<!-- expect-exit: 1 -->
<!-- expect-text: "error_code": "NOT_FOUND" -->
```bash
pyrite update nope -k notes --title x
```

<!-- expect-exit: 1 -->
<!-- expect-text: ERROR [NOT_FOUND] -->
```bash
pyrite link hello nope -k notes
```

`ENTRY_EXISTS`, `UNDECLARED_TYPE`, `SCHEMA_VIOLATION`, `VALIDATION_FAILED`:

<!-- expect-exit: 1 -->
<!-- expect-text: ERROR [ENTRY_EXISTS] -->
```bash
pyrite create -k notes -t note --title "Hello" --body "Again."
```

<!-- expect-exit: 1 -->
<!-- expect-text: ERROR [UNDECLARED_TYPE] -->
```bash
pyrite create -k typed -t note --title "Nope"
```

<!-- expect-exit: 1 -->
<!-- expect-text: ERROR [SCHEMA_VIOLATION] -->
```bash
pyrite create -k typed -t ticket --title "T1" -f kind=chore
```

<!-- expect-exit: 1 -->
<!-- expect-text: ERROR [VALIDATION_FAILED] -->
```bash
pyrite create -k notes -t event --title "An event"
```

`QUERY_SYNTAX`, from `search`:

<!-- expect-exit: 1 -->
<!-- expect-text: "error_code": "QUERY_SYNTAX" -->
```bash
pyrite search 'foo AND bar-baz' -k notes
```

## Write success shapes

What a write returns when it is not refused, per surface. The CLI rows are
run in "Reproduce the codes" and in `docs/agent-write-path.md`; the MCP
blocks marked as output are checked by key against the tool handler's real
result (`tests/test_doc_agent_contracts.py`). REST rows are reference only:
they are **not run** (a server would be needed).

### `create`

| Surface | Call | Success shape |
|---|---|---|
| CLI | `pyrite create -k <kb> -t <type> --title <t> --body <b>` | **No `--format`.** Rich text: `Created: <id>` / `Type: <type>`, plus a `Warning: ...` line per schema warning. Never JSON. |
| MCP | `kb_create` | the block below, plus `"warnings": [...]` when any, plus `"qa_issues"` when a QA validator ran and found something. |
| REST (not run) | `POST /api/entries` | `{"created": true, "id": "<id>", "kb_name": "<kb>", "file_path": "", "warnings": []}`. `file_path` is **always the empty string** here (MCP returns the real path). |

<!-- mcp-output: kb_create -->
```json
{"created": true, "entry_id": "hello", "file_path": "/abs/path/hello.md"}
```

`pyrite add <file>` is the same shape as `create`: Rich text only,
`Added: <id>` / `Type: <type>`, no `--format`.

### `update`

| Surface | Call | Success shape |
|---|---|---|
| CLI | `pyrite update <id> -k <kb> --title <t>` | **`--format` defaults to `json`:** `{"updated": true, "entry_id": "<id>"}`. With `--format rich`: `Updated: <id>`. |
| MCP | `kb_update` | the block below, plus `"warnings"` when any, `"ignored"` listing keys no update writes, and `"unchanged"` listing fields an echoed read result carried at the value the read returned (not written). |
| REST (not run) | `PUT /api/entries/{id}` (body carries `kb`) or `PATCH /api/entries/{id}` (body: `kb`, `field`, `value`; one field, `value` a string) | Both return `{"updated": true, "id": "<id>", "warnings": []}`. |

<!-- mcp-output: kb_update -->
```json
{"updated": true, "entry_id": "hello", "file_path": "/abs/path/hello.md"}
```

`update -f key=value` (CLI) and MCP `kb_update` with an extra key store an
undeclared field under the entry's `metadata` dict, not as a top-level key.
Which fields an update may set, and which it refuses or sets aside, is the
**Update fields** paragraph above.

### `bulk_create` / import

| Surface | Call | Success shape |
|---|---|---|
| CLI | `pyrite import <file> -k <kb>` | Rich text: one `Created: <id>` / `Failed [CODE]: <title>: <message>` line per record, then `Imported N entries` (or `Imported N entries (M failed)`). Exit `3` if some records were written and some failed, `1` if none was written. Its `--format` selects the *input* file format. |
| MCP | `kb_bulk_create` | the block below, results in input order. |
| REST (not run) | `POST /api/entries/import` (multipart) | `{"imported": N, "errors": M, "entries": [{"id", "title"}, ...], "error_details": [{"title", "error", "error_code"}, ...]}`. |

<!-- mcp-output: kb_bulk_create -->
```json
{"total": 2, "created": 1, "failed": 1, "results": [{"created": false, "error": "...", "error_code": "ENTRY_EXISTS"}, {"created": true, "entry_id": "two"}]}
```

### `task create` and `link`

`pyrite task create` takes `--field key=value` (repeatable) for fields a
KB's task schema allows. Its `-f` is `--format` (`rich` default, or `json`),
**not** `--field` as on `create`/`update`: check `--help` per command.

`pyrite link <source> <target> -k <kb> -r <relation>` prints
`Linked: <src> --[<relation>]--> <tgt> (in <kb>)` for a new link, or
`Already linked: ...` when that `(target, kb, relation)` already exists.
Another relation between the same two entries is a new link.

## Repo endpoint errors

`/api/repos/*` is not `PyriteError`-based (it is out of scope for ADR-0037
theme 2's endpoint conversion) and answers a failure with its own
hand-built `detail` object, structurally the same wrapper as REST's error
shape above but without `retryable` or `hint`:

```json
{"detail": {"code": "REPO_NOT_FOUND", "message": "human-readable"}}
```

`code` is drawn from a closed set — an unrecognised service code is
replaced by the endpoint's default rather than echoed to the caller:

| Code | Meaning |
|---|---|
| `REPO_NOT_FOUND` | the remote repository is absent, or the configured credentials cannot see it |
| `AUTH_REQUIRED` | connect or refresh the GitHub credentials |
| `BRANCH_NOT_FOUND` | the branch does not exist on the remote |
| `PATH_EXISTS` | that `owner/repo` is already present in the workspace |
| `CLONE_TIMEOUT` | the clone exceeded its deadline |
| `CLONE_FAILED` | an unrecognised clone failure; git's stderr is in the operator's log |
| `INVALID_REQUEST` | the URL or branch was rejected before git ran |
| `SUBSCRIBE_FAILED`, `FORK_FAILED`, `SYNC_FAILED`, `UNSUBSCRIBE_FAILED`, `PR_FAILED` | per-endpoint defaults when nothing more specific applies |
| `GITHUB_NOT_CONNECTED` | `/repos/fork` with no GitHub account connected |
| `KB_NAME_CONFLICT` | a KB in the repository has the name of an already-registered KB, or two KBs in it share a name; nothing was subscribed and the clone was removed |
| `INVALID_KB_NAME` | a KB in the repository has a name that is not a plain KB name (1-64 letters, digits, `-` or `_`, starting with a letter or digit) |
| `REPO_NAME_CONFLICT` | a repository with that name is already registered |

`message` never contains an absolute filesystem path or a token: git's
stderr is redacted on the way out and logged unredacted at `WARNING`
server-side (CodeQL `py/stack-trace-exposure` #51, #52, #53). Remote URLs
the caller supplied are preserved, since they are the actionable part.
`POST /repos/{name}/sync` reports per-repo failures nested under `repos`
in a 200 body; those `error` strings are redacted the same way.

Match on `code`, not on `message` text.

Source of truth: `pyrite/server/endpoints/repos.py` (`_PUBLIC_ERROR_CODES`,
`_error_detail`) and `pyrite/services/git_service.py` (`sanitize_error`,
`classify_git_error`).

## Repo endpoint success bodies

`RepoInfo.local_path` (`GET /repos`, `GET /repos/{name}`) and the `path` key
in a `subscribe`/`fork` success body are **relative to the workspace root**,
not an absolute server filesystem path — `owner/repo_name`, matching
`workspace_path = self.config.settings.workspace_path / owner / repo_name`
in `RepoService`. Issue #195, the success-path twin of #161: the field stays
populated (it is public response shape an external consumer may already
depend on) but discloses nothing about the server's directory layout or
usernames. A path that cannot be expressed relative to the workspace root
(legacy data from a moved workspace) comes back as the literal string
`"<path>"` rather than the absolute value.

This applies to the HTTP response only. The `pyrite repo list` /
`pyrite repo status` CLI output, and every internal caller reading
`local_path` off the DB row or a service dict directly, still show the real
absolute path — the CLI operator is not a remote caller.

A `subscribe`/`fork` success body also carries `kb_default_role`, the access
policy of the KBs it registered. It is `null`: those KBs have no
`default_role`, so each user reaches them at their global role (a subscribed
KB is also read-only). An admin can set a KB's `default_role` afterwards.

Source of truth: `pyrite/server/endpoints/repos.py` (`_relativize_path`,
`_repo_dict_to_info`).

## Search result envelope

```json
{
  "query": "the search string",
  "count": 3,
  "results": [ { "...": "entry dict" } ]
}
```

`count` is `len(results)`, not a total-matches count for search results —
none of `search`'s three transports return a separate total. `has_more`
and a separate `total` are not uniform across the paginated surfaces or
across transports; measured per surface (CLI `--format json`, MCP tool,
REST `GET`):

| surface | CLI | MCP | REST |
|---|---|---|---|
| `search` | neither | `has_more` (no `total`) | neither |
| `list_entries` | `has_more` + `total` | `has_more` + `total` | `total`, no `has_more` |
| `recent` | neither | neither | no REST route |
| `tags` | neither | `has_more` (no `total`) | neither |
| `backlinks` | `total`, no `has_more` | `has_more` (no `total`) | no REST route |

Where present, `has_more` means the page was full (`len(page) == limit`
for CLI/MCP `tags`/`recent`, or `offset + limit < total` where a total is
computed) — a signal to fetch the next page, not an exact remaining
count. Don't assume either key exists; check for it.

The result array's key also differs by transport for the same logical
call: CLI `backlinks` → `entries`, MCP `kb_backlinks` → `backlinks`; CLI
`tags` → `count` + `tags`, MCP `kb_tags` → `tag_count` + `tags`.

Source of truth: `pyrite/server/mcp_server.py` (`_kb_search`,
`_kb_list_entries`, `_kb_recent`, `_kb_backlinks`, `_kb_tags`);
`pyrite/server/endpoints/search.py` (`search`), `pyrite/server/endpoints/entries.py`
(`list_entries`) and `pyrite/server/endpoints/tags.py` (`get_tags`) for REST;
`pyrite/cli/browse_commands.py` for the CLI commands' own JSON assembly.

## Entry envelope

A single entry (`kb_get`, `pyrite get`) returns the entry dict directly
— not wrapped in an `{"entry": ...}` key. `outlinks` and `backlinks` are
included when resolvable.

### Backlink rows

Each row in `backlinks` (and in `pyrite backlinks`, MCP `kb_backlinks`) reads
from the entry's side, with three relation fields:

| Field | Reads as | No inverse declared (`informs`) | Declared after the row was indexed |
|---|---|---|---|
| `forward_relation` | what the source's file says, source → this entry, as written | the relation itself (`informs`) | the relation itself |
| `relation` | this entry's relation to the source (the inverse stored in the index when the link was indexed) | `related_to` | stays `related_to` until the source is reindexed |
| `inverse_relation` | the declared inverse of `forward_relation`, looked up when the query runs | `null` | the declared inverse (`informed_by`) |

`inverse_relation: null` means no inverse is declared for that relation. That
is true of a custom relation, and also of some relations Pyrite writes itself:
`transclusion` and `references` (written by the indexer for `![[...]]` and
object-reference fields) and the legacy default `related` are not declared
relationship types today, so their rows read `relation: related_to`,
`inverse_relation: null`.

A stale row is the last column: a plugin declares `informs` after the link was
indexed. `relation` is a stored column and keeps `related_to`;
`inverse_relation` is computed per query and already gives the declared
inverse. When the two disagree, `inverse_relation` is the current one.

The table output (`pyrite-read backlinks`, `pyrite backlinks --format rich`)
shows `relation` as "Relation" and `forward_relation` as "Written as".

`outlinks` rows carry one field, `relation`, which is the forward relation.

## Body truncation

Entries with large bodies may come back chunked. When truncated, the
entry dict gains:

```json
{
  "body": "...(chunk, not the full body)...",
  "body_truncated": true,
  "body_length": 15234,
  "body_offset": 0,
  "body_chunk_size": 4000
}
```

`body_truncated` is only present when truncation actually happened —
don't assume its absence means anything other than "not truncated".
Use `kb_read_body` (offset-based continuation) to read past the first
chunk; stop once `body_offset + body_chunk_size >= body_length`.

The four keys survive a `fields` projection that kept `body`: a bounded
body always arrives with the means to tell it was bounded (ADR-0034 rule
2). A projection that excluded `body` carries none of them.

### `body_chunk_size: 0` in a multi-entry read

`kb_batch_read`, and the other tools that return several bodies, spend a
per-response budget (`PYRITE_BODY_RESPONSE_BUDGET`, default 40,000
characters) in request order. An entry reached after the budget is spent
comes back **in place, with an empty body and the full marker**:

```json
{
  "id": "some-entry",
  "body": "",
  "body_truncated": true,
  "body_length": 50000,
  "body_offset": 0,
  "body_chunk_size": 0
}
```

It is not dropped from `entries` and never appears in `not_found` —
`body_length` is its true length, so a caller can see there was content
and fetch it with `kb_read_body`. A loop that advances by
`body_chunk_size` must treat `0` as "this call returned nothing, ask
again for this entry alone" rather than incrementing by zero forever.

### Writing a body back

**A truncated body is never valid input to a write** (ADR-0034 rule 2).
Every write path that can receive a body — MCP (`kb_create`, `kb_update`,
`kb_bulk_create`, `task_create`, …), REST (`POST`/`PUT`/`PATCH
/api/entries`, `POST /api/entries/import`) and the CLI (`pyrite create`,
`pyrite update`, `pyrite import`) — refuses a request carrying a truthy
`body_truncated` alongside a `body`, with `VALIDATION_FAILED` and
`retryable: false`. Writing back what a bounded read returned would
replace the whole stored body with the chunk you were given.

Assemble the full body first (`kb_read_body` paged by `body_offset`, or a
`body_limit` above `body_length`) and write that, without the marker. To
change other fields without touching the body, omit `body` — a request
carrying the marker but no body is allowed. `body_truncated: false` is
allowed, and is never persisted as entry content.

## Exit codes (CLI)

- `0` — success: **every effect you asked for happened.** A command that
  prints a failure and exits `0` is a bug.
- `1` — refused, or nothing was done: any error surfaced via `cli_error`
  (parses the JSON error shape above from stdout/stderr depending on
  `--format`). A subject that does not exist is a not-found error here, in the
  command's own format, never an empty result (`pyrite backlinks nope` answers
  `ENTRY_NOT_FOUND` today, the class's code; the spelling is #610's). Also a
  batch command that did none of its items (below).
- `2` — usage, click's own.
- `3` — the command ran and did only part of what was asked, or left items
  for a retry. It prints what happened and what did not, so a script reads the
  output instead of repeating the call. For a command that loops over items
  the rule is one function, `exit_unless_whole(failed, done)`: nothing failed
  is `0`, some failed and some were done is `3`, none was done is `1`. A dry
  run that would refuse exits as the real run would.
  - `pyrite create --link a --link b` with a link that does not resolve: the
    entry is kept (a retry answers `ENTRY_EXISTS`), the links that resolved
    are kept, and the output names the `pyrite link <id> <target> -r <relation>`
    that finishes each failed one (always `3`: the entry was written);
  - `pyrite link --bidi` whose forward link was written and whose inverse failed;
  - the batch commands, by the rule above (done = items written, embedded or
    already fine; `export collection` counts exported entries, `search --files`
    counts files scanned, `repo sync` repos synced): `index embed` and
    `pyrite-admin index embed` (`Errors: N`; the entries stay owed and a rerun
    retries them, so offline with nothing embedded is `1`), `schema migrate`,
    `index reconcile`, `import`, `links bulk-create`, `export collection`,
    `search --files`, `repo sync`, `batch-read` (an id not found), `task decompose`,
    `sw prioritize`, `sw migrate-standards`, `investigation bulk-edges` and
    `ftm-import`;
  - `pyrite init` that created the KB but could not index it, and
    `pyrite extension install --verify` that installed a plugin that would not
    load (always `3`: the install happened);
  - `pyrite ids missing` (a file has no `id:`, an empty one, or cannot be
    read) and `pyrite ids pin` (a file or collision group was left, and the
    output says which and why). See `docs/pinning-entry-ids.md`.

  Single-effect commands that fail answer `1`: `kb commit` when the commit was
  rejected (a hook, a staging error; "No changes to commit" is `0`), `task claim`
  when the claim is lost (in `--format json` too), `social reputation` when it
  cannot compute the reputation, `investigation network` and `evidence-chain`
  when the answer is an error.

  This amends the 2026-10-02 decision on #303 ("exit codes stay 0/1/2"),
  which `ids` had already outgrown; the maintainer's decision of 2026-10-08
  is recorded on #526. `tests/test_requested_effect_exit_code.py` classifies
  every command that can do part of its job.

## `--format` defaults

Most *read* commands default to `--format json`. A few interactive/status
commands (`task` subcommands, `config`) default to a rich terminal
view instead; pass `--format json` explicitly when scripting against
those. The write commands are not uniform, and the table is checked
against each command's Typer signature by `tests/test_doc_agent_contracts.py`:

| Command | `--format` | Default |
|---|---|---|
| `create` | none | Rich text, always |
| `add` | none | Rich text, always |
| `delete` | none | Rich text, always |
| `link` | none | Rich text, always |
| `update` | yes | `json` |
| `rename` | yes | `json` |
| `import` | input file format, not output | Rich text, always |
| `task create` | yes (`-f`) | `rich` |

**#303** (open, "CLI output format is inconsistent when stdout is not a TTY")
is the decision that would make this one rule. Until it lands, a command with
none prints its refusals as Rich text too, so branch on the exit code, not on
JSON.

## Operational contracts

Not JSON shapes, but part of the same "don't relearn this the hard way"
surface. This is the verbatim `operational_contracts` that `pyrite orient`
and `kb_orient` return (`KBService.orient`); a test fails when the two
differ, so edit `KBService.orient` and this block together. `error_contract`
is the error shape above in one line.

<!-- orient-operational-contracts -->
```json
{
  "indexing": "Entries are only searchable once indexed. Direct file writes under a KB's path (not via `pyrite create`/`update`) need `pyrite index sync` afterward -- it's incremental and cheap, safe to run after every batch of writes.",
  "error_contract": {
    "shape": "{error, error_code, suggestion?, retryable}",
    "error": "human-readable message",
    "error_code": "machine-readable code, e.g. QUERY_SYNTAX, KB_NOT_FOUND",
    "suggestion": "optional fix hint, omitted when not applicable",
    "retryable": "bool -- whether retrying the same request could succeed"
  },
  "search_quoting": "Special-char tokens (hyphens, dots, colons) are auto-quoted ONLY when the query has no AND/OR/NOT operator and no existing quote. Once you use an operator or a phrase quote, quote special-char tokens yourself (e.g. '\"family separation\" \"cross-link\"') or the query can fail with error_code QUERY_SYNTAX (deterministic, not retryable).",
  "task_claims": "Task claims are atomic; a lost race means the task is already claimed by someone else. On conflict, do NOT override the claim -- re-run the task list and pick a different item."
}
```

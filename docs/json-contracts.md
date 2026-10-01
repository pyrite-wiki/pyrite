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
below. Some write commands (`create`, `add`, `delete`, `link`) have no
`--format` flag and always print Rich-formatted text, never this JSON shape,
on error or success — see `docs/agent-write-path.md` for the per-command
table. Making CLI output consistent regardless of TTY is **#303** (open);
this page describes the shape you get when you do get JSON, not a promise
that every command gives you the option.

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
so the same entry is refused with the same `error_code` on every surface:

| `error_code` | Meaning |
|---|---|
| `UNDECLARED_TYPE` | the KB's `kb.yaml` declares types and this is not one of them. Core types (`note`, `person`, …) are **not** exempt. Override with `allow_undeclared` (MCP, REST body or import query) / `--allow-undeclared` (CLI). The error carries `declared_types` on MCP and REST. |
| `ENTRY_EXISTS` | the id (given, or derived from the title) already exists. Create never replaces; use update. REST answers `409`. |
| `SCHEMA_VIOLATION` | the KB schema or a plugin validator rejected a field: enum, required, range, format. A kb.yaml enum (`options:`/`values:`, list `items:`, rule `enum:`) is refused when `validation.enforce_enums` is on (the default); every other kb.yaml finding when `validation.enforce` is on. The message names the field, the value and the allowed list (`kind: 'chore' is not one of [...]`). An update that leaves an off-list value as it was is not refused (see **Warnings**). |
| `VALIDATION_FAILED` | anything else the entry model refuses (an event without a date, a missing title), and the ADR-0034 truncated-body refusal below. Unchanged by ADR-0037 theme 2 (2026-09-25): REST, MCP and the CLI already agreed on this code before that theme, and continue to — no `legacy_error_code`. |

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
`Failed [CODE]: <title>: <message>` per refused record and exits `1` if any
record was refused (re-importing a file whose entries exist is refused per
record, so it exits `1`); `--dry-run` prints `Would refuse [CODE]: …`,
including for a record whose id an earlier record of the same file would
create, and writes nothing.

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

**Success shapes.** What `create`/`update`/`bulk_create` return when they
*don't* refuse — per surface (CLI, MCP, REST), plus how to discover a
type's required fields (including the plugin-type gap, #232) and an
"error X, do Y" table — is `docs/agent-write-path.md`.

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

- `0` — success.
- `1` — any error surfaced via `cli_error` (parses the JSON error shape
  above from stdout/stderr depending on `--format`).

## `--format` defaults

Most *read* commands default to `--format json`. A few interactive/status
commands (`task` subcommands, `config`) default to a rich terminal
view instead — pass `--format json` explicitly when scripting against
those.

**Write commands are not uniform, and some have no `--format` at all:**
`create`, `add`, `delete` and `link` always print Rich text, with no flag
to ask for JSON; `update` and `rename` do have `--format`, defaulting to
`json`. See `docs/agent-write-path.md` for the full per-command table and
success shapes, and cite **#303** (open) for the consistency this page
used to claim already existed.

## Related contracts

These aren't JSON shapes but are part of the same "don't relearn this
the hard way" surface — also returned in `pyrite orient`'s
`operational_contracts` field:

- **Indexing**: entries are only searchable once indexed. Writes made
  directly to files under a KB's path (bypassing `pyrite create`/
  `update`) need `pyrite index sync` afterward — incremental and cheap.
- **Search auto-quote rule**: special-char tokens (hyphens, dots,
  colons) are auto-quoted only when the query has no `AND`/`OR`/`NOT`
  operator and no existing quote. Once you use an operator or a phrase
  quote, quote special-char tokens yourself or the query can fail with
  `error_code: QUERY_SYNTAX` (deterministic, not retryable).
- **Task claims**: atomic. A lost race means the task is already
  claimed by someone else — don't override; re-run the task list and
  pick a different item.

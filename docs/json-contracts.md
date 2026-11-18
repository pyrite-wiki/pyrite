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
defaults" says which. With `--format rich` the same refusal is the one
`ERROR [CODE]: message` line. Each CLI module takes the one shared `--format`
(JSON by default; #303, decided 2026-10-02) as it moves to the outcome
contract; until then this page describes the shape you get when you do get
JSON, not a promise that every command gives you the option.

```json
{
  "error": "human-readable message",
  "error_code": "MACHINE_CODE",
  "suggestion": "optional fix hint",
  "retryable": false,
  "outcome": "error"
}
```

- `outcome` — on the CLI, from a command on the outcome contract (see "Exit codes (CLI)"): always `"error"` on a refusal, as
  `"done"`, `"partial"` or `"nothing"` is on a result. Additive: absent from
  MCP and from a CLI command that has not migrated, so do not require it.

- `error` — always present, human-readable.
- `error_code` — always present, machine-readable (`QUERY_SYNTAX`,
  `KB_NOT_FOUND`, `INVALID_TIER`, etc.). Match on this, not on `error`
  text.
- `suggestion` — omitted entirely when there's no applicable fix hint.
  Don't assume the key exists.
- `retryable` — always present. `true` means the same request could
  succeed on retry (e.g. a transient lock); `false` means the request
  itself needs to change first (e.g. a malformed query).

**`AMBIGUOUS`: an id in more than one KB (#528).** An entry or task id given
without a KB (or with a blank one) that the index holds in more than one KB
the caller can read is refused, never answered by config order. Every surface
that resolves an id this way answers the same code and lists the KBs in
`candidate_kbs`, beside the contract's keys: CLI `get`, `task get`, `task
status`, `pyrite-read get`, and `update`/`links suggest`/`links discover`
given `-k ""`; MCP `kb_get`, `kb_read_body`, `task_status` and the four
task-graph tools (in the tool result); REST `GET /api/entries/{id}/blocks`,
`GET /api/collections/{id}` and `/entries`, and the AI routes, given a blank
`kb`, answer `409` with `candidate_kbs` in `detail`. `candidate_kbs` names
only KBs the caller can read: a private KB holding the same id is neither
listed nor a cause of the refusal. One readable match still answers; zero is
still a not-found refusal; MCP spellings are in the
[MCP code transitions](#mcp-code-transitions) table. Only indexed copies are counted:
an id indexed in one KB with a not-yet-indexed `<id>.md` in another answers
from the indexed KB (the
by-name read runs only when no readable KB's index holds the id, #753). To
comply, name one of the KBs: `-k`, `kb`, `kb_name`, `kb_a`/`kb_b`
(`find_connections`), or `pyrite://kbs/<kb>/entries/<id>`.

**MCP prompts and resources** (`prompts/get`, `resources/read`) have no result
shape for a refusal, so theirs is a JSON-RPC error: `message` is the
envelope's `error`, `code` is `-32602` (invalid params) for a refusal and
`-32603` (internal error) for `INTERNAL`, a fault on the server's side, and
`data` is the whole envelope above (`error_code`, `suggestion`, `retryable`,
and `candidate_kbs` for `AMBIGUOUS`). Match on `data.error_code`. This holds for
every refusal from a prompt or resource, `KB_NOT_FOUND` included; a KB in
`pyrite://kbs/<kb>/entries/<id>` that does not exist answers `KB_NOT_FOUND`,
as one the caller cannot read does.

REST `GET /api/entries/{id}` without `kb` is refused the same way (`409`,
`candidate_kbs` in `detail`); the web client's entry links carry `?kb=` (#910).

**Not yet:** extension commands and tools that look ids up through
their own queries (`sw`, `wiki`, `social vote`, `investigation_*`) and bare
`[[id]]` resolution (`/api/entries/resolve`) are not covered either (#911).
The admin-tier MCP tools `kb_registry_health`, `kb_registry_reindex` and
`kb_registry_remove` also bypass the resolver: an unknown KB answers
`NOT_FOUND`, not `KB_NOT_FOUND` (#610).

### MCP code transitions

MCP reports the class-level `error_code`. Where its old code differed,
`legacy_error_code` carries that old spelling; an unchanged code has no
transition field. The CLI has no transition field. This table is the single
reference for the tool-specific transitions still accepted in 0.26.0.
The maintainer set removal to 0.27; these compatibility spellings remain
accepted in 0.26 (see [0.26.0](../CHANGELOG.md#0260---2026-10-22)).
The evidence column records the introduction where known, rather than
inventing a release date for older class-level transitions.

| Tool or refusal | Condition | `error_code` | `legacy_error_code` | `retryable` | Added / evidence | Leaves in |
|---|---|---|---|---|---|---|
| `kb_get` | Missing entry | `ENTRY_NOT_FOUND` | `NOT_FOUND` | false | 0.25.8 (#761) | 0.27 |
| `kb_get` | Unknown KB | `KB_NOT_FOUND` | `NOT_FOUND` | false | 0.25.8 (#761) | 0.27 |
| `kb_update` | Missing entry | `ENTRY_NOT_FOUND` | `UPDATE_FAILED` | false | 0.25.8 (#761) | 0.27 |
| `kb_update` | Unknown KB, unscoped caller | `KB_NOT_FOUND` | `UPDATE_FAILED` | false | 0.25.8 (#761) | 0.27 |
| `kb_update` | Unknown KB, scoped caller | `KB_NOT_FOUND` | `NOT_FOUND` | false | KB resolver (#952) | 0.27 |
| `kb_update` | File changed during update | `CONFLICT` | `UPDATE_FAILED` | true | #990 | 0.27 |
| `kb_update` | Storage fault | `STORAGE_ERROR` | `UPDATE_FAILED` | only for a busy database | #990 | 0.27 |
| `kb_read_body` | Missing entry | `ENTRY_NOT_FOUND` | `NOT_FOUND` | false | #984 (present in 0.26.0) | 0.27 |
| `kb_read_body` | Unknown KB | `KB_NOT_FOUND` | `NOT_FOUND` | false | #984 (present in 0.26.0) | 0.27 |
| `kb_delete` | Missing entry | `ENTRY_NOT_FOUND` | `NOT_FOUND` | false | #984 (present in 0.26.0) | 0.27 |
| `kb_delete` | Unknown KB | `KB_NOT_FOUND` | `NOT_FOUND` | false | #984 (present in 0.26.0) | 0.27 |
| KB resolver (including `kb_orient`) | Unknown or unreadable KB | `KB_NOT_FOUND` | `NOT_FOUND` | false | 0.25.7 (#66), unified by #952 | 0.27 |
| Domain refusal | Read-only KB | `KB_READ_ONLY` | `READ_ONLY` | false | ADR-0037 theme 2 | 0.27 |
| Domain refusal | Invalid frontmatter | `INVALID_FRONTMATTER` | `VALIDATION_FAILED` | false | ADR-0037 theme 2 | 0.27 |
| Domain refusal | A file its declared type cannot load: every write, and a read the refusal cannot be isolated for | `ENTRY_UNREADABLE` | `VALIDATION_FAILED` | false | ADR-0042 8a (#1261) | 0.27 |
| Domain refusal | Conflicting / refused config save | `CONFIG_CONFLICT` / `CONFIG_SAVE_REFUSED` | `CONFIG_ERROR` | false | ADR-0037 theme 2 | 0.27 |
| Domain refusal | Storage / plugin fault | `STORAGE_ERROR` / `PLUGIN_ERROR` | `REQUEST_REFUSED` | only busy storage is retryable | ADR-0037 theme 2 | 0.27 |

The scoped update refusal happens before the old handler; its legacy code
therefore differs from the unscoped update row. A base `ValidationError`
remains `VALIDATION_FAILED` without a legacy field. Other unnamed update
and delete failures retain their existing `UPDATE_FAILED` / `DELETE_FAILED`
codes with `retryable: true`.

The KB resolver returns `suggestion` and `did_you_mean` beside the contract's
keys. For an unknown name its static suggestion names no KB. Near matches
are shown only to an unscoped caller; a scoped caller gets an empty list.
A readable KB's shortname is accepted only in link syntax, and a refusal
explains that syntax. Non-string KB names are `VALIDATION_FAILED`; null or
blank means no name. These details apply equally to CLI and REST resolution.

### Deprecated, still accepted

- `legacy_error_code`: match `error_code` instead. Leaves in 0.27.
- Search `distance`: read `similarity` instead. Leaves in 0.27.
- `-f` as `--format`: spell `--format` explicitly. Leaves in 0.27.
- `export --format`: use `export --target`. Leaves in 0.27.
- `task status`: use `task get`. Leaves in 0.27.
- `db.conn`: use `db.session` for writes or `db.execute_sql()` for reads. Leaves in 0.27.
- `FRONTMATTER_ALIASES`: declare `field_aliases` in the type's schema. Leaves in 0.27.
- orient's `schema.guidelines`, `schema.goals`, `schema.evaluation_rubric`, `schema.types.<type>.ai_instructions` (and the type's `guidelines`, `goals`, `evaluation_rubric`) and top-level `guidelines`: read `kb_authored` and `pyrite_guidance`. Leaves in 0.27.

The compatibility warnings also name 0.27, following the maintainer decision.

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
(REST's central handler), `pyrite/server/mcp_server.py` (`_refusal`). The CLI
root's own codes, `USAGE_ERROR` and `ERROR`, are in `pyrite/utils/errors.py`.

## Server and account codes

These are the central REST mappings (`pyrite/server/errors.py`); an endpoint
that handles a refusal itself can retain its documented route-specific status.
CLI and MCP report the code without an HTTP status.

| Code | Surface / central HTTP status | Remedy |
|---|---|---|
| `KB_DEFINED_IN_CONFIG` | REST KB metadata changes: 409 | Edit the KB in `config.yaml`, then restart; requests cannot replace file-owned metadata. |
| `KB_PROTECTED` | REST protected KB operation: 403 | Choose an unprotected KB or ask the operator to change its protection. |
| `LAST_ADMIN` | REST account role change: 409 | Keep at least one administrator; promote another account first. |
| `KB_PATH_NOT_ALLOWED` | REST `POST /api/kbs` and MCP `kb_registry_add` from a network caller: 403. The message names the configured `kb_roots`. | Register the KB in a folder under one of the named roots, or ask the operator to add a root (`settings.kb_roots`) or register it locally (`pyrite kb add`). |
| `FORBIDDEN` | REST authorization: 403; MCP refusal. A known tool above the connection's tier includes `required_tier`. | For local MCP, start `pyrite mcp --tier <required_tier>`. For HTTP, ask the operator for the required role or grant. |
| `UNAUTHENTICATED` | REST authentication: 401; MCP refusal | Supply valid credentials before retrying. |
| `QUERY_TOO_LONG` | Search, REST 422; CLI/MCP | Shorten the query; it is not silently truncated. |
| `INVALID_REF` | Git-ref validation, REST 422; CLI/MCP | Use a valid git reference. |
| `CLIPPER_BLOCKED_HOST` | Clipper, central REST mapping 500 | Choose an allowed URL; ask the operator about the host policy. |
| `CLIP_TOO_LARGE` | Clipper, central REST mapping 413 | The page is larger than the clipper's 5 MiB cap, counted after decompression; clip a smaller page. |
| `BRANDING_INVALID` | Branding configuration, REST 500 | Ask the operator to repair `branding.yaml`; repeating the request will not fix configuration. |
| `INTERNAL_ERROR` | Unclassified domain failure, REST 500 | Consult the server log; do not assume retrying is safe. |
| `KB_CONFIG_INVALID` | A KB whose `kb.yaml` Pyrite can read but cannot load as a mapping: bytes that are not UTF-8, a YAML syntax error, a duplicate key, a document over the YAML limits, or a top level that is neither a mapping nor null (a list, a string, a number, a boolean) (#1547). A null document (empty, `~`, `null`, only comments) is a KB with no schema, not this code. Every operation that needs that KB's schema, on every verb (get, create, update, delete, schema show/add/remove/set, sync, rebuild or reindex of that KB). CLI exit 1, MCP refusal, central REST mapping 500. Always `retryable: false`, always the same message; it names no KB, no path and nothing the parser said. The KB's index rows are kept, `kb.yaml` is never rewritten, and every other KB still works. `pyrite kb list` and `pyrite index health` on the local CLI show the parser's words; a network caller does not get them. | Correct `kb.yaml` by hand (`pyrite kb list` or `pyrite index health` says what is wrong), then `pyrite index sync`. |
| `KB_UNREADABLE` | A KB whose files Pyrite cannot read (its folder cannot be listed or searched, its `kb.yaml` cannot be opened): every operation that needs that KB's files, on every verb (get, create, update, delete, schema, sync of that KB). CLI exit 1, MCP refusal, central REST mapping 500. Always `retryable: false`, always the same message, whatever the operating system refused; it names no path. The KB's index rows are kept, and every other KB still works. | Fix the folder's or `kb.yaml`'s permissions, then `pyrite index sync`. |

## MCP operation codes

These codes are also emitted directly by MCP handlers. Domain exceptions
use the shared codes above and in the write-refusal table below. The legacy
spellings remain part of the contract until their callers are migrated.

| Code | Meaning | Remedy |
|---|---|---|
| `UNKNOWN_TOOL` | No tool with this name is known at any tier. | Use `list_tools` to check the name and installed plugins. |
| `RATE_LIMITED` | This client's request limit was reached. `retryable` is true. | Wait for the duration in the suggestion. |
| `MISSING_PARAM` | A legacy handler is missing a required argument. | Supply the argument named in the error. |
| `MISSING_PARAMETER` | A handler is missing a required argument. | Supply the argument named in the error. |
| `INVALID_PARAMETER` | An argument has an unsupported value. | Use a value allowed by the tool's input schema. |
| `VALIDATION_ERROR` | A legacy handler rejected a value, such as a git reference. | Correct the value named in the error. |
| `READ_ONLY` | A legacy write handler refused a read-only KB. | Choose a writable KB or ask the operator. |
| `PROTECTED` | Removing a protected KB was refused. | Choose an unprotected KB or ask the operator. |
| `CREATE_FAILED` | The entry could not be created. | Check whether the entry exists and inspect the server log before retrying. |
| `BULK_CREATE_FAILED` | A bulk-create request failed. | Check which entries were created before retrying. |
| `UPDATE_FAILED` | The requested update failed. | Read the entry and inspect the server log before retrying. |
| `DELETE_FAILED` | The requested deletion failed. | Check whether the entry still exists and inspect the server log. |
| `LINK_FAILED` | The requested link could not be added. | Check both entries and whether the link already exists. |
| `OPERATION_FAILED` | A schema or git operation failed without a more specific code. | Follow the error's advice and inspect the server log. |
| `INTERNAL` | An unexpected handler failure occurred. The message is always `Internal error; the server log has the details.` (`INTERNAL_PUBLIC_MESSAGE`, below). | Ask the operator to inspect the server log. |

**A fault's own text is never sent to a network caller.** When an exception
that is not one of Pyrite's reaches a REST response, an MCP result or a
stream event -- an operating-system error naming a path, a model provider's
error, git's error output -- the message is the fixed
`Internal error; the server log has the details.` (`pyrite.exceptions.INTERNAL_PUBLIC_MESSAGE`)
under the route's error code (`AI_ERROR`, `EXPORT_FAILED`, `CREATE_FAILED`,
`UPDATE_FAILED`, a QA issue's message, ...), and the text goes to the server
log. The local process (the CLI, `pyrite mcp` over stdio) sees the text
itself. A Pyrite refusal always shows its own message, validation that used
to be a built-in `TypeError`/`ValueError` included (`title must be a string,
got a list`, `cannot derive an id: title is a list`, a value the index cannot
store, a value the file cannot hold): those are `VALIDATION_FAILED`.

**Detail that is the caller's own is shown to that caller (0.26.3).** Two
kinds of outside text are the caller's, each marked where the text is made
(`pyrite.exceptions.caller_owned` / `kb_owned`), never at a distant catch:

- *Detail about the caller's own request*: a parser's message about bytes the
  caller sent -- the JSON, CSV, YAML or markdown of an import (REST
  `POST /api/entries/import` answers `PARSE_ERROR` with `The file could not be
  read as json: <the parser's words>. ...`), a field path, and a search
  query's syntax error (`QUERY_SYNTAX`: `Query could not be parsed: <the FTS
  parser's words>. ...`) when the parser's message is one of its fixed
  templates quoting nothing but the query as the caller wrote it (REST
  `/api/search`, MCP `kb_search`). A query Pyrite builds is not the caller's
  -- one expanded with a provider's terms, or one derived from an entry's
  title and tags (suggest-links) or a chat message's words: a message quoting
  it, or any other driver text, reads `the server log has the details`. The parser is given only the bytes, never a file name.
- *Bytes of a KB the caller holds a read grant on*: the YAML parser's quote of
  an entry file in that KB that does not parse (a QA issue, a load refusal),
  and of the file an update edits (`Cannot update ...: the frontmatter does
  not parse: ...`), before or after the caller's edit is applied. For a
  signed-in caller the grant is the routes' own rule, access through a KB's
  `default_role` included; an anonymous REST caller is never shown text
  quoted from a KB file, whatever its `default_role` allows (MCP over HTTP
  refuses an anonymous caller before any tool runs). With authentication
  disabled, every network caller is the operator, and this rule does not
  narrow what it is shown.
  A call with no grant bound (a job, an embedded MCP caller with no
  principal) is not shown it either.

Anything else outside stays the operator's: a fault that is not the parser's
(an import that fails for another reason still reads `the server log has the
details`), an operating-system error, a library's or a provider's message,
git's output, and the bytes of a KB the caller cannot read. An OS error is
named by the C library's text for its code (`Permission denied`), never by the
exception's own `strerror`. The search `warnings` name a sqlite-vec load
failure by its class and the remedy; its text reaches the local process only.

## Write refusals (create, import, update)

Entry deletion on MCP `kb_delete`, REST `DELETE /api/entries/{id}` and CLI
`delete --force --format json` also returns `inbound_links`,
`inbound_link_count` and `warnings`. Each link row has `id`, `kb_name` and
the relation written by the source. Only readable KBs contribute rows or
counts. Self-links are excluded. With no inbound links the list and warnings
are empty and the count is zero. These are index snapshots, so a hand edit
made since the last sync may not appear. Deletion does not edit the linkers.

The same three surfaces return `links_left`: the symbolic links inside the KB
that the walk lists and that pointed at the removed file, `[{path, target}]`,
KB-relative (`[]` when there are none). A delete removes the entry's real file
only and never removes a link, which is the user's file; `warnings` names
every link the walk lists, and the next `index sync` / `index health` lists
them under `dangling_links` (#1262). The delete's candidates come from the
files: the index row's path is a candidate only where the walk would list it,
so a row naming an in-KB link (dev's sync wrote one when the link sorted
before its real file; any row can be stale) does not make the link the
entry, and `links_left` lists only links still on disk that point at nothing.

Limit: a link in a folder the walk skips (a dot-prefixed folder such as
`.hidden/`, or `_templates/`) is not walked. A delete keeps it but does not
name it, and the next sync does not report it as dangling; a templated move
(below) does not see it and moves the file, leaving it dangling, unreported.

What the walk lists is one rule (`pyrite/storage/entry_paths.py`, #1552): a
`*.md` name (lower-case suffix) below the KB root, with no dot-prefixed part,
no part spelled `_templates` and no file named `README.md`. The last two are
matched the way a filesystem may match a name: in any case, after Unicode
case folding and normalisation, and without trailing dots or spaces, so a
folder spelled `memos/_TeMpLaTeS/` counts as `_templates` on every disk: the
walk skips it and a write into it is refused (on APFS or NTFS it may be the
very folder `memos/_templates/`). The four calls that place a new entry file (a create, the templated move,
software-kb's `migrate-standards`, `index reconcile --apply`) ask this rule
before they write. One test fails when any of them stops asking, asks only
after it writes, or asks about another path; a scan of the code fails when a
new call of a placing shape it knows appears (`create_entry_text`,
`move_entry_text`, a `move`, `move_bytes` or `rename`), and names shapes
it is known to miss; that list is not complete (`tests/test_one_predicate_decides_what_the_walk_lists.py`). So an entry a
write placed is one the next sync lists, and a delete, update or read by id
finds it before and after that sync. One spelling no rule on the name can see
is an NTFS 8.3 short name (`_TEMPL~1`). Where Pyrite chose the file name it
chooses a listed one: an id whose default name the walk skips (`readme`,
`README`) is written as `readme-2.md` by the existing `-N` rule, and the id
stays `readme`. Where `kb.yaml` (a `subdirectory`, a `file_pattern`) or a
field value (a templated `subdirectory`) chose a path the walk skips, the
create is refused with `VALIDATION_FAILED` and nothing is written; the message
says what the caller can change. A templated move or `index reconcile` toward
such a folder keeps the file in place and says why. A file an earlier version
wrote at such a path is not indexed and not found by id.

A write that would move a file such a link names (a link the walk lists),
whether a templated-subdirectory move on update or `pyrite index reconcile`,
leaves the file in place and says why, naming the link. On update it is
`not_moved`. On reconcile every such file gets a "Kept in place <id>: ..."
line. When no move is planned at all, a summary follows ("No moves planned;
N misplaced file(s) stay where they are because an in-KB link names each",
never "could not be checked"); when other files are movable, the table of
moves follows instead, with no summary. The exit code: with no move planned,
or on a dry run, a kept file exits 3. With `--apply` and moves planned, it is
`exit_unless_whole` over kept files plus failed moves: 3 when at least one
move succeeded, 1 when none did (for example a kept file beside a move that
failed), 0 when nothing was kept or failed. A read-only KB with moves planned exits 1 under `--apply` before any move.

CLI deletion keeps its default rich output and supports `--format json`.
Without a terminal it refuses an unconfirmed deletion with `USAGE_ERROR`
and a suggestion to pass `--force`.

Every entry write — REST `POST /api/entries`, `PUT`/`PATCH
/api/entries/{id}`, `POST /api/entries/import`, `POST /api/clip`; MCP `kb_create`,
`kb_update`, `kb_bulk_create`; CLI `pyrite create`, `pyrite update`,
`pyrite add`, `pyrite import` — goes through one pipeline in `KBService`,
so the same entry is refused with the same `error_code` on every surface.
A request cannot set a managed field. Two rules relax that: an in-process
owner sets its own field through `create_entry(..., owns=...)` (`sw new-adr`
names `adr_number`), and an import drops the managed fields and the index and
location keys an export carries and reports them (below). One more exception
stands, pending the maintainer's explicit OK: `pyrite add FILE` adopts a
hand-written file's own values (an ADR keeps its `adr_number`, a task its audit
trail), which are not a request (#1010).

REST entry writes share one top-level request-model rule: `POST /api/entries`,
`PUT /api/entries/{id}` and `PATCH /api/entries/{id}` refuse an undeclared key
with HTTP 400 `VALIDATION_FAILED`, naming all unknown keys before validating
other values or writing the file or index. Each route uses the same dependency,
`refuses_undeclared_keys(Model, verb)`, followed by its model's `extra="forbid"`
guard. The `metadata` envelope spelling of a managed field is refused by the
service, as on every surface (#1010, #1090).

Send only the fields declared by the route's request model. Type-specific
fields, such as the web New Entry form's Status, travel in `metadata`. A whole
GET response is not a PUT body: read-only keys such as `id` and `file_path`
must be omitted. PATCH accepts `kb`, `field`, `value` and `op` (#1221).

All three verbs accept the ADR-0034 transport markers (`body_truncated`,
`body_length`, `body_offset`, `body_chunk_size`) from `body_bounds.MARKER_KEYS`.
They are checked on the raw JSON and removed before model validation, never
stored as entry fields. A truthy truncation marker still refuses a body write;
it is inert for a PATCH of a non-body field.

A misspelt `vaule` is not an instruction to clear the field. Explicit `value:
null` remains a valid set; `append`, `remove` and `unset` keep their existing
semantics.

This is the one table of codes an agent meets while writing; the
how-to (`docs/agent-write-path.md`) links here instead of repeating it. Each row
is reproduced by a command in "Reproduce the codes" below, which CI runs.

| `error_code` | Meaning | What to do |
|---|---|---|
| `KB_NOT_FOUND` | the KB name is not registered, or, for a scoped caller, not readable (the same answer, byte for byte), or it is a KB's **shortname**: a shortname is link syntax only (`[[short:id]]`), and `-k`, REST `kb` and MCP `kb_name` take the full name (#952). One resolver answers every surface; the refusal carries `did_you_mean` (near matches drawn only from the KBs you can read; for a readable KB's shortname, its full name) and a `suggestion`/`hint`. A blank name names no KB and is never this code: a command that needs one answers `VALIDATION_FAILED`. The one exception is a blank REST path segment (`/api/kbs/%20/permissions`): a path that must name a KB and names none is `KB_NOT_FOUND`. Every CLI write command (`create`, `update`, `delete`, `link`) reports it. | Use a name from `did_you_mean`, or `pyrite orient` (`kb_orient`) with no KB to list the KBs you can read. |
| `NOT_FOUND` | the entry id does not exist: CLI `get`, `update`, `delete`, `link`. Also a path you named as input (`--body-file`, `--schema-file`, `--alias-file`, `--file`, `import`'s and `add`'s file) that does not exist; an empty path or a non-regular file (directory, FIFO or device) is `VALIDATION_FAILED` before command work (#923, #1165). `pyrite backlinks` and `pyrite-read backlinks` on an id that no entry has and nothing links to answer `ENTRY_NOT_FOUND` (the class's code, today; #610 settles the spelling). An id that is only linked to (`[[ghost-page]]`) is answered with its linking entries. For MCP spellings and legacy codes, see [MCP code transitions](#mcp-code-transitions); `ENTRY_NOT_FOUND` is also the REST class code. | `pyrite search <term> -k <kb>` to find the id. |
| `AMBIGUOUS` | the id was given without a KB (or with a blank `-k`) and more than one KB you can read holds it (see "`AMBIGUOUS`" above). The error lists them in `candidate_kbs`; REST answers `409`. | Repeat the command with `-k <kb>` (`kb` on REST, `kb_name` on MCP), one of `candidate_kbs`. |
| `UNDECLARED_TYPE` | the KB's `kb.yaml` declares types and this is not one of them. Core types (`note`, `person`, …) are **not** exempt. Only the type a create would store is checked (#1526). A declared mapping (`type_mappings` in `kb_schema`/`orient`, e.g. `event` → `investigation_event`) applies when the KB declares its target, or declares no types; the named type need not be declared. So in a KB made from the journalism-investigation preset, which declares `investigation_event` but not `event`, `create -t event` stores `investigation_event`. When no mapping applies, the named type is checked: a KB that declares neither `event` nor its target refuses `create -t event`. `allow_undeclared` never changes which mapping applies; it only lets an undeclared named type with no applicable mapping be stored as named. The write path also has a check that refuses, naming it, a mapped type the KB does not declare; no create reaches it today (the resolver never offers such a pair), and it guards a later `kb.yaml` `type_map:` override (#468). Override with `allow_undeclared` (MCP, REST body or import query) / `--allow-undeclared` (CLI). The error carries `declared_types` on MCP and REST. | Use a declared type (`pyrite kb schema show <kb>` prints the KB's `kb.yaml`; the types and the `type_mappings` a create applies are in `pyrite orient -k <kb> --format json` under `schema`), or pass the override. |
| `ENTRY_EXISTS` | the id (given, or derived from the title) already exists. Create never replaces; use update. REST answers `409`. | `update` it, or choose another title or id. |
| `SCHEMA_VIOLATION` | the KB schema or a plugin validator rejected a field: enum, required, range, format. A kb.yaml enum (`options:`/`values:`, list `items:`, rule `enum:`) is refused when `validation.enforce_enums` is on (the default); every other kb.yaml finding when `validation.enforce` is on. The message names the field, the value and the allowed list (`kind: 'chore' is not one of [...]`). An update that leaves an off-list value as it was is not refused (see **Warnings**). | Re-read the message: it names the field and the allowed values. |
| `VALIDATION_FAILED` | anything else the entry model refuses (an event without a date, a missing title), a `managed_fields` key in an update, a field Pyrite or the type manages on a create at any spelling (`status_change_log`, `adr_number`, `file_path`, ...: top level, dotted `metadata.k`, under `metadata`, or an alias of the field; REST `POST` refuses only the `metadata` spelling, see above; #1010), an `entry` key holding a whole `kb_get` envelope sent to `kb_update` or REST `PUT` (send `result["entry"]`'s fields at the top level; the file is left untouched; #1024), the ADR-0034 truncated-body refusal below, and a create whose path, chosen by `kb.yaml` (a static `subdirectory`, a `file_pattern`) or by a field value (a templated `subdirectory`), is one the index walk does not list: a dot-prefixed folder or file, a folder named `_templates` in any letter case, a file named `README.md` in any case, or a name not ending in `.md` (#1552; a field value adds an `errors` row `{field, rule: "entry_path", got, message}`; nothing is written; a dry run, `validate_only` and bulk validate answer the same, counting the paths earlier items of the batch would take; the answer, not the path: no dry run reports the file name the write will use; on a case-insensitive disk two such paths that differ only in case are still compared as different names). Unchanged by ADR-0037 theme 2 (2026-09-25): REST, MCP and the CLI already agreed on this code, so no `legacy_error_code`. | Fix the named field. For a truncated body, reassemble it with `kb_read_body` and retry without the marker. |
| `CONFLICT` | the file kept changing between the update's read and its replace, five tries in a row; nothing was written. REST answers `409`; MCP and the CLI use the shape above. | Re-read the entry, then repeat the update. |
| `STORAGE_ERROR` | a storage fault on the write (REST `500`). The message is fixed text; the cause is in the server log. One exception: an index Pyrite cannot bring to its schema when it opens it (a column that cannot be added in place, a migration that failed) is refused with a message naming only schema versions and `table.column`, plus a `suggestion` (#1260); a lock met while opening, upgrading or writing the index is the retryable one. | Read `retryable`: `true` only for a locked database, so wait and repeat; `false` needs an administrator. |
| `OUTSIDE_KB_ROOT` | a write to a KB entry whose path leaves the KB root: the entry file, or a folder on its path, is a symbolic link to somewhere outside the KB's folder, or a configured path (a type's `subdirectory`, a `file_pattern`) leads outside it. The writer opens each folder beneath the KB root without following a link, so a folder swapped for a link while the write runs is refused the same way. Every write refuses it before anything is written: `create`, `update` (including `qa fix`'s), `rename`, `delete`, `schema migrate`, the reconcile move, software-kb's `migrate-standards`. A write **by id** (`update`, `rename`, `delete`) never reaches such a file: a file that is a link out of the KB, or has a second hard link, is not the KB's, so an id it holds matches nothing and the answer is `NOT_FOUND`, the same as for an id nothing holds, whatever that file holds. `OUTSIDE_KB_ROOT` is the answer where the path itself is the KB's name for the file (a `create` at that path, a folder on the way swapped for a link while the write runs). An update used to write through such a link to its target. A link whose target is inside the KB is still written through, and stays a link. The message names no path; the server log does. One neighbour keeps its code: a write whose own path is a file with more than one hard link (a write that does not look the file up by id) is `STORAGE_ERROR` (its other name may be outside the KB; the message names the KB-relative path only), in a KB under git or not; and `migrate-standards`, which moves the file, refuses an entry whose file is a symbolic link with `VALIDATION_FAILED` and the reason (the link's target is never removed). A `rename` moves nothing, so it is written through a link whose target is inside the KB, as an update is. | Replace the link with the file or folder it points to, inside the KB, then retry. |
| `LANDING_REFUSED` | a write with no place to land under the KB's `landing` (ADR-0044 §1): the KB is `landing: review` and the writer's change set cannot be made (no git repository, no user to own it), a write that names several KBs one of which lands in a change set, or a database-only record asked of a change set (a review verdict: `sw_review`, `POST`/`DELETE /api/reviews` on a `landing: review` KB), which the change set could not carry through its merge; that one is available to the operator, or after the merge. Nothing is written to the KB's files or its index, and a write never falls back to them. The refusals above come before the working copy is made, so none leaves a worktree or branch behind. Two refusals after it is made are possible in principle (its config or its index cannot be opened); they leave the new working copy, unused, at main's commit. | Write to one KB at a time, or ask the operator about the KB's landing. |
| `UNSAFE_DESTINATION` | export to a repository: a directory or file the export would create or write in its temporary clone does not resolve inside that clone (a link in the cloned repository, or a name leading out of it). Nothing is committed or pushed. | Remove the link from the target repository, or export to another one. |
| `QUERY_SYNTAX` | (`search`, not a write, but what an agent hits right after a failed write) a token containing `-`, `:` or `.` used beside `AND`/`OR`/`NOT` or a quote, unquoted. | Quote the token yourself: `"bar-baz"`. |

All are `retryable: false` except `CONFLICT` (always `true`) and `STORAGE_ERROR`
(`true` only for a locked database). REST reports a refusal as
`{"detail": {"code", "message", "retryable": false, "hint"?, "declared_types"?}}`
with status `400` (`409` for `ENTRY_EXISTS`).
An update whose candidate entry raises `EntryLoadError` returns `422` with
`VALIDATION_FAILED` and, when fields are isolated, one `errors` row with
`rule: "entry_load"` per refused field.
REST `CONFLICT` is `409` with
`{"detail": {"code", "message"}}`, no `retryable` key; treat it as retryable.
`STORAGE_ERROR` is `500` and carries `retryable`. MCP and the CLI use the error
shape above.

**A field refusal names the field.** `VALIDATION_FAILED` and `SCHEMA_VIOLATION`
carry `errors`, one row per refused field, in the same shape on REST, MCP and
the CLI:

```json
{
  "error": "Event must have a date",
  "error_code": "VALIDATION_FAILED",
  "retryable": false,
  "suggestion": "set date (YYYY-MM-DD)",
  "errors": [
    {"field": "date", "rule": "required", "message": "Event must have a date", "expected": "YYYY-MM-DD"}
  ]
}
```

Every row carries `field`; a row Pyrite itself builds also carries `rule`, on
`SCHEMA_VIOLATION` too: the schema path's own rows name the check that refused
(`field_select`, `field_range`, `required`, ...). A plugin validator's rows are
copied as the plugin wrote them, so a plugin that sends only `{field, message}`
contributes a row without `rule`. A row from the entry model also carries
`message` — the sentence that reaches `error`, so a caller never has to parse
it — and `expected` when the fix is mechanical; a row from the KB schema or a
plugin validator carries `expected` and, where the rule knows it, `got`: the
allowed-list/value pair a `SCHEMA_VIOLATION` row has always carried.
`importance` has one rule on every type, write and surface (#1588): a whole
number from 1 to 10, sent as a number (a whole float such as `5.0` counts) or as
plain ASCII digits (`"05"`), and every create and update stores it as that number
(`5.0` and `"05"` are written as `5`; `pyrite add` leaves a file's own `'05'` as
written, and every reader sees `5`); anything else (`5.5`, `true`, `" 5 "`,
`"+5"`, `"１０"`, `high`) is refused with the model row `{"field": "importance",
"rule": "range", ...}` (over REST, HTTP 400 `VALIDATION_FAILED`, where `99` and
`10.7` used to get pydantic's 422; the OpenAPI schema names the integer to send).
A value already in a file, at the top level or under `metadata:`, is never
rewritten by a write that does not set it, and is kept with a warning carrying
that `rule` only while the write leaves it as it is or sends that same value in
the same YAML type (`"10.7"` over `10.7` is a new value, judged and refused). A row from
the KB schema's enum pass also names `origin` (`field` for a field's own
`options:`/`values:` list, `rule` for a `validation.rules` entry), `severity`
(`error`, or `warning` when the field has `allow_other: true`), and
`allow_other: true` on such a field. `got` and `expected` are
always JSON-safe: a date written without quotes in frontmatter arrives as its
ISO string (`"2026-01-01"`), and a non-finite float as its text (`"inf"`), so
an unquoted value cannot turn the refusal into an unrenderable answer. A
refusal that names one field carries one row — a frontmatter key the entry type
does not declare, the first managed spelling a create finds, a bulk link spec
missing `source` or `target`, a required frontmatter key that is absent. Several
rows come back when one pass refuses several fields — the entry model's own
validation, or a plugin validator that reports a list — in the order that pass
reports them. A
refusal that is not about a field carries no `errors` key at all: the key is
absent, never empty. In a bulk answer the rows travel on the record they
refused, not at the top: `results[i].errors` for `kb_bulk_create` and
`links bulk-create`, `error_details[i].errors` for an import, and an
`entity_errors[i].errors` row for the journalism-investigation setup tools that
create several entities at once, or an `entries[i].errors` row for the edges
`investigation_bulk_edges` creates. A refused edge always carries rows there,
the shape checks included: an edge that is not an object answers
`{"field": "edge", "rule": "type", "expected": "object"}`, `fields` that are
not an object `{"field": "fields", "rule": "type", ...}`, and a key that
belongs to the entry itself (`title`, `id`, `body`, ...) `{"field": <key>,
"rule": "reserved"}`.

**Whole-request refusals (REST).** Two refusals stop an import, or any
request, before a record is read:

| status | `code` | Meaning | What to do |
|---|---|---|---|
| `413` | `PAYLOAD_TOO_LARGE` | the request body is larger than `settings.max_request_body_bytes` (default 32 MiB; see `docs/configuration.md`, "Server"). Applies to every route, `/auth` and `/mcp` included. Answered as soon as the body passes the limit (at once when `Content-Length` declares it); nothing is written. | Send a smaller body: split the import into several files, or ask the operator to raise the limit. |
| `400` | `PARSE_ERROR` | `POST /api/entries/import` could not read the file in the named format (not UTF-8, or not valid JSON, YAML, CSV or markdown frontmatter). The message is Pyrite's own and names the format; the parser's detail is in the server log, not the response. Nothing is written. | Check the file's encoding and format, or pass the right `format=`. |

Both answer `{"detail": {"code", "message", "retryable": false}}`; the `413`
adds a `hint`.

**Per-item results.** `kb_bulk_create`, `POST /api/entries/import` and
`pyrite import` refuse a bad item on its own and create its siblings;
results keep the input order. Each failed item carries `error_code`:

```json
{"created": false, "error": "Entry with ID 'x' already exists in KB 'k'. ...", "error_code": "ENTRY_EXISTS"}
```

REST import reports the same pair per item in `error_details`
(`{"title", "error", "error_code"}`).

**Import drops what a create refuses, and says so.** A file an export wrote
carries keys about the index and the location (`kb_name`, `file_path`,
`indexed_at`, `content_hash`, `outlinks`, `backlinks`, ...) and a type's
managed fields (a task's `assigned_at`, `status_change_log`, `evidence`,
`agent_context`; an ADR's `adr_number`), at any spelling. `pyrite import` and
`POST /api/entries/import` (not the direct creates `kb_create`,
`kb_bulk_create`, REST `POST`, `create -f`, which refuse them) drop those
keys from each record and list every one in that record's `ignored`:
REST `entries[].ignored`, `pyrite import`'s `Ignored:` line. Every dropped key is listed, whatever its value (a `null` index column such
as `created_by` is listed too). The entry's `id` and
`entry_type` are kept, so an export from one KB imports into another with
`imported` equal to the count. `pyrite import` prints
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
lines. An update is refused only for a violation it causes (ADR-0042
decision 8, #676): a schema violation already in the file -- an off-list
enum value, a multi-select holding a scalar, a number out of range, a
missing required field -- in a field the request does not change (compared
by value, so an echoed read counts; per element for a list-valued enum)
succeeds and is returned here as `{"field", "rule", "severity": "warning",
"note": "..already on disk.."}` rather than refused (#47, #555). This covers
a plugin validator's finding that names a `field`. Changing the field to
another bad value, adding an off-list list element, or a change that
triggers a new finding is still refused; create is always strict. **Limit:**
a plugin validator finding with no `field` cannot be matched to a request
key, so it still refuses an unrelated update.

**`index health` → `off_list_values`.** One row per off-list value of a
declared enum: `{kb, id, type, field, value, allowed, origin, severity}`.
`origin` is `field` or `rule` (kb.yaml) or `plugin` (a plugin validator's
`rule: "enum"` on any field but `status`, which stays in
`invalid_statuses`). `severity` is `error` for a kb.yaml enum in a KB with
`enforce_enums` on (status `unhealthy`, exit `1`), `warning` with it off
and for every plugin row (status `warning`), and `info` for an
`allow_other` field (does not change the status). Empty for a KB without
drift.

**`index health` keys.** `pyrite index health` prints these top-level keys.
`checks` repeats the detailed finding arrays; the top-level counts and rows are
the same findings. One row is one top-level key; a key added to the report needs
a row here (a test compares this table with the command's output).

| Key | What it counts | Status | Clear with |
|---|---|---|---|
| `status` | The verdict over every row below: `healthy`, `warning` or `unhealthy`. | Verdict | Clear the rows that make it `unhealthy`; `--no-fail` only changes the exit code. |
| `missing_files` | Indexed entries whose recorded file path no longer exists. | Failure | Restore the file, or drop the stale rows with `pyrite index sync`. |
| `unindexed_files` | Files on disk that have no index row. | Failure | `pyrite index sync`. |
| `stale_entries` | Entries whose file mtime or size differs from the values recorded at index time. | Failure | `pyrite index sync`. |
| `content_changed` | Entries whose content hash differs from the indexed hash even when mtime/size did not change. | Failure | `pyrite index sync --verify`. |
| `broken_links` | Link rows whose target resolves to no entry. | Warning | Hand edit the link or create its target, then `pyrite index sync`. |
| `links_stale_kb` | Link rows whose written KB prefix would now resolve to a different KB. | Warning | `pyrite index build` (it re-reads every link row). |
| `undeclared_types` | Entries whose `type` is not declared in the KB's `kb.yaml`, core types included. | Warning | Hand edit the `type`, or declare it in `kb.yaml`, then `pyrite index sync`. |
| `missing_required_fields` | Entries missing a field the type's `required:` list declares. | Warning | `pyrite update <id> -f <field>=<value>`. |
| `subdirectory_mismatches` | Entries whose file is outside the type's declared `subdirectory:`. | Warning | `pyrite index reconcile <kb> --apply`. |
| `malformed_frontmatter` | Files that read but whose frontmatter does not parse. | Warning | Hand edit the YAML, then `pyrite index sync`. |
| `unreadable_files` | Files that exist but whose bytes cannot be read or decoded. | Failure | Fix permissions or encoding, then `pyrite index sync`. |
| `unreadable_kbs` | KBs whose `kb.yaml` Pyrite could not check or read (for example, a KB folder it may not search), as `{kb, error}`; every other KB is still checked. | Failure | Fix the folder's permissions, then `pyrite index sync`. |
| `unreadable_folders` | Folders below a KB's root that the index walk could not list (for example, mode `000` or `100`), as `{kb, path, error}`: `path` KB-relative, `error` the fixed `KB_UNREADABLE` message, never the OS's words. The index rows of the entries under such a folder are kept as they are (#1546). Only the outermost of nested folders is named; a folder under a dot-prefixed folder or `_templates/` is never named (the walk lists nothing there). A symbolic link the OS reports as one is never named, whatever its target. REST `GET /api/kbs/{name}/health`, MCP `kb_registry_health` and `pyrite kb health` carry it as a count. See "A folder or file Pyrite cannot reach". | Failure | Fix the folder's permissions, then `pyrite index sync`. |
| `orphaned_entries` | Indexed IDs absent from the readable files although the recorded path exists. | Failure | `pyrite index sync` (or hand edit the ID). |
| `invalid_statuses` | Entries whose `status` a plugin validator's enum rejects. | Warning | `pyrite update <id> -f status=<allowed>`. |
| `off_list_values` | Off-list values of declared enums, with their `origin` and `severity`. | Failure for `severity: error`; warning for `warning`; `info` changes nothing | `pyrite update <id> -f <field>=<allowed>`. |
| `duplicates` | IDs held by more than one file; `winner` names the indexed copy. | Failure | Keep one file, then `pyrite index sync`. |
| `aliases` | In-KB symbolic links to another walked entry file, `{kb, path, target}`. The link is never indexed or read itself, and is left as it is; `target` is read by the walk's own rules (indexed once, unless it is skipped, as a file with a second hard link is). The same list is returned by `IndexManager.sync_incremental`/`reconcile_kb`, CLI `index sync` (text) and `kb reindex` (JSON and text), REST `POST /api/kbs/{name}/reindex` and MCP `kb_registry_reindex`; MCP `kb_index_sync` and REST `POST /api/index/sync` return neither key. | Information, not a finding | Nothing to do; remove the link only if it is not wanted. |
| `dangling_links` | In-KB symbolic links with nothing at the path they name, `{kb, path, target}` (`target` KB-relative): hand-made, or left by a delete, which keeps the user's link. They hold no entry, so they are not `unreadable_files` and not `malformed`. A `__collection.yaml` that is such a link is reported here too. Only links the walk lists are reported: one in a dot-prefixed folder or `_templates/` is not. The same surfaces as `aliases` return it. A link that leads out of the KB, or a link loop, is reported as before. | Warning | Restore the file the link names, or remove the link. |
| `unlisted_entry_files` | `*.md` files the index walk meets and skips whose frontmatter states an `id:`, `{kb, path, reason}` with `reason` `readme` (named `README.md` in any case), `hidden` (a dot-prefixed folder or file) or `templates` (under a folder named `_templates` in any letter case). An earlier version could write one (a create titled README wrote `readme.md`, #1552); it is not indexed, so its id is not found. Only skipped files are read, through the KB's contained read: a link, a file with a second hard link, or one that cannot be decoded is not listed here. A README, dot-folder or `_templates/` file with no `id:` line is the user's and is not listed. On the local CLI's `pyrite index health` the key carries these rows. REST `GET /api/kbs/{name}/health`, MCP `kb_registry_health` and the CLI's `pyrite kb health`, which all answer through `KBRegistryService.health_kb`, carry it as a count (an integer) and name no file (maintainer, 2026-11-17); the text form of `pyrite kb health` prints a line pointing to `pyrite index health` when the count is above zero; `healthy` is unchanged. | Warning | Rename the file to a name the walk lists, keep its `id:` line, then `pyrite index sync`. |
| `removed_extensions` | KBs whose type belonged to an extension that is no longer installed. | Warning | If the KB's `kb_type` is a `cascade-*` one, change it in `kb.yaml` (to `generic`, or to `journalism-investigation` with that extension installed). If the KB holds entries of a removed extension's types, declare the types you keep under `types:` in `kb.yaml`. A KB may need both. |
| `type_name_drift` | Groups of one type spelled more than one way after case, `_` and spaces are folded to `-`. | Warning | Hand edit the `type:` spellings, then `pyrite index sync`. |
| `type_name_drift_total` | Number of drift groups before the report's cap. | Metadata, not a finding | Fix the groups listed there. |
| `type_name_drift_has_more` | Whether more drift groups exist beyond the 100-group cap. | Metadata, not a finding | Narrow with `-k <kb>`; fix the groups listed. |
| `schema_errors` | KBs whose `kb.yaml` failed to load. A row with `error_code: KB_CONFIG_INVALID` (the file does not load as a mapping, #1547) is Unhealthy; a row with no `error_code` (a key with the wrong shape, such as `types: null`) is a Warning. | Unhealthy / Warning | Hand edit `kb.yaml`, then `pyrite index sync`. |
| `unmodellable_frontmatter` | Files whose frontmatter Pyrite cannot model (a non-string key, a set, a duplicate key). | Warning | Hand edit the unsupported frontmatter, then `pyrite index sync`. |
| `double_spellings` | Fields one file spells twice (`k:` and `metadata.k`, or a field and its alias). | Warning | `pyrite qa fix <kb>` when both values are identical; otherwise hand edit both spellings. |
| `checks` | The detailed arrays behind the counts above, under the same key names. | Not a finding | Fix the rows above; this key repeats them for callers that want paths and ids. |

**`index health` file findings.** The JSON report exposes the detailed
`unreadable_files` and `orphaned_entries` arrays both at the top level and in
`checks`:

- `unreadable_files` rows are `{kb, path, error}`. They represent files that
  exist but whose bytes cannot be read or decoded (an `OSError` or a
  `UnicodeError`, for a Markdown file and a `__collection.yaml` alike);
  `error` includes the exception type and message. Any such row makes status
  `unhealthy` and the CLI exits `1`; `pyrite-admin index health` counts them
  too. A file in a folder the process cannot search (mode `600` or `000`) is
  listed here too, with its `PermissionError`; health reports it and does not
  stop. **Behaviour change:** before this, a file with an encoding or
  permission failure was filed under `malformed_frontmatter` (status
  `warning`, exit `0`); it now moves to `unreadable_files` (status
  `unhealthy`, exit `1`). A caller keying on the old field or on exit `0`
  for such a KB sees the change.
- `orphaned_entries` rows are `{kb, id, path}` for an indexed ID absent from
  the files health check successfully loaded while its recorded path still
  exists. The files checked are the ones `index sync` walks (Markdown and
  `__collection.yaml`), so a healthy KB with a folder collection has none.
  Paths already reported as unreadable or malformed stay in those
  categories. When a readable replacement ID is the only file holding it and
  sits at the same path, the row also has `replacement_id`. An ID edited to
  one another file already holds leaves no `replacement_id`: that ID is
  reported under `duplicates`, not as a replacement. These findings make
  status `unhealthy` and exit `1`; `pyrite-admin index health` counts them
  too.
- `malformed_frontmatter` remains its own category: content that reads but
  does not parse (bad YAML, a collection file that is not a mapping) is never
  labeled unreadable, and bytes that cannot be read are never labeled
  malformed YAML.
- `missing_files` still refers to the path recorded in the index. A file moved
  on disk with its ID unchanged is reported there until `index sync` updates
  the indexed path, even if health check can already read the moved file.

**`index health` → `type_name_drift`.** One group per KB per entry type
spelled more than one way once case, `_` and spaces are folded to `-`:
`{kb, normalized, spellings, spellings_total, spellings_has_more}`, each
spelling `{type, count, declared}` (`declared`: that exact spelling is a
`kb.yaml` type). KBs are never combined. Bounded to 100 groups
(`type_name_drift_total`, `type_name_drift_has_more`) and 50 spellings per
group. Read-only: nothing is renamed. Any group makes the status
`warning`. `pyrite kb health`, REST `GET /api/kbs/{kb}/health` and MCP
`kb_registry_health` (admin tier; argument `name`) carry the same three fields for their one KB.

**`index health` → `schema_errors`.** One row `{kb, error}` per KB whose
`kb.yaml` failed to load (invalid YAML, `types: null`, `types` a list).
Health still answers for that KB: it counts as declaring no types, so its
non-core types appear in `undeclared_types` and no spelling is `declared`.
A row for a `kb.yaml` that does not load as a mapping carries
`error_code: KB_CONFIG_INVALID` (the file is not UTF-8, not valid YAML, over the YAML limits, or not a
mapping, #1547) makes the status `unhealthy`; its `error` is the parser's
words on the local CLI and stdio MCP and `the server log has the details` for
a network caller, and that KB's files are not compared with the index (each
would read as malformed against no schema). Any other row makes the status
`warning`. The same field is on `kb health`, REST and MCP
`kb_registry_health`.

**`index health` → `double_spellings`; `qa validate` →
`field_spelled_twice`.** `metadata` is not a field: `metadata.k` is a
second spelling of field `k`, one level only, the same as an alias (Andon
#994, decision A; ADR-0051, an accepted amendment to ADR-0042 decision 6). A
file that spells one field twice (`k:` and `metadata.k`, a field and its
alias, or either under `metadata:`) is read one way, the field's own
top-level name first, in the model and the index row alike, and an update
of that field is refused with `VALIDATION_FAILED` naming both spellings
until one goes; its other fields stay editable. A pair whose two spellings
hold the same value is still a double spelling: `qa fix` (rule
`field_spelled_twice`) merges it into the field's own top-level spelling,
removing the other spelling and losing nothing, and removes a `metadata:`
block that is left empty; "the same value" is the same YAML value, type
included (`true` is not `1`, `1` is not `1.0`; the notation is not the
type: `x`, `'x'` and `"x"` are one string), so a pair that differs in
any way stays a hand fix, as does a doubly spelled `type`. Only `qa fix`'s
own call may remove a spelling; an `unset` of one through any surface is
refused like any other update of the field. On create, a declared field or an alias sent inside the
`metadata` envelope lands at its own key; only an undeclared key is placed
under `metadata:` (#178); a field Pyrite or the type manages (`id`,
`provenance`, a task's `status_change_log`, ...) is refused there as an
update refuses it, on REST `POST`, MCP `kb_create` and the service
(`links` and `sources` stay a new entry's own content). **The reader reads
every field where the writer writes it (#1025):** a field the type declares
and the file spells only under `metadata:` reads exactly as if the file
spelled it at the top level, in the model, the index row, the single-entry
read, search and health, so `update -f importance=8` then `get` agree and
`metadata.links` is indexed and appended to. **A single-entry read spells
each field once, at its own name** (`pyrite get`, MCP `kb_get`, REST `GET
/api/entries/{id}`; ADR-0051, accepted amendment 2026-10-20): a declared
field never appears under the read's `metadata`, which holds only keys the
type does not declare (a backlog item's `rank` reads at `rank`, never at
`metadata.rank`; a task's `evidence`, `status_reason` and `parked_awaiting`
likewise). A key the type does not declare is not lifted: it reads under
`metadata` only. A read never loses a value the file holds: a file that
spells a field twice reads the top-level value at the field's name and
keeps the nested value under `metadata` (where `index health` reports the
double spelling), and a declared field a type's class does not read from
its frontmatter keeps its `metadata` spelling. A `metadata` key leaves the
read whenever the file has no top-level spelling of the field, whatever
the index column made of its value (`metadata: {importance: '8'}` reads
`importance: 8` and no `metadata.importance`); only a true double spelling
compares values. **A single-entry read returns every key the read
produces, the same set on all three surfaces:** REST `GET
/api/entries/{id}`, MCP `kb_get` and `pyrite get --format json` carry the
same keys with the same values, the bookkeeping fields included
(`created_by`, `modified_by`, `indexed_at`, `assignee`, `content_hash`,
`file_path`, ...). `EntryResponse` declares the stable core and passes every
other key of the read through (a fixed field list used to drop a lifted
field such as a task's `status_reason` or a backlog item's `rank`). One
REST-only key remains: `participants: []` on an entry whose type has no
such field, an `EntryResponse` default the web client reads unguarded
(under review, #1025). An anonymous REST reader of a public KB sees the
same keys as an authenticated one: the read scope decides whether the
entry is returned, never which of its keys. An echo of a read back through REST `PUT` or
MCP `kb_update`, with a key added or a field edited, is written; a `metadata`
key the file did not hold lands at the top level of the file (decision 6).
The index row (`db.get_entry`, `db.list_entries`, the shape the plugins
read) keeps its whole `metadata` bag. `aliases` is its own row column, so a file with a
top-level `aliases:` reads it at the top level and never under `metadata`,
and wikilink resolution by alias reads that column (and the bag, for a file
that nests it). `index health` lists each
such field once under `double_spellings`: `{kb, id, field, spellings}`,
where `spellings` are the dotted paths as the file spells them. The status
is `warning`. `qa validate` (the KB and one entry alike) reports one
`field_spelled_twice` issue per field, `severity: "warning"`, with the
`field`, the file's `path` and `entry_id`, and both spellings in `message`.
One rule decides both and the refusal
(`schema.field_aliases.double_spellings` over `spelling_in`).

**`index health` → `unmodellable_frontmatter`; `qa validate` →
`frontmatter_unmodellable`.** A file whose frontmatter Pyrite cannot model
(a tagged key `!!str a: 1`, a complex key `? [a, b]`, a duplicate key, a
non-mapping or one flow-style `{...}` root) gets no write through Pyrite,
body included; an update is refused with `VALIDATION_FAILED` and the reason
(#760 (b)). `index health` lists each such file once under
`unmodellable_frontmatter`: `{kb, id, path, line, reason}`, where `line` is
the 1-based line of the key (or root) the reason names, `reason` is the
refusal's own text, and `id` is the reader's id for the file (an explicit
`id:`, else the one derived from the title; the file's stem when the file
cannot be read at all). The status is `warning`. `qa validate` (the KB and
one entry alike) reports one `frontmatter_unmodellable` issue per such file,
`severity: "warning"`, `field: "frontmatter"`, with the same `path`, `line`
and `entry_id` and the reason in `message`. One rule decides both
(`storage.file_operations.frontmatter_refusal`), so a file is listed exactly
when an update of it is refused, for the same reason, at the same line.
A read is refused only when the file has no readable fields: `get` of a
file with a tagged key, a complex key, a duplicate key, a root that is not a
mapping, or a value JSON cannot hold (`!!set`) (CLI, MCP `kb_get`, REST
`GET`) answers `VALIDATION_FAILED` with the reason (worded for a read, not an
edit) and, for the #760 shapes, `(line N)`. A file whose frontmatter is one
flow-style mapping still reads; only its update is refused. A duplicate-key
or non-mapping-root file is found by the file named `<id>.md` only, since the
reader cannot read its `id:` (#1035).
Frontmatter that does not parse is not listed here: it is the reader's
`malformed_frontmatter` row. A file that is not a regular file, cannot be
read or is not UTF-8 is skipped, as the reconcile skips it.

**`index health` → `links_stale_kb`.** A count (top level and under
`checks`) of link rows that a rebuild would point at a different KB: the
`kb:` prefix the file wrote, resolved against today's config, is not the KB
the row stores (#980). It catches a shortname renamed or added in config since
the file was indexed (`index sync` does not re-read an unchanged file) and a
row indexed before the full name was stored. A link whose prefix names no KB
is not counted: it is a broken link (`broken_links`), and a rebuild writes the
same row. Any count makes the status `warning`; `pyrite index build` re-reads
every file and clears it.

**`orient` → `notices`; `index health` → `removed_extensions`.** A KB
registered with a `kb_type` that a deleted extension owned (`cascade-research`,
`cascade-timeline`, `cascade-solidarity`), or holding entries of one of that
extension's types (`actor`, `timeline_event`, ...) that its `kb.yaml` does not
declare (#1468), is still a KB: its files
read and write as generic entries. Instead of leaving its types silently
undeclared, `pyrite orient -k <kb>` (MCP `kb_orient`) carries one sentence
for such a KB under `notices`, and `index health` lists it under
`removed_extensions`: `{kb, kb_type, extension, entry_types, message}`, where
`message` is the same sentence and `entry_types` are the removed extension's
types the KB holds and its `kb.yaml` does not declare (empty for a `cascade-*`
KB that declares them all). The status is `warning`. `pyrite extension list` keeps
naming the plugin until `pip uninstall pyrite-cascade` is run in the
environment that had it. The removed extensions are listed in
`pyrite/plugins/removed.py`.

**`kb_schema` / `orient` → `type_mappings`.** These six surfaces carry
`type_mappings`, an object `{core type: stored type}`, always present and
`{}` when nothing maps (#468): MCP `kb_schema`, MCP `kb_orient` (under
`schema`), `GET /api/kbs/{kb}/schema`, `GET /api/kbs/{kb}/orient` (under
`schema`), `pyrite orient --format json` (under `schema`) and
`pyrite-admin schema`. `pyrite kb schema show` does not: it prints the KB's
`kb.yaml` as written. It is exactly what a create in
that KB applies: a create that names a key stores its value, with or without
`allow_undeclared`; any other accepted name is stored as named. The pairs are
the plugins' declared mappings for the KB's registered `kb_type`
(journalism-investigation: `{"event": "investigation_event", "document":
"document_source"}`), less any pair whose value (the target) the KB's
`kb.yaml` leaves out when it declares types, through the one resolver the
write path reads (#1526). The key need not be declared. So a KB made from the
journalism-investigation preset, which declares `investigation_event` and
`document_source` but not `event` or `document`, shows both pairs, and
`create -t event` there stores `investigation_event`. A KB that declares
`event` but not `investigation_event` shows no `event` pair and stores
`event` as named. A KB made from the known-entities preset declares neither
and shows `{}`. Subclassing a core class maps nothing.

**`orient` -> `kb_authored`, `pyrite_guidance`.** A KB's `kb.yaml` carries
free text an agent reads (`guidelines`, `goals`, `evaluation_rubric`, a type's
`ai_instructions`), and whoever controls the KB wrote it. `pyrite orient -k
<kb>` (MCP `kb_orient`, REST orient) keeps it apart from Pyrite's own
guidance (#1369). Both keys are in `detail=full` only; `detail=brief` omits
them with the other write-side blocks.

- `kb_authored`: `{notice, source, read_only, guidelines?, goals?,
  evaluation_rubric?, types?, malformed?}`. `source` is `own`, `subscribed`
  (a clone under the workspace) or `fork`; `read_only` is the KB's flag.
  Each text key is present when the KB's own `kb.yaml` sets it to a
  non-null, non-empty value, and carries that value as written, in whatever
  shape the author gave it (a string `guidelines` stays a string; a key left
  blank inside a mapping, or a null list item, comes back as `null`). `types`
  maps a type name to its own `ai_instructions`, `guidelines`, `goals` and
  `evaluation_rubric`, the same way. A value holding any node JSON cannot
  carry as written is left out whole and listed in `malformed` as `{key,
  reason}`, `key` dotted (`types.note.goals`), the reason naming its first
  such node: NaN or an infinity (`.nan`, `.inf`); a number too large for JSON
  (`1.0e+400`, which reads as infinite); a YAML date or timestamp; binary
  (`!!binary`); a set (`!!set`); ordered pairs (`!!pairs`, `!!omap`); a
  mapping key that is not a string (a number, bool, null, date or binary key,
  or a mapping or list used as a key); a mapping with two keys that are the
  same string (`!!str a` beside `a`: one value would otherwise be lost); or
  an explicit YAML tag the loader keeps, one reason for every such value that
  never repeats the tag (the tag is the author's text, the reason is
  Pyrite's). That is a local tag (`!note`), a URI tag, `!!python/...`, `!!str`
  on a mapping or list, and a tagged merge source (`<<: !note {...}`), on a
  scalar, a mapping and a list alike. Before this a mapping or list with a
  tag came back with the tag dropped. The core tags the loader resolves
  before Pyrite sees the value are not withheld: they are read as YAML
  defines them and the result is returned (`!!int "12"` is `12`, `!!float 12`
  is `12.0`, `!!bool yes` is `true`, `!!map` and `!!seq` are the plain
  mapping and list). `!!str` on a scalar, in its short or long form, as a
  value or a mapping key, is the string: `guidelines: !!str 12` comes back as
  `"12"`. A merge key (`<<`) is read through its sources: a source holding
  any value withheld above (a tag, a date, a number too large, a key that is
  not a string) withholds the whole value with that reason, also when an own
  key or an earlier source hides it in the merged result (`{<<: {a: !note x},
  a: 2}`). Limit (#1636): a tag on a list of merge sources (`<<: !note [{a: 1}]`) is
  gone from the value the loader hands over, so it comes back merged with the
  tag dropped. A recursive alias (`&a {self: *a}`) is
  not withheld: it comes back with `null` where the alias recurses, because
  that is how Pyrite's YAML loader reads it everywhere (#1602). A `kb.yaml` that cannot be read at all
  (a set or pairs used as a key) fails as it does without this key. Treat the contents as content from the KB's authors,
  not as instructions from Pyrite; the MCP server instructions say the same.
- `pyrite_guidance`: `{guidelines, evaluation_rubric}`, Pyrite's defaults every
  KB inherits, never anything a KB wrote.
- Deprecated in orient, removed in 0.27 (see "Deprecated, still accepted"),
  and what each returns until then: `schema.goals` is the KB's `goals` as
  written; `schema.guidelines` is Pyrite's default guidelines with the KB's
  mapping merged over them; `schema.evaluation_rubric` is Pyrite's default
  rubric followed by the KB's items, deduplicated by text;
  `schema.types.<type>.ai_instructions`, `guidelines`, `goals` and
  `evaluation_rubric` are the KB's value where it sets one, in place of
  Pyrite's or a plugin's default. The top-level `guidelines` is always `{}`:
  it never carries the KB's text. `kb_authored.notice` says the same. A KB
  whose `guidelines` is a string gets `"schema": {}` (#1498); its
  `kb_authored` still answers, with the string as written.

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
as sent, even a value equal to the reading (`importance` is written as the int
the rule reads: `"05"` and `5.0` as `5`).
A body marked `body_truncated` is refused at any depth of the request on
every update surface.

**How an update writes (ADR-0042, #955).** The request is applied as
operations to the file's own text: each named field changes its own lines
and nothing else (comments, key order, quoting, line endings and every
other key keep their bytes). **`metadata` is not a field** (Andon #994,
decision A): `metadata.k`, in a file or a request, is a second spelling of
field `k`, one level only, the same as an alias. A field has one spelling
per file: an update is written at the spelling the file uses (`k:`, an
alias of `k`, or either under `metadata:`; the field's own top-level name
when the file has none), and a file that spells a field twice is reported
(`index health` → `double_spellings`, `qa validate` → `field_spelled_twice`)
and refused for an update of that field. A path whose field would be
`metadata` (`metadata` alone, `metadata.metadata.k`) is refused at any
depth, so a key refused at the top level (`type`, `id`, `links`, `body`) is
refused under `metadata` too. A request that names one field at two
spellings (`k` and `metadata.k`, a field and its alias, a key at the top
level and again inside the `metadata` envelope) with different values is
refused and writes nothing; equal values name one value (#720, extended
from aliases to every spelling). A path is read as `.`-separated keys with
`[i]` indexes, so a hand-written key whose own name contains a dot or a
bracket (`v1.2: old`) cannot be addressed from a request: `{"v1.2": "new"}`
writes `v1: {"2": new}` beside it. A key is written under the exact name
sent, so a name with whitespace or a lookalike character (`" id"`, a
Cyrillic `іd`) is a new key, not the field it resembles. `updated_at` is
refreshed only where the file already carries it, in its own spelling (the
separator, a `Z` or an offset, the fraction's digits); a timestamp the
request sets is written as sent, and one that is no instant is refused. A
storage fault on the write (a hard-linked file, a lock held too long) is
`STORAGE_ERROR` (`500` on REST; `retryable: true` only for the lock). A
request that changes nothing writes
nothing: the result carries `"changed": false` (CLI and MCP say it only
then; REST `UpdateResponse` always carries `changed`). The file is replaced
only if it still holds the bytes the edit was computed on, under a lock in
the KB's git directory; a file that keeps changing is refused with
`CONFLICT` (`409` on REST, `retryable: true`). A KB outside git is written
without a lock and the result says `"locked": false`. On a KB whose
`landing` is `review` (the default is `canonical`), a non-operator user's
write to a KB outside git is refused with `LANDING_REFUSED` (ADR-0044 §1;
see [the LANDING_REFUSED row](#write-refusals-create-import-update)): there
is no worktree to isolate the change set in. Not every route follows this rule
yet; #1043 tracks the
rest. An item operation
(`append`, `remove`) touches the one item it names: the other items keep
their bytes, and `links`/`sources` take item operations on every surface
though a set of the whole list is still refused; an appended link is
checked as a link. Every operation (`set`, `append`, `remove`, `unset`)
is first parsed into segments once, at the request boundary (`.` between
keys, `[i]` an index; a key inside the `metadata` envelope such as
`status_change_log[0]` or `metadata.id` is read the same way, and a path no
parser can read is refused), then resolved to its field (`field_at`: the
key, an alias folded to its target, `metadata.k` folded to `k`) and to the
one path the file spells that field at (`spelling_in`); every refusal is
decided on that field, and the path written is the path decided on,
in one place, whatever the path form (`k`, `metadata.k`, an alias,
`k[0].x`) or surface: a managed field, `type`, `id` and `body` as a key are
refused for every operation; `links` and `sources` take an item appended or
removed as a whole item only (`links[0].x` is refused), and an appended
item must be a mapping the item type reads (a link: `target` and
`relation`), checked before anything is written. MCP's `append` and
`remove` are objects (`{field: item}`), `unset` a list of keys; another
shape is `VALIDATION_FAILED`. `unset` removes a key with its own lines; a `null`
value is written as `null` (MCP `kb_update` sets a `null` for an undeclared
key aside, as before). In-process callers (`qa fix`'s link and tag
repairs, a task's evidence and audit trail) send one operation each
(`KBService.apply_operations`), never a list read through the model. A templated-folder
field on a file with no `id:` line does not move the file; the result says
why in `not_moved`. Frontmatter Pyrite cannot model (a tagged, complex or
duplicate key; an anchor on the edited key) is refused with
`VALIDATION_FAILED` and the reason, nothing written.

**Timestamp input: `created_at`, `updated_at`.** These two keys, and only
these two (`date` and every other key are written as sent, #1027), are
written as sent and accepted only when YAML reads them back as a date or
timestamp: `2026-10-02`, `2026-10-02T12:00:00Z`, `2026-10-02 12:00:00
+05:00`, `2026-10-02T12:00:00+5:30`, a fraction (`2026-10-02T12:00:00.123456Z`).
The one exception is what an HTML `datetime-local` field sends,
`2026-10-02T12:00`: it is accepted and written as `2026-10-02T12:00:00`.
Every other form is `VALIDATION_FAILED`, nothing written, with the accepted
forms in the message: `2026-10-02T12:00Z` and `2026-10-02 12:00` (no
seconds), a week date (`2026-W40-1`), a compact date (`20261002`), an
hour-only time (`2026-10-02T12`), a one-digit month (`2026-1-5`), a compact
offset (`+0530`) and `yesterday`. 0.25.8 accepted some of these and wrote
something else. The rule holds for `pyrite update --field` and for the
service every REST write goes through (`kb_service._TIMESTAMP_FIELDS`). MCP
`kb_update` sets a top-level `created_at` or `updated_at` argument aside and
lists it in `ignored`; the same key inside the `metadata` envelope is set, and
checked by this rule.

**Web client.** The web app sends `allow_undeclared: true` on create,
`POST /api/clip` and import, so its forms, which offer every core and
plugin type, keep working in a KB that declares types.

Source of truth: `pyrite/services/kb_service.py` (`_prepare`,
`bulk_create_entries`, `update`, `updatable_fields`) and the
`ValidationError` subclasses in `pyrite/exceptions.py`.

### Reproduce the codes

Each block below is run by `tests/test_doc_agent_contracts.py` in a scratch
HOME, in order; the exit code and the text it expects are the ones stated.
A refusal is JSON by default and one `ERROR [CODE]: message` line under `--format rich`; `pyrite import` has no output format and prints Rich text.

<!-- expect-exit: 0 -->
```bash
pyrite init -t empty -p ./notes --name notes --no-examples
mkdir typed && printf 'name: typed\ntypes:\n  ticket:\n    description: A ticket\n    fields:\n      kind:\n        type: select\n        options: [bug, feature]\n' > typed/kb.yaml
pyrite kb add ./typed --name typed
pyrite create -k notes -t note --title "Hello" --body "First."
```

`KB_NOT_FOUND`, in JSON and in Rich:

<!-- expect-exit: 1 -->
<!-- expect-text: "error_code": "KB_NOT_FOUND" -->
```bash
pyrite update hello -k nokb --title x
```

<!-- expect-exit: 1 -->
<!-- expect-text: ERROR [KB_NOT_FOUND] -->
```bash
pyrite create -k nokb -t note --title x --format rich
```

`NOT_FOUND`:

<!-- expect-exit: 1 -->
<!-- expect-text: "error_code": "NOT_FOUND" -->
```bash
pyrite update nope -k notes --title x
```

<!-- expect-exit: 1 -->
<!-- expect-text: "error_code": "NOT_FOUND" -->
```bash
pyrite link hello nope -k notes
```

`AMBIGUOUS`, from an id in two KBs, and the same command with `-k`:

<!-- expect-exit: 0 -->
```bash
pyrite init -t empty -p ./more --name more --no-examples
pyrite create -k more -t note --title "Hello" --body "Elsewhere."
```

<!-- expect-exit: 1 -->
<!-- expect-text: "error_code": "AMBIGUOUS" -->
```bash
pyrite get hello
```

<!-- expect-exit: 1 -->
<!-- expect-text: "candidate_kbs": ["notes", "more"] -->
```bash
pyrite update hello -k "" --title x
```

<!-- expect-exit: 0 -->
<!-- expect-text: "kb_name": "more" -->
```bash
pyrite get hello -k more
```

`ENTRY_EXISTS`, `UNDECLARED_TYPE`, `SCHEMA_VIOLATION`, `VALIDATION_FAILED`:

<!-- expect-exit: 1 -->
<!-- expect-text: "error_code": "ENTRY_EXISTS" -->
```bash
pyrite create -k notes -t note --title "Hello" --body "Again."
```

<!-- expect-exit: 1 -->
<!-- expect-text: "error_code": "UNDECLARED_TYPE" -->
```bash
pyrite create -k typed -t note --title "Nope"
```

<!-- expect-exit: 1 -->
<!-- expect-text: "error_code": "SCHEMA_VIOLATION" -->
```bash
pyrite create -k typed -t ticket --title "T1" -f kind=chore
```

<!-- expect-exit: 1 -->
<!-- expect-text: "error_code": "VALIDATION_FAILED" -->
```bash
pyrite create -k notes -t event --title "An event"
```

An entry file that is a link to a file outside the KB is not the KB's: an update by id answers `NOT_FOUND`, as for an id nothing holds, and the file it points to keeps its bytes:

<!-- expect-exit: 0 -->
```bash
printf -- '---\nid: away\ntype: note\ntitle: Away\n---\n\nKept.\n' > away.md
ln -s ../away.md notes/away.md
pyrite index sync
```

<!-- expect-exit: 1 -->
<!-- expect-text: "error_code": "NOT_FOUND" -->
```bash
pyrite update away -k notes --title x
```

<!-- expect-exit: 0 -->
```bash
grep -q '^title: Away$' away.md
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
| CLI | `pyrite create -k <kb> -t <type> --title <t> --body <b>` | **`--format` defaults to `json`:** `{"created": true, "entry_id": "<id>", "entry_type": "<type>", "outcome": "done"}`, plus `"warnings"` when any. With `--format rich`: `Created: <id>` / `Type: <type>`, plus a `Warning: ...` line per schema warning. With `--link`, `"linked"` and `"failed_links"` (each failed link carries its `finish_up` command); a link that did not resolve makes the outcome `partial` and the exit `3`. |
| MCP | `kb_create` | the block below, plus `"warnings": [...]` when any, plus `"qa_issues"` when a QA validator ran and found something. |
| REST (not run) | `POST /api/entries` | `{"created": true, "id": "<id>", "kb_name": "<kb>", "file_path": "", "warnings": []}`. `file_path` is **always the empty string** here (MCP returns the real path). |

<!-- mcp-output: kb_create -->
```json
{"created": true, "entry_id": "hello", "file_path": "/abs/path/hello.md"}
```

`pyrite add <file>` is the same shape as `create`: `{"added": true, "entry_id": "<id>", "entry_type": "<type>", "outcome": "done"}` (`"valid"` for `--validate-only`), or under `--format rich` `Added: <id>` / `Type: <type>`.

### `update`

| Surface | Call | Success shape |
|---|---|---|
| CLI | `pyrite update <id> -k <kb> --title <t>`; one item: `--append tags=gamma`, `--remove tags=alpha`, a key: `--unset extra` | **`--format` defaults to `json`:** `{"updated": true, "entry_id": "<id>", "outcome": "done"}`. With `--format rich`: `Updated: <id>`. |
| MCP | `kb_update`; one item: `"append": {"tags": "gamma"}` or `{"links": {"target": "b", "relation": "related_to"}}`, `"remove": {"tags": "alpha"}`; keys: `"unset": ["extra"]` (a list value under `append`/`remove` means each of its items) | the block below, plus `"warnings"` when any, `"ignored"` listing keys no update writes, and `"unchanged"` listing fields an echoed read result carried at the value the read returned (not written). |
| REST (not run) | `PUT /api/entries/{id}` (body carries `kb`) or `PATCH /api/entries/{id}` (body: `kb`, `field`, `value`, and `op`: `set` (default), `append`, `remove` or `unset`; one field per call; `value` is the field's value, or the item for `append`/`remove`) | `PUT` returns `{"updated": true, "id": "<id>", "warnings": [], "ignored": [], "unchanged": [], "changed": true, "locked": true, "not_moved": null}`. `ignored` is always `[]`: unknown top-level keys are refused with HTTP 400 `VALIDATION_FAILED` before the echo comparison. ADR-0034 transport keys `body_truncated`, `body_length`, `body_offset` and `body_chunk_size` are accepted and never persisted; a truthy truncation marker on a body write is still refused. Accepted fields sent at their current read value can appear in `unchanged` (#561). `PATCH` returns the same shape with both lists always `[]`, and also refuses unknown body keys with HTTP 400 `VALIDATION_FAILED` (#1221). |

<!-- mcp-output: kb_update -->
```json
{"updated": true, "entry_id": "hello", "file_path": "/abs/path/hello.md", "locked": false}
```

`update -f key=value` (CLI) and MCP `kb_update` with an extra key write an
undeclared field as a top-level key where the request names it (or under
`metadata:` when the file already holds it there). Which fields an update
may set, and which it refuses or sets aside, is the **Update fields**
paragraph above; how it writes, and the `changed`, `locked` and
`not_moved` fields of the result, is **How an update writes**.

### `bulk_create` / import

| Surface | Call | Success shape |
|---|---|---|
| CLI | `pyrite import <file> -k <kb>` | Rich text: one `Created: <id>` / `Failed [CODE]: <title>: <message>` line per record, then `Imported N entries` (or `Imported N entries (M failed)`). Exit `3` if some records were written and some failed, `1` if none was written. Its `--format` selects the *input* file format. |
| MCP | `kb_bulk_create` | the block below, results in input order. |
| REST (not run) | `POST /api/entries/import` (multipart) | `{"imported": N, "errors": M, "entries": [{"id", "title"}, ...], "error_details": [{"title", "error", "error_code", "errors?"}, ...]}`. The top-level `errors` is a count, so a record's rows ride on its `error_details` item. |

`--format` selects JSON, YAML or Markdown input. Without it, `.json`, `.yaml`/`.yml` and `.md` file extensions are detected; `--stream` applies only to Markdown imports.

<!-- mcp-output: kb_bulk_create -->
```json
{"total": 2, "created": 1, "failed": 1, "results": [{"created": false, "error": "...", "error_code": "ENTRY_EXISTS"}, {"created": true, "entry_id": "two"}]}
```

A failed record carries `errors` beside `error_code` when the refusal named a
field, in the row shape above.

### `task create` and `link`

`pyrite task create` takes `--field key=value` (repeatable) for fields a
KB's task schema allows. It has no short flag: `-f` was `--format` on the
`task` commands and is deprecated there (see "`--format` defaults"), and is
**not** `--field` as on `create`/`update`.

`pyrite task create` also takes `--due-date <YYYY-MM-DD>` (`-d`) and
`--link <target[:relation]>` (`-l`, repeatable). With `--link`, the JSON
document carries `"linked"` and `"failed_links"` (each failed link carries its
`finish_up` command); a link that did not resolve makes the outcome `partial`
and the exit `3`, while the created task itself is kept. MCP `task_create`
accepts optional `due_date` and `links` (`[{"target": ..., "relation": ...}]`).

`pyrite link <source> <target> -k <kb> -r <relation>` answers
`{"links": [{"created": true, "source", "relation", "target", "target_kb", "resolved"}], "outcome": "done"}`
(`target_kb` is the KB the link points into: the one name for it across the link family, here and in `links bulk-create`; a bare `kb` is not a key of these payloads)
(`--bidi` adds the inverse as a second item; one that failed is in `"failed"`,
with `"outcome": "partial"` and exit `3`); under
`--format rich` it prints
`Linked: <src> --[<relation>]--> <tgt> (in <kb>)` for a new link, or
`Already linked: ...` when that `(target, kb, relation)` already exists
(`"created": false`). When the target does not exist yet (`"resolved": false`),
the line ends with `— unresolved: the target does not exist yet`.
Another relation between the same two entries is a new link.

`pyrite links check` is a check (ADR-0046): it exits `0` with `"outcome": "done"`
when no link is broken, and `1` with `"outcome": "nothing"` when any is, with
the findings still in the document (`missing_targets`, `total_references`,
`targets`, each target with its `references`).

## Link discovery: what is left out

`pyrite links discover`, `pyrite links batch-suggest`, MCP `kb_discover_neighbors`
and `kb_batch_suggest`, and REST `GET /api/links/discover-neighbors` and
`GET /api/links/batch-suggest` leave out candidate entries whose body and
summary are both blank (nothing but space, tab, CR or LF; a summary without a
body is kept) (#200). The exclusion is a predicate in the search query, so
`limit` results come back when that many qualify. It applies to candidates
only: an entry with no body still gets suggestions. `kb_search` and
`pyrite search` hide nothing.

| Surface | Switch |
|---|---|
| CLI | `--include-empty`; the JSON document carries `"include_empty": true\|false` |
| MCP | `include_empty` (boolean, default `false`); a value that is not a boolean answers `INVALID_PARAMETER` |
| REST | `include_empty` query parameter (default `false`) |

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
| `INVALID_REQUEST` | the URL or branch was rejected before git ran, or the clone path would not resolve inside the workspace root |
| `REMOTE_NOT_ALLOWED` | the URL git would contact is not `https://` on a host in the `git_allowed_hosts` setting (also when a git `insteadOf` rule rewrites it to one); refused before git ran, and the message names the setting |
| `REMOTE_REDIRECTED` | the remote answered with an HTTP redirect, which Pyrite does not follow; use the repository's current URL |
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

An unknown search `--mode` is refused with `VALIDATION_FAILED`: CLI exits
1 in every requested format, MCP reports the code, and REST returns 400.
Choose a supported mode rather than retrying the same request.

## Search result envelope

```json
{
  "query": "the search string",
  "count": 3,
  "results": [ { "id": "e1", "kb_name": "notes", "entry_type": "note", "title": "...", "snippet": "...", "similarity": 0.412 } ],
  "match": "weak",
  "match_basis": "keyword: nothing matched; semantic: best similarity 0.41, below 0.50 but 0.19 above the next 9 results",
  "legs_run": ["keyword", "semantic"],
  "keyword_match": "none"
}
```

### What a result row carries: the lean default (CLI only)

`pyrite search` and `pyrite-read search` return, per result, `id`, `kb_name`,
`entry_type`, `title`, `snippet` and the score key the mode produced: `rank`
(keyword), `similarity` (semantic), `rank`, `rrf_score` and `similarity`
(hybrid; a row only one leg returned has only that leg's) and, in hybrid, the
per-result `legs`. `distance` is not in the default. The page-level keys below
(`match`, `match_basis`, `legs_run`, `keyword_match`, `warnings`) are in every
response whatever the rows carry. A key that is not in the row is absent, not
`null`: reading it is a `KeyError`, never a wrong answer.

To get more: `--fields '*'` (or `PYRITE_SEARCH_FIELDS='*'`) is the whole row
as before #54 (`body` still needs `--include-body`), a named list
(`--fields title,date,status`) is those fields after `id` and `kb_name`, and
`PYRITE_SEARCH_FIELDS` sets the same list for every search in a shell.
`--fields` wins over the variable; an empty or blank value of either
(`--fields ''`, `--fields ','`) is "not given" and gives the lean row (before
#54 it gave the whole row). Names are case-sensitive and the variable is as
lenient as the flag: a name that no returned row carries and no search row is
known to have (`Title`, a typo) is skipped, and a warning naming it goes to
stderr. A name a row merely lacks (`status` on an entry with none) is silent, and an empty result page gives no warning.
`--fields '*'` means every field on every CLI command that takes `--fields`
(`search`, `get`, `list-entries`, `recent`, `batch-read`); on REST and MCP `*`
is not special. `search --files` ignores both and says so on stderr.
`--include-body` adds `body` to the default row. In CSV the
columns are the default row's keys that the page has; Markdown keeps its list.
MCP `kb_search` and REST `GET /api/search` are unchanged: they return the
whole row.

### What the page is worth: `match`, `match_basis`, `keyword_match`, `similarity`

Every search response, on the CLI (`pyrite search`, `pyrite-read search`),
MCP `kb_search` and REST `GET /api/search`, says what its results are worth.
The three get these keys from one place (`SearchService.search_response`),
so they are the same keys, with the same values, in the same words.

| key | where | values |
|---|---|---|
| `match` | top level, always | `strong`, `weak` or `none` |
| `match_basis` | top level, always | the reason, in words; for a person or a log, not for parsing |
| `legs_run` | top level, always | the legs that ran: `["keyword"]`, `["semantic"]`, both, or `[]`. A `hybrid` search with `["keyword"]` here was a keyword search. Not the per-result `legs` |
| `keyword_match` | top level, when a keyword leg ran (`keyword` and `hybrid` modes) | `all_terms`, `some_terms` or `none` |
| `warnings` | top level, only when there are any | a leg that was skipped or dropped. Now in the CLI's JSON too; it was on stderr only |
| `similarity` | each result the vector leg returned | cosine similarity to the query, 3 decimal places, the same number on SQLite and Postgres |
| `legs` | each result, `hybrid` mode | the legs that returned that result: `keyword`, `semantic` or both |

A search for a term the KB does not have, run as this page's test:

```bash
pyrite search "zzznosuchterm" -k notes --mode keyword
```

<!-- output-keys -->
```json
{
  "query": "zzznosuchterm",
  "count": 0,
  "results": [],
  "match": "none",
  "match_basis": "keyword: nothing matched",
  "legs_run": ["keyword"],
  "keyword_match": "none"
}
```

How to read it: `match: none` means the KB does not contain an answer; say so,
do not build an answer from these results. `weak` means verify the entries
before using them. `weak` with no results means the search could not run in
full or found nothing strong, not that the KB lacks an answer: read
`match_basis` and `legs_run`, then try keyword mode or other words.
`similarity` compares results of the same model; it is not a probability.

- **The results are returned whatever the verdict.** A `none` page holds the
  nearest entries. The verdict is a hint to verify: on the labelled queries
  behind it, 2 of 52 answerable queries read `none` in semantic mode (a
  paraphrase far from the entry's own words), and 1 of 52 in hybrid.
- **The verdict belongs to the query, not the page.** It is the same for every
  `limit` and `offset`, and a `fields` projection does not remove it.
- **`none` is only said with the evidence for it:** the mode searched as it
  promises and nothing answered. If the semantic leg did not run (the
  `[semantic]` extra is not installed, nothing in the KBs searched is embedded,
  the backend cannot filter it), if the embedding model has no thresholds, or
  if the vector index could not reach every entry in scope, the verdict is
  `weak` at worst, `match_basis` says which, and `legs_run` shows which legs ran.
  A `weak` page can be empty. One thing `none` does not account for: entries
  written since the last `pyrite index embed` and still in the embed queue are
  not seen by the semantic leg (`pyrite index embed-status`).
- **It is computed only from entries you may read:** the rows of both legs,
  whether anything is embedded, and the warning are all asked of the readable
  KBs, and `match_basis` never quotes an entry outside them. The readable set
  is applied inside each leg's own query, on SQLite inside the vector search
  itself, so a scoped search answers as a search of the readable KBs alone
  would, however many nearer entries sit in KBs outside them. (sqlite-vec
  considers at most 4096 nearest vectors; they are the 4096 nearest readable
  ones.)
- **Keyword:** `strong` when every term matched; `weak` when only some did
  (the OR-relaxed retry, an `OR` you wrote, or a hit only on `expand`'s terms);
  `none` with no hits. In `hybrid`, beside a semantic leg that ran, the
  keyword leg does not relax, so `keyword_match: none` there means no entry
  has every term.
- **Semantic:** decided by thresholds measured for the embedding model
  (`all-MiniLM-L6-v2`: `strong` at 0.50 and above; `none` below 0.35, or
  between the two when the best hit is less than 0.10 above the mean of the
  next nine results). The second test needs neighbours: in a KB of a few dozen
  entries an absent but adjacent topic can read `weak`.
- **Another embedding model** has no thresholds yet. Results still carry
  `similarity`; `match_basis` says `uncalibrated model <name>`, and `match` is
  `strong` only when the keyword leg says so, `weak` when the vector leg
  returned rows nobody can judge.
- **Hybrid:** `strong` if either leg is strong; `none` if the semantic leg says
  none and the keyword leg is not strong; `weak` otherwise. When the semantic
  leg did not run, the search is a keyword search: it retries with the terms
  OR-combined as `keyword` mode does, `legs_run` is `["keyword"]`, `warnings` says
  why, and the verdict is `strong` or `weak`, never `none`.

Ordering fields are not scores. `rank` is a raw BM25 value and `rrf_score` is
rank arithmetic (a few fixed values, whatever the query); neither compares
across queries. `distance` is **backend-specific**: sqlite-vec's L2 distance on
SQLite, pgvector's cosine distance on Postgres. It is removed in 0.27; read `similarity`
instead (see [Deprecated, still accepted](#deprecated-still-accepted)). On SQLite, candidates are the
L2-nearest vectors, then ordered by cosine: with a model that does not
normalise its output, a long vector with a high cosine can be missed (the
default model normalises). REST results carry
`similarity` and `legs` and none of the ordering fields.

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
`pyrite/services/search_service.py` (`search_response`, `match_verdict`, and
`SearchService.search`'s docstring, the one statement of the search contract);
`pyrite/server/endpoints/search.py` (`search`), `pyrite/server/endpoints/entries.py`
(`list_entries`) and `pyrite/server/endpoints/tags.py` (`get_tags`) for REST;
`pyrite/cli/browse_commands.py` for the CLI commands' own JSON assembly.

## Entry envelope

A single entry (`kb_get`, `pyrite get`) returns the entry dict directly
— not wrapped in an `{"entry": ...}` key. `outlinks` and `backlinks` are
included when resolvable.

A single entry is read from its file as the file is now, so an edit made
since the last `index sync` is in it; `content_hash` is the SHA-256 of the
bytes that read. Lists and search are index reads and may lag.

`"content_hash": null` means one of two things. On a single-entry read it
means the entry has no file (a row only). On a list result it means the
hash was not computed: `GET /api/entries`, `GET /api/collections/{id}/entries`,
`POST /api/collections/query-preview` and the daily-note routes when they
answer from the index build their entries from index rows, which carry no
hash. Read the entry by id for its hash.

### `load_problems` and `ENTRY_UNREADABLE`: a file its type cannot load

A file *does not load as its type* when its frontmatter parses to a mapping
and the declared type's loader refuses it: a value it will not convert (a
`backlog_item` with `rank: .inf`), a shape it will not hold (a `task` whose
`metadata:` is not a mapping), or a plugin loader's own check (ADR-0042
decision 8a). Pyrite reads such a file partially and writes it not at all.

**Reads.** `kb_get` (the entry dict), `pyrite get`, `pyrite-read get` and REST
`GET /api/entries/{id}` return the entry as the type it declares, with a
`load_problems` list. The key is present only then.

```json
{
  "id": "x", "entry_type": "backlog_item", "title": "T",
  "load_problems": [
    {"field": "rank", "rule": "load", "severity": "warning",
     "message": "rank: cannot convert float infinity to integer"}
  ]
}
```

- **A refused field is absent, never a default.** `rank` is not in the reply
  (not `0`), and a refused `status` is not `open`. REST leaves a refused
  column out of the reply too (`metadata` is absent, not `{}` or `null`, when
  the whole `metadata:` was refused; likewise `tags`, `participants` and
  `sources`). GET and POST `/api/daily/{date}` follow the same rule for an
  existing note (#1490). A list is never `null`: `tags`, `participants`, `sources`,
  `outlinks` and `backlinks` are lists when present, and the ones `load_problems`
  does not name keep the value the read gave them or the empty list, so a
  client may read `.length` of any of them and must treat an absent one as
  "refused". `field` is the file's key; a
  value under `metadata:` is `metadata.rank`, and the field is then absent from
  the top level and from `metadata`.
- **A refusal no field explains** (a `title` that is a list, a key the loader
  requires) answers `ENTRY_UNREADABLE`, never "not found".
- Lists, search and counts have no row for the file; `pyrite index sync` and
  `pyrite index health` list it as malformed, with its path and the loader's
  message.

**Writes.** Every write to the file is refused with `ENTRY_UNREADABLE` (CLI
exit 1, REST 422, MCP `error_code`), and nothing is written: an update (REST
`PUT`/`PATCH`, MCP `kb_update`, CLI `update`), an echo of a read (it carries
`load_problems`, which is set aside, and is refused like any other update), a
delete, a link from it, a rename of it, a task claim, update, checkpoint or
reset, and a plugin's write through `ctx.kb_service`. A request that would
change nothing is refused the same way. No validator or hook is run, so none
judges a value the file does not hold. The message names the file by its path
relative to the KB, the declared type, the refused fields and the loader's
message. The loader's message is shown to the local process (the CLI, stdio
MCP); a network caller (REST, MCP over HTTP) gets `the server log has the
details` in its place unless the loader raised a Pyrite error, as for any text
Pyrite did not write (ADR-0052 N3): a type's loader is code (a plugin's, a
library's), not a parser given only the file's bytes. The same holds for each
`load_problems[].message`. Over the CLI:

```text
items/x.md does not load as its type `backlog_item` (refused: rank; cannot
convert float infinity to integer). Pyrite reads it but writes nothing to it
until the file is fixed by hand.
```

Repair is by hand (or by an explicit repair command, ADR-0042 decision 7);
there is no ordinary update that repairs. A rename of *another* entry leaves
such a file's `[[links]]` as they were and lists it under `files_skipped` with
this reason. `qa fix` records the file's fixes as failed and goes on; a bulk
link fails that source's items and goes on; `export site` names the ids it
left out.

### Backlink rows

Each row in `backlinks` (and in `pyrite backlinks`, MCP `kb_backlinks`) reads
from the entry's side, with three relation fields:

| Field | Reads as | No inverse declared (`informs`) | Declared after the row was indexed |
|---|---|---|---|
| `forward_relation` | what the source's file says, source → this entry, as written | the relation itself (`informs`) | the relation itself |
| `relation` | this entry's relation to the source (the inverse stored in the index when the link was indexed) | `related_to` | stays `related_to` until the source is reindexed |
| `inverse_relation` | the declared inverse of `forward_relation`, looked up when the query runs | `null` | the declared inverse (`informed_by`) |

`inverse_relation: null` means no inverse is declared for that relation. That
is true of an undeclared custom relation. Core-written `transclusion` (for
`![[...]]`) and `references` (object-reference fields) have the declared
inverses `transcluded_by` and `referenced_by`; those reverse names are declared
too. The legacy spelling `related` is an alias of `related_to`, with
`inverse_relation: related_to`. `forward_relation` still preserves the spelling
in the source. Existing stored rows are not rewritten: their `relation` can
remain `related_to` until reindexing, while `inverse_relation` already reports
the current declared inverse. A plugin declaration of the same name takes
precedence over a core declaration, as for other relationship types.

A stale row is the last column: a plugin declares `informs` after the link was
indexed. `relation` is a stored column and keeps `related_to`;
`inverse_relation` is computed per query and already gives the declared
inverse. When the two disagree, `inverse_relation` is the current one.

The table output (`pyrite-read backlinks`, `pyrite backlinks --format rich`)
shows `relation` as "Relation" and `forward_relation` as "Written as".

There is one row per link the source writes, not one per source (#1009). Two
links are one row only when they have the same target (as the text the index
stores an id as), the same resolved KB, the same relation and the same
`kb:` prefix as written. So a source that links an entry with `[[ops:x]]`
and `[[ops-kb:x]]`, or with a frontmatter `links:` item and a body `[[x]]`,
appears twice in that entry's `backlinks`, once per link; `[[x]]` written
twice, or `[[x]]` and `[[x#Heading]]`, is one row (the heading is not part of
the key). `![[x]]` is one row, relation `transclusion`.

The web panel and the `backlinks` table list each source once, with each of
its links' relation in the row; the graph draws one edge per pair of entries.

The row also carries `prefix`: the `kb:` prefix the source wrote, exactly as
written (`null` for a row indexed before #980). It is what tells `[[ops:x]]`
from `[[ops-kb:x]]` in a row, so a reader that merges rows keeps it in the
identity — the overlay backend merges the main and overlay stores and used to
collapse those two back into one (#1062).

A link's target is compared as the text the index stores an id as (#1022):
`target: true` in a file is the id `"1"`, so `pyrite link src 1`, MCP
`kb_link` with `target_id: "1"` and `links bulk-create` find it and write
nothing, while `target_id: "True"` names a different id and is a new link. The
KB is compared as it resolves (`kb: ops`, the shortname of `ops-kb`, is the
link `--target-kb ops-kb` asks for), whatever prefix the file wrote.

`outlinks` rows carry one field, `relation`, which is the forward relation.

### Resolved: does the target exist for this caller?

Every `outlink` and `backlink` row also carries `resolved: true|false` (#64):
whether the target exists *for this caller*, the same field `kb_link` and
`pyrite link --allow-dangling` report on write. A backlink row is never
unresolved -- the row exists because the source exists and the target is the
entry being read.

On an `outlink` whose `resolved` is `false`, the row also carries
`resolved_in_kbs`, a list of KBs (sorted) the target id exists in that this
caller can read: a dangling `[[widget]]` whose id lives in both `ops` and
`research` reads `"resolved_in_kbs": ["ops", "research"]`. A caller who can
read neither KB gets `[]` -- an unreadable copy is indistinguishable from a
missing one (ADR-0037, section 4). A `resolved: true` row carries no
`resolved_in_kbs`.

`pyrite link --allow-dangling --bidi` to a missing target writes the forward
link and cannot write the inverse on an entry that does not exist: the run
ends `partial` (exit 3) with the inverse in `failed`.

`resolved` is computed when the link is read, so a forward reference written
with `--allow-dangling` flips to `true` the moment its target is created, with
no rewrite of the link itself.

Through the multi-user overlay (experimental) the answer is the overlay
reader's: a target held by either the shared index or the user's pending edits
resolves a link held by the other, and `resolved_in_kbs` names the readable KBs
holding the id in either. A row's `title` is null exactly when `resolved` is
`false`. A target that a change set created and then deleted is unresolved
again; a target held by main and deleted in a change set still resolves, as
`get_entry` still finds it (the overlay has no tombstones yet).

## Entry row shape

These readers return entry rows from the index in one shape, whatever the
surface (MCP, `--format json`, REST) and the backend (SQLite, Postgres): the
list and find tools (`kb_list_entries`, `kb_recent`, `kb_batch_read`,
`kb_find_by_status`, `kb_find_by_assignee`, `kb_find_overdue`,
`kb_find_by_location`), search (`kb_search`, keyword and semantic legs),
folder collections, and the single read (`kb_get`). Each reader keeps its
own set of keys: shaping adds none. Where a row carries one of these, it has
this type:

| Key | JSON type |
|---|---|
| `metadata` | an object; `{}` when the entry has none |
| `aliases` | a list; `[]` when the entry has none |
| `importance` | an integer (or `null`) |
| a field the type declares, held in the `metadata` bag (a backlog item's `rank`) | at its own name too on the list readers (`kb_list_entries`, `kb_recent`, `kb_batch_read`) and the single read; search and the finds return only the columns they select, so there it is in `metadata` alone (the keyword search row's `rank` is its score) |
| a column field (`priority`, `status`, `assignee`, `due_date` ...) | by the type's declaration (`KBSchema.type_fields`: kb.yaml, plugins, and a task's own `priority`, a number); see below |

How a column field reads. A field declared a `number` reads as a number when
the stored text is a plain ASCII whole number or decimal, with an optional
leading minus (`7`, `2.5`, `-7`, `-1.5`): no plus sign, no surrounding space,
no leading zero, no exponent, no digits from other scripts. Any other text
(`"007"`, `"+7"`, `"-0"`, `"1e3"`, `high`) reads as that text. A task's `priority` is declared a number, and its column holds
the task model's integer (`coerce_task_priority`: a word on the 1 to 10
scale, any other non-integer the default 5), so every spelling of it reads
as that integer and `task_list` and `kb_get` agree. A field declared a
`checkbox` reads the stored `1`, `0`, `true` or `false` as a boolean. Every
other field, and one the type does not declare, reads as text. A
whole-valued decimal (`7.0`) reads as `7.0` on SQLite and as `7` on
Postgres.

What a caller reads, sent back as that one field (MCP `kb_update`,
`pyrite update -f`, REST PATCH), leaves the file as it was, except in
these cases. REST PUT takes no column field, so it has none. A read never
writes anything.

- A quoted plain number in a field declared a number: `priority: "7"`
  reads 7 (and `"-7"` reads -7), and writing that number back stores it
  without the quotes (`priority: 7`). `qa validate` reports the text (`number_as_text`), saying what
  Pyrite reads it as.
- A task's `priority` spelled as anything but a plain whole number
  (`high`, `P1`, `"007"`, `"+7"`, `"1e1"`): it reads as the model's integer
  (7, 5, 7, 7, 10), and writing that back stores the integer in place of
  the file's spelling. `qa validate` reports the file's value
  (`task_priority`, or `number_as_text` for numeric text).
- A quoted `"true"`, `"false"`, `"1"` or `"0"` in a field declared a
  checkbox: it reads as a boolean, and writing that back stores `true` or
  `false`.
- An unquoted `1` or `0` in a field declared a checkbox: it reads as a
  boolean, and writing that back stores `true` or `false` (on earlier
  releases it was stored as the quoted text `"1"` or `"0"`).
- A YAML boolean in a field declared a number: `priority: true` reads 1
  (and `false` reads 0), and writing that back stores the number
  (`priority: 1`; earlier releases stored the quoted text `"1"`).
- A number the index spells with an exponent, in a field declared a number:
  `priority: 0.00001` reads as the index's text for it, which the strict
  rule keeps as text, and writing that back stores that text quoted. The
  spelling depends on the reader: `kb_get` returns `"1e-05"` and the list
  readers (`kb_list_entries`) `"1.0e-05"`, so a write-back stores
  `priority: "1e-05"` or `priority: "1.0e-05"`. Very large or very small
  numbers (`1.5e300`, `100000000000000000.0`) take the same path.

`kb_timeline` returns `date` and `location` as stored text: its rows carry
no `entry_type`, so no declaration applies. The type-specific tools
(`task_*`, `sw_*` and the other plugin tools) return their own dicts, not
index rows. (#1503)

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

### `batch-read` and a KB it cannot resolve

The surfaces differ, on purpose. The CLI `pyrite batch-read my-kb:source
nokb:x` refuses the whole call with `KB_NOT_FOUND` (exit `1`) and returns no
entry. MCP `kb_batch_read` and REST `POST /api/entries/batch` name the KB
inside each `entries` item, so they return the entries that exist and list a
pair whose KB the caller cannot read, or that does not exist, under
`not_found` with the entries that are simply missing: an error would reveal
that the KB exists (`pyrite/server/mcp_server.py`, `_kb_batch_read`).

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

**One point sets them** (ADR-0046). A command on the outcome contract
returns what happened -- `done`, `partial` or `nothing` -- or raises a
refusal; the root group every command runs inside (`PyriteCLIGroup`, in
`pyrite`, `pyrite-admin` and `pyrite-read`) prints it in the requested
`--format` and exits `0`, `3` or `1`. Its JSON carries an additive `outcome`
key: `"done"`, `"partial"`, `"nothing"`, or `"error"` on the error shape of a
refusal, so a caller that cannot see the exit code can still tell. Every
result is an object, so the key always has somewhere to go.

A command is on the contract when it declares the shared `--format` option.
The commands that are not yet are migrated one slice at a time and keep the
behaviour below until they are; they are named in `tests/legacy_commands/`
(one file per CLI module), which a test keeps equal to the registry. Every
command on the contract shows `PYRITE_FORMAT` on its `--format` in `--help`,
but the sign does not run the other way: `pyrite mcp-setup` has its own
`--format` honouring `PYRITE_FORMAT` and is still legacy. For a command on the
contract the root is also the only way *out*, checked on every run: once the
command's own code has started, anything but a returned outcome or a raised
refusal is answered `NO_OUTCOME`, exit `1`, in the format you asked for. That
covers an exit of its own with any code, a crash (one line naming the
exception; `-vv` shows the traceback), a return of anything else, and any
byte it wrote to stdout itself, which is moved to stderr. So for these
commands, in a machine format:

- stdout holds exactly one document, and its `outcome` agrees with the exit
  code;
- `NO_OUTCOME` means a bug in the command, not in your request: what it did
  is unknown, so check before you retry;
- exit `0` with empty stdout is never an answer. If you see it, the process
  ended without reaching the root (`os._exit`); treat it as a failure.

What the root does not decide: stderr (progress, warnings and deprecation
notices go there; do not parse it for failures), the words of `--format rich`
(a renderer writes for a person; the exit code is still the root's), and
`--help` (exit `0`, human text), which click answers before the command
starts. A usage error (`2`) is answered by the root too, as `USAGE_ERROR`
(below). `-f` together with `--format` is a usage error. Ctrl-C exits
`130`; Ctrl-C or end of input at a prompt is `Aborted!` on stderr and exit
`1`, with no document.

**Check commands** (a command whose answer is a verdict: `kb validate`, `ids
missing`, `index health`, `qa validate`, `protocol check`) exit `0` when the
check is clean and `1` when it fails; warnings never change the exit code
(maintainer, 2026-10-08). A check command adopts this when its module
migrates; until then `kb validate` drift exits `2` and `ids missing` `3`.

- `0` — success: **every effect you asked for happened.** A command that
  prints a failure and exits `0` is a bug.
- `1` — refused, or nothing was done: any error surfaced via `cli_error`
  (parses the JSON error shape above from stdout/stderr depending on
  `--format`). A subject that does not exist is a not-found error here, in the
  command's own format, never an empty result (`pyrite backlinks nope` answers
  `ENTRY_NOT_FOUND` today, the class's code; the spelling is #610's). Also a
  batch command that did none of its items (below).
- `2` — usage: an unknown option, a missing argument, a bad choice, an
  unknown command, an unknown `--format`, `-f` together with `--format`. It is
  a refusal like any other: one document on stdout in the format asked for
  (`--format` as typed, else `PYRITE_FORMAT`, else json), with `"outcome":
  "error"` and `error_code` `USAGE_ERROR` (`retryable: false`, a `suggestion`
  naming `--help`), on every command, on the contract or not. stderr carries
  one plain `Error: ...` line for a person, never a Rich box or an ANSI
  escape. Nothing depends on a terminal. The one difference from a refusal in
  the body is the exit code: `2`, not `1`. A `ClickException` that is not a
  usage error (one a command raises) is the same document with `error_code`
  `ERROR` and its own exit code, `1`.
  Two cases are not documents. A bare `pyrite`, `pyrite-admin` or `pyrite-read`
  prints its help and exits `2`, as before (it is help, not an error). And a
  command that is not on the contract and has already printed to stdout when
  it raises the error keeps stdout to its own text: the error is the one
  `Error:` line on stderr, with the same exit code. The root sees writes made
  through `sys.stdout`; descriptor 1 written directly, or a stream a module
  took at import, it does not see. When the format cannot be read from the
  arguments (`pyrite export`, where `--format` means `--target`), the document
  is in `PYRITE_FORMAT`, else JSON.
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
    `export site` (done = entries exported, a row that could not be loaded is
    named and counted as left out), `search --files`, `repo sync`, `batch-read`
    (an id not found), `task decompose`
    (every child refused is `1`, `"outcome": "nothing"`),
    `sw prioritize`, `sw migrate-standards`, `investigation bulk-edges` and
    `ftm-import`;
  - `pyrite qa duplicates` that left a KB out of the scan and named it in
    `unreadable_kbs` (#1547);
  - `pyrite index sync`, `pyrite index build` and `pyrite kb reindex`
    count each KB, folder and file they could not reach as failed (done =
    KBs synced): a KB with one folder it could not list exits `3`, and so
    does `kb reindex` of that KB (#1546; see "A folder or file Pyrite cannot
    reach");
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

### Usage errors raised after parsing

A shared-format command whose body raises a Click/Typer usage error violates
the Outcome contract: the root reports `NO_OUTCOME`, exit 1. A parse-time usage
error is `USAGE_ERROR`, exit 2. This preserves the existing contract rather than
turning a command implementation bug into a caller's parse error. Text or bytes
printed by a contract command before raising are moved to stderr and the result
is still one `NO_OUTCOME` document. Non-contract commands that already wrote
through `sys.stdout` or `sys.stdout.buffer` keep that text on stdout; a subsequent
Click error is a plain stderr diagnostic, with no appended document. Writes
through an independently retained stream or descriptor 1 remain outside this
non-contract counting guard.

### CLI root boundaries (ADR-0046, slice 1)

Shared-format commands require a `PyriteCLIGroup` root; invoking a sub-app
under an ordinary Typer root refuses before its body. The guard drains root
close callbacks before publishing the answer. Cleanup that prints or exits
is `NO_OUTCOME`, rather than a second document or a replacement exit code.
When the body printed and then raised a refusal, stdout remains the one
`NO_OUTCOME` document and stderr retains both the printed text and the refusal.

Legacy plugin commands that return `Outcome` without `OUTPUT_FORMAT` remain
outside the shared-format guarantee: their private `--format` does not select
the renderer, and unsupported `PYRITE_FORMAT` falls back to JSON. A shared
option instead rejects unsupported values as usage errors (exit 2). Plugins
that need the guarantee declare the shared option and mount under the Pyrite
root. See the root's docstring for the other process-level limits.


## `--format` defaults

The rule (#303, decided 2026-10-02) is one shared `--format` option
(`pyrite/cli/output.py`): `json` by default, `PYRITE_FORMAT` sets your own
default (`PYRITE_FORMAT=rich` in a terminal), an explicit `--format`
overrides it, and an unknown format is a usage error (`2`, a `USAGE_ERROR` document). It takes `json`,
`yaml` and `rich`. `markdown` and `csv` are not offered by the shared option:
they exist for the shapes the REST API serves (entries, search results) and
print a Python `repr` for anything else. A refusal is printed in the format
asked for, YAML included. `-f` no longer
means `--format`: where it did it remains accepted until 0.27
and prints a deprecation line on stderr (see
[Deprecated, still accepted](#deprecated-still-accepted)). Each module takes the
shared option when it moves to the outcome contract; the commands not named in
`tests/legacy_commands/` have it. Until then most *read* commands default to `--format json`, `config`
to a rich terminal view, and the write commands are not uniform. The table is
checked against each command's Typer signature by
`tests/test_doc_agent_contracts.py`:

| Command | `--format` | Default |
|---|---|---|
| `create` | yes (shared) | `json` |
| `add` | yes (shared) | `json` |
| `delete` | yes (own option) | `rich` |
| `link` | yes (shared) | `json` |
| `update` | yes (shared) | `json` |
| `rename` | yes (shared) | `json` |
| `import` | input file format, not output | Rich text, always |
| `task create` | yes (shared; `-f` deprecated) | `json` |

A command with no `--format` prints its refusals as Rich text too, so branch
on the exit code, not on JSON, until its module takes the shared option.
`get` and `delete` do not have the shared option yet: `get` keeps
`--format markdown` and `csv`, and `delete` asks a question, which a command on
the contract may not (the prompt rule is still open). On `create`, `update` and `links
bulk-create` the short `-f` is `--field`/`--file`, never `--format`.

### Explicit fields and output formats

Who accepts `fields`, and what each does per format
(`tests/test_fields_acceptors.py` lists them and fails on one that is not
classified):

| Acceptor | CSV, Markdown, Rich | JSON, YAML |
|---|---|---|
| CLI `search`, `recent`, `list-entries`, `batch-read`, `get` (`--fields`) | projected | projected |
| REST `GET /api/search?fields=`, `GET /api/entries/{id}?fields=` (Accept `text/csv`, `text/markdown`, `text/yaml`; YAML is in the JSON column) | projected | projected |
| REST `POST /api/entries/batch`, body key `fields` (a JSON list, cleaned like `?fields=`: spaces and duplicates dropped; `[]` is no projection; a list that cleans to nothing, such as `[""]`, keeps `id` and `kb_name` only; `["a,b"]` is one field named `a,b`, unlike the query-string form, which splits on commas) | projected | projected |
| CLI `search --files`, and the file-search fallback in Rich mode | not projected: it prints its own Rich table (KB, Type, Title, ID) whatever `--format` and `--fields` say, and says so on stderr | same |
| REST `GET /api/entries` | takes no `fields` (the CLI `list-entries` does); the legacy columns, whatever the parameter | same |
| MCP tools | JSON only | projected |

The identity fields `id`, then `kb_name`, are always retained, followed by
requested fields in the supplied order, with duplicates removed. JSON and
YAML omit requested keys absent from a record rather than inventing values.

CSV, Markdown, and Rich retain every requested column, even when all records
lack that field; absent and null values are empty cells. A single entry
(`get`, `GET /api/entries/{id}`) is one row. CSV encodes list and
dictionary cells as JSON; ordinary strings, including snippets with quotes,
newlines, or `<mark>` tags, are preserved through standard CSV quoting.
Markdown uses an ordered table (header rule `| --- |`), escaping pipes and
displaying cell line breaks as `<br>`.

Two Markdown shapes change under `--fields`. A single entry
(`get X --fields title,body --format markdown`, or `GET /api/entries/{id}?fields=...`
with `Accept: text/markdown`) used to be a frontmatter block plus the body; it is now a
one-row table with the body flattened onto one line. And an empty result used to be the
line `# Search: q (0 results)`; under a projection it is an empty string. Without
`--fields`, each format keeps its existing rendering.

Rich renders field names and values literally, without interpreting markup. It
is for people: its width is the console's. Rich takes it from the terminal even
when stdout is piped (stdin or stderr being a terminal is enough), uses 80 when no
standard stream is a terminal, and lets `COLUMNS` override both. Scripts should
use `json`, `yaml` or `csv`. At that width
it prints a table when the `id` column at its widest id, every header, and the
widest character of each column fit whole (measured in terminal cells, so a
wide character counts 2); long values fold onto further lines. When they do not
fit, it prints each record as `field: value` lines, records separated by a blank
line, so a value that itself contains a blank line can look like a record
boundary. Nothing is cut with an ellipsis, and from a console width of 2 no
value is cut; at width 1 a stacked value made of wide characters is dropped,
since a wide character needs 2 cells. A projection never changes the
underlying records. What a command returns when no projection is requested is
that command's default: the whole record everywhere except `search`, whose
default row is the lean one above (#54).

Empty projected CSV responses retain their column header and contain no data
records. Rich retains each command's existing no-results message. JSON and
YAML retain their existing empty envelope.

For example, `pyrite search needle --fields status,title --format csv`
returns the columns `id,kb_name,status,title`, including an empty `status`
cell if a hit has no status. A requested field does not make search acquire
data its result records do not otherwise carry.

## A KB whose files cannot be read

When a registered KB's folder cannot be listed or searched, or its `kb.yaml`
cannot be opened, every command on every other KB still works, and that KB is
named rather than dropped:

- `pyrite kb list --format json` gives its row an `unreadable` key holding
  the fixed `KB_UNREADABLE` message, and `error_code`; a readable KB's row
  has neither key.
  REST `GET /api/kbs` and MCP `kb_list` do not carry it.
- `pyrite index health` lists it in `unreadable_kbs` (`{kb, error}`), and the
  status is `unhealthy`.
- `pyrite index sync` (every KB) does not sync it and keeps its index rows;
  it prints `Not synced: <kb>: <message>` and exits 3 when other KBs synced
  (1 when none did). A sync that runs in the foreground (REST
  `POST /api/index/sync` with `wait=true`, MCP `kb_index_sync` without
  `background`) names it the same way, in the result's `unreadable_kbs` list.
  A background sync job keeps the rows too, and its job result does not yet
  name the KB; `pyrite index health` does. A sync, rebuild or reindex of that
  one KB answers `KB_UNREADABLE` and changes nothing. `pyrite index build`
  skips it, names it, and exits 3 or 1 the same way.
- Every operation that needs its files answers `KB_UNREADABLE`.

A registered KB whose `kb.yaml` can be read but cannot be loaded as a mapping
(not UTF-8, a YAML syntax error, a duplicate key, over the YAML limits, a top
level that is neither a mapping nor null; #1547) is handled the same way, whether it is in
`config.yaml` or was added with `pyrite kb add`, with its own code,
`KB_CONFIG_INVALID`, and three differences: the fix is to correct the file, not
its permissions; `pyrite kb list` gives its row `error_code` and `error_detail`
(the parser's words: `kb list` is the local CLI only) beside `unreadable`; and
`pyrite index health` names it in `schema_errors` with `error_code`, not in
`unreadable_kbs`, and is `unhealthy`. Each `unreadable_kbs` row of a sync
result, and each `kb list` row with `unreadable`, carries `error_code`
(`KB_UNREADABLE` or `KB_CONFIG_INVALID`). Pyrite never rewrites the file:
`pyrite kb schema add-type`, `remove-type` and `set` answer
`KB_CONFIG_INVALID` before they write.

What `kb.yaml` is decides, at the moment of the read, in every process:

- **A running server.** A process that loaded its config while `kb.yaml` was
  good answers `KB_CONFIG_INVALID` as soon as the file breaks, and answers
  normally as soon as it is repaired: the schema is cached only while
  `kb.yaml` is the file it was read from (its inode, size and times). Index
  health, `unreadable`, sync and every operation on the KB read the file as
  it is then. An atomic replace (a new inode) is always seen. Two in-place
  writes of the same size within one step of the filesystem's timestamp
  clock (on Linux that step can be a few milliseconds) leave the same
  identity, so the second is not seen until the next change.
- **Kinds of path.** A directory, a FIFO, nesting deeper than the
  interpreter's recursion limit (flow or block style), or anything else the
  parser raises on, answers `KB_CONFIG_INVALID`. A socket answers
  `KB_UNREADABLE` (the operating system refuses to open it). A link out of
  the KB (to another KB's `kb.yaml` or anywhere else) or a `kb.yaml` with a
  second hard link answers `OUTSIDE_KB_ROOT`, and is handled like the other
  two codes: index sync, build and reindex keep its rows, `kb list` marks it,
  index health names it in `schema_errors` and is unhealthy. A dangling link
  or a link loop is no `kb.yaml` (a KB with no schema), and so is a null
  document. Every other KB still works in each case.
- **Commands that span KBs.** For any of the three codes, `pyrite qa
  duplicates` (with or without `--status`) leaves the KB out, names it in
  `unreadable_kbs` (`{kb, error, error_code}`, present only when a KB was
  left out) and exits 3. MCP `list_edge_types` without `kb_name` names it the
  same way; with `kb_name` naming it, it answers that KB's refusal. Index
  sync and build, index health, `kb list`, `qa validate` and MCP
  `kb_qa_validate` name it and go on; MCP `kb_qa_status` counts it under
  `kb_unreadable`.
- **One message.** `KB_CONFIG_INVALID`'s message is fixed wherever it is
  printed, `pyrite-read` and `pyrite-admin` included. The parser's words are
  in that process's own log. For a KB registered in `config.yaml`, `pyrite`,
  `pyrite-read`, `pyrite-admin` and `pyrite-server` each write one record at
  config load, at their default log level, holding the fixed text and the
  parser's words (one record; the parser's message runs over several
  lines). A KB added with `pyrite kb add` is not logged at config load: the
  record is written when a command reads that KB's schema, so a command that
  does not read it logs nothing about it, and `pyrite kb list` logs the fixed
  text alone. A process whose
  `kb.yaml` breaks after it loaded logs the same record once, at the first
  read that finds it broken. On the local CLI the parser's words are also in
  `pyrite kb list`'s `error_detail` and in `pyrite index health`. No REST or
  MCP response carries them.
- **Limit: a `kb.yaml` that loads but is not a schema** (`types: 3.5`,
  `types: [memo, 2]`): its own KB answers an untyped error, and `qa
  duplicates` and MCP `list_edge_types` stop for every KB. Since #1547 that
  includes `qa duplicates --status`, which did not read schemas before and so
  still answered beside such a file. Filed as #1591.
- **Limit: a very large `kb.yaml`.** There is no size bound: every command
  loads every `kb.yaml`, so a file of tens of megabytes slows every command
  (measured: 64 MB of one scalar takes about 24 seconds to load; two million
  short keys, more than a minute).

## A folder or file Pyrite cannot reach

The same rule one level down (#1546): a folder below a KB's root that the
index walk could not list, and a file it could not stat or read (an `OSError`
other than "absent"), say nothing about the entries in them, so nothing is
retired or called missing on their evidence. A file that was read and does
not parse still loses its row, as before.

- **The rows are kept.** Every reconcile (`pyrite index sync`, `index build`,
  `index build --with-attribution`, `kb reindex`, `pyrite init`, the
  background jobs, REST and MCP sync and reindex) keeps the row of every
  path under such a folder and of every such file, neither rewritten nor
  removed. Every row kept this way is named in that reconcile's own
  result (its folder under `unreadable_folders`, or the file under
  `unreadable_files`) and counts for its exit code. A row kept this way
  never wins its id over a file read now: the file read now takes the id's
  row, and the kept path is not a duplicate (its folder or file is still
  named). When the folder or file can be read again, the next sync reports
  `added`, `updated` and `removed` as `0` for them.
- **The rows are retired exactly when the files say so.** A file missing
  from a folder whose listing worked (a mode-`600` folder can be listed) is
  gone, and its row is retired, as before. So is a path that
  `KBFiles.present` answers absent: a link loop, or a file removed while the
  walk ran; a loop stays reported under `malformed` and in health's
  `unreadable_files`, as before, and does not count for the exit code.
- **What counts as a folder it could not list.** Only a folder the walk
  tried to list and could not (its listing failed, or failed part way), and
  a name whose own type the operating system will not give (in a folder
  Pyrite may not search, on a filesystem that does not say a name's type),
  whatever its name; when that name is really a file (`*.md`,
  `__collection.yaml`), the file's own stat also names it under
  `unreadable_files`. A symbolic link the OS reports as one is never one: the walk
  never enters a link, so a link whose target cannot be reached (a link to
  itself, a link into a folder Pyrite may not search, a link through a
  file) is not named, and a sync that meets one still exits `0` and health
  stays healthy, as before.
- **What a reconcile reports.** Its result carries `unreadable_folders`,
  `{kb, path, error}` (`path` KB-relative, `error` the fixed `KB_UNREADABLE`
  message), and `unreadable_files`, `{kb, path, error}` (the absolute path
  and the exception, as in `index health`); each such file is also under
  `malformed`, as before. `pyrite index sync` prints `Unreadable folders: N`
  with each folder and lists the files under `Malformed`; `pyrite index
  build` prints both lists; `pyrite kb reindex --format json` carries both
  keys. Each counts as failed for `exit_unless_whole`: `3` when a KB synced,
  `1` when none did.
- **Over the network.** REST `POST /api/index/sync?wait=true`, REST `POST
  /api/kbs/{name}/reindex`, MCP `kb_index_sync`, MCP `kb_registry_reindex`,
  REST `GET /api/kbs/{name}/health` and MCP `kb_registry_health` carry
  `unreadable_folders` as a count (an integer), never a path and never the
  OS's words, and do not carry `unreadable_files` (following the
  maintainer's 2026-11-17 decision for `unlisted_entry_files`;
  `pyrite.services.index_health.network_shape` is the one place that decides
  it). Network health's `healthy` is `false`.
- **Health.** `pyrite index health` names each folder in
  `unreadable_folders` and each file in `unreadable_files`, and is
  `unhealthy`, exit `1`.
- **An in-KB link into such a folder** is reported as an unreadable file
  (under `malformed` and `unreadable_files`), never as a dangling link to a
  missing file.
- **Not reported:** a folder under a dot-prefixed folder or `_templates/` (the
  walk lists nothing there), and the KB's root, which is "A KB whose files
  cannot be read".
- **Limit:** only the outermost of nested folders it could not list can be
  named. A background sync job keeps the rows but does not yet name the
  folders in its job result (#1548); `pyrite index health` does.

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
    "shape": "{error, error_code, suggestion?, errors?, retryable}",
    "error": "human-readable message",
    "error_code": "machine-readable code, e.g. QUERY_SYNTAX, KB_NOT_FOUND",
    "suggestion": "optional fix hint, omitted when not applicable",
    "errors": "optional list, one row per refused field ({field, rule, message, ...}); absent when the refusal is not about a field",
    "retryable": "bool -- whether retrying the same request could succeed"
  },
  "search_quoting": "Special-char tokens (hyphens, dots, colons) are auto-quoted ONLY when the query has no AND/OR/NOT operator and no existing quote. Once you use an operator or a phrase quote, quote special-char tokens yourself (e.g. '\"family separation\" \"cross-link\"') or the query can fail with error_code QUERY_SYNTAX (deterministic, not retryable).",
  "task_claims": "Task claims are atomic; a lost race means the task is already claimed by someone else. On conflict, do NOT override the claim -- re-run the task list and pick a different item."
}
```

### Entry rename and file placement

Renaming an entry changes its id while preserving its filename and folder.
A subsequent update or delete resolves the new id in the existing file.
Updates preserve deliberate placement, including the KB root; a schema with
a templated subdirectory may still move the file when its folder fields change.

## QA results: only what ran

A QA result says a check passed, or a fix landed, only when that check ran or
that write returned (#795). This applies to `pyrite qa assess`, `pyrite ci`,
`pyrite qa fix` and MCP `kb_qa_assess`.

**Tiers.** QA runs tier 1 (structural and deterministic rubric checks) and
tier 2 (tier 1 plus the judgment-only rubric items, judged by the configured
model). Any other tier is refused with `INVALID_TIER` (on CLI `ci` and `qa assess`,
Typer refuses out-of-range values as `USAGE_ERROR`, exit 2). Tier 2 with no model
configured is refused with `AI_NOT_CONFIGURED`. Both refusals come before
anything is checked or written, on every surface. `ci` refuses them even when
no KB is configured.

The codes:

- `INVALID_TIER`: `--tier` / `tier` is not 1 or 2 (on the CLI's `ci` and `qa assess`
  an out-of-range `--tier` is a usage error (`USAGE_ERROR`, exit 2) before this
  check runs). `pyrite mcp --tier` uses the same code for an unknown server tier.
  Use 1 or 2.
- `AI_NOT_CONFIGURED`: tier 2 was asked for and no model is configured. REST's
  AI routes use the same code. Configure `ai_provider`, `ai_api_key` and
  `ai_model`, or use tier 1.
- `KB_UNREADABLE`: `qa assess` / `kb_qa_assess` on a KB whose folder is
  missing or cannot be read. The message names the KB and the reason, not
  the path. Restore the folder, or its permissions. (The same code answers
  every other operation on a KB whose files cannot be read; see "Server and
  account codes".)
- `EVALUATION_FAILED`: `qa assess --entry`, or `kb_qa_assess` with `entry_id`,
  and the model did not answer. The call raised, or the reply was empty, not
  JSON, not a JSON list, or did not judge every rubric item with a boolean
  `pass`. The message says which. Retry, or check the model's configuration.

**No record for a tier that did not run.** A refused or failed entry writes no
`qa_assessment` file. `tier: 2` with `qa_status: pass` means the model judged
every item and found nothing. A model finding (`llm_rubric_violation`) is a
`warning`, so it makes the record `warn`. Records written before this change
cannot be told apart: a `tier: 2` record from then may hold tier 1 checks only.

**`qa assess` on a whole KB** keeps going past a failed entry. It adds two
lists beside `results`:

```json
{
  "kb_name": "notes",
  "assessed": 2,
  "skipped": 0,
  "results": [{"assessment_id": "...", "target_entry": "a", "qa_status": "pass", "issues_found": 0}],
  "failed": [{"entry_id": "b", "error_code": "EVALUATION_FAILED", "reason": "the model's reply was not JSON"}],
  "tasks_failed": [{"entry_id": "c", "reason": "EntryExistsError: ..."}]
}
```

With `--create-tasks`, a single-entry result carries `task_failed: {entry_id,
reason}` when its task could not be created. On a KB run, these are listed in
`tasks_failed`. `--format csv` and `--format markdown` render only `results`,
so each entry not assessed (`not assessed: <id>: <code>: <reason>`) and each
task not created (`task not created: <id>: <reason>`) is also written to
stderr. The default (json) output carries both lists; `--format rich` prints them as tables. The exit code is still 0 when only some entries failed; ADR-0046
slice 6 settles partial results.

**`ci --tier 2`** runs the judgment rubric over every entry whose type has
judgment items (one model call per entry; `qa_assessment` entries are skipped).
A finding is an `llm_rubric_violation` warning and fails the gate at the
default `--severity warning`. An entry the model did not answer for is an
`evaluation_failed` **error**, so `"result"` is never `"pass"` for a tier that
did not run. `ci` resolves KBs as the other commands do, with KBs registered
in the index (`pyrite kb add`, or indexed before they left `config.yaml`)
merged in, so a named KB's schema, file and rubric checks read its real
configuration. `ci --kb <name>` for a KB that resolves to nothing is refused
with `KB_NOT_FOUND`; it used to validate nothing and pass. Plain `ci`, with
no `--kb`, now validates the DB-registered KBs too, not only those in
`config.yaml`.

**Nothing to validate.** A `ci` run that finds no KB, in `config.yaml` or
registered with `pyrite kb add`, did not read anything and so does not pass:
it exits 1 with `NO_KBS_TO_VALIDATE` (`retryable: false`), in the CLI error
shape in JSON and as one line of prose in `--format text`. It used to print
`"result": "pass"` and exit 0. Add a KB with `pyrite kb add`.

**An index that does not match the files.** The index-only checks read the
index, so a KB whose files the index does not have (added with `kb add` and
never indexed, or edited, added or deleted since) would pass having validated
nothing of them. `ci` and `qa validate` report one `index_out_of_date`
**error** for that KB, with the counts and the remedy, `pyrite index sync -k
<kb>`. An empty KB has no files to miss and may pass.

**Malformed files.** `ci`, `qa validate` and MCP `kb_qa_validate` report a
`malformed_frontmatter` error for each file that cannot be parsed, with its
KB-relative path and the parser's message. `kb validate` includes those rows
under `malformed_frontmatter` for each KB and sets `has_drift`, exiting 2.
`index health` keeps its existing warning verdict for malformed frontmatter.

**A KB whose folder cannot be read.** When a KB's folder is missing, is not a
folder, or cannot be read (its `kb.yaml` included), `ci` and `qa validate`
(KB, entry or all) report one `kb_unreadable` **error** for that KB. The
message gives the reason and says the file and schema checks did not run.
Those checks are skipped, the index-only checks still run, and the other KBs
are still validated. `qa assess` and `kb_qa_assess` refuse such a KB with
`KB_UNREADABLE` and write nothing; a missing folder is not recreated to hold a
record. In `--format text`
an entry the model did not answer for is printed as an `ERROR` line and the
result is `FAIL`.

A model reply that is a list but does not answer counts as not run: an `item`
that is not a string, a row that is not an object, a `pass` that is not a
boolean, or a `confidence` on a failed item that is not a number or not
between 0 and 1. Any other error while one entry is evaluated is that entry's
`evaluation_failed` (or its `failed` row); the run carries on.

**`qa fix`** adds `failed` and `failed_count` beside `fixed`, `manual` and
`skipped`. A fix is in `fixed` only once its write returned. Each entry in
`failed` is the fix with a `reason`:

- `update_failed: <Error>: ...`: the write raised (for example, a read-only
  folder);
- `entry_not_found: ...`: the entry's file is gone;
- `link_not_rewritten: ...`: the broken link is not in the entry's `links`
  (a `[[wikilink]]` in the body is not rewritten).

The default (rich) report prints a **Failed** table with each reason, and the
summary counts `failed`. It no longer says "No fixable issues found." when
fixes were attempted and failed. `manual` keeps only the issues a person must
decide. One failed entry does not
stop the rest. With `--dry-run`, `fixed` lists what would be attempted and
`failed` is empty.

**Schema issues name their rule.** A `schema_violation` issue in `qa validate`
(and in the `issues` a `qa fix` reads), an error or a warning alike, carries
`schema_rule`: the rule the schema or a plugin validator broke (`required`,
`enum`, `field_select`, ...), beside `field`. A caller tells a required field
from this key, never from the message. A validator that names no rule adds no
key. `qa fix` offers no fix for a schema issue: a required field is reported
in `manual` with `reason: "no_safe_default"`, any other schema issue with
`not_auto_fixable`; `--fix-rule schema_violation` still selects them and
writes nothing.

## QA duplicate candidates

`pyrite qa duplicates -t <type>` reports live entries that may describe the
same thing, scanning every KB the caller may read. It is a **report**, not a
merge: finding candidates is not a failure, so the exit code is `0` when the scan covered every readable KB and `3` when a KB was left out and named in `unreadable_kbs`; an unknown
`--format` or an out-of-range `--threshold` is a usage error (`2`). Empty
`--type` or empty/comma-only `--status` values are structured
`VALIDATION_FAILED` refusals (`1`).

The command is CLI-only: there is no REST or MCP surface for it.

Scores are word-overlap (Jaccard) over the id slug, after removing the first
matching `--strip-prefix`, and over the title. A word is a run of Unicode word characters, compared after casefolding and accent folding (NFKD, then the marks with a non-zero combining class removed: accents and the kana voicing marks; then NFC), so `Élection` matches `election`, and Cyrillic and CJK titles compare as runs of letters (`选举日` is one word). Scripts whose words are runs of letters are supported. A script that writes vowels or voicing as combining marks (Devanagari, Thai, the kana voicing marks) is compared badly: the marks are dropped or split the word, so two different words can fold alike. Kana voicing marks have a non-zero combining class and are removed (`が` folds to `か`); some Devanagari and Thai marks have class 0 and are kept (DEVANAGARI VOWEL SIGN AA, THAI SARA I) and split the word at the tokeniser, while others have a non-zero class and are removed (DEVANAGARI VIRAMA and NUKTA, THAI SARA U and the tone marks). Tokenising those scripts is not built. A title match means the two titles' word sets overlap at or above the threshold; it says nothing about the entries' bodies. A pair is a candidate when either
score is at or above `--threshold` (default `0.62`; a number from 0 to 1, `nan` and `inf` included in what is refused, as the usage error `2` / `USAGE_ERROR`; the service function refuses anything but an `int` or `float` from 0 to 1, with `ValidationError`: a `bool`, `None`, a string, a `Decimal` or a `Fraction` is refused); `score` is the
higher one and `signal` names every signal that matched (`id`, `title`,
or `id+title`). Within each pair, `a` sorts before `b` by `(kb_name, id)`.
Without `--status`, an entry is live while its lifecycle is `active` and its
status is not one the **type declares settled**: a `task`'s `done` and `cancelled`; a software-kb `backlog_item`'s `done`, `retired`, `superseded` and
`wont_do`; an `adr`'s `rejected`, `deprecated` and `superseded`; a `milestone`'s `closed`; a `design_doc`'s `obsolete` and `implemented`. `deferred` backlog items stay live. A type whose status lives in a field other than `status` (`review_status`, `research_status`, `case_status`, `claim_status`) has no status the scan reads. A plugin
declares them as `settled_statuses` in `get_type_metadata()`; a type declared only in `kb.yaml` declares them with the same key (`types.<name>.settled_statuses`), read per KB. The value is a list of status names. `kb.yaml` overrides a plugin, which overrides core, and an empty list in `kb.yaml` is a declaration: this KB's type has no settled statuses (a plugin's empty list is the same as not declaring). An absent or `null` key is not declared. Any other shape (a bare string, which is not read as one status; a number; a boolean; a mapping; a list holding a non-string or blank item) is treated as not declared, never stops another command, and `qa validate` reports it as a `config_error` issue (severity `error`, `field` `types.<name>.settled_statuses`, `entry_id` empty): `kb.yaml types.<name>.settled_statuses must be a list of status names; got <value>`. A name that is not in the type's own `status` options, where `kb.yaml` declares them, is reported as a `config_error` with severity `warning`, and the list is still used: it replaces the lower layer's set. A wrong shape (not a list of status names) is ignored and leaves the lower layer's set in force (core `task`: `done`, `cancelled`; software-kb `backlog_item`: its four), never an empty set. Declaring `types.task.settled_statuses` in a `kb.yaml` that declares no other type makes that the KB's whole type list, and `create -t note` is then refused with `UNDECLARED_TYPE`; declare the other types too (`note: {description: notes}`), or leave the key to a plugin. Core `task` is `done` and `cancelled`: a `failed` task can be reopened, so it stays in the scan. A type that
declares none, or has no status field at all (a `note`), has every entry live
and is scanned. Task status is effective: a task completed by its children
counts as `done` even while its file still says `open`. This applies both to
`--status` and the default live-entry scan; the scan does not rewrite files.

```json
{
  "entry_type": "task",
  "threshold": 0.62,
  "skipped_tokens": 0,
  "count": 1,
  "pairs": [
    {
      "a": {"id": "2026-11-03", "kb_name": "notes", "title": "Election Day"},
      "b": {"id": "election-day", "kb_name": "notes", "title": "Election Day"},
      "score": 1.0,
      "signal": "title"
    }
  ]
}
```

`--format rich` prints one line per pair (`score kb/id ~ kb/id [signal]`) and
discloses skipped common-token expansion when coverage is incomplete.

To bound common-word expansion, tokens occurring in more than
`max(64, entries // 25)` scanned entries are not broadly expanded.
`skipped_tokens` reports how many tokens hit this bound; when nonzero, near-match
coverage is incomplete. Identical nonempty id or title token sets are still
paired, even in large clusters. Producing all pairs in a large identical cluster
necessarily takes time proportional to that output.

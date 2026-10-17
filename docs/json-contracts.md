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
still `NOT_FOUND`, except on MCP `kb_get`, which answers `ENTRY_NOT_FOUND`
(`legacy_error_code: "NOT_FOUND"`, #761). Only indexed copies are counted:
an id indexed in one KB with a not-yet-indexed `<id>.md` in another answers
from the indexed KB (the
by-name read runs only when no readable KB's index holds the id, #753). To
comply, name one of the KBs: `-k`, `kb`, `kb_name`, `kb_a`/`kb_b`
(`find_connections`), or `pyrite://kbs/<kb>/entries/<id>`.

**MCP prompts and resources** (`prompts/get`, `resources/read`) have no result
shape for a refusal, so theirs is a JSON-RPC error: `message` is the
envelope's `error`, `code` is `-32602` (invalid params), and `data` is the
whole envelope above (`error_code`, `suggestion`, `retryable`, and
`candidate_kbs` for `AMBIGUOUS`). Match on `data.error_code`. This holds for
every refusal from a prompt or resource, `KB_NOT_FOUND` included; a KB in
`pyrite://kbs/<kb>/entries/<id>` that does not exist answers `KB_NOT_FOUND`,
as one the caller cannot read does.

REST `GET /api/entries/{id}` without `kb` is refused the same way (`409`,
`candidate_kbs` in `detail`); the web client's entry links carry `?kb=` (#910).

**Not yet:** extension commands and tools that look ids up through
their own queries (`sw`, `wiki`, `social vote`, `investigation_*`) and bare
`[[id]]` resolution (`/api/entries/resolve`) are not covered either (#911).

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

Two more joined in 0.25.7 (#66), both `KB_NOT_FOUND` with
`legacy_error_code: "NOT_FOUND"`: the refusal a caller with per-KB scoping
gets from any MCP tool, prompt or resource for a KB outside its readable
set (the answer is the same whether the KB is private or does not exist),
and `kb_orient`'s answer for a name that is not a KB. Since #952 both are
one refusal from the one KB resolver (`services/kb_resolver.py`), on every
surface, and it carries two things beside the contract's keys: a
`suggestion` and a `did_you_mean` list. For an unknown name the suggestion
is one static sentence ("To list the KBs you can read, call kb_orient (MCP)
or `pyrite orient` (CLI) with no KB name."), the same for a private and an
absent name and naming no KB; `did_you_mean` holds at most three near-match
KB names for an unscoped caller (an operator key, auth off, the CLI) and is
empty for a scoped one, whose readable set is one `kb_orient` call away. For
the **shortname** of a KB the caller may read -- link syntax only
(`[[short:id]]`), refused everywhere else -- the suggestion says so and
`did_you_mean` names the full name, for every caller. A `kb_name` that is not
a string is `VALIDATION_FAILED`; `null` and a blank string are "no name".

Two more in 0.25.8 (#761): MCP `kb_get` and `kb_update` answer a missing
entry with `ENTRY_NOT_FOUND` and a KB that does not exist with
`KB_NOT_FOUND`, both `retryable: false`. Their old codes are in
`legacy_error_code`: `NOT_FOUND` for `kb_get`, and `UPDATE_FAILED` for
`kb_update`, which used to answer both with `retryable: true`. The
`UPDATE_FAILED` legacy code for a missing KB holds for an unscoped caller
only (an operator key, auth off), the one whose call reached the old
handler; a scoped caller is refused by the resolver before the handler,
with `legacy_error_code: NOT_FOUND`, as on every other tool (#952). The
other `kb_update` refusals answer their class's own code, each with
`legacy_error_code: UPDATE_FAILED`, the one code the old handler gave
everything it did not name: `CONFLICT` (a write that lost the race for its
file, `retryable: true`) and `STORAGE_ERROR` (a storage fault on the write;
`retryable: true` only for a lock held too long) -- see "Update fields"
below; a `VALIDATION_FAILED` refusal carries no legacy code (the code did
not change). Any other `kb_update` failure is still `UPDATE_FAILED`,
`retryable: true`.

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
| `KB_NOT_FOUND` | the KB name is not registered, or, for a scoped caller, not readable (the same answer, byte for byte), or it is a KB's **shortname**: a shortname is link syntax only (`[[short:id]]`), and `-k`, REST `kb` and MCP `kb_name` take the full name (#952). One resolver answers every surface; the refusal carries `did_you_mean` (near matches drawn only from the KBs you can read; for a readable KB's shortname, its full name) and a `suggestion`/`hint`. A blank name names no KB and is never this code: a command that needs one answers `VALIDATION_FAILED`. The one exception is a blank REST path segment (`/api/kbs/%20/permissions`): a path that must name a KB and names none is `KB_NOT_FOUND`. Every CLI write command (`create`, `update`, `delete`, `link`) reports it. | Use a name from `did_you_mean`, or `pyrite orient` (`kb_orient`) with no KB to list the KBs you can read. |
| `NOT_FOUND` | the entry id does not exist: CLI `get`, `update`, `delete`, `link`. Also a path you named as input (`--body-file`, `--schema-file`, `--alias-file`, `--file`, `import`'s and `add`'s file) that does not exist; a directory there is `VALIDATION_FAILED` (#923). `pyrite backlinks` and `pyrite-read backlinks` on an id that no entry has and nothing links to answer `ENTRY_NOT_FOUND` (the class's code, today; #610 settles the spelling). An id that is only linked to (`[[ghost-page]]`) is answered with its linking entries. MCP `kb_get` and `kb_update` answer `ENTRY_NOT_FOUND`, `retryable: false`, with `legacy_error_code` `NOT_FOUND` and `UPDATE_FAILED` respectively (see "MCP only, for one release" above, #761); `ENTRY_NOT_FOUND` is also the REST code for the class. | `pyrite search <term> -k <kb>` to find the id. |
| `AMBIGUOUS` | the id was given without a KB (or with a blank `-k`) and more than one KB you can read holds it (see "`AMBIGUOUS`" above). The error lists them in `candidate_kbs`; REST answers `409`. | Repeat the command with `-k <kb>` (`kb` on REST, `kb_name` on MCP), one of `candidate_kbs`. |
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

**`index health` → `type_name_drift`.** One group per KB per entry type
spelled more than one way once case, `_` and spaces are folded to `-`:
`{kb, normalized, spellings, spellings_total, spellings_has_more}`, each
spelling `{type, count, declared}` (`declared`: that exact spelling is a
`kb.yaml` type). KBs are never combined. Bounded to 100 groups
(`type_name_drift_total`, `type_name_drift_has_more`) and 50 spellings per
group. Read-only: nothing is renamed. Any group makes the status
`warning`. `pyrite kb health`, REST `GET /api/kbs/{kb}/health` and MCP
`kb_health` carry the same three fields for their one KB.

**`index health` → `schema_errors`.** One row `{kb, error}` per KB whose
`kb.yaml` failed to load (invalid YAML, `types: null`, `types` a list).
Health still answers for that KB: it counts as declaring no types, so its
non-core types appear in `undeclared_types` and no spelling is `declared`.
Any row makes the status `warning`. The same field is on `kb health`, REST
and MCP `kb_health`.

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
Frontmatter that does not parse is not listed here: it is the reader's
`malformed_frontmatter` row. A file that is not a regular file, cannot be
read or is not UTF-8 is skipped, as the reconcile skips it.

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

**How an update writes (ADR-0042, #955).** The request is applied as
operations to the file's own text: each named field changes its own lines
and nothing else (comments, key order, quoting, line endings and every
other key keep their bytes). An alias is written under the spelling the
file uses; a key the file holds under `metadata:` is edited there, any
other key at the top level, so a key refused at the top level (`type`,
`id`, `links`, `body`) is refused under `metadata` too. `updated_at` is
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
without a lock and the result says `"locked": false`. An item operation
(`append`, `remove`) touches the one item it names: the other items keep
their bytes, and `links`/`sources` take item operations on every surface
though a set of the whole list is still refused; an appended link is
checked as a link. Every operation (`set`, `append`, `remove`, `unset`)
is first resolved onto the file's own key -- an alias to the spelling the
file uses, `metadata.k` to the top-level `k` (decision 6; the file's own
`metadata.k` only where it holds one) -- and every refusal is decided on
that key, in one place, whatever the path form (`k`, `metadata.k`,
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
| REST (not run) | `PUT /api/entries/{id}` (body carries `kb`) or `PATCH /api/entries/{id}` (body: `kb`, `field`, `value`, and `op`: `set` (default), `append`, `remove` or `unset`; one field per call; `value` is the field's value, or the item for `append`/`remove`) | Both return `{"updated": true, "id": "<id>", "warnings": [], "changed": true, "locked": true, "not_moved": null}`. |

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
| REST (not run) | `POST /api/entries/import` (multipart) | `{"imported": N, "errors": M, "entries": [{"id", "title"}, ...], "error_details": [{"title", "error", "error_code"}, ...]}`. |

<!-- mcp-output: kb_bulk_create -->
```json
{"total": 2, "created": 1, "failed": 1, "results": [{"created": false, "error": "...", "error_code": "ENTRY_EXISTS"}, {"created": true, "entry_id": "two"}]}
```

### `task create` and `link`

`pyrite task create` takes `--field key=value` (repeatable) for fields a
KB's task schema allows. It has no short flag: `-f` was `--format` on the
`task` commands and is deprecated there (see "`--format` defaults"), and is
**not** `--field` as on `create`/`update`.

`pyrite link <source> <target> -k <kb> -r <relation>` answers
`{"links": [{"created": true, "source", "relation", "target", "target_kb"}], "outcome": "done"}`
(`target_kb` is the KB the link points into: the one name for it across the link family, here and in `links bulk-create`; a bare `kb` is not a key of these payloads)
(`--bidi` adds the inverse as a second item; one that failed is in `"failed"`,
with `"outcome": "partial"` and exit `3`); under
`--format rich` it prints
`Linked: <src> --[<relation>]--> <tgt> (in <kb>)` for a new link, or
`Already linked: ...` when that `(target, kb, relation)` already exists
(`"created": false`).
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
SQLite, pgvector's cosine distance on Postgres. It is kept for this release
and then leaves the response; read `similarity`. On SQLite, candidates are the
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
    `search --files`, `repo sync`, `batch-read` (an id not found), `task decompose`
    (every child refused is `1`, `"outcome": "nothing"`),
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

The rule (#303, decided 2026-10-02) is one shared `--format` option
(`pyrite/cli/output.py`): `json` by default, `PYRITE_FORMAT` sets your own
default (`PYRITE_FORMAT=rich` in a terminal), an explicit `--format`
overrides it, and an unknown format is a usage error (`2`, a `USAGE_ERROR` document). It takes `json`,
`yaml` and `rich`. `markdown` and `csv` are not offered by the shared option:
they exist for the shapes the REST API serves (entries, search results) and
print a Python `repr` for anything else. A refusal is printed in the format
asked for, YAML included. `-f` no longer
means `--format`: where it did it still works for one
release and prints a deprecation line on stderr. Each module takes the
shared option when it moves to the outcome contract; the commands not named in
`tests/legacy_commands/` have it. Until then most *read* commands default to `--format json`, `config`
to a rich terminal view, and the write commands are not uniform. The table is
checked against each command's Typer signature by
`tests/test_doc_agent_contracts.py`:

| Command | `--format` | Default |
|---|---|---|
| `create` | yes (shared) | `json` |
| `add` | yes (shared) | `json` |
| `delete` | none | Rich text, always (it asks before it deletes) |
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

## QA results: only what ran

A QA result says a check passed, or a fix landed, only when that check ran or
that write returned (#795). This applies to `pyrite qa assess`, `pyrite ci`,
`pyrite qa fix` and MCP `kb_qa_assess`.

**Tiers.** QA runs tier 1 (structural and deterministic rubric checks) and
tier 2 (tier 1 plus the judgment-only rubric items, judged by the configured
model). Any other tier is refused with `INVALID_TIER`. Tier 2 with no model
configured is refused with `AI_NOT_CONFIGURED`. Both refusals come before
anything is checked or written, on every surface. `ci` refuses them even when
no KB is configured.

The codes:

- `INVALID_TIER`: `--tier` / `tier` is not 1 or 2. `pyrite mcp --tier` uses
  the same code for an unknown server tier. Use 1 or 2.
- `AI_NOT_CONFIGURED`: tier 2 was asked for and no model is configured. REST's
  AI routes use the same code. Configure `ai_provider`, `ai_api_key` and
  `ai_model`, or use tier 1.
- `KB_UNREADABLE`: `qa assess` / `kb_qa_assess` on a KB whose folder is
  missing or cannot be read. The message names the KB and the reason, not
  the path. Restore the folder, or its permissions.
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
stderr. The default (rich) output lists both. The exit code is still 0 when only some entries failed; ADR-0046
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
boolean, or a `confidence` on a failed item that is not a number. Any other
error while one entry is evaluated is that entry's `evaluation_failed` (or its
`failed` row); the run carries on.

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

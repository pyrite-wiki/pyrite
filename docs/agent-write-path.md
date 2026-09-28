# The agent write path

How an agent creates and updates entries, on each of the three surfaces
(CLI, MCP, REST), and what to do when a write is refused. Complements
`docs/json-contracts.md` (the JSON shapes) — this page is about the write
commands specifically: discovering what a type needs, what a successful
write returns per surface, and the current truth about `--format` on
write commands.

All of it verified by running against `dev` (0.25.4, commit `8c1e2a43`).

## 1. Discovering what a type needs

Call `pyrite orient -k <kb>` (CLI) or `kb_orient` (MCP; same underlying
`KBService.orient`) first. Its `schema.types` block is good enough for
**core types** (`note`, `person`, `organization`, `event`, …): each carries
a `fields` dict with a type per field, plus `field_descriptions` and
`ai_instructions` where the type has them.

**It is not good enough for a plugin-declared type**, e.g. this repo's own
`backlog_item`, `adr`, `component`, `standard` (from the `software-kb`
extension, `kb_type: software`). For those, `orient`'s schema block gives
only:

```json
{
  "description": "Feature, bug, or tech debt item",
  "optional": ["kind", "status", "priority", "assignee", "effort"],
  "subdirectory": "backlog/"
}
```

— field *names*, no types, no allowed values, and `required` is present
only when it differs from the default `["title"]` (`backlog_item` has none
of `kind`/`status`/`priority`/`assignee`/`effort` marked required, though in
practice a KB's own validators or conventions may expect them). `pyrite kb
schema show <kb>` returns the same `required`/`optional` name lists (with
`required` always present, even when it's just `["title"]`) — more visible
than `orient`'s, but still no allowed values. `pyrite schema diff -k <kb>
-t backlog_item` returns prose only (`No typed fields defined`), no field
list at all.

**Where the allowed values actually live today:** the plugin's own MCP
create-tool `inputSchema`, if it has one. For `backlog_item`,
`sw_create_backlog_item`'s schema (`extensions/software-kb/src/pyrite_software_kb/plugin.py`)
has real JSON Schema `enum`s:

```json
"kind": {"enum": ["feature", "bug", "tech_debt", "improvement", "spike", "epic"]},
"priority": {"enum": ["critical", "high", "medium", "low"]},
"effort": {"enum": ["XS", "S", "M", "L", "XL"]},
"required": ["title", "kind", "kb_name"]
```

This is not general: it exists because `software-kb` happens to expose a
typed MCP tool for this one entry type, not because `orient`/`kb_schema
show` merge it in. No plugin implements `get_field_schemas()` (the
protocol method at `pyrite/plugins/protocol.py:190` and its aggregator
`get_all_field_schemas` at `pyrite/plugins/registry.py:696` exist, but
`extensions/software-kb/src/pyrite_software_kb/plugin.py` — or any other
extension — has no such method), so there is no code path that could
merge enum constraints into `orient`'s schema block today. This is
tracked as **#232** (open): until it lands, an agent writing a
plugin-declared type either reads the extension's own MCP tool schema (if
one exists for that type), the extension's source for its validator
constants, or writes speculatively and lets `SCHEMA_VIOLATION` name the
rejected field.

**Summary, per surface:**

| Surface | Core type fields | Plugin type fields | Plugin type allowed values |
|---|---|---|---|
| `pyrite orient -k <kb>` / MCP `kb_orient` | yes (`fields` dict, typed) | names only (`optional`/`required` lists) | no |
| `pyrite kb schema show <kb>` | yes | names only | no |
| `pyrite schema diff -k <kb> -t <type>` | prose | prose, no field list | no |
| a plugin's own typed MCP create tool (e.g. `sw_create_backlog_item`), if it has one | n/a | yes | yes |

## 2. What a successful write returns, per surface

### `create`

| Surface | Call | Success shape |
|---|---|---|
| CLI | `pyrite create -k <kb> -t <type> --title <t> --body <b>` | **No `--format` option at all.** Always prints Rich text: `Created: <id>` / `Type: <type>`, plus a `Warning: ...` line per schema warning. Never JSON. |
| MCP | `kb_create` | `{"created": true, "entry_id": "<id>", "file_path": "<absolute path>"}`, plus `"warnings": [...]` when any, plus `"qa_issues"` when a QA validator ran and found something. |
| REST | `POST /api/entries` | `{"created": true, "id": "<id>", "kb_name": "<kb>", "file_path": "", "warnings": []}`. `file_path` is **always the empty string** on this surface (unlike MCP, which returns the real path) — verified by running both against the same entry. |

`pyrite add <file>` (load a markdown file with frontmatter) is the same
shape as `create`: Rich text only, `Added: <id>` / `Type: <type>`, no
`--format`.

### `update`

| Surface | Call | Success shape |
|---|---|---|
| CLI | `pyrite update <id> -k <kb> --title <t> --format json` | **Has `--format`, defaulting to `json`.** With `--format json` (or any non-`rich` value): `{"updated": true, "entry_id": "<id>"}`. With `--format rich`: `Updated: <id>`. |
| MCP | `kb_update` | `{"updated": true, "entry_id": "<id>", "file_path": "<absolute path>"}`, plus `"warnings"` when any, and `"ignored"` listing any keys it did not write. |
| REST | `PUT /api/entries/{id}` (body carries `kb`, not a query param) or `PATCH /api/entries/{id}` (body: `kb`, `field`, `value` — single field, `value` must be a string) | Both return the same `UpdateResponse`: `{"updated": true, "id": "<id>", "warnings": []}`. |

An update only ever sets fields that surface's request shape exposes, plus
`--field`/`-f key=value` (CLI) for anything else the type's schema allows.
Fields Pyrite manages itself (`id`, `file_path`, `kb_name`, `links`,
`sources`, `provenance`, a type's own `managed_fields`) are refused
(`VALIDATION_FAILED`, CLI/REST) or set aside and listed in the result's
`ignored` field (MCP `kb_update`) if you try to set them directly — see `docs/json-contracts.md`'s "Update
fields" section.

`update -f key=value` (CLI) and MCP `kb_update` with an extra key both
store an undeclared field under the entry's `metadata` dict (verified: a
key that is not one of the type's own fields lands at
`entry.metadata.<key>` in the frontmatter and in the read-back JSON, not
as a top-level key).

### `bulk_create` / import

| Surface | Call | Success shape |
|---|---|---|
| CLI | `pyrite import <file> -k <kb>` | Rich text: one `Created: <id>` / `Failed [CODE]: <title>: <message>` line per record, then `Imported N entries` (or `Imported N entries (M failed)`). Exit `1` if any record failed. No `--format` (the command's own `--format` flag selects the *input* file format, json/yaml, not output). |
| MCP | `kb_bulk_create` | `{"total": N, "created": C, "failed": F, "results": [{"created": true, "entry_id": "..."}, {"created": false, "error": "...", "error_code": "..."}, ...]}`, results in input order. |
| REST | `POST /api/entries/import` (multipart file upload) | `{"imported": N, "errors": M, "entries": [{"id", "title"}, ...], "error_details": [{"title", "error", "error_code"}, ...]}`. |

### `task create`

`pyrite task create` supports `--field key=value` (repeatable) for any
field a KB's task schema requires or allows beyond the built-in options
(e.g. a `desk` KB's `project`/`kind`). Its own output-format flag is
`--format`/`-f` (`rich` default, or `json`) — `-f` is *not* `--field` on
this command, unlike `create`/`update`, where `-f` is short for `--field`.
Check `--help` per command; the short flags are not consistent across
write commands.

### `link`

`pyrite link <source> <target> -k <kb> -r <relation>` — no `--format`,
always Rich text: `Linked: <src> --[<relation>]--> <tgt> (in <kb>)` on a
new link, or `Already linked: <src> --[<relation>]--> <tgt> (in <kb>)`
when that exact `(target, kb, relation)` triple already exists. A second
`link` call with a *different* `relation` between the same two entries is
a new link, not a duplicate — both are stored, both print `Linked:`.

## 3. The truthful `--format` situation on write commands

`docs/json-contracts.md` says "Most commands default to `--format json`."
That is not true of the write commands specifically — verified by reading
every write command's Typer signature and running each one:

| Command | Has `--format`? | Default |
|---|---|---|
| `create` | **No** | always Rich text |
| `add` | **No** | always Rich text |
| `delete` | **No** | always Rich text |
| `link` | **No** | always Rich text |
| `update` | Yes | `json` |
| `rename` | Yes | `json` |
| `import` | Its `--format` selects the *input* file format (json/yaml), not output | always Rich text |
| `task create` | Yes (`-f`/`--format`) | `rich` |

This is a known, filed gap: **#303** (open, `good first issue`, "CLI
output format is inconsistent when stdout is not a TTY") is the design
decision that would make this consistent (one rule: human-readable unless
`-f json` is asked for, or a documented non-TTY default, applied
uniformly). This document states today's behaviour; #303 is the "made
true" path, not implemented here.

`docs/json-contracts.md`'s error-shape claim ("every error surface …
returns the same structure") is also not quite true for write commands
today: `create`, `add`, `delete` and `link` have no `--format`, so their
errors are always Rich-formatted text (box-drawing characters included),
never the `{error, error_code, ...}` JSON object — there is no flag to
ask for it. Only `update`/`rename` (which have `--format`) can print that
JSON error shape from the CLI. MCP and REST always return the JSON/detail
shape regardless. See `docs/json-contracts.md`'s corrected note and cite
#303.

## 4. Is a write searchable immediately, or does it need `index sync`?

Verified by writing both ways and searching right after:

- **`pyrite create`/`update`, MCP `kb_create`/`kb_update`/`kb_bulk_create`,
  REST `POST`/`PUT`/`PATCH /api/entries*`** — searchable immediately. Each
  goes through `KBService`'s write pipeline, which indexes as part of the
  write.
- **A file written directly to disk under a KB's path, bypassing all of
  the above** — not searchable until `pyrite index sync` runs. `search`
  even warns about it: `Warning: index may be stale for: <kb> (a file on
  disk is newer than the index). Results could be out of date — run
  pyrite index sync to refresh.` `index sync` is incremental and cheap;
  safe to run after any batch of out-of-band writes.

## 5. Error X, do Y

Verified by triggering each error against a temporary KB.

| `error_code` | Trigger | What to do |
|---|---|---|
| `KB_NOT_FOUND` | write against a KB name that isn't registered | `pyrite kb list` to see registered KBs. On `search`, MCP and REST, the error's own `suggestion`/message already names this. **On the CLI's `create`, `update`, `delete` and `link`, this case currently reports the generic `error_code: "ERROR"` instead of `KB_NOT_FOUND`** — filed as **#481**; treat any CLI write failure whose message starts "KB not found" the same as `KB_NOT_FOUND` until it's fixed. |
| `NOT_FOUND` | `pyrite get <missing-id> -k <kb>` (or update/delete an id that doesn't exist) | `pyrite search <term> -k <kb>` to find the right id. Same CLI caveat as above: `update`/`delete`/`link` against a missing entry currently also report `error_code: "ERROR"`, not `NOT_FOUND` (#481); `get` itself does report `NOT_FOUND` correctly. |
| `UNDECLARED_TYPE` | `create`/`import` with a type not in a KB's declared `types:` list (core types are not exempt) | `pyrite kb schema show <kb>` to see the declared types and their field names (not enums — see §1), or pass `--allow-undeclared` / `allow_undeclared: true` to override. The CLI's own refusal message already gives this exact hint. |
| `ENTRY_EXISTS` | `create` (or `import`/`kb_bulk_create`) with an id (given or derived from title) that already exists | Use `update` instead, or choose a different title/id. REST answers `409`. |
| `SCHEMA_VIOLATION` | a field value the KB schema or a plugin validator rejects (enum, required, range, format) | Re-read the rejected field's message — it names the field and the constraint. For a plugin-declared type, check whether the plugin has its own typed MCP create tool (§1) for the real allowed values. |
| `VALIDATION_FAILED` | a model-level rule (e.g. an `event` with no `date`), a `managed_fields` key in the update payload, or a body carrying `body_truncated: true` alongside a `body` (ADR-0034) | Fix the named field; for a truncated-body refusal, reassemble the full body with `kb_read_body` (or a `body_limit` above `body_length`) and retry without the marker. |
| `QUERY_SYNTAX` (on `search`, not a write, but the error an agent hits right after adjusting a failed write) | a search token containing `-`, `:` or `.` used alongside `AND`/`OR`/`NOT` or a quote, without its own quotes | Quote the token yourself, e.g. `"bar-baz"`. |

All are `retryable: false` — none of these succeed by retrying the same
request unchanged.

## Related

- `docs/json-contracts.md` — the JSON shapes (error envelope, write
  refusal codes, warnings, body truncation) referenced throughout this
  page.
- `pyrite orient`'s `operational_contracts` field carries the indexing,
  error-shape, search-quoting and task-claim rules inline, for an agent
  that wants them without reading this file.
- **#232** — the plugin-field-schema gap in §1 (open).
- **#303** — the `--format` consistency decision in §3 (open).

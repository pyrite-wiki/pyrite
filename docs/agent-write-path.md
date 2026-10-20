# Write an entry as an agent

A how-to for an agent (or the person wiring one up) that writes entries
through the CLI, MCP or REST. Each step runs a command and links the row of
`docs/json-contracts.md` that owns the contract: the error shape, every
`error_code` with its remedy, the success shapes per surface, and which
commands have `--format`. This page does not repeat them.

Every command below runs in CI (`tests/test_doc_agent_contracts.py`), in a
scratch HOME, in order. A block marked `output-keys` is compared, by key,
with what the command printed; ids, paths and times vary. This page names no
version it was checked on: the test is the check.

The CLI is the example surface. MCP `kb_orient`, `kb_create`, `kb_update`
and REST `POST /api/entries` do the same; the success shape of each is in
[Write success shapes](json-contracts.md#write-success-shapes).

## 0. A scratch KB

<!-- expect-exit: 0 -->
```bash
pyrite init -t empty -p ./notes --name notes --no-examples
```

## 1. Discover what a type needs

`pyrite orient -k <kb>` (MCP `kb_orient`, the same `KBService.orient`) first;
with no KB named it lists the KBs you can read. `schema.types` lists the
types `create` accepts in that KB: only the declared ones when `kb.yaml`
declares any, otherwise the core types. `--detail brief` drops the write-side
blocks (`ai_instructions`, `evaluation_rubric`, `guidelines`, `goals`); use
the default, or `--detail full`, when you are about to write.

<!-- expect-text: "note" -->
```bash
pyrite orient -k notes
```

A **core type** (`note`, `person`, `organization`, `event`, ...) carries a
`fields` dict with a type per field, plus `field_descriptions` and
`ai_instructions`.

A type the **KB declares in `kb.yaml`** carries what the KB wrote: a
`fields` block with `options` (the allowed values) when it declared fields,
or only prose (`description`, `guidelines`, `goals`, `evaluation_rubric`)
when it did not, as this repo's own `kb/` does for `backlog_item`. A
**plugin-declared** type with no `kb.yaml` entry gets only field names
(`optional`, `required`, `subdirectory`). In neither of those two cases do
you learn types or allowed values from `orient` or `pyrite kb schema show
<kb>`; that gap is **#232** (open). Until it lands, read the plugin's own
typed MCP create tool schema if it has one (`sw_create_backlog_item` has real
`enum`s), or write and let `SCHEMA_VIOLATION` name the rejected field.

## 2. Write

<!-- expect-text: "entry_id": "hello-agent" -->
```bash
pyrite create -k notes -t note --title "Hello agent" --body "First note."
```

**What a create writes.** A create from fields (`create`, MCP `kb_create` and
`kb_bulk_create`, REST `POST /api/entries`, the web clipper, `pyrite import` of
JSON, YAML or markdown) writes every field the type's schema declares: the
fields in this KB's `kb.yaml` (`fields`, `optional`, `required`), any a plugin
declares through `get_field_schemas()` (no shipped plugin does yet), and the
core type's own (a `person`'s `role` and `affiliations`). `kb.yaml` can add a
field or change its type and default; it cannot remove a core type's field.
Base fields (`summary`, `tags`, `aliases`, `sources`, `links`, ...) are written
only when set, and a field the type manages (a task's audit trail, an ADR's
number) is never added.

A declared field you send is written as sent, an empty value included: in a KB
whose `kb.yaml` declares `kind` for `backlog_item` (one made with
`pyrite init -t software` does), `-f kind=` writes `kind: ""`; where nothing
declares it, an empty `kind` is not written. A declared field you leave out
gets its `default` when it declares one, otherwise an empty value: `""` for
text and select, `[]` for a list, multi-select or tags, `null` for a number,
date, datetime, checkbox or reference. A field the type's model holds keeps
the model's type when empty (`affiliations: []`). The create validates, runs
its hooks on and indexes these values, so a default that breaks the schema is
refused like a value you sent. An empty value (`""`, `[]`, `null`) is no value
to every optional check, whatever the field's declared type; a `required`
field still refuses it. A typed entry writes a declared field its model
has no attribute for under `metadata:`, where it writes the same field when
you send it. When an update later fills an empty `""`, the value is written
plain (`kind: bug`).

`pyrite import` reads a markdown file's frontmatter as fields and builds the
entry from them, so it is a create from fields too. `pyrite add` writes the
file's own keys and nothing else, and an update never adds a key the file
lacks. A `note` declares no fields of its own, so the file below has none
(#86).

`create` defaults to JSON like every write command (see the
[`--format` table](json-contracts.md#--format-defaults); `--format rich` prints
`Created: <id>`). Read the id from `entry_id`. To change it, use `update`:

<!-- expect-exit: 0 -->
```bash
pyrite update hello-agent -k notes --body "Second note."
```

<!-- output-keys -->
```json
{"updated": true, "entry_id": "hello-agent", "outcome": "done", "locked": false}
```

`locked: false` says this KB is not a git work tree: the write compared the
file with what it read and replaced it, with no lock (two writers can both
pass that compare). Inside git the key is absent, which means `true`.

A field the type does not declare is written at the top level of the file;
`get` returns it under `metadata`. Read the file to see where it landed:

<!-- expect-text: foo: bar -->
```bash
pyrite update hello-agent -k notes -f foo=bar
cat notes/notes/hello-agent.md
```

```text
---
id: hello-agent
title: Hello agent
type: note
importance: 5
foo: bar
---

Second note.
```

Which fields an update may set, and which it refuses, is the **Update
fields** paragraph of the contracts page. An update writes only the fields it
names, so the lines you did not name keep their bytes; see
[How Pyrite edits your files](how-pyrite-edits-your-files.md).

### Add or remove one item

`--tags` (MCP `tags`) replaces the whole list. To add or drop one item
without resending the list, use an item operation. `--append` takes
`field=item`, or a JSON list (`'tags=["x","y"]'`) for several items:

<!-- expect-text: - x -->
<!-- expect-text: - y -->
```bash
pyrite update hello-agent -k notes --append 'tags=["x","y"]'
cat notes/notes/hello-agent.md
```

```text
---
id: hello-agent
title: Hello agent
type: note
importance: 5
foo: bar
tags:
- x
- y
---

Second note.
```

`--remove` drops one item and `--unset` removes a key with its lines. The
last line of the block fails the step if either is still in the file:

<!-- expect-text: - y -->
```bash
pyrite update hello-agent -k notes --remove tags=x
pyrite update hello-agent -k notes --unset foo
cat notes/notes/hello-agent.md
test "$(grep -c -e '^foo:' -e '^- x$' notes/notes/hello-agent.md)" = 0
```

```text
---
id: hello-agent
title: Hello agent
type: note
importance: 5
tags:
- y
---

Second note.
```

MCP `kb_update` spells these `"append": {"tags": "x"}`, `"remove": {"tags":
"x"}` and `"unset": ["foo"]`; REST `PATCH` takes `op`. The table is in
[How Pyrite edits your files](how-pyrite-edits-your-files.md), the result
shapes in [Write success shapes](json-contracts.md#update).

## 3. Read it back

<!-- expect-text: "title": "Hello agent" -->
```bash
pyrite get hello-agent -k notes
```

## 4. Is it searchable?

A write through `create`, `update`, `kb_create`, `kb_update`,
`kb_bulk_create` or the REST entry routes is indexed as part of the write:

<!-- expect-text: "id": "hello-agent" -->
```bash
pyrite search "Second" -k notes
```

<!-- output-keys -->
```json
{"query": "Second", "count": 1, "results": [],
 "match": "strong", "match_basis": "keyword: every term matched", "legs_run": ["keyword"],
 "keyword_match": "all_terms"}
```

`match` says what the page is worth (`strong`, `weak` or `none`); on `none`
the KB does not contain an answer. See
[What the page is worth](json-contracts.md#what-the-page-is-worth-match-match_basis-keyword_match-similarity).

A file you write yourself under the KB's folder is not searchable until
`pyrite index sync` (incremental and cheap; the
[operational contracts](json-contracts.md#operational-contracts) say so,
and `search` warns when it sees a file newer than the index):

<!-- expect-text: Directly -->
```bash
printf -- '---\ntitle: Directly\ntype: note\n---\nWritten by hand.\n' > notes/notes/directly.md
pyrite index sync
pyrite search "Directly" -k notes
```

## 5. Handle a refusal

A refused write names an `error_code`; match on it, not on the message. The
codes, what each means and what to do are one table:
[Write refusals](json-contracts.md#write-refusals-create-import-update).
Two things this page adds:

- All are `retryable: false` except `CONFLICT` and `STORAGE_ERROR` on a locked
  database (`retryable: true`): for `CONFLICT`, re-read the entry, then repeat
  the update. For every other code, retrying the same request unchanged fails
  the same way. Change the request.
- A CLI write command prints a refusal as JSON by default
  (`{"error", "error_code", ...}`), or as `ERROR [CODE]: message` under
  `--format rich`; branch on exit code `1` and read `error_code`. (`delete`,
  which asks first, always prints the `ERROR [CODE]` line.) `create --link` with
  a link that does not resolve is the one exception: the entry was written, so
  it exits `3` (not `1`) and says which link failed and what `pyrite link`
  finishes it. `update` prints the JSON shape:

<!-- expect-exit: 1 -->
<!-- expect-text: "error_code": "KB_NOT_FOUND" -->
```bash
pyrite update hello-agent -k nokb --title x
```

<!-- output-keys -->
```json
{"outcome": "error", "error": "KB 'nokb' not found", "error_code": "KB_NOT_FOUND", "retryable": false, "suggestion": "To list the KBs you can read, call kb_orient (MCP) or `pyrite orient` (CLI) with no KB name.", "did_you_mean": []}
```

An update is refused only for a violation it causes. A schema violation
already in the file, in a field your request does not name, comes back as a
warning in the result (`note`: "already on disk"), and the file's value is
left as it was. Setting that field to another bad value, or a change that
triggers a new finding, is still refused (ADR-0042 decision 8).

## Not covered here

- Bulk writes (`kb_bulk_create`, `pyrite import`): per-item results are in
  [Write success shapes](json-contracts.md#write-success-shapes).
- Truncated bodies: never write back a body marked `body_truncated`; see
  [Writing a body back](json-contracts.md#writing-a-body-back).
- CLI output format is one rule, decided in #303: see [`--format` defaults](json-contracts.md#--format-defaults). Modules not yet migrated are listed in `tests/legacy_commands/`.

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

<!-- expect-text: Created: hello-agent -->
```bash
pyrite create -k notes -t note --title "Hello agent" --body "First note."
```

`create` has no `--format`: it prints Rich text (see the
[`--format` table](json-contracts.md#--format-defaults)). Read the id from
`Created: <id>`. To change it, use `update`, which defaults to JSON:

<!-- expect-exit: 0 -->
```bash
pyrite update hello-agent -k notes --body "Second note."
```

<!-- output-keys -->
```json
{"updated": true, "entry_id": "hello-agent"}
```

A field the type does not declare is stored under `metadata`, not at the top
level of the entry:

<!-- expect-text: "foo": "bar" -->
```bash
pyrite update hello-agent -k notes -f foo=bar
pyrite get hello-agent -k notes
```

Which fields an update may set, and which it refuses, is the **Update
fields** paragraph of the contracts page.

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
{"query": "Second", "count": 1, "results": []}
```

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

- All are `retryable: false`: retrying the same request unchanged fails the
  same way. Change the request.
- A CLI command with no `--format` (`create`, `add`, `delete`, `link`)
  prints a refusal as `ERROR [CODE]: message`, not JSON; branch on exit code
  `1` and read the code in the brackets. `update` prints the JSON shape:

<!-- expect-exit: 1 -->
<!-- expect-text: "error_code": "KB_NOT_FOUND" -->
```bash
pyrite update hello-agent -k nokb --title x
```

<!-- output-keys -->
```json
{"error": "KB not found: nokb", "error_code": "KB_NOT_FOUND", "retryable": false}
```

## Not covered here

- Bulk writes (`kb_bulk_create`, `pyrite import`): per-item results are in
  [Write success shapes](json-contracts.md#write-success-shapes).
- Truncated bodies: never write back a body marked `body_truncated`; see
  [Writing a body back](json-contracts.md#writing-a-body-back).
- #303 (open) is the decision that would make CLI output format one rule.

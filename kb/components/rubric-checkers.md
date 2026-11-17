---
id: rubric-checkers
type: component
title: "Rubric Checkers"
kind: module
path: "pyrite/services/rubric_checkers.py"
owner: "markr"
dependencies: ["pyrite.services.qa_service", "pyrite.schema.core_types"]
tags: [core, qa, validation, rubric]
links:
  - target: qa-service
    relation: part_of
    note: "Rubric checkers are used by QAService for evaluation"
---

Named registry of deterministic rubric checker functions. Each checker validates one rubric item against an entry and returns an issue dict or None. Checkers are looked up by name via the `NAMED_CHECKERS` registry.

## Architecture

- `NAMED_CHECKERS`: dict mapping checker names to callable functions — the sole lookup mechanism
- `GENERIC_TITLES` — frozenset of title strings considered too vague (e.g., "Update", "Notes", "TODO")
- `_parse_metadata(entry)` — extracts metadata dict from entry (handles JSON string or dict)

## Checker Functions

All checkers accept `(entry, schema, params=None)` and return an issue dict or None.

- **Structural**: `check_descriptive_title`, `check_has_tags`, `check_has_outlinks`, `check_status_present`, `check_priority_present`
- **Parameterized field**: `check_has_field` (params: `{field}`), `check_has_any_field` (params: `{fields}`)
- **Body structure**: `check_body_has_section` (params: `{heading}`), `check_body_has_pattern` (params: `{pattern}`), `check_body_has_code_block`

## The entry a checker receives

Every caller builds it with one function, `checker_entry(db, row)` in `pyrite/services/checker_entry.py`. The callers are a `board.yaml` gate (`_evaluate_gate` in the software-kb extension, and the load-time dry run, which uses `empty_checker_entry()`) and QA (`validate_entry` and the whole-KB rubric pass). So a checker gives one verdict for one item whoever calls it (#1587). No caller builds the entry itself: `tests/test_a_checker_sees_the_items_own_fields.py` fails if any module other than the builder writes `_tag_count`, `_outlink_count` or `_links`.

From the item's whole index row (`SELECT * FROM entry`), the entry holds:

- **the row's columns as they are**: `id`, `kb_name`, `entry_type`, `title`, `body`, `status`, and the rest. `descriptive_title`, `status_present` and the body checkers read these.
- **`metadata`**: a new dict, never the stored bag. It is the bag, with each field column (`FIELD_COLUMNS`: `title summary date importance status location assignee assigned_at priority due_date start_date end_date coordinates fips state lifecycle`) filled in where the bag has no non-empty value, plus `tags` (the tag names) when the item has any. A field whose bag value and column are both empty (`None` or `""`) is absent, so a kb.yaml type that keeps a protocol field in its column and its bag, and a plugin class that keeps it in the column only, give one answer. `has_field`, `has_any_field` and `priority_present` read this, so they see a field wherever the index keeps it.
- **`_tag_count`**: the number of tags.
- **`_links`**: `[{"target", "relation"}]`, every `link` row the item is the source of (frontmatter links and body wikilinks alike), read by `not_oversized` and `orphan_backlog_item`. **`_outlink_count`** is `len(_links)`, read by `has_outlinks`.

What that means for `has_field`:

- It sees `tags`, `title` and `summary`, as well as the bag and the field columns.
- It does not see `aliases`, `sources` or `links`. The link checkers read `_links`.
- It tests truthiness, so `priority: 0` and `status: ""` are missing. `priority_present` asks only for a key, so `priority: 0` holds and `priority: ""` does not.
- For a type whose model gives a field a default, `has_field` and `priority_present` hold when the file leaves that field out, because the item has that value as Pyrite reads it, the value `pyrite get` shows. A `backlog_item` with no `priority` line reads `medium`, and a `task` reads `5`. A checker cannot tell a defaulted field from a written one: that would need the index to record which fields the file wrote.

The entry is read from the index, so a hand edit that has not been synced gives the indexed verdict, as every index read does. In a gate, the builder runs outside `board.evaluating`, so a failed read keeps its own error and never names `board.yaml` (#1585).

## Integration

Called by `QAService._check_rubric_evaluation()` via named lookup. Rubric items in `kb.yaml` bind to checkers using `{text, checker, params}` dict format. Plain string items are judgment-only (LLM-evaluated). Items with `covered_by: schema` are skipped.

Plugins register additional checkers via `PyritePlugin.get_rubric_checkers()`, aggregated by `PluginRegistry.get_all_rubric_checkers()`.


## CI action for named checkers

`kb.yaml` accepts exactly two action settings: a top-level
`rubric_action: warn|fail` beside `evaluation_rubric` (default `fail`), and
`action: warn|fail` on a named rubric item, which overrides that default.
For example:

```yaml
rubric_action: fail
evaluation_rubric:
  - text: Has an owner
    checker: has_field
    params: {field: owner}
    action: warn
```

Type-level `rubric_action`, `rubric`, `validation.rubric`, item `mode` and
item `severity` are not action aliases. Invalid values of either supported
key are configuration errors naming the key and allowed values, and fail CI.
`warn` applies only to named checker findings. Existing bulk missing-tag and
missing-link findings keep failing CI, and a broken or unknown checker still
fails. JSON CI rows include `rubric_action` when a named check assigned one.

---
id: qa-service
title: QA Service
type: component
kind: service
path: pyrite/services/qa_service.py
owner: core
dependencies:
- pyrite.storage
- pyrite.config
- pyrite.schema
tags:
- core
- service
- qa
---

Structural validation and assessment service for knowledge bases. Validates KB integrity without LLM involvement: missing titles, empty bodies, broken links, orphan entries, date format violations, importance range checks, and schema field violations.

## Key Methods

- `validate_kb(kb_name, tier=1)` — runs all validation rules on a KB, returns `{kb_name, total, checked, issues}`; `tier=2` adds the model-judged rubric (what `pyrite ci --tier 2` runs)
- `validate_entry(entry_id, kb_name)` — validates a single entry
- `validate_all(tier=1)` — validates all configured KBs
- `assess_entry` / `assess_kb` — validate and write a `qa_assessment` record per entry (see "Tiers" below)
- `require_tier(tier)` — the refusal every tier-taking surface runs first
- `get_status(kb_name)` — dashboard aggregation (counts by severity and rule)

## Validation Rules

| Rule | Severity | Description |
|------|----------|-------------|
| `missing_title` | error | Entry has empty or missing title |
| `non_string_title` | error | File's `title:` is a list, a mapping or NaN; pyrite cannot index it (#711) |
| `empty_body` | warning | Entry has no body content |
| `missing_event_date` | error | Event type without a date |
| `invalid_date` | error | Date field not in YYYY-MM-DD format |
| `importance_range` | warning | Importance outside 1-10 range |
| `broken_link` | warning | Wikilink target doesn't exist |
| `orphan` | info | Entry has no incoming links |
| `schema_violation` | error/warning | Field violates kb.yaml type schema |
| `frontmatter_line_in_body` | warning | First body line repeats a scalar frontmatter field; may be a migration leak |
| `rubric_violation` | warning | Entry fails a named rubric checker |
| `config_error` | warning | Unknown checker name in rubric config |
| `checker_failed` | warning | A named rubric checker raised, so its item was not checked (#795) |
| `file_unloadable` | error | `validate_entry`: the entry's file would not load, so the checks that read it did not run (#795) |
| `llm_rubric_violation` | warning | Tier 2: the model judged a rubric item failed (was `info` until #795, which no status counted) |
| `kb_unreadable` | error | The KB's folder is missing or cannot be read; the file, schema and kb.yaml checks were skipped (#950) |
| `evaluation_failed` | error | Tier 2 in `validate_kb`/`ci`: the model did not answer for this entry; the message says why |

## Schema Validation

When `kb.yaml` exists, `_check_schema_all()` validates each entry against its type's `FieldSchema` definitions — required fields, select options, type constraints. Uses `KBSchema.validate_entry()` for the actual field checking.

## Rubric Evaluation

`_check_rubric_evaluation()` processes rubric items from `SYSTEM_INTENT`, `CORE_TYPE_METADATA`, and `kb.yaml`:
- **Dict items with `checker`** — looked up in `NAMED_CHECKERS` (core + plugins), run deterministically
- **Dict items with `covered_by`** — skipped (handled by schema validation)
- **Plain strings** — judgment-only, collected for LLM evaluation by `_collect_judgment_items()`

## Tiers, and what did not run (#795)

`QA_TIERS = (1, 2)`. Tier 1 is the structural and deterministic rubric checks;
tier 2 adds the judgment-only rubric items, sent to the model through
`LLMRubricEvaluator`. The rule every QA surface keeps: **a result says a check
passed, or a fix landed, only when that check ran or that write returned.**

- `require_tier` refuses an unknown tier (`InvalidQATierError`, `INVALID_TIER`)
  and tier 2 with no model (`AINotConfiguredError`, `AI_NOT_CONFIGURED`) before
  anything is checked or written. `pyrite ci` runs it before it looks for KBs.
- The evaluator returns `[]` only when the model answered every item with a
  boolean `pass`, or there were no items. Anything else (the call raised, the
  reply was empty, not JSON, not a list, or silent on an item) raises
  `EvaluationFailedError` (`EVALUATION_FAILED`) with the reason.
- `assess_entry` writes no record for a tier that did not run (maintainer
  decision, 2026-10-15). `assess_kb` lists such entries in `failed`
  (`{entry_id, error_code, reason}`) and carries on. A task
  `create_task_on_fail` could not create comes back as `task_failed`
  (`tasks_failed` for a KB).
- `QAFixService.fix_kb` puts a fix in `fixed` only once its write returned; a
  write that raised, a missing file, or a link with nothing to rewrite goes in
  `failed` with a `reason`. `FIX_KINDS` lists the kinds it dispatches.
- Tier 2 evaluation of one entry ends as that entry's failure whatever goes
  wrong in it (`_evaluate_llm_rubric_row` turns any non-Pyrite exception into
  `EvaluationFailedError`), so `ci` and `assess_kb` keep every other entry's
  report. `ci` resolves KBs with the DB registry merged (`get_config_and_db`);
  the checks that need `config.get_kb()` return early for a KB that resolves
  to nothing, so `ci` refuses such a KB rather than pass it.
- Records written before #795 cannot be told apart: a `tier: 2` record from
  then may hold tier 1 checks only.

Tests: `tests/test_qa_records_only_what_ran.py` (every surface that takes a
tier, every not-run regime, every fix kind).

## Consumers

- MCP tools: `kb_qa_validate`, `kb_qa_status`, `kb_qa_assess`
- REST API: `/api/qa/status`, `/api/qa/validate`, `/api/qa/validate/{id}`, `/api/qa/coverage`
- CLI: `pyrite qa validate`, `pyrite qa status`, `pyrite qa assess`, `pyrite qa fix`, `pyrite ci`

## Related

- [[schema-validation]] — field-level validation rules
- [[kb-service]] — CRUD operations that QA validates

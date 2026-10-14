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

- `validate_kb(kb_name)` — runs all validation rules on a KB, returns `{kb_name, total, checked, issues}`
- `validate_entry(entry_id, kb_name)` — validates a single entry
- `validate_all()` — validates all configured KBs
- `get_status(kb_name)` — dashboard aggregation (counts by severity and rule)

## Validation Rules

| Rule | Severity | Description |
|------|----------|-------------|
| `missing_title` | error | Entry has empty or missing title |
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

## Schema Validation

When `kb.yaml` exists, `_check_schema_all()` validates each entry against its type's `FieldSchema` definitions — required fields, select options, type constraints. Uses `KBSchema.validate_entry()` for the actual field checking.

## Rubric Evaluation

`_check_rubric_evaluation()` processes rubric items from `SYSTEM_INTENT`, `CORE_TYPE_METADATA`, and `kb.yaml`:
- **Dict items with `checker`** — looked up in `NAMED_CHECKERS` (core + plugins), run deterministically
- **Dict items with `covered_by`** — skipped (handled by schema validation)
- **Plain strings** — judgment-only, collected for LLM evaluation by `_collect_judgment_items()`

## Consumers

- MCP tools: `kb_qa_validate`, `kb_qa_status`
- REST API: `/api/qa/status`, `/api/qa/validate`, `/api/qa/validate/{id}`, `/api/qa/coverage`
- CLI: `pyrite qa validate`, `pyrite qa status`

## Related

- [[schema-validation]] — field-level validation rules
- [[kb-service]] — CRUD operations that QA validates

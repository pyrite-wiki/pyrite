---
id: migrate-rubric-items-to-named-format
title: Migrate Rubric Items to Named Format
type: runbook
tags:
- qa
- migration
- runbook
---

## What Changed

String-format rubric items in `kb.yaml` are no longer matched by regex. The legacy regex path (`RUBRIC_CHECKERS`, `match_rubric_item()`, `is_already_covered()`) has been removed.

Rubric items must now be either:
- **Named format** (dict with `checker` key) — bound to a deterministic checker function
- **Plain string** — evaluated by LLM judgment only

## Available Checkers

| Checker | Params | What it checks |
|---------|--------|----------------|
| `descriptive_title` | — | Title is not generic (TODO, Untitled, etc.) |
| `has_tags` | — | Entry has at least one tag |
| `has_outlinks` | — | Entry links to another entry (stubs exempt) |
| `status_present` | — | Entry has a status value |
| `priority_present` | — | Entry has `priority` in metadata |
| `has_field` | `field: <name>` | Metadata contains the named field |
| `has_any_field` | `fields: [a, b]` | Metadata contains at least one of the listed fields |
| `body_has_section` | `heading: <name>` | Body contains `## <heading>` |
| `body_has_pattern` | `pattern: <regex>` | Body matches the regex pattern |
| `body_has_code_block` | — | Body contains a fenced code block |

## How to Migrate

For each string item in your `kb.yaml` `evaluation_rubric`:

1. **If a checker exists** — convert to named format:
   ```yaml
   # Before
   - "Component specifies its filesystem path"

   # After
   - checker: has_field
     params:
       field: path
     text: "Component specifies its filesystem path"
   ```

2. **If no checker applies** — leave as a plain string for LLM judgment:
   ```yaml
   - "ADR explains the rationale for the chosen approach"
   ```

3. **If already enforced by schema validation** — mark as schema-covered:
   ```yaml
   - text: "Entry body is non-empty"
     covered_by: schema
   ```

## Gradual Adoption: Warn vs Fail in CI

Whole-KB QA and `pyrite ci` enforce named rubric checks. When migrating an existing KB, you can choose whether named rubric checker findings fail `pyrite ci` or are reported as warnings without failing CI:

1. **Per-KB setting** in `kb.yaml`:
   ```yaml
   name: my-kb
   rubric_action: warn  # findings report as warnings and pyrite ci exits 0 (default: fail)
   ```
2. **Per-checker setting** in `evaluation_rubric`:
   ```yaml
   evaluation_rubric:
     - checker: has_field
       params:
         field: path
       action: warn   # this check warns without failing ci; others follow the KB default
       text: "Component specifies its filesystem path"
   ```

Existing bulk missing-tag and missing-link findings keep failing CI. Invalid
`rubric_action` or item `action` values fail CI and name the key and allowed
values (`warn`, `fail`).

## Verifying Migration

```bash
# See current coverage — checkers vs judgment-only
pyrite qa checkers --kb <name>

# Run validation to catch issues
pyrite qa validate --kb <name>
```

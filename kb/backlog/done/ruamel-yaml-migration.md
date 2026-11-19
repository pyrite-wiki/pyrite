---
id: ruamel-yaml-migration
title: "Migrate from PyYAML to ruamel.yaml"
type: backlog_item
tags: [yaml, git, core, developer-experience]
kind: improvement
status: done
priority: high
effort: S
---

# Migrate from PyYAML to ruamel.yaml

Replace PyYAML with ruamel.yaml for round-trip-safe YAML serialization. Critical for git-native storage where programmatic edits should not reformat entire files.

## Problem

PyYAML's `safe_dump` does not preserve comments, blank lines, quoting style, or flow/block style choices. When Pyrite programmatically updates an entry (adds a tag, changes a date), the entire frontmatter gets reformatted, producing noisy git diffs that obscure the actual change.

## Scope

- Add `ruamel.yaml` to dependencies
- Create `pyrite/utils/yaml.py` with `load_yaml()`, `dump_yaml()`, `load_yaml_file()`, `dump_yaml_file()` wrappers
- Migrate ~12 call sites from `yaml.safe_load`/`yaml.safe_dump` to wrapper functions
- Update `Entry.to_markdown()` and `Entry.from_markdown()`
- Add round-trip fidelity tests (load → save → diff shows no changes)
- Evaluate removing PyYAML dependency

## Key Files

- `pyproject.toml` — dependency change
- `pyrite/utils/yaml.py` — new wrapper module
- `pyrite/models/base.py` — to_markdown, from_markdown
- `pyrite/schema.py` — KBSchema.from_yaml
- `pyrite/config.py` — config load/save
- `pyrite/storage/repository.py` — frontmatter parsing
- `pyrite/services/repo_service.py` — kb.yaml loading
- `pyrite/github_auth.py` — auth config

## Acceptance Criteria

- [ ] All existing tests pass with ruamel.yaml
- [ ] Round-trip test: load entry → save without changes → file is byte-identical
- [ ] Round-trip test: load entry → change one field → save → diff shows only that field
- [ ] Comments in kb.yaml preserved through load/save cycle
- [ ] No formatting noise in git diffs from programmatic updates

## Completed

Implemented in commit `46ea1d6`. Created `pyrite/utils/yaml.py` with round-trip wrappers using `ruamel.yaml.YAML()` with `preserve_quotes=True`. Migrated all call sites in config.py, base.py, schema.py, repository.py, repo_service.py, github_auth.py. 18 new tests in `tests/test_yaml_utils.py`. All 376 backend tests pass.

## References

- [ADR-0008](../../adrs/0008-structured-data-and-schema.md)
- [Design Doc](../../designs/structured-data-and-schema.md)

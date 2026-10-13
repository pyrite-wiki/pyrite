---
id: building-an-extension
type: runbook
title: "Building a Pyrite Extension"
runbook_kind: howto
audience: "developers"
tags: [extensions, howto]
links:
- plugin-developer-guide
---

Quick-start guide for building extensions. See [[plugin-developer-guide]] for the comprehensive reference.

## Prerequisites
- Python 3.11+
- pyrite installed in a venv (`pip install -e .`)

## Steps

### 1. Scaffold the package
```bash
mkdir -p extensions/<name>/src/pyrite_<name> extensions/<name>/tests
```

### 2. Create pyproject.toml
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "pyrite-<name>"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []

[project.entry-points."pyrite.plugins"]
<name> = "pyrite_<name>.plugin:<Name>Plugin"

[tool.hatch.build.targets.wheel]
packages = ["src/pyrite_<name>"]
```

### 3. Implement entry types
- Extend NoteEntry or DocumentEntry
- Override `entry_type` property, `to_frontmatter()`, `from_frontmatter()`
- Define enum tuples as module-level constants

### 4. Implement validators
- Single function: `validate_<name>(entry_type, data, context) -> list[dict]`
- Dispatch on entry_type, return `[]` for unrelated types

### 5. Implement plugin class
- Set `name` attribute
- Implement protocol methods: `get_entry_types()`, `get_validators()`, etc.
- Import CLI lazily in `get_cli_commands()` for graceful degradation

### 6. Write tests following the 8-section pattern

### 7. Install and verify
```bash
pip install -e extensions/<name>              # system
.venv/bin/pip install -e extensions/<name>    # venv (for pre-commit)
python -m pytest extensions/<name>/tests/ -v
# Diagnostic example: a focused plugin-integration guard
python -m pytest tests/test_plugin_integration.py -v
```

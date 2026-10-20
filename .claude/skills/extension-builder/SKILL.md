---
name: extension-builder
description: "Use when the user wants to scaffold a new Pyrite extension from a description. Generates the full directory structure, plugin class, entry types, validators, preset, tests, and installs in dev mode."
---

# Extension Builder Skill

Scaffolds a complete Pyrite extension from a user description. Generates all required files, optionally generates hooks/tables/workflows/CLI, and verifies the plugin loads.

**Announce at start:** "I'm using the extension-builder skill to scaffold your extension."

## Process

### Step 1: Gather Requirements

Ask the user for:

1. **Extension name** — kebab-case (e.g., `recipe-manager`). Python package becomes `pyrite_recipe_manager`.
2. **Description** — one sentence describing what the extension does.
3. **Entry types** — each with:
   - Type name (snake_case, e.g., `recipe`)
   - Custom fields with types (str, int, list[str], bool, etc.)
   - Required fields
4. **Optional capabilities** — which of these does the extension need?
   - Hooks (before_save, after_save, before_delete, after_delete)
   - Custom DB tables
   - Workflows (state machines)
   - CLI commands
   - MCP tools
   - Relationship types

### Step 2: Generate Files

Generate the full extension directory under `extensions/<name>/`:

#### Always generated:

**`pyproject.toml`**
```
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "pyrite-<name>"
version = "0.1.0"
description = "<description>"
requires-python = ">=3.11"
dependencies = []

[project.entry-points."pyrite.plugins"]
<snake_name> = "pyrite_<snake_name>.plugin:<ClassName>Plugin"

[tool.hatch.build.targets.wheel]
packages = ["src/pyrite_<snake_name>"]
```

**`src/pyrite_<snake_name>/__init__.py`** — empty

**`src/pyrite_<snake_name>/plugin.py`** — Plugin class implementing PyritePlugin Protocol. Wire up all entry types, validators, presets, and optional capabilities.

**`src/pyrite_<snake_name>/entry_types.py`** — One `@dataclass` class per entry type. Each MUST implement:
- `entry_type` property returning the type key
- `to_frontmatter()` calling `super().to_frontmatter()` and adding custom fields
- `from_frontmatter()` classmethod with full field parsing

**`src/pyrite_<snake_name>/validators.py`** — One validate function per entry type. MUST return `[]` for unrelated types.

**`src/pyrite_<snake_name>/preset.py`** — KB preset dict with type definitions, policies, validation rules.

#### Optionally generated:

**`src/pyrite_<snake_name>/hooks.py`** — If hooks requested. before_save/after_save functions following the hook pattern.

**`src/pyrite_<snake_name>/tables.py`** — If custom tables requested. Table definitions with extension-prefixed names.

**`src/pyrite_<snake_name>/workflows.py`** — If workflows requested. State machine definitions.

**`src/pyrite_<snake_name>/cli.py`** — If CLI commands requested. Typer app with subcommands.

**`tests/test_<snake_name>.py`** — ALWAYS generated. Comprehensive test file with 8-section structure:
1. Entry type round-trip (to_frontmatter → from_frontmatter)
2. Entry type defaults
3. Validator accepts valid data
4. Validator rejects invalid data
5. Plugin registration
6. Preset structure
7. Entry type property
8. Integration (if hooks/tables/workflows present)

### Step 3: Install and Verify

```bash
# Install in dev mode
cd <pyrite repo root>
uv pip install --python .venv/bin/python -e extensions/<name>

# Verify plugin loads
.venv/bin/python -c "from pyrite.plugins.registry import get_registry; r = get_registry(); print(r.list_plugins())"

# Run extension tests
.venv/bin/pytest extensions/<name>/tests/ -v
```

## Reference Files

For patterns and conventions, consult:
- `.claude/skills/pyrite-dev/extensions.md` — comprehensive extension building guide
- `.claude/skills/pyrite-dev/testing.md` — test patterns and conventions

For working examples, read these existing extensions:
- `extensions/zettelkasten/` — simplest (2 entry types, no hooks)
- `extensions/encyclopedia/` — medium (2 entry types and the article review workflow)
- `extensions/social/` — complex (2 entry types and the hooks, no workflow)
- `extensions/software-kb/` — full-featured (CLI, MCP tools, multiple types)

## Key Rules

1. Entry point group MUST be `"pyrite.plugins"` — no other group name works
2. `entry_type` property MUST match the key in `get_entry_types()`
3. `from_frontmatter()` MUST call `generate_entry_id(title)` when `meta.get("id")` is empty
4. Validators MUST return `[]` for unrelated types
5. Prefix custom DB table names with extension name
6. Hooks MUST return the entry (before_save) or None (after_save). A before_save hook may refuse by raising; on update its changes are ignored, only create keeps them
7. Declare `capabilities` for every method the plugin implements; the registry skips methods whose capability is missing
8. All list/dict fields MUST handle None → default: `meta.get("field", []) or []`
9. Tests MUST pass before claiming completion

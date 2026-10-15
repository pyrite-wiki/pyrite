# Building Pyrite Extensions

## Directory Layout

```
extensions/my-extension/
  pyproject.toml                           # Package metadata + entry point
  src/pyrite_my_extension/
    __init__.py                            # Empty or re-exports
    plugin.py                              # Plugin class (implements PyritePlugin Protocol)
    entry_types.py                         # Custom Entry subclasses
    validators.py                          # Validation functions
    preset.py                              # KB preset definition
    hooks.py                               # Lifecycle hooks (optional)
    tables.py                              # Custom DB tables (optional)
    workflows.py                           # State machine definitions (optional)
    cli.py                                 # Typer commands (optional)
  tests/
    test_my_extension.py                   # 8-section test structure
```

## pyproject.toml

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "pyrite-my-extension"
version = "0.1.0"
description = "Description of the extension"
requires-python = ">=3.11"
dependencies = []  # pyrite is a peer dependency

[project.entry-points."pyrite.plugins"]
my_extension = "pyrite_my_extension.plugin:MyExtensionPlugin"

[tool.hatch.build.targets.wheel]
packages = ["src/pyrite_my_extension"]
```

**Critical:** The entry point group MUST be `"pyrite.plugins"`. The key (`my_extension`) becomes the plugin's discovery name.

## Plugin Class

Implement the `PyritePlugin` Protocol. All methods are optional — implement only what you need.

```python
"""My Extension plugin."""

from collections.abc import Callable
from typing import Any

from .entry_types import MyEntry
from .preset import MY_PRESET
from .validators import validate_my_type


class MyExtensionPlugin:
    name = "my_extension"

    def get_entry_types(self) -> dict[str, type]:
        return {"my_type": MyEntry}

    def get_kb_types(self) -> list[str]:
        return ["my_kb_type"]

    def get_validators(self) -> list[Callable]:
        return [validate_my_type]

    def get_kb_presets(self) -> dict[str, dict]:
        return {"my_preset": MY_PRESET}

    def get_cli_commands(self) -> list[tuple[str, Any]]:
        from .cli import my_app  # Lazy import
        return [("my-cmd", my_app)]

    def get_mcp_tools(self, tier: str) -> dict[str, dict]:
        tools = {}
        if tier in ("read", "write", "admin"):
            tools["my_tool"] = {
                "description": "What it does",
                "inputSchema": {
                    "type": "object",
                    "properties": { ... },
                    "required": [...],
                },
                "handler": self._mcp_handler,
            }
        return tools

    def get_relationship_types(self) -> dict[str, dict]:
        return {
            "my_relation": {
                "inverse": "my_inverse",
                "description": "Describes the relationship",
            },
            "my_inverse": {
                "inverse": "my_relation",
                "description": "Inverse of the relationship",
            },
        }

    def get_hooks(self) -> dict[str, list[Callable]]:
        from .hooks import before_save_check, after_save_update
        return {
            "before_save": [before_save_check],
            "after_save": [after_save_update],
        }

    def get_db_tables(self) -> list[dict]:
        from .tables import MY_TABLES
        return MY_TABLES

    def get_workflows(self) -> dict[str, dict]:
        from .workflows import MY_WORKFLOW
        return {"my_workflow": MY_WORKFLOW}

    def get_field_schemas(self) -> dict[str, dict[str, dict]]:
        return {
            "my_type": {
                "category": {
                    "type": "select",
                    "options": ["a", "b", "c"],
                    "default": "a",
                    "description": "Entry category",
                },
            },
        }

    def get_type_metadata(self) -> dict[str, dict]:
        return {
            "my_type": {
                "ai_instructions": "Guidance for AI agents creating this type",
                "field_descriptions": {"category": "The entry's classification"},
            },
        }

    def get_collection_types(self) -> dict[str, dict]:
        return {}  # Only if your plugin defines custom collection types

    def set_context(self, ctx) -> None:
        """Receive PluginContext with DB, config, services."""
        self._ctx = ctx
```

## Entry Type Contract

Every custom entry type must implement these three methods:

```python
from dataclasses import dataclass
from typing import Any
from pyrite.models.core_types import NoteEntry  # or Entry base
from pyrite.schema import Provenance, generate_entry_id
from pyrite.models.base import parse_datetime, parse_links, parse_sources

@dataclass
class MyEntry(NoteEntry):
    """Description of the entry type."""

    custom_field: str = ""
    custom_list: list[str] = field(default_factory=list)

    @property
    def entry_type(self) -> str:
        return "my_type"  # Must match the key in get_entry_types()

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "my_type"
        # Only include non-default values
        if self.custom_field:
            meta["custom_field"] = self.custom_field
        if self.custom_list:
            meta["custom_list"] = self.custom_list
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "MyEntry":
        prov_data = meta.get("provenance")
        provenance = Provenance.from_dict(prov_data) if prov_data else None

        entry_id = meta.get("id", "")
        if not entry_id:
            entry_id = generate_entry_id(meta.get("title", ""))

        return cls(
            id=entry_id,
            title=meta.get("title", ""),
            body=body,
            summary=meta.get("summary", ""),
            tags=meta.get("tags", []) or [],
            sources=parse_sources(meta.get("sources")),
            links=parse_links(meta.get("links")),
            provenance=provenance,
            metadata=meta.get("metadata", {}),
            created_at=parse_datetime(meta.get("created_at")),
            updated_at=parse_datetime(meta.get("updated_at")),
            custom_field=meta.get("custom_field", ""),
            custom_list=meta.get("custom_list", []) or [],
        )
```

**Key rules:**
- `entry_type` property must return the exact key used in `get_entry_types()`
- `from_frontmatter` must call `generate_entry_id(title)` when `meta.get("id")` is empty
- `to_frontmatter` should omit default values (reduces noise in markdown files)
- Always handle `None` → default for list/dict fields: `meta.get("field", []) or []`

## Validator Pattern

```python
def validate_my_type(
    entry_type: str,
    data: dict[str, Any],
    context: dict[str, Any],
) -> list[dict]:
    """Validate my_type-specific rules."""
    errors: list[dict] = []

    # IMPORTANT: Return empty list for types you don't handle
    if entry_type != "my_type":
        return errors

    # Required field check
    if not data.get("custom_field"):
        errors.append({
            "field": "custom_field",
            "rule": "required",
            "expected": "non-empty custom_field",
            "got": data.get("custom_field"),
        })

    # Enum check
    valid_values = ("a", "b", "c")
    val = data.get("category", "a")
    if val not in valid_values:
        errors.append({
            "field": "category",
            "rule": "enum",
            "expected": list(valid_values),
            "got": val,
        })

    # Soft warnings (don't block save, just inform)
    if not data.get("links"):
        errors.append({
            "field": "links",
            "rule": "should_have_links",
            "severity": "warning",
            "expected": "at least one link",
            "got": None,
        })

    return errors
```

**Critical:** Validators MUST return `[]` for unrelated types. Plugin validators always run for all entries in schema validation, even for types not declared in `kb.yaml`. See [gotchas.md](gotchas.md).

## Preset Pattern

```python
MY_PRESET = {
    "name": "my-kb-name",
    "description": "What this KB type is for",
    "types": {
        "my_type": {
            "description": "Description of the type",
            "required": ["title", "custom_field"],
            "optional": ["category", "tags"],
            "subdirectory": "entries/",
        },
    },
    "policies": {
        "private": False,
        "single_author": False,
    },
    "validation": {
        "enforce": True,
        "rules": [
            {"field": "category", "enum": ["a", "b", "c"]},
        ],
    },
    "directories": ["entries", "media"],
}
```

## DB Tables Pattern

Custom tables are engagement-tier data: local to this install, not git-tracked.

```python
MY_TABLES = [
    {
        "name": "my_extension_reviews",  # Prefix with extension name
        "columns": [
            {"name": "id", "type": "INTEGER", "primary_key": True},
            {"name": "entry_id", "type": "TEXT", "nullable": False},
            {"name": "kb_name", "type": "TEXT", "nullable": False},
            {"name": "reviewer_id", "type": "TEXT", "nullable": False},
            {"name": "status", "type": "TEXT", "nullable": False},
            {"name": "created_at", "type": "TEXT", "nullable": False},
        ],
        "indexes": [
            {"columns": ["entry_id", "kb_name"]},
            {"columns": ["reviewer_id"]},
        ],
    },
]
```

**Naming rule:** Prefix table names with extension name to avoid collisions.

## Hooks Pattern

```python
from pyrite.models.base import Entry
from typing import Any

def before_save_check(entry: Entry, context: dict[str, Any]) -> Entry:
    """before_save hooks receive (entry, context) and must return the entry."""
    if entry.entry_type != "my_type":
        return entry  # Don't interfere with other types

    # context keys: kb_name, user, kb_schema, operation ("create"|"update"|"delete")
    # Can modify entry or raise to abort
    if context.get("operation") == "create":
        # Set defaults, validate state, etc.
        pass

    return entry  # MUST return entry

def after_save_update(entry: Entry, context: dict[str, Any]) -> None:
    """after_save hooks receive (entry, context) and return nothing."""
    if entry.entry_type != "my_type":
        return

    # Side effects only — DB updates, logging, notifications
    # Failures should be logged, not raised (best-effort)
    pass
```

**DB access in hooks:** Hooks receive DB and config via `PluginContext`, injected by `set_context()` after plugin discovery. Store the context in your plugin class and use it in hooks and MCP handlers:

```python
class MyExtensionPlugin:
    name = "my_extension"

    def set_context(self, ctx):
        """Receive DI context — store for use in hooks and MCP handlers."""
        self._ctx = ctx

    def get_hooks(self):
        def after_save_update(entry, context):
            if entry.entry_type != "my_type":
                return
            # Access DB via the injected context
            self._ctx.db.execute_raw("INSERT INTO my_table ...")
        return {"after_save": [after_save_update]}
```

See `pyrite/plugins/context.py` for `PluginContext` fields (db, config, kb_type, etc.).

## Workflow Pattern

```python
MY_WORKFLOW = {
    "states": ["draft", "in_review", "published", "archived"],
    "initial": "draft",
    "transitions": [
        {"from": "draft", "to": "in_review", "requires": "write"},
        {"from": "in_review", "to": "published", "requires": "reviewer"},
        {"from": "in_review", "to": "draft", "requires": "reviewer"},
        {"from": "published", "to": "archived", "requires": "admin"},
        {"from": "published", "to": "in_review", "requires": "write", "requires_reason": True},
    ],
    "field": "review_status",  # Entry field this workflow controls
}
```

## Installation and Development

```bash
# Install in development mode (required for plugin discovery)
cd extensions/my-extension
pip install -e . --target ../../.venv/lib/python3.*/site-packages/

# Or simpler:
cd /path/to/repo
.venv/bin/pip install -e extensions/my-extension

# Verify plugin loads
.venv/bin/python -c "from pyrite.plugins.registry import get_registry; print(get_registry().list_plugins())"
```

**Pre-push hook:** the affected tests run from `.venv/`. If your extension isn't `pip install -e` in the venv, tests importing it will fail with `ModuleNotFoundError`.

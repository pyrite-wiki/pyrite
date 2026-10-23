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
from typing import Any, ClassVar

from pyrite.plugins.capabilities import Capability

from .entry_types import MyEntry
from .preset import MY_PRESET
from .validators import validate_my_type


class MyExtensionPlugin:
    name = "my_extension"
    # Declare a capability for every method group you implement; the registry
    # skips methods whose capability is missing (map: _METHOD_CAPABILITIES in
    # pyrite/plugins/registry.py).
    capabilities: ClassVar[set[Capability]] = {
        Capability.SCHEMA,    # get_entry_types
        Capability.STORAGE,   # get_validators, get_hooks
        Capability.SURFACE,   # get_kb_presets, get_cli_commands, get_mcp_tools, get_kb_types
        Capability.DOMAIN,    # get_relationship_types, get_workflows
    }

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
            # A tool that takes an entry id and an optional kb_name:
            tools["my_item_tool"] = {
                "description": "Act on one item",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "kb_name": {"type": "string", "description": "KB name"},
                        "item_id": {"type": "string", "description": "Item entry ID"},
                    },
                    "required": ["item_id"],
                },
                "locates_entry": {"id_args": ["item_id"], "entry_type": "my_type"},
                "handler": self._mcp_item_handler,  # args["kb_name"] is set
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

### MCP tools that take an entry id: `locates_entry`

A tool whose arguments carry entry ids declares `locates_entry` beside its
`handler` (#1310): `id_args`, every argument that names an entry (a string or
a list of strings; anchors such as `after`/`before` count), and `entry_type`,
the one type the tool acts on (leave it out for a tool that takes any type).
The MCP dispatcher then finds the ids with `KBService.locate`, the rule
`kb_get` and the CLI's `require_entry_kb` use: with `kb_name` blank or
absent, the one readable KB holding them, `AMBIGUOUS` with `candidate_kbs` when
several do, `ENTRY_NOT_FOUND` when none does; every id must be in that one KB.
It sets `args["kb_name"]` to the KB it found **before** the read, write and
landing checks, so the handler always works in a checked KB.

- The handler reads `args["kb_name"]` and never resolves an id itself, never
  falls back to a default KB, and never works in `kb_name or ""`.
- `kb_name` is the handler's KB. Another KB argument (`kb`, `target_kb`, ...)
  is checked but does not choose where the id is found.
- A call whose id arguments are all blank (missing, empty, whitespace) is
  refused `VALIDATION_FAILED` before any check: the handler never sees a call
  without a located id, so it needs no fallback for one.
- `kb_name` is registered as optional even if your schema lists it in
  `required`: the MCP SDK validates arguments against the schema before the
  dispatcher, and a required `kb_name` would refuse the call the lookup is for.
  Leave it out of `required` anyway.
- With no `kb_name`, the lookup reads the canonical index, so an id that
  exists only in the caller's pending change set is found only when the call
  names its KB.
- Only the top-level `id_args` are located. An id nested in an object
  argument (`investigation_promote_claim`'s `endpoint_fields`) is not; write it
  only in the located KB, never use it to choose one.
- `tests/test_plugin_mcp_tools_locate_ids.py` walks the registry and fails for
  a tool with an id argument (by name, or by a description such as "Item entry
  ID") and no `locates_entry`. Add a `Case` for your tool there.

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

Hooks may refuse a write, not set fields; follow [the protocol contract](../../../pyrite/plugins/protocol.py).

```python
from pyrite.models.base import Entry
from typing import Any

def before_save_check(entry: Entry, context: dict[str, Any]) -> Entry:
    """Refuse invalid state without changing the entry."""
    if entry.entry_type != "my_type":
        return entry  # Don't interfere with other types

    # context keys: kb_name, user, kb_schema, operation ("create"|"update"|"delete")
    # Check state here and raise ValidationError to refuse if necessary.
    return entry

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
    capabilities = {Capability.STORAGE, Capability.CONTEXT}  # get_hooks, set_context

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

## 8-Section Extension Test Structure

Every extension test file follows this structure. Not every section is required — only include sections for capabilities your extension implements.

```python
"""Tests for the my_extension extension."""

from pyrite_my_extension.entry_types import MyEntry
from pyrite_my_extension.plugin import MyExtensionPlugin
from pyrite_my_extension.preset import MY_PRESET
from pyrite_my_extension.validators import validate_my_type

from pyrite.plugins.registry import PluginRegistry

# =========================================================================
# 1. Plugin registration
# =========================================================================

class TestPluginRegistration:
    def test_plugin_has_name(self):
        plugin = MyExtensionPlugin()
        assert plugin.name == "my_extension"

    def test_register_with_registry(self):
        registry = PluginRegistry()
        registry.register(MyExtensionPlugin())
        assert "my_extension" in registry.list_plugins()

    def test_entry_types_registered(self):
        registry = PluginRegistry()
        registry.register(MyExtensionPlugin())
        types = registry.get_all_entry_types()
        assert "my_type" in types  # Use `in`, not len()

    # ... validators, commands, presets, kb_types, mcp_tools

# =========================================================================
# 2. Entry types
# =========================================================================

class TestMyEntry:
    def test_default_values(self): ...
    def test_entry_type_property(self): ...
    def test_to_frontmatter(self): ...
    def test_to_frontmatter_omits_defaults(self): ...
    def test_from_frontmatter(self): ...
    def test_from_frontmatter_generates_id(self): ...
    def test_roundtrip_markdown(self): ...

# =========================================================================
# 3. Validators
# =========================================================================

class TestValidators:
    def test_valid_data_passes(self): ...
    def test_required_field_missing(self): ...
    def test_invalid_enum_value(self): ...
    def test_ignores_unrelated_types(self): ...  # CRITICAL: always include

# =========================================================================
# 4. Hooks (if applicable)
# =========================================================================

class TestHooks:
    def test_before_save_modifies_entry(self): ...
    def test_before_save_raises_on_violation(self): ...
    def test_after_save_side_effect(self): ...
    def test_hooks_ignore_unrelated_types(self): ...

# =========================================================================
# 5. Workflows (if applicable)
# =========================================================================

class TestWorkflows:
    def test_valid_transitions(self): ...
    def test_invalid_transition_rejected(self): ...
    def test_initial_state(self): ...

# =========================================================================
# 6. DB Tables (if applicable)
# =========================================================================

class TestDBTables:
    def test_table_definitions_valid(self): ...
    def test_table_names_prefixed(self): ...

# =========================================================================
# 7. Preset
# =========================================================================

class TestPreset:
    def test_preset_structure(self): ...
    def test_preset_contains_expected_types(self): ...
    def test_preset_directories(self): ...

# =========================================================================
# 8. Core integration
# =========================================================================

class TestCoreIntegration:
    def test_entry_class_resolution(self): ...
    def test_entry_from_frontmatter_resolution(self): ...
    def test_relationship_types_merged(self): ...
```

## Installation and Development

```bash
# Install in development mode (required for plugin discovery).
# Worktree venvs have no pip, so use uv (or re-run scripts/setup-checkout.sh):
cd /path/to/repo
uv pip install --python .venv/bin/python -e extensions/my-extension

# Verify plugin loads
.venv/bin/python -c "from pyrite.plugins.registry import get_registry; print(get_registry().list_plugins())"
```

**Pre-push hook:** the affected tests run from `.venv/`. If your extension isn't `pip install -e` in the venv, tests importing it will fail with `ModuleNotFoundError`.

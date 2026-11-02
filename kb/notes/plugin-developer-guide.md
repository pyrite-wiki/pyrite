---
id: plugin-developer-guide
title: "Plugin Developer Guide"
type: standard
tags: [plugins, developer-guide, standards]
---

# Pyrite Plugin Developer Guide

## 1. Introduction

Pyrite plugins extend the platform with domain-specific functionality. A plugin can add new entry types, CLI commands, MCP tools for AI agents, custom database tables, validation rules, lifecycle hooks, workflows, relationship types, and KB presets — all through a single plugin class that implements the `PyritePlugin` protocol.

### What plugins can do

| Capability | Method | Example |
|---|---|---|
| Custom entry types | `get_entry_types()` | Zettelkasten adds `zettel` and `literature_note` types |
| CLI sub-commands | `get_cli_commands()` | Social adds `pyrite social vote`, `pyrite social top` |
| MCP tools for AI agents | `get_mcp_tools(tier)` | Encyclopedia adds `wiki_review_queue`, `wiki_submit_review` |
| Custom DB tables | `get_db_tables()` | Social adds `social_vote` and `social_reputation_log` tables |
| Validation rules | `get_validators()` | Encyclopedia enforces source counts for GA/FA articles |
| Lifecycle hooks | `get_hooks()` | Social enforces author-only editing via `before_save` hook |
| State machine workflows | `get_workflows()` | Encyclopedia defines `draft -> under_review -> published` |
| Custom relationship types | `get_relationship_types()` | Zettelkasten adds `elaborates`, `branches_from`, `synthesizes` |
| KB presets | `get_kb_presets()` | Each extension provides a scaffold for `pyrite kb init --preset` |
| KB type identifiers | `get_kb_types()` | Social registers `"social"` as a KB type |
| Rich field schemas | `get_field_schemas()` | Zettelkasten declares `zettel_type` as a select with options |
| Type metadata / AI instructions | `get_type_metadata()` | Encyclopedia provides AI guidance for writing articles |
| Collection types | `get_collection_types()` | Investigation adds `evidence-board` collection type |
| DI context injection | `set_context(ctx)` | Plugin receives DB, config, KB type via PluginContext |

### When to write a plugin vs. use kb.yaml config types

You do **not** need a plugin if you only need:

- **Custom type names with custom fields**: Define them in `kb.yaml` and they work via `GenericEntry` (fields stored in `metadata` dict, round-tripped through frontmatter).
- **Required/optional field declarations**: Declare them in `kb.yaml` under `types.<name>.required` and `types.<name>.optional`.
- **Basic validation rules** (ranges, enums, formats): Add them to `kb.yaml` under `validation.rules`.

You **do** need a plugin when you want:

- **Typed Python dataclass entries** with named fields, IDE autocomplete, and type safety.
- **Custom validation logic** beyond simple range/enum checks.
- **Lifecycle hooks** (permission enforcement, side effects on save/delete).
- **CLI commands** specific to your domain.
- **MCP tools** for AI agents.
- **Custom database tables** for engagement data (votes, reviews, etc.).
- **Workflow state machines** with role-based transitions.

---

## 2. Quick Start

Here is a minimal plugin in about 20 lines — it registers a name and one custom entry type:

```python
# src/pyrite_minimal/plugin.py
from pyrite.models.core_types import NoteEntry
from dataclasses import dataclass
from typing import Any

@dataclass
class BookmarkEntry(NoteEntry):
    url: str = ""

    @property
    def entry_type(self) -> str:
        return "bookmark"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "bookmark"
        if self.url:
            meta["url"] = self.url
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "BookmarkEntry":
        from pyrite.models.base import parse_datetime, parse_links, parse_sources
        from pyrite.schema import Provenance, generate_entry_id
        prov_data = meta.get("provenance")
        provenance = Provenance.from_dict(prov_data) if prov_data else None
        entry_id = meta.get("id", "") or generate_entry_id(meta.get("title", ""))
        return cls(
            id=entry_id, title=meta.get("title", ""), body=body,
            summary=meta.get("summary", ""),
            tags=meta.get("tags", []) or [],
            sources=parse_sources(meta.get("sources")),
            links=parse_links(meta.get("links")),
            provenance=provenance,
            metadata=meta.get("metadata", {}),
            created_at=parse_datetime(meta.get("created_at")),
            updated_at=parse_datetime(meta.get("updated_at")),
            url=meta.get("url", ""),
        )

class MinimalPlugin:
    name = "minimal"

    def get_entry_types(self) -> dict[str, type]:
        return {"bookmark": BookmarkEntry}
```

Register it in `pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "pyrite-minimal"
version = "0.1.0"
description = "Minimal bookmark plugin for pyrite"
requires-python = ">=3.11"
dependencies = []  # pyrite is a peer dependency

[project.entry-points."pyrite.plugins"]
minimal = "pyrite_minimal.plugin:MinimalPlugin"

[tool.hatch.build.targets.wheel]
packages = ["src/pyrite_minimal"]
```

Install it:

```bash
pip install -e extensions/minimal/
```

After installation, Pyrite automatically discovers the plugin via Python entry points. The `bookmark` type is immediately available in the CLI, API, MCP server, and web UI.

---

## 3. Plugin Structure

### Directory layout

Every extension follows this proven layout:

```
extensions/<name>/
  pyproject.toml                    # Package metadata + entry point
  src/pyrite_<name>/
    __init__.py                     # Re-exports the plugin class
    plugin.py                       # Main plugin class (PyritePlugin)
    entry_types.py                  # Dataclass Entry subclasses
    validators.py                   # Validation callables
    cli.py                          # Typer sub-app (optional)
    preset.py                       # KB preset definition (optional)
    hooks.py                        # Lifecycle hooks (optional)
    tables.py                       # Custom DB table defs (optional)
    workflows.py                    # State machines (optional)
  tests/
    test_<name>.py
```

### pyproject.toml entry points

The critical line is the entry point declaration under the `"pyrite.plugins"` group:

```toml
[project.entry-points."pyrite.plugins"]
zettelkasten = "pyrite_zettelkasten.plugin:ZettelkastenPlugin"
```

Real examples from the three proof-of-concept extensions:

```toml
# extensions/zettelkasten/pyproject.toml
[project.entry-points."pyrite.plugins"]
zettelkasten = "pyrite_zettelkasten.plugin:ZettelkastenPlugin"

# extensions/social/pyproject.toml
[project.entry-points."pyrite.plugins"]
social = "pyrite_social.plugin:SocialPlugin"

# extensions/encyclopedia/pyproject.toml
[project.entry-points."pyrite.plugins"]
encyclopedia = "pyrite_encyclopedia.plugin:EncyclopediaPlugin"
```

The format is: `<entry_point_name> = "<package>.<module>:<ClassName>"`

### Plugin class pattern

The plugin class is a plain Python class (not a subclass — it uses structural subtyping via `Protocol`). It must have a `name` attribute and can implement any subset of the 15 protocol methods:

```python
# From extensions/zettelkasten/src/pyrite_zettelkasten/plugin.py
class ZettelkastenPlugin:
    """Zettelkasten plugin for pyrite."""

    name = "zettelkasten"

    def get_entry_types(self) -> dict[str, type]:
        return {
            "zettel": ZettelEntry,
            "literature_note": LiteratureNoteEntry,
        }

    def get_kb_types(self) -> list[str]:
        return ["zettelkasten"]

    def get_cli_commands(self) -> list[tuple[str, Any]]:
        from .cli import zettel_app
        return [("zettel", zettel_app)]

    def get_mcp_tools(self, tier: str) -> dict[str, dict]:
        tools = {}
        if tier in ("read", "write", "admin"):
            tools["zettel_inbox"] = { ... }
            tools["zettel_graph"] = { ... }
        return tools

    def get_relationship_types(self) -> dict[str, dict]:
        return {
            "elaborates": {"inverse": "elaborated_by", "description": "..."},
            ...
        }

    def get_validators(self) -> list[Callable]:
        return [validate_zettel]

    def get_kb_presets(self) -> dict[str, dict]:
        return {"zettelkasten": ZETTELKASTEN_PRESET}
```

**All methods are optional.** Only implement the ones you need. The registry uses `hasattr()` to check which methods a plugin provides:

```python
# From pyrite/plugins/registry.py
if hasattr(plugin, "get_entry_types"):
    try:
        plugin_types = plugin.get_entry_types()
        if plugin_types:
            types.update(plugin_types)
    except Exception as e:
        logger.warning("Plugin %s get_entry_types failed: %s", plugin.name, e)
```

### The `__init__.py` convention

Each extension's `__init__.py` re-exports the plugin class for convenience:

```python
# extensions/social/src/pyrite_social/__init__.py
"""Social KB extension for pyrite."""
from .plugin import SocialPlugin
__all__ = ["SocialPlugin"]
```

---

## 4. Entry Types

### How entry types work

Pyrite resolves entry types through a three-step lookup chain in `get_entry_class()` (defined in `pyrite/models/core_types.py`):

1. **Core types** — 8 built-in types (`note`, `person`, `organization`, `event`, `document`, `topic`, `relationship`, `timeline`)
2. **Plugin types** — types registered by plugins via `get_entry_types()`
3. **GenericEntry fallback** — for types defined only in `kb.yaml` config; stores custom fields in `metadata` dict

```python
# From pyrite/models/core_types.py
def get_entry_class(entry_type: str) -> type[Entry]:
    """Get the entry class for a type name."""
    if entry_type in ENTRY_TYPE_REGISTRY:
        return ENTRY_TYPE_REGISTRY[entry_type]

    # Check plugin registry for custom entry types
    try:
        from ..plugins import get_registry
        plugin_types = get_registry().get_all_entry_types()
        if entry_type in plugin_types:
            return plugin_types[entry_type]
    except Exception:
        pass

    from .generic import GenericEntry
    return GenericEntry
```

### Creating a custom entry type

Custom entry types are Python `@dataclass` classes that extend an existing Entry subclass (usually `NoteEntry` or `PersonEntry`). Every entry type must implement three things:

1. `entry_type` property — returns the type identifier string
2. `to_frontmatter()` — serializes to YAML frontmatter dict
3. `from_frontmatter(meta, body)` — deserializes from frontmatter dict and body text

### Real example: ZettelEntry

From `extensions/zettelkasten/src/pyrite_zettelkasten/entry_types.py`:

```python
from dataclasses import dataclass, field
from typing import Any

from pyrite.models.base import parse_datetime, parse_links, parse_sources
from pyrite.models.core_types import NoteEntry
from pyrite.schema import Provenance, generate_entry_id

ZETTEL_TYPES = ("fleeting", "literature", "permanent", "hub")
MATURITY_LEVELS = ("seed", "sapling", "evergreen")
PROCESSING_STAGES = ("capture", "elaborate", "question", "review", "connect")


@dataclass
class ZettelEntry(NoteEntry):
    """Atomic knowledge note in the Zettelkasten tradition."""

    zettel_type: str = "fleeting"    # fleeting, literature, permanent, hub
    maturity: str = "seed"           # seed, sapling, evergreen
    source_ref: str = ""             # reference to source material
    processing_stage: str = ""       # capture, elaborate, question, review, connect

    @property
    def entry_type(self) -> str:
        return "zettel"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "zettel"
        # Only emit non-default values for clean YAML
        if self.zettel_type != "fleeting":
            meta["zettel_type"] = self.zettel_type
        if self.maturity != "seed":
            meta["maturity"] = self.maturity
        if self.source_ref:
            meta["source_ref"] = self.source_ref
        if self.processing_stage:
            meta["processing_stage"] = self.processing_stage
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "ZettelEntry":
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
            # Plugin-specific fields:
            zettel_type=meta.get("zettel_type", "fleeting"),
            maturity=meta.get("maturity", "seed"),
            source_ref=meta.get("source_ref", ""),
            processing_stage=meta.get("processing_stage", ""),
        )
```

### Key rules for entry types

1. **You MUST override `from_frontmatter()`** to map your custom fields. Even though `NoteEntry.from_frontmatter` uses `cls(...)`, it does not know about your extra dataclass fields. If you skip this override, your custom fields will silently be left at defaults.

2. **`to_frontmatter()` MUST set `meta["type"]`** to your plugin's type name. Call `super().to_frontmatter()` first, then override the type.

3. **Omit default values** from frontmatter output for cleaner YAML files. Check before adding: `if self.zettel_type != "fleeting":`.

4. **Use `generate_entry_id(title)`** as fallback when `meta.get("id")` is empty.

5. **Use the standard helpers** for parsing: `parse_datetime()`, `parse_links()`, `parse_sources()`, and `Provenance.from_dict()`.

### Extending PersonEntry

If your type represents a person-like entity, extend `PersonEntry` instead of `NoteEntry`. This gives you `role`, `affiliations`, `importance`, and `research_status` fields automatically:

```python
# From extensions/social/src/pyrite_social/entry_types.py
@dataclass
class UserProfileEntry(PersonEntry):
    """A user profile in a social knowledge base."""

    reputation: int = 0
    join_date: str = ""
    writeup_count: int = 0

    @property
    def entry_type(self) -> str:
        return "user_profile"
    ...
```

### GenericEntry fallback

Types defined only in `kb.yaml` (without a plugin) use `GenericEntry`. It stores all unknown frontmatter keys in `self.metadata` and promotes them back to top-level on serialization:

```python
# From pyrite/models/generic.py
class GenericEntry(Entry):
    _entry_type: str = "note"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        if self.summary:
            meta["summary"] = self.summary
        # Promote metadata keys to top-level frontmatter
        for key, value in self.metadata.items():
            if key not in meta:
                meta[key] = value
        return meta
```

This means simple custom types work without any plugin code — just declare them in `kb.yaml`.

---

## 5. Validators

### Validator signature

```python
def validate_my_plugin(
    entry_type: str,
    data: dict[str, Any],
    context: dict[str, Any]
) -> list[dict]:
```

**Parameters:**
- `entry_type` — the entry's type string (e.g., `"zettel"`, `"writeup"`, `"article"`)
- `data` — the entry's frontmatter fields as a flat dict
- `context` — a dict with keys:
  - `kb_name: str` — name of the knowledge base
  - `kb_schema: KBSchema | None` — the KB's schema object
  - `user: str` — current user identity
  - `existing_entry: Entry | None` — the existing entry (for updates, `None` for creates)

**Returns:** A `list[dict]` of validation issue dicts (NOT `list[str]` — see rule 4
below). Each dict has these keys:
- `field: str` — the field name that failed validation
- `rule: str` — a short identifier for the rule (e.g., `"required"`, `"enum"`, `"ga_min_sources"`)
- `expected` — what was expected (string or list)
- `got` — the actual value
- `message: str` (optional, recommended) — a human-readable message; preferred over the
  field/rule/expected/got rendering when present, and the only thing shown for an item
  with no `rule`
- `severity: str` (optional) — set to `"warning"` for non-blocking advisories; omit for hard errors

### Key rules for validators

1. **Return `[]` for unrelated entry types.** Your validator is called for every entry in every KB. If the entry type is not one your plugin owns, return an empty list immediately.

2. **Plugin validators always run**, even for types not declared in `kb.yaml`. This was a deliberate design decision — see `KBSchema.validate_entry()` in `pyrite/schema/kb_schema.py`.

3. **The `ctx` parameter is required — there is no fallback.** `(entry_type: str, fields: dict, ctx: dict) -> list[dict]` is the *only* supported signature (#379). `PluginRegistry` checks this with `inspect.signature(...).bind` the first time a plugin's validators are needed (cached after that, not re-checked per call) and refuses — logs once, drops — a validator that does not bind it. A 2-argument `(entry_type, fields)` validator does not run at all; there used to be a `TypeError`-catching fallback that retried the 2-arg form, but it silently disabled two in-tree extensions' validators for months (they were never updated past a 1-arg signature, so even the fallback didn't help) and is gone.

4. **Return `list[dict]`, not `list[str]`.** Binding the signature says nothing about the return type — a validator that returns strings still passes registration. `PluginRegistry.run_validators` drops any non-dict item (logged as a warning) rather than passing it to a caller that calls `.get(...)` on it, but that just means your strings vanish silently; write the dict shape from the start.

### Real example: Social's author validation

From `extensions/social/src/pyrite_social/validators.py`:

```python
from typing import Any
from .entry_types import WRITEUP_TYPES


def validate_social(
    entry_type: str, data: dict[str, Any], context: dict[str, Any]
) -> list[dict]:
    """Validate social KB-specific rules."""
    errors: list[dict] = []

    if entry_type == "writeup":
        # Writeups must have an author_id
        if not data.get("author_id"):
            errors.append({
                "field": "author_id",
                "rule": "required",
                "expected": "non-empty author_id for writeups",
                "got": data.get("author_id"),
            })

        # Writeup type must be valid
        writeup_type = data.get("writeup_type", "essay")
        if writeup_type not in WRITEUP_TYPES:
            errors.append({
                "field": "writeup_type",
                "rule": "enum",
                "expected": list(WRITEUP_TYPES),
                "got": writeup_type,
            })

    return errors
```

### Real example: Encyclopedia quality-gated validation

From `extensions/encyclopedia/src/pyrite_encyclopedia/validators.py`:

```python
def validate_encyclopedia(
    entry_type: str, data: dict[str, Any], context: dict[str, Any]
) -> list[dict]:
    errors: list[dict] = []

    if entry_type == "article":
        quality = data.get("quality", "stub")
        if quality not in QUALITY_LEVELS:
            errors.append({
                "field": "quality",
                "rule": "enum",
                "expected": list(QUALITY_LEVELS),
                "got": quality,
            })

        # Articles with quality >= GA must have at least 3 sources
        if quality in ("GA", "FA"):
            sources = data.get("sources", [])
            if len(sources) < 3:
                errors.append({
                    "field": "sources",
                    "rule": "ga_min_sources",
                    "expected": "at least 3 sources for GA/FA articles",
                    "got": len(sources),
                })

        # Published stub is suspicious (warning, not error)
        if data.get("review_status") == "published" and quality == "stub":
            errors.append({
                "field": "quality",
                "rule": "published_not_stub",
                "expected": "published articles should be at least 'start' quality",
                "got": quality,
                "severity": "warning",
            })

    elif entry_type == "talk_page":
        if not data.get("article_id"):
            errors.append({
                "field": "article_id",
                "rule": "required",
                "expected": "non-empty article_id for talk pages",
                "got": data.get("article_id"),
            })

    return errors
```

### How validators are called

Validators run inside `KBSchema.validate_entry()` (in `pyrite/schema/kb_schema.py`),
through `PluginRegistry.run_validators` — the single call site both
`kb_schema.py` and `storage/index.py` use, scoped to the KB's `kb_type`:

```python
# From pyrite/schema/kb_schema.py — inside validate_entry()
from ..plugins import get_registry

for item in get_registry().run_validators(kb_type, entry_type, fields, ctx):
    if item.get("severity") == "warning":
        warnings.append(item)
    else:
        errors.append(item)
```

`run_validators` (in `pyrite/plugins/registry.py`) does the per-validator work:
calls each validator scoped to `kb_type`, catches and logs (then skips) one that
raises rather than aborting the whole pass, and drops any non-dict item a
validator returns. There is no signature fallback — every validator it calls
already bound the `(entry_type, fields, ctx)` contract at registration, or it
would not be in the list at all.

---

## 6. Lifecycle Hooks

### Hook names and signatures

Pyrite supports five lifecycle hooks:

| Hook | When it fires | Can abort? | Use case |
|---|---|---|---|
| `before_save` | Before an entry is saved | Yes (raise) | Permission checks, field defaults |
| `after_save` | After an entry is saved | No | Side effects, count updates |
| `before_delete` | Before an entry is deleted | Yes (raise) | Permission checks |
| `after_delete` | After an entry is deleted | No | Cleanup, reputation adjustments |
| `before_index` | Before an entry is indexed | Yes (raise) | Index enrichment |

**Hook signature:**

```python
def my_hook(entry: Entry, context: dict[str, Any]) -> Entry | None:
```

**Parameters:**
- `entry` — the `Entry` object being operated on
- `context` — a dict with keys:
  - `kb_name: str` — name of the knowledge base
  - `user: str` — current user identity
  - `kb_schema: KBSchema` — the KB's schema
  - `operation: str` — one of `"create"`, `"update"`, or `"delete"`

**Return value:**
- `before_*` hooks should return the entry (possibly modified) or `None` (no change)
- `after_*` hooks return nothing (return value is ignored)
- `before_*` hooks can raise an exception to abort the operation

### Registration in the plugin class

```python
# From extensions/social/src/pyrite_social/plugin.py
def get_hooks(self) -> dict[str, list[Callable]]:
    return {
        "before_save": [before_save_author_check],
        "after_save": [after_save_update_counts],
        "after_delete": [after_delete_adjust_reputation],
    }
```

### Real example: Permission enforcement (Social)

From `extensions/social/src/pyrite_social/hooks.py`:

```python
def before_save_author_check(entry: Entry, context: dict[str, Any]) -> Entry:
    """Enforce author_edit_only policy."""
    if entry.entry_type != "writeup":
        return entry

    author_id = getattr(entry, "author_id", "")
    user = context.get("user", "")
    operation = context.get("operation", "")

    # On create, set author_id to current user if not already set
    if operation == "create" and not author_id and user:
        entry.author_id = user
        return entry

    # On update, check that the user is the author
    if operation == "update" and user and author_id:
        if user != author_id:
            raise PermissionError(
                f"User '{user}' cannot edit writeup owned by '{author_id}'"
            )

    return entry
```

### Real example: Side effects (Social)

```python
def after_save_update_counts(entry: Entry, context: dict[str, Any]) -> None:
    """Update the author's writeup_count after saving a writeup."""
    if entry.entry_type != "writeup":
        return

    author_id = getattr(entry, "author_id", "")
    if not author_id:
        return

    operation = context.get("operation", "")
    if operation != "create":
        return

    logger.info(
        "Writeup created by %s in %s — writeup_count should be incremented",
        author_id, context.get("kb_name", ""),
    )
```

### How hooks are executed

`HookRunner` (`pyrite/services/hook_runner.py`) is the only place that runs
hooks -- both core hooks and plugin hooks, under one raise-before/
swallow-after contract. `PluginRegistry.run_hooks`/`run_hooks_for_kb` used
to run plugin hooks directly; both are gone (#379). `PluginRegistry.get_hooks_for_kb(kb_type)`
is now a pure lookup -- it returns `{hook_name: [callables]}` for the plugins
active on `kb_type` and does not call any of them.

```python
# From pyrite/services/hook_runner.py, simplified
class HookRunner:
    def run_before_save(self, entry, context):
        return self._run("before_save", entry, context)
    # run_after_save / run_before_delete / run_after_delete follow the same shape

    def _run(self, hook_name, entry, context):
        is_before = hook_name.startswith("before_")
        for hook_fn in self._core_hooks.get(hook_name, []):
            ...  # raise-before/swallow-after, same as below
        if self._plugin_registry is None:
            return entry
        kb_type = context.get("kb_type", "")
        plugin_hooks = self._plugin_registry.get_hooks_for_kb(kb_type).get(hook_name, [])
        for hook_fn in plugin_hooks:
            try:
                result = hook_fn(entry, context)
                if result is not None:
                    entry = result
            except Exception:
                if is_before:
                    raise  # Let before_* hooks abort operations
                logger.warning("Hook %s failed", hook_name, exc_info=True)
        # a before_* hook DROPPED as non-conforming (wrong arity) also
        # refuses the write for this KB -- see below
        return entry
```

Key behavior:
- **before_* hooks** that raise will abort the operation (exception propagates)
- **after_* hooks** that raise are logged but do not abort
- If a hook returns a value, it replaces the entry for subsequent hooks
- If a hook returns `None`, the entry is passed through unchanged
- **Signature is checked once, at registration** (the first time a plugin's
  hooks are needed, then cached): a hook callable that does not bind
  `(entry, context)` is dropped and logged once, not discovered as a
  `TypeError` at call time
- **A dropped before_* hook fails the KB's before_* dispatch closed** — the
  write is refused, the same as if the hook itself had raised, because a
  before_* hook exists to enforce an invariant and silently losing it would
  make writes succeed that a conforming version of the plugin would have
  refused. A dropped after_* hook only logs a warning (the operation has
  already succeeded; after-hooks are independent side effects)

To use a runner directly (e.g. in a test), construct one with a registry:

```python
from pyrite.plugins.registry import PluginRegistry
from pyrite.services.hook_runner import HookRunner

registry = PluginRegistry()
registry.register(MyPlugin())
runner = HookRunner(plugin_registry=registry)
result = runner.run_before_save(entry, {"kb_type": "my-kb-type", ...})
```

### Known gap: hooks do not receive the DB instance

Currently, hooks receive `(entry, context)` but the context does not include the database connection. This means `after_save` hooks cannot directly update database tables. This is a known gap — workaround is to log the needed action or use a separate DB connection inside the hook.

---

## 7. Workflows

Workflows define state machines that control a field on an entry. They specify valid states, allowed transitions, and role requirements for each transition.

### Workflow definition structure

```python
{
    "states": ["state1", "state2", "state3"],
    "initial": "state1",
    "field": "my_status_field",   # entry field this workflow controls
    "transitions": [
        {
            "from": "state1",
            "to": "state2",
            "requires": "write",          # role required
            "description": "Submit for review",
        },
        {
            "from": "state2",
            "to": "state3",
            "requires": "reviewer",
            "description": "Approve and publish",
        },
        {
            "from": "state2",
            "to": "state1",
            "requires": "reviewer",
            "description": "Send back for revisions",
        },
        {
            "from": "state3",
            "to": "state2",
            "requires": "write",
            "requires_reason": True,       # transition needs justification
            "description": "Dispute, send back for review",
        },
    ],
}
```

### Real example: Encyclopedia article review workflow

From `extensions/encyclopedia/src/pyrite_encyclopedia/workflows.py`:

```python
ARTICLE_REVIEW_WORKFLOW = {
    "states": ["draft", "under_review", "published"],
    "initial": "draft",
    "field": "review_status",
    "transitions": [
        {
            "from": "draft",
            "to": "under_review",
            "requires": "write",
            "description": "Submit article for review",
        },
        {
            "from": "under_review",
            "to": "published",
            "requires": "reviewer",
            "description": "Approve and publish article",
        },
        {
            "from": "under_review",
            "to": "draft",
            "requires": "reviewer",
            "description": "Send article back for revisions",
        },
        {
            "from": "published",
            "to": "under_review",
            "requires": "write",
            "requires_reason": True,
            "description": "Dispute published article, send back for review",
        },
    ],
}
```

### Registration in the plugin class

```python
# From extensions/encyclopedia/src/pyrite_encyclopedia/plugin.py
def get_workflows(self) -> dict[str, dict]:
    return {"article_review": ARTICLE_REVIEW_WORKFLOW}
```

### Helper functions for workflow logic

The Encyclopedia extension also provides helper functions for querying the workflow. While these are not part of the protocol, they are a useful pattern:

```python
# From extensions/encyclopedia/src/pyrite_encyclopedia/workflows.py
def get_allowed_transitions(current_state: str, user_role: str = "") -> list[dict]:
    """Get allowed transitions from the current state for the given role."""
    allowed = []
    for t in ARTICLE_REVIEW_WORKFLOW["transitions"]:
        if t["from"] != current_state:
            continue
        required = t.get("requires", "")
        if not required:
            allowed.append(t)
        elif required == "write" and user_role in ("write", "reviewer", "admin"):
            allowed.append(t)
        elif required == "reviewer" and user_role in ("reviewer", "admin"):
            allowed.append(t)
        elif required == "admin" and user_role == "admin":
            allowed.append(t)
    return allowed

def can_transition(current_state: str, target_state: str, user_role: str = "") -> bool:
    """Check if a specific transition is allowed."""
    for t in get_allowed_transitions(current_state, user_role):
        if t["to"] == target_state:
            return True
    return False

def requires_reason(current_state: str, target_state: str) -> bool:
    """Check if a transition requires a reason."""
    for t in ARTICLE_REVIEW_WORKFLOW["transitions"]:
        if t["from"] == current_state and t["to"] == target_state:
            return t.get("requires_reason", False)
    return False
```

### Validating transitions via the registry

The registry provides a `validate_transition()` method:

```python
# From pyrite/plugins/registry.py
def validate_transition(
    self, workflow_name: str, current_state: str,
    target_state: str, user_role: str = ""
) -> bool:
    workflows = self.get_all_workflows()
    workflow = workflows.get(workflow_name)
    if not workflow:
        return False
    for transition in workflow.get("transitions", []):
        if transition["from"] == current_state and transition["to"] == target_state:
            required_role = transition.get("requires", "")
            if not required_role or required_role == user_role:
                return True
    return False
```

---

## 8. CLI Commands

### Typer app vs. single command

Plugins can register either a Typer app (with multiple sub-commands) or a single command function. The registration is done via `get_cli_commands()`:

```python
def get_cli_commands(self) -> list[tuple[str, Any]]:
    from .cli import zettel_app
    return [("zettel", zettel_app)]
```

**Important:** Import the CLI module lazily inside `get_cli_commands()`. This prevents failures when `typer` is not installed — the registry catches the `ImportError` and skips the plugin's CLI commands.

### How CLI commands are registered

From `pyrite/cli/__init__.py`:

```python
try:
    from ..plugins import get_registry
    for name, command in get_registry().get_all_cli_commands():
        if hasattr(command, "registered_commands"):
            # It's a Typer app — register as sub-app
            app.add_typer(command, name=name)
        else:
            # It's a single command callback
            app.command(name)(command)
except Exception:
    pass  # Plugin loading shouldn't break the CLI
```

If you return a Typer app, it becomes a command group: `pyrite zettel new`, `pyrite zettel inbox`, etc. If you return a plain function, it becomes a single command: `pyrite mycommand`.

### Real example: Zettelkasten CLI

From `extensions/zettelkasten/src/pyrite_zettelkasten/cli.py`:

```python
import typer
from rich.console import Console
from rich.table import Table

from pyrite.config import load_config
from pyrite.schema import generate_entry_id
from pyrite.storage.database import PyriteDB

from .entry_types import LiteratureNoteEntry, ZettelEntry

zettel_app = typer.Typer(help="Zettelkasten knowledge management")
console = Console()


@zettel_app.command("new")
def zettel_new(
    title: str = typer.Argument(..., help="Note title"),
    zettel_type: str = typer.Option(
        "fleeting", "--type", "-t",
        help="Type: fleeting, literature, permanent, hub"
    ),
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="Target KB"),
    source: str = typer.Option("", "--source", "-s", help="Source reference"),
):
    """Create a new zettel with the appropriate template."""
    from pyrite.storage.repository import KBRepository

    config = load_config()
    kb_config = None
    if kb_name:
        kb_config = config.get_kb(kb_name)
    else:
        for kb in config.knowledge_bases:
            kb_config = kb
            break

    if not kb_config:
        console.print("[red]Error:[/red] No KB found. Specify --kb.")
        raise typer.Exit(1)

    entry_id = generate_entry_id(title)
    now = datetime.now(UTC)

    if zettel_type == "literature":
        entry = LiteratureNoteEntry(
            id=entry_id, title=title,
            source_work=source, created_at=now, updated_at=now,
        )
    else:
        entry = ZettelEntry(
            id=entry_id, title=title,
            zettel_type=zettel_type,
            processing_stage="capture" if zettel_type == "fleeting" else "",
            source_ref=source, created_at=now, updated_at=now,
        )

    repo = KBRepository(kb_config)
    file_path = repo.save(entry)
    console.print(f"[green]Created {zettel_type} note:[/green] {file_path}")


@zettel_app.command("inbox")
def zettel_inbox(
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB to search"),
):
    """List fleeting notes not yet fully processed."""
    ...


@zettel_app.command("orphans")
def zettel_orphans(
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB to search"),
):
    """Find notes with no incoming or outgoing links."""
    ...


@zettel_app.command("maturity")
def zettel_maturity(
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB to search"),
):
    """Show maturity distribution of zettels."""
    ...
```

This gives users: `pyrite zettel new`, `pyrite zettel inbox`, `pyrite zettel orphans`, `pyrite zettel maturity`.

---

## 9. MCP Tools

MCP (Model Context Protocol) tools expose plugin functionality to AI agents. Tools are organized into three tiers with increasing privilege:

| Tier | Access level | Example operations |
|---|---|---|
| `read` | Safe for any agent | Search, browse, get stats |
| `write` | For trusted agents | Create, update, delete, vote |
| `admin` | For administrators | Protection levels, index management |

### Tool definition structure

Each tool is a dict with three required keys:

```python
{
    "description": "Human-readable description of what the tool does",
    "inputSchema": {
        "type": "object",
        "properties": {
            "param_name": {
                "type": "string",
                "description": "Parameter description",
            },
        },
        "required": ["param_name"],
    },
    "handler": self._handler_method,
}
```

### Registration by tier

From `get_mcp_tools(tier)`, return tools appropriate for the requested tier. Read tools should be available at all tiers; write tools at write and admin; admin tools at admin only:

```python
# From extensions/encyclopedia/src/pyrite_encyclopedia/plugin.py
def get_mcp_tools(self, tier: str) -> dict[str, dict]:
    tools = {}

    # Read-tier tools (available at all tiers)
    if tier in ("read", "write", "admin"):
        tools["wiki_quality_stats"] = {
            "description": "Get quality distribution and review queue stats",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kb_name": {
                        "type": "string",
                        "description": "KB name (optional)",
                    },
                },
                "required": [],
            },
            "handler": self._mcp_quality_stats,
        }
        tools["wiki_review_queue"] = { ... }
        tools["wiki_stubs"] = { ... }

    # Write-tier tools
    if tier in ("write", "admin"):
        tools["wiki_submit_review"] = {
            "description": "Submit a review for an article",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "entry_id": {"type": "string", "description": "Article entry ID"},
                    "kb_name": {"type": "string", "description": "KB name"},
                    "reviewer_id": {"type": "string", "description": "Reviewer user ID"},
                    "action": {
                        "type": "string",
                        "enum": ["approve", "reject", "comment"],
                        "description": "Review action",
                    },
                    "comments": {"type": "string", "description": "Review comments"},
                },
                "required": ["entry_id", "kb_name", "reviewer_id", "action"],
            },
            "handler": self._mcp_submit_review,
        }
        tools["wiki_assess_quality"] = { ... }

    # Admin-tier tools
    if tier == "admin":
        tools["wiki_protect"] = {
            "description": "Set protection level on an article",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "entry_id": {"type": "string", "description": "Article entry ID"},
                    "kb_name": {"type": "string", "description": "KB name"},
                    "level": {
                        "type": "string",
                        "enum": ["none", "semi", "full"],
                        "description": "Protection level",
                    },
                },
                "required": ["entry_id", "kb_name", "level"],
            },
            "handler": self._mcp_protect,
        }

    return tools
```

### How tools are registered in the MCP server

From `pyrite/server/mcp_server.py`:

```python
def _register_plugin_tools(self):
    """Register plugin-provided tools for this tier."""
    try:
        from ..plugins import get_registry
        plugin_tools = get_registry().get_all_mcp_tools(self.tier)
        self.tools.update(plugin_tools)
    except Exception:
        pass
```

Plugin tools are merged after core tools, so they appear alongside `kb_search`, `kb_get`, etc.

### MCP handler pattern

Handler methods receive an `args` dict (parsed from the input schema) and return a dict:

```python
def _mcp_submit_review(self, args: dict[str, Any]) -> dict[str, Any]:
    """Submit a review."""
    from datetime import UTC, datetime
    from pyrite.config import load_config
    from pyrite.storage.database import PyriteDB

    config = load_config()
    db = PyriteDB(config.settings.index_path)

    try:
        now = datetime.now(UTC).isoformat()
        db._raw_conn.execute(
            """INSERT INTO encyclopedia_review
               (entry_id, kb_name, reviewer_id, status, comments, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (args["entry_id"], args["kb_name"], args["reviewer_id"],
             args["action"], args.get("comments", ""), now),
        )
        db._raw_conn.commit()
        return {
            "reviewed": True,
            "entry_id": args["entry_id"],
            "action": args["action"],
        }
    finally:
        db.close()
```

### Naming convention

Prefix all MCP tool names with your plugin name to avoid collisions across plugins:

- `zettel_inbox`, `zettel_graph` (Zettelkasten)
- `social_top`, `social_vote`, `social_post` (Social)
- `wiki_quality_stats`, `wiki_submit_review`, `wiki_protect` (Encyclopedia)

---

## 10. Custom DB Tables

### When to use custom tables vs. metadata

Pyrite has a two-tier data durability model:

- **Content tier** (markdown files, git-tracked): Entries, profiles, articles. Survives `git clone`.
- **Engagement tier** (SQLite, local-only): Votes, reviews, reputation, edit history. Lost on clone.

Use custom DB tables for engagement data that is:
- High-volume (many votes per entry)
- Needs fast aggregation (sum of votes, count of reviews)
- Is inherently local to an install (reputation scores)
- Represents metadata *about* entries, not knowledge content

Use `entry.metadata` for fields that should travel with the entry in git.

### Table definition format

From `extensions/social/src/pyrite_social/tables.py`:

```python
SOCIAL_TABLES = [
    {
        "name": "social_vote",
        "columns": [
            {"name": "id", "type": "INTEGER", "primary_key": True},
            {"name": "entry_id", "type": "TEXT", "nullable": False},
            {"name": "kb_name", "type": "TEXT", "nullable": False},
            {"name": "user_id", "type": "TEXT", "nullable": False},
            {"name": "value", "type": "INTEGER", "nullable": False},  # +1 or -1
            {"name": "created_at", "type": "TEXT", "nullable": False},
        ],
        "indexes": [
            {"columns": ["entry_id", "kb_name", "user_id"], "unique": True},
            {"columns": ["entry_id", "kb_name"]},
            {"columns": ["user_id"]},
        ],
    },
    {
        "name": "social_reputation_log",
        "columns": [
            {"name": "id", "type": "INTEGER", "primary_key": True},
            {"name": "user_id", "type": "TEXT", "nullable": False},
            {"name": "delta", "type": "INTEGER", "nullable": False},
            {"name": "reason", "type": "TEXT", "nullable": False},
            {"name": "entry_id", "type": "TEXT"},
            {"name": "kb_name", "type": "TEXT"},
            {"name": "created_at", "type": "TEXT", "nullable": False},
        ],
        "indexes": [
            {"columns": ["user_id"]},
            {"columns": ["entry_id", "kb_name"]},
        ],
    },
]
```

### Column definition keys

| Key | Type | Required | Description |
|---|---|---|---|
| `name` | `str` | Yes | Column name |
| `type` | `str` | Yes | SQLite type: `INTEGER`, `TEXT`, `REAL`, `BLOB` |
| `primary_key` | `bool` | No | If `True`, makes this the primary key (with AUTOINCREMENT for INTEGER) |
| `nullable` | `bool` | No | If `False`, adds `NOT NULL` constraint |
| `default` | `Any` | No | Default value |

### Index definition keys

| Key | Type | Required | Description |
|---|---|---|---|
| `columns` | `list[str]` | Yes | Columns to index |
| `unique` | `bool` | No | If `True`, creates a unique index |

### Foreign key definition keys (optional)

| Key | Type | Required | Description |
|---|---|---|---|
| `column` | `str` | Yes | Local column name |
| `references` | `str` | Yes | Format: `"table.column"` |

### How tables are created

`PyriteDB.__init__()` calls `_create_plugin_tables()` which discovers all plugin table definitions and creates them using `CREATE TABLE IF NOT EXISTS`:

```python
# From pyrite/storage/database.py
def _create_plugin_tables(self):
    """Create custom tables defined by plugins."""
    try:
        from ..plugins import get_registry
        for table_def in get_registry().get_all_db_tables():
            self._create_table_from_def(table_def)
    except Exception:
        pass

def _create_table_from_def(self, table_def: dict):
    name = table_def["name"]
    columns = table_def.get("columns", [])
    indexes = table_def.get("indexes", [])

    col_defs = []
    for col in columns:
        parts = [col["name"], col["type"]]
        if col.get("primary_key"):
            parts.append("PRIMARY KEY")
            if col["type"] == "INTEGER":
                parts.append("AUTOINCREMENT")
        if col.get("nullable") is False:
            parts.append("NOT NULL")
        if "default" in col:
            parts.append(f"DEFAULT {col['default']}")
        col_defs.append(" ".join(parts))

    for fk in table_def.get("foreign_keys", []):
        col_defs.append(
            f"FOREIGN KEY ({fk['column']}) REFERENCES {fk['references']}"
        )

    sql = f"CREATE TABLE IF NOT EXISTS {name} ({', '.join(col_defs)})"
    self._raw_conn.execute(sql)

    for idx in indexes:
        cols = ", ".join(idx["columns"])
        unique = "UNIQUE " if idx.get("unique") else ""
        idx_name = f"idx_{name}_{'_'.join(idx['columns'])}"
        self._raw_conn.execute(
            f"CREATE {unique}INDEX IF NOT EXISTS {idx_name} ON {name} ({cols})"
        )

    self._raw_conn.commit()
```

### Real example: Encyclopedia review and history tables

From `extensions/encyclopedia/src/pyrite_encyclopedia/tables.py`:

```python
ENCYCLOPEDIA_TABLES = [
    {
        "name": "encyclopedia_review",
        "columns": [
            {"name": "id", "type": "INTEGER", "primary_key": True},
            {"name": "entry_id", "type": "TEXT", "nullable": False},
            {"name": "kb_name", "type": "TEXT", "nullable": False},
            {"name": "reviewer_id", "type": "TEXT", "nullable": False},
            {"name": "status", "type": "TEXT", "nullable": False},
            {"name": "comments", "type": "TEXT"},
            {"name": "created_at", "type": "TEXT", "nullable": False},
        ],
        "indexes": [
            {"columns": ["entry_id", "kb_name"]},
            {"columns": ["reviewer_id"]},
            {"columns": ["status"]},
        ],
    },
    {
        "name": "encyclopedia_article_history",
        "columns": [
            {"name": "id", "type": "INTEGER", "primary_key": True},
            {"name": "entry_id", "type": "TEXT", "nullable": False},
            {"name": "kb_name", "type": "TEXT", "nullable": False},
            {"name": "edit_summary", "type": "TEXT"},
            {"name": "editor_id", "type": "TEXT", "nullable": False},
            {"name": "created_at", "type": "TEXT", "nullable": False},
        ],
        "indexes": [
            {"columns": ["entry_id", "kb_name"]},
            {"columns": ["editor_id"]},
        ],
    },
]
```

### Naming convention

**Always prefix table names with the plugin name** to avoid collisions between plugins:

- `social_vote`, `social_reputation_log` (Social)
- `encyclopedia_review`, `encyclopedia_article_history` (Encyclopedia)

Index names are automatically generated as `idx_{table}_{col1}_{col2}` by the table creation code.

---

## 11. KB Presets

### What presets are

Presets provide ready-made KB configurations for `pyrite kb init --preset <name>`. They scaffold a new knowledge base with appropriate types, policies, validation rules, and directories.

### Preset structure

A preset is a dict that matches the structure of `kb.yaml`:

```python
{
    "name": "my-kb-name",          # default KB name (user can override)
    "description": "What this KB is for",
    "types": {
        "type_name": {
            "description": "...",
            "required": ["title", ...],
            "optional": ["field1", "field2"],
            "subdirectory": "dirname/",
        },
    },
    "policies": { ... },           # domain-specific policies
    "validation": {
        "enforce": True,
        "rules": [ ... ],
    },
    "directories": ["dir1", "dir2"],  # directories to create
}
```

### Real example: Zettelkasten preset

From `extensions/zettelkasten/src/pyrite_zettelkasten/preset.py`:

```python
ZETTELKASTEN_PRESET = {
    "name": "my-zettelkasten",
    "description": "Personal knowledge garden",
    "types": {
        "zettel": {
            "description": "Atomic knowledge note",
            "required": ["title"],
            "optional": ["zettel_type", "maturity", "source_ref", "processing_stage"],
            "subdirectory": "zettels/",
        },
        "literature_note": {
            "description": "Note capturing ideas from a source work",
            "required": ["title", "source_work"],
            "optional": ["author", "page_refs"],
            "subdirectory": "literature/",
        },
    },
    "policies": {
        "private": True,
        "single_author": True,
    },
    "validation": {
        "enforce": True,
        "rules": [
            {"field": "zettel_type", "enum": ["fleeting", "literature", "permanent", "hub"]},
            {"field": "maturity", "enum": ["seed", "sapling", "evergreen"]},
            {"field": "processing_stage",
             "enum": ["capture", "elaborate", "question", "review", "connect"]},
        ],
    },
    "directories": ["zettels", "literature"],
}
```

### Registration

```python
# In your plugin class
def get_kb_presets(self) -> dict[str, dict]:
    return {"zettelkasten": ZETTELKASTEN_PRESET}
```

The key is the preset name that users pass to `--preset`.

---

## 12. Relationship Types

### Custom relationships with inverses

Plugins can register custom relationship types that extend the core set. Each relationship must define an inverse:

```python
# From extensions/zettelkasten/src/pyrite_zettelkasten/plugin.py
def get_relationship_types(self) -> dict[str, dict]:
    return {
        "elaborates": {
            "inverse": "elaborated_by",
            "description": "Elaborates on another note",
        },
        "elaborated_by": {
            "inverse": "elaborates",
            "description": "Elaborated by another note",
        },
        "branches_from": {
            "inverse": "has_branch",
            "description": "Branches from a parent note",
        },
        "has_branch": {
            "inverse": "branches_from",
            "description": "Has a branch note",
        },
        "synthesizes": {
            "inverse": "synthesized_from",
            "description": "Synthesizes multiple notes into a permanent note",
        },
        "synthesized_from": {
            "inverse": "synthesizes",
            "description": "Was synthesized from into a permanent note",
        },
    }
```

### How relationships are integrated

Plugin relationship types are merged into the core set in `pyrite/schema.py`:

```python
# From pyrite/schema.py
def get_all_relationship_types() -> dict[str, dict[str, Any]]:
    """Get all relationship types: core + plugin-provided."""
    all_types = dict(RELATIONSHIP_TYPES)
    try:
        from .plugins import get_registry
        all_types.update(get_registry().get_all_relationship_types())
    except Exception:
        pass
    return all_types
```

The inverse lookup also works with plugin relationships:

```python
def get_inverse_relation(relation: str) -> str:
    all_types = get_all_relationship_types()
    if relation in all_types:
        return all_types[relation]["inverse"]
    return "related_to"
```

**Important:** Always register both directions of the relationship. If you register `elaborates -> elaborated_by`, you must also register `elaborated_by -> elaborates`.

---

## 13. Integration Points

### How plugin types appear across the system

When a plugin is installed and discovered, its types automatically appear in all five integration points:

1. **Entry type resolution** — `get_entry_class("zettel")` returns `ZettelEntry` instead of `GenericEntry`

2. **CLI** — Plugin Typer apps appear as sub-commands: `pyrite zettel new`, `pyrite social vote`

3. **MCP server** — Plugin tools appear alongside core tools, filtered by tier

4. **Validation** — Plugin validators run on every `KBSchema.validate_entry()` call, for all entry types

5. **Relationships** — Plugin relationship types merge into the agent schema and inverse lookup

### The get_entry_class() lookup chain

```
get_entry_class("zettel")
  1. Check ENTRY_TYPE_REGISTRY (core types: note, person, event, etc.)
     -> Not found
  2. Check get_registry().get_all_entry_types() (plugin types)
     -> Found: ZettelEntry
  3. If neither found -> GenericEntry
```

This means:
- Core types always take precedence (a plugin cannot override `note` or `person`)
- Plugin types take precedence over GenericEntry
- Unknown types gracefully fall back to GenericEntry

### Entry discovery via Python entry points

The `PluginRegistry` uses `importlib.metadata.entry_points()` to discover plugins:

```python
# From pyrite/plugins/registry.py
eps = entry_points()
if hasattr(eps, "select"):
    plugin_eps = eps.select(group=self.ENTRY_POINT_GROUP)
else:
    plugin_eps = eps.get(self.ENTRY_POINT_GROUP, [])

for ep in plugin_eps:
    plugin_class = ep.load()
    plugin = plugin_class()
    if hasattr(plugin, "name"):
        self._plugins[plugin.name] = plugin
```

Discovery is lazy (runs once on first access) and idempotent. Failed plugin loads are logged but do not crash the system.

---

## 14. Testing

### Test structure

The proven test structure for an extension has 8 sections:

1. **TestPluginRegistration** — Verify name, all capabilities appear in registry
2. **TestEntryType** — Defaults, to_frontmatter, from_frontmatter, roundtrip_markdown
3. **TestValidators** — One test per rule (positive + negative), test ignores-other-types
4. **TestHooks** — Direct call tests + HookRunner tests
5. **TestWorkflows** — Test each transition allowed/blocked, requires_reason
6. **TestDBTables** — Definition checks + actual SQLite creation in tmpdir
7. **TestPreset** — Structure, directories, validation rules
8. **TestCoreIntegration** — entry_class_resolution, entry_from_frontmatter, multi-plugin coexistence

### Testing plugin registration

```python
from pyrite.plugins.registry import PluginRegistry
from pyrite_zettelkasten.plugin import ZettelkastenPlugin

class TestPluginRegistration:
    def test_plugin_has_name(self):
        plugin = ZettelkastenPlugin()
        assert plugin.name == "zettelkasten"

    def test_register_with_registry(self):
        registry = PluginRegistry()
        plugin = ZettelkastenPlugin()
        registry.register(plugin)
        assert "zettelkasten" in registry.list_plugins()

    def test_entry_types_registered(self):
        registry = PluginRegistry()
        registry.register(ZettelkastenPlugin())
        types = registry.get_all_entry_types()
        assert "zettel" in types
        assert types["zettel"] is ZettelEntry
```

**Critical rule:** Use `in` checks, not exact length assertions. When multiple plugins are installed (via `pip install -e`), `get_all_*()` returns results from ALL installed plugins, not just the one you registered.

```python
# GOOD: uses "in" check
assert "zettel" in cmd_names

# BAD: breaks when other plugins are installed
assert len(cmd_names) == 1
```

### Testing validators

```python
class TestValidators:
    def test_fleeting_requires_processing_stage(self):
        errors = validate_zettel("zettel", {"zettel_type": "fleeting"}, {})
        assert any(e["rule"] == "required_for_fleeting" for e in errors)

    def test_fleeting_with_stage_ok(self):
        errors = validate_zettel(
            "zettel",
            {"zettel_type": "fleeting", "processing_stage": "capture"},
            {},
        )
        assert not any(e["rule"] == "required_for_fleeting" for e in errors)

    def test_ignores_unrelated_types(self):
        errors = validate_zettel("note", {"title": "regular note"}, {})
        assert errors == []
```

### Testing hooks

```python
from pyrite.plugins.registry import PluginRegistry
from pyrite.services.hook_runner import HookRunner

class TestHooks:
    def test_before_save_sets_author_on_create(self):
        entry = WriteupEntry(id="test", title="Test")
        ctx = {"user": "alice", "operation": "create"}
        result = before_save_author_check(entry, ctx)
        assert result.author_id == "alice"

    def test_before_save_blocks_non_author_update(self):
        entry = WriteupEntry(id="test", title="Test", author_id="alice")
        ctx = {"user": "bob", "operation": "update"}
        with pytest.raises(PermissionError, match="bob.*cannot edit.*alice"):
            before_save_author_check(entry, ctx)

    def test_hooks_run_via_hook_runner(self):
        # HookRunner, not the registry, runs hooks (#379) --
        # registry.get_hooks_for_kb is a lookup, not a runner.
        registry = PluginRegistry()
        registry.register(SocialPlugin())
        runner = HookRunner(plugin_registry=registry)
        entry = WriteupEntry(id="test", title="Test")
        ctx = {"user": "alice", "operation": "create"}
        result = runner.run_before_save(entry, ctx)
        assert result.author_id == "alice"

    def test_hooks_abort_on_permission_error(self):
        registry = PluginRegistry()
        registry.register(SocialPlugin())
        runner = HookRunner(plugin_registry=registry)
        entry = WriteupEntry(id="test", title="Test", author_id="alice")
        ctx = {"user": "bob", "operation": "update"}
        with pytest.raises(PermissionError):
            runner.run_before_save(entry, ctx)
```

### Testing entry type resolution through core

To test that your plugin types resolve via `get_entry_class()`, you need to temporarily replace the global registry:

```python
class TestCoreIntegration:
    def test_entry_class_resolution(self):
        import pyrite.plugins.registry as reg_module
        registry = PluginRegistry()
        registry.register(ZettelkastenPlugin())

        old = reg_module._registry
        reg_module._registry = registry

        try:
            from pyrite.models.core_types import get_entry_class
            cls = get_entry_class("zettel")
            assert cls is ZettelEntry

            # Core types still work
            from pyrite.models.core_types import NoteEntry
            cls = get_entry_class("note")
            assert cls is NoteEntry
        finally:
            reg_module._registry = old
```

**Always use `try/finally` to restore the registry**, even in tests. Cross-test contamination from a leaked global registry is a common source of flaky tests.

### Testing DB table creation

```python
class TestDBTables:
    def test_tables_created_in_sqlite(self):
        import tempfile
        import pyrite.plugins.registry as reg_module

        with tempfile.TemporaryDirectory() as tmpdir:
            old = reg_module._registry
            registry = PluginRegistry()
            registry.register(SocialPlugin())
            reg_module._registry = registry

            try:
                db = PyriteDB(Path(tmpdir) / "test.db")

                cursor = db._raw_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
                table_names = [row["name"] for row in cursor.fetchall()]
                assert "social_vote" in table_names

                # Test CRUD
                db._raw_conn.execute(
                    "INSERT INTO social_vote "
                    "(entry_id, kb_name, user_id, value, created_at) "
                    "VALUES ('e1', 'kb1', 'alice', 1, '2025-01-01')"
                )
                db._raw_conn.commit()

                row = db._raw_conn.execute(
                    "SELECT * FROM social_vote WHERE entry_id = 'e1'"
                ).fetchone()
                assert row["user_id"] == "alice"

                # Test unique constraint
                with pytest.raises(sqlite3.IntegrityError):
                    db._raw_conn.execute(
                        "INSERT INTO social_vote "
                        "(entry_id, kb_name, user_id, value, created_at) "
                        "VALUES ('e1', 'kb1', 'alice', -1, '2025-01-02')"
                    )

                db.close()
            finally:
                reg_module._registry = old
```

### Testing workflows

```python
class TestWorkflows:
    def test_draft_to_under_review_write(self):
        assert can_transition("draft", "under_review", "write") is True

    def test_draft_to_published_blocked(self):
        """Can't skip review and go straight to published."""
        assert can_transition("draft", "published", "write") is False

    def test_under_review_to_published_write_blocked(self):
        """Regular writers can't approve their own articles."""
        assert can_transition("under_review", "published", "write") is False

    def test_published_to_under_review_requires_reason(self):
        assert requires_reason("published", "under_review") is True

    def test_workflow_via_registry(self):
        registry = PluginRegistry()
        registry.register(EncyclopediaPlugin())
        assert registry.validate_transition(
            "article_review", "under_review", "published", "reviewer"
        ) is True
```

### Testing multi-plugin coexistence

```python
def test_three_plugins_coexist(self):
    registry = PluginRegistry()
    registry.register(ZettelkastenPlugin())
    registry.register(SocialPlugin())
    registry.register(EncyclopediaPlugin())

    types = registry.get_all_entry_types()
    assert "zettel" in types
    assert "writeup" in types
    assert "article" in types

    commands = registry.get_all_cli_commands()
    cmd_names = [name for name, _ in commands]
    assert "zettel" in cmd_names
    assert "social" in cmd_names
    assert "wiki" in cmd_names

    presets = registry.get_all_kb_presets()
    assert "zettelkasten" in presets
    assert "social" in presets
    assert "encyclopedia" in presets
```

---

## 15. Patterns and Pitfalls

### Patterns that work well

**One validator function per plugin, dispatching on entry_type.**
Rather than one validator per type, use a single function that checks `if entry_type == "article":` and returns `[]` for unrelated types. This keeps registration simple.

**Quality-gated validation (Encyclopedia pattern).**
Higher quality levels impose stricter requirements. This creates a natural progression path and makes the validator a useful enforcement tool.

**Folder-per-author convention for permission enforcement.**
Extensions that need per-user ownership (Social writeups, Encyclopedia drafts) use folder layout like `writeups/alice/my-essay.md`. This gives two enforcement layers: app-level hooks + git branch protection rules.

**Lazy CLI imports.**
Always import the CLI module inside `get_cli_commands()`, not at the top of `plugin.py`. This prevents crashes when `typer` is not installed.

**Prefixed table names.**
Always prefix custom DB table names with the plugin name (`social_vote`, not `vote`) to avoid collisions.

### Common mistakes

**Forgetting to override `from_frontmatter()`.**
The most common bug. If you extend `NoteEntry` and add custom fields but do not override `from_frontmatter()`, your fields will silently be left at their defaults when loading from markdown. Every custom entry type MUST have its own `from_frontmatter()`.

**Not setting `meta["type"]` in `to_frontmatter()`.**
The parent's `to_frontmatter()` sets `type` to the parent's type name (e.g., `"note"`). You must override it: `meta["type"] = "zettel"`.

**Exact count assertions in tests.**
When other plugins are `pip install -e`'d in the same environment, `get_all_*()` returns results from all plugins. Use `in` checks and `>= N` comparisons, never `== N`.

**Not returning `[]` for unrelated types in validators.**
Your validator is called for every entry in every KB. If you do not guard with `if entry_type == "mytype":`, you may produce false validation errors for other types.

**Hooks not returning the entry.**
`before_*` hooks must return the entry (possibly modified). If you forget the `return entry` statement, the hook returns `None`, and the entry is passed through unchanged (the registry treats `None` as "no change"). This is safe but means your modifications are lost.

**Not using `try/finally` for registry patching in tests.**
If a test patches `reg_module._registry` and then fails before restoring it, subsequent tests will use the wrong registry. Always use `try/finally`.

### Architecture decisions to know

**Two-tier data durability.**
- Content tier (markdown files): git-tracked, portable, survives clone/fork
- Engagement tier (SQLite tables): local-only, not git-tracked, lost on clone
- Votes, reviews, reputation logs are engagement data
- Entry content, profiles, articles are content data

**Plugin validators always run.**
Even for types not declared in `kb.yaml`. This was a deliberate fix — without it, plugin types would never get validated unless the KB explicitly declared them.

**Hooks do not receive the DB instance.**
This is a known gap. Hooks receive `(entry, context)` but context does not include a database connection. `after_save` hooks that need to update DB tables must open their own connection or log the needed action.

**Entry-point discovery is global.**
Once a plugin is `pip install -e`'d, its entry point is live for all `PluginRegistry` instances. There is no way to selectively enable/disable plugins per KB at the registry level.

### Development workflow

1. Create the extension directory structure under `extensions/<name>/`
2. Write `pyproject.toml` with the entry point declaration
3. Write entry types, validators, and the plugin class
4. Install with `pip install -e extensions/<name>/`
5. Write tests following the 8-section structure
6. Run tests: `pytest extensions/<name>/tests/`
7. Keep that extension installed in the checkout `.venv` used by the hooks.

### Pre-commit hook environment

The project's `.pre-commit-config.yaml` runs fast checks at commit time and tests at pre-push. Install extensions in the checkout's `.venv`; there is no separate system-Python installation requirement. See [CONTRIBUTING.md](../../CONTRIBUTING.md), "Branches, hooks and pull requests" and "Running the tests", for the current commands and hook stages. The `ruff-format` hook may reformat files, requiring re-staging before the commit succeeds.

---

## Appendix: Protocol Reference

All 15 methods of the `PyritePlugin` protocol (from `pyrite/plugins/protocol.py`), plus the `name` attribute:

| Method | Returns | Description |
|---|---|---|
| `get_entry_types()` | `dict[str, type]` | Map of type name to Entry subclass |
| `get_kb_types()` | `list[str]` | KB type identifiers this plugin supports |
| `get_cli_commands()` | `list[tuple[str, Any]]` | CLI commands as `(name, typer_app_or_func)` |
| `get_mcp_tools(tier)` | `dict[str, dict]` | MCP tools for the given tier (`read`/`write`/`admin`) |
| `get_db_columns()` | `list[dict]` | Additional columns for the core entry table |
| `get_relationship_types()` | `dict[str, dict]` | Custom relationship types with inverses |
| `get_workflows()` | `dict[str, dict]` | State machine definitions |
| `get_db_tables()` | `list[dict]` | Custom DB table definitions |
| `get_hooks()` | `dict[str, list[Callable]]` | Lifecycle hooks by hook name |
| `get_kb_presets()` | `dict[str, dict]` | KB preset configurations |
| `get_validators()` | `list[Callable]` | Validation functions: `(entry_type, data, context) -> list[dict]` |
| `get_field_schemas()` | `dict[str, dict[str, dict]]` | Rich field schema definitions (FieldSchema format) per type |
| `get_type_metadata()` | `dict[str, dict]` | AI instructions, field descriptions, display hints per type |
| `get_collection_types()` | `dict[str, dict]` | Custom collection type definitions |
| `set_context(ctx)` | `None` | Receive `PluginContext` with DB, config, services (DI injection) |

All methods are optional. Implement only what your plugin needs.

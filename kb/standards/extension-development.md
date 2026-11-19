---
id: extension-development
type: standard
title: "Extension Development Standards"
category: coding
enforced: false
tags: [extensions, plugins]
---

Worked examples: `extensions/zettelkasten`, `extensions/encyclopedia` and
`extensions/social` are example plugins built to this standard — read their
`plugin.py`, `entry_types.py` and `README.md` alongside these rules. They
demonstrate entry types, CLI commands, MCP tools, presets, hooks, workflows
and custom DB tables; they are reference code for plugin authors, not
supported products.

## Package Structure
```
extensions/<name>/
  pyproject.toml          # declares pyrite.plugins entry point
  src/pyrite_<name>/
    __init__.py
    plugin.py             # main plugin class
    entry_types.py         # dataclass Entry subclasses
    validators.py          # validation callables
    cli.py                 # typer sub-app
    preset.py              # KB preset definition
    workflows.py           # state machines (if needed)
    hooks.py               # lifecycle hooks (if needed)
    tables.py              # custom DB table defs (if needed)
  tests/
    test_<name>.py
```

## Entry Type Rules
- Subclasses MUST override `from_frontmatter()` to map custom fields
- `to_frontmatter()` MUST set `meta["type"]` and SHOULD omit default values
- Use helper for common NoteEntry kwargs to reduce boilerplate

## Validator Rules
- Return `[]` for unrelated entry types (type-dispatched)
- Use severity="warning" for advisory checks
- DB table names MUST be prefixed with plugin name to avoid collisions

### Validator signature

A validator returned from `get_validators()` MUST bind exactly
`(entry_type: str, fields: dict, ctx: dict) -> list[dict]`. There is one
contract; there is no 2-argument `(entry_type, fields)` fallback and no
`(entry: Entry)` object form. `PluginRegistry` checks the signature with
`inspect.signature(...).bind` **at registration**, not at call time: a
validator that does not bind the contract is logged and dropped before it
ever runs, rather than raising a `TypeError` on every write that gets
swallowed by a broad `except Exception` and silently disables validation for
that plugin (#379, #376, #48 — two in-tree extensions shipped for months
with a 1-argument, `list[str]`-returning validator that never actually ran).

Each returned item is a dict, not a string: `{"field": ..., "rule": ...,
"message": ..., "severity": "warning"}` (`severity` omitted or anything
other than `"warning"` is treated as an error). `field`/`rule`/`expected`/
`got` are conventional, used by callers that build a human-readable message
or match on a specific rule (e.g. `index.py`'s invalid-status check matches
`field == "status"` and `rule == "enum"`); only `message` (or `field`, as a
fallback label) is required.

Lifecycle hooks (`get_hooks()`) have the equivalent rule: every callable
MUST bind `(entry, ctx)`. The registry checks this the same way, at
registration.

Write a test using the real plugin instance, not a fixture that hand-crafts
a conforming callable: `plugin.get_validators()` (or `get_hooks()`) fed
through `inspect.signature(...).bind(...)` with the contract's argument
shape, the way `tests/test_plugin_contract.py` does for every installed
plugin. That test would have caught #376/#48 the day the validator was
written, not months later against production data.

## Capabilities
- Declare `capabilities: ClassVar[set[Capability]]` on the plugin class (`from pyrite.plugins.capabilities import Capability`).
- Declare one capability for every method the plugin implements. The registry skips a method whose capability is not declared, and a plugin with no `capabilities` has every method skipped.
- The method-to-capability map is `_METHOD_CAPABILITIES` in `pyrite/plugins/registry.py`.

## Hook rules
A `before_save` hook may refuse a write by raising. On update its changes are ignored; only create keeps them (ADR-0042 decision 4). An `after_save` hook never writes another entry; `tests/test_after_save_hooks_write_no_other_entry.py` runs every installed plugin's `after_save` hooks and fails if one does.

## CLI Rules
- CLI module imported lazily inside `get_cli_commands()` to handle missing typer
- Dependencies list should be empty — pyrite is a peer dependency

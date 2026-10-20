---
id: config-system
type: component
title: "Configuration System"
kind: module
path: "pyrite/config.py"
owner: "markr"
dependencies: ["ruamel.yaml", "pathlib", "dotenv"]
tags: [core, config]
---

# Configuration System

The configuration system manages multi-KB setups, global application settings, repository definitions, and GitHub authentication. Configuration is loaded from `~/.pyrite/config.yaml` (or `$PYRITE_CONFIG_DIR`), with per-KB overrides in each KB's `kb.yaml` file and environment variable fallbacks.

A repo-local `.pyrite/config.yaml` found by searching up from the cwd (the ADR-0032 worktree registry) is untrusted: `load_config` reads only the `_UNTRUSTED_*_KEYS` allowlist from it (KB registry entries inside its tree, `index_path` inside its tree, a few switches) and warns about the rest; credentials always come from `trusted_config_dir()`. `~/.pyrite` and an explicit `PYRITE_CONFIG_DIR`/`PYRITE_DATA_DIR` are trusted in full.

## Key Files

| File | Purpose |
|------|---------|
| `pyrite/config.py` | All dataclasses, load/save helpers, auto-discovery |
| `~/.pyrite/config.yaml` | Persisted global config (created on first run) |
| `<kb-root>/kb.yaml` | Per-KB schema, types, and policies |
| `~/.pyrite/github_auth.yaml` | Separate secure file for GitHub OAuth credentials |

## Key Classes

### `KBConfig`
Dataclass for a single knowledge base. Fields include `name`, `path`, `kb_type` (free-form string, default `"generic"`), `description`, `read_only`, `remote`, `shortname` (alias for cross-KB links), `ephemeral`, `ttl`, `created_at_ts`, and `default_role`. The `default_role` field controls per-KB access: `None` = use global role, `"read"` = public, `"none"` = private. Provides `load_kb_yaml()` to pull in per-KB schema/types/policies and `validate()` for path checking. Properties expose `kb_yaml_path` and `local_db_path` (the per-KB SQLite index under `<kb>/.pyrite/index.db`).

### `PyriteConfig`
Root configuration aggregating `knowledge_bases`, `repositories`, `subscriptions`, `github_auth`, and `settings`. Maintains internal `_kb_by_name` and `_repo_by_name` lookup dicts rebuilt on init. Methods: `get_kb()`, `list_kbs()`, `add_kb()`, `remove_kb()`, `get_repo()`, `add_repo()`, `remove_repo()`, `get_kbs_in_repo()`, `validate()`, `to_dict()`, `from_dict()`.

### `Settings`
Global application settings: `default_editor`, `ai_provider`/`ai_model`/`ai_api_key`, `summary_length`, `enable_mcp`, `index_path`, `host`/`port`, `cors_origins`, `api_key`, rate limits, `embedding_model`/`embedding_dimensions`, `search_mode`, and `workspace_path`. Environment variables (`EDITOR`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_BASE`) serve as fallbacks.

### `Repository`
Configuration for a git repository that may contain one or more KBs. Supports local and remote repos with authentication methods (`none`, `ssh`, `github_oauth`, `token`).

### `GitHubAuth`
GitHub OAuth and GitHub App authentication credentials. Stored in a separate `github_auth.yaml` file for security. Provides `has_valid_token` with expiry checking.

### `Subscription`
Configuration for a subscribed remote KB with `url`, `local_path`, `auto_sync`, and `sync_interval`.

## Module-Level Functions

- **`load_config()`** -- Reads `CONFIG_FILE`, deserializes via `PyriteConfig.from_dict()`, and loads `kb.yaml` for each KB. Creates a default `PyriteConfig` if the file does not exist.
- **`save_config(config)`** -- Serializes `PyriteConfig.to_dict()` to `CONFIG_FILE` using `dump_yaml_file`.
- **`auto_discover_kbs(search_paths)`** -- Recursively finds `kb.yaml` files under the given paths and returns a list of `KBConfig` objects.
- **`ensure_config_dir()`** -- Creates `CONFIG_DIR` if missing.
- **`get_notes_dir(kb_name)`** / **`get_db_path(kb_name)`** -- Legacy compatibility helpers.

## Constants

- `CONFIG_DIR` -- `Path("~/.pyrite")` (overridable via `$PYRITE_CONFIG_DIR`)
- `CONFIG_FILE` -- `CONFIG_DIR / "config.yaml"`

## Design Notes

- YAML serialization uses `pyrite.utils.yaml` wrappers around `ruamel.yaml`, not the stdlib `yaml` module.
- `KBConfig.kb_type` is a free-form string for extensibility; the `KBType` enum exists only for legacy compatibility.
- Ephemeral KBs are tracked via `ephemeral`, `ttl`, and `created_at_ts` fields and garbage-collected by `KBService.gc_ephemeral_kbs()`.
- The `shortname` field on `KBConfig` enables concise cross-KB wikilink syntax (`[[shortname:entry-id]]`) and nothing else (#952, ADR-0049 Q3): the link prefix is looked up among the caller's readable KBs only, by `readable_kb_named(name, readable_kbs=...)` (full name, then shortname; `readable_kb_with_shortname` is its second step) -- scoped methods, `readable_kbs` a required keyword, the implementation `services/kb_resolver` delegates to and the index uses to store the KB a link names (ADR-0049 Q3); the unscoped `kb_name_for`/`get_kb_by_shortname` are gone; every `kb` parameter, `-k` and `kb_name` takes the full name, and a shortname there is refused by the one resolver, `services/kb_resolver.py`, with the full name suggested. `require_kb(name, db=None)` is that resolver for a caller with no scope: a blank name is `ValidationError`, an unknown one `KBNotFoundError` with `did_you_mean`, and with `db` a KB registered after the config was loaded is found (#925).

## Consumers

- **KBService** -- receives `PyriteConfig` and uses it for all KB lookups and ephemeral lifecycle.
- **CLI commands** -- call `load_config()` / `save_config()` directly.
- **REST API factory** (`pyrite/server/api.py`) -- loads config at startup and injects it into endpoint dependencies.
- **MCP server** -- reads config for KB enumeration and tool registration.

## Related

- [[kb-service]] -- primary consumer of `PyriteConfig`
- [[storage-layer]] -- `PyriteDB` path comes from `Settings.index_path`
- [[rest-api]] -- injects config into FastAPI dependency chain

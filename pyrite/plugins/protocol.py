"""
Plugin Protocol

Defines the interface that pyrite plugins must implement.
Plugins can provide any subset of these capabilities.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .context import PluginContext

# The plugin validator contract (checked at registration in PluginRegistry,
# see `_conforms_to` / `register`): a validator binds
# ``(entry_type: str, fields: dict, ctx: dict)`` and returns a list of issue
# dicts, each with a ``message`` (or ``field``) key and an optional
# ``severity`` key (``"warning"``, or omitted/anything else for an error).
# There is exactly one contract; the legacy 2-argument
# ``(entry_type, fields)`` form is not supported (#379, #376, #48).
Validator = Callable[[str, dict, dict], list[dict]]

# The plugin hook contract: a hook binds ``(entry, ctx)``. ``before_*`` hooks
# may raise to abort the operation or return a (possibly modified) entry;
# ``after_*`` hook exceptions are logged and swallowed (HookRunner owns this
# raise/swallow contract).
Hook = Callable[[Any, dict], Any]


@runtime_checkable
class PyritePlugin(Protocol):
    """
    Protocol for pyrite plugins.

    Plugins extend pyrite with domain-specific functionality.
    All methods are optional — implement only what you need.

    Example plugin:

        class JournalismPlugin:
            name = "journalism"

            def get_entry_types(self):
                return {"investigation": InvestigationEntry}

            def get_cli_commands(self):
                return [("timeline", timeline_command)]
    """

    name: str

    def get_entry_types(self) -> dict[str, type]:
        """
        Return custom entry types.

        Returns:
            Dict mapping type name to Entry subclass.
            Example: {"investigation": InvestigationEntry}
        """
        ...

    def get_kb_types(self) -> list[str]:
        """
        Return custom KB type identifiers.

        Returns:
            List of KB type strings this plugin supports.
            Example: ["investigative", "osint"]
        """
        ...

    def get_cli_commands(self) -> list[tuple[str, Any]]:
        """
        Return CLI commands to register.

        Returns:
            List of (name, typer_command_or_app) tuples.
            Example: [("timeline", timeline_app)]
        """
        ...

    def get_mcp_tools(self, tier: str) -> dict[str, dict]:
        """
        Return MCP tools for the given tier.

        Args:
            tier: "read", "write", or "admin"

        Returns:
            Dict mapping tool name to tool definition.
            Each tool has "description", "inputSchema", "handler".
        """
        ...

    def get_db_columns(self) -> list[dict]:
        """
        Return additional DB columns for the entry table.

        Returns:
            List of column definitions.
            Example: [{"name": "capture_lane", "type": "TEXT"}]
        """
        ...

    def get_relationship_types(self) -> dict[str, dict]:
        """
        Return custom relationship types.

        Returns:
            Dict mapping relation name to metadata.
            Example: {"investigated_by": {"description": "...", "inverse": "investigates"}}
        """
        ...

    def get_workflows(self) -> dict[str, dict]:
        """
        Return workflow/state machine definitions.

        Returns:
            Dict mapping workflow name to definition:
            {
                "states": ["draft", "under_review", "published"],
                "initial": "draft",
                "transitions": [
                    {"from": "draft", "to": "under_review", "requires": "write"},
                    {"from": "under_review", "to": "published", "requires": "reviewer"},
                    {"from": "under_review", "to": "draft", "requires": "reviewer"},
                    {"from": "published", "to": "under_review", "requires": "write",
                     "requires_reason": True},
                ],
                "field": "review_status",  # entry field this workflow controls
            }
        """
        ...

    def get_db_tables(self) -> list[dict]:
        """
        Return custom DB table definitions.

        Returns:
            List of table definitions. Each dict has:
                name: str - table name
                columns: list[dict] - each with {name, type, nullable?, default?}
                indexes: list[dict] - each with {columns: list[str], unique?: bool}
                foreign_keys: list[dict] - each with {column, references: "table.column"}

            Example:
                [{"name": "vote",
                  "columns": [
                      {"name": "id", "type": "INTEGER", "primary_key": True},
                      {"name": "entry_id", "type": "TEXT"},
                      {"name": "kb_name", "type": "TEXT"},
                      {"name": "user_id", "type": "TEXT"},
                      {"name": "value", "type": "INTEGER"},
                      {"name": "created_at", "type": "TEXT"},
                  ],
                  "indexes": [{"columns": ["entry_id", "kb_name", "user_id"], "unique": True}],
                }]
        """
        ...

    def get_hooks(self) -> dict[str, list[Callable]]:
        """
        Return lifecycle hooks.

        Returns:
            Dict mapping hook name to list of callables.
            Hook names: before_save, after_save, before_delete, after_delete, before_index
            Each callable receives (entry, context) where context contains:
                kb_name, user, kb_schema, operation ("create"|"update"|"delete")
            before_* hooks can modify the entry or raise to abort.
            after_* hooks are for side effects (e.g. updating reputation).
        """
        ...

    def get_kb_presets(self) -> dict[str, dict]:
        """
        Return KB preset configurations.

        Returns:
            Dict mapping preset name to KB configuration dict.
            Used by `pyrite-admin kb init --preset <name>` to scaffold a new KB.
            Each preset dict should match the structure of kb.yaml:
            {name, description, types, policies, validation, directories}
        """
        ...

    def get_field_schemas(self) -> dict[str, dict[str, dict]]:
        """
        Return rich field schema definitions for plugin entry types.

        Returns:
            Dict mapping type name to dict of field definitions.
            Each field definition matches the FieldSchema.from_dict() format.

            Example:
                {
                    "zettel": {
                        "zettel_type": {
                            "type": "select",
                            "options": ["fleeting", "literature", "permanent", "hub"],
                            "default": "fleeting",
                            "description": "Classification of the zettel",
                        },
                        "maturity": {
                            "type": "select",
                            "options": ["seed", "sapling", "evergreen"],
                            "default": "seed",
                        },
                    }
                }
        """
        ...

    def get_type_metadata(self) -> dict[str, dict]:
        """
        Return type metadata for plugin-provided types.

        Returns:
            Dict mapping type name to metadata dict with keys:
                ai_instructions: str - guidance for AI agents on how to use this type
                field_descriptions: dict[str, str] - per-field human-readable descriptions
                display: dict - display hints (icon, color, layout)
                field_aliases: dict[str, str] - {alias: target} for keys a hand-written
                    file may spell another way, e.g. {"participants": "actors"}.
                    The type's class reads the target; the platform resolves the
                    alias through pyrite.schema.field_aliases.field_aliases().
                protocols, guidelines, goals, evaluation_rubric - as in kb.yaml
        """
        ...

    def get_collection_types(self) -> dict[str, dict]:
        """
        Return custom collection type definitions.

        Returns:
            Dict mapping collection type name to definition:
            {
                "evidence-board": {
                    "description": "Evidence board for investigations",
                    "default_view": "kanban",
                    "fields": {"confidence": {"type": "select", "options": [...]}},
                    "ai_instructions": "Use for organizing evidence in investigations...",
                    "icon": "shield",
                }
            }
        """
        ...

    def set_context(self, ctx: PluginContext) -> None:
        """
        Receive shared dependencies from the plugin infrastructure.

        Called after plugin discovery to inject config, db, and other
        shared resources. Plugins should store the context and use it
        in MCP tool handlers instead of calling load_config()/PyriteDB().

        Default implementation: no-op (backwards compatible).
        """
        ...

    def get_validators(self) -> list[Callable]:
        """
        Return custom validation functions.

        Returns:
            List of callables that accept (entry_type, data, context) and return
            list of validation error dicts.

            For create/update writes, context contains:
                kb_name: str - name of the knowledge base
                kb_schema: KBSchema - the KB's schema
                kb_type: str - the KB's type identifier
                _schema_version: int - the entry's schema version
                existing_entry: Entry | None - the entry before an update,
                    or None for a create

            Validators run twice per write: once on the entry as the caller
            built it, and again after the ``before_save`` hooks, because a
            hook may change the entry that is persisted. Both passes get the
            same context (an update's ``existing_entry`` is set on both), so
            a validator must be cheap and free of side effects: no I/O that
            changes state, no counters it relies on, no assumption that it
            runs once.

            Other validator call sites may provide only a subset. A user
            identity is not available on every write surface and is therefore
            not included.
        """
        ...

    def get_migrations(self) -> list[dict]:
        """
        Return schema migrations for plugin entry types.

        Returns:
            List of migration dicts, each with:
                entry_type: str
                from_version: int
                to_version: int
                fn: Callable[[dict], dict]
                description: str
        """
        ...

    def get_protocols(self) -> dict[str, type]:
        """
        Return additional protocol mixin classes (ADR-0017).

        The core 5 protocols (Assignable, Temporal, Locatable, Statusable,
        Prioritizable) are always available. Plugins can define additional
        protocols for domain-specific field patterns.

        Returns:
            Dict mapping protocol name to mixin dataclass.
            Example: {"claimable": ClaimableMixin}
        """
        ...

    def get_orient_supplement(self, kb_name: str, kb_type: str) -> dict[str, Any] | None:
        """
        Return supplementary orient data for a KB.

        Called during orient() to let plugins enrich the orientation summary
        with domain-specific context. Return None to skip.

        Args:
            kb_name: Name of the knowledge base
            kb_type: The KB's type identifier (e.g. "software", "default")

        Returns:
            Dict of additional keys to merge into the orient response, or None.
        """
        ...

    def get_rubric_checkers(self) -> dict[str, Callable]:
        """
        Return named rubric checker functions for QA evaluation.

        Returns:
            Dict mapping checker name to callable.
            Names should be namespaced: "plugin_name.checker_name"

            Example: {"software_kb.has_adr_number": check_adr_number}
        """
        ...

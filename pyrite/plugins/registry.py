"""
Plugin Registry

Discovers and manages pyrite plugins via Python entry points.

Plugins register via pyproject.toml:

    [project.entry-points."pyrite.plugins"]
    my_plugin = "my_package.plugin:MyPlugin"
"""

import inspect
import logging
from collections.abc import Callable
from typing import Any

from ..exceptions import PluginError
from .capabilities import Capability
from .context import PluginContext
from .protocol import PyritePlugin

logger = logging.getLogger(__name__)

# Probe arguments used only to check that a callable's signature *binds* the
# plugin contract's arity (inspect.signature(...).bind never calls the
# callable). Validators bind (entry_type, fields, ctx); hooks bind
# (entry, ctx). A callable that cannot bind these is refused at
# registration rather than discovered via a TypeError at call time (#379).
_VALIDATOR_PROBE_ARGS: tuple[Any, ...] = ("__probe_entry_type__", {}, {})
_HOOK_PROBE_ARGS: tuple[Any, ...] = (None, {})


def _binds(fn: Callable, probe_args: tuple[Any, ...]) -> bool:
    """True if ``fn``'s signature can bind ``probe_args`` positionally."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        # Builtins / C callables without introspectable signatures: assume
        # conforming rather than refuse something we can't check.
        return True
    try:
        sig.bind(*probe_args)
    except TypeError:
        return False
    return True


def _filter_conforming_validators(validators: list[Callable], plugin_name: str) -> list[Callable]:
    """Drop validators that don't bind (entry_type, fields, ctx), logging each once."""
    conforming = []
    for fn in validators:
        if _binds(fn, _VALIDATOR_PROBE_ARGS):
            conforming.append(fn)
        else:
            logger.warning(
                "Plugin '%s' validator %r does not accept (entry_type, fields, ctx); "
                "refusing it at registration rather than discovering the mismatch via "
                "a TypeError at call time",
                plugin_name,
                getattr(fn, "__name__", fn),
            )
    return conforming


def _filter_conforming_hooks(
    hooks: dict[str, list[Callable]], plugin_name: str
) -> tuple[dict[str, list[Callable]], set[str]]:
    """Drop hook callables that don't bind (entry, ctx), logging each once.

    Returns ``(conforming_hooks, before_hook_names_with_a_drop)``. The second
    element is how the registry tells ``HookRunner`` "a before_* hook for
    this hook point was dropped" so before_* dispatch can fail closed
    (coordinator blocker 2) instead of silently proceeding with whatever
    before_* hooks survived the filter.
    """
    filtered: dict[str, list[Callable]] = {}
    before_hooks_with_a_drop: set[str] = set()
    for hook_name, callables in hooks.items():
        kept = []
        for fn in callables:
            if _binds(fn, _HOOK_PROBE_ARGS):
                kept.append(fn)
            else:
                logger.warning(
                    "Plugin '%s' hook %r for %r does not accept (entry, ctx); "
                    "refusing it at registration rather than discovering the mismatch "
                    "via a TypeError at call time",
                    plugin_name,
                    getattr(fn, "__name__", fn),
                    hook_name,
                )
                if hook_name.startswith("before_"):
                    before_hooks_with_a_drop.add(hook_name)
        filtered[hook_name] = kept
    return filtered, before_hooks_with_a_drop


class _PluginConformance:
    """One plugin's validators/hooks, filtered to the contract exactly once.

    Computed lazily on first access and cached for the registry's lifetime
    (coordinator blocker 3: "checked at registration" was not true before --
    the filter, and its warning log, re-ran on every write and on every row
    of ``index health``). ``before_hooks_with_a_drop`` is the hook-point
    names (e.g. ``"before_save"``) for which at least one callable this
    plugin returned was dropped as non-conforming -- ``HookRunner`` uses
    this to fail before_* dispatch closed rather than silently proceeding
    with fewer hooks than the plugin declared (coordinator blocker 2).

    ``validators``/``hooks`` are signature-filtered only -- NOT also gated
    on the plugin's declared capability. The KB-scoped callers
    (``get_validators_for_kb``/``get_hooks_for_kb``, and everything built on
    them: ``run_validators``, ``HookRunner``) never applied the capability
    gate, only the unscoped ``get_all_validators``/``get_all_hooks`` did
    (Tier A r1500 / Option B) -- ``capability_declared`` lets the unscoped
    aggregators apply that gate on top of the same cached, signature-filtered
    result instead of recomputing it.
    """

    __slots__ = ("validators", "hooks", "before_hooks_with_a_drop", "capability_declared")

    def __init__(
        self,
        validators: list[Callable],
        hooks: dict[str, list[Callable]],
        before_hooks_with_a_drop: set[str],
        capability_declared: dict[str, bool],
    ) -> None:
        self.validators = validators
        self.hooks = hooks
        self.before_hooks_with_a_drop = before_hooks_with_a_drop
        self.capability_declared = capability_declared


# =============================================================================
# Method-to-capability map (Tier A r1500 / Option B / ADR-0002 addendum)
# =============================================================================
# Each dispatched method maps to the Capability that authorizes it.
# Aggregation helpers consult `plugin.capabilities` and skip methods whose
# capability is not declared. A plugin without a `capabilities` attribute
# defaults to the empty set — every dispatch loop skips it. Safe failure
# mode: a plugin that forgets to declare gets ignored entirely rather than
# silently half-loaded.
# =============================================================================

_METHOD_CAPABILITIES: dict[str, Capability] = {
    # SCHEMA — type system extension
    "get_entry_types": Capability.SCHEMA,
    "get_type_metadata": Capability.SCHEMA,
    "get_collection_types": Capability.SCHEMA,
    "get_field_schemas": Capability.SCHEMA,
    "get_protocols": Capability.SCHEMA,
    # STORAGE — DB and lifecycle
    "get_db_columns": Capability.STORAGE,
    "get_db_tables": Capability.STORAGE,
    "get_migrations": Capability.STORAGE,
    "get_validators": Capability.STORAGE,
    "get_hooks": Capability.STORAGE,
    # SURFACE — UI / API extension
    "get_cli_commands": Capability.SURFACE,
    "get_mcp_tools": Capability.SURFACE,
    "get_kb_presets": Capability.SURFACE,
    "get_kb_types": Capability.SURFACE,
    # DOMAIN — vocabulary
    "get_relationship_types": Capability.DOMAIN,
    "get_workflows": Capability.DOMAIN,
    "get_rubric_checkers": Capability.DOMAIN,
    # CONTEXT — runtime hooks
    "set_context": Capability.CONTEXT,
    "get_orient_supplement": Capability.CONTEXT,
}


def _plugin_declares(plugin: PyritePlugin, method_name: str) -> bool:
    """Return True if the plugin's declared capability set includes the
    capability required to dispatch ``method_name``.

    Methods not in ``_METHOD_CAPABILITIES`` (e.g. ``name`` access) are
    treated as always-allowed since they're not part of the dispatch
    surface that the optimization targets.

    A plugin without a ``capabilities`` attribute defaults to the
    empty set — every capability-gated method is skipped. Mitigated
    by the migration step that updates every in-tree plugin to
    declare its real set in the same commit (r1500 fire 2/3).
    """
    cap = _METHOD_CAPABILITIES.get(method_name)
    if cap is None:
        # Method isn't dispatch-gated (e.g. `name`); allow.
        return True
    declared = getattr(plugin, "capabilities", set()) or set()
    return cap in declared


# Core relationship types (platform-level, not plugin-provided)
CORE_RELATIONSHIP_TYPES: dict[str, dict] = {
    "subtask_of": {
        "inverse": "has_subtask",
        "description": "Task is a subtask of another task",
    },
    "has_subtask": {
        "inverse": "subtask_of",
        "description": "Task has a subtask",
    },
    "produces": {
        "inverse": "produced_by",
        "description": "Task produces an entry as evidence",
    },
    "produced_by": {
        "inverse": "produces",
        "description": "Entry was produced by a task",
    },
}

# Core KB presets (platform-level)
CORE_KB_PRESETS: dict[str, dict] = {}  # Populated lazily to avoid circular import

# Singleton registry
_registry: "PluginRegistry | None" = None


class PluginRegistry:
    """
    Discovers and manages pyrite plugins.

    Lazy singleton — call get_registry() to access.
    """

    ENTRY_POINT_GROUP = "pyrite.plugins"

    def __init__(self):
        self._plugins: dict[str, PyritePlugin] = {}
        self._discovered = False
        # Signature-conformance is checked once per plugin (coordinator
        # blocker 3, #379 follow-up: "checked at registration" wasn't true --
        # the filter re-ran, and re-warned, on every call). Keyed by plugin
        # name; populated lazily the first time that plugin's validators or
        # hooks are asked for, not necessarily at discover()/register() time,
        # since a plugin's get_validators()/get_hooks() are themselves only
        # ever called once they're actually needed.
        self._conformance_cache: dict[str, _PluginConformance] = {}

    def discover(self, strict: bool = False) -> None:
        """Discover plugins via entry points.

        Args:
            strict: If True, raise PluginError on any plugin load failure
                instead of logging a warning and continuing. Useful for
                development, CI, and ``pyrite ci``.
        """
        if self._discovered:
            return

        try:
            from importlib.metadata import entry_points

            eps = entry_points()
            # Python 3.12+ returns SelectableGroups
            if hasattr(eps, "select"):
                plugin_eps = eps.select(group=self.ENTRY_POINT_GROUP)
            else:
                plugin_eps = eps.get(self.ENTRY_POINT_GROUP, [])

            for ep in plugin_eps:
                try:
                    plugin_class = ep.load()
                    plugin = plugin_class()
                    if hasattr(plugin, "name"):
                        self._plugins[plugin.name] = plugin
                        logger.info("Loaded plugin: %s", plugin.name)
                    else:
                        msg = f"Plugin {ep.name} has no 'name' attribute"
                        if strict:
                            raise PluginError(msg)
                        logger.warning("%s, skipping", msg)
                except PluginError:
                    raise
                except Exception as e:
                    if strict:
                        raise PluginError(f"Failed to load plugin {ep.name}: {e}") from e
                    logger.warning("Failed to load plugin %s: %s", ep.name, e)

        except PluginError:
            raise
        except Exception as e:
            if strict:
                raise PluginError(f"Plugin discovery failed: {e}") from e
            logger.warning("Plugin discovery failed: %s", e)

        self._discovered = True

    def register(self, plugin: PyritePlugin) -> None:
        """Manually register a plugin (for testing or programmatic use)."""
        self._plugins[plugin.name] = plugin
        # A re-register (e.g. a test replacing a plugin instance under the
        # same name) must not serve a stale conformance result computed from
        # the previous instance's validators/hooks.
        self._conformance_cache.pop(plugin.name, None)

    def _conformance_for(self, plugin: PyritePlugin) -> "_PluginConformance":
        """This plugin's conforming validators/hooks, computed once and cached.

        Coordinator blocker 3: previously the signature filter re-ran (and
        re-warned) on every ``get_validators_for_kb``/``get_hooks_for_kb``
        call -- once per write, once per row of ``index health``. Now the
        plugin's ``get_validators()``/``get_hooks()`` are each called once,
        filtered once, and the result (plus which before_* hook points had a
        drop) is cached for the registry's lifetime.
        """
        cached = self._conformance_cache.get(plugin.name)
        if cached is not None:
            return cached

        capability_declared: dict[str, bool] = {}

        validators: list[Callable] = []
        if hasattr(plugin, "get_validators"):
            try:
                raw_validators = plugin.get_validators() or []
            except Exception as e:
                logger.error(
                    "Plugin %s get_validators failed: %s — validator data from this "
                    "plugin is missing",
                    plugin.name,
                    e,
                )
                raw_validators = []
            if raw_validators:
                # Signature-filtered regardless of capability declaration --
                # the KB-scoped callers (get_validators_for_kb and
                # everything built on it) never gated on capability, only
                # the unscoped get_all_validators does (see
                # capability_declared below).
                validators = _filter_conforming_validators(list(raw_validators), plugin.name)
                declared = _plugin_declares(plugin, "get_validators")
                capability_declared["get_validators"] = declared
                if not declared:
                    logger.warning(
                        "Plugin '%s' returned non-empty from get_validators but did not "
                        "declare the %s capability; the unscoped get_all_validators() "
                        "drops it (KB-scoped get_validators_for_kb does not). "
                        "Add the capability to the plugin's declared set, "
                        "or remove the method.",
                        plugin.name,
                        _METHOD_CAPABILITIES.get("get_validators"),
                    )

        hooks: dict[str, list[Callable]] = {}
        before_hooks_with_a_drop: set[str] = set()
        if hasattr(plugin, "get_hooks"):
            try:
                raw_hooks = plugin.get_hooks() or {}
            except Exception as e:
                logger.error(
                    "Plugin %s get_hooks failed: %s — hook data from this plugin is missing",
                    plugin.name,
                    e,
                )
                raw_hooks = {}
            if raw_hooks:
                hooks, before_hooks_with_a_drop = _filter_conforming_hooks(
                    dict(raw_hooks), plugin.name
                )
                declared = _plugin_declares(plugin, "get_hooks")
                capability_declared["get_hooks"] = declared
                if not declared:
                    logger.warning(
                        "Plugin '%s' returned non-empty from get_hooks but did not "
                        "declare the %s capability; the unscoped get_all_hooks() "
                        "drops it (KB-scoped get_hooks_for_kb does not).",
                        plugin.name,
                        _METHOD_CAPABILITIES.get("get_hooks"),
                    )

        conformance = _PluginConformance(
            validators, hooks, before_hooks_with_a_drop, capability_declared
        )
        self._conformance_cache[plugin.name] = conformance
        return conformance

    def set_context(self, ctx: PluginContext) -> None:
        """Inject shared context into all discovered plugins."""
        self.discover()
        for plugin in self._plugins.values():
            if hasattr(plugin, "set_context"):
                try:
                    plugin.set_context(ctx)
                except Exception as e:
                    logger.warning("Plugin %s set_context failed: %s", plugin.name, e)

    def get_plugin(self, name: str) -> PyritePlugin | None:
        """Get a plugin by name."""
        self.discover()
        return self._plugins.get(name)

    def list_plugins(self) -> list[str]:
        """List all discovered plugin names."""
        self.discover()
        return list(self._plugins.keys())

    # =========================================================================
    # Generic aggregation helpers
    # =========================================================================

    def _merge_dict(self, target: dict, source: dict, plugin_name: str, kind: str) -> None:
        """Merge source into target, warning on key collisions."""
        for key, value in source.items():
            if key in target:
                logger.warning(
                    "Plugin '%s' registers %s '%s' which conflicts with an "
                    "existing registration — last writer wins",
                    plugin_name,
                    kind,
                    key,
                )
            target[key] = value

    def _aggregate_dict(self, method_name: str, kind: str) -> dict:
        """Aggregate dict results from all plugins, warning on key collisions.

        Skips plugins whose declared ``capabilities`` set does not
        include the capability required for this method (Tier A r1500
        / Option B). A plugin that returns non-empty from an
        undeclared-capability method triggers a WARNING and the
        return is dropped.
        """
        self.discover()
        result: dict = {}
        for plugin in self._plugins.values():
            if not hasattr(plugin, method_name):
                continue
            declared = _plugin_declares(plugin, method_name)
            try:
                items = getattr(plugin, method_name)()
            except Exception as e:
                logger.error(
                    "Plugin %s %s failed: %s — %s data from this plugin is missing",
                    plugin.name,
                    method_name,
                    e,
                    kind,
                )
                continue
            if not items:
                continue
            if not declared:
                logger.warning(
                    "Plugin '%s' returned non-empty from %s but did not "
                    "declare the %s capability; dropping the return. "
                    "Add the capability to the plugin's declared set, "
                    "or remove the method.",
                    plugin.name,
                    method_name,
                    _METHOD_CAPABILITIES.get(method_name),
                )
                continue
            self._merge_dict(result, items, plugin.name, kind)
        return result

    def _aggregate_list(self, method_name: str) -> list:
        """Aggregate list results from all plugins.

        Skips plugins whose declared capabilities don't include the
        method's required capability; warns on undeclared non-empty
        drift (Tier A r1500 / Option B).

        ``get_validators`` is special-cased to the cached, checked-once
        conformance result (``_conformance_for``, coordinator blocker 3)
        instead of calling the plugin and filtering on every aggregation.
        """
        self.discover()
        if method_name == "get_validators":
            result: list = []
            for plugin in self._plugins.values():
                if not hasattr(plugin, "get_validators"):
                    continue
                conformance = self._conformance_for(plugin)
                # Unscoped: apply the capability gate on top of the cached,
                # signature-filtered result (get_validators_for_kb does not
                # gate on capability; see _PluginConformance's docstring).
                if conformance.validators and not conformance.capability_declared.get(
                    "get_validators", True
                ):
                    continue
                result.extend(conformance.validators)
            return result
        result = []
        for plugin in self._plugins.values():
            if not hasattr(plugin, method_name):
                continue
            declared = _plugin_declares(plugin, method_name)
            try:
                items = getattr(plugin, method_name)()
            except Exception as e:
                logger.error(
                    "Plugin %s %s failed: %s — data from this plugin is missing",
                    plugin.name,
                    method_name,
                    e,
                )
                continue
            if not items:
                continue
            if not declared:
                logger.warning(
                    "Plugin '%s' returned non-empty from %s but did not "
                    "declare the %s capability; dropping the return.",
                    plugin.name,
                    method_name,
                    _METHOD_CAPABILITIES.get(method_name),
                )
                continue
            result.extend(items)
        return result

    def _aggregate_dict_of_lists(self, method_name: str) -> dict[str, list]:
        """Aggregate dict-of-list results, extending lists per key.

        Same capability-skip + warn-on-drift contract as the other
        aggregation helpers (Tier A r1500 / Option B).

        ``get_hooks`` is special-cased to the cached, checked-once
        conformance result (``_conformance_for``, coordinator blocker 3)
        instead of calling the plugin and filtering on every aggregation.
        """
        self.discover()
        if method_name == "get_hooks":
            result: dict[str, list] = {}
            for plugin in self._plugins.values():
                if not hasattr(plugin, "get_hooks"):
                    continue
                conformance = self._conformance_for(plugin)
                # Unscoped: apply the capability gate on top of the cached,
                # signature-filtered result (get_hooks_for_kb does not gate
                # on capability; see _PluginConformance's docstring).
                if conformance.hooks and not conformance.capability_declared.get("get_hooks", True):
                    continue
                for key, lst in conformance.hooks.items():
                    result.setdefault(key, []).extend(lst)
            return result
        result = {}
        for plugin in self._plugins.values():
            if not hasattr(plugin, method_name):
                continue
            declared = _plugin_declares(plugin, method_name)
            try:
                items = getattr(plugin, method_name)()
            except Exception as e:
                logger.error(
                    "Plugin %s %s failed: %s — data from this plugin is missing",
                    plugin.name,
                    method_name,
                    e,
                )
                continue
            if not items:
                continue
            if not declared:
                logger.warning(
                    "Plugin '%s' returned non-empty from %s but did not "
                    "declare the %s capability; dropping the return.",
                    plugin.name,
                    method_name,
                    _METHOD_CAPABILITIES.get(method_name),
                )
                continue
            for key, lst in items.items():
                result.setdefault(key, []).extend(lst)
        return result

    # =========================================================================
    # Public aggregation methods — collect capabilities from all plugins
    # =========================================================================

    def get_all_entry_types(self) -> dict[str, type]:
        """Get all custom entry types from all plugins."""
        return self._aggregate_dict("get_entry_types", "entry type")

    def get_all_kb_types(self) -> list[str]:
        """Get all custom KB types from all plugins."""
        return self._aggregate_list("get_kb_types")

    def get_all_cli_commands(self) -> list[tuple[str, Any]]:
        """Get all CLI commands from all plugins."""
        return self._aggregate_list("get_cli_commands")

    def get_all_mcp_tools(self, tier: str) -> dict[str, dict]:
        """Get all MCP tools for the given tier from all plugins."""
        self.discover()
        tools: dict[str, dict] = {}
        for plugin in self._plugins.values():
            if hasattr(plugin, "get_mcp_tools"):
                try:
                    plugin_tools = plugin.get_mcp_tools(tier)
                    if plugin_tools:
                        self._merge_dict(tools, plugin_tools, plugin.name, "MCP tool")
                except Exception as e:
                    logger.warning("Plugin %s get_mcp_tools failed: %s", plugin.name, e)
        return tools

    def get_all_db_columns(self) -> list[dict]:
        """Get all additional DB columns from all plugins."""
        return self._aggregate_list("get_db_columns")

    def get_all_relationship_types(self) -> dict[str, dict]:
        """Get all relationship types: core + plugins."""
        result = dict(CORE_RELATIONSHIP_TYPES)
        plugin_types = self._aggregate_dict("get_relationship_types", "relationship type")
        result.update(plugin_types)
        return result

    def get_all_workflows(self) -> dict[str, dict]:
        """Get all workflow definitions from all plugins."""
        return self._aggregate_dict("get_workflows", "workflow")

    def validate_transition(
        self, workflow_name: str, current_state: str, target_state: str, user_role: str = ""
    ) -> bool:
        """Check if a workflow transition is allowed."""
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

    def get_all_db_tables(self) -> list[dict]:
        """Get all custom DB table definitions from all plugins."""
        return self._aggregate_list("get_db_tables")

    def get_all_hooks(self) -> dict[str, list[Callable]]:
        """Get all lifecycle hooks from all plugins, merged by hook name."""
        return self._aggregate_dict_of_lists("get_hooks")

    def get_all_kb_presets(self) -> dict[str, dict]:
        """Get all KB presets: core + plugins."""
        # Lazy-load core preset to avoid circular import
        if not CORE_KB_PRESETS:
            from ..models.task import TASK_KB_PRESET

            CORE_KB_PRESETS["task"] = TASK_KB_PRESET
        result = dict(CORE_KB_PRESETS)
        plugin_presets = self._aggregate_dict("get_kb_presets", "KB preset")
        result.update(plugin_presets)
        return result

    def get_type_default_subdirectory(self, entry_type: str, kb_type: str = "") -> str | None:
        """Default subdirectory a KB preset declares for ``entry_type``.

        The preset matching ``kb_type`` wins; otherwise the first preset that
        declares the type. Returns None when no preset places the type.
        """
        presets = self.get_all_kb_presets()
        ordered = [presets[kb_type]] if kb_type in presets else []
        ordered += [p for name, p in presets.items() if name != kb_type]
        for preset in ordered:
            subdir = (preset.get("types", {}).get(entry_type) or {}).get("subdirectory")
            if subdir:
                return subdir.strip("/")
        return None

    def get_all_type_metadata(self) -> dict[str, dict]:
        """Get type metadata from all plugins (deep-merged per type)."""
        self.discover()
        metadata: dict[str, dict] = {}
        for plugin in self._plugins.values():
            if hasattr(plugin, "get_type_metadata"):
                try:
                    plugin_meta = plugin.get_type_metadata()
                    if plugin_meta:
                        for type_name, meta in plugin_meta.items():
                            merged = metadata.setdefault(type_name, {})
                            # Generic: a key a plugin supplies is never dropped
                            # (the old merge named three keys and silently lost
                            # protocols, guidelines, goals, evaluation_rubric
                            # and, from #697, field_aliases). Maps merge per
                            # key; any other non-empty value replaces.
                            for key, value in meta.items():
                                if not value:
                                    continue
                                if key in merged and type(merged[key]) is not type(value):
                                    # Two plugins disagree on the shape of a
                                    # key (a map vs a string): keep the first,
                                    # say so, never drop the rest of the
                                    # second plugin's metadata.
                                    logger.warning(
                                        "Plugin %s type metadata '%s.%s' is a %s but another "
                                        "plugin gave a %s; keeping the first",
                                        plugin.name,
                                        type_name,
                                        key,
                                        type(value).__name__,
                                        type(merged[key]).__name__,
                                    )
                                    continue
                                if isinstance(value, dict):
                                    merged.setdefault(key, {}).update(value)
                                elif isinstance(value, list):
                                    # A copy: the caller may mutate the result,
                                    # and the plugin's own list must not change.
                                    merged[key] = list(value)
                                else:
                                    merged[key] = value
                except Exception as e:
                    logger.warning("Plugin %s get_type_metadata failed: %s", plugin.name, e)
        return metadata

    def get_all_collection_types(self) -> dict[str, dict]:
        """Get all custom collection types from all plugins."""
        return self._aggregate_dict("get_collection_types", "collection type")

    def get_all_field_schemas(self) -> dict[str, dict[str, dict]]:
        """Get all rich field schemas from all plugins (merged per type)."""
        self.discover()
        schemas: dict[str, dict[str, dict]] = {}
        for plugin in self._plugins.values():
            if hasattr(plugin, "get_field_schemas"):
                try:
                    plugin_schemas = plugin.get_field_schemas()
                    if plugin_schemas:
                        for type_name, fields in plugin_schemas.items():
                            schemas.setdefault(type_name, {}).update(fields)
                except Exception as e:
                    logger.warning("Plugin %s get_field_schemas failed: %s", plugin.name, e)
        return schemas

    def get_all_rubric_checkers(self) -> dict[str, Any]:
        """Collect named rubric checkers from core + all plugins."""
        from ..services.rubric_checkers import NAMED_CHECKERS

        checkers: dict[str, Any] = dict(NAMED_CHECKERS)
        plugin_checkers = self._aggregate_dict("get_rubric_checkers", "rubric checker")
        checkers.update(plugin_checkers)
        return checkers

    def get_orient_supplements(self, kb_name: str, kb_type: str) -> dict[str, Any]:
        """Collect orient supplements from all plugins."""
        self.discover()
        result: dict[str, Any] = {}
        for plugin in self._plugins.values():
            if hasattr(plugin, "get_orient_supplement"):
                try:
                    supplement = plugin.get_orient_supplement(kb_name, kb_type)
                    if supplement:
                        result.update(supplement)
                except Exception as e:
                    logger.warning("Plugin %s get_orient_supplement failed: %s", plugin.name, e)
        return result

    def get_all_validators(self) -> list[Callable]:
        """Get all validators from all plugins."""
        return self._aggregate_list("get_validators")

    def get_all_migrations(self) -> list[dict]:
        """Get all schema migrations from all plugins."""
        return self._aggregate_list("get_migrations")

    def get_all_protocols(self) -> dict[str, type]:
        """Get all protocol mixin classes: core 5 + plugin-provided (ADR-0017)."""
        from ..models.protocols import PROTOCOL_REGISTRY

        protocols = dict(PROTOCOL_REGISTRY)
        self.discover()
        for plugin in self._plugins.values():
            if hasattr(plugin, "get_protocols"):
                try:
                    plugin_protocols = plugin.get_protocols()
                    if plugin_protocols:
                        self._merge_dict(protocols, plugin_protocols, plugin.name, "protocol")
                except Exception as e:
                    logger.warning("Plugin %s get_protocols failed: %s", plugin.name, e)
        return protocols

    # =========================================================================
    # KB-type-scoped queries
    # =========================================================================

    def _plugin_matches_kb_type(self, plugin: PyritePlugin, kb_type: str) -> bool:
        """Check if a plugin should be active for a given KB type.

        Fails CLOSED: a plugin whose get_kb_types() raises is excluded
        from this KB rather than assumed compatible. DECIDED 2026-07-03
        (fail-open-exception-sweep site #6, see
        plugin-type-resolution-scoping) -- the check reads static plugin
        declarations, so a failure here is structural, not transient, and
        the blast radius of wrongly applying an incompatible plugin
        (global type remapping) outweighs the cost of skipping a plugin
        that might have been compatible.
        """
        if not kb_type:
            return True
        if not hasattr(plugin, "get_kb_types"):
            return True
        try:
            plugin_kb_types = plugin.get_kb_types()
            if not plugin_kb_types:
                return True
            return kb_type in plugin_kb_types
        except Exception:
            logger.warning(
                "Plugin %r's KB-type compatibility check failed; excluding it "
                "from KB type %r (fail closed)",
                getattr(plugin, "name", plugin),
                kb_type,
                exc_info=True,
            )
            return False

    def _aggregate_list_for_kb(self, method_name: str, kb_type: str) -> list:
        """Aggregate list results from plugins matching a KB type.

        ``get_validators`` is special-cased to the cached, checked-once
        conformance result (``_conformance_for``, coordinator blocker 3).
        """
        self.discover()
        if method_name == "get_validators":
            result: list = []
            for plugin in self._plugins.values():
                if not self._plugin_matches_kb_type(plugin, kb_type):
                    continue
                if hasattr(plugin, "get_validators"):
                    result.extend(self._conformance_for(plugin).validators)
            return result
        result = []
        for plugin in self._plugins.values():
            if not self._plugin_matches_kb_type(plugin, kb_type):
                continue
            if hasattr(plugin, method_name):
                try:
                    items = getattr(plugin, method_name)()
                    if items:
                        result.extend(items)
                except Exception as e:
                    logger.warning("Plugin %s %s failed: %s", plugin.name, method_name, e)
        return result

    def _aggregate_dict_of_lists_for_kb(self, method_name: str, kb_type: str) -> dict[str, list]:
        """Aggregate dict-of-list results from plugins matching a KB type.

        ``get_hooks`` is special-cased to the cached, checked-once
        conformance result (``_conformance_for``, coordinator blocker 3).
        """
        self.discover()
        if method_name == "get_hooks":
            result: dict[str, list] = {}
            for plugin in self._plugins.values():
                if not self._plugin_matches_kb_type(plugin, kb_type):
                    continue
                if not hasattr(plugin, "get_hooks"):
                    continue
                for key, lst in self._conformance_for(plugin).hooks.items():
                    result.setdefault(key, []).extend(lst)
            return result
        result = {}
        for plugin in self._plugins.values():
            if not self._plugin_matches_kb_type(plugin, kb_type):
                continue
            if hasattr(plugin, method_name):
                try:
                    items = getattr(plugin, method_name)()
                    if items:
                        for key, lst in items.items():
                            result.setdefault(key, []).extend(lst)
                except Exception as e:
                    logger.warning("Plugin %s %s failed: %s", plugin.name, method_name, e)
        return result

    def dropped_before_hooks_for_kb(self, kb_type: str = "") -> set[str]:
        """Hook-point names (e.g. ``"before_save"``) for which a plugin
        active in ``kb_type`` had at least one non-conforming callable
        dropped from that hook point (coordinator blocker 2).

        ``HookRunner`` uses this to fail before_* dispatch closed: a KB
        whose plugin declared a before_* hook that got silently dropped
        must refuse the write, the same as if that hook had raised, rather
        than proceeding as though the plugin had never declared it.
        """
        self.discover()
        dropped: set[str] = set()
        for plugin in self._plugins.values():
            if not self._plugin_matches_kb_type(plugin, kb_type):
                continue
            if not hasattr(plugin, "get_hooks"):
                continue
            dropped |= self._conformance_for(plugin).before_hooks_with_a_drop
        return dropped

    def _aggregate_dict_for_kb(self, method_name: str, kb_type: str) -> dict:
        """Aggregate dict results from plugins matching a KB type.

        Insertion order follows plugin discovery order, which is
        filesystem-dependent (importlib.metadata enumerates site-packages).
        Callers that pick a "first match" out of the result MUST therefore
        scope by kb_type, or their answer varies by machine -- see
        get_all_entry_types_for_kb.
        """
        self.discover()
        result: dict = {}
        for plugin in self._plugins.values():
            if not self._plugin_matches_kb_type(plugin, kb_type):
                continue
            if not hasattr(plugin, method_name):
                continue
            try:
                items = getattr(plugin, method_name)()
            except Exception as e:
                logger.warning("Plugin %s %s failed: %s", plugin.name, method_name, e)
                continue
            if items:
                result.update(items)
        return result

    def get_all_entry_types_for_kb(self, kb_type: str = "") -> dict[str, type]:
        """Entry types contributed by plugins active for this KB type.

        The unscoped get_all_entry_types() returns every installed plugin's
        types regardless of which KB is being written to. That is correct for
        the factory (it must be able to build any declared type) but wrong
        for type *resolution*, where picking the first subclass of a core
        type out of a globally-merged dict makes the answer depend on
        site-packages enumeration order. Concretely: both cascade's `actor`
        and social's `user_profile` subclass PersonEntry, so `person`
        resolved to whichever plugin happened to be discovered first --
        `actor` on one machine, `user_profile` on another, for the same code.
        See plugin-type-resolution-scoping.
        """
        return self._aggregate_dict_for_kb("get_entry_types", kb_type)

    def get_validators_for_kb(self, kb_type: str = "") -> list[Callable]:
        """Get validators scoped to a specific KB type."""
        return self._aggregate_list_for_kb("get_validators", kb_type)

    def run_validators(self, kb_type: str, entry_type: str, fields: dict, ctx: dict) -> list[dict]:
        """Run every validator scoped to ``kb_type`` against ``fields``.

        The call site for plugin validation on writes (#379): ``kb_schema.py``
        is this method's only caller, instead of open-coding the
        aggregation-plus-call loop. ``storage/index.py``'s health check does
        **not** call this method -- it fetches the validator list once per KB
        with ``get_validators_for_kb`` and calls each validator itself
        (``_check_invalid_status``), to avoid re-fetching the list per row;
        it applies the same non-dict filter this method does, so the two
        stay consistent without sharing a call site. Every returned
        validator already binds the ``(entry_type, fields, ctx)`` contract --
        registration refused any that didn't (see
        ``_filter_conforming_validators``) -- so there is no signature
        fallback here; a validator that still raises is a bug in that
        validator, not a contract mismatch, and is logged and skipped rather
        than aborting the whole validation pass.

        Return-shape normalization (coordinator should-fix 5): binding the
        3-argument signature says nothing about the return type -- a
        validator can bind ``(entry_type, fields, ctx)`` and still return
        the OLD ``list[str]`` shape. This method's caller (``kb_schema.py``)
        calls ``.get(...)`` on each item for ``severity``, which raises
        ``AttributeError`` on a plain string. A non-dict item is refused
        here -- logged and dropped -- rather than reaching a caller not
        expecting it; the dict items in the same return survive.
        """
        results: list[dict] = []
        for validator in self.get_validators_for_kb(kb_type):
            try:
                items = validator(entry_type, fields, ctx)
            except Exception:
                logger.warning(
                    "Validator %r raised for entry_type %r",
                    getattr(validator, "__name__", validator),
                    entry_type,
                    exc_info=True,
                )
                continue
            if not items:
                continue
            for item in items:
                if isinstance(item, dict):
                    results.append(item)
                else:
                    logger.warning(
                        "Validator %r for entry_type %r returned a non-dict item "
                        "(%s: %r); refusing it -- validators must return "
                        "list[dict], not list[str]",
                        getattr(validator, "__name__", validator),
                        entry_type,
                        type(item).__name__,
                        item,
                    )
        return results

    def get_hooks_for_kb(self, kb_type: str = "") -> dict[str, list[Callable]]:
        """Get lifecycle hooks scoped to a specific KB type.

        This is a pure lookup -- it does not run the hooks. Running them
        under the raise-before/swallow-after contract is HookRunner's job
        alone (#379): exactly one module implements that contract, so it
        cannot drift between a "core hook" code path and a "plugin hook"
        code path. See ``HookRunner._run``, which calls this to get the
        plugin-provided callables for a hook point.
        """
        return self._aggregate_dict_of_lists_for_kb("get_hooks", kb_type)


def get_registry() -> PluginRegistry:
    """Get the global plugin registry (lazy singleton)."""
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry

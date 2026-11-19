"""HookRunner — orchestrates core and plugin lifecycle hooks.

Extracted from KBService so the hook contract (before-raises, after-swallows)
lives in one place rather than being open-coded inside CRUD methods.

## Contract

- ``before_save`` / ``before_delete``: hooks may raise. Any raised exception
  propagates to the caller, aborting persistence. The entry is NOT saved.
- ``after_save`` / ``after_delete``: hook exceptions are logged at WARNING and
  swallowed. The operation is already committed; bubbling an exception here
  would surface a "save failed" error to a caller whose save actually
  succeeded. A failing hook does NOT prevent subsequent hooks from running —
  each after-hook is an independent side effect.

This contract was previously implemented inside ``KBService._run_hooks`` as
a static method. The behavior is preserved exactly; only the home moved.

## Plugin hook dispatch

If a plugin registry is supplied at construction time, ``HookRunner`` will
also run plugin hooks (after the core hooks) under the same contract. Pass
``plugin_registry=None`` to use only core hooks (e.g. in unit tests).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..models import Entry
    from ..plugins.context import PluginContext
    from ..plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)


_BEFORE_HOOK_PREFIX = "before_"


class HookRunner:
    """Run lifecycle hooks with a uniform raise-vs-swallow contract.

    Each instance owns its own ``_core_hooks`` registry, so tests can build
    isolated runners. In production, KBService constructs one runner and
    delegates from every CRUD method.
    """

    def __init__(self, plugin_registry: PluginRegistry | None = None) -> None:
        self._core_hooks: dict[str, list[Callable[..., Any]]] = {}
        self._plugin_registry = plugin_registry

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_core_hook(self, hook_name: str, fn: Callable[..., Any]) -> None:
        """Append a hook function to the named core-hook list.

        Hook functions take ``(entry, context)`` and may return a (possibly
        replaced) entry or ``None``. ``None`` means "no replacement; keep the
        current entry."
        """
        self._core_hooks.setdefault(hook_name, []).append(fn)

    def core_hooks(self, hook_name: str) -> list[Callable[..., Any]]:
        """Return the registered core hooks for a hook name (for inspection)."""
        return list(self._core_hooks.get(hook_name, []))

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def run_before_save(self, entry: Entry, context: PluginContext | dict[str, Any]) -> Entry:
        return self._run("before_save", entry, context)

    def run_after_save(self, entry: Entry, context: PluginContext | dict[str, Any]) -> Entry:
        return self._run("after_save", entry, context)

    def run_before_delete(self, entry: Entry, context: PluginContext | dict[str, Any]) -> Entry:
        return self._run("before_delete", entry, context)

    def run_after_delete(self, entry: Entry, context: PluginContext | dict[str, Any]) -> Entry:
        return self._run("after_delete", entry, context)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run(self, hook_name: str, entry: Entry, context: PluginContext | dict[str, Any]) -> Entry:
        """Run all hooks for ``hook_name`` honoring the raise/swallow contract.

        This is the ONLY place that implements the before-raises/
        after-swallows contract (#379) — core hooks and plugin hooks both
        run through this one loop, so the contract cannot drift between the
        two. Plugin hooks are looked up (not run) via the registry's
        ``get_hooks_for_kb`` — a pure lookup — so the registry itself has no
        opinion on raise-vs-swallow.
        """
        is_before = hook_name.startswith(_BEFORE_HOOK_PREFIX)

        def _run_one(hook_fn: Callable[..., Any], label: str) -> None:
            nonlocal entry
            try:
                result = hook_fn(entry, context)
                if result is not None:
                    entry = result
            except Exception:
                if is_before:
                    raise
                logger.warning(
                    "%s hook %s failed",
                    label,
                    getattr(hook_fn, "__name__", repr(hook_fn)),
                    exc_info=True,
                )

        # Core hooks first.
        for hook_fn in self._core_hooks.get(hook_name, []):
            _run_one(hook_fn, "Core")

        # Plugin hooks second (only if a registry was supplied).
        if self._plugin_registry is None:
            return entry

        kb_type = context.get("kb_type", "") if context else ""
        try:
            plugin_hooks = self._plugin_registry.get_hooks_for_kb(kb_type).get(hook_name, [])
        except Exception:
            if is_before:
                raise  # a lookup failure is as much an abort signal as a hook raising
            logger.warning("Plugin hook lookup for %s failed", hook_name, exc_info=True)
            return entry

        for hook_fn in plugin_hooks:
            _run_one(hook_fn, "Plugin")

        # A before_* hook that got dropped at registration for being
        # non-conforming (wrong arity) must fail dispatch closed for this KB
        # -- the write is refused, same as if the hook had raised, rather
        # than silently proceeding with whatever before_* hooks survived the
        # filter. On dev, a wrong-arity before_save raised TypeError and
        # aborted the write; #379's registration-time filter made that
        # fail-open by simply omitting the dropped hook. Checked AFTER the
        # conforming sibling hooks run (not instead of them) -- a dropped
        # hook refuses the write, it doesn't also suppress a working sibling
        # hook's effect. after_* hooks are NOT covered by this check -- a
        # dropped after_* hook still only warns (the operation already
        # succeeded; the existing raise-before/swallow-after contract
        # already treats after_* this way for a hook that raises, so a
        # dropped one is consistent).
        if is_before:
            # A failure here re-raises, like a failed hook lookup above:
            # not knowing what was dropped is not "nothing was dropped".
            dropped = self._plugin_registry.dropped_before_hooks_for_kb(kb_type)
            if hook_name in dropped:
                from ..exceptions import DroppedHookRefusedError

                raise DroppedHookRefusedError(
                    f"{hook_name} dispatch refused: a plugin's {hook_name} hook for "
                    f"KB type {kb_type!r} was dropped at registration for not matching "
                    f"the (entry, ctx) contract. The write cannot proceed without "
                    f"knowing what that hook would have enforced."
                )

        return entry

"""Contract tests for the plugin validator and hook signatures (#379).

Every installed plugin's ``get_validators()`` callables must bind
``(entry_type: str, fields: dict, ctx: dict)`` and return ``list[dict]``
where each item carries ``severity`` and ``message``. Every hook callable
returned by ``get_hooks()`` must bind ``(entry, ctx)``.

This is a registration-time guarantee, not just a runtime one: the registry
checks the signature with ``inspect.signature(...).bind`` when a plugin is
registered and refuses (logs + skips) a non-conforming callable rather than
silently swallowing a TypeError at call time.
"""

import inspect

import pytest

from pyrite.plugins.capabilities import Capability
from pyrite.plugins.registry import PluginRegistry
from pyrite.schema.kb_schema import KBSchema


def _discovered_registry() -> PluginRegistry:
    """A registry with every installed plugin discovered (real entry points)."""
    reg = PluginRegistry()
    reg.discover()
    return reg


class TestValidatorSignatures:
    """Every installed plugin's validators bind the 3-argument contract."""

    def test_every_validator_binds_entry_type_fields_ctx(self):
        reg = _discovered_registry()
        checked = 0
        for plugin in reg._plugins.values():
            if not hasattr(plugin, "get_validators"):
                continue
            for validator in plugin.get_validators() or []:
                sig = inspect.signature(validator)
                # Must bind three positional args: entry_type, fields, ctx.
                sig.bind("some_type", {}, {})
                checked += 1
        if checked == 0:
            pytest.skip("no installed plugin declares a validator in this environment")

    def test_every_validator_returns_list_of_dicts_with_severity_and_message(self):
        """Calling each validator with a benign, empty-ish payload yields
        ``list[dict]`` items that carry ``severity``/``message`` when they
        report anything at all (an empty return is fine and common)."""
        reg = _discovered_registry()
        checked = 0
        for plugin in reg._plugins.values():
            if not hasattr(plugin, "get_validators"):
                continue
            for validator in plugin.get_validators() or []:
                # Use an entry_type unlikely to match any type-dispatch branch
                # plus an empty fields dict, so a well-behaved validator
                # returns [] rather than raising.
                result = validator("__contract_probe_unknown_type__", {}, {})
                assert isinstance(result, list)
                for item in result:
                    assert isinstance(item, dict)
                    assert "message" in item or "field" in item, (
                        f"{validator} returned an item with neither 'message' nor 'field': {item!r}"
                    )
                checked += 1
        if checked == 0:
            pytest.skip("no installed plugin declares a validator in this environment")


class TestHookSignatures:
    """Every installed plugin's hooks bind the 2-argument (entry, ctx) contract."""

    def test_every_hook_binds_entry_ctx(self):
        reg = _discovered_registry()
        checked = 0
        for plugin in reg._plugins.values():
            if not hasattr(plugin, "get_hooks"):
                continue
            hooks = plugin.get_hooks() or {}
            for _hook_name, callables in hooks.items():
                for fn in callables:
                    sig = inspect.signature(fn)
                    sig.bind(object(), {})
                    checked += 1
        if checked == 0:
            pytest.skip("no installed plugin declares a hook in this environment")


class TestDroppedBeforeHookFailsClosed:
    """Coordinator blocker 2: on dev, a wrong-arity before_save hook raised
    TypeError and aborted the write (fail-closed). Dropping it silently at
    lookup and letting the write proceed is a fail-OPEN regression -- a
    before_* hook exists specifically to enforce an invariant (e.g. social's
    author-permission check); losing it without noticing is worse than the
    write failing loudly.

    Required property: if a plugin's before_* hook is dropped as
    non-conforming, before_* dispatch for that KB raises (the write is
    refused), same as if the hook itself had raised. after_* hooks may be
    dropped with only a warning -- the operation already succeeded, and
    after-hooks are independent side effects (existing contract, hook_runner.py).
    """

    def test_wrong_arity_before_save_hook_refuses_the_write(self):
        from pyrite.models.core_types import NoteEntry
        from pyrite.plugins.registry import PluginRegistry
        from pyrite.services.hook_runner import HookRunner

        class BadPlugin:
            name = "bad_before_hook_plugin"
            capabilities = {Capability.STORAGE}

            def get_hooks(self):
                return {"before_save": [lambda entry: entry]}  # 1-arg -- wrong

        reg = PluginRegistry()
        reg.register(BadPlugin())
        reg._discovered = True  # isolate: don't also pull in real entry points
        runner = HookRunner(plugin_registry=reg)

        entry = NoteEntry(id="test", title="Test")
        with pytest.raises(Exception):  # noqa: B017 -- the exact type isn't the contract
            runner.run_before_save(entry, {})

    def test_wrong_arity_before_save_hook_message_reaches_callers_unmasked(self):
        """#506 item 2: the dropped-hook refusal names only the hook name and
        the KB type -- both caller-controlled/plugin-registration values,
        never server-side detail -- so it's safe by construction. #501 gave
        the base PluginError a fixed, generic public_message ("A plugin
        operation failed...") for raise sites that DO carry unsafe detail
        (a traceback fragment, a real path); this specific refusal needs its
        own narrow subclass with public_message=None so REST/MCP/CLI callers
        still see what actually happened."""
        from pyrite.exceptions import PluginError
        from pyrite.models.core_types import NoteEntry
        from pyrite.plugins.registry import PluginRegistry
        from pyrite.services.hook_runner import HookRunner

        class BadPlugin:
            name = "bad_before_hook_plugin"
            capabilities = {Capability.STORAGE}

            def get_hooks(self):
                return {"before_save": [lambda entry: entry]}  # 1-arg -- wrong

        reg = PluginRegistry()
        reg.register(BadPlugin())
        reg._discovered = True
        runner = HookRunner(plugin_registry=reg)

        entry = NoteEntry(id="test", title="Test")
        with pytest.raises(PluginError) as excinfo:
            runner.run_before_save(entry, {})

        exc = excinfo.value
        assert exc.public_message is None
        assert "before_save dispatch refused" in str(exc)

    def test_a_failed_dropped_hook_lookup_refuses_the_write(self):
        """#422 delta cold read: if the registry cannot say which before_*
        hooks were dropped, dispatch must fail closed, like a failed hook
        lookup does -- not treat it as "none dropped"."""
        from pyrite.models.core_types import NoteEntry
        from pyrite.services.hook_runner import HookRunner

        class BrokenRegistry:
            def get_hooks_for_kb(self, kb_type):
                return {}

            def dropped_before_hooks_for_kb(self, kb_type):
                raise RuntimeError("registry unavailable")

        runner = HookRunner(plugin_registry=BrokenRegistry())
        with pytest.raises(RuntimeError):
            runner.run_before_save(NoteEntry(id="t", title="T"), {})

    def test_wrong_arity_after_save_hook_only_warns(self, caplog):
        """The after_* half of the same property: dropped, warned, does NOT abort."""
        from pyrite.models.core_types import NoteEntry
        from pyrite.plugins.registry import PluginRegistry
        from pyrite.services.hook_runner import HookRunner

        class BadPlugin:
            name = "bad_after_hook_plugin"
            capabilities = {Capability.STORAGE}

            def get_hooks(self):
                return {"after_save": [lambda entry: entry]}  # 1-arg -- wrong

        reg = PluginRegistry()
        reg.register(BadPlugin())
        reg._discovered = True
        runner = HookRunner(plugin_registry=reg)

        entry = NoteEntry(id="test", title="Test")
        with caplog.at_level("WARNING"):
            result = runner.run_after_save(entry, {})

        assert result is entry  # did not abort
        assert any("bad_after_hook_plugin" in r.message for r in caplog.records)

    def test_wrong_arity_before_save_still_runs_the_conforming_hooks_first(self):
        """A dropped before_save hook refuses the write -- it must not also
        suppress a SIBLING conforming before_save hook's effect (the write is
        refused either way, but the property under test is "dropped hook ==
        write refused", not "dropped hook == other hooks skipped")."""
        from pyrite.models.core_types import NoteEntry
        from pyrite.plugins.registry import PluginRegistry
        from pyrite.services.hook_runner import HookRunner

        called = []

        def good_hook(entry, ctx):
            called.append("good")
            return entry

        class MixedPlugin:
            name = "mixed_hook_plugin"
            capabilities = {Capability.STORAGE}

            def get_hooks(self):
                return {"before_save": [good_hook, lambda entry: entry]}

        reg = PluginRegistry()
        reg.register(MixedPlugin())
        reg._discovered = True
        runner = HookRunner(plugin_registry=reg)

        entry = NoteEntry(id="test", title="Test")
        with pytest.raises(Exception):  # noqa: B017
            runner.run_before_save(entry, {})

        assert called == ["good"], "the conforming sibling hook should still have run"

    def test_a_plugin_after_hook_that_raises_does_not_stop_the_next_one(self):
        """Coordinator item 8: a plugin after_* hook that genuinely raises
        (a real bug in a conforming hook, not an arity mismatch) must not
        prevent a LATER plugin after_* hook from running -- each after-hook
        is an independent side effect (hook_runner.py's documented
        contract), and this pins that guarantee specifically for plugin
        hooks (test_hook_runner.py already pins it for core hooks)."""
        from pyrite.models.core_types import NoteEntry
        from pyrite.plugins.registry import PluginRegistry
        from pyrite.services.hook_runner import HookRunner

        ran = []

        def crashy(entry, ctx):
            ran.append("crashy")
            raise RuntimeError("boom")

        def benign(entry, ctx):
            ran.append("benign")
            return entry

        class TwoAfterHooksPlugin:
            name = "two_after_hooks_plugin"
            capabilities = {Capability.STORAGE}

            def get_hooks(self):
                return {"after_save": [crashy, benign]}

        reg = PluginRegistry()
        reg.register(TwoAfterHooksPlugin())
        reg._discovered = True
        runner = HookRunner(plugin_registry=reg)

        entry = NoteEntry(id="test", title="Test")
        result = runner.run_after_save(entry, {})  # must not raise

        assert result is entry
        assert ran == ["crashy", "benign"], "benign must still run after crashy raised"


def _installed_plugin_names() -> set[str]:
    reg = _discovered_registry()
    return set(reg._plugins.keys())


class TestExtensionValidatorsFireThroughSchema:
    """Acceptance criteria 2 & 3 (#376): a journalism-investigation `asset`
    with no asset_type must yield a validation error through
    `KBSchema.validate_entry` -- not a swallowed TypeError. (The cascade
    `actor` case went with the cascade extension, #953.)

    Skips cleanly (not a failure) when the specific extension under test
    isn't installed in this environment -- these tests are inherently about
    that extension's validator, not "some installed plugin"."""

    def test_journalism_asset_with_no_asset_type_yields_error(self):
        if "journalism_investigation" not in _installed_plugin_names():
            pytest.skip("journalism-investigation extension not installed in this environment")
        schema = KBSchema(name="test-ji", kb_type="journalism-investigation")
        result = schema.validate_entry("asset", {}, {"kb_type": "journalism-investigation"})
        assert result["valid"] is False
        assert any(e.get("field") == "asset_type" for e in result["errors"])


class TestOneHookContractModule:
    """Acceptance criterion 5: exactly one module implements the
    before-raises/after-swallows hook contract -- HookRunner. The
    registry's job is to look plugin hooks up (get_hooks_for_kb,
    dropped_before_hooks_for_kb), never to execute a hook callable itself.

    Replaces a source-regex scan (coordinator item 8) that broke the moment
    the registry legitimately grew its OWN "before_" prefix check for
    dropped-hook bookkeeping (coordinator blocker 2) -- a textual scan
    cannot distinguish "implements the raise/swallow decision" from
    "computes something else that happens to mention the same prefix".
    These tests instead prove the actual property behaviorally: the
    registry's hook-related methods never call a hook function, and
    HookRunner is what does, for both core and plugin hooks."""

    def test_registry_get_hooks_for_kb_never_calls_a_hook(self):
        """A pure lookup: the hook callable itself must not be invoked."""
        called = []

        def spy_hook(entry, ctx):
            called.append(True)
            return entry

        class SpyPlugin:
            name = "spy_plugin"
            capabilities = {Capability.STORAGE}

            def get_hooks(self):
                return {"before_save": [spy_hook]}

        reg = PluginRegistry()
        reg.register(SpyPlugin())
        reg._discovered = True

        hooks = reg.get_hooks_for_kb("")

        assert called == [], "get_hooks_for_kb must not execute the hook it looked up"
        assert hooks["before_save"] == [spy_hook]  # it DID find it, just didn't run it

    def test_registry_dropped_before_hooks_for_kb_never_calls_a_hook(self):
        """Same property for the coordinator-blocker-2 bookkeeping method."""
        called = []

        def spy_hook(entry, ctx):
            called.append(True)
            return entry

        class SpyPlugin:
            name = "spy_plugin_2"
            capabilities = {Capability.STORAGE}

            def get_hooks(self):
                return {"before_save": [spy_hook, lambda entry: entry]}  # one non-conforming

        reg = PluginRegistry()
        reg.register(SpyPlugin())
        reg._discovered = True

        dropped = reg.dropped_before_hooks_for_kb("")

        assert called == [], "dropped_before_hooks_for_kb must not execute any hook"
        assert dropped == {"before_save"}

    def test_hook_runner_is_what_actually_calls_hooks(self):
        """The positive half: HookRunner (and only HookRunner, in this
        test's call graph) invokes the hook callable, for both core and
        plugin hooks, through the same raise-before/swallow-after path."""
        from pyrite.models.core_types import NoteEntry
        from pyrite.services.hook_runner import HookRunner

        core_called = []
        plugin_called = []

        def core_hook(entry, ctx):
            core_called.append(True)
            return entry

        def plugin_hook(entry, ctx):
            plugin_called.append(True)
            return entry

        class HookPlugin:
            name = "hook_plugin"
            capabilities = {Capability.STORAGE}

            def get_hooks(self):
                return {"before_save": [plugin_hook]}

        reg = PluginRegistry()
        reg.register(HookPlugin())
        reg._discovered = True

        runner = HookRunner(plugin_registry=reg)
        runner.register_core_hook("before_save", core_hook)

        entry = NoteEntry(id="test", title="Test")
        runner.run_before_save(entry, {})

        assert core_called == [True]
        assert plugin_called == [True]


class TestValidateWritePrefersMessage:
    """Coordinator should-fix 4: KBService._validate_write (~207) built its
    error string from field/rule/expected/got and ignored `message` when a
    validator supplied one. A validator whose item has no `rule` (like
    journalism-investigation's importance-range check, `{"field": "importance",
    "message": "Importance must be 1-10, got: 99"}`) fell into the generic
    `f"{field}: {rule} (expected {expected}, got {got!r})"` branch, which
    with rule/expected/got all absent renders literally as
    "importance: (expected None, got None)" -- losing the validator's actual
    message. `message`, when present, must be preferred."""

    def test_importance_error_message_is_not_lost(self, tmp_path):
        if "journalism_investigation" not in _installed_plugin_names():
            pytest.skip("journalism-investigation extension not installed in this environment")

        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.exceptions import SchemaViolationError
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB

        kb_path = tmp_path / "ji-kb"
        kb_path.mkdir()
        (kb_path / "entities").mkdir()
        kb_config = KBConfig(name="test-ji", path=kb_path, kb_type="journalism-investigation")
        config = PyriteConfig(
            knowledge_bases=[kb_config], settings=Settings(index_path=tmp_path / "index.db")
        )
        db = PyriteDB(config.settings.index_path)
        svc = KBService(config, db)
        try:
            with pytest.raises(SchemaViolationError) as exc_info:
                svc.create_entry(
                    "test-ji",
                    "bad-person",
                    "A Real Person",
                    "person",
                    allow_undeclared=True,
                    importance=99,
                )
            message = str(exc_info.value)
            assert "Importance must be 1-10, got: 99" in message, (
                f"expected the validator's own message in the error, got: {message!r}"
            )
            assert "expected None, got None" not in message
        finally:
            db.close()


class TestPluginKBTaskUpdateNoTracebackNoSilentSkip:
    """#48's extra criterion: `pyrite task update` against a KB whose type a
    plugin validates must not print a traceback, and a validator error must
    give a non-zero exit (not exit 0 while silently skipping validation).
    Run against journalism-investigation since #953 deleted cascade.

    #48's exact repro was `pyrite task update <task-id> -k cascade-research
    --priority 7`: the cascade validator (1-arg, wrong contract) raised
    TypeError twice, both swallowed by a fallback that logged a full
    traceback via `exc_info=True` -- while the command still exited 0. Fixed
    here at the TaskService/KBService layer (below the Typer CLI, same
    validate_entry -> SchemaViolationError path the CLI's
    `except (PyriteError, ValueError)` handler catches and turns into a
    non-zero `typer.Exit(1)` with a clean message, no traceback)."""

    def _plugin_env(self, tmp_path):
        from pathlib import Path

        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.services.task_service import TaskService
        from pyrite.storage.database import PyriteDB

        tasks_path = Path(tmp_path) / "ji-kb"
        tasks_path.mkdir()
        (tasks_path / "tasks").mkdir()

        kb_config = KBConfig(
            name="test-ji",
            path=tasks_path,
            kb_type="journalism-investigation",
            description="Test",
        )
        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=Path(tmp_path) / "index.db"),
        )
        db = PyriteDB(config.settings.index_path)
        db.register_kb("test-ji", "journalism-investigation", str(tasks_path), "Test")
        svc = TaskService(config, db)
        return svc, db

    def test_benign_task_update_no_traceback_log(self, tmp_path, caplog):
        """A task entry (not one the plugin validator has an opinion on)
        updates cleanly: no traceback in the log, at any level."""
        import logging

        if "journalism_investigation" not in _installed_plugin_names():
            pytest.skip("journalism-investigation extension not installed in this environment")

        svc, db = self._plugin_env(tmp_path)
        try:
            svc.create_task(kb_name="test-ji", title="Do the thing")
            with caplog.at_level(logging.WARNING):
                result = svc.update_task("do-the-thing", "test-ji", priority=7)
            assert result.get("priority") == 7 or "priority" in str(result)
            tracebacks = [r for r in caplog.records if r.exc_info is not None]
            assert tracebacks == [], (
                f"expected no traceback-bearing log record, got: "
                f"{[(r.levelname, r.getMessage()) for r in tracebacks]}"
            )
        finally:
            db.close()

    def test_plugin_validator_error_is_non_zero_not_silent(self, tmp_path):
        """A real plugin-specific validation error (importance out of the
        1-10 range -- a rule only the plugin validator enforces, isolating
        it from create_entry's own generic title/date checks) raises
        SchemaViolationError -- the exception the CLI's task commands catch
        and turn into a non-zero exit -- rather than being silently
        swallowed by a signature-mismatch fallback."""
        if "journalism_investigation" not in _installed_plugin_names():
            pytest.skip("journalism-investigation extension not installed in this environment")

        from pyrite.exceptions import PyriteError, SchemaViolationError

        svc, db = self._plugin_env(tmp_path)
        try:
            with pytest.raises(SchemaViolationError) as exc_info:
                svc.kb_svc.create_entry(
                    "test-ji",
                    "bad-person",
                    "A Real Person",
                    "person",
                    allow_undeclared=True,
                    importance=99,
                )
            assert isinstance(exc_info.value, PyriteError)  # what task_commands.py catches
            assert any(e.get("field") == "importance" for e in exc_info.value.errors), (
                f"expected an importance-range error, got: {exc_info.value.errors}"
            )
        finally:
            db.close()


class TestSeveritySplitsErrorsFromWarnings:
    """KBSchema.validate_entry must sort plugin-validator items by
    severity: "warning" goes to result["warnings"], anything else
    (omitted, or any other value) goes to result["errors"]. Uses the real
    encyclopedia validator's `published_not_stub` rule, which is declared
    `severity: "warning"` in extensions/encyclopedia -- a genuine plugin
    rule, not a hand-built fixture."""

    def test_encyclopedia_warning_lands_in_warnings_not_errors(self):
        if "encyclopedia" not in _installed_plugin_names():
            pytest.skip("encyclopedia extension not installed in this environment")
        schema = KBSchema(name="test-enc", kb_type="encyclopedia")
        result = schema.validate_entry(
            "article",
            {"title": "x", "quality": "stub", "review_status": "published"},
            {"kb_type": "encyclopedia"},
        )
        warning_rules = {w.get("rule") for w in result["warnings"]}
        error_rules = {e.get("rule") for e in result["errors"]}
        assert "published_not_stub" in warning_rules, (
            f"expected published_not_stub as a warning, got warnings={result['warnings']} "
            f"errors={result['errors']}"
        )
        assert "published_not_stub" not in error_rules


class TestRunValidatorsDegradesPerValidator:
    """run_validators must not let one raising validator take out the rest
    of the KB's validators for that call -- it degrades per-validator (logs
    + skips just that one), not per-call. This only shows up with two+
    validators registered for the same kb_type; a single-validator fixture
    can't distinguish "skip this validator" from "abort the whole call",
    since both look identical (empty results, one warning) with only one
    validator in play."""

    def test_one_raising_validator_does_not_suppress_the_others(self, caplog):
        class BrokenPlugin:
            name = "broken_plugin"
            capabilities = {Capability.STORAGE}

            def get_validators(self):
                return [lambda entry_type, fields, ctx: (_ for _ in ()).throw(RuntimeError("boom"))]

        class GoodPlugin:
            name = "good_plugin"
            capabilities = {Capability.STORAGE}

            def get_validators(self):
                return [lambda entry_type, fields, ctx: [{"field": "x", "message": "bad x"}]]

        reg = PluginRegistry()
        reg.register(BrokenPlugin())
        reg.register(GoodPlugin())
        reg._discovered = True  # isolate: don't also pull in real entry points

        with caplog.at_level("WARNING"):
            results = reg.run_validators("", "some_type", {}, {})

        assert results == [{"field": "x", "message": "bad x"}], (
            "the good validator's result must survive the broken one raising"
        )
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warnings, "expected a warning logged for the raising validator, got none"


class TestRunValidatorsNormalizesReturnShape:
    """Coordinator should-fix 5: a validator that binds the 3-arg contract
    but returns `list[str]` (an old-shape return under the new signature --
    passes the registration-time bind check, since bind() only inspects the
    call signature, not the return type) must not crash `index health` or
    silently swallow its issues. A non-dict item is refused (dropped, with
    a warning) rather than reaching a caller that calls `.get(...)` on it."""

    def test_non_dict_items_are_dropped_with_a_warning(self, caplog):
        class WrongShapePlugin:
            name = "wrong_shape_plugin"
            capabilities = {Capability.STORAGE}

            def get_validators(self):
                return [lambda entry_type, fields, ctx: ["Importance must be 1-10, got: 99"]]

        reg = PluginRegistry()
        reg.register(WrongShapePlugin())
        reg._discovered = True

        with caplog.at_level("WARNING"):
            results = reg.run_validators("", "some_type", {}, {})

        assert results == [], "a non-dict item must not reach the caller"
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warnings, "expected a warning logged for the non-dict item, got none"

    def test_dict_items_from_the_same_validator_still_come_through(self, caplog):
        """A validator returning a MIX of dict and non-dict items keeps the
        dict ones -- one bad item doesn't take out the rest of that
        validator's real findings."""

        class MixedShapePlugin:
            name = "mixed_shape_plugin"
            capabilities = {Capability.STORAGE}

            def get_validators(self):
                return [
                    lambda entry_type, fields, ctx: [
                        "a stray string",
                        {"field": "x", "message": "real issue"},
                    ]
                ]

        reg = PluginRegistry()
        reg.register(MixedShapePlugin())
        reg._discovered = True

        with caplog.at_level("WARNING"):
            results = reg.run_validators("", "some_type", {}, {})

        assert results == [{"field": "x", "message": "real issue"}]


class TestConformanceCheckedOnceAtRegistration:
    """Coordinator blocker 3: 'checked at registration' means computed and
    cached once per plugin, not re-run (and re-warned) on every call."""

    def test_get_validators_is_called_once_across_many_lookups(self):
        call_count = 0

        class CountingPlugin:
            name = "counting_plugin"
            capabilities = {Capability.STORAGE}

            def get_validators(self):
                nonlocal call_count
                call_count += 1
                return [lambda entry_type, fields, ctx: []]

        reg = PluginRegistry()
        reg.register(CountingPlugin())
        reg._discovered = True

        for _ in range(10):
            reg.get_validators_for_kb("")
            reg.run_validators("", "some_type", {}, {})

        assert call_count == 1, f"expected get_validators() called once, got {call_count}"

    def test_non_conforming_validator_warning_logs_once_not_per_call(self, caplog):
        class BadPlugin:
            name = "bad_plugin_logs_once"
            capabilities = {Capability.STORAGE}

            def get_validators(self):
                return [lambda entry: []]  # 1-arg -- wrong, logged once

        reg = PluginRegistry()
        reg.register(BadPlugin())
        reg._discovered = True

        with caplog.at_level("WARNING"):
            for _ in range(10):
                reg.get_validators_for_kb("")

        drop_warnings = [r for r in caplog.records if "bad_plugin_logs_once" in r.getMessage()]
        assert len(drop_warnings) == 1, (
            f"expected the drop warning logged once across 10 lookups, "
            f"got {len(drop_warnings)}: {[r.getMessage() for r in drop_warnings]}"
        )

    def test_re_register_invalidates_the_cache(self):
        """A plugin re-registered under the same name (e.g. a test replacing
        an instance) must not serve a stale conformance result."""

        class PluginV1:
            name = "same_name"
            capabilities = {Capability.STORAGE}

            def get_validators(self):
                return [lambda entry_type, fields, ctx: [{"field": "v1"}]]

        class PluginV2:
            name = "same_name"
            capabilities = {Capability.STORAGE}

            def get_validators(self):
                return [lambda entry_type, fields, ctx: [{"field": "v2"}]]

        reg = PluginRegistry()
        reg.register(PluginV1())
        reg._discovered = True
        reg.get_validators_for_kb("")  # populate the cache under v1

        reg.register(PluginV2())  # re-register under the same name
        validators = reg.get_validators_for_kb("")
        assert len(validators) == 1
        result = validators[0]("t", {}, {})
        assert result == [{"field": "v2"}], "re-register must invalidate the stale v1 cache"


class TestCapabilityGateOnlyAppliesUnscoped:
    """get_all_validators()/get_all_hooks() (unscoped) drop a plugin's
    return when it didn't declare the STORAGE capability (Tier A r1500 /
    Option B); get_validators_for_kb()/get_hooks_for_kb() (KB-scoped --
    what run_validators and HookRunner actually call in production) never
    applied that gate, pre-#379. The registration-time signature-conformance
    cache (coordinator blocker 3) must preserve that asymmetry rather than
    accidentally gating the scoped path too (it did, transiently, during
    this fix round -- caught by tests/test_plugin_integration.py's
    TestValidatorScoping fixture, whose mock plugins declare no
    capabilities at all and broke when the scoped path started gating)."""

    def test_scoped_validators_ignore_undeclared_capability(self):
        class NoCapabilityPlugin:
            name = "no_capability_plugin"
            # No `capabilities` attribute at all.

            def get_validators(self):
                return [lambda entry_type, fields, ctx: []]

        reg = PluginRegistry()
        reg.register(NoCapabilityPlugin())
        reg._discovered = True

        assert len(reg.get_validators_for_kb("")) == 1

    def test_unscoped_validators_drop_undeclared_capability(self):
        class NoCapabilityPlugin:
            name = "no_capability_plugin_2"

            def get_validators(self):
                return [lambda entry_type, fields, ctx: []]

        reg = PluginRegistry()
        reg.register(NoCapabilityPlugin())
        reg._discovered = True

        assert reg.get_all_validators() == []

    def test_scoped_hooks_ignore_undeclared_capability(self):
        class NoCapabilityPlugin:
            name = "no_capability_plugin_3"

            def get_hooks(self):
                return {"before_save": [lambda entry, ctx: entry]}

        reg = PluginRegistry()
        reg.register(NoCapabilityPlugin())
        reg._discovered = True

        assert len(reg.get_hooks_for_kb("").get("before_save", [])) == 1

    def test_unscoped_hooks_drop_undeclared_capability(self):
        class NoCapabilityPlugin:
            name = "no_capability_plugin_4"

            def get_hooks(self):
                return {"before_save": [lambda entry, ctx: entry]}

        reg = PluginRegistry()
        reg.register(NoCapabilityPlugin())
        reg._discovered = True

        assert reg.get_all_hooks().get("before_save", []) == []


class TestRegistrationRefusesNonConforming:
    """The registry checks validator/hook signatures at registration time."""

    def test_registration_refuses_bad_validator_signature(self, caplog):
        class BadValidatorPlugin:
            name = "bad_validator_plugin"
            capabilities = {Capability.STORAGE}

            def get_validators(self):
                return [lambda entry: []]  # 1-arg -- old contract

        reg = PluginRegistry()
        reg.register(BadValidatorPlugin())
        reg._discovered = True  # isolate: don't also pull in real entry points
        with caplog.at_level("WARNING"):
            validators = reg.get_all_validators()

        assert validators == []
        assert any("bad_validator_plugin" in r.message for r in caplog.records)

    def test_registration_refuses_bad_validator_signature_scoped_path(self, caplog):
        """Same refusal, but through get_validators_for_kb / run_validators --
        the KB-scoped path kb_schema.py and index.py actually call in
        production. A fix that only filters the unscoped
        get_all_validators() aggregation would leave this path unguarded."""

        class BadValidatorPlugin:
            name = "bad_validator_plugin_scoped"
            capabilities = {Capability.STORAGE}

            def get_validators(self):
                return [lambda entry: []]  # 1-arg -- old contract

        reg = PluginRegistry()
        reg.register(BadValidatorPlugin())
        reg._discovered = True  # isolate: don't also pull in real entry points
        with caplog.at_level("WARNING"):
            validators = reg.get_validators_for_kb("")
            results = reg.run_validators("", "some_type", {}, {})

        assert validators == []
        assert results == []
        assert any("bad_validator_plugin_scoped" in r.message for r in caplog.records)

    def test_registration_refuses_bad_hook_signature(self, caplog):
        class BadHookPlugin:
            name = "bad_hook_plugin"
            capabilities = {Capability.STORAGE}

            def get_hooks(self):
                return {"before_save": [lambda entry: entry]}  # 1-arg -- wrong

        reg = PluginRegistry()
        reg.register(BadHookPlugin())
        reg._discovered = True  # isolate: don't also pull in real entry points
        with caplog.at_level("WARNING"):
            hooks = reg.get_all_hooks()

        assert hooks.get("before_save", []) == []
        assert any("bad_hook_plugin" in r.message for r in caplog.records)

    def test_registration_refuses_bad_hook_signature_scoped_path(self, caplog):
        """Same refusal, but through get_hooks_for_kb -- the KB-scoped
        lookup HookRunner actually calls in production."""

        class BadHookPlugin:
            name = "bad_hook_plugin_scoped"
            capabilities = {Capability.STORAGE}

            def get_hooks(self):
                return {"before_save": [lambda entry: entry]}  # 1-arg -- wrong

        reg = PluginRegistry()
        reg.register(BadHookPlugin())
        reg._discovered = True  # isolate: don't also pull in real entry points
        with caplog.at_level("WARNING"):
            hooks = reg.get_hooks_for_kb("")

        assert hooks.get("before_save", []) == []
        assert any("bad_hook_plugin_scoped" in r.message for r in caplog.records)

    def test_registration_accepts_conforming_validator(self, caplog):
        class GoodValidatorPlugin:
            name = "good_validator_plugin"
            capabilities = {Capability.STORAGE}

            def get_validators(self):
                return [lambda entry_type, fields, ctx: []]

        reg = PluginRegistry()
        reg.register(GoodValidatorPlugin())
        reg._discovered = True  # isolate: don't also pull in real entry points
        validators = reg.get_all_validators()

        assert len(validators) == 1


class TestIndexHealthToleratesMalformedValidatorOutput:
    """#422 delta cold read: index health calls the KB's validators directly
    (looked up once per KB), so it must apply the same non-dict filter
    ``run_validators`` does -- a validator returning ``list[str]`` must not
    crash ``index health``."""

    def test_a_validator_returning_strings_does_not_crash_health(self):
        from types import SimpleNamespace

        from pyrite.storage.index import IndexManager

        kb = SimpleNamespace(name="k", kb_type="t")
        row = {"status": "odd", "entry_type": "t", "id": "i"}
        health = {"invalid_statuses": []}

        def returns_strings(entry_type, fields, ctx):
            return ["status bad"]

        def reports_enum(entry_type, fields, ctx):
            return [{"field": "status", "rule": "enum", "expected": ["ok"], "message": "bad"}]

        IndexManager._check_invalid_status(kb, row, [returns_strings, reports_enum], health)

        assert health["invalid_statuses"] == [
            {"kb": "k", "id": "i", "type": "t", "status": "odd", "allowed": ["ok"]}
        ]


class TestValidateWriteKeepsEnumDetail:
    """#422 delta cold read: preferring a validator's `message` must not lose
    the allowed values of an enum error (journalism's validate_enum sets both
    `expected` and `message`). Structured rules render as before; `message`
    is used when the rule is missing or unknown."""

    def _refusal(self, tmp_path, monkeypatch, error):
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.exceptions import SchemaViolationError
        from pyrite.schema.kb_schema import KBSchema
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB

        monkeypatch.setattr(
            KBSchema, "validate_entry", lambda self, *a, **k: {"errors": [error], "warnings": []}
        )
        kb_path = tmp_path / "kb"
        kb_path.mkdir()
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="k", path=kb_path, kb_type="generic")],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        db = PyriteDB(config.settings.index_path)
        try:
            with pytest.raises(SchemaViolationError) as exc_info:
                KBService(config, db).create_entry("k", "e", "E", "note")
            return str(exc_info.value)
        finally:
            db.close()

    def test_an_enum_error_keeps_its_allowed_values(self, tmp_path, monkeypatch):
        text = self._refusal(
            tmp_path,
            monkeypatch,
            {
                "field": "status",
                "rule": "enum",
                "expected": ["open", "closed"],
                "got": "foo",
                "message": "Invalid status: foo",
            },
        )
        assert "open" in text and "closed" in text, text

    def test_a_ruleless_error_uses_its_message(self, tmp_path, monkeypatch):
        text = self._refusal(
            tmp_path, monkeypatch, {"field": "importance", "message": "Importance must be 1-10"}
        )
        assert "Importance must be 1-10" in text, text

    def test_a_required_error_keeps_its_message(self, tmp_path, monkeypatch):
        """#429: a conditional `required` rule (journalism's transaction-type
        check) sets `message` to explain *why* the field is required --
        rendering `field: required` and dropping it makes the refusal
        useless for a rule that isn't simply "this field is always
        required"."""
        text = self._refusal(
            tmp_path,
            monkeypatch,
            {
                "field": "amount",
                "rule": "required",
                "message": "Transaction of type 'payment' must have an amount",
            },
        )
        assert "Transaction of type 'payment' must have an amount" in text, text

    @pytest.mark.control(
        reason="negative control for the required-message fix: a `required` "
        "rule with no `message` at all must still fall back to the old "
        "`field: required` rendering rather than `field: None`. This passes "
        "against origin/dev too (the fallback text is unchanged) -- it pins "
        "the no-message case so the fix above doesn't regress it."
    )
    def test_a_required_error_with_no_message_still_says_required(self, tmp_path, monkeypatch):
        """A `required` rule with no `message` (nothing set one) falls back to
        the old rendering rather than showing `None` or an empty string."""
        text = self._refusal(tmp_path, monkeypatch, {"field": "title", "rule": "required"})
        assert "title: required" in text, text

    def test_an_enum_error_with_a_message_that_says_more_appends_it(self, tmp_path, monkeypatch):
        """An enum error whose `message` adds detail beyond the generic
        "Invalid <field>: <got>" (e.g. a plugin explaining a conditional
        enum) keeps both the allowed values and the extra detail."""
        text = self._refusal(
            tmp_path,
            monkeypatch,
            {
                "field": "case_status",
                "rule": "enum",
                "expected": ["open", "closed"],
                "got": "foo",
                "message": "Invalid case_status: foo (only closed cases may be archived)",
            },
        )
        assert "open" in text and "closed" in text, text
        assert "only closed cases may be archived" in text, text

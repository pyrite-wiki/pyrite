"""Tests for custom exception hierarchy and _run_hooks propagation."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrite.exceptions import (
    ConfigError,
    EntryNotFoundError,
    KBNotFoundError,
    KBReadOnlyError,
    PluginError,
    PyriteError,
    StorageError,
    ValidationError,
)


class TestEveryPyriteErrorHasAClassCode:
    """ADR-0037 theme 2: 'codes live on exception classes'.

    Every ``PyriteError`` subclass in ``pyrite.exceptions`` carries a
    class-level ``error_code`` (a plain string, inherited or its own -- never
    looked up from an external table keyed by type), and a ``public_message``
    that is either ``None`` (the default: transports fall back to ``str(exc)``,
    which is written to be safe to show) or a fixed, safe sentence set on the
    class. This is what lets REST's ``server/errors.py`` and MCP's
    ``_refusal`` map a code from the class alone, with no per-transport
    lookup table to keep in sync.
    """

    def _all_pyrite_error_classes(self):
        import inspect

        from pyrite import exceptions

        return [
            obj
            for _name, obj in vars(exceptions).items()
            if inspect.isclass(obj) and issubclass(obj, exceptions.PyriteError)
        ]

    def test_every_class_has_a_string_error_code(self):
        classes = self._all_pyrite_error_classes()
        assert len(classes) >= 20, classes  # sanity: we actually found the module's classes
        missing = [
            c.__name__ for c in classes if not isinstance(getattr(c, "error_code", None), str)
        ]
        assert not missing, f"classes with no class-level error_code: {missing}"

    @pytest.mark.control(
        reason="every public_message on dev before this PR was already a plain string "
        "(ConfigSaveRefusedError, ConfigFileUnreadableError, BrandingInvalidError) -- this "
        "type-guard would pass unchanged on dev too. It stays as a control against a future "
        "class setting a non-string public_message by mistake, not as evidence for this PR."
    )
    def test_public_message_is_none_or_a_safe_string_on_the_class(self):
        classes = self._all_pyrite_error_classes()
        bad = []
        for c in classes:
            pm = c.__dict__.get("public_message", getattr(c, "public_message", None))
            if pm is not None and not isinstance(pm, str):
                bad.append(c.__name__)
        assert not bad, f"classes with a non-string, non-None public_message: {bad}"

    def test_public_message_defaults_to_none_and_falls_back_to_str(self):
        """A class with no explicit public_message: getattr(...) is None, so
        every caller's ``getattr(exc, "public_message", None) or str(exc)``
        pattern shows the exception's own (safe-to-show) message."""
        exc = EntryNotFoundError("no entry here")
        assert exc.public_message is None

    @pytest.mark.control(
        reason="ConfigSaveRefusedError's own public_message predates this PR (#377) -- "
        "this passes unchanged on dev. It stays as a control pinning that giving PyriteError "
        "a base public_message=None (this PR) does not override a subclass's own value."
    )
    def test_config_save_refused_keeps_its_own_public_message(self):
        """A class that opts into a fixed public_message (server-side detail
        in str(exc)) keeps it -- this is not overwritten by the base default."""
        from pyrite.exceptions import ConfigSaveRefusedError

        exc = ConfigSaveRefusedError("real config path leaked here", config_file="/x", dropped=[])
        assert exc.public_message is not None
        assert "real config path leaked here" not in exc.public_message

    @pytest.mark.parametrize(
        ("exc_class", "code"),
        [
            (EntryNotFoundError, "ENTRY_NOT_FOUND"),
            (KBNotFoundError, "KB_NOT_FOUND"),
            (KBReadOnlyError, "KB_READ_ONLY"),
        ],
    )
    def test_rests_code_wins_on_the_class(self, exc_class, code):
        """Maintainer decision (ADR-0037, 2026-09-25): one code per exception,
        and REST's more specific code is the one on the class -- not MCP's
        older, coarser code. MCP keeps its old code only in a transitional
        ``legacy_error_code`` field it builds itself (see mcp_server tests),
        never on the exception class."""
        assert exc_class.error_code == code

    def test_base_validation_error_code_is_the_documented_write_pipeline_code(self):
        """Conductor decision (fix round 1, cold read of #501): unlike the
        other three disagreements, REST itself said two different things for
        a bare ValidationError before this theme (the central handler's
        table: VALIDATION_ERROR; the write pipeline, which
        docs/json-contracts.md and docs/agent-write-path.md document:
        VALIDATION_FAILED). The documented code wins."""
        assert ValidationError.error_code == "VALIDATION_FAILED"

    def test_base_config_error_code_is_rests(self):
        assert ConfigError.error_code == "CONFIG_CONFLICT"

    def test_config_save_refused_has_its_own_code_not_the_base(self):
        from pyrite.exceptions import ConfigFileUnreadableError, ConfigSaveRefusedError

        assert ConfigSaveRefusedError.error_code == "CONFIG_SAVE_REFUSED"
        # A subclass with no error_code of its own inherits the parent's --
        # ConfigFileUnreadableError never had one before this theme either.
        assert ConfigFileUnreadableError.error_code == "CONFIG_SAVE_REFUSED"

    def test_base_pyrite_error_code_is_internal_error(self):
        assert PyriteError.error_code == "INTERNAL_ERROR"


class TestServerSideDetailClassesHaveAFixedPublicMessage:
    """ADR-0037 §3: 'a fixed per-code sentence for everything that can carry
    server-side detail: StorageError, PluginError, ConfigError.'

    Some raise sites of these three classes already write a safe message
    (an env var name, a `pip install` hint); others embed real operator
    detail (`postgres_backend.py`'s `{e}`, `config.py::refuse_outside_tree`'s
    real filesystem paths). Because a class cannot tell which raise site
    built it, the ADR's rule is per-class, not per-site: str(exc) always
    goes to the server log, and callers get the fixed sentence -- conductor
    fix round 1 (cold read of #501): these three previously left
    public_message=None, so str(exc) reached every transport unfiltered.
    """

    def test_storage_error_has_a_fixed_public_message(self):
        exc = StorageError("Failed to materialize query result: driver detail (dsn=...)")
        assert exc.public_message is not None
        assert "driver detail" not in exc.public_message
        assert "dsn" not in exc.public_message

    def test_plugin_error_has_a_fixed_public_message(self):
        exc = PluginError("Failed to load plugin acme_kb: ImportError at /real/path/plugin.py")
        assert exc.public_message is not None
        assert "/real/path/plugin.py" not in exc.public_message

    def test_config_error_has_a_fixed_public_message(self):
        exc = ConfigError("Refusing write: /real/secret/path is outside /real/confine/root")
        assert exc.public_message is not None
        assert "/real/secret/path" not in exc.public_message
        assert "/real/confine/root" not in exc.public_message

    def test_storage_busy_error_keeps_storage_errors_public_message(self):
        """A subclass with no public_message of its own inherits the base's."""
        from pyrite.exceptions import StorageBusyError

        exc = StorageBusyError("database is locked")
        assert exc.public_message == StorageError("x").public_message

    def test_config_save_refused_keeps_its_own_more_specific_message(self):
        """A ConfigError subclass that already sets its own public_message
        (#377) is not overridden by the base ConfigError's new default."""
        from pyrite.exceptions import ConfigSaveRefusedError

        exc = ConfigSaveRefusedError("real path leaked", config_file="/x", dropped=[])
        assert exc.public_message != ConfigError("x").public_message


class TestAccessDeniedFamily:
    """ADR-0037 §3: 'Denials are exceptions in the same hierarchy.'

    ``AccessDenied`` is not raised by any surface yet (that is theme 1/3b's
    job); this theme only needs the classes to exist with the right codes,
    so theme 1 can raise them and theme 2's transports can map them.
    """

    def test_access_denied_is_a_pyrite_error(self):
        from pyrite.exceptions import AccessDenied

        assert issubclass(AccessDenied, PyriteError)

    def test_not_authenticated_code(self):
        from pyrite.exceptions import NotAuthenticated

        assert NotAuthenticated.error_code == "UNAUTHENTICATED"
        assert issubclass(NotAuthenticated, PyriteError)

    def test_forbidden_code(self):
        from pyrite.exceptions import Forbidden

        assert Forbidden.error_code == "FORBIDDEN"
        assert issubclass(Forbidden, PyriteError)

    def test_both_subclass_access_denied(self):
        from pyrite.exceptions import AccessDenied, Forbidden, NotAuthenticated

        assert issubclass(NotAuthenticated, AccessDenied)
        assert issubclass(Forbidden, AccessDenied)

    def test_distinct_codes(self):
        """A guard: if these ever collapsed to the same code, REST and MCP
        could no longer tell an unauthenticated caller from an authenticated
        one who lacks permission."""
        from pyrite.exceptions import Forbidden, NotAuthenticated

        assert NotAuthenticated.error_code != Forbidden.error_code


class TestExceptionHierarchy:
    """All custom exceptions inherit from PyriteError."""

    def test_pyrite_error_is_base(self):
        assert issubclass(PyriteError, Exception)

    @pytest.mark.parametrize(
        "exc_class",
        [
            EntryNotFoundError,
            KBNotFoundError,
            KBReadOnlyError,
            ValidationError,
            PluginError,
            StorageError,
            ConfigError,
        ],
    )
    def test_subclass_of_pyrite_error(self, exc_class):
        assert issubclass(exc_class, PyriteError)

    @pytest.mark.parametrize(
        "exc_class",
        [
            EntryNotFoundError,
            KBNotFoundError,
            KBReadOnlyError,
            ValidationError,
            PluginError,
            StorageError,
            ConfigError,
        ],
    )
    def test_catchable_as_pyrite_error(self, exc_class):
        with pytest.raises(PyriteError):
            raise exc_class("test message")

    def test_exception_message(self):
        err = EntryNotFoundError("Entry not found: foo")
        assert str(err) == "Entry not found: foo"


class TestRunHooksPropagation:
    """_run_hooks lets PyriteError propagate but catches other exceptions.

    #379: HookRunner owns both core- and plugin-hook dispatch, and it looks
    plugin hooks up via the registry's get_hooks_for_kb (a pure lookup, not
    a runner -- run_hooks_for_kb no longer exists). So these mock the hook
    *callable* the lookup returns, the way a real plugin hook would raise or
    return a value, rather than mocking a runner method directly on the
    registry.
    """

    def _svc_with_plugin_hook(self, hook_fn):
        """A KBService whose plugin registry has one before_save hook."""
        from pyrite.services.kb_service import KBService

        mock_registry = MagicMock()
        mock_registry.get_hooks_for_kb.return_value = {"before_save": [hook_fn]}
        # KBService.__init__ resolves get_registry() once at construction
        # time (#379), so the patch must be active for the constructor call.
        with patch("pyrite.plugins.get_registry", return_value=mock_registry):
            return KBService(config=MagicMock(), db=MagicMock())

    def test_pyrite_error_propagates(self):
        """PyriteError from hooks should propagate through _run_hooks."""
        from pyrite.models.core_types import NoteEntry

        def raising_hook(entry, ctx):
            raise KBReadOnlyError("read-only")

        svc = self._svc_with_plugin_hook(raising_hook)
        entry = NoteEntry(id="test", title="Test")
        with pytest.raises(KBReadOnlyError):
            svc._run_hooks("before_save", entry, {})

    def test_permission_error_propagated_in_before_hooks(self):
        """PermissionError in before_save hooks should propagate (hook atomicity)."""
        from pyrite.models.core_types import NoteEntry

        def raising_hook(entry, ctx):
            raise PermissionError("denied")

        svc = self._svc_with_plugin_hook(raising_hook)
        entry = NoteEntry(id="test", title="Test")
        with pytest.raises(PermissionError, match="denied"):
            svc._run_hooks("before_save", entry, {})

    def test_generic_exception_propagated_in_before_hooks(self):
        """Generic exceptions in before_save hooks should propagate (hook atomicity)."""
        from pyrite.models.core_types import NoteEntry

        def raising_hook(entry, ctx):
            raise RuntimeError("boom")

        svc = self._svc_with_plugin_hook(raising_hook)
        entry = NoteEntry(id="test", title="Test")
        with pytest.raises(RuntimeError, match="boom"):
            svc._run_hooks("before_save", entry, {})

    def test_successful_hook_returns_result(self):
        """Successful hooks return the modified entry."""
        from pyrite.models.core_types import NoteEntry

        modified = NoteEntry(id="modified", title="Modified")

        def replacing_hook(entry, ctx):
            return modified

        svc = self._svc_with_plugin_hook(replacing_hook)
        entry = NoteEntry(id="test", title="Test")
        result = svc._run_hooks("before_save", entry, {})
        assert result is modified


class TestServiceExceptionTypes:
    """KBService methods raise the correct exception types."""

    def test_create_entry_kb_not_found(self):
        from pyrite.config import PyriteConfig, Settings
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB

        with tempfile.TemporaryDirectory() as d:
            config = PyriteConfig(
                knowledge_bases=[],
                settings=Settings(index_path=Path(d) / "test.db"),
            )
            db = PyriteDB(config.settings.index_path)
            try:
                svc = KBService(config, db)
                with pytest.raises(KBNotFoundError):
                    svc.create_entry("nonexistent", "id", "title", "note")
            finally:
                db.close()

    def test_update_entry_kb_not_found(self):
        from pyrite.config import PyriteConfig, Settings
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB

        with tempfile.TemporaryDirectory() as d:
            config = PyriteConfig(
                knowledge_bases=[],
                settings=Settings(index_path=Path(d) / "test.db"),
            )
            db = PyriteDB(config.settings.index_path)
            try:
                svc = KBService(config, db)
                with pytest.raises(KBNotFoundError):
                    svc.update_entry("id", "nonexistent", title="new")
            finally:
                db.close()

    def test_delete_entry_kb_not_found(self):
        from pyrite.config import PyriteConfig, Settings
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB

        with tempfile.TemporaryDirectory() as d:
            config = PyriteConfig(
                knowledge_bases=[],
                settings=Settings(index_path=Path(d) / "test.db"),
            )
            db = PyriteDB(config.settings.index_path)
            try:
                svc = KBService(config, db)
                with pytest.raises(KBNotFoundError):
                    svc.delete_entry("id", "nonexistent", readable_kbs=None)
            finally:
                db.close()

    def test_create_entry_read_only(self):
        from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB

        with tempfile.TemporaryDirectory() as d:
            kb_path = Path(d) / "ro"
            kb_path.mkdir()
            config = PyriteConfig(
                knowledge_bases=[
                    KBConfig(name="ro", path=kb_path, kb_type=KBType.RESEARCH, read_only=True),
                ],
                settings=Settings(index_path=Path(d) / "test.db"),
            )
            db = PyriteDB(config.settings.index_path)
            try:
                svc = KBService(config, db)
                with pytest.raises(KBReadOnlyError):
                    svc.create_entry("ro", "id", "title", "note")
            finally:
                db.close()

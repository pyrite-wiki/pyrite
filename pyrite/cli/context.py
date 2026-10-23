"""Shared CLI context — eliminates duplicated PyriteDB + service construction."""

import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from ..config import PyriteConfig, load_config
from ..exceptions import PyriteError, StorageError
from ..services.kb_registry_service import KBRegistryService
from ..services.kb_service import KBService
from ..storage.database import PyriteDB
from ..storage.index import IndexManager

logger = logging.getLogger(__name__)


def require_kb(
    config: PyriteConfig,
    name: object,
    *,
    db: PyriteDB | None = None,
    output_format: str | None = None,
):
    """The KB a ``-k``/``--kb`` or positional KB names, or None when it names none.

    The one resolver (``services/kb_resolver.py``, #952) for the CLI, which
    runs as the local principal and so is unscoped: an unknown name and a
    shortname (link syntax only) are ``KBNotFoundError`` -- ``KB_NOT_FOUND``,
    exit 1, with the resolver's suggestion and ``did_you_mean``; a blank
    name is None. A KB ``pyrite kb add`` registered in the index rather than
    config.yaml is found too: through ``db`` when the command has the index
    open, else through :func:`get_config_with_registered_kbs`, which opens
    the registry only for a name config.yaml does not know. Every command
    that takes a KB resolves it here before reading or writing anything
    (#924); ``tests/test_kb_walk_cli.py`` walks the Typer trees to check.

    A command on the ADR-0046 outcome contract passes no ``output_format``
    and the refusal is raised for the root to render. A legacy command
    renders its own errors, so it passes its format and the refusal is
    printed here in the CLI's error shape (``cli_error_from``), exit 1 --
    the same split ``input_file_or_exit`` makes.
    """
    from ..services.access_policy import UNSCOPED
    from ..services.kb_resolver import resolve_kb

    if db is None and isinstance(name, str) and name.strip() and config.get_kb(name) is None:
        config = get_config_with_registered_kbs(config, name=name)
    try:
        return resolve_kb(config, name, readable_kbs=UNSCOPED, db=db)
    except PyriteError as exc:
        if output_format is None:
            raise
        from ..utils.errors import cli_error_from

        cli_error_from(exc, output_format)
        raise  # unreachable: cli_error_from always exits


def require_entry_kb(
    entry_id: str,
    kb_name: object,
    *,
    entry_type: str | None = None,
    output_format: str | None = None,
) -> str:
    """The KB that holds ``entry_id``, for a command whose ``-k`` is optional (#911).

    The CLI's door to ``KBService.locate``, the one rule for an id given
    with or without a KB: ``-k`` resolved by :func:`require_kb`, then the id
    found in that KB; without ``-k``, the one KB that holds it (config.yaml
    and registry-only KBs, as ``pyrite get`` searches), or ``AMBIGUOUS`` with
    ``candidate_kbs`` when several do, or ``NOT_FOUND``. A command calls it
    before it reads or writes anything, and then works in the KB it returns
    -- never in ``kb_name or ""``, which is a KB named '' that holds nothing
    (rows written there belong to no entry). Unscoped: the CLI runs as the
    local principal.

    ``output_format`` as :func:`require_kb`: None raises for the root to
    render (a command on the ADR-0046 contract); a legacy command passes its
    format and the refusal is printed here, exit 1.
    """
    from ..exceptions import EntryNotFoundError
    from ..services.access_policy import UNSCOPED

    try:
        with cli_context(kbs=(kb_name,)) as (_config, _db, svc):
            return svc.locate(entry_id, kb_name, readable_kbs=UNSCOPED, entry_type=entry_type)
    except PyriteError as exc:
        if output_format is None:
            raise
        from ..utils.errors import cli_error_from

        # The CLI spells a missing entry NOT_FOUND (utils/errors.py).
        code = "NOT_FOUND" if isinstance(exc, EntryNotFoundError) else None
        cli_error_from(exc, output_format, error_code=code)
        raise  # unreachable: cli_error_from always exits


def _init_base(
    kbs: tuple[object, ...], output_format: str | None
) -> tuple[PyriteConfig, PyriteDB, KBService]:
    """Shared construction for all CLI context managers."""
    config, db = get_config_and_db()
    try:
        _require_kbs(config, db, kbs, output_format)
    except BaseException:
        db.close()
        raise
    svc = KBService(config, db)
    return config, db, svc


def _require_kbs(
    config: PyriteConfig, db: PyriteDB, kbs: tuple[object, ...], output_format: str | None
) -> None:
    """Resolve every KB a command named (``cli_context(kbs=...)``) before its body runs."""
    for name in kbs:
        require_kb(config, name, db=db, output_format=output_format)


@contextmanager
def cli_context(
    *, kbs: tuple[object, ...] = (), output_format: str | None = None
) -> Generator[tuple[PyriteConfig, PyriteDB, KBService], None, None]:
    """Provide config, db, and service for CLI commands.

    ``kbs`` are the KB names the command took (``-k``, a positional KB, a
    ``--target-kb``): each is resolved by :func:`require_kb` once the
    registry is open, so a name that is not a KB is refused before the body
    runs. None and blank names name no KB and pass. ``output_format`` is
    what a legacy command renders in (see :func:`require_kb`).
    """
    config, db, svc = _init_base(kbs, output_format)
    try:
        yield config, db, svc
    finally:
        db.close()


@contextmanager
def cli_registry_context(
    *, kbs: tuple[object, ...] = (), output_format: str | None = None
) -> Generator[tuple[PyriteConfig, PyriteDB, KBService, KBRegistryService], None, None]:
    """Provide config, db, service, and registry for CLI commands that need KB management."""
    config, db, svc = _init_base(kbs, output_format)
    index_mgr = IndexManager(db, config)
    registry = KBRegistryService(config, db, index_mgr)
    registry.seed_from_config()
    try:
        yield config, db, svc, registry
    finally:
        db.close()


@contextmanager
def cli_db_context(
    *, kbs: tuple[object, ...] = (), output_format: str | None = None
) -> Generator[tuple[PyriteConfig, PyriteDB], None, None]:
    """Provide config and db for commands that only need db access."""
    config, db = get_config_and_db()
    try:
        _require_kbs(config, db, kbs, output_format)
        yield config, db
    finally:
        db.close()


def open_index_db(config: PyriteConfig) -> PyriteDB:
    """Open the index database for ``config`` exactly as configured.

    Unlike ``get_config_and_db`` this does **not** merge DB-registered KBs
    into ``config``: it is for the commands that never did (``pyrite-admin``,
    ``pyrite-read``, ``pyrite ci``, ``search``, ``init``, the repo commands),
    which #380 moved here unchanged so that opening the index happens in one
    module. The caller closes it.
    """
    return PyriteDB(config.settings.index_path)


@contextmanager
def index_db_context(config: PyriteConfig) -> Generator[PyriteDB, None, None]:
    """``open_index_db`` as a context manager: the database is closed on exit."""
    db = open_index_db(config)
    try:
        yield db
    finally:
        db.close()


def get_config_and_db(config: PyriteConfig | None = None) -> tuple[PyriteConfig, PyriteDB]:
    """Get config and db without a context manager, merging DB-registered KBs.

    Pass an already-loaded config when a command owns config loading; otherwise
    this helper loads it. Callers are responsible for calling db.close().
    """
    config = config or load_config()
    db = PyriteDB(config.settings.index_path)
    try:
        db.merge_registered_kbs(config)
    except Exception:
        # Do not leak the engine if registry loading fails before this helper
        # can return the database to its caller.
        db.close()
        raise
    return config, db


def open_index_for_validation(
    config: PyriteConfig,
) -> tuple[PyriteConfig, PyriteDB | None, dict[str, Any]]:
    """Open the index and collect health, falling back to YAML on DB errors.

    The returned database belongs to the caller, which must close it. If
    opening or checking the index fails, the returned config is freshly loaded
    from YAML so partially merged DB-only KBs cannot leak into validation.
    File-system errors while checking KB contents still propagate. A
    ``StorageError`` (a corrupt or truncated index, #922) propagates too: the
    caller is a check command, and a check that cannot be made is a refusal
    (exit 1, ``STORAGE_ERROR``), not a clean verdict from the YAML alone.
    """
    try:
        config, db = get_config_and_db(config)
    except (OSError, sqlite3.Error, SQLAlchemyError) as exc:
        logger.warning(
            "Could not read index database %s while validating KBs; "
            "using YAML config without content-drift checks: %s",
            config.settings.index_path,
            exc,
        )
        return load_config(), None, {}

    try:
        health = IndexManager(db, config).check_health()
    except (sqlite3.Error, SQLAlchemyError) as exc:
        logger.warning(
            "Could not read index database %s while validating KBs; "
            "using YAML config without content-drift checks: %s",
            config.settings.index_path,
            exc,
        )
        db.close()
        return load_config(), None, {}
    except BaseException:
        db.close()
        raise

    return config, db, health


def get_config_with_registered_kbs(
    config: PyriteConfig | None = None, *, name: str
) -> PyriteConfig:
    """Resolve a KB from YAML first, then the database registry if necessary.

    Avoid opening the index database when the requested KB is already in
    config.yaml. If registry lookup fails, keep the YAML config usable and let
    the caller report that the requested KB was not found.
    """
    config = config or load_config()
    if config.get_kb(name) is not None:
        return config

    try:
        db = PyriteDB(config.settings.index_path)
        try:
            db.merge_registered_kbs(config)
        finally:
            db.close()
    except (OSError, sqlite3.Error, SQLAlchemyError, StorageError) as exc:
        logger.warning(
            "Could not read KB registry from index database %s while resolving %r; "
            "using YAML config: %s",
            config.settings.index_path,
            name,
            exc,
        )
    return config


def require_input_file(path: Path | str, what: str = "File") -> Path:
    """The path of a file the caller named as input, or the refusal for it.

    One rule for every CLI input path (#846): a path that does not exist is
    ``EntryNotFoundError`` (``NOT_FOUND``, as each site always said), and a
    path that is a directory is ``ValidationError`` naming it -- before this
    it reached ``read_text`` and left the body as ``IsADirectoryError``,
    which the root reports as ``NO_OUTCOME``. Used by ``create`` and
    ``update`` (``--body-file``), ``add`` and ``links bulk-create``.
    """
    from ..exceptions import EntryNotFoundError, ValidationError

    if isinstance(path, str) and not path.strip():
        raise ValidationError(f"{what}: no path given")
    p = Path(path)
    if not p.exists():
        raise EntryNotFoundError(f"{what} not found: {path}")
    if p.is_dir():
        raise ValidationError(f"{what} is a directory, not a file: {path}")
    if not p.is_file():
        import stat

        mode = p.stat().st_mode
        kind = (
            "FIFO"
            if stat.S_ISFIFO(mode)
            else "device"
            if stat.S_ISCHR(mode) or stat.S_ISBLK(mode)
            else "non-regular file"
        )
        raise ValidationError(f"{what} is a {kind}, not a regular file: {path}")
    return p


def input_file_or_exit(path: Path | str, what: str = "File", output_format: str = "rich") -> Path:
    """:func:`require_input_file` for a command that renders its own errors.

    The legacy commands (not yet on the ADR-0046 outcome contract) catch
    nothing the root would render, so the refusal is printed here in the CLI's
    error shape and the command exits 1 (#923).
    """
    from ..exceptions import PyriteError
    from ..utils.errors import cli_error_from

    try:
        return require_input_file(path, what)
    except PyriteError as exc:
        # The CLI spells a missing path NOT_FOUND (the class says ENTRY_NOT_FOUND).
        code = "NOT_FOUND" if exc.error_code == "ENTRY_NOT_FOUND" else None
        cli_error_from(exc, output_format, error_code=code)
        raise  # unreachable: cli_error_from always exits


def read_input_file(path: Path | str, what: str = "File") -> str:
    """The text of an input file, refused as :func:`require_input_file` says."""
    return require_input_file(path, what).read_text(encoding="utf-8")

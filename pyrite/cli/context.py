"""Shared CLI context — eliminates duplicated PyriteDB + service construction."""

import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from ..config import PyriteConfig, load_config
from ..services.kb_registry_service import KBRegistryService
from ..services.kb_service import KBService
from ..storage.database import PyriteDB
from ..storage.index import IndexManager

logger = logging.getLogger(__name__)


def _init_base() -> tuple[PyriteConfig, PyriteDB, KBService]:
    """Shared construction for all CLI context managers."""
    config, db = get_config_and_db()
    svc = KBService(config, db)
    return config, db, svc


@contextmanager
def cli_context() -> Generator[tuple[PyriteConfig, PyriteDB, KBService], None, None]:
    """Provide config, db, and service for CLI commands."""
    config, db, svc = _init_base()
    try:
        yield config, db, svc
    finally:
        db.close()


@contextmanager
def cli_registry_context() -> Generator[
    tuple[PyriteConfig, PyriteDB, KBService, KBRegistryService], None, None
]:
    """Provide config, db, service, and registry for CLI commands that need KB management."""
    config, db, svc = _init_base()
    index_mgr = IndexManager(db, config)
    registry = KBRegistryService(config, db, index_mgr)
    registry.seed_from_config()
    try:
        yield config, db, svc, registry
    finally:
        db.close()


@contextmanager
def cli_db_context() -> Generator[tuple[PyriteConfig, PyriteDB], None, None]:
    """Provide config and db for commands that only need db access."""
    config, db = get_config_and_db()
    try:
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
    File-system errors while checking KB contents still propagate.
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
    except (OSError, sqlite3.Error, SQLAlchemyError) as exc:
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

    p = Path(path)
    if not p.exists():
        raise EntryNotFoundError(f"{what} not found: {path}")
    if p.is_dir():
        raise ValidationError(f"{what} is a directory, not a file: {path}")
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

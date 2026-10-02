"""
Structured Logging for pyrite

Provides consistent logging across all modules with:
- Configurable log levels
- Structured output (JSON optional)
- Module-specific loggers
"""

import logging
import os
import sys
from typing import Literal

# Log level type
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# Default format
DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Module logger names
LOGGER_NAMES = {
    "root": "pyrite",
    "storage": "pyrite.storage",
    "index": "pyrite.storage.index",
    "database": "pyrite.storage.database",
    "migrations": "pyrite.storage.migrations",
    "repository": "pyrite.storage.repository",
    "services": "pyrite.services",
    "api": "pyrite.server.api",
    "cli": "pyrite.cli",
    "mcp": "pyrite.mcp",
}


def get_logger(name: str = "root") -> logging.Logger:
    """
    Get a logger for a specific module.

    Args:
        name: Logger name (use keys from LOGGER_NAMES or full dotted name)

    Returns:
        Configured logger instance
    """
    logger_name = LOGGER_NAMES.get(name, name)
    return logging.getLogger(logger_name)


def configure_logging(
    level: LogLevel = "INFO",
    format_string: str | None = None,
    date_format: str | None = None,
    stream: object = None,
) -> None:
    """
    Configure logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format_string: Custom format string (default: timestamp [level] name: message)
        date_format: Custom date format (default: YYYY-MM-DD HH:MM:SS)
        stream: Output stream (default: stderr)
    """
    root_logger = logging.getLogger("pyrite")

    # Clear existing handlers
    root_logger.handlers.clear()

    # Set level
    root_logger.setLevel(getattr(logging, level))

    # Create handler
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setLevel(getattr(logging, level))

    # Create formatter
    formatter = logging.Formatter(
        fmt=format_string or DEFAULT_FORMAT,
        datefmt=date_format or DEFAULT_DATE_FORMAT,
    )
    handler.setFormatter(formatter)

    # Add handler
    root_logger.addHandler(handler)

    # Don't propagate to root logger
    root_logger.propagate = False


#: Environment variable that sets the level when no ``-v`` flag is given.
LOG_LEVEL_ENV = "PYRITE_LOG_LEVEL"

_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def split_verbosity(argv: list[str]) -> tuple[int, list[str]]:
    """Count ``-v`` / ``-vv`` / ``--verbose`` in ``argv`` and return the rest.

    The flag is taken out of the argument list before Typer sees it, so it works
    in any position (``pyrite search x -v``, not only ``pyrite -v search x``;
    a Typer root option must precede the subcommand, which is not what people
    type) and identically on all three entry points. Scanning stops at ``--``.
    """
    count = 0
    rest: list[str] = []
    for i, arg in enumerate(argv):
        if arg == "--":
            rest.extend(argv[i:])
            break
        if arg == "--verbose":
            count += 1
        elif len(arg) > 1 and arg[0] == "-" and arg[1] != "-" and set(arg[1:]) == {"v"}:
            count += len(arg) - 1
        else:
            rest.append(arg)
    return count, rest


def logging_epilog(prog: str) -> str:
    """Help text for an entry point whose parser has no `-v` option of its own."""
    return (
        f"Logging: warnings only by default. `-v` (INFO) or `-vv` (DEBUG), in any position, "
        f"shows progress on stderr; {LOG_LEVEL_ENV}=INFO does the same without a flag. "
        f"Use `{prog} ... -- -v` to pass a literal -v."
    )


def configure_entry_point_logging(default: LogLevel = "WARNING") -> None:
    """The one place a command-line entry point decides what reaches the terminal.

    A command's default output is its result (#584): diagnostics are asked for.
    Level, most specific first: ``-v`` (INFO) / ``-vv`` (DEBUG) in ``sys.argv``,
    then ``PYRITE_LOG_LEVEL``, then ``default``. The flag is removed from
    ``sys.argv`` so the command parser never sees it. Output goes to stderr only;
    stdout belongs to the result (and, for stdio MCP, to the protocol).
    """
    verbosity, rest = split_verbosity(sys.argv[1:])
    sys.argv[1:] = rest

    level: LogLevel = default
    bad_env = None
    if verbosity:
        level = "DEBUG" if verbosity > 1 else "INFO"
    elif env := os.environ.get(LOG_LEVEL_ENV, "").strip():
        if env.upper() in _LEVELS:
            level = env.upper()  # type: ignore[assignment]
        else:
            bad_env = env
    configure_logging(level=level)
    if bad_env is not None:
        logging.getLogger("pyrite.cli").warning(
            "ignoring %s=%r: expected one of %s", LOG_LEVEL_ENV, bad_env, ", ".join(_LEVELS)
        )


def configure_quiet() -> None:
    """Configure logging to suppress all but errors."""
    configure_logging(level="ERROR")


def configure_verbose() -> None:
    """Configure logging for verbose output."""
    configure_logging(level="DEBUG")


# Pre-configured loggers for common use
logger = get_logger("root")
storage_logger = get_logger("storage")
index_logger = get_logger("index")
db_logger = get_logger("database")
migration_logger = get_logger("migrations")
api_logger = get_logger("api")
cli_logger = get_logger("cli")

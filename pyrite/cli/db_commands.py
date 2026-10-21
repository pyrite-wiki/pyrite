"""
Database backup and restore commands for pyrite CLI.

Commands: backup, restore
"""

import logging
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from ..config import load_config
from ..services.index_backup import backup_index
from ..utils.errors import cli_error

logger = logging.getLogger(__name__)

db_app = typer.Typer(help="Database backup and restore.")
console = Console()


@db_app.command("backup")
def db_backup(
    output: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Output path for the backup file"),
    ] = None,
):
    """Create a backup of the Pyrite index database."""
    config = load_config()
    db_path = config.settings.index_path

    if not db_path.exists():
        cli_error(
            "Database file does not exist.",
            error_code="NOT_FOUND",
            suggestion="run `pyrite index sync` to build the index",
        )

    if output is None:
        # Beside the database being backed up, not in whatever directory the
        # command happened to be run from. A bare relative filename here put
        # 125 backup files (58 MB) into the repo root, gitignored so nobody
        # noticed (#21). An explicit --output keeps its usual meaning,
        # including relative-to-cwd.
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        output_path = db_path.parent / "backups" / f"pyrite-backup-{timestamp}.db"
    else:
        output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    backup_index(db_path, output_path)

    # soft_wrap: a long path must stay copy-pasteable. Rich's default wrapping
    # breaks it across lines at the console width, so the one thing the
    # operator needs from this output cannot be pasted back into a shell.
    console.print(f"Backup created: {output_path}", soft_wrap=True)


@db_app.command("restore")
def db_restore(
    backup_path: Annotated[str, typer.Argument(help="Path to backup file to restore from")],
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Skip confirmation prompt"),
    ] = False,
):
    """Restore the Pyrite index database from a backup."""
    backup = Path(backup_path)

    if not backup.exists():
        cli_error(
            f"Backup file not found: {backup}",
            error_code="NOT_FOUND",
            suggestion="check the path or list backups with `ls *.db`",
        )

    # Validate it's a real SQLite database
    try:
        conn = sqlite3.connect(str(backup))
        conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        conn.close()
    except sqlite3.DatabaseError:
        cli_error(
            f"Not a valid SQLite database: {backup}",
            error_code="VALIDATION_FAILED",
        )

    if not force:
        console.print(
            "[yellow]Warning:[/yellow] This will overwrite the current database. "
            "Use --force to confirm."
        )
        raise typer.Exit(1)

    config = load_config()
    db_path = config.settings.index_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Copy the backup over the current database
    shutil.copy2(str(backup), str(db_path))

    console.print(f"Restored database from {backup}")

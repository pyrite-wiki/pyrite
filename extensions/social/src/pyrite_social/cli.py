"""Social KB CLI commands."""

from datetime import UTC, datetime

import typer
from rich.console import Console
from rich.table import Table

from pyrite.config import load_config
from pyrite.storage.database import PyriteDB

social_app = typer.Typer(help="Social knowledge base commands")
console = Console()


@social_app.command("vote")
def social_vote(
    entry_id: str = typer.Argument(..., help="Entry ID to vote on"),
    direction: str = typer.Argument("up", help="Vote direction: up or down"),
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB name"),
    user: str = typer.Option("local", "--user", "-u", help="Voter user ID"),
):
    """Cast a vote on a writeup."""
    if direction not in ("up", "down"):
        console.print("[red]Error:[/red] Direction must be 'up' or 'down'")
        raise typer.Exit(1)

    value = 1 if direction == "up" else -1
    config = load_config()
    db = PyriteDB(config.settings.index_path)

    try:
        now = datetime.now(UTC).isoformat()
        # Upsert vote
        db._raw_conn.execute(
            """INSERT INTO social_vote (entry_id, kb_name, user_id, value, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(entry_id, kb_name, user_id)
               DO UPDATE SET value = ?, created_at = ?""",
            (entry_id, kb_name or "", user, value, now, value, now),
        )
        db._raw_conn.commit()
        console.print(f"[green]Voted {direction} on {entry_id}[/green]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    finally:
        db.close()


@social_app.command("reputation")
def social_reputation(
    user_id: str = typer.Argument("local", help="User ID to check"),
):
    """Show a user's reputation."""
    config = load_config()
    db = PyriteDB(config.settings.index_path)

    try:
        # Sum votes received on the user's writeups
        row = db._raw_conn.execute(
            """SELECT COALESCE(SUM(v.value), 0) as total
               FROM social_vote v
               JOIN entry e ON v.entry_id = e.id AND v.kb_name = e.kb_name
               WHERE json_extract(e.metadata, '$.author_id') = ?""",
            (user_id,),
        ).fetchone()

        rep = row["total"] if row else 0

        # Also check reputation_log
        log_row = db._raw_conn.execute(
            "SELECT COALESCE(SUM(delta), 0) as total FROM social_reputation_log WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        log_rep = log_row["total"] if log_row else 0

        total = rep + log_rep
        console.print(f"[bold cyan]{user_id}[/bold cyan]: reputation [bold]{total}[/bold]")
        console.print(f"  From votes: {rep}")
        if log_rep:
            console.print(f"  From adjustments: {log_rep}")
    except Exception as e:
        console.print(f"[yellow]Could not compute reputation: {e}[/yellow]")
        raise typer.Exit(1) from None  # the answer asked for was not produced (#526)
    finally:
        db.close()


@social_app.command("newest")
def social_newest(
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB name"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of results"),
):
    """List most recent writeups."""
    config = load_config()
    db = PyriteDB(config.settings.index_path)

    try:
        query = "SELECT * FROM entry WHERE entry_type = 'writeup'"
        params: list = []
        if kb_name:
            query += " AND kb_name = ?"
            params.append(kb_name)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = db._raw_conn.execute(query, params).fetchall()

        if not rows:
            console.print("[dim]No writeups found.[/dim]")
            return

        table = Table(title="Newest Writeups")
        table.add_column("ID", style="cyan")
        table.add_column("Title")
        table.add_column("Author", style="green")
        table.add_column("Type", style="yellow")
        table.add_column("Created", style="dim")

        for row in rows:
            import json

            meta = {}
            if row["metadata"]:
                try:
                    meta = json.loads(row["metadata"])
                except (json.JSONDecodeError, TypeError):
                    pass
            table.add_row(
                row["id"],
                row["title"],
                meta.get("author_id", ""),
                meta.get("writeup_type", "essay"),
                str(row["created_at"] or "")[:10],
            )

        console.print(table)
    finally:
        db.close()


@social_app.command("top")
def social_top(
    period: str = typer.Option("all", "--period", "-p", help="Time period: week, month, all"),
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB name"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of results"),
):
    """Show highest-voted writeups."""
    config = load_config()
    db = PyriteDB(config.settings.index_path)

    try:
        query = """
            SELECT e.id, e.title, e.kb_name, e.metadata,
                   COALESCE(SUM(v.value), 0) as score
            FROM entry e
            LEFT JOIN social_vote v ON e.id = v.entry_id AND e.kb_name = v.kb_name
            WHERE e.entry_type = 'writeup'
        """
        params: list = []

        if kb_name:
            query += " AND e.kb_name = ?"
            params.append(kb_name)

        if period == "week":
            query += " AND v.created_at >= datetime('now', '-7 days')"
        elif period == "month":
            query += " AND v.created_at >= datetime('now', '-30 days')"

        query += " GROUP BY e.id, e.kb_name ORDER BY score DESC LIMIT ?"
        params.append(limit)

        rows = db._raw_conn.execute(query, params).fetchall()

        if not rows:
            console.print("[dim]No writeups found.[/dim]")
            return

        table = Table(title=f"Top Writeups ({period})")
        table.add_column("Score", justify="right", style="bold")
        table.add_column("ID", style="cyan")
        table.add_column("Title")
        table.add_column("Author", style="green")

        for row in rows:
            import json

            meta = {}
            if row["metadata"]:
                try:
                    meta = json.loads(row["metadata"])
                except (json.JSONDecodeError, TypeError):
                    pass
            table.add_row(
                str(row["score"]),
                row["id"],
                row["title"],
                meta.get("author_id", ""),
            )

        console.print(table)
    finally:
        db.close()

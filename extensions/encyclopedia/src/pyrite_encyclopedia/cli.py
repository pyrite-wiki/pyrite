"""Encyclopedia CLI commands."""

from datetime import UTC, datetime

import typer
from rich.console import Console
from rich.table import Table

from pyrite.cli.context import require_entry_kb, require_kb
from pyrite.config import load_config
from pyrite.storage.database import PyriteDB

wiki_app = typer.Typer(help="Encyclopedia (wiki) commands")
console = Console()


@wiki_app.command("review")
def wiki_review(
    entry_id: str = typer.Argument(..., help="Article entry ID"),
    action: str = typer.Argument(..., help="Action: approve, reject, or comment"),
    comment: str = typer.Option("", "--comment", "-c", help="Review comment"),
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB name"),
    user: str = typer.Option("local", "--user", "-u", help="Reviewer ID"),
):
    """Submit a review for an article."""
    if action not in ("approve", "reject", "comment"):
        console.print("[red]Error:[/red] Action must be 'approve', 'reject', or 'comment'")
        raise typer.Exit(1)

    config = load_config()

    require_kb(config, kb_name, output_format="rich")
    # The review row names the entry's KB, never '' (#911).
    kb_name = require_entry_kb(entry_id, kb_name, entry_type="article", output_format="rich")
    db = PyriteDB(config.settings.index_path)

    try:
        now = datetime.now(UTC).isoformat()
        db._raw_conn.execute(
            """INSERT INTO encyclopedia_review
               (entry_id, kb_name, reviewer_id, status, comments, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entry_id, kb_name, user, action, comment, now),
        )
        db._raw_conn.commit()

        if action == "approve":
            console.print(f"[green]Approved {entry_id}[/green]")
            console.print("[dim]Update review_status to 'published' to complete.[/dim]")
        elif action == "reject":
            console.print(f"[yellow]Rejected {entry_id}[/yellow]")
            console.print("[dim]Update review_status to 'draft' to send back.[/dim]")
        else:
            console.print(f"[blue]Comment added to {entry_id}[/blue]")
    finally:
        db.close()


@wiki_app.command("quality")
def wiki_quality(
    entry_id: str = typer.Argument(..., help="Article entry ID"),
    new_quality: str | None = typer.Argument(None, help="New quality level"),
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB name"),
):
    """Assess or set article quality level."""
    from .entry_types import QUALITY_LEVELS

    config = load_config()

    require_kb(config, kb_name, output_format="rich")
    db = PyriteDB(config.settings.index_path)

    try:
        if new_quality:
            if new_quality not in QUALITY_LEVELS:
                console.print(
                    f"[red]Error:[/red] Quality must be one of: {', '.join(QUALITY_LEVELS)}"
                )
                raise typer.Exit(1)
            console.print(f"[green]Quality for {entry_id} set to {new_quality}[/green]")
            console.print("[dim]Update the article's frontmatter to apply.[/dim]")
        else:
            # Show current quality from index, in the one KB that holds the
            # id (#911: a loop over config.yaml picked the first KB and
            # skipped registry-only ones).
            home = require_entry_kb(entry_id, kb_name, entry_type="article", output_format="rich")
            entry = db.get_entry(entry_id, home)

            if entry:
                import json

                meta = {}
                if entry.get("metadata"):
                    try:
                        meta = (
                            json.loads(entry["metadata"])
                            if isinstance(entry["metadata"], str)
                            else entry["metadata"]
                        )
                    except (json.JSONDecodeError, TypeError):
                        pass
                quality = meta.get("quality", entry.get("quality", "stub"))
                review = meta.get("review_status", entry.get("review_status", "draft"))
                console.print(f"[bold cyan]{entry.get('title', entry_id)}[/bold cyan]")
                console.print(f"  Quality: [yellow]{quality}[/yellow]")
                console.print(f"  Review status: {review}")
            else:
                console.print(f"[red]Error:[/red] Entry '{entry_id}' not found")
                raise typer.Exit(1)
    finally:
        db.close()


@wiki_app.command("stats")
def wiki_stats(
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB name"),
):
    """Show quality distribution and review queue length."""
    config = load_config()
    require_kb(config, kb_name, output_format="rich")
    db = PyriteDB(config.settings.index_path)

    try:
        import json

        # Get all articles
        query = "SELECT * FROM entry WHERE entry_type = 'article'"
        params: list = []
        if kb_name:
            query += " AND kb_name = ?"
            params.append(kb_name)

        rows = db._raw_conn.execute(query, params).fetchall()

        quality_counts: dict[str, int] = {}
        review_counts: dict[str, int] = {}

        for row in rows:
            meta = {}
            if row["metadata"]:
                try:
                    meta = json.loads(row["metadata"])
                except (json.JSONDecodeError, TypeError):
                    pass
            q = meta.get("quality", "stub")
            r = meta.get("review_status", "draft")
            quality_counts[q] = quality_counts.get(q, 0) + 1
            review_counts[r] = review_counts.get(r, 0) + 1

        total = len(rows)
        if total == 0:
            console.print("[dim]No articles found.[/dim]")
            return

        # Quality distribution
        qt = Table(title="Quality Distribution")
        qt.add_column("Quality", style="cyan")
        qt.add_column("Count", justify="right")
        qt.add_column("Percentage", justify="right")

        from .entry_types import QUALITY_LEVELS

        for level in QUALITY_LEVELS:
            count = quality_counts.get(level, 0)
            pct = (count / total * 100) if total > 0 else 0
            qt.add_row(level, str(count), f"{pct:.0f}%")
        console.print(qt)

        # Review queue
        queue_len = review_counts.get("under_review", 0)
        console.print(f"\n[bold]Review queue:[/bold] {queue_len} article(s)")
        console.print(f"[bold]Total articles:[/bold] {total}")
    finally:
        db.close()


@wiki_app.command("stubs")
def wiki_stubs(
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB name"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of results"),
):
    """List stub articles needing expansion."""
    config = load_config()
    require_kb(config, kb_name, output_format="rich")
    db = PyriteDB(config.settings.index_path)

    try:
        import json

        query = "SELECT * FROM entry WHERE entry_type = 'article'"
        params: list = []
        if kb_name:
            query += " AND kb_name = ?"
            params.append(kb_name)
        query += " ORDER BY created_at ASC"

        rows = db._raw_conn.execute(query, params).fetchall()

        stubs = []
        for row in rows:
            meta = {}
            if row["metadata"]:
                try:
                    meta = json.loads(row["metadata"])
                except (json.JSONDecodeError, TypeError):
                    pass
            if meta.get("quality", "stub") == "stub":
                stubs.append(row)
            if len(stubs) >= limit:
                break

        if not stubs:
            console.print("[green]No stubs found — all articles have been expanded.[/green]")
            return

        table = Table(title="Stub Articles")
        table.add_column("ID", style="cyan")
        table.add_column("Title")
        table.add_column("KB", style="dim")
        table.add_column("Created", style="dim")

        for row in stubs:
            table.add_row(
                row["id"],
                row["title"],
                row["kb_name"],
                str(row["created_at"] or "")[:10],
            )

        console.print(table)
        console.print(f"\n[dim]{len(stubs)} stub(s) found[/dim]")
    finally:
        db.close()

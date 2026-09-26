"""
pyrite-read: Read-only CLI for pyrite

Safe for AI agents and automated workflows. Exposes only read operations:
get, list, search, tags, backlinks, timeline, config.

For write operations: pyrite
For admin operations: pyrite-admin
"""

import typer
from rich.console import Console
from rich.table import Table

from .cli.context import open_index_db
from .cli.search_commands import register_search_command
from .config import CONFIG_FILE, load_config
from .exceptions import PyriteError
from .services.access_policy import UNSCOPED
from .services.kb_service import KBService

app = typer.Typer(
    name="pyrite-read",
    help="Pyrite read-only CLI — search, browse, retrieve (safe for AI agents)",
    no_args_is_help=True,
)
console = Console()
# Errors/status print here so machine formats keep stdout clean and parseable.
err_console = Console(stderr=True)


def _emit_error(message: str, output_format: str, *, error_code: str = "ERROR") -> None:
    """Report a CLI error without polluting stdout in machine formats.

    In a non-rich format, write a valid JSON error object to stdout so a
    programmatic caller's ``json.load()`` still parses; otherwise print a
    human-readable error to stderr. Either way stdout stays parseable.
    """
    if output_format and output_format != "rich":
        import json

        typer.echo(json.dumps({"error": message, "error_code": error_code}))
    else:
        err_console.print(f"[red]Error:[/red] {message}")


def _get_svc():
    """Create a KBService instance for CLI commands."""
    config = load_config()
    db = open_index_db(config)
    return KBService(config, db), db


def _format_output(data: dict, fmt: str) -> str | None:
    """Format data using the format registry. Returns None for default (rich) output."""
    if fmt == "rich":
        return None
    from .formats import format_response

    content, _ = format_response(data, fmt)
    return content


# =============================================================================
# Get command
# =============================================================================


@app.command("get")
def get_entry(
    entry_id: str = typer.Argument(..., help="Entry ID"),
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB to search in"),
    output_format: str = typer.Option(
        "rich", "--format", help="Output format: rich, json, markdown, csv, yaml"
    ),
):
    """Get a specific entry by ID."""
    svc, db = _get_svc()
    try:
        result = svc.get_entry(entry_id, kb_name=kb_name, readable_kbs=UNSCOPED)

        if not result:
            _emit_error(f"Entry '{entry_id}' not found", output_format, error_code="NOT_FOUND")
            raise typer.Exit(1)

        formatted = _format_output(result, output_format)
        if formatted is not None:
            typer.echo(formatted)
            return

        console.print(f"\n[bold cyan]{result.get('title', '')}[/bold cyan]")
        console.print(
            f"[dim]KB: {result.get('kb_name', '')} | Type: {result.get('entry_type', '')} | ID: {result.get('id', '')}[/dim]\n"
        )

        summary = result.get("summary")
        if summary:
            console.print(f"[italic]{summary}[/italic]\n")

        body = result.get("body", "")
        if body:
            console.print(body)

        sources = result.get("sources", [])
        if sources:
            console.print(f"\n[bold]Sources ({len(sources)}):[/bold]")
            for src in sources:
                if isinstance(src, dict):
                    console.print(f"  • {src.get('title', '')}: {src.get('url', '')}")
                else:
                    console.print(f"  • {src.title}: {src.url}")
    except (PyriteError, ValueError) as e:
        _emit_error(str(e), output_format)
        raise typer.Exit(1) from None
    finally:
        db.close()


# =============================================================================
# List command
# =============================================================================


@app.command("list")
def list_kbs(
    output_format: str = typer.Option(
        "rich", "--format", help="Output format: rich, json, markdown, csv, yaml"
    ),
):
    """List all knowledge bases."""
    svc, db = _get_svc()
    try:
        kbs = svc.list_kbs()

        if not kbs:
            console.print("[yellow]No knowledge bases configured.[/yellow]")
            return

        formatted = _format_output({"kbs": kbs, "total": len(kbs)}, output_format)
        if formatted is not None:
            typer.echo(formatted)
            return

        table = Table(title="Knowledge Bases")
        table.add_column("Name", style="cyan")
        table.add_column("Type", style="dim")
        table.add_column("Entries", justify="right")
        table.add_column("Path", style="dim")

        for kb in kbs:
            table.add_row(
                kb["name"], kb.get("type", ""), str(kb.get("entries", 0)), kb.get("path", "")
            )

        console.print(table)
    except (PyriteError, ValueError) as e:
        _emit_error(str(e), output_format)
        raise typer.Exit(1) from None
    finally:
        db.close()


# =============================================================================
# Search command
# =============================================================================

register_search_command(app)


# =============================================================================
# Timeline command
# =============================================================================


@app.command("timeline")
def timeline(
    date_from: str = typer.Option(None, "--from", help="Start date (YYYY-MM-DD)"),
    date_to: str = typer.Option(None, "--to", help="End date (YYYY-MM-DD)"),
    min_importance: int = typer.Option(1, "--min-importance", help="Minimum importance (1-10)"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max results"),
    output_format: str = typer.Option(
        "rich", "--format", help="Output format: rich, json, markdown, csv, yaml"
    ),
):
    """Query timeline events."""
    svc, db = _get_svc()
    try:
        results = svc.get_timeline(
            date_from=date_from,
            date_to=date_to,
            min_importance=min_importance,
        )
        results = results[:limit]

        if not results:
            console.print("[yellow]No timeline events found.[/yellow]")
            return

        formatted = _format_output({"count": len(results), "events": results}, output_format)
        if formatted is not None:
            typer.echo(formatted)
            return

        table = Table(title="Timeline Events")
        table.add_column("Date", style="cyan")
        table.add_column("Title")
        table.add_column("Imp", justify="right", style="dim")
        table.add_column("KB", style="dim")

        for evt in results:
            table.add_row(
                evt.get("date", ""),
                evt.get("title", ""),
                str(evt.get("importance", "")),
                evt.get("kb_name", ""),
            )

        console.print(table)
    except (PyriteError, ValueError) as e:
        _emit_error(str(e), output_format)
        raise typer.Exit(1) from None
    finally:
        db.close()


# =============================================================================
# Tags command
# =============================================================================


@app.command("tags")
def tags_cmd(
    kb_name: str = typer.Option(None, "--kb", "-k", help="Filter by KB"),
    prefix: str = typer.Option(None, "--prefix", "-p", help="Filter tags by prefix"),
    limit: int = typer.Option(100, "--limit", "-n", help="Max tags to show"),
    output_format: str = typer.Option(
        "rich", "--format", help="Output format: rich, json, markdown, csv, yaml"
    ),
):
    """List tags with counts."""
    svc, db = _get_svc()
    try:
        tag_list = svc.get_tags(kb_name=kb_name, limit=limit)

        if prefix:
            tag_list = [t for t in tag_list if t.get("name", "").startswith(prefix)]

        if not tag_list:
            console.print("[yellow]No tags found.[/yellow]")
            return

        formatted = _format_output({"tags": tag_list, "count": len(tag_list)}, output_format)
        if formatted is not None:
            typer.echo(formatted)
            return

        title = f"Tags (prefix: {prefix})" if prefix else "Tags"
        table = Table(title=title)
        table.add_column("Tag", style="cyan")
        table.add_column("Count", justify="right")

        for tag in tag_list:
            table.add_row(tag.get("name", ""), str(tag.get("count", 0)))

        console.print(table)
    except (PyriteError, ValueError) as e:
        _emit_error(str(e), output_format)
        raise typer.Exit(1) from None
    finally:
        db.close()


# =============================================================================
# Backlinks command
# =============================================================================


@app.command("backlinks")
def backlinks_cmd(
    entry_id: str = typer.Argument(..., help="Entry ID to find backlinks for"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
    output_format: str = typer.Option(
        "rich", "--format", help="Output format: rich, json, markdown, csv, yaml"
    ),
):
    """Find entries that link to a given entry."""
    svc, db = _get_svc()
    try:
        from .services.graph_service import GraphService

        graph_svc = GraphService(db)
        links = graph_svc.get_backlinks(entry_id, kb_name, readable_kbs=UNSCOPED)

        if not links:
            console.print(f"[yellow]No backlinks found for '{entry_id}'.[/yellow]")
            return

        formatted = _format_output(
            {"entry_id": entry_id, "entries": links, "total": len(links)}, output_format
        )
        if formatted is not None:
            typer.echo(formatted)
            return

        table = Table(title=f"Backlinks to {entry_id}")
        table.add_column("ID", style="cyan")
        table.add_column("Title")
        table.add_column("Type", style="dim")
        table.add_column("Relation", style="dim")

        for link in links:
            table.add_row(
                link.get("id", ""),
                link.get("title", ""),
                link.get("entry_type", ""),
                link.get("relation", ""),
            )

        console.print(table)
    except (PyriteError, ValueError) as e:
        _emit_error(str(e), output_format)
        raise typer.Exit(1) from None
    finally:
        db.close()


# =============================================================================
# Config command (read-only view)
# =============================================================================


@app.command("config")
def show_config():
    """Show current configuration (read-only)."""
    console.print(f"[bold]Config file:[/bold] {CONFIG_FILE}")
    console.print(f"[bold]Exists:[/bold] {CONFIG_FILE.exists()}")

    if CONFIG_FILE.exists():
        config = load_config()
        console.print(f"\n[bold]Knowledge Bases:[/bold] {len(config.knowledge_bases)}")
        console.print(f"[bold]Subscriptions:[/bold] {len(config.subscriptions)}")
        console.print(f"[bold]AI Provider:[/bold] {config.settings.ai_provider}")


def main():
    app()


if __name__ == "__main__":
    main()

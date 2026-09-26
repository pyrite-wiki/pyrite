"""CLI commands for collections — list and query."""

import typer
from rich.console import Console
from rich.table import Table

from ..services.access_policy import UNSCOPED
from .context import cli_context

collections_app = typer.Typer(help="Collection management")
console = Console()


@collections_app.command("list")
def list_collections(
    kb: str | None = typer.Option(None, "--kb", "-k", help="Filter by KB name"),
    output_format: str = typer.Option(
        "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """List all collections with entry counts."""
    import json as json_mod

    with cli_context() as (config, db, svc):
        results = svc.list_collections(kb_name=kb)

        if not results:
            console.print("[yellow]No collections found.[/yellow]")
            return

        if output_format != "rich":
            from ..formats import format_response

            content, _ = format_response(
                {"collections": results, "total": len(results)}, output_format
            )
            typer.echo(content)
            return

        table = Table(title="Collections")
        table.add_column("ID", style="cyan")
        table.add_column("Title")
        table.add_column("Type", style="dim")
        table.add_column("KB", style="dim")

        for r in results:
            meta = r.get("metadata", {})
            if isinstance(meta, str):
                try:
                    meta = json_mod.loads(meta)
                except (json_mod.JSONDecodeError, TypeError):
                    meta = {}
            source_type = meta.get("source_type", "folder") if isinstance(meta, dict) else "folder"
            table.add_row(
                r.get("id", ""),
                r.get("title", ""),
                source_type,
                r.get("kb_name", ""),
            )

        console.print(table)


@collections_app.command("query")
def query_entries(
    query_str: str = typer.Argument(
        ..., help="Query string, e.g. 'type:backlog_item status:proposed'"
    ),
    kb: str | None = typer.Option(None, "--kb", "-k", help="Filter by KB name"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max results"),
    output_format: str = typer.Option(
        "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """Run an ad-hoc collection query."""
    from ..services.collection_query import (
        evaluate_query,
        parse_query,
        validate_query,
    )

    with cli_context() as (config, db, svc):
        query = parse_query(query_str)

        # Override kb and limit from CLI flags
        if kb:
            query.kb_name = kb
        if limit:
            query.limit = limit

        errors = validate_query(query)
        if errors:
            from ..utils.errors import cli_error

            cli_error(
                "; ".join(str(e) for e in errors),
                output_format,
                error_code="VALIDATION_FAILED",
            )

        entries, total = evaluate_query(query, db, readable_kbs=UNSCOPED)

        if not entries:
            console.print("[yellow]No entries matched the query.[/yellow]")
            return

        if output_format != "rich":
            from ..formats import format_response

            content, _ = format_response(
                {"entries": entries, "total": total, "query": query_str}, output_format
            )
            typer.echo(content)
            return

        table = Table(title=f"Query Results ({total} total)")
        table.add_column("ID", style="cyan")
        table.add_column("Title")
        table.add_column("Type", style="dim")
        table.add_column("KB", style="dim")
        table.add_column("Tags", style="dim")

        for entry in entries:
            tags = entry.get("tags", [])
            tag_str = ", ".join(tags) if isinstance(tags, list) else str(tags)
            table.add_row(
                entry.get("id", ""),
                entry.get("title", ""),
                entry.get("entry_type", ""),
                entry.get("kb_name", ""),
                tag_str,
            )

        console.print(table)

"""
Read-only browsing and discovery commands for pyrite CLI.

Commands: list-entries, batch-read, orient, recent, timeline, tags, backlinks
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from ..exceptions import PyriteError
from ..services.access_policy import UNSCOPED
from ..services.read_shaping import parse_fields_param, project_fields
from .context import cli_context

console = Console()


def _format_output(data: dict, fmt: str) -> str | None:
    from .output import format_output

    return format_output(data, fmt)


def _cli_error(message: str, output_format: str = "rich", error_code: str | None = None) -> None:
    """Print an error and exit. Thin wrapper over the shared cli_error helper
    (kept for the existing call sites' 3-arg signature)."""
    from ..utils.errors import cli_error

    cli_error(message, output_format, error_code=error_code or "ERROR")


def register_browse_commands(app: typer.Typer) -> None:
    """Register read-only browsing/discovery commands on the main app."""

    @app.command("list-entries")
    def list_entries_cmd(
        kb_name: str | None = typer.Option(None, "--kb", "-k", help="Filter by KB"),
        entry_type: str | None = typer.Option(None, "--type", "-t", help="Filter by entry type"),
        tag: str | None = typer.Option(None, "--tag", help="Filter by tag"),
        sort_by: str = typer.Option(
            "updated_at",
            "--sort-by",
            help="Sort column: title, updated_at, created_at, entry_type",
        ),
        sort_order: str = typer.Option("desc", "--sort-order", help="Sort direction: asc, desc"),
        limit: int = typer.Option(50, "--limit", "-n", help="Max entries (max 200)"),
        offset: int = typer.Option(0, "--offset", help="Pagination offset"),
        fields: str = typer.Option(None, "--fields", help="Comma-separated fields to return"),
        output_format: str = typer.Option(
            "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
        ),
    ):
        """Browse entries with filters and pagination."""
        limit = min(limit, 200)
        with cli_context() as (config, db, svc):
            entries = svc.list_entries(
                kb_name=kb_name,
                entry_type=entry_type,
                tag=tag,
                sort_by=sort_by,
                sort_order=sort_order,
                limit=limit,
                offset=offset,
            )
            total = svc.count_entries(kb_name=kb_name, entry_type=entry_type, tag=tag)

            # Apply field projection. One rule for every read surface (#193).
            fields_list = parse_fields_param(fields)
            if fields_list:
                entries = [project_fields(record, fields_list) for record in entries]

            resp_data = {
                "entries": entries,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            }

            formatted = _format_output(resp_data, output_format)
            if formatted is not None:
                typer.echo(formatted)
                return

            if not entries:
                console.print("[yellow]No entries found.[/yellow]")
                return

            table = Table(title=f"Entries ({total} total)")
            table.add_column("KB", style="cyan", width=12)
            table.add_column("Type", style="green", width=12)
            table.add_column("Title", width=40)
            table.add_column("Updated", width=20, style="dim")

            for e in entries:
                table.add_row(
                    e.get("kb_name", ""),
                    e.get("entry_type", ""),
                    (e.get("title", "") or "")[:40],
                    (e.get("updated_at", "") or "")[:19],
                )

            console.print(table)

    @app.command("batch-read")
    def batch_read(
        ids: list[str] = typer.Argument(
            ..., help="Entry IDs as kb_name:entry_id (e.g. mydb:alice-smith)"
        ),
        fields: str = typer.Option(None, "--fields", help="Comma-separated fields to return"),
        output_format: str = typer.Option(
            "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
        ),
    ):
        """Fetch multiple entries in one call."""
        parsed_ids: list[tuple[str, str]] = []
        for spec in ids:
            if ":" not in spec:
                _cli_error(
                    f"Invalid format '{spec}' — use kb_name:entry_id",
                    output_format,
                    "VALIDATION_FAILED",
                )
            kb, eid = spec.split(":", 1)
            parsed_ids.append((eid.strip(), kb.strip()))

        with cli_context() as (config, db, svc):
            results = svc.get_entries(parsed_ids)

            # Apply field projection. One rule for every read surface (#193).
            fields_list = parse_fields_param(fields)
            if fields_list:
                results = [project_fields(record, fields_list) for record in results]

            found_ids = {(r.get("id"), r.get("kb_name")) for r in results}
            not_found = [
                {"entry_id": eid, "kb_name": kb}
                for eid, kb in parsed_ids
                if (eid, kb) not in found_ids
            ]

            resp_data = {
                "entries": results,
                "found": len(results),
                "not_found": not_found,
            }

            formatted = _format_output(resp_data, output_format)
            if formatted is not None:
                typer.echo(formatted)
                return

            if not results:
                console.print("[yellow]No entries found.[/yellow]")
                return

            for r in results:
                console.print(f"\n[bold cyan]{r.get('title', '')}[/bold cyan]")
                console.print(f"[dim]KB: {r.get('kb_name', '')} | ID: {r.get('id', '')}[/dim]")

            if not_found:
                console.print(f"\n[yellow]Not found: {len(not_found)} entries[/yellow]")
                for nf in not_found:
                    console.print(f"  [dim]{nf['kb_name']}:{nf['entry_id']}[/dim]")

    @app.command("orient")
    def orient_kb(
        kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base to orient in"),
        recent: int = typer.Option(5, "--recent", "-r", help="Number of recent entries to include"),
        output_format: str = typer.Option(
            "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
        ),
    ):
        """One-shot KB orientation summary — types, tags, recent changes, and schema."""
        with cli_context() as (config, db, svc):
            try:
                result = svc.orient(kb_name, recent_limit=recent)
            except PyriteError as e:
                _cli_error(str(e), output_format, "KB_NOT_FOUND")

            formatted = _format_output(result, output_format)
            if formatted is not None:
                typer.echo(formatted)
                return

            console.print(
                f"\n[bold cyan]{result['kb']}[/bold cyan]  [dim]{result.get('description', '')}[/dim]"
            )
            console.print(
                f"Type: {result.get('kb_type', 'default')}  |  Entries: {result['total_entries']}  |  Read-only: {result.get('read_only', False)}\n"
            )

            # Types table
            types = result.get("types", [])
            if types:
                type_table = Table(title="Entry Types")
                type_table.add_column("Type", style="green")
                type_table.add_column("Count", justify="right")
                for t in types:
                    type_table.add_row(t["type"], str(t["count"]))
                console.print(type_table)

            # Top tags
            top_tags = result.get("top_tags", [])
            if top_tags:
                tag_strs = [f"{t.get('name', '')} ({t.get('count', 0)})" for t in top_tags[:10]]
                console.print(f"\n[bold]Top Tags:[/bold] {', '.join(tag_strs)}")

            # Recent
            recent_entries = result.get("recent", [])
            if recent_entries:
                console.print("\n[bold]Recent Changes:[/bold]")
                for e in recent_entries:
                    console.print(
                        f"  {e.get('updated_at', '')[:19]}  [{e.get('entry_type', '')}]  {e.get('title', '')}"
                    )

            # Software KB supplement
            sw = result.get("software")
            if sw:
                # Board summary line
                board_parts = []
                for lane in sw.get("board_summary", []):
                    part = f"{lane['count']} {lane['name']}"
                    wip = lane.get("wip_limit")
                    if wip is not None:
                        part += f" (limit {wip})"
                        if lane.get("over_limit"):
                            part = f"[red]{part}[/red]"
                    board_parts.append(part)
                if board_parts:
                    console.print(f"\n[bold]Board:[/bold] {' | '.join(board_parts)}")

                # In-progress table
                ip_items = sw.get("in_progress", [])
                if ip_items:
                    ip_table = Table(title="In Progress")
                    ip_table.add_column("ID", style="cyan", width=30)
                    ip_table.add_column("Title", width=40)
                    ip_table.add_column("Priority", width=10)
                    for item in ip_items:
                        ip_table.add_row(item["id"], item["title"], item.get("priority", ""))
                    console.print(ip_table)

                # Review queue table
                rv_items = sw.get("review_queue", [])
                if rv_items:
                    rv_table = Table(title="Review Queue")
                    rv_table.add_column("ID", style="cyan", width=30)
                    rv_table.add_column("Title", width=40)
                    rv_table.add_column("Priority", width=10)
                    for item in rv_items:
                        rv_table.add_row(item["id"], item["title"], item.get("priority", ""))
                    console.print(rv_table)

                # Recent ADRs
                adrs = sw.get("recent_adrs", [])
                if adrs:
                    adr_table = Table(title="Recent ADRs")
                    adr_table.add_column("ID", style="cyan", width=30)
                    adr_table.add_column("Title", width=50)
                    for adr in adrs:
                        adr_table.add_row(adr["id"], adr["title"])
                    console.print(adr_table)

                # Recommended next
                rec = sw.get("recommended_next")
                if rec:
                    console.print(
                        f"\n[bold green]Recommended Next:[/bold green] {rec['title']} "
                        f"[dim]({rec['id']})[/dim] — priority: {rec.get('priority', 'medium')}"
                    )

    @app.command("recent")
    def recent_entries(
        kb_name: str | None = typer.Option(None, "--kb", "-k", help="Filter by KB"),
        entry_type: str | None = typer.Option(None, "--type", "-t", help="Filter by entry type"),
        limit: int = typer.Option(20, "--limit", "-n", help="Max entries"),
        since: str = typer.Option(
            None, "--since", help="Only entries updated after this ISO datetime"
        ),
        fields: str = typer.Option(None, "--fields", help="Comma-separated fields to return"),
        output_format: str = typer.Option(
            "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
        ),
    ):
        """Show recently changed entries."""
        with cli_context() as (config, db, svc):
            entries = svc.list_entries(
                kb_name=kb_name,
                entry_type=entry_type,
                sort_by="updated_at",
                sort_order="desc",
                limit=min(limit, 200),
            )

            if since:
                entries = [e for e in entries if (e.get("updated_at") or "") >= since]

            # Apply field projection. One rule for every read surface (#193).
            fields_list = parse_fields_param(fields)
            if fields_list:
                entries = [project_fields(record, fields_list) for record in entries]

            resp_data = {"entries": entries, "count": len(entries)}

            formatted = _format_output(resp_data, output_format)
            if formatted is not None:
                typer.echo(formatted)
                return

            if not entries:
                console.print("[yellow]No recent entries found.[/yellow]")
                return

            table = Table(title=f"Recent Entries ({len(entries)})")
            table.add_column("KB", style="cyan", width=12)
            table.add_column("Type", style="green", width=12)
            table.add_column("Title", width=40)
            table.add_column("Updated", width=20, style="dim")

            for e in entries:
                table.add_row(
                    e.get("kb_name", ""),
                    e.get("entry_type", ""),
                    (e.get("title", "") or "")[:40],
                    (e.get("updated_at", "") or "")[:19],
                )

            console.print(table)

    @app.command("timeline")
    def timeline(
        kb_name: str = typer.Option(None, "--kb", "-k", help="Filter to a specific knowledge base"),
        date_from: str = typer.Option(None, "--from", help="Start date (YYYY-MM-DD)"),
        date_to: str = typer.Option(None, "--to", help="End date (YYYY-MM-DD)"),
        min_importance: int = typer.Option(1, "--min-importance", help="Minimum importance (1-10)"),
        limit: int = typer.Option(50, "--limit", "-n", help="Max results"),
        sort: str = typer.Option(
            "desc", "--sort", "-s", help="Sort order: asc or desc (default: desc)"
        ),
        output_format: str = typer.Option(
            "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
        ),
    ):
        """Query timeline events."""
        with cli_context() as (config, db, svc):
            results = svc.get_timeline(
                date_from=date_from,
                date_to=date_to,
                min_importance=min_importance,
                kb_name=kb_name,
                limit=limit,
                sort_order=sort,
            )

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

    @app.command("tags")
    def tags_cmd(
        kb_name: str = typer.Option(None, "--kb", "-k", help="Filter by KB"),
        prefix: str = typer.Option(None, "--prefix", "-p", help="Filter tags by prefix"),
        limit: int = typer.Option(100, "--limit", "-n", help="Max tags to show"),
        output_format: str = typer.Option(
            "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
        ),
    ):
        """List tags with counts."""
        with cli_context() as (config, db, svc):
            tag_list = svc.get_tags(kb_name=kb_name, limit=limit)

            # Apply prefix filter
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

    @app.command("backlinks")
    def backlinks_cmd(
        entry_id: str = typer.Argument(..., help="Entry ID to find backlinks for"),
        kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
        output_format: str = typer.Option(
            "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
        ),
    ):
        """Find entries that link to a given entry."""
        with cli_context() as (config, db, svc):
            from ..services.graph_service import GraphService

            graph_svc = GraphService(db)
            links = graph_svc.get_backlinks(entry_id, kb_name, readable_kbs=UNSCOPED)

            if not links:
                console.print(f"[yellow]No backlinks found for '{entry_id}'.[/yellow]")
                return

            formatted = _format_output(
                {"entry_id": entry_id, "entries": links, "total": len(links)},
                output_format,
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

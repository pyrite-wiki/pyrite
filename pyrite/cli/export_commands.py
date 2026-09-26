"""Export commands — export KB entries to external formats."""

import logging
from pathlib import Path

import typer
from rich.console import Console

from ..cli.context import cli_context
from ..services.access_policy import UNSCOPED

logger = logging.getLogger(__name__)
console = Console()

export_app = typer.Typer(help="Export KB entries to external formats")


@export_app.command("collection")
def export_collection(
    collection_id: str = typer.Argument(
        ..., help="Collection entry ID, or a query string (prefix with 'q:' for query mode)"
    ),
    kb: str = typer.Option(None, "-k", "--kb", help="Knowledge base name"),
    output: Path = typer.Option(Path("./export"), "-o", "--output", help="Output directory"),
    bundle: str = typer.Option(
        "auto", "--bundle", help="Bundle strategy: auto, none, by-type, single"
    ),
    sources: str = typer.Option("public", "--sources", help="Source mode: public, full, redact"),
    depth: int = typer.Option(
        0, "--depth", help="Link-following depth (0=no following, 1=direct links, etc.)"
    ),
    title: str = typer.Option(None, "--title", help="Title for the manifest document"),
    format: str = typer.Option(
        "notebooklm", "--format", "-f", help="Export format (currently: notebooklm)"
    ),
):
    """Export a collection of KB entries for NotebookLM.

    Examples:

        # Export a named collection
        pyrite export collection my-story-research -k investigations -o ./export

        # Export entries matching a query
        pyrite export collection "q:type:adr status:accepted" -k pyrite

        # Export with source redaction for public sharing
        pyrite export collection my-collection --sources redact

        # Bundle into fewer files for large collections
        pyrite export collection my-collection --bundle by-type
    """
    from ..models.base import Entry
    from ..renderers.notebooklm import BundleStrategy, SourceMode
    from ..services.export_service import ExportService

    # Parse bundle strategy
    bundle_map = {
        "auto": BundleStrategy.AUTO,
        "none": BundleStrategy.NONE,
        "by-type": BundleStrategy.BY_TYPE,
        "single": BundleStrategy.SINGLE,
    }
    bundle_strategy = bundle_map.get(bundle)
    if bundle_strategy is None:
        console.print(f"[red]Unknown bundle strategy: {bundle}[/red]")
        console.print(f"Valid options: {', '.join(bundle_map.keys())}")
        raise typer.Exit(1)

    # Parse source mode
    source_map = {
        "public": SourceMode.PUBLIC,
        "full": SourceMode.FULL,
        "redact": SourceMode.REDACT,
    }
    source_mode = source_map.get(sources)
    if source_mode is None:
        console.print(f"[red]Unknown source mode: {sources}[/red]")
        console.print(f"Valid options: {', '.join(source_map.keys())}")
        raise typer.Exit(1)

    with cli_context() as (config, db, svc):
        entries: list[Entry] = []

        if collection_id.startswith("q:"):
            # Query mode — resolve entries via collection query DSL
            query_str = collection_id[2:]
            from ..services.collection_query import evaluate_query, parse_query

            query = parse_query(query_str)
            if kb:
                query.kb_name = kb
            results, total = evaluate_query(query, db, readable_kbs=UNSCOPED)
            console.print(f"Query matched {total} entries")

            # Load full Entry objects from disk
            for result in results:
                entry = _load_entry_from_result(result, svc, kb)
                if entry:
                    entries.append(entry)
        else:
            # Collection ID mode — load collection, resolve its entries
            collection = svc.load_entry_from_disk(collection_id, kb or "")
            if not collection:
                console.print(f"[red]Collection not found: {collection_id}[/red]")
                raise typer.Exit(1)

            from ..models.collection import CollectionEntry

            if not isinstance(collection, CollectionEntry):
                console.print(f"[red]Entry {collection_id} is not a collection[/red]")
                raise typer.Exit(1)

            if collection.source_type == "query" and collection.query:
                from ..services.collection_query import evaluate_query, parse_query

                query = parse_query(collection.query)
                if kb:
                    query.kb_name = kb
                results, total = evaluate_query(query, db, readable_kbs=UNSCOPED)
                for result in results:
                    entry = _load_entry_from_result(result, svc, kb)
                    if entry:
                        entries.append(entry)
            elif collection.folder_path:
                # Folder-based: list entries in that folder
                kb_config = config.get_kb(kb or "")
                if kb_config:
                    folder = kb_config.path / collection.folder_path
                    if folder.exists():
                        from ..models.core_types import entry_from_frontmatter
                        from ..utils.yaml import load_yaml

                        for md_file in sorted(folder.rglob("*.md")):
                            if md_file.name.startswith("__"):
                                continue
                            try:
                                import re

                                text = md_file.read_text(encoding="utf-8")
                                parts = re.split(r"^---\s*$", text, flags=re.MULTILINE, maxsplit=2)
                                if len(parts) >= 3:
                                    meta = load_yaml(parts[1])
                                    body = parts[2].strip()
                                    entry = entry_from_frontmatter(meta, body)
                                    entry.file_path = md_file
                                    entries.append(entry)
                            except Exception as e:
                                logger.warning("Could not load %s: %s", md_file, e)

        # Follow links to depth N
        if depth > 0 and entries:
            entries = _follow_links(entries, svc, kb or "", depth)

        if not entries:
            console.print("[yellow]No entries to export.[/yellow]")
            raise typer.Exit(0)

        # Export
        export_title = title or f"Export: {collection_id}"
        result = ExportService.export_collection_entries(
            entries=entries,
            output_dir=output,
            bundle_strategy=bundle_strategy,
            source_mode=source_mode,
            title=export_title,
        )

        console.print(f"[green]Exported {result['entries_exported']} entries[/green]")
        console.print(f"  Files created: {result['files_created']}")
        console.print(f"  Manifest: {output / '_manifest.md'}")
        console.print(f"  Output: {output}")


@export_app.command("site")
def export_site(
    kb: str = typer.Option(..., "-k", "--kb", help="Knowledge base name"),
    output: Path = typer.Option(Path("./site"), "-o", "--output", help="Output directory"),
    format: str = typer.Option("quartz", "--format", "-f", help="Site format (currently: quartz)"),
    exclude_types: str = typer.Option(
        None, "--exclude-types", help="Comma-separated entry types to exclude"
    ),
    exclude_status: str = typer.Option(
        None, "--exclude-status", help="Comma-separated status values to exclude"
    ),
    init: bool = typer.Option(
        False, "--init", help="Scaffold a complete project (first-time setup)"
    ),
):
    """Export an entire KB as a static site.

    Examples:

        # First-time setup: scaffold Quartz project + export content
        pyrite export site -k my-kb -o ./my-site --init

        # Update content only (after initial setup)
        pyrite export site -k my-kb -o ./my-site

        # Exclude certain entry types and statuses
        pyrite export site -k my-kb -o ./my-site \\
            --exclude-types backlog_item,note --exclude-status draft,done
    """
    if format != "quartz":
        console.print(f"[red]Unknown site format: {format}[/red]")
        console.print("Valid options: quartz")
        raise typer.Exit(1)

    from ..renderers.quartz import export_site as quartz_export
    from ..renderers.quartz import scaffold_quartz_project

    # Parse filter sets
    type_filter = set(exclude_types.split(",")) if exclude_types else set()
    status_filter = set(exclude_status.split(",")) if exclude_status else set()

    with cli_context() as (config, db, svc):
        kb_config = config.get_kb(kb)
        if not kb_config:
            console.print(f"[red]KB not found: {kb}[/red]")
            raise typer.Exit(1)

        # Scaffold project if --init
        if init:
            scaffolded = scaffold_quartz_project(output, kb_name=kb_config.name)
            for f in scaffolded:
                console.print(f"  [dim]Created {f}[/dim]")

        # Load all entries from DB
        raw_entries = svc.list_entries(kb_name=kb, limit=100000)
        if not raw_entries:
            console.print("[yellow]No entries found in KB.[/yellow]")
            raise typer.Exit(0)

        # Load full Entry objects from disk
        entries = []
        for row in raw_entries:
            entry = _load_entry_from_result(row, svc, kb)
            if entry:
                entries.append(entry)

        if not entries:
            console.print("[yellow]No entries could be loaded.[/yellow]")
            raise typer.Exit(0)

        # Export to content/ subdirectory (or root if not --init)
        content_dir = (
            output / "content" if init or (output / "quartz.config.ts").exists() else output
        )
        result = quartz_export(
            entries=entries,
            output_dir=content_dir,
            kb_name=kb_config.name,
            kb_description=kb_config.description,
            exclude_types=type_filter,
            exclude_statuses=status_filter,
        )

        console.print(f"[green]Exported {result['entries_exported']} entries[/green]")
        if result["skipped"]:
            console.print(f"  Skipped: {result['skipped']} (filtered)")
        console.print(f"  Files created: {result['files_created']}")
        console.print(f"  Output: {content_dir}")

        if init:
            console.print("")
            console.print("[bold]Next steps:[/bold]")
            console.print(f"  cd {output}")
            console.print("  npm install")
            console.print("  npx quartz build --serve")


def _load_entry_from_result(result: dict, svc, kb: str | None):
    """Load an Entry from a DB query result, using file_path when available."""
    from ..models.core_types import entry_from_frontmatter
    from ..utils.yaml import load_yaml

    # Try file_path from DB first (handles ID/filename mismatches)
    file_path = result.get("file_path", "")
    if file_path:
        import re
        from pathlib import Path

        path = Path(file_path)
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
                parts = re.split(r"^---\s*$", text, flags=re.MULTILINE, maxsplit=2)
                if len(parts) >= 3:
                    meta = load_yaml(parts[1])
                    body = parts[2].strip()
                    entry = entry_from_frontmatter(meta, body)
                    entry.file_path = path
                    return entry
            except Exception:
                pass

    # Fallback to load_entry_from_disk (searches by ID)
    entry_id = result.get("id", "")
    entry_kb = result.get("kb_name", kb or "")
    if entry_id and entry_kb:
        return svc.load_entry_from_disk(entry_id, entry_kb)
    return None


def _follow_links(entries: list, svc, kb_name: str, depth: int) -> list:
    """Follow links from entries to include linked entries up to depth N."""
    seen_ids = {e.id for e in entries}
    all_entries = list(entries)
    frontier = list(entries)

    for _ in range(depth):
        next_frontier = []
        for entry in frontier:
            for link in entry.links:
                target_id = link.target
                if target_id not in seen_ids:
                    linked = svc.load_entry_from_disk(target_id, kb_name)
                    if linked:
                        all_entries.append(linked)
                        next_frontier.append(linked)
                        seen_ids.add(target_id)
        frontier = next_frontier
        if not frontier:
            break

    return all_entries

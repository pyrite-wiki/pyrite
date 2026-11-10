"""Export commands — export KB entries to external formats."""

import logging
from pathlib import Path

import typer

from pyrite.cli.output import cli_console, path_line

from ..cli.context import cli_context
from ..services.access_policy import UNSCOPED
from ..utils.errors import exit_unless_whole
from ..utils.frontmatter import Frontmatter, split_frontmatter

logger = logging.getLogger(__name__)
console = cli_console()

export_app = typer.Typer(help="Export KB entries to external formats")


def _legacy_format_option():
    """`--format`/`-f` named the export target until #873 gave `--format` to the
    shared output option (ADR-0046). Kept as a hidden alias until 0.27."""
    return typer.Option(None, "--format", "-f", hidden=True)


def _resolve_target(target: str | None, legacy_format: str | None, valid: tuple[str, ...]) -> str:
    """The export target, from `--target` or its deprecated alias `--format`/`-f`.

    Both given with different values is a usage error (exit 2); the old spelling
    alone warns once on stderr (the deprecation pattern of #303). A value the
    command cannot produce is a usage error too, before anything is written.
    The first of ``valid`` is the default.
    """
    if legacy_format is not None and target is not None and target != legacy_format:
        raise typer.BadParameter(
            f"--target {target} and --format {legacy_format} disagree; "
            "use --target only (--format is deprecated)."
        )
    for given in (target, legacy_format):
        if given is not None and given not in valid:
            raise typer.BadParameter(
                f"unknown target {given!r}; valid targets: {', '.join(valid)}."
            )
    if legacy_format is None:
        return target if target is not None else valid[0]
    typer.echo(
        "Warning: --format/-f on `pyrite export` is deprecated and will be removed in "
        f"0.27; use --target {legacy_format}.",
        err=True,
    )
    return legacy_format


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
        0, "--depth", min=0, help="Link-following depth (0=no following, 1=direct links, etc.)"
    ),
    title: str = typer.Option(None, "--title", help="Title for the manifest document"),
    target: str = typer.Option(
        None, "--target", help="Export target (currently: notebooklm; the default)"
    ),
    legacy_format: str = _legacy_format_option(),
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
    _resolve_target(target, legacy_format, ("notebooklm",))

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

    with cli_context(kbs=(kb,), output_format="rich") as (config, db, svc):
        entries: list[Entry] = []
        unreadable: list[tuple[str, str]] = []  # (name, reason) for each row left out

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
                entry, reason = _load_entry_from_result(result, svc, kb)
                if entry:
                    entries.append(entry)
                else:
                    unreadable.append((_row_name(result), reason))
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
                    entry, reason = _load_entry_from_result(result, svc, kb)
                    if entry:
                        entries.append(entry)
                    else:
                        unreadable.append((_row_name(result), reason))
            elif collection.folder_path:
                # Folder-based: list entries in that folder
                kb_config = config.get_kb(kb or "")
                if kb_config:
                    folder = kb_config.path / collection.folder_path
                    if folder.exists():
                        for md_file in sorted(folder.rglob("*.md")):
                            if md_file.name.startswith("__"):
                                continue
                            try:
                                entry = _entry_from_file(md_file)
                            except Exception as e:
                                logger.warning("Could not load %s: %s", md_file, e)
                                unreadable.append((str(md_file), _reason(e)))
                            else:
                                if entry:
                                    entries.append(entry)
                                else:
                                    unreadable.append((str(md_file), "the file has no frontmatter"))

        # Follow links to depth N
        if depth > 0 and entries:
            entries = _follow_links(entries, svc, kb or "", depth)

        _report_left_out(unreadable)

        if not entries:
            console.print("[yellow]No entries to export.[/yellow]")
            exit_unless_whole(len(unreadable), 0)  # matched but unloadable: nothing exported
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

        console.print(
            f"[green]Exported {_count(result['entries_exported'], 'entry', 'entries')}[/green]"
        )
        console.print(f"  Files created: {result['files_created']}")
        console.print(path_line("  Manifest: ", output / "_manifest.md"))
        console.print(path_line("  Output: ", output))
        exit_unless_whole(len(unreadable), result["entries_exported"])


@export_app.command("site")
def export_site(
    kb: str = typer.Option(..., "-k", "--kb", help="Knowledge base name"),
    output: Path = typer.Option(Path("./site"), "-o", "--output", help="Output directory"),
    target: str = typer.Option(
        None, "--target", help="Site target (currently: quartz; the default)"
    ),
    legacy_format: str = _legacy_format_option(),
    exclude_types: str = typer.Option(
        None, "--exclude-types", help="Comma-separated entry types to exclude"
    ),
    exclude_status: str = typer.Option(
        None, "--exclude-status", help="Comma-separated status values to exclude"
    ),
    init: bool = typer.Option(
        False, "--init", help="Scaffold a complete project (first-time setup)"
    ),
    allow_partial: bool = typer.Option(
        False, "--allow-partial", help="Exit 0 even when entries were left out"
    ),
):
    """Export an entire KB as a static site.

    An entry that cannot be loaded is named with its loader's message and left
    out of the export; the command then exits 3 (some exported) or 1 (none).
    ``--allow-partial`` restores exit 0 for callers that accept a partial site.

    Examples:

        # First-time setup: scaffold Quartz project + export content
        pyrite export site -k my-kb -o ./my-site --init

        # Update content only (after initial setup)
        pyrite export site -k my-kb -o ./my-site

        # Exclude certain entry types and statuses
        pyrite export site -k my-kb -o ./my-site \\
            --exclude-types backlog_item,note --exclude-status draft,done
    """
    _resolve_target(target, legacy_format, ("quartz",))

    from ..renderers.quartz import export_site as quartz_export
    from ..renderers.quartz import scaffold_quartz_project

    # Parse filter sets
    type_filter = set(exclude_types.split(",")) if exclude_types else set()
    status_filter = set(exclude_status.split(",")) if exclude_status else set()

    with cli_context(kbs=(kb,), output_format="rich") as (config, db, svc):
        kb_config = config.get_kb(kb)
        if not kb_config:
            console.print(f"[red]KB not found: {kb}[/red]")
            raise typer.Exit(1)

        # Scaffold project if --init
        if init:
            scaffolded = scaffold_quartz_project(output, kb_name=kb_config.name)
            for f in scaffolded:
                console.print(path_line("  Created ", f, style="dim"))

        # Load all entries from DB
        raw_entries = svc.list_entries(kb_name=kb, limit=100000)
        if not raw_entries:
            console.print("[yellow]No entries found in KB.[/yellow]")
            raise typer.Exit(0)

        # Load full Entry objects from disk
        entries = []
        left_out: list[tuple[str, str]] = []
        for row in raw_entries:
            entry, reason = _load_entry_from_result(row, svc, kb)
            if entry:
                entries.append(entry)
            else:
                left_out.append((_row_name(row), reason))

        _report_left_out(left_out)

        if not entries:
            console.print("[yellow]No entries could be loaded.[/yellow]")
            if not allow_partial:
                exit_unless_whole(len(left_out), 0)  # matched but unloadable: nothing exported
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

        console.print(
            f"[green]Exported {_count(result['entries_exported'], 'entry', 'entries')}[/green]"
        )
        if result["skipped"]:
            console.print(f"  Skipped: {result['skipped']} (filtered)")
        console.print(f"  Files created: {result['files_created']}")
        console.print(path_line("  Output: ", content_dir))
        if not allow_partial:
            exit_unless_whole(len(left_out), result["entries_exported"])

        if init:
            console.print("")
            console.print("[bold]Next steps:[/bold]")
            console.print(path_line("  cd ", output))
            console.print("  npm install")
            console.print("  npx quartz build --serve")


def _reason(error: Exception) -> str:
    """The loader's message for an error, falling back to its class name."""
    return str(error) or type(error).__name__


def _row_name(row: dict) -> str:
    """How an export names a row it left out: its id, else its file, else a dash."""
    return row.get("id") or row.get("file_path") or "(unknown)"


def _count(n: int, singular: str, plural: str) -> str:
    """``n`` with the right word, so an export never prints "1 entries"."""
    return f"{n} {singular if n == 1 else plural}"


def _report_left_out(left_out: list[tuple[str, str]]) -> None:
    """Name every row an export left out, one line each, with the loader's reason.

    Filtering (`--exclude-types`, `--exclude-status`) is not "left out": the
    caller asked for it and it is reported separately.
    """
    if not left_out:
        return
    console.print(
        f"[yellow]{_count(len(left_out), 'entry', 'entries')} left out, "
        f"could not be loaded:[/yellow]"
    )
    for name, reason in left_out:
        console.print(f"  {name}: {reason}")


def _entry_from_file(path):
    """The Entry a markdown file holds, split by the loader's rule; None when
    the file has no frontmatter."""
    from ..models.core_types import entry_from_frontmatter
    from ..utils.yaml import load_yaml

    split = split_frontmatter(path.read_text(encoding="utf-8"))
    if not isinstance(split, Frontmatter):
        return None
    entry = entry_from_frontmatter(load_yaml(split.text), split.body)
    entry.file_path = path
    return entry


def _load_entry_from_result(result: dict, svc, kb: str | None) -> tuple[object, str]:
    """Load an Entry from a DB query result, using file_path when available.

    Returns ``(entry, "")`` on success and ``(None, reason)`` otherwise, so an
    export can name every row it leaves out and say why.
    """
    # Try file_path from DB first (handles ID/filename mismatches)
    file_path = result.get("file_path", "")
    if file_path:
        from pathlib import Path

        path = Path(file_path)
        if path.exists():
            try:
                entry = _entry_from_file(path)
            except Exception as e:
                return None, _reason(e)
            if entry:
                return entry, ""
            return None, "the file has no frontmatter"

    # Fallback to load_entry_from_disk (searches by ID)
    entry_id = result.get("id", "")
    entry_kb = result.get("kb_name", kb or "")
    if entry_id and entry_kb:
        try:
            entry = svc.load_entry_from_disk(entry_id, entry_kb)
        except Exception as e:
            return None, _reason(e)
        if entry:
            return entry, ""
    if file_path:
        return None, f"the file is missing: {file_path}"
    return None, "it is not on disk"


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

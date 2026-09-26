"""
Index management commands for pyrite CLI.

Commands: build, sync, stats, embed, health
"""

import logging
import os

import typer
from rich.console import Console
from rich.table import Table

from ..utils.errors import cli_error
from .context import cli_db_context, get_config_and_db

logger = logging.getLogger(__name__)

index_app = typer.Typer(help="Search index management")
console = Console()


def _format_output(data: dict, fmt: str) -> str | None:
    from .output import format_output

    return format_output(data, fmt)


def _settle_embed_queue(db) -> int:
    """Pay off whatever ADR-0035 writes left in `embed_queue`.

    Shares one implementation with the server (`settle_embed_queue`), which is
    the point: an earlier version retired rows here by asking only "does this
    entry have *a* vector?", which is false for an entry whose body changed
    after it was embedded. Combined with `embed_all(force=False)` skipping
    entries that already have a vector, an update's queue row was deleted
    while its vector still encoded the old body -- `embed-status` zero, the
    semantic index silently stale, and only `--force` able to recover it.
    Draining re-embeds through `upsert_embedding`, which replaces the vector
    unconditionally, so the update is handled correctly and no reconciliation
    step is needed.

    Never raises: a CLI that indexed successfully must not exit non-zero
    because the queue bookkeeping could not be finished.
    """
    from ..services.embedding_worker import settle_embed_queue

    return settle_embed_queue(db)


@index_app.command("build")
def index_build(
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB to index (all if omitted)"),
    force: bool = typer.Option(False, "--force", "-f", help="Force full reindex"),
    with_attribution: bool = typer.Option(
        False, "--with-attribution", help="Extract git history for attribution"
    ),
    no_embed: bool = typer.Option(False, "--no-embed", help="Skip auto-embedding after build"),
    background: bool = typer.Option(False, "--background", help="Run in background thread"),
):
    """Build or rebuild the search index."""
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

    from ..storage import IndexManager

    config, db = get_config_and_db()

    if background:
        from ..services.index_worker import IndexWorker

        worker = IndexWorker(db, config)
        if kb_name:
            job_id = worker.submit_rebuild(kb_name)
        else:
            # Submit rebuild for each KB (incl. `kb add` / DB-registered ones)
            job_ids = []
            for kb in config.all_kbs():
                if kb.path.exists():
                    job_ids.append(worker.submit_rebuild(kb.name))
            console.print(f"[green]Submitted {len(job_ids)} rebuild job(s)[/green]")
            console.print("Use 'pyrite index jobs' to check progress.")
            return
        console.print(f"[green]Rebuild job submitted:[/green] {job_id}")
        console.print("Use 'pyrite index jobs' to check progress.")
        return

    index_mgr = IndexManager(db, config)

    if kb_name:
        kb = config.get_kb(kb_name)
        if not kb:
            cli_error(
                f"KB '{kb_name}' not found",
                error_code="KB_NOT_FOUND",
                suggestion="run `pyrite kb list` to see available KBs",
            )
        kbs = [kb]
    else:
        kbs = config.all_kbs()

    if not kbs:
        console.print("[yellow]No knowledge bases configured.[/yellow]")
        return

    git_service = None
    if with_attribution:
        from ..services.git_service import GitService

        git_service = GitService()
        console.print("[dim]Building index with git attribution...[/dim]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        for kb in kbs:
            if not kb.path.exists():
                console.print(f"[yellow]Skipping {kb.name}: path does not exist[/yellow]")
                continue

            task = progress.add_task(f"Indexing {kb.name}...", total=None)

            def make_progress_callback(task_id):
                def update_progress(current: int, total: int):
                    progress.update(task_id, completed=current, total=total)

                return update_progress

            if with_attribution and git_service:
                count = index_mgr.index_with_attribution(
                    kb.name, git_service, progress_callback=make_progress_callback(task)
                )
            else:
                count = index_mgr.index_kb(kb.name, make_progress_callback(task))
            progress.update(task, description=f"[green]✓[/green] {kb.name}: {count} entries")

    console.print("\n[green]Index build complete.[/green]")

    # Auto-embed after build if embeddings are available
    if not no_embed:
        try:
            from ..services.embedding_service import EmbeddingService, is_available

            if is_available() and db.vec_available:
                console.print("[dim]Generating embeddings...[/dim]")
                # Queue first: a queued row means "this entry changed", and
                # draining re-embeds it through upsert_embedding, which
                # replaces the vector. embed_all(force=False) afterwards would
                # *skip* it -- it only asks whether a vector exists, not
                # whether it is current -- so an update's row must be settled
                # before, not after.
                _settle_embed_queue(db)
                svc = EmbeddingService(db, model_name=config.settings.embedding_model)
                stats = svc.embed_all(kb_name=kb_name, force=force)
                if stats["embedded"] > 0:
                    console.print(
                        f"[green]Embedded {stats['embedded']} entries[/green] "
                        f"(skipped {stats['skipped']})"
                    )
        except Exception:
            logger.debug("Embedding not available, skipping")


@index_app.command("sync")
def index_sync(
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB to sync (all if omitted)"),
    no_embed: bool = typer.Option(False, "--no-embed", help="Skip auto-embedding after sync"),
    background: bool = typer.Option(False, "--background", help="Run in background thread"),
):
    """Incremental sync: update index for changed files only."""
    from ..storage import IndexManager

    config, db = get_config_and_db()

    if background:
        from ..services.index_worker import IndexWorker

        worker = IndexWorker(db, config)
        job_id = worker.submit_sync(kb_name)
        console.print(f"[green]Sync job submitted:[/green] {job_id}")
        console.print("Use 'pyrite index jobs' to check progress.")
        return

    index_mgr = IndexManager(db, config)

    results = index_mgr.sync_incremental(kb_name)

    console.print("[green]Sync complete:[/green]")
    console.print(f"  Added: {results['added']}")
    console.print(f"  Updated: {results['updated']}")
    console.print(f"  Removed: {results['removed']}")
    # Surface malformed-frontmatter files as a summary instead of letting
    # per-file ScannerError tracebacks pollute stderr. Tier A 1080.
    malformed = results.get("malformed", [])
    if malformed:
        console.print(f"  [yellow]Malformed: {len(malformed)} file(s) skipped[/yellow]")
        for entry in malformed[:5]:
            console.print(f"    [dim]• {entry['path']}[/dim]")
        if len(malformed) > 5:
            console.print(f"    [dim]… and {len(malformed) - 5} more[/dim]")

    # Auto-embed new/updated entries if embeddings are available.
    #
    # The queue is settled FIRST and outside the `changed > 0` gate, for two
    # separate reasons. (a) `changed > 0` gates the *file*-driven embed only:
    # under ADR-0035 a write indexes its own entry and leaves a queue row, so
    # a sync can find nothing changed on disk and still owe embeddings --
    # which is the whole point of `pyrite index sync` as a drain point. (b) a
    # queued row means "this entry changed", and only draining re-embeds it;
    # `embed_all(force=False)` would skip it because a (stale) vector exists.
    changed = results["added"] + results["updated"]
    if not no_embed:
        _settle_embed_queue(db)

    if changed > 0 and not no_embed:
        try:
            from ..services.embedding_service import EmbeddingService, is_available

            if is_available() and db.vec_available:
                svc = EmbeddingService(db, model_name=config.settings.embedding_model)
                stats = svc.embed_all(kb_name=kb_name)
                if stats["embedded"] > 0:
                    console.print(f"  Embedded: {stats['embedded']}")
        except Exception:
            logger.debug("Embedding not available, skipping")


@index_app.command("stats")
def index_stats(
    output_format: str = typer.Option(
        "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """Show index statistics."""
    from ..storage import IndexManager

    with cli_db_context() as (config, db):
        index_mgr = IndexManager(db, config)
        stats = index_mgr.get_index_stats()

    formatted = _format_output(stats, output_format)
    if formatted is not None:
        typer.echo(formatted)
        return

    console.print("\n[bold]Index Statistics[/bold]\n")
    console.print(f"Total entries: {stats['total_entries']}")
    console.print(f"Total tags: {stats['total_tags']}")
    console.print(f"Total links: {stats['total_links']}")

    if stats["kbs"]:
        console.print("\n[bold]Knowledge Bases:[/bold]")
        table = Table()
        table.add_column("Name", style="cyan")
        table.add_column("Type")
        table.add_column("Entries", justify="right")
        table.add_column("Last Indexed")

        for name, kb_stats in stats["kbs"].items():
            table.add_row(
                name,
                kb_stats.get("kb_type", "-"),
                str(kb_stats.get("actual_count", 0)),
                kb_stats.get("last_indexed", "-")[:19] if kb_stats.get("last_indexed") else "-",
            )
        console.print(table)


@index_app.command("embed")
def index_embed(
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB to embed (all if omitted)"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-embed all entries"),
):
    """Generate vector embeddings for semantic search."""
    from ..services.embedding_service import EmbeddingService, is_available

    if not is_available():
        cli_error(
            "sentence-transformers is not installed.",
            error_code="DEPENDENCY_MISSING",
            suggestion="install with: pip install pyrite[semantic]",
        )

    config, db = get_config_and_db()

    if not db.vec_available:
        cli_error(
            "sqlite-vec is not installed or failed to load.",
            error_code="DEPENDENCY_MISSING",
            suggestion="install with: pip install pyrite[semantic]",
        )

    # Check index has entries
    from ..storage import IndexManager

    if IndexManager(db, config).is_empty():
        cli_error(
            "Index is empty.",
            error_code="INDEX_EMPTY",
            suggestion="run `pyrite index build` first",
        )

    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

    # Before embed_all, not after: a queued row marks an entry whose body
    # changed, and only the drain re-embeds it. embed_all(force=False) skips
    # anything that already has a vector, stale or not.
    _settle_embed_queue(db)

    svc = EmbeddingService(db, model_name=config.settings.embedding_model)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Embedding entries...", total=None)

        def update_progress(current: int, total: int):
            progress.update(task, completed=current, total=total)

        stats = svc.embed_all(
            kb_name=kb_name,
            force=force,
            progress_callback=update_progress,
        )

    console.print("\n[green]Embedding complete.[/green]")
    console.print(f"  Embedded: {stats['embedded']}")
    console.print(f"  Skipped: {stats['skipped']}")
    if stats.get("truncated"):
        # Surface silent body-truncation count so operators know
        # how many entries had only a prefix embedded (Tier A r2100).
        console.print(
            f"  [yellow]Truncated: {stats['truncated']}[/yellow]"
            f" (body clipped to embedding model's char limit)"
        )
    if stats["errors"]:
        console.print(f"  [red]Errors: {stats['errors']}[/red]")


@index_app.command("health")
def index_health(
    output_format: str = typer.Option(
        "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
    kb_name: str = typer.Option(
        None, "--kb", "-k", help="Only check this KB (default: every configured KB)"
    ),
    fail: bool = typer.Option(
        True,
        "--fail/--no-fail",
        help="Exit 1 when the index is unhealthy (use --no-fail to always exit 0)",
    ),
):
    """Check index health and consistency.

    Exits 1 when the status is `unhealthy`, so a script, CI step or agent can
    gate on this command. `--no-fail` restores the old always-0 behaviour for
    callers that only want to read the report.
    """
    from ..storage import IndexManager

    with cli_db_context() as (config, db):
        if kb_name is not None and config.get_kb(kb_name) is None:
            # Reporting a clean bill for a KB that does not exist would be the same
            # bug as exiting 0 while unhealthy: a non-answer that reads as success.
            cli_error(
                f"KB not found: {kb_name}",
                output_format,
                error_code="KB_NOT_FOUND",
                suggestion="run `pyrite kb list` to see configured KBs",
            )
        index_mgr = IndexManager(db, config)
        health = index_mgr.check_health(kb_name=kb_name)

    broken_links = health.get("broken_links", 0)
    undeclared_types = health.get("undeclared_types", [])
    missing_required = health.get("missing_required_fields", [])
    subdirectory_mismatches = health.get("subdirectory_mismatches", [])
    malformed_frontmatter = health.get("malformed_frontmatter", [])
    invalid_statuses = health.get("invalid_statuses", [])
    is_unhealthy = (
        health["missing_files"]
        or health["unindexed_files"]
        or health["stale_entries"]
        or health.get("content_changed")
    )
    has_warning = (
        bool(broken_links)
        or bool(undeclared_types)
        or bool(missing_required)
        or bool(subdirectory_mismatches)
        or bool(malformed_frontmatter)
        or bool(invalid_statuses)
    )
    status = "unhealthy" if is_unhealthy else ("warning" if has_warning else "healthy")

    formatted = _format_output(
        {
            "status": status,
            "missing_files": len(health["missing_files"]),
            "unindexed_files": len(health["unindexed_files"]),
            "stale_entries": len(health["stale_entries"]),
            "content_changed": len(health.get("content_changed", [])),
            "broken_links": broken_links,
            "undeclared_types": undeclared_types,
            "missing_required_fields": missing_required,
            "subdirectory_mismatches": subdirectory_mismatches,
            "malformed_frontmatter": malformed_frontmatter,
            "invalid_statuses": invalid_statuses,
            "checks": health,
        },
        output_format,
    )
    # Print the report, then decide the exit code once, so that every output
    # format gates the same way (#18: the JSON path used to `return` here and
    # skip the verdict entirely).
    _report_health(
        formatted,
        is_unhealthy=is_unhealthy,
        has_warning=has_warning,
        broken_links=broken_links,
        undeclared_types=undeclared_types,
        missing_required=missing_required,
        subdirectory_mismatches=subdirectory_mismatches,
        malformed_frontmatter=malformed_frontmatter,
        invalid_statuses=invalid_statuses,
        health=health,
    )
    if is_unhealthy and fail:
        raise typer.Exit(1)


def _report_health(
    formatted,
    *,
    is_unhealthy,
    has_warning,
    broken_links,
    undeclared_types,
    missing_required,
    subdirectory_mismatches,
    malformed_frontmatter,
    invalid_statuses,
    health,
):
    """Print the health report. Returns nothing; the caller sets the exit code."""
    if formatted is not None:
        typer.echo(formatted)
        return

    console.print("\n[bold]Index Health Check[/bold]\n")

    if not is_unhealthy and not has_warning:
        console.print("[green]✓ Index is healthy[/green]")
        return

    if not is_unhealthy and has_warning:
        console.print("[green]✓ Index is healthy[/green]")

    if broken_links:
        console.print(
            f"[yellow]⚠ {broken_links} broken link(s) found.[/yellow]"
            " Run 'pyrite links check' for details."
        )

    if undeclared_types:
        total = sum(row["count"] for row in undeclared_types)
        console.print(
            f"[yellow]⚠ {len(undeclared_types)} undeclared entry type(s)"
            f" across {total} entries — not in kb.yaml:[/yellow]"
        )
        for row in undeclared_types[:10]:
            console.print(f"  • {row['kb']}: type='{row['type']}' ({row['count']} entries)")
        if len(undeclared_types) > 10:
            console.print(f"  ... and {len(undeclared_types) - 10} more")

    if missing_required:
        console.print(
            f"[yellow]⚠ {len(missing_required)} entries missing required fields:[/yellow]"
        )
        for row in missing_required[:10]:
            console.print(
                f"  • {row['kb']}/{row['id']} (type={row['type']}): missing {row['missing']}"
            )
        if len(missing_required) > 10:
            console.print(f"  ... and {len(missing_required) - 10} more")

    if subdirectory_mismatches:
        console.print(
            f"[yellow]⚠ {len(subdirectory_mismatches)} entries in the wrong"
            " subdirectory (writer hint, not enforced):[/yellow]"
        )
        for row in subdirectory_mismatches[:10]:
            console.print(
                f"  • {row['kb']}/{row['id']} (type={row['type']}):"
                f" expected '{row['declared_subdirectory']}/', found '{row['actual_path']}/'"
            )
        if len(subdirectory_mismatches) > 10:
            console.print(f"  ... and {len(subdirectory_mismatches) - 10} more")

    if malformed_frontmatter:
        console.print(
            f"[yellow]⚠ {len(malformed_frontmatter)} file(s) with malformed"
            " frontmatter (skipped — fix the YAML to index them):[/yellow]"
        )
        for row in malformed_frontmatter[:10]:
            console.print(f"  • {row['kb']}: {row['path']} — {row['error']}")
        if len(malformed_frontmatter) > 10:
            console.print(f"  ... and {len(malformed_frontmatter) - 10} more")

    if invalid_statuses:
        console.print(
            f"[yellow]⚠ {len(invalid_statuses)} entries with an invalid status"
            " (not in the type's declared enum):[/yellow]"
        )
        for row in invalid_statuses[:10]:
            allowed = ", ".join(row.get("allowed", [])) or "(see schema)"
            console.print(
                f"  • {row['kb']}/{row['id']} ({row['type']}):"
                f" status='{row['status']}' — allowed: {allowed}"
            )
        if len(invalid_statuses) > 10:
            console.print(f"  ... and {len(invalid_statuses) - 10} more")

    if health["missing_files"]:
        console.print(f"[red]Missing files ({len(health['missing_files'])}):[/red]")
        for item in health["missing_files"][:10]:
            console.print(f"  • {item['kb']}/{item['id']}")
        if len(health["missing_files"]) > 10:
            console.print(f"  ... and {len(health['missing_files']) - 10} more")

    if health["unindexed_files"]:
        console.print(f"[yellow]Unindexed files ({len(health['unindexed_files'])}):[/yellow]")
        for item in health["unindexed_files"][:10]:
            console.print(f"  • {item['kb']}/{item['id']}")
        if len(health["unindexed_files"]) > 10:
            console.print(f"  ... and {len(health['unindexed_files']) - 10} more")

    if health["stale_entries"]:
        console.print(f"[yellow]Stale entries ({len(health['stale_entries'])}):[/yellow]")
        for item in health["stale_entries"][:10]:
            console.print(f"  • {item['kb']}/{item['id']}")
        if len(health["stale_entries"]) > 10:
            console.print(f"  ... and {len(health['stale_entries']) - 10} more")

    content_changed = health.get("content_changed", [])
    if content_changed:
        console.print(
            f"[yellow]Content changed, mtime unchanged ({len(content_changed)}):[/yellow]"
        )
        for item in content_changed[:10]:
            console.print(f"  • {item['kb']}/{item['id']}")
        if len(content_changed) > 10:
            console.print(f"  ... and {len(content_changed) - 10} more")

    console.print("\nRun 'pyrite index sync' to fix issues.")


@index_app.command("reconcile")
def index_reconcile(
    kb_name: str = typer.Argument(..., help="KB to reconcile"),
    apply: bool = typer.Option(False, "--apply", help="Execute moves (default is dry-run)"),
):
    """Move files to match their templated subdirectories.

    Only types with a templated subdirectory are reconciled. Static subdirectories
    preserve deliberate placement, and collection descriptors stay in their folders.
    Filenames remain fixed at creation. By default this is a dry run; use --apply
    to execute moves.
    """
    from ..storage.document_manager import DocumentManager
    from ..storage.index import IndexManager
    from ..storage.repository import KBRepository

    config, db = get_config_and_db()
    kb_config = config.get_kb(kb_name)
    if not kb_config:
        cli_error(
            f"KB '{kb_name}' not found",
            error_code="KB_NOT_FOUND",
            suggestion="run pyrite kb list to see available KBs",
        )

    repo = KBRepository(kb_config)
    moves = []
    planning_errors = []

    for entry, current_path in repo.list_entries():
        if current_path.name == "__collection.yaml":
            continue
        try:
            if not DocumentManager._uses_templated_subdir(repo, entry):
                continue
            inferred_subdir = repo._infer_subdir(entry)
            expected_dir = repo._get_file_path(entry.id, inferred_subdir).parent
            expected_path = repo._contained(expected_dir / current_path.name)
            if current_path.parent.resolve() != expected_dir.resolve():
                moves.append((entry, current_path, expected_path))
        except Exception as e:
            # A bad template (for example one that resolves through an external
            # symlink) must not prevent other entries from being reconciled.
            planning_errors.append((entry.id, e))

    for entry_id, error in planning_errors:
        console.print(f"[yellow]Could not plan a move for {entry_id}:[/yellow] {error}")

    if not moves:
        if not planning_errors:
            console.print("[green]All files match their templated subdirectories.[/green]")
        else:
            console.print(
                f"[yellow]No moves planned; {len(planning_errors)} entry path(s) could not be checked.[/yellow]"
            )
        return

    table = Table(title=f"{'[DRY RUN] ' if not apply else ''}Files to move")
    table.add_column("Entry ID", style="cyan")
    table.add_column("Current Path")
    table.add_column("Target Path")
    for entry, current, target in moves:
        try:
            current_rel = str(current.relative_to(kb_config.path))
        except ValueError:
            current_rel = str(current)
        try:
            target_rel = str(target.relative_to(kb_config.path))
        except ValueError:
            target_rel = str(target)
        table.add_row(entry.id, current_rel, target_rel)
    console.print(table)
    console.print(f"\nTotal: {len(moves)} file(s) to move")

    if not apply:
        console.print("\n[yellow]Dry run. Use --apply to execute moves.[/yellow]")
        return

    if kb_config.read_only:
        console.print("[red]Cannot move files in a read-only KB.[/red]")
        return

    index_mgr = IndexManager(db, config)
    moved = 0
    for entry, current_path, target_path in moves:
        try:
            if target_path.exists():
                raise FileExistsError(f"Target path already exists: {target_path}")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            # Hard-link creation is atomic and fails if another process created
            # the destination after the check above; it cannot overwrite it.
            os.link(current_path, target_path)
            try:
                current_path.unlink()
            except Exception:
                target_path.unlink(missing_ok=True)
                raise
            try:
                entry.file_path = target_path
                index_mgr.index_entry(entry, kb_name, target_path)
            except Exception:
                try:
                    os.link(target_path, current_path)
                    target_path.unlink()
                except OSError:
                    logger.exception("Could not roll back reconcile move for %s", entry.id)
                entry.file_path = current_path
                raise
            moved += 1
        except Exception as e:
            # Per-entry failure inside a batch: warn and continue, do NOT exit.
            console.print(f"[red]Failed to move {entry.id}:[/red] {e}")

    console.print(f"\n[green]Moved {moved} file(s).[/green]")
    console.print("The index was updated for successfully moved files.")


@index_app.command("jobs")
def index_jobs():
    """List active and recent index jobs."""
    from ..services.index_worker import IndexWorker

    config, db = get_config_and_db()
    worker = IndexWorker(db, config)

    # Show recent jobs (active and completed)
    jobs = worker.get_recent_jobs()

    if not jobs:
        console.print("[dim]No index jobs found.[/dim]")
        return

    table = Table(title="Index Jobs")
    table.add_column("Job ID", style="cyan")
    table.add_column("KB")
    table.add_column("Operation")
    table.add_column("Status")
    table.add_column("Progress")
    table.add_column("Results")
    table.add_column("Created")

    for job in jobs:
        status_style = {
            "pending": "yellow",
            "running": "blue",
            "completed": "green",
            "failed": "red",
        }.get(job["status"], "")

        progress = ""
        if job["progress_total"]:
            progress = f"{job['progress_current']}/{job['progress_total']}"

        result_parts = []
        if job["added"]:
            result_parts.append(f"+{job['added']}")
        if job["updated"]:
            result_parts.append(f"~{job['updated']}")
        if job["removed"]:
            result_parts.append(f"-{job['removed']}")
        results_str = " ".join(result_parts) if result_parts else ""

        if job["error"]:
            results_str = job["error"][:40]

        table.add_row(
            job["job_id"],
            job["kb_name"] or "all",
            job["operation"],
            f"[{status_style}]{job['status']}[/{status_style}]",
            progress,
            results_str,
            job["created_at"][:19] if job["created_at"] else "",
        )

    console.print(table)

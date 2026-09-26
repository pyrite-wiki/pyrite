"""
Link management commands for pyrite CLI.

Commands: check, bulk-create, suggest, discover, batch-suggest
"""

from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.table import Table

from ..services.access_policy import UNSCOPED
from .context import cli_context, get_config_and_db

links_app = typer.Typer(help="Link validation and inspection")
console = Console()


def _format_output(data: dict, fmt: str) -> str | None:
    from .output import format_output

    return format_output(data, fmt)


@links_app.command("check")
def links_check(
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB to check links from"),
    limit: int = typer.Option(500, "--limit", help="Max missing targets to show"),
    detail: bool = typer.Option(False, "--detail", help="Show per-link breakdown"),
    output_format: str = typer.Option(
        "rich", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """Check for broken links (links to missing targets).

    Shows missing targets sorted by how many entries reference them.
    Use --detail to see which entries contain each broken link.
    """
    from ..services.wikilink_service import WikilinkService

    config, db = get_config_and_db()
    svc = WikilinkService(config, db)
    targets = svc.check_links(kb_name=kb_name, limit=limit)

    total_refs = sum(t["ref_count"] for t in targets)

    formatted = _format_output(
        {
            "missing_targets": len(targets),
            "total_references": total_refs,
            "targets": targets,
        },
        output_format,
    )
    if formatted is not None:
        typer.echo(formatted)
        return

    if not targets:
        console.print("[green]No broken links found.[/green]")
        return

    console.print(
        f"\n[bold]Link Check:[/bold] {len(targets)} missing target(s), {total_refs} reference(s)\n"
    )

    if detail:
        for t in targets:
            console.print(
                f"[bold]{t['target_id']}[/bold] ({t['target_kb']})"
                f" \u2014 {t['ref_count']} reference(s)"
            )
            for ref in t["references"]:
                rel = f" ({ref['relation']})" if ref.get("relation") else ""
                source = ref["source_id"]
                if ref["source_kb"] != t["target_kb"]:
                    source = f"{ref['source_kb']}/{source}"
                console.print(f"  \u2190 {source}{rel}")
            console.print()
    else:
        table = Table(show_lines=False)
        table.add_column("Missing Entry")
        table.add_column("KB")
        table.add_column("Refs", justify="right")
        table.add_column("Referenced By")

        for t in targets:
            ref_ids = [r["source_id"] for r in t["references"]]
            if len(ref_ids) > 3:
                ref_summary = ", ".join(ref_ids[:3]) + f" (+{len(ref_ids) - 3})"
            else:
                ref_summary = ", ".join(ref_ids)
            table.add_row(
                t["target_id"],
                t["target_kb"],
                str(t["ref_count"]),
                ref_summary,
            )

        console.print(table)
        if not detail:
            console.print("\nRun with [bold]--detail[/bold] for per-link breakdown.")


def _parse_link_specs(raw: str) -> list[dict]:
    """Parse YAML link specifications from a string."""
    import yaml

    data = yaml.safe_load(raw)
    if not isinstance(data, list):
        raise typer.BadParameter("YAML input must be a list of link specs")
    return data


def _validate_link_spec(spec: dict, index: int) -> list[str]:
    """Validate a single link spec dict. Returns list of error strings."""
    errors = []
    if not isinstance(spec, dict):
        return [f"Link {index}: expected a mapping, got {type(spec).__name__}"]
    if "source" not in spec:
        errors.append(f"Link {index}: missing required field 'source'")
    if "target" not in spec:
        errors.append(f"Link {index}: missing required field 'target'")
    return errors


@links_app.command("bulk-create")
def links_bulk_create(
    file: str | None = typer.Argument(
        None,
        help="YAML file with link specs, or '-' for stdin",
    ),
    kb_name: str = typer.Option(..., "--kb", "-k", help="Source KB name"),
    file_option: str | None = typer.Option(None, "--file", "-f", help="YAML file with link specs"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be created without writing"
    ),
):
    """Bulk-create links from a YAML file or stdin.

    YAML format (list of link specs):

    \b
    - source: entry-a
      target: entry-b
      relation: related_to
      note: "optional note"
      target_kb: other-kb

    Each spec requires 'source' and 'target'. Optional fields:
    'relation' (default: related_to), 'target_kb' (default: source KB), 'note'.
    """

    from ..exceptions import KBNotFoundError, KBReadOnlyError

    # Resolve input source: positional arg, --file option, or stdin
    input_path = file or file_option
    if input_path == "-" or (input_path is None and not sys.stdin.isatty()):
        raw = sys.stdin.read()
    elif input_path is not None:
        from pathlib import Path

        p = Path(input_path)
        if not p.exists():
            from ..utils.errors import cli_error

            cli_error(
                f"File not found: {input_path}",
                "rich",
                error_code="NOT_FOUND",
                suggestion="Provide a path to an existing YAML file.",
            )
        raw = p.read_text(encoding="utf-8")
    else:
        from ..utils.errors import cli_error

        cli_error(
            "Provide a YAML file path, --file, or pipe to stdin",
            "rich",
            error_code="VALIDATION_FAILED",
        )

    try:
        specs = _parse_link_specs(raw)
    except Exception as e:
        from ..utils.errors import cli_error

        cli_error(
            f"Failed to parse YAML: {e}",
            "rich",
            error_code="VALIDATION_FAILED",
        )

    # Validate all specs up front
    all_errors: list[str] = []
    for i, spec in enumerate(specs):
        all_errors.extend(_validate_link_spec(spec, i))
    if all_errors:
        from ..utils.errors import cli_error

        cli_error(
            "; ".join(all_errors),
            "rich",
            error_code="VALIDATION_FAILED",
        )

    with cli_context() as (_config, _db, svc):
        # KBService.add_links saves each source once, in place, through the
        # same path `pyrite link` uses. This command used to `repo.save` the
        # source, which re-derived its path from the type and wrote a second
        # copy of any entry kept elsewhere (#375).
        try:
            results = svc.add_links(kb_name, specs, dry_run=dry_run)
        except KBNotFoundError as e:
            from ..utils.errors import cli_error

            cli_error(
                str(e),
                "rich",
                error_code="KB_NOT_FOUND",
                suggestion="Run `pyrite kb list` to see available KBs.",
            )
        except KBReadOnlyError as e:
            from ..utils.errors import cli_error

            cli_error(str(e), "rich", error_code="READ_ONLY")

        created = skipped = failed = 0
        failed_details: list[str] = []
        for i, (spec, r) in enumerate(zip(specs, results, strict=True)):
            source_id = spec["source"]
            target_id = spec["target"]
            relation = spec.get("relation", "related_to")
            if r["status"] == "created":
                created += 1
                if dry_run:
                    console.print(
                        f"  [green]create[/green] {source_id} --[{relation}]--> {target_id}"
                        f" (target_kb={spec.get('target_kb', kb_name)})"
                    )
            elif r["status"] == "skipped":
                skipped += 1
                if dry_run:
                    console.print(
                        f"  [dim]skip[/dim] {source_id} --[{relation}]--> {target_id} (duplicate)"
                    )
            else:
                failed += 1
                failed_details.append(f"Link {i}: {r.get('error', 'failed')}")

        # Report
        label = "Dry run" if dry_run else "Bulk create"
        console.print(
            f"\n[bold]{label}:[/bold] {created} created, {skipped} skipped, {failed} failed"
        )
        for detail in failed_details:
            console.print(f"  [red]{detail}[/red]")


def _build_suggest_query(entry: dict) -> str:
    """Build an FTS5 OR query from an entry's title words and tags.

    Delegates to LinkDiscoveryService.build_suggest_query.
    """
    from ..services.link_discovery_service import LinkDiscoveryService

    return LinkDiscoveryService.build_suggest_query(entry)


def _suggest_links(
    entry_id: str,
    kb_name: str,
    target_kb: str | None,
    limit: int,
) -> list[dict]:
    """Find entries related to the given entry using FTS5 keyword search.

    Delegates to LinkDiscoveryService.suggest_links.
    """
    from ..services.link_discovery_service import LinkDiscoveryService

    config, db = get_config_and_db()
    try:
        svc = LinkDiscoveryService(config, db)
        return svc.suggest_links(entry_id, kb_name, target_kb, limit)
    finally:
        db.close()


@links_app.command("suggest")
def links_suggest(
    entry_id: str = typer.Argument(..., help="Entry ID to find suggestions for"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB containing the entry"),
    target_kb: str | None = typer.Option(
        None, "--target-kb", help="KB to search for candidates (default: same as --kb)"
    ),
    limit: int = typer.Option(10, "--limit", "-n", help="Max number of suggestions"),
    output_format: str = typer.Option(
        "rich", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """Suggest related entries that could be linked.

    Uses FTS5 keyword search on title and tags to find entries
    that are likely related. No LLM required.

    \b
    Examples:
        pyrite links suggest my-entry --kb notes
        pyrite links suggest my-entry --kb notes --target-kb other-kb --format json
    """
    from ..services.kb_service import KBService

    config, db = get_config_and_db()
    try:
        svc = KBService(config, db)
        entry = svc.get_entry(entry_id, kb_name=kb_name, readable_kbs=UNSCOPED)
    finally:
        db.close()

    if entry is None:
        from ..utils.errors import cli_error

        cli_error(
            f"Entry not found: {entry_id} (kb={kb_name})",
            output_format,
            error_code="NOT_FOUND",
        )

    candidates = _suggest_links(entry_id, kb_name, target_kb, limit)

    data = {
        "entry_id": entry_id,
        "kb_name": kb_name,
        "target_kb": target_kb or kb_name,
        "count": len(candidates),
        "suggestions": candidates,
    }

    formatted = _format_output(data, output_format)
    if formatted is not None:
        typer.echo(formatted)
        return

    if not candidates:
        console.print("[dim]No suggestions found.[/dim]")
        return

    console.print(
        f"\n[bold]Link suggestions for[/bold] {entry_id}"
        f" [dim](target KB: {target_kb or kb_name})[/dim]\n"
    )

    table = Table(show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Entry ID")
    table.add_column("Title")
    table.add_column("Type", style="dim")
    table.add_column("Score", justify="right")

    for i, c in enumerate(candidates, 1):
        table.add_row(
            str(i),
            c["id"],
            c["title"][:60],
            c["entry_type"],
            str(c["score"]),
        )

    console.print(table)


def _discover_neighbors(
    entry_id: str,
    kb_name: str,
    target_kb: str | None,
    limit: int,
    mode: str = "keyword",
    exclude_linked: bool = True,
    config=None,
    db=None,
) -> list[dict]:
    """Find related entries in all KBs, including the source KB, unless narrowed.

    Delegates to LinkDiscoveryService.discover_neighbors.
    """
    from ..services.link_discovery_service import LinkDiscoveryService

    close_db = False
    if config is None or db is None:
        config, db = get_config_and_db()
        close_db = True

    try:
        svc = LinkDiscoveryService(config, db)
        return svc.discover_neighbors(
            entry_id=entry_id,
            kb_name=kb_name,
            target_kb=target_kb,
            limit=limit,
            mode=mode,
            exclude_linked=exclude_linked,
            readable_kbs=UNSCOPED,
        )
    finally:
        if close_db:
            db.close()


@links_app.command("discover")
def links_discover(
    entry_id: str = typer.Argument(..., help="Entry ID to find neighbors for"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB containing the source entry"),
    target_kb: str | None = typer.Option(
        None, "--target-kb", help="KB to search (default: all KBs, including the source KB)"
    ),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results"),
    mode: str = typer.Option(
        "hybrid", "--mode", "-m", help="Search mode: keyword, semantic, hybrid"
    ),
    exclude_linked: bool = typer.Option(
        True,
        "--exclude-linked/--include-linked",
        help="Exclude entries that already have a link to the source",
    ),
    output_format: str = typer.Option(
        "rich", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """Discover related entries across KBs, including the source KB.

    Searches all KBs unless --target-kb is set. Excludes the source entry
    and, by default, entries already linked to it.

    \\b
    Examples:
        pyrite links discover trust-mechanisms --kb research
        pyrite links discover trust-mechanisms --kb research --target-kb governance
        pyrite links discover trust-mechanisms --kb research --mode semantic --limit 20
    """
    from ..services.kb_service import KBService

    config, db = get_config_and_db()
    try:
        svc = KBService(config, db)
        entry = svc.get_entry(entry_id, kb_name=kb_name, readable_kbs=UNSCOPED)
    finally:
        db.close()

    if entry is None:
        from ..utils.errors import cli_error

        cli_error(
            f"Entry not found: {entry_id} (kb={kb_name})",
            output_format,
            error_code="NOT_FOUND",
        )

    candidates = _discover_neighbors(
        entry_id,
        kb_name,
        target_kb,
        limit,
        mode=mode,
        exclude_linked=exclude_linked,
    )

    data = {
        "entry_id": entry_id,
        "kb_name": kb_name,
        "target_kb": target_kb or "all",
        "mode": mode,
        "exclude_linked": exclude_linked,
        "count": len(candidates),
        "discoveries": candidates,
    }

    formatted = _format_output(data, output_format)
    if formatted is not None:
        typer.echo(formatted)
        return

    if not candidates:
        console.print("[dim]No unlinked neighbors found.[/dim]")
        return

    target_label = target_kb or "all KBs"
    console.print(
        f"\n[bold]Unlinked neighbors for[/bold] {entry_id}"
        f" [dim](searching {target_label}, mode={mode})[/dim]\n"
    )

    table = Table(show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("KB", style="cyan")
    table.add_column("Entry ID")
    table.add_column("Title")
    table.add_column("Type", style="dim")
    table.add_column("Score", justify="right", style="green")

    for i, c in enumerate(candidates, 1):
        table.add_row(
            str(i),
            c["kb_name"],
            c["id"],
            c["title"][:50],
            c["entry_type"],
            str(c["score"]),
        )

    console.print(table)


def _batch_suggest(
    source_kb: str,
    target_kb: str,
    limit_per_entry: int = 3,
    mode: str = "keyword",
    exclude_linked: bool = True,
    config=None,
    db=None,
) -> list[dict]:
    """Find all potential cross-KB links between two KBs.

    Delegates to LinkDiscoveryService.batch_suggest.
    """
    from ..services.link_discovery_service import LinkDiscoveryService

    close_db = False
    if config is None or db is None:
        config, db = get_config_and_db()
        close_db = True

    try:
        svc = LinkDiscoveryService(config, db)
        return svc.batch_suggest(
            source_kb=source_kb,
            target_kb=target_kb,
            limit_per_entry=limit_per_entry,
            mode=mode,
            exclude_linked=exclude_linked,
            readable_kbs=UNSCOPED,
        )
    finally:
        if close_db:
            db.close()


@links_app.command("batch-suggest")
def links_batch_suggest(
    source_kb: str = typer.Option(..., "--source-kb", help="KB to find connections FROM"),
    target_kb: str = typer.Option(..., "--target-kb", help="KB to find connections TO"),
    limit_per_entry: int = typer.Option(
        3, "--limit-per-entry", "-n", help="Max matches per source entry"
    ),
    mode: str = typer.Option(
        "keyword", "--mode", "-m", help="Search mode: keyword, semantic, hybrid"
    ),
    exclude_linked: bool = typer.Option(
        True,
        "--exclude-linked/--include-linked",
        help="Exclude already-linked pairs",
    ),
    output_format: str = typer.Option(
        "rich", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """Batch-compare all entries between two KBs to find potential links.

    For each entry in source-kb, finds the most similar entries in target-kb.
    Deduplicates bidirectional matches and sorts by similarity score.

    \\b
    Examples:
        pyrite links batch-suggest --source-kb ramm --target-kb senge
        pyrite links batch-suggest --source-kb ramm --target-kb senge --mode semantic
        pyrite links batch-suggest --source-kb ramm --target-kb senge --limit-per-entry 5 --format json
    """
    pairs = _batch_suggest(
        source_kb=source_kb,
        target_kb=target_kb,
        limit_per_entry=limit_per_entry,
        mode=mode,
        exclude_linked=exclude_linked,
    )

    data = {
        "source_kb": source_kb,
        "target_kb": target_kb,
        "mode": mode,
        "exclude_linked": exclude_linked,
        "count": len(pairs),
        "pairs": pairs,
    }

    formatted = _format_output(data, output_format)
    if formatted is not None:
        typer.echo(formatted)
        return

    if not pairs:
        console.print("[dim]No cross-KB matches found.[/dim]")
        return

    console.print(
        f"\n[bold]Cross-KB matches:[/bold] {source_kb} → {target_kb}"
        f" [dim]({len(pairs)} pairs, mode={mode})[/dim]\n"
    )

    table = Table(show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Source Entry")
    table.add_column("", style="dim")
    table.add_column("Target Entry")
    table.add_column("Score", justify="right", style="green")

    for i, p in enumerate(pairs, 1):
        table.add_row(
            str(i),
            f"{p['source_title'][:35]}",
            "→",
            f"{p['target_title'][:35]}",
            str(p["score"]),
        )
        if i >= 50:
            table.add_row("", f"... and {len(pairs) - 50} more", "", "", "")
            break

    console.print(table)


def _find_orphans(
    kb_name: str,
    min_importance: int = 5,
    limit: int = 20,
    config=None,
    db=None,
) -> list[dict]:
    """Find high-importance entries with few cross-KB links relative to their potential.

    Delegates to LinkDiscoveryService.find_orphans.
    """
    from ..services.link_discovery_service import LinkDiscoveryService

    close_db = False
    if config is None or db is None:
        config, db = get_config_and_db()
        close_db = True

    try:
        svc = LinkDiscoveryService(config, db)
        return svc.find_orphans(
            kb_name=kb_name,
            min_importance=min_importance,
            limit=limit,
        )
    finally:
        if close_db:
            db.close()


@links_app.command("orphans")
def links_orphans(
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB to check for orphans"),
    min_importance: int = typer.Option(5, "--min-importance", help="Minimum importance threshold"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
    output_format: str = typer.Option(
        "rich", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """Find high-importance entries that lack cross-KB connections.

    Identifies entries that have high importance but few or no links to
    entries in other KBs, despite having potential semantic matches.
    These represent gaps in the knowledge graph.

    \\b
    Examples:
        pyrite links orphans --kb ramm
        pyrite links orphans --kb ramm --min-importance 8
        pyrite links orphans --kb ramm --format json
    """
    orphans = _find_orphans(kb_name=kb_name, min_importance=min_importance, limit=limit)

    data = {
        "kb_name": kb_name,
        "min_importance": min_importance,
        "count": len(orphans),
        "orphans": orphans,
    }

    formatted = _format_output(data, output_format)
    if formatted is not None:
        typer.echo(formatted)
        return

    if not orphans:
        console.print("[dim]No orphaned entries found.[/dim]")
        return

    console.print(
        f"\n[bold]Orphaned entries in[/bold] {kb_name}"
        f" [dim](importance >= {min_importance})[/dim]\n"
    )

    table = Table(show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Entry ID")
    table.add_column("Title")
    table.add_column("Imp", justify="right")
    table.add_column("Links", justify="right", style="dim")
    table.add_column("Potential", justify="right", style="cyan")
    table.add_column("Gap", justify="right", style="yellow")

    for i, o in enumerate(orphans, 1):
        table.add_row(
            str(i),
            o["id"],
            o["title"][:40],
            str(o["importance"]),
            str(o["cross_kb_links"]),
            str(o["potential_matches"]),
            str(o["orphan_score"]),
        )

    console.print(table)


def _find_asymmetric_links(
    kb_a: str,
    kb_b: str,
    config=None,
    db=None,
) -> list[dict]:
    """Find one-directional cross-KB links between two KBs.

    Delegates to LinkDiscoveryService.find_asymmetric_links.
    """
    from ..services.link_discovery_service import LinkDiscoveryService

    close_db = False
    if config is None or db is None:
        config, db = get_config_and_db()
        close_db = True

    try:
        svc = LinkDiscoveryService(config, db)
        return svc.find_asymmetric_links(kb_a=kb_a, kb_b=kb_b)
    finally:
        if close_db:
            db.close()


@links_app.command("asymmetric")
def links_asymmetric(
    kb: str | None = typer.Option(None, "-k", "--kb", help="Single KB to check"),
    kb_a: str | None = typer.Option(None, "--kb-a", help="First KB in a cross-KB check"),
    kb_b: str | None = typer.Option(None, "--kb-b", help="Second KB in a cross-KB check"),
    output_format: str = typer.Option(
        "rich", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """Find one-directional links between two KBs or within one KB.

    Detects links that exist A→B but not B→A (or vice versa).
    These asymmetries may represent missing reverse connections.

    \\b
    Examples:
        pyrite links asymmetric -k linkkb
        pyrite links asymmetric --kb linkkb --format json
        pyrite links asymmetric --kb-a ramm --kb-b senge
        pyrite links asymmetric --kb-a ramm --kb-b senge --format json
    """
    if kb is not None:
        if kb_a is not None or kb_b is not None:
            typer.echo(
                "Use either -k/--kb for one KB or both --kb-a and --kb-b for two KBs, not both.",
                err=True,
            )
            raise typer.Exit(code=2)
        kb_a = kb_b = kb
    elif kb_a is None or kb_b is None:
        typer.echo(
            "Use -k/--kb for one KB or provide both --kb-a and --kb-b for two KBs.",
            err=True,
        )
        raise typer.Exit(code=2)

    results = _find_asymmetric_links(kb_a=kb_a, kb_b=kb_b)

    data = {
        "kb_a": kb_a,
        "kb_b": kb_b,
        "count": len(results),
        "asymmetric_links": results,
    }

    formatted = _format_output(data, output_format)
    if formatted is not None:
        typer.echo(formatted)
        return

    if not results:
        console.print(
            "[dim]No asymmetric links found — all cross-KB links are bidirectional.[/dim]"
        )
        return

    console.print(
        f"\n[bold]Asymmetric links between[/bold] {kb_a} and {kb_b}"
        f" [dim]({len(results)} one-directional)[/dim]\n"
    )

    table = Table(show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Source")
    table.add_column("Direction", style="dim")
    table.add_column("Target")
    table.add_column("Relation", style="dim")

    for i, r in enumerate(results, 1):
        table.add_row(
            str(i),
            f"{r['source_title'][:30]}",
            r["direction"],
            f"{r['target_title'][:30]}",
            r["relation"],
        )

    console.print(table)

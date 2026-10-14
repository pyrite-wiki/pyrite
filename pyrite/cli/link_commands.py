"""
Link management commands for pyrite CLI.

Commands: check, bulk-create, suggest, discover, batch-suggest, orphans, asymmetric

On the outcome contract (ADR-0046): each command declares the shared
``--format`` option and returns an ``Outcome`` or raises a ``PyriteError``; the
root group prints it and sets the exit code. Bad input found in a body is a
``ValidationError`` (exit 1): after the body starts, a usage error is no
longer click's to raise.
"""

from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.table import Table

from ..exceptions import EntryNotFoundError, KBNotFoundError, ValidationError
from ..services.access_policy import UNSCOPED
from .context import cli_context, get_config_and_db, read_input_file
from .outcome import Outcome
from .output import OUTPUT_FORMAT

links_app = typer.Typer(help="Link validation and inspection")
console = Console()


@links_app.command("check")
def links_check(
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB to check links from"),
    limit: int = typer.Option(500, "--limit", help="Max missing targets to show"),
    detail: bool = typer.Option(False, "--detail", help="Show per-link breakdown"),
    output_format: str = OUTPUT_FORMAT,
) -> Outcome:
    """Check for broken links (links to missing targets).

    Exits 1 when any broken link is found (the findings are still printed),
    0 when there are none.

    Shows missing targets sorted by how many entries reference them.
    Use --detail to see which entries contain each broken link.
    """
    from ..services.wikilink_service import WikilinkService

    config, db = get_config_and_db()
    try:
        targets = WikilinkService(config, db).check_links(kb_name=kb_name, limit=limit)
    finally:
        db.close()

    total_refs = sum(t["ref_count"] for t in targets)

    def rich(console: Console, data: dict) -> None:
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
            console.print("\nRun with [bold]--detail[/bold] for per-link breakdown.")

    # A check exits 1 when it fails (ADR-0046, "Check commands"): a broken link
    # is the failure, and the findings stay in the data. `nothing` is the kind
    # whose exit is 1.
    data = {"missing_targets": len(targets), "total_references": total_refs, "targets": targets}
    return (Outcome.nothing if targets else Outcome.done)(data, rich=rich)


def _parse_link_specs(raw: str) -> list[dict]:
    """Parse YAML link specifications from a string."""
    import yaml

    data = yaml.safe_load(raw)
    if not isinstance(data, list):
        raise ValidationError("YAML input must be a list of link specs")
    return data


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
    output_format: str = OUTPUT_FORMAT,
) -> Outcome:
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

    # Resolve input source: positional arg, --file option, or stdin
    input_path = file or file_option
    if input_path == "-" or (input_path is None and not sys.stdin.isatty()):
        raw = sys.stdin.read()
    elif input_path is not None:
        try:
            raw = read_input_file(input_path)
        except EntryNotFoundError as error:
            error.suggestion = "Provide a path to an existing YAML file."
            raise
    else:
        raise ValidationError("Provide a YAML file path, --file, or pipe to stdin")

    try:
        specs = _parse_link_specs(raw)
    except Exception as e:
        raise ValidationError(f"Failed to parse YAML: {e}") from None

    # Each spec is checked by the service (KBService.add_links), the one place
    # a link spec is judged (#846): a malformed one fails alone, before its
    # source is touched, and the others go on.
    with cli_context() as (_config, _db, svc):
        # KBService.add_links saves each source once, in place, through the
        # same path `pyrite link` uses. This command used to `repo.save` the
        # source, which re-derived its path from the type and wrote a second
        # copy of any entry kept elsewhere (#375). A missing or read-only KB
        # is raised by the service and rendered by the root with its own code.
        try:
            results = svc.add_links(kb_name, specs, dry_run=dry_run)
        except KBNotFoundError as e:
            # The hint this command always gave; the root prints `suggestion`.
            e.suggestion = "Run `pyrite kb list` to see available KBs."
            raise

    created = skipped = failed = 0
    items: list[dict] = []
    for i, (spec, r) in enumerate(zip(specs, results, strict=True)):
        given = spec if isinstance(spec, dict) else {}
        item = {
            "index": i,
            "source": given.get("source"),
            "target": given.get("target"),
            "relation": given.get("relation", "related_to"),
            "target_kb": given.get("target_kb", kb_name),
            "status": r["status"] if r["status"] in ("created", "skipped") else "failed",
        }
        if item["status"] == "created":
            created += 1
        elif item["status"] == "skipped":
            skipped += 1
        else:
            failed += 1
            item["error"] = r.get("error", "failed")
            if r.get("error_code"):
                item["error_code"] = r["error_code"]
        items.append(item)

    def rich(console: Console, data: dict) -> None:
        if dry_run:
            for item in data["results"]:
                arrow = f"{item['source']} --[{item['relation']}]--> {item['target']}"
                if item["status"] == "created":
                    console.print(
                        f"  [green]create[/green] {arrow} (target_kb={item['target_kb']})"
                    )
                elif item["status"] == "skipped":
                    console.print(f"  [dim]skip[/dim] {arrow} (duplicate)")
        label = "Dry run" if dry_run else "Bulk create"
        console.print(
            f"\n[bold]{label}:[/bold] {created} created, {skipped} skipped, {failed} failed"
        )
        for item in data["results"]:
            if item["status"] == "failed":
                console.print(f"  [red]Link {item['index']}: {item['error']}[/red]")

    # The batch rule: none failed is done, some of each partial, none done nothing.
    return Outcome.from_counts(
        created + skipped,
        failed,
        {
            "dry_run": dry_run,
            "created": created,
            "skipped": skipped,
            "failed": failed,
            "results": items,
        },
        rich=rich,
    )


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
    output_format: str = OUTPUT_FORMAT,
) -> Outcome:
    """Suggest related entries that could be linked.

    Uses FTS5 keyword search on title and tags to find entries
    that are likely related. No LLM required.

    \b
    Examples:
        pyrite links suggest my-entry --kb notes
        pyrite links suggest my-entry --kb notes --target-kb other-kb --format json
    """
    from ..services.kb_service import KBService

    # The service first: it refuses a KB that does not exist (KB_NOT_FOUND),
    # which the entry lookup below would report as a missing entry (#846).
    candidates = _suggest_links(entry_id, kb_name, target_kb, limit)

    config, db = get_config_and_db()
    try:
        svc = KBService(config, db)
        entry = svc.get_entry(entry_id, kb_name=kb_name, readable_kbs=UNSCOPED)
    finally:
        db.close()

    if entry is None:
        raise EntryNotFoundError(f"Entry not found: {entry_id} (kb={kb_name})")

    data = {
        "entry_id": entry_id,
        "kb_name": kb_name,
        "target_kb": target_kb or kb_name,
        "count": len(candidates),
        "suggestions": candidates,
    }

    def rich(console: Console, data: dict) -> None:
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

    return Outcome.done(data, rich=rich)


def _discover_neighbors(
    entry_id: str,
    kb_name: str,
    target_kb: str | None,
    limit: int,
    mode: str = "keyword",
    exclude_linked: bool = True,
    config=None,
    db=None,
    include_empty: bool = False,
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
            include_empty=include_empty,
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
    include_empty: bool = typer.Option(
        False,
        "--include-empty",
        help="Also return entries with no body and no summary (left out by default)",
    ),
    output_format: str = OUTPUT_FORMAT,
) -> Outcome:
    """Discover related entries across KBs, including the source KB.

    Searches all KBs unless --target-kb is set. Excludes the source entry
    and, by default, entries already linked to it. Entries with no body and
    no summary are left out; --include-empty returns them.

    \\b
    Examples:
        pyrite links discover trust-mechanisms --kb research
        pyrite links discover trust-mechanisms --kb research --target-kb governance
        pyrite links discover trust-mechanisms --kb research --mode semantic --limit 20
    """
    from ..services.kb_service import KBService

    # The service first: it refuses a KB that does not exist (KB_NOT_FOUND),
    # which the entry lookup below would report as a missing entry (#846).
    candidates = _discover_neighbors(
        entry_id,
        kb_name,
        target_kb,
        limit,
        mode=mode,
        exclude_linked=exclude_linked,
        include_empty=include_empty,
    )

    config, db = get_config_and_db()
    try:
        svc = KBService(config, db)
        entry = svc.get_entry(entry_id, kb_name=kb_name, readable_kbs=UNSCOPED)
    finally:
        db.close()

    if entry is None:
        raise EntryNotFoundError(f"Entry not found: {entry_id} (kb={kb_name})")

    data = {
        "entry_id": entry_id,
        "kb_name": kb_name,
        "target_kb": target_kb or "all",
        "mode": mode,
        "exclude_linked": exclude_linked,
        "include_empty": include_empty,
        "count": len(candidates),
        "discoveries": candidates,
    }

    def rich(console: Console, data: dict) -> None:
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

    return Outcome.done(data, rich=rich)


def _batch_suggest(
    source_kb: str,
    target_kb: str,
    limit_per_entry: int = 3,
    mode: str = "keyword",
    exclude_linked: bool = True,
    config=None,
    db=None,
    include_empty: bool = False,
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
            include_empty=include_empty,
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
    include_empty: bool = typer.Option(
        False,
        "--include-empty",
        help="Also return entries with no body and no summary (left out by default)",
    ),
    output_format: str = OUTPUT_FORMAT,
) -> Outcome:
    """Batch-compare all entries between two KBs to find potential links.

    For each entry in source-kb, finds the most similar entries in target-kb.
    Deduplicates bidirectional matches and sorts by similarity score. Target
    entries with no body and no summary are left out; --include-empty returns
    them.

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
        include_empty=include_empty,
    )

    data = {
        "source_kb": source_kb,
        "target_kb": target_kb,
        "mode": mode,
        "exclude_linked": exclude_linked,
        "include_empty": include_empty,
        "count": len(pairs),
        "pairs": pairs,
    }

    def rich(console: Console, data: dict) -> None:
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

    return Outcome.done(data, rich=rich)


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
    output_format: str = OUTPUT_FORMAT,
) -> Outcome:
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

    def rich(console: Console, data: dict) -> None:
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

    return Outcome.done(data, rich=rich)


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
    output_format: str = OUTPUT_FORMAT,
) -> Outcome:
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
            raise ValidationError(
                "Use either -k/--kb for one KB or both --kb-a and --kb-b for two KBs, not both."
            )
        kb_a = kb_b = kb
    elif kb_a is None or kb_b is None:
        raise ValidationError(
            "Use -k/--kb for one KB or provide both --kb-a and --kb-b for two KBs."
        )

    results = _find_asymmetric_links(kb_a=kb_a, kb_b=kb_b)

    data = {
        "kb_a": kb_a,
        "kb_b": kb_b,
        "count": len(results),
        "asymmetric_links": results,
    }

    def rich(console: Console, data: dict) -> None:
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

    return Outcome.done(data, rich=rich)

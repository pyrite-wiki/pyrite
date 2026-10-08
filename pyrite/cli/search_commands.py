"""
Search command for pyrite CLI.

Commands: search (with file-based fallback)
"""

import logging

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from ..config import load_config
from ..exceptions import QuerySyntaxError, QueryTooLongError
from ..services.read_shaping import parse_fields_param, project_fields
from ..storage.repository import KBRepository
from ..utils.errors import exit_unless_whole
from .context import get_config_with_registered_kbs
from .output import validate_output_format

logger = logging.getLogger(__name__)

console = Console()
# Status/progress chatter goes here so it never pollutes stdout in machine
# formats (-f json etc.), where stdout must be valid parseable output only.
err_console = Console(stderr=True)


def _warn_if_stale(config, db, kb_name: str | None) -> None:
    """Emit a stderr warning if the index is behind the files on disk.

    Keeps the warning off stdout so `--format json` output stays parseable.
    Best-effort: any failure in the probe is swallowed (a staleness check must
    never break a search). Scoped to ``kb_name`` when the caller passed ``-k``.
    """
    try:
        from ..storage import IndexManager

        stale = IndexManager(db, config).check_staleness()
        if kb_name:
            stale = [s for s in stale if s["kb"] == kb_name]
        if not stale:
            return
        kbs = ", ".join(s["kb"] for s in stale)
        err_console.print(
            f"[yellow]Warning:[/yellow] index may be stale for: {kbs} "
            f"(a file on disk is newer than the index). Results could be out "
            f"of date — run [cyan]pyrite index sync[/cyan] to refresh."
        )
    except Exception:
        logger.debug("Staleness probe failed; continuing with search", exc_info=True)


def register_search_command(app: typer.Typer):
    """Register the search command on the given Typer app."""

    @app.command("search")
    def search(
        query: str = typer.Argument(..., help="Search query (FTS5 syntax supported)"),
        kb_name: str | None = typer.Option(None, "--kb", "-k", help="Search specific KB"),
        entry_type: str | None = typer.Option(None, "--type", "-t", help="Filter by type"),
        tag: str | None = typer.Option(None, "--tag", help="Filter by tag"),
        date_from: str | None = typer.Option(None, "--from", help="Events from date (YYYY-MM-DD)"),
        date_to: str | None = typer.Option(None, "--to", help="Events until date (YYYY-MM-DD)"),
        fips: str | None = typer.Option(
            None, "--fips", help="Filter by county FIPS code (e.g. 12086)"
        ),
        state_filter: str | None = typer.Option(
            None, "--state", help="Filter by US state (e.g. FL, TX)"
        ),
        status: str | None = typer.Option(
            None,
            "--status",
            help=(
                "Filter by the indexed 'status' frontmatter field (e.g. "
                "unprocessed, draft, done). Other metadata fields (e.g. "
                "'readiness') are NOT reachable via this flag."
            ),
        ),
        limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
        mode: str = typer.Option(
            None, "--mode", "-m", help="Search mode: keyword, semantic, hybrid"
        ),
        use_files: bool = typer.Option(False, "--files", help="Search files directly (skip index)"),
        expand: bool = typer.Option(False, "--expand", "-x", help="Use AI query expansion"),
        include_archived: bool = typer.Option(
            False, "--include-archived", help="Include archived entries in results"
        ),
        include_body: bool = typer.Option(
            False, "--include-body", help="Include full body text (default: snippet only)"
        ),
        debug: bool = typer.Option(
            False, "--debug", help="Print the search trace (mode, fallback reason, latency)"
        ),
        fields: str = typer.Option(
            None, "--fields", help="Comma-separated fields to return (e.g. id,title,tags)"
        ),
        output_format: str = typer.Option(
            "json",
            "--format",
            callback=validate_output_format,
            help="Output format: json, rich, markdown, csv, yaml",
        ),
    ):
        """
        Search across knowledge bases.

        Supports FTS5 query syntax:
        - Simple terms: miller immigration
        - Phrases: "family separation"
        - Boolean: miller AND immigration
        - Prefix: immigr*
        - Exclude: miller NOT bannon

        Auto-quote rule: special-char tokens (hyphens, dots, colons, etc.)
        are quoted automatically ONLY when the query has no AND/OR/NOT
        operator and no existing quote — e.g. "cross-link" alone becomes
        "cross-link" as a literal. Once you use AND/OR/NOT or a phrase
        quote, auto-quoting is skipped (the query is assumed intentional),
        so quote special-char tokens yourself: '"family separation"
        "cross-link"' NOT 'miller -bannon' (a bare hyphen there is parsed
        as the NOT operator, not a literal exclude — matching Bannon
        entries, not excluding them).
        """
        config = load_config()
        if kb_name:
            config = get_config_with_registered_kbs(config, name=kb_name)

        # A search scoped to an unregistered KB used to return an empty result
        # set (exit 0), which looks like a query miss rather than a wrong KB.
        # Fail loudly with a distinct KB_NOT_FOUND instead.
        if kb_name and config.get_kb(kb_name) is None:
            from ..utils.errors import cli_error

            known = ", ".join(sorted(kb.name for kb in config.all_kbs())) or "none"
            cli_error(
                f"KB not found: {kb_name}",
                output_format,
                error_code="KB_NOT_FOUND",
                suggestion=f"known KBs: {known} (or run `pyrite kb list`)",
            )

        # Every surface refuses an over-long query the same way, file search
        # included; the index path must not fall back to file search with it.
        from ..services.search_service import check_query_length

        try:
            check_query_length(query)
        except QueryTooLongError as e:
            from ..utils.errors import cli_error

            cli_error(
                str(e),
                output_format,
                error_code=e.error_code,
                suggestion="shorten the query",
                retryable=False,
            )

        if use_files:
            _search_files(config, query, kb_name, entry_type, limit)
            return

        from ..storage import IndexManager
        from .context import open_index_db

        db = None
        try:
            db = open_index_db(config)

            index_mgr = IndexManager(db, config)
            if index_mgr.is_empty():
                # Status to stderr — stdout must stay clean for -f json callers.
                err_console.print("[yellow]Index is empty. Building index...[/yellow]")
                index_mgr.index_all()
            else:
                # Warn (never silently) when the index is behind the files on
                # disk — otherwise search returns a confidently-wrong stale
                # view. Cheap probe (no per-entry parse); warning goes to
                # stderr so it never corrupts `--format json` on stdout.
                _warn_if_stale(config, db, kb_name)

            tags_list = [tag] if tag else None

            from ..services.search_service import SearchService

            search_svc = SearchService(db, settings=config.settings)
            search_mode = mode or config.settings.search_mode or "keyword"
            search_trace: dict = {} if debug else None
            # Anything the search could not do as asked — today, a filter a
            # backend's vector leg cannot honour (#56).
            search_warnings: list[str] = []
            results = search_svc.search(
                query=query,
                kb_name=kb_name,
                entry_type=entry_type,
                tags=tags_list,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
                mode=search_mode,
                expand=expand,
                include_archived=include_archived,
                fips=fips,
                state=state_filter,
                status=status,
                trace=search_trace,
                warnings=search_warnings,
            )

            for warning in search_warnings:
                # stderr, like the trace and the staleness notice, so it never
                # corrupts --format json on stdout.
                # escape(): a warning names `pip install pyrite[semantic]`, and
                # Rich reads `[semantic]` as a style tag and prints nothing there.
                err_console.print(f"[yellow]warning:[/yellow] {escape(warning)}", soft_wrap=True)

            if debug and search_trace is not None:
                # Trace goes to stderr so it never corrupts --format json on stdout.
                err_console.print(
                    "[dim]trace:[/dim] "
                    f"mode={search_trace.get('requested_mode')} "
                    f"actual={search_trace.get('actual_mode')} "
                    f"reason={search_trace.get('reason') or '-'} "
                    f"results={search_trace.get('result_count')} "
                    f"latency_ms={search_trace.get('latency_ms')} "
                    f"relaxed={search_trace.get('relaxed')}"
                )

            if not results:
                if output_format != "rich":
                    # Machine formats get a valid empty-result payload, not a
                    # human "no results" line that would break json.load().
                    from ..formats import format_response

                    content, _ = format_response(
                        {"query": query, "count": 0, "results": []}, output_format
                    )
                    typer.echo(content)
                else:
                    console.print("[yellow]No results found.[/yellow]")
                return

            # Apply field projection. One rule for every read surface (#193).
            fields_list = parse_fields_param(fields)
            if fields_list:
                results = [project_fields(record, fields_list) for record in results]
            elif not include_body:
                for r in results:
                    r.pop("body", None)

            if output_format != "rich":
                from ..formats import format_response

                resp_data = {"query": query, "count": len(results), "results": results}
                content, _ = format_response(resp_data, output_format)
                typer.echo(content)
                return

            table = Table(title=f"Search Results ({len(results)})")
            table.add_column("KB", style="cyan", width=12)
            table.add_column("Type", style="green", width=10)
            table.add_column("Title", width=40)
            table.add_column("Date", width=10)
            table.add_column("Snippet", width=50)

            for r in results:
                date = r.get("date", "")[:10] if r.get("date") else ""
                raw_snippet = r.get("snippet", "") or ""
                snippet = (raw_snippet[:97] + "...") if len(raw_snippet) > 100 else raw_snippet
                raw_title = r.get("title", "") or ""
                title = (raw_title[:37] + "...") if len(raw_title) > 40 else raw_title
                table.add_row(
                    r.get("kb_name", ""),
                    r.get("entry_type", ""),
                    title,
                    date,
                    snippet,
                )

            console.print(table)

        except QuerySyntaxError as e:
            # Deterministic, not retryable — falling back to file search
            # would not help (the query itself is the problem) and the
            # generic {error, error_type} shape below is off-contract for
            # a classified error. Use the canonical shape via cli_error.
            logger.debug("Query syntax error for query %r", query, exc_info=True)
            from ..utils.errors import cli_error

            cli_error(
                str(e),
                output_format,
                error_code="QUERY_SYNTAX",
                suggestion=(
                    "quote tokens containing - : . yourself when your query "
                    "uses AND/OR/NOT or phrase quotes"
                ),
                retryable=False,
            )
        except Exception as e:
            # Log the full exception (with traceback) so operators can tell a
            # corrupt index from a bad query from a locked DB — the one-line
            # messages below are not enough to troubleshoot from.
            logger.debug("Index search failed for query %r", query, exc_info=True)
            if output_format != "rich":
                import json

                typer.echo(
                    json.dumps(
                        {
                            "query": query,
                            "count": 0,
                            "results": [],
                            "error": str(e),
                            "error_type": type(e).__name__,
                        }
                    )
                )
                raise typer.Exit(1)
            console.print(f"[red]Search error ({type(e).__name__}):[/red] {e}")
            console.print("[dim]Falling back to file search...[/dim]")
            _search_files(config, query, kb_name, entry_type, limit)
        finally:
            if db is not None:
                db.close()


def _search_files(config, query, kb_name, entry_type, limit):
    """File-based search fallback."""
    if kb_name:
        kb = config.get_kb(kb_name)
        if not kb:
            from ..utils.errors import cli_error

            cli_error(
                f"KB '{kb_name}' not found",
                error_code="KB_NOT_FOUND",
                suggestion="run `pyrite kb list` to see registered KBs",
            )
        kbs = [kb]
    else:
        kbs = config.knowledge_bases

    if not kbs:
        console.print("[yellow]No knowledge bases configured.[/yellow]")
        return

    console.print(f"[dim]Searching for '{query}'...[/dim]")

    results = []
    unreadable = 0
    scanned = 0
    for kb in kbs:
        if not kb.path.exists():
            continue

        repo = KBRepository(kb)
        for md_file in repo.list_files():
            try:
                content = md_file.read_text(encoding="utf-8")
                scanned += 1
                if query.lower() in content.lower():
                    entry = repo._load_entry(md_file)

                    if entry_type and entry.entry_type != entry_type:
                        continue

                    results.append((kb.name, entry, md_file))

                    if len(results) >= limit:
                        break
            except Exception:
                logger.warning("Search failed for KB %s", kb.name, exc_info=True)
                unreadable += 1
                continue

        if len(results) >= limit:
            break

    if unreadable:
        # The answer is incomplete: say so, and do not exit 0 (#526).
        typer.echo(
            f"{unreadable} file(s) could not be read; the results below may be incomplete.",
            err=True,
        )

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        exit_unless_whole(unreadable, scanned)
        return

    table = Table(title=f"Search Results ({len(results)})")
    table.add_column("KB", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Title")
    table.add_column("ID", style="dim")

    for kb_name, entry, _path in results:
        table.add_row(kb_name, entry.entry_type, entry.title, entry.id)

    console.print(table)
    exit_unless_whole(unreadable, scanned)

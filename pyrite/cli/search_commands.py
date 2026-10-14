"""
Search command for pyrite CLI.

Commands: search (with file-based fallback)
"""

import logging
import os

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from ..config import load_config
from ..exceptions import QuerySyntaxError, QueryTooLongError
from ..services.read_shaping import (
    IDENTITY_FIELDS,
    SEARCH_FIELDS_ENV,
    parse_fields_param,
    project_fields,
    resolve_search_fields,
    unmatched_search_fields,
)
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
        from ..services.kb_service import KBService

        stale = KBService(config, db).stale_kbs(kb_name)
        if not stale:
            return
        kbs = ", ".join(stale)
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
            None,
            "--fields",
            help=(
                "Comma-separated fields per result (e.g. id,title,date); '*' for "
                "every field. Default: id, kb_name, entry_type, title, snippet and "
                "the score. Overrides PYRITE_SEARCH_FIELDS."
            ),
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

        Each result carries id, kb_name, entry_type, title, snippet and the
        score the mode produced (rank, rrf_score, similarity); `--fields '*'`
        (or PYRITE_SEARCH_FIELDS='*') returns every field, a named list
        returns those. `--fields` wins over the variable. The page-level keys
        below are in every response whatever the row carries.

        JSON output says what the page is worth: "match" (strong, weak or
        none) with "match_basis", and "similarity" on each result of the
        semantic leg. `match: none` means the KB does not contain an answer;
        say so, do not build an answer from these results. `weak` means verify
        the entries before using them. `weak` with no results means the search
        could not run in full or found nothing strong, not that the KB lacks an
        answer: read `match_basis` and `legs_run`, then try keyword mode or
        other words. `similarity` compares results of the same model; it is not
        a probability. The results are returned
        whatever the verdict. The contract is stated once, in the docstring of
        SearchService.search.
        """
        asked = bool(
            parse_fields_param(fields) or parse_fields_param(os.environ.get(SEARCH_FIELDS_ENV))
        )
        row_fields = resolve_search_fields(
            fields, os.environ.get(SEARCH_FIELDS_ENV), include_body=include_body
        )

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
            _warn_fields_not_applied(fields)
            _search_files(config, query, kb_name, entry_type, limit)
            return

        from ..services.kb_service import KBService
        from .context import open_index_db

        db = None
        try:
            db = open_index_db(config)

            kb_svc = KBService(config, db)
            if kb_svc.index_is_empty():
                # Status to stderr — stdout must stay clean for -f json callers.
                err_console.print("[yellow]Index is empty. Building index...[/yellow]")
                kb_svc.build_index()
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
            # The one assembler (#668): the rows, the verdict (`match`,
            # `match_basis`, `keyword_match`) and any `warnings`. Every key but
            # `results` goes into the JSON payload as it is.
            response = search_svc.search_response(
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
            results = response.pop("results")

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

            # `fields_list` is what the caller asked for (--fields or the variable):
            # it also shapes csv/markdown/rich. The lean default only trims the
            # rows, so markdown keeps its list and the verdict keeps its keys.
            fields_list = row_fields if asked else None
            if not results:
                if output_format != "rich":
                    # Machine formats get a valid empty-result payload, not a
                    # human "no results" line that would break json.load().
                    from ..formats import format_response

                    content, _ = format_response(
                        {"query": query, "count": 0, "results": [], **response},
                        output_format,
                        fields=fields_list,
                    )
                    typer.echo(content)
                else:
                    console.print("[yellow]No results found.[/yellow]")
                return

            # Apply field projection. One rule for every read surface (#193).
            if row_fields:
                missing = unmatched_search_fields(results, row_fields) if asked else []
                if missing:
                    typer.echo(
                        f"warning: no result carries: {', '.join(missing)} "
                        "(field names are case-sensitive; '*' returns every field)",
                        err=True,
                    )
                results = [project_fields(record, row_fields) for record in results]
            elif not include_body:
                for r in results:
                    r.pop("body", None)

            if output_format != "rich":
                from ..formats import format_response

                resp_data = {"query": query, "count": len(results), "results": results, **response}
                table_fields = fields_list
                if table_fields is None and output_format == "csv" and row_fields:
                    # The columns the lean row has: a score column no row of this
                    # mode carries would be an empty column that looks like data.
                    table_fields = [
                        f
                        for f in row_fields
                        if f not in IDENTITY_FIELDS and any(f in r for r in results)
                    ]
                content, _ = format_response(resp_data, output_format, fields=table_fields)
                typer.echo(content)
                return

            if fields_list:
                from ..formats.tabular import projected_rich_table

                console.print(projected_rich_table(results, fields_list, "Search Results"))
                return

            table = Table(title=f"Search Results ({len(results)})")
            table.add_column("KB", style="cyan", width=12)
            table.add_column("Type", style="green", width=10)
            table.add_column("Title", width=40)
            show_date = any(r.get("date") for r in results)
            if show_date:
                table.add_column("Date", width=10)
            table.add_column("Snippet", width=50)

            for r in results:
                date = r.get("date", "")[:10] if r.get("date") else ""
                raw_snippet = r.get("snippet", "") or ""
                snippet = (raw_snippet[:97] + "...") if len(raw_snippet) > 100 else raw_snippet
                raw_title = r.get("title", "") or ""
                title = (raw_title[:37] + "...") if len(raw_title) > 40 else raw_title
                cells = [r.get("kb_name", ""), r.get("entry_type", ""), title]
                table.add_row(*cells, *([date] if show_date else []), snippet)

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
            _warn_fields_not_applied(fields)
            _search_files(config, query, kb_name, entry_type, limit)
        finally:
            if db is not None:
                db.close()


def _warn_fields_not_applied(fields):
    """File search prints its own table (KB, Type, Title, ID) whatever the caller asked.

    Both ways of asking are named: `--fields`, and `PYRITE_SEARCH_FIELDS` when
    `--fields` is absent.
    """
    if parse_fields_param(fields):
        source = "--fields"
    elif parse_fields_param(os.environ.get(SEARCH_FIELDS_ENV)):
        source = SEARCH_FIELDS_ENV
    else:
        return
    typer.echo(f"{source} is not applied by file search; showing its own table.", err=True)


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

    from ..services.file_search import search_kb_files

    found = search_kb_files(kbs, query, entry_type, limit)
    results, unreadable, scanned = found.hits, found.unreadable, found.scanned

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

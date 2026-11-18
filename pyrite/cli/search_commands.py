"""
Search command for pyrite CLI.

Commands: search (with file-based fallback)
"""

import logging
import os
import re

import typer
from rich.markup import escape
from rich.table import Table

from ..config import load_config
from ..exceptions import (
    PyriteError,
    QuerySyntaxError,
    QueryTooLongError,
    StorageError,
    ValidationError,
)
from ..services.read_shaping import (
    IDENTITY_FIELDS,
    SEARCH_FIELDS_ENV,
    parse_fields_param,
    project_fields,
    resolve_search_fields,
    unmatched_search_fields,
)
from ..utils.errors import exit_unless_whole
from .context import get_config_with_registered_kbs, require_kb
from .output import cli_console, validate_output_format

logger = logging.getLogger(__name__)

console = cli_console()
# Status/progress chatter goes here so it never pollutes stdout in machine
# formats (-f json etc.), where stdout must be valid parseable output only.
err_console = cli_console(stderr=True)


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
        query: str | None = typer.Argument(
            None, help="Search query (FTS5 syntax supported); omit with --batch"
        ),
        kb_name: str | None = typer.Option(None, "--kb", "-k", help="Search specific KB"),
        entry_types: list[str] | None = typer.Option(
            None,
            "--type",
            "-t",
            help=("Filter by type; repeat to keep several types (e.g. --type note --type event)"),
        ),
        exclude_types: list[str] | None = typer.Option(
            None,
            "--exclude-type",
            help=("Remove this type from the results; repeat to exclude several"),
        ),
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
        batch: str | None = typer.Option(
            None,
            "--batch",
            help=(
                "Run many searches in one process: a JSON file (or '-' for stdin) "
                'holding a list of {"query": ..., "kb", "type", "mode", "limit", '
                '"fields", "status", "tag"} objects, at most 50. The command-line '
                "options are the defaults; a key in a query overrides them. Each "
                "result equals the single search's, in input order; a refused query "
                "fills its own slot with the error shape (exit 3 if some were refused, "
                "1 if all). Instead of QUERY."
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
        for flag, values in (("--type", entry_types), ("--exclude-type", exclude_types)):
            if any(not value.strip() for value in (values or [])):
                raise typer.BadParameter("value must not be blank", param_hint=flag)

        if batch is not None:
            _refuse_batch_usage(query, use_files, expand, output_format)
            _run_batch(
                batch,
                output_format=output_format,
                debug=debug,
                include_body=include_body,
                defaults={
                    "kb": kb_name,
                    "type": entry_types,
                    "tag": tag,
                    "status": status,
                    "limit": limit,
                    "mode": mode,
                    "fields": fields,
                },
                shared={
                    "exclude_types": exclude_types or None,
                    "date_from": date_from,
                    "date_to": date_to,
                    "fips": fips,
                    "state": state_filter,
                    "include_archived": include_archived,
                },
            )
            return
        if query is None:
            raise typer.BadParameter("Missing argument 'QUERY' (or use --batch FILE).")

        for name, value in (("query", query), ("kb", kb_name)):
            if value is not None:
                try:
                    value.encode("utf-8")
                except UnicodeEncodeError:
                    from ..utils.errors import cli_error

                    cli_error(
                        f"{name} holds text that is not valid UTF-8 (a lone surrogate, or bytes that were not UTF-8)",
                        output_format,
                        error_code="VALIDATION_FAILED",
                        retryable=False,
                    )

        asked = bool(
            parse_fields_param(fields) or parse_fields_param(os.environ.get(SEARCH_FIELDS_ENV))
        )
        row_fields = resolve_search_fields(
            fields, os.environ.get(SEARCH_FIELDS_ENV), include_body=include_body
        )

        config = load_config()
        if kb_name:
            config = get_config_with_registered_kbs(config, name=kb_name)

        # A search scoped to a name that is not a KB is refused, never an
        # empty result that looks like a query miss: the one resolver (#952).
        require_kb(config, kb_name, output_format=output_format)

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
            _search_files(config, query, kb_name, entry_types, exclude_types, limit)
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
                caller_wrote_query=True,  # the caller's own words, as written
                kb_name=kb_name,
                entry_types=entry_types or None,
                exclude_types=exclude_types or None,
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
            results, missing = _shape_rows(results, row_fields, asked, include_body)
            if missing:
                typer.echo(_missing_fields_warning(missing), err=True)

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
            table.add_column("KB", style="cyan")
            table.add_column("ID", style="dim", no_wrap=True, overflow="fold")
            table.add_column("Type", style="green")
            table.add_column("Title")
            show_date = any(r.get("date") for r in results)
            if show_date:
                table.add_column("Date")
            table.add_column("Snippet")

            for r in results:
                date = r.get("date", "")[:10] if r.get("date") else ""
                raw_snippet = r.get("snippet", "") or ""
                snippet = (raw_snippet[:97] + "...") if len(raw_snippet) > 100 else raw_snippet
                parts = re.split(r"(</?mark>)", snippet)
                snippet = "".join(
                    "[bold]" if p == "<mark>" else "[/bold]" if p == "</mark>" else escape(p)
                    for p in parts
                )
                raw_title = r.get("title", "") or ""
                title = (raw_title[:37] + "...") if len(raw_title) > 40 else raw_title
                title = escape(title)
                cells = [
                    r.get("kb_name", ""),
                    r.get("id", ""),
                    r.get("entry_type", ""),
                    title,
                ]
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
        except StorageError as e:
            from ..utils.errors import cli_error_from

            cli_error_from(e, output_format)
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
            if isinstance(e, ValidationError):
                # A refused request (an unknown --mode) is not an index
                # failure: falling back to file search answered a question the
                # caller did not ask, with exit 0 (#1006). Refuse on every
                # format, as the machine formats already did.
                from ..utils.errors import cli_error

                cli_error(str(e), output_format, error_code="VALIDATION_FAILED", retryable=False)
            console.print(f"[red]Search error ({type(e).__name__}):[/red] {e}")
            console.print("[dim]Falling back to file search...[/dim]")
            _warn_fields_not_applied(fields)
            _search_files(config, query, kb_name, entry_types, exclude_types, limit)
        finally:
            if db is not None:
                db.close()


def _shape_rows(results, row_fields, asked: bool, include_body: bool):
    """The rows as a single search prints them, and the requested fields no row carries.

    One rule for the single search and every slot of ``--batch``: a projection
    when there is one (#193), else the snippet-only default.
    """
    if row_fields:
        missing = unmatched_search_fields(results, row_fields) if asked else []
        return [project_fields(record, row_fields) for record in results], missing
    if not include_body:
        for r in results:
            r.pop("body", None)
    return results, []


def _missing_fields_warning(missing: list[str]) -> str:
    return (
        f"warning: no result carries: {', '.join(missing)} "
        "(field names are case-sensitive; '*' returns every field)"
    )


# --- search --batch (#1692) ---------------------------------------------------

#: The most queries one batch runs; the same cap as `batch-read` (#1692).
MAX_BATCH_QUERIES = 50

#: What a query object may carry. Everything else is refused in its slot.
_BATCH_KEYS = ("query", "kb", "type", "mode", "limit", "fields", "status", "tag")


def _refuse_batch_usage(query, use_files: bool, expand: bool, output_format: str) -> None:
    """Usage errors (exit 2): options that cannot go with ``--batch``."""
    if query is not None:
        raise typer.BadParameter(
            "QUERY and --batch are exclusive; put the query in the batch file."
        )
    if use_files:
        raise typer.BadParameter("--files cannot be combined with --batch.", param_hint="--files")
    if expand:
        raise typer.BadParameter(
            "--expand is not available with --batch: it makes one LLM call per query, "
            "the cost a batch exists to avoid.",
            param_hint="--expand",
        )
    if output_format not in ("json", "yaml", "rich"):
        raise typer.BadParameter(
            f"--batch prints json, yaml or rich, not {output_format}.", param_hint="--format"
        )


def _error_fields(exc: PyriteError) -> dict:
    """A refusal as the canonical error shape, the one a single search prints."""
    from ..exceptions import public_text, refusal_fields
    from ..utils.errors import build_error

    if isinstance(exc, QuerySyntaxError):
        return build_error(
            str(exc),
            exc.error_code,
            suggestion=(
                "quote tokens containing - : . yourself when your query "
                "uses AND/OR/NOT or phrase quotes"
            ),
        )
    if isinstance(exc, QueryTooLongError):
        return build_error(str(exc), exc.error_code, suggestion="shorten the query")
    return {
        **refusal_fields(exc),
        **build_error(
            public_text(exc, log=logger),
            exc.error_code,
            suggestion=getattr(exc, "suggestion", None),
            retryable=bool(getattr(exc, "retryable", False)),
        ),
    }


def _slot_spec(raw, defaults: dict) -> dict:
    """One query object merged over the command-line defaults, or a ValidationError."""
    if not isinstance(raw, dict):
        raise ValidationError(
            f'each batch item is an object like {{"query": "..."}}, not {type(raw).__name__}'
        )
    import json

    try:  # a lone surrogate ("\ud800", or stdin bytes under LC_ALL=C) reaches no search (#1728)
        json.dumps(raw, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        raise ValidationError(
            "a batch query holds text that is not valid UTF-8 (a lone surrogate, or "
            "bytes that were not UTF-8)"
        ) from None
    unknown = sorted(set(raw) - set(_BATCH_KEYS))
    if unknown:
        hint = (
            " (--expand is not available in a batch: it makes one LLM call per query)"
            if "expand" in unknown
            else ""
        )
        raise ValidationError(
            f"unknown key(s) in a batch query: {', '.join(unknown)}{hint}; "
            f"the keys are {', '.join(_BATCH_KEYS)}"
        )
    query = raw.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValidationError('each batch query needs a non-blank string "query"')
    for key in ("kb", "mode", "status", "tag"):
        if key in raw and not (raw[key] is None or isinstance(raw[key], str)):
            raise ValidationError(f'"{key}" must be a string')
    types = raw.get("type")
    if "type" in raw and not (
        types is None
        or (isinstance(types, list) and all(isinstance(t, str) and t.strip() for t in types))
    ):
        raise ValidationError('"type" must be a list of non-blank strings, like repeated --type')
    limit = raw.get("limit")
    if "limit" in raw and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise ValidationError('"limit" must be an integer of 1 or more')
    fields = raw.get("fields")
    if isinstance(fields, list) and all(isinstance(f, str) for f in fields):
        fields = ",".join(fields)
    if "fields" in raw and not (fields is None or isinstance(fields, str)):
        raise ValidationError('"fields" must be a comma-separated string or a list of strings')
    merged = {**defaults, **{k: v for k, v in raw.items() if k in defaults}}
    if "fields" in raw:
        merged["fields"] = fields
    merged["query"] = query
    return merged


def _read_batch(source: str, output_format: str) -> list:
    """The batch file's list of query objects; a whole-batch refusal exits 1."""
    import json
    import sys

    from ..exceptions import outside_text
    from ..utils.errors import cli_error
    from .context import input_file_or_exit

    try:
        if source == "-":
            text = sys.stdin.read()
        else:
            path = input_file_or_exit(source, "Batch file", output_format)
            text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        cli_error(
            "--batch input is not valid UTF-8 text",
            output_format,
            error_code="VALIDATION_FAILED",
            suggestion="save the file as UTF-8 JSON",
        )
    except OSError as e:
        cli_error(
            f"--batch input could not be read: {outside_text(e)}",
            output_format,
            error_code="VALIDATION_FAILED",
            suggestion="check the path and its permissions",
        )
    try:
        items = json.loads(text)
    except ValueError as e:
        cli_error(
            f"--batch input is not valid JSON: {e}",
            output_format,
            error_code="VALIDATION_FAILED",
            suggestion='a JSON list like [{"query": "falcon"}, {"query": "heron", "kb": "birds"}]',
        )
    if not isinstance(items, list):
        cli_error(
            f"--batch input must be a JSON list of query objects, not {type(items).__name__}",
            output_format,
            error_code="VALIDATION_FAILED",
            suggestion='a JSON list like [{"query": "falcon"}]',
        )
    if len(items) > MAX_BATCH_QUERIES:
        cli_error(
            f"--batch takes at most {MAX_BATCH_QUERIES} queries, got {len(items)}",
            output_format,
            error_code="VALIDATION_FAILED",
            suggestion=f"split the file into batches of {MAX_BATCH_QUERIES} or fewer, as batch-read does",
        )
    return list(items)


def _run_batch(
    source: str,
    *,
    output_format: str,
    debug: bool,
    include_body: bool,
    defaults: dict,
    shared: dict,
) -> None:
    """``pyrite search --batch``: one config load, one index, one SearchService.

    Each query goes through the same ``search_response`` and the same row
    shaping as a single search; a refusal fills its own slot (#1692).
    """
    from ..utils.errors import exit_unless_whole

    items = _read_batch(source, output_format)
    # The base settings are the ones a single search uses: with `-k`, it reads
    # them from get_config_with_registered_kbs, which returns this same config
    # (it only merges registered KBs into it), so `settings` and `search_mode`
    # do not differ per slot.
    config = load_config()
    env_fields = os.environ.get(SEARCH_FIELDS_ENV)

    from ..services.kb_service import KBService
    from ..services.search_service import SearchService, check_query_length
    from .context import open_index_db

    searches: list[dict] = []
    db = None
    try:
        db = open_index_db(config) if items else None
        if db is not None:
            kb_svc = KBService(config, db)
            if kb_svc.index_is_empty():
                err_console.print("[yellow]Index is empty. Building index...[/yellow]")
                kb_svc.build_index()
            else:
                # Once for the whole batch, over every KB: slots name different
                # KBs, so there is no single scope to narrow it to.
                _warn_if_stale(config, db, None)
            search_svc = SearchService(db, settings=config.settings)

        for position, raw in enumerate(items, start=1):
            echoed = raw.get("query") if isinstance(raw, dict) else None
            slot: dict = {"query": echoed if isinstance(echoed, str) else None}
            try:
                spec = _slot_spec(raw, defaults)
                kb_name = spec["kb"]
                slot_config = (
                    get_config_with_registered_kbs(config, name=kb_name) if kb_name else config
                )
                require_kb(slot_config, kb_name)
                check_query_length(spec["query"])

                asked = bool(parse_fields_param(spec["fields"]) or parse_fields_param(env_fields))
                row_fields = resolve_search_fields(
                    spec["fields"], env_fields, include_body=include_body
                )
                trace: dict | None = {} if debug else None
                warnings: list[str] = []
                response = search_svc.search_response(
                    query=spec["query"],
                    caller_wrote_query=True,
                    kb_name=kb_name,
                    entry_types=spec["type"] or None,
                    exclude_types=shared["exclude_types"],
                    tags=[spec["tag"]] if spec["tag"] else None,
                    date_from=shared["date_from"],
                    date_to=shared["date_to"],
                    limit=spec["limit"],
                    mode=spec["mode"] or config.settings.search_mode or "keyword",
                    expand=False,
                    include_archived=shared["include_archived"],
                    fips=shared["fips"],
                    state=shared["state"],
                    status=spec["status"],
                    trace=trace,
                    warnings=warnings,
                )
                results = response.pop("results")
                for warning in warnings:
                    err_console.print(
                        f"[yellow]warning:[/yellow] query {position}: {escape(warning)}",
                        soft_wrap=True,
                    )
                if trace is not None:
                    err_console.print(
                        f"[dim]trace:[/dim] query {position}: "
                        f"mode={trace.get('requested_mode')} actual={trace.get('actual_mode')} "
                        f"reason={trace.get('reason') or '-'} results={trace.get('result_count')} "
                        f"latency_ms={trace.get('latency_ms')} relaxed={trace.get('relaxed')}"
                    )
                results, missing = _shape_rows(results, row_fields, asked, include_body)
                if missing:
                    typer.echo(f"query {position}: {_missing_fields_warning(missing)}", err=True)
                slot = {
                    "query": spec["query"],
                    "count": len(results),
                    "results": results,
                    **response,
                }
            except PyriteError as e:
                # A refusal (syntax, length, unknown KB, a bad value) is its own
                # slot. Anything that is not a PyriteError is a fault, not a
                # refusal: it is not caught here and ends the command.
                slot = {**slot, "count": 0, "results": [], **_error_fields(e)}
            searches.append(slot)
    finally:
        if db is not None:
            db.close()

    failed = sum(1 for slot in searches if "error" in slot)
    _print_batch({"count": len(searches), "searches": searches}, output_format)
    exit_unless_whole(failed, len(searches) - failed)


def _safe_rich(val: object) -> str:
    if val is None:
        return ""
    return str(val).encode("utf-8", "backslashreplace").decode("utf-8")


def _print_batch(data: dict, output_format: str) -> None:
    if output_format != "rich":
        from ..formats import format_response

        typer.echo(format_response(data, output_format)[0])
        return
    for position, slot in enumerate(data["searches"], start=1):
        query_str = _safe_rich(slot.get("query"))
        console.print(f"[bold]{position}. {escape(query_str)}[/bold]")
        if "error" in slot:
            err_code = _safe_rich(slot.get("error_code", ""))
            err_msg = _safe_rich(slot.get("error", ""))
            console.print(f"  [red]{escape(err_code)}:[/red] {escape(err_msg)}")
            continue
        if not slot["results"]:
            console.print("  [yellow]No results found.[/yellow]")
        for r in slot["results"]:
            kb = _safe_rich(r.get("kb_name", ""))
            rid = _safe_rich(r.get("id", ""))
            title = _safe_rich(r.get("title", ""))
            console.print(f"  [cyan]{escape(kb)}[/cyan] [dim]{escape(rid)}[/dim] {escape(title)}")


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


def _search_files(
    config,
    query: str,
    kb_name: str | None,
    entry_types: list[str] | None,
    exclude_types: list[str] | None,
    limit: int,
):
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

    found = search_kb_files(
        kbs,
        query,
        None,
        limit,
        entry_types=entry_types or None,
        exclude_types=exclude_types or None,
    )
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
    table.add_column("ID", style="dim", no_wrap=True, overflow="fold")
    table.add_column("Type", style="green")
    table.add_column("Title")

    for kb_name, entry, _path in results:
        table.add_row(kb_name, entry.id, entry.entry_type, entry.title)

    console.print(table)
    exit_unless_whole(unreadable, scanned)

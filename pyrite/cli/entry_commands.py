"""
Entry CRUD commands for pyrite CLI.

Commands: get, create, add, update, delete, link
"""

from __future__ import annotations

import json as _json
import logging
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from ..exceptions import EntryNotFoundError, KBNotFoundError, PyriteError, ValidationError
from ..services.access_policy import UNSCOPED
from ..services.read_shaping import parse_fields_param, project_fields
from .context import cli_context

logger = logging.getLogger(__name__)
console = Console()


def _format_output(data: dict, fmt: str) -> str | None:
    from .output import format_output

    return format_output(data, fmt)


def _cli_error(message: str, output_format: str = "rich", error_code: str | None = None) -> None:
    """Print an error and exit. Thin wrapper over the shared cli_error helper
    (kept for the existing call sites' 3-arg signature)."""
    from ..utils.errors import cli_error

    cli_error(message, output_format, error_code=error_code or "ERROR")


def _refusal_exit(exc: ValidationError, output_format: str = "rich") -> None:
    """Report a write-pipeline refusal with its own code, and exit 1.

    `KBService` decides every create and update refusal (#378); the CLI only
    maps it: the error's own `error_code` (see the ValidationError subclasses
    in `pyrite.exceptions`), and a hint phrased for the CLI.
    """
    from ..utils.errors import cli_error

    suggestion = getattr(exc, "suggestion", None)
    if getattr(exc, "declared_types", None) is not None:
        suggestion = (
            "Use `pyrite kb schema show <kb>` to inspect the declared types, or pass "
            "--allow-undeclared to override."
        )
    cli_error(
        str(exc),
        output_format,
        error_code=getattr(exc, "error_code", None) or "VALIDATION_FAILED",
        suggestion=suggestion,
        retryable=False,
    )


def _parse_field_value(value: str) -> Any:
    """Parse a --field value, supporting JSON, integers, floats, and comma-separated lists.

    Parsing order:
      1. JSON (arrays/objects): ``[1,2]`` or ``{"k": "v"}``
      2. Integer: ``42``
      3. Float: ``3.14``
      4. Boolean: ``true``/``false``
      5. Comma-separated list (if commas present): ``a,b,c`` → ``["a","b","c"]``
      6. Plain string (fallback)
    """
    stripped = value.strip()

    # 1. JSON arrays or objects
    if stripped.startswith(("[", "{")):
        try:
            return _json.loads(stripped)
        except _json.JSONDecodeError:
            pass  # fall through to other parsers

    # 2. Integer
    try:
        return int(stripped)
    except ValueError:
        pass

    # 3. Float
    try:
        return float(stripped)
    except ValueError:
        pass

    # 4. Boolean
    if stripped.lower() == "true":
        return True
    if stripped.lower() == "false":
        return False

    # 5. Comma-separated list (must contain comma, items are stripped)
    if "," in stripped:
        return [item.strip() for item in stripped.split(",") if item.strip()]

    # 6. Plain string
    return value


def register_entry_commands(app: typer.Typer) -> None:
    """Register entry CRUD commands on the main app."""

    @app.command("get")
    def get_entry(
        entry_id: str = typer.Argument(..., help="Entry ID"),
        kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB to search in"),
        fields: str = typer.Option(None, "--fields", help="Comma-separated fields to return"),
        output_format: str = typer.Option(
            "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
        ),
    ):
        """Get a specific entry by ID."""
        with cli_context() as (config, db, svc):
            result = svc.get_entry(entry_id, kb_name=kb_name, readable_kbs=UNSCOPED)

            if not result:
                _cli_error(f"Entry '{entry_id}' not found", output_format, "NOT_FOUND")

            # Apply field projection. One rule for every read surface (#193):
            # `--fields` keeps the identity pair, like the REST routes and MCP.
            fields_list = parse_fields_param(fields)
            if fields_list:
                result = project_fields(result, fields_list)

            formatted = _format_output(result, output_format)
            if formatted is not None:
                typer.echo(formatted)
                return

            console.print(f"\n[bold cyan]{result.get('title', '')}[/bold cyan]")
            console.print(
                f"[dim]KB: {result.get('kb_name', '')} | Type: {result.get('entry_type', '')} | ID: {result.get('id', '')}[/dim]\n"
            )

            summary = result.get("summary")
            if summary:
                console.print(f"[italic]{summary}[/italic]\n")

            body = result.get("body", "")
            if body:
                console.print(body)

            sources = result.get("sources", [])
            if sources:
                console.print(f"\n[bold]Sources ({len(sources)}):[/bold]")
                for src in sources:
                    if isinstance(src, dict):
                        console.print(f"  • {src.get('title', '')}: {src.get('url', '')}")
                    else:
                        console.print(f"  • {src.title}: {src.url}")

    @app.command("create")
    def create_entry(
        kb_name: str = typer.Option(None, "--kb", "-k", help="Target knowledge base"),
        entry_type: str = typer.Option("note", "--type", "-t", help="Entry type"),
        title: str = typer.Option(None, "--title", help="Entry title"),
        body: str = typer.Option("", "--body", "-b", help="Entry body text"),
        tags: str = typer.Option("", "--tags", help="Comma-separated tags"),
        date: str = typer.Option("", "--date", "-d", help="Date (YYYY-MM-DD, for events)"),
        importance: int = typer.Option(5, "--importance", "-i", help="Importance (1-10)"),
        status: str = typer.Option("", "--status", help="Entry status (e.g., draft, confirmed)"),
        field: list[str] | None = typer.Option(
            None, "--field", "-f", help="Extra field as key=value"
        ),
        link: list[str] | None = typer.Option(
            None,
            "--link",
            "-l",
            help="Link to target entry (format: target-id or target-id:relation)",
        ),
        body_file: Path | None = typer.Option(None, "--body-file", help="Read body from file"),
        stdin: bool = typer.Option(False, "--stdin", help="Read body from stdin"),
        template: bool = typer.Option(False, "--template", help="Output entry skeleton to stdout"),
        allow_undeclared: bool = typer.Option(
            False,
            "--allow-undeclared",
            help="Allow entry types not declared in the KB's kb.yaml (the "
            "entry will be flagged by `pyrite index health`).",
        ),
    ):
        """Create a new entry in a knowledge base."""
        from ..schema import CORE_TYPES
        from ..utils.yaml import dump_yaml

        # Template mode: output a skeleton and exit
        if template:
            fm: dict = {
                "id": "",
                "type": entry_type,
                "title": title or "Your Title Here",
                "tags": [],
            }
            # Add type-specific fields as empty values
            type_info = CORE_TYPES.get(entry_type, {})
            for field_name, field_type in type_info.get("fields", {}).items():
                if field_name in ("tags", "links"):
                    continue
                if "list" in field_type:
                    fm[field_name] = []
                elif field_type == "int":
                    fm[field_name] = 0
                else:
                    fm[field_name] = ""
            typer.echo(f"---\n{dump_yaml(fm)}\n---\n\nYour content here.")
            return

        # Require title for non-template mode
        if title is None:
            _cli_error(
                "--title is required (unless using --template)",
                "rich",
                "VALIDATION_FAILED",
            )

        # Require kb for non-template mode
        if kb_name is None:
            _cli_error(
                "--kb is required (unless using --template)",
                "rich",
                "VALIDATION_FAILED",
            )

        # Body resolution: --stdin > --body-file > --body
        if stdin:
            import sys

            body = sys.stdin.read()
        elif body_file:
            if not body_file.exists():
                _cli_error(f"Body file not found: {body_file}", "rich", "NOT_FOUND")
            body = body_file.read_text(encoding="utf-8")

        # Extract YAML frontmatter from body content (--body-file or --stdin)
        _file_meta: dict = {}
        if body and body.startswith("---"):
            _fm_end = body.find("---", 3)
            if _fm_end > 0:
                from pyrite.utils.yaml import load_yaml

                _parsed = load_yaml(body[3:_fm_end])
                if _parsed and isinstance(_parsed, dict):
                    for _k, _v in _parsed.items():
                        if _k not in ("id", "type", "title"):
                            _file_meta[_k] = _v
                    body = body[_fm_end + 3 :].strip()

        extra: dict = {**_file_meta}
        if date:
            extra["date"] = date
        extra["importance"] = importance
        if status:
            extra["status"] = status
        if tags:
            extra["tags"] = [t.strip() for t in tags.split(",")]

        # Parse --field key=value pairs
        if field:
            for fv in field:
                if "=" not in fv:
                    _cli_error(
                        f"--field must be key=value, got '{fv}'",
                        "rich",
                        "VALIDATION_FAILED",
                    )
                k, v = fv.split("=", 1)
                extra[k] = _parse_field_value(v)

        # Every create decision -- the ADR-0034 truncated-body refusal (the
        # marker may arrive via --field or in the frontmatter of a
        # --body-file/--stdin read), the undeclared-type refusal (core types
        # not exempt, #197), schema validation, the exists check -- is made by
        # the service's write pipeline, the same one REST and MCP use (#378).
        spec = {**extra, "entry_type": entry_type, "title": title, "body": body}

        with cli_context() as (config, db, svc):
            try:
                written = svc.create(kb_name, spec, allow_undeclared=allow_undeclared)
            except ValidationError as e:
                _refusal_exit(e)
            except EntryNotFoundError as e:
                _cli_error(str(e), "rich", "NOT_FOUND")
            except KBNotFoundError as e:
                _cli_error(str(e), "rich", "KB_NOT_FOUND")
            except (PyriteError, ValueError) as e:
                _cli_error(str(e), "rich")
            entry = written.entry
            console.print(f"[green]Created:[/green] {entry.id}")
            console.print(f"[dim]Type: {entry.entry_type}[/dim]")
            for warning in written.warnings:
                console.print("[yellow]Warning:[/yellow]", _json.dumps(warning, default=str))

            # Add links after creation
            if link:
                for link_spec in link:
                    if ":" in link_spec:
                        target, relation = link_spec.split(":", 1)
                    else:
                        target, relation = link_spec, "related_to"
                    try:
                        svc.add_link(entry.id, kb_name, target.strip(), relation.strip())
                        console.print(f"  [dim]Linked to {target} ({relation})[/dim]")
                    except (PyriteError, ValueError) as e:
                        console.print(f"  [yellow]Link failed:[/yellow] {e}")

    @app.command("add")
    def add_entry(
        file_path: Path = typer.Argument(..., help="Path to markdown file with frontmatter"),
        kb_name: str = typer.Option(..., "--kb", "-k", help="Target knowledge base"),
        validate_only: bool = typer.Option(
            False, "--validate-only", help="Validate without saving"
        ),
        allow_undeclared: bool = typer.Option(
            False,
            "--allow-undeclared",
            help="Allow entry types not declared in the KB's kb.yaml (the "
            "entry will be flagged by `pyrite index health`).",
        ),
    ):
        """Add a markdown file to a knowledge base.

        Reads a markdown file with YAML frontmatter, validates it, copies it to the
        correct KB subdirectory, and indexes it.

        Frontmatter must include 'type' and 'title'. If 'id' is missing, it is
        generated from the title.
        """
        if not file_path.exists():
            _cli_error(f"File not found: {file_path}", "rich", "NOT_FOUND")

        with cli_context() as (config, db, svc):
            try:
                entry, result = svc.add_entry_from_file(
                    kb_name,
                    file_path,
                    validate_only=validate_only,
                    allow_undeclared=allow_undeclared,
                )

                # Show warnings
                for warning in result.get("warnings", []):
                    console.print(f"[yellow]Warning:[/yellow] {warning}")

                if validate_only:
                    errors = result.get("errors", [])
                    if errors:
                        _cli_error(
                            "Validation failed: " + "; ".join(str(e) for e in errors),
                            "rich",
                            "VALIDATION_FAILED",
                        )
                    console.print(f"[green]Valid:[/green] {entry.id}")
                    console.print(f"[dim]Type: {entry.entry_type}[/dim]")
                else:
                    console.print(f"[green]Added:[/green] {entry.id}")
                    console.print(f"[dim]Type: {entry.entry_type}[/dim]")
            except ValidationError as e:
                _refusal_exit(e)
            except PyriteError as e:
                _cli_error(str(e), "rich", "ERROR")

    @app.command("update")
    def update_entry(
        entry_id: str = typer.Argument(..., help="Entry ID to update"),
        kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
        title: str = typer.Option(None, "--title", help="New title"),
        body: str = typer.Option(None, "--body", "-b", help="New body text"),
        body_file: Path | None = typer.Option(None, "--body-file", help="Read body from file"),
        stdin_flag: bool = typer.Option(False, "--stdin", help="Read body from stdin"),
        tags: str = typer.Option(None, "--tags", help="New comma-separated tags"),
        importance: int = typer.Option(None, "--importance", "-i", help="New importance (1-10)"),
        lifecycle: str = typer.Option(
            None, "--lifecycle", help="Lifecycle state: active or archived"
        ),
        field: list[str] | None = typer.Option(
            None, "--field", "-f", help="Extra field as key=value"
        ),
        output_format: str = typer.Option(
            "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
        ),
    ):
        """Update an existing entry."""
        # Body resolution: --stdin > --body-file > --body
        if stdin_flag:
            import sys

            body = sys.stdin.read()
        elif body_file:
            if not body_file.exists():
                _cli_error(f"Body file not found: {body_file}", output_format, "NOT_FOUND")
            body = body_file.read_text(encoding="utf-8")

        updates: dict[str, Any] = {}
        if title is not None:
            updates["title"] = title
        if body is not None:
            updates["body"] = body
        if importance is not None:
            updates["importance"] = importance
        if lifecycle is not None:
            updates["lifecycle"] = lifecycle
        if tags is not None:
            updates["tags"] = [t.strip() for t in tags.split(",")]

        # Parse --field key=value pairs
        if field:
            for fv in field:
                if "=" not in fv:
                    _cli_error(
                        f"--field must be key=value, got '{fv}'",
                        output_format,
                        "VALIDATION_FAILED",
                    )
                k, v = fv.split("=", 1)
                # One parser for `-f` on both write commands: JSON arrays and
                # objects, ints, floats, booleans and comma-separated lists.
                # This path used to coerce ints only, so `-f tags=alpha,beta`
                # stored the raw string and the reader then iterated its
                # characters (#231).
                updates[k] = _parse_field_value(v)

        # ADR-0034 rule 2 and schema validation are the service's (#378):
        # `updates` goes through as given, marker keys included.
        with cli_context() as (config, db, svc):
            try:
                entry = svc.update(entry_id, kb_name, updates).entry
            except ValidationError as e:
                _refusal_exit(e, output_format)
            except EntryNotFoundError as e:
                _cli_error(str(e), output_format, "NOT_FOUND")
            except KBNotFoundError as e:
                _cli_error(str(e), output_format, "KB_NOT_FOUND")
            except (PyriteError, ValueError) as e:
                _cli_error(str(e), output_format)
            if output_format != "rich":
                typer.echo(_json.dumps({"updated": True, "entry_id": entry.id}))
            else:
                console.print(f"[green]Updated:[/green] {entry.id}")

    @app.command("delete")
    def delete_entry(
        entry_id: str = typer.Argument(..., help="Entry ID to delete"),
        kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
        force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    ):
        """Delete an entry from a knowledge base."""
        if not force:
            if not typer.confirm(f"Delete entry '{entry_id}' from KB '{kb_name}'?"):
                raise typer.Abort()

        with cli_context() as (config, db, svc):
            try:
                deleted = svc.delete_entry(entry_id, kb_name)
                if not deleted:
                    _cli_error(f"Entry '{entry_id}' not found", "rich", "NOT_FOUND")
                console.print(f"[green]Deleted:[/green] {entry_id}")
            except EntryNotFoundError as e:
                _cli_error(str(e), "rich", "NOT_FOUND")
            except KBNotFoundError as e:
                _cli_error(str(e), "rich", "KB_NOT_FOUND")
            except (PyriteError, ValueError) as e:
                _cli_error(str(e), "rich", "ERROR")

    @app.command("rename")
    def rename_entry(
        old_id: str = typer.Argument(..., help="Current entry ID"),
        new_id: str = typer.Argument(..., help="Target entry ID"),
        kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
        update_links: bool = typer.Option(
            True,
            "--update-links/--no-update-links",
            help="Rewrite [[old_id]] / [[old_id|alias]] wikilinks in this KB",
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Show the rename + link-rewrite plan without executing",
        ),
        output_format: str = typer.Option("json", "--format", help="Output format: json or rich"),
    ):
        """Rename an entry, rewrite frontmatter id, and update wikilinks.

        Same-KB scope only in this release. Cross-KB wikilink rewrite
        and redirect-stub creation are tracked as r1700 follow-ups.
        """
        with cli_context() as (config, db, svc):
            try:
                result = svc.rename_entry(
                    old_id,
                    new_id,
                    kb_name,
                    update_links=update_links,
                    dry_run=dry_run,
                )
            except (PyriteError, ValueError) as e:
                _cli_error(str(e), output_format)
                return

        if output_format == "rich":
            verb = "Would rename" if dry_run else "Renamed"
            console.print(f"[green]{verb}:[/green] {old_id} -> {new_id}")
            if update_links:
                console.print(
                    f"  Wikilink rewrites: "
                    f"{result['links_rewritten']} link(s) in "
                    f"{result['files_rewritten']} file(s)"
                )
            if dry_run:
                console.print("[yellow]Dry run — no files modified.[/yellow]")
        else:
            typer.echo(_json.dumps(result))

    @app.command("link")
    def link_entries(
        source: str = typer.Argument(..., help="Source entry ID"),
        target: str = typer.Argument(..., help="Target entry ID"),
        kb_name: str = typer.Option(..., "--kb", "-k", help="Source KB name"),
        relation: str = typer.Option("related_to", "--relation", "-r", help="Relationship type"),
        target_kb: str | None = typer.Option(None, "--target-kb", help="Target KB (if different)"),
        note: str = typer.Option("", "--note", "-n", help="Note about the link"),
        bidirectional: bool = typer.Option(False, "--bidi", help="Create link in both directions"),
    ):
        """Create a link between two entries."""
        from rich.markup import escape

        def _report(created: bool, src: str, rel: str, tgt: str, kb: str) -> None:
            # `--[implements]-->` puts the relation in literal brackets, which
            # Rich reads as a style tag (e.g. `[implements]`) and swallows --
            # escaping just the relation is not enough, since the brackets
            # themselves are the markup delimiters; escape the whole dynamic
            # segment so the relation survives in the confirmation (#396).
            verb = "[green]Linked:[/green]" if created else "[yellow]Already linked:[/yellow]"
            arrow = escape(f"{src} --[{rel}]--> {tgt} (in {kb})")
            console.print(f"{verb} {arrow}")

        with cli_context() as (config, db, svc):
            try:
                result = svc.add_link(
                    source, kb_name, target, relation, target_kb=target_kb, note=note
                )
                tkb = target_kb or kb_name
                # `result["relation"]` is what is actually on disk, not the
                # caller's own `relation` argument -- on a no-op (already
                # linked) those can differ in spelling while still matching
                # under the service's normalisation (round-2 cold read): the
                # confirmation must name what a `grep` of the file would find.
                _report(result["created"], source, result["relation"], target, tkb)

                if bidirectional:
                    from ..schema import get_inverse_relation

                    inverse = get_inverse_relation(relation)
                    inv_result = svc.add_link(
                        target, tkb, source, inverse, target_kb=kb_name, note=note
                    )
                    _report(inv_result["created"], target, inv_result["relation"], source, kb_name)
            except EntryNotFoundError as e:
                _cli_error(str(e), "rich", "NOT_FOUND")
            except KBNotFoundError as e:
                _cli_error(str(e), "rich", "KB_NOT_FOUND")
            except (PyriteError, ValueError) as e:
                _cli_error(str(e), "rich", "ERROR")

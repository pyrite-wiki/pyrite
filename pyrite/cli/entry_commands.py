"""
Entry CRUD commands for pyrite CLI.

Commands: get, create, add, update, delete, rename, link

`get` (markdown and csv are taught for it) and `delete` (it asks) are still off
the contract; their docstrings say why.

On the outcome contract (ADR-0046): each command declares the shared
``--format`` option and returns an ``Outcome`` -- what happened, the data the
machine formats print, and a renderer for ``--format rich`` -- or raises a
``PyriteError``. ``PyriteCLIGroup.invoke`` prints the result and sets the exit
code (0 done, 3 partial, 1 nothing or refused), and it is the only way out of
these commands. The write pipeline (``KBService``) decides every refusal; this
module raises what it raised and the root maps the class's own code.
"""

from __future__ import annotations

import json as _json
import logging
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from ..exceptions import (
    EntryNotFoundError,
    KBNotFoundError,
    PyriteError,
    UndeclaredTypeError,
    ValidationError,
)
from ..services.access_policy import UNSCOPED
from ..services.read_shaping import parse_cli_fields, project_fields
from ..utils.errors import cli_error
from ..utils.frontmatter import (
    Frontmatter,
    Malformed,
    Unsupported,
    Unterminated,
    describe,
    split_frontmatter,
)
from .context import cli_context
from .outcome import Outcome
from .output import OUTPUT_FORMAT, validate_output_format

logger = logging.getLogger(__name__)
console = Console()


def _format_output(data: dict, fmt: str, **kwargs) -> str | None:
    from .output import format_output

    return format_output(data, fmt, **kwargs)


def _body_frontmatter(body: str | None) -> tuple[dict, str | None]:
    """(fields from the body's own frontmatter, the body without it).

    A body that opens a frontmatter block and cannot close it is refused (VALIDATION_FAILED),
    not stored with its YAML as prose; so is a first line that starts with ``---``
    and is not a valid opener. A body with no frontmatter is returned as is, and
    a leading ``{`` is just body text here (a body is not a file)."""
    if not body:
        return {}, body
    split = split_frontmatter(body)
    if isinstance(split, (Unterminated, Malformed)) or (
        isinstance(split, Unsupported) and split.format == "toml"
    ):
        raise ValidationError(f"Cannot read the frontmatter in the body: {describe(split)}")
    if not isinstance(split, Frontmatter):
        return {}, body
    from pyrite.utils.yaml import load_yaml

    parsed = load_yaml(split.text)
    if not (parsed and isinstance(parsed, dict)):
        return {}, body
    return {k: v for k, v in parsed.items() if k not in ("id", "type", "title")}, split.body


def _with_cli_hint(exc: ValidationError) -> ValidationError:
    """A write-pipeline refusal, with the hint phrased for the CLI.

    `KBService` decides every create and update refusal (#378) and the root
    prints the class's own `error_code`; the one thing the CLI adds is that an
    undeclared type is answered with the command that lists the declared ones.
    """
    if isinstance(exc, UndeclaredTypeError):
        exc.suggestion = (
            "Use `pyrite kb schema show <kb>` to inspect the declared types, or pass "
            "--allow-undeclared to override."
        )
    return exc


def _read_stdin() -> str:
    import sys

    return sys.stdin.read()


def _field_type_for(
    config: Any, kb_name: str, entry_type: str | None, field_name: str
) -> str | None:
    """Return the declared type for a field, including core-type metadata."""
    if not entry_type:
        return None

    kb_config = config.get_kb(kb_name)
    if kb_config is not None:
        type_schema = kb_config.kb_schema.types.get(entry_type)
        if type_schema is not None:
            field_schema = type_schema.fields.get(field_name)
            if field_schema is not None:
                return field_schema.field_type

    from ..schema import CORE_TYPES
    from ..schema.field_aliases import field_aliases

    core_fields = CORE_TYPES.get(entry_type, {}).get("fields", {})
    # `participants` (the attribute name `create` accepts) is an alias of the
    # file key `actors`: it parses as the target does.
    name = field_aliases(entry_type).get(field_name, field_name) if entry_type else field_name
    return core_fields.get(field_name) or core_fields.get(name)


def _parse_field_value(value: str, field_type: str | None = None) -> Any:
    """Parse a ``--field`` value using its declared type when available.

    Parsing order:
      1. JSON (strings, arrays, and objects): ``"a, b"``, ``[1,2]``, or ``{"k": "v"}``
      2. Integer: ``42``
      3. Float: ``3.14``
      4. Boolean: ``true``/``false``
      5. Comma-separated list for list-like fields: ``a,b,c`` → ``["a","b","c"]``
      6. Plain string (fallback)
    """
    stripped = value.strip()

    # 1. JSON strings, arrays, or objects
    if stripped.startswith(('"', "[", "{")):
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

    # 5. Comma-separated list only when the schema declares a list-like field.
    is_list_field = field_type in {"list", "multi-select", "tags"} or (
        isinstance(field_type, str) and field_type.startswith("list[")
    )
    if is_list_field and "," in stripped:
        return [item.strip() for item in stripped.split(",") if item.strip()]

    # 6. Plain string
    return value


def _print_warnings(console: Console, warnings: list) -> None:
    for warning in warnings:
        console.print("[yellow]Warning:[/yellow]", _json.dumps(warning, default=str))


def _report_link(console: Console, link: dict) -> None:
    from rich.markup import escape

    # `--[implements]-->` puts the relation in literal brackets, which Rich
    # reads as a style tag (e.g. `[implements]`) and swallows -- escaping just
    # the relation is not enough, since the brackets themselves are the markup
    # delimiters; escape the whole dynamic segment so the relation survives in
    # the confirmation (#396).
    verb = "[green]Linked:[/green]" if link["created"] else "[yellow]Already linked:[/yellow]"
    arrow = escape(
        f"{link['source']} --[{link['relation']}]--> {link['target']} (in {link['target_kb']})"
    )
    console.print(f"{verb} {arrow}")


def register_entry_commands(app: typer.Typer) -> None:
    """Register entry CRUD commands on the main app."""

    @app.command("get")
    def get_entry(
        entry_id: str = typer.Argument(..., help="Entry ID"),
        kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB to search in"),
        fields: str = typer.Option(None, "--fields", help="Comma-separated fields to return"),
        output_format: str = typer.Option(
            "json",
            "--format",
            callback=validate_output_format,
            help="Output format: json, rich, markdown, csv, yaml",
        ),
    ):
        """Get a specific entry by ID."""
        # Not on the outcome contract yet: `--format markdown` and `csv` are taught for
        # this command and the shared option offers json, yaml and rich only. It moves
        # with `pyrite-read get` (ADR-0046, slice 3).
        with cli_context() as (config, db, svc):
            result = svc.get_entry(entry_id, kb_name=kb_name, readable_kbs=UNSCOPED)

            if not result:
                cli_error(f"Entry '{entry_id}' not found", output_format, error_code="NOT_FOUND")

            # Apply field projection. One rule for every read surface (#193):
            # `--fields` keeps the identity pair, like the REST routes and MCP.
            fields_list = parse_cli_fields(fields)
            if fields_list:
                result = project_fields(result, fields_list)

            formatted = _format_output(result, output_format, fields=fields_list, single_entry=True)
            if formatted is not None:
                typer.echo(formatted)
                return

            if fields_list:
                from ..formats.tabular import projected_rich_table

                console.print(projected_rich_table([result], fields_list, "Entry"))
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
        template: bool = typer.Option(False, "--template", help="Output entry skeleton"),
        allow_undeclared: bool = typer.Option(
            False,
            "--allow-undeclared",
            help="Allow entry types not declared in the KB's kb.yaml (the "
            "entry will be flagged by `pyrite index health`).",
        ),
        output_format: str = OUTPUT_FORMAT,
    ) -> Outcome:
        """Create a new entry in a knowledge base."""
        from ..schema import CORE_TYPES
        from ..utils.yaml import dump_yaml

        # Template mode: the skeleton is the result
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
            skeleton = f"---\n{dump_yaml(fm)}\n---\n\nYour content here."

            def raw(console: Console, data: dict) -> None:
                # The skeleton is a file's text: no markup, no wrapping.
                console.print(data["template"], markup=False, highlight=False, soft_wrap=True)

            return Outcome.done({"template": skeleton, "entry_type": entry_type}, rich=raw)

        # Require title for non-template mode
        if title is None:
            raise ValidationError("--title is required (unless using --template)")

        # Require kb for non-template mode
        if kb_name is None:
            raise ValidationError("--kb is required (unless using --template)")

        # Body resolution: --stdin > --body-file > --body
        if stdin:
            body = _read_stdin()
        elif body_file:
            if not body_file.exists():
                raise EntryNotFoundError(f"Body file not found: {body_file}")
            body = body_file.read_text(encoding="utf-8")

        # Extract YAML frontmatter from body content (--body-file or --stdin)
        _file_meta, body = _body_frontmatter(body)

        extra: dict = {**_file_meta}
        if date:
            extra["date"] = date
        extra["importance"] = importance
        if status:
            extra["status"] = status
        if tags:
            extra["tags"] = [t.strip() for t in tags.split(",")]

        with cli_context() as (config, db, svc):
            # Parse --field key=value pairs with the current KB schema.
            if field:
                for fv in field:
                    if "=" not in fv:
                        raise ValidationError(f"--field must be key=value, got '{fv}'")
                    k, v = fv.split("=", 1)
                    extra[k] = _parse_field_value(
                        v, _field_type_for(config, kb_name, entry_type, k)
                    )

            # Every create decision -- the ADR-0034 truncated-body refusal (the
            # marker may arrive via --field or in the frontmatter of a
            # --body-file/--stdin read), the undeclared-type refusal (core types
            # not exempt, #197), schema validation, the exists check -- is made by
            # the service's write pipeline, the same one REST and MCP use (#378).
            spec = {**extra, "entry_type": entry_type, "title": title, "body": body}
            try:
                written = svc.create(kb_name, spec, allow_undeclared=allow_undeclared)
            except ValidationError as e:
                raise _with_cli_hint(e) from None
            entry = written.entry

            # Add links after creation
            linked: list[dict] = []
            failed_links: list[dict] = []
            for link_spec in link or []:
                if ":" in link_spec:
                    target, relation = link_spec.split(":", 1)
                else:
                    target, relation = link_spec, "related_to"
                target, relation = target.strip(), relation.strip()
                try:
                    svc.add_link(entry.id, kb_name, target, relation)
                    linked.append({"target": target, "relation": relation})
                except (PyriteError, ValueError) as e:
                    # The entry is kept (maintainer, 2026-10-02), so a retry of the
                    # create answers ENTRY_EXISTS: say what finishes the job.
                    failed_links.append(
                        {
                            "target": target,
                            "relation": relation,
                            "error": str(e),
                            "finish_up": f"pyrite link {entry.id} {target} -r {relation} -k {kb_name}",
                        }
                    )

        data: dict[str, Any] = {
            "created": True,
            "entry_id": entry.id,
            "entry_type": entry.entry_type,
        }
        if written.warnings:
            data["warnings"] = written.warnings
        if link:
            data["linked"] = linked
            data["failed_links"] = failed_links
        total_links = len(link or [])

        def rich(console: Console, data: dict) -> None:
            console.print(f"[green]Created:[/green] {data['entry_id']}")
            console.print(f"[dim]Type: {data['entry_type']}[/dim]")
            _print_warnings(console, data.get("warnings", []))
            for done_link in data.get("linked", []):
                console.print(
                    f"  [dim]Linked to {done_link['target']} ({done_link['relation']})[/dim]"
                )
            for failed in data.get("failed_links", []):
                console.print(f"  [yellow]Link failed:[/yellow] {failed['error']}")
            if data.get("failed_links"):
                console.print(
                    f"[yellow]Entry '{data['entry_id']}' was created; "
                    f"{len(data['failed_links'])} of {total_links} link(s) failed.[/yellow]"
                )
                for failed in data["failed_links"]:
                    console.print(
                        f"  add it once '{failed['target']}' exists: {failed['finish_up']}",
                        markup=False,
                        highlight=False,
                    )

        return (Outcome.partial if failed_links else Outcome.done)(data, rich=rich)

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
        output_format: str = OUTPUT_FORMAT,
    ) -> Outcome:
        """Add a markdown file to a knowledge base.

        Reads a markdown file with YAML frontmatter, validates it, copies it to the
        correct KB subdirectory, and indexes it.

        Frontmatter must include 'type' and 'title'. If 'id' is missing, it is
        generated from the title.
        """
        if not file_path.exists():
            raise EntryNotFoundError(f"File not found: {file_path}")

        with cli_context() as (config, db, svc):
            try:
                entry, result = svc.add_entry_from_file(
                    kb_name,
                    file_path,
                    validate_only=validate_only,
                    allow_undeclared=allow_undeclared,
                )
            except ValidationError as e:
                raise _with_cli_hint(e) from None

        errors = result.get("errors", [])
        if validate_only and errors:
            raise ValidationError("Validation failed: " + "; ".join(str(e) for e in errors))

        warnings = list(result.get("warnings", []))
        data: dict[str, Any] = {
            ("valid" if validate_only else "added"): True,
            "entry_id": entry.id,
            "entry_type": entry.entry_type,
        }
        if warnings:
            data["warnings"] = warnings

        def rich(console: Console, data: dict) -> None:
            for warning in data.get("warnings", []):
                console.print(f"[yellow]Warning:[/yellow] {warning}")
            label = "Valid" if data.get("valid") else "Added"
            console.print(f"[green]{label}:[/green] {data['entry_id']}")
            console.print(f"[dim]Type: {data['entry_type']}[/dim]")

        return Outcome.done(data, rich=rich)

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
        output_format: str = OUTPUT_FORMAT,
    ) -> Outcome:
        """Update an existing entry."""
        # Body resolution: --stdin > --body-file > --body
        if stdin_flag:
            body = _read_stdin()
        elif body_file:
            if not body_file.exists():
                raise EntryNotFoundError(f"Body file not found: {body_file}")
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

        with cli_context() as (config, db, svc):
            current = svc.get_entry(entry_id, kb_name=kb_name, readable_kbs=UNSCOPED)
            entry_type = current.get("entry_type") if current is not None else None

            # Parse --field key=value pairs with the existing entry's schema.
            if field:
                for fv in field:
                    if "=" not in fv:
                        raise ValidationError(f"--field must be key=value, got '{fv}'")
                    k, v = fv.split("=", 1)
                    updates[k] = _parse_field_value(
                        v, _field_type_for(config, kb_name, entry_type, k)
                    )

            # ADR-0034 rule 2 and schema validation are the service's (#378):
            # `updates` goes through as given, marker keys included.
            try:
                written = svc.update(entry_id, kb_name, updates)
            except ValidationError as e:
                raise _with_cli_hint(e) from None
        entry = written.entry

        # The write's schema warnings -- e.g. an off-list value already on
        # disk that the update kept (#47, #555) -- the way REST and MCP
        # already return them.
        data: dict[str, Any] = {"updated": True, "entry_id": entry.id}
        if written.warnings:
            data["warnings"] = written.warnings

        def rich(console: Console, data: dict) -> None:
            console.print(f"[green]Updated:[/green] {data['entry_id']}")
            _print_warnings(console, data.get("warnings", []))

        return Outcome.done(data, rich=rich)

    @app.command("delete")
    def delete_entry(
        entry_id: str = typer.Argument(..., help="Entry ID to delete"),
        kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
        force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    ):
        """Delete an entry from a knowledge base."""
        # Asks before it deletes, so it stays off the outcome contract: a command on
        # the contract does not prompt (ADR-0046, amendment; the prompt rule is the
        # maintainer's open question).
        if not force:
            if not typer.confirm(f"Delete entry '{entry_id}' from KB '{kb_name}'?"):
                raise typer.Abort()

        with cli_context() as (config, db, svc):
            try:
                deleted = svc.delete_entry(entry_id, kb_name)
                if not deleted:
                    cli_error(f"Entry '{entry_id}' not found", "rich", error_code="NOT_FOUND")
                console.print(f"[green]Deleted:[/green] {entry_id}")
            except EntryNotFoundError as e:
                cli_error(str(e), "rich", error_code="NOT_FOUND")
            except KBNotFoundError as e:
                cli_error(str(e), "rich", error_code="KB_NOT_FOUND")
            except (PyriteError, ValueError) as e:
                cli_error(str(e), "rich", error_code="ERROR")

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
        output_format: str = OUTPUT_FORMAT,
    ) -> Outcome:
        """Rename an entry, rewrite frontmatter id, and update wikilinks.

        Same-KB scope only in this release. Cross-KB wikilink rewrite
        and redirect-stub creation are tracked as r1700 follow-ups.
        """
        with cli_context() as (config, db, svc):
            result = svc.rename_entry(
                old_id,
                new_id,
                kb_name,
                update_links=update_links,
                dry_run=dry_run,
            )

        def rich(console: Console, result: dict) -> None:
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

        return Outcome.done(result, rich=rich)

    @app.command("link")
    def link_entries(
        source: str = typer.Argument(..., help="Source entry ID"),
        target: str = typer.Argument(..., help="Target entry ID"),
        kb_name: str = typer.Option(..., "--kb", "-k", help="Source KB name"),
        relation: str = typer.Option("related_to", "--relation", "-r", help="Relationship type"),
        target_kb: str | None = typer.Option(None, "--target-kb", help="Target KB (if different)"),
        note: str = typer.Option("", "--note", "-n", help="Note about the link"),
        bidirectional: bool = typer.Option(False, "--bidi", help="Create link in both directions"),
        output_format: str = OUTPUT_FORMAT,
    ) -> Outcome:
        """Create a link between two entries."""
        from rich.markup import escape

        links: list[dict] = []
        failed: list[dict] = []

        with cli_context() as (config, db, svc):
            result = svc.add_link(source, kb_name, target, relation, target_kb=target_kb, note=note)
            tkb = target_kb or kb_name
            # `result["relation"]` is what is actually on disk, not the
            # caller's own `relation` argument -- on a no-op (already
            # linked) those can differ in spelling while still matching
            # under the service's normalisation (round-2 cold read): the
            # confirmation must name what a `grep` of the file would find.
            links.append(
                {
                    "created": result["created"],
                    "source": source,
                    "relation": result["relation"],
                    "target": target,
                    "target_kb": tkb,
                }
            )

            if bidirectional:
                from ..schema import get_inverse_relation
                from ..schema.provenance import known_inverse_relation

                # An unknown relation has no known inverse: write the
                # vague-but-true related_to on the target, never the
                # forward relation reversed (#527).
                inverse = get_inverse_relation(relation)
                if known_inverse_relation(relation) is None:
                    # A notice, not the result: stderr, so stdout holds
                    # only the result (#590). The relation is free
                    # text and may hold brackets; escape as `_report_link` does.
                    notice = escape(
                        f"No inverse is known for '{relation}'; writing '{inverse}' on {target}."
                    )
                    Console(stderr=True).print(f"[dim]{notice}[/dim]", soft_wrap=True)
                try:
                    inv_result = svc.add_link(
                        target, tkb, source, inverse, target_kb=kb_name, note=note
                    )
                except (PyriteError, ValueError) as inv_err:
                    # The forward link is on disk: part of --bidi happened (#526).
                    failed.append(
                        {
                            "source": target,
                            "relation": inverse,
                            "target": source,
                            "target_kb": kb_name,
                            "error": f"The forward link was written, but the inverse ({target} "
                            f"--[{inverse}]--> {source}) failed: {inv_err}",
                        }
                    )
                else:
                    links.append(
                        {
                            "created": inv_result["created"],
                            "source": target,
                            "relation": inv_result["relation"],
                            "target": source,
                            "target_kb": kb_name,
                        }
                    )

        def rich(console: Console, data: dict) -> None:
            for link in data["links"]:
                _report_link(console, link)
            for problem in data.get("failed", []):
                console.print(escape(problem["error"]), soft_wrap=True, style="red")

        data: dict[str, Any] = {"links": links}
        if failed:
            data["failed"] = failed
        return (Outcome.partial if failed else Outcome.done)(data, rich=rich)

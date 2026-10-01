"""Schema versioning CLI commands."""

import logging
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from ..utils.errors import cli_error
from .context import cli_context, get_config_with_registered_kbs

logger = logging.getLogger(__name__)
console = Console()


# =========================================================================
# Schema validation logic (stateless, testable)
# =========================================================================

# Protocol fields and their expected Python types
_PROTOCOL_FIELD_TYPES: dict[str, type] = {
    "priority": int,
    "date": str,
    "start_date": str,
    "end_date": str,
    "due_date": str,
    "status": str,
    "assignee": str,
    "assigned_at": str,
    "location": str,
    "coordinates": str,
}


def _parse_frontmatter(file_path: Path) -> tuple[dict[str, Any], str, list[dict[str, str]]]:
    """Parse a markdown file's YAML frontmatter.

    Returns (frontmatter_dict, body, errors).
    Errors are dicts with keys: file, check, message, severity.
    """
    errors: list[dict[str, str]] = []
    path_str = str(file_path)

    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        errors.append(
            {
                "file": path_str,
                "check": "read",
                "message": f"Cannot read file: {e}",
                "severity": "error",
            }
        )
        return {}, "", errors

    if not text.startswith("---"):
        errors.append(
            {
                "file": path_str,
                "check": "frontmatter_parse",
                "message": "No YAML frontmatter found (missing opening ---)",
                "severity": "error",
            }
        )
        return {}, text, errors

    end = text.find("---", 3)
    if end < 0:
        errors.append(
            {
                "file": path_str,
                "check": "frontmatter_parse",
                "message": "Unterminated YAML frontmatter (missing closing ---)",
                "severity": "error",
            }
        )
        return {}, text, errors

    yaml_text = text[3:end]
    body = text[end + 3 :].strip()

    try:
        from ..utils.yaml import load_yaml

        fm = load_yaml(yaml_text)
        if not isinstance(fm, dict):
            errors.append(
                {
                    "file": path_str,
                    "check": "frontmatter_parse",
                    "message": "Frontmatter is not a YAML mapping",
                    "severity": "error",
                }
            )
            return {}, body, errors
    except Exception as e:
        errors.append(
            {
                "file": path_str,
                "check": "frontmatter_parse",
                "message": f"Invalid YAML: {e}",
                "severity": "error",
            }
        )
        return {}, body, errors

    # Check required base fields
    if "type" not in fm:
        errors.append(
            {
                "file": path_str,
                "check": "frontmatter_parse",
                "message": "Missing required field: type",
                "severity": "error",
            }
        )
    if "title" not in fm:
        errors.append(
            {
                "file": path_str,
                "check": "frontmatter_parse",
                "message": "Missing required field: title",
                "severity": "error",
            }
        )

    return fm, body, errors


def validate_entry(
    file_path: Path,
    fm: dict[str, Any],
    schema: Any | None = None,
) -> list[dict[str, str]]:
    """Validate a single entry's frontmatter against schema and protocol rules.

    Args:
        file_path: Path to the markdown file.
        fm: Parsed frontmatter dict.
        schema: Optional KBSchema for type-specific required field checks.

    Returns list of error/warning dicts.
    """
    errors: list[dict[str, str]] = []
    path_str = str(file_path)
    entry_type = fm.get("type", "")

    if not entry_type:
        return errors  # Already reported by _parse_frontmatter

    # Check required fields from kb.yaml TypeSchema
    if schema:
        type_schema = schema.get_type_schema(entry_type)
        if type_schema:
            for req_field in type_schema.required:
                if req_field not in fm:
                    errors.append(
                        {
                            "file": path_str,
                            "check": "required_fields",
                            "message": f"Missing required field '{req_field}' for type '{entry_type}'",
                            "severity": "error",
                        }
                    )

        # Declared enums: the same function the write path and `index health`
        # use, with the same severity (`validation.enforce_enums`), so the
        # pre-commit hook agrees with what a write would do (#555).
        from ..schema.enum_check import enum_findings

        for finding in enum_findings(schema, entry_type, fm):
            errors.append(
                {
                    "file": path_str,
                    "check": "field_value",
                    "message": (
                        f"Field '{finding['field']}' value {finding['got']!r} not in "
                        f"{'options' if finding['origin'] == 'field' else 'rule enum'}: "
                        f"{finding['expected']}"
                    ),
                    "severity": finding["severity"],
                }
            )

    # Protocol field type checks
    for field_name, expected_type in _PROTOCOL_FIELD_TYPES.items():
        if field_name in fm and fm[field_name] is not None:
            value = fm[field_name]
            if not isinstance(value, expected_type):
                errors.append(
                    {
                        "file": path_str,
                        "check": "protocol_field_type",
                        "message": (
                            f"Protocol field '{field_name}' should be {expected_type.__name__}, "
                            f"got {type(value).__name__}: {value!r}"
                        ),
                        "severity": "warning",
                    }
                )

    return errors


def detect_id_collisions(
    entries: list[tuple[Path, dict[str, Any]]],
) -> list[dict[str, str]]:
    """Detect entries that would generate the same ID across different types.

    Args:
        entries: List of (file_path, frontmatter) tuples.

    Returns list of error dicts for collisions.
    """
    from ..schema import generate_entry_id

    errors: list[dict[str, str]] = []

    # Map generated_id -> list of (file_path, entry_type)
    id_map: dict[str, list[tuple[Path, str]]] = defaultdict(list)

    for file_path, fm in entries:
        entry_id = fm.get("id", "")
        if not entry_id:
            title = fm.get("title", "")
            if title:
                entry_id = generate_entry_id(title)
        if entry_id:
            entry_type = fm.get("type", "unknown")
            id_map[entry_id].append((file_path, entry_type))

    for entry_id, occurrences in id_map.items():
        if len(occurrences) > 1:
            types = {t for _, t in occurrences}
            if len(types) > 1:
                # Cross-type collision
                files_desc = ", ".join(f"{p.name} ({t})" for p, t in occurrences)
                for file_path, _entry_type in occurrences:
                    errors.append(
                        {
                            "file": str(file_path),
                            "check": "id_collision",
                            "message": (f"ID '{entry_id}' collides across types: {files_desc}"),
                            "severity": "error",
                        }
                    )
            else:
                # Same-type collision (duplicate ID)
                dup_type = occurrences[0][1]
                for file_path, _entry_type in occurrences:
                    errors.append(
                        {
                            "file": str(file_path),
                            "check": "id_collision",
                            "message": (f"Duplicate ID '{entry_id}' within type '{dup_type}'"),
                            "severity": "error",
                        }
                    )

    return errors


schema_app = typer.Typer(help="Schema versioning and migration commands")


@schema_app.command("diff")
def schema_diff(
    kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
    entry_type: str = typer.Option(None, "--type", "-t", help="Filter by entry type"),
):
    """Show schema types with version and field annotations."""
    with cli_context() as (config, db, svc):
        kb_config = config.get_kb(kb_name)
        if not kb_config:
            cli_error(
                f"KB '{kb_name}' not found",
                error_code="KB_NOT_FOUND",
                suggestion="run `pyrite kb list` to see available knowledge bases",
            )

        if not kb_config.kb_yaml_path.exists():
            console.print(f"[yellow]No kb.yaml found for '{kb_name}'[/yellow]")
            raise typer.Exit(1)

        schema = kb_config.kb_schema
        console.print(f"[bold]Schema for KB '{kb_name}'[/bold]")
        console.print(f"[dim]Schema version: {schema.schema_version}[/dim]\n")

        types_to_show = (
            {entry_type: schema.types[entry_type]}
            if entry_type and entry_type in schema.types
            else schema.types
        )

        for type_name, type_schema in types_to_show.items():
            console.print(f"[bold cyan]{type_name}[/bold cyan] (v{type_schema.version})")
            if not type_schema.fields:
                console.print("  [dim]No typed fields defined[/dim]")
                continue

            table = Table(show_header=True, box=None, padding=(0, 2))
            table.add_column("Field", style="white")
            table.add_column("Type", style="dim")
            table.add_column("Required", style="dim")
            table.add_column("Since", style="yellow")

            for field_name, field_schema in type_schema.fields.items():
                since = (
                    str(field_schema.since_version)
                    if field_schema.since_version is not None
                    else ""
                )
                req = "yes" if field_schema.required else ""
                table.add_row(field_name, field_schema.field_type, req, since)

            console.print(table)
            console.print()


@schema_app.command("migrate")
def schema_migrate(
    kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
    entry_type: str = typer.Option(None, "--type", "-t", help="Filter by entry type"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be migrated without saving"
    ),
):
    """Migrate entries to current schema version.

    Loads and re-saves all entries, applying any pending migrations.
    """
    from ..storage.repository import KBRepository

    with cli_context() as (config, db, svc):
        kb_config = config.get_kb(kb_name)
        if not kb_config:
            cli_error(
                f"KB '{kb_name}' not found",
                error_code="KB_NOT_FOUND",
                suggestion="run `pyrite kb list` to see available knowledge bases",
            )

        repo = KBRepository(kb_config)
        checked = 0
        migrated = 0
        errors = 0

        for file_path in repo.list_files():
            try:
                entry = repo._load_entry(file_path)
                if entry_type and entry.entry_type != entry_type:
                    continue

                checked += 1

                # Check if version changed (migration happened on load)
                type_schema = kb_config.kb_schema.get_type_schema(entry.entry_type)
                needs_save = (
                    type_schema
                    and type_schema.version > 0
                    and entry._schema_version != type_schema.version
                )

                if needs_save:
                    migrated += 1
                    if not dry_run:
                        entry._schema_version = type_schema.version
                        entry.kb_name = kb_name
                        entry.file_path = file_path
                        entry.save(file_path)
            except Exception as e:
                errors += 1
                logger.warning("Migration error for %s: %s", file_path, e)

        label = "[dim](dry run)[/dim] " if dry_run else ""
        console.print(f"\n{label}[bold]Migration results for '{kb_name}':[/bold]")
        console.print(f"  Checked: {checked}")
        console.print(f"  Migrated: {migrated}")
        if errors:
            console.print(f"  [red]Errors: {errors}[/red]")
        if migrated == 0:
            console.print("  [green]All entries are up to date.[/green]")


def _collect_md_files(paths: list[Path]) -> list[Path]:
    """Resolve paths to a flat list of .md files."""
    result: list[Path] = []
    for p in paths:
        if p.is_file() and p.suffix == ".md":
            result.append(p)
        elif p.is_dir():
            result.extend(sorted(p.rglob("*.md")))
    return result


def _get_git_changed_md_files(kbs: list | None = None) -> list[Path]:
    """Get .md files changed in the current git working tree.

    When ``kbs`` (a list of ``KBConfig``, e.g. from ``config.all_kbs()``) is
    given, results are filtered to paths under one of those KBs -- a
    changed .md file outside every configured KB (a doc, a skill file) is
    not a KB entry and must not be validated as one. ``kbs=None`` (no filter
    argument at all) preserves the old unfiltered behavior for callers that
    want every changed .md file regardless of KB configuration. An *empty
    list* is a real answer, not an absent one -- it means "zero KBs are
    configured", so results are filtered to nothing (see #346: with no KB
    configured, ``schema validate --changed`` must not treat every changed
    .md file in the repo, e.g. CHANGELOG.md, as a KB entry).
    """
    try:
        output = subprocess.run(
            ["git", "diff", "--name-only", "--cached", "--diff-filter=ACMR"],
            capture_output=True,
            text=True,
            check=True,
        )
        staged = output.stdout.strip().splitlines()

        output = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR"],
            capture_output=True,
            text=True,
            check=True,
        )
        unstaged = output.stdout.strip().splitlines()

        output = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            check=True,
        )
        untracked = output.stdout.strip().splitlines()

        all_files = set(staged + unstaged + untracked)
        md_files = [Path(f) for f in all_files if f.endswith(".md") and Path(f).exists()]

        if kbs is None:
            return md_files
        if not kbs:
            return []

        kb_roots = [kb.path.resolve() for kb in kbs]

        def _under_a_kb(p: Path) -> bool:
            resolved = p.resolve()
            return any(resolved == root or root in resolved.parents for root in kb_roots)

        return [f for f in md_files if _under_a_kb(f)]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


@schema_app.command("validate")
def schema_validate(
    files: list[Path] = typer.Argument(None, help="Files or directories to validate"),
    kb_name: str = typer.Option(None, "--kb", "-k", help="Validate entire KB"),
    changed: bool = typer.Option(False, "--changed", help="Validate git-changed .md files"),
):
    """Validate KB entry frontmatter against schema rules.

    Checks: frontmatter parsing, required fields, protocol field types,
    and ID collision detection.
    """
    from ..config import load_config

    config = load_config()
    if kb_name:
        config = get_config_with_registered_kbs(config, name=kb_name)
    schema = None

    # Resolve files to validate
    md_files: list[Path] = []

    if kb_name:
        kb_config = config.get_kb(kb_name)
        if not kb_config:
            cli_error(
                f"KB '{kb_name}' not found",
                error_code="KB_NOT_FOUND",
                suggestion="run `pyrite kb list` to see available knowledge bases",
            )
        md_files = _collect_md_files([kb_config.path])
        schema = kb_config.kb_schema
    elif changed:
        md_files = _get_git_changed_md_files(config.all_kbs())
        # Try to find a schema from the first file's KB
        if md_files:
            for kb in config.all_kbs():
                try:
                    for f in md_files:
                        f.resolve().relative_to(kb.path.resolve())
                        schema = kb.kb_schema
                        break
                except ValueError:
                    continue
    elif files:
        md_files = _collect_md_files(files)
        # Try to detect KB for schema
        for kb in config.all_kbs():
            try:
                for f in md_files:
                    f.resolve().relative_to(kb.path.resolve())
                    schema = kb.kb_schema
                    break
            except ValueError:
                continue
    else:
        console.print("[yellow]Specify files, --kb, or --changed[/yellow]")
        raise typer.Exit(1)

    if not md_files:
        console.print("[dim]No .md files found to validate.[/dim]")
        raise typer.Exit(0)

    # Phase 1: Parse all files
    all_errors: list[dict[str, str]] = []
    parsed_entries: list[tuple[Path, dict[str, Any]]] = []

    for file_path in md_files:
        fm, body, parse_errors = _parse_frontmatter(file_path)
        all_errors.extend(parse_errors)

        if fm:
            parsed_entries.append((file_path, fm))
            entry_errors = validate_entry(file_path, fm, schema)
            all_errors.extend(entry_errors)

    # Phase 2: ID collision detection
    collision_errors = detect_id_collisions(parsed_entries)
    all_errors.extend(collision_errors)

    # Schema-level: a non-boolean `enforce_enums`, a field declaring both
    # `options:` and `values:` (#555).
    if schema:
        from ..schema.enum_check import schema_enum_warnings

        for w in schema_enum_warnings(schema):
            all_errors.append(
                {
                    "file": w["where"],
                    "check": "schema_enum",
                    "message": w["message"],
                    "severity": "warning",
                }
            )

    # Phase 3: Protocol satisfaction checking
    if schema:
        from ..models.core_types import get_entry_class
        from ..models.protocols import check_protocol_satisfaction
        from ..schema.core_types import resolve_type_metadata

        types_seen = {fm.get("type", "") for _, fm in parsed_entries}
        types_seen.discard("")

        for entry_type_name in types_seen:
            metadata = resolve_type_metadata(entry_type_name, schema)
            protocols = metadata.get("protocols", [])
            if not protocols:
                continue
            cls = get_entry_class(entry_type_name)
            ts = schema.get_type_schema(entry_type_name)
            results = check_protocol_satisfaction(cls, protocols, ts)
            for r in results:
                if not r.satisfied:
                    all_errors.append(
                        {
                            "file": f"<type:{entry_type_name}>",
                            "check": "protocol_satisfaction",
                            "message": (
                                f"Type '{entry_type_name}' declares protocol "
                                f"'{r.protocol_name}' but is missing fields: "
                                f"{r.missing_fields}"
                            ),
                            "severity": "warning",
                        }
                    )

    # Output
    error_count = sum(1 for e in all_errors if e["severity"] == "error")
    warning_count = sum(1 for e in all_errors if e["severity"] == "warning")

    # Group by file
    by_file: dict[str, list[dict[str, str]]] = defaultdict(list)
    for err in all_errors:
        by_file[err["file"]].append(err)

    for file_path_str, file_errors in sorted(by_file.items()):
        console.print(f"\n[bold]{file_path_str}:[/bold]")
        for err in file_errors:
            icon = "[red]x[/red]" if err["severity"] == "error" else "[yellow]![/yellow]"
            console.print(f"  {icon} [{err['check']}] {err['message']}")

    # Files with no errors get a checkmark
    clean_files = len(md_files) - len(by_file)

    console.print(
        f"\n{len(md_files)} files, {error_count} errors, {warning_count} warnings"
        + (f", {clean_files} clean" if clean_files > 0 else "")
    )

    if error_count > 0:
        raise typer.Exit(1)

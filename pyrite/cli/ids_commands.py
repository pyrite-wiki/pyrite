"""`pyrite ids`: list the files with no ``id:`` and pin each one (#700).

The upgrade steps (docs/pinning-entry-ids.md, ADR-0042 decision 5): with
the old version installed, ``ids missing``, then ``ids pin --dry-run``, then
``ids pin``. Each id-less file gains one line, ``id: <the id it has today>``.

Exit codes (also in docs/json-contracts.md):

- ``0``: every walked file states an id (``missing``), or every planned pin
  was written (``pin``);
- ``3``: some files are outside the id contract: missing an id, an empty
  ``id:``, unreadable, or in a collision group left for the operator. A dry
  run returns the code the real run would. Not ``2``, which is click's usage
  error;
- ``1``: an error (unknown KB, an invalid ``--rename``); nothing written.
"""

from __future__ import annotations

import json

import typer

from ..exceptions import ValidationError
from ..services import id_pin_service
from ..utils.errors import cli_error
from .context import cli_context

OUTSIDE_CONTRACT = 3

ids_app = typer.Typer(help="Entry ids: list the files with no id, and pin them")

_FORMAT_HELP = "Output format: text (default) or json"


def _scan(config, kb_name: str, output_format: str) -> id_pin_service.IdScan:
    kb_config = config.get_kb(kb_name)
    if kb_config is None:
        cli_error(
            f"Knowledge base '{kb_name}' not found",
            output_format if output_format == "json" else "rich",
            error_code="KB_NOT_FOUND",
            suggestion="Run `pyrite kb list` for the KB names",
        )
    return id_pin_service.scan(kb_config)


def _print_collisions(collisions: list[dict]) -> None:
    for group in collisions:
        typer.echo(f"  collision: {len(group['claimants'])} files hold id '{group['id']}'")
        for c in group["claimants"]:
            how = "its id: line" if c["explicit"] else "derived, no id: line"
            typer.echo(f"      {c['path']}  ({how})")


@ids_app.command("missing")
def ids_missing(
    kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
    output_format: str = typer.Option("text", "--format", help=_FORMAT_HELP),
):
    """List the files with no `id:`, with the id each has today.

    Exits 0 when every file states an id, 3 when some do not.

    \b
    Examples:
        pyrite ids missing -k notes
        pyrite ids missing -k notes --format json
    """
    with cli_context() as (config, _db, _svc):
        found = _scan(config, kb_name, output_format)
    if output_format == "json":
        typer.echo(json.dumps(found.to_dict(), indent=2))
    else:
        if not found.missing and not found.skipped:
            typer.echo(f"Every file in '{kb_name}' has an id.")
        if found.missing:
            typer.echo(f"{len(found.missing)} file(s) in '{kb_name}' have no id: line:")
            for row in found.missing:
                note = "  (empty id: line)" if row["status"] == "empty_id" else ""
                typer.echo(f"  {row['path']}  ->  {row['id']}{note}")
        _print_collisions(found.collisions)
        for row in found.skipped:
            typer.echo(f"  skipped {row['path']}: {row['reason']}")
        if found.missing:
            typer.echo(f"Next: pyrite ids pin -k {kb_name} --dry-run")
    if found.outside_contract:
        raise typer.Exit(OUTSIDE_CONTRACT)


@ids_app.command("pin")
def ids_pin(
    kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan; write nothing"),
    rename: list[str] = typer.Option(
        [],
        "--rename",
        help="<path>=<id>: in a collision, give this file a new id (repeatable)",
    ),
    output_format: str = typer.Option("text", "--format", help=_FORMAT_HELP),
):
    """Add one line, `id: <the id it has today>`, to each file with no id.

    No other byte of any file changes. Files that share an id are left alone
    until you choose which keeps it (`--rename` the others). The index is
    synced afterwards. Exits 0 when everything was pinned, 3 when something
    was left (the output says what and why), 1 on an error.

    \b
    Examples:
        pyrite ids pin -k notes --dry-run
        pyrite ids pin -k notes
        pyrite ids pin -k notes --rename drafts/meeting.md=meeting-2026-03
    """
    err_format = output_format if output_format == "json" else "rich"
    with cli_context() as (config, _db, svc):
        found = _scan(config, kb_name, output_format)
        try:
            the_plan = id_pin_service.plan(found, id_pin_service.parse_renames(rename))
        except ValidationError as e:
            cli_error(str(e), err_format, error_code="INVALID_RENAME")
        if dry_run:
            result = id_pin_service.check(found, the_plan)
        else:
            result = id_pin_service.apply(found, the_plan)
        result["dry_run"] = dry_run
        result["synced"] = False
        if not dry_run and result["pinned"]:
            # A pin changes no id the index holds, except that a resolved
            # collision makes shadowed files reachable: sync so the next
            # command agrees with the files.
            svc.sync_index(kb_name)
            result["synced"] = True

    if output_format == "json":
        typer.echo(json.dumps(result, indent=2))
    else:
        verb = "would pin" if dry_run else "pinned"
        head = "Plan (dry run, nothing written)" if dry_run else "Pinned"
        typer.echo(f"{head} for '{kb_name}': {len(result['pinned'])} file(s)")
        for item in result["pinned"]:
            was = f"  (renamed; was {item['renamed_from']})" if "renamed_from" in item else ""
            typer.echo(f"  {verb} {item['path']}  ->  id: {item['id']}{was}")
        for group in result["refused"]:
            typer.echo(
                f"  refused: {len(group['claimants'])} files hold id '{group['id']}'; "
                f"nothing written for them. Keep one, --rename the others: "
                + ", ".join(group["unrenamed"])
            )
        for row in result["skipped"]:
            typer.echo(f"  skipped {row['path']}: {row['reason']}")
        if result["synced"]:
            typer.echo("Index synced.")
    if result["refused"] or result["skipped"]:
        raise typer.Exit(OUTSIDE_CONTRACT)

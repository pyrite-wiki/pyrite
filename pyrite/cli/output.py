"""CLI output formatting utilities."""

import typer


def validate_output_format(value: str) -> str:
    """Validate a CLI output format before the command starts.

    ``rich`` is handled by the CLI rather than the format registry; all other
    formats must have a registered serializer. Raising ``BadParameter`` here
    makes an unknown format a usage error (exit 2), instead of allowing a
    late ``ValueError`` from the serializer to escape as a traceback.
    """
    from ..formats import get_format_registry

    registry = get_format_registry()
    if value == "rich" or registry.get(value) is not None:
        return value

    available = ["rich", *registry.available_formats()]
    raise typer.BadParameter(f"unknown format {value!r}; choose one of: {', '.join(available)}")


#: Where the shared option leaves the format for the root group. ``ctx.meta`` is
#: one dict for the whole context chain, so the root reads what the leaf parsed.
FORMAT_META_KEY = "pyrite.output_format"
_LEGACY_META_KEY = "pyrite.output_format.short"
_EXPLICIT_META_KEY = "pyrite.output_format.explicit"

#: Where the root group (``PyriteCLIGroup.invoke``) leaves its guard for the
#: shared option to find. The option tells it which command is about to run.
BODY_GUARD_META_KEY = "pyrite.body_guard"

#: What the shared option accepts: the formats the root can print for any
#: payload. ``markdown`` and ``csv`` are in the format registry for the shapes
#: REST serves (entries, search results); for anything else they print a
#: Python ``repr``, so a command on the contract does not offer them.
SHARED_FORMATS = ("json", "yaml", "rich")


def validate_shared_format(value: str) -> str:
    """The shared option's check: an unknown format is a usage error (exit 2)."""
    if value in SHARED_FORMATS:
        return value
    raise typer.BadParameter(
        f"unknown format {value!r}; choose one of: {', '.join(SHARED_FORMATS)}"
    )


def _given_on_the_command_line(ctx: typer.Context, param: typer.CallbackParam) -> bool:
    source = ctx.get_parameter_source(param.name)
    return getattr(source, "name", None) == "COMMANDLINE"


def _two_answers() -> typer.BadParameter:
    return typer.BadParameter("-f and --format were both given; use --format only.")


def _record_format(ctx: typer.Context, param: typer.CallbackParam, value: str) -> str:
    value = validate_shared_format(value)
    if _given_on_the_command_line(ctx, param):
        # Click runs the callbacks of options given on the command line in the
        # order they were given, so whichever of -f and --format comes second
        # sees the other and refuses: two answers are a usage error, not a race.
        if _LEGACY_META_KEY in ctx.meta:
            raise _two_answers()
        ctx.meta[_EXPLICIT_META_KEY] = True
    ctx.meta[FORMAT_META_KEY] = value
    # Click is still parsing here: a later option may yet be a usage error, so
    # this is not the moment the body starts. The root's guard is handed the
    # leaf command (only this callback has it) and wraps its body; the watch
    # starts when click calls that (ADR-0046, decision 7).
    guard = ctx.meta.get(BODY_GUARD_META_KEY)
    if guard is not None:
        guard.watch_body_of(ctx)
    return value


def _record_short_format(ctx: typer.Context, value: str | None) -> str | None:
    if value is None:
        return None
    if ctx.meta.get(_EXPLICIT_META_KEY):
        raise _two_answers()
    value = validate_shared_format(value)
    typer.echo(
        f"Warning: -f is deprecated and will stop meaning --format in the next release; "
        f"use --format {value} (or set PYRITE_FORMAT).",
        err=True,
    )
    ctx.meta[_LEGACY_META_KEY] = value
    return value


#: The one output option (#303, ADR-0046): JSON unless asked otherwise,
#: ``PYRITE_FORMAT`` sets a person's default, an explicit ``--format`` overrides
#: it, an unknown format is a usage error. Declaring it is what puts a command
#: on the outcome contract: the root group renders the command's Outcome in
#: this format, and from the moment the command's body starts the root is the
#: only way out of it (``PyriteCLIGroup``).
OUTPUT_FORMAT = typer.Option(
    "json",
    "--format",
    envvar="PYRITE_FORMAT",
    callback=_record_format,
    help="Output format: json (default), yaml, or rich for text. PYRITE_FORMAT sets the default.",
)

#: ``-f`` meant ``--format`` on the task commands. #303: it keeps working for one
#: release, with a warning on stderr, then goes. Declared beside OUTPUT_FORMAT on
#: a command that had ``-f``; hidden from help.
LEGACY_FORMAT_SHORT = typer.Option(None, "-f", hidden=True, callback=_record_short_format)


def declares_shared_format(command) -> bool:
    """Is this click command on the outcome contract? It is when one of its
    parameters is the shared ``--format`` option (``OUTPUT_FORMAT``).

    The one definition: the root's guard asks it before it watches a body, and
    the registry tests ask it to list the commands on the contract. Typer wraps
    an option's callback, so the original is one ``__wrapped__`` down.
    """
    for param in getattr(command, "params", ()):
        callback = getattr(param, "callback", None)
        if callback is _record_format or getattr(callback, "__wrapped__", None) is _record_format:
            return True
    return False


def requested_format(ctx) -> str | None:
    """The format the leaf command was asked for, or None when it declared no
    shared option (a legacy command, which renders itself)."""
    meta = ctx.meta
    if _LEGACY_META_KEY in meta:
        return meta[_LEGACY_META_KEY]
    return meta.get(FORMAT_META_KEY)


def format_output(data: dict, fmt: str) -> str | None:
    """Format data using the format registry. Returns None for default (rich) output."""
    if fmt == "rich":
        return None
    from ..formats import format_response

    content, _ = format_response(data, fmt)
    return content


def backlinks_table(entry_id: str, links: list[dict]):
    """The `backlinks` table, shared by `pyrite` and `pyrite-read` so the two
    cannot drift.

    ``Relation`` is this entry's reading (``related_to`` when the relation has
    no declared inverse); ``Written as`` is what the source's file says, so an
    undeclared relation such as ``informs`` is still visible (#527). An
    edge-derived row has no ``forward_relation`` and leaves the cell empty.
    """
    from rich.markup import escape
    from rich.table import Table

    table = Table(title=f"Backlinks to {escape(entry_id)}")
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("Type", style="dim")
    table.add_column("Relation", style="dim")
    table.add_column("Written as", style="dim")
    for link in links:
        # Cells are Rich markup: a relation is free text and may hold brackets.
        table.add_row(
            link.get("id", ""),
            link.get("title", ""),
            link.get("entry_type", ""),
            escape(link.get("relation") or ""),
            escape(link.get("forward_relation") or ""),
        )
    return table

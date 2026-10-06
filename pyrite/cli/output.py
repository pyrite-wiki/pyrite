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


def format_output(data: dict, fmt: str, **kwargs) -> str | None:
    """Format data using the format registry. Returns None for default (rich) output."""
    if fmt == "rich":
        return None
    from ..formats import format_response

    content, _ = format_response(data, fmt, **kwargs)
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

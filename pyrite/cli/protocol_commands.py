"""Protocol inspection and satisfaction checking CLI commands."""

import dataclasses
import logging

import typer
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()

protocol_app = typer.Typer(help="Protocol inspection and satisfaction checking")


@protocol_app.command("list")
def protocol_list():
    """List all available protocols with their fields and versions."""
    from ..models.protocols import PROTOCOL_REGISTRY

    # Also get plugin protocols
    try:
        from ..plugins import get_registry

        all_protocols = get_registry().get_all_protocols()
    except Exception:
        all_protocols = dict(PROTOCOL_REGISTRY)

    table = Table(title="Available Protocols", show_header=True)
    table.add_column("Protocol", style="cyan")
    table.add_column("Version", style="yellow")
    table.add_column("Fields", style="white")
    table.add_column("Source", style="dim")

    for name, cls in sorted(all_protocols.items()):
        version = str(getattr(cls, "PROTOCOL_VERSION", 0))
        fields = ", ".join(f.name for f in dataclasses.fields(cls))
        source = "core" if name in PROTOCOL_REGISTRY else "plugin"
        table.add_row(name, version, fields, source)

    console.print(table)


@protocol_app.command("check")
def protocol_check(
    kb_name: str = typer.Option(None, "--kb", "-k", help="Check types for a specific KB"),
    type_name: str = typer.Option(None, "--type", "-t", help="Check a specific type"),
):
    """Verify that entry types satisfy their declared protocols."""
    from ..models.core_types import get_entry_class
    from ..models.protocols import check_protocol_satisfaction
    from ..schema.core_types import CORE_TYPE_METADATA, resolve_type_metadata

    checks: list[tuple[str, type, list[str], object]] = []

    if kb_name:
        from .context import cli_context

        with cli_context(kbs=(kb_name,), output_format="rich") as (config, db, svc):
            kb_config = config.get_kb(kb_name)
            if not kb_config:
                from ..utils.errors import cli_error

                cli_error(
                    f"KB '{kb_name}' not found",
                    error_code="KB_NOT_FOUND",
                    suggestion="run `pyrite kb list` to see registered KBs",
                )
            schema = kb_config.kb_schema
            for tn, ts in schema.types.items():
                metadata = resolve_type_metadata(tn, schema)
                protocols = metadata.get("protocols", []) or ts.protocols
                if protocols:
                    cls = get_entry_class(tn)
                    checks.append((tn, cls, protocols, ts))
    elif type_name:
        cls = get_entry_class(type_name)
        metadata = resolve_type_metadata(type_name)
        protocols = metadata.get("protocols", [])
        if not protocols:
            console.print(f"[dim]Type '{type_name}' declares no protocols.[/dim]")
            raise typer.Exit(0)
        checks.append((type_name, cls, protocols, None))
    else:
        # Check all core types
        for tn, meta in CORE_TYPE_METADATA.items():
            protocols = meta.get("protocols", [])
            if protocols:
                cls = get_entry_class(tn)
                checks.append((tn, cls, protocols, None))
        # Plugin-contributed types
        try:
            from ..plugins import get_registry

            plugin_types = get_registry().get_all_entry_types()
            plugin_meta = get_registry().get_all_type_metadata()
            for tn, cls in plugin_types.items():
                protocols = plugin_meta.get(tn, {}).get("protocols", [])
                if protocols:
                    checks.append((tn, cls, protocols, None))
        except Exception:
            pass

    if not checks:
        console.print("[dim]No types with protocol declarations found.[/dim]")
        raise typer.Exit(0)

    total_pass = 0
    total_fail = 0

    for tn, cls, protocols, ts in checks:
        results = check_protocol_satisfaction(cls, protocols, ts)
        for r in results:
            if r.satisfied:
                total_pass += 1
                console.print(f"  [green]✓[/green] {tn} satisfies '{r.protocol_name}' ({r.method})")
            else:
                total_fail += 1
                console.print(
                    f"  [red]✗[/red] {tn} does NOT satisfy '{r.protocol_name}': {r.message}"
                )

    console.print(f"\n{total_pass + total_fail} checks: {total_pass} passed, {total_fail} failed")

    if total_fail > 0:
        raise typer.Exit(1)

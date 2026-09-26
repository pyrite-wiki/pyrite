"""Zettelkasten CLI commands."""

import logging

import typer
from rich.console import Console
from rich.table import Table

from pyrite.config import load_config
from pyrite.services.access_policy import UNSCOPED
from pyrite.storage.database import PyriteDB

zettel_app = typer.Typer(help="Zettelkasten knowledge management")
console = Console()
logger = logging.getLogger(__name__)


@zettel_app.command("new")
def zettel_new(
    title: str = typer.Argument(..., help="Note title"),
    zettel_type: str = typer.Option(
        "fleeting", "--type", "-t", help="Type: fleeting, literature, permanent, hub"
    ),
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="Target KB"),
    source: str = typer.Option("", "--source", "-s", help="Source reference"),
):
    """Create a new zettel through the KBService write pipeline (#391).

    Was: build the entry, `repo.save(entry)` directly (no exists check -- an
    existing id was silently overwritten -- no validators, no before/after-save
    hooks, and nothing indexed until a separate `pyrite index sync`). Now goes
    through `KBService.create_entry`, same as `software-kb`'s backlog_item
    creation.
    """
    from pyrite.exceptions import PyriteError
    from pyrite.services.kb_service import KBService
    from pyrite.storage.database import PyriteDB
    from pyrite.utils.errors import cli_error

    config = load_config()

    # Find target KB
    kb_config = None
    if kb_name:
        kb_config = config.get_kb(kb_name)
    else:
        # Find first KB that has zettel type configured
        for kb in config.knowledge_bases:
            kb_config = kb
            break

    if not kb_config:
        cli_error("No KB found. Specify --kb.", error_code="KB_NOT_FOUND")

    entry_type = "literature_note" if zettel_type == "literature" else "zettel"
    fields: dict[str, object]
    if entry_type == "zettel":
        fields = {
            "source_ref": source,
            "zettel_type": zettel_type,
            "processing_stage": "capture" if zettel_type == "fleeting" else "",
        }
    else:
        fields = {"source_work": source}

    db = PyriteDB(config.settings.index_path)
    try:
        svc = KBService(config, db)
        try:
            entry = svc.create_entry(
                kb_config.name,
                None,
                title,
                entry_type,
                "",
                **fields,
            )
        except PyriteError as e:
            code = getattr(e, "error_code", None) or "CREATE_FAILED"
            # This is the operator's own terminal, not a transport boundary
            # -- public_message exists to keep server-side detail (a real
            # path, a driver's own text) out of REST/MCP responses, but
            # masking it here hides that same detail from the person who
            # would read the server log anyway (#506 item 3). Always str(e).
            cli_error(str(e), error_code=code)

        from pyrite.storage.repository import KBRepository

        repo = KBRepository(kb_config)
        file_path = repo._resolve_file_path(entry, repo._infer_subdir(entry))
    finally:
        db.close()

    console.print(f"[green]Created {zettel_type} note:[/green] {file_path}")


@zettel_app.command("inbox")
def zettel_inbox(
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB to search"),
):
    """List fleeting notes not yet fully processed (not at 'connect' stage)."""
    config = load_config()
    db = PyriteDB(config.settings.index_path)

    try:
        results = db.search("*", limit=500)
        fleeting = []
        for r in results:
            if r.get("entry_type") != "zettel":
                if kb_name and r.get("kb_name") != kb_name:
                    continue
                continue
            if kb_name and r.get("kb_name") != kb_name:
                continue
            # Check metadata for zettel_type and processing_stage
            meta = r.get("metadata") or {}
            if isinstance(meta, str):
                import json

                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            zt = meta.get("zettel_type", r.get("zettel_type", ""))
            stage = meta.get("processing_stage", r.get("processing_stage", ""))
            if zt == "fleeting" and stage != "connect":
                fleeting.append({**r, "processing_stage": stage})

        if not fleeting:
            console.print("[dim]Inbox empty — all fleeting notes processed.[/dim]")
            return

        table = Table(title="Zettel Inbox")
        table.add_column("ID", style="cyan")
        table.add_column("Title")
        table.add_column("Stage", style="yellow")
        table.add_column("KB", style="dim")

        for z in fleeting:
            table.add_row(
                z.get("id", ""),
                z.get("title", ""),
                z.get("processing_stage", "capture"),
                z.get("kb_name", ""),
            )

        console.print(table)
    finally:
        db.close()


@zettel_app.command("orphans")
def zettel_orphans(
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB to search"),
):
    """Find notes with no incoming or outgoing links."""
    config = load_config()
    db = PyriteDB(config.settings.index_path)

    try:
        orphans = db.get_orphans(kb_name=kb_name, readable_kbs=UNSCOPED)

        if not orphans:
            console.print("[green]No orphan notes found.[/green]")
            return

        table = Table(title="Orphan Notes")
        table.add_column("ID", style="cyan")
        table.add_column("Title")
        table.add_column("Type", style="yellow")
        table.add_column("KB", style="dim")

        for o in orphans:
            table.add_row(
                o.get("id", ""),
                o.get("title", ""),
                o.get("entry_type", ""),
                o.get("kb_name", ""),
            )

        console.print(table)
        console.print(f"\n[dim]{len(orphans)} orphan(s) found[/dim]")
    finally:
        db.close()


@zettel_app.command("maturity")
def zettel_maturity(
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="KB to search"),
):
    """Show maturity distribution of zettels."""
    config = load_config()
    db = PyriteDB(config.settings.index_path)

    try:
        results = db.search("*", limit=1000)
        counts = {"seed": 0, "sapling": 0, "evergreen": 0, "unknown": 0}
        total = 0

        for r in results:
            if r.get("entry_type") != "zettel":
                continue
            if kb_name and r.get("kb_name") != kb_name:
                continue
            meta = r.get("metadata") or {}
            if isinstance(meta, str):
                import json

                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            maturity = meta.get("maturity", r.get("maturity", "seed"))
            counts[maturity] = counts.get(maturity, 0) + 1
            total += 1

        if total == 0:
            console.print("[dim]No zettels found.[/dim]")
            return

        table = Table(title="Zettel Maturity Distribution")
        table.add_column("Maturity", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Percentage", justify="right")

        for level in ("seed", "sapling", "evergreen"):
            count = counts.get(level, 0)
            pct = (count / total * 100) if total > 0 else 0
            table.add_row(level, str(count), f"{pct:.0f}%")

        console.print(table)
        console.print(f"\n[dim]Total zettels: {total}[/dim]")
    finally:
        db.close()

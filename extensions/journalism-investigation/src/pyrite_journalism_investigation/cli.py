"""Journalism Investigation CLI commands."""

import json as json_mod

import typer
from rich.console import Console
from rich.table import Table

from pyrite.config import load_config
from pyrite.exceptions import QueryTooLongError
from pyrite.storage.database import PyriteDB

from .plugin import JournalismInvestigationPlugin

investigation_app = typer.Typer(help="Journalism investigation commands")
console = Console()


def _get_plugin_with_db():
    """Create a plugin instance with DB context for CLI usage."""
    config = load_config()
    db = PyriteDB(config.settings.index_path)
    plugin = JournalismInvestigationPlugin()
    # Set minimal context so _get_db() returns the injected DB
    from dataclasses import dataclass

    @dataclass
    class _CLIContext:
        db: PyriteDB
        config: object = None

    plugin.set_context(_CLIContext(db=db, config=config))
    return plugin, db


@investigation_app.command("timeline")
def timeline(
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    from_date: str = typer.Option("", "--from", help="Start date (YYYY-MM-DD)"),
    to_date: str = typer.Option("", "--to", help="End date (YYYY-MM-DD)"),
    actor: str = typer.Option("", "--actor", help="Filter by actor name"),
    event_type: str = typer.Option(
        "", "--type", help="Filter by type: investigation_event, transaction, legal_action"
    ),
    min_importance: int = typer.Option(0, "--min-importance", help="Minimum importance (1-10)"),
    limit: int = typer.Option(50, "--limit", help="Max results"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Query investigation events by date range, actor, and type."""
    plugin, db = _get_plugin_with_db()
    try:
        args = {"kb_name": kb_name, "limit": limit}
        if from_date:
            args["from_date"] = from_date
        if to_date:
            args["to_date"] = to_date
        if actor:
            args["actor"] = actor
        if event_type:
            args["event_type"] = event_type
        if min_importance:
            args["min_importance"] = min_importance

        result = plugin._mcp_timeline(args)

        if output_json:
            typer.echo(json_mod.dumps(result, indent=2))
            return

        if not result["events"]:
            console.print("[dim]No events found.[/dim]")
            return

        table = Table(title=f"Timeline ({result['count']} events)")
        table.add_column("Date", style="cyan")
        table.add_column("Type", style="green")
        table.add_column("Title")
        table.add_column("Imp", justify="right")
        table.add_column("Actors", style="dim")

        for e in result["events"]:
            table.add_row(
                e.get("date", ""),
                e.get("type", ""),
                e.get("title", ""),
                str(e.get("importance", "")),
                ", ".join(e.get("actors", [])),
            )
        console.print(table)
    finally:
        db.close()


@investigation_app.command("entities")
def entities(
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    entity_type: str = typer.Option(
        "", "--type", help="Filter by type: person, organization, asset, account"
    ),
    jurisdiction: str = typer.Option("", "--jurisdiction", help="Filter by jurisdiction"),
    min_importance: int = typer.Option(0, "--min-importance", help="Minimum importance (1-10)"),
    limit: int = typer.Option(50, "--limit", help="Max results"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Query investigation entities by type, importance, and jurisdiction."""
    plugin, db = _get_plugin_with_db()
    try:
        args = {"kb_name": kb_name, "limit": limit}
        if entity_type:
            args["entity_type"] = entity_type
        if jurisdiction:
            args["jurisdiction"] = jurisdiction
        if min_importance:
            args["min_importance"] = min_importance

        result = plugin._mcp_entities(args)

        if output_json:
            typer.echo(json_mod.dumps(result, indent=2))
            return

        if not result["entities"]:
            console.print("[dim]No entities found.[/dim]")
            return

        table = Table(title=f"Entities ({result['count']})")
        table.add_column("ID", style="cyan")
        table.add_column("Type", style="green")
        table.add_column("Title")
        table.add_column("Imp", justify="right")

        for e in result["entities"]:
            table.add_row(e["id"], e["type"], e["title"], str(e["importance"]))
        console.print(table)
    finally:
        db.close()


@investigation_app.command("sources")
def sources(
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    reliability: str = typer.Option("", "--reliability", help="Filter: high, medium, low, unknown"),
    classification: str = typer.Option(
        "", "--classification", help="Filter: public, leaked, foia, etc."
    ),
    from_date: str = typer.Option("", "--from", help="Start date (YYYY-MM-DD)"),
    to_date: str = typer.Option("", "--to", help="End date (YYYY-MM-DD)"),
    limit: int = typer.Option(50, "--limit", help="Max results"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Query source documents by reliability and classification."""
    plugin, db = _get_plugin_with_db()
    try:
        args = {"kb_name": kb_name, "limit": limit}
        if reliability:
            args["reliability"] = reliability
        if classification:
            args["classification"] = classification
        if from_date:
            args["from_date"] = from_date
        if to_date:
            args["to_date"] = to_date

        result = plugin._mcp_sources(args)

        if output_json:
            typer.echo(json_mod.dumps(result, indent=2))
            return

        if not result["sources"]:
            console.print("[dim]No sources found.[/dim]")
            return

        table = Table(title=f"Sources ({result['count']})")
        table.add_column("ID", style="cyan")
        table.add_column("Reliability", style="green")
        table.add_column("Classification")
        table.add_column("Title")
        table.add_column("Date", style="dim")

        for s in result["sources"]:
            table.add_row(
                s["id"],
                s["reliability"],
                s.get("classification", ""),
                s["title"],
                s.get("date", ""),
            )
        console.print(table)
    finally:
        db.close()


@investigation_app.command("claims")
def claims(
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    status: str = typer.Option(
        "",
        "--status",
        help="Filter: unverified, partially_verified, corroborated, disputed, retracted",
    ),
    confidence: str = typer.Option("", "--confidence", help="Filter: high, medium, low"),
    min_importance: int = typer.Option(0, "--min-importance", help="Minimum importance (1-10)"),
    limit: int = typer.Option(50, "--limit", help="Max results"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Query claims by status and confidence level."""
    plugin, db = _get_plugin_with_db()
    try:
        args = {"kb_name": kb_name, "limit": limit}
        if status:
            args["claim_status"] = status
        if confidence:
            args["confidence"] = confidence
        if min_importance:
            args["min_importance"] = min_importance

        result = plugin._mcp_claims(args)

        if output_json:
            typer.echo(json_mod.dumps(result, indent=2))
            return

        if not result["claims"]:
            console.print("[dim]No claims found.[/dim]")
            return

        table = Table(title=f"Claims ({result['count']})")
        table.add_column("ID", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Confidence")
        table.add_column("Title")
        table.add_column("Evidence", justify="right")
        table.add_column("Imp", justify="right")

        for c in result["claims"]:
            table.add_row(
                c["id"],
                c["claim_status"],
                c["confidence"],
                c["title"],
                str(c["evidence_count"]),
                str(c["importance"]),
            )
        console.print(table)
    finally:
        db.close()


@investigation_app.command("network")
def network(
    entry_id: str = typer.Argument(..., help="Entry ID to get network for"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Get the connection network for an entity."""
    plugin, db = _get_plugin_with_db()
    try:
        result = plugin._mcp_network({"entry_id": entry_id, "kb_name": kb_name})

        if output_json:
            typer.echo(json_mod.dumps(result, indent=2))
            if "error" in result:
                raise typer.Exit(1)  # the answer is an error: say so in the exit (#526)
            return

        if "error" in result:
            console.print(f"[red]Error:[/red] {result['error']}")
            raise typer.Exit(1)

        console.print(f"[bold]{result['center']['title']}[/bold] ({entry_id})")
        console.print()

        if result["outlinks"]:
            console.print("[green]Outlinks:[/green]")
            for link in result["outlinks"]:
                console.print(f"  → {link}")
        else:
            console.print("[dim]No outlinks[/dim]")

        if result["backlinks"]:
            console.print("[green]Backlinks:[/green]")
            for link in result["backlinks"]:
                console.print(f"  ← {link}")
        else:
            console.print("[dim]No backlinks[/dim]")
    finally:
        db.close()


@investigation_app.command("evidence-chain")
def evidence_chain(
    claim_id: str = typer.Argument(..., help="Claim entry ID to trace"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Trace the evidence chain for a claim."""
    plugin, db = _get_plugin_with_db()
    try:
        result = plugin._mcp_evidence_chain({"claim_id": claim_id, "kb_name": kb_name})

        if output_json:
            typer.echo(json_mod.dumps(result, indent=2))
            if "error" in result:
                raise typer.Exit(1)  # the answer is an error: say so in the exit (#526)
            return

        if "error" in result:
            console.print(f"[red]Error:[/red] {result['error']}")
            raise typer.Exit(1)

        claim = result["claim"]
        console.print(f"[bold]Claim:[/bold] {claim['title']}")
        console.print(f"  Assertion: {claim['assertion']}")
        console.print(f"  Status: {claim['claim_status']}  Confidence: {claim['confidence']}")
        console.print()

        if result["evidence_chain"]:
            console.print("[green]Evidence Chain:[/green]")
            for ev in result["evidence_chain"]:
                if ev.get("status") == "missing":
                    console.print(f"  [red]✗[/red] {ev['evidence_id']} — missing")
                else:
                    source = ev.get("source_document")
                    source_info = (
                        f" → {source['title']} ({source['reliability']})"
                        if source
                        else " → [dim]no source[/dim]"
                    )
                    console.print(f"  ✓ {ev['title']} ({ev['evidence_type']}){source_info}")

        if result["gaps"]:
            console.print()
            console.print("[yellow]Gaps:[/yellow]")
            for gap in result["gaps"]:
                console.print(f"  ⚠ {gap}")
    finally:
        db.close()


@investigation_app.command("qa")
def qa_report(
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    stale_days: int = typer.Option(
        30, "--stale-days", help="Days before unverified claims are stale"
    ),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show investigation quality metrics and warnings."""
    from .qa import compute_qa_metrics

    config = load_config()
    db = PyriteDB(config.settings.index_path)
    try:
        result = compute_qa_metrics(db, kb_name, stale_days=stale_days)

        if output_json:
            typer.echo(json_mod.dumps(result, indent=2))
            return

        score = result["quality_score"]
        score_color = "green" if score >= 70 else "yellow" if score >= 40 else "red"
        console.print(
            f"[bold]Investigation Quality Score: [{score_color}]{score}/100[/{score_color}][/bold]"
        )
        console.print()

        # Source tiers
        tiers = result["source_tiers"]
        console.print("[bold]Source Reliability:[/bold]")
        table = Table(show_header=True)
        table.add_column("Tier")
        table.add_column("Count", justify="right")
        for tier in ("high", "medium", "low", "unknown"):
            table.add_row(tier, str(tiers[tier]))
        table.add_row("[bold]Total[/bold]", f"[bold]{tiers['total']}[/bold]")
        console.print(table)
        console.print(f"  High-reliability: {tiers['high_pct']}%")
        console.print()

        # Claims
        claims = result["claims"]
        console.print("[bold]Claims:[/bold]")
        console.print(f"  Total: {claims['total']}")
        console.print(
            f"  With evidence: {claims['total'] - claims['orphans']} ({claims['coverage_pct']}%)"
        )
        console.print(f"  Orphans (no evidence): {claims['orphans']}")
        console.print(f"  Disputed/retracted: {claims['disputed_ratio']}%")
        console.print()

        if claims["confidence"]:
            console.print("[bold]Confidence Distribution:[/bold]")
            for level in ("high", "medium", "low"):
                count = claims["confidence"].get(level, 0)
                console.print(f"  {level}: {count}")
            console.print()

        # Warnings
        if result["warnings"]:
            console.print("[bold yellow]Warnings:[/bold yellow]")
            for w in result["warnings"]:
                console.print(f"  ⚠ {w}")
        else:
            console.print("[green]No warnings.[/green]")
    finally:
        db.close()


@investigation_app.command("search")
def search_all(
    query: str = typer.Argument(..., help="Search query"),
    kb_names: list[str] = typer.Option(
        [], "--kb", "-k", help="KB names to search (repeat for multiple; omit for all)"
    ),
    correlate: bool = typer.Option(
        False, "--correlate", "-c", help="Correlate results by entity identity across KBs"
    ),
    entry_type: str = typer.Option("", "--type", help="Filter by entry type"),
    limit: int = typer.Option(50, "--limit", help="Max results"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Search across all KBs with optional entity correlation."""
    from .cross_kb_search import correlate_results, cross_kb_search

    config = load_config()
    db = PyriteDB(config.settings.index_path)
    try:
        try:
            result = cross_kb_search(
                db,
                query,
                kb_names=kb_names if kb_names else None,
                entry_type=entry_type if entry_type else None,
                limit=limit,
            )
        except QueryTooLongError as e:
            from pyrite.utils.errors import cli_error

            cli_error(
                str(e),
                "json" if output_json else "rich",
                error_code=e.error_code,
                suggestion="shorten the query",
                retryable=False,
            )

        if correlate:
            # Flatten and correlate
            flat = [r for g in result["groups"] for r in g["results"]]
            correlated = correlate_results(flat)
            if output_json:
                typer.echo(json_mod.dumps({"query": query, "correlated": correlated}, indent=2))
                return

            if not correlated:
                console.print("[dim]No results found.[/dim]")
                return

            console.print(f"[bold]Cross-KB Entity Correlation ({len(correlated)} entities)[/bold]")
            console.print()
            for group in correlated:
                kb_label = (
                    f"[green]{group['kb_count']} KBs[/green]" if group["kb_count"] > 1 else "1 KB"
                )
                console.print(
                    f"  [bold]{group['title']}[/bold]  ({kb_label}, importance: {group['max_importance']})"
                )
                for app in group["appearances"]:
                    console.print(f"    - {app['kb_name']}: {app['id']} ({app['entry_type']})")
                console.print()
            return

        if output_json:
            typer.echo(json_mod.dumps(result, indent=2))
            return

        if result["total_count"] == 0:
            console.print("[dim]No results found.[/dim]")
            return

        console.print(f'[bold]Cross-KB Search: "{query}" ({result["total_count"]} results)[/bold]')
        console.print()
        for group in result["groups"]:
            console.print(f"[green]{group['kb_name']}[/green] ({group['count']} results)")
            table = Table(show_header=True)
            table.add_column("ID", style="cyan")
            table.add_column("Type", style="dim")
            table.add_column("Title")
            table.add_column("Imp", justify="right")
            for r in group["results"]:
                table.add_row(
                    r.get("id", ""),
                    r.get("entry_type", ""),
                    r.get("title", ""),
                    str(r.get("importance", "")),
                )
            console.print(table)
            console.print()
    finally:
        db.close()


@investigation_app.command("start")
def start_investigation(
    title: str = typer.Option(..., "--title", help="Investigation title"),
    scope: str = typer.Option("", "--scope", help="Investigation scope/description"),
    questions: list[str] = typer.Option(
        [], "--question", "-q", help="Key questions (repeat for multiple)"
    ),
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Create a new investigation with guided setup."""
    from .investigation_setup import create_investigation

    config = load_config()
    db = PyriteDB(config.settings.index_path)
    try:
        result = create_investigation(
            db=db,
            kb_name=kb_name,
            title=title,
            scope=scope,
            key_questions=questions if questions else None,
        )

        if output_json:
            typer.echo(json_mod.dumps(result, indent=2))
            return

        if "error" in result:
            console.print(f"[red]Error:[/red] {result['error']}")
            raise typer.Exit(1)

        console.print(f"[green]Created investigation:[/green] {result['created']}")
        console.print(f"  Title: {result['title']}")
        if result.get("entities_created"):
            console.print(f"  Initial entities: {len(result['entities_created'])}")
            for ent in result["entities_created"]:
                console.print(f"    - {ent['title']} ({ent['type']})")
    finally:
        db.close()


@investigation_app.command("status")
def investigation_status(
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show investigation status — entity/event/claim counts and unverified claims."""
    from .investigation_setup import build_investigation_status

    config = load_config()
    db = PyriteDB(config.settings.index_path)
    try:
        result = build_investigation_status(db, kb_name)

        if output_json:
            typer.echo(json_mod.dumps(result, indent=2))
            return

        console.print(f"[bold]Investigation Status: {kb_name}[/bold]")
        console.print()
        console.print(f"  Entities: {result['entity_count']}")
        console.print(f"  Events:   {result['event_count']}")
        console.print(f"  Claims:   {result['claim_count']}")
        console.print(f"  Sources:  {result['source_count']}")
        console.print()

        if result["claim_breakdown"]:
            console.print("[bold]Claim Breakdown:[/bold]")
            for status, count in sorted(result["claim_breakdown"].items()):
                console.print(f"  {status}: {count}")
            console.print()

        if result["unverified_claims"]:
            console.print("[yellow]Unverified Claims Needing Attention:[/yellow]")
            for c in result["unverified_claims"]:
                console.print(f"  - {c['title']} (importance: {c['importance']})")
        else:
            console.print("[green]No unverified claims.[/green]")
    finally:
        db.close()


@investigation_app.command("ownership")
def ownership_chain(
    entity_id: str = typer.Argument(..., help="Entity entry ID to trace ownership for"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    depth: int = typer.Option(5, "--depth", help="Maximum chain depth (default 5)"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Trace ownership chains to find beneficial owners and shell companies."""
    from .ownership import trace_ownership_chain

    config = load_config()
    db = PyriteDB(config.settings.index_path)
    try:
        result = trace_ownership_chain(db, kb_name, entity_id, max_depth=depth)

        if output_json:
            typer.echo(json_mod.dumps(result, indent=2))
            return

        entity = result["entity"]
        console.print(f"[bold]Ownership Analysis: {entity['title']}[/bold] ({entity['id']})")
        console.print()

        if not result["chains"]:
            console.print("[dim]No ownership chains found.[/dim]")
            return

        console.print(f"[green]Ownership Chains ({len(result['chains'])}):[/green]")
        for i, chain in enumerate(result["chains"], 1):
            path_str = " -> ".join(
                f"{n['title']} ({n['percentage']}%)" for n in reversed(chain["path"])
            )
            console.print(f"  {i}. {path_str} -> {entity['title']}")
            console.print(f"     Effective ownership: {chain['effective_percentage']:.1f}%")
        console.print()

        if result["beneficial_owners"]:
            console.print("[green]Beneficial Owners:[/green]")
            for bo in result["beneficial_owners"]:
                console.print(f"  - {bo['title']} ({bo['id']})")
            console.print()

        if result["shell_indicators"]:
            console.print("[yellow]Shell Company Indicators:[/yellow]")
            for s in result["shell_indicators"]:
                console.print(f"  ! {s['title']} ({s['id']})")
    finally:
        db.close()


@investigation_app.command("money-flow")
def money_flow(
    entity_id: str = typer.Argument(..., help="Entity ID to trace money flows for"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    direction: str = typer.Option(
        "both", "--direction", "-d", help="Flow direction: outbound, inbound, or both"
    ),
    hops: int = typer.Option(3, "--hops", help="Max transaction hops to follow"),
    from_date: str = typer.Option("", "--from", help="Start date (YYYY-MM-DD)"),
    to_date: str = typer.Option("", "--to", help="End date (YYYY-MM-DD)"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Trace money flows for an entity through transaction chains."""
    from .money_flow import aggregate_flows, trace_money_flow

    config = load_config()
    db = PyriteDB(config.settings.index_path)
    try:
        result = trace_money_flow(
            db,
            kb_name,
            entity_id,
            direction=direction,
            max_hops=hops,
            from_date=from_date,
            to_date=to_date,
        )

        if output_json:
            typer.echo(json_mod.dumps(result, indent=2))
            return

        entity = result["entity"]
        console.print(f"[bold]Money Flow: {entity['title']}[/bold] ({entity['id']})")
        console.print(f"  Direction: {result['direction']}")
        console.print()

        if not result["flows"] and not result["circular_flows"]:
            console.print("[dim]No money flows found.[/dim]")
            return

        if result["flows"]:
            table = Table(title=f"Flow Paths ({len(result['flows'])})")
            table.add_column("#", justify="right", style="dim")
            table.add_column("Path", style="cyan")
            table.add_column("Total", justify="right", style="green")

            for i, flow in enumerate(result["flows"], 1):
                path_parts = []
                for step in flow["path"]:
                    amt = step.get("amount", "?")
                    path_parts.append(f"{step['title']} ({amt})")
                table.add_row(str(i), " → ".join(path_parts), flow["total_amount"])
            console.print(table)

        if result["circular_flows"]:
            console.print()
            console.print("[red bold]Circular Flows Detected:[/red bold]")
            for i, flow in enumerate(result["circular_flows"], 1):
                path_parts = []
                for step in flow["path"]:
                    amt = step.get("amount", "?")
                    path_parts.append(f"{step['title']} ({amt})")
                console.print(
                    f"  {i}. {' → '.join(path_parts)} → [red]BACK TO {entity['title']}[/red]"
                )

        # Also show aggregate summary
        console.print()
        agg = aggregate_flows(db, kb_name, entity_id, from_date=from_date, to_date=to_date)
        if agg["inflows"] or agg["outflows"]:
            console.print("[bold]Aggregate Summary:[/bold]")
            if agg["outflows"]:
                console.print("  [red]Outflows:[/red]")
                for o in agg["outflows"]:
                    console.print(
                        f"    → {o['counterparty']['title']}: {o['total']} ({o['count']} txn(s))"
                    )
            if agg["inflows"]:
                console.print("  [green]Inflows:[/green]")
                for i in agg["inflows"]:
                    console.print(
                        f"    ← {i['counterparty']['title']}: {i['total']} ({i['count']} txn(s))"
                    )
            console.print(f"  Net flow: {agg['net_flow']}")
    finally:
        db.close()


@investigation_app.command("export")
def export_pack(
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    fmt: str = typer.Option("json", "--format", "-f", help="Export format: json or markdown"),
    output: str = typer.Option("", "--output", "-o", help="Output file path (default: stdout)"),
    redact_sources: bool = typer.Option(
        False, "--redact-sources", help="Redact source URLs and titles"
    ),
    min_importance: int = typer.Option(
        0, "--min-importance", help="Minimum importance filter (1-10)"
    ),
):
    """Export investigation as a self-contained pack."""
    from .export import build_investigation_pack, export_as_json, export_as_markdown

    config = load_config()
    db = PyriteDB(config.settings.index_path)
    try:
        pack = build_investigation_pack(
            db,
            kb_name,
            redact_sources=redact_sources,
            min_importance=min_importance,
        )

        if fmt == "markdown":
            content = export_as_markdown(pack)
        else:
            content = export_as_json(pack)

        if output:
            with open(output, "w") as f:
                f.write(content)
            console.print(f"[green]Exported to {output}[/green]")
        else:
            console.print(content)
    finally:
        db.close()


@investigation_app.command("bulk-edges")
def bulk_edges(
    file: str = typer.Option("", "--file", "-f", help="JSON or YAML file with edge definitions"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without creating"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Create multiple connection edges from JSON/YAML input."""
    import sys

    from .bulk import create_edge_batch

    config = load_config()
    db = PyriteDB(config.settings.index_path)
    try:
        # Load edges from file or stdin
        if file:
            with open(file) as f:
                raw = f.read()
        else:
            raw = sys.stdin.read()

        # Parse as JSON first, fall back to YAML
        try:
            data = json_mod.loads(raw)
        except json_mod.JSONDecodeError:
            try:
                import yaml

                data = yaml.safe_load(raw)
            except Exception:
                console.print("[red]Error:[/red] Could not parse input as JSON or YAML")
                raise typer.Exit(1)

        # Accept either a list of edges or {"edges": [...]}
        if isinstance(data, dict) and "edges" in data:
            edges = data["edges"]
        elif isinstance(data, list):
            edges = data
        else:
            console.print('[red]Error:[/red] Input must be a list of edges or {"edges": [...]}')
            raise typer.Exit(1)

        result = create_edge_batch(db, kb_name, edges, dry_run=dry_run)

        from pyrite.utils.errors import exit_unless_whole

        # A dry run that would refuse exits as the real run would (#526).
        if output_json:
            typer.echo(json_mod.dumps(result, indent=2))
            exit_unless_whole(result["errors"], result["created"] + result["skipped"])
            return

        if dry_run:
            console.print("[bold]Dry run — no entries created[/bold]")

        console.print(
            f"  Created: {result['created']}  Skipped: {result['skipped']}  Errors: {result['errors']}"
        )

        if result["entries"]:
            table = Table(title="Edge Results")
            table.add_column("ID", style="cyan")
            table.add_column("Type", style="green")
            table.add_column("Title")
            table.add_column("Status")

            for e in result["entries"]:
                status_style = {
                    "created": "green",
                    "skipped": "yellow",
                    "error": "red",
                    "would_create": "dim",
                }.get(e["status"], "")
                table.add_row(
                    e.get("id") or "-",
                    e.get("type", ""),
                    e.get("title") or "-",
                    f"[{status_style}]{e['status']}[/{status_style}]",
                )
            console.print(table)
        exit_unless_whole(result["errors"], result["created"] + result["skipped"])
    finally:
        db.close()


@investigation_app.command("ftm-import")
def ftm_import(
    file: str = typer.Option(
        ..., "--file", "-f", help="Path to FtM JSON file (one entity per line, or JSON array)"
    ),
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without importing"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Import FollowTheMoney (FtM) entities from a JSON file."""
    from .ftm import import_ftm

    config = load_config()
    db = PyriteDB(config.settings.index_path)
    try:
        # Read and parse file
        with open(file) as f:
            raw = f.read().strip()

        # Support JSON array or newline-delimited JSON
        try:
            data = json_mod.loads(raw)
            if isinstance(data, dict):
                entities = [data]
            elif isinstance(data, list):
                entities = data
            else:
                console.print("[red]Error:[/red] File must contain a JSON array or object")
                raise typer.Exit(1)
        except json_mod.JSONDecodeError:
            # Try newline-delimited JSON
            entities = []
            for line in raw.split("\n"):
                line = line.strip()
                if line:
                    try:
                        entities.append(json_mod.loads(line))
                    except json_mod.JSONDecodeError:
                        console.print(
                            f"[red]Error:[/red] Could not parse line as JSON: {line[:80]}..."
                        )
                        raise typer.Exit(1)

        result = import_ftm(db, kb_name, entities, dry_run=dry_run)

        from pyrite.utils.errors import exit_unless_whole

        if output_json:
            typer.echo(json_mod.dumps(result, indent=2))
            exit_unless_whole(result["errors"], result["imported"] + result["skipped"])
            return

        if dry_run:
            console.print("[bold]Dry run — no entries imported[/bold]")

        console.print(
            f"  Imported: {result['imported']}  Skipped: {result['skipped']}  Unmapped: {result['unmapped']}  Errors: {result['errors']}"
        )

        if result["unmapped_schemas"]:
            console.print(f"  Unmapped schemas: {', '.join(result['unmapped_schemas'])}")

        if result["entries"]:
            table = Table(title="Import Results")
            table.add_column("ID", style="cyan")
            table.add_column("Type", style="green")
            table.add_column("Title")
            table.add_column("Status")

            for e in result["entries"]:
                status_style = {
                    "imported": "green",
                    "skipped": "yellow",
                    "error": "red",
                    "would_import": "dim",
                }.get(e["status"], "")
                table.add_row(
                    e.get("id", ""),
                    e.get("type", ""),
                    e.get("title", ""),
                    f"[{status_style}]{e['status']}[/{status_style}]",
                )
            console.print(table)
        exit_unless_whole(result["errors"], result["imported"] + result["skipped"])
    finally:
        db.close()


@investigation_app.command("ftm-export")
def ftm_export(
    output: str = typer.Option("", "--output", "-o", help="Output file path (default: stdout)"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    types: list[str] = typer.Option(
        [], "--types", "-t", help="Entry types to export (repeat for multiple; omit for all)"
    ),
    output_json: bool = typer.Option(
        False, "--json", help="Output as JSON (always JSON, this flag is for consistency)"
    ),
):
    """Export KB entries as FollowTheMoney (FtM) JSON."""
    import sys

    from .ftm import export_ftm

    config = load_config()
    db = PyriteDB(config.settings.index_path)
    try:
        entry_types = types if types else None
        result = export_ftm(db, kb_name, entry_types=entry_types)

        json_output = json_mod.dumps(result, indent=2)

        if output:
            with open(output, "w") as f:
                f.write(json_output)
                f.write("\n")
            console.print(f"[green]Exported {len(result)} entities to {output}[/green]")
        else:
            sys.stdout.write(json_output)
            sys.stdout.write("\n")
    finally:
        db.close()


@investigation_app.command("promote-claim")
def promote_claim(
    claim_id: str = typer.Argument(..., help="Claim entry ID to promote"),
    edge_type: str = typer.Option(
        ..., "--edge-type", help="Edge type: ownership, membership, funding"
    ),
    kb_name: str = typer.Option(..., "--kb", "-k", help="KB name"),
    owner: str = typer.Option("", "--owner", help="ownership: the owning entity"),
    asset: str = typer.Option("", "--asset", help="ownership: the owned asset"),
    funder: str = typer.Option("", "--funder", help="funding: the funding entity"),
    recipient: str = typer.Option("", "--recipient", help="funding: the recipient entity"),
    person: str = typer.Option("", "--person", help="membership: the member"),
    organization: str = typer.Option("", "--organization", help="membership: the organization"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without creating"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Promote a corroborated claim to an edge-entity.

    The edge type's own required relationship fields must be supplied:
    --owner/--asset for ownership, --funder/--recipient for funding,
    --person/--organization for membership. A promotion missing them is
    refused (the same check runs for --dry-run) rather than creating an
    entry the plugin validator would refuse (#424).
    """
    from pyrite.services.kb_service import KBService

    from .promote import promote_claim_to_edge

    endpoint_fields = {
        k: v
        for k, v in {
            "owner": owner,
            "asset": asset,
            "funder": funder,
            "recipient": recipient,
            "person": person,
            "organization": organization,
        }.items()
        if v
    }

    config = load_config()
    db = PyriteDB(config.settings.index_path)
    kb_service = KBService(config, db)
    try:
        result = promote_claim_to_edge(
            db=db,
            kb_name=kb_name,
            claim_id=claim_id,
            edge_type=edge_type,
            kb_service=kb_service,
            endpoint_fields=endpoint_fields,
            dry_run=dry_run,
        )

        if output_json:
            typer.echo(json_mod.dumps(result, indent=2))
            return

        if "error" in result:
            console.print(f"[red]Error:[/red] {result['error']}")
            raise typer.Exit(1)

        if dry_run:
            console.print("[bold]Dry run — no entry created[/bold]")
            proposed = result["proposed"]
            console.print(f"  Entry ID: {proposed['entry_id']}")
            console.print(f"  Title: {proposed['title']}")
            console.print(f"  Type: {proposed['edge_type']}")
            console.print(f"  Sourced from: {proposed['sourced_from']}")
        else:
            console.print(f"[green]Created[/green] {result['edge_type']} edge: {result['created']}")
            console.print(f"  Title: {result['title']}")
            console.print(f"  Source claim: {result['source_claim']}")
    finally:
        db.close()


@investigation_app.command("dedup")
def dedup(
    kb_names: list[str] = typer.Option(
        [], "--kb", "-k", help="KB names to scan (repeat for multiple; omit for all)"
    ),
    threshold: float = typer.Option(
        0.85, "--threshold", help="Minimum fuzzy match ratio (0.0-1.0)"
    ),
    link: bool = typer.Option(
        False, "--link", help="Auto-link high-confidence matches (confidence >= 0.95)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without creating links"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Find and review duplicate entities across KBs."""
    from .dedup import find_duplicates

    config = load_config()
    db = PyriteDB(config.settings.index_path)
    try:
        groups = find_duplicates(
            db,
            kb_names=kb_names if kb_names else None,
            threshold=threshold,
        )

        if output_json:
            typer.echo(json_mod.dumps(groups, indent=2))
            if link:
                _auto_link_groups(db, groups, dry_run=dry_run)
            return

        if not groups:
            console.print("[dim]No duplicate entities found.[/dim]")
            return

        console.print(f"[bold]Duplicate Entity Groups ({len(groups)})[/bold]")
        console.print()

        for i, group in enumerate(groups, 1):
            canonical = group["canonical"]
            console.print(
                f"  [bold]{i}. {canonical['title']}[/bold]"
                f"  (canonical: {canonical['id']} @ {canonical['kb_name']})"
            )
            for dup in group["duplicates"]:
                conf_pct = f"{dup['confidence'] * 100:.0f}%"
                console.print(
                    f"     - {dup['title']} ({dup['id']} @ {dup['kb_name']}) "
                    f"[{dup['match_type']}, {conf_pct}]"
                )
            console.print()

        if link:
            linked_total = _auto_link_groups(db, groups, dry_run=dry_run)
            if dry_run:
                console.print(f"[dim]Dry run: would link {linked_total} duplicate pair(s).[/dim]")
            else:
                console.print(f"[green]Linked {linked_total} duplicate pair(s).[/green]")
    finally:
        db.close()


def _auto_link_groups(db, groups: list[dict], *, dry_run: bool = False) -> int:
    """Auto-link high-confidence matches (>= 0.95)."""
    from .dedup import create_same_as_links

    total = 0
    for group in groups:
        canonical = group["canonical"]
        high_conf = [
            {"id": d["id"], "kb_name": d["kb_name"]}
            for d in group["duplicates"]
            if d["confidence"] >= 0.95
        ]
        if high_conf:
            result = create_same_as_links(
                db,
                canonical["id"],
                canonical["kb_name"],
                high_conf,
                dry_run=dry_run,
            )
            total += result["linked"]
    return total

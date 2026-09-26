"""
pyrite CLI

Command-line interface for managing knowledge bases.
Split into submodules for maintainability:
- entry_commands: Entry CRUD (get, create, add, update, delete, link)
- browse_commands: Read-only browsing/discovery (list-entries, batch-read, orient, recent, timeline, tags, backlinks)
- kb_commands: Knowledge base management (list, add, remove, discover, validate)
- index_commands: Search index management (build, sync, stats, embed, health)
- search_commands: Search command with file fallback
- repo_commands: Repository collaboration (subscribe, fork, sync, unsubscribe, status)
"""

import json as _json
import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ..config import (
    CONFIG_FILE,
    Repository,
    auto_discover_kbs,
    load_config,
    open_registration_warning,
    save_config,
)
from ..exceptions import (
    EntryNotFoundError,
    KBNotFoundError,
    PyriteError,
    ValidationError,
)
from ..logging import configure_logging
from ..services.access_policy import UNSCOPED
from ..services.kb_service import KBService
from ..utils.errors import PyriteCLIGroup, cli_error
from .browse_commands import register_browse_commands
from .collection_commands import collections_app
from .context import cli_context, open_index_db
from .db_commands import db_app
from .entry_commands import register_entry_commands
from .export_commands import export_app
from .extension_commands import extension_app
from .index_commands import index_app
from .init_command import init_kb
from .kb_commands import kb_app
from .link_commands import links_app
from .protocol_commands import protocol_app
from .qa_commands import qa_app
from .repo_commands import repo_collab_app
from .schema_commands import schema_app
from .search_commands import register_search_command
from .task_commands import task_app

logger = logging.getLogger(__name__)

app = typer.Typer(
    cls=PyriteCLIGroup,
    name="pyrite",
    help="Multi-KB research infrastructure for citizen journalists and AI agents",
    no_args_is_help=True,
    epilog="New to a KB? Run `pyrite orient -k <kb-name>` first -- it summarizes "
    "types, tags, recent changes, and schema in one call.",
)
console = Console()


def _version_callback(value: bool) -> None:
    """Print the version and exit, before anything touches config or a KB.

    Raising `typer.Exit()` here rather than returning is what makes
    `pyrite --version` work with `no_args_is_help=True`: the callback runs
    before Typer decides a bare invocation should print help and exit 2.
    """
    if value:
        from pyrite import __version__

        console.print(__version__)
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        None,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed Pyrite version and exit.",
    ),
) -> None:
    """Multi-KB research infrastructure for citizen journalists and AI agents."""


def _get_svc():
    """Create a KBService instance for CLI commands.

    Deprecated: use cli_context() directly in new code.
    """
    config = load_config()
    db = open_index_db(config)
    return KBService(config, db), db


def _cli_err(exc: Exception, fmt: str = "rich") -> None:
    """Map a caught PyriteError to the right machine code and emit via cli_error.

    Always raises typer.Exit(1).
    """
    code = "ERROR"
    if isinstance(exc, EntryNotFoundError):
        code = "NOT_FOUND"
    elif isinstance(exc, KBNotFoundError):
        code = "KB_NOT_FOUND"
    elif isinstance(exc, ValidationError):
        code = "VALIDATION_FAILED"
    cli_error(str(exc), fmt, error_code=code)


# Register sub-apps
app.add_typer(kb_app, name="kb")
app.add_typer(index_app, name="index")
app.add_typer(collections_app, name="collections")
app.add_typer(qa_app, name="qa")
app.add_typer(db_app, name="db")
app.add_typer(links_app, name="links")
app.add_typer(export_app, name="export")

# Repository management — collaboration app with subscribe/fork/sync/unsubscribe/status/list
# Plus legacy add/remove commands added below
app.add_typer(repo_collab_app, name="repo")

# Extension management
app.add_typer(extension_app, name="extension")
app.add_typer(schema_app, name="schema")
app.add_typer(protocol_app, name="protocol")

# Init command (headless KB init)
app.command("init")(init_kb)

# Authentication commands
auth_app = typer.Typer(help="Authentication (GitHub OAuth)")
app.add_typer(auth_app, name="auth")

# Register search command
register_search_command(app)

# Entry CRUD commands (get, create, add, update, delete, link)
register_entry_commands(app)

# Browse/discovery commands (list-entries, batch-read, orient, recent, timeline, tags, backlinks)
register_browse_commands(app)

# Task management (core, not plugin)
app.add_typer(task_app, name="task")

# Register plugin CLI commands
try:
    from ..plugins import get_registry

    for name, command in get_registry().get_all_cli_commands():
        if hasattr(command, "registered_commands"):
            # It's a Typer app — register as sub-app
            app.add_typer(command, name=name)
        else:
            # It's a single command callback
            app.command(name)(command)
except Exception:
    import logging

    logging.getLogger(__name__).warning("Plugin CLI loading failed", exc_info=True)


def _format_output(data: dict, fmt: str) -> str | None:
    from .output import format_output

    return format_output(data, fmt)


# =========================================================================
# CI command — CI/CD-optimized validation
# =========================================================================


@app.command("ci")
def ci_command(
    kb: str | None = typer.Option(None, "--kb", help="Validate specific KB"),
    output_format: str = typer.Option("text", "--format", help="Output format: text or json"),
    severity: str = typer.Option(
        "warning", "--severity", help="Minimum severity to fail on: error or warning"
    ),
    tier: int = typer.Option(
        1, "--tier", help="Assessment tier: 1=structural, 2=structural+LLM judgment"
    ),
):
    """CI/CD-optimized KB validation. Exits 0 on pass, 1 on failure.

    Validates knowledge base integrity and reports results in a format
    suitable for CI pipelines. Use --format json for machine-readable output.
    Use --tier 2 to include LLM-assisted rubric evaluation (requires AI config).
    """
    from ..services.qa_service import QAService

    config = load_config()

    # Handle no KBs configured
    if not config.knowledge_bases:
        if output_format == "json":
            typer.echo(
                _json.dumps(
                    {
                        "kbs": [],
                        "total_entries": 0,
                        "total_errors": 0,
                        "total_warnings": 0,
                        "result": "pass",
                    }
                )
            )
        else:
            typer.echo("pyrite ci — no KBs configured, nothing to validate.")
        raise typer.Exit(0)

    db = open_index_db(config)
    try:
        # Set up LLM service for tier >= 2
        llm_service = None
        if tier >= 2:
            from ..services.llm_service import LLMService

            llm_service = LLMService(config.settings)
            if not llm_service.status()["configured"]:
                if output_format != "json":
                    typer.echo(
                        "Note: LLM not configured — tier 2 evaluation will skip "
                        "judgment rubric items. Configure ai_provider + ai_api_key "
                        "in pyrite.yaml to enable."
                    )

        qa = QAService(config, db, llm_service=llm_service)

        if kb:
            result = {"kbs": [qa.validate_kb(kb, readable_kbs=UNSCOPED)]}
        else:
            result = qa.validate_all(readable_kbs=UNSCOPED)

        # Aggregate results
        severity_order = {"error": 0, "warning": 1, "info": 2}
        fail_threshold = severity_order.get(severity, 1)

        kb_summaries = []
        total_entries = 0
        total_errors = 0
        total_warnings = 0

        for kb_result in result["kbs"]:
            kb_name = kb_result["kb_name"]
            entries = kb_result["total"]
            issues = kb_result["issues"]

            errors = [i for i in issues if i.get("severity") == "error"]
            warnings = [i for i in issues if i.get("severity") == "warning"]

            total_entries += entries
            total_errors += len(errors)
            total_warnings += len(warnings)

            kb_summaries.append(
                {
                    "kb_name": kb_name,
                    "entries": entries,
                    "errors": len(errors),
                    "warnings": len(warnings),
                    "issues": issues,
                }
            )

        # Determine pass/fail based on severity threshold
        has_failure = False
        if fail_threshold >= 0 and total_errors > 0:
            has_failure = True
        if fail_threshold >= 1 and total_warnings > 0:
            has_failure = True

        result_str = "fail" if has_failure else "pass"

        if output_format == "json":
            json_output = {
                "kbs": [
                    {
                        "kb_name": s["kb_name"],
                        "entries": s["entries"],
                        "errors": s["errors"],
                        "warnings": s["warnings"],
                        "issues": [
                            {
                                "entry_id": i.get("entry_id", ""),
                                "severity": i.get("severity", "info"),
                                "rule": i.get("rule", ""),
                                "field": i.get("field", ""),
                                "message": i.get("message", ""),
                            }
                            for i in s["issues"]
                            if severity_order.get(i.get("severity", "info"), 2) <= fail_threshold
                        ],
                    }
                    for s in kb_summaries
                ],
                "total_entries": total_entries,
                "total_errors": total_errors,
                "total_warnings": total_warnings,
                "result": result_str,
            }
            typer.echo(_json.dumps(json_output, indent=2))
        else:
            # Text output
            kb_count = len(kb_summaries)
            typer.echo(
                f"pyrite ci — {kb_count} KB{'s' if kb_count != 1 else ''}, "
                f"{total_entries} entries validated"
            )
            for s in kb_summaries:
                typer.echo(
                    f"  {s['kb_name']}: {s['entries']} entries, "
                    f"{s['errors']} errors, {s['warnings']} warnings"
                )
                # Show individual issues at/above threshold
                for issue in s["issues"]:
                    sev = issue.get("severity", "info")
                    if severity_order.get(sev, 2) <= fail_threshold:
                        entry_id = issue.get("entry_id", "")
                        message = issue.get("message", "")
                        typer.echo(f"    {sev.upper()}: {entry_id} — {message}")

            # Result line
            if has_failure:
                parts = []
                if total_errors > 0:
                    parts.append(f"{total_errors} error{'s' if total_errors != 1 else ''}")
                if fail_threshold >= 1 and total_warnings > 0:
                    parts.append(f"{total_warnings} warning{'s' if total_warnings != 1 else ''}")
                typer.echo(f"\nResult: FAIL ({', '.join(parts)})")
            else:
                typer.echo("\nResult: PASS")

        if has_failure:
            raise typer.Exit(1)
    finally:
        db.close()


# =============================================================================
# List command (top-level KB listing)
# =============================================================================


@app.command("list")
def list_kbs(
    output_format: str = typer.Option(
        "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """List all knowledge bases."""
    with cli_context() as (config, db, svc):
        kbs = svc.list_kbs()

        if not kbs:
            console.print("[yellow]No knowledge bases configured.[/yellow]")
            return

        formatted = _format_output({"kbs": kbs, "total": len(kbs)}, output_format)
        if formatted is not None:
            typer.echo(formatted)
            return

        table = Table(title="Knowledge Bases")
        table.add_column("Name", style="cyan")
        table.add_column("Type", style="dim")
        table.add_column("Entries", justify="right")
        table.add_column("Path", style="dim")

        for kb in kbs:
            table.add_row(
                kb["name"], kb.get("type", ""), str(kb.get("entries", 0)), kb.get("path", "")
            )

        console.print(table)


# =============================================================================
# Config command
# =============================================================================


@app.command("config")
def show_config():
    """Show current configuration."""
    console.print(f"[bold]Config file:[/bold] {CONFIG_FILE}")
    console.print(f"[bold]Exists:[/bold] {CONFIG_FILE.exists()}")

    if CONFIG_FILE.exists():
        config = load_config()
        console.print(f"\n[bold]Knowledge Bases:[/bold] {len(config.knowledge_bases)}")
        console.print(f"[bold]Subscriptions:[/bold] {len(config.subscriptions)}")
        console.print(f"[bold]AI Provider:[/bold] {config.settings.ai_provider}")


# =============================================================================
# Serve command
# =============================================================================


@app.command("serve")
def serve(
    host: str = typer.Option(None, "--host", "-h", help="Host to bind to"),
    port: int = typer.Option(None, "--port", "-p", help="Port to bind to"),
    dev: bool = typer.Option(
        False, "--dev", help="API-only mode (frontend dev server runs separately on :5173)"
    ),
    build: bool = typer.Option(False, "--build", help="Run npm build before serving"),
):
    """Start the Pyrite web server.

    By default, serves the built web app from web/dist/ and the API.
    Use --dev when developing the frontend (Vite dev server on :5173, API on :8088).
    """
    import subprocess

    import uvicorn

    config = load_config()
    if warning := open_registration_warning(config):
        console.print(f"[yellow]Warning:[/yellow] {warning}")
    host = host or config.settings.host or "127.0.0.1"
    port = port or config.settings.port or 8088

    web_dir = Path(__file__).parent.parent.parent / "web"
    dist_dir = web_dir / "dist"

    if build and web_dir.is_dir():
        console.print("[dim]Building frontend...[/dim]")
        result = subprocess.run(
            ["npm", "run", "build"], cwd=str(web_dir), capture_output=True, text=True
        )
        if result.returncode != 0:
            cli_error(
                f"Frontend build failed:\n{result.stderr}",
                error_code="BUILD_FAILED",
            )
        console.print("[green]Frontend built successfully.[/green]")

    if dev:
        console.print(f"[dim]Starting API server at http://{host}:{port} (dev mode)[/dim]")
        console.print("[dim]Run 'cd web && npm run dev' for the frontend.[/dim]")
    elif dist_dir.is_dir():
        console.print(f"[dim]Starting Pyrite at http://{host}:{port}[/dim]")
    else:
        console.print(f"[dim]Starting API server at http://{host}:{port}[/dim]")
        console.print(
            "[yellow]No web/dist/ found — run 'pyrite serve --build' or "
            "'cd web && npm run build' first.[/yellow]"
        )

    from ..server.api import create_app

    # The bind host is one the app must answer (request_guard admits a
    # non-wildcard `settings.host`), so a --host flag reaches the config too.
    config.settings.host = host
    application = create_app(config=config)
    uvicorn.run(application, host=host, port=port, access_log=False)


# =============================================================================
# Legacy Repository Commands (add/remove for local repos)
# =============================================================================


@repo_collab_app.command("add")
def repo_add(
    path: Path = typer.Argument(..., help="Path to the repository"),
    name: str | None = typer.Option(None, "--name", "-n", help="Name for the repo"),
    remote: str | None = typer.Option(None, "--remote", "-r", help="Git remote URL"),
    auth_method: str = typer.Option(
        "none", "--auth", "-a", help="Auth method (none/ssh/github_oauth/token)"
    ),
    discover: bool = typer.Option(
        True, "--discover/--no-discover", help="Auto-discover KBs in repo"
    ),
):
    """Add a local repository to the registry."""
    config = load_config()

    path = path.expanduser().resolve()
    repo_name = name or path.name

    if config.get_repo(repo_name):
        cli_error(
            f"Repository '{repo_name}' already exists",
            error_code="ALREADY_EXISTS",
            suggestion="Use a different --name or remove the existing repo first.",
        )

    repo = Repository(
        name=repo_name,
        path=path,
        remote=remote,
        auth_method=auth_method,  # type: ignore
    )

    config.add_repo(repo)

    if discover and path.exists():
        discovered = auto_discover_kbs([path])
        for kb in discovered:
            if not config.get_kb(kb.name):
                kb.repo = repo_name
                kb.repo_subpath = str(kb.path.relative_to(path))
                config.add_kb(kb)
                console.print(f"  [green]Discovered KB:[/green] {kb.name} ({kb.kb_type})")

    save_config(config)
    console.print(f"[green]Added repository:[/green] {repo_name}")


@repo_collab_app.command("remove")
def repo_remove(
    name: str = typer.Argument(..., help="Name of the repository"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove a repository from the registry."""
    config = load_config()

    repo = config.get_repo(name)
    if not repo:
        cli_error(
            f"Repository '{name}' not found",
            error_code="NOT_FOUND",
            suggestion="Run `pyrite repo list` to see registered repositories.",
        )

    kbs = config.get_kbs_in_repo(name)

    if not force:
        msg = f"Remove repository '{name}'?"
        if kbs:
            msg += f" ({len(kbs)} KBs will also be removed)"
        if not typer.confirm(msg):
            raise typer.Abort()

    for kb in kbs:
        config.remove_kb(kb.name)

    config.remove_repo(name)
    save_config(config, removed=[kb.name for kb in kbs])

    console.print(f"[green]Removed:[/green] {name}")
    if kbs:
        console.print(f"[dim]Also removed {len(kbs)} KB(s)[/dim]")


# =============================================================================
# Authentication Commands
# =============================================================================


@auth_app.command("status")
def auth_status(
    output_format: str = typer.Option(
        "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """Check GitHub authentication status."""
    from ..github_auth import check_github_auth

    valid, message = check_github_auth()

    formatted = _format_output({"authenticated": valid, "message": message}, output_format)
    if formatted is not None:
        typer.echo(formatted)
        return

    if valid:
        console.print(f"[green]{message}")
    else:
        console.print(f"[yellow]![/yellow] {message}")


@auth_app.command("whoami")
def auth_whoami(
    output_format: str = typer.Option(
        "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """Show current user identity."""
    with cli_context() as (config, db, svc):
        from ..services.user_service import UserService

        user_service = UserService(db)
        user = user_service.get_current_user()

        formatted = _format_output(user, output_format)
        if formatted is not None:
            typer.echo(formatted)
            return

        if user.get("github_id", 0) == 0:
            console.print("[yellow]Not authenticated with GitHub[/yellow]")
            console.print("Identity: [bold]local[/bold] (no GitHub auth)")
            console.print("\nRun 'pyrite auth github-login' to authenticate.")
        else:
            console.print(f"[bold cyan]{user['github_login']}[/bold cyan]")
            if user.get("display_name"):
                console.print(f"  Name: {user['display_name']}")
            if user.get("email"):
                console.print(f"  Email: {user['email']}")
            console.print(f"  GitHub ID: {user['github_id']}")


@auth_app.command("github-login")
def auth_github_login(
    client_id: str | None = typer.Option(None, "--client-id", help="OAuth App client ID"),
    client_secret: str | None = typer.Option(
        None, "--client-secret", help="OAuth App client secret"
    ),
):
    """Authenticate with GitHub using OAuth."""
    from ..github_auth import start_oauth_flow

    success, message = start_oauth_flow(client_id, client_secret)
    if success:
        console.print(f"[green]{message}")
    else:
        console.print(f"[red]{message}")
        raise typer.Exit(1)


@auth_app.command("github-logout")
def auth_github_logout():
    """Remove GitHub authentication."""
    from ..github_auth import clear_github_auth

    if typer.confirm("Remove GitHub authentication?"):
        clear_github_auth()
        console.print("[green]GitHub authentication removed.[/green]")


@auth_app.command("github-setup")
def auth_github_setup():
    """Set up GitHub OAuth App credentials."""
    console.print("\n[bold]GitHub OAuth Setup[/bold]\n")
    console.print("To use GitHub OAuth, you need to create an OAuth App:")
    console.print("1. Go to https://github.com/settings/developers")
    console.print("2. Click 'New OAuth App'")
    console.print("3. Set the callback URL to: http://127.0.0.1:8765/callback")
    console.print("4. Copy the Client ID and Client Secret\n")

    client_id = typer.prompt("Client ID")
    client_secret = typer.prompt("Client Secret", hide_input=True)

    from ..github_auth import GitHubAuth, save_github_auth

    auth = GitHubAuth(
        client_id=client_id,
        client_secret=client_secret,
    )
    save_github_auth(auth)

    console.print("\n[green]Credentials saved.")
    console.print("Run 'pyrite auth github-login' to authenticate.")


# =============================================================================
# MCP Server Commands
# =============================================================================


def _mcp_command_help() -> str:
    """Tool counts derived from tool_schemas.py plus plugin tools registered
    per tier, so this can't drift the way two fixed enumerations already
    have: a hardcoded "8 read tools" once drifted to 29 as orient/batch_read/
    task_* etc. were added; then 29/11/8 (core tools only) drifted from the
    live count once plugins (sw_*, investigation_*, cascade_*, zettel_*,
    wiki_*, social_*) started exposing 41+ tools at read tier alone (#229).
    Mirrors how PyriteMCPServer.__init__ assembles self.tools, without
    constructing a full server (no DB/config needed for --help text)."""
    from ..plugins import get_registry
    from ..server.tool_schemas import ADMIN_TOOLS, READ_TOOLS, WRITE_TOOLS

    registry = get_registry()
    read_total = len(READ_TOOLS) + len(registry.get_all_mcp_tools("read"))
    write_total = len(READ_TOOLS) + len(WRITE_TOOLS) + len(registry.get_all_mcp_tools("write"))
    admin_total = (
        len(READ_TOOLS)
        + len(WRITE_TOOLS)
        + len(ADMIN_TOOLS)
        + len(registry.get_all_mcp_tools("admin"))
    )

    return (
        "Start the MCP (Model Context Protocol) server.\n\n"
        "Runs the server over stdio for integration with Claude Code and "
        "other MCP-compatible AI agents. Tier controls which tools are "
        "exposed (counts include plugin tools registered for that tier):\n\n"
        f"- read: {read_total} tools (kb_orient, kb_search, kb_get, "
        "kb_timeline, kb_backlinks, kb_tags, kb_stats, kb_schema, task_*, "
        "sw_*/investigation_*/cascade_* and other plugin tools, and more)\n"
        f"- write: read tier + {write_total - read_total} more (kb_create, "
        "kb_update, kb_delete, kb_link, task_claim, and more)\n"
        f"- admin: write tier + {admin_total - write_total} more (kb_registry_*, "
        "kb_commit, kb_push, and more)"
    )


@app.command(
    "mcp",
    help=_mcp_command_help(),
    epilog="orient (kb_orient) is the recommended first call in any session.",
)
def mcp_server(
    tier: str = typer.Option(
        "write",
        "--tier",
        help="Tool tier to expose: read, write, or admin",
    ),
):
    from ..server.mcp_server import PyriteMCPServer

    if tier not in PyriteMCPServer.VALID_TIERS:
        cli_error(
            f"Invalid tier: {tier!r}",
            "rich",
            error_code="INVALID_TIER",
            suggestion=f"choose one of: {', '.join(PyriteMCPServer.VALID_TIERS)}",
        )

    # stderr, not stdout -- stdout carries the MCP protocol stream itself.
    Console(stderr=True).print(f"[dim]Starting MCP server ({tier} tier) on stdio...[/dim]")
    server = PyriteMCPServer(tier=tier)
    try:
        server.run_stdio()
    finally:
        server.close()


@app.command("mcp-setup")
def mcp_setup(
    config_path: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to Claude Code config (default: ~/.claude/claude_desktop_config.json)",
    ),
):
    """
    Set up MCP server integration with Claude Code.

    Adds pyrite to Claude Code's MCP server configuration.
    """
    import json
    import shutil

    if config_path is None:
        config_path = Path.home() / ".claude" / "claude_desktop_config.json"

    config_path = config_path.expanduser()

    pyrite_exe = shutil.which("pyrite-admin")
    if not pyrite_exe:
        pyrite_exe = "python -m pyrite.admin_cli"
        console.print("[yellow]Warning: pyrite-admin not in PATH, using module path[/yellow]")

    if config_path.exists():
        with open(config_path) as f:
            claude_config = json.load(f)
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        claude_config = {}

    if "mcpServers" not in claude_config:
        claude_config["mcpServers"] = {}

    claude_config["mcpServers"]["pyrite"] = {
        "command": pyrite_exe if "python" not in pyrite_exe else "python",
        "args": ["-m", "pyrite.admin_cli", "mcp"] if "python" in pyrite_exe else ["mcp"],
        "env": {},
    }

    with open(config_path, "w") as f:
        json.dump(claude_config, f, indent=2)

    console.print(f"[green]MCP server configured in {config_path}[/green]")
    console.print("\nRestart Claude Code to load the new MCP server.")
    console.print("\nAvailable tools:")
    console.print("  • kb_list - List knowledge bases")
    console.print("  • kb_search - Full-text search across KBs")
    console.print("  • kb_get - Get entry by ID")
    console.print("  • kb_schema - Get KB schema for agents")
    console.print("  • kb_create - Create new entry")
    console.print("  • kb_update - Update entry")
    console.print("  • kb_delete - Delete entry")
    console.print("  • kb_timeline - Query timeline events")
    console.print("  • kb_backlinks - Find entries linking to an entry")
    console.print("  • kb_tags - Get all tags with counts")
    console.print("  • kb_stats - Get index statistics")
    console.print("  • kb_index_sync - Sync index (admin tier)")
    console.print("  • kb_manage - Manage KBs (admin tier)")


# =============================================================================
# Import command
# =============================================================================


@app.command("import")
def import_entries(
    file_path: Path = typer.Argument(..., help="Path to JSON or YAML file"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="Target knowledge base"),
    fmt: str = typer.Option(None, "--format", help="json or yaml (auto-detected from extension)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate without creating entries"),
    allow_undeclared: bool = typer.Option(
        False,
        "--allow-undeclared",
        help="Allow entry types not declared in the KB's kb.yaml (the entries "
        "will be flagged by `pyrite index health`).",
    ),
):
    """Bulk import entries from a JSON or YAML file.

    Every record goes through the same write pipeline as `pyrite create`
    (declared type, existing id, schema and plugin validation, the ADR-0034
    truncated-body refusal). A refused record fails on its own, its siblings
    are imported, and the command exits 1.
    """
    from ..formats.importers import get_importer_registry

    if not file_path.exists():
        cli_error(
            f"File not found: {file_path}",
            error_code="NOT_FOUND",
        )

    # Auto-detect format from extension
    if fmt is None:
        suffix = file_path.suffix.lower()
        format_map = {".json": "json", ".yaml": "yaml", ".yml": "yaml"}
        fmt = format_map.get(suffix)
        if fmt is None:
            cli_error(
                f"Cannot detect format from extension '{suffix}'.",
                error_code="VALIDATION_FAILED",
                suggestion="Use --format json or --format yaml.",
            )

    registry = get_importer_registry()
    importer = registry.get(fmt)
    if importer is None:
        cli_error(
            f"Unknown format '{fmt}'.",
            error_code="VALIDATION_FAILED",
            suggestion=f"Available: {', '.join(registry.available_formats())}",
        )

    data = file_path.read_text(encoding="utf-8")
    try:
        parsed = importer(data)
    except Exception as e:
        cli_error(f"Error parsing file: {e}", error_code="VALIDATION_FAILED")

    if not parsed:
        console.print("[yellow]No entries found in file.[/yellow]")
        raise typer.Exit(0)

    from rich.text import Text

    def _refusal_line(label: str, record: dict, r: dict) -> Text:
        # Text, not markup: the code's brackets and the message are data.
        title = record.get("title", "?") if isinstance(record, dict) else "?"
        line = Text(f"  {label} ")
        line.append(f"[{r.get('error_code', 'ERROR')}]", style="red")
        line.append(f": {title}: {r.get('error', 'unknown error')}")
        return line

    with cli_context() as (config, db, svc):
        try:
            results = svc.bulk_create_entries(
                kb_name, parsed, allow_undeclared=allow_undeclared, validate_only=dry_run
            )
        except PyriteError as e:
            _cli_err(e)

        if dry_run:
            console.print(f"[bold]Dry run:[/bold] {len(parsed)} entries parsed")
            for i, (record, r) in enumerate(zip(parsed, results, strict=True)):
                title = record.get("title", "Untitled")
                etype = record.get("entry_type", "note")
                console.print(f"  {i + 1}. [{etype}] {title}", markup=False)
                if not r.get("valid"):
                    console.print(_refusal_line("Would refuse", record, r))
            console.print("\n[dim]No entries were created (--dry-run).[/dim]")
            return

        created = 0
        failed = 0
        for record, r in zip(parsed, results, strict=True):
            if r.get("created"):
                created += 1
                console.print(f"  [green]Created:[/green] {r['entry_id']}")
            else:
                failed += 1
                console.print(_refusal_line("Failed", record, r))

        console.print(f"\n[bold]Imported {created} entries[/bold]", end="")
        if failed:
            console.print(f" [red]({failed} failed)[/red]")
        else:
            console.print()

        # A refused record is the write pipeline saying no -- a truncated
        # body, an existing id, an undeclared type, a schema violation. Exit
        # non-zero so a script or agent cannot read "Imported N" off a run
        # that silently dropped records.
        if failed:
            raise typer.Exit(1)


# =============================================================================
# Readme command
# =============================================================================


@app.command("readme")
def generate_readme_cmd(
    kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base"),
    output: Path = typer.Option(None, "--output", "-o", help="Write to file path"),
    write: bool = typer.Option(False, "--write", "-w", help="Write to KB's README.md"),
):
    """Generate a README.md for a knowledge base."""
    with cli_context() as (config, db, svc):
        try:
            readme = svc.generate_readme(kb_name)
        except PyriteError as e:
            _cli_err(e)

        if write:
            kb_config = config.get_kb(kb_name)
            if not kb_config:
                cli_error(
                    f"KB not found: {kb_name}",
                    error_code="KB_NOT_FOUND",
                    suggestion="Run `pyrite kb list` to see available KBs.",
                )
            readme_path = kb_config.path / "README.md"
            readme_path.write_text(readme, encoding="utf-8")
            console.print(f"[green]Written:[/green] {readme_path}")
        elif output:
            output.write_text(readme, encoding="utf-8")
            console.print(f"[green]Written:[/green] {output}")
        else:
            typer.echo(readme)


def main():
    configure_logging()
    app()


if __name__ == "__main__":
    main()

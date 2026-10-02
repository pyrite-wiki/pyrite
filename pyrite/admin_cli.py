"""
pyrite-admin: Admin CLI for pyrite

Infrastructure and policy management: KB create/remove, index management,
repo subscribe/fork/sync, authentication, config, schema enforcement.

For write operations: pyrite
For read-only operations: pyrite-read
"""

from pathlib import Path

import typer
from rich.console import Console

from .config import (
    CONFIG_FILE,
    KBConfig,
    Repository,
    auto_discover_kbs,
    load_config,
    save_config,
)
from .logging import configure_entry_point_logging, logging_epilog
from .utils.errors import PyriteCLIGroup

app = typer.Typer(
    cls=PyriteCLIGroup,
    name="pyrite-admin",
    help="Pyrite admin CLI — KB management, indexing, repos, auth, config",
    no_args_is_help=True,
    epilog=logging_epilog("pyrite-admin"),
)
console = Console()

# =============================================================================
# Sub-apps
# =============================================================================

kb_app = typer.Typer(help="Knowledge base management")
index_app = typer.Typer(help="Search index management")
repo_app = typer.Typer(help="Repository collaboration")
auth_app = typer.Typer(help="Authentication (GitHub OAuth)")
config_app = typer.Typer(help="Configuration management")
user_app = typer.Typer(help="Web users (auth-enabled servers)")

app.add_typer(kb_app, name="kb")
app.add_typer(index_app, name="index")
app.add_typer(repo_app, name="repo")
app.add_typer(auth_app, name="auth")
app.add_typer(config_app, name="config")
app.add_typer(user_app, name="user")


# =============================================================================
# KB Management
# =============================================================================


@kb_app.command("list")
def kb_list(
    kb_type: str | None = typer.Option(None, "--type", "-t", help="Filter by type"),
):
    """List all knowledge bases."""
    config = load_config()

    if kb_type:
        kbs = config.list_kbs(kb_type)
    else:
        kbs = config.knowledge_bases

    if not kbs:
        console.print("[yellow]No knowledge bases configured.[/yellow]")
        return

    for kb in kbs:
        console.print(f"  [bold cyan]{kb.name}[/bold cyan] ({kb.kb_type})")
        console.print(f"    Path: {kb.path}")
        if kb.description:
            console.print(f"    {kb.description}")


@kb_app.command("add")
def kb_add(
    path: Path = typer.Argument(..., help="Path to the KB directory"),
    name: str | None = typer.Option(None, "--name", "-n", help="Name for the KB"),
    kb_type: str = typer.Option("generic", "--type", "-t", help="KB type"),
    description: str = typer.Option("", "--desc", "-d", help="Description"),
):
    """Register a knowledge base."""
    config = load_config()

    path = path.expanduser().resolve()
    kb_name = name or path.name

    if config.get_kb(kb_name):
        console.print(f"[red]Error:[/red] KB '{kb_name}' already exists")
        raise typer.Exit(1)

    kb = KBConfig(
        name=kb_name,
        path=path,
        kb_type=kb_type,
        description=description,
    )

    config.add_kb(kb)
    save_config(config)
    console.print(f"[green]Added KB:[/green] {kb_name}")


@kb_app.command("remove")
def kb_remove(name: str = typer.Argument(..., help="KB name")):
    """Remove a KB from the registry."""
    config = load_config()

    if not config.get_kb(name):
        console.print(f"[red]Error:[/red] KB '{name}' not found")
        raise typer.Exit(1)

    config.remove_kb(name)
    save_config(config, removed=[name])
    console.print(f"[green]Removed:[/green] {name}")


@kb_app.command("discover")
def kb_discover(path: Path = typer.Argument(".", help="Path to search")):
    """Auto-discover KBs by looking for kb.yaml files."""
    config = load_config()
    path = path.expanduser().resolve()

    discovered = auto_discover_kbs([path])

    if not discovered:
        console.print("[yellow]No KBs found.[/yellow]")
        return

    for kb in discovered:
        if not config.get_kb(kb.name):
            config.add_kb(kb)
            console.print(f"  [green]Discovered:[/green] {kb.name} ({kb.kb_type})")
        else:
            console.print(f"  [dim]Already registered:[/dim] {kb.name}")

    save_config(config)


@kb_app.command("validate")
def kb_validate(name: str = typer.Argument(..., help="KB name")):
    """Validate a KB configuration."""
    config = load_config()
    kb = config.get_kb(name)

    if not kb:
        console.print(f"[red]Error:[/red] KB '{name}' not found")
        raise typer.Exit(1)

    errors = kb.validate()
    if errors:
        for err in errors:
            console.print(f"  [red]Error:[/red] {err}")
        raise typer.Exit(1)
    else:
        console.print(f"[green]KB '{name}' is valid.[/green]")


# =============================================================================
# Index Management
# =============================================================================


@index_app.command("build")
def index_build(
    kb_name: str | None = typer.Argument(None, help="KB to index (all if not specified)"),
    force: bool = typer.Option(False, "--force", "-f", help="Force full rebuild"),
    with_attribution: bool = typer.Option(
        False, "--with-attribution", help="Extract git attribution"
    ),
):
    """Build or rebuild the search index."""
    from .cli.context import index_db_context
    from .storage import IndexManager

    config = load_config()
    with index_db_context(config) as db:
        index_mgr = IndexManager(db, config)

        if kb_name:
            count = index_mgr.index_kb(kb_name)
            console.print(f"[green]Indexed {count} entries from {kb_name}[/green]")
        else:
            results = index_mgr.index_all()
            total = sum(results.values())
            console.print(f"[green]Indexed {total} entries across {len(results)} KBs[/green]")


@index_app.command("sync")
def index_sync(
    kb_name: str | None = typer.Argument(None, help="KB to sync"),
):
    """Incremental index sync with file changes."""
    from .cli.context import index_db_context
    from .storage import IndexManager

    config = load_config()
    with index_db_context(config) as db:
        index_mgr = IndexManager(db, config)

        results = index_mgr.sync_incremental(kb_name)
        console.print(
            f"[green]Synced:[/green] +{results['added']} -{results['removed']} ~{results['updated']}"
        )


@index_app.command("stats")
def index_stats(kb_name: str | None = typer.Argument(None, help="KB name")):
    """Show index statistics."""
    from .cli.context import index_db_context
    from .storage import IndexManager

    config = load_config()
    with index_db_context(config) as db:
        index_mgr = IndexManager(db, config)

        stats = index_mgr.get_index_stats()
        console.print(f"[bold]Total entries:[/bold] {stats.get('total_entries', 0)}")
        console.print(f"[bold]Total tags:[/bold] {stats.get('total_tags', 0)}")
        console.print(f"[bold]Total links:[/bold] {stats.get('total_links', 0)}")


@index_app.command("embed")
def index_embed(kb_name: str = typer.Argument(..., help="KB to generate embeddings for")):
    """Generate vector embeddings for semantic search."""

    from .cli.context import index_db_context

    config = load_config()
    with index_db_context(config) as db:
        try:
            from .services.embedding_service import EmbeddingService

            embed_svc = EmbeddingService(db)
            count = embed_svc.embed_kb(kb_name)
            console.print(f"[green]Generated embeddings for {count} entries[/green]")
        except ImportError:
            console.print("[red]Error:[/red] Install semantic extras: pip install pyrite[semantic]")


@index_app.command("health")
def index_health():
    """Check index health and consistency."""
    from .cli.context import index_db_context
    from .storage import IndexManager

    config = load_config()
    with index_db_context(config) as db:
        index_mgr = IndexManager(db, config)

        health = index_mgr.check_health()
        is_healthy = not (
            health["missing_files"] or health["unindexed_files"] or health["stale_entries"]
        )

        if is_healthy:
            console.print("[green]Index is healthy.[/green]")
        else:
            console.print("[yellow]Index has issues:[/yellow]")
            if health["missing_files"]:
                console.print(f"  Missing files: {len(health['missing_files'])}")
            if health["unindexed_files"]:
                console.print(f"  Unindexed files: {len(health['unindexed_files'])}")
            if health["stale_entries"]:
                console.print(f"  Stale entries: {len(health['stale_entries'])}")


# =============================================================================
# Repository Collaboration
# =============================================================================


@repo_app.command("subscribe")
def repo_subscribe(
    url: str = typer.Argument(..., help="Git remote URL"),
    name: str | None = typer.Option(None, "--name", "-n", help="Local name"),
    branch: str = typer.Option("main", "--branch", "-b", help="Branch"),
):
    """Subscribe to a remote repository."""
    from .cli.context import index_db_context
    from .services.repo_service import RepoService

    config = load_config()
    with index_db_context(config) as db:
        svc = RepoService(config, db)
        result = svc.subscribe(url, name=name, branch=branch)
        console.print(f"[green]Subscribed:[/green] {result['name']}")


@repo_app.command("fork")
def repo_fork(url: str = typer.Argument(..., help="GitHub repo URL to fork")):
    """Fork a GitHub repository and clone locally."""
    from .cli.context import index_db_context
    from .services.repo_service import RepoService

    config = load_config()
    with index_db_context(config) as db:
        svc = RepoService(config, db)
        result = svc.fork(url)
        console.print(f"[green]Forked and cloned:[/green] {result['name']}")


@repo_app.command("sync")
def repo_sync(repo_name: str = typer.Argument(..., help="Repository name")):
    """Sync a repository with its remote."""
    from .cli.context import index_db_context
    from .services.repo_service import RepoService

    config = load_config()
    with index_db_context(config) as db:
        svc = RepoService(config, db)
        result = svc.sync(repo_name)
        console.print(f"[green]Synced:[/green] {repo_name} ({result.get('status', 'ok')})")


@repo_app.command("unsubscribe")
def repo_unsubscribe(repo_name: str = typer.Argument(..., help="Repository name")):
    """Unsubscribe from a repository."""
    from .cli.context import index_db_context
    from .services.repo_service import RepoService

    config = load_config()
    with index_db_context(config) as db:
        svc = RepoService(config, db)
        svc.unsubscribe(repo_name)
        console.print(f"[green]Unsubscribed:[/green] {repo_name}")


@repo_app.command("status")
def repo_status():
    """Show repository subscription status."""
    config = load_config()

    if not config.repositories:
        console.print("[yellow]No repositories registered.[/yellow]")
        return

    for repo in config.repositories:
        console.print(f"  [bold cyan]{repo.name}[/bold cyan]")
        console.print(f"    Path: {repo.path}")
        if repo.remote:
            console.print(f"    Remote: {repo.remote}")
        console.print(f"    Branch: {repo.branch}")


@repo_app.command("list")
def repo_list():
    """List all registered repositories."""
    repo_status()


@repo_app.command("add")
def repo_add(
    path: Path = typer.Argument(..., help="Path to the repository"),
    name: str | None = typer.Option(None, "--name", "-n", help="Name for the repo"),
    remote: str | None = typer.Option(None, "--remote", "-r", help="Git remote URL"),
    auth_method: str = typer.Option("none", "--auth", "-a", help="Auth method"),
    discover: bool = typer.Option(True, "--discover/--no-discover", help="Auto-discover KBs"),
):
    """Add a local repository to the registry."""
    config = load_config()

    path = path.expanduser().resolve()
    repo_name = name or path.name

    if config.get_repo(repo_name):
        console.print(f"[red]Error:[/red] Repository '{repo_name}' already exists")
        raise typer.Exit(1)

    repo = Repository(
        name=repo_name,
        path=path,
        remote=remote,
        auth_method=auth_method,
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


@repo_app.command("remove")
def repo_remove(
    name: str = typer.Argument(..., help="Repository name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove a repository from the registry."""
    config = load_config()

    repo = config.get_repo(name)
    if not repo:
        console.print(f"[red]Error:[/red] Repository '{name}' not found")
        raise typer.Exit(1)

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
# Authentication
# =============================================================================


@auth_app.command("whoami")
def auth_whoami():
    """Show current user identity."""

    from .cli.context import index_db_context

    config = load_config()
    with index_db_context(config) as db:
        from .services.user_service import UserService

        user_service = UserService(db)
        user = user_service.get_current_user()

        if user.get("github_id", 0) == 0:
            console.print("[yellow]Not authenticated with GitHub[/yellow]")
            console.print("Identity: [bold]local[/bold] (no GitHub auth)")
            console.print("\nRun 'pyrite-admin auth login' to authenticate.")
        else:
            console.print(f"[bold cyan]{user['github_login']}[/bold cyan]")
            if user.get("display_name"):
                console.print(f"  Name: {user['display_name']}")
            if user.get("email"):
                console.print(f"  Email: {user['email']}")


@auth_app.command("login")
def auth_login(
    client_id: str | None = typer.Option(None, "--client-id"),
    client_secret: str | None = typer.Option(None, "--client-secret"),
):
    """Authenticate with GitHub using OAuth."""
    from .github_auth import start_oauth_flow

    success, message = start_oauth_flow(client_id, client_secret)
    if success:
        console.print(f"[green]{message}")
    else:
        console.print(f"[red]{message}")
        raise typer.Exit(1)


@auth_app.command("logout")
def auth_logout():
    """Remove GitHub authentication."""
    from .github_auth import clear_github_auth

    if typer.confirm("Remove GitHub authentication?"):
        clear_github_auth()
        console.print("[green]GitHub authentication removed.[/green]")


# =============================================================================
# Configuration
# =============================================================================


@config_app.command("show")
def config_show():
    """Show current configuration."""
    console.print(f"[bold]Config file:[/bold] {CONFIG_FILE}")
    console.print(f"[bold]Exists:[/bold] {CONFIG_FILE.exists()}")

    if CONFIG_FILE.exists():
        config = load_config()
        console.print(f"\n[bold]Knowledge Bases:[/bold] {len(config.knowledge_bases)}")
        console.print(f"[bold]Repositories:[/bold] {len(config.repositories)}")
        console.print(f"[bold]Subscriptions:[/bold] {len(config.subscriptions)}")
        console.print(f"[bold]AI Provider:[/bold] {config.settings.ai_provider}")
        console.print(f"[bold]Index Path:[/bold] {config.settings.index_path}")


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config key (e.g., ai_provider)"),
    value: str = typer.Argument(..., help="Config value"),
):
    """Set a configuration value."""
    config = load_config()

    if hasattr(config.settings, key):
        setattr(config.settings, key, value)
        save_config(config)
        console.print(f"[green]Set {key} = {value}[/green]")
    else:
        console.print(f"[red]Error:[/red] Unknown config key '{key}'")
        raise typer.Exit(1)


# =============================================================================
# Schema
# =============================================================================


@app.command("schema")
def schema_show(kb_name: str = typer.Argument(..., help="KB name")):
    """Show schema for a KB (agent-friendly output)."""
    import json

    config = load_config()
    kb = config.get_kb(kb_name)

    if not kb:
        console.print(f"[red]Error:[/red] KB '{kb_name}' not found")
        raise typer.Exit(1)

    schema = kb.kb_schema
    agent_schema = schema.to_agent_schema()

    typer.echo(json.dumps(agent_schema, indent=2))


# =============================================================================
# MCP Server
# =============================================================================


@app.command("mcp")
def mcp_server(
    tier: str = typer.Option("admin", "--tier", "-t", help="Permission tier: read, write, admin"),
):
    """Start an MCP server at the specified permission tier."""
    import sys

    from .server.mcp_server import PyriteMCPServer

    print(f"Starting MCP server (tier={tier}) on stdio...", file=sys.stderr)
    server = PyriteMCPServer(tier=tier)
    try:
        server.run_stdio()
    finally:
        server.close()


@app.command("mcp-setup")
def mcp_setup(
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    tier: str = typer.Option("write", "--tier", "-t", help="Default MCP tier: read, write, admin"),
):
    """Set up MCP server integration with Claude Code."""
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

    from .services.access_policy import ROLES

    # Register one server per tier
    for t in ROLES:
        server_name = f"pyrite-{t}"
        if "python" in str(pyrite_exe):
            claude_config["mcpServers"][server_name] = {
                "command": "python",
                "args": ["-m", "pyrite.admin_cli", "mcp", "--tier", t],
                "env": {},
            }
        else:
            claude_config["mcpServers"][server_name] = {
                "command": pyrite_exe,
                "args": ["mcp", "--tier", t],
                "env": {},
            }

    with open(config_path, "w") as f:
        json.dump(claude_config, f, indent=2)

    console.print(f"[green]MCP servers configured in {config_path}[/green]")
    console.print("\nRegistered three MCP servers:")
    console.print("  pyrite-read  — Search, browse, retrieve (safe for any agent)")
    console.print("  pyrite-write — Read + create/update/delete entries")
    console.print("  pyrite-admin — Write + KB management, indexing, repos, config")
    console.print("\nRestart Claude Code to load the new MCP servers.")


# =============================================================================
# Web users
# =============================================================================


@user_app.command("create")
def user_create(
    username: str = typer.Argument(..., help="Login name"),
    role: str = typer.Option("read", "--role", "-r", help="Global role: read, write or admin"),
    display_name: str | None = typer.Option(None, "--display-name", help="Shown in the web UI"),
    password: str = typer.Option(
        ...,
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
        help="Prompted for when omitted (at least 8 characters)",
    ),
):
    """Create a web user in this server's index.

    The documented way to make the first admin: with auth enabled, web
    registration and OAuth sign-up stay closed until an admin exists, and
    nobody becomes admin by registering first.

        pyrite-admin user create alice --role admin
    """
    from .cli.context import index_db_context
    from .services.auth_service import AuthService

    config = load_config()
    with index_db_context(config) as db:
        try:
            user = AuthService(db, config.settings.auth).create_user(
                username, password, role=role, display_name=display_name
            )
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1) from None
    console.print(f"[green]Created user[/green] {user['username']} (role: {user['role']})")


def main():
    configure_entry_point_logging(app=app)
    app()


if __name__ == "__main__":
    main()

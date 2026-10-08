"""
Repository CLI Commands — Phase 7 collaboration.

Extended repo management: subscribe, fork, sync, unsubscribe, status.
"""

import typer
from rich.console import Console
from rich.table import Table

from ..config import load_config
from ..utils.errors import cli_error

repo_collab_app = typer.Typer(help="Repository collaboration commands")
console = Console()


def _format_output(data: dict, fmt: str) -> str | None:
    from .output import format_output

    return format_output(data, fmt)


def _get_db_and_services():
    """Helper to create DB and service instances."""
    from ..services.repo_service import RepoService
    from ..services.user_service import UserService
    from .context import open_index_db

    config = load_config()
    db = open_index_db(config)
    user_service = UserService(db)
    repo_service = RepoService(config, db, user_service=user_service)
    return config, db, repo_service, user_service


@repo_collab_app.command("subscribe")
def repo_subscribe(
    url: str = typer.Argument(..., help="GitHub repository URL"),
    name: str | None = typer.Option(None, "--name", "-n", help="Override repo name"),
    branch: str = typer.Option("main", "--branch", "-b", help="Branch to clone"),
):
    """Subscribe to a remote repository (shallow clone, read-only)."""
    config, db, repo_service, _ = _get_db_and_services()
    try:
        console.print(f"[dim]Subscribing to {url}...[/dim]")
        result = repo_service.subscribe(url, name=name, branch=branch)

        if result["success"]:
            console.print(f"[green]Subscribed to {result['repo']}[/green]")
            console.print(f"  Path: {result['path']}")
            console.print(f"  KBs discovered: {len(result['kbs'])}")
            for kb in result["kbs"]:
                console.print(f"    - {kb}")
            console.print(f"  Entries indexed: {result['entries_indexed']}")
        else:
            cli_error(
                result["error"],
                error_code="SUBSCRIBE_FAILED",
                retryable=True,
            )
    finally:
        db.close()


@repo_collab_app.command("fork")
def repo_fork(
    url: str = typer.Argument(..., help="GitHub repository URL to fork"),
):
    """Fork a repo on GitHub, clone the fork, and set up upstream tracking."""
    config, db, repo_service, _ = _get_db_and_services()
    try:
        console.print(f"[dim]Forking {url}...[/dim]")
        result = repo_service.fork_and_subscribe(url)

        if result["success"]:
            console.print(f"[green]Forked and cloned {result['repo']}[/green]")
            console.print(f"  Path: {result['path']}")
            console.print(f"  Upstream: {result.get('upstream', 'N/A')}")
            if result.get("kbs"):
                console.print(f"  KBs: {', '.join(result['kbs'])}")
        else:
            cli_error(
                result["error"],
                error_code="FORK_FAILED",
                retryable=True,
            )
    finally:
        db.close()


@repo_collab_app.command("sync")
def repo_sync(
    name: str | None = typer.Argument(None, help="Repository to sync (all if omitted)"),
):
    """Sync repositories: pull + re-index changed files with attribution."""
    config, db, repo_service, _ = _get_db_and_services()
    try:
        console.print("[dim]Syncing...[/dim]")
        result = repo_service.sync(repo_name=name)

        if not result["success"]:
            cli_error(
                result.get("error", "Unknown error"),
                error_code="SYNC_FAILED",
                retryable=True,
            )

        repos = result.get("repos", {})
        for repo_name, info in repos.items():
            if info["success"]:
                console.print(f"[green]{repo_name}:[/green] {info['message']}")
                if info.get("changes", 0) > 0:
                    console.print(f"  Changed files: {info['changes']}")
                    console.print(f"  Re-indexed: {info.get('reindexed', 0)}")
            else:
                console.print(f"[red]{repo_name}:[/red] {info['error']}")
        from ..utils.errors import exit_unless_whole

        failed = sum(1 for info in repos.values() if not info["success"])
        exit_unless_whole(failed, len(repos) - failed)
    finally:
        db.close()


@repo_collab_app.command("unsubscribe")
def repo_unsubscribe(
    name: str = typer.Argument(..., help="Repository name to unsubscribe from"),
    delete_files: bool = typer.Option(False, "--delete-files", help="Also delete cloned files"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove a repository from the workspace."""
    if not force:
        msg = f"Unsubscribe from '{name}'?"
        if delete_files:
            msg += " (files will be deleted)"
        if not typer.confirm(msg):
            raise typer.Abort()

    config, db, repo_service, _ = _get_db_and_services()
    try:
        result = repo_service.unsubscribe(name, delete_files=delete_files)

        if result["success"]:
            console.print(f"[green]Unsubscribed from {result['repo']}[/green]")
            if result["kbs_removed"]:
                console.print(f"  KBs removed: {', '.join(result['kbs_removed'])}")
        else:
            cli_error(
                result["error"],
                error_code="UNSUBSCRIBE_FAILED",
            )
    finally:
        db.close()


@repo_collab_app.command("status")
def repo_status(
    name: str = typer.Argument(..., help="Repository name"),
    output_format: str = typer.Option(
        "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """Show detailed status for a repository."""
    config, db, repo_service, _ = _get_db_and_services()
    try:
        status = repo_service.get_repo_status(name)

        if not status.get("name"):
            cli_error(
                status.get("error", f"Repository not found: {name}"),
                output_format,
                error_code="REPO_NOT_FOUND",
                suggestion="run `pyrite repo list` to see subscribed repos",
            )

        formatted = _format_output(status, output_format)
        if formatted is not None:
            typer.echo(formatted)
            return

        console.print(f"\n[bold cyan]{status['name']}[/bold cyan]")
        console.print(f"  Path: {status['local_path']}")
        if status.get("remote_url"):
            console.print(f"  Remote: {status['remote_url']}")
        if status.get("current_branch"):
            console.print(f"  Branch: {status['current_branch']}")
        if status.get("head_commit"):
            console.print(f"  HEAD: {status['head_commit'][:12]}")
        if status.get("last_synced"):
            console.print(f"  Last synced: {status['last_synced']}")
        if status.get("is_fork"):
            console.print(f"  Fork of: repo ID {status.get('upstream_repo_id')}")

        console.print(f"\n  KBs: {status.get('kb_count', 0)}")
        for kb_name in status.get("kb_names", []):
            count = repo_service.kb_entry_count(kb_name)
            console.print(f"    - {kb_name} ({count} entries)")

        contributors = status.get("contributors", [])
        if contributors:
            console.print(f"\n  Contributors: {len(contributors)}")
            for c in contributors[:10]:
                login = c.get("author_github_login") or c["author_name"]
                console.print(f"    - {login} ({c['commits']} commits)")
    finally:
        db.close()


@repo_collab_app.command("list")
def repo_list_extended(
    output_format: str = typer.Option(
        "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """List all repositories with sync status."""
    config, db, repo_service, _ = _get_db_and_services()
    try:
        repos = repo_service.list_repos()

        if not repos:
            # Fall back to config-based listing
            config = load_config()
            if not config.repositories:
                console.print("[yellow]No repositories configured.[/yellow]")
                console.print("Subscribe with: pyrite repo subscribe <url>")
                return

        formatted = _format_output({"repos": repos}, output_format)
        if formatted is not None:
            typer.echo(formatted)
            return

        table = Table(title="Repositories")
        table.add_column("Name", style="cyan")
        table.add_column("Path")
        table.add_column("Remote")
        table.add_column("Fork?")
        table.add_column("Last Sync")
        table.add_column("KBs")

        for repo in repos:
            remote = repo.get("remote_url", "-") or "-"
            if len(remote) > 45:
                remote = remote[:42] + "..."
            is_fork = "Yes" if repo.get("is_fork") else "-"
            last_synced = repo.get("last_synced", "-") or "-"
            if last_synced != "-" and len(last_synced) > 19:
                last_synced = last_synced[:19]

            kb_count = len(repo_service.repo_kb_names(repo["id"]))

            table.add_row(
                repo["name"],
                str(repo["local_path"]),
                remote,
                is_fork,
                last_synced,
                str(kb_count),
            )

        console.print(table)
    finally:
        db.close()

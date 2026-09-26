"""Task CLI commands."""

import json
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from ..exceptions import PyriteError
from ..services.access_policy import UNSCOPED
from ..services.task_service import TaskService
from ..storage.database import PyriteDB

task_app = typer.Typer(help="Task management commands")
console = Console()


def _format_output(data: dict, fmt: str) -> str | None:
    from .output import format_output

    return format_output(data, fmt)


def _task_error(exc: Exception, fmt: str = "rich") -> None:
    """Emit a structured error for a caught task-command exception and exit.

    Maps the exception type to a machine code so scripts/agents get a stable
    error_code, and routes through the shared cli_error so JSON callers get the
    canonical shape (cli-error-shape-consistency)."""
    from ..exceptions import (
        EntryNotFoundError,
        KBNotFoundError,
        ValidationError,
    )
    from ..utils.errors import cli_error

    code = "ERROR"
    if isinstance(exc, EntryNotFoundError):
        code = "NOT_FOUND"
    elif isinstance(exc, KBNotFoundError):
        code = "KB_NOT_FOUND"
    elif isinstance(exc, ValidationError):
        code = "VALIDATION_FAILED"
    cli_error(str(exc), fmt, error_code=code)


def _get_service() -> tuple[TaskService, PyriteDB]:
    """Create TaskService and return (service, db) for cleanup.

    Goes through the shared loader so KBs registered in the database
    (``kb create``, ``kb add``) are merged into the config first. Reading the
    YAML config directly made those KBs invisible here: ``kb list`` showed
    them, and ``task create -k <kb>`` answered KB_NOT_FOUND (#245).
    """
    from .context import get_config_and_db

    config, db = get_config_and_db()
    return TaskService(config, db), db


#: `--field` keys refused because a dedicated option already sets them,
#: named so the error can point the caller at the right option.
_FIELD_OWN_OPTION: dict[str, str] = {
    "title": "--title (or the positional TITLE)",
    "body": "--body",
    "parent": "--parent",
    "priority": "--priority",
    "assignee": "--assignee",
    "tags": "--tags",
}

#: `--field` keys refused because `TaskService.create_task` forwards
#: `fields` as `**kwargs` straight into `KBService.create_entry(kb_name,
#: entry_id, title, entry_type, body, *, allow_undeclared, **kwargs)`
#: (round-1 cold read). `kb_name`/`entry_id`/`entry_type` collide with that
#: call's own positional/keyword arguments and crash with a raw "got
#: multiple values for keyword argument" TypeError; `allow_undeclared`
#: doesn't crash -- it silently binds to the control parameter that decides
#: whether the undeclared-type refusal runs, so a caller naming a field
#: `allow_undeclared` would instead flip the create pipeline's own safety
#: switch without knowing it.
_FIELD_CREATE_ENTRY_COLLISION: frozenset[str] = frozenset(
    {"kb_name", "entry_id", "entry_type", "allow_undeclared"}
)

#: `--field` keys refused because a freshly created entry can never actually
#: carry them, the #407 symptom recurring on `create` (round-2 cold read):
#: `type` is frontmatter-only -- `build_entry` always writes the caller's
#: own `entry_type` argument as `type:`, so `--field type=note` on a task
#: silently had no effect and `task create` reported success anyway, the
#: same as `update -f type=...` before it was refused. `created_at`/
#: `updated_at` are accepted by the constructor (`build_entry` passes them
#: through) but `Entry._base_frontmatter` only re-emits a timestamp for an
#: entry loaded FROM a file (`_source_frontmatter` set, #151/#46) -- a
#: freshly created entry never has that, so the value is silently dropped
#: before the file is ever written, unlike `update -f created_at=...` on an
#: existing entry, which does persist. Refusing here rather than silently
#: accepting and dropping.
_FIELD_CREATE_NEVER_TAKES_EFFECT: frozenset[str] = frozenset({"type", "created_at", "updated_at"})


def _parse_task_create_fields(field: list[str] | None) -> dict[str, Any]:
    """Parse `--field key=value` pairs for `task create`.

    Uses the same value parser as `create -f`/`update -f`
    (`_parse_field_value`), and refuses:

    - a key that already has its own option;
    - `status` (task lifecycle is `task update --status`, not a free-form
      field);
    - a `TaskEntry.managed_fields` key -- `create` does not run `update`'s
      managed-field refusal, so without this an agent could forge the audit
      trail (`status_change_log`, `evidence`, `agent_context`,
      `assigned_at`) at creation instead of only failing to set it later;
    - every service-level `KBService._MANAGED_FIELDS` key (`id`, `kb_name`,
      `file_path`, `links`, `sources`, `provenance`, `extra_frontmatter`) --
      `update` already refuses these on every entry type, not just tasks;
      `create` didn't, so `--field links=[{"target": "ghost"}]` created a
      task already linked to a target that doesn't exist, bypassing the
      dangling-target check `add_link` applies everywhere else (round-1
      cold read);
    - a key colliding with `create_entry`'s own call signature or control
      parameters (`_FIELD_CREATE_ENTRY_COLLISION`);
    - `type`, `created_at` and `updated_at` -- a freshly created entry can
      never actually carry them (`_FIELD_CREATE_NEVER_TAKES_EFFECT`), the
      #407 symptom recurring on `create` (round-2 cold read).
    """
    from ..models.task import TaskEntry
    from ..services.kb_service import _MANAGED_FIELDS
    from .entry_commands import _parse_field_value

    fields: dict[str, Any] = {}
    for fv in field or []:
        if "=" not in fv:
            console.print(f"[red]Error:[/red] --field must be key=value, got '{fv}'")
            raise typer.Exit(1)
        k, v = fv.split("=", 1)
        if k in _FIELD_OWN_OPTION:
            console.print(
                f"[red]Error:[/red] --field {k}=... is refused: use {_FIELD_OWN_OPTION[k]} instead."
            )
            raise typer.Exit(1)
        if k == "status":
            console.print(
                "[red]Error:[/red] --field status=... is refused: a new task is "
                "always created open; use `task update --status` to change it."
            )
            raise typer.Exit(1)
        if k in TaskEntry.managed_fields:
            console.print(
                f"[red]Error:[/red] --field {k}=... is refused: Pyrite maintains "
                f"this field as part of the task's audit trail."
            )
            raise typer.Exit(1)
        if k in _MANAGED_FIELDS:
            console.print(
                f"[red]Error:[/red] --field {k}=... is refused: Pyrite maintains "
                f"this field; `update` refuses it too."
            )
            raise typer.Exit(1)
        if k in _FIELD_CREATE_ENTRY_COLLISION:
            console.print(
                f"[red]Error:[/red] --field {k}=... is refused: this name collides "
                f"with a parameter `task create` itself needs."
            )
            raise typer.Exit(1)
        if k in _FIELD_CREATE_NEVER_TAKES_EFFECT:
            console.print(
                f"[red]Error:[/red] --field {k}=... is refused: a freshly created "
                f"task cannot carry this value; it would report success and set "
                f"nothing."
            )
            raise typer.Exit(1)
        fields[k] = _parse_field_value(v)
    return fields


@task_app.command("create")
def task_create(
    title_arg: str | None = typer.Argument(
        None, metavar="TITLE", help="Task title (or use --title)"
    ),
    title_opt: str | None = typer.Option(None, "--title", "-t", help="Task title"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
    parent: str | None = typer.Option(None, "--parent", "-p", help="Parent task entry ID"),
    priority: int = typer.Option(5, "--priority", help="Priority 1-10"),
    assignee: str | None = typer.Option(
        None, "--assignee", "-a", help="Assignee (e.g. agent:claude-code-7a3f)"
    ),
    body: str | None = typer.Option(None, "--body", "-b", help="Task description"),
    tags: str = typer.Option("", "--tags", help="Comma-separated tags"),
    field: list[str] | None = typer.Option(
        None, "--field", help="Extra field as key=value (repeatable)"
    ),
    fmt: str = typer.Option("rich", "--format", "-f", help="Output format: rich, json"),
):
    """Create a new task.

    The title may be given either positionally (``task create "Title"``) or via
    the ``--title`` flag (``task create --title "Title"``) — both work, for
    consistency with the other ``--body``/``--priority`` flags.

    ``--field key=value`` (repeatable) sets any field a KB's schema requires
    or allows for ``task`` beyond the built-in options -- e.g. the desk
    schema's ``project``/``kind``. ``-f`` is already ``--format`` on this
    command (#303), so this is ``--field`` only, with no short flag.
    """
    if title_arg and title_opt:
        console.print(
            "[red]Error:[/red] Provide the title either positionally or with --title, not both."
        )
        raise typer.Exit(1)
    title = title_arg or title_opt
    if not title:
        console.print(
            "[red]Error:[/red] A task title is required: "
            "`pyrite task create <title> ...` or `--title <title>`."
        )
        raise typer.Exit(1)

    fields = _parse_task_create_fields(field)

    svc, db = _get_service()
    try:
        result = svc.create_task(
            kb_name=kb_name,
            title=title,
            body=body or "",
            parent=parent or "",
            priority=priority,
            assignee=assignee or "",
            tags=[t.strip() for t in tags.split(",")] if tags else None,
            fields=fields or None,
        )

        formatted = _format_output(result, fmt)
        if formatted is not None:
            typer.echo(formatted)
            return

        console.print(f"[green]Task created:[/green] {result['title']}")
        console.print(f"  ID: [cyan]{result['entry_id']}[/cyan]")
        console.print(f"  Status: open, Priority: {priority}")
        if parent:
            console.print(f"  Parent: {parent}")
        if assignee:
            console.print(f"  Assignee: {assignee}")
    except (PyriteError, ValueError) as e:
        _task_error(e, fmt)
    finally:
        db.close()


@task_app.command("list")
def task_list(
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="Knowledge base name"),
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status"),
    assignee: str | None = typer.Option(None, "--assignee", "-a", help="Filter by assignee"),
    parent: str | None = typer.Option(None, "--parent", "-p", help="Filter by parent task"),
    fmt: str = typer.Option("rich", "--format", "-f", help="Output format: rich, json"),
    priority: int | None = typer.Option(None, "--priority", help="Filter by priority"),
):
    """List tasks with optional filters."""
    svc, db = _get_service()
    try:
        items = svc.list_tasks(
            kb_name=kb_name,
            status=status,
            assignee=assignee,
            parent=parent,
            priority=priority,
        )

        formatted = _format_output({"count": len(items), "tasks": items}, fmt)
        if formatted is not None:
            typer.echo(formatted)
            return

        table = Table(title="Tasks")
        table.add_column("ID", style="cyan", max_width=12)
        table.add_column("Title")
        table.add_column("Status", style="green")
        table.add_column("Pri", justify="right")
        table.add_column("Assignee", style="yellow")
        table.add_column("Parent", style="dim", max_width=12)

        for item in items:
            table.add_row(
                item["id"][:12],
                item["title"],
                item["status"],
                str(item["priority"]),
                item["assignee"],
                item["parent"][:12] if item["parent"] else "",
            )
        console.print(table)
    except (PyriteError, ValueError) as e:
        _task_error(e, fmt)
    finally:
        db.close()


@task_app.command("get")
def task_get(
    task_id: str = typer.Argument(..., help="Task entry ID"),
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="Knowledge base name"),
    fmt: str = typer.Option("rich", "--format", "-f", help="Output format: rich, json"),
):
    """Show task details with children, dependencies, and evidence."""
    _task_get_impl(task_id, kb_name, fmt)


@task_app.command("status")
def task_status(
    task_id: str = typer.Argument(..., help="Task entry ID"),
    kb_name: str | None = typer.Option(None, "--kb", "-k", help="Knowledge base name"),
    fmt: str = typer.Option("rich", "--format", "-f", help="Output format: rich, json"),
):
    """[Deprecated] Alias for `task get`. Use `task get` instead."""
    # Deprecation notice goes to stderr so it never corrupts JSON on stdout.
    Console(stderr=True).print(
        "[yellow]Warning:[/yellow] `task status` is deprecated and will be "
        "removed in a future release; use `task get` instead.",
        style="dim",
    )
    _task_get_impl(task_id, kb_name, fmt)


def _task_get_impl(task_id: str, kb_name: str | None, fmt: str):
    """Shared implementation for `task get` and the deprecated `task status`."""
    svc, db = _get_service()
    try:
        task = svc.get_task(task_id, kb_name, readable_kbs=UNSCOPED)
        if not task:
            from ..utils.errors import cli_error

            cli_error(
                f"Task '{task_id}' not found",
                fmt,
                error_code="NOT_FOUND",
                suggestion=f"run `pyrite task list -k {kb_name or '<kb>'}` to find task IDs",
            )

        meta = task.get("metadata", {})
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}

        children_list = svc.list_tasks(kb_name=kb_name, parent=task_id)
        children = [
            {"id": c["id"], "title": c["title"], "status": c["status"]} for c in children_list
        ]

        result = {
            "id": task["id"],
            "title": task["title"],
            "status": task.get("status") or meta.get("status", "open"),
            "assignee": task.get("assignee") or meta.get("assignee", ""),
            "priority": task.get("priority") or meta.get("priority", 5),
            "parent": meta.get("parent", ""),
            "dependencies": meta.get("dependencies", []),
            "evidence": meta.get("evidence", []),
            "due_date": meta.get("due_date", ""),
            "agent_context": meta.get("agent_context", {}),
            "children": children,
            "kb_name": task.get("kb_name", kb_name or ""),
        }

        formatted = _format_output(result, fmt)
        if formatted is not None:
            typer.echo(formatted)
            return

        console.print(f"\n[bold]{result['title']}[/bold]")
        console.print(f"  ID: [cyan]{result['id']}[/cyan]")
        console.print(f"  Status: [green]{result['status']}[/green]")
        console.print(f"  Priority: {result['priority']}")
        if result["assignee"]:
            console.print(f"  Assignee: [yellow]{result['assignee']}[/yellow]")
        if result["parent"]:
            console.print(f"  Parent: {result['parent']}")
        if result["due_date"]:
            console.print(f"  Due: {result['due_date']}")
        if result["dependencies"]:
            console.print(f"  Dependencies: {', '.join(result['dependencies'])}")
        if result["evidence"]:
            console.print(f"  Evidence: {', '.join(result['evidence'])}")
        if children:
            console.print(f"\n  [bold]Children ({len(children)}):[/bold]")
            for c in children:
                console.print(f"    {c['id'][:12]}  {c['status']:12}  {c['title']}")
    except (PyriteError, ValueError) as e:
        _task_error(e, fmt)
    finally:
        db.close()


@task_app.command("update")
def task_update(
    task_id: str = typer.Argument(..., help="Task entry ID"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
    status: str | None = typer.Option(None, "--status", "-s", help="New status"),
    assignee: str | None = typer.Option(None, "--assignee", "-a", help="New assignee"),
    priority: int | None = typer.Option(None, "--priority", help="New priority 1-10"),
    reason: str | None = typer.Option(
        None,
        "--reason",
        "--status-reason",
        help=(
            "Free-string reason for the status change. Required under "
            "relaxed-mode entry types (Tier A r1175); ignored under "
            "strict-mode types unless the transition declares "
            "requires_reason."
        ),
    ),
    comment: str | None = typer.Option(
        None,
        "--comment",
        help=(
            "Audit comment recorded in the task's status_change_log when this "
            "update changes status (captures *why* the transition happened)."
        ),
    ),
    by: str | None = typer.Option(
        None, "--by", help="Who made the change, for the status_change_log entry"
    ),
    fmt: str = typer.Option("rich", "--format", "-f", help="Output format: rich, json"),
):
    """Update task fields (status, assignee, priority, reason)."""
    updates: dict[str, Any] = {}
    if status is not None:
        updates["status"] = status
    if assignee is not None:
        updates["assignee"] = assignee
    if priority is not None:
        updates["priority"] = priority
    if reason is not None:
        # Lands on the task entry's status_reason field via the
        # KBService.update_entry pass-through. The relaxed-mode
        # validator reads it from the entry attribute on the next
        # before_save hook.
        updates["status_reason"] = reason
    if comment is not None:
        updates["comment"] = comment
    if by is not None:
        updates["by"] = by

    if not updates:
        console.print("[yellow]No updates specified.[/yellow]")
        raise typer.Exit(1)

    svc, db = _get_service()
    try:
        result = svc.update_task(task_id, kb_name, **updates)

        formatted = _format_output(result, fmt)
        if formatted is not None:
            typer.echo(formatted)
            return

        console.print(f"[green]Updated task:[/green] {task_id}")
        for k, v in updates.items():
            console.print(f"  {k}: {v}")
    except (PyriteError, ValueError) as e:
        _task_error(e, fmt)
    finally:
        db.close()


@task_app.command("claim")
def task_claim(
    task_id: str = typer.Argument(..., help="Task entry ID"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
    assignee: str = typer.Option(
        ..., "--assignee", "-a", help="Assignee (e.g. agent:claude-code-7a3f)"
    ),
    fmt: str = typer.Option("rich", "--format", "-f", help="Output format: rich, json"),
):
    """Atomically claim an open task."""
    svc, db = _get_service()
    try:
        result = svc.claim_task(task_id, kb_name, assignee)

        formatted = _format_output(result, fmt)
        if formatted is not None:
            typer.echo(formatted)
            return

        if result.get("claimed"):
            console.print(f"[green]Claimed:[/green] {task_id}")
            console.print(f"  Assignee: {assignee}")
        else:
            console.print(f"[red]Failed:[/red] {result.get('error', 'Unknown error')}")
            raise typer.Exit(1)
    except (PyriteError, ValueError) as e:
        _task_error(e, fmt)
    finally:
        db.close()


@task_app.command("reset")
def task_reset(
    task_id: str = typer.Argument(..., help="Task entry ID"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
    reason: str = typer.Option(
        "", "--reason", "-r", help="Why the claim is being released (audit trail)"
    ),
    operator: str = typer.Option(
        "operator", "--operator", help="Who is performing the reset (e.g. conductor)"
    ),
    fmt: str = typer.Option("rich", "--format", "-f", help="Output format: rich, json"),
):
    """Release a stale in_progress/blocked claim back to `open`.

    For tasks whose worker crashed or aged out: returns the task to `open` so it
    can be re-dispatched, clears the assignee, and appends a work-log entry.
    """
    svc, db = _get_service()
    try:
        result = svc.reset_task(task_id, kb_name, reason=reason, operator=operator)

        formatted = _format_output(result, fmt)
        if formatted is not None:
            typer.echo(formatted)
            return

        console.print(f"[green]Reset:[/green] {task_id} ({result['prior_status']} → open)")
        console.print(f"  Reason: {result['reason']}")
    except (PyriteError, ValueError) as e:
        _task_error(e, fmt)
    finally:
        db.close()


@task_app.command("decompose")
def task_decompose(
    parent_id: str = typer.Argument(..., help="Parent task entry ID"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
    child: list[str] = typer.Option(..., "--child", "-c", help="Child task title (repeatable)"),
    fmt: str = typer.Option("rich", "--format", "-f", help="Output format: rich, json"),
):
    """Decompose a parent task into child tasks."""
    children = [{"title": t} for t in child]
    svc, db = _get_service()
    try:
        results = svc.decompose_task(parent_id, kb_name, children)

        output = {"decomposed": True, "parent_id": parent_id, "children": results}
        formatted = _format_output(output, fmt)
        if formatted is not None:
            typer.echo(formatted)
            return

        console.print(f"[green]Decomposed:[/green] {parent_id}")
        for r in results:
            if r.get("created"):
                console.print(f"  [green]+[/green] {r['entry_id']}")
            else:
                console.print(f"  [red]x[/red] {r.get('error', 'Unknown error')}")
    except (PyriteError, ValueError) as e:
        _task_error(e, fmt)
    finally:
        db.close()


@task_app.command("migrate-relaxed-mode")
def task_migrate_relaxed_mode(
    kb_name: str = typer.Argument(..., help="Knowledge base to migrate"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plan without writing"),
    fmt: str = typer.Option("rich", "--format", "-f", help="Output format: rich, json"),
):
    """Backfill status_reason='pre-relaxed-mode' for tasks of types
    that have opted into relaxed-reason mode but lack a reason.

    Tier A r1175 — provides a migration path so existing tasks don't
    fail validation the next time their status changes. Idempotent.
    """
    svc, db = _get_service()
    try:
        result = svc.migrate_relaxed_mode(kb_name, dry_run=dry_run)

        formatted = _format_output(result, fmt)
        if formatted is not None:
            typer.echo(formatted)
            return

        verb = "Would migrate" if dry_run else "Migrated"
        console.print(f"[green]{verb}[/green] in '{kb_name}':")
        console.print(f"  Scanned: {result['scanned']}")
        console.print(f"  Migrated: {result['migrated']}")
        console.print(f"  Skipped: {result['skipped']}")
        if dry_run:
            console.print("[yellow]Dry run — no files modified.[/yellow]")
    except (PyriteError, ValueError) as e:
        _task_error(e, fmt)
    finally:
        db.close()


@task_app.command("checkpoint")
def task_checkpoint(
    task_id: str = typer.Argument(..., help="Task entry ID"),
    kb_name: str = typer.Option(..., "--kb", "-k", help="Knowledge base name"),
    message: str = typer.Option(..., "--message", "-m", help="Checkpoint message"),
    confidence: float = typer.Option(0.0, "--confidence", help="Confidence 0.0-1.0"),
    evidence: list[str] | None = typer.Option(
        None, "--evidence", "-e", help="Evidence entry IDs (repeatable)"
    ),
    fmt: str = typer.Option("rich", "--format", "-f", help="Output format: rich, json"),
):
    """Log a checkpoint on a task."""
    svc, db = _get_service()
    try:
        result = svc.checkpoint_task(
            task_id=task_id,
            kb_name=kb_name,
            message=message,
            confidence=confidence,
            partial_evidence=evidence,
        )

        formatted = _format_output(result, fmt)
        if formatted is not None:
            typer.echo(formatted)
            return

        console.print(f"[green]Checkpoint logged:[/green] {task_id}")
        console.print(f"  {message}")
        if confidence > 0:
            console.print(f"  Confidence: {int(confidence * 100)}%")
    except (PyriteError, ValueError) as e:
        _task_error(e, fmt)
    finally:
        db.close()

"""Task claim endpoint.

mcp-rest-tool-parity: ports task_claim (atomic task claim used by the
conductor pattern) from MCP-only (pyrite/server/mcp_server.py) to
REST. Shares the same TaskService.claim_task call the MCP handler
already uses -- no service-layer duplication.
"""

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from ...plugins.identity import CALLER, resolve_person_arguments
from ...services.access_policy import KB, Action, ReadScope
from ...services.task_service import TaskService
from ..api import (
    get_task_service,
    limiter,
    requires_kb_tier,
)
from ..authz import authorize, get_principal

router = APIRouter(tags=["Tasks"])


@router.get("/tasks")
@limiter.limit("120/minute")
def list_tasks(
    request: Request,
    kb: str | None = Query(None, description="KB name; omit to span readable KBs"),
    status: str | None = Query(None, description="Filter by status ('open' also matches unset)"),
    assignee: str | None = Query(None, description="Filter by assignee, e.g. 'mark'"),
    parent: str | None = Query(None, description="Filter by parent task id"),
    svc: TaskService = Depends(get_task_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """List tasks across one KB or every readable one.

    Exists so the human worklist board can ask one question -- "what is
    assigned to Mark, everywhere?" -- in a single call. "Everywhere" now
    means the KBs the caller may read: a task is an entry, with a title
    and an assignee, and ``count`` counts the rows returned, so the
    readable set goes into the SQL rather than into a comprehension.
    """
    tasks = svc.list_tasks(
        kb_name=kb,
        status=status,
        assignee=assignee,
        parent=parent,
        kb_names=None if kb else scope.as_set(),
    )
    return {"count": len(tasks), "tasks": tasks}


class TaskClaimRequest(BaseModel):
    """Request body for claiming a task.

    ``assignee`` names who claims it. A signed-in caller is recorded as
    themselves (``user:<id>``), and naming anyone else is refused; with no
    session identity (an operator key, auth disabled) it is a label and
    required (the rule MCP's task_claim follows)."""

    assignee: str | None = None


@router.post("/tasks/{task_id}/claim", dependencies=[Depends(requires_kb_tier("write"))])
@limiter.limit("60/minute")
def claim_task(
    request: Request,
    task_id: str,
    req: TaskClaimRequest,
    kb: str = Query(..., description="KB the task belongs to"),
    svc: TaskService = Depends(get_task_service),
):
    """Atomically claim an open task. Returns claimed=False (not an HTTP
    error) for expected non-claim outcomes -- already claimed, or the
    task doesn't exist -- matching the underlying CAS's own contract
    (see KBService.claim_entry)."""
    resolved = resolve_person_arguments(
        {"assignee": req.assignee}, {"assignee": CALLER}, get_principal(request)
    )
    return svc.claim_task(task_id=task_id, kb_name=kb, assignee=resolved["assignee"])

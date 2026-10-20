"""Worktree collaboration endpoints: user submission and admin merge queue."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ...exceptions import PyriteError
from ...services.access_policy import AccessPolicy
from ..api import get_access_policy, get_worktree_resolver, limiter, requires_tier
from ..authz import resolve_named_kb

logger = logging.getLogger(__name__)

router = APIRouter(tags=["worktree"])


# ── Request/Response models ─────────────────────────────────────────


class WorktreeStatusResponse(BaseModel):
    has_worktree: bool
    status: str  # none, active, submitted, merged, rejected
    changes_count: int = 0
    submitted_at: str | None = None
    feedback: str | None = None
    branch: str | None = None


class SubmitRequest(BaseModel):
    kb: str


class SubmitResponse(BaseModel):
    submitted: bool
    kb_name: str
    branch: str
    submitted_at: str | None = None


class ResetRequest(BaseModel):
    kb: str


class MergeQueueEntry(BaseModel):
    username: str
    kb_name: str
    branch: str
    status: str
    submitted_at: str | None = None
    changes_count: int = 0


class MergeQueueResponse(BaseModel):
    submissions: list[MergeQueueEntry]
    total: int


class MergeRequest(BaseModel):
    kb: str


class MergeResponse(BaseModel):
    merged: bool
    message: str


class RejectRequest(BaseModel):
    kb: str
    feedback: str = ""


def _resolve_named_kb(request: Request, policy: AccessPolicy, kb: str | None) -> None:
    """The KB a worktree route names, through the one resolver (#952).

    These routes sit behind the global tier floor only (ADR-0037 theme 3b
    has not moved them), so nothing resolved ``kb`` before the handler: an
    unknown name answered "no worktree" like a real KB with none. Resolved
    for the caller's read scope, an unknown, unreadable or shortname KB is
    404 ``KB_NOT_FOUND`` before any worktree is looked up; a blank or
    absent one names no KB.
    """
    resolve_named_kb(request, policy, kb)


# ── User endpoints ──────────────────────────────────────────────────


@router.get(
    "/worktree/status",
    response_model=WorktreeStatusResponse,
    dependencies=[Depends(requires_tier("read"))],
)
@limiter.limit("60/minute")
def worktree_status(
    request: Request,
    kb: str = Query(..., description="KB name"),
    resolver=Depends(get_worktree_resolver),
    policy: AccessPolicy = Depends(get_access_policy),
):
    """Get worktree status for the current user."""
    _resolve_named_kb(request, policy, kb)
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        return WorktreeStatusResponse(has_worktree=False, status="none")

    wt_svc = resolver.worktree_service
    wt = wt_svc.get_worktree(kb, auth_user["id"])
    if not wt:
        return WorktreeStatusResponse(has_worktree=False, status="none")

    # Count changes via git diff
    changes_count = 0
    try:
        from ...services.git_service import GitService

        success, diff = GitService.diff_branches(
            repo_path=wt.repo_path, base="main", head=wt.branch, stat_only=True
        )
        if success and diff.strip():
            changes_count = len([l for l in diff.strip().splitlines() if "|" in l])
    except Exception:
        pass

    return WorktreeStatusResponse(
        has_worktree=True,
        status=wt.status,
        changes_count=changes_count,
        submitted_at=wt.submitted_at,
        feedback=wt.feedback,
        branch=wt.branch,
    )


@router.post(
    "/worktree/submit",
    response_model=SubmitResponse,
    dependencies=[Depends(requires_tier("write"))],
)
@limiter.limit("10/minute")
def worktree_submit(
    request: Request,
    req: SubmitRequest,
    resolver=Depends(get_worktree_resolver),
    policy: AccessPolicy = Depends(get_access_policy),
):
    """Submit worktree changes for admin review."""
    _resolve_named_kb(request, policy, req.kb)
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    wt_svc = resolver.worktree_service
    try:
        wt = wt_svc.submit(req.kb, auth_user["id"])
    except PyriteError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return SubmitResponse(
        submitted=True,
        kb_name=req.kb,
        branch=wt.branch,
        submitted_at=wt.submitted_at,
    )


@router.get(
    "/worktree/changes",
    dependencies=[Depends(requires_tier("read"))],
)
@limiter.limit("30/minute")
def worktree_changes(
    request: Request,
    kb: str = Query(..., description="KB name"),
    resolver=Depends(get_worktree_resolver),
    policy: AccessPolicy = Depends(get_access_policy),
):
    """Get pending changes in the user's worktree (diff against main)."""
    _resolve_named_kb(request, policy, kb)
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    wt_svc = resolver.worktree_service
    wt = wt_svc.get_worktree(kb, auth_user["id"])
    if not wt:
        return {"changes": [], "count": 0}

    from pathlib import Path

    from ...services.git_service import GitService

    success, diff = GitService.diff_branches(
        repo_path=Path(wt.repo_path), base="main", head=wt.branch
    )
    if not success:
        return {"changes": [], "count": 0, "error": diff}

    success, stat = GitService.diff_branches(
        repo_path=Path(wt.repo_path), base="main", head=wt.branch, stat_only=True
    )
    stat_lines = stat.strip().splitlines() if success else []
    files_changed = [l.strip() for l in stat_lines if "|" in l]

    return {
        "changes": files_changed,
        "count": len(files_changed),
        "diff": diff,
        "branch": wt.branch,
    }


@router.post(
    "/worktree/reset",
    dependencies=[Depends(requires_tier("write"))],
)
@limiter.limit("5/minute")
def worktree_reset(
    request: Request,
    req: ResetRequest,
    resolver=Depends(get_worktree_resolver),
    policy: AccessPolicy = Depends(get_access_policy),
):
    """Reset worktree to main (discard all changes)."""
    _resolve_named_kb(request, policy, req.kb)
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    wt_svc = resolver.worktree_service
    try:
        wt = wt_svc.reset_to_main(req.kb, auth_user["id"])
    except PyriteError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"reset": True, "status": wt.status}


# ── Admin endpoints ─────────────────────────────────────────────────


@router.get(
    "/admin/merge-queue",
    response_model=MergeQueueResponse,
    dependencies=[Depends(requires_tier("admin"))],
)
@limiter.limit("30/minute")
def merge_queue(
    request: Request,
    kb: str | None = Query(None, description="Filter by KB name"),
    resolver=Depends(get_worktree_resolver),
    policy: AccessPolicy = Depends(get_access_policy),
):
    """List all submitted worktrees pending admin review."""
    _resolve_named_kb(request, policy, kb)
    wt_svc = resolver.worktree_service
    submissions = wt_svc.get_submissions(kb)

    entries = []
    for wt in submissions:
        # Count changes
        changes_count = 0
        try:
            from pathlib import Path

            from ...services.git_service import GitService

            success, stat = GitService.diff_branches(
                repo_path=Path(wt.repo_path), base="main", head=wt.branch, stat_only=True
            )
            if success:
                changes_count = len([l for l in stat.strip().splitlines() if "|" in l])
        except Exception:
            pass

        entries.append(
            MergeQueueEntry(
                username=wt.username,
                kb_name=wt.kb_name,
                branch=wt.branch,
                status=wt.status,
                submitted_at=wt.submitted_at,
                changes_count=changes_count,
            )
        )

    return MergeQueueResponse(submissions=entries, total=len(entries))


@router.get(
    "/admin/merge-queue/{username}/diff",
    dependencies=[Depends(requires_tier("admin"))],
)
@limiter.limit("30/minute")
def merge_queue_diff(
    request: Request,
    username: str,
    kb: str = Query(..., description="KB name"),
    resolver=Depends(get_worktree_resolver),
    policy: AccessPolicy = Depends(get_access_policy),
):
    """Get diff for a submitted worktree against main."""
    _resolve_named_kb(request, policy, kb)
    wt_svc = resolver.worktree_service

    # Find the submission by username
    submissions = wt_svc.get_submissions(kb)
    wt = next((s for s in submissions if s.username == username), None)
    if not wt:
        # Also check active worktrees
        all_wts = wt_svc.list_worktrees(kb)
        wt = next((w for w in all_wts if w.username == username), None)
    if not wt:
        raise HTTPException(
            status_code=404,
            detail=f"No worktree found for user '{username}' in KB '{kb}'",
        )

    from pathlib import Path

    from ...services.git_service import GitService

    success, diff = GitService.diff_branches(
        repo_path=Path(wt.repo_path), base="main", head=wt.branch
    )
    success_stat, stat = GitService.diff_branches(
        repo_path=Path(wt.repo_path), base="main", head=wt.branch, stat_only=True
    )

    return {
        "username": username,
        "kb_name": kb,
        "branch": wt.branch,
        "status": wt.status,
        "diff": diff if success else "",
        "stat": stat if success_stat else "",
        "submitted_at": wt.submitted_at,
    }


@router.post(
    "/admin/merge-queue/{username}/merge",
    response_model=MergeResponse,
    dependencies=[Depends(requires_tier("admin"))],
)
@limiter.limit("10/minute")
def merge_queue_merge(
    request: Request,
    username: str,
    req: MergeRequest,
    resolver=Depends(get_worktree_resolver),
    policy: AccessPolicy = Depends(get_access_policy),
):
    """Merge a user's submitted changes into main."""
    _resolve_named_kb(request, policy, req.kb)
    wt_svc = resolver.worktree_service

    # Find worktree by username + kb
    all_wts = wt_svc.list_worktrees(req.kb)
    wt = next((w for w in all_wts if w.username == username), None)
    if not wt:
        raise HTTPException(
            status_code=404,
            detail=f"No worktree found for user '{username}' in KB '{req.kb}'",
        )
    if wt.status != "submitted":
        raise HTTPException(
            status_code=400,
            detail=f"Worktree is '{wt.status}', not 'submitted'. Submit first.",
        )

    success, msg = wt_svc.merge(req.kb, wt.user_id)
    return MergeResponse(merged=success, message=msg)


@router.post(
    "/admin/merge-queue/{username}/reject",
    dependencies=[Depends(requires_tier("admin"))],
)
@limiter.limit("10/minute")
def merge_queue_reject(
    request: Request,
    username: str,
    req: RejectRequest,
    resolver=Depends(get_worktree_resolver),
    policy: AccessPolicy = Depends(get_access_policy),
):
    """Reject a submission with feedback."""
    _resolve_named_kb(request, policy, req.kb)
    wt_svc = resolver.worktree_service

    all_wts = wt_svc.list_worktrees(req.kb)
    wt = next((w for w in all_wts if w.username == username), None)
    if not wt:
        raise HTTPException(
            status_code=404,
            detail=f"No worktree found for user '{username}' in KB '{req.kb}'",
        )

    result = wt_svc.reject(req.kb, wt.user_id, req.feedback)
    return {"rejected": True, "status": result.status, "feedback": result.feedback}

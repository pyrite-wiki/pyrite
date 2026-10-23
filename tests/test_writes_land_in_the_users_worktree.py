"""A signed-in non-admin user's write lands in that user's worktree, or is
refused -- on every transport, never in the main checkout
(markramm/pyrite-security#110; the landing rule of ADR-0044 §1).

The world: auth on, five KBs (generic, software, journalism-investigation,
encyclopedia, social), each the root of its own git repository in its own
parent directory (two repositories sharing a parent collide on
``.pyrite-worktrees/<user>``, ADR-0044 P9). ``alice`` has the global role
``write`` and is not an admin -- the principal REST POST/PUT/DELETE route to a
worktree (``endpoints/entries.py``). ``root`` is the admin, whose writes land
on the main checkout by design.

Every surface is the production one: ``create_app`` with its own ``get_db``
and lifespan, REST through ``TestClient``, and MCP over ``/mcp/sse`` and
``/mcp/messages/`` exactly as a client drives them (the ASGI-level SSE driver
of ``test_mcp_sse_session.py``) -- never a call to a handler.

The cases are generated from the registered lists, so a new write route or
write tool fails ``test_every_*_has_a_case`` until it is given one:

- every ``POST``/``PUT``/``PATCH``/``DELETE`` APIRoute of ``create_app()``
  (``tests/_surface_inventory.rest_operations``);
- every MCP tool registered above the read tier on an admin-tier server, core
  and plugin (``PyriteMCPServer._tool_tiers``).

Each case has a kind, and the kind is itself tested:

- ``WRITES``: the call writes a KB file. The control
  (``test_*_operator_call_writes``) runs it as ``root`` and requires a KB's
  main checkout to change, which proves the arguments reach a write. As
  ``alice`` the main checkout must not change, and a call that succeeds must
  have changed her worktree.
- ``NO_KB_FILE``: the call writes no KB file for anyone (a DB row, a setting);
  the control requires every KB tree unchanged even as ``root``. As ``alice``
  the main checkout must not change.
- ``REFUSED``: ``alice`` may not make the call at all (admin tier, or a tool
  the write tier does not register). She must be refused, and nothing changes.
- ``NOT_CALLED``: not driven here, with the reason.

"Main" is both the checkout and the main index: every ``alice`` case also
requires that no entry row was added to or changed in the index everyone else
reads (an index-only write, with no file, still lands on main).

The second regime is a KB outside any git repository (``PLAIN``): no worktree
can be made, so a non-admin write has nowhere to land and must be refused
(ADR-0044 §1: "It never falls back to canonical"; invariant L2).
"""

from __future__ import annotations

import hashlib
import io
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app
from tests._surface_inventory import rest_operations
from tests.auth_seed import seed_and_sign_in
from tests.test_mcp_sse_session import _cookie, _login, _run, _SSE, _World

NOTES, SW, INV, WIKI, SOCIAL = "notes", "sw", "inv", "wiki", "social"
KB_TYPES = {
    NOTES: "generic",
    SW: "software",
    INV: "journalism-investigation",
    WIKI: "encyclopedia",
    SOCIAL: "social",
}
USER = "alice"
# Main-index tables that change on any authenticated request, whatever a
# write's landing: they say nothing about where the write landed.
# Main-index columns that change on any authenticated request, whatever a
# write's landing: they say nothing about where the write landed. Only these
# columns are left out, never a whole table.
_INDEX_VOLATILE_COLUMNS = {
    "session": {"last_used"},  # touched by every request a session makes
    "local_user": {"updated_at"},  # touched by sign-in bookkeeping
}
# The instance's own accounting of model calls, one row per call: not KB
# content and never a change set's. The one whole table left out.
_INDEX_VOLATILE_TABLES = frozenset({"llm_usage"})
# A KB outside any git repository: no worktree can be made for it, so a
# non-admin write to it has nowhere to land and must be refused (ADR-0044 §1,
# L2), never sent to the main checkout instead.
PLAIN = "plain"

WRITES, NO_KB_FILE, REFUSED, NOT_CALLED = "writes", "no_kb_file", "refused", "not_called"

# An operator API key at the write role: served by the same write-tier MCP
# server as alice, and always canonical -- the binding must not leak to it.
WRITE_KEY = "operator-write-key"
WRITE_KEY_HEADER = {"authorization": f"Bearer {WRITE_KEY}"}


class Seeds:
    """The ids of one case's seed entries for one principal, and a tag that
    makes anything the case creates unique."""

    def __init__(self, case: str, who: str, count: int):
        slug = "".join(c if c.isalnum() else "-" for c in case.lower()).strip("-")
        self.tag = f"{slug}-{who}"
        self.ids = [f"{self.tag}-{i}" for i in range(count)]

    def __getitem__(self, i: int) -> str:
        return self.ids[i]


@dataclass(frozen=True)
class Case:
    kind: str
    call: Callable[[Seeds], dict] | None = None
    # (kb, entry type, extra frontmatter lines) per seed entry, on main
    seeds: tuple[tuple[str, str, str], ...] = ()
    reason: str = ""
    # a stub for an outside dependency only (the network), never Pyrite code
    stub: Callable | None = None
    # main-index tables this call may change whatever the KB's landing, each
    # a key of _MAIN_ROWS (why it is not a change-set write)
    main_rows: tuple[str, ...] = ()
    # Set when the call makes a database-only record a change set cannot carry
    # through its merge: on a review KB, alice is refused and nothing changes
    # anywhere -- files, worktree or index (decided 2026-10-20).
    change_set_refused: str = ""


# Main-index rows a call writes that are not KB content, so no landing applies.
_MAIN_ROWS = {
    "setting": "an instance setting (write tier), not a KB write",
    "starred_entry": "the caller's own star, a per-user preference",
    "kb": "the caller's own new ephemeral KB is registered on the instance",
    "kb_permission": "the creator's grant on their own ephemeral KB",
    "local_user": "the creator's count of ephemeral KBs (local_user.ephemeral_kb_count)",
    "worktree": "submit marks the caller's change set submitted: its own queue row",
    # Follow-ups (tracked privately, not this change): QA review rows and
    # plugin side tables are written through main's connection.
    "review": "a QA review row on a canonical KB (on a review KB it is refused)",
    "encyclopedia_review": "FOLLOW-UP: a plugin side table, written through main's connection",
    "social_vote": "FOLLOW-UP: a plugin side table, written through main's connection",
    "social_reputation_log": "FOLLOW-UP: a plugin hook's side table, through main's connection",
}


def _note(kb=NOTES, extra=""):
    return (kb, "note", extra)


def _stub_clipper(monkeypatch):
    """The network edge only: DNS answers a public address, and HTTP answers
    one page. The clipper's own checks and conversion run for real."""
    import socket

    import httpx

    real_client = httpx.AsyncClient

    def page(request):
        return httpx.Response(200, html="<html><title>Clipped</title><p>clipped body</p></html>")

    def client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(page), **kwargs)

    monkeypatch.setattr(
        "pyrite.services.clipper.socket.getaddrinfo",
        lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )
    monkeypatch.setattr("pyrite.services.clipper.httpx.AsyncClient", client)


# =============================================================================
# REST: every registered write route
# =============================================================================

_IDENTITY = "identity store (users, sessions, keys, invites): DB rows, no KB file"
_NETWORK = "fetches or pushes a remote repository; not drivable offline"

REST_CASES: dict[str, Case] = {
    # -- identity ----------------------------------------------------------
    "PUT /auth/users/{user_id}/role": Case(NOT_CALLED, reason=_IDENTITY),
    "POST /auth/register": Case(NOT_CALLED, reason=_IDENTITY),
    "POST /auth/login": Case(NOT_CALLED, reason=_IDENTITY),
    "POST /auth/logout": Case(NOT_CALLED, reason=_IDENTITY + "; would end the session"),
    "DELETE /auth/github/connect": Case(NOT_CALLED, reason=_IDENTITY),
    "POST /auth/api-keys": Case(NOT_CALLED, reason=_IDENTITY),
    "DELETE /auth/api-keys/{provider}": Case(NOT_CALLED, reason=_IDENTITY),
    "POST /auth/invite-codes": Case(NOT_CALLED, reason=_IDENTITY),
    "DELETE /auth/invite-codes/{code}": Case(NOT_CALLED, reason=_IDENTITY),
    # -- admin tier: alice is refused -------------------------------------
    "POST /api/index/sync": Case(REFUSED, lambda s: {}),
    "POST /api/ai/test": Case(
        NO_KB_FILE, lambda s: {}, reason="tests the AI provider connection (write tier)"
    ),
    "POST /api/kbs": Case(REFUSED, lambda s: {"json": {"name": s.tag, "path": "/nowhere"}}),
    "PUT /api/kbs/{name}": Case(
        REFUSED, lambda s: {"url": f"/api/kbs/{NOTES}", "json": {"description": "x"}}
    ),
    "DELETE /api/kbs/{name}": Case(REFUSED, lambda s: {"url": f"/api/kbs/{NOTES}"}),
    "POST /api/kbs/{name}/reindex": Case(REFUSED, lambda s: {"url": f"/api/kbs/{NOTES}/reindex"}),
    "POST /api/kbs/gc": Case(REFUSED, lambda s: {}),
    "DELETE /api/kbs/ephemeral/{name}": Case(
        REFUSED, lambda s: {"url": f"/api/kbs/ephemeral/{NOTES}"}
    ),
    "POST /api/kbs/ephemeral": Case(
        NO_KB_FILE,
        lambda s: {"json": {"name": s.tag}},
        reason="creates the caller's own ephemeral KB, a new directory; no configured KB's files",
        main_rows=("kb", "kb_permission", "local_user"),
    ),
    "POST /api/kbs/{name}/permissions": Case(
        REFUSED,
        lambda s: {"url": f"/api/kbs/{NOTES}/permissions", "json": {"user_id": 1, "role": "write"}},
    ),
    "PUT /api/kbs/{name}/default-role": Case(
        REFUSED, lambda s: {"url": f"/api/kbs/{NOTES}/default-role", "json": {"role": "write"}}
    ),
    "POST /api/kbs/{kb_name}/publish": Case(
        REFUSED, lambda s: {"url": f"/api/kbs/{NOTES}/publish", "json": {}}
    ),
    "POST /api/kbs/{kb_name}/commit": Case(
        REFUSED, lambda s: {"url": f"/api/kbs/{NOTES}/commit", "json": {"message": "m"}}
    ),
    "POST /api/kbs/{kb_name}/push": Case(
        REFUSED, lambda s: {"url": f"/api/kbs/{NOTES}/push", "json": {}}
    ),
    "DELETE /api/repos/{name}": Case(REFUSED, lambda s: {"url": f"/api/repos/{NOTES}"}),
    "POST /api/admin/merge-queue/{username}/merge": Case(
        REFUSED, lambda s: {"url": f"/api/admin/merge-queue/{USER}/merge", "json": {"kb": NOTES}}
    ),
    "POST /api/admin/merge-queue/{username}/reject": Case(
        REFUSED, lambda s: {"url": f"/api/admin/merge-queue/{USER}/reject", "json": {"kb": NOTES}}
    ),
    # -- entry writes ------------------------------------------------------
    "POST /api/entries": Case(
        WRITES, lambda s: {"json": {"kb": NOTES, "title": f"Rest {s.tag}", "body": "b"}}
    ),
    "PUT /api/entries/{entry_id}": Case(
        WRITES,
        lambda s: {"url": f"/api/entries/{s[0]}", "json": {"kb": NOTES, "body": "changed"}},
        seeds=(_note(),),
    ),
    "PATCH /api/entries/{entry_id}": Case(
        WRITES,
        lambda s: {
            "url": f"/api/entries/{s[0]}",
            "json": {"kb": NOTES, "field": "status", "value": "done"},
        },
        seeds=(_note(),),
    ),
    "DELETE /api/entries/{entry_id}": Case(
        WRITES,
        lambda s: {"url": f"/api/entries/{s[0]}", "params": {"kb": NOTES}},
        seeds=(_note(),),
    ),
    "POST /api/entries/import": Case(
        WRITES,
        lambda s: {
            "params": {"kb": NOTES, "format": "markdown"},
            "files": {
                "file": (
                    "import.md",
                    io.BytesIO(f"---\ntitle: Import {s.tag}\ntype: note\n---\nb\n".encode()),
                    "text/markdown",
                )
            },
        },
    ),
    "POST /api/daily/{date_str}": Case(
        WRITES,
        # one date per principal, so the operator's note does not pre-empt hers
        lambda s: {
            "url": f"/api/daily/{'2030-01-01' if s.tag.endswith('-a') else '2030-01-02'}",
            "params": {"kb": NOTES},
        },
    ),
    "POST /api/collections": Case(
        WRITES,
        lambda s: {"json": {"kb": NOTES, "title": f"Coll {s.tag}", "query": "type:note"}},
    ),
    "POST /api/clip": Case(
        WRITES,
        lambda s: {"json": {"url": "https://example.com/", "kb": NOTES, "title": f"Clip {s.tag}"}},
        stub=_stub_clipper,
    ),
    "POST /api/tasks/{task_id}/claim": Case(
        WRITES,
        lambda s: {
            "url": f"/api/tasks/{s[0]}/claim",
            "params": {"kb": NOTES},
            "json": {"assignee": "agent:x"},
        },
        seeds=((NOTES, "task", "status: open\n"),),
    ),
    # -- reads by POST, and writes that are not KB files ---------------------
    "POST /api/entries/resolve-batch": Case(
        NO_KB_FILE, lambda s: {"json": {"targets": ["x"], "kb": NOTES}}, reason="a read"
    ),
    "POST /api/entries/batch": Case(
        NO_KB_FILE, lambda s: {"json": {"ids": ["x"], "kb": NOTES}}, reason="a read"
    ),
    "POST /api/collections/query-preview": Case(
        NO_KB_FILE, lambda s: {"json": {"query": "type:note", "kb": NOTES}}, reason="a read"
    ),
    "POST /api/kbs/{kb_name}/templates/{template_name}/render": Case(
        NO_KB_FILE,
        lambda s: {"url": f"/api/kbs/{NOTES}/templates/none/render", "json": {}},
        reason="renders text; writes nothing",
    ),
    "POST /api/ai/summarize": Case(
        NO_KB_FILE,
        lambda s: {"json": {"entry_id": "x", "kb_name": NOTES}},
        reason="suggestions only",
    ),
    "POST /api/ai/auto-tag": Case(
        NO_KB_FILE,
        lambda s: {"json": {"entry_id": "x", "kb_name": NOTES}},
        reason="suggestions only",
    ),
    "POST /api/ai/suggest-links": Case(
        NO_KB_FILE,
        lambda s: {"json": {"entry_id": "x", "kb_name": NOTES}},
        reason="suggestions only",
    ),
    "POST /api/ai/chat": Case(
        NO_KB_FILE,
        lambda s: {"json": {"messages": [{"role": "user", "content": "hi"}]}},
        reason="a chat reply",
    ),
    "POST /api/starred": Case(
        NO_KB_FILE,
        lambda s: {"json": {"entry_id": s[0], "kb_name": NOTES}},
        seeds=(_note(),),
        reason="a DB row",
        main_rows=("starred_entry",),
    ),
    "DELETE /api/starred/{entry_id}": Case(
        NO_KB_FILE, lambda s: {"url": f"/api/starred/{s.tag}", "params": {"kb": NOTES}}
    ),
    "PUT /api/starred/reorder": Case(NO_KB_FILE, lambda s: {"json": {"entries": []}}),
    "PUT /api/settings": Case(
        NO_KB_FILE,
        lambda s: {"json": {"settings": {"ui.theme": "dark"}}},
        main_rows=("setting",),
    ),
    "PUT /api/settings/{key}": Case(
        NO_KB_FILE,
        lambda s: {"url": "/api/settings/ui.theme", "json": {"value": "dark"}},
        main_rows=("setting",),
    ),
    "DELETE /api/settings/{key}": Case(
        NO_KB_FILE,
        lambda s: {"url": "/api/settings/ui.theme"},
        main_rows=("setting",),
    ),
    "POST /api/reviews": Case(
        NO_KB_FILE,
        lambda s: {
            "json": {
                "entry_id": s[0],
                "kb_name": NOTES,
                "reviewer": "r",
                "reviewer_type": "user",
                "result": "pass",
            }
        },
        seeds=(_note(),),
        reason="a QA verdict row keyed by blob hash (review_service)",
        main_rows=("review",),
        change_set_refused="a QA review row is database-only",
    ),
    "DELETE /api/reviews/{review_id}": Case(NO_KB_FILE, lambda s: {"url": "/api/reviews/999999"}),
    "POST /api/worktree/submit": Case(
        NO_KB_FILE,
        lambda s: {"json": {"kb": NOTES}},
        reason="commits in the caller's own worktree, never on main",
        main_rows=("worktree",),
    ),
    "POST /api/worktree/reset": Case(
        NOT_CALLED,
        reason="resets the caller's own worktree; run last it would hide earlier landings",
    ),
    # -- other repositories -------------------------------------------------
    "POST /api/kbs/{kb_name}/export": Case(NOT_CALLED, reason=_NETWORK),
    "POST /api/repos/subscribe": Case(NOT_CALLED, reason=_NETWORK),
    "POST /api/repos/fork": Case(NOT_CALLED, reason=_NETWORK),
    "POST /api/repos/{name}/sync": Case(NOT_CALLED, reason=_NETWORK),
    "POST /api/repos/{name}/pr": Case(NOT_CALLED, reason=_NETWORK),
}


# =============================================================================
# MCP: every tool registered above the read tier
# =============================================================================

_ADMIN_TOOL = "admin tier: the write-tier server alice is given does not register it"
# Not KB-file writes today; each control fails the day one starts writing a
# file, and the case becomes WRITES. The main-index check covers them meanwhile.
_ADVISORY = "returns the frontmatter for the caller to write; writes no file"

MCP_CASES: dict[str, Case] = {
    "kb_create": Case(
        WRITES, lambda s: {"kb_name": NOTES, "entry_type": "note", "title": f"Mcp {s.tag}"}
    ),
    "kb_bulk_create": Case(
        WRITES,
        lambda s: {"kb_name": NOTES, "entries": [{"entry_type": "note", "title": f"Bulk {s.tag}"}]},
    ),
    "kb_update": Case(
        WRITES, lambda s: {"kb_name": NOTES, "entry_id": s[0], "body": "changed"}, seeds=(_note(),)
    ),
    "kb_delete": Case(WRITES, lambda s: {"kb_name": NOTES, "entry_id": s[0]}, seeds=(_note(),)),
    "kb_link": Case(
        WRITES,
        lambda s: {"source_id": s[0], "source_kb": NOTES, "target_id": s[1], "target_kb": NOTES},
        seeds=(_note(), _note()),
    ),
    "task_create": Case(WRITES, lambda s: {"kb_name": NOTES, "title": f"Task {s.tag}"}),
    "task_update": Case(
        WRITES,
        lambda s: {"kb_name": NOTES, "task_id": s[0], "status": "claimed"},
        seeds=((NOTES, "task", "status: open\n"),),
    ),
    "task_claim": Case(
        WRITES,
        lambda s: {"kb_name": NOTES, "task_id": s[0], "assignee": "agent:x"},
        seeds=((NOTES, "task", "status: open\n"),),
    ),
    "task_decompose": Case(
        WRITES,
        lambda s: {"kb_name": NOTES, "parent_id": s[0], "children": [{"title": f"Child {s.tag}"}]},
        seeds=((NOTES, "task", "status: open\n"),),
    ),
    "task_checkpoint": Case(
        WRITES,
        lambda s: {"kb_name": NOTES, "task_id": s[0], "message": "halfway"},
        seeds=((NOTES, "task", "status: in_progress\nassignee: agent:x\n"),),
    ),
    "kb_qa_assess": Case(
        WRITES,
        lambda s: {"kb_name": NOTES, "entry_id": s[0], "tier": 1, "max_age_hours": 0},
        seeds=(_note(),),
    ),
    "wiki_submit_review": Case(
        NO_KB_FILE,
        lambda s: {"kb_name": WIKI, "entry_id": s[0], "reviewer_id": "r", "action": "approve"},
        seeds=((WIKI, "article", "review_status: under_review\n"),),
        reason=_ADVISORY,
        main_rows=("encyclopedia_review",),
    ),
    "wiki_assess_quality": Case(
        NO_KB_FILE,
        lambda s: {"kb_name": WIKI, "entry_id": s[0], "quality": "B"},
        seeds=((WIKI, "article", ""),),
        reason=_ADVISORY,
    ),
    "investigation_create_entity": Case(
        WRITES,
        lambda s: {"kb_name": INV, "entity_type": "person", "title": f"Ent {s.tag}"},
    ),
    "investigation_create_event": Case(
        WRITES,
        lambda s: {
            "kb_name": INV,
            "event_type": "investigation_event",
            "title": f"Ev {s.tag}",
            "date": "2020-01-01",
        },
    ),
    "investigation_create_claim": Case(
        WRITES,
        lambda s: {"kb_name": INV, "title": f"Claim {s.tag}", "assertion": "a"},
    ),
    "investigation_start": Case(
        WRITES,
        lambda s: {"kb_name": INV, "title": f"Inv {s.tag}"},
    ),
    "investigation_promote_claim": Case(
        WRITES,
        lambda s: {
            "kb_name": INV,
            "claim_id": s[0],
            "edge_type": "membership",
            "endpoint_fields": {"person": s[1], "organization": s[2]},
        },
        seeds=(
            (INV, "claim", "assertion: a\nclaim_status: corroborated\n"),
            (INV, "person", ""),
            (INV, "organization", ""),
        ),
    ),
    "investigation_log_source": Case(
        WRITES,
        lambda s: {"kb_name": INV, "title": f"Src {s.tag}", "url": "https://e.com"},
    ),
    "investigation_bulk_edges": Case(
        WRITES,
        lambda s: {
            "kb_name": INV,
            "edges": [
                {
                    "type": "membership",
                    "fields": {"person": s[0], "organization": s[1]},
                    "title": f"Edge {s.tag}",
                }
            ],
        },
        seeds=((INV, "person", ""), (INV, "organization", "")),
    ),
    "investigation_ftm_import": Case(
        WRITES,
        lambda s: {
            "kb_name": INV,
            "entities": [{"id": s.tag, "schema": "Person", "properties": {"name": [s.tag]}}],
        },
    ),
    "sw_prioritize": Case(
        WRITES,
        lambda s: {"kb_name": SW, "item_ids": [s[0], s[1]]},
        seeds=(
            (SW, "backlog_item", "kind: feature\nstatus: accepted\n"),
            (SW, "backlog_item", "kind: feature\nstatus: accepted\n"),
        ),
    ),
    "sw_create_adr": Case(
        NO_KB_FILE, lambda s: {"kb_name": SW, "title": f"Adr {s.tag}"}, reason=_ADVISORY
    ),
    "sw_claim": Case(
        WRITES,
        lambda s: {"kb_name": SW, "item_id": s[0], "assignee": "agent:x"},
        seeds=((SW, "backlog_item", "kind: feature\nstatus: accepted\n"),),
    ),
    "sw_review": Case(
        WRITES,
        lambda s: {"kb_name": SW, "item_id": s[0], "outcome": "approved", "reviewer": "r"},
        seeds=((SW, "backlog_item", "kind: feature\nstatus: review\nassignee: agent:x\n"),),
        change_set_refused="it records a review verdict in the database",
    ),
    "sw_submit": Case(
        WRITES,
        lambda s: {"kb_name": SW, "item_id": s[0]},
        seeds=((SW, "backlog_item", "kind: feature\nstatus: in_progress\nassignee: agent:x\n"),),
    ),
    "sw_create_backlog_item": Case(
        WRITES, lambda s: {"kb_name": SW, "title": f"Item {s.tag}", "kind": "feature"}
    ),
    "sw_transition": Case(
        WRITES,
        lambda s: {"kb_name": SW, "item_id": s[0], "to_status": "accepted"},
        seeds=((SW, "backlog_item", "kind: feature\nstatus: proposed\n"),),
    ),
    "sw_log": Case(
        NO_KB_FILE,
        lambda s: {"kb_name": SW, "item_id": s[0], "summary": "did a thing"},
        seeds=((SW, "backlog_item", "kind: feature\nstatus: in_progress\n"),),
        reason=_ADVISORY,
    ),
    "social_post": Case(
        WRITES,
        lambda s: {"kb_name": SOCIAL, "title": f"Post {s.tag}", "body": "b", "author_id": "alice"},
        main_rows=("social_reputation_log",),
    ),
    "social_vote": Case(
        NO_KB_FILE,
        lambda s: {"kb_name": SOCIAL, "entry_id": s[0], "user_id": "u", "value": 1},
        seeds=((SOCIAL, "writeup", "author_id: bob\n"),),
        reason="a vote row in the index DB (social_vote table)",
        main_rows=("social_vote",),
    ),
    # -- admin tier ---------------------------------------------------------
    "kb_index_sync": Case(REFUSED, lambda s: {"kb_name": NOTES}, reason=_ADMIN_TOOL),
    "kb_manage": Case(REFUSED, lambda s: {"action": "list", "kb_name": NOTES}, reason=_ADMIN_TOOL),
    "kb_commit": Case(REFUSED, lambda s: {"kb": NOTES, "message": "m"}, reason=_ADMIN_TOOL),
    "kb_push": Case(REFUSED, lambda s: {"kb": NOTES}, reason=_ADMIN_TOOL),
    "kb_registry_add": Case(REFUSED, lambda s: {"name": s.tag, "path": "/x"}, reason=_ADMIN_TOOL),
    "kb_registry_remove": Case(REFUSED, lambda s: {"name": NOTES}, reason=_ADMIN_TOOL),
    "kb_registry_reindex": Case(REFUSED, lambda s: {"name": NOTES}, reason=_ADMIN_TOOL),
    "kb_registry_health": Case(REFUSED, lambda s: {"name": NOTES}, reason=_ADMIN_TOOL),
    "wiki_protect": Case(
        REFUSED,
        lambda s: {"kb_name": WIKI, "entry_id": "x", "level": "semi"},
        reason=_ADMIN_TOOL,
    ),
}


# =============================================================================
# The world
# =============================================================================


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        timeout=60,
    )


def _tree(root: Path) -> dict[str, str]:
    """Every file under ``root`` but git's own and Pyrite's diff index, by
    content hash."""
    out = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and ".git" not in rel.parts and ".pyrite" not in rel.parts:
            out[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# The regime where the landing place cannot be reached: the entry routes and
# tools that route to a worktree today, aimed at PLAIN.
UNREACHABLE_REST: dict[str, Case] = {
    "POST /api/entries": Case(
        WRITES, lambda s: {"json": {"kb": PLAIN, "title": f"Rest {s.tag}", "body": "b"}}
    ),
    "PUT /api/entries/{entry_id}": Case(
        WRITES,
        lambda s: {"url": f"/api/entries/{s[0]}", "json": {"kb": PLAIN, "body": "changed"}},
        seeds=(_note(PLAIN),),
    ),
    "PATCH /api/entries/{entry_id}": Case(
        WRITES,
        lambda s: {
            "url": f"/api/entries/{s[0]}",
            "json": {"kb": PLAIN, "field": "status", "value": "done"},
        },
        seeds=(_note(PLAIN),),
    ),
    "DELETE /api/entries/{entry_id}": Case(
        WRITES,
        lambda s: {"url": f"/api/entries/{s[0]}", "params": {"kb": PLAIN}},
        seeds=(_note(PLAIN),),
    ),
}
UNREACHABLE_MCP: dict[str, Case] = {
    "kb_create": Case(
        WRITES, lambda s: {"kb_name": PLAIN, "entry_type": "note", "title": f"Mcp {s.tag}"}
    ),
    "kb_update": Case(
        WRITES,
        lambda s: {"kb_name": PLAIN, "entry_id": s[0], "body": "changed"},
        seeds=(_note(PLAIN),),
    ),
}


# Per-item reporting in the multi-entry plugin writers: one item that is
# already there, or refused, never stops the rest, and never fails the call
# after part of it is written. `stray` is an entry file the index does not
# hold yet (a hand edit not yet reconciled, ADR-0038): the KB service refuses
# to create over it (ENTRY_EXISTS) where the index's duplicate check sees
# nothing.
PER_ITEM_MCP: dict[str, tuple[str, Case]] = {
    "investigation_start with an existing and a repeated entity": (
        "investigation_start",
        Case(
            WRITES,
            lambda s: {
                "kb_name": INV,
                "title": f"Inv items {s.tag}",
                "initial_entities": [
                    {"name": f"New {s.tag}", "type": "person"},
                    {"name": s[0], "type": "organization"},  # in the KB, as a person
                    {"name": f"New {s.tag}", "type": "person"},  # listed twice
                ],
            },
            seeds=((INV, "person", ""),),
        ),
    ),
    "investigation_bulk_edges with one refused edge": (
        "investigation_bulk_edges",
        Case(
            WRITES,
            lambda s: {
                "kb_name": INV,
                "edges": [
                    {
                        "type": "membership",
                        "fields": {"person": "p", "organization": "o"},
                        "title": title,
                    }
                    for title in (f"First {s.tag}", f"{s.tag}-stray", f"Third {s.tag}")
                ],
            },
        ),
    ),
}
# Cases whose `{tag}-stray` entry file is written after indexing.
_STRAYS = {"investigation_bulk_edges with one refused edge": INV}


def _write_strays(repos: dict[str, Path]) -> None:
    for name, kb in _STRAYS.items():
        for who in ("a", "u"):
            stray = f"{Seeds(f'items:{name}', who, 0).tag}-stray"
            (repos[kb] / "strays").mkdir(exist_ok=True)
            (repos[kb] / "strays" / f"{stray}.md").write_text(
                f"---\nid: {stray}\ntype: note\ntitle: {stray}\n---\nby hand\n"
            )


def _all_cases():
    for name, (_, case) in PER_ITEM_MCP.items():
        yield f"items:{name}", case
    for name, case in REST_CASES.items():
        yield f"rest:{name}", case
    for name, case in MCP_CASES.items():
        yield f"mcp:{name}", case
    for name, case in UNREACHABLE_REST.items():
        yield f"plain-rest:{name}", case
    for name, case in UNREACHABLE_MCP.items():
        yield f"plain-mcp:{name}", case


def _write_seeds(repos: dict[str, Path]) -> None:
    for name, case in _all_cases():
        for who in ("a", "u"):
            seeds = Seeds(name, who, len(case.seeds))
            for (kb, entry_type, extra), entry_id in zip(case.seeds, seeds.ids, strict=True):
                path = repos[kb] / "seeds" / f"{entry_id}.md"
                path.parent.mkdir(exist_ok=True)
                path.write_text(
                    f"---\nid: {entry_id}\ntype: {entry_type}\ntitle: {entry_id}\n"
                    f"{extra}---\nseed body\n"
                )


class World:
    def __init__(self, client, w, repos, tmp):
        self.client = client
        self.w = w
        self.app = w.app
        self.repos = repos
        self.tmp = tmp
        self.cookies = {}

    def main_trees(self) -> dict[str, dict[str, str]]:
        return {kb: _tree(path) for kb, path in self.repos.items()}

    def main_index(self) -> frozenset[tuple]:
        """Every row of every table in the main index, as everyone else reads
        it -- not only `entry`: a change-set write that repoints the `kb` row,
        adds a `link` or a `tag`, has landed on main as surely as a file.
        Only volatile columns are left out (`_INDEX_VOLATILE_COLUMNS`)."""
        import sqlite3

        con = sqlite3.connect(f"file:{self.tmp / 'index.db'}?mode=ro", uri=True)
        try:
            rows: list[tuple] = []
            for name, sql in con.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table'"
            ):
                # A virtual table (FTS, vectors) is read through its shadow
                # tables, which are listed here like any other.
                if (
                    name in _INDEX_VOLATILE_TABLES
                    or name.startswith("sqlite_")
                    or (sql or "").upper().startswith("CREATE VIRTUAL TABLE")
                ):
                    continue
                skip = _INDEX_VOLATILE_COLUMNS.get(name, set())
                cols = [
                    c[1] for c in con.execute(f'PRAGMA table_info("{name}")') if c[1] not in skip
                ]
                select = ", ".join(f'"{c}"' for c in cols)
                rows += [(name, *row) for row in con.execute(f'SELECT {select} FROM "{name}"')]
            return frozenset(rows)
        finally:
            con.close()

    def index_changes(self, before: frozenset[tuple]) -> dict[str, int]:
        """Rows added, changed or removed since `before`, per main-index table.

        One change is expected, and checked rather than ignored: the
        `worktree` row the user's first write to a review KB adds -- the change
        set's own record, the queue state ADR-0044 §3 keeps in the database.
        It must be alice's, on a KB of this world, and nothing else in that
        table may change."""
        changed: dict[str, int] = {}
        for row in self.main_index() ^ before:
            if (
                row[0] == "worktree"
                and row not in before
                and row[2] == self.alice_id
                and row[3] == USER
                and row[4] in self.repos
            ):
                continue
            changed[row[0]] = changed.get(row[0], 0) + 1
        return changed

    def reset_kb_rows(self) -> None:
        """Put main's `kb` table back to the baseline taken once the world
        started, so a case is judged from the same `kb` rows as every other:
        a leak that repointed a row in an earlier case cannot hide the same
        leak in a later one (an idempotent rewrite shows no change)."""
        import sqlite3

        con = sqlite3.connect(self.tmp / "index.db")
        try:
            for row in self.kb_baseline:
                cols = list(row)
                con.execute(
                    f"UPDATE kb SET {', '.join(f'{c} = ?' for c in cols[1:])} WHERE name = ?",
                    [row[c] for c in cols[1:]] + [row["name"]],
                )
            con.commit()
        finally:
            con.close()

    def worktree_trees(self) -> dict[str, dict[str, str]]:
        return {
            kb: _tree(path.parent / ".pyrite-worktrees" / USER) for kb, path in self.repos.items()
        }

    def rest(self, who: str, method: str, path: str, case: Case, seeds: Seeds):
        spec = dict(case.call(seeds))
        url = spec.pop("url", path)
        self.client.cookies.clear()
        self.client.cookies.set("pyrite_session", self.cookies[who])
        try:
            return self.client.request(method, url, **spec)
        finally:
            self.client.cookies.clear()

    def mcp(self, who: str, tool: str, args: dict) -> dict:
        headers = WRITE_KEY_HEADER if who == "key" else _cookie(self.cookies[who])
        out = {}

        async def scenario(tg):
            sse = await _SSE(self.app, headers).open(tg)
            await self.w.initialize(sse, headers)
            msg = await self.w.rpc(sse, "tools/call", {"name": tool, "arguments": args}, headers)
            out["msg"] = msg

        _run(self.client, scenario)
        msg = out["msg"]
        if "error" in msg:  # a protocol-level refusal (unknown tool and the like)
            return {"error": msg["error"].get("message", "protocol error")}
        import json

        text = msg["result"]["content"][0]["text"]
        try:
            return json.loads(text)
        except ValueError:  # the SDK reports a handler exception as plain text
            return {"error": text}


@pytest.fixture(scope="module", params=["canonical", "review"])
def world(request):
    """The same KBs twice: every KB `landing: canonical` (the default), then
    every KB `landing: review` (ADR-0044 §1)."""
    landing = request.param
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        repos = {}
        for kb in KB_TYPES:
            repo = tmp / kb / "repo"
            repo.mkdir(parents=True)
            _git(repo, "init", "-q", "-b", "main")
            repos[kb] = repo
        repos[PLAIN] = tmp / PLAIN / "files"
        repos[PLAIN].mkdir(parents=True)
        _write_seeds(repos)
        for kb in KB_TYPES:
            (repos[kb] / "README.md").write_text("kb\n")
            _git(repos[kb], "add", "-A")
            _git(repos[kb], "commit", "-q", "-m", "seed")
        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name=kb, path=repos[kb], kb_type=kb_type, landing=landing)
                for kb, kb_type in KB_TYPES.items()
            ]
            + [KBConfig(name=PLAIN, path=repos[PLAIN], kb_type="generic", landing=landing)],
            settings=Settings(
                index_path=tmp / "index.db",
                auth=AuthConfig(enabled=True, max_sessions_per_user=50),
                api_keys=[
                    {"key_hash": hashlib.sha256(WRITE_KEY.encode()).hexdigest(), "role": "write"}
                ],
                rate_limit_write="100000/minute",
                rate_limit_admin="100000/minute",
                rate_limit_read="100000/minute",
            ),
        )
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        with PyriteDB(config.settings.index_path) as db:
            IndexManager(db, config).index_all()
        # Committed, so a worktree has them too, but never indexed.
        _write_strays(repos)
        for kb in set(_STRAYS.values()):
            _git(repos[kb], "add", "-A")
            _git(repos[kb], "commit", "-q", "-m", "strays")
        app = create_app(config=config)
        with TestClient(app) as client:
            seed_and_sign_in(client, "root", "password123", role="admin")
            w = _World(app, config)
            alice_id = w.auth(lambda a: a.create_user(USER, "password123", role="write"))["id"]
            world = World(client, w, repos, tmp)
            world.alice_id = alice_id
            world.landing = landing
            world.cookies = {"a": _login(app, "root"), "u": _login(app, USER)}
            # Start the admin- and write-tier MCP servers now: a server's first
            # start reconciles the KB registry with the config (seed_from_config:
            # `kb.source` back to "config"). That is needed because every
            # KBService write calls `register_kb(...)` with no `source`, which
            # turns a config KB's `kb.source` into "user" on main
            # (kb_service.py `_update`, index.py) -- a defect older than this
            # change, tracked separately. Starting the servers here keeps that
            # reconcile from being charged to whichever call came first, and
            # `reset_kb_rows` puts the rows back before every user case.
            world.mcp("a", "kb_list", {})
            world.mcp("key", "kb_list", {})
            import sqlite3

            con = sqlite3.connect(tmp / "index.db")
            con.row_factory = sqlite3.Row
            world.kb_baseline = [dict(r) for r in con.execute("SELECT * FROM kb")]
            con.close()
            yield world


def _refused_rest(resp) -> bool:
    return resp.status_code >= 400


def _refused_mcp(result: dict) -> bool:
    return "error" in result or result.get("success") is False


# =============================================================================
# The registered lists are covered
# =============================================================================


def _registered_rest_writes() -> set[str]:
    return {
        ep.name.replace("{name:path}", "{name}")
        for ep in rest_operations()
        if ep.name.split()[0] in ("POST", "PUT", "PATCH", "DELETE")
    }


@pytest.mark.control(reason="the drift guard over the registered lists holds before and after")
def test_every_rest_write_route_has_a_case():
    registered = _registered_rest_writes()
    assert registered - set(REST_CASES) == set(), "a write route with no case"
    assert set(REST_CASES) - registered == set(), "a case for no registered route"


@pytest.mark.control(reason="the drift guard over the registered lists holds before and after")
def test_every_mcp_write_tool_has_a_case():
    from tests._surface_inventory import mcp_server

    with mcp_server() as server:
        registered = {n for n in server.tools if server._tool_tiers[n] != "read"}
    assert registered - set(MCP_CASES) == set(), "a write tool with no case"
    assert set(MCP_CASES) - registered == set(), "a case for no registered tool"


def _ids(cases: dict[str, Case], kinds: tuple[str, ...]) -> list[str]:
    return [n for n, c in cases.items() if c.kind in kinds]


# =============================================================================
# Controls: the operator's call writes what the case says it writes
# =============================================================================


@pytest.mark.control(reason="the operator lands on main before and after the fix")
@pytest.mark.parametrize("route", _ids(REST_CASES, (WRITES, NO_KB_FILE)))
def test_rest_operator_call_writes(world, route, monkeypatch):
    case = REST_CASES[route]
    if case.stub:
        case.stub(monkeypatch)
    method, path = route.split(" ", 1)
    before = world.main_trees()
    resp = world.rest("a", method, path, case, Seeds(f"rest:{route}", "a", len(case.seeds)))
    after = world.main_trees()
    if case.kind == WRITES:
        assert resp.status_code < 400, resp.text
        assert after != before, f"the operator's {route} changed no KB file: {resp.text}"
    else:
        assert after == before, f"{route} is NO_KB_FILE but changed a file"


@pytest.mark.control(reason="the operator lands on main before and after the fix")
@pytest.mark.parametrize("tool", _ids(MCP_CASES, (WRITES, NO_KB_FILE)))
def test_mcp_operator_call_writes(world, tool):
    case = MCP_CASES[tool]
    before = world.main_trees()
    result = world.mcp("a", tool, case.call(Seeds(f"mcp:{tool}", "a", len(case.seeds))))
    after = world.main_trees()
    if case.kind == WRITES:
        assert not _refused_mcp(result), result
        assert after != before, f"the operator's {tool} changed no KB file: {result}"
    else:
        assert after == before, f"{tool} is NO_KB_FILE but changed a file"


# =============================================================================
# The property: alice's write lands where the rule says, or is refused
# =============================================================================


def _main_changes(world, before) -> dict:
    return {
        kb: sorted(set(t.items()) ^ set(before[kb].items()))
        for kb, t in world.main_trees().items()
        if t != before[kb]
    }


def _worktree_writes(world, wt_before) -> dict:
    """What changed in alice's worktrees since `wt_before`. A worktree made by
    this call (the REST write guard makes one before the handler runs) starts
    as the repository's last commit, so it is unwritten while git sees no
    change in it -- main's checkout, which may hold the operator's uncommitted
    writes, is not the measure."""
    out = {}
    for kb, after in world.worktree_trees().items():
        if wt_before[kb]:
            if after != wt_before[kb]:
                out[kb] = sorted(set(after.items()) ^ set(wt_before[kb].items()))
        elif after:
            path = world.repos[kb].parent / ".pyrite-worktrees" / USER
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=path,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout
            if status.strip():
                out[kb] = status.splitlines()
    return out


def _check_user_call(world, label, case, refused, wt_before, before, index_before, *, rest):
    """The landing rule's answer for one call by alice (ADR-0044 §1, L1-L3).

    Canonical world: a WRITES call lands on the main checkout and makes no
    change set (L3). Review world: nothing reaches main -- neither the
    checkout nor the index -- and a WRITES call either lands in her worktree
    or is refused. REST makes every write it routes; MCP refuses a change-set
    write until it binds services per call.
    """
    changed = _main_changes(world, before)
    if case.kind == REFUSED:
        assert refused, f"{label}: alice must be refused"
        assert changed == {}, f"{label} was refused but wrote the main checkout"
        rows = world.index_changes(index_before)
        assert rows == {}, f"{label} was refused but wrote the main index: {rows}"
        return
    if case.change_set_refused and world.landing == "review":
        assert refused, f"{label}: {case.change_set_refused}; it must be refused"
        assert changed == {}, f"{label} was refused but wrote the main checkout"
        assert _worktree_writes(world, wt_before) == {}, f"{label} was refused but half-wrote"
        rows = world.index_changes(index_before)
        assert rows == {}, f"{label} was refused but wrote the main index: {rows}"
        return
    if world.landing == "canonical":
        assert world.worktree_trees() == wt_before, f"{label} made a change set on a canonical KB"
        if case.kind == WRITES:
            assert not refused, f"{label}: a canonical write by a write-role user was refused"
            assert changed, f"{label} landed nowhere"
        else:
            assert changed == {}, f"{label} is NO_KB_FILE but changed a file"
        return
    assert changed == {}, f"{label} as {USER} wrote the main checkout: {changed}"
    changed_rows = {
        t: n for t, n in world.index_changes(index_before).items() if t not in case.main_rows
    }
    assert changed_rows == {}, f"{label} as {USER} wrote the main index: {changed_rows}"
    if case.kind == WRITES:
        assert not refused, f"{label}: a write to a review KB lands in the change set"
        assert world.worktree_trees() != wt_before, f"{label} succeeded and wrote nowhere"


@pytest.mark.parametrize("route", _ids(REST_CASES, (WRITES, NO_KB_FILE, REFUSED)))
def test_rest_user_write_lands_by_the_rule(world, route, monkeypatch):
    case = REST_CASES[route]
    if case.stub:
        case.stub(monkeypatch)
    method, path = route.split(" ", 1)
    world.reset_kb_rows()
    before, wt_before, index_before = world.main_trees(), world.worktree_trees(), world.main_index()
    resp = world.rest("u", method, path, case, Seeds(f"rest:{route}", "u", len(case.seeds)))
    _check_user_call(
        world,
        f"{route} ({resp.status_code} {resp.text[:200]})",
        case,
        _refused_rest(resp),
        wt_before,
        before,
        index_before,
        rest=True,
    )


@pytest.mark.parametrize("tool", _ids(MCP_CASES, (WRITES, NO_KB_FILE, REFUSED)))
def test_mcp_user_write_lands_by_the_rule(world, tool):
    case = MCP_CASES[tool]
    world.reset_kb_rows()
    before, wt_before, index_before = world.main_trees(), world.worktree_trees(), world.main_index()
    result = world.mcp("u", tool, case.call(Seeds(f"mcp:{tool}", "u", len(case.seeds))))
    _check_user_call(
        world,
        f"{tool} ({str(result)[:200]})",
        case,
        _refused_mcp(result),
        wt_before,
        before,
        index_before,
        rest=False,
    )


# =============================================================================
# Regime: a KB with no git repository (PLAIN)
# =============================================================================


@pytest.mark.control(reason="the operator lands on main before and after the fix")
@pytest.mark.parametrize("route", list(UNREACHABLE_REST))
def test_rest_operator_call_writes_a_plain_kb(world, route):
    case = UNREACHABLE_REST[route]
    method, path = route.split(" ", 1)
    before = world.main_trees()
    resp = world.rest("a", method, path, case, Seeds(f"plain-rest:{route}", "a", len(case.seeds)))
    assert resp.status_code < 400, resp.text
    assert world.main_trees() != before


def _check_plain(world, label, refused, before, index_before):
    """`landing: review` with no repository to hold a change set: refused,
    nothing written anywhere (L2). `landing: canonical`: the write lands on
    the KB's files, git or not (L3)."""
    if world.landing == "canonical":
        assert not refused, f"{label}: a canonical write was refused"
        assert world.main_trees() != before, f"{label} landed nowhere"
        return
    assert world.main_trees() == before, f"{label} fell back to main"
    assert world.index_changes(index_before) == {}, f"{label} wrote the main index"
    assert refused, f"{label}: a change set that cannot be made must refuse"


@pytest.mark.parametrize("route", list(UNREACHABLE_REST))
def test_rest_user_write_to_a_plain_kb(world, route):
    case = UNREACHABLE_REST[route]
    method, path = route.split(" ", 1)
    world.reset_kb_rows()
    before, index_before = world.main_trees(), world.main_index()
    resp = world.rest("u", method, path, case, Seeds(f"plain-rest:{route}", "u", len(case.seeds)))
    _check_plain(
        world,
        f"{route} ({resp.status_code} {resp.text[:200]})",
        _refused_rest(resp),
        before,
        index_before,
    )


@pytest.mark.parametrize("tool", list(UNREACHABLE_MCP))
def test_mcp_user_write_to_a_plain_kb(world, tool):
    case = UNREACHABLE_MCP[tool]
    world.reset_kb_rows()
    before, index_before = world.main_trees(), world.main_index()
    result = world.mcp("u", tool, case.call(Seeds(f"plain-mcp:{tool}", "u", len(case.seeds))))
    _check_plain(world, f"{tool} ({str(result)[:200]})", _refused_mcp(result), before, index_before)


# =============================================================================
# The binding is per call: it never reaches the next caller
# =============================================================================


def test_a_binding_ends_with_its_call_and_never_reaches_a_sibling(world):
    """One shared write-tier server, as `/mcp/sse` gives alice and a
    write-role operator key. (1) In one task, alice's change-set call and then
    the key's call: the key lands on main -- this fails if the binding is not
    reset when the call ends. (2) While alice's target is bound in one task, a
    sibling task on the same server reads the canonical config and index."""
    if world.landing != "review":
        pytest.skip("only a landing: review KB binds a target")
    import anyio

    from pyrite.server.mcp_server import PyriteMCPServer
    from pyrite.services.access_policy import Principal
    from pyrite.services.write_target import binding, write_target

    server = PyriteMCPServer(config=world.w.config, tier="write")
    alice = Principal.user(world.alice_id, "write")
    key = Principal.from_api_key("write")
    try:
        mine = server._dispatch_tool(
            "kb_create",
            {"kb_name": NOTES, "entry_type": "note", "title": "Leak alice"},
            principal=alice,
        )
        assert not _refused_mcp(mine), mine
        before = world.main_trees()
        theirs = server._dispatch_tool(
            "kb_create",
            {"kb_name": NOTES, "entry_type": "note", "title": "Leak key"},
            principal=key,
        )
        assert not _refused_mcp(theirs), theirs
        added = set(world.main_trees()[NOTES]) - set(before[NOTES])
        assert any("leak-key" in path for path in added), f"the key's write missed main: {theirs}"

        canonical_path = server.config.get_kb(NOTES).path
        target = write_target(server.config, server.db, alice, NOTES)
        seen: dict[str, object] = {}

        async def bound(entered, release):
            with binding(target):
                seen["inside"] = server.config.get_kb(NOTES).path
                entered.set()
                await release.wait()

        async def sibling(entered, release):
            await entered.wait()
            seen["sibling"] = server.config.get_kb(NOTES).path
            seen["sibling_db_is_main"] = not getattr(server.db, "is_change_set", False)
            release.set()

        async def main():
            entered, release = anyio.Event(), anyio.Event()
            async with anyio.create_task_group() as tg:
                tg.start_soon(bound, entered, release)
                tg.start_soon(sibling, entered, release)

        anyio.run(main)
        assert seen["inside"] != canonical_path, "the bound task did not see its target"
        assert seen["sibling"] == canonical_path and seen["sibling_db_is_main"], seen
    finally:
        server.close()


def test_an_id_only_in_a_change_set_is_found_only_in_a_named_kb(world):
    """The limit of an id lookup over MCP (#1310, `kb-service.md`): with no
    `kb_name`, a `locates_entry` tool finds the id in the canonical index,
    before any change set is bound -- the change set is chosen by the KB, and
    no KB is known yet. So an item alice created in her change set is
    ENTRY_NOT_FOUND until she names its KB; named, the dispatcher finds it in
    her change set and hands the handler that KB. The handler is a spy: what
    `sw_claim` itself then reads is #1315 (raw SQL reads main's index)."""
    if world.landing != "review":
        pytest.skip("only a landing: review KB has a change set")
    from pyrite.server.mcp_server import PyriteMCPServer
    from pyrite.services.access_policy import Principal

    server = PyriteMCPServer(config=world.w.config, tier="write")
    alice = Principal.user(world.alice_id, "write")
    reached: list[object] = []
    server.tools["sw_claim"] = {
        **server.tools["sw_claim"],
        "handler": lambda args: reached.append(args.get("kb_name")) or {"claimed": True},
    }
    try:
        made = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": SW,
                "entry_type": "backlog_item",
                "title": "Only in the change set 1310",
                "fields": {"kind": "feature", "status": "accepted", "priority": "low"},
            },
            principal=alice,
        )
        assert not _refused_mcp(made), made
        item = made["entry_id"]

        blank = server._dispatch_tool(
            "sw_claim", {"item_id": item, "assignee": "alice"}, principal=alice
        )
        assert blank.get("error_code") == "ENTRY_NOT_FOUND", blank
        assert reached == [], "the handler ran for an id the lookup did not find"

        named = server._dispatch_tool(
            "sw_claim", {"item_id": item, "assignee": "alice", "kb_name": SW}, principal=alice
        )
        assert named == {"claimed": True}, named
        assert reached == [SW], reached
    finally:
        server.close()


# =============================================================================
# Per-item reporting: an existing or refused item never stops the rest
# =============================================================================


@pytest.mark.parametrize("label", list(PER_ITEM_MCP))
@pytest.mark.parametrize("who", ["a", "u"])
def test_one_item_does_not_fail_the_rest(world, label, who):
    """For the operator and for alice, in both worlds: the call succeeds, the
    new items are written where the rule says, an item already there is
    linked (not created again) and a refused item is reported."""
    tool, case = PER_ITEM_MCP[label]
    seeds = Seeds(f"items:{label}", who, len(case.seeds))
    before, wt_before = world.main_trees(), world.worktree_trees()
    result = world.mcp(who, tool, case.call(seeds))
    assert not _refused_mcp(result), result
    lands_in_worktree = who == "u" and world.landing == "review"
    changed = (world.worktree_trees() if lands_in_worktree else world.main_trees()) != (
        wt_before if lands_in_worktree else before
    )
    assert changed, f"{label} wrote nothing: {result}"
    if tool == "investigation_start":
        assert [e["id"] for e in result["entities_created"]] == [f"new-{seeds.tag}"], result
        assert sorted(e["id"] for e in result["entities_linked"]) == sorted(
            [seeds[0], f"new-{seeds.tag}"]
        ), result
        (existing,) = [e for e in result["entities_linked"] if e["id"] == seeds[0]]
        assert (existing["type"], existing["requested_type"]) == ("person", "organization")
        assert "already exists as a person" in existing["warning"], existing
        again = world.mcp(who, tool, case.call(seeds))
        assert again.get("error_code") == "ENTRY_EXISTS", again
    else:
        statuses = [e["status"] for e in result["entries"]]
        assert statuses == ["created", "error", "created"], result
        assert result["entries"][1]["error_code"] == "ENTRY_EXISTS", result
        assert (result["created"], result["errors"]) == (2, 1), result

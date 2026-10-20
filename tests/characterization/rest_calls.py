"""One request-call spec per KB-bearing REST route (ADR-0037 theme 0).

`kb_bearing_rest_operations()` (tests/characterization/surfaces.py) enumerates
the 74 routes structurally. This module says, for each one, how to build a
real HTTP call against a given target KB name -- substituting the KB into
whichever path/query/body location that particular route actually reads it
from (routes disagree: some use `{kb_name}` in the path, some `kb` or
`kb_name` as a query param or a JSON body field), and filling any other
required field with a fixed, harmless placeholder so the call reaches the
authorization layer rather than failing to parse.

Every one of the 74 routes has an entry -- deliberately, so a route added to
`kb_bearing_rest_operations()`'s output with no spec here is a loud
`KeyError`, not a silently-skipped case. A route whose resource this world
does not model (a repo, a review row, a collection, a starred entry, a task)
still gets a spec: the call is real, and what it characterizes is today's
answer for "no such resource exists yet" against each KB state -- which is a
true, if less varied, golden. Nothing here is marked `skip`.

**Write identity is unique per case.** The world is one shared,
session-scoped fixture (built once for speed -- ADR-0037's own perf
constraint), not reset between principals. A write whose identity-bearing
field (a title that becomes an entry slug, a collection title) were a fixed
placeholder would let the SECOND principal's otherwise-identical call collide
with the FIRST's already-created row (`EntryExistsError`/409) instead of
repeating the golden outcome that principal's own authorization actually
produces -- found and fixed while building this harness (a real collision:
`write_key`'s successful `POST /api/entries` left `admin_key`'s later,
otherwise-identical call 409ing instead of also succeeding). Every
`build_call` therefore takes a `call_key`, unique per (route, principal,
kb_state), and every write spec below folds it into its identity fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from tests.characterization.world import (
    PRIVATE_ENTRY,
    READABLE_ENTRY,
    READ_ONLY_ENTRY,
    World,
)

FAKE_ENTRY_ID = "does-not-exist-entry"
FAKE_REVIEW_ID = "999999"
FAKE_COLLECTION_ID = "does-not-exist-collection"
FAKE_TASK_ID = "does-not-exist-task"
FAKE_COMMIT_HASH = "0" * 40
FAKE_TEMPLATE_NAME = "does-not-exist-template"


def _unique_date(call_key: str) -> str:
    """A valid ISO date, unique per `call_key`, for a route (POST
    /api/daily/{date_str}) whose write is idempotent BY DATE -- a fixed
    date would let two different cases' POSTs collide (the second sees
    "already exists" instead of repeating the first's golden creation
    outcome), the same class of bug `_writable_target` fixes for entry ids.
    Deterministic across regenerate and compare runs: a stable hash of
    `call_key`, not a random or wall-clock value."""
    import hashlib

    offset = int(hashlib.sha256(call_key.encode()).hexdigest(), 16) % 3650
    from datetime import date, timedelta

    return (date(2015, 1, 1) + timedelta(days=offset)).isoformat()


def _entry_for(kb: str) -> str:
    """The entry id this world actually put in `kb`, or a fixed placeholder
    for a KB (MISSING, or otherwise) with none seeded under that name -- a
    lookup for it is still a real, meaningful call (it characterizes "this
    id is not in this KB")."""
    from tests.characterization.world import (
        NO_DEFAULT_ROLE,
        NO_DEFAULT_ROLE_ENTRY,
        PRIVATE,
        READABLE,
        READ_ONLY,
    )

    return {
        READABLE: READABLE_ENTRY,
        PRIVATE: PRIVATE_ENTRY,
        READ_ONLY: READ_ONLY_ENTRY,
        NO_DEFAULT_ROLE: NO_DEFAULT_ROLE_ENTRY,
    }.get(kb, FAKE_ENTRY_ID)


_disposable_cache: dict[tuple[str, str], str] = {}
_DISPOSABLE_GRANTEE = "characterization-disposable-grantee"


def _disposable_grantee(world: World) -> int:
    """A local user id that exists ONLY to be the target of a permission
    grant/revoke call -- never one of `world.py`'s own principals (see
    `POST /api/kbs/{name}/permissions`'s spec for why). Created in THIS
    world's database if it is not there yet, and looked up there every time.

    Not cached at module scope: one xdist worker runs more than one module
    that builds a world (`test_rest_matrix`, then `test_kb_walk`), and an id
    cached from the first world names a row the second world's database
    never had -- the grant then fails its foreign key ("FOREIGN KEY
    constraint failed" on `kb_permission.user_id`, CI's shard 2 only, since
    the local run put the two modules on different workers)."""
    rows = world.db.execute_sql(
        "SELECT id FROM local_user WHERE username = :u", {"u": _DISPOSABLE_GRANTEE}
    )
    if rows:
        return rows[0]["id"]
    from pyrite.services.auth_service import AuthService

    auth_service = AuthService(world.db, world.config.settings.auth)
    try:
        return auth_service.create_user(_DISPOSABLE_GRANTEE, "password123", role="read")["id"]
    except ValueError:
        # A concurrent xdist worker on the same database created it first.
        rows = world.db.execute_sql(
            "SELECT id FROM local_user WHERE username = :u", {"u": _DISPOSABLE_GRANTEE}
        )
        return rows[0]["id"]


def _writable_target(world: World, kb: str, call_key: str) -> str:
    """An entry id in `kb` that THIS case alone may mutate -- never the
    world's shared READABLE_ENTRY/PRIVATE_ENTRY/READ_ONLY_ENTRY fixture,
    which every read-only case's golden also depends on staying exactly as
    seeded. Created on demand (via KBService directly, not through the route
    under test -- a fixture must not depend on the write tier or scoping the
    case is itself characterizing) and cached per (kb, call_key) so a
    PUT-then-nothing-reads-it-back case still targets one stable id. A KB
    with no entries at all here (MISSING, and any KB this world does not
    seed) gets a fixed nonexistent id instead -- there is nothing to create
    a disposable row IN."""
    from tests.characterization.world import NO_DEFAULT_ROLE, PRIVATE, READABLE

    if kb not in (READABLE, PRIVATE, NO_DEFAULT_ROLE):
        # MISSING (nothing to create in) and READ_ONLY (KBService refuses
        # any write to it -- that refusal is itself part of what this
        # harness characterizes elsewhere, not something to route around
        # here) both fall back to a fixed nonexistent id.
        return FAKE_ENTRY_ID
    cache_key = (kb, call_key)
    if cache_key in _disposable_cache:
        return _disposable_cache[cache_key]
    entry_id = f"characterization-disposable-{call_key}"
    from pyrite.services.kb_service import KBService

    svc = KBService(world.config, world.db)
    try:
        svc.create_entry(kb, entry_id, "Disposable", "note", "created for a characterization case")
    except Exception:
        pass  # already exists (a re-run within the same process) -- reused as-is
    _disposable_cache[cache_key] = entry_id
    return entry_id


@dataclass(frozen=True)
class RouteCallSpec:
    method: str
    path: str
    build: Callable[[World, str, str], dict[str, Any]]


def _url(path: str, **path_params: str) -> str:
    return path.format(**path_params)


def _spec(
    method: str, path: str, build: Callable[[World, str, str], dict[str, Any]]
) -> RouteCallSpec:
    return RouteCallSpec(method, path, build)


REST_CALL_SPECS: dict[tuple[str, str], RouteCallSpec] = {}


def _register(method: str, path: str, build: Callable[[World, str, str], dict[str, Any]]) -> None:
    REST_CALL_SPECS[(method, path)] = _spec(method, path, build)


# ---------------------------------------------------------------------------
# /api/kbs/{kb_name}...  -- KB named in the path
# ---------------------------------------------------------------------------

_register(
    "GET", "/api/kbs/{kb_name}", lambda w, kb, ck: {"url": _url("/api/kbs/{kb_name}", kb_name=kb)}
)
_register(
    "GET",
    "/api/kbs/{kb_name}/health",
    lambda w, kb, ck: {"url": _url("/api/kbs/{kb_name}/health", kb_name=kb)},
)
_register(
    "GET",
    "/api/kbs/{kb_name}/schema",
    lambda w, kb, ck: {"url": _url("/api/kbs/{kb_name}/schema", kb_name=kb)},
)
_register(
    "GET",
    "/api/kbs/{kb_name}/orient",
    lambda w, kb, ck: {"url": _url("/api/kbs/{kb_name}/orient", kb_name=kb)},
)
_register(
    "POST",
    "/api/kbs/{kb_name}/export",
    lambda w, kb, ck: {
        "url": _url("/api/kbs/{kb_name}/export", kb_name=kb),
        "json": {"repo_url": "https://example.invalid/characterization.git"},
    },
)
_register(
    "GET",
    "/api/kbs/{kb_name}/templates",
    lambda w, kb, ck: {"url": _url("/api/kbs/{kb_name}/templates", kb_name=kb)},
)
_register(
    # {name}, not {kb_name}: admin.py's own path param spelling for this
    # route. Decides access INLINE (surfaces.INLINE_ACCESS_DECIDING_ROUTES),
    # not through a FastAPI dependency -- #476 blocker 4.
    "GET",
    "/api/kbs/{name}/permissions",
    lambda w, kb, ck: {"url": _url("/api/kbs/{name}/permissions", name=kb)},
)
_register(
    "POST",
    "/api/kbs/{name}/permissions",
    # user_id must be a REAL row (kb_permission.user_id is a foreign key to
    # local_user) -- a dedicated disposable grantee, NEVER one of world.py's
    # own principals: a call that succeeds would grant that principal real
    # access, corrupting every OTHER case's assumption about what that
    # principal can see (`local_user` is deliberately grant-less; using it
    # here would give it a real PRIVATE-KB grant the moment any
    # write-capable principal's case runs). Idempotent (an UPSERT), so it
    # needs no call_key-uniqueness the way a content-creating write does.
    lambda w, kb, ck: {
        "url": _url("/api/kbs/{name}/permissions", name=kb),
        "json": {"user_id": _disposable_grantee(w), "role": "read"},
    },
)
_register(
    "GET",
    "/api/kbs/{kb_name}/templates/{template_name}",
    lambda w, kb, ck: {
        "url": _url(
            "/api/kbs/{kb_name}/templates/{template_name}",
            kb_name=kb,
            template_name=FAKE_TEMPLATE_NAME,
        )
    },
)
_register(
    "POST",
    "/api/kbs/{kb_name}/templates/{template_name}/render",
    lambda w, kb, ck: {
        "url": _url(
            "/api/kbs/{kb_name}/templates/{template_name}/render",
            kb_name=kb,
            template_name=FAKE_TEMPLATE_NAME,
        ),
        "json": {"variables": {}},
    },
)
_register(
    "GET",
    "/api/kbs/{kb_name}/changes",
    lambda w, kb, ck: {"url": _url("/api/kbs/{kb_name}/changes", kb_name=kb)},
)

# ---------------------------------------------------------------------------
# /api/kbs  -- cross-KB, filtered by readable_kbs. No KB to target; the same
# call for every "target_kb" (there is no per-KB behaviour to distinguish
# here -- the list itself is what's scoped).
# ---------------------------------------------------------------------------
_register("GET", "/api/kbs", lambda w, kb, ck: {"url": "/api/kbs"})

# ---------------------------------------------------------------------------
# Query-param KB: `kb` or `kb_name`
# ---------------------------------------------------------------------------
_register(
    "GET",
    "/api/search",
    lambda w, kb, ck: {"url": "/api/search", "params": {"q": "zebra", "kb": kb}},
)
_register("GET", "/api/entries", lambda w, kb, ck: {"url": "/api/entries", "params": {"kb": kb}})
_register(
    "GET",
    "/api/entries/titles",
    lambda w, kb, ck: {"url": "/api/entries/titles", "params": {"kb": kb}},
)
_register(
    "GET",
    "/api/entries/wanted",
    lambda w, kb, ck: {"url": "/api/entries/wanted", "params": {"kb": kb}},
)
_register(
    "GET",
    "/api/entries/resolve",
    lambda w, kb, ck: {"url": "/api/entries/resolve", "params": {"target": "x", "kb": kb}},
)
_register(
    "GET",
    "/api/entries/export",
    lambda w, kb, ck: {"url": "/api/entries/export", "params": {"kb": kb}},
)
_register(
    "POST",
    "/api/entries/import",
    lambda w, kb, ck: {
        "url": "/api/entries/import",
        "params": {"kb": kb, "format": "json"},
        "files": {"file": ("characterization.json", b"[]", "application/json")},
    },
)
_register("GET", "/api/timeline", lambda w, kb, ck: {"url": "/api/timeline", "params": {"kb": kb}})
_register("GET", "/api/tags", lambda w, kb, ck: {"url": "/api/tags", "params": {"kb": kb}})
_register(
    "GET", "/api/tags/tree", lambda w, kb, ck: {"url": "/api/tags/tree", "params": {"kb": kb}}
)
_register("GET", "/api/starred", lambda w, kb, ck: {"url": "/api/starred", "params": {"kb": kb}})
_register(
    "GET", "/api/daily/dates", lambda w, kb, ck: {"url": "/api/daily/dates", "params": {"kb": kb}}
)
_register(
    "GET", "/api/collections", lambda w, kb, ck: {"url": "/api/collections", "params": {"kb": kb}}
)
_register(
    "GET",
    "/api/collections/{collection_id}",
    lambda w, kb, ck: {
        "url": _url("/api/collections/{collection_id}", collection_id=FAKE_COLLECTION_ID),
        "params": {"kb": kb},
    },
)
_register(
    "GET",
    "/api/collections/{collection_id}/entries",
    lambda w, kb, ck: {
        "url": _url("/api/collections/{collection_id}/entries", collection_id=FAKE_COLLECTION_ID),
        "params": {"kb": kb},
    },
)
_register(
    "GET", "/api/qa/status", lambda w, kb, ck: {"url": "/api/qa/status", "params": {"kb": kb}}
)
_register(
    "GET", "/api/qa/validate", lambda w, kb, ck: {"url": "/api/qa/validate", "params": {"kb": kb}}
)
_register(
    "GET", "/api/qa/coverage", lambda w, kb, ck: {"url": "/api/qa/coverage", "params": {"kb": kb}}
)
_register("GET", "/api/tasks", lambda w, kb, ck: {"url": "/api/tasks", "params": {"kb": kb}})

_register(
    "GET",
    "/api/qa/validate/{entry_id}",
    lambda w, kb, ck: {
        "url": _url("/api/qa/validate/{entry_id}", entry_id=_entry_for(kb)),
        "params": {"kb": kb},
    },
)

# entry_id path param + kb query
_register(
    "GET",
    "/api/entries/{entry_id}",
    lambda w, kb, ck: {
        "url": _url("/api/entries/{entry_id}", entry_id=_entry_for(kb)),
        "params": {"kb": kb},
    },
)
_register(
    "DELETE",
    "/api/entries/{entry_id}",
    # A disposable entry, never READABLE_ENTRY/PRIVATE_ENTRY: a DELETE that
    # targeted the world's shared fixture entry (this route's fix, found
    # while building this harness) permanently removed it the first time a
    # write-capable principal's case ran, so every READ case that ran after
    # it in the same process -- including a later principal's `GET
    # /api/entries/{entry_id}` on the SAME shared world -- saw 404 instead of
    # the golden's 200. `_writable_target` never touches the seeded fixture.
    lambda w, kb, ck: {
        "url": _url("/api/entries/{entry_id}", entry_id=_writable_target(w, kb, ck)),
        "params": {"kb": kb},
    },
)


def _delete_starred_kwargs(world: World, kb: str, call_key: str) -> dict[str, Any]:
    """Guarantee `entry_id` is already starred by the CALLING principal's
    own owner id, directly via the service (not through POST /api/starred,
    whose own authorization this DELETE case is not testing), before
    building the DELETE call. Without this, the call depends on whether
    some OTHER case -- this principal's own `POST /api/starred`
    (alphabetically AFTER `DELETE` in `_run`'s sorted route order) or
    another principal's, in the same worker -- already starred it first:
    found live under pytest-xdist -n4, `admin_key`'s DELETE flipping between
    200 (unstarred) and 404 (nothing to unstar) depending on worker
    composition. `call_key` (`"{principal_name}-{kb_state}"`, see
    test_rest_matrix.py) is parsed back to find which principal is calling,
    since REST_CALL_SPECS's build functions are not otherwise given one.
    """
    from pyrite.services.kb_service import KBService
    from pyrite.services.starred_service import INSTANCE_USER, StarredService

    entry_id = _entry_for(kb)
    # NOT a bare rsplit("-", 1): call_key is "{principal_name}-{kb_state}",
    # but every kb_state value itself contains a hyphen (readable-kb,
    # private-kb, missing-kb, no-default-role-kb), so a naive rsplit finds
    # the WRONG boundary every time (e.g. "admin_key-readable-kb".rsplit("-",
    # 1) -> "admin_key-readable", which matches no real principal -- found
    # live: this silently skipped pre-starring for every call, and the
    # xdist flakiness this function exists to fix was still reproducing).
    # Matching against the known, fixed set of principal names instead is
    # unambiguous regardless of how many hyphens the kb_state suffix has.
    principal_name = next(
        (name for name in world.principals if call_key.startswith(f"{name}-")), None
    )
    principal = world.principals.get(principal_name) if principal_name else None
    # anonymous (and an unrecognised call_key, e.g. the skip-list probe
    # which passes call_key="shared") has no star list at all (_star_owner
    # returns None for anonymous -- 401 LOGIN_REQUIRED); nothing to pre-star,
    # and the call still characterizes that refusal correctly without one.
    if principal is not None and principal.kind != "anonymous":
        owner = principal.user_id if principal.user_id is not None else INSTANCE_USER
        svc = StarredService(world.db, KBService(world.config, world.db))
        try:
            svc.star_entry(owner, entry_id, kb)
        except Exception:
            pass  # already starred (a re-run within the same process) -- fine
    return {
        "url": _url("/api/starred/{entry_id}", entry_id=entry_id),
        "params": {"kb": kb},
    }


_register(
    "DELETE",
    "/api/starred/{entry_id}",
    lambda w, kb, ck: _delete_starred_kwargs(w, kb, ck),
)
_register(
    "GET",
    "/api/daily/{date_str}",
    # A call-key-unique date -- NOT a fixed reserved one. This route's
    # handler (`get_or_create_daily_note`) auto-creates the note as a side
    # effect for any WRITE-tier caller (its own docstring: "auto-creating it
    # only for a write-tier caller"), so a fixed date still collided: one
    # principal's write-tier GET silently created it, and a later, different
    # principal's read-tier GET on the SAME date then saw 200-with-a-note in
    # the golden but 404 on an isolated re-run (worker composition changed
    # which GET ran first). Namespaced with a "get" prefix so this route's
    # own date never collides with the POST spec's date for the same
    # (principal, kb_state).
    lambda w, kb, ck: {
        "url": _url("/api/daily/{date_str}", date_str=_unique_date(f"get-{ck}")),
        "params": {"kb": kb},
    },
)
_register(
    "POST",
    "/api/daily/{date_str}",
    # A call-key-unique date so two different (principal, kb_state) cases'
    # POSTs never collide with each other OR with the GET spec's date above.
    lambda w, kb, ck: {
        "url": _url("/api/daily/{date_str}", date_str=_unique_date(f"post-{ck}")),
        "params": {"kb": kb},
    },
)
_register(
    "GET",
    "/api/entries/{entry_id}/versions",
    lambda w, kb, ck: {
        "url": _url("/api/entries/{entry_id}/versions", entry_id=_entry_for(kb)),
        "params": {"kb": kb},
    },
)
_register(
    "GET",
    "/api/entries/{entry_id}/versions/{commit_hash}",
    lambda w, kb, ck: {
        "url": _url(
            "/api/entries/{entry_id}/versions/{commit_hash}",
            entry_id=_entry_for(kb),
            commit_hash=FAKE_COMMIT_HASH,
        ),
        "params": {"kb": kb},
    },
)
_register(
    "GET",
    "/api/entries/{entry_id}/blocks",
    lambda w, kb, ck: {
        "url": _url("/api/entries/{entry_id}/blocks", entry_id=_entry_for(kb)),
        "params": {"kb": kb},
    },
)

# reviews: `kb` query maps to the `kb_name` alias
_register(
    "GET",
    "/api/reviews",
    lambda w, kb, ck: {"url": "/api/reviews", "params": {"entry_id": _entry_for(kb), "kb": kb}},
)
_register(
    "GET",
    "/api/reviews/latest",
    lambda w, kb, ck: {
        "url": "/api/reviews/latest",
        "params": {"entry_id": _entry_for(kb), "kb": kb},
    },
)
_register(
    "GET",
    "/api/reviews/status",
    lambda w, kb, ck: {
        "url": "/api/reviews/status",
        "params": {"entry_id": _entry_for(kb), "kb": kb},
    },
)
_register(
    "DELETE",
    "/api/reviews/{review_id}",
    # RowKB-resolved: no review row exists in this world under any id, so
    # every KB target hits the same "review not found" answer -- a true
    # characterization of "no such review", not of per-KB scoping. See the
    # report's recommendation to seed one review row per KB for a sharper
    # golden here.
    lambda w, kb, ck: {"url": _url("/api/reviews/{review_id}", review_id=FAKE_REVIEW_ID)},
)

_register(
    "POST",
    "/api/tasks/{task_id}/claim",
    lambda w, kb, ck: {
        "url": _url("/api/tasks/{task_id}/claim", task_id=FAKE_TASK_ID),
        "params": {"kb": kb},
        "json": {"assignee": "characterization-bot"},
    },
)

# ---------------------------------------------------------------------------
# JSON body carries the KB field
# ---------------------------------------------------------------------------
_register(
    "POST",
    "/api/entries/resolve-batch",
    lambda w, kb, ck: {"url": "/api/entries/resolve-batch", "json": {"targets": ["x"], "kb": kb}},
)
_register(
    "POST",
    "/api/entries/batch",
    lambda w, kb, ck: {
        "url": "/api/entries/batch",
        "json": {"entries": [{"entry_id": _entry_for(kb), "kb_name": kb}]},
    },
)
_register(
    "POST",
    "/api/entries",
    # A unique title per case: two different (principal, kb_state) cases
    # that both reach the create path must each get their own row, not
    # collide on "characterization entry" and 409 the second one -- see the
    # module docstring's write-identity note.
    lambda w, kb, ck: {
        "url": "/api/entries",
        "json": {"kb": kb, "title": f"characterization entry {ck}", "body": "x"},
    },
)
_register(
    "PUT",
    "/api/entries/{entry_id}",
    # Targets a disposable entry unique to this case (never the world's
    # shared READABLE_ENTRY/PRIVATE_ENTRY fixture, which other cases' reads
    # depend on staying as seeded) -- created on demand by `_writable_target`.
    lambda w, kb, ck: {
        "url": _url("/api/entries/{entry_id}", entry_id=_writable_target(w, kb, ck)),
        "json": {"kb": kb, "title": f"characterization entry (updated) {ck}"},
    },
)
_register(
    "PATCH",
    "/api/entries/{entry_id}",
    lambda w, kb, ck: {
        "url": _url("/api/entries/{entry_id}", entry_id=_writable_target(w, kb, ck)),
        "json": {"kb": kb, "field": "title", "value": f"characterization patched title {ck}"},
    },
)
_register(
    "POST",
    "/api/ai/summarize",
    lambda w, kb, ck: {
        "url": "/api/ai/summarize",
        "json": {"entry_id": _entry_for(kb), "kb_name": kb},
    },
)
_register(
    "POST",
    "/api/ai/auto-tag",
    lambda w, kb, ck: {
        "url": "/api/ai/auto-tag",
        "json": {"entry_id": _entry_for(kb), "kb_name": kb},
    },
)
_register(
    "POST",
    "/api/ai/suggest-links",
    lambda w, kb, ck: {
        "url": "/api/ai/suggest-links",
        "json": {"entry_id": _entry_for(kb), "kb_name": kb},
    },
)
_register(
    "POST",
    "/api/ai/chat",
    lambda w, kb, ck: {
        "url": "/api/ai/chat",
        "json": {"messages": [{"role": "user", "content": "hello"}], "kb": kb},
    },
)
_register(
    "POST",
    "/api/starred",
    lambda w, kb, ck: {"url": "/api/starred", "json": {"entry_id": _entry_for(kb), "kb_name": kb}},
)
_register(
    "POST",
    "/api/collections",
    lambda w, kb, ck: {
        "url": "/api/collections",
        "json": {"kb": kb, "title": f"characterization collection {ck}", "query": "*"},
    },
)
_register(
    "POST",
    "/api/collections/query-preview",
    lambda w, kb, ck: {"url": "/api/collections/query-preview", "json": {"query": "*", "kb": kb}},
)
_register(
    "POST",
    "/api/clip",
    lambda w, kb, ck: {
        "url": "/api/clip",
        "json": {"url": "https://example.invalid/characterization", "kb": kb},
    },
)
_register(
    "POST",
    "/api/reviews",
    lambda w, kb, ck: {
        "url": "/api/reviews",
        "json": {
            "entry_id": _entry_for(kb),
            "kb_name": kb,
            "reviewer": "characterization-bot",
            "reviewer_type": "agent",
            "result": "pass",
        },
    },
)

# ---------------------------------------------------------------------------
# Cross-KB, no single target: graph (center_kb secondary), links discovery
# ---------------------------------------------------------------------------
_register(
    "GET",
    "/api/graph",
    lambda w, kb, ck: {"url": "/api/graph", "params": {"kb": kb}},
)
_register(
    "GET",
    "/api/links/discover-neighbors",
    # mode="keyword" pinned explicitly: the route's default is "hybrid",
    # which lazily loads a real sentence-transformers model on first call
    # (seconds, and -- observed while building this harness -- reusing that
    # loaded model from a later, unrelated request under TestClient hung
    # indefinitely; a tokenizer-parallelism/fork interaction, not an
    # authorization behaviour). Irrelevant to what this harness pins, so the
    # semantic/hybrid path is avoided rather than characterized.
    lambda w, kb, ck: {
        "url": "/api/links/discover-neighbors",
        "params": {"entry_id": _entry_for(kb), "kb": kb, "mode": "keyword"},
    },
)
_register(
    "GET",
    "/api/links/batch-suggest",
    lambda w, kb, ck: {
        "url": "/api/links/batch-suggest",
        "params": {"source_kb": kb, "target_kb": kb, "mode": "keyword"},
    },
)

# ---------------------------------------------------------------------------
# Cross-KB list routes with no per-call KB parameter at all: `readable`
# filters after the fact. Calling once per nominal "target_kb" still
# produces the same request every time (there is nothing to vary), but
# keeping one spec per (method, path) keeps the driver uniform; the request
# itself does not change with `kb`.
# ---------------------------------------------------------------------------
_register("GET", "/api/stats", lambda w, kb, ck: {"url": "/api/stats"})

# ---------------------------------------------------------------------------
# Repos: the KB is inside a repository record this world never registers.
# Every target_kb produces the identical call (there is no repo named `kb`),
# which is a true, if repo-registration-blind, characterization: "no
# subscribed repository" answers the same regardless of which KB a caller
# is asking about, for every principal. See the report's recommendation to
# add one subscribed repo per KB state for a sharper golden.
# ---------------------------------------------------------------------------
_register("GET", "/api/repos", lambda w, kb, ck: {"url": "/api/repos"})
_register(
    "GET", "/api/repos/{name:path}", lambda w, kb, ck: {"url": _url("/api/repos/{name}", name=kb)}
)
_register(
    "POST",
    "/api/repos/{name:path}/sync",
    lambda w, kb, ck: {"url": _url("/api/repos/{name}/sync", name=kb)},
)
_register(
    "DELETE",
    "/api/repos/{name:path}",
    lambda w, kb, ck: {"url": _url("/api/repos/{name}", name=kb)},
)
_register(
    "POST",
    "/api/repos/{name:path}/pr",
    lambda w, kb, ck: {
        "url": _url("/api/repos/{name}/pr", name=kb),
        "json": {"title": "characterization PR"},
    },
)


# ---------------------------------------------------------------------------
# Worktree + entries/types routes (#476 round-2 issue 2/3): newly
# characterized via INLINE_ACCESS_DECIDING_ROUTES, none of them had a spec
# before this round.
# ---------------------------------------------------------------------------

_register(
    "GET",
    "/api/worktree/status",
    lambda w, kb, ck: {"url": "/api/worktree/status", "params": {"kb": kb}},
)
_register(
    "GET",
    "/api/worktree/changes",
    lambda w, kb, ck: {"url": "/api/worktree/changes", "params": {"kb": kb}},
)
_register(
    "POST",
    "/api/worktree/submit",
    lambda w, kb, ck: {"url": "/api/worktree/submit", "json": {"kb": kb}},
)
_register(
    "POST",
    "/api/worktree/reset",
    lambda w, kb, ck: {"url": "/api/worktree/reset", "json": {"kb": kb}},
)
_register(
    "GET",
    "/api/entries/types",
    lambda w, kb, ck: {"url": "/api/entries/types", "params": {"kb": kb}},
)
_register(
    "GET",
    "/api/entries/type-schemas",
    lambda w, kb, ck: {"url": "/api/entries/type-schemas", "params": {"kb": kb}},
)


def build_call(
    world: World, method: str, path: str, target_kb: str, *, call_key: str = "shared"
) -> dict[str, Any]:
    """The `client.request(method, **kwargs)` kwargs for `(method, path)`
    against `target_kb`. Raises KeyError -- not a silent skip -- for a route
    this module has no spec for.

    `call_key` must be unique per (route, principal, kb_state) case for a
    write whose identity-bearing field would otherwise collide with an
    earlier case's already-created row (see the module docstring); read-only
    specs ignore it."""
    try:
        spec = REST_CALL_SPECS[(method, path)]
    except KeyError as exc:
        raise KeyError(
            f"tests/characterization/rest_calls.py has no REST_CALL_SPECS entry for "
            f"{method} {path} -- every KB-bearing route must have one (add it, don't skip)"
        ) from exc
    return dict(spec.build(world, target_kb, call_key))

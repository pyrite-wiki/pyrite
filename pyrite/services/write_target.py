"""The one factory every surface writes through (ADR-0044 §1).

`write_target(config, db, principal, kb_name)` asks the landing rule,
`AccessPolicy.lands_in`, and returns the `(config, db)` a write by
`principal` to `kb_name` is made with:

- Canonical: the instance's own config and index, unchanged.
- ChangeSet: a config whose `kb_name` points at the principal's working copy,
  and an overlay of that working copy's diff index over the main index.
- Deny, or a change set that cannot be reached (no git repository, no user
  to own it): `LandingRefusedError`. A write never falls back to canonical
  (ADR-0044 §1, invariant L2).

No surface decides where a write lands: REST asks through
`requires_kb_tier("write")` and builds its services from the result; MCP asks
in `_dispatch_tool`.

The working copy behind a change set is, for now, the per-user worktree of
ADR-0024 (`WorktreeService`, branch `user/<name>`, uncommitted until submit).
ADR-0044 step 1 replaces it with a ref per change set; this seam is the part
that stays.
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
from typing import TYPE_CHECKING, cast

from ..exceptions import LandingRefusedError as _LandingRefusedError
from ..exceptions import ValidationError
from .access_policy import AccessPolicy, Landing, Principal

if TYPE_CHECKING:
    from ..config import PyriteConfig
    from ..storage.database import PyriteDB


# Defined with the other refusals, so storage can raise it too; re-exported.
LandingRefusedError = _LandingRefusedError


def write_target(
    config: PyriteConfig,
    db: PyriteDB,
    principal: Principal | None,
    kb_name: str,
    *,
    diff_cache: dict | None = None,
) -> tuple[PyriteConfig, PyriteDB]:
    """The `(config, db)` `principal`'s write to `kb_name` is made with.

    `diff_cache` keeps one diff-index handle per (user, KB) across calls;
    without one, a fresh handle is opened.
    """
    landing = AccessPolicy(config, db).lands_in(principal, kb_name)
    if landing is Landing.CANONICAL:
        return config, db
    if landing is Landing.DENY or principal is None or principal.user_id is None:
        raise LandingRefusedError(f"A write to KB '{kb_name}' has no place to land")
    return change_set_target(config, db, principal.user_id, kb_name, diff_cache=diff_cache)


def _username(db: PyriteDB, user_id: int) -> str:
    rows = db.execute_sql(
        "SELECT username FROM local_user WHERE id = :user_id", {"user_id": user_id}
    )
    return rows[0]["username"] if rows else f"user-{user_id}"


def change_set_target(
    config: PyriteConfig,
    db: PyriteDB,
    user_id: int,
    kb_name: str,
    *,
    diff_cache: dict | None = None,
    username: str | None = None,
) -> tuple[PyriteConfig, PyriteDB]:
    """The target of a write into `user_id`'s change set on `kb_name`, made
    on first use. Asks no rule: `write_target` decides that this is where a
    write lands; tests that need a pending change set call this directly."""
    from ..storage.backends.overlay_backend import WorktreeDB
    from .worktree_service import WorktreeService

    wt_svc = WorktreeService(config, db)
    try:
        wt_svc.ensure_worktree(kb_name, user_id, username or _username(db, user_id))
    except ValidationError as e:
        # Not a git repository, or a name that cannot be a branch: the change
        # set cannot be made, so the write is refused (L2).
        raise LandingRefusedError(
            f"KB '{kb_name}' lands writes in a change set, which cannot be made: {e}"
        ) from e
    user_kb = wt_svc.get_user_kb_config(kb_name, user_id)
    if user_kb is None:
        raise LandingRefusedError(f"KB '{kb_name}': the change set's working copy is missing")
    # The working copy is where this write lands; nothing downstream re-asks.
    user_kb = dataclasses.replace(user_kb, landing="canonical")
    target_config = dataclasses.replace(
        config,
        knowledge_bases=[user_kb if kb.name == kb_name else kb for kb in config.knowledge_bases],
    )

    key = (user_id, kb_name)
    diff_db = diff_cache.get(key) if diff_cache is not None else None
    if diff_db is None:
        diff_db = wt_svc.get_user_diff_db(kb_name, user_id)
        if diff_db is not None and diff_cache is not None:
            diff_cache[key] = diff_db
    if diff_db is None:
        raise LandingRefusedError(f"KB '{kb_name}': the change set has no index")
    # WorktreeDB is the overlay that stands in for a PyriteDB (duck-typed).
    return target_config, cast("PyriteDB", WorktreeDB(db, diff_db))


# =============================================================================
# Per-call binding (MCP, plugins)
# =============================================================================
#
# MCP serves every caller at a tier from one shared `PyriteMCPServer`, and
# plugins read one process-wide `PluginContext`; neither can hold a caller's
# write target. `_dispatch_tool` binds the target for exactly one tool call,
# and the server's services and the plugin context read it while it is bound.
# A contextvar: an asyncio task (one MCP session's call) sees only its own
# binding, and the dispatch is synchronous, so no other call interleaves.


@dataclasses.dataclass
class BoundTarget:
    """A write target bound for one call, with the services built on it."""

    config: PyriteConfig
    db: PyriteDB
    services: dict = dataclasses.field(default_factory=dict)

    def service(self, name: str, build):
        """The call's `name` service, built once on this target."""
        if name not in self.services:
            self.services[name] = build(self.config, self.db)
        return self.services[name]


_BOUND: contextvars.ContextVar[BoundTarget | None] = contextvars.ContextVar(
    "pyrite_write_target", default=None
)


def current_target() -> BoundTarget | None:
    """The write target bound for this call, or None (canonical)."""
    return _BOUND.get()


@contextlib.contextmanager
def binding(target: tuple[PyriteConfig, PyriteDB] | None):
    """Bind `target` for the duration of the block; None binds nothing."""
    if target is None:
        yield None
        return
    token = _BOUND.set(BoundTarget(*target))
    try:
        yield _BOUND.get()
    finally:
        _BOUND.reset(token)


def context_kb_service(ctx):
    """The `KBService` for a plugin context (`PluginContext.kb_service`): the
    bound call's own while a target is bound, else one on the context's."""
    from .kb_service import KBService

    bound = current_target()
    if bound is not None:
        return bound.service("kb", KBService)
    return KBService(ctx.config, ctx.db)

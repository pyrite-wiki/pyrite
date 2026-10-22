"""Request-scoped worktree resolution for per-user read/write routing.

Provides WorktreeResolver as a FastAPI dependency that read endpoints use to
see a user's own pending edits through an overlay. Where a *write* lands is
not decided here: that is the landing rule (`AccessPolicy.lands_in`) and its
one factory, `services.write_target` (ADR-0044 §1).
"""

from __future__ import annotations

import logging
from typing import Any

from pyrite.config import PyriteConfig
from pyrite.services.kb_service import KBService
from pyrite.services.worktree_service import WorktreeService
from pyrite.storage.backends.overlay_backend import WorktreeDB
from pyrite.storage.database import PyriteDB

logger = logging.getLogger(__name__)


class WorktreeResolver:
    """Resolves worktree context for the current request user + KB.

    Used by entry endpoints to route reads through an overlay, so users see
    their own pending edits.
    """

    def __init__(
        self,
        config: PyriteConfig,
        main_db: PyriteDB,
        diff_db_cache: dict[tuple[int, str], PyriteDB],
    ):
        self._config = config
        self._main_db = main_db
        self._wt_svc = WorktreeService(config, main_db)
        self._diff_cache = diff_db_cache

    def _get_diff_db(self, user_id: int, kb_name: str) -> PyriteDB | None:
        """Get or create a cached diff PyriteDB for a user+KB."""
        key = (user_id, kb_name)
        if key in self._diff_cache:
            return self._diff_cache[key]
        diff_db = self._wt_svc.get_user_diff_db(kb_name, user_id)
        if diff_db is not None:
            self._diff_cache[key] = diff_db
        return diff_db

    def get_read_db(self, kb_name: str, auth_user: dict[str, Any] | None) -> PyriteDB:
        """Get a DB handle for reading, with overlay if user has pending edits.

        Returns a WorktreeDB (overlay) if the user has an active worktree,
        otherwise returns the main DB unchanged. ``kb_name`` is the full
        name the route's authorization already resolved (#952).
        """
        if not auth_user:
            return self._main_db
        user_id = auth_user["id"]
        wt = self._wt_svc.get_worktree(kb_name, user_id)
        if not wt:
            return self._main_db
        diff_db = self._get_diff_db(user_id, kb_name)
        if not diff_db:
            return self._main_db
        return WorktreeDB(self._main_db, diff_db)

    def get_read_service(self, kb_name: str, auth_user: dict[str, Any] | None) -> KBService:
        """Get a KBService configured for reading with overlay."""
        read_db = self.get_read_db(kb_name, auth_user)
        return KBService(self._config, read_db)

    def invalidate_diff_cache(self, user_id: int, kb_name: str) -> None:
        """Remove a cached diff DB after merge/reset/delete."""
        key = (user_id, kb_name)
        cached = self._diff_cache.pop(key, None)
        if cached:
            try:
                cached.close()
            except Exception:
                pass

    @property
    def worktree_service(self) -> WorktreeService:
        """Access the underlying WorktreeService for submit/merge/reject."""
        return self._wt_svc

"""
Plugin Context

Provides shared dependencies to plugins, replacing per-handler self-bootstrapping.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..config import PyriteConfig
    from ..schema import KBSchema
    from ..storage.database import PyriteDB


@dataclass
class PluginContext:
    """Shared context for plugin operations.

    Provides config, db, and request-scoped metadata to plugins,
    eliminating the need for each handler to call load_config()/PyriteDB().

    Supports dict-style access for backwards compatibility with hook context dicts.
    """

    config: PyriteConfig
    db: PyriteDB
    kb_name: str = ""
    user: str = ""
    operation: str = ""
    kb_type: str = ""
    kb_schema: KBSchema | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __getattribute__(self, name: str) -> Any:
        """`config` and `db` are the bound write target while one is bound.

        One PluginContext serves every caller of a server process, so it
        cannot hold a caller's write target. While an MCP call whose write
        lands in a change set runs, `services.write_target.binding` holds that
        target, and a plugin handler that reads `ctx.config` / `ctx.db` -- or
        builds a `KBService` from them -- writes where the landing rule says
        (ADR-0044 §1). Outside such a call they are the values given.
        """
        if name == "config" or name == "db":
            from ..services.write_target import current_target

            bound = current_target()
            if bound is not None:
                return getattr(bound, name)
        return object.__getattribute__(self, name)

    @property
    def kb_service(self):
        """The `KBService` a plugin writes with: the bound call's, else one on
        this context's config and index."""
        from ..services.write_target import context_kb_service

        return context_kb_service(self)

    def __getitem__(self, key: str) -> Any:
        """Dict-style access for backwards compat with hooks expecting context['kb_name']."""
        if hasattr(self, key):
            return getattr(self, key)
        return self.extra[key]

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-style .get() for backwards compat."""
        if hasattr(self, key):
            return getattr(self, key)
        return self.extra.get(key, default)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key) or key in self.extra

    def search_semantic(
        self, query: str, kb_name: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Search using semantic (vector) search if available, falls back to FTS5.

        Args:
            query: Natural language search query
            kb_name: Filter to specific KB (None for all)
            limit: Maximum results to return

        Returns:
            List of matching entry dicts with id, kb_name, title, entry_type,
            snippet. Rows from the vector index also carry ``similarity``
            (cosine, 3 places, the same on every backend) and ``distance``
            (backend-specific; removed in 0.27; use ``similarity``). Rows from the
            keyword fallback carry neither. No verdict is attached: that is
            `SearchService.search_response`, for a surface that shows a page.
        """
        kb = kb_name or self.kb_name

        # Try semantic search first
        try:
            from ..services.embedding_service import (
                EmbeddingService,
                configured_embedding_model,
                is_available,
            )

            if is_available() and self.db.vec_available:
                # The configured model, like the entries (#668). And
                # `search_similar`: this called `svc.search`, which
                # EmbeddingService has never had, so the AttributeError was
                # caught below and every plugin search was a keyword search.
                svc = EmbeddingService(
                    self.db,
                    model_name=configured_embedding_model(getattr(self.config, "settings", None)),
                )
                results = svc.search_similar(query, kb_name=kb, limit=limit)
                if results:
                    return results
        except Exception:
            # Broad on purpose: a plugin asked for results and the keyword
            # index can still give some. Never silent: a bug in the vector
            # path (a TypeError, a KeyError) is logged here with its traceback.
            logger.warning("Semantic search failed, falling back to keyword", exc_info=True)

        # Fallback to FTS5 keyword search
        try:
            from ..services.search_service import SearchService

            search_svc = SearchService(self.db)
            return search_svc.search(query, kb_name=kb, limit=limit)
        except Exception:
            logger.warning("Keyword search fallback also failed", exc_info=True)
            return []

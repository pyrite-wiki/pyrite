"""
Service Layer for pyrite

Provides unified business logic extracted from API, CLI, and UI layers.
Eliminates duplication and ensures consistent behavior across interfaces.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .git_service import GitService
    from .kb_service import KBService
    from .query_expansion_service import QueryExpansionService
    from .repo_service import RepoService
    from .search_service import SearchMode, SearchService
    from .user_service import UserService

__all__ = [
    "GitService",
    "KBService",
    "QueryExpansionService",
    "RepoService",
    "SearchMode",
    "SearchService",
    "UserService",
]

_SERVICE_MAP = {
    "GitService": ".git_service",
    "KBService": ".kb_service",
    "QueryExpansionService": ".query_expansion_service",
    "RepoService": ".repo_service",
    "SearchMode": ".search_service",
    "SearchService": ".search_service",
    "UserService": ".user_service",
}


def __getattr__(name: str):
    if name in _SERVICE_MAP:
        import importlib

        mod = importlib.import_module(_SERVICE_MAP[name], __package__)
        val = getattr(mod, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

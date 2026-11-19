"""
pyrite Storage Layer

SQLAlchemy ORM for standard tables, raw SQL for FTS5/sqlite-vec virtual tables.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .database import PyriteDB
    from .document_manager import DocumentManager
    from .index import IndexManager
    from .models import (
        KB,
        Base,
        Entry,
        EntryTag,
        EntryVersion,
        Link,
        Repo,
        Source,
        Tag,
        User,
        WorkspaceRepo,
    )
    from .repository import KBRepository

__all__ = [
    "Base",
    "DocumentManager",
    "PyriteDB",
    "Entry",
    "EntryTag",
    "EntryVersion",
    "IndexManager",
    "KB",
    "KBRepository",
    "Link",
    "Repo",
    "Source",
    "Tag",
    "User",
    "WorkspaceRepo",
]


_LAZY_MODULES = {
    "PyriteDB": ".database",
    "DocumentManager": ".document_manager",
    "IndexManager": ".index",
    "KBRepository": ".repository",
}


def __getattr__(name: str) -> Any:
    """Keep pure storage helpers cheap while retaining the public classes."""
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_LAZY_MODULES.get(name, ".models"), __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

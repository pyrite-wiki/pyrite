"""
Entry CRUD operations.

Mixin class for insert, update, delete, and get operations on entries.
Delegates to the SearchBackend instance at ``self._backend``.
"""

from datetime import date, datetime
from typing import Any


def temporal_text(value: Any) -> Any:
    """A date or datetime as the one text a column stores; anything else as is.

    ``2025-06-01`` for a date and ``2025-06-01 00:00:00+00:00`` for a
    datetime: the text sqlite3's own adapter wrote for a plain ``datetime``
    (``isoformat`` with a space). Spelled out rather than ``str()``, because
    ruamel's ``TimeStamp`` (a ``datetime`` subclass, what the loader gives a
    bare YAML timestamp) keeps the file's ``T`` in its ``str()``, and the row
    holds one form whatever the file's spelling. The read of a file keeps the
    loader's spelling (``index._as_text``): the row is the index's, the
    spelling the file's.
    """
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def bindable(value: Any) -> Any:
    """``value`` with every date or datetime, at any depth, as its column text.

    The one place a row's values are made bindable (#951): a bare YAML
    timestamp loads as ruamel's ``TimeStamp``, which sqlite3's adapters do not
    match (they match the exact type, and ``TimeStamp`` subclasses
    ``datetime``), so an entry carrying one in its ``date``, a source's
    ``date`` or a tag could not be bound and was never indexed, with a log
    line as the only signal. Walked here, before the backend sees the row,
    rather than column by column. ``metadata`` is left to the backend's JSON
    encoder, which already spells a date (``SafeEncoder``).
    """
    if isinstance(value, dict):
        return {k: (v if k == "metadata" else bindable(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [bindable(v) for v in value]
    return temporal_text(value)


class CRUDMixin:
    """Entry create, read, update, delete operations — delegates to backend."""

    def upsert_entry(self, entry_data: dict[str, Any]) -> None:
        """Insert or update an entry. Extension fields go into metadata JSON.

        Every date or datetime in the row is bound as text (``bindable``), so
        a YAML timestamp anywhere a frontmatter value reaches a column is
        indexable (#951)."""
        self._backend.upsert_entry(bindable(entry_data))

    def delete_entry(self, entry_id: str, kb_name: str) -> bool:
        """Delete an entry. Returns True if deleted."""
        return self._backend.delete_entry(entry_id, kb_name)

    def get_entry(self, entry_id: str, kb_name: str) -> dict[str, Any] | None:
        """Get a single entry with all metadata."""
        return self._backend.get_entry(entry_id, kb_name)

    def get_entries(self, ids: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """Batch-get multiple entries by (entry_id, kb_name) pairs."""
        return self._backend.get_entries(ids)

    # Allowed sort columns to prevent SQL injection
    _SORT_COLUMNS = {"title", "updated_at", "created_at", "entry_type"}

    def list_entries(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
        entry_type: str | None = None,
        tag: str | None = None,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
        limit: int = 50,
        offset: int = 0,
        include_archived: bool = False,
        status: str | None = None,
        min_importance: int | None = None,
    ) -> list[dict[str, Any]]:
        """List entries with pagination, optionally filtered by KB, type, tag, status, importance."""
        return self._backend.list_entries(
            kb_names=kb_names,
            kb_name=kb_name,
            entry_type=entry_type,
            tag=tag,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
            include_archived=include_archived,
            status=status,
            min_importance=min_importance,
        )

    def count_entries(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
        entry_type: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        min_importance: int | None = None,
    ) -> int:
        """Count entries, optionally filtered by KB, type, tag, status, importance."""
        return self._backend.count_entries(
            kb_names=kb_names,
            kb_name=kb_name,
            entry_type=entry_type,
            tag=tag,
            status=status,
            min_importance=min_importance,
        )

    def get_distinct_types(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[str]:
        """Get distinct entry types, optionally filtered by KB or scoped to kb_names."""
        return self._backend.get_distinct_types(kb_name, kb_names)

    def get_entries_for_indexing(self, kb_name: str) -> list[dict[str, Any]]:
        """Get entry id, file_path, indexed_at for incremental indexing."""
        return self._backend.get_entries_for_indexing(kb_name)

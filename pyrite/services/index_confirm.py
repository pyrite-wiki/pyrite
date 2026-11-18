"""What ``index sync --confirm`` reports for each named entry (#1691).

One place decides it; the CLI (``pyrite index sync --confirm``), MCP
(``kb_index_sync`` with ``confirm``) and REST (``POST /api/index/sync`` with
``confirm``) run the sync, hand its result here, and translate what comes
back. Writes nothing.

Each name, an id or a KB-relative path (principle 5: a file with no ``id:``
can be confirmed by its path), gets one row:

- ``status``: what the files and the rows say (``IndexManager.confirm_rows``), with
  one more check: an entry whose row matches its file is ``indexed`` only if a
  keyword search for it returns it; otherwise it is ``not_indexed``. The
  keyword row is written by a trigger on the entry row (SQLite), so the two
  should agree; the lookup runs anyway, for every backend.
- ``searchable``: whether that keyword lookup returned it (None when there is
  no row to look for).
- ``findings``: the schema findings ``qa validate`` reports for the entry
  (``QAService.validate_entry``, the validator ``qa validate`` and ``index
  health`` share), restricted to the schema rules. No third validator.
- ``embedding``: ``embedded``, ``queued`` (owed: not embedded yet, or changed
  since; ADR-0035, never waited on) or ``unavailable`` (no embedding
  provider here). None for a name with no row.
- ``confirmed``: ``indexed``, and, for a NAMED entry, no schema finding of
  severity error. With no names (every entry the sync added or changed) the
  findings are reported only.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..exceptions import KBNotFoundError, PyriteError

if TYPE_CHECKING:
    from ..storage.index import IndexManager

logger = logging.getLogger(__name__)

#: Every status a confirm row can carry.
STATUSES = ("indexed", "not_indexed", "not_found", "malformed", "duplicate", "skipped")

#: The ``qa validate`` rules that are schema findings: the KB schema's own
#: check, and frontmatter Pyrite cannot model (the walk ``index health`` and
#: ``qa validate`` share, ``storage.index.unmodellable_files``).
SCHEMA_RULES = frozenset({"schema_violation", "frontmatter_unmodellable"})

#: The fields of a ``qa validate`` issue a confirm row carries.
_FINDING_KEYS = ("rule", "severity", "field", "message")

#: How many keyword results the lookup reads, page by page, before it says
#: the entry was not returned. An id is a rare phrase; the bound keeps a
#: common one from reading the whole KB.
_PAGE, _PAGES = 100, 10

#: The ``reason`` of an entry the keyword lookup did not return. It names
#: the bound: past it, a real entry with a common id and a common title in a
#: large KB reads ``not_indexed`` (exit 3: the confirm fails safe, never
#: claiming an entry it did not see).
_NOT_RETURNED = (
    f"a keyword search for its id or its title did not return it "
    f"in the first {_PAGE * _PAGES} results"
)


def confirm_entries(
    index_mgr: IndexManager,
    kb_name: str | None,
    names: list[str] | None,
    sync_result: dict[str, Any],
) -> dict[str, Any]:
    """The confirm block for one KB after a sync.

    ``names`` are the ids or KB-relative paths the caller asked about, as
    the surface received them: this function, not the surface, decides what
    they mean. A blank name names nothing; no name left (an empty list, or
    only blanks) confirms every entry ``sync_result`` added or updated in that
    KB. Returns ``kb``, ``named`` (whether the caller named the entries),
    ``entries`` (one row per name, in order), the ``confirmed`` and
    ``not_confirmed`` counts, and ``outcome`` (``done`` when every row is
    confirmed, else ``partial``) for a caller that sees no exit code.

    Raises ``KBNotFoundError`` when ``kb_name`` is not a KB this process has
    loaded: the sync read the same configuration, so it synced nothing there,
    and nothing confirmed must not read as ``done``. (MCP and REST resolve a
    KB name through the registry as well; the CLI merges the registry first.)
    """
    config, db = index_mgr.config, index_mgr.db
    kb = config.get_kb(kb_name) if kb_name else None
    if kb is None:
        raise KBNotFoundError(
            f"KB '{kb_name}' is not loaded here, so nothing was synced or confirmed in it",
            suggestion="Reload the configuration (restart the server), then sync again.",
        )
    asked = [name for name in names or [] if name.strip()]
    named = bool(asked)
    if named:
        targets = list(dict.fromkeys(asked))
    else:
        targets = list(
            dict.fromkeys(
                row["id"]
                for row in sync_result.get("changed", [])
                if row["kb"] == kb_name and row["change"] != "removed"
            )
        )
    rows = index_mgr.confirm_rows(kb, targets) if targets else []

    from .search_service import SearchService

    search = SearchService(db, config.settings)
    embedding = _EmbeddingStates(db, kb_name, config.settings.embedding_model)
    entries = []
    for row in rows:
        entries.append(_confirm_row(row, kb_name, named, db, config, search, embedding))
    confirmed = sum(1 for r in entries if r["confirmed"])
    return {
        "kb": kb_name,
        "named": named,
        "entries": entries,
        "confirmed": confirmed,
        "not_confirmed": len(entries) - confirmed,
        "outcome": "done" if confirmed == len(entries) else "partial",
    }


def _confirm_row(row, kb_name, named, db, config, search, embedding) -> dict[str, Any]:
    status = row["status"]
    entry_id = row.get("id")
    reason = row.get("reason")
    searchable = None
    findings: list[dict[str, Any]] = []
    embedded = None
    stored = db.get_entry(entry_id, kb_name) if status in ("indexed", "not_indexed") else None
    if stored is not None:
        searchable = _keyword_returns(search, kb_name, entry_id, stored.get("title"))
        if status == "indexed" and not searchable:
            status, reason = "not_indexed", _NOT_RETURNED
        findings = _schema_findings(db, config, kb_name, entry_id)
        embedded = embedding.state(entry_id)
    fails = named and any(f["severity"] == "error" for f in findings)
    out: dict[str, Any] = {
        "name": row["name"],
        "id": entry_id,
        "path": row.get("path"),
        "status": status,
        "reason": reason,
    }
    if "paths" in row:
        out["paths"] = row["paths"]
    out.update(
        {
            "searchable": searchable,
            "findings": findings,
            "embedding": embedded,
            "confirmed": status == "indexed" and not fails,
        }
    )
    return out


def _keyword_returns(search, kb_name: str, entry_id: str, title: Any) -> bool:
    """Does a keyword search return the entry? Its id first (the SQLite
    keyword index holds the id), then its title (every backend's does)."""
    for query in (entry_id, title if isinstance(title, str) else None):
        if not query or not query.strip():
            continue
        for page in range(_PAGES):
            try:
                found = search.search(
                    query,
                    kb_name=kb_name,
                    mode="keyword",
                    limit=_PAGE,
                    offset=page * _PAGE,
                    include_archived=True,
                )
            except PyriteError:
                logger.debug("Keyword lookup for %s failed", entry_id, exc_info=True)
                break
            if any(r.get("id") == entry_id for r in found):
                return True
            if len(found) < _PAGE:
                break
    return False


def _schema_findings(db, config, kb_name: str, entry_id: str) -> list[dict[str, Any]]:
    from .access_policy import UNSCOPED
    from .qa_service import QAService

    try:
        issues = QAService(config, db).validate_entry(entry_id, kb_name, readable_kbs=UNSCOPED)
    except PyriteError:
        logger.debug("Validation of %s failed", entry_id, exc_info=True)
        return []
    return [
        {key: issue.get(key) for key in _FINDING_KEYS}
        for issue in issues.get("issues", [])
        if issue.get("rule") in SCHEMA_RULES
    ]


class _EmbeddingStates:
    """``embedded``, ``queued`` or ``unavailable`` for an entry of one KB,
    read once for the KB, never waited on (ADR-0035)."""

    def __init__(self, db, kb_name: str | None, model_name: str | None):
        self._db = db
        self._model_name = model_name
        self._kb_name = kb_name
        self._loaded = False
        self._unavailable = False
        self._embedded: set[str] = set()
        self._queued: set[str] = set()

    def _load(self) -> None:
        self._loaded = True
        if self._kb_name is None:
            return
        from .embedding_service import semantic_unavailable_for

        if semantic_unavailable_for(self._db) is not None:
            self._unavailable = True
            return
        backend = self._db.backend
        try:
            rowids = backend.get_embedded_rowids()
            self._embedded = {
                r["id"]
                for r in backend.get_entries_for_embedding(self._kb_name)
                if r.get("rowid") in rowids
            }
        except Exception:
            logger.debug("Could not read the embedded entries", exc_info=True)
        from .embedding_worker import EmbeddingWorker

        try:
            self._queued = EmbeddingWorker(self._db, model_name=self._model_name).queued_ids(
                self._kb_name
            )
        except Exception:
            logger.debug("Could not read the embed queue", exc_info=True)

    def state(self, entry_id: str) -> str:
        if not self._loaded:
            self._load()
        if self._unavailable:
            return "unavailable"
        if entry_id in self._embedded and entry_id not in self._queued:
            return "embedded"
        return "queued"

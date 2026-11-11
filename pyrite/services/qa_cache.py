"""A short-lived cache for the QA reports a read-tier route or tool serves.

``/api/qa/validate``, ``/api/qa/status`` and the MCP tools ``kb_qa_validate`` and
``kb_qa_status`` check every entry of a KB. A report is reused until the
*index* of the KBs it covers changes (an entry written, removed or re-indexed)
or ``QA_CACHE_SECONDS`` pass, whichever is first: a write through Pyrite is
visible at once, a hand edit of a file within the minute. A report is keyed by
the readable set it was built for, because the all-KBs form is an inventory of
what the caller can read. Concurrent callers share one pass.
"""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

#: How long a report is reused if the index does not change (seconds).
QA_CACHE_SECONDS = 60.0
_MAX_ENTRIES = 128

_CACHE: dict[tuple, tuple[float, tuple, Any]] = {}
_LOCKS: dict[tuple, threading.Lock] = {}
_GUARD = threading.Lock()


def _lock(key: tuple) -> threading.Lock:
    with _GUARD:
        if len(_LOCKS) >= _MAX_ENTRIES and key not in _LOCKS:
            _LOCKS.clear()
        return _LOCKS.setdefault(key, threading.Lock())


def index_fingerprint(db, kb_names: Iterable[str] | None) -> tuple:
    """What the index holds for the KBs: per KB its entry count and newest
    ``indexed_at``. One grouped query over the index, not a read of any file."""
    rows = db.execute_sql(
        "SELECT kb_name, COUNT(*) AS n, MAX(indexed_at) AS newest FROM entry GROUP BY kb_name"
    )
    wanted = None if kb_names is None else set(kb_names)
    return tuple(
        sorted(
            (r["kb_name"], r["n"], str(r["newest"]))
            for r in rows
            if wanted is None or r["kb_name"] in wanted
        )
    )


def cached_report(
    qa,
    name: str,
    kb_names: Iterable[str] | None,
    readable: Iterable[str] | None,
    compute: Callable[[], Any],
    *,
    ttl: float = QA_CACHE_SECONDS,
) -> Any:
    """``compute()``'s report, reused while the index of ``kb_names`` is
    unchanged and younger than ``ttl``. ``name`` says which report (it and the
    two sets key the cache); ``None`` for a set means unscoped."""
    key = (
        str(qa.db.db_path),
        name,
        None if kb_names is None else tuple(sorted(kb_names)),
        None if readable is None else tuple(sorted(readable)),
    )
    with _lock(key):
        fingerprint = index_fingerprint(qa.db, kb_names)
        hit = _CACHE.get(key)
        if hit is not None and hit[1] == fingerprint and time.monotonic() - hit[0] < ttl:
            return copy.deepcopy(hit[2])
        report = compute()
        if len(_CACHE) >= _MAX_ENTRIES:
            _CACHE.clear()
        _CACHE[key] = (time.monotonic(), fingerprint, report)
        return copy.deepcopy(report)

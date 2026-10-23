"""
Index Manager - Syncs File Storage with SQLite Index

Handles indexing entries from file-based KBs into the SQLite FTS database.
Supports incremental updates based on file modification times.
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
import stat as stat_module
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import exc as sa_exc

from ..config import KBConfig, PyriteConfig, load_config
from ..exceptions import StorageError
from ..models import Entry
from ..models.core_types import as_stored_text, id_text
from ..models.protocols import (
    PROTOCOL_COLUMN_KEYS,
    Assignable,
    Locatable,
    Prioritizable,
    Statusable,
    Temporal,
)
from ..schema.field_aliases import lift_declared_fields, read_metadata, read_view
from ..utils.metadata import parse_metadata
from .database import PyriteDB
from .errors import DISK_SUGGESTION, driver_error, is_disk_fault, storage_error_from
from .repository import KBRepository

logger = logging.getLogger(__name__)

# Errors from writing a row that are the database's, not the file's (#1260):
# ``OperationalError`` is SQLite's class for a missing column or table, a
# read-only or full file, and a lock; a ``StorageError`` is one already
# translated (corruption, busy). Data a file supplies that SQLite cannot store
# raises another class and stays a per-file problem.
_DATABASE_FAULTS = (sqlite3.OperationalError, sa_exc.OperationalError, StorageError)

WRITE_RETRY_SUGGESTION = (
    "Run the command again: opening the index repairs its schema. If the same row "
    "fails again, the log names the error and `pyrite index health` shows the "
    "index's state."
)


def _storage_error_for_write(exc: BaseException, rel: str, db: PyriteDB) -> StorageError:
    """The error ``reconcile_kb`` raises when the database refuses a row.

    Classified by ``storage_error_from``: a lock is retryable and says to wait;
    a disk fault says so; anything else (a column lost after the open) says to
    run the command again, since the next open repairs the schema. None of
    them is told to rebuild: the file is intact.
    """
    try:
        db.session.rollback()
    except Exception:  # pragma: no cover - defensive: the session may be gone
        logger.debug("Rollback after a failed index write failed", exc_info=True)
    if isinstance(exc, StorageError):
        err = exc
    else:
        # The first line only: a driver error carries the SQL and every bound value.
        err = storage_error_from(driver_error(exc), f"The index refused the row for {rel}")
        err.args = ((str(err).splitlines() or [type(exc).__name__])[0],)
    if not err.suggestion:
        err.suggestion = DISK_SUGGESTION if is_disk_fault(exc) else WRITE_RETRY_SUGGESTION
    return err


# Matches wikilinks: [[target]], [[kb:target]], [[target#heading]], [[target^block-id]], [[target|display]]
# Groups: (1) kb prefix, (2) target, (3) heading, (4) block-id, (5) display text
_WIKILINK_RE = re.compile(
    r"\[\[(?:([a-z0-9-]+):)?([^\]|#^]+?)(?:#([^\]|^]+?))?(?:\^([^\]|]+?))?(?:\|([^\]]+?))?\]\]"
)

# Matches transclusions: ![[target]], ![[target#heading]], ![[target^block-id]]
# Same groups as _WIKILINK_RE: (1) kb prefix, (2) target, (3) heading, (4) block-id, (5) display text
_TRANSCLUSION_RE = re.compile(
    r"!\[\[(?:([a-z0-9-]+):)?([^\]|#^]+?)(?:#([^\]|^]+?))?(?:\^([^\]|]+?))?(?:\|([^\]]+?))?\]\]"
)

# Matches fenced code blocks (``` ... ```, ~~~ ... ~~~) including the fence lines.
# Non-greedy so adjacent blocks don't collapse. DOTALL so . matches newlines.
_FENCED_CODE_RE = re.compile(r"^(```|~~~).*?^\1\s*$", re.MULTILINE | re.DOTALL)

# Matches inline code spans: `...` or ``...`` (spans that could contain a literal backtick).
# Matches the shorter form only on a single line to avoid eating multi-paragraph text.
_INLINE_CODE_RE = re.compile(r"``[^`\n]+?``|`[^`\n]+?`")


def _strip_code_regions(text: str) -> str:
    """Blank out fenced code blocks and inline code spans before link extraction.

    Wikilink/transclusion syntax appearing inside code is documentation (e.g.
    "write `[[entry-id]]` to link"), not a real reference. Stripping code
    regions before regex scans avoids false-positive broken-link reports.
    Replaces matches with same-length whitespace so any future position-based
    logic stays aligned.
    """

    def _blank(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    stripped = _FENCED_CODE_RE.sub(_blank, text)
    stripped = _INLINE_CODE_RE.sub(_blank, stripped)
    return stripped


def content_hash(raw: bytes) -> str:
    """THE ``content_hash``: SHA-256 of a file's raw bytes (ADR-0042 decision 9).

    The index records it per row; a single-entry read returns it for the bytes
    it read. One definition, so the two can be compared.
    """
    return hashlib.sha256(raw).hexdigest()


def _hash_file(file_path: Path) -> str | None:
    """``content_hash`` of a file, or None if it can't be read.

    Used for content-based staleness detection: mtime alone misses
    same-second edits and coarse-resolution filesystems.
    """
    try:
        return content_hash(file_path.read_bytes())
    except OSError:
        return None


#: The entry columns of a read, in the order the backend's row puts them
#: (``base_backend._entry_to_dict``) between ``file_path`` and ``metadata``.
_READ_COLUMNS = (
    "date",
    "importance",
    "status",
    "location",
    "lifecycle",
    "assignee",
    "assigned_at",
    "priority",
    "due_date",
    "start_date",
    "end_date",
    "coordinates",
)

#: The row's one-value columns, as the backend binds them
#: (``base_backend._upsert_entry_main``): what ``row_for`` checks holds a
#: scalar before a write is published (#955).
_SCALAR_COLUMNS = (
    "id",
    "kb_name",
    "entry_type",
    "title",
    "body",
    "summary",
    "file_path",
    "content_hash",
    "date",
    "importance",
    "status",
    "location",
    "assignee",
    "assigned_at",
    "priority",
    "due_date",
    "start_date",
    "end_date",
    "coordinates",
    "fips",
    "state",
    "lifecycle",
    "created_at",
    "updated_at",
)

#: What a read keeps from the index row whatever the file says: the index's
#: own bookkeeping and the derived link list (decision 9: derived values may
#: lag, like search; outlinks and backlinks come from the index too).
_ROW_ONLY_KEYS = ("indexed_at", "created_by", "modified_by")


def _as_text(value: Any) -> Any:
    """``value`` as a TEXT column returns it once SQLite has stored it: a YAML
    number or date as its text, a bool as ``"1"``/``"0"`` (sqlite3 binds a
    bool as an integer). A date or datetime is its ``str()``: for ruamel's
    ``TimeStamp`` that keeps the file's separator (``2026-01-01T10:00:00``),
    so a read of the entry's own columns reports the file's spelling as far
    as the loader keeps it (a ``Z`` reads back ``+00:00``). The index row
    holds one form instead (``crud.temporal_text``, ``isoformat()``, the
    same text for a ``T``-spelled file); the file is the truth for a read
    (#951). A tag and a source's date are ``_one_text`` instead."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(int(value))
    return str(value)


def _read_value_is_the_bags(key: str, read_value: Any, bag_value: Any) -> bool:
    """Whether a read's value for ``key`` is the bag's value, spelled as the
    read spells it: a TEXT column holds the bag's ``2`` as ``"2"`` and its
    ``true`` as ``"1"`` (``_as_column``); a timestamp the model parsed is the
    bag's instant whatever the file's separator or zone spelling; anything
    else (a value lifted from the bag itself) compares as JSON text. What
    ``read_metadata`` asks to decide whether a bag key leaves the read's
    ``metadata`` (#1025): a value the read does not carry stays."""
    if key in _READ_COLUMNS:
        return bool(_as_column(key, bag_value) == read_value)
    if key in ("created_at", "updated_at"):
        from ..models.base import _timestamp_instant

        a, b = _timestamp_instant(read_value), _timestamp_instant(bag_value)
        if a is not None and b is not None:
            return a == b
    from ..utils.json_utils import SafeEncoder

    return json.dumps(read_value, sort_keys=True, cls=SafeEncoder) == json.dumps(
        bag_value, sort_keys=True, cls=SafeEncoder
    )


def _one_text(value: Any) -> Any:
    """A tag or a source's date as the index stores and returns it: a date or
    timestamp as ``isoformat()`` (``crud.temporal_text``), the one text every
    row ever held, whatever the file's spelling; any other value as
    ``_as_text``. A tag is a name the index matches by text, so one spelling
    whatever release built the row: a tag spelled ``2026-01-01 10:00:00``
    and one spelled ``2026-01-01T10:00:00`` are one tag, and an index built
    before this fix and one built after agree (#987)."""
    from .crud import temporal_text

    return _as_text(temporal_text(value))


def _as_column(key: str, value: Any) -> Any:
    """``value`` as the index column ``key`` returns it.

    ``importance`` is an INTEGER column and the model has already made it an
    int; the rest of ``_READ_COLUMNS`` are TEXT (``storage/models.py``), so
    ``priority: 2.5`` reads back as ``"2.5"`` and ``status: true`` as ``"1"``.
    The pin test in ``tests/test_get_reads_the_file.py`` compares unquoted
    dates, bools, floats and nulls with a real row.
    """
    return value if key == "importance" else _as_text(value)


def _read_sources(
    sources: list[dict[str, Any]], entry_id: str, kb_name: str, row: dict[str, Any]
) -> list[dict[str, Any]]:
    """The file's sources in the shape a row returns them (``_get_entry_sources``).

    ``id`` is the index's row number for a source; the file has none. It is
    kept from the row where the row's source at the same position says the
    same thing, so an unchanged source reads exactly as before; otherwise None.
    A source's ``date`` is the index's one text for it (``_one_text``,
    ``isoformat()`` for a timestamp whatever the file's spelling, #987), as
    the row's TEXT column holds it, so the comparison with the row holds for
    a space-spelled date too.
    """
    indexed = row.get("sources") or []
    out = []
    for i, src in enumerate(sources):
        shaped = {
            "id": None,
            "entry_id": entry_id,
            "kb_name": kb_name,
            "title": src.get("title", ""),
            "url": src.get("url", ""),
            "outlet": src.get("outlet", ""),
            "date": _one_text(src.get("date", "")),
            "verified": 1 if src.get("verified") else 0,
        }
        if i < len(indexed) and {**indexed[i], "id": None} == shaped:
            shaped["id"] = indexed[i].get("id")
        out.append(shaped)
    return out


def _is_valid_link_target(target: str) -> bool:
    """Reject targets that can't be valid entry IDs.

    Entry IDs are path-safe slugs — no slashes, whitespace, or backticks.
    This filters extractor false positives like `[[jan6/witnesses]]` where
    the `/` makes it structurally impossible to resolve to an entry.
    """
    if not target:
        return False
    return not any(ch in target for ch in "/\\`\n\r\t")


def _parse_indexed_at(indexed_at: str) -> datetime:
    """Parse indexed_at timestamp string to a UTC-aware datetime.

    SQLite CURRENT_TIMESTAMP produces UTC strings like '2026-02-25 12:00:00'.
    Some values may have 'Z' or '+00:00' suffix. We normalize all to UTC-aware.

    Legacy rows may store the literal string 'CURRENT_TIMESTAMP' — an
    unresolved SQLAlchemy server_default from before the explicit-timestamp
    fix in base_backend. Treat these as the Unix epoch so `check_staleness`
    sees them as maximally out-of-date. (A prior version of this function
    returned `datetime.now(UTC)` here, which had the opposite effect.) The
    reconcile no longer reads `indexed_at`: it compares the recorded file
    stat (`_file_changed`), so such a row is re-read once and re-recorded.
    """
    if indexed_at == "CURRENT_TIMESTAMP":
        return datetime.fromtimestamp(0, tz=UTC)
    cleaned = indexed_at.replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        # Naive datetime from SQLite — it's UTC
        dt = dt.replace(tzinfo=UTC)
    return dt


def _same_entry_history(
    entry_id: str,
    current_rel_path: str,
    log_entries: list[dict],
    kb_path: Path,
    git_service: Any,
) -> list[dict]:
    """The prefix of `log_entries` (newest first) that belongs to `entry_id`.

    Two ways the log reaches another entry's commits (#432):

    - A path is reused. An entry is deleted and a different one is later
      created at the same path; the log of the path runs through both. The
      history stops at the newest commit that added the file (status "A"):
      what is older belongs to the path's previous life.
    - A rename is inferred. `git log --follow` crosses a rename whenever git
      finds the two files similar enough, and entries share frontmatter
      boilerplate, so a deleted entry and an unrelated added one can look
      like a rename. At each point where the path changes, the older path
      must hold the same entry id at that commit (ids are unique within a
      KB); the history stops at the first that does not, or cannot be read.
    """
    from ..models.core_types import entry_id_from_markdown

    newer_path = current_rel_path
    for i, log_entry in enumerate(log_entries):
        path = log_entry.get("file_path", newer_path)
        if path != newer_path:
            text = git_service.read_file_at(kb_path, log_entry["hash"], path)
            if text is None or entry_id_from_markdown(text) != entry_id:
                return log_entries[:i]
        if str(log_entry.get("status", "")).startswith("A"):
            return log_entries[: i + 1]
        newer_path = path
    return log_entries


# ---------------------------------------------------------------------------
# One reconcile (ADR-0038 step 2)
# ---------------------------------------------------------------------------


@dataclass
class _Claim:
    """One parseable file and the id it holds."""

    rel: str  # KB-relative path, POSIX spelling: the duplicate tiebreaker
    path: Path
    entry_id: str
    stat: os.stat_result
    entry: Entry | None = None  # None: a known file whose stat matched, not read


@dataclass
class ReconcilePlan:
    """What the files say, before anything is written.

    ``winners`` maps each id held by a parseable file to the file that wins
    it: the lexicographically first KB-relative path (maintainer, 2026-09-26).
    ``duplicates`` lists every id held by more than one file. Building a plan
    writes nothing, so a caller that only needs to know who holds an id
    (``DocumentManager.delete_entry``, ``check_health``) asks for one.
    """

    kb_name: str
    winners: dict[str, _Claim] = field(default_factory=dict)
    duplicates: list[dict[str, Any]] = field(default_factory=list)
    malformed: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    holders: dict[str, list[_Claim]] = field(default_factory=dict)
    # path -> "ExcType: detail" for the ``malformed`` files whose bytes could not
    # be read or decoded (OSError, UnicodeError), whatever the file's kind:
    # health reports these as ``unreadable_files`` (#593). Content that reads
    # but does not parse stays only in ``malformed``.
    unreadable: dict[str, str] = field(default_factory=dict)
    walked: int = 0


def _file_changed(row: dict[str, Any], stat: os.stat_result) -> bool:
    """THE staleness rule: a known file is re-read when its mtime OR its size
    differs from what was recorded when it was indexed (ADR-0038 decision 4).

    "Differs", not "is newer": a restore from a backup carries an older mtime.
    A row indexed before the stat was recorded (v27) has none and is re-read
    once; the content hash then decides whether it changed, so that first
    reconcile after an upgrade reports nothing for an unchanged file.
    """
    return row.get("file_mtime_ns") != stat.st_mtime_ns or row.get("file_size") != stat.st_size


def unmodellable_files(kb: KBConfig, paths: Iterable[Path] | None = None) -> list[dict[str, Any]]:
    """Every file of ``kb`` whose frontmatter Pyrite cannot model (or of
    ``paths``, the files a caller already narrowed it to, in that order),
    with the 1-based line of the key and ``apply()``'s reason (#760 (b)):
    ``{kb, id, path, line, reason}``.

    ``index health`` and ``qa validate`` share this one walk, so the two
    reports name the same files for the same reason. A file that does not
    parse is not here (it is malformed; the reader's row). What the
    reconcile reads around, this reads around too: a file that is not a
    regular file (a FIFO would block the read), that cannot be read or is
    not UTF-8, or that the check itself cannot handle, is skipped with a log
    line, never a crash of the health or validate surface (#972 round 1).

    ``id`` is the reader's id for the file (``KBRepository.read_id``: an
    explicit ``id:``, else the one derived from the title), or the file's
    stem when the file cannot be read at all (a duplicate key).
    """
    from .file_operations import unmodellable_in_text

    repo = KBRepository(kb)
    found: list[dict[str, Any]] = []
    for path in sorted(repo.list_files()) if paths is None else paths:
        try:
            if not stat_module.S_ISREG(path.stat().st_mode):
                continue
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            why = unmodellable_in_text(text)
        except Exception as e:
            logger.warning("Could not check the frontmatter of %s: %s", path, e)
            continue
        if why is None:
            continue
        reason, line = why
        entry_id = repo.read_id(path)[0] or path.stem
        found.append(
            {"kb": kb.name, "id": entry_id, "path": str(path), "line": line, "reason": reason}
        )
    return found


class IndexManager:
    """
    Manages the SQLite FTS index for all KBs.

    Responsibilities:
    - Full reindexing of KBs
    - Incremental updates based on file changes
    - Index statistics and health checks
    """

    def __init__(self, db: PyriteDB, config: PyriteConfig | None = None):
        self.db = db
        self.config = config or load_config()

    def _link_kb(self, prefix: str | None, kb_name: str) -> str:
        """The KB a link's ``prefix:`` names, as the index stores it
        (``PyriteConfig.link_kb``, the rule the write path's duplicate check
        uses too). ADR-0049 / #952: the index stores the full name, so
        backlinks, the graph and the broken-link count agree with
        `resolve_entry` (#979)."""
        return self.config.link_kb(prefix, kb_name)

    def _frontmatter_link(self, link: dict[str, Any], kb_name: str) -> dict[str, Any]:
        """A frontmatter ``links:`` item as the index stores it: its ``kb`` is
        a KB field in link position (a name or a shortname, like a ``[[kb:id]]``
        prefix), so it resolves the same way and the written value is kept
        beside it (#980 review item 2).

        Its ``target`` is the text the index stores an entry id as
        (``as_stored_text``), converted here, before ``crud.bindable`` spells
        a YAML timestamp as ``isoformat()``: ``target: 2024-01-01 10:00:00``
        then names the entry ``id: 2024-01-01 10:00:00`` is stored as, rather
        than a ``T``-spelled id no entry has (#1022)."""
        link = {**link, "target": as_stored_text(link.get("target"))}
        written = link.get("kb")
        if not written:
            return link
        return {**link, "kb": self._link_kb(written, kb_name), "prefix": written}

    def _entry_to_dict(
        self,
        entry: Entry,
        kb_name: str,
        file_path: Path,
        *,
        stat: os.stat_result | None = None,
        content_hash: str | None = None,
    ) -> dict[str, Any]:
        """Convert an Entry to a dict for database storage.

        ``stat`` and ``content_hash`` are the reconcile's own reading of the
        file, taken before it parsed it; without them they are read here. The
        stat is what the next reconcile compares (``_file_changed``).
        """
        if stat is None:
            try:
                stat = file_path.stat()
            except OSError:
                stat = None
        data: dict[str, Any] = {
            "id": id_text(entry.id),
            "kb_name": kb_name,
            "entry_type": entry.entry_type,
            "title": entry.title,
            "body": entry.body,
            "summary": entry.summary,
            "file_path": str(file_path),
            "content_hash": content_hash or _hash_file(file_path),
            "file_mtime_ns": stat.st_mtime_ns if stat else None,
            "file_size": stat.st_size if stat else None,
            # A tag is a name: bound as the index's one text for it
            # (`_one_text`: a loaded timestamp as `isoformat()`, #987), the
            # text a read returns, so a search by the tag `get` returned
            # finds the entry (#951).
            "tags": [_one_text(t) for t in entry.tags],
            "aliases": entry.aliases,
            "sources": [s.to_dict() for s in entry.sources],
            "links": [self._frontmatter_link(l.to_dict(), kb_name) for l in entry.links],
            "lifecycle": getattr(entry, "lifecycle", "active"),
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
            "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
        }

        # Protocol-based field extraction (ADR-0017)
        # Temporal protocol: date, start_date, end_date, due_date
        if isinstance(entry, Temporal):
            data["date"] = entry.date
            data["start_date"] = entry.start_date
            data["end_date"] = entry.end_date
            data["due_date"] = entry.due_date
        elif hasattr(entry, "date"):
            data["date"] = entry.date

        # Locatable protocol: location, coordinates
        if isinstance(entry, Locatable):
            location = entry.location
            if isinstance(location, list):
                location = ", ".join(str(loc) for loc in location)
            data["location"] = location
            data["coordinates"] = entry.coordinates
        elif hasattr(entry, "location"):
            location = getattr(entry, "location", "")
            if isinstance(location, list):
                location = ", ".join(str(loc) for loc in location)
            data["location"] = location

        # Statusable protocol: status
        if isinstance(entry, Statusable):
            status = entry.status
            if hasattr(status, "value"):
                status = status.value
            elif isinstance(status, list):
                status = status[0] if status else None
            data["status"] = status
        elif hasattr(entry, "status"):
            status = getattr(entry, "status", "")
            if hasattr(status, "value"):
                status = status.value
            data["status"] = status

        # Assignable protocol: assignee, assigned_at
        if isinstance(entry, Assignable):
            data["assignee"] = entry.assignee
            data["assigned_at"] = entry.assigned_at
        elif hasattr(entry, "assignee"):
            data["assignee"] = getattr(entry, "assignee", "")

        # Prioritizable protocol: priority
        if isinstance(entry, Prioritizable):
            data["priority"] = entry.priority
        elif hasattr(entry, "priority"):
            data["priority"] = getattr(entry, "priority", "")

        # importance (promoted to base Entry)
        data["importance"] = entry.importance

        # For GenericEntry types: promote protocol fields from metadata to DB columns
        # This handles kb.yaml types that declare protocols: [temporal, assignable, ...]
        if hasattr(entry, "metadata") and entry.metadata:
            for key in PROTOCOL_COLUMN_KEYS:
                if key not in data and key in entry.metadata:
                    value = entry.metadata[key]
                    if key == "location" and isinstance(value, list):
                        value = ", ".join(str(v) for v in value)
                    data[key] = value

        # Promote fips/state from metadata or entry attributes to DB columns
        # for geographic filtering (cross-KB FIPS queries)
        if hasattr(entry, "metadata") and entry.metadata:
            if "fips" not in data and "fips" in entry.metadata:
                data["fips"] = entry.metadata["fips"]
            if "state" not in data and "state" in entry.metadata:
                data["state"] = entry.metadata["state"]
        if "fips" not in data and hasattr(entry, "fips"):
            data["fips"] = getattr(entry, "fips", "")
        if "state" not in data and hasattr(entry, "state"):
            data["state"] = getattr(entry, "state", "")

        # Store extension-specific fields in metadata column
        # Use to_frontmatter() to capture all fields, then strip keys
        # already stored in their own DB columns

        # Keys that map to actual DB columns (always excluded from metadata).
        # `provenance` has no column: it rides in the bag like every other
        # declared field without one, and a read lifts it to its own name
        # (`lift_declared_fields`, #1025); it used to be dropped here, so no
        # read carried it.
        db_column_keys = {
            "id",
            "type",
            "title",
            "body",
            "summary",
            "tags",
            "sources",
            "links",
            "metadata",
            "created_at",
            "updated_at",
        }
        # Also exclude keys that were explicitly set in data above
        stored_keys = db_column_keys | set(data.keys())
        try:
            # A new entry's declared fields too (#86): the row a create writes
            # is the row a rebuild from its file writes.
            fm = entry.declared_frontmatter()
            # The bag of keys the model does not declare first, the model's
            # own reading of every field over it: `metadata.k` is a second
            # spelling of field `k` (Andon #994, decision A), and the reader
            # must agree with itself, so a file holding `evidence: [real]` and
            # `metadata: {evidence: [fake]}` indexes `[real]`, as the model
            # reads it. The bag used to be merged last and override the model.
            ext_fields: dict[str, Any] = {}
            if hasattr(entry, "metadata") and isinstance(entry.metadata, dict):
                ext_fields.update(entry.metadata)
            ext_fields.update({k: v for k, v in fm.items() if k not in stored_keys})
            if ext_fields:
                data["metadata"] = ext_fields  # stored as dict; upsert_entry JSON-encodes
        except Exception:
            logger.warning(
                "Metadata extraction failed for %s, using fallback", entry.id, exc_info=True
            )
            # Fallback: just store the metadata dict
            if hasattr(entry, "metadata") and entry.metadata:
                data["metadata"] = entry.metadata

        # Extract body wikilinks as links with relation="wikilink"
        body = entry.body or ""
        scannable_body = _strip_code_regions(body) if body else ""
        # Every link the file writes is kept, whatever else links to the same
        # target: the backend's sync key (`_link_rows`) makes one row of two
        # links only when they are one link, the same target, KB, relation
        # and written prefix. Deduplicating here by the bare target dropped
        # `[[kb:x]]` after `[[ka:x]]`, a link to a different entry, and the
        # second prefix of one target, which health then never checked (#1009).
        if scannable_body:
            for match in _WIKILINK_RE.finditer(scannable_body):
                if match.start() and scannable_body[match.start() - 1] == "!":
                    # The `[[x]]` inside `![[x]]`: that link is a
                    # transclusion, extracted below, not a second link.
                    continue
                kb_prefix = match.group(1)  # Optional kb: prefix
                target = match.group(2).strip()
                heading = match.group(3)
                block_id = match.group(4)
                note = ""
                if heading:
                    note = f"#{heading}"
                elif block_id:
                    note = f"^{block_id}"
                if target and _is_valid_link_target(target) and target != entry.id:
                    data["links"].append(
                        {
                            "target": target,
                            "kb": self._link_kb(kb_prefix, kb_name),
                            "prefix": kb_prefix or None,
                            "relation": "wikilink",
                            "note": note,
                        }
                    )

        # Extract references from frontmatter (cross-KB structured links)
        # Format: references: ["kb_name:entry_id", "entry_id", ...]
        refs = getattr(entry, "metadata", {})
        if isinstance(refs, dict):
            refs = refs.get("references", [])
        else:
            refs = []
        fm_refs = getattr(entry, "_raw_frontmatter", {}) or {}
        if not refs and isinstance(fm_refs, dict):
            refs = fm_refs.get("references", [])
        # Also check to_frontmatter output -- the fallback for typed
        # entries (EventEntry, PersonEntry, etc.) whose from_frontmatter
        # doesn't preserve unknown frontmatter keys in .metadata the way
        # GenericEntry does.
        if not refs:
            try:
                fm = entry.to_frontmatter()
                refs = fm.get("references", [])
            except Exception:
                # A crash here silently drops the entry's cross-KB
                # `references` links from indexing with no trace --
                # the recall-bug class (fail-open-exception-sweep site
                # #4). Log it; the entry itself still indexes, just
                # without these links.
                logger.warning(
                    "references extraction failed for %s; cross-KB "
                    "references links will be missing from this entry",
                    entry.id,
                    exc_info=True,
                )
        if isinstance(refs, list):
            for ref in refs:
                ref_str = str(ref).strip()
                if not ref_str:
                    continue
                ref_prefix: str | None = None
                if ":" in ref_str and not ref_str.startswith("http"):
                    ref_prefix, ref_id = ref_str.split(":", 1)
                    ref_kb = self._link_kb(ref_prefix, kb_name)
                else:
                    ref_kb, ref_id = kb_name, ref_str
                if ref_id and ref_id != entry.id:
                    data["links"].append(
                        {
                            "target": ref_id,
                            "kb": ref_kb,
                            "prefix": ref_prefix,
                            "relation": "references",
                            "note": "",
                        }
                    )

        # Extract transclusions as links with relation="transclusion"
        if scannable_body:
            for match in _TRANSCLUSION_RE.finditer(scannable_body):
                kb_prefix = match.group(1)
                target = match.group(2).strip()
                heading = match.group(3)
                block_id = match.group(4)
                note = ""
                if heading:
                    note = f"#{heading}"
                elif block_id:
                    note = f"^{block_id}"
                if target and _is_valid_link_target(target) and target != entry.id:
                    data["links"].append(
                        {
                            "target": target,
                            "kb": self._link_kb(kb_prefix, kb_name),
                            "prefix": kb_prefix or None,
                            "relation": "transclusion",
                            "note": note,
                        }
                    )

        # Extract object-ref fields for entry_ref table
        refs = []
        try:
            kb_config = self.config.get_kb(kb_name)
            if kb_config and kb_config.kb_yaml_path.exists():
                schema = kb_config.kb_schema
                entry_type_name = entry.entry_type
                type_schema = schema.types.get(entry_type_name)
                if type_schema:
                    fm = entry.to_frontmatter()
                    for field_name, field_schema in type_schema.fields.items():
                        if field_schema.field_type == "object-ref":
                            value = fm.get(field_name)
                            if value:
                                target_type = field_schema.constraints.get("target_type")
                                if isinstance(value, list):
                                    for v in value:
                                        if isinstance(v, str):
                                            refs.append(
                                                {
                                                    "target_id": v,
                                                    "field_name": field_name,
                                                    "target_type": target_type,
                                                }
                                            )
                                elif isinstance(value, str):
                                    refs.append(
                                        {
                                            "target_id": value,
                                            "field_name": field_name,
                                            "target_type": target_type,
                                        }
                                    )
        except Exception:
            logger.debug("Schema not available for ref extraction: %s", entry.id)
        if refs:
            data["_refs"] = refs

        # Extract edge endpoints for edge_endpoint table
        edge_endpoints = []
        try:
            kb_config = self.config.get_kb(kb_name)
            if kb_config and kb_config.kb_yaml_path.exists():
                schema = kb_config.kb_schema
                entry_type_name = entry.entry_type
                type_schema = schema.types.get(entry_type_name)
                if type_schema and getattr(type_schema, "edge_type", False):
                    fm = entry.to_frontmatter()
                    for role, endpoint_spec in type_schema.endpoints.items():
                        value = fm.get(endpoint_spec.field)
                        if value and isinstance(value, str):
                            # Strip wikilink brackets if present: [[target]] -> target
                            endpoint_id = value.strip()
                            if endpoint_id.startswith("[[") and endpoint_id.endswith("]]"):
                                endpoint_id = endpoint_id[2:-2].strip()
                            if endpoint_id:
                                edge_endpoints.append(
                                    {
                                        "role": role,
                                        "field_name": endpoint_spec.field,
                                        "endpoint_id": endpoint_id,
                                        "endpoint_kb": kb_name,
                                        "edge_type": entry_type_name,
                                    }
                                )
        except Exception:
            logger.debug("Schema not available for edge endpoint extraction: %s", entry.id)
        if edge_endpoints:
            data["_edge_endpoints"] = edge_endpoints

        # Extract blocks for block-level references
        body = entry.body or ""
        if body:
            from ..utils.markdown_blocks import extract_blocks

            data["_blocks"] = extract_blocks(body)

        return data

    def read_fields(
        self,
        entry: Entry,
        kb_name: str,
        file_path: Path,
        *,
        content_hash: str,
        row: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """A single-entry read of ``entry``, parsed from ``file_path`` just now.

        The same keys, in the same order, with the same values a row indexed
        from this parse would return (``PyriteDB.get_entry``): the entry is
        projected by ``_entry_to_dict`` -- the index's own projection -- and
        then shaped as the backend shapes a row. Every file-held value comes
        from this one parse, so a read never pairs a new body with an old title.
        From ``row`` come only what the index alone knows: ``indexed_at``,
        ``created_by``, ``modified_by``, the derived ``links``, and a
        timestamp the file does not carry (whose value is the time of
        indexing, #670). ``content_hash`` names the bytes the entry was parsed
        from. ``tests/test_get_reads_the_file.py`` pins that on a fresh index
        this equals the row, key for key; the backend's shaping is mirrored,
        not shared, because it reads an ORM row (a finding for #730 P5).
        """
        from ..utils.json_utils import SafeEncoder

        data = self._entry_to_dict(entry, kb_name, file_path, content_hash=content_hash)
        row = row or {}
        # Stored as JSON (upsert_entry), so a read sees JSON's values: a date
        # becomes its ISO string, a tuple a list.
        metadata = json.loads(json.dumps(data.get("metadata") or {}, cls=SafeEncoder))
        result: dict[str, Any] = {
            "id": data["id"],
            "kb_name": kb_name,
            "entry_type": data["entry_type"],
            "title": data["title"],
            "body": data["body"],
            "summary": data["summary"],
            "file_path": data["file_path"],
        }
        for key in _READ_COLUMNS:
            result[key] = _as_column(key, data.get(key))
        result["lifecycle"] = result["lifecycle"] or "active"
        result["metadata"] = metadata  # replaced below, once the fields are lifted
        # Its own column (#1001, decision 5), read back through JSON like the
        # row: a read spells `aliases` at its own name, never under `metadata`.
        result["aliases"] = json.loads(json.dumps(list(data["aliases"] or []), cls=SafeEncoder))
        # A timestamp the file does not carry (or carries unparsed) is not the
        # file's: the model fills it with "now", which would change on every
        # read. The row's value stands, as before (#670).
        source = entry._source_frontmatter or {}
        # "Carried" at any spelling (#1025): the view the model read.
        view = read_view(source, type(entry), entry.entry_type) if source else {}
        for key in ("created_at", "updated_at"):
            from_file = key in view and key not in entry._unparsed_timestamp_keys
            result[key] = data.get(key) if from_file else row.get(key)
        for key in _ROW_ONLY_KEYS:
            result[key] = row.get(key)
        # A declared field the row keeps in its bag is read at its own name
        # (#1025): the one rule the backend's row shaping applies too.
        lift_declared_fields(result, metadata, type(entry), entry.entry_type)
        # The single-entry read spells a declared field once, at its own name
        # (just lifted), and its `metadata` holds only keys the type does not
        # declare (#1025, option B of the maintainer's decision of 2026-10-20).
        # The row keeps the whole bag: the storage dict the plugins read
        # declared fields from.
        result["metadata"] = read_metadata(
            metadata,
            result,
            type(entry),
            entry.entry_type,
            file_values=source,
            agrees=_read_value_is_the_bags,
        )
        # As _sync_tags stores them: falsy skipped, duplicates (by ==, so
        # ``1`` and ``true`` are one) dropped first-wins, names as TEXT.
        result["tags"] = [_one_text(t) for t in dict.fromkeys(t for t in data["tags"] if t)]
        result["sources"] = _read_sources(data["sources"], entry.id, kb_name, row)
        result["links"] = row.get("links", [])
        result["content_hash"] = content_hash
        return result

    # -----------------------------------------------------------------
    # One reconcile (ADR-0038 step 2). Every path that indexes a KB's files
    # -- `index_kb` (pyrite index build, init, the rebuild job),
    # `sync_incremental` (pyrite index sync, rename, the sync job), `sync_kb`
    # (pyrite kb reindex) and `index_with_attribution` -- is `reconcile_kb`
    # with a different `force`. tests/test_one_reconcile.py pins the rule;
    # tests/test_one_reconcile_structure.py fails when a new path walks files
    # and writes rows without coming through here.
    # -----------------------------------------------------------------

    def plan_reconcile(
        self,
        kb_config: KBConfig,
        *,
        force: bool | Iterable[Path] = False,
        indexed: dict[str, dict[str, Any]] | None = None,
        verify: bool = False,
    ) -> ReconcilePlan:
        """Read the files and decide which file holds each id. Writes nothing.

        Files are walked in KB-relative path order. A file the index already
        knows at that path, whose recorded stat still matches
        (``_file_changed``), is not read: its row's id stands for it. Every
        other file is parsed -- new paths, changed files, and the losing copies
        of a duplicate, which never have a row -- and one that fails to parse
        is listed in ``malformed``. ``force`` (True, or a set of paths) parses
        those files whatever their stat says. With verify=True, known files
        whose stat still matches are hashed and reparsed when content differs.
        """
        repo = KBRepository(kb_config)
        if indexed is None:
            indexed = self._load_indexed_state(kb_config.name)
        path_to_row = {
            info["file_path"]: (entry_id, info)
            for entry_id, info in indexed.items()
            if info.get("file_path")
        }
        forced = None if isinstance(force, bool) else {Path(p) for p in force}
        plan = ReconcilePlan(kb_name=kb_config.name)

        files = []
        for file_path in repo.list_all_files():
            try:
                rel = file_path.relative_to(kb_config.path).as_posix()
            except ValueError:
                continue
            files.append((rel, file_path))
        files.sort()

        for rel, file_path in files:
            plan.walked += 1
            try:
                # Follow symlinks to preserve existing reads from regular-file targets.
                file_stat = file_path.stat()
            except OSError as e:
                # Not "removed while walking": a dangling symlink, a symlink
                # loop or a folder this process cannot search is listed by the
                # walk and cannot be read. Health must say so (#1119); sync
                # already reports it as malformed.
                error = f"{type(e).__name__}: {e}"
                plan.malformed.append({"path": str(file_path), "error": error})
                plan.unreadable[str(file_path)] = error
                logger.warning("Could not stat %s: %s", file_path, e)
                continue
            if not stat_module.S_ISREG(file_stat.st_mode):
                plan.skipped.append({"path": str(file_path), "reason": "not a regular file"})
                logger.warning("Skipping %s: not a regular file", file_path)
                continue
            known = path_to_row.get(str(file_path))
            must_read = force is True or (forced is not None and file_path in forced)
            stat_changed = known is None or _file_changed(known[1], file_stat)
            if known and not must_read and not stat_changed and verify:
                current_hash = _hash_file(file_path)
                if current_hash is None:
                    error = (
                        "Could not hash file for verification; keeping the existing indexed entry"
                    )
                    plan.malformed.append({"path": str(file_path), "error": error})
                    logger.warning("%s: %s", error, file_path)
                    claim = _Claim(rel, file_path, known[0], file_stat)
                    plan.holders.setdefault(claim.entry_id, []).append(claim)
                    continue
                must_read = current_hash != known[1].get("content_hash")
            if known and not must_read and not stat_changed:
                claim = _Claim(rel, file_path, known[0], file_stat)
            else:
                try:
                    entry = repo.load_entry_from_file(file_path)
                except Exception as e:
                    # An unparseable file is content drift, not a Pyrite bug:
                    # reported in the result, one log line, and it holds no id.
                    plan.malformed.append({"path": str(file_path), "error": str(e)})
                    if isinstance(e, OSError | UnicodeError):
                        plan.unreadable[str(file_path)] = f"{type(e).__name__}: {e}"
                    logger.warning("Could not parse %s: %s", file_path, e)
                    continue
                claim = _Claim(rel, file_path, id_text(entry.id), file_stat, entry)
            plan.holders.setdefault(claim.entry_id, []).append(claim)

        for entry_id, claims in plan.holders.items():
            # `files` was sorted, so the first claim is the first path.
            plan.winners[entry_id] = claims[0]
            if len(claims) > 1:
                plan.duplicates.append(
                    {
                        "kb": kb_config.name,
                        "id": entry_id,
                        "winner": claims[0].rel,
                        "paths": [c.rel for c in claims],
                    }
                )
        plan.duplicates.sort(key=lambda d: d["id"])
        return plan

    def reconcile_kb(
        self,
        kb_config: KBConfig,
        *,
        force: bool | Iterable[Path] = False,
        verify: bool = False,
        enrich: Callable[[Entry, Path, dict[str, Any]], Callable[[], None] | None] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        """Make the KB's rows equal its files (I2), and report what changed.

        Rows become exactly the ids of parseable files: each id's row points at
        its winning file (``plan_reconcile``) with that file's hash; a row no
        file claims is retired. A file is written when its row is missing, its
        path moved, or its content hash differs; a file whose stat moved but
        whose hash did not is rewritten only to record the new stat, and is not
        counted. ``force=True`` (a rebuild) rewrites every row, to recompute what
        the row derives from the file. Never writes a file (I4).

        ``enrich(entry, path, data)`` may add to a row before it is written and
        return a callable to run after (attribution writes ``entry_version``).

        Returns ``added``, ``updated``, ``removed`` (row counts), ``malformed``
        (``{"path", "error"}``), ``skipped`` (non-regular paths and reasons),
        ``duplicates`` (``{"kb", "id", "winner", "paths"}``, KB-relative
        paths, the winner first) and ``written`` (rows written, counted or not).
        """
        results: dict[str, Any] = {
            "added": 0,
            "updated": 0,
            "removed": 0,
            "malformed": [],
            "skipped": [],
            "duplicates": [],
            "written": 0,
        }
        if not kb_config.path.exists():
            return results
        if not isinstance(force, bool):
            force = {Path(p) for p in force}

        self.db.register_kb(
            name=kb_config.name,
            kb_type=kb_config.kb_type,
            path=str(kb_config.path),
            description=kb_config.description,
        )
        indexed = self._load_indexed_state(kb_config.name)
        if verify:
            plan = self.plan_reconcile(kb_config, force=force, indexed=indexed, verify=True)
        else:
            plan = self.plan_reconcile(kb_config, force=force, indexed=indexed)
        results["malformed"] = plan.malformed
        results["skipped"] = plan.skipped
        results["duplicates"] = plan.duplicates

        total = len(plan.winners)
        unwritable: set[str] = set()
        for done, (entry_id, claim) in enumerate(sorted(plan.winners.items()), start=1):
            if claim.entry is not None:
                try:
                    self._write_claim(
                        kb_config.name, claim, indexed.get(entry_id), force, enrich, results
                    )
                except _DATABASE_FAULTS as e:
                    # The database refused the write: a column the schema
                    # lacks, a read-only or full disk, a lock. That is not the
                    # file's fault, and filing it under "could not be parsed"
                    # emptied the index and blamed every file, exit 0 (#1260).
                    # Stop before the removal pass below, so the rows already
                    # there are kept.
                    raise _storage_error_for_write(e, claim.rel, self.db) from e
                except Exception as e:
                    # The file parsed but its row cannot be written (a
                    # `title:` that is a YAML list, found on the pyrite KB).
                    # It is reported like a file that cannot be read, and
                    # holds no row: a row kept from before would be stale.
                    logger.warning("Could not index %s", claim.path, exc_info=True)
                    # First line only: a driver error carries the SQL and
                    # every bound value, the file's body among them.
                    error = (str(e).splitlines() or [type(e).__name__])[0]
                    results["malformed"].append({"path": str(claim.path), "error": error})
                    unwritable.add(entry_id)
            if progress_callback and done % 10 == 0 and done < total:
                progress_callback(done, total)
        if progress_callback:
            # Always once at the end, an empty KB included: the index worker
            # turns this into the job's `index_progress` event.
            progress_callback(total, total)

        for entry_id in indexed:
            if entry_id not in plan.winners or entry_id in unwritable:
                self.remove_entry(entry_id, kb_config.name)
                results["removed"] += 1

        self.db.update_kb_indexed(kb_config.name, len(plan.winners) - len(unwritable))
        return results

    def _write_claim(
        self,
        kb_name: str,
        claim: _Claim,
        row: dict[str, Any] | None,
        force: bool | Iterable[Path],
        enrich,
        results: dict[str, Any],
    ) -> None:
        """Write one winning file's row if the reconcile rule says so."""
        content_hash = _hash_file(claim.path)
        if row is None:
            counter = "added"
        elif row.get("file_path") != str(claim.path) or row.get("content_hash") != content_hash:
            counter = "updated"
        else:
            # Same file, same bytes: the hash breaks the tie. Rewrite only for
            # a rebuild or to record a stat that moved (a touch, a checkout),
            # so the next reconcile does not read the file again.
            counter = None
            forced = force is True or (not isinstance(force, bool) and claim.path in force)
            if not forced and not _file_changed(row, claim.stat):
                return
        entry = claim.entry
        assert entry is not None
        data = self._entry_to_dict(
            entry, kb_name, claim.path, stat=claim.stat, content_hash=content_hash
        )
        after = enrich(entry, claim.path, data) if enrich else None
        self.db.upsert_entry(data)
        if after:
            after()
        results["written"] += 1
        if counter:
            results[counter] += 1

    def index_kb(
        self, kb_name: str, progress_callback: Callable[[int, int], None] | None = None
    ) -> int:
        """Rebuild a KB's rows from its files: ``reconcile_kb`` reading every
        file. Returns the number of entries indexed (one per id, not per file).
        """
        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            raise ValueError(f"KB '{kb_name}' not found in config")
        result = self.reconcile_kb(kb_config, force=True, progress_callback=progress_callback)
        if result["malformed"]:
            logger.warning("%d files could not be parsed", len(result["malformed"]))
        return result["written"]

    def index_all(
        self, progress_callback: Callable[[str, int, int], None] | None = None
    ) -> dict[str, int]:
        """
        Index all configured KBs.

        Args:
            progress_callback: Optional callback(kb_name, current, total)

        Returns:
            Dict of kb_name -> entries indexed
        """
        results = {}

        for kb in self.config.all_kbs():
            if not kb.path.exists():
                logger.warning("Skipping %s: path does not exist", kb.name)
                continue

            def make_kb_progress(kb_name: str):
                def kb_progress(current: int, total: int):
                    if progress_callback:
                        progress_callback(kb_name, current, total)

                return kb_progress

            count = self.index_kb(kb.name, make_kb_progress(kb.name))
            results[kb.name] = count

        return results

    def index_entry(self, entry: Entry, kb_name: str, file_path: Path) -> None:
        """Index a single entry."""
        data = self._entry_to_dict(entry, kb_name, file_path)
        self.db.upsert_entry(data)

    def row_for(
        self, entry: Entry, kb_name: str, file_path: Path, *, content_hash: str
    ) -> dict[str, Any]:
        """The index row of ``entry`` as parsed from the bytes whose hash is
        ``content_hash``, checked to be storable before anything is published.

        ADR-0042 decision 10: the row is built from the bytes written, never
        from an in-memory entry; and a write is never reported as failed
        after it is published (#730 spike, surprise 2), so a result the index
        cannot store -- a tagged value the row's JSON cannot encode, a
        ``title:`` that is a list -- raises ``ValueError`` here, with the
        reason, and the caller refuses the write with nothing written. The
        file's stat is read by ``index_row`` once the file is on disk.
        """
        from ..utils.json_utils import SafeEncoder
        from .crud import bindable

        data = self._entry_to_dict(entry, kb_name, file_path, content_hash=content_hash)
        bound = bindable(data)
        try:
            json.dumps(bound.get("metadata") or {}, cls=SafeEncoder)
        except (TypeError, ValueError) as e:
            raise ValueError(f"metadata cannot be stored as JSON: {e}") from e
        for key in _SCALAR_COLUMNS:
            value = bound.get(key)
            if not isinstance(value, str | int | float | bool | type(None)):
                raise ValueError(
                    f"{key} is a {type(value).__name__}, not a value the index can store"
                )
        for tag in bound.get("tags") or []:
            if not isinstance(tag, str | int | float | bool):
                raise ValueError(
                    f"a tag is a {type(tag).__name__}, not a value the index can store"
                )
        return data

    def index_row(self, data: dict[str, Any], file_path: Path) -> None:
        """Store a row ``row_for`` built, with the stat of the file as it is now."""
        try:
            stat = file_path.stat()
        except OSError:
            stat = None
        data["file_mtime_ns"] = stat.st_mtime_ns if stat else None
        data["file_size"] = stat.st_size if stat else None
        self.db.upsert_entry(data)

    def remove_entry(self, entry_id: str, kb_name: str) -> bool:
        """Remove an entry from the index."""
        return self.db.delete_entry(entry_id, kb_name)

    def remove_kb(self, kb_name: str) -> None:
        """Remove a KB and all its entries from the index."""
        self.db.unregister_kb(kb_name)

    def is_empty(self) -> bool:
        """True when the index holds no entries at all."""
        return self.db.count_entries() == 0

    def get_index_stats(self, kb_names: set[str] | None = None) -> dict[str, Any]:
        """Get statistics about the index.

        ``kb_names`` restricts every part of the answer -- the per-KB map and
        each total -- to those KBs; ``None`` means the whole index. The REST
        route passes the caller's readable set, so a private KB contributes
        neither its name nor its rows to a caller without a grant.
        """
        stats = {
            "kbs": {},
            "total_entries": 0,
            "total_tags": 0,
            "total_links": 0,
        }

        for kb in self.config.all_kbs():
            if kb_names is not None and kb.name not in kb_names:
                continue
            kb_stats = self.db.get_kb_stats(kb.name)
            if kb_stats:
                stats["kbs"][kb.name] = kb_stats
                stats["total_entries"] += kb_stats.get("actual_count", 0)

        global_counts = self.db.get_global_counts(kb_names=kb_names)
        stats["total_tags"] = global_counts["total_tags"]
        stats["total_links"] = global_counts["total_links"]

        stats["type_counts"] = self.db.get_type_counts(kb_names=kb_names)

        return stats

    def _load_indexed_state(self, kb_name: str) -> dict[str, dict[str, str]]:
        """Load indexed entry state from DB for a KB.

        Returns dict mapping entry_id -> {"file_path", "indexed_at",
        "content_hash", "file_mtime_ns", "file_size"}.
        """
        indexed = {}
        for row in self.db.get_entries_for_indexing(kb_name):
            indexed[row["id"]] = {
                "file_path": row["file_path"],
                "indexed_at": row["indexed_at"],
                "content_hash": row.get("content_hash"),
                "file_mtime_ns": row.get("file_mtime_ns"),
                "file_size": row.get("file_size"),
            }
        return indexed

    def check_staleness(self, kb_names: list[str] | None = None) -> list[dict[str, Any]]:
        """Cheap per-KB staleness probe for the search path.

        Unlike ``check_health()``, this does NOT parse every entry. For each
        KB it walks the files for the newest mtime, then compares it against
        the index's newest ``indexed_at``. A KB is reported stale when a file
        on disk is newer than the last index write — which covers both edited
        files and brand-new files (a new file carries a recent mtime).

        The mtime comparison is deliberately the *only* signal. A naive
        file-count-vs-indexed-count check looks tempting but false-positives:
        the indexer stores one entry per id, so duplicate ids on disk (e.g. a
        file left behind by a botched ``done/`` move) make the counts diverge
        permanently without the index being stale at all. Counting would cry
        wolf on every search; mtime does not.

        Returns one dict per stale KB:
        ``{"kb", "reason", "newest_file_mtime", "newest_indexed_at"}``. Empty
        list means the index is fresh — safe to search without a warning.
        Cheap enough to run on every search invocation.

        ``kb_names`` restricts the probe to those KBs: a KB left out is not
        walked or stat'ed at all (``search -k X`` must not pay for the others).
        ``None`` probes every KB.
        """
        stale: list[dict[str, Any]] = []

        for kb in self.config.all_kbs():
            if kb_names is not None and kb.name not in kb_names:
                continue
            if not kb.path.exists():
                continue

            repo = KBRepository(kb)
            newest_mtime: datetime | None = None
            for file_path in repo.list_files():
                mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=UTC)
                if newest_mtime is None or mtime > newest_mtime:
                    newest_mtime = mtime

            indexed = self._load_indexed_state(kb.name)
            newest_indexed: datetime | None = None
            for meta in indexed.values():
                ts = meta.get("indexed_at")
                if not ts:
                    continue
                parsed = _parse_indexed_at(ts)
                if newest_indexed is None or parsed > newest_indexed:
                    newest_indexed = parsed

            # A non-empty index with no parseable timestamp is itself suspect
            # (legacy rows): treat as stale so the user re-syncs.
            is_stale = (newest_mtime is not None) and (
                newest_indexed is None or newest_mtime > newest_indexed
            )

            if is_stale:
                stale.append(
                    {
                        "kb": kb.name,
                        "reason": "a file is newer than the index",
                        "newest_file_mtime": newest_mtime.isoformat(),
                        "newest_indexed_at": (
                            newest_indexed.isoformat() if newest_indexed else None
                        ),
                    }
                )

        return stale

    def check_health(self, kb_name: str | None = None) -> dict[str, Any]:
        """
        Check index health and consistency.

        Args:
            kb_name: restrict every check to this KB. Without it the report
                covers all configured KBs, which on a machine with many of them
                is dominated by KBs the caller is not asking about and cannot
                serve as a per-project gate (#18).

        Returns dict with:
        - missing_files: entries in DB but file not found
        - unreadable_files: files that exist but could not be loaded for a
          non-frontmatter error, with path and exception detail
        - orphaned_entries: indexed IDs absent from readable files even though
          their indexed path still exists; replacement_id identifies an ID
          now found at that same path
        - unindexed_files: files not in DB
        - stale_entries: entries whose file's mtime or size differs from the
          values recorded when it was indexed (``_file_changed``, the
          reconcile's rule)
        - content_changed: entries whose on-disk content hash no longer
          matches the hash recorded at index time. Catches same-second
          edits and coarse-mtime filesystems that `stale_entries` (mtime
          comparison) misses. This check already reads every file's bytes
          (via `_load_entry`), so hashing here is nearly free — unlike
          `check_staleness()`, which stays mtime-only to remain cheap
          enough for the search path.
        - broken_links: count of link rows with an unresolvable target
        - undeclared_types: entries whose `entry_type` isn't declared in the
          KB's `kb.yaml` types, core types included. Aggregated per
          `(kb, type)` with a count. KBs without a `kb.yaml`, or whose
          `kb.yaml` declares no types, are skipped since there's no
          declared-type list to enforce.
        - missing_required_fields: entries missing a field the type's
          `required:` list declares. One entry per (kb, id, type, missing).
          KBs without a `kb.yaml` are skipped.
        - subdirectory_mismatches: entries whose file_path doesn't sit in
          the `subdirectory:` the type declares. `subdirectory:` is a
          writer hint, not a reader constraint, so this is surfaced as a
          warning, not an error. KBs without a `kb.yaml` are skipped.
        - invalid_statuses: entries whose `status` a plugin validator's enum
          rejects.
        - off_list_values: one row per off-list value of a declared enum
          (#555, #47) -- see `_check_off_list_values`.
        - duplicates: one row per id held by more than one file --
          ``{"kb", "id", "winner", "paths"}`` -- with the file the index
          holds (the lexicographically first KB-relative path). Only the
          winner is checked for staleness; a losing copy is not the indexed
          file, so it is neither stale nor changed.
        """
        health: dict[str, Any] = {
            "missing_files": [],
            "unreadable_files": [],
            "orphaned_entries": [],
            "unindexed_files": [],
            "stale_entries": [],
            "content_changed": [],
            "broken_links": 0,
            "links_stale_kb": 0,
            "undeclared_types": [],
            "missing_required_fields": [],
            "subdirectory_mismatches": [],
            "malformed_frontmatter": [],
            "invalid_statuses": [],
            "off_list_values": [],
            "duplicates": [],
            "removed_extensions": [],
            # Files whose frontmatter Pyrite cannot model (#760 (b)): a
            # tagged, complex or duplicate key. No write reaches them, so
            # they are listed with the line, never left invisible.
            "unmodellable_frontmatter": [],
            # A field one file spells twice (`k:` and `metadata.k`, or a field
            # and its alias): the reader believes one spelling, no update
            # reaches the field until the file is fixed (Andon #994).
            "double_spellings": [],
        }

        # The file's own value of each protocol column, per (kb, id), read
        # while every file is loaded below anyway: a typed column holds
        # Pyrite's reading (a task's `priority` is 5 for a file with none),
        # so the off-list check takes these from the file (#554, #555).
        file_values: dict[tuple[str, str], dict[str, Any]] = {}

        # Plugin validators per KB, looked up ONCE per KB however many checks
        # and rows use them (coordinator blocker 3).
        validators_by_kb: dict[str, list] = {}

        def validators_for(kb) -> list:
            if kb.name not in validators_by_kb:
                try:
                    from ..plugins import get_registry

                    validators_by_kb[kb.name] = get_registry().get_validators_for_kb(kb.kb_type)
                except Exception:
                    logger.warning(
                        "Could not load validators for KB %r; the invalid-status and "
                        "plugin off-list checks are disabled for this KB this pass",
                        kb.name,
                        exc_info=True,
                    )
                    validators_by_kb[kb.name] = []
            return validators_by_kb[kb.name]

        # One scoped list drives every check below, so `-k` cannot scope some
        # of the report and leave the rest global.
        kbs = [kb for kb in self.config.all_kbs() if kb_name is None or kb.name == kb_name]

        # A KB whose type belonged to an extension Pyrite has deleted (#953):
        # its types are undeclared for a reason the user should be told. The
        # rule is `plugins/removed.py`'s; this report only shows it.
        from ..plugins.removed import (
            removed_entry_types_held,
            removed_extension_for_entry_types,
            removed_extension_for_kb_type,
            removed_extension_notice,
        )

        for kb in kbs:
            held_types = [
                row["entry_type"]
                for row in self.db.execute_sql(
                    "SELECT DISTINCT entry_type FROM entry WHERE kb_name = :kb_name",
                    {"kb_name": kb.name},
                )
                if row["entry_type"]
            ]
            ext = removed_extension_for_kb_type(kb.kb_type) or removed_extension_for_entry_types(
                held_types
            )
            if ext is not None:
                health["removed_extensions"].append(
                    {
                        "kb": kb.name,
                        "kb_type": kb.kb_type,
                        "extension": ext.name,
                        "entry_types": removed_entry_types_held(ext, held_types),
                        "message": removed_extension_notice(kb.name, kb.kb_type, held_types),
                    }
                )

        for kb in kbs:
            if not kb.path.exists():
                continue

            indexed = self._load_indexed_state(kb.name)
            # The reconcile's own reading of the files (every file parsed):
            # the same winner per id, the same duplicates, the same staleness
            # rule as `pyrite index sync` (ADR-0038 step 2).
            plan = self.plan_reconcile(kb, force=True, indexed=indexed)
            health["duplicates"].extend(plan.duplicates)
            # A path sync lists and skips because it is not a regular file
            # (a directory named `x.md`, a FIFO) used to vanish from health
            # while sync reported it in `skipped` (#1119).
            for skipped_file in plan.skipped:
                health["unreadable_files"].append(
                    {
                        "kb": kb.name,
                        "path": skipped_file["path"],
                        "error": skipped_file["reason"],
                    }
                )
            for bad in plan.malformed:
                if bad["path"] in plan.unreadable:
                    # A present file that cannot be decoded/read is neither
                    # missing nor malformed frontmatter: report its actual
                    # failure so health cannot silently pass (#593).
                    health["unreadable_files"].append(
                        {"kb": kb.name, "path": bad["path"], "error": plan.unreadable[bad["path"]]}
                    )
                else:
                    health["malformed_frontmatter"].append({"kb": kb.name, **bad})
            # The #760 listing, from the reconcile's own read (#990): only a
            # file whose loaded frontmatter could be refused (a non-string
            # key, a set, a flow-style root: `may_be_unmodellable`), or that
            # did not load, is parsed again; the rest cannot be listed.
            from .file_operations import may_be_unmodellable

            suspects = [
                claim.path
                for claims in plan.holders.values()
                for claim in claims
                if claim.entry is None
                or may_be_unmodellable(getattr(claim.entry, "_source_frontmatter", None))
            ] + [Path(bad["path"]) for bad in plan.malformed]
            health["unmodellable_frontmatter"].extend(unmodellable_files(kb, sorted(suspects)))

            # Ids the files hold this pass tell an indexed id replaced in place
            # from a file that simply moved. A file whose id could not be read
            # is not an orphan. Same file set as sync (list_all_files).
            loaded_ids = set(plan.holders)
            # An id two files hold is a duplicate (reported as such), not a
            # replacement: it names no new id for the old row.
            loaded_ids_by_path = {
                str(cs[0].path): entry_id for entry_id, cs in plan.holders.items() if len(cs) == 1
            }
            uninspectable_paths = {bad["path"] for bad in plan.malformed}

            from ..schema.field_aliases import double_spellings

            for entry_id, claim in sorted(plan.winners.items()):
                file_path = claim.path
                entry = claim.entry
                source = getattr(entry, "_source_frontmatter", None) or {}
                if entry is not None and isinstance(source, dict):
                    for field_name, paths in double_spellings(
                        source, type(entry), entry.entry_type
                    ).items():
                        health["double_spellings"].append(
                            {
                                "kb": kb.name,
                                "id": entry_id,
                                "field": field_name,
                                "spellings": [".".join(p) for p in paths],
                            }
                        )
                file_values[(kb.name, entry_id)] = {
                    k: source[k] for k in PROTOCOL_COLUMN_KEYS if source.get(k) is not None
                }
                row = indexed.get(entry_id)
                if row is None:
                    health["unindexed_files"].append(
                        {"kb": kb.name, "path": str(file_path), "id": entry_id}
                    )
                    continue
                if row.get("file_path") != str(file_path):
                    continue  # moved: reported as the row's missing file below
                if _file_changed(row, claim.stat):
                    health["stale_entries"].append(
                        {
                            "kb": kb.name,
                            "id": entry_id,
                            "file_mtime": datetime.fromtimestamp(
                                claim.stat.st_mtime, tz=UTC
                            ).isoformat(),
                            "indexed_at": row.get("indexed_at"),
                        }
                    )
                indexed_hash = row.get("content_hash")
                if indexed_hash:
                    current_hash = _hash_file(file_path)
                    if current_hash and current_hash != indexed_hash:
                        health["content_changed"].append(
                            {"kb": kb.name, "id": entry_id, "path": str(file_path)}
                        )

            # Check for missing files and IDs superseded at an existing path.
            reported_unreadable = {row["path"] for row in health["unreadable_files"]}
            for entry_id, info in indexed.items():
                if not info["file_path"]:
                    continue  # an index-only row has no file to be missing
                indexed_path = Path(info["file_path"])
                try:
                    present = indexed_path.exists()
                except OSError as e:
                    # A folder this process cannot search (Python 3.13 raises
                    # PermissionError where 3.12 said "absent"): the file may
                    # well be there. Report it; never let one row end the report.
                    if str(indexed_path) not in reported_unreadable:
                        reported_unreadable.add(str(indexed_path))
                        health["unreadable_files"].append(
                            {
                                "kb": kb.name,
                                "path": str(indexed_path),
                                "error": f"{type(e).__name__}: {e}",
                            }
                        )
                    continue
                if not present:
                    health["missing_files"].append(
                        {"kb": kb.name, "id": entry_id, "path": info["file_path"]}
                    )
                elif entry_id not in loaded_ids and str(indexed_path) not in uninspectable_paths:
                    orphan = {"kb": kb.name, "id": entry_id, "path": info["file_path"]}
                    replacement_id = loaded_ids_by_path.get(str(indexed_path))
                    if replacement_id and replacement_id != entry_id:
                        orphan["replacement_id"] = replacement_id
                    health["orphaned_entries"].append(orphan)

        # Count broken links (targets that don't resolve to entries)
        broken_sql = """
            SELECT COUNT(*) as cnt FROM link l
            LEFT JOIN entry e ON l.target_id = e.id AND l.target_kb = e.kb_name
            WHERE e.id IS NULL
        """
        broken_params: dict[str, Any] = {}
        if kb_name is not None:
            # Links owned by the scoped KB, not links pointing into it: the
            # report answers "is this KB's index sound".
            broken_sql += " AND l.source_kb = :kb_name"
            broken_params["kb_name"] = kb_name
        rows = self.db.execute_sql(broken_sql, broken_params)
        if rows:
            health["broken_links"] = rows[0]["cnt"]

        # Link rows a rebuild would point at a different KB (ADR-0049 Q3,
        # #980): the prefix the file wrote, resolved now, is not the stored
        # KB. A row from before v28 has no `target_prefix`, but its
        # `target_kb` is the prefix as written (the old rule), so it is
        # checked the same way. Causes: a shortname renamed or added in
        # config since the file was indexed (an unchanged file's hash keeps
        # `sync` from re-reading it), or a row stored before the full name
        # was. A link whose prefix names no KB is not counted: a rebuild
        # writes the same row, and `broken_links` counts it. `pyrite index
        # build` re-reads every file, which clears this count by definition.
        stale_sql = (
            "SELECT COALESCE(l.target_prefix, l.target_kb) AS written, l.target_kb, "
            "COUNT(*) AS cnt FROM link l"
        )
        stale_params: dict[str, Any] = {}
        if kb_name is not None:
            stale_sql += " WHERE l.source_kb = :kb_name"
            stale_params["kb_name"] = kb_name
        stale_sql += " GROUP BY written, l.target_kb"
        health["links_stale_kb"] = sum(
            row["cnt"]
            for row in self.db.execute_sql(stale_sql, stale_params)
            if self._link_kb(row["written"], row["target_kb"]) != row["target_kb"]
        )

        # Each kb.yaml is loaded once here, never again below: a schema that
        # fails to load is reported in `schema_errors` and its KB counts as
        # declaring no types, so health still answers for the broken KB.
        schemas = self._load_schemas(kbs)

        # The schema owns the type vocabulary; an empty declaration is
        # unrestricted, including plugin types and core types.
        for kb in kbs:
            if not kb.path.exists() or not kb.kb_yaml_path.exists():
                continue

            kb_schema = schemas[kb.name][0]
            declared = set(kb_schema.declared_types()) if kb_schema else set()
            if not declared:
                continue

            type_rows = self.db.execute_sql(
                "SELECT entry_type, COUNT(*) AS cnt FROM entry "
                "WHERE kb_name = :kb_name GROUP BY entry_type",
                {"kb_name": kb.name},
            )
            for row in type_rows:
                etype = row["entry_type"]
                if etype and etype not in declared:
                    health["undeclared_types"].append(
                        {"kb": kb.name, "type": etype, "count": row["cnt"]}
                    )

        health.update(self._type_name_drift(kbs, schemas))

        # Required-field + subdirectory checks. Per KB with a kb.yaml,
        # compare each entry against its type's `required:` list (default
        # ["title"]) and its `subdirectory:` hint. Skip KBs with no kb.yaml.
        for kb in kbs:
            if not kb.path.exists() or not kb.kb_yaml_path.exists():
                continue
            kb_schema = schemas[kb.name][0]
            if not kb_schema or not kb_schema.types:
                continue

            # Plugin validators scoped to this KB type, looked up ONCE per
            # KB (coordinator blocker 3: this used to be re-looked-up, and
            # its non-conforming-validator warnings re-logged, on every row
            # -- a KB with N entries meant N lookups). A KB with none
            # registered short-circuits the per-row status check below
            # entirely: `run_validators` is never called for that KB's rows.
            kb_validators = validators_for(kb)

            entry_rows = self.db.execute_sql(
                "SELECT id, entry_type, title, body, summary, file_path, "
                "date, start_date, end_date, due_date, status, location, "
                "assignee, priority FROM entry WHERE kb_name = :kb_name",
                {"kb_name": kb.name},
            )
            for row in entry_rows:
                if kb_validators:
                    self._check_invalid_status(kb, row, kb_validators, health)

                type_schema = kb_schema.types.get(row["entry_type"])
                if type_schema is None:
                    continue  # undeclared_types handles this separately

                missing = [
                    fname
                    for fname in type_schema.required
                    # Only validate fields we can see as a column. Unknown
                    # required-field names are silently skipped; they'd need
                    # the metadata JSON to enforce, out of scope here.
                    if fname in row and not row[fname]
                ]
                if missing:
                    health["missing_required_fields"].append(
                        {
                            "kb": kb.name,
                            "id": row["id"],
                            "type": row["entry_type"],
                            "missing": missing,
                        }
                    )

                # Subdirectory mismatch. An empty string means "explicitly
                # KB root allowed"; None means "no hint"; any non-empty
                # string is the expected first-path-component. Both sides are
                # normalized: a declared `people/` and the path component
                # `people` are the same directory, so a trailing slash is a
                # spelling, not a location (#44).
                declared_sub = type_schema.subdirectory
                if declared_sub:  # non-empty string
                    file_path = row["file_path"]
                    if file_path:
                        try:
                            rel = Path(file_path).resolve().relative_to(kb.path.resolve())
                            actual_sub = rel.parts[0] if len(rel.parts) > 1 else ""
                        except ValueError:
                            actual_sub = ""
                        if actual_sub.strip("/") != declared_sub.strip("/"):
                            health["subdirectory_mismatches"].append(
                                {
                                    "kb": kb.name,
                                    "id": row["id"],
                                    "type": row["entry_type"],
                                    "declared_subdirectory": declared_sub.strip("/"),
                                    "actual_path": actual_sub or ".",
                                }
                            )

        for kb in kbs:
            if kb.path.exists():
                self._check_off_list_values(
                    kb, health, file_values, validators_for(kb), schemas[kb.name][0]
                )

        return health

    @staticmethod
    def _load_schemas(kbs: Iterable[KBConfig]) -> dict[str, tuple[Any, str | None]]:
        """Each KB's schema, loaded once: ``{kb: (schema or None, error or None)}``.

        Health diagnoses broken KBs, so a kb.yaml that fails to load (invalid
        YAML, `types: null`, `types: [a, b]`) gives ``(None, error)`` -- the KB
        declares no types -- instead of raising. ``KBConfig.kb_schema`` does not
        cache a failure, so every later read would raise again. A KB with no
        kb.yaml gives ``(None, None)``.
        """
        loaded: dict[str, tuple[Any, str | None]] = {}
        for kb in kbs:
            if not kb.kb_yaml_path.exists():
                loaded[kb.name] = (None, None)
                continue
            try:
                loaded[kb.name] = (kb.kb_schema, None)
            except Exception as exc:
                loaded[kb.name] = (None, str(exc) or type(exc).__name__)
        return loaded

    def _type_name_drift(
        self,
        kbs: Iterable[KBConfig],
        schemas: dict[str, tuple[Any, str | None]] | None = None,
    ) -> dict[str, Any]:
        """Report exact case/underscore/space variants, independently per KB.

        Only the derived index is read; this does not normalize stored types.
        JSON is bounded to 100 groups and 50 spellings per group, with totals.
        ``schemas`` is ``_load_schemas(kbs)`` when the caller already loaded
        them; ``schema_errors`` names each KB whose kb.yaml failed to load.
        """
        kbs = sorted(kbs, key=lambda item: item.name)
        if schemas is None:
            schemas = self._load_schemas(kbs)
        groups = []
        schema_errors = []
        for kb in kbs:
            schema, error = schemas[kb.name]
            if error is not None:
                schema_errors.append({"kb": kb.name, "error": error})
            declared: set[str] = schema.declared_types() if schema is not None else set()
            by_name: dict[str, list[dict[str, Any]]] = {}
            rows = self.db.execute_sql(
                "SELECT entry_type, COUNT(*) AS cnt FROM entry "
                "WHERE kb_name = :kb_name GROUP BY entry_type ORDER BY entry_type",
                {"kb_name": kb.name},
            )
            for row in rows:
                spelling = row["entry_type"]
                if not spelling:
                    continue
                normalized = spelling.lower().replace("_", "-").replace(" ", "-")
                by_name.setdefault(normalized, []).append(
                    {"type": spelling, "count": row["cnt"], "declared": spelling in declared}
                )
            for normalized, spellings in sorted(by_name.items()):
                if len(spellings) > 1:
                    groups.append(
                        {
                            "kb": kb.name,
                            "normalized": normalized,
                            "spellings": spellings[:50],
                            "spellings_total": len(spellings),
                            "spellings_has_more": len(spellings) > 50,
                        }
                    )
        return {
            "type_name_drift": groups[:100],
            "type_name_drift_total": len(groups),
            "type_name_drift_has_more": len(groups) > 100,
            "schema_errors": schema_errors,
        }

    #: Index columns that are never an enum-constrained field value.
    _NOT_FIELD_COLUMNS = frozenset(
        {"id", "kb_name", "metadata", "body", "summary", "file_path", "indexed_at", "content_hash"}
    )

    def _check_off_list_values(
        self,
        kb,
        health: dict,
        file_values: dict[tuple[str, str], dict[str, Any]],
        validators: list,
        kb_schema: Any = None,
    ) -> None:
        """Report every off-list value of a declared enum in ``kb`` (#555, #47).

        Each row is ``{kb, id, type, field, value, allowed, origin, severity}``,
        one per offending value (a list contributes one row per element), and
        is built from the index row plus its ``metadata`` JSON, so a custom
        field such as ``org_type`` is seen, not only the columns. A protocol
        column (``priority``, ``status``, dates...) is taken from
        ``file_values`` -- the file's own value, absent when the file has none
        or did not load -- never from the column, which holds Pyrite's reading.

        Sources, by ``origin``:
        - ``field`` / ``rule``: the kb.yaml enums, through the same
          ``enum_findings`` the write path and ``schema validate`` use.
          ``severity`` follows ``validation.enforce_enums`` (``error`` on,
          ``warning`` off); an ``allow_other`` field's rows are ``info``.
        - ``plugin``: a plugin validator's ``rule: enum`` on any field but
          ``status``, which stays in ``invalid_statuses``. Plugin
          vocabularies are code-owned and the switch does not govern them,
          so their rows are ``warning`` -- as ``invalid_statuses`` rolls up.

        A kb.yaml enum on ``status`` is reported here unless the entry is
        already in ``invalid_statuses``. Runs for any KB with a kb.yaml enum
        or a plugin validator, not only KBs whose kb.yaml declares types.
        """
        from ..schema.enum_check import enum_findings

        # ``kb_schema`` is check_health's one load: None for no kb.yaml or one
        # that failed to load (then only plugin validators apply).
        declares_enums = kb_schema is not None and (
            any(fs.allowed_values() for ts in kb_schema.types.values() for fs in ts.fields.values())
            or any(
                isinstance(r, dict) and "enum" in r for r in kb_schema.validation.get("rules") or []
            )
        )
        if not declares_enums and not validators:
            return

        bad_status = {r["id"] for r in health["invalid_statuses"] if r.get("kb") == kb.name}
        rows = self.db.execute_sql(
            "SELECT * FROM entry WHERE kb_name = :kb_name", {"kb_name": kb.name}
        )
        seen: set[tuple] = set()
        out = health["off_list_values"]

        def add(row, field_name, values, allowed, origin, severity):
            for value in values if isinstance(values, list) else [values]:
                key = (row["id"], field_name, repr(value))
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "kb": kb.name,
                        "id": row["id"],
                        "type": row["entry_type"],
                        "field": field_name,
                        "value": value,
                        "allowed": allowed,
                        "origin": origin,
                        "severity": severity,
                    }
                )

        for row in rows:
            fields = {
                k: v
                for k, v in row.items()
                if v is not None
                and k not in self._NOT_FIELD_COLUMNS
                and k not in PROTOCOL_COLUMN_KEYS
            }
            fields.pop("entry_type", None)
            fields.update(file_values.get((kb.name, row["id"]), {}))
            for k, v in parse_metadata(row.get("metadata")).items():
                if k not in fields and v is not None:
                    fields[k] = v

            if declares_enums:
                for f in enum_findings(kb_schema, row["entry_type"], fields):
                    if f["field"] == "status" and row["id"] in bad_status:
                        continue
                    severity = "info" if f.get("allow_other") else f["severity"]
                    add(row, f["field"], f["got"], f["expected"], f["origin"], severity)

            ctx = {"kb_type": kb.kb_type, "kb_name": kb.name}
            for validator in validators:
                try:
                    results = validator(row["entry_type"], fields, ctx)
                except Exception:
                    logger.warning(
                        "Validator %r raised for %s/%s; off-list check skipped for "
                        "this entry from this validator",
                        getattr(validator, "__name__", validator),
                        kb.name,
                        row["id"],
                        exc_info=True,
                    )
                    continue
                for item in results or []:
                    if not isinstance(item, dict) or item.get("rule") != "enum":
                        continue
                    if item.get("field") in (None, "status"):
                        continue
                    add(
                        row,
                        item["field"],
                        item.get("got"),
                        list(item.get("expected") or []),
                        "plugin",
                        "warning",
                    )

    @staticmethod
    def _check_invalid_status(kb, row: dict, validators: list, health: dict) -> None:
        """Flag an entry whose `status` is not in its type's declared enum.

        ``validators`` is the KB's plugin validators, looked up ONCE per KB
        by the caller (coordinator blocker 3) -- not re-fetched here per
        row. Every validator in the list already binds the
        ``(entry_type, fields, ctx)`` contract (registration refused any
        that didn't); calling one directly that still raises is a bug in
        that validator, not a contract mismatch, and costs this one entry's
        check, not the whole KB's pass -- the same degrade-per-validator
        guarantee ``PluginRegistry.run_validators`` provides, applied here
        without re-fetching the validator list on every call. This reuses
        the existing validator logic (e.g. software-kb's BACKLOG_STATUSES)
        so core does not hardcode any plugin's status vocabulary. Silently
        turning the whole check off for a KB is exactly the failure mode
        that let 75 backlog items drift onto an off-enum status undetected
        (fail-open-exception-sweep site #2).
        """
        status = row.get("status")
        if not status:
            return
        fields = {"status": status}
        ctx = {"kb_type": kb.kb_type}
        for validator in validators:
            try:
                results = validator(row["entry_type"], fields, ctx)
            except Exception:
                logger.warning(
                    "Status validator %r raised for %s/%s; invalid-status "
                    "check skipped for this entry from this validator",
                    getattr(validator, "__name__", validator),
                    kb.name,
                    row["id"],
                    exc_info=True,
                )
                continue
            for item in results or []:
                if not isinstance(item, dict):
                    continue  # malformed validator output; run_validators drops it too
                if item.get("field") == "status" and item.get("rule") == "enum":
                    health["invalid_statuses"].append(
                        {
                            "kb": kb.name,
                            "id": row["id"],
                            "type": row["entry_type"],
                            "status": status,
                            "allowed": item.get("expected", []),
                        }
                    )
                    return  # one report per entry is enough

    def sync_incremental(
        self,
        kb_name: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        *,
        verify: bool = False,
    ) -> dict[str, Any]:
        """Reconcile one KB, or every configured KB, reading only files whose
        stat moved and paths the index does not know (``reconcile_kb``).

        When verify=True, also hash known files whose stat is unchanged and re-read
        those whose content hash differs.

        Returns ``added``, ``updated``, ``removed``, ``malformed``,
        ``skipped`` non-regular files and ``duplicates`` summed over the KBs.
        The CLI prints malformed, skipped and duplicate files as a summary
        rather than per-file tracebacks (Tier A 1080).
        """
        results: dict[str, Any] = {
            "added": 0,
            "updated": 0,
            "removed": 0,
            "malformed": [],
            "skipped": [],
            "duplicates": [],
        }
        kbs = [self.config.get_kb(kb_name)] if kb_name else self.config.all_kbs()
        kbs = [kb for kb in kbs if kb and kb.path.exists()]

        for n, kb in enumerate(kbs, start=1):
            callback = None
            if progress_callback and len(kbs) == 1:
                callback = progress_callback
            if verify:
                one = self.reconcile_kb(kb, progress_callback=callback, verify=True)
            else:
                one = self.reconcile_kb(kb, progress_callback=callback)
            for key in ("added", "updated", "removed"):
                results[key] += one[key]
            results["malformed"].extend(one["malformed"])
            results["skipped"].extend(one["skipped"])
            results["duplicates"].extend(one["duplicates"])
            if progress_callback and len(kbs) > 1:
                progress_callback(n, len(kbs))
        return results

    def sync_kb(self, kb_config: KBConfig) -> dict[str, Any]:
        """Reconcile one KB given its config: ``sync_incremental`` for a KB
        that may exist only as a registry row (``KBRegistryService``)."""
        result = self.reconcile_kb(kb_config)
        result.pop("written", None)
        return result

    def _drop_unservable_versions(
        self,
        entry_id: str,
        kb_name: str,
        kb_path: Path,
        current_rel_path: str,
        accepted: set[str],
        git_service: Any,
    ) -> None:
        """Delete this entry's rows outside the walk on a positive fact only:
        the read refuses a file git did read (another entry's id), or the
        stored path is unsafe. `git_service` is the injected GitService:
        storage does not import it. Anything else (git failed, the commit is
        unreadable or past a shallow boundary) keeps the row (#453)."""
        for commit_hash, stored_path in self.db.list_entry_version_rows(entry_id, kb_name):
            if commit_hash in accepted:
                continue
            verdict, _ = git_service.version_servability(
                kb_path, commit_hash, stored_path, current_rel_path, entry_id
            )
            if verdict == git_service.REFUSED:
                self.db.delete_entry_version(entry_id, kb_name, commit_hash)

    def index_with_attribution(
        self,
        kb_name: str,
        git_service: Any = None,
        since_commit: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> int:
        """
        Index a KB with git attribution.

        The rows come from ``reconcile_kb`` like every other index path; for
        each file whose history is read:
        1. Its row is written by the reconcile, with the attribution below
        2. git log --follow -> populate entry_version table
        3. Set entry.created_by = first commit author
        4. Set entry.modified_by = last commit author

        If since_commit is provided, only process changed files.

        Args:
            kb_name: KB to index
            git_service: GitService instance (or duck-typed equivalent)
            since_commit: Only process files changed since this commit
            progress_callback: Optional callback(current, total)

        Returns:
            Number of entries whose history was read
        """
        if git_service is None:
            raise ValueError("git_service is required for index_with_attribution")

        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            raise ValueError(f"KB '{kb_name}' not found in config")

        kb_path = kb_config.path
        is_git = git_service.is_git_repo(kb_path)

        # Which files get their git history read: every file, or the ones git
        # says changed since `since_commit`. The reconcile itself covers the
        # whole KB either way, so a file deleted since then loses its row.
        force: bool | set[Path] = True
        if since_commit and is_git:
            force = set()
            # `git diff --name-only` prints repo-root-relative paths, so a KB
            # in a repo subdirectory must strip its own prefix first, as
            # `get_file_log` does: joined raw, `repo/kb/` + `kb/b.md` names
            # nothing and every changed file is skipped (#898).
            kb_prefix = git_service.get_kb_prefix(kb_path)
            for repo_path in git_service.get_changed_files(kb_path, since_commit=since_commit):
                rel_path = git_service._kb_relative_path(repo_path, kb_prefix)
                if rel_path is None:  # another KB's file in the same repo
                    continue
                full_path = kb_path / rel_path
                if full_path.exists() and full_path.suffix == ".md":
                    force.add(full_path)

        attributed = 0

        def enrich(entry: Entry, file_path: Path, data: dict[str, Any]):
            nonlocal attributed
            if force is not True and file_path not in force:
                return None
            attributed += 1
            if progress_callback:
                progress_callback(attributed, 0)
            if not is_git:
                return None
            rel_path = str(file_path.relative_to(kb_path))
            walked = git_service.get_file_log(kb_path, rel_path)
            log_entries = _same_entry_history(entry.id, rel_path, walked, kb_path, git_service)
            if log_entries:
                # First commit = created_by, last commit = modified_by
                data["created_by"] = log_entries[-1]["author_name"]
                data["modified_by"] = log_entries[0]["author_name"]

            def write_versions() -> None:
                # entry_version is derived from git, so a recompute removes
                # what is stale, but only a row the version read would refuse
                # (#453). Not "every row outside the walk": the walk misses
                # renames git does not detect, and a row `record_commit` adds
                # concurrently is servable, so a servability check (the
                # read's own rule) spares both. An empty walk learned nothing.
                if walked:
                    self._drop_unservable_versions(
                        entry.id,
                        kb_name,
                        kb_path,
                        rel_path,
                        {e["hash"] for e in log_entries},
                        git_service,
                    )
                # After the row (entry_version's FK). Each commit's file_path
                # is the KB-relative path this entry had *at that commit* (its
                # own tree), not necessarily its current path -- get_file_log
                # resolves this per commit via --name-status so a pre-rename
                # commit stays readable at the name it actually had (#432).
                # Stored KB-relative, not absolute: an absolute path breaks the
                # moment the KB's directory moves.
                for i, log_entry in enumerate(log_entries):
                    change_type = "created" if i == len(log_entries) - 1 else "modified"
                    self.db.upsert_entry_version(
                        entry_id=entry.id,
                        kb_name=kb_name,
                        commit_hash=log_entry["hash"],
                        author_name=log_entry["author_name"],
                        author_email=log_entry["author_email"],
                        commit_date=log_entry["date"],
                        message=log_entry["message"],
                        change_type=change_type,
                        file_path=log_entry.get("file_path", rel_path),
                    )

            return write_versions

        self.reconcile_kb(kb_config, force=force, enrich=enrich)
        return attributed


def create_index(config: PyriteConfig | None = None) -> IndexManager:
    """Create an IndexManager with default configuration."""
    config = config or load_config()
    db = PyriteDB(config.settings.index_path)
    return IndexManager(db, config)

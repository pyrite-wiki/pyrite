"""
Index Manager - Syncs File Storage with SQLite Index

Handles indexing entries from file-based KBs into the SQLite FTS database.
Supports incremental updates based on file modification times.
"""

import hashlib
import logging
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import KBConfig, PyriteConfig, load_config
from ..models import Entry
from ..models.core_types import id_text
from ..models.protocols import (
    PROTOCOL_COLUMN_KEYS,
    Assignable,
    Locatable,
    Prioritizable,
    Statusable,
    Temporal,
)
from ..utils.metadata import parse_metadata
from .database import PyriteDB
from .repository import KBRepository

logger = logging.getLogger(__name__)

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


def _hash_file(file_path: Path) -> str | None:
    """SHA-256 of a file's raw bytes, or None if it can't be read.

    Used for content-based staleness detection: mtime alone misses
    same-second edits and coarse-resolution filesystems.
    """
    try:
        return hashlib.sha256(file_path.read_bytes()).hexdigest()
    except OSError:
        return None


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
    holders: dict[str, list[_Claim]] = field(default_factory=dict)
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
        data = {
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
            "tags": entry.tags,
            "aliases": entry.aliases,
            "sources": [s.to_dict() for s in entry.sources],
            "links": [l.to_dict() for l in entry.links],
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

        # Keys that map to actual DB columns (always excluded from metadata)
        db_column_keys = {
            "id",
            "type",
            "title",
            "body",
            "summary",
            "tags",
            "sources",
            "links",
            "provenance",
            "metadata",
            "created_at",
            "updated_at",
        }
        # Also exclude keys that were explicitly set in data above
        stored_keys = db_column_keys | set(data.keys())
        try:
            fm = entry.to_frontmatter()
            ext_fields = {k: v for k, v in fm.items() if k not in stored_keys}
            # Merge in the explicit metadata dict if present
            if hasattr(entry, "metadata") and entry.metadata:
                ext_fields.update(entry.metadata)
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
        if scannable_body:
            existing_targets = {l.get("target") for l in data["links"]}
            for match in _WIKILINK_RE.finditer(scannable_body):
                kb_prefix = match.group(1)  # Optional kb: prefix
                target = match.group(2).strip()
                heading = match.group(3)
                block_id = match.group(4)
                note = ""
                if heading:
                    note = f"#{heading}"
                elif block_id:
                    note = f"^{block_id}"
                if (
                    target
                    and _is_valid_link_target(target)
                    and target != entry.id
                    and target not in existing_targets
                ):
                    data["links"].append(
                        {
                            "target": target,
                            "kb": kb_prefix or kb_name,
                            "relation": "wikilink",
                            "note": note,
                        }
                    )
                    existing_targets.add(target)

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
            existing_targets = {l.get("target") for l in data["links"]}
            for ref in refs:
                ref_str = str(ref).strip()
                if not ref_str:
                    continue
                if ":" in ref_str and not ref_str.startswith("http"):
                    ref_kb, ref_id = ref_str.split(":", 1)
                else:
                    ref_kb, ref_id = kb_name, ref_str
                if ref_id and ref_id != entry.id and ref_id not in existing_targets:
                    data["links"].append(
                        {
                            "target": ref_id,
                            "kb": ref_kb,
                            "relation": "references",
                            "note": "",
                        }
                    )
                    existing_targets.add(ref_id)

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
                if (
                    target
                    and _is_valid_link_target(target)
                    and target != entry.id
                    and target not in existing_targets
                ):
                    data["links"].append(
                        {
                            "target": target,
                            "kb": kb_prefix or kb_name,
                            "relation": "transclusion",
                            "note": note,
                        }
                    )
                    existing_targets.add(target)

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
    ) -> ReconcilePlan:
        """Read the files and decide which file holds each id. Writes nothing.

        Files are walked in KB-relative path order. A file the index already
        knows at that path, whose recorded stat still matches
        (``_file_changed``), is not read: its row's id stands for it. Every
        other file is parsed -- new paths, changed files, and the losing copies
        of a duplicate, which never have a row -- and one that fails to parse
        is listed in ``malformed``. ``force`` (True, or a set of paths) parses
        those files whatever their stat says.
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
                stat = file_path.stat()
            except OSError:
                continue  # removed while walking: as if never seen
            known = path_to_row.get(str(file_path))
            must_read = force is True or (forced is not None and file_path in forced)
            if known and not must_read and not _file_changed(known[1], stat):
                claim = _Claim(rel, file_path, known[0], stat)
            else:
                try:
                    entry = repo.load_entry_from_file(file_path)
                except Exception as e:
                    # An unparseable file is content drift, not a Pyrite bug:
                    # reported in the result, one log line, and it holds no id.
                    plan.malformed.append({"path": str(file_path), "error": str(e)})
                    logger.warning("Could not parse %s: %s", file_path, e)
                    continue
                claim = _Claim(rel, file_path, id_text(entry.id), stat, entry)
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
        (``{"path", "error"}``), ``duplicates`` (``{"kb", "id", "winner",
        "paths"}``, KB-relative paths, the winner first) and ``written`` (rows
        written, counted or not).
        """
        results: dict[str, Any] = {
            "added": 0,
            "updated": 0,
            "removed": 0,
            "malformed": [],
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
        plan = self.plan_reconcile(kb_config, force=force, indexed=indexed)
        results["malformed"] = plan.malformed
        results["duplicates"] = plan.duplicates

        total = len(plan.winners)
        unwritable: set[str] = set()
        for done, (entry_id, claim) in enumerate(sorted(plan.winners.items()), start=1):
            if claim.entry is not None:
                try:
                    self._write_claim(
                        kb_config.name, claim, indexed.get(entry_id), force, enrich, results
                    )
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

    def check_staleness(self) -> list[dict[str, Any]]:
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
        """
        stale: list[dict[str, Any]] = []

        for kb in self.config.all_kbs():
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
          KB's `kb.yaml` types (and isn't a core type). Aggregated per
          `(kb, type)` with a count. KBs without a `kb.yaml` are skipped
          since there's no declared-type list to enforce.
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
        health = {
            "missing_files": [],
            "unindexed_files": [],
            "stale_entries": [],
            "content_changed": [],
            "broken_links": 0,
            "undeclared_types": [],
            "missing_required_fields": [],
            "subdirectory_mismatches": [],
            "malformed_frontmatter": [],
            "invalid_statuses": [],
            "off_list_values": [],
            "duplicates": [],
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

        for kb in kbs:
            if not kb.path.exists():
                continue

            indexed = self._load_indexed_state(kb.name)
            # The reconcile's own reading of the files (every file parsed):
            # the same winner per id, the same duplicates, the same staleness
            # rule as `pyrite index sync` (ADR-0038 step 2).
            plan = self.plan_reconcile(kb, force=True, indexed=indexed)
            health["duplicates"].extend(plan.duplicates)
            for bad in plan.malformed:
                health["malformed_frontmatter"].append({"kb": kb.name, **bad})

            for entry_id, claim in sorted(plan.winners.items()):
                file_path = claim.path
                entry = claim.entry
                source = getattr(entry, "_source_frontmatter", None) or {}
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

            # Check for missing files
            for entry_id, info in indexed.items():
                if not Path(info["file_path"]).exists():
                    health["missing_files"].append(
                        {"kb": kb.name, "id": entry_id, "path": info["file_path"]}
                    )

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

        # The schema owns the type vocabulary; an empty declaration is
        # unrestricted, including plugin types and core types.
        for kb in kbs:
            if not kb.path.exists() or not kb.kb_yaml_path.exists():
                continue

            declared = set(kb.kb_schema.declared_types())
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

        # Required-field + subdirectory checks. Per KB with a kb.yaml,
        # compare each entry against its type's `required:` list (default
        # ["title"]) and its `subdirectory:` hint. Skip KBs with no kb.yaml.
        for kb in kbs:
            if not kb.path.exists() or not kb.kb_yaml_path.exists():
                continue
            kb_schema = kb.kb_schema
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
                self._check_off_list_values(kb, health, file_values, validators_for(kb))

        return health

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

        kb_schema = kb.kb_schema if kb.kb_yaml_path.exists() else None
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
    ) -> dict[str, Any]:
        """Reconcile one KB, or every configured KB, reading only files whose
        stat moved and paths the index does not know (``reconcile_kb``).

        Returns ``added``, ``updated``, ``removed``, ``malformed`` and
        ``duplicates`` summed over the KBs. The CLI prints the malformed and
        duplicate files as a summary rather than per-file tracebacks (Tier A
        1080).
        """
        results: dict[str, Any] = {
            "added": 0,
            "updated": 0,
            "removed": 0,
            "malformed": [],
            "duplicates": [],
        }
        kbs = [self.config.get_kb(kb_name)] if kb_name else self.config.all_kbs()
        kbs = [kb for kb in kbs if kb and kb.path.exists()]

        for n, kb in enumerate(kbs, start=1):
            callback = None
            if progress_callback and len(kbs) == 1:
                callback = progress_callback
            one = self.reconcile_kb(kb, progress_callback=callback)
            for key in ("added", "updated", "removed"):
                results[key] += one[key]
            results["malformed"].extend(one["malformed"])
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
            for rel_path in git_service.get_changed_files(kb_path, since_commit=since_commit):
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
            log_entries = _same_entry_history(
                entry.id,
                rel_path,
                git_service.get_file_log(kb_path, rel_path),
                kb_path,
                git_service,
            )
            if log_entries:
                # First commit = created_by, last commit = modified_by
                data["created_by"] = log_entries[-1]["author_name"]
                data["modified_by"] = log_entries[0]["author_name"]

            def write_versions() -> None:
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

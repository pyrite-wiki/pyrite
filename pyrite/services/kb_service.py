"""
Knowledge Base Service

Unified KB operations used by API, CLI, and UI layers.
"""

from __future__ import annotations

import copy
import dataclasses
import logging
import os
import re
import shlex
import stat
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

if TYPE_CHECKING:
    from .kb_registry_service import KBRegistryService

from ..config import KBConfig, PyriteConfig
from ..exceptions import (
    AmbiguousEntryError,
    ConflictError,
    EntryExistsError,
    EntryNotFoundError,
    IndexSyncRecoveryHintError,
    KBNotFoundError,
    KBReadOnlyError,
    PyriteError,
    SchemaViolationError,
    StorageBusyError,
    StorageError,
    UndeclaredTypeError,
    ValidationError,
    public_text,
)
from ..models import Entry
from ..models.base import _timestamp_instant
from ..models.core_types import as_stored_text, id_text
from ..models.factory import build_entry
from ..plugins.context import PluginContext
from ..schema import Link, Source
from ..schema.enum_check import is_off_list, is_per_element
from ..schema.reserved import RESERVED_FIELD_NAMES
from ..storage.database import PyriteDB
from ..storage.document_manager import DocumentManager
from ..storage.entry_writer import (
    FileChanged,
    create_entry_text,
    publish_entry_text,
    raw_scalar,
    updated_at_stamp,
)
from ..storage.file_operations import (
    AddSubkey,
    Append,
    FrontmatterSpan,
    OperationRefusedError,
    Remove,
    ReplaceBody,
    Set,
    TimestampText,
    Unset,
    apply,
    reason_for_read,
    unmodellable_in_text,
)
from ..storage.index import IndexManager, content_hash
from ..storage.repository import KBRepository
from ..utils.atomic_write import HardLinked
from ..utils.file_lock import LOCK_TIMEOUT, LockDirError, LockTimeout
from ..utils.frontmatter import Frontmatter, NoFrontmatter, describe, split_frontmatter
from ..utils.kb_files import KBFiles
from ..utils.metadata import declared_over_bag, parse_metadata
from ..utils.yaml import load_yaml
from .access_policy import named_kb
from .body_bounds import MARKER_KEYS, ensure_not_truncated
from .export_service import ExportService
from .hook_runner import HookRunner
from .wikilink_service import WikilinkService

logger = logging.getLogger(__name__)

#: Entry attributes a caller's field update never sets, on any type: identity,
#: storage location and structure the service owns. A type adds its own through
#: ``Entry.managed_fields`` (a task's audit trail, an ADR's number).
_MANAGED_FIELDS = frozenset(
    {
        "id",
        "kb_name",
        "file_path",
        "links",
        "sources",
        "provenance",
        "extra_frontmatter",
    }
)

#: Timestamps a caller may set only by naming them (`--field updated_at=...`,
#: REST PATCH, #151). They are left out of ``updatable_fields``, because a read
#: result carries them and an agent echoing it back would freeze them.
_TIMESTAMP_FIELDS = frozenset({"created_at", "updated_at"})

#: How long an update waits for the entry's momentary lock before it is a
#: ``StorageBusyError`` (the lock's own default; a test shortens it).
_LOCK_TIMEOUT = LOCK_TIMEOUT

#: What an HTML ``datetime-local`` field sends: a date, ``T``, hours and
#: minutes, no seconds. YAML reads it as a string; it is accepted and written
#: with ``:00`` seconds, a value YAML reads as a timestamp (the maintainer on
#: #991). Every other form is accepted iff the loader reads it as a date or
#: timestamp (``_yaml_reads_a_timestamp``).
_DATETIME_LOCAL = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d")


def _yaml_reads_a_timestamp(spelled: str) -> bool:
    """Does the loader read ``spelled``, written bare, as a date or a
    timestamp? The one rule for what a caller may send for a timestamp key
    (#990): it is written as sent, so it must read back as one. Asked of the
    loader itself, not of a regex beside it (the cold read of #991: a regex
    let ``2026-1-5`` and ``+0530`` through, which ruamel reads as strings)."""
    from collections.abc import Mapping

    if not spelled or "\n" in spelled or "\r" in spelled or "#" in spelled:
        return False
    try:
        loaded = load_yaml(f"k: {spelled}")
    except Exception:  # noqa: BLE001 - a value YAML cannot parse is no timestamp
        return False
    return (
        isinstance(loaded, Mapping)
        and list(loaded) == ["k"]
        and isinstance(loaded["k"], date | datetime)
    )


#: Keys a read result (`kb_get`, `GET /api/entries/{id}`) carries that are the
#: index's, not the entry's: an update never writes them. With the managed
#: fields, the timestamps and the type, they are what
#: :meth:`KBService.split_echoed_update` sets aside when a caller sends a whole
#: read result back (#455).
_INDEX_ONLY_KEYS = frozenset(
    {
        "entry_type",
        "type",
        "indexed_at",
        "content_hash",
        "created_by",
        "modified_by",
        "outlinks",
        "backlinks",
    }
)

#: Keys only a read result carries -- the index's own and the entry's
#: identity. A request holding one is an echoed read result, whose other keys
#: are Pyrite's reading unless the caller changed them (#561).
_READ_RESULT_MARKERS = (_INDEX_ONLY_KEYS - {"type"}) | {"id", "file_path"}

#: Two different spellings of "no relation was ever named" that must compare
#: equal in a link's duplicate key. Every write surface (CLI `link`, MCP
#: `kb_link`, `add_link`'s own default, `add_links`' bulk path) defaults an
#: omitted relation to ``related_to``. But a file written before #396 --  or
#: by hand -- with `links: [{target: b}]` and no `relation:` key at all loads
#: through `Link.from_dict`, whose *own* default is the older ``related``
#: (round-1 cold read). Comparing the raw strings in the duplicate check
#: treated those as different relations: `pyrite link a b` with no `-r` on
#: such a file added a second link where `dev` did nothing. `_norm_relation`
#: is the one place both spellings collapse to the same key.
_LEGACY_DEFAULT_RELATION = "related"
_DEFAULT_RELATION = "related_to"


def _norm_relation(relation: str) -> str:
    """Canonicalise a relation for the link duplicate-key comparison only.

    Never used to decide what gets *written* -- a link is saved with the
    caller's or the file's relation exactly as given, never rewritten to this
    canonical form, so this cannot silently migrate legacy data on save.
    """
    return _DEFAULT_RELATION if relation == _LEGACY_DEFAULT_RELATION else relation


#: What one link of a file's `links:` must hold for the index insert to store
#: it, field by field of the mapping ``Link.to_dict()`` hands to
#: ``_sync_links`` (``pyrite/storage/backends/sqlite_backend.py``). A field is
#: refused when it is null where the column is NOT NULL, or when it is a list
#: or mapping -- unhashable in the row's key ``(target, kb, relation)``, or a
#: value SQLite cannot bind (``note``). ``note`` and ``kb`` reach the insert
#: only when truthy (``to_dict`` drops them otherwise), so a null or empty one
#: is stored as the default. Anything else is a scalar SQLite stores as the
#: file spells it, read back as text (``true`` becomes ``'1'``).
_LINK_INSERT_RULES: dict[str, bool] = {
    # field: refuse when null?
    "target": True,  # target_id NOT NULL; part of the key
    "relation": True,  # relation NOT NULL; part of the key
    "note": False,  # dropped when falsy; bound as a value
    "kb": False,  # dropped when falsy (defaults to the source KB); part of the key
}


def check_link_target(link: dict[str, Any]) -> None:
    """Refuse a file's link exactly when the index insert cannot store it (#846).

    The rule for links that arrive in a file or a frontmatter mapping
    (``add``, a create's own ``links`` field, ``import``, MCP
    ``kb_bulk_create``): ``link`` is ``Link.to_dict()``, what ``_sync_links``
    receives, and it is refused for a null ``target`` or ``relation`` or a
    list or mapping in any field (``_LINK_INSERT_RULES``). That is exactly the
    set ``index sync`` reports as malformed, so ``add``, ``index sync`` and
    ``update`` give one file one answer (principle 2; fix round 2). A blank
    target or relation, a number, a boolean or a date is accepted and the
    file is not rewritten. Before this, such a link was written and then the
    index insert failed, after the file was on disk.
    """
    for key, refuse_null in _LINK_INSERT_RULES.items():
        if key not in link:
            continue
        value = link[key]
        if value is None and refuse_null:
            raise ValidationError(f"A link's {key} is empty (null); the index cannot store it.")
        if isinstance(value, list | tuple | set | dict):
            raise ValidationError(
                f"A link's {key} is a {type(value).__name__}, not a single value; "
                "the index cannot store it."
            )


def _shown(value: Any) -> str:
    """A non-text target as the caller likely typed it, and what YAML made of it."""
    import datetime as _dt

    if isinstance(value, bool):
        return (
            f"{value!r} (YAML reads yes/no/on/off/true/false as a boolean; "
            f'quote it, e.g. target: "yes")'
        )
    if isinstance(value, int | float):
        # Not echoed back as the example: YAML read `010` as 8, so "8" would
        # be the wrong id. The caller knows what they typed.
        return (
            f"{value!r} (a number; YAML reads 010 as 8 and 1e3 as 1000.0; "
            'quote the value as you wrote it, e.g. target: "2024")'
        )
    if isinstance(value, _dt.date):
        return f'{value.isoformat()} (a date; quote it, e.g. target: "{value.isoformat()}")'
    return repr(value)


def check_link_spec(
    target: Any,
    relation: Any = None,
    *,
    note: Any = None,
    target_kb: Any = None,
) -> tuple[str, str, str]:
    """The check a link the caller is creating passes before anything is written (#846).

    For the explicit link-creation surfaces only: ``add_link`` (CLI ``link``,
    MCP ``kb_link``), ``add_links`` (``links bulk-create``) and ``pyrite
    create --link``, which calls it for every spec before it creates the
    entry. There the caller typed the link, so it must say what it means:
    the target must be non-blank text -- a number, boolean or date (what
    YAML or JSON made of an unquoted value: ``yes`` is true, ``010`` is 8) is
    refused with a hint to quote it, never converted, since converting
    silently changes what was typed. A relation that is given must be
    non-blank text; a note and a ``target_kb`` must be text.

    Returns ``(target, relation, note)`` as they will be written: a relation
    left out (``None``) is ``related_to``, a note left out is ``""``.

    Never on read, and never on a file's own links: those get
    :func:`check_link_target`, which refuses only what the index cannot store
    (``kb/design.md``, principle 2). A ``:`` in a relation is accepted: it is
    ambiguous only in the CLI's ``--link target:relation`` syntax, whose
    parser refuses a third part.
    """
    if target is None or (isinstance(target, str) and not target.strip()):
        raise ValidationError(f"A link needs a target entry id; got {target!r}.")
    if not isinstance(target, str):
        raise ValidationError(f"A link target must be text; got {_shown(target)}.")
    if relation is None:
        relation = _DEFAULT_RELATION
    elif not isinstance(relation, str) or not relation.strip():
        raise ValidationError(
            f"A link's relation must be non-empty text; got {relation!r}. "
            f"Leave it out for {_DEFAULT_RELATION}.",
        )
    if note is None:
        note = ""
    elif not isinstance(note, str):
        raise ValidationError(f"A link's note must be text; got {type(note).__name__}.")
    if target_kb is not None and not isinstance(target_kb, str):
        raise ValidationError(f"A link's target_kb must be a KB name; got {target_kb!r}.")
    return target, relation, note


def _same_target(existing: Any, target: str) -> bool:
    """The duplicate key's target comparison: as the text the index stores
    (``as_stored_text``, the conversion the link sync keys on), so a hand
    file's ``target: 2024`` and a typed ``"2024"`` are one link, and so are
    ``target: true`` and ``"1"``, as they are one row in the index. ``str()``
    spelled ``true`` as ``'True'`` and wrote a second item (#1022)."""
    return bool(as_stored_text(existing) == as_stored_text(target))


@dataclass
class WriteResult:
    """What a write produced: the saved entry and the schema warnings it drew.

    Warnings are the non-blocking findings of the same validation that refuses
    a write (an unknown select value when the KB does not enforce, a missing
    optional source...). Every surface can now return them (#378).
    """

    entry: Entry
    warnings: list[dict[str, Any]] = field(default_factory=list)
    #: False when the request changed nothing: nothing was written and the
    #: file's mtime is as it was (ADR-0042 decision 3). Always True for a create.
    changed: bool = True
    #: False when the file was replaced with compare-before-replace and no
    #: lock, because its KB is not in a git repository (D1 (b), #955).
    locked: bool = True
    #: Why an update of a templated-folder field did not move the file
    #: (ADR-0042 decision 5: "not moved: no id pin"), else None.
    not_moved: str | None = None


@dataclass
class ReconcileResult:
    """Planned directory moves and per-entry findings from index reconciliation."""

    moves: list[tuple[str, Path, Path]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    moved: int = 0
    read_only: bool = False


def _safe_message(exc: Exception) -> str:
    """The message a client-facing body may show for ``exc``.

    ADR-0037 theme 2 round 2 (conductor cold read of 5d65caa7, item 1): a
    ``StorageError``/``PluginError``/``ConfigError`` (or a subclass that
    doesn't set its own) can carry server-side detail in ``str(exc)`` -- a
    real filesystem path, a driver's own text. ``public_message``, when the
    class sets one, is what REST's central handler (``server/errors.py``)
    and MCP's ``_refusal`` already show instead; every other client-facing
    body this module builds by hand (bulk per-item results, a batch
    write-back's per-position error, a publish's ``push_error``) should use
    this too, rather than ``str(exc)`` directly. Logs the real detail
    server-side when it masks it.
    """
    return public_text(exc, log=logger)


def _storage_error(entry_id: str, e: OSError) -> StorageError:
    """A write's filesystem fault as the ``StorageError`` the surfaces show.
    ``str()`` keeps the detail for the log (``public_text`` logs it, ADR-0037
    §3). The two faults a caller can act on carry their reason as the public
    message, with no path in it (#975 review): a file with more than one
    hard link, and a lock directory that cannot be used."""
    err = StorageError(f"Cannot write '{entry_id}': {e}")
    if isinstance(e, HardLinked):
        err.public_message = (
            f"Cannot write '{entry_id}': its file has {e.links} hard links. Another "
            "name for the same file may be outside the KB, and would take a different "
            "lock. Break the link (copy the file over itself), then retry."
        )
    elif isinstance(e, LockDirError):
        err.public_message = (
            f"Cannot write '{entry_id}': the lock directory of the repository holding it "
            "cannot be used (a `.git` file that points at no repository, or a directory "
            "that cannot be created there); the server log names it."
        )
    return err


def _refusal_result(exc: Exception) -> dict[str, Any]:
    """A per-item failure in the bulk result shape, with its stable code."""
    if isinstance(exc, PyriteError):
        code = getattr(exc, "error_code", None) or "CREATE_FAILED"
    else:
        code = "CREATE_FAILED"
    return {"created": False, "error": _safe_message(exc), "error_code": code}


class KBService:
    """
    Service for KB operations.

    Provides:
    - KB listing and stats
    - Entry CRUD with proper type handling
    - Index synchronization
    """

    def __init__(
        self,
        config: PyriteConfig,
        db: PyriteDB,
        doc_mgr: DocumentManager | None = None,
        registry: KBRegistryService | None = None,
    ):
        self.config = config
        self.db = db
        self._index_mgr = IndexManager(db, config)
        self._doc_mgr = doc_mgr or DocumentManager(db, self._index_mgr)
        self._export_svc = ExportService(config, db)
        self._registry: KBRegistryService | None = registry
        self._embedding_svc = None
        self._embedding_checked = False
        self._embedding_worker = None  # Set externally to enable queue-based embedding
        self._wikilink_svc: WikilinkService | None = None

        # Hook orchestration. HookRunner owns BOTH core-hook and plugin-hook
        # dispatch under one raise/swallow contract (#379); _run_hooks below
        # is a one-line delegation. The lazy `from ..plugins import
        # get_registry` here (rather than a module-level import) is
        # deliberate, not a leftover: tests patch `pyrite.plugins.get_registry`
        # with `unittest.mock.patch`, which only intercepts a *fresh* lookup
        # of that name -- a module-level `from ..plugins import get_registry`
        # binds this module's own reference at import time, which the patch
        # cannot reach, so construction would silently keep using the real
        # registry. `get_registry()` itself is a lazy singleton (same
        # instance every call in production), so this costs nothing but the
        # one attribute lookup.
        from ..plugins import get_registry

        self.hook_runner = HookRunner(plugin_registry=get_registry())
        # Register the platform-level core hooks. The task-system ones live in
        # task_service.py — register_task_hooks is the explicit entry point so
        # the cross-service dependency is visible at the call site rather than
        # buried in a module-level _CORE_HOOKS dict.
        from .task_service import register_task_hooks

        register_task_hooks(self.hook_runner)

    def _get_embedding_svc(self):
        """Lazy-load embedding service if available."""
        if self._embedding_checked:
            return self._embedding_svc
        self._embedding_checked = True
        if not getattr(self.config.settings, "auto_embed", True):
            return None
        try:
            from .embedding_service import EmbeddingService, is_available

            if is_available() and self.db.vec_available:
                self._embedding_svc = EmbeddingService(
                    self.db, model_name=self.config.settings.embedding_model
                )
        except Exception:
            logger.warning("Embedding service initialization failed", exc_info=True)
        return self._embedding_svc

    @staticmethod
    def _validated_fields(entry: Entry) -> dict[str, Any]:
        """The field set a write is validated on.

        A typed entry keeps fields its model does not declare under a nested
        `metadata:` block, which is where build_entry puts a caller's extra
        kwargs. The schema declares them for the type all the same, so they
        are validated as the fields they are; a top-level key wins a clash.
        """
        fields = entry.declared_frontmatter()
        nested = fields.get("metadata")
        if isinstance(nested, dict):
            fields = {**nested, **fields}
        return fields

    @staticmethod
    def _keep_on_disk_enum_values(
        errors: list[dict[str, Any]], before: dict[str, Any], after: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """``(errors, warnings)`` after excepting enum values already on disk (#47, #555).

        An off-list error (a kb.yaml enum or a plugin's ``rule: enum``; never
        a type error, see ``enum_check.is_off_list``) on a field whose value
        is the same after the update as before is a value the file already
        had: reported, not re-validated, so an update that does not change it
        succeeds. The comparison is by value, so a caller echoing a whole read
        back is covered. For a list-valued field (``enum_check.is_per_element``)
        it is per element: elements already present stay allowed, an added
        off-list element is still refused. A select keeps its off-list value
        only by sending the same value. Every other error is left as it is.
        """
        kept: list[dict[str, Any]] = []
        excepted: list[dict[str, Any]] = []
        note = "value already on disk; kept, not re-validated"
        for err in errors:
            name = err.get("field")
            if not is_off_list(err) or name not in before:
                kept.append(err)
                continue
            old, new = before.get(name), after.get(name)
            if old == new:
                excepted.append({**err, "severity": "warning", "note": note})
                continue
            got = err.get("got")
            if is_per_element(err, old, new) and isinstance(new, list) and isinstance(got, list):
                had = old if isinstance(old, list) else [old]
                existing = [v for v in got if v in had]
                added = [v for v in got if v not in had]
                if existing:
                    excepted.append({**err, "got": existing, "severity": "warning", "note": note})
                if added:
                    kept.append({**err, "got": added})
                continue
            kept.append(err)
        return kept, excepted

    def _schema_result(
        self,
        entry: Entry,
        fields: dict[str, Any],
        kb_name: str,
        kb_config: KBConfig,
        existing_entry: Entry | None,
    ) -> dict[str, Any] | None:
        """The KB schema's and plugin validators' verdict on ``fields``, or
        ``None`` when a validator itself raised (validation is then skipped,
        with a log line, as it always was)."""
        try:
            kb_schema = kb_config.kb_schema
            return kb_schema.validate_entry(
                entry.entry_type,
                fields,
                context={
                    "kb_name": kb_name,
                    "kb_schema": kb_schema,
                    "kb_type": kb_config.kb_type,
                    "_schema_version": getattr(entry, "_schema_version", 0),
                    "existing_entry": copy.deepcopy(existing_entry),
                },
            )
        except Exception:
            logger.warning("Schema validation skipped for %s/%s", kb_name, entry.id, exc_info=True)
            return None

    @staticmethod
    def _keep_on_disk_violations(
        errors: list[dict[str, Any]],
        prior: list[dict[str, Any]],
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """``(errors, warnings)`` after excepting violations the file already had.

        ADR-0042 decision 8, #676: a write is refused only for a violation it
        causes. A finding is the file's, not the write's, when the loaded
        entry has the same finding (field and rule) and the field's value is
        the same after the write -- the request did not change it. It comes
        back as a warning. A finding the write introduces (a cross-field rule
        its change triggers, a field set to another bad value) stays an error.
        A finding with no ``field`` cannot be matched to a request key, so it
        always stays an error.
        """
        had = {(e.get("field"), e.get("rule")) for e in prior if e.get("field")}
        kept: list[dict[str, Any]] = []
        excepted: list[dict[str, Any]] = []
        for err in errors:
            name = err.get("field")
            if name and (name, err.get("rule")) in had and before.get(name) == after.get(name):
                excepted.append(
                    {
                        **err,
                        "severity": "warning",
                        "note": "violation already on disk, in a field this write did not "
                        "change; kept, not re-validated",
                    }
                )
            else:
                kept.append(err)
        return kept, excepted

    def _validate_write(
        self,
        entry: Entry,
        kb_name: str,
        kb_config: KBConfig,
        before: dict[str, Any] | None = None,
        existing_entry: Entry | None = None,
    ) -> list[dict[str, Any]]:
        """Refuse a write the KB schema or a plugin validator rejects.

        The same rules `index health` and `schema validate` report after the
        fact (enum, required, min/max, pattern) are applied before the file is
        written, on every surface. Without this, `update -f status=bogus`
        succeeded and drifted the board (75 items once sat on an undeclared
        status). No kb.yaml means no schema to enforce; plugin validators for
        the KB type still run through validate_entry.

        ``before`` is an update's field set as loaded, before the update was
        applied (:meth:`_validated_fields` of the loaded entry); with it, an
        off-list enum value the update leaves unchanged is a warning, not a
        refusal (:meth:`_keep_on_disk_enum_values`). Create passes none and
        is always strict.
        """
        fields = self._validated_fields(entry)
        result = self._schema_result(entry, fields, kb_name, kb_config, existing_entry)
        if result is None:  # a broken validator must not make every write fail
            return []
        errors = result.get("errors") or []
        warnings = list(result.get("warnings") or [])
        if before is not None and errors:
            errors, excepted = self._keep_on_disk_enum_values(errors, before, fields)
            warnings.extend(excepted)
        if before is not None and errors and existing_entry is not None:
            prior = self._schema_result(existing_entry, before, kb_name, kb_config, None)
            errors, excepted = self._keep_on_disk_violations(
                errors, (prior or {}).get("errors") or [], before, fields
            )
            warnings.extend(excepted)
        if not errors:
            return warnings
        parts = []
        for e in errors:
            field = e.get("field", "?")
            rule = e.get("rule", "")
            got = e.get("got")
            expected = e.get("expected")
            message = e.get("message")
            if is_off_list(e) and isinstance(expected, list):
                # Every declared-enum refusal reads the same way, naming the
                # field, the value and the allowed list (#555); the rule
                # identifiers themselves are unchanged.
                rendered = f"{field}: {got!r} is not one of {expected}"
                # A validator's `message` can say more than the generic
                # "Invalid <field>: <got>" (e.g. explaining why an enum only
                # applies conditionally) -- append it when it does, same
                # spirit as the `required` case below.
                generic = f"Invalid {field}: {got}"
                if message and message != generic:
                    rendered += f" ({message})"
                parts.append(rendered)
            elif rule == "required":
                # A conditional `required` rule (e.g. journalism's "amount is
                # required when transaction_type is payment") sets `message`
                # to explain *why*; dropping it for the bare "field: required"
                # loses the only useful part of the refusal (#429).
                parts.append(f"{field}: {message or 'required'}")
            elif message:
                # A rule this renderer does not know, or none at all (e.g.
                # journalism-investigation's "Importance must be 1-10, got: 99"): the
                # validator's own message beats "(expected None, got None)".
                parts.append(f"{field}: {message}" if field != "?" else message)
            else:
                parts.append(f"{field}: {rule} (expected {expected}, got {got!r})")
        raise SchemaViolationError(
            f"Invalid {entry.entry_type} for KB '{kb_name}': " + "; ".join(parts), errors
        )

    def _get_embedding_worker(self):
        """Lazy `EmbeddingWorker` over this service's index DB.

        Constructing one is a ``CREATE TABLE IF NOT EXISTS`` and nothing else:
        no thread, no torch import, ~50 ms cold (ADR-0035's spike). It is built
        here rather than assigned by each caller because #13 *was* the
        assign-it-yourself design -- ``_embedding_worker`` existed, nothing in
        production ever set it, so every write on every surface took the
        synchronous branch. Callers may still inject one by setting
        ``_embedding_worker`` directly; tests do.
        """
        if self._embedding_worker is not None:
            return self._embedding_worker
        if not self._queue_can_see_our_writes():
            return None
        try:
            from .embedding_worker import EmbeddingWorker

            self._embedding_worker = EmbeddingWorker(
                self.db, model_name=self.config.settings.embedding_model
            )
        except Exception:
            # A DB that cannot hold the queue (read-only, locked, out of disk)
            # must not fail the write: the entry is on disk and in the index
            # either way, and `pyrite index embed` re-derives what is missing
            # from the index rather than from the queue. exc_info because
            # "embed queue unavailable" without a cause leaves an operator
            # unable to tell a locked database from a full disk.
            logger.warning(
                "Embed queue unavailable for %s; run `pyrite index embed` to catch up",
                getattr(self.config.settings, "index_path", "?"),
                exc_info=True,
            )
            return None
        return self._embedding_worker

    def _queue_can_see_our_writes(self) -> bool:
        """Refuse to queue into a database that cannot see what we just wrote.

        ``WorktreeDB`` routes entry writes to a per-user *diff* DB while
        forwarding ``_raw_conn`` -- which is where ``embed_queue`` lives -- to
        **main**. Queuing through it files the debt in main's queue naming an
        entry only the diff DB holds. The drain then calls ``embed_entry``
        against main, which cannot find the entry; before the sibling fix in
        ``process_batch`` that returned ``False`` without raising and the row
        was deleted as though embedded.

        A worktree entry is embedded when its branch merges and the main index
        picks the file up, so skipping the queue here loses nothing; filing a
        row that names an unreachable entry loses the operator's trust in
        ``embed-status``.

        Checked structurally rather than by class name: ask the backend that
        *receives the writes* for its connection and compare it with the one
        ``embed_queue`` would be created on. A future overlay type gets the
        same protection without this method learning about it.
        """
        try:
            queue_conn = self.db._raw_conn
            backend = self.db.backend
            # OverlaySearchBackend sends writes to `_diff`; a plain backend is
            # its own write target.
            write_backend = getattr(backend, "_diff", backend)
            write_conn = getattr(write_backend, "_raw_conn", queue_conn)
        except Exception:
            logger.debug("Could not compare write/queue connections", exc_info=True)
            return True
        if write_conn is queue_conn:
            return True
        logger.debug(
            "Skipping embed queue: entry writes go to a different database "
            "than the one embed_queue lives on (overlay/worktree DB). The "
            "entry embeds from the main index once its branch merges."
        )
        return False

    def _auto_embed(self, entry_id: str, kb_name: str) -> None:
        """Record that an entry needs embedding. Never embeds inline.

        ADR-0035: ``auto_embed: true`` guarantees an entry **will be**
        embedded, not that it is embedded when the write returns. Loading the
        sentence-transformers model inside a write is what made the first
        ``POST /api/entries`` on a fresh install block for over a minute while
        it downloaded ~90 MB (#13).

        The debt is drained by callers who can afford to wait and who already
        exist: the server's startup prewarm hook, ``POST /api/index/sync``,
        and ``pyrite index embed`` / ``sync`` / ``build``. ``auto_embed:
        false`` still means *nothing happens at all* -- no queue row, no
        embedding stack touched (ADR-0035 §4).
        """
        if not getattr(self.config.settings, "auto_embed", True):
            return
        worker = self._get_embedding_worker()
        if worker is None:
            return
        try:
            worker.enqueue(entry_id, kb_name)
        except Exception as e:
            logger.warning("Could not queue %s for embedding: %s", entry_id, e, exc_info=True)

    @property
    def wikilinks(self) -> WikilinkService:
        """Lazy WikilinkService instance."""
        if self._wikilink_svc is None:
            self._wikilink_svc = WikilinkService(self.config, self.db)
        return self._wikilink_svc

    # =========================================================================
    # KB Operations
    # =========================================================================

    def list_kbs(self) -> list[dict[str, Any]]:
        """List all knowledge bases with stats. Delegates to registry if available."""
        if self._registry:
            return self._registry.list_kbs()
        kbs = []
        for kb in self.config.all_kbs():
            stats = self.db.get_kb_stats(kb.name)
            kbs.append(
                {
                    "name": kb.name,
                    "type": kb.kb_type,
                    "path": str(kb.path),
                    "description": kb.description,
                    "read_only": kb.read_only,
                    "entries": stats.get("entry_count", 0) if stats else 0,
                    "indexed": bool(stats.get("last_indexed")) if stats else False,
                    "last_indexed": stats.get("last_indexed") if stats else None,
                }
            )
        return kbs

    def get_kb(self, name: str) -> KBConfig | None:
        """Get KB config by name. Falls back to registry for DB-only KBs."""
        cfg = self.config.get_kb(name)
        if cfg:
            return cfg
        if self._registry:
            return self._registry.get_kb_config(name)
        return None

    def require_kb(self, name: str) -> KBConfig:
        """The KB ``name`` names, or the one refusal (#952).

        ``PyriteConfig.require_kb`` through this service's database: an
        unknown name or a shortname is ``KBNotFoundError`` with the
        resolver's suggestion, a blank name is ``ValidationError`` (the
        caller named no KB), and a KB registered after the config was
        loaded is found. Every service method that acts on one KB looks it
        up here, so no method answers an empty result for a name that is
        not a KB.
        """
        return self.config.require_kb(name, db=self.db)

    def resolve_kb(self, name: object, *, readable_kbs: set[str] | None) -> KBConfig | None:
        """The one resolver (``kb_resolver.resolve_kb``) through this service's
        database, for a surface with a scope: REST's ``authz`` and the MCP
        chokepoints ask here, so no surface holds a storage handle for it
        (``tests/test_layer_boundaries.py``). None when ``name`` names no KB.
        """
        from .kb_resolver import resolve_kb

        return resolve_kb(self.config, name, readable_kbs=readable_kbs, db=self.db)

    def get_kb_stats(self, name: str) -> dict[str, Any] | None:
        """Get stats for a specific KB."""
        return self.db.get_kb_stats(name)

    # =========================================================================
    # Entry Operations
    # =========================================================================

    def get_entry(
        self,
        entry_id: str,
        kb_name: str | None = None,
        *,
        readable_kbs: set[str] | None,
    ) -> dict[str, Any] | None:
        """
        Get entry by ID, with its outlinks and backlinks.

        If kb_name is omitted, searches readable KBs and refuses duplicate IDs.

        ``readable_kbs`` is the caller's ``ReadScope`` set (``None``:
        unscoped). With it, a lookup without a KB walks only readable KBs,
        so an entry in a KB the caller cannot read neither answers nor
        shadows a readable one with the same id (P-R5), and the links are
        computed over readable KBs only (P-R4). A named KB the caller
        cannot read is the caller's to refuse; here it reads as a miss. A
        blank ``kb_name`` names no KB (``access_policy.named_kb``).

        The entry is read from its file as it is now (``_read_entry``), so
        every surface that calls this -- CLI ``get``, ``pyrite-read get``,
        REST ``GET /api/entries/{id}``, MCP ``kb_get`` and ``kb_read_body``
        -- returns a hand edit before the next sync (#753). Without a KB,
        readable KBs are tried in config order, ids the index knows first.
        A read the index can answer (its row's file still holds the id) opens
        that one file and walks no directory; only a miss looks for files by
        name (``_entry_file``).
        """
        kb_name = named_kb(kb_name)
        if kb_name:
            # The full name only: a shortname is link syntax, refused by the
            # surface's resolver before this is reached (#952).
            if readable_kbs is not None and kb_name not in readable_kbs:
                return None
            result = self._read_entry(entry_id, kb_name)
            if result:
                self._attach_links(result, entry_id, kb_name, readable_kbs)
            return result

        found = self._find_unqualified(entry_id, readable_kbs)
        if found is None:
            return None
        name, result = found
        self._attach_links(result, entry_id, name, readable_kbs)
        return result

    def locate(
        self,
        entry_id: str,
        kb_name: str | None = None,
        *,
        readable_kbs: set[str] | None,
        entry_type: str | None = None,
    ) -> str:
        """The one KB that holds ``entry_id``, or the refusal (#911).

        For a surface that takes an id and an optional KB and then works in
        that KB -- every plugin command with ``-k`` optional calls this once
        before it reads or writes. It answers as ``get_entry`` does, from the
        same pass: a named KB holding the id is that KB; without one (absent
        or blank, ``named_kb``), an id in exactly one readable KB is that KB,
        and an id in more than one is ``AmbiguousEntryError`` with
        ``candidate_kbs`` (readable KBs only, config order). Anything else is
        ``EntryNotFoundError``, whose message names only the KB the caller
        named: a KB outside ``readable_kbs`` neither answers nor is named.

        **The type rule (#1310), one for every surface.** A command or tool
        that acts on one entry type passes it as ``entry_type`` -- ``sw``
        backlog items (``backlog_item``), ``wiki`` articles (``article``),
        ``social vote`` writeups (``writeup``), a claim tool ``claim`` -- and
        only entries of that type count: a note that shares the id in another
        KB neither makes the id ambiguous nor answers it, and an id held only
        by other types is ``ENTRY_NOT_FOUND`` ("backlog item 'x' not found").
        A command that accepts any type (``zettel`` graph, the investigation
        network tools) passes none, and then two entries of any types sharing
        the id are ``AMBIGUOUS``. The CLI (``require_entry_kb``) and the MCP
        dispatcher (a tool's ``locates_entry``) both pass the type the command
        or tool is documented to act on; ``pyrite get`` and ``kb_get``, which
        return any entry, pass none.

        Registry-only KBs (``pyrite kb add``) are searched when this service's
        config has them merged (``cli.context.get_config_and_db``).
        """
        kb_name = named_kb(kb_name)
        what = f"{entry_type.replace('_', ' ')} '{entry_id}'" if entry_type else f"'{entry_id}'"
        if kb_name:
            # The full name only: a shortname is link syntax, refused by the
            # surface's resolver (require_kb) before this is reached (#952).
            readable = readable_kbs is None or kb_name in readable_kbs
            result = self._read_entry(entry_id, kb_name) if readable else None
            if result and (entry_type is None or result.get("entry_type") == entry_type):
                return kb_name
            raise EntryNotFoundError(f"Entry {what} not found in KB '{kb_name}'")
        found = self._find_unqualified(entry_id, readable_kbs, entry_type=entry_type)
        if found is None:
            raise EntryNotFoundError(f"Entry {what} not found in any KB")
        return found[0]

    def _find_unqualified(
        self,
        entry_id: str,
        readable_kbs: set[str] | None,
        *,
        entry_type: str | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """The (KB, entry) an id names when no KB is named; None for no match.

        The one rule for an unqualified id (principle 8): ``get_entry`` and
        ``locate`` both ask here. Search all KBs the caller can read. An id
        the index knows in more than one of them is refused (#528), never
        answered by config order; only KBs in ``readable`` are counted, so a
        KB the caller cannot read never becomes a candidate (concealment,
        ADR-0037 section 4). With ``entry_type``, only entries of that type
        are matches.

        KNOWN EXCEPTION to "never resolved silently": the by-name pass for
        unindexed files runs only when no readable KB's index knows the id
        (#857's cost bound). So an id indexed in one KB, with a
        not-yet-indexed <id>.md in another, is answered from the indexed KB
        (as #753 reads it); `pyrite index sync` removes the exception.
        """
        readable = [
            kb.name
            for kb in self.config.all_kbs()
            if readable_kbs is None or kb.name in readable_kbs
        ]
        for unindexed in (False, True):
            matches: list[tuple[str, dict[str, Any]]] = []
            for name in readable:
                result = self._read_entry(entry_id, name, unindexed=unindexed)
                if result and (entry_type is None or result.get("entry_type") == entry_type):
                    matches.append((name, result))
            if len(matches) > 1:
                raise AmbiguousEntryError(entry_id, [name for name, _ in matches])
            if matches:
                return matches[0]
        return None

    def _read_entry(
        self, entry_id: str, kb_name: str, *, unindexed: bool | None = None
    ) -> dict[str, Any] | None:
        """THE single-entry read: the entry's file, parsed now (ADR-0042 decision 9).

        The index row only says where to look. Its file is read if it still
        holds the id (ADR-0038 I8); otherwise, and when there is no row, the
        files named ``<id>.md`` in the KB are tried, each verified. With the
        KB named (``unindexed`` None), the KB is then scanned as ``find_file``
        scans it, so a file renamed by hand answers before the next sync, as
        create's exists check already saw it. Without a KB (``get_entry``'s
        two passes over every readable KB) a miss stops at the names: a scan
        per KB would grow with the number of KBs (maintainer, 2026-10-20), so
        a renamed, unindexed file answers ``-k`` only. No file holding the id
        is a miss (``None``) even when a row remains: the file decides
        (principle 5).

        The scan reads every file's raw text but parses only those it cannot
        rule out (#135): about 100 ms on 2,000 files. That bound does not hold
        in a KB with schema migrations to apply, where every file is parsed
        (about 1 ms each), nor for files with no ``id:`` line (a derived id),
        each parsed on every miss.

        A row whose path lies outside the KB root is a user's worktree file
        (the overlay read service, ``WorktreeResolver.get_read_service``): it
        is read where it points and never replaced by a file in main.

        A row with no ``file_path`` (rows the experimental journalism tools
        create, #92) is returned as it is, with ``content_hash: None``.

        ``unindexed``: None reads either way; False only ids with a row; True
        only ids without one (``get_entry``'s two passes without a KB).

        Lists, search and batch reads stay index reads and may lag.
        """
        row = self.db.get_entry(entry_id, kb_name)
        if unindexed is not None and unindexed != (row is None):
            return None
        kb_config = self.get_kb(kb_name)
        if row is not None and (not row.get("file_path") or kb_config is None):
            row["content_hash"] = None  # no file to read: the row, as before
            return row
        if kb_config is None:
            return None
        found = self._entry_file(kb_config, entry_id, row, scan=unindexed is None)
        if found is None:
            return None
        path, digest, entry = found
        try:
            return self._index_mgr.read_fields(entry, kb_name, path, content_hash=digest, row=row)
        except TypeError as exc:
            # A file with no readable fields: a tagged or complex key, or a
            # value JSON cannot hold (a `!!set`). Refused, not a traceback
            # (#1035). A file that reads (a flow-style root) never gets here:
            # a read is refused only when it cannot be answered.
            _refuse_unreadable_fields(path, entry_id, exc)
            raise

    def _entry_file(
        self, kb_config: KBConfig, entry_id: str, row: dict[str, Any] | None, *, scan: bool
    ) -> tuple[Path, str, Entry] | None:
        """``(path, content_hash, entry)`` of the file holding ``entry_id``, or None.

        ``scan``: after the indexed file and the files named like the id, scan
        the KB (``_files_holding``); False stops at the names."""
        repo = KBRepository(kb_config)
        indexed = Path(row["file_path"]) if row else None

        def candidates() -> Iterator[Path]:
            # Lazy: the indexed file is tried first, and the KB's directories
            # are walked (``_filename_candidates`` runs ``rglob``) only when it
            # does not hold the id. A hit never walks a directory.
            if indexed is not None:
                yield indexed
            if indexed is not None and not repo._lexically_inside(indexed):
                return  # an overlay row: never main's file in its place
            # find_file's steps: the files named like the id, then (one KB
            # named) the prefiltered scan of the KB (#135), so a read and a
            # create agree on which file holds the id.
            try:
                repo._validate_entry_id(entry_id)
            except ValidationError:
                pass
            else:
                for c in repo._filename_candidates(entry_id):
                    if c != indexed and repo._lexically_inside(c):
                        yield c
            if scan:
                yield from repo._files_holding(entry_id, skip=tried)

        unreadable: tuple[Path, Exception] | None = None
        tried: set[Path] = set()
        for path in candidates():
            if path in tried:
                continue
            tried.add(path)
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            try:
                # One read: the entry is parsed from the bytes that are hashed.
                entry = repo.load_entry_from_bytes(path, raw)
            except Exception as exc:
                # A file the loader refuses (a duplicate key, a root that is
                # no mapping) is the unmodellable refusal, not a miss (#1035).
                _refuse_unmodellable(raw, entry_id, "read")
                unreadable = unreadable or (path, exc)
                continue
            if id_text(entry.id) == entry_id:
                return path, content_hash(raw), entry
        if unreadable is not None:
            self._unreadable_entry_file(kb_config.name, entry_id, *unreadable)
        return None

    @staticmethod
    def _unreadable_entry_file(kb_name: str, entry_id: str, path: Path, exc: Exception) -> None:
        """The file that may hold ``entry_id`` cannot be parsed.

        A miss for now, with one log line and never a traceback. #777 gives
        this its own error (``MALFORMED_ENTRY``): raise it here.
        """
        logger.warning("get %s/%s: cannot parse %s: %s", kb_name, entry_id, path, exc)

    def _attach_links(
        self,
        result: dict[str, Any],
        entry_id: str,
        kb_name: str,
        readable_kbs: set[str] | None,
    ) -> None:
        result["outlinks"] = self.db.get_outlinks(entry_id, kb_name, readable_kbs=readable_kbs)
        result["backlinks"] = self.db.get_backlinks(entry_id, kb_name, readable_kbs=readable_kbs)

    def _resolve_entry_type(self, entry_type: str, kb_type: str = "") -> str:
        """Resolve a generic core type to a plugin subtype if one exists.

        If a plugin ACTIVE FOR THIS KB provides a type that subclasses the
        core type for the given name, prefer the plugin type. E.g.
        "event" -> "investigation_event" in a journalism-investigation KB,
        because that plugin registers InvestigationEventEntry(EventEntry)
        and declares journalism-investigation in get_kb_types().

        Scoping by kb_type is load-bearing, not an optimization. Resolution
        picks the FIRST subclass found, and the unscoped registry dict is
        ordered by plugin discovery, which follows site-packages enumeration
        and therefore varies between machines. Two plugins' PersonEntry
        subclasses (the former cascade `actor` and social's `user_profile`)
        made an unscoped `person` resolve to `actor` on one host and
        `user_profile` on another for identical code -- with the wrong answer silently written
        to disk. It also meant installing an unrelated extension rewrote
        types in every KB (the tutorial-KB `undeclared_types` symptom).
        See plugin-type-resolution-scoping.

        An empty kb_type means "no KB context", which matches every plugin
        and preserves the previous global behavior for callers that have no
        KB in hand.
        """
        from ..models.core_types import ENTRY_TYPE_REGISTRY

        core_cls = ENTRY_TYPE_REGISTRY.get(entry_type)
        if not core_cls:
            return entry_type
        try:
            from ..plugins import get_registry

            plugin_types = get_registry().get_all_entry_types_for_kb(kb_type)
            # Even within one KB type several types can subclass the same
            # core type (journalism-investigation declares investigation_event,
            # transaction and legal_action, all EventEntry subclasses).
            # Discovery order must not decide the winner, but neither may
            # alphabetical order -- that once picked `cascade_event` over
            # `timeline_event`, silently changing the type of every new entry
            # in a 5,505-entry timeline KB. Prefer the MOST DERIVED class
            # (longest MRO): a subclass of InvestigationEventEntry ->
            # EventEntry beats a direct EventEntry subclass, because a deeper
            # chain is a strictly more specific declaration of the same
            # concept. Name is the final tiebreak so equal-depth candidates
            # still resolve identically on every machine.
            candidates = [
                (name, cls)
                for name, cls in plugin_types.items()
                if name != entry_type and isinstance(cls, type) and issubclass(cls, core_cls)
            ]
            if candidates:
                candidates.sort(key=lambda nc: (-len(nc[1].__mro__), nc[0]))
                return candidates[0][0]
        except Exception:
            logger.warning("Plugin type resolution failed for %s", entry_type, exc_info=True)
        return entry_type

    # =========================================================================
    # The write pipeline (#378)
    #
    # Every create decision is made here, once, for every surface: REST, MCP
    # and the CLI only map their arguments into a spec and map the
    # ValidationError subclasses below back out with ``error_code``. Before
    # this, each surface made its own copy of these decisions and they drifted
    # (#197, #359, #366).
    # =========================================================================

    def _writable_kb(self, kb_name: str) -> KBConfig:
        # require_kb, the one resolver (#952), through this service's index:
        # a KB registered after this process started is writable wherever
        # it is readable (#764 review), not KB_NOT_FOUND to create/update alone.
        kb_config = self.require_kb(kb_name)
        if kb_config.read_only:
            raise KBReadOnlyError(f"KB is read-only: {kb_name}")
        return kb_config

    def _hook_ctx(
        self, kb_name: str, kb_config: KBConfig, operation: str, extra: dict | None = None
    ) -> PluginContext:
        return PluginContext(
            config=self.config,
            db=self.db,
            kb_name=kb_name,
            user="",
            operation=operation,
            kb_type=kb_config.kb_type,
            **({"extra": extra} if extra else {}),
        )

    @staticmethod
    def _refuse_undeclared_type(entry_type: str, kb_name: str, kb_config: KBConfig) -> None:
        """Refuse a type the KB's kb.yaml does not declare, when it declares any.

        Core types are NOT exempt (#197): a KB that declares a schema declares
        the vocabulary for that KB, and exempting core names is how `-t note`
        against a software KB skipped this refusal and then had plugin type
        resolution rewrite it to its most-derived `note` subtype -- an ADR with
        `adr_number: 0` under `kb/adrs/`. Checked on the type as the caller
        named it, before that resolution.
        """
        schema = kb_config.kb_schema
        declared = schema.declared_types() if schema else []
        if declared and entry_type not in declared:
            raise UndeclaredTypeError(
                f"type '{entry_type}' is not declared in KB '{kb_name}'. "
                f"Declared types: {', '.join(declared)}. Inspect the KB schema, or "
                f"override with allow_undeclared (the entry will be flagged by "
                f"`pyrite index health`).",
                declared,
            )

    def _prepare(
        self,
        kb_name: str,
        kb_config: KBConfig,
        spec: dict[str, Any],
        *,
        allow_undeclared: bool,
        builder: Callable[[str, str, str, str, dict[str, Any]], Entry] | None = None,
        resolve_type: bool = True,
        pending_ids: set[str] | None = None,
        pending_paths: set[Path] | None = None,
        owns: frozenset[str] = frozenset(),
    ) -> tuple[Entry, list[dict[str, Any]]]:
        """Decide whether ``spec`` is a valid new entry; build it if so.

        In order: the ADR-0034 truncated-body refusal (on the spec as the
        caller sent it, marker included) and marker stripping; the title; the
        undeclared-type refusal; the plugin type resolution; the model's own
        ``validate()``; the KB schema and plugin validators; and the exists
        check -- create never replaces. Raises a ValidationError subclass
        carrying a stable ``error_code``; returns the entry and its warnings.

        ``builder(entry_type, entry_id, title, body, fields)`` builds the entry;
        the default is :func:`build_entry`. ``add_entry_from_file`` passes its
        own so a file's frontmatter round-trips exactly as the loader reads it.

        ``resolve_type=False`` keeps the type exactly as named. A file's
        frontmatter declares its type; rewriting `type: note` to a plugin's
        most-derived `note` subtype is how #197 filed an ADR. ``pending_ids``
        are ids an earlier item of the same dry run would have created;
        ``pending_paths`` are resolved file paths an earlier item of the same
        dry run would have created -- needed because a `file_pattern` type's
        filename comes from FIELDS, not the id, so two different ids can
        collide on the same path (#391 cold read).

        A caller's spec never sets a field Pyrite or the type manages, at any
        spelling (#1010). ``owns`` names the managed fields an in-process
        owner sets itself (``sw new-adr``'s ``adr_number``); only those are
        exempt, and only on that call.
        """
        from ..schema import generate_entry_id

        if not isinstance(owns, frozenset):
            raise ValidationError(
                f"owns must be a frozenset of field names, got {type(owns).__name__}"
            )
        ensure_not_truncated(spec)
        # MARKER_KEYS are ADR-0034 read transport, never entry content: an
        # allowed `body_truncated: false` must not be persisted as frontmatter.
        # A None value means "not given", so the model's default applies.
        fields = {k: v for k, v in spec.items() if k not in MARKER_KEYS and v is not None}

        # A create names a field once, as an update does (#720; #1001
        # decision 1 and round 4 item 2): `title: A` with
        # `metadata: {title: B}` is refused, equal values write once.
        # Decided on the named type's class before anything is popped, so
        # `title`, `id` and `body` count as fields here too.
        self._refuse_conflicting_create_spellings(
            fields.get("entry_type") or fields.get("type") or "note", fields
        )

        named_type = fields.pop("entry_type", None)
        file_type = fields.pop("type", None)
        entry_type = named_type or file_type or "note"
        title = fields.pop("title", None)
        if not title:
            raise ValidationError("title is required")
        if not allow_undeclared:
            self._refuse_undeclared_type(entry_type, kb_name, kb_config)

        entry_id = fields.pop("id", None) or generate_entry_id(title)
        body = fields.pop("body", "")

        # Resolve a generic core type to a plugin subtype, scoped to THIS KB's
        # type so an unrelated installed extension can't rewrite the type
        # (plugin-type-resolution-scoping).
        if resolve_type:
            entry_type = self._resolve_entry_type(entry_type, kb_config.kb_type)
        if builder is None:
            self._refuse_managed_on_create(entry_type, kb_config, fields, owns)
            fields, attributes = self._with_declared_fields(entry_type, kb_config, fields)
            entry = build_entry(entry_type, entry_id=entry_id, title=title, body=body, **fields)
            entry._create_fields = attributes
        else:
            entry = builder(entry_type, entry_id, title, body, fields)

        # The model's own rules (an event needs a date, importance 1-10, ...).
        errors = entry.validate()
        if errors:
            raise ValidationError("; ".join(errors))
        # A new entry's own links (a file given to `add`, a create's `links`
        # field, an import) are ingest, not a link the caller is typing: only
        # what the index insert cannot store is refused (check_link_target) --
        # such a link used to be written, then fail the insert (#846). Nothing
        # else, so the
        # file that `update` and `index sync` accept, `add` accepts
        # (principle 2). The target is not rewritten: `target: 2024` stays an
        # int in the file. Not in `_validate_write`, which an update also
        # runs: a link already in a hand-edited file is not the update's.
        for link in entry.links:
            try:
                check_link_target(link.to_dict())
            except ValidationError as e:
                raise ValidationError(f"links: {e}") from None
        warnings = self._validate_write(entry, kb_name, kb_config)

        # Create never replaces. Ids are derived from titles, so two entries
        # sharing a title is ordinary -- and used to destroy the first one while
        # reporting "Created". Callers that mean to replace use update_entry.
        if KBRepository(kb_config).exists(entry.id) or entry.id in (pending_ids or ()):
            raise EntryExistsError(
                f"Entry with ID '{entry.id}' already exists in KB '{kb_name}'. "
                "Use update to change it, or choose a different title/id."
            )

        # #391 cold read: the id check above is not enough once a filename
        # comes from FIELDS (file_pattern), not the id -- two different ids
        # (or two ids without a number, whose titles slug alike) can resolve
        # to the SAME path. `adr-a` and `adr-b`, both adr_number=5, both
        # resolve to `0005-same.md`; the second call must refuse, not
        # overwrite the first's file. The pending set uses the resolved path
        # itself as its key so a dry-run batch catches an in-batch collision
        # the same way the real filesystem does for a non-dry-run one.
        repo = KBRepository(kb_config)
        resolved_path = repo._resolve_file_path(entry, repo._infer_subdir(entry))
        if resolved_path.exists() or resolved_path in (pending_paths or ()):
            raise EntryExistsError(
                f"Entry with ID '{entry.id}' would resolve to a file that already "
                f"exists ({resolved_path.name}) in KB '{kb_name}'. Use update to "
                "change it, or choose a different title/id."
            )
        return entry, warnings

    def _with_declared_fields(
        self, entry_type: str, kb_config: KBConfig, fields: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, str | None]]:
        """A create's request with every field the type's schema declares
        (#86), and the declared fields the model holds as attributes.

        The declaration is ``KBSchema.type_fields`` (core < plugin < kb.yaml),
        never the class's dataclass fields (the maintainer on #86, 2026-10-20).
        A field the caller sent, at any spelling and empty or not, is the
        caller's (ADR-0042 #720). One not sent is added before the entry is
        built, so the entry that is validated, given to the hooks, indexed,
        returned and written is one entry:

        - with no model attribute, at ``FieldSchema.empty_value()`` (its
          declared default, else ``''``, ``[]`` or null by field type);
        - with a model attribute, at its declared default when there is one;
          otherwise the attribute keeps the class's own default, so a typed
          field is never set to a value of the wrong type (``kind: None``).

        Left out: base Entry fields (``RESERVED_FIELD_NAMES``) and the fields
        a create refuses (``_field_sets``' managed set, #1010). An alias is
        added at its target's name. The second value maps each declared
        declared field to its attribute (None for a metadata-held field), for
        ``Entry._create_fields``.
        """
        from ..models.core_types import get_entry_class
        from ..schema.field_aliases import class_field_aliases, field_at, spellings_of

        cls = get_entry_class(entry_type)
        skip = RESERVED_FIELD_NAMES | self._field_sets(entry_type, kb_config)[2]
        model = {f.name for f in dataclasses.fields(cls) if f.init and not f.name.startswith("_")}
        aliases = class_field_aliases(cls, entry_type)
        envelope = fields.get("metadata")
        envelope = envelope if isinstance(envelope, Mapping) else {}
        added: dict[str, Any] = {}
        attributes: dict[str, str | None] = {}
        for name, schema in kb_config.kb_schema.type_fields(entry_type).items():
            if name in skip:
                continue
            field, _rest = field_at(cls, entry_type, (name,))
            if field in skip or field in added or field in attributes:
                continue
            sent = any(
                path[-1] in (envelope if len(path) > 1 else fields)
                for path in spellings_of(cls, entry_type, field)
            )
            attribute = field if field in model else None
            if attribute is None:
                attribute = next((a for a, t in aliases.items() if t == field and a in model), None)
            if attribute is not None:
                attributes[field] = attribute
                if not sent and schema.default is not None:
                    added[field] = schema.empty_value()
            elif not sent:
                added[field] = schema.empty_value()
            if attribute is None:
                attributes[field] = None
        return {**fields, **added}, attributes

    def _prepare_and_save(
        self,
        kb_name: str,
        kb_config: KBConfig,
        spec: dict[str, Any],
        *,
        allow_undeclared: bool,
        hook_ctx: PluginContext | None = None,
        embed: bool = True,
        builder: Callable[[str, str, str, str, dict[str, Any]], Entry] | None = None,
        resolve_type: bool = True,
        owns: frozenset[str] = frozenset(),
    ) -> WriteResult:
        """The one create pipeline: :meth:`_prepare`, then hooks, save, index, embed."""
        entry, warnings = self._prepare(
            kb_name,
            kb_config,
            spec,
            allow_undeclared=allow_undeclared,
            builder=builder,
            resolve_type=resolve_type,
            owns=owns,
        )
        ctx = hook_ctx or self._hook_ctx(kb_name, kb_config, "create")
        prepared_id = entry.id
        entry = self._run_hooks("before_save", entry, ctx)
        # _prepare looked the id up once, and save_entry trusts that answer
        # (#135). A hook that changed the id is looked up again, or a create
        # would write a second file holding an id another file holds.
        if entry.id != prepared_id and KBRepository(kb_config).exists(entry.id):
            raise EntryExistsError(
                f"Entry with ID '{entry.id}' already exists in KB '{kb_name}' "
                f"(a before_save hook changed the id from '{prepared_id}'). "
                "Use update to change it, or choose a different title/id."
            )
        # Hooks return the entry that will be persisted, so validate their
        # changes before the file is written.
        warnings = self._validate_write(entry, kb_name, kb_config)
        try:
            self._doc_mgr.save_entry(entry, kb_name, kb_config, is_create=True)
        except FileExistsError as e:
            # #391 cold read round 2: the exists() check in _prepare and this
            # write are two different moments -- two truly concurrent creates
            # can both pass the check before either publishes. The write
            # itself is exclusive (KBRepository.save's exclusive=True, from
            # is_create), so the LOSING side's publish raises this instead of
            # silently overwriting the winner's file. Same refusal the fast
            # path gives, so callers do not need to handle two exceptions
            # for one user-visible outcome.
            raise EntryExistsError(
                f"Entry with ID '{entry.id}' already exists in KB '{kb_name}' "
                "(a concurrent create won the race). Use update to change it, "
                "or choose a different title/id."
            ) from e
        if embed:
            self._auto_embed(entry.id, kb_name)
        self._run_hooks("after_save", entry, ctx)
        return WriteResult(entry=entry, warnings=warnings)

    def create(
        self,
        kb_name: str,
        spec: dict[str, Any],
        *,
        allow_undeclared: bool = True,
        owns: frozenset[str] = frozenset(),
    ) -> WriteResult:
        """Create one entry from a spec, returning the entry and its warnings.

        ``spec`` is the caller's entry as sent: ``entry_type`` (or ``type``),
        ``title``, optional ``id`` (derived from the title otherwise),
        ``body`` and any fields, with ADR-0034 marker keys left on so they can
        be refused. See :meth:`_prepare` for every check.

        ``allow_undeclared`` defaults to True for in-process callers (task,
        collection, daily-note and plugin code writing their own types). Every
        user-facing surface passes the caller's own override flag, False unless
        they asked, which is what applies the undeclared-type refusal.
        """
        kb_config = self._writable_kb(kb_name)
        return self._prepare_and_save(
            kb_name, kb_config, spec, allow_undeclared=allow_undeclared, owns=owns
        )

    def create_entry(
        self,
        kb_name: str,
        entry_id: str,
        title: str,
        entry_type: str,
        body: str = "",
        *,
        allow_undeclared: bool = True,
        owns: frozenset[str] = frozenset(),
        **kwargs,
    ) -> Entry:
        """
        Create a new entry. The positional form of :meth:`create`.

        Args:
            kb_name: Target KB name
            entry_id: Entry ID (filename without .md)
            title: Entry title
            entry_type: Type (event, person, organization, note, topic, etc.)
            body: Markdown body content
            allow_undeclared: False applies the undeclared-type refusal
            owns: managed fields this in-process caller sets itself (``sw
                new-adr``: ``{"adr_number"}``); every other managed field,
                at any spelling, is refused as on every surface (#1010)
            **kwargs: Additional fields (date, importance, tags, etc.)

        Returns:
            Created Entry object

        Raises:
            KBNotFoundError: If KB not found
            KBReadOnlyError: If KB is read-only
            ValidationError: (or a subclass) if the pipeline refuses the entry
        """
        spec = {**kwargs, "id": entry_id, "title": title, "entry_type": entry_type, "body": body}
        return self.create(kb_name, spec, allow_undeclared=allow_undeclared, owns=owns).entry

    def bulk_create_entries(
        self,
        kb_name: str,
        entries: list[dict[str, Any]],
        *,
        allow_undeclared: bool = True,
        validate_only: bool = False,
        drop_managed: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Create multiple entries in a single batch, through the same pipeline as
        :meth:`create`, one item at a time.

        Each spec has at least {entry_type, title} plus optional fields (id,
        body, date, importance, tags, metadata, etc.). An item the pipeline
        refuses fails on its own; its siblings are still created, and results
        keep the input order. An existing id -- including one an earlier item
        in the same batch just created -- is refused (#359); schema and plugin
        validation applies to every item (#366).

        Returns a list of result dicts, one per input entry:
            {"created": True, "entry_id": "...", "warnings": [...]} on success
                (``warnings`` only when there are any)
            {"created": False, "error": "...", "error_code": "..."} on failure

        ``validate_only`` runs every check without writing; a passing item is
        reported as {"created": False, "valid": True, "entry_id": "..."}.

        ``drop_managed`` is for an import (a file an export wrote): the keys an
        export adds about the index and location, and the type's managed
        fields, are dropped from each record and named in its ``ignored``.
        Without it (a direct create such as MCP ``kb_bulk_create``) they are
        refused.
        """
        if validate_only:
            kb_config = self.require_kb(kb_name)
        else:
            kb_config = self._writable_kb(kb_name)
        hook_ctx = self._hook_ctx(kb_name, kb_config, "create")

        results: list[dict[str, Any]] = []
        created_ids: list[tuple[str, str]] = []  # (entry_id, kb_name) for batch embed
        # A dry run writes nothing, so the exists check alone cannot see an id
        # -- or, for a file_pattern type, a resolved PATH -- an earlier item
        # of the same batch would have created.
        would_create: set[str] = set()
        would_create_paths: set[Path] = set()

        for spec in entries:
            ignored: list[str] = []
            try:
                if drop_managed:
                    spec, ignored = self._drop_managed_for_import(kb_config, spec)
                if validate_only:
                    entry, warnings = self._prepare(
                        kb_name,
                        kb_config,
                        spec,
                        allow_undeclared=allow_undeclared,
                        pending_ids=would_create,
                        pending_paths=would_create_paths,
                    )
                    would_create.add(entry.id)
                    repo = KBRepository(kb_config)
                    would_create_paths.add(
                        repo._resolve_file_path(entry, repo._infer_subdir(entry))
                    )
                    item: dict[str, Any] = {"created": False, "valid": True, "entry_id": entry.id}
                else:
                    written = self._prepare_and_save(
                        kb_name,
                        kb_config,
                        spec,
                        allow_undeclared=allow_undeclared,
                        hook_ctx=hook_ctx,
                        embed=False,
                    )
                    entry, warnings = written.entry, written.warnings
                    created_ids.append((entry.id, kb_name))
                    item = {"created": True, "entry_id": entry.id}
                if warnings:
                    item["warnings"] = warnings
                if ignored:
                    item["ignored"] = ignored
                results.append(item)
            except Exception as e:
                result = _refusal_result(e)
                if validate_only:
                    result["valid"] = False
                results.append(result)

        # Batch embed all created entries
        for eid, ekb in created_ids:
            self._auto_embed(eid, ekb)

        return results

    def add_entry_from_file(
        self,
        kb_name: str,
        source_path: Path,
        *,
        validate_only: bool = False,
        allow_undeclared: bool = True,
    ) -> tuple[Entry, dict[str, Any]]:
        """
        Add a markdown file with frontmatter to a knowledge base, through the
        same pipeline as :meth:`create`.

        Reads the file, parses frontmatter, validates, and saves to the KB.
        Frontmatter must include 'type' and 'title'.

        Args:
            kb_name: Target KB name
            source_path: Path to the markdown file
            validate_only: If True, validate without saving; refusals are
                reported in the returned ``errors`` instead of raised
            allow_undeclared: False applies the undeclared-type refusal

        Returns:
            Tuple of (Entry, validation_result dict with errors/warnings)

        Raises:
            KBNotFoundError: If KB not found
            KBReadOnlyError: If KB is read-only
            ValidationError: If frontmatter is missing required fields or the
                pipeline refuses the entry
        """
        from ..models.core_types import entry_from_frontmatter
        from ..schema import generate_entry_id
        from ..utils.yaml import load_yaml

        kb_config = self.require_kb(kb_name)
        if not validate_only and kb_config.read_only:
            raise KBReadOnlyError(f"KB is read-only: {kb_name}")

        source_path = Path(source_path)

        # Read and parse frontmatter
        text = source_path.read_text(encoding="utf-8")
        split = split_frontmatter(text)
        if isinstance(split, NoFrontmatter):
            raise ValidationError("File must start with YAML frontmatter (---)")
        if not isinstance(split, Frontmatter):
            raise ValidationError(f"Could not read the frontmatter: {describe(split)}")

        meta = load_yaml(split.text)
        if not meta or not isinstance(meta, dict):
            raise ValidationError("Frontmatter is empty or invalid")

        body = split.body

        # Require type and title
        if "type" not in meta:
            raise ValidationError("Frontmatter must include 'type'")
        if "title" not in meta:
            raise ValidationError("Frontmatter must include 'title'")

        def from_file(entry_type, entry_id, title, body_, fields):
            # The loader's own reading of the frontmatter, so links, sources,
            # timestamps and custom-type fields round-trip exactly.
            fm = {**fields, "id": entry_id, "title": title, "type": entry_type}
            return entry_from_frontmatter(fm, body_)

        spec = {**meta, "body": body}
        if validate_only:
            try:
                entry, warnings = self._prepare(
                    kb_name,
                    kb_config,
                    spec,
                    allow_undeclared=allow_undeclared,
                    builder=from_file,
                    resolve_type=False,
                )
            except ValidationError as e:
                fallback = {k: v for k, v in meta.items() if k not in MARKER_KEYS}
                fallback.setdefault("id", generate_entry_id(meta["title"]))
                entry = entry_from_frontmatter(fallback, body)
                errors = getattr(e, "errors", None) or [str(e)]
                return entry, {"errors": errors, "warnings": []}
            return entry, {"errors": [], "warnings": warnings}

        written = self._prepare_and_save(
            kb_name,
            kb_config,
            spec,
            allow_undeclared=allow_undeclared,
            builder=from_file,
            resolve_type=False,
        )
        return written.entry, {"errors": [], "warnings": written.warnings}

    @staticmethod
    def _field_sets(
        entry_type: str, kb_config: KBConfig | None
    ) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
        """``(updatable, schema_only, managed)`` for one entry type.

        ``updatable`` is the type's own field set: its model class's fields
        (core or plugin, from the type registry) plus every field its KB
        schema names for the type (``fields``, ``optional`` and ``required``),
        less ``managed`` -- the service's identity fields and the type's own
        ``managed_fields``. ``schema_only`` is the schema's part, which an
        update writes as a custom field when the model has no attribute for
        it. One computation, so what ``updatable_fields`` reports and what
        ``update`` writes cannot disagree.
        """
        from ..models.core_types import get_entry_class

        cls = get_entry_class(entry_type)
        managed = _MANAGED_FIELDS | frozenset(getattr(cls, "managed_fields", frozenset()))
        model = {f.name for f in dataclasses.fields(cls) if f.init and not f.name.startswith("_")}
        schema: set[str] = set()
        if kb_config is not None:
            type_schema = kb_config.kb_schema.get_type_schema(entry_type)
            if type_schema is not None:
                schema = set(type_schema.fields) | set(type_schema.optional)
                schema |= set(type_schema.required)
        return (
            frozenset((model | schema) - managed - _TIMESTAMP_FIELDS),
            frozenset(schema - managed),
            managed,
        )

    def updatable_fields(self, entry_id: str, kb_name: str) -> frozenset[str]:
        """The fields a caller's field update may set on this entry.

        The entry type's own field set (see ``_field_sets``): model fields plus
        the KB schema's fields for the type, less identity fields and the
        type's ``managed_fields``. Timestamps are left out: :meth:`update`
        accepts them only when a caller names them. Replaces MCP's
        hand-maintained allowlist, which named extension vocabulary in core
        and still missed every kb.yaml-declared field (#378). Empty if the
        entry does not exist.
        """
        row = self.db.get_entry(entry_id, kb_name)
        entry_type = row.get("entry_type") if row else None
        kb_config = self.config.get_kb(kb_name)
        if entry_type is None and kb_config is not None:
            loaded = KBRepository(kb_config).load(entry_id)
            entry_type = loaded.entry_type if loaded else None
        if entry_type is None:
            return frozenset()
        return self._field_sets(entry_type, kb_config)[0]

    def split_echoed_update(
        self, entry_id: str, kb_name: str, fields: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str], list[str]]:
        """``(updates, ignored, unchanged)`` for a caller that may send a read result back.

        MCP ``kb_update`` takes the entry's fields as top-level arguments, and
        an agent commonly edits what ``kb_get`` returned and sends all of it.
        That result carries keys no update may set -- the id, the path, the
        timestamps, the type's managed fields, index columns -- and ``null``
        for every index column the entry does not use. Those are set aside
        and named in ``ignored``, so the caller can see what was not written.
        Every other key goes to :meth:`update`, which stores one the type does
        not declare the way CLI ``update -f`` and REST PATCH do (#407, #455):
        before this, MCP kept only the declared fields and dropped the rest
        with ``updated: true``.

        In an echo (the request carries a key only a read result has, such as
        ``id`` or ``indexed_at``), a key whose value is still Pyrite's reading
        -- what the same read returns for it -- is not a change either: it is
        set aside too, and named in ``unchanged`` rather than ``ignored`` so a
        caller can tell "left as it is" from "never written by an update"
        (#561). Assigning it would rewrite the file:
        ``importance: high`` read as 5 became ``importance: 5``, and a task
        grew the ``importance``/``priority`` defaults it never had. A request
        naming fields on its own (``{importance: 5}``) is the caller's values,
        each written as sent; the CLI's ``-f`` never passes through here.
        """
        # `kb_get` answers `{"entry": {...}}`; sent back whole, that is one
        # undeclared key holding the read, which an update would write into
        # the file (#1024). Refused, not unwrapped: the caller sends the
        # entry's own fields (ADR-0042 decision 9, fail closed). An `entry`
        # mapping with no read-result key is a declared field's value; `id`
        # alone is not one (a declared field's value may have an `id`), but
        # `entry_type`, `file_path` or an index column is.
        envelope = fields.get("metadata")
        wrapped = fields.get("entry")
        if wrapped is None and isinstance(envelope, dict):
            wrapped = envelope.get("entry")  # REST's only spelling of it
        if isinstance(wrapped, dict) and not (_READ_RESULT_MARKERS - {"id"}).isdisjoint(wrapped):
            raise ValidationError(
                "Cannot update: `entry` holds a whole read result (the envelope `kb_get` "
                "answers with). Send the entry's fields at the top level instead: "
                "the contents of `result['entry']`, with your changes."
            )
        updatable = self.updatable_fields(entry_id, kb_name)
        # The reading an echo is compared with is the read a get returns -- the
        # file now -- not the row, which may lag it: a value the read
        # normalised is then "unchanged" and not written over the file's own
        # spelling (#753).
        row = self._read_entry(entry_id, kb_name)
        entry_type = row.get("entry_type") if row else None
        managed = (
            self._field_sets(entry_type, self.config.get_kb(kb_name))[2]
            if entry_type
            else _MANAGED_FIELDS
        )
        not_written = managed | _TIMESTAMP_FIELDS | _INDEX_ONLY_KEYS
        reading = row if row and not _READ_RESULT_MARKERS.isdisjoint(fields) else {}
        updates: dict[str, Any] = {}
        ignored: list[str] = []
        unchanged: list[str] = []
        for key, value in fields.items():
            if key in not_written or (value is None and key not in updatable):
                ignored.append(key)
            elif key in reading and reading[key] == value:
                unchanged.append(key)
            else:
                updates[key] = value
        return updates, sorted(ignored), sorted(unchanged)

    def update(
        self,
        entry_id: str,
        kb_name: str,
        updates: dict[str, Any],
        *,
        append: dict[str, Any] | None = None,
        remove: dict[str, Any] | None = None,
        unset: list[str] | None = None,
    ) -> WriteResult:
        """Update an existing entry for a caller, returning it and its warnings.

        ``append`` and ``remove`` name a list field and one item (or a list
        of items, each one appended or removed); ``unset`` names keys to
        remove with their own lines (ADR-0042 decision 1; #955, B6 P3b).
        ``null`` sent in ``updates`` is a value, never a removal. An appended
        link is checked as a link (``check_link_target``). ``links`` and
        ``sources`` may be appended to and removed from here, though a set
        of the whole list is still refused: an item operation touches only
        the item it names.

        The surfaces' update (REST PUT/PATCH, MCP ``kb_update``, CLI
        ``update``). ``updates`` is the caller's field set as sent, ADR-0034
        marker keys included: a body marked truncated is refused, at any
        depth. A field the service or the type manages (``id``, the file
        path, links, a task's audit trail...) is refused rather than written:
        setting ``id`` used to leave a second file. A field the entry's model
        does not have -- kb.yaml-declared or not -- is written as a custom
        field, the way ``create`` would have stored it, instead of being
        dropped (#407).

        Raises:
            KBNotFoundError: If KB not found
            KBReadOnlyError: If KB is read-only
            EntryNotFoundError: If entry not found
            ValidationError: (or a subclass) if the write is refused
        """
        return self._update(
            entry_id,
            kb_name,
            updates,
            ops=_item_operations(append, remove, unset),
            restrict=True,
        )

    def apply_operations(
        self, entry_id: str, kb_name: str, ops: list[Any], *, merge_spellings: bool = False
    ) -> WriteResult:
        """The in-process form of an operation write: ``ops`` are
        ``file_operations`` operations (``Set``, ``Append``, ``Remove``,
        ``Unset``, ``AddSubkey``, ``ReplaceBody``) whose paths name the
        file's keys as a read returns them; aliases and a key the file nests
        under ``metadata:`` resolve as for a field update. The services that
        own a type's managed fields (a task's audit trail and evidence, qa
        fix's link and tag repairs) write one item here instead of a list
        read through the model (#955, B6 P3b). ``merge_spellings`` is `qa
        fix`'s own: the one write a doubly spelled field takes (removing a
        spelling of an equal pair); no surface passes it (#1001 round 4)."""
        return self._update(
            entry_id, kb_name, {}, ops=list(ops), restrict=False, merge_spellings=merge_spellings
        )

    def _update(
        self,
        entry_id: str,
        kb_name: str,
        updates: dict[str, Any],
        *,
        restrict: bool,
        ops: list[Any] | None = None,
        merge_spellings: bool = False,
    ) -> WriteResult:
        """The one update: the request as operations applied to the file's own
        bytes (ADR-0042; #955, B6 P3a).

        Read the file once (``read_for_write``: the bytes are the base, the
        model is parsed from the same bytes); map the request and the item
        operations onto the file's own keys and refuse what today refuses
        (type, managed fields, link shape: ``_resolve_operation``, the one
        place; conflicting spellings); ``apply()`` them to the decoded bytes; stamp
        ``updated_at`` in the file's own form where the file carries it and
        something changed; parse the result, validate it once, run the
        ``before_save`` hooks on it with their return value ignored (decision
        4); build the index row from the result before anything is published
        (a result the index cannot store is refused with nothing written);
        publish with compare-and-replace under the momentary lock
        (``publish_entry_text``: no lock outside git, and the result says so).
        If the file changed between the read and the replace the whole thing
        runs again from a fresh read, at most ``_UPDATE_ATTEMPTS`` times, then
        ``ConflictError`` (Q2: last writer wins on the same key). No ``Entry``
        is serialised: a key the request does not name keeps its bytes.
        """
        # ADR-0034 marker keys are refused here (a truthy `body_truncated`)
        # and otherwise never stored; `_prepare` does the same at the create
        # path.
        ensure_not_truncated(updates)
        updates = {k: v for k, v in updates.items() if k not in MARKER_KEYS}

        kb_config = self._writable_kb(kb_name)
        repo = KBRepository(kb_config)

        for _attempt in range(_UPDATE_ATTEMPTS):
            found = repo.read_for_write(entry_id)
            if found is None:
                # No file holds the id as the reader reads ids, but `<id>.md`
                # may be one it cannot read (#1035): that is a refusal, not a miss.
                _refuse_unmodellable_named(repo, entry_id, "update")
                raise EntryNotFoundError(
                    f"Entry not found: {entry_id}{repo.not_found_hint(entry_id)}"
                )
            path, raw = found
            _refuse_unmodellable(raw, entry_id, "update")
            try:
                entry = repo.load_entry_from_bytes(path, raw)
            except Exception as e:
                logger.warning("Could not load %s: %s", path, e)
                raise EntryNotFoundError(
                    f"Entry not found: {entry_id}{repo.not_found_hint(entry_id)}"
                ) from None
            text = raw.decode("utf-8")
            split = split_frontmatter(text)
            if not isinstance(split, Frontmatter):
                raise ValidationError(f"Cannot update '{entry_id}': {describe(split)}")
            span = FrontmatterSpan(split.yaml_start, split.close_start, split.close_end)
            file_values = load_yaml(split.text) or {}
            if not isinstance(file_values, dict):
                raise ValidationError(
                    f"Cannot update '{entry_id}': the frontmatter is not a mapping"
                )
            # Every operation -- the request's fields as `Set`s and the item
            # operations alike -- goes through the one resolver, which maps
            # its path onto the file's own key and decides every refusal on
            # that key (#973 fix round 1): no operation kind, path form or
            # surface can reach `apply()` another way.
            all_ops: list[Any] = []
            placed: dict[Any, Any] = {}
            for op in [*_request_operations(updates), *(ops or [])]:
                resolved = self._resolve_operation(
                    entry, entry_id, kb_config, file_values, op, restrict, merge_spellings
                )
                if isinstance(resolved, Set):
                    if _same_instant_as_file(file_values, resolved.path, resolved.value):
                        continue  # the instant the file holds, in another spelling (decision 3)
                    if resolved.path in placed and placed[resolved.path] == resolved.value:
                        continue  # the same field named twice with one value
                    placed[resolved.path] = resolved.value
                all_ops.append(resolved)
            self._refuse_conflicting_spellings(entry, updates)
            try:
                new_text, _report = apply(text, all_ops, span)
                changed = new_text != text
                if changed and "updated_at" in file_values and "updated_at" not in updates:
                    stamp = _updated_at_stamp(
                        file_values["updated_at"], _raw_scalar(split.text, "updated_at")
                    )
                    if stamp is not None:
                        new_text, _report = apply(
                            new_text, [Set("updated_at", stamp)], _frontmatter_span(new_text)
                        )
            except OperationRefusedError as e:
                raise ValidationError(f"Cannot update '{entry_id}': {e.reason}") from e

            # The field set as loaded, for the exception that keeps an off-list
            # enum value the update does not change (#47, #555).
            before = copy.deepcopy(self._validated_fields(entry))
            old_status = getattr(entry, "status", None)
            if not changed:
                # A request that changes nothing writes nothing (decision 3);
                # its warnings are still the file's.
                warnings = self._validate_write(
                    entry, kb_name, kb_config, before=before, existing_entry=entry
                )
                return WriteResult(entry=entry, warnings=warnings, changed=False)

            new_bytes = new_text.encode("utf-8")
            try:
                new_entry = repo.load_entry_from_bytes(path, new_bytes)
            except Exception as e:
                raise ValidationError(
                    f"Cannot update '{entry_id}': the result would not load ({e})"
                ) from e
            # Refuse a transition into claimed, not edits which preserve an
            # existing claim after its children have since finished.
            if (
                new_entry.entry_type == "task"
                and getattr(new_entry, "status", None) == "claimed"
                and old_status != "claimed"
            ):
                refusal = self._refuse_claim_of_complete_task(entry_id, kb_name)
                if refusal:
                    raise ValidationError(refusal["error"])
            # Once, on the entry parsed from the result (decision 2): the
            # type, managed-field and enum refusals happen before anything is
            # written.
            warnings = self._validate_write(
                new_entry, kb_name, kb_config, before=before, existing_entry=entry
            )
            extra = {"old_status": old_status} if old_status else {}
            hook_ctx = self._hook_ctx(kb_name, kb_config, "update", extra)
            # A before_save hook may refuse (raise); its return value is
            # ignored and nothing it does reaches the file (decision 4). What
            # it changes in place is this write's index row, until the next
            # sync (P6 replaces that with an index-time derivation).
            self._run_hooks("before_save", new_entry, hook_ctx)
            try:
                row = self._index_mgr.row_for(
                    new_entry, kb_name, path, content_hash=content_hash(new_bytes)
                )
            except ValueError as e:
                raise ValidationError(
                    f"Cannot update '{entry_id}': the result cannot be indexed ({e})"
                ) from e
            try:
                locked = publish_entry_text(
                    path, new_text, expect=raw, root=kb_config.path, lock_timeout=_LOCK_TIMEOUT
                )
            except FileChanged:
                continue  # re-read and re-apply (decision 10; Q2)
            except LockTimeout as e:
                # Another writer held the momentary lock for too long: the
                # same call can succeed once it is released.
                raise StorageBusyError(f"Cannot write '{entry_id}': {e}") from e
            except OSError as e:
                # The lock directory (`LockDirError`), a hard link
                # (`HardLinked`), the filesystem: a storage fault whose detail
                # (a real path) belongs in the log, not in a client-facing
                # body (ADR-0037 §3; `public_text` logs it). The two a caller
                # can act on name their reason, without the path.
                raise _storage_error(entry_id, e) from e
            break
        else:
            raise ConflictError(
                f"'{entry_id}' in KB '{kb_name}' kept changing while this update ran "
                f"({_UPDATE_ATTEMPTS} attempts); nothing was written. Read it again and retry."
            )

        path, not_moved = self._templated_move(
            repo, kb_config, new_entry, path, new_text, file_values
        )
        row["file_path"] = str(path)
        self.db.register_kb(
            name=kb_name,
            kb_type=kb_config.kb_type,
            path=str(kb_config.path),
            description=kb_config.description,
        )
        self._index_mgr.index_row(row, path)
        self._auto_embed(new_entry.id, kb_name)
        self._run_hooks("after_save", new_entry, hook_ctx)
        return WriteResult(
            entry=new_entry, warnings=warnings, changed=True, locked=locked, not_moved=not_moved
        )

    @staticmethod
    def _refuse_conflicting_spellings(entry: Entry, updates: dict[str, Any]) -> None:
        """One name per field (#697, #720): both spellings of one field with
        different values are refused; equal values name one value."""
        from ..schema.field_aliases import refuse_conflicting_spellings

        refuse_conflicting_spellings(
            type(entry),
            entry.entry_type,
            {k: v for k, v in updates.items() if k != "metadata"},
            updates["metadata"] if isinstance(updates.get("metadata"), dict) else {},
        )

    @staticmethod
    def _refuse_conflicting_create_spellings(entry_type: str, fields: dict[str, Any]) -> None:
        """One name per field on create (#720; #1001 round 4 item 2)."""
        from ..models.core_types import get_entry_class
        from ..schema.field_aliases import refuse_conflicting_spellings

        try:
            cls = get_entry_class(entry_type)
        except Exception:  # an unknown type is refused later, by name
            return
        refuse_conflicting_spellings(
            cls,
            entry_type,
            {k: v for k, v in fields.items() if k != "metadata"},
            fields["metadata"] if isinstance(fields.get("metadata"), dict) else {},
        )

    def _managed_spellings(
        self,
        entry_type: str,
        kb_config: KBConfig,
        fields: dict[str, Any],
        owns: frozenset[str] = frozenset(),
    ) -> list[tuple[str, str, bool]]:
        """``(key, field, in_envelope)`` for every key of a create's ``fields``
        that spells a field Pyrite or the type manages, at every spelling
        ADR-0051 treats as the same field: the top-level key, a dotted
        top-level key (``metadata.evidence``), an alias, and the key under the
        ``metadata`` envelope -- all resolved by ``field_at``. ``links`` and
        ``sources`` are a new entry's own content and are not listed;
        ``owns`` names the managed fields an in-process owner sets itself.
        Raises ``ValidationError`` for an envelope key no parser reads (not a
        key a new file gets, decision A)."""
        from ..models.core_types import get_entry_class
        from ..schema.field_aliases import NotAFieldError, field_at, path_segments

        cls = get_entry_class(entry_type)
        managed = self._field_sets(entry_type, kb_config)[2] - _ITEM_LISTS - owns
        envelope = fields.get("metadata")
        keyed: list[tuple[str, tuple[Any, ...], bool]] = []
        for k in fields:
            if not isinstance(k, str) or k == "metadata":
                continue
            try:
                keyed.append((k, path_segments(k), False))
            except ValueError:
                continue  # a key no parser reads is a literal custom key
        if isinstance(envelope, dict):
            for k in envelope:
                if isinstance(k, str):
                    keyed.append((k, ("metadata", *path_segments(k)), True))
        found: list[tuple[str, str, bool]] = []
        for key, path, in_envelope in keyed:
            try:
                field, _rest = field_at(cls, entry_type, path)
            except (NotAFieldError, ValueError) as e:
                if in_envelope:
                    raise ValidationError(f"Cannot create a {entry_type}: {e}") from None
                continue  # a custom top-level key: written as sent
            if field in managed:
                found.append((key, field, in_envelope))
        return found

    def _refuse_managed_on_create(
        self,
        entry_type: str,
        kb_config: KBConfig,
        fields: dict[str, Any],
        owns: frozenset[str] = frozenset(),
    ) -> None:
        """A create refuses a field Pyrite or the type manages, at every
        spelling (:meth:`_managed_spellings`; #1010, #1001 decision 6). One
        message naming the field, on every surface. A key the build would
        drop (``file_path``, ``provenance``, ``extra_frontmatter``) is
        managed, so it is refused here and never silently dropped (ADR-0042
        decision 3). The top-level ``id`` and title are popped before this
        runs; ``id`` in the envelope is refused."""
        found = self._managed_spellings(entry_type, kb_config, fields, owns)
        if found:
            key, name, _in_envelope = found[0]
            spelled = f" (sent as `{key}`)" if key != name else ""
            raise ValidationError(
                f"Cannot set {name} on a new {entry_type}{spelled}: Pyrite maintains "
                "this field. To keep a field of your own under that name, "
                f"name it differently (for example `{entry_type}_{name}`)."
            )

    def _drop_managed_for_import(
        self, kb_config: KBConfig, spec: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        """``(spec, ignored)`` for an import record: an export carries keys
        about the index and the location (``kb_name``, ``file_path``,
        ``indexed_at``, ``content_hash``, ``outlinks``, ``backlinks``, ...) and
        a type's managed fields (a task's ``assigned_at``,
        ``status_change_log``). None of them is a request, so an import
        drops them and names each in ``ignored`` -- never silently (the
        maintainer's decision, 2026-10-20). A direct create refuses them
        instead. The entry's ``id`` and ``entry_type`` are kept."""
        if not isinstance(spec, dict):
            return spec, []
        named = spec.get("entry_type") or spec.get("type") or "note"
        try:
            entry_type = self._resolve_entry_type(named, kb_config.kb_type)
            managed = self._managed_spellings(entry_type, kb_config, spec)
        except Exception:  # an unknown type or bad envelope key: the create says why
            return spec, []
        index_only = (_INDEX_ONLY_KEYS - {"entry_type", "type"}) | {
            "file_path",
            "kb_name",
            # An export carries the read's timestamps; a create stamps new
            # ones, so an import reports them instead of dropping them (#1091).
            "created_at",
            "updated_at",
        }
        out = dict(spec)
        ignored: list[str] = []
        for key in list(out):
            if key in index_only or any(key == k and not env for k, _f, env in managed):
                if key == "id":
                    continue
                ignored.append(key)
                del out[key]
        envelope = out.get("metadata")
        if isinstance(envelope, dict):
            kept = dict(envelope)
            for key, _field, in_env in managed:
                if in_env and key in kept:
                    ignored.append(f"metadata.{key}")
                    del kept[key]
            if kept:
                out["metadata"] = kept
            else:
                out.pop("metadata")
        return out, sorted(ignored)

    def _resolve_operation(
        self,
        entry: Entry,
        entry_id: str,
        kb_config: KBConfig,
        file_values: dict[str, Any],
        op: Any,
        restrict: bool,
        merge_spellings: bool = False,
    ) -> Any:
        """THE place an operation's path is resolved onto the file and every
        refusal is decided (#955 acceptance 1; #971 and #973 fix rounds;
        Andon #994, decision A).

        Which field: ``field_at`` (``schema/field_aliases``). ``metadata`` is
        not a field: ``metadata.k`` is a second spelling of field ``k``, one
        level only, the same as an alias, so ``metadata.type``,
        ``metadata.evidence`` and ``metadata.links`` are those fields, an
        alias is its target, and a path whose field would be ``metadata``
        (the bare key, ``metadata.metadata.k``) is refused at any depth. The
        rest of the path (``[0].x``) follows the field unchanged.

        Where: ``spelling_in`` -- the one path the file spells the field at
        (its own name, an alias, or either under ``metadata:``), the field's
        own top-level name when the file has none. A file that spells the
        field twice refuses the update (``SpelledTwiceError``): no spelling
        is edited while another stands.

        Refusals, on the field, for every operation kind (set, append,
        remove, unset, add-subkey) alike: ``type``/``entry_type`` and the
        empty key, never settable; ``body`` as a frontmatter key; under
        ``restrict`` (a surface), a field Pyrite or the type manages
        (``_field_sets``), except an item appended to or removed from
        ``links`` or ``sources`` as a whole item -- ``links[0].x`` is refused.
        An appended link or source item is checked as one before anything is
        written (``check_link_target``: what the index could not store).
        Values are validated as the field's type (``_value_for_file``) and
        written as the caller sent them, never as the model's reading.
        """
        from ..schema.field_aliases import (
            NotAFieldError,
            SpelledTwiceError,
            field_at,
            spelling_in,
            values_identical,
        )

        if isinstance(op, ReplaceBody):
            return op
        if (
            merge_spellings
            and isinstance(op, Unset)
            and tuple(getattr(op, "path", ()) or ()) == ("metadata",)
            and _nested_block_is_all_equal_pairs(entry, file_values)
        ):
            # `qa fix`'s merge removing the last nested spelling(s): the
            # emptied `metadata:` block goes with them, so no `metadata: {}`
            # the author never wrote appears (#1001 round 4, item 4).
            return Unset(("metadata",))
        try:
            field, rest = field_at(type(entry), entry.entry_type, getattr(op, "path", None))
        except NotAFieldError as e:
            raise ValidationError(f"Cannot update {entry_id!r}: {e}") from None
        except ValueError as e:
            raise ValidationError(f"Cannot update {entry_id!r}: {e}") from None

        # `type`/`entry_type` and the empty key are never model attributes on
        # any entry (`type` is frontmatter-only; `entry_type` is a read-only
        # property), and no caller means anything sensible by setting them
        # through an update. Refused unconditionally, not gated by `restrict`.
        # Checked AFTER the KB and entry lookups: a missing KB or entry, or a
        # read-only KB, must still answer its own 404/403.
        if field in ("type", "entry_type") or field == "":
            raise ValidationError(
                f"Cannot set {field or '(empty key)'} on {entry_id!r} with an update: "
                "an entry's type is fixed once created, and a --field key must be "
                "non-empty."
            )
        if field == "body":
            # The request named `body` where only a frontmatter key can land
            # (under `metadata`): the body is the entry's text, never a key.
            raise ValidationError(
                f"Cannot set body on {entry_id!r} as a frontmatter key: the body is "
                "the entry's text; send it as `body` at the top level."
            )
        whole_item = isinstance(op, Append | Remove) and not rest
        if restrict:
            _, _, managed = self._field_sets(entry.entry_type, kb_config)
            if field in managed and not (whole_item and field in _ITEM_LISTS):
                # #557: the usual way to arrive here is a KB using one of these
                # names for a field of its own (`provenance:` on a draft), so
                # the refusal says how to keep that field.
                raise ValidationError(
                    f"Cannot set {field} on {entry.entry_type} '{entry_id}' with an "
                    "update: Pyrite maintains these fields. To keep a field of your "
                    "own under that name, rename it in the file (for example "
                    f"`{entry.entry_type}_{field}`) and update the new name."
                )
        try:
            head = spelling_in(file_values, type(entry), entry.entry_type, field)
        except SpelledTwiceError as e:
            # The one write a doubly spelled field takes, and only from `qa
            # fix`'s own call (`merge_spellings`, #1001 round 4 item 3):
            # unsetting one of its spellings when every spelling holds the
            # same YAML value, type included (`values_identical`: `true` is
            # not `1`, item 1), which loses nothing and is how the pair is
            # merged into the field's own name (decision 2). The field's own
            # name (the first spelling) is never the one removed.
            path_sent = tuple(getattr(op, "path", ()) or ())
            values = [_value_at(file_values, p) for p in e.spellings]
            if (
                merge_spellings
                and isinstance(op, Unset)
                and not rest
                and path_sent in e.spellings[1:]
                and all(values_identical(v, values[0]) for v in values)
            ):
                return Unset(path_sent)
            raise ValidationError(
                f"Cannot update {field} on {entry_id!r}: {e}. A field has one spelling "
                "per file; remove one of them by hand, or `pyrite qa fix` merges an "
                "equal pair, then update."
            ) from None

        # The output path is the tuple `field_at` decided on, with the file's
        # spelling in place of the field: never re-joined into a string that
        # `apply()` would parse again (#1001 review: a one-segment head
        # `status_change_log[0]` re-read as a list index).
        path: tuple[Any, ...] = (*head, *rest)
        if isinstance(op, Set):
            value = (
                self._value_for_file(entry, field, op.value) if not rest else _plain_value(op.value)
            )
            spelled = path[0] if isinstance(path, tuple) and len(path) == 1 else path
            if _same_instant_as_file(file_values, spelled, value):
                return Set(path, file_values[spelled])  # the same instant: no change
            return Set(path, value)
        if isinstance(op, Append | Remove):
            value = _plain_value(op.value)
            if isinstance(op, Append) and whole_item and field in _ITEM_LISTS:
                value = _checked_item(entry, field, value)
            return type(op)(path, value)
        if isinstance(op, AddSubkey):
            return AddSubkey(path, op.key, _plain_value(op.value))
        if isinstance(op, Unset):
            return Unset(path)
        raise ValidationError(f"Cannot update {entry_id!r}: unknown operation {op!r}.")

    @staticmethod
    def _value_for_file(entry: Entry, key: str, value: Any) -> Any:
        """``value`` validated as ``key``'s type, as plain data for ``apply``.

        A timestamp the caller spelled is written as sent (``TimestampText``:
        its own separator, zone form and fraction digits, bare where the
        file's value was bare), never as the model's reading of it; one that
        is no instant is refused. A ``datetime`` (an in-process caller's) is
        written in the house spelling.
        """
        if key in _TIMESTAMP_FIELDS and not isinstance(value, date):
            # A `datetime` or a `date` from an in-process caller is written as
            # the value it is (a date stays a date, #990). Text is accepted
            # iff the loader reads it back as a date or timestamp, not by
            # `fromisoformat`'s wider rule (a week date, an hour-only time or
            # a compact date would be written as a plain string or an int,
            # #990), nor by a regex beside the loader (#991). The one form
            # read as a string and still accepted is `datetime-local`'s
            # `YYYY-MM-DDTHH:MM`, written with `:00` seconds.
            spelled = str(value).strip()
            if _DATETIME_LOCAL.fullmatch(spelled):
                spelled += ":00"
            if not _yaml_reads_a_timestamp(spelled):
                raise ValidationError(
                    f"Cannot set {key} on {entry.entry_type} '{entry.id}': {spelled!r} is "
                    "not a timestamp YAML reads. Send YYYY-MM-DD, optionally followed by T "
                    "or a space, HH:MM:SS, a fraction, and Z or an offset such as +05:30 "
                    "(e.g. 2026-01-15T09:00:00Z), or YYYY-MM-DDTHH:MM, which is written "
                    "with :00 seconds."
                )
            value = TimestampText(spelled)
        if hasattr(entry, key):
            _as_field_type(entry, key, value)  # validation only: refuses before any write
        return _plain_value(value)

    def _templated_move(
        self,
        repo: KBRepository,
        kb_config: KBConfig,
        entry: Entry,
        path: Path,
        text: str,
        file_values: dict[str, Any],
    ) -> tuple[Path, str | None]:
        """The one move an update makes (decision 5): a type whose subdirectory
        is templated by a field (``backlog/{status}``) moves a *pinned* file
        when that field changes; an unpinned file stays, and the result says
        why. Returns ``(path, not_moved)``."""
        if not self._doc_mgr._uses_templated_subdir(repo, entry):
            return path, None
        subdir = repo._infer_subdir(entry)
        target = repo._contained(
            kb_config.path / subdir / path.name if subdir else kb_config.path / path.name
        )
        if target.resolve() == path.resolve():
            return path, None
        if "id" not in file_values:
            return path, "no id pin: the file keeps its place; add an `id:` line to let it move"
        try:
            KBFiles(kb_config.path).require_movable(path, target)
        except HardLinked as e:
            # A linked or hard-linked file is written in place, never moved
            # ; the result says why it stayed. Neither message names a path.
            return path, f"the file keeps its place: it has {e.links} hard links"
        except ValidationError as e:
            return path, f"the file keeps its place: {e}"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            create_entry_text(target, text, root=kb_config.path)
        except FileExistsError:
            # The update is already published at the old path; the move is
            # the part that did not happen. Said with the KB-relative target,
            # never a raw error with an absolute path (#990); the index row
            # keeps pointing at the file's real place.
            where = target.relative_to(kb_config.path).as_posix()
            return path, f"target exists: the file keeps its place; `{where}` already holds a file"
        self._doc_mgr._remove_old_file(path, kb_config.path)
        entry.file_path = target
        return target, None

    def update_entry(self, entry_id: str, kb_name: str, **updates) -> Entry:
        """
        Update an existing entry, for in-process callers.

        The keyword form of :meth:`update`, with the same checks except the
        managed-field refusal: the services that own a type's managed fields
        (TaskService's audit trail, a plugin's numbering) write them here.

        Args:
            entry_id: Entry ID to update
            kb_name: KB containing the entry
            **updates: Fields to update

        Returns:
            Updated Entry object
        """
        return self._update(entry_id, kb_name, updates, restrict=False).entry

    def delete_entry(self, entry_id: str, kb_name: str) -> bool:
        """
        Delete an entry.

        Returns:
            True if deleted, False if not found

        Raises:
            KBNotFoundError: If KB not found
            KBReadOnlyError: If KB is read-only
        """
        kb_config = self.require_kb(kb_name)
        if kb_config.read_only:
            raise KBReadOnlyError(f"KB is read-only: {kb_name}")

        # Load entry for hooks before deleting
        repo = KBRepository(kb_config)
        entry = repo.load(entry_id)
        hook_ctx = PluginContext(
            config=self.config,
            db=self.db,
            kb_name=kb_name,
            user="",
            operation="delete",
            kb_type=kb_config.kb_type,
        )
        if entry:
            entry = self._run_hooks("before_delete", entry, hook_ctx)

        # Delete from file system and index
        file_deleted = self._doc_mgr.delete_entry(entry_id, kb_name, kb_config)

        # Run after_delete hooks
        if entry:
            self._run_hooks("after_delete", entry, hook_ctx)

        return file_deleted

    def rename_entry(
        self,
        old_id: str,
        new_id: str,
        kb_name: str,
        *,
        update_links: bool = True,
        dry_run: bool = False,
    ) -> dict:
        """Rename an entry in-place: move the file, rewrite frontmatter
        id, rewrite ``[[old_id]]`` and ``[[old_id|alias]]`` wikilinks
        across this KB. Tier A r1700.

        Cross-KB wikilink rewrite, redirect-stub creation, and the
        ``move`` (subdir-change) variant are filed as r1700 follow-ups.

        Args:
            old_id: Existing entry id.
            new_id: Target id. Must not already exist in this KB.
            kb_name: KB containing the entry.
            update_links: Default True. Set False to leave references
                dangling (rare; ticket calls it out).
            dry_run: When True, return the plan without modifying disk.

        Returns:
            See KBRepository.rename for the result-dict shape.
        """
        kb_config = self.require_kb(kb_name)
        if kb_config.read_only and not dry_run:
            raise KBReadOnlyError(f"KB is read-only: {kb_name}")

        repo = KBRepository(kb_config)
        try:
            result = repo.rename(old_id, new_id, update_links=update_links, dry_run=dry_run)
        except LockTimeout as e:
            raise StorageBusyError(f"Cannot rename '{old_id}': {e}") from e
        except OSError as e:
            # A hard-linked entry file (`HardLinked`), the lock directory,
            # the filesystem: the same storage fault an update reports, its
            # detail (a real path) in the log only.
            raise _storage_error(old_id, e) from e

        # Re-sync the index so old_id resolves to None and new_id
        # resolves to the renamed entry, then read back to confirm it
        # actually worked. Skip on dry_run (nothing was written).
        #
        # The file rename already succeeded on disk by this point — that
        # part is never rolled back. A degraded index is recovered by the
        # next `pyrite index sync`, but the caller must be told the write
        # is degraded, not given a silent-success response while old_id
        # keeps resolving and new_id stays invisible
        # (verify-after-write-on-the-index-path).
        if not dry_run and result.get("renamed"):
            try:
                self._index_mgr.sync_incremental(kb_name)
            except Exception as e:
                # `e` itself is NOT interpolated into the message: it can be
                # an OSError carrying a real filesystem path, or a driver's
                # own error text -- neither is safe by construction, unlike
                # the fixed sentence below (which names only the two entry
                # ids). Logged here, with a traceback, since nothing else
                # will (this is the one and only place `e` is available).
                logger.error(
                    "Index sync failed after renaming %r -> %r in KB %r",
                    old_id,
                    new_id,
                    kb_name,
                    exc_info=e,
                )
                raise IndexSyncRecoveryHintError(
                    f"Renamed {old_id!r} -> {new_id!r} on disk, but the index "
                    f"sync failed. Run `pyrite index sync` to recover — "
                    f"until then, search/lookups may resolve the old id and "
                    f"miss the new one."
                ) from e

            if self.db.get_entry(new_id, kb_name) is None:
                raise IndexSyncRecoveryHintError(
                    f"Renamed {old_id!r} -> {new_id!r} on disk, but {new_id!r} "
                    f"did not resolve in the index after sync. Run "
                    f"`pyrite index sync` to recover."
                )
            result["index_verified"] = True

        return result

    def add_link(
        self,
        source_id: str,
        source_kb: str,
        target_id: str,
        relation: str = "related_to",
        target_kb: str | None = None,
        note: str = "",
        allow_dangling: bool = False,
    ) -> dict[str, Any]:
        """
        Add a link from one entry to another.

        Updates the source entry's frontmatter and re-indexes.

        The target is looked up index-first and confirmed against the
        repository: the SQLite index answers the common case in O(1), and a
        miss falls through to disk, which is the source of truth.

        Args:
            source_id: Source entry ID
            source_kb: Source KB name
            target_id: Target entry ID
            relation: Relationship type (default: related_to)
            target_kb: Target KB (defaults to source_kb)
            note: Optional note about the link
            allow_dangling: If True, permit linking to a target that
                doesn't exist yet (forward reference).  Otherwise raise
                EntryNotFoundError.

        Returns:
            dict with ``resolved`` (bool) indicating whether the target
            exists as of this call -- including on the duplicate-link path,
            where the write is a no-op but the target may since have gone --
            ``created`` (bool): False on that no-op path, True when a new
            link was written -- and ``relation`` (str): the relation actually
            recorded on disk, which on the no-op path can differ from the
            caller's own ``relation`` argument (``_norm_relation`` treats a
            legacy file's ``related`` as equal to the ``related_to`` default,
            so a caller's default matches without their spellings being
            identical -- round-2 cold read: a confirmation naming the
            caller's relation instead of the file's would mislead a caller
            who then greps the file for what they typed). The duplicate key
            is ``(target, kb, relation)``, with targets compared as text: a
            second, different relation between the same two entries is a new
            link, not a duplicate (#396). The check reads the file once, before
            the write; a link another writer appends between that read and
            the publish is not re-checked, and both items land (Q2: the
            publish re-applies on ``FileChanged`` and the last writer wins; a
            duplicate pair is what the check tolerates on a replay). A file
            that spells ``links`` only under ``metadata:`` gets the item
            appended there, where the reader now reads it (#1025).
        """
        # A malformed request is refused before anything is loaded or written
        # (#846); every link writer passes through this one check.
        target_id, relation, note = check_link_spec(
            target_id, relation, note=note, target_kb=target_kb
        )
        kb_config = self.require_kb(source_kb)
        if kb_config.read_only:
            raise KBReadOnlyError(f"KB is read-only: {source_kb}")

        repo = KBRepository(kb_config)
        entry = repo.load(source_id)
        if not entry:
            raise EntryNotFoundError(f"Entry not found: {source_id}")

        tkb = target_kb or source_kb
        target_kb_config = self.require_kb(tkb)

        target_repo = repo if tkb == source_kb else KBRepository(target_kb_config)

        def _target_exists() -> bool:
            """Index first, disk as the verdict.

            `self.db.get_entry` is an O(1) lookup in the SQLite index, so the
            common case -- a target that is present and indexed -- costs a row
            read instead of a directory scan. But the index is a *derived*
            cache: the markdown is the source of truth, and an entry written by
            `pyrite create` may not have an index row yet. So a miss is not an
            answer, only a reason to ask the repository.
            """
            if self.db.get_entry(target_id, tkb) is not None:
                return True
            return target_repo.load(target_id) is not None

        # Duplicates are checked before the target is validated: re-issuing a
        # link already recorded in the source's frontmatter must stay a no-op
        # even if the target has since been deleted. Otherwise anything that
        # replays a link set for idempotency -- a re-run migration, a re-driven
        # bulk script, an agent retrying a batch -- fails on links its own
        # earlier pass wrote correctly.
        for existing in entry.links:
            if (
                _same_target(existing.target, target_id)
                and self._same_link_kb(existing.kb, source_kb, tkb)
                and _norm_relation(existing.relation) == _norm_relation(relation)
            ):
                # The write is a no-op, but `resolved` is a claim about the
                # target as it is now, so check rather than assume: a link
                # recorded earlier may have been left dangling since.
                # `relation` in the result is what is actually on disk
                # (`existing.relation`), not the caller's own argument: on
                # legacy data those can differ in spelling while still
                # matching under `_norm_relation` (round-2 cold read).
                return {
                    "resolved": _target_exists(),
                    "created": False,
                    "relation": existing.relation,
                }

        resolved = _target_exists()
        if not resolved and not allow_dangling:
            raise EntryNotFoundError(f"Entry not found: {target_id}")

        # One appended item through the one write path (#955, B6 P4): the
        # file's other keys keep their bytes, `updated_at` is stamped where
        # the file carries it, and the publish is compare-and-replace under
        # the momentary lock. The item is spelled as it always was (`kb:` is
        # the target KB, the source's own included).
        item = Link(target=target_id, relation=relation, note=note, kb=tkb).to_dict()
        self.apply_operations(source_id, source_kb, [Append("links", item)])
        return {"resolved": resolved, "created": True, "relation": relation}

    def _same_link_kb(self, written: Any, source_kb: str, target_kb: str) -> bool:
        """The duplicate key's KB comparison: the KB the file's ``kb:``
        resolves to, by the rule the index stores a row's KB with
        (``PyriteConfig.link_kb``), against the requested KB (a full name:
        ``require_kb`` refuses a shortname). So ``kb: ops``, the shortname of
        ``ops-kb``, and ``--target-kb ops-kb`` are one link, whatever prefix
        the file wrote (#1053 review). The written prefix still keys the
        index's row (#1009); it does not make a second link to write."""
        return bool(self.config.link_kb(as_stored_text(written), source_kb) == target_kb)

    def add_links(
        self, kb_name: str, links: list[dict[str, Any]], *, dry_run: bool = False
    ) -> list[dict[str, Any]]:
        """Add many links whose sources are in ``kb_name``, one save per source.

        Each spec is ``{source, target, relation?, target_kb?, note?}``. A link
        already recorded on its source is skipped, keyed on
        ``(target, kb, relation)``, as :meth:`add_link` treats it -- a second,
        different relation between the same pair is created, not skipped.
        Targets are not required to exist: a bulk link set is often loaded
        before, or alongside, the entries it points at.

        Each source is loaded once, gets all of its new links, and is written
        once: one ``Append`` per new link through the same operation write
        :meth:`add_link` uses (#955, B6 P4) -- in place, wherever the file
        lives, the other keys' bytes untouched, and re-indexed. The CLI used
        to ``repo.save`` it, which re-derived the path from the type and
        wrote a second copy of any entry kept elsewhere (#375).

        Returns one result per spec, in input order:
        ``{"status": "created" | "skipped" | "failed", "error"?: str,
        "error_code"?: str}``. A spec that is malformed (not a mapping, no
        source, a target or relation :func:`check_link_spec` refuses) or names
        a ``target_kb`` that does not exist is ``failed`` before its source is
        touched, with the code that refusal carries; the other specs go on
        (#846). ``dry_run`` reports what would happen without writing.
        """
        kb_config = self._writable_kb(kb_name)
        repo = KBRepository(kb_config)

        results: list[dict[str, Any]] = [{} for _ in links]
        loaded: dict[str, Entry | None] = {}
        dirty: dict[str, list[int]] = {}
        new_items: dict[str, list[dict[str, Any]]] = {}

        for i, spec in enumerate(links):
            try:
                source_id, target_id, relation, note, tkb = self._checked_bulk_spec(spec, kb_name)
            except (ValidationError, KBNotFoundError) as e:
                results[i] = {"status": "failed", "error": str(e), "error_code": e.error_code}
                continue
            if source_id not in loaded:
                loaded[source_id] = repo.load(source_id)
            entry = loaded[source_id]
            if entry is None:
                results[i] = {"status": "failed", "error": f"source entry not found: {source_id}"}
                continue
            # Duplicate key matches add_link's: (target, kb, relation) (#396),
            # normalised the same way so legacy data (no `relation:` key,
            # loaded as "related") matches this method's "related_to" default.
            if any(
                _same_target(existing.target, target_id)
                and self._same_link_kb(existing.kb, kb_name, tkb)
                and _norm_relation(existing.relation) == _norm_relation(relation)
                for existing in entry.links
            ):
                results[i] = {"status": "skipped"}
                continue
            # The model's list is what the duplicate check above reads, so a
            # second spec for the same link in this batch is skipped too.
            link = Link(target=target_id, relation=relation, note=note, kb=tkb)
            entry.links.append(link)
            new_items.setdefault(source_id, []).append(link.to_dict())
            results[i] = {"status": "created"}
            dirty.setdefault(source_id, []).append(i)

        if dry_run:
            return results

        for source_id, positions in dirty.items():
            try:
                self.apply_operations(
                    source_id, kb_name, [Append("links", item) for item in new_items[source_id]]
                )
            except Exception as e:
                # Once per failed save, not once per position: one cause, one line.
                message = _safe_message(e)
                for i in positions:
                    results[i] = {"status": "failed", "error": message}
        return results

    def _checked_bulk_spec(self, spec: Any, kb_name: str) -> tuple[str, str, str, str, str]:
        """One ``add_links`` spec as ``(source, target, relation, note,
        target_kb)``, or the refusal that fails it alone."""
        if not isinstance(spec, dict):
            raise ValidationError(f"expected a mapping, got {type(spec).__name__}")
        for key in ("source", "target"):
            if spec.get(key) is None:
                raise ValidationError(f"missing required field '{key}'")
        source_id = spec["source"]
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValidationError(f"A link needs a source entry id; got {source_id!r}.")
        target_id, relation, note = check_link_spec(
            spec["target"],
            spec.get("relation"),
            note=spec.get("note"),
            target_kb=spec.get("target_kb"),
        )
        tkb = spec.get("target_kb") or kb_name
        self.require_kb(tkb)
        return source_id, target_id, relation, note, tkb

    # =========================================================================
    # Query Operations (read-only, delegate to db)
    # =========================================================================

    def get_entries(self, ids: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """Batch-get multiple entries by (entry_id, kb_name) pairs."""
        return self.db.get_entries(ids)

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
        status: str | None = None,
        min_importance: int | None = None,
    ) -> list[dict[str, Any]]:
        """List entries with pagination."""
        return self.db.list_entries(
            kb_names=kb_names,
            kb_name=kb_name,
            entry_type=entry_type,
            tag=tag,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
            status=status,
            min_importance=min_importance,
        )

    def list_collections(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List all collection entries, optionally restricted to readable KBs."""
        return self.list_entries(kb_name=kb_name, kb_names=kb_names, entry_type="collection")

    @staticmethod
    def _normalize_metadata_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Parse the ``metadata`` field on each row to a dict.

        Raw-SQL list paths (``_exec``-based) return ``metadata`` as a
        JSON-encoded string straight from the column, while the ORM
        single-entry path (``_entry_to_dict``) returns it parsed. REST callers
        rely on the parsed shape (``EntryResponse.metadata: dict``), so this
        helper aligns the list-path contract.

        See ``bug-collection-entries-endpoint-metadata-string-pydantic-rejection``
        for the broader latent-bug class; this is the narrow per-call fix.
        """
        for r in rows:
            if "metadata" in r:
                r["metadata"] = parse_metadata(r["metadata"])
        return rows

    def get_collection_entries(
        self,
        collection_id: str,
        kb_name: str,
        sort_by: str = "title",
        sort_order: str = "asc",
        limit: int = 200,
        offset: int = 0,
        *,
        readable_kbs: set[str] | None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Get entries belonging to a collection (folder-based or query-based).

        ``readable_kbs`` is the *viewer's* readable set (None: unscoped). A
        stored query is evaluated with it, not with its author's: a query
        naming a KB the viewer may not read returns what a KB that does not
        exist returns -- nothing.

        Returns:
            Tuple of (entries, total_count). Each entry's ``metadata`` field
            is guaranteed to be a dict (parsed from the column JSON), not a
            string — so REST callers can pass rows directly to
            ``EntryResponse(**r)``.

        Raises:
            EntryNotFoundError: If collection not found
        """
        entry = self.get_entry(collection_id, kb_name, readable_kbs=readable_kbs)
        if not entry or entry.get("entry_type") != "collection":
            raise EntryNotFoundError(f"Collection not found: {collection_id}")
        # A single-entry read spells a declared field (`source_type`,
        # `folder_path`, `query`, `entry_filter`) at its own name and keeps
        # `metadata` for undeclared keys (#1025, option B); the bag is read
        # second for a dict shaped like a row.
        metadata = declared_over_bag(entry, ("source_type", "folder_path", "query", "entry_filter"))

        source_type = metadata.get("source_type", "folder")

        # Virtual collection (query-based)
        if source_type == "query":
            entries, total = self._get_query_collection_entries(
                metadata, kb_name, sort_by, sort_order, limit, offset, readable_kbs=readable_kbs
            )
            return self._normalize_metadata_rows(entries), total

        # Folder-based collection (Phase 1)
        folder_path = metadata.get("folder_path", "") if isinstance(metadata, dict) else ""
        if not folder_path:
            return [], 0
        kb_config = self.require_kb(kb_name)
        abs_folder = str(kb_config.path / folder_path)
        entries = self.db.list_entries_in_folder(
            kb_name, abs_folder, sort_by, sort_order, limit, offset
        )
        total = self.db.count_entries_in_folder(kb_name, abs_folder)
        return self._normalize_metadata_rows(entries), total

    def _get_query_collection_entries(
        self,
        metadata: dict,
        kb_name: str,
        sort_by: str,
        sort_order: str,
        limit: int,
        offset: int,
        *,
        readable_kbs: set[str] | None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Evaluate a query-based virtual collection within ``readable_kbs``."""
        from .collection_query import (
            evaluate_query_cached,
            parse_query,
            query_from_dict,
        )

        query_str = metadata.get("query", "")
        entry_filter = metadata.get("entry_filter", {})

        if query_str:
            query = parse_query(query_str)
        elif entry_filter and isinstance(entry_filter, dict):
            query = query_from_dict(entry_filter)
        else:
            return [], 0

        # Override sort/pagination from caller
        query.sort_by = sort_by
        query.sort_order = sort_order
        query.limit = limit
        query.offset = offset

        # Default kb_name if not set in query
        if not query.kb_name:
            query.kb_name = kb_name

        return evaluate_query_cached(query, self.db, readable_kbs=readable_kbs)

    def evaluate_collection_query(
        self, query: Any, *, readable_kbs: set[str] | None
    ) -> tuple[list[dict[str, Any]], int]:
        """Evaluate a parsed collection query within the caller's readable set.

        A KB the query names in its own text is authorized against the same
        set (``collection_query.evaluate_query``); None is unscoped.
        """
        from .collection_query import evaluate_query

        return evaluate_query(query, self.db, readable_kbs=readable_kbs)

    def count_entries(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
        entry_type: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        min_importance: int | None = None,
    ) -> int:
        """Count entries, optionally filtered."""
        return self.db.count_entries(
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
        """Get distinct entry types from the database.

        ``kb_names`` restricts the result to the caller's readable KBs, so a
        private KB's entry types never appear in the cross-KB aggregate.
        """
        return self.db.get_distinct_types(kb_name=kb_name, kb_names=kb_names)

    def get_timeline(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        min_importance: int = 1,
        kb_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort_order: str = "asc",
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get timeline events ordered by date.

        ``kb_names`` restricts the result to the caller's readable KBs;
        pushed into the query so ``limit`` and the reported count are
        computed over readable rows only.
        """
        return self.db.get_timeline(
            date_from=date_from,
            date_to=date_to,
            min_importance=min_importance,
            kb_name=kb_name,
            limit=limit,
            offset=offset,
            sort_order=sort_order,
            kb_names=kb_names,
        )

    def get_tags(
        self,
        kb_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
        prefix: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get tags with counts as dicts.

        ``kb_names`` restricts the result to the caller's readable KBs, so
        that neither a tag name nor its count comes from a KB they cannot
        read.
        """
        return self.db.get_tags_as_dicts(
            kb_name=kb_name, limit=limit, offset=offset, prefix=prefix, kb_names=kb_names
        )

    def get_most_linked(self, kb_name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Get most referenced entries."""
        return self.db.get_most_linked(kb_name, limit)

    def get_orphans(
        self, kb_name: str | None = None, *, readable_kbs: set[str] | None
    ) -> list[dict[str, Any]]:
        """Get entries with no links a caller within ``readable_kbs`` can see."""
        return self.db.get_orphans(kb_name, readable_kbs=readable_kbs)

    def get_tag_tree(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict]:
        """Get hierarchical tag tree, optionally restricted to readable KBs."""
        return self.db.get_tag_tree(kb_name=kb_name, kb_names=kb_names)

    def search_by_tag_prefix(
        self, prefix: str, kb_name: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Search entries by tag prefix (includes child tags)."""
        return self.db.search_by_tag_prefix(prefix, kb_name=kb_name, limit=limit)

    @staticmethod
    def _operational_contracts() -> dict[str, Any]:
        """Operational contracts a cold agent needs to use pyrite correctly,
        surfaced from the tool itself rather than left to external skill
        docs or an operator's memory (docs-operational-contracts-travel-
        with-tool). Kept in sync with the canonical wording in
        pyrite/utils/errors.py (error contract) and
        pyrite/server/tool_schemas.py's kb_search description (auto-quote
        rule) -- update all three together if either changes."""
        return {
            "indexing": (
                "Entries are only searchable once indexed. Direct file "
                "writes under a KB's path (not via `pyrite create`/`update`) "
                "need `pyrite index sync` afterward -- it's incremental "
                "and cheap, safe to run after every batch of writes."
            ),
            "error_contract": {
                "shape": "{error, error_code, suggestion?, retryable}",
                "error": "human-readable message",
                "error_code": "machine-readable code, e.g. QUERY_SYNTAX, KB_NOT_FOUND",
                "suggestion": "optional fix hint, omitted when not applicable",
                "retryable": "bool -- whether retrying the same request could succeed",
            },
            "search_quoting": (
                "Special-char tokens (hyphens, dots, colons) are "
                "auto-quoted ONLY when the query has no AND/OR/NOT operator "
                "and no existing quote. Once you use an operator or a "
                "phrase quote, quote special-char tokens yourself (e.g. "
                '\'"family separation" "cross-link"\') or the query can '
                "fail with error_code QUERY_SYNTAX (deterministic, not "
                "retryable)."
            ),
            "task_claims": (
                "Task claims are atomic; a lost race means the task is "
                "already claimed by someone else. On conflict, do NOT "
                "override the claim -- re-run the task list and pick a "
                "different item."
            ),
        }

    #: `orient`'s `detail` values. `full` is the default until 0.25.8
    #: (maintainer, 2026-10-01); `brief` is the read session's answer.
    ORIENT_DETAILS = ("brief", "full")
    #: What `detail="brief"` drops from `schema`, at the KB level and per type
    #: (and `guidelines` from the top level too): the blocks a caller needs to
    #: write an entry, not to read the KB. `relationship_types` is kept: a read
    #: session following links needs the relation names (review round 1).
    _ORIENT_WRITE_SIDE_KEYS = frozenset(
        {"ai_instructions", "evaluation_rubric", "guidelines", "goals"}
    )
    #: Cap on near-match KB names offered for a name that is not a KB.
    KB_NAME_SUGGESTION_LIMIT = 3

    @classmethod
    def _orient_detail(cls, detail: str | None) -> str:
        """`detail` as one of `ORIENT_DETAILS`; absent is `full`. Decided here
        so MCP, the CLI and REST refuse the same values with the same code."""
        if detail is None:
            return "full"
        if detail not in cls.ORIENT_DETAILS:
            raise ValidationError(f"detail must be 'brief' or 'full', not {detail!r}")
        return detail

    def kb_name_suggestions(
        self, kb_name: Any, *, readable_kbs: set[str] | None
    ) -> tuple[list[str], str]:
        """Near matches for a name that is not a KB, and the hint to show.

        Drawn only from the KBs the caller may read (`readable_kbs`, required;
        `UNSCOPED` is the caller for whom that is every KB) and capped: a name
        offered from outside that set would confirm a private KB exists. The
        hint names no KB when nothing matches. The rule is the resolver's
        (`kb_resolver.near_kb_names`, #952); this is what it says.
        """
        from .kb_resolver import HOW_TO_LIST_KBS, near_kb_names

        matches = near_kb_names(self.config, kb_name, readable_kbs)
        if matches:
            return matches, f"Did you mean: {', '.join(matches)}? {HOW_TO_LIST_KBS}"
        return [], HOW_TO_LIST_KBS

    def orient_overview(
        self,
        *,
        readable_kbs: set[str] | None,
        detail: str | None = None,
        limit: int | None = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Orient with no KB named: the first call of a session.

        The KBs the caller may read (`readable_kbs`, required; `UNSCOPED` is
        the caller with no per-KB scoping) and nothing about any other, the operational contracts, and
        what to call next. One shape whether there are zero, one or many
        KBs. `detail` is validated and otherwise has no effect: there are no
        per-type blocks here to omit.
        """
        self._orient_detail(detail)
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100
        ):
            raise ValidationError("limit must be between 1 and 100")
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
            or (limit is None and offset)
        ):
            raise ValidationError("offset must be non-negative and requires a limit")

        registry = self._registry
        if registry is None:
            from .kb_registry_service import KBRegistryService

            registry = KBRegistryService(self.config, self.db)
        page = registry.list_kbs(
            limit=limit + 1 if limit is not None else None,
            offset=offset,
            kb_names=readable_kbs,
            include_yaml_origin=False,
        )
        has_more = limit is not None and len(page) > limit
        if limit is not None:
            page = page[:limit]
        kbs = [
            {
                "name": kb["name"],
                "type": kb["type"],
                "description": kb.get("description") or "",
                "entry_count": kb["entries"],
                "read_only": kb.get("read_only", False),
            }
            for kb in page
        ]
        result = {
            "knowledge_bases": kbs,
            "operational_contracts": self._operational_contracts(),
            "next": (
                "Orient in one KB: kb_orient with kb_name (MCP) or `pyrite orient -k <name>` "
                "(CLI). Add detail='brief' (`--detail brief`) for a read session."
            ),
        }
        if limit is not None:
            result.update({"limit": limit, "offset": offset, "has_more": has_more})
        return result

    def orient(
        self, kb_name: str, recent_limit: int = 5, *, detail: str | None = None
    ) -> dict[str, Any]:
        """One-shot KB orientation summary for agents entering a new KB.

        `detail="brief"` drops the write-side blocks from `schema` and adds
        `detail` and `detail_note`; absent or `"full"` adds nothing, so an
        existing caller's response is unchanged.
        """
        detail = self._orient_detail(detail)
        kb_config = self.require_kb(kb_name)

        total = self.count_entries(kb_name=kb_name)
        distinct_types = self.get_distinct_types(kb_name=kb_name)

        # Per-type counts
        types = []
        for t in distinct_types:
            count = self.count_entries(kb_name=kb_name, entry_type=t)
            types.append({"type": t, "count": count})
        types.sort(key=lambda x: x["count"], reverse=True)

        # Top tags
        top_tags = self.get_tags(kb_name=kb_name, limit=10)

        # Recent entries (slim)
        recent = self.list_entries(
            kb_name=kb_name,
            sort_by="updated_at",
            sort_order="desc",
            limit=recent_limit,
        )
        recent_slim = [
            {
                "id": e.get("id"),
                "title": e.get("title"),
                "entry_type": e.get("entry_type"),
                "updated_at": e.get("updated_at"),
            }
            for e in recent
        ]

        # Schema info
        schema_info = {}
        if kb_config.kb_schema:
            try:
                schema_info = kb_config.kb_schema.to_agent_schema()
            except Exception:
                logger.warning("Failed schema-to-agent conversion", exc_info=True)

        # Guidelines from config (if available)
        guidelines = getattr(kb_config, "guidelines", None) or {}

        result = {
            "kb": kb_name,
            "description": kb_config.description or "",
            "kb_type": kb_config.kb_type or "default",
            "read_only": kb_config.read_only,
            "guidelines": guidelines,
            "total_entries": total,
            "types": types,
            "top_tags": top_tags,
            "recent": recent_slim,
            "schema": schema_info,
            "operational_contracts": self._operational_contracts(),
        }

        # A KB type that belonged to an extension Pyrite has deleted (#953):
        # said here, the first call an agent makes in a KB, and nowhere else
        # but `index health`; the rule is `plugins/removed.py`'s.
        from ..plugins.removed import removed_extension_notice

        notice = removed_extension_notice(kb_name, kb_config.kb_type, distinct_types)
        if notice:
            result["notices"] = [notice]

        # Plugin orient supplements
        from ..plugins.registry import get_registry

        supplements = get_registry().get_orient_supplements(kb_name, kb_config.kb_type or "default")

        if detail == "brief":
            drop = self._ORIENT_WRITE_SIDE_KEYS
            brief_schema = {k: v for k, v in schema_info.items() if k not in drop}
            if "types" in brief_schema:
                brief_schema["types"] = {
                    name: {k: v for k, v in info.items() if k not in drop}
                    for name, info in brief_schema["types"].items()
                }
            result["schema"] = brief_schema
            # The top-level `guidelines` goes with `schema.guidelines`: left
            # behind as `{}` it would read as "this KB has none".
            result.pop("guidelines", None)

        if supplements:
            result.update(supplements)

        if detail == "brief":
            # Set after the supplements so a plugin key named `detail` cannot
            # overwrite them. A plugin's block is kept whole: the service
            # cannot tell which of a plugin's keys are write-side without a
            # new plugin shape (ADR-0040), so `brief` is not a bound for a KB
            # whose plugin supplement is large (ADR-0034). Said in the
            # response, not hidden.
            result["detail"] = "brief"
            result["detail_note"] = (
                "brief omits ai_instructions, evaluation_rubric, guidelines and goals from "
                "schema, and the top-level guidelines. Get them with kb_schema (MCP), "
                "`pyrite orient -k <kb> --detail full` (CLI) or detail='full'. Blocks added by "
                "a plugin are returned whole, so brief does not bound their size."
            )

        return result

    def generate_readme(self, kb_name: str) -> str:
        """Generate a README.md for a knowledge base."""
        kb_config = self.require_kb(kb_name)

        description = kb_config.description or ""
        total = self.count_entries(kb_name=kb_name)
        distinct_types = self.get_distinct_types(kb_name=kb_name)

        # Per-type counts
        type_counts: list[tuple[str, int]] = []
        for t in distinct_types:
            count = self.count_entries(kb_name=kb_name, entry_type=t)
            type_counts.append((t, count))
        type_counts.sort(key=lambda x: x[1], reverse=True)

        # Build markdown
        lines: list[str] = [f"# {kb_name}", ""]
        if description:
            lines += [description, ""]

        # Contents table
        if type_counts:
            lines += ["## Contents", "", "| Type | Count |", "|------|-------|"]
            for t, count in type_counts:
                lines.append(f"| {t} | {count} |")
            lines.append("")

        # Entries grouped by type
        if total > 0:
            lines.append("## Entries")
            lines.append("")
            for entry_type, _ in type_counts:
                entries = self.list_entries(
                    kb_name=kb_name,
                    entry_type=entry_type,
                    sort_by="importance",
                    sort_order="desc",
                    limit=500,
                )
                type_label = entry_type.replace("_", " ").title()
                lines.append(f"### {type_label}")
                lines.append("")
                for e in entries:
                    title = e.get("title", "Untitled")
                    eid = e.get("id", "")
                    imp = e.get("importance")
                    suffix = f" — importance: {imp}" if imp is not None else ""
                    lines.append(f"- **{title}** (`{eid}`){suffix}")
                lines.append("")

        # Footer
        from .branding_service import DEFAULT_BRAND_NAME, BrandingService

        brand = BrandingService(self.config.settings.branding_dir).get()
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        if brand.name == DEFAULT_BRAND_NAME:
            footer = f"*Generated by Pyrite on {date}*"
        else:
            footer = f"*Generated by {brand.name} (powered by Pyrite) on {date}*"
        lines.append("---")
        lines.append(footer)
        lines.append("")

        return "\n".join(lines)

    # Settings: use db.get_setting / db.set_setting / db.get_all_settings /
    # db.delete_setting directly — thin wrappers removed in 0.9.

    # =========================================================================
    # Wikilink delegation (implementation in WikilinkService)
    # =========================================================================

    def list_entry_titles(
        self,
        kb_name: str | None = None,
        query: str | None = None,
        limit: int = 500,
        *,
        readable_kbs: set[str] | None,
    ) -> list[dict[str, Any]]:
        """Lightweight listing of entry IDs and titles for wikilink autocomplete."""
        return self.wikilinks.list_entry_titles(
            kb_name=kb_name, query=query, limit=limit, readable_kbs=readable_kbs
        )

    def resolve_entry(
        self, target: str, kb_name: str | None = None, *, readable_kbs: set[str] | None
    ) -> dict[str, Any] | None:
        """Resolve a wikilink target to an entry. Supports kb:id format for cross-KB links."""
        return self.wikilinks.resolve_entry(target, kb_name=kb_name, readable_kbs=readable_kbs)

    def resolve_batch(
        self,
        targets: list[str],
        kb_name: str | None = None,
        *,
        readable_kbs: set[str] | None,
    ) -> dict[str, bool]:
        """Batch-resolve wikilink targets. Supports kb:id format."""
        return self.wikilinks.resolve_batch(targets, kb_name=kb_name, readable_kbs=readable_kbs)

    def get_wanted_pages(
        self, kb_name: str | None = None, limit: int = 100, *, readable_kbs: set[str] | None
    ) -> list[dict[str, Any]]:
        """Get link targets that don't exist as entries (wanted pages)."""
        return self.wikilinks.get_wanted_pages(
            kb_name=kb_name, limit=limit, readable_kbs=readable_kbs
        )

    def check_links(
        self,
        kb_name: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Check for broken links, grouped by missing target."""
        return self.wikilinks.check_links(kb_name=kb_name, limit=limit)

    def list_daily_dates(self, kb_name: str, month: str) -> list[str]:
        """List dates that have daily notes for a given month (YYYY-MM)."""
        prefix = f"daily-{month}"
        sql = "SELECT id FROM entry WHERE kb_name = :kb_name AND id LIKE :prefix ORDER BY id"
        rows = self.db.execute_sql(sql, {"kb_name": kb_name, "prefix": f"{prefix}%"})

        dates = []
        for row in rows:
            entry_id = row["id"]
            if entry_id.startswith("daily-") and len(entry_id) >= 16:
                dates.append(entry_id[6:])  # strip "daily-"
        return dates

    def require_entry(self, entry_id: str, kb_name: str) -> None:
        """Raise unless ``entry_id`` exists in ``kb_name``: an empty answer about
        a subject that is not there is a lie, so a reader asks this first.

        Index first, disk as the verdict, as ``add_link``'s target check does:
        the index is a derived cache, so a miss in it is a reason to ask the
        repository, not an answer (design principle 5). Raises
        ``KBNotFoundError`` for an unknown KB, ``EntryNotFoundError`` for an
        absent entry.
        """
        kb_config = self.require_kb(kb_name)
        if self.db.get_entry(entry_id, kb_name) is not None:
            return
        if KBRepository(kb_config).load(entry_id) is not None:
            return
        # A link to a page that does not exist yet is stored and answered (a
        # red link, `[[ghost-page]]`): the subject is absent, but the question
        # "what links here" has an answer. Refuse only when nothing links to it.
        if self.db.get_backlinks(entry_id, kb_name, limit=1, readable_kbs=None):
            return
        raise EntryNotFoundError(f"Entry not found: {entry_id}")

    def load_entry_from_disk(self, entry_id: str, kb_name: str) -> Entry | None:
        """Load an entry from disk via KBRepository."""
        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            return None
        repo = KBRepository(kb_config)
        return repo.load(entry_id)

    def index_entry_from_disk(self, entry: Entry, kb_name: str) -> None:
        """Index an entry that was loaded from disk."""
        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            return
        repo = KBRepository(kb_config)
        file_path = repo.find_file(entry.id)
        if file_path:
            self._doc_mgr.index_entry(entry, kb_name, file_path)

    # =========================================================================
    # Protocol-level operations
    # =========================================================================

    def _refuse_claim_of_complete_task(self, entry_id: str, kb_name: str) -> dict[str, Any] | None:
        """Refuse to claim a task whose children are all resolved.

        ADR-0042 decision 4 allows a refusal; it closes the risk the ADR names
        when the parent rollup stopped writing (a worker could claim a parent
        whose work is finished). The answer comes from the same function every
        other reader uses (``derive_completion``), so a claim, a filter and a
        read never disagree. A task whose own file says ``done`` or
        ``cancelled`` is left to the CAS below, which refuses it with the
        status.

        Check-then-CAS, so the answer can be stale in either direction: a
        child that resolves between the check and the CAS lets a claim
        through on a task that has just become complete, and a child that
        reopens (or is added) in that window gives a refusal that a retry
        would not. Both windows are the length of one index read.
        """
        rows = self.db.execute_sql(
            "SELECT entry_type FROM entry WHERE id = :entry_id AND kb_name = :kb_name",
            {"entry_id": entry_id, "kb_name": kb_name},
        )
        if not rows or rows[0].get("entry_type") != "task":
            return None
        from .task_service import TaskService

        derived = TaskService(self.config, self.db, kb_svc=self).derived_for(kb_name, entry_id)
        completion = derived["completion"]
        if not (completion["complete"] and completion["basis"] == "children"):
            return None
        return {
            "claimed": False,
            "error": (
                f"Task '{entry_id}' counts as done: all {completion['children']} of its "
                "children are resolved (derived), so there is nothing left to claim. "
                "Add an open subtask with `pyrite task create 'New work' "
                f"-k {shlex.quote(kb_name)} --parent {shlex.quote(entry_id)}`, "
                "then claim again. Or mark the completed parent done with "
                f"`pyrite task update {shlex.quote(entry_id)} -k {shlex.quote(kb_name)} -s done`."
            ),
            "current_status": derived["effective_status"],
            "derived": derived,
        }

    def claim_entry(
        self,
        entry_id: str,
        kb_name: str,
        assignee: str,
        *,
        from_status: str = "open",
        to_status: str = "claimed",
    ) -> dict[str, Any]:
        """Atomically claim an Assignable + Statusable entry via CAS.

        Uses compare-and-swap on the index to ensure only one agent can claim.
        On success, updates the markdown file to match.

        Returns:
            Dict with claimed=True on success, or error details.
        """
        from sqlalchemy import text

        refusal = self._refuse_claim_of_complete_task(entry_id, kb_name)
        if refusal:
            return refusal

        if getattr(self.db, "is_change_set", False):
            return self._claim_in_change_set(entry_id, kb_name, assignee, from_status, to_status)

        session = self.db.session

        # CAS: only update if status matches from_status
        if from_status == "open":
            status_clause = "(status = :from_status OR status IS NULL)"
        else:
            status_clause = "status = :from_status"
        result = session.execute(
            text(f"""UPDATE entry
               SET status = :to_status,
                   assignee = :assignee
               WHERE id = :entry_id AND kb_name = :kb_name
               AND {status_clause}"""),
            {
                "assignee": assignee,
                "entry_id": entry_id,
                "kb_name": kb_name,
                "to_status": to_status,
                "from_status": from_status,
            },
        )
        session.commit()

        if result.rowcount == 0:
            rows = self.db.execute_sql(
                "SELECT status FROM entry WHERE id = :entry_id AND kb_name = :kb_name",
                {"entry_id": entry_id, "kb_name": kb_name},
            )
            if not rows:
                return {
                    "claimed": False,
                    "error": f"Entry '{entry_id}' not found in KB '{kb_name}'",
                }
            current = rows[0].get("status", from_status)
            return {
                "claimed": False,
                "error": f"Entry '{entry_id}' is '{current}', not '{from_status}'",
                "current_status": current,
            }

        # Update the markdown file to match
        try:
            self.update_entry(entry_id, kb_name, status=to_status, assignee=assignee)
        except Exception as e:
            # Rollback index CAS on file error
            logger.warning("File update failed for claim on %s, rolling back: %s", entry_id, e)
            session.execute(
                text("""UPDATE entry
                   SET status = :from_status,
                       assignee = NULL
                   WHERE id = :entry_id AND kb_name = :kb_name"""),
                {"entry_id": entry_id, "kb_name": kb_name, "from_status": from_status},
            )
            session.commit()
            return {"claimed": False, "error": f"File update failed: {e}"}

        return {
            "claimed": True,
            "task_id": entry_id,
            "assignee": assignee,
            "status": to_status,
        }

    def lands_in(self, principal, kb_name: str):
        """Where `principal`'s write to `kb_name` lands: the landing rule,
        `AccessPolicy.lands_in` (ADR-0044 §1), for a surface that holds this
        service rather than the policy."""
        from .access_policy import AccessPolicy

        return AccessPolicy(self.config, self.db).lands_in(principal, kb_name)

    def _claim_in_change_set(
        self, entry_id: str, kb_name: str, assignee: str, from_status: str, to_status: str
    ) -> dict[str, Any]:
        """A claim whose write lands in the claimer's change set (a KB with
        `landing: review`; ADR-0044 §1, decided 2026-10-20: claims follow the
        rule). The compare-and-swap above runs on the shared index, which is
        main: here the claim is the claimer's own, so the status is read
        through the overlay and the change is the file write alone. Other
        users do not see it until the change set merges."""
        entry = self.db.get_entry(entry_id, kb_name)
        if not entry:
            return {"claimed": False, "error": f"Entry '{entry_id}' not found in KB '{kb_name}'"}
        current = entry.get("status")
        if not (current == from_status or (from_status == "open" and not current)):
            return {
                "claimed": False,
                "error": f"Entry '{entry_id}' is '{current}', not '{from_status}'",
                "current_status": current,
            }
        self.update_entry(entry_id, kb_name, status=to_status, assignee=assignee)
        return {"claimed": True, "task_id": entry_id, "assignee": assignee, "status": to_status}

    # =========================================================================
    # Hooks
    # =========================================================================

    def _run_hooks(
        self, hook_name: str, entry: Entry, context: PluginContext | dict[str, Any]
    ) -> Entry:
        """Run core hooks then plugin hooks, both via HookRunner.

        Hook ordering:
        - ``before_save`` / ``before_delete``: Run BEFORE persistence. If any hook
          raises, the operation is aborted — the entry is NOT saved. All
          exceptions propagate to the caller.
        - ``after_save`` / ``after_delete``: Run AFTER persistence. The entry is
          already committed. Exceptions are logged but swallowed — the operation
          is considered successful.

        HookRunner owns the raise/swallow contract for both phases (#379);
        this is a one-line delegation.
        """
        method = getattr(self.hook_runner, f"run_{hook_name}", None)
        if method is None:
            raise ValueError(f"Unknown hook name: {hook_name}")
        return method(entry, context)

    # =========================================================================
    # Index Operations
    # =========================================================================

    def reconcile_templated_subdirectories(
        self, kb_name: str, *, apply: bool = False
    ) -> ReconcileResult:
        """Reconcile misplaced files whose type uses a templated subdirectory.

        Static subdirectories preserve deliberate placement, collection YAML files
        describe their containing folders, and filenames remain fixed at creation.
        """
        kb_config = self.require_kb(kb_name)

        repo = KBRepository(kb_config)
        planned: list[tuple[Entry, Path, Path]] = []
        result = ReconcileResult()

        for entry, current_path in repo.list_entries():
            if current_path.name == "__collection.yaml":
                continue
            try:
                if not DocumentManager._uses_templated_subdir(repo, entry):
                    continue
                inferred_subdir = repo._infer_subdir(entry)
                expected_dir = repo._get_file_path(entry.id, inferred_subdir).parent
                target_path = repo._contained(expected_dir / current_path.name)
                if current_path.parent.resolve() != expected_dir.resolve():
                    planned.append((entry, current_path, target_path))
            except Exception as exc:
                # A bad template (such as a directory symlink outside the KB)
                # should be reported without preventing other entries from running.
                result.errors.append((entry.id, str(exc)))

        result.moves = [
            (entry.id, current_path, target_path) for entry, current_path, target_path in planned
        ]
        if not apply:
            return result
        if kb_config.read_only:
            result.read_only = True
            return result

        files = KBFiles(kb_config.path)
        for entry, current_path, target_path in planned:
            try:
                # The same file moved by name (an atomic link, never over a
                # file there), inside the root only, never a link moved as
                # its target nor a hard-linked file; the folders are opened
                # beneath the root without following a link.
                files.move(current_path, target_path)
                try:
                    entry.file_path = target_path
                    self._index_mgr.index_entry(entry, kb_name, target_path)
                except Exception:
                    try:
                        files.move(target_path, current_path)
                    except (OSError, ValidationError):
                        logger.exception("Could not roll back reconcile move for %s", entry.id)
                    entry.file_path = current_path
                    raise
                result.moved += 1
            except Exception as exc:
                result.errors.append((entry.id, str(exc)))

        return result

    def sync_index(self, kb_name: str | None = None) -> dict[str, Any]:
        """
        Synchronize index with file system.

        Args:
            kb_name: Specific KB to sync, or None for all

        Returns:
            Sync statistics
        """
        return self._index_mgr.sync_incremental(kb_name)

    def index_is_empty(self) -> bool:
        """True when nothing is indexed yet (the first search builds it)."""
        return self._index_mgr.is_empty()

    def build_index(self) -> Any:
        """Index every configured KB from its files (``IndexManager.index_all``)."""
        return self._index_mgr.index_all()

    def index_kb(self, kb_name: str) -> int:
        """Index one KB from its files; returns the entry count (``IndexManager.index_kb``)."""
        return self._index_mgr.index_kb(kb_name)

    def check_index_health(self, kb_name: str | None = None) -> dict[str, Any]:
        """Compare the index with the files (``IndexManager.check_health``)."""
        return self._index_mgr.check_health(kb_name)

    def stale_kbs(self, kb_name: str | None = None) -> list[str]:
        """Names of KBs with a file on disk newer than the index (cheap mtime probe).

        Scoped to ``kb_name`` when given.
        """
        stale = self._index_mgr.check_staleness([kb_name] if kb_name else None)
        return [s["kb"] for s in stale]

    def get_index_stats(self) -> dict[str, Any]:
        """Get index statistics."""
        return self._index_mgr.get_index_stats()

    def get_pending_changes(self, kb_name: str) -> dict:
        """
        Get uncommitted changes in a KB, presented as entry-level changes.

        Returns dict with:
            changes: list of {change_type, file_path, title, entry_type,
                              entry_id, current_body, previous_body}
            summary: {total, created, modified, deleted}
        """
        from ..services.git_service import GitService

        kb_config = self.require_kb(kb_name)

        kb_path = kb_config.path
        if not GitService.is_git_repo(kb_path):
            return {
                "changes": [],
                "summary": {"total": 0, "created": 0, "modified": 0, "deleted": 0},
            }

        status = GitService.get_status(kb_path)
        if status["clean"]:
            return {
                "changes": [],
                "summary": {"total": 0, "created": 0, "modified": 0, "deleted": 0},
            }

        changes = []
        counts = {"created": 0, "modified": 0, "deleted": 0}

        # Combine all changed files
        all_files: dict[str, str] = {}  # filename -> change_type
        for f in status.get("untracked", []):
            if f.endswith(".md"):
                all_files[f] = "created"
        for f in status.get("unstaged", []):
            if f.endswith(".md"):
                if not (kb_path / f).exists():
                    all_files[f] = "deleted"
                elif f not in all_files:
                    all_files[f] = "modified"
        for f in status.get("staged", []):
            if f.endswith(".md") and f not in all_files:
                if not (kb_path / f).exists():
                    all_files[f] = "deleted"
                else:
                    all_files[f] = "modified"

        for file_path, change_type in all_files.items():
            entry_info = self._parse_change_entry(kb_path, file_path, change_type)
            changes.append(entry_info)
            counts[change_type] = counts.get(change_type, 0) + 1

        counts["total"] = len(changes)
        return {"changes": changes, "summary": counts}

    def _parse_change_entry(self, kb_path: Path, file_path: str, change_type: str) -> dict:
        """Parse an entry file to extract metadata for a change record."""
        from ..services.git_service import GitService

        result = {
            "change_type": change_type,
            "file_path": file_path,
            "title": file_path,
            "entry_type": "unknown",
            "entry_id": None,
            "current_body": None,
            "previous_body": None,
        }

        # Get current content (for created/modified)
        full_path = kb_path / file_path
        if full_path.exists():
            content = full_path.read_text(encoding="utf-8")
            result["current_body"] = content
            # Parse frontmatter for title/type/id
            meta = self._extract_frontmatter(content)
            if meta:
                result["title"] = meta.get("title", file_path)
                result["entry_type"] = meta.get("type", "unknown")
                result["entry_id"] = meta.get("id")

        # Get previous content (for modified/deleted)
        if change_type in ("modified", "deleted"):
            previous = GitService.show(kb_path, file_path)
            if previous is not None:
                result["previous_body"] = previous
                if change_type == "deleted":
                    meta = self._extract_frontmatter(previous)
                    if meta:
                        result["title"] = meta.get("title", file_path)
                        result["entry_type"] = meta.get("type", "unknown")
                        result["entry_id"] = meta.get("id")

        return result

    @staticmethod
    def _extract_frontmatter(content: str) -> dict | None:
        """Extract YAML frontmatter from markdown content."""
        split = split_frontmatter(content)
        if not isinstance(split, Frontmatter):
            return None
        try:
            from ..utils.yaml import load_yaml

            return load_yaml(split.text)
        except Exception:
            return None

    def publish_changes(self, kb_name: str, summary: str | None = None) -> dict:
        """
        Commit and push all pending changes in a KB.

        Auto-generates a commit message from the change summary.
        Returns dict with success, commit_hash, entries_published.
        """
        from ..services.git_service import GitService

        kb_config = self.require_kb(kb_name)

        kb_path = kb_config.path
        if not GitService.is_git_repo(kb_path):
            return {"success": False, "error": "Not a git repository"}

        # Check what's pending
        pending = self.get_pending_changes(kb_name)
        if pending["summary"]["total"] == 0:
            return {
                "success": True,
                "entries_published": 0,
                "commit_hash": None,
                "message": "Nothing to publish",
            }

        # Build commit message
        if summary:
            message = summary
        else:
            parts = []
            s = pending["summary"]
            if s["created"]:
                parts.append(f"Created {s['created']} entr{'y' if s['created'] == 1 else 'ies'}")
            if s["modified"]:
                parts.append(f"Updated {s['modified']} entr{'y' if s['modified'] == 1 else 'ies'}")
            if s["deleted"]:
                parts.append(f"Removed {s['deleted']} entr{'y' if s['deleted'] == 1 else 'ies'}")
            message = "Published: " + ", ".join(parts)

        # Commit
        commit_result = self._export_svc.commit_kb(kb_name, message)
        if not commit_result.get("success"):
            return {"success": False, "error": commit_result.get("error", "Commit failed")}

        # Try to push (non-fatal -- the commit already succeeded either
        # way). GitService.push() never raises for real push failures
        # (no remote, auth, network); it catches its own subprocess and
        # returns (False, message), which surfaces below. The except only
        # catches something genuinely unexpected (e.g. push_kb's own
        # KBNotFoundError/PyriteError checks, already impossible here
        # since kb_name and git-repo-ness were validated above, or a
        # future push_kb change). Report the real exception, not a canned
        # "No remote configured" that could mask an auth/network failure
        # as a config problem (fail-open-exception-sweep site #3).
        push_error = None
        try:
            push_result = self._export_svc.push_kb(kb_name)
            if not push_result.get("success"):
                push_error = push_result.get("message", "Push failed")
        except Exception as e:
            logger.warning("Push failed for KB %r after publish: %s", kb_name, e, exc_info=True)
            # Already logged above (with a traceback) -- public_message, when
            # set, is what the caller sees; str(e) can carry server-side
            # detail (ADR-0037 theme 2 round 2, item 1).
            push_error = public_text(e, already_logged=True, log=logger)

        return {
            "success": True,
            "commit_hash": commit_result.get("commit_hash"),
            "entries_published": pending["summary"]["total"],
            "message": message,
            "push_error": push_error,
        }


# =============================================================================
# Core hooks moved out: _task_validate_transition and _parent_rollup now live
# in task_service.py, where they belong with task semantics. KBService.__init__
# wires them via register_task_hooks(self.hook_runner). The module-level
# _CORE_HOOKS dict that used to live here is gone — runner.core_hooks(name) is
# the inspection surface now.
# =============================================================================


#: One read-and-apply, then five more from a fresh read (ADR-0042 decision 10).
_UPDATE_ATTEMPTS = 6


#: The managed lists a surface may append an item to or remove one from, as
#: a whole item (``links``, never ``links[0].x``).
_ITEM_LISTS = frozenset({"links", "sources"})


def _request_operations(updates: dict[str, Any]) -> list[Any]:
    """The request's fields as operations, paths as sent: ``body`` a
    ``ReplaceBody``, ``metadata: {k: v}`` one ``Set(("metadata", k))`` per
    key, every other key a ``Set(key)``. ``_resolve_operation`` spells the
    paths onto the file and decides the refusals."""
    # A request path is parsed into segments once, here, at the boundary
    # (`path_segments`: `.` and `[i]`, the one parser `apply()` uses). From
    # here on a path is a tuple and no segment is read again, so an envelope
    # key `status_change_log[0]` or `metadata.id` is the field it names, not
    # a literal key the refusal never saw (#1001 review).
    from ..schema.field_aliases import path_segments

    ops: list[Any] = []
    try:
        for key, value in updates.items():
            if key == "body":
                ops.append(ReplaceBody(value if isinstance(value, str) else str(value)))
            elif key == "metadata" and isinstance(value, dict):
                ops += [Set(("metadata", *path_segments(mk)), mv) for mk, mv in value.items()]
            else:
                ops.append(Set(path_segments(key), value))
    except ValueError as e:
        raise ValidationError(f"Cannot read a field path in the update: {e}") from None
    return ops


def _refuse_unmodellable(raw: bytes, entry_id: str, verb: str) -> None:
    """Refuse a file whose frontmatter Pyrite cannot model (#760, #1035) as a
    ``ValidationError`` (``VALIDATION_FAILED`` on every surface), naming the
    reason and the 1-based line: the one answer for ``get`` and ``update``,
    instead of a traceback or a miss. The rule is
    ``file_operations.unmodellable_in_text``, the one ``index health`` lists by."""
    try:
        why = unmodellable_in_text(raw.decode("utf-8"))
    except Exception:  # not UTF-8, or a file the check cannot handle: not this rule's
        return
    if why is not None:
        reason, line = why
        if verb == "read":
            reason = reason_for_read(reason)
        raise ValidationError(f"Cannot {verb} '{entry_id}': {reason} (line {line})")


def _refuse_unreadable_fields(path: Path, entry_id: str, exc: Exception) -> NoReturn:
    """``read_fields`` raised ``TypeError`` on a loaded entry: refuse it as the
    #760 rule does when the rule names it (with its line), else as a value
    Pyrite cannot read (a `!!set`)."""
    raw = _read_regular_file(path)
    if raw is not None:
        _refuse_unmodellable(raw, entry_id, "read")
    raise ValidationError(
        f"Cannot read '{entry_id}': the frontmatter holds a value Pyrite cannot read ({exc})"
    ) from exc


def _refuse_unmodellable_named(repo: KBRepository, entry_id: str, verb: str) -> None:
    """``_refuse_unmodellable`` for the files named ``<id>.md``: the one place
    a file the loader refuses (a duplicate key, a root that is no mapping) can
    still be found by id. A file whose stem is not its id is not."""
    try:
        repo._validate_entry_id(entry_id)
    except ValidationError:
        return
    for path in repo._filename_candidates(entry_id):
        if not repo._lexically_inside(path):
            continue
        raw = _read_regular_file(path)
        if raw is None:
            continue
        _refuse_unmodellable(raw, entry_id, verb)


def _read_regular_file(path: Path) -> bytes | None:
    """``path``'s bytes when it is a regular file, else None. Opened
    ``O_NONBLOCK`` and checked on the descriptor: a FIFO (or a device) at
    the name never holds the caller, nor the lock it may hold."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks = []
        while chunk := os.read(fd, 1 << 16):
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def _item_operations(
    append: dict[str, Any] | None, remove: dict[str, Any] | None, unset: list[str] | None
) -> list[Any]:
    """The surfaces' ``append``/``remove``/``unset`` as operations: a list
    value under a key means each of its items; ``unset`` is a list of keys.
    A wrong shape (``unset: "tags"``, ``append: ["tags", "x"]``) is refused
    as a ``ValidationError``, so every surface answers ``VALIDATION_FAILED``
    (#973 fix round 1: MCP iterated a string per character)."""

    def mapping(name: str, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict) or any(not isinstance(k, str) or not k for k in value):
            raise ValidationError(
                f"`{name}` must be an object of {{field: item}} (a list value means each "
                f"item), got {type(value).__name__}."
            )
        return value

    if unset is not None and (
        not isinstance(unset, list) or any(not isinstance(k, str) or not k for k in unset)
    ):
        raise ValidationError(
            f'`unset` must be a list of field names, e.g. ["tags"], got {unset!r}.'
        )
    from ..schema.field_aliases import path_segments

    ops: list[Any] = []
    try:
        for key, value in mapping("append", append).items():
            ops += [
                Append(path_segments(key), item)
                for item in (value if isinstance(value, list) else [value])
            ]
        for key, value in mapping("remove", remove).items():
            ops += [
                Remove(path_segments(key), item)
                for item in (value if isinstance(value, list) else [value])
            ]
        for key in unset or []:
            ops.append(Unset(path_segments(key)))
    except ValueError as e:
        raise ValidationError(f"Cannot read a field path in the operation: {e}") from None
    return ops


def _checked_item(entry: Entry, field: str, value: Any) -> Any:
    """An item appended to ``links`` or ``sources``, checked as one before
    anything is written: a mapping the item type reads (``Link.from_dict``,
    ``Source.from_dict``), a link with a target the index can store
    (``check_link_target``). A string or a list is refused here, never
    written and then refused by the index insert (#973 fix round 1)."""
    item_type = Link if field == "links" else Source
    if not isinstance(value, dict):
        raise ValidationError(
            f"Cannot append to {field} on {entry.entry_type} '{entry.id}': an item is an "
            f'object (for a link, {{"target": "<id>", "relation": "<relation>"}}), '
            f"got {type(value).__name__}."
        )
    try:
        item = item_type.from_dict(value)
    except Exception as e:
        raise ValidationError(
            f"Cannot append to {field} on {entry.entry_type} '{entry.id}': {e}"
        ) from e
    if field == "links":
        check_link_target(item.to_dict())
    return value


def _frontmatter_span(text: str) -> FrontmatterSpan:
    split = split_frontmatter(text)
    assert isinstance(split, Frontmatter), split  # apply() just produced it
    return FrontmatterSpan(split.yaml_start, split.close_start, split.close_end)


def _nested_block_is_all_equal_pairs(entry: Entry, file_values: dict[str, Any]) -> bool:
    """True when the file's ``metadata:`` block holds only second spellings
    of fields whose every spelling is the same YAML value: the block the
    merge empties, which may then go whole (#1001 round 4, item 4)."""
    from ..schema.field_aliases import double_spellings, values_identical

    nested = file_values.get("metadata")
    if not isinstance(nested, dict) or not nested:
        return False
    doubled = double_spellings(file_values, type(entry), entry.entry_type)
    nested_fields = {field for field, paths in doubled.items() for p in paths if p[0] == "metadata"}
    for key in nested:
        if key not in nested_fields and not any(
            p == ("metadata", key) for paths in doubled.values() for p in paths
        ):
            return False
    for paths in doubled.values():
        values = [_value_at(file_values, p) for p in paths]
        if not all(values_identical(v, values[0]) for v in values):
            return False
    return True


def _value_at(values: Any, path: tuple[Any, ...]) -> Any:
    """The value a file's frontmatter holds at ``path`` (a key, or a key under
    ``metadata:``), or None when the path is absent; a reading only, for
    comparing two spellings of one field."""
    node = values
    for seg in path:
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            return None
    return node


def _plain_value(value: Any) -> Any:
    """``value`` as plain data the file can hold: an Enum its value, a Link or
    Source its mapping, containers recursively; everything else as sent."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Link | Source):
        return value.to_dict()
    if isinstance(value, dict):
        return {k: _plain_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_value(v) for v in value]
    return value


#: The ``updated_at`` bookkeeping lives with the write path (rename stamps it
#: too, #955 P4); kept under its old name for this module's callers.
_updated_at_stamp = updated_at_stamp
_raw_scalar = raw_scalar


def _same_instant_as_file(file_values: dict[str, Any], path: Any, value: Any) -> bool:
    """Is ``value`` the instant the file already holds at the timestamp key
    ``path``, in another spelling? ``created_at: 2026-01-15`` loads as a
    date; a read reports it and an echo sends it back as the datetime
    ``parse_datetime`` makes of it. Equal instants are one value (ADR-0042
    decision 3), so the key keeps its bytes and ``updated_at`` is not
    stamped for it. Only the timestamp keys take this path: a title edited
    from ``"2026-01-15"`` to ``"2026-01-15T00:00:00Z"`` is a change."""
    key = path[0] if isinstance(path, tuple) and len(path) == 1 else path
    if not isinstance(key, str) or key not in _TIMESTAMP_FIELDS or key not in file_values:
        return False
    old = _timestamp_instant(file_values[key])
    new = _timestamp_instant(value)
    return old is not None and new is not None and old == new


def _as_field_type(entry: Entry, key: str, value: Any) -> Any:
    """Coerce an update value to the model type expected for its field.

    Enum fields arrive as strings such as status values, and assigning a
    string to EventEntry.status failed later during frontmatter serialization
    (#561). Source and Link lists arrive as JSON dictionaries on reads and are
    restored to typed objects here, before validation or serialization.
    Unsupported input is refused before anything is written.
    """
    if key in ("sources", "links"):
        item_type = Source if key == "sources" else Link
        if not isinstance(value, list):
            raise ValidationError(
                f"Cannot set {key} on {entry.entry_type} '{entry.id}': expected a list of objects."
            )
        converted = []
        for index, item in enumerate(value):
            if isinstance(item, item_type):
                converted.append(item)
            elif isinstance(item, dict):
                converted.append(item_type.from_dict(item))
            else:
                raise ValidationError(
                    f"Cannot set {key}[{index}] on {entry.entry_type} '{entry.id}': expected an object."
                )
        return converted

    current = getattr(entry, key, None)
    if not isinstance(current, Enum) or isinstance(value, type(current)):
        return value
    enum_type = type(current)
    try:
        return enum_type(value)
    except ValueError:
        allowed = ", ".join(str(member.value) for member in enum_type)
        raise ValidationError(
            f"Cannot set {key}={value!r} on {entry.entry_type} '{entry.id}': "
            f"expected one of {allowed}."
        ) from None

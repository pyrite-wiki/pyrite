"""
Knowledge Base Service

Unified KB operations used by API, CLI, and UI layers.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .kb_registry_service import KBRegistryService

from ..config import KBConfig, PyriteConfig
from ..exceptions import (
    EntryExistsError,
    EntryNotFoundError,
    IndexSyncRecoveryHintError,
    KBNotFoundError,
    KBReadOnlyError,
    PyriteError,
    SchemaViolationError,
    UndeclaredTypeError,
    ValidationError,
)
from ..models import Entry
from ..models.base import parse_datetime
from ..models.factory import build_entry
from ..plugins.context import PluginContext
from ..storage.database import PyriteDB
from ..storage.document_manager import DocumentManager
from ..storage.index import IndexManager
from ..storage.repository import KBRepository
from ..utils.metadata import parse_metadata
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


@dataclass
class WriteResult:
    """What a write produced: the saved entry and the schema warnings it drew.

    Warnings are the non-blocking findings of the same validation that refuses
    a write (an unknown select value when the KB does not enforce, a missing
    optional source...). Every surface can now return them (#378).
    """

    entry: Entry
    warnings: list[dict[str, Any]] = field(default_factory=list)


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
    public_message = getattr(exc, "public_message", None)
    if public_message is not None:
        logger.warning("%s", exc)
        return public_message
    return str(exc)


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

    def _validate_write(
        self, entry: Entry, kb_name: str, kb_config: KBConfig
    ) -> list[dict[str, Any]]:
        """Refuse a write the KB schema or a plugin validator rejects.

        The same rules `index health` and `schema validate` report after the
        fact (enum, required, min/max, pattern) are applied before the file is
        written, on every surface. Without this, `update -f status=bogus`
        succeeded and drifted the board (75 items once sat on an undeclared
        status). No kb.yaml means no schema to enforce; plugin validators for
        the KB type still run through validate_entry.
        """
        # A typed entry keeps fields its model does not declare under a nested
        # `metadata:` block, which is where build_entry puts a caller's extra
        # kwargs. The schema declares them for the type all the same, so they
        # are validated as the fields they are; a top-level key wins a clash.
        fields = entry.to_frontmatter()
        nested = fields.get("metadata")
        if isinstance(nested, dict):
            fields = {**nested, **fields}
        try:
            result = kb_config.kb_schema.validate_entry(
                entry.entry_type,
                fields,
                context={
                    "kb_name": kb_name,
                    "kb_type": kb_config.kb_type,
                    "_schema_version": getattr(entry, "_schema_version", 0),
                },
            )
        except Exception:  # a broken validator must not make every write fail
            logger.warning("Schema validation skipped for %s/%s", kb_name, entry.id, exc_info=True)
            return []
        errors = result.get("errors") or []
        if not errors:
            return list(result.get("warnings") or [])
        parts = []
        for e in errors:
            field = e.get("field", "?")
            rule = e.get("rule", "")
            got = e.get("got")
            expected = e.get("expected")
            message = e.get("message")
            if rule == "enum":
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
                # cascade's "Importance must be 1-10, got: 99"): the
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

            self._embedding_worker = EmbeddingWorker(self.db)
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

    def get_kb_stats(self, name: str) -> dict[str, Any] | None:
        """Get stats for a specific KB."""
        return self.db.get_kb_stats(name)

    # =========================================================================
    # Entry Operations
    # =========================================================================

    def get_entry(self, entry_id: str, kb_name: str | None = None) -> dict[str, Any] | None:
        """
        Get entry by ID.

        If kb_name not specified, searches all KBs.
        """
        if kb_name:
            result = self.db.get_entry(entry_id, kb_name)
            if result:
                result["outlinks"] = self.db.get_outlinks(entry_id, kb_name)
                result["backlinks"] = self.db.get_backlinks(entry_id, kb_name)
            return result

        # Search all KBs
        for kb in self.config.all_kbs():
            result = self.db.get_entry(entry_id, kb.name)
            if result:
                result["outlinks"] = self.db.get_outlinks(entry_id, kb.name)
                result["backlinks"] = self.db.get_backlinks(entry_id, kb.name)
                return result
        return None

    def _resolve_entry_type(self, entry_type: str, kb_type: str = "") -> str:
        """Resolve a generic core type to a plugin subtype if one exists.

        If a plugin ACTIVE FOR THIS KB provides a type that subclasses the
        core type for the given name, prefer the plugin type. E.g.
        "event" -> "timeline_event" in a cascade-timeline KB, because the
        Cascade plugin registers TimelineEventEntry(EventEntry) and declares
        cascade-timeline in get_kb_types().

        Scoping by kb_type is load-bearing, not an optimization. Resolution
        picks the FIRST subclass found, and the unscoped registry dict is
        ordered by plugin discovery, which follows site-packages enumeration
        and therefore varies between machines. Both cascade's `actor` and
        social's `user_profile` subclass PersonEntry, so an unscoped
        `person` resolved to `actor` on one host and `user_profile` on
        another for identical code -- with the wrong answer silently written
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
            # core type (cascade declares cascade_event, solidarity_event and
            # timeline_event, all EventEntry subclasses). Discovery order must
            # not decide the winner, but neither may alphabetical order --
            # that picks `cascade_event` over `timeline_event`, silently
            # changing the type of every new entry in the 5,505-entry
            # cascade-timeline KB. Prefer the MOST DERIVED class (longest
            # MRO): TimelineEventEntry -> InvestigationEventEntry ->
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
        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            raise KBNotFoundError(f"KB not found: {kb_name}")
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
        declared = sorted(schema.types.keys()) if schema and schema.types else []
        if declared and entry_type not in schema.types:
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
        """
        from ..schema import generate_entry_id

        ensure_not_truncated(spec)
        # MARKER_KEYS are ADR-0034 read transport, never entry content: an
        # allowed `body_truncated: false` must not be persisted as frontmatter.
        # A None value means "not given", so the model's default applies.
        fields = {k: v for k, v in spec.items() if k not in MARKER_KEYS and v is not None}

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
            entry = build_entry(entry_type, entry_id=entry_id, title=title, body=body, **fields)
        else:
            entry = builder(entry_type, entry_id, title, body, fields)

        # The model's own rules (an event needs a date, importance 1-10, ...).
        errors = entry.validate()
        if errors:
            raise ValidationError("; ".join(errors))
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
    ) -> WriteResult:
        """The one create pipeline: :meth:`_prepare`, then hooks, save, index, embed."""
        entry, warnings = self._prepare(
            kb_name,
            kb_config,
            spec,
            allow_undeclared=allow_undeclared,
            builder=builder,
            resolve_type=resolve_type,
        )
        ctx = hook_ctx or self._hook_ctx(kb_name, kb_config, "create")
        entry = self._run_hooks("before_save", entry, ctx)
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
        self, kb_name: str, spec: dict[str, Any], *, allow_undeclared: bool = True
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
        return self._prepare_and_save(kb_name, kb_config, spec, allow_undeclared=allow_undeclared)

    def create_entry(
        self,
        kb_name: str,
        entry_id: str,
        title: str,
        entry_type: str,
        body: str = "",
        *,
        allow_undeclared: bool = True,
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
            **kwargs: Additional fields (date, importance, tags, etc.)

        Returns:
            Created Entry object

        Raises:
            KBNotFoundError: If KB not found
            KBReadOnlyError: If KB is read-only
            ValidationError: (or a subclass) if the pipeline refuses the entry
        """
        spec = {**kwargs, "id": entry_id, "title": title, "entry_type": entry_type, "body": body}
        return self.create(kb_name, spec, allow_undeclared=allow_undeclared).entry

    def bulk_create_entries(
        self,
        kb_name: str,
        entries: list[dict[str, Any]],
        *,
        allow_undeclared: bool = True,
        validate_only: bool = False,
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
        """
        if validate_only:
            kb_config = self.config.get_kb(kb_name)
            if not kb_config:
                raise KBNotFoundError(f"KB not found: {kb_name}")
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
            try:
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

        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            raise KBNotFoundError(f"KB not found: {kb_name}")
        if not validate_only and kb_config.read_only:
            raise KBReadOnlyError(f"KB is read-only: {kb_name}")

        source_path = Path(source_path)

        # Read and parse frontmatter
        text = source_path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            raise ValidationError("File must start with YAML frontmatter (---)")

        end = text.find("---", 3)
        if end < 0:
            raise ValidationError("Could not find closing frontmatter delimiter (---)")

        meta = load_yaml(text[3:end])
        if not meta or not isinstance(meta, dict):
            raise ValidationError("Frontmatter is empty or invalid")

        body = text[end + 3 :].strip()

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

    def update(self, entry_id: str, kb_name: str, updates: dict[str, Any]) -> WriteResult:
        """Update an existing entry for a caller, returning it and its warnings.

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
        return self._update(entry_id, kb_name, updates, restrict=True)

    def _update(
        self, entry_id: str, kb_name: str, updates: dict[str, Any], *, restrict: bool
    ) -> WriteResult:
        # ADR-0034 marker keys are refused here (a truthy `body_truncated`)
        # and otherwise never stored. Before #407 this fell out for free: an
        # undeclared key was always dropped by the loop below, and a marker
        # key is never a model attribute. Now that an undeclared key is
        # stored instead of dropped, the markers have to be stripped
        # explicitly -- the same way `_prepare` does at its own write path --
        # or an allowed `body_truncated: false` would be persisted as
        # frontmatter.
        ensure_not_truncated(updates)
        updates = {k: v for k, v in updates.items() if k not in MARKER_KEYS}

        kb_config = self._writable_kb(kb_name)

        repo = KBRepository(kb_config)
        entry = repo.load(entry_id)
        if not entry:
            raise EntryNotFoundError(f"Entry not found: {entry_id}{repo.not_found_hint(entry_id)}")

        # `type`/`entry_type` and the empty key are never model attributes on
        # any entry (`type` is frontmatter-only; `entry_type` is a read-only
        # `@property` with no setter, so setting it raised a raw
        # AttributeError instead of a clean refusal -- almost certainly a 500
        # through REST PATCH, round-2 cold read), so #407's undeclared-key
        # branch below would route `type` into `metadata` instead of refusing
        # it: `-f type=hacked` reported `updated: true` and left the real
        # `type:` frontmatter line untouched -- the #407 symptom recurring
        # for a reserved key #407's own fix did not cover (round-1 cold
        # read). Refused unconditionally, not gated by `restrict`: unlike a
        # managed field an in-process caller might legitimately own (links,
        # status), no caller -- internal or external -- means anything
        # sensible by setting `type`, `entry_type` or `""` through an update.
        #
        # Checked AFTER the KB and entry lookups above (round-2 cold read):
        # a missing KB or entry, or a read-only KB, must still answer its own
        # 404/403 (KBNotFoundError/EntryNotFoundError/KBReadOnlyError), not a
        # ValidationError that tells the caller their *request* was invalid
        # when the real problem is that the KB or entry they named doesn't
        # exist.
        bad_keys = sorted(k for k in updates if k in ("type", "entry_type") or k == "")
        if bad_keys:
            raise ValidationError(
                f"Cannot set {', '.join(k or '(empty key)' for k in bad_keys)} on "
                f"{entry_id!r} with an update: an entry's type is fixed once "
                "created, and a --field key must be non-empty."
            )

        _, _, managed = self._field_sets(entry.entry_type, kb_config)
        if restrict:
            refused = sorted(k for k in updates if k in managed)
            if refused:
                raise ValidationError(
                    f"Cannot set {', '.join(refused)} on {entry.entry_type} "
                    f"'{entry_id}' with an update: Pyrite maintains these fields."
                )

        # Capture old_status before applying updates (for workflow hooks)
        old_status = getattr(entry, "status", None)

        # A timestamp may arrive as a string: REST `PATCH /entries/{id}` sends
        # `value: str` and the CLI passes `--field updated_at=…`. Coerce it
        # before it is assigned -- the file, the model and the index all expect
        # a `datetime`, and a string used to reach
        # `IndexManager._entry_to_dict` (`entry.<ts>.isoformat()`) *after* the
        # file had been written: an `AttributeError` that left the file written
        # and the index stale, so the two diverged (#173 review). A naive
        # string is read as UTC by `parse_datetime`, like ingestion.
        for ts_key in ("created_at", "updated_at"):
            if ts_key in updates and not isinstance(updates[ts_key], datetime):
                updates[ts_key] = parse_datetime(updates[ts_key])

        # Apply updates
        for key, value in updates.items():
            if not hasattr(entry, key):
                # A key with no model attribute: kb.yaml-declared or not,
                # `create` stores it rather than dropping it (`build_entry`
                # puts unknown kwargs in metadata), so `update` follows the
                # same rule -- this is what generalises the old
                # `schema_fields`-only branch. A key already recorded under
                # `extra_frontmatter` (an undeclared key on a typed entry,
                # loaded from the file) stays there; everything else merges
                # into `metadata`, where `GenericEntry`/`TaskEntry` promote it
                # back to a top-level frontmatter key on save, same as create.
                if key in entry.extra_frontmatter:
                    entry.extra_frontmatter[key] = value
                else:
                    entry.metadata = {**(entry.metadata or {}), key: value}
                continue
            # Metadata is a bag of keys — merge shallowly so a partial update
            # (e.g. just review_comments) does not clobber other metadata.
            if key == "metadata" and isinstance(value, dict):
                merged = dict(getattr(entry, "metadata", None) or {})
                merged.update(value)
                setattr(entry, key, merged)
            else:
                setattr(entry, key, value)

        # An explicit `updated_at` in the update is the caller's value; only
        # stamp the bookkeeping time when they did not supply one, and tell the
        # repository below not to stamp over a supplied one either (#151).
        explicit_updated_at = "updated_at" in updates
        if not explicit_updated_at:
            entry.touch_updated_at()

        # Refuse before anything is written: the file must stay exactly as it was.
        warnings = self._validate_write(entry, kb_name, kb_config)

        # Run before_save hooks
        extra = {"old_status": old_status} if old_status else {}
        hook_ctx = self._hook_ctx(kb_name, kb_config, "update", extra)
        entry = self._run_hooks("before_save", entry, hook_ctx)

        # Save to file, register KB, and re-index
        self._doc_mgr.save_entry(
            entry, kb_name, kb_config, touch_updated_at=not explicit_updated_at
        )

        # Auto-embed for semantic search
        self._auto_embed(entry.id, kb_name)

        # Run after_save hooks
        self._run_hooks("after_save", entry, hook_ctx)

        return WriteResult(entry=entry, warnings=warnings)

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
        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            raise KBNotFoundError(f"KB not found: {kb_name}")
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
        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            raise KBNotFoundError(f"KB not found: {kb_name}")
        if kb_config.read_only and not dry_run:
            raise KBReadOnlyError(f"KB is read-only: {kb_name}")

        repo = KBRepository(kb_config)
        result = repo.rename(old_id, new_id, update_links=update_links, dry_run=dry_run)

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
            is ``(target, kb, relation)``: a second, different relation
            between the same two entries is a new link, not a duplicate
            (#396).
        """
        kb_config = self.config.get_kb(source_kb)
        if not kb_config:
            raise KBNotFoundError(f"KB not found: {source_kb}")
        if kb_config.read_only:
            raise KBReadOnlyError(f"KB is read-only: {source_kb}")

        repo = KBRepository(kb_config)
        entry = repo.load(source_id)
        if not entry:
            raise EntryNotFoundError(f"Entry not found: {source_id}")

        tkb = target_kb or source_kb
        target_kb_config = self.config.get_kb(tkb)
        if not target_kb_config:
            raise KBNotFoundError(f"KB not found: {tkb}")

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
                existing.target == target_id
                and (existing.kb or source_kb) == tkb
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

        entry.add_link(target=target_id, relation=relation, note=note, kb=tkb)
        entry.touch_updated_at()
        self._doc_mgr.save_entry(entry, source_kb, kb_config)
        return {"resolved": resolved, "created": True, "relation": relation}

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

        Each source is loaded once, gets all of its new links, and is saved
        once through the same DocumentManager path :meth:`add_link` uses --
        in place, wherever the file lives, and re-indexed. The CLI used to
        ``repo.save`` it, which re-derived the path from the type and wrote a
        second copy of any entry kept elsewhere (#375).

        Returns one result per spec, in input order:
        ``{"status": "created" | "skipped" | "failed", "error"?: str}``.
        ``dry_run`` reports what would happen without writing.
        """
        kb_config = self._writable_kb(kb_name)
        repo = KBRepository(kb_config)

        results: list[dict[str, Any]] = [{} for _ in links]
        loaded: dict[str, Entry | None] = {}
        dirty: dict[str, list[int]] = {}

        for i, spec in enumerate(links):
            source_id = spec.get("source")
            target_id = spec.get("target")
            tkb = spec.get("target_kb") or kb_name
            relation = spec.get("relation") or "related_to"
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
                existing.target == target_id
                and (existing.kb or kb_name) == tkb
                and _norm_relation(existing.relation) == _norm_relation(relation)
                for existing in entry.links
            ):
                results[i] = {"status": "skipped"}
                continue
            entry.add_link(
                target=target_id,
                relation=relation,
                note=spec.get("note", ""),
                kb=tkb,
            )
            results[i] = {"status": "created"}
            dirty.setdefault(source_id, []).append(i)

        if dry_run:
            return results

        for source_id, positions in dirty.items():
            entry = loaded[source_id]
            try:
                entry.touch_updated_at()
                self._doc_mgr.save_entry(entry, kb_name, kb_config)
            except Exception as e:
                for i in positions:
                    results[i] = {"status": "failed", "error": _safe_message(e)}
        return results

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
        readable_kbs: set[str] | None = None,
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
        entry = self.get_entry(collection_id, kb_name)
        if not entry or entry.get("entry_type") != "collection":
            raise EntryNotFoundError(f"Collection not found: {collection_id}")
        metadata = parse_metadata(entry.get("metadata", {}))

        source_type = metadata.get("source_type", "folder")

        # Virtual collection (query-based)
        if source_type == "query":
            entries, total = self._get_query_collection_entries(
                metadata, kb_name, sort_by, sort_order, limit, offset, readable_kbs
            )
            return self._normalize_metadata_rows(entries), total

        # Folder-based collection (Phase 1)
        folder_path = metadata.get("folder_path", "") if isinstance(metadata, dict) else ""
        if not folder_path:
            return [], 0
        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            raise KBNotFoundError(f"KB not found: {kb_name}")
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
        readable_kbs: set[str] | None = None,
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
        self, query: Any, readable_kbs: set[str] | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        """Evaluate a parsed collection query within the caller's readable set.

        A KB the query names in its own text is authorized against the same
        set (``collection_query.evaluate_query``); None is unscoped.
        """
        from .collection_query import evaluate_query

        return evaluate_query(query, self.db, kb_names=readable_kbs)

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

    def get_orphans(self, kb_name: str | None = None) -> list[dict[str, Any]]:
        """Get entries with no links."""
        return self.db.get_orphans(kb_name)

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

    def orient(self, kb_name: str, recent_limit: int = 5) -> dict[str, Any]:
        """One-shot KB orientation summary for agents entering a new KB."""
        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            raise KBNotFoundError(f"KB '{kb_name}' not found")

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

        # Plugin orient supplements
        from ..plugins.registry import get_registry

        supplements = get_registry().get_orient_supplements(kb_name, kb_config.kb_type or "default")
        if supplements:
            result.update(supplements)

        return result

    def generate_readme(self, kb_name: str) -> str:
        """Generate a README.md for a knowledge base."""
        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            raise KBNotFoundError(f"KB not found: {kb_name}")

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
        readable_kbs: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Lightweight listing of entry IDs and titles for wikilink autocomplete."""
        return self.wikilinks.list_entry_titles(
            kb_name=kb_name, query=query, limit=limit, readable_kbs=readable_kbs
        )

    def resolve_entry(
        self, target: str, kb_name: str | None = None, readable_kbs: set[str] | None = None
    ) -> dict[str, Any] | None:
        """Resolve a wikilink target to an entry. Supports kb:id format for cross-KB links."""
        return self.wikilinks.resolve_entry(target, kb_name=kb_name, readable_kbs=readable_kbs)

    def resolve_batch(
        self,
        targets: list[str],
        kb_name: str | None = None,
        readable_kbs: set[str] | None = None,
    ) -> dict[str, bool]:
        """Batch-resolve wikilink targets. Supports kb:id format."""
        return self.wikilinks.resolve_batch(targets, kb_name=kb_name, readable_kbs=readable_kbs)

    def get_wanted_pages(
        self, kb_name: str | None = None, limit: int = 100, readable_kbs: set[str] | None = None
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

    # =========================================================================
    # Hooks
    # =========================================================================

    def _run_hooks(self, hook_name: str, entry: Entry, context: dict) -> Entry:
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

    def sync_index(self, kb_name: str | None = None) -> dict[str, Any]:
        """
        Synchronize index with file system.

        Args:
            kb_name: Specific KB to sync, or None for all

        Returns:
            Sync statistics
        """
        return self._index_mgr.sync_incremental(kb_name)

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

        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            raise KBNotFoundError(kb_name)

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
        import subprocess

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
            try:
                proc = subprocess.run(
                    ["git", "show", f"HEAD:{file_path}"],
                    cwd=str(kb_path),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if proc.returncode == 0:
                    result["previous_body"] = proc.stdout
                    if change_type == "deleted":
                        meta = self._extract_frontmatter(proc.stdout)
                        if meta:
                            result["title"] = meta.get("title", file_path)
                            result["entry_type"] = meta.get("type", "unknown")
                            result["entry_id"] = meta.get("id")
            except Exception:
                pass

        return result

    @staticmethod
    def _extract_frontmatter(content: str) -> dict | None:
        """Extract YAML frontmatter from markdown content."""
        if not content.startswith("---"):
            return None
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None
        try:
            from ..utils.yaml import load_yaml

            return load_yaml(parts[1])
        except Exception:
            return None

    def publish_changes(self, kb_name: str, summary: str | None = None) -> dict:
        """
        Commit and push all pending changes in a KB.

        Auto-generates a commit message from the change summary.
        Returns dict with success, commit_hash, entries_published.
        """
        from ..services.git_service import GitService

        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            raise KBNotFoundError(kb_name)

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
            push_error = getattr(e, "public_message", None) or str(e)

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

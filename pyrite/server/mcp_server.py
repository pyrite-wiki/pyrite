"""
MCP (Model Context Protocol) Server for pyrite

Three-tier MCP server supporting read, write, and admin access levels.
Each tier is a separate server instance with appropriate tools.

Tiers:
- read:  Search, browse, retrieve entries. Safe for any agent.
- write: Read + create/update/delete entries. For trusted agents/users.
- admin: Write + KB management, index rebuild, repo sync, config.
"""

import json
import logging
from typing import Any

from pydantic import AnyUrl

from ..config import PyriteConfig, load_config
from ..exceptions import (
    BrandingInvalidError,
    ConfigError,
    ConflictError,
    EntryNotFoundError,
    InvalidGitRefError,
    KBNotFoundError,
    KBProtectedError,
    KBReadOnlyError,
    PyriteError,
    QuerySyntaxError,
    QueryTooLongError,
    StorageError,
    ValidationError,
    public_text,
    refusal_fields,
)
from ..services.access_policy import ROLES, Principal, named_kb
from ..services.body_bounds import (
    MARKER_KEYS,
    BodyBounds,
    ensure_not_truncated,
    load_body_bounds,
)
from ..services.export_service import ExportService
from ..services.graph_service import GraphService
from ..services.kb_service import KBService, check_link_spec
from ..services.read_shaping import project_fields
from ..services.write_target import binding, current_target
from ..storage.database import PyriteDB
from ..storage.index import IndexManager
from .mcp_rate_limiter import MCPRateLimiter

logger = logging.getLogger(__name__)

URI_SCHEME = "pyrite://"
MAX_BATCH_READ_ENTRIES = 50
#: Arguments of kb_create / kb_update that steer the tool, never entry fields.
_CREATE_CONTROL_KEYS = frozenset({"kb_name", "validate", "allow_undeclared"})
#: Arguments of kb_update that name the entry or steer the tool.
_UPDATE_CONTROL_KEYS = frozenset({"entry_id", "kb_name", "validate", "append", "remove", "unset"})


# Identity fields every `fields` projection keeps. Agents key on these to
# re-fetch, link or report an entry afterwards, and `_kb_batch_read` reads
# them directly while computing `not_found`. The identity pair itself now lives
# in `services/read_shaping.py`, shared with the REST routes and the CLI (#193).


def _project_fields(entry: dict, fields: list[str] | None) -> dict:
    """Project only requested fields from an entry dict.

    The identity pair and the "never invent a key" rule come from
    ``read_shaping.project_fields``. What stays here is MCP's own addition: the
    body truncation markers, when the projection kept a `body`.

    ADR-0034 rule 2 forbids silent truncation, and a projection that dropped
    `body_truncated` would hand an agent a slice it cannot tell apart from a
    whole body. A projection that excluded `body` keeps no markers, because
    there is nothing there to have been truncated.
    """
    out = project_fields(entry, fields)
    if out is not entry and "body" in out and entry.get("body_truncated"):
        for key in MARKER_KEYS:
            if key in entry:
                out[key] = entry[key]
    return out


# ADR-0037 theme 2 (maintainer decision, 2026-09-25): "one code per
# exception, and REST's code wins. For one release, MCP keeps emitting its
# current code in a legacy_error_code field, then switches." (Removal: 0.27.)
# Codes now live on the exception classes (``pyrite.exceptions``) -- REST's spelling, since
# REST's is the more specific one where the two transports used to disagree.
#
# This table and _legacy_mcp_code() below reproduce *exactly* what this
# module computed before this theme (the old _DOMAIN_ERROR_CODES lookup,
# most-specific-first via isinstance, falling back to the class's own old
# error_code attribute, and finally to "REQUEST_REFUSED") -- kept only to
# detect where that disagrees with the class's new code and populate
# ``legacy_error_code`` until 0.27. It is not consulted for the code
# MCP reports going forward (``exc.error_code`` is). A hand-picked list of
# "the codes that changed" was tried first and missed cases (FrontmatterError,
# TruncatedBodyError, PluginError, StorageError -- anything that used to fall
# through to the REQUEST_REFUSED fallback or inherit a base class's old
# literal); replaying the actual old algorithm cannot miss one.
_LEGACY_DOMAIN_ERROR_CODES: tuple[tuple[type[PyriteError], str], ...] = (
    (EntryNotFoundError, "NOT_FOUND"),
    (KBNotFoundError, "NOT_FOUND"),
    (KBReadOnlyError, "READ_ONLY"),
    (KBProtectedError, "KB_PROTECTED"),
    (QuerySyntaxError, "QUERY_SYNTAX"),
    (QueryTooLongError, "QUERY_TOO_LONG"),
    (ValidationError, "VALIDATION_FAILED"),
    (ConfigError, "CONFIG_ERROR"),
    (BrandingInvalidError, "BRANDING_INVALID"),
)
#: The literal ``error_code`` every ``PyriteError`` subclass carried before
#: this theme gave classes without their own a code at all (ADR-0037 theme
#: 2). Anything not listed here had no class-level code pre-theme.
_LEGACY_OWN_ERROR_CODE: dict[str, str] = {
    "ValidationError": "VALIDATION_FAILED",
    "UndeclaredTypeError": "UNDECLARED_TYPE",
    "EntryExistsError": "ENTRY_EXISTS",
    "SchemaViolationError": "SCHEMA_VIOLATION",
    "InvalidGitRefError": "INVALID_REF",
    "QuerySyntaxError": "QUERY_SYNTAX",
    "QueryTooLongError": "QUERY_TOO_LONG",
    "ClipperBlockedHostError": "CLIPPER_BLOCKED_HOST",
    "LastAdminError": "LAST_ADMIN",
}


#: Classes ADR-0037 theme 2 introduces (``exceptions.AccessDenied`` and its
#: subclasses). Never raised by any surface before this theme -- theme 1/3b's
#: job -- so there is no old MCP behaviour to disagree with, and no
#: ``legacy_error_code`` should be reported for them even though they are
#: not explicitly in the tables above either.
_NEW_IN_THIS_THEME = frozenset(
    {
        "AccessDenied",
        "NotAuthenticated",
        "Forbidden",
        "AmbiguousEntryError",
        # #795: QA refusals no MCP surface raised before, so there is no old
        # code to carry either.
        "InvalidQATierError",
        "AINotConfiguredError",
        "EvaluationFailedError",
        "KBUnreadableError",
        # ADR-0044 §1 (2026-10-20): a refusal no surface raised before.
        "LandingRefusedError",
    }
)


def _legacy_mcp_code(exc: PyriteError) -> str | None:
    """What this module would have reported for ``exc`` before this theme,
    or ``None`` for a class this theme introduces (see ``_NEW_IN_THIS_THEME``).

    Walks the exception's own MRO up to (and including) ``PyriteError`` so a
    subclass with no code of its own inherits its nearest ancestor's old
    literal, exactly as plain attribute lookup did before this theme.
    """
    for klass in type(exc).__mro__:
        name = klass.__name__
        if name in _NEW_IN_THIS_THEME:
            return None
        if name in _LEGACY_OWN_ERROR_CODE:
            return _LEGACY_OWN_ERROR_CODE[name]
        if klass is PyriteError:
            break
    for t, c in _LEGACY_DOMAIN_ERROR_CODES:
        if isinstance(exc, t):
            return c
    return "REQUEST_REFUSED"


def _safe_message(exc: Exception) -> str:
    """The message an MCP tool handler may show for ``exc``.

    ADR-0037 theme 2 round 2 (conductor cold read of 5d65caa7, item 1): a
    hand-written ``except PyriteError as e: return _error(CODE, str(e))``
    site bypasses the ``public_message`` ``_refusal`` already respects for
    the dispatcher's own catch-all. A ``StorageError``/``PluginError``/
    ``ConfigError`` (or a subclass that doesn't set its own) can carry
    server-side detail in ``str(exc)`` -- a real path, a driver's own text.
    Every one of these hand-written sites should call this instead of
    ``str(e)`` directly. Logs the real detail server-side when it masks it,
    the same split ``_refusal``/``server/errors.py`` already make.

    Some of these sites catch ``(PyriteError, ValueError)`` together, so
    ``exc`` is not always a ``PyriteError`` -- ``public_message`` is looked
    up with ``getattr``, not assumed present.
    """
    return public_text(exc, log=logger)


def _boolean_arg(args: dict[str, Any], name: str, default: bool = False) -> bool | None:
    """A boolean tool argument; ``None`` when it is neither a bool nor "true"/"false".

    A client that sends the string "false" must not get the truthy reading
    ``bool("false")`` gives (#200).
    """
    value = args.get(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _error(
    code: str,
    message: str,
    *,
    suggestion: str | None = None,
    retryable: bool = False,
    legacy_error_code: str | None = None,
) -> dict:
    """Build a structured error response for MCP tools."""
    r: dict = {"error": message, "error_code": code, "retryable": retryable}
    if suggestion:
        r["suggestion"] = suggestion
    if legacy_error_code is not None:
        r["legacy_error_code"] = legacy_error_code
    return r


def _with_old_kb_update_code(refusal: dict) -> dict:
    """``kb_update``'s ``legacy_error_code`` for a class its old handler never
    named: ``UPDATE_FAILED``, the one code it answered every such failure
    with (its ``except PyriteError`` catch-all), kept until 0.27 (ADR-0037
    theme 2). ``_refusal`` alone would carry the module-wide fallback
    ``REQUEST_REFUSED``, a code ``kb_update`` never gave (#990)."""
    refusal["legacy_error_code"] = "UPDATE_FAILED"
    return refusal


def _refusal(exc: PyriteError, *, already_logged: bool = False) -> dict:
    """Map a service refusal to the MCP envelope.

    The code is the exception class's own (``exc.error_code`` -- every
    ``PyriteError`` carries one; see ``pyrite.exceptions``) -- REST's more
    specific spelling where REST and MCP used to disagree (``ENTRY_NOT_FOUND``
    replacing ``NOT_FOUND``, etc.), or the one spelling REST, MCP and the CLI
    already agreed on where they did not (the base ``ValidationError``:
    conductor decision, fix round 1 -- the write pipeline's documented
    ``VALIDATION_FAILED`` wins over the central handler's less-visited
    ``VALIDATION_ERROR``). Where the class's code differs from what MCP used
    to report for this class (``_legacy_mcp_code``, replaying the old
    ``_LEGACY_DOMAIN_ERROR_CODES``/``_LEGACY_OWN_ERROR_CODE`` lookup), the old
    code is carried until 0.27 in ``legacy_error_code`` so an existing
    MCP caller matching on the old string is not broken outright (ADR-0037
    theme 2, maintainer decision 2026-09-25). A class MCP and REST already
    agreed on gets no ``legacy_error_code`` key at all -- there is nothing
    legacy to report.

    Not retryable -- the same call fails the same way -- except a class
    that says otherwise: a locked or busy database (``StorageBusyError``)
    and a write that lost the race for its file (``ConflictError``, #955)
    can succeed on a retry; schema drift, a missing table and corruption
    cannot (#431).
    """
    code = exc.error_code
    legacy_code = _legacy_mcp_code(exc)
    if legacy_code == code:
        legacy_code = None
    # A class that says it is retryable (a busy database, a write that lost
    # the race for its file and asks for a re-read) is reported so.
    retryable = bool(getattr(exc, "retryable", False))
    # str(exc) is safe for every existing domain error here -- validation
    # messages, "not found", query syntax -- except one that opts out via a
    # `public_message` class attribute because its own str() names a real
    # filesystem path (BrandingInvalidError; the REST side's #377 pattern,
    # #445's cold read).
    # `already_logged`: the dispatchers log a StorageError at ERROR with its
    # traceback (#431) before calling this; the cause is logged once (#848).
    message = public_text(exc, already_logged=already_logged, log=logger)
    err = _error(
        code,
        message,
        suggestion=getattr(exc, "suggestion", None),
        retryable=retryable,
        legacy_error_code=legacy_code,
    )
    # A refusal's public extras (AMBIGUOUS's candidate_kbs, #528), mapped
    # once for every class from REFUSAL_FIELDS in pyrite.exceptions.
    err.update(refusal_fields(exc))
    declared = getattr(exc, "declared_types", None)
    if declared is not None:
        err["declared_types"] = declared
        err.setdefault(
            "suggestion",
            f"Re-call with entry_type in [{', '.join(declared)}] (see kb_schema), "
            "or pass allow_undeclared=true to override.",
        )
    return err


def _protocol_refusal(err: dict) -> Exception:
    """A refusal from a prompt or resource, as a JSON-RPC error.

    `prompts/get` and `resources/read` have no result shape for a refusal, so
    the envelope ``_refusal``/``_error`` built (``error_code``, ``suggestion``,
    ``retryable``, and ``REFUSAL_FIELDS`` such as ``candidate_kbs``) travels
    whole in the JSON-RPC error's ``data``, with ``error`` as its message. A
    bare ``ValueError`` here sent the message alone (#528 fix round 1).
    Tools answer the same envelope as their result instead.
    """
    from mcp.shared.exceptions import McpError
    from mcp.types import INVALID_PARAMS, ErrorData

    return McpError(ErrorData(code=INVALID_PARAMS, message=err["error"], data=err))


MAX_TIMELINE_EVENTS = 50
MAX_BULK_CREATE_ENTRIES = 50
MAX_RESOURCE_LIST_ENTRIES = 200

#: Write tools whose handlers go through KBService's write pipeline, which
#: makes the ADR-0034 truncated-body refusal itself (per item, for bulk). The
#: dispatcher's gate skips them so that decision is made once, in the service
#: (#378); it still covers every other write tool -- task tools and any plugin
#: tool registered into the write or admin tier -- by construction.

#: Write tools that make a database-only record beside their file write (a
#: review verdict). A change set's index is discarded when it merges, so the
#: record would be lost; and the file write comes first, so refusing when the
#: record is reached would leave half a write. They are refused up front on a
#: change-set landing (decided 2026-10-20). The change-set index refuses the
#: same records (`overlay_backend._RECORDS_REFUSED`) for any tool not listed.
_CHANGE_SET_UNSUPPORTED_TOOLS: dict[str, str] = {
    "sw_review": "records a review verdict in the database",
}
_PIPELINE_WRITE_TOOLS = frozenset({"kb_create", "kb_update", "kb_bulk_create"})


# ---------------------------------------------------------------------------
# Per-KB read scoping (#201)
# ---------------------------------------------------------------------------
#
# Every argument name by which an MCP tool can name a KB. `_dispatch_tool`
# checks **every value** found under these names -- not the first -- so that
# naming a readable KB alongside a private one buys nothing. That is the
# `_resolve_kb_names` rule from #180, which exists because a request naming
# two KBs was otherwise checked against one and served from the other.
#
# `kb_names` is plural: a *list* of KB names (the journalism-investigation
# plugin's `investigation_search_all` and `investigation_find_duplicates`).
# Every element is checked. This name was found by the registry gate in
# `tests/test_mcp_tool_registry_is_scoped.py`, not by hand -- which is the
# gate's whole point: a KB-bearing parameter under a name nobody wired up
# reads private content and breaks no existing test.
#
# The gate pins this set. Adding a name here without adding it there (or
# vice versa) fails `test_the_chokepoint_checks_every_name_this_file_claims`.
KB_ARGUMENT_NAMES = (
    "kb_name",
    "kb",
    "source_kb",
    "target_kb",
    "center_kb",
    "kb_names",
    # find_connections names a KB per entry (#528)
    "kb_a",
    "kb_b",
)

# The keyword by which `_dispatch_tool` hands the readable set to a handler
# that spans KBs. A handler opts in by declaring it; handlers that do not --
# including every plugin handler -- are called exactly as before, so no
# reserved key ever leaks into a plugin's `args` dict.
READABLE_KBS_KWARG = "readable_kbs"

# Tools that serve no KB content and therefore need neither a KB refusal nor
# a readable set. Everything NOT here and not filtering is refused for a
# scoped caller -- fail closed, so a tool that spans KBs without being able
# to filter cannot quietly serve private content. Each entry is a claim that
# the tool reads nothing from any KB; the registry gate holds the matching
# inventory with a reason apiece.
NON_KB_CONTENT_TOOLS = frozenset(
    {
        "kb_index_job_status",  # background job state, keyed by job id
        "kb_registry_remove",  # admin: registration, not content
        "kb_registry_reindex",
        "kb_registry_health",
        "kb_registry_add",
    }
)


def _located_ids(spec: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    """The entry ids a `locates_entry` call names: each `id_args` value, a
    string or a list of strings, with blanks and non-strings dropped."""
    ids: list[str] = []
    for arg in spec["id_args"]:
        value = arguments.get(arg)
        values = value if isinstance(value, list | tuple) else [value]
        ids.extend(v for v in values if isinstance(v, str) and v.strip())
    return ids


def _no_id_message(spec: dict[str, Any]) -> str:
    return f"Name an entry id in {' or '.join(spec['id_args'])}"


def _kbs_named_in(arguments: dict[str, Any]) -> list[str]:
    """Every KB name the call names, under any of `KB_ARGUMENT_NAMES`.

    Scalars and lists alike; blanks and non-strings are ignored (a missing
    or malformed value names no KB, and the handler's own validation owns
    the error message for it).
    """
    named: list[str] = []
    for key in KB_ARGUMENT_NAMES:
        value = arguments.get(key)
        if isinstance(value, str):
            if named_kb(value):
                named.append(value)
        elif isinstance(value, list | tuple):
            named.extend(v for v in value if named_kb(v))
    return named


def _only_readable(rows: list[dict], readable_kbs: set[str] | None) -> list[dict]:
    """Drop rows from KBs the caller may not read.

    For the handlers whose storage call takes no `kb_names` (the
    `find_by_*` finders on `PyriteDB`): every row carries `kb_name`, so the
    filter is exact. Widening the storage signatures would be the tidier fix
    and belongs with them, not here.

    **Known imprecision, deliberate:** those queries apply `LIMIT` in SQL,
    so filtering afterwards can return fewer than `limit` rows for a scoped
    caller -- a short page, never a leak. Counts below are computed from the
    filtered list, so they stay truthful about what was returned.
    """
    if readable_kbs is None:
        return rows
    return [r for r in rows if r.get("kb_name") in readable_kbs]


def _kb_refusal(svc: KBService, kb_name: object, readable_kbs: set[str] | None) -> dict | None:
    """The refusal for a KB name the resolver refuses, or None when it is a
    KB the caller may read (or names no KB).

    The one resolver (`services/kb_resolver.py`, #952), asked through
    `KBService.resolve_kb` for **every** caller and every KB a call names,
    by the three chokepoints KB content passes through: `_dispatch_tool`,
    `_get_prompt` and `_read_resource`. An absent name, a KB outside the
    caller's readable set and a shortname (link syntax only) are one
    `KBNotFoundError` -- deliberately not a "forbidden": a private KB's
    existence is itself private, so a caller cannot tell "you may not read
    this" from "there is no such KB". The envelope is `_refusal`'s: the class
    code `KB_NOT_FOUND`, the resolver's suggestion, `did_you_mean` and the
    old `NOT_FOUND` in `legacy_error_code` until 0.27 (#66, ADR-0037
    theme 2). A handler's own not-found answer is only ever reached for a
    name the caller may read.
    """
    try:
        svc.resolve_kb(kb_name, readable_kbs=readable_kbs)
    except KBNotFoundError as e:
        return _refusal(e)
    return None


class PyriteMCPServer:
    """
    Three-tier MCP Server for pyrite.

    Provides tool-based access to knowledge bases for AI agents.
    Tier controls which tools are available.
    """

    VALID_TIERS = ROLES

    def __init__(self, config: PyriteConfig | None = None, tier: str = "read"):
        if tier not in self.VALID_TIERS:
            raise ConfigError(f"Invalid tier '{tier}'. Must be one of {self.VALID_TIERS}")

        self.config = config or load_config()
        self.tier = tier
        # Loaded before anything expensive: ADR-0034 rule 4 wants an invalid
        # PYRITE_BODY_* to stop the server at start, not to be discovered by
        # the first oversized read.
        self.body_bounds: BodyBounds = load_body_bounds()
        self.db = PyriteDB(self.config.settings.index_path)
        self.db.merge_registered_kbs(self.config)
        self.index_mgr = IndexManager(self.db, self.config)

        # KB registry (seeded from config on init)
        from ..services.kb_registry_service import KBRegistryService

        self.registry = KBRegistryService(self.config, self.db, self.index_mgr)
        self.registry.seed_from_config()
        self.svc = KBService(self.config, self.db, registry=self.registry)
        self.graph_svc = GraphService(self.db)
        self.export_svc = ExportService(self.config, self.db)
        self._index_worker = None  # Lazy-init

        # Rate limiter and tool→tier map
        self.rate_limiter = MCPRateLimiter(self.config.settings)
        self._tool_tiers: dict[str, str] = {}

        # Build tool registry based on tier
        self.tools = {}
        self._build_read_tools()
        if tier in ("write", "admin"):
            self._build_write_tools()
        if tier == "admin":
            self._build_admin_tools()

        # Register plugin tools for this tier
        self._register_plugin_tools()

        # Build prompt and resource registries (available at all tiers)
        self.prompts = self._build_prompts()
        self.resources = self._build_resources()
        self.resource_templates = self._build_resource_templates()

    # =========================================================================
    # Where this call writes (ADR-0044 §1)
    # =========================================================================
    #
    # This instance is shared by every caller at its tier, so it holds the
    # canonical config, index and services. While `_dispatch_tool` runs a
    # write whose landing is a change set, the call's target is bound
    # (`services.write_target.binding`) and these read it: a handler that
    # writes with `self.svc` or `self.db` writes where the rule says.

    @property
    def config(self) -> PyriteConfig:
        bound = current_target()
        return bound.config if bound is not None else self._config

    @config.setter
    def config(self, value: PyriteConfig) -> None:
        self._config = value

    @property
    def db(self) -> PyriteDB:
        bound = current_target()
        return bound.db if bound is not None else self._db

    @db.setter
    def db(self, value: PyriteDB) -> None:
        self._db = value

    @property
    def svc(self) -> KBService:
        bound = current_target()
        if bound is None:
            return self._svc
        return bound.service("kb", KBService)

    @svc.setter
    def svc(self, value: KBService) -> None:
        self._svc = value

    def _per_target(self, name: str, build):
        """A lazily built service: the instance's own, or the bound call's."""
        bound = current_target()
        if bound is not None:
            return bound.service(name, build)
        cache = self.__dict__.setdefault("_service_cache", {})
        if name not in cache:
            cache[name] = build(self._config, self._db)
        return cache[name]

    # =========================================================================
    # Tool registration by tier
    # =========================================================================

    def _render_schemas(self, schemas: dict[str, Any]) -> dict[str, Any]:
        """Fill the body-bound placeholders in tool descriptions.

        ADR-0034 rule 4: descriptions report the effective values, not the
        compiled-in ones, so an agent reading the schema on a tuned
        deployment is told that deployment's numbers.
        """
        from .tool_schemas import render_tool_schemas

        return render_tool_schemas(
            schemas,
            body_chunk_default=self.body_bounds.default_chunk,
            body_chunk_max=self.body_bounds.max_chunk,
            body_response_budget=self.body_bounds.response_budget,
        )

    def _register_tool(self, name: str, schema: dict[str, Any], tier: str) -> None:
        """Label a copy at registration, keeping shared schemas unmodified.

        A tool that declares `locates_entry` takes `kb_name` as optional,
        whatever its plugin's schema says (#1310): the SDK validates a call
        against `inputSchema` before `_dispatch_tool` runs, so a required
        `kb_name` would refuse the call the lookup exists to answer. Derived
        here from the declaration, so no plugin can get it wrong."""
        self._tool_tiers[name] = tier
        if schema.get("locates_entry"):
            input_schema = schema.get("inputSchema") or {}
            required = [r for r in input_schema.get("required", []) if r != "kb_name"]
            schema = {**schema, "inputSchema": {**input_schema, "required": required}}
        self.tools[name] = {
            **schema,
            "description": f"[{tier}] {schema.get('description') or ''}",
        }

    def _build_read_tools(self):
        """Register read-only tools (available in all tiers)."""
        from .tool_schemas import READ_TOOLS

        for name, schema in self._render_schemas(READ_TOOLS).items():
            self._register_tool(name, {**schema, "handler": getattr(self, f"_{name}")}, "read")

    def _build_write_tools(self):
        """Register write tools (available in write and admin tiers)."""
        from .tool_schemas import WRITE_TOOLS

        for name, schema in self._render_schemas(WRITE_TOOLS).items():
            self._register_tool(name, {**schema, "handler": getattr(self, f"_{name}")}, "write")

    def _build_admin_tools(self):
        """Register admin tools (available only in admin tier)."""
        from .tool_schemas import ADMIN_TOOLS

        for name, schema in self._render_schemas(ADMIN_TOOLS).items():
            self._register_tool(name, {**schema, "handler": getattr(self, f"_{name}")}, "admin")

    def _register_plugin_tools(self):
        """Register MCP tools from plugins for the current tier."""
        try:
            from ..plugins import PluginContext, get_registry

            registry = get_registry()

            # Inject shared context so plugin handlers don't need to self-bootstrap
            ctx = PluginContext(config=self.config, db=self.db)
            registry.set_context(ctx)

            plugin_tools = registry.get_all_mcp_tools(self.tier)
            # Plugins return every tool up to the tier asked for, so a tool's
            # own tier is the lowest one that returns it. Labelling them all
            # with the server's tier would make a plugin's read tools look
            # like writes to the per-KB write check and the rate limiter.
            lower: set[str] = set()
            for tier in self.VALID_TIERS[: self.VALID_TIERS.index(self.tier) + 1]:
                at_tier = plugin_tools if tier == self.tier else registry.get_all_mcp_tools(tier)
                for name in at_tier:
                    if name not in lower and name in plugin_tools:
                        self._tool_tiers[name] = tier
                lower.update(at_tier)
            for name, schema in plugin_tools.items():
                self._register_tool(name, schema, self._tool_tiers[name])
        except Exception:
            logger.warning("Plugin MCP tool loading failed", exc_info=True)

    # =========================================================================
    # Lazy service properties
    # =========================================================================

    @property
    def qa_svc(self):
        def build(config, db):
            from ..services.llm_service import LLMService
            from ..services.llm_usage_service import LLMUsageService
            from ..services.qa_service import QAService

            # llm-usage-tracking-and-quotas: MCP has no per-request user
            # identity today (server-wide tier, not per-user auth), so
            # usage is recorded with user_id=None -- still gives platform-
            # key cost visibility even without per-user attribution.
            # Usage is the instance's accounting, never a change set's row.
            usage_service = LLMUsageService(self._db)
            llm_service = LLMService(config.settings, usage_service=usage_service)
            return QAService(config, db, llm_service=llm_service)

        return self._per_target("qa", build)

    @property
    def task_svc(self):
        from ..services.task_service import TaskService

        return self._per_target("task", TaskService)

    @property
    def link_svc(self):
        from ..services.link_discovery_service import LinkDiscoveryService

        return self._per_target("link", LinkDiscoveryService)

    @property
    def search_svc(self):
        from ..services.search_service import SearchService

        return self._per_target("search", lambda c, d: SearchService(d, settings=c.settings))

    # =========================================================================
    # Read handlers
    # =========================================================================

    def _kb_list(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """List all knowledge bases, limited to the ones the caller may read.

        The listing *is* the leak here: naming a private KB reveals its
        existence even with no entry ever returned.
        """
        kbs_data = self.registry.list_kbs()
        if readable_kbs is not None:
            kbs_data = [k for k in kbs_data if k.get("name") in readable_kbs]
        kbs = [
            {
                "name": kb["name"],
                "type": kb["type"],
                "path": kb["path"],
                "description": kb.get("description", ""),
                "entry_count": kb["entries"],
                "read_only": kb.get("read_only", False),
                "source": kb.get("source", "user"),
            }
            for kb in kbs_data
        ]
        return {"knowledge_bases": kbs}

    def _kb_search(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Full-text search with optional semantic/hybrid mode.

        With `kb_name` given, the chokepoint has already refused it if it is
        unreadable. With `kb_name` omitted the search spans every KB, so the
        readable set goes to `SearchService.search(kb_names=...)`, which
        restricts *before* the limit is applied -- so `count` counts only
        what the caller may see. Same shape as `endpoints/search.py`.
        """
        query = args.get("query", "")
        limit = args.get("limit", 20)
        fields = args.get("fields")
        include_body = args.get("include_body", False)
        # Anything the search could not do as asked lands here; omitted from the
        # response when empty so the happy path costs an agent no tokens, and so
        # an agent can test for the key's presence. See SearchService.search's
        # docstring, "what a search response owes its caller" (#56).
        warnings: list[str] = []
        try:
            # The one assembler (#668): rows, verdict and warnings. Every key
            # but `results` goes into the payload as it is.
            response = self.search_svc.search_response(
                query=query,
                kb_name=args.get("kb_name"),
                kb_names=None if args.get("kb_name") else readable_kbs,
                entry_type=args.get("entry_type"),
                tags=args.get("tags"),
                date_from=args.get("date_from"),
                date_to=args.get("date_to"),
                limit=limit,
                offset=args.get("offset", 0),
                mode=args.get("mode", "hybrid"),
                expand=args.get("expand", False),
                fips=args.get("fips"),
                state=args.get("state"),
                status=args.get("status"),
                warnings=warnings,
            )
        except QuerySyntaxError as e:
            # Deterministic, not retryable — _dispatch_tool's catch-all
            # would otherwise map this to INTERNAL/retryable=True, sending
            # agents into pointless retry loops on a query that will fail
            # identically every time. search-query-syntax-error-contract.
            return _error(
                "QUERY_SYNTAX",
                str(e),
                suggestion=(
                    "quote tokens containing - : . yourself when your query "
                    "uses AND/OR/NOT or phrase quotes"
                ),
                retryable=False,
            )
        results = response.pop("results")

        if fields or include_body:
            # kb_search takes no body_limit, so every body it returns is
            # bounded by the response budget at the default chunk each
            # (ADR-0034 rule 3: bounded by default on every path, including
            # `fields`). Before this, `fields=["id","body"]` and
            # `include_body=True` both returned whole bodies with no marker.
            results = self.body_bounds.fill_budget(results)
            if fields:
                results = [_project_fields(r, fields) for r in results]
        else:
            # Strip body by default to save tokens — snippet is included instead
            for r in results:
                r.pop("body", None)

        payload: dict[str, Any] = {
            "query": query,
            "count": len(results),
            "has_more": len(results) == limit,
            "results": results,
            **response,
        }
        return payload

    def _kb_get(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get entry by ID.

        With `kb_name` omitted, `KBService.get_entry` considers only the
        caller's readable KBs and refuses an ID found in more than one.
        An entry in an unreadable KB neither answers nor becomes an
        ambiguity candidate.
        """
        entry_id = args.get("entry_id")
        kb_name = args.get("kb_name")
        fields = args.get("fields")
        body_offset = args.get("body_offset", 0)
        body_limit = args.get("body_limit")

        # `kb_name` has already been through the one resolver in
        # `_dispatch_tool` (#952): an unknown name, a KB outside the caller's
        # readable set and a shortname were refused before this ran, and a KB
        # `pyrite kb add` registered while this server runs was found there
        # through the index's registry (#764, #925), so it is served here as
        # kb_list_entries and kb_read_body serve it.

        # Scoped lookup: without `kb_name` it walks only readable KBs, so a
        # private entry neither answers nor shadows a readable twin (P-R5),
        # and the entry's links cover readable KBs only (P-R4).
        result = self.svc.get_entry(entry_id, kb_name=kb_name, readable_kbs=readable_kbs)

        if not result:
            refusal = _refusal(EntryNotFoundError(f"Entry '{entry_id}' not found"))
            refusal["suggestion"] = "Use kb_list_entries or kb_search to find entries"
            return refusal

        # Chunk first, project second (ADR-0034 rules 1 and 3): `fields` is a
        # token-reduction parameter and must never raise the body bound. #58
        # was this pair the other way round, where `fields=[...,"body"]`
        # skipped chunking and returned 28x the caller's explicit body_limit.
        result = self.body_bounds.chunk_body(result, offset=body_offset, limit=body_limit)
        result = _project_fields(result, fields)

        return {"entry": result}

    def _kb_read_body(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Read a chunk of an entry's body text. Lightweight continuation tool.

        This one serves body text directly, so the kb-omitted lookup walking
        every KB was the most direct read of private content there was.
        """
        entry_id = args.get("entry_id")
        kb_name = args.get("kb_name")
        # Same clamp as chunk_body: a negative offset slices from the end,
        # which on a long body returns an empty chunk AND has_more: false —
        # stopping a paginating agent dead on a body it has not read.
        offset = max(0, int(args.get("body_offset", 0)))
        limit = self.body_bounds.effective_limit(args.get("body_limit"))

        result = self.svc.get_entry(entry_id, kb_name=kb_name, readable_kbs=readable_kbs)
        if not result:
            # An entry that is not there is `ENTRY_NOT_FOUND`, the same answer
            # kb_get gives and the same shape (#981, #984): the KB itself was
            # resolved by the dispatcher before this ran.
            refusal = _refusal(EntryNotFoundError(f"Entry '{entry_id}' not found"))
            refusal["suggestion"] = "Use kb_list_entries or kb_search to find entries"
            return refusal

        body = result.get("body") or ""
        body_len = len(body)
        chunk = body[offset : offset + limit]
        return {
            "body": chunk,
            "body_length": body_len,
            "body_offset": offset,
            "body_chunk_size": len(chunk),
            "has_more": offset + len(chunk) < body_len,
        }

    def _kb_timeline(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get timeline events across every readable KB.

        `count` and `has_more` below are computed from the filtered list, so
        they never reveal how many private events matched.
        """
        limit = args.get("limit", 50)
        results = self.svc.get_timeline(
            date_from=args.get("date_from"),
            date_to=args.get("date_to"),
            min_importance=args.get("min_importance", 1),
            limit=limit,
            offset=args.get("offset", 0),
            kb_names=readable_kbs,
        )
        return {
            "count": len(results),
            "has_more": len(results) == limit,
            "events": results,
        }

    def _kb_backlinks(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get backlinks to an entry, from the KBs the caller can read.

        The chokepoint checks the named KB; the sources come from any KB, so
        one the caller cannot read is dropped in the query (P-R4), and the
        count and `has_more` are over what is returned.
        """
        entry_id = args.get("entry_id")
        kb_name = args.get("kb_name")
        limit = args.get("limit", 100)
        backlinks = self.graph_svc.get_backlinks(
            entry_id, kb_name, limit=limit, offset=args.get("offset", 0), readable_kbs=readable_kbs
        )
        return {
            "entry_id": entry_id,
            "backlink_count": len(backlinks),
            "has_more": len(backlinks) == limit,
            "backlinks": backlinks,
        }

    def _kb_tags(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get all tags with counts.

        Tag *names* are KB content: `confidential/operation-zebra` says a
        great deal without a single entry being returned.
        """
        kb_name = args.get("kb_name")
        prefix = args.get("prefix") or None
        kb_names = None if kb_name else readable_kbs

        if args.get("tree"):
            tree = self.svc.get_tag_tree(kb_name=kb_name, kb_names=kb_names)
            return {"tree": tree}

        limit = args.get("limit", 100)
        tag_dicts = self.svc.get_tags(
            kb_name=kb_name,
            kb_names=kb_names,
            limit=limit,
            offset=args.get("offset", 0),
            prefix=prefix,
        )
        tags = [{"tag": t["name"], "count": t["count"]} for t in tag_dicts]
        return {
            "tag_count": len(tags),
            "has_more": len(tags) == limit,
            "tags": tags,
        }

    def _kb_stats(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get index statistics over the KBs the caller may read.

        As REST's /api/stats: the per-KB map and every total are computed
        from the readable set; `None` (unscoped) is the whole index.
        """
        return self.index_mgr.get_index_stats(kb_names=readable_kbs)

    def _kb_schema(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get KB schema for agent discoverability."""
        kb_name = args.get("kb_name")
        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            return _error("NOT_FOUND", f"KB '{kb_name}' not found")

        schema = kb_config.kb_schema
        return schema.to_agent_schema()

    def _kb_qa_validate(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Validate KB structural integrity.

        The `validate_all` branch (no `kb_name`) sweeps every KB and reports
        per-entry issues -- entry ids and titles from KBs the caller may not
        read. Restricted to the readable set.
        """
        qa = self.qa_svc

        entry_id = args.get("entry_id")
        kb_name = args.get("kb_name")
        severity_filter = args.get("severity", "warning")
        limit = args.get("limit", 50)

        # Link checks are bounded by the readable set too: a target the caller
        # cannot read is reported as a missing one, and a private linker does
        # not keep an entry from being an orphan (P-R4, P-R5).
        if entry_id and kb_name:
            result = qa.validate_entry(entry_id, kb_name, readable_kbs=readable_kbs)
            issues = result["issues"]
        elif kb_name:
            result = qa.validate_kb(kb_name, readable_kbs=readable_kbs)
            issues = result["issues"]
        else:
            result = qa.validate_all(kb_names=readable_kbs, readable_kbs=readable_kbs)
            issues = []
            for kb in result["kbs"]:
                issues.extend(kb["issues"])

        # Filter by severity
        severity_order = {"error": 0, "warning": 1, "info": 2}
        min_level = severity_order.get(severity_filter, 1)
        issues = [
            i for i in issues if severity_order.get(i.get("severity", "info"), 2) <= min_level
        ]

        # Apply limit
        truncated = len(issues) > limit
        issues = issues[:limit]

        return {
            "issues": issues,
            "count": len(issues),
            "truncated": truncated,
        }

    def _kb_qa_status(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get QA status dashboard with coverage stats."""
        qa = self.qa_svc
        # Counts over the readable set too: a private target counts as broken
        # and a private linker does not keep an entry from being an orphan,
        # exactly as kb_qa_validate reports them (P-R4, P-R5).
        status = qa.get_status(
            kb_name=args.get("kb_name"),
            kb_names=None if args.get("kb_name") else readable_kbs,
            readable_kbs=readable_kbs,
        )

        # Add coverage stats if a specific KB is requested
        kb_name = args.get("kb_name")
        if kb_name:
            status["coverage"] = qa.get_coverage(kb_name)

        return status

    def _kb_batch_read(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Fetch multiple entries in one call.

        The KBs are named inside the `entries` array, not by a KB parameter,
        so the chokepoint cannot see them. Unreadable pairs are dropped and
        fall through to the existing `not_found` list: an *error* here would
        itself reveal that the KB exists. Same rule as `/api/entries/batch`.
        """
        entries_spec = args.get("entries", [])
        fields = args.get("fields")
        body_offset = args.get("body_offset", 0)
        body_limit = args.get("body_limit")

        if not isinstance(entries_spec, list):
            return _error(
                "VALIDATION_FAILED",
                "entries must be an array of {entry_id, kb_name} objects",
                suggestion='pass entries as [{"entry_id": ..., "kb_name": ...}]',
                retryable=False,
            )
        if not entries_spec:
            return _error("VALIDATION_FAILED", "entries array is required and must not be empty")
        if len(entries_spec) > MAX_BATCH_READ_ENTRIES:
            return _error("VALIDATION_FAILED", f"Maximum {MAX_BATCH_READ_ENTRIES} entries per call")

        # Key presence is not enough: a non-dict item, or a non-string/empty
        # id, reached the SQL layer or a bare `not_found` and surfaced as
        # INTERNAL/retryable=True or a plausible-looking miss. All of them are
        # deterministic client errors (kb-batch-read-spec-validation-contract).
        for index, spec in enumerate(entries_spec):
            if (
                not isinstance(spec, dict)
                or not isinstance(spec.get("entry_id"), str)
                or not spec["entry_id"]
                or not isinstance(spec.get("kb_name"), str)
                or not spec["kb_name"]
            ):
                return _error(
                    "VALIDATION_FAILED",
                    f"entries[{index}] must be an object with non-empty string entry_id and kb_name",
                    suggestion='pass entries as [{"entry_id": ..., "kb_name": ...}]',
                    retryable=False,
                )

        ids = [(e["entry_id"], e["kb_name"]) for e in entries_spec]
        if readable_kbs is not None:
            # Items in KBs the caller may not read are reported as not found.
            ids = [(eid, kb) for eid, kb in ids if kb in readable_kbs]
        results = self.svc.get_entries(ids)

        # The per-body ceiling does not bound a response: 50 entries at the
        # ceiling is a megabyte. fill_budget spends PYRITE_BODY_RESPONSE_BUDGET
        # in request order, so later entries come back truncated (possibly to
        # zero) WITH the marker rather than the response growing without limit
        # (ADR-0034 rule 4). Projection runs after, and keeps the marker.
        results = self.body_bounds.fill_budget(results, offset=body_offset, limit=body_limit)
        if fields:
            results = [_project_fields(r, fields) for r in results]

        found_ids = {(r["id"], r["kb_name"]) for r in results}
        # From what was *requested*, not from the filtered `ids`: a pair
        # dropped by scoping must appear here, indistinguishable from one
        # that simply does not exist. Dropping it from the response entirely
        # would be its own signal.
        requested = [(e["entry_id"], e["kb_name"]) for e in entries_spec]
        not_found = [
            {"entry_id": eid, "kb_name": kb} for eid, kb in requested if (eid, kb) not in found_ids
        ]

        return {
            "entries": results,
            "found": len(results),
            "not_found": not_found,
        }

    def _kb_list_entries(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Browse entries with optional filters and pagination.

        `total` (and therefore `has_more`) is counted over the same readable
        set as the rows, so the pagination metadata cannot reveal entries the
        listing itself withheld.
        """
        kb_name = args.get("kb_name")
        entry_type = args.get("entry_type")
        tag = args.get("tag")
        sort_by = args.get("sort_by", "updated_at")
        sort_order = args.get("sort_order", "desc")
        limit = min(args.get("limit", 50), 200)
        offset = args.get("offset", 0)
        fields = args.get("fields")

        kb_names = None if kb_name else readable_kbs
        entries = self.svc.list_entries(
            kb_name=kb_name,
            kb_names=kb_names,
            entry_type=entry_type,
            tag=tag,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
        total = self.svc.count_entries(
            kb_name=kb_name, kb_names=kb_names, entry_type=entry_type, tag=tag
        )

        # list_entries returns whole bodies from the index; up to 200 of them
        # was an unbounded response on a browse tool (ADR-0034 rule 3).
        entries = self.body_bounds.fill_budget(entries)
        if fields:
            entries = [_project_fields(e, fields) for e in entries]

        return {
            "entries": entries,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        }

    def _kb_orient(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """One-shot KB orientation; with no `kb_name`, the KBs the caller may read.

        Takes `readable_kbs` for two reasons: the no-name answer is a listing,
        which must be filtered, and without the parameter the dispatcher's
        fail-closed rule refuses a scoped caller who names no KB.
        """
        kb_name = args.get("kb_name")
        # `"detail": null` is "not given"; anything else goes to the service.
        detail = args.get("detail")
        try:
            # Null or a blank string is "no name"; any other non-string is a
            # malformed name, not a request for the overview.
            if kb_name is not None and not isinstance(kb_name, str):
                raise ValidationError(f"kb_name must be a string, not {type(kb_name).__name__}")
            if not named_kb(kb_name):
                return self.svc.orient_overview(
                    readable_kbs=readable_kbs,
                    detail=detail,
                    limit=args.get("limit", 50),
                    offset=args.get("offset", 0),
                )
            return self.svc.orient(kb_name, recent_limit=args.get("recent_limit", 5), detail=detail)
        except PyriteError as e:
            # A name the resolver refuses never reaches this handler: the
            # dispatcher answers it, with `did_you_mean` and the suggestion,
            # for every caller (#952).
            return _refusal(e)

    def _kb_recent(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get recently changed entries."""
        kb_name = args.get("kb_name")
        entry_type = args.get("entry_type")
        limit = min(args.get("limit", 20), 200)
        since = args.get("since")
        fields = args.get("fields")

        entries = self.svc.list_entries(
            kb_name=kb_name,
            kb_names=None if kb_name else readable_kbs,
            entry_type=entry_type,
            sort_by="updated_at",
            sort_order="desc",
            limit=limit,
        )

        # Post-filter by `since` if provided
        if since:
            entries = [e for e in entries if (e.get("updated_at") or "") >= since]

        # Same unbounded-browse shape as kb_list_entries (ADR-0034 rule 3).
        entries = self.body_bounds.fill_budget(entries)
        if fields:
            entries = [_project_fields(e, fields) for e in entries]

        return {
            "entries": entries,
            "count": len(entries),
        }

    def _kb_qa_assess(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Assess entry or KB quality; link checks bounded by the readable set."""
        qa = self.qa_svc
        kb_name = args["kb_name"]
        entry_id = args.get("entry_id")
        tier = args.get("tier", 1)
        create_tasks = args.get("create_tasks", False)

        if entry_id:
            return qa.assess_entry(
                entry_id,
                kb_name,
                tier=tier,
                create_task_on_fail=create_tasks,
                readable_kbs=readable_kbs,
            )
        else:
            max_age = args.get("max_age_hours", 24)
            return qa.assess_kb(
                kb_name,
                tier=tier,
                max_age_hours=max_age,
                create_task_on_fail=create_tasks,
                readable_kbs=readable_kbs,
            )

    # =========================================================================
    # Post-save QA validation
    # =========================================================================

    def _maybe_validate(
        self,
        entry_id: str,
        kb_name: str,
        args: dict,
        *,
        readable_kbs: set[str] | None,
    ) -> list[dict] | None:
        """Run post-save QA validation if requested or KB has qa_on_write.

        The issues go back to the writer, so their link checks are bounded
        by the writer's readable set, as `kb_qa_validate`'s are.
        """
        should_validate = args.get("validate", False)
        if not should_validate:
            kb_config = self.config.get_kb(kb_name)
            if kb_config:
                schema = kb_config.kb_schema
                should_validate = schema.validation.get("qa_on_write", False)
        if should_validate:
            result = self.qa_svc.validate_entry(entry_id, kb_name, readable_kbs=readable_kbs)
            if result["issues"]:
                return result["issues"]
        return None

    # =========================================================================
    # Protocol query handlers (ADR-0017)
    # =========================================================================

    def _kb_find_by_assignee(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Find entries assigned to a specific agent/user."""
        assignee = args.get("assignee", "")
        if not assignee:
            return _error("VALIDATION_FAILED", "assignee is required")
        rows = self.task_svc.find_by_assignee(
            assignee=assignee,
            kb_name=args.get("kb_name"),
            status=args.get("status"),
            limit=min(args.get("limit", 50), 200),
            offset=args.get("offset", 0),
            kb_names=None if args.get("kb_name") else readable_kbs,
        )
        rows = _only_readable(rows, readable_kbs)
        return {"entries": rows, "count": len(rows), "assignee": assignee}

    def _kb_find_overdue(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Find entries with overdue due_date."""
        rows = self.task_svc.find_overdue(
            as_of=args.get("as_of"),
            kb_name=args.get("kb_name"),
            limit=min(args.get("limit", 50), 200),
            offset=args.get("offset", 0),
            kb_names=None if args.get("kb_name") else readable_kbs,
        )
        rows = _only_readable(rows, readable_kbs)
        return {"entries": rows, "count": len(rows)}

    def _kb_find_by_status(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Find entries by status across all types."""
        status = args.get("status", "")
        if not status:
            return _error("VALIDATION_FAILED", "status is required")
        rows = self.task_svc.find_by_status(
            status=status,
            kb_name=args.get("kb_name"),
            entry_type=args.get("entry_type"),
            limit=min(args.get("limit", 50), 200),
            offset=args.get("offset", 0),
            kb_names=None if args.get("kb_name") else readable_kbs,
        )
        rows = _only_readable(rows, readable_kbs)
        return {"entries": rows, "count": len(rows), "status": status}

    def _kb_find_by_location(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Find entries by location (substring match)."""
        location = args.get("location", "")
        if not location:
            return _error("VALIDATION_FAILED", "location is required")
        rows = self.task_svc.find_by_location(
            location=location,
            kb_name=args.get("kb_name"),
            limit=min(args.get("limit", 50), 200),
            offset=args.get("offset", 0),
            kb_names=None if args.get("kb_name") else readable_kbs,
        )
        rows = _only_readable(rows, readable_kbs)
        return {"entries": rows, "count": len(rows), "location": location}

    def _list_edge_types(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """List available edge types with their endpoint schemas.

        Iterates config directly, so with `kb_name` omitted it names every
        KB and counts its entries -- a private KB's name and size, without
        an entry ever being returned.
        """
        kb_name = args.get("kb_name")

        edge_types = []

        for kb_config in self.config.all_kbs():
            if kb_name and kb_config.name != kb_name:
                continue
            if readable_kbs is not None and kb_config.name not in readable_kbs:
                continue

            schema = kb_config.kb_schema
            for type_name, type_schema in schema.types.items():
                if not getattr(type_schema, "edge_type", False):
                    continue

                endpoints = {}
                for role, ep in type_schema.endpoints.items():
                    endpoints[role] = {
                        "field": ep.field,
                        "accepts": ep.accepts,
                    }

                # Count existing edges of this type
                count = self.svc.count_entries(
                    kb_name=kb_config.name,
                    entry_type=type_name,
                )

                edge_types.append(
                    {
                        "type": type_name,
                        "kb_name": kb_config.name,
                        "description": type_schema.description,
                        "endpoints": endpoints,
                        "count": count,
                    }
                )

        return {
            "edge_types": edge_types,
            "total": len(edge_types),
        }

    # =========================================================================
    # Write handlers
    # =========================================================================

    def _kb_create(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Create a new entry. Every create decision is KBService's (#378)."""
        kb_name = args.get("kb_name")
        spec = {k: v for k, v in args.items() if k not in _CREATE_CONTROL_KEYS}
        spec.setdefault("entry_type", "note")
        try:
            written = self.svc.create(
                kb_name, spec, allow_undeclared=bool(args.get("allow_undeclared"))
            )
        except KBNotFoundError:
            return _error(
                "KB_NOT_FOUND",
                f"KB '{kb_name}' not found",
                suggestion="Use kb_list to see available KBs",
            )
        except KBReadOnlyError:
            return _error("READ_ONLY", f"KB '{kb_name}' is read-only")
        except ValidationError as e:
            return _refusal(e)
        except PyriteError as e:
            return _error("CREATE_FAILED", _safe_message(e), retryable=True)

        entry = written.entry
        result = {
            "created": True,
            "entry_id": entry.id,
            "file_path": str(entry.file_path) if entry.file_path else "",
        }
        if written.warnings:
            result["warnings"] = written.warnings
        qa_issues = self._maybe_validate(entry.id, kb_name, args, readable_kbs=readable_kbs)
        if qa_issues:
            result["qa_issues"] = qa_issues
        return result

    def _kb_bulk_create(self, args: dict[str, Any]) -> dict[str, Any]:
        """Batch-create multiple entries through KBService's write pipeline.

        Each item is refused or created on its own, with the same codes
        kb_create gives (#359, #366, #378); results keep the input order.
        """
        kb_name = args.get("kb_name")
        entries = args.get("entries", [])

        if not entries:
            return _error("VALIDATION_FAILED", "entries array is required and must not be empty")
        if len(entries) > MAX_BULK_CREATE_ENTRIES:
            return _error(
                "VALIDATION_FAILED", f"Maximum {MAX_BULK_CREATE_ENTRIES} entries per call"
            )

        try:
            results = self.svc.bulk_create_entries(
                kb_name, entries, allow_undeclared=bool(args.get("allow_undeclared"))
            )
        except PyriteError as e:
            return _error("BULK_CREATE_FAILED", _safe_message(e), retryable=True)

        created = sum(1 for r in results if r.get("created"))
        failed = len(results) - created
        return {
            "total": len(results),
            "created": created,
            "failed": failed,
            "results": results,
        }

    def _kb_update(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Update an existing entry.

        Every argument other than the tool's own goes to ``KBService.update``,
        so a key the type does not declare is stored the way CLI and REST
        store it (#455). An agent that echoes a whole read result back still
        cannot rewrite the id, path, timestamps, a type's managed fields or
        index columns: ``KBService.split_echoed_update`` sets those aside,
        and the result names them in ``ignored``. A field an echo carries at
        the value the read returned is not written either; the result names
        it in ``unchanged`` (#561).
        """
        entry_id = args.get("entry_id")
        kb_name = args.get("kb_name")

        # ADR-0034 at any depth, on the raw arguments: the filter below keeps
        # only the type's fields, which would drop a marker nested under any
        # other key while keeping the body it marks.
        try:
            ensure_not_truncated(args)
        except ValidationError as e:
            return _refusal(e)

        updates, ignored, unchanged = self.svc.split_echoed_update(
            entry_id,
            kb_name,
            {k: v for k, v in args.items() if k not in _UPDATE_CONTROL_KEYS},
        )

        try:
            written = self.svc.update(
                entry_id,
                kb_name,
                updates,
                append=args.get("append"),
                remove=args.get("remove"),
                unset=args.get("unset"),
            )
        except ValidationError as e:
            return _refusal(e)
        except ConflictError as e:
            # A write that lost the race for its file (#955): the class's
            # own code, with the old code this handler gave everything it
            # did not name (ADR-0037, removal in 0.27).
            return _with_old_kb_update_code(_refusal(e))
        except StorageError as e:
            # A storage fault on the write (a hard-linked file, the lock
            # directory, a lock held too long): the class's own code and
            # `retryable`, its public text, the detail logged once (#431).
            logger.error("Tool kb_update failed: %s", e, exc_info=e)
            return _with_old_kb_update_code(_refusal(e, already_logged=True))
        except (EntryNotFoundError, KBNotFoundError) as e:
            # Keep the old handler-level spelling until 0.27 while the
            # class code becomes the primary contract (ADR-0037).
            refusal = _refusal(e)
            refusal["legacy_error_code"] = "UPDATE_FAILED"
            return refusal
        except PyriteError as e:
            return _error("UPDATE_FAILED", _safe_message(e), retryable=True)

        entry = written.entry
        result: dict[str, Any] = {
            "updated": True,
            "entry_id": entry.id,
            "file_path": str(entry.file_path) if entry.file_path else "",
        }
        # ADR-0042 (#955): said only when not the usual: a request that
        # changed nothing wrote nothing; a KB outside git was written with
        # no lock; a templated-folder field did not move an unpinned file.
        if not written.changed:
            result["changed"] = False
        if not written.locked:
            result["locked"] = False
        if written.not_moved:
            result["not_moved"] = written.not_moved
        if ignored:
            result["ignored"] = ignored
        if unchanged:
            result["unchanged"] = unchanged
        if written.warnings:
            result["warnings"] = written.warnings
        qa_issues = self._maybe_validate(entry.id, kb_name, args, readable_kbs=readable_kbs)
        if qa_issues:
            result["qa_issues"] = qa_issues
        return result

    def _kb_delete(self, args: dict[str, Any]) -> dict[str, Any]:
        """Delete an entry."""
        entry_id = args.get("entry_id")
        kb_name = args.get("kb_name")

        try:
            deleted = self.svc.delete_entry(entry_id, kb_name)
        except PyriteError as e:
            return _error("DELETE_FAILED", _safe_message(e), retryable=True)

        if not deleted:
            # An entry that is not in the named KB is `ENTRY_NOT_FOUND`, as
            # kb_get and kb_update answer it (#981, #984); the KB was resolved
            # by the dispatcher, and a file that is gone was not deleted.
            refusal = _refusal(EntryNotFoundError(f"Entry '{entry_id}' not found"))
            refusal["suggestion"] = "Use kb_list_entries or kb_search to find entries"
            return refusal

        return {"deleted": True, "entry_id": entry_id}

    def _kb_link(self, args: dict[str, Any]) -> dict[str, Any]:
        """Create a link between two entries."""
        source_id = args.get("source_id")
        source_kb = args.get("source_kb")
        target_id = args.get("target_id")
        relation = args.get("relation", "related_to")
        target_kb = args.get("target_kb")
        note = args.get("note", "")
        allow_dangling = args.get("allow_dangling", False)

        # The spec is checked here, outside the try below, so VALIDATION_FAILED
        # is the answer for this refusal only: a ValidationError subclass
        # raised later inside add_link (storage, frontmatter) keeps the
        # LINK_FAILED answer it always had (#846, fix round 1). add_link runs
        # the same check again; it is the rule's home for every other caller.
        try:
            target_id, _relation, note = check_link_spec(
                target_id, relation, note=note, target_kb=target_kb
            )
        except ValidationError as e:
            return _refusal(e)

        try:
            result = self.svc.add_link(
                source_id=source_id,
                source_kb=source_kb,
                target_id=target_id,
                relation=relation,
                target_kb=target_kb,
                note=note,
                allow_dangling=allow_dangling,
            )
        except (EntryNotFoundError, KBNotFoundError) as e:
            # Both are deterministic: a missing entry or an unregistered KB does
            # not appear on retry. _DOMAIN_ERROR_CODES maps both to NOT_FOUND
            # elsewhere; #97's acceptance text asks for LINK_FAILED here, so
            # this handler deliberately diverges on the code but not on
            # retryability.
            return _error("LINK_FAILED", str(e), retryable=False)
        except PyriteError as e:
            return _error("LINK_FAILED", _safe_message(e), retryable=True)

        return {
            "linked": True,
            "source_id": source_id,
            # Every echoed field is a string, as tool_schemas.py types the
            # argument it echoes (fix round 3). `target_id` is the link the
            # caller asked for, the checked argument -- always text, since
            # check_link_spec refuses any other type. Not the stored value: on
            # a replay against a hand file that is whatever YAML made of it
            # (an int, a bool, a date), which matched the request as text.
            # `relation` is the one on disk, as `pyrite link` reports it (a
            # legacy `related` replayed as `related_to`); a match is always
            # text, and a create stores the checked one. The raw argument
            # could be null.
            "target_id": target_id,
            "relation": result["relation"],
            "resolved": result["resolved"],
            "created": result["created"],
        }

    # =========================================================================
    # Task handlers
    # =========================================================================

    def _task_list(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """List tasks with filters."""
        kb_name = args.get("kb_name")
        tasks = self.task_svc.list_tasks(
            kb_name=kb_name,
            kb_names=None if kb_name else readable_kbs,
            status=args.get("status"),
            assignee=args.get("assignee"),
            parent=args.get("parent"),
        )
        return {"count": len(tasks), "tasks": tasks}

    def _resolve_task_kb(
        self, task_id: str, kb_name: str | None, *, readable_kbs: set[str] | None
    ) -> tuple[str, dict[str, Any] | None]:
        """Resolve the KB a task lives in. Returns (kb_name, error).

        The task-graph service methods need a concrete kb_name, but the tool
        schemas make it optional, so look the task up when it is omitted.

        That lookup spans the KBs, so it is the shared chokepoint for the
        four task-graph tools, and it is made within the caller's readable
        set: a task in a KB the caller may not read is a plain task miss,
        identical to a task id that does not exist, and never hides a
        readable task with the same id (P-R5). Naming the KB would reveal
        where the task lives.
        """
        task = self.task_svc.get_task(task_id, kb_name, readable_kbs=readable_kbs)
        if not task:
            return "", _error("NOT_FOUND", f"Task '{task_id}' not found")
        return kb_name or task.get("kb_name", ""), None

    def _task_subtree(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get all descendants of a task."""
        task_id = args.get("task_id")
        if not task_id:
            return _error("MISSING_PARAMETER", "task_id is required")
        kb_name, err = self._resolve_task_kb(
            task_id, args.get("kb_name"), readable_kbs=readable_kbs
        )
        if err:
            return err
        result = self.task_svc.get_subtree(task_id, kb_name)
        return {"task_id": task_id, "count": len(result), "subtree": result}

    def _task_ancestors(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get parent chain from task to root."""
        task_id = args.get("task_id")
        if not task_id:
            return _error("MISSING_PARAMETER", "task_id is required")
        kb_name, err = self._resolve_task_kb(
            task_id, args.get("kb_name"), readable_kbs=readable_kbs
        )
        if err:
            return err
        result = self.task_svc.get_ancestors(task_id, kb_name)
        return {"task_id": task_id, "count": len(result), "ancestors": result}

    def _task_blocked_by(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get transitive dependency chain."""
        task_id = args.get("task_id")
        if not task_id:
            return _error("MISSING_PARAMETER", "task_id is required")
        kb_name, err = self._resolve_task_kb(
            task_id, args.get("kb_name"), readable_kbs=readable_kbs
        )
        if err:
            return err
        result = self.task_svc.get_blocked_by(task_id, kb_name)
        return {"task_id": task_id, "count": len(result), "blocked_by": result}

    def _task_critical_path(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Find the longest blocking dependency chain."""
        task_id = args.get("task_id")
        if not task_id:
            return _error("MISSING_PARAMETER", "task_id is required")
        kb_name, err = self._resolve_task_kb(
            task_id, args.get("kb_name"), readable_kbs=readable_kbs
        )
        if err:
            return err
        result = self.task_svc.critical_path(task_id, kb_name)
        return {"task_id": task_id, "chain_length": len(result), "critical_path": result}

    def _task_status(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get task details with children, deps, evidence."""
        import json as _json

        task_id = args.get("task_id")
        kb_name = args.get("kb_name")

        task = self.task_svc.get_task(task_id, kb_name, readable_kbs=readable_kbs)
        if not task:
            return _error("NOT_FOUND", f"Task '{task_id}' not found")

        meta = task.get("metadata", {})
        if isinstance(meta, str):
            try:
                meta = _json.loads(meta)
            except (ValueError, TypeError):
                meta = {}

        children_list = self.task_svc.list_tasks(
            kb_name=kb_name,
            kb_names=None if kb_name else readable_kbs,
            parent=task_id,
        )
        children = [
            {"id": c["id"], "title": c["title"], "status": c["status"], "derived": c["derived"]}
            for c in children_list
        ]
        task_kb = task.get("kb_name", kb_name or "")
        from ..models.task import coerce_task_priority as _coerce_task_priority

        return {
            "id": task["id"],
            "title": task["title"],
            # The index row's columns, as `task get` reads them: `status`
            # is a column, never in `metadata`, so meta.get("status") said
            # "open" for every task, whatever its file said.
            "status": task.get("status") or meta.get("status", "open"),
            "assignee": task.get("assignee") or meta.get("assignee", ""),
            # The model's reading, as list_tasks gives it (#554): an index row
            # may still hold a word such as `medium`.
            "priority": _coerce_task_priority(
                task.get("priority") or meta.get("priority", 5), task["id"]
            ),
            "parent": meta.get("parent", ""),
            "dependencies": meta.get("dependencies", []),
            "evidence": meta.get("evidence", []),
            "due_date": meta.get("due_date", ""),
            "agent_context": meta.get("agent_context", {}),
            "children": children,
            "kb_name": task_kb,
            # Computed from the children; never a field of the file.
            "derived": self.task_svc.derived_for(task_kb, task["id"]),
        }

    def _kb_batch_suggest(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Batch-compare two KBs to find potential cross-KB links."""
        source_kb = args.get("source_kb")
        target_kb = args.get("target_kb")
        if not source_kb or not target_kb:
            return _error("MISSING_PARAMETER", "source_kb and target_kb are required")

        include_empty = _boolean_arg(args, "include_empty")
        if include_empty is None:
            return _error("INVALID_PARAMETER", "include_empty must be true or false")

        svc = self.link_svc
        pairs = svc.batch_suggest(
            source_kb=source_kb,
            target_kb=target_kb,
            limit_per_entry=args.get("limit_per_entry", 3),
            mode=args.get("mode", "keyword"),
            exclude_linked=args.get("exclude_linked", True),
            readable_kbs=readable_kbs,
            include_empty=include_empty,
        )

        return {
            "source_kb": source_kb,
            "target_kb": target_kb,
            "count": len(pairs),
            "pairs": pairs,
        }

    def _kb_discover_neighbors(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Find related entries in all KBs, including the source KB, unless narrowed."""
        entry_id = args.get("entry_id")
        kb_name = args.get("kb_name")
        if not entry_id or not kb_name:
            return _error("MISSING_PARAMETER", "entry_id and kb_name are required")

        include_empty = _boolean_arg(args, "include_empty")
        if include_empty is None:
            return _error("INVALID_PARAMETER", "include_empty must be true or false")

        svc = self.link_svc
        candidates = svc.discover_neighbors(
            entry_id=entry_id,
            kb_name=kb_name,
            target_kb=args.get("target_kb"),
            limit=args.get("limit", 10),
            mode=args.get("mode", "hybrid"),
            exclude_linked=args.get("exclude_linked", True),
            readable_kbs=readable_kbs,
            include_empty=include_empty,
        )

        return {
            "entry_id": entry_id,
            "kb_name": kb_name,
            "count": len(candidates),
            "discoveries": candidates,
        }

    def _task_create(self, args: dict[str, Any]) -> dict[str, Any]:
        """Create a new task."""
        return self.task_svc.create_task(
            kb_name=args["kb_name"],
            title=args["title"],
            body=args.get("body", ""),
            parent=args.get("parent", ""),
            priority=args.get("priority", 5),
            assignee=args.get("assignee", ""),
            dependencies=args.get("dependencies"),
            tags=args.get("tags"),
        )

    def _task_update(self, args: dict[str, Any]) -> dict[str, Any]:
        """Update task fields."""
        task_id = args.get("task_id")
        kb_name = args.get("kb_name")
        if not task_id:
            return _error("MISSING_PARAMETER", "task_id is required")
        if not kb_name:
            return _error("MISSING_PARAMETER", "kb_name is required")

        updates = {}
        if "status" in args:
            updates["status"] = args["status"]
        if "assignee" in args:
            updates["assignee"] = args["assignee"]
        if "priority" in args:
            updates["priority"] = args["priority"]

        if not updates:
            return _error("VALIDATION_ERROR", "No updates specified")

        return self.task_svc.update_task(task_id, kb_name, **updates)

    def _task_claim(self, args: dict[str, Any]) -> dict[str, Any]:
        """Atomically claim an open task."""
        return self.task_svc.claim_task(
            task_id=args["task_id"],
            kb_name=args["kb_name"],
            assignee=args["assignee"],
        )

    def _task_decompose(self, args: dict[str, Any]) -> dict[str, Any]:
        """Decompose a parent task into children."""
        try:
            results = self.task_svc.decompose_task(
                parent_id=args["parent_id"],
                kb_name=args["kb_name"],
                children=args["children"],
            )
            return {
                "decomposed": True,
                "parent_id": args["parent_id"],
                "children": results,
                "parent_derived": self.task_svc.derived_for(args["kb_name"], args["parent_id"]),
            }
        except (PyriteError, ValueError) as e:
            return _error("OPERATION_FAILED", _safe_message(e))

    def _task_checkpoint(self, args: dict[str, Any]) -> dict[str, Any]:
        """Log a checkpoint on a task."""
        try:
            return self.task_svc.checkpoint_task(
                task_id=args["task_id"],
                kb_name=args["kb_name"],
                message=args["message"],
                confidence=args.get("confidence", 0.0),
                partial_evidence=args.get("partial_evidence"),
            )
        except (PyriteError, ValueError) as e:
            return _error("OPERATION_FAILED", _safe_message(e))

    # =========================================================================
    # Admin handlers
    # =========================================================================

    def _kb_index_sync(self, args: dict[str, Any]) -> dict[str, Any]:
        """Sync index with file changes. Set background=true for async execution."""
        kb_name = args.get("kb_name")
        background = args.get("background", False)

        if background:
            worker = self._get_index_worker()
            job_id = worker.submit_sync(kb_name)
            return {"submitted": True, "job_id": job_id}

        results = self.index_mgr.sync_incremental(kb_name)
        return {
            "synced": True,
            "added": results["added"],
            "updated": results["updated"],
            "removed": results["removed"],
            "skipped": results["skipped"],
        }

    def _kb_index_job_status(self, args: dict[str, Any]) -> dict[str, Any]:
        """Check status of a background index job."""
        job_id = args.get("job_id")
        if not job_id:
            # List active jobs
            worker = self._get_index_worker()
            return {"jobs": worker.get_active_jobs()}

        worker = self._get_index_worker()
        job = worker.get_job(job_id)
        if not job:
            return _error("NOT_FOUND", f"Job '{job_id}' not found")
        return job

    def _get_index_worker(self):
        """Lazy-init IndexWorker."""
        if self._index_worker is None:
            from ..services.index_worker import IndexWorker

            self._index_worker = IndexWorker(self.db, self.config)
        return self._index_worker

    def _kb_manage(self, args: dict[str, Any]) -> dict[str, Any]:
        """Manage knowledge bases."""
        action = args.get("action")

        if action == "discover":
            from ..config import auto_discover_kbs

            discovered = auto_discover_kbs()
            return {
                "discovered": len(discovered),
                "kbs": [{"name": kb.name, "path": str(kb.path)} for kb in discovered],
            }
        elif action == "validate":
            kb_name = args.get("kb_name")
            if not kb_name:
                return _error("MISSING_PARAMETER", "kb_name required for validate")
            kb_config = self.config.get_kb(kb_name)
            if not kb_config:
                return _error("NOT_FOUND", f"KB '{kb_name}' not found")
            schema = kb_config.kb_schema
            return {"valid": True, "types": list(schema.types.keys())}

        elif action in ("show_schema", "add_type", "remove_type", "set_schema"):
            return self._kb_manage_schema(action, args)

        return _error("OPERATION_FAILED", f"Unknown action: {action}")

    def _kb_manage_schema(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        """Handle schema management actions for _kb_manage."""
        from pyrite.services.schema_service import SchemaService

        kb_name = args.get("kb_name")
        svc = SchemaService(self.config)

        try:
            if action == "show_schema":
                if not kb_name:
                    return _error("MISSING_PARAMETER", "kb_name required for show_schema")
                return svc.show_schema(kb_name)

            elif action == "add_type":
                type_name = args.get("type_name")
                type_def = args.get("type_def", {})
                if not kb_name or not type_name:
                    return _error(
                        "MISSING_PARAMETER", "kb_name and type_name required for add_type"
                    )
                return svc.add_type(kb_name, type_name, type_def)

            elif action == "remove_type":
                type_name = args.get("type_name")
                if not kb_name or not type_name:
                    return _error(
                        "MISSING_PARAMETER", "kb_name and type_name required for remove_type"
                    )
                return svc.remove_type(kb_name, type_name)

            elif action == "set_schema":
                schema = args.get("schema", {})
                if not kb_name:
                    return _error("MISSING_PARAMETER", "kb_name required for set_schema")
                return svc.set_schema(kb_name, schema)

        except (PyriteError, ValueError) as e:
            return _error("OPERATION_FAILED", _safe_message(e))

        return _error("OPERATION_FAILED", f"Unknown schema action: {action}")

    def _kb_commit(self, args: dict[str, Any]) -> dict[str, Any]:
        """Commit changes in a KB's git repository."""
        kb_name = args.get("kb")
        message = args.get("message")
        paths = args.get("paths")
        sign_off = args.get("sign_off", False)

        if not kb_name or not message:
            return _error("MISSING_PARAMETER", "Both 'kb' and 'message' are required")

        try:
            return self.export_svc.commit_kb(
                kb_name, message=message, paths=paths, sign_off=sign_off
            )
        except PyriteError as e:
            return _error("OPERATION_FAILED", _safe_message(e))

    def _kb_push(self, args: dict[str, Any]) -> dict[str, Any]:
        """Push KB commits to a remote repository."""
        kb_name = args.get("kb")
        remote = args.get("remote", "origin")
        branch = args.get("branch")

        if not kb_name:
            return _error("MISSING_PARAMETER", "'kb' is required")

        try:
            return self.export_svc.push_kb(kb_name, remote=remote, branch=branch)
        except InvalidGitRefError as e:
            return _error("VALIDATION_ERROR", str(e))
        except PyriteError as e:
            return _error("OPERATION_FAILED", _safe_message(e))

    def _kb_registry_add(self, args: dict[str, Any]) -> dict[str, Any]:
        """Register a new user KB by path."""
        name = args.get("name")
        path = args.get("path")
        if not name or not path:
            return _error("MISSING_PARAM", "'name' and 'path' are required")
        try:
            result = self.registry.add_kb(
                name=name,
                path=path,
                kb_type=args.get("kb_type", "generic"),
                description=args.get("description", ""),
            )
            return {"created": True, **result}
        except (PyriteError, ValueError) as e:
            return _error("CONFLICT", _safe_message(e))

    def _kb_registry_remove(self, args: dict[str, Any]) -> dict[str, Any]:
        """Remove a user-added KB."""
        name = args.get("name")
        if not name:
            return _error("MISSING_PARAM", "'name' is required")
        try:
            self.registry.remove_kb(name)
            return {"deleted": True, "name": name}
        except KBNotFoundError as e:
            return _error("NOT_FOUND", str(e))
        except KBProtectedError as e:
            return _error("PROTECTED", str(e))

    def _kb_registry_reindex(self, args: dict[str, Any]) -> dict[str, Any]:
        """Reindex a specific KB."""
        name = args.get("name")
        if not name:
            return _error("MISSING_PARAM", "'name' is required")
        try:
            result = self.registry.reindex_kb(name)
            return {"reindexed": True, "name": name, **result}
        except KBNotFoundError as e:
            return _error("NOT_FOUND", str(e))

    def _kb_registry_health(self, args: dict[str, Any]) -> dict[str, Any]:
        """Check KB health."""
        name = args.get("name")
        if not name:
            return _error("MISSING_PARAM", "'name' is required")
        try:
            return self.registry.health_kb(name)
        except KBNotFoundError as e:
            return _error("NOT_FOUND", str(e))

    # =========================================================================
    # Prompts
    # =========================================================================

    def _build_prompts(self) -> dict[str, dict[str, Any]]:
        """Build prompt definitions available to MCP clients."""
        return {
            "research_topic": {
                "name": "research_topic",
                "description": "Research a topic across all knowledge bases, summarize findings, and identify gaps",
                "arguments": [
                    {"name": "topic", "description": "Topic to research", "required": True}
                ],
            },
            "summarize_entry": {
                "name": "summarize_entry",
                "description": "Get an entry and generate a concise summary",
                "arguments": [
                    {
                        "name": "entry_id",
                        "description": "Entry ID to summarize",
                        "required": True,
                    },
                    {
                        "name": "kb_name",
                        "description": "KB name (optional)",
                        "required": False,
                    },
                ],
            },
            "find_connections": {
                "name": "find_connections",
                "description": "Analyze connections between two entries",
                "arguments": [
                    {"name": "entry_a", "description": "First entry ID", "required": True},
                    {"name": "entry_b", "description": "Second entry ID", "required": True},
                    {
                        "name": "kb_a",
                        "description": "KB of the first entry (needed when its id is in "
                        "more than one KB)",
                        "required": False,
                    },
                    {
                        "name": "kb_b",
                        "description": "KB of the second entry (needed when its id is in "
                        "more than one KB)",
                        "required": False,
                    },
                ],
            },
            "daily_briefing": {
                "name": "daily_briefing",
                "description": "Generate a briefing from recent entries and timeline events",
                "arguments": [
                    {
                        "name": "days",
                        "description": "Number of days to look back (default 7)",
                        "required": False,
                    }
                ],
            },
        }

    def _get_prompt(
        self, name: str, arguments: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Dispatch to the appropriate prompt handler.

        A **third** KB-content chokepoint, alongside `_dispatch_tool` and
        `_read_resource`: `prompts/get` embeds whole entries -- bodies and
        all -- in the prompt text it returns. `summarize_entry`,
        `find_connections` and `daily_briefing` all read across every KB, so
        they are scoped here on the same per-connection readable set.

        (Not among #201's nine criteria, which name tools and resources.
        Found while wiring those two, and closed rather than left as a known
        hole in a security release.)
        """
        handlers = {
            "research_topic": self._prompt_research_topic,
            "summarize_entry": self._prompt_summarize_entry,
            "find_connections": self._prompt_find_connections,
            "daily_briefing": self._prompt_daily_briefing,
        }
        handler = handlers.get(name)
        if not handler:
            return _error("OPERATION_FAILED", f"Unknown prompt: {name}")
        for kb_name in _kbs_named_in(arguments):
            refusal = _kb_refusal(self.svc, kb_name, readable_kbs)
            if refusal is not None:
                return refusal
        import inspect

        try:
            if READABLE_KBS_KWARG in inspect.signature(handler).parameters:
                return handler(arguments, readable_kbs=readable_kbs)
            return handler(arguments)
        except PyriteError as e:
            # Same refusal-not-a-crash handling as _dispatch_tool (#445 cold
            # read): research_topic reads BrandingService as a side effect,
            # and an uncaught BrandingInvalidError previously propagated raw
            # out of this method with nothing to convert it to the MCP
            # envelope. A storage fault is the server's problem and is
            # logged once, with its traceback (#431); every other refusal
            # (including BrandingInvalidError) is the caller finding out the
            # service said no, not a crash.
            logged = isinstance(e, StorageError)
            if logged:
                logger.error("Prompt %s failed: %s", name, e, exc_info=e)
            return _refusal(e, already_logged=logged)

    def _prompt_research_topic(self, args: dict[str, Any]) -> dict[str, Any]:
        """Generate research prompt for a topic."""
        from ..services.branding_service import DEFAULT_BRAND_NAME, BrandingService

        brand = BrandingService(self.config.settings.branding_dir).get()
        brand_label = brand.mcp_agent_prompt_brand or brand.name
        topic = args.get("topic", "")
        if brand_label == DEFAULT_BRAND_NAME:
            intro = "You are a research assistant with access to Pyrite knowledge bases."
        else:
            intro = (
                f"You are a research assistant with access to {brand_label} knowledge "
                f"bases (powered by Pyrite)."
            )
        return {
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            f"{intro} "
                            f"Research the topic: {topic}. Search across all KBs, summarize key "
                            f"findings, identify related entries, and note any gaps in coverage."
                        ),
                    },
                }
            ]
        }

    def _prompt_summarize_entry(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None
    ) -> dict[str, Any]:
        """Fetch an entry and generate a summary prompt."""
        entry_id = args.get("entry_id", "")
        kb_name = args.get("kb_name")

        # The prompt embeds the whole entry, links included: both bounded by
        # the caller's readable set (P-R4, P-R5).
        entry = self.svc.get_entry(entry_id, kb_name=kb_name, readable_kbs=readable_kbs)
        if not entry:
            return {
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": f"Entry '{entry_id}' was not found. Please check the ID and try again.",
                        },
                    }
                ]
            }

        entry_text = json.dumps(entry, separators=(",", ":"), default=str)
        return {
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            f"Summarize the following entry concisely. Highlight the key facts, "
                            f"connections to other entries, and significance.\n\n{entry_text}"
                        ),
                    },
                }
            ]
        }

    def _prompt_find_connections(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None
    ) -> dict[str, Any]:
        """Fetch two entries and generate a connections analysis prompt."""
        entry_a_id = args.get("entry_a", "")
        entry_b_id = args.get("entry_b", "")

        # Without kb_a/kb_b each lookup spans the readable KBs only: an entry
        # elsewhere reads as "not found", exactly as a missing id does, and
        # the embedded links are bounded the same way. An id in more than one
        # readable KB raises AMBIGUOUS (#528), which _get_prompt returns as
        # the refusal; kb_a/kb_b are how the caller complies (both are in
        # KB_ARGUMENT_NAMES, so an unreadable one is refused before this).
        entry_a = self.svc.get_entry(
            entry_a_id, kb_name=args.get("kb_a"), readable_kbs=readable_kbs
        )
        entry_b = self.svc.get_entry(
            entry_b_id, kb_name=args.get("kb_b"), readable_kbs=readable_kbs
        )

        entry_a_text = (
            json.dumps(entry_a, separators=(",", ":"), default=str)
            if entry_a
            else f"Entry '{entry_a_id}' not found"
        )
        entry_b_text = (
            json.dumps(entry_b, separators=(",", ":"), default=str)
            if entry_b
            else f"Entry '{entry_b_id}' not found"
        )

        return {
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            f"Analyze the connections between these two entries. Identify shared "
                            f"themes, people, organizations, events, or other relationships.\n\n"
                            f"## Entry A\n{entry_a_text}\n\n## Entry B\n{entry_b_text}"
                        ),
                    },
                }
            ]
        }

    def _prompt_daily_briefing(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None
    ) -> dict[str, Any]:
        """Generate a briefing prompt from recent timeline events."""
        from datetime import datetime, timedelta

        days = int(args.get("days", 7))
        date_to = datetime.now().strftime("%Y-%m-%d")
        date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        events = self.svc.get_timeline(date_from=date_from, date_to=date_to, kb_names=readable_kbs)
        events = events[:MAX_TIMELINE_EVENTS]

        if events:
            events_text = json.dumps(events, separators=(",", ":"), default=str)
        else:
            events_text = "No timeline events found in this period."

        return {
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            f"Generate a briefing based on the following timeline events from "
                            f"the last {days} days ({date_from} to {date_to}). Summarize key "
                            f"developments, highlight the most important items, and note any "
                            f"emerging patterns.\n\n{events_text}"
                        ),
                    },
                }
            ]
        }

    # =========================================================================
    # Resources
    # =========================================================================

    def _build_resources(self) -> list[dict[str, Any]]:
        """Build static resource list."""
        return [
            {
                "uri": f"{URI_SCHEME}kbs",
                "name": "Knowledge Bases",
                "description": "List all knowledge bases",
                "mimeType": "application/json",
            },
        ]

    def _build_resource_templates(self) -> list[dict[str, Any]]:
        """Build resource URI templates."""
        return [
            {
                "uriTemplate": f"{URI_SCHEME}kbs/{{name}}/entries",
                "name": "KB Entries",
                "description": "List entries in a knowledge base",
                "mimeType": "application/json",
            },
            {
                "uriTemplate": f"{URI_SCHEME}entries/{{id}}",
                "name": "Entry",
                "description": "Get a specific entry (refused as AMBIGUOUS when the id "
                "is in more than one KB: use the KB-qualified template)",
                "mimeType": "application/json",
            },
            {
                "uriTemplate": f"{URI_SCHEME}kbs/{{name}}/entries/{{id}}",
                "name": "Entry in a KB",
                "description": "Get a specific entry from a named knowledge base",
                "mimeType": "application/json",
            },
        ]

    def _read_resource(self, uri: str, *, readable_kbs: set[str] | None = None) -> dict[str, Any]:
        """Read a resource by URI and return contents.

        The **second** KB-content chokepoint: resources do not pass through
        `_dispatch_tool`, so scoping them is a separate act. Reached from the
        same per-connection closure in `build_sdk_server`, so it gets the
        same readable set.

        Fixed by #217: `build_sdk_server`'s `@sdk.read_resource()` closure now
        adapts this method's return value into
        `Iterable[mcp.server.lowlevel.helper_types.ReadResourceContents]`, the
        shape the installed SDK's `read_resource` decorator expects, so
        `resources/read` serves content over a real session
        (`tests/test_mcp_resources_session.py`). Scoping was added ahead of
        that fix so it did not ship unscoped the moment #217 landed (#201,
        criterion 6).
        """
        if uri == f"{URI_SCHEME}kbs":
            kbs_data = self.svc.list_kbs()
            if readable_kbs is not None:
                kbs_data = [k for k in kbs_data if k.get("name") in readable_kbs]
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(kbs_data, separators=(",", ":"), default=str),
                    }
                ]
            }

        # pyrite://kbs/{name}/entries
        if (
            uri.startswith(f"{URI_SCHEME}kbs/")
            and uri.endswith("/entries")
            and "/entries/" not in uri
        ):
            kb_name = uri[len(f"{URI_SCHEME}kbs/") : -len("/entries")]
            refusal = _kb_refusal(self.svc, kb_name, readable_kbs)
            if refusal is not None:
                return refusal
            entries = self.svc.list_entries(kb_name=kb_name, limit=MAX_RESOURCE_LIST_ENTRIES)
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(entries, separators=(",", ":"), default=str),
                    }
                ]
            }

        # pyrite://entries/{id} and pyrite://kbs/{name}/entries/{id}
        entry_id, entry_kb = None, None
        if uri.startswith(f"{URI_SCHEME}entries/"):
            entry_id = uri[len(f"{URI_SCHEME}entries/") :]
        elif uri.startswith(f"{URI_SCHEME}kbs/") and "/entries/" in uri:
            entry_kb, _, entry_id = uri[len(f"{URI_SCHEME}kbs/") :].partition("/entries/")
            # A KB the caller cannot read and a KB that does not exist answer
            # the same KB_NOT_FOUND (scoped or not), never a plain NOT_FOUND.
            refusal = _kb_refusal(self.svc, entry_kb, readable_kbs)
            if refusal is not None:
                return refusal
        if entry_id is not None:
            # Without a KB the lookup is bounded by the caller's readable set:
            # an entry in a private KB is a plain miss (naming the KB would
            # reveal which one holds it) and never hides or doubles a readable
            # entry with the same id; its links are bounded too. An id in more
            # than one readable KB is refused (AMBIGUOUS, #528) in the tool
            # envelope; the KB-qualified URI is how the caller complies.
            try:
                entry = self.svc.get_entry(entry_id, kb_name=entry_kb, readable_kbs=readable_kbs)
            except PyriteError as e:
                if isinstance(e, StorageError):
                    logger.error("Resource %s failed: %s", uri, e, exc_info=e)
                return _refusal(e)
            if not entry:
                return _error("NOT_FOUND", f"Entry '{entry_id}' not found")
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(entry, separators=(",", ":"), default=str),
                    }
                ]
            }

        return _error("OPERATION_FAILED", f"Unknown resource URI: {uri}")

    # =========================================================================
    # MCP Protocol
    # =========================================================================

    def _handler_takes_readable_kbs(self, name: str) -> bool:
        """Does this tool's handler opt in to receiving the readable set?

        Introspected once per tool and cached: a handler that spans KBs
        declares `readable_kbs` and gets it; every other handler -- including
        all plugin handlers -- is called with `args` alone, unchanged. Opting
        in by signature rather than by a name list keeps the two from
        drifting, and injecting a reserved key into `arguments` was rejected
        for the same reason: a plugin handler that iterates its args would
        see it.
        """
        cache = self.__dict__.setdefault("_scope_aware_handlers", {})
        if name not in cache:
            import inspect

            try:
                params = inspect.signature(self.tools[name]["handler"]).parameters
                cache[name] = READABLE_KBS_KWARG in params
            except (TypeError, ValueError):  # pragma: no cover - exotic callables
                cache[name] = False
        return cache[name]

    def get_tools_list(self) -> list[dict[str, Any]]:
        """Return list of available tools in MCP format."""
        return [
            {"name": name, "description": meta["description"], "inputSchema": meta["inputSchema"]}
            for name, meta in self.tools.items()
        ]

    def _refuse_truncated_write(self, name: str, arguments: dict[str, Any]) -> dict | None:
        """ADR-0034 rule 2, applied to one MCP call to a non-pipeline write tool.

        Returns the error envelope when this call is a write carrying both a
        body and a truthy `body_truncated`, otherwise None. Reads are never
        guarded: `body_truncated` is a marker a read *produces*, and a read
        that echoes it back as an argument loses nothing. Tools whose handlers
        go through the KBService write pipeline are left to it, so the
        decision for them is made once (#378).
        """
        if self._tool_tiers.get(name, "read") == "read":
            return None
        if name in _PIPELINE_WRITE_TOOLS:
            return None
        try:
            ensure_not_truncated(arguments)
        except ValidationError as e:
            return _refusal(e)
        return None

    def _landing_target(
        self, tool: str, named_kbs: list[str], principal: Principal | None
    ) -> tuple[tuple | None, dict | None]:
        """Where a write tool's call lands: `(target, None)` -- the target to
        bind, None for canonical -- or `(None, refusal)`.

        `principal` None is the local operator (stdio). One call lands in one
        place: a call naming several KBs, one of which lands in a change set,
        is refused. A change set that cannot be made is refused; nothing
        falls back to the canonical files (ADR-0044 §1, L2).
        """
        from ..services.access_policy import Landing
        from ..services.write_target import LandingRefusedError, write_target

        who = principal if principal is not None else Principal.local()
        landings = {kb: self._svc.lands_in(who, kb) for kb in named_kbs}
        if any(landing is Landing.DENY for landing in landings.values()):
            return None, _refusal(LandingRefusedError("This write has no place to land"))
        moved = sorted(kb for kb, landing in landings.items() if landing is Landing.CHANGE_SET)
        if not moved:
            return None, None
        if len(landings) > 1:
            return None, _refusal(
                LandingRefusedError(
                    "This write names several knowledge bases and one of them lands in a "
                    "change set; write to one knowledge base at a time"
                )
            )
        if tool in _CHANGE_SET_UNSUPPORTED_TOOLS:
            from ..exceptions import ChangeSetRecordRefusedError

            return None, _refusal(
                ChangeSetRecordRefusedError(
                    f"{tool} {_CHANGE_SET_UNSUPPORTED_TOOLS[tool]}, which a change set "
                    f"cannot carry through its merge, and KB '{moved[0]}' lands your writes "
                    "in a change set. It is available to the operator, or after the change "
                    "set merges."
                )
            )
        try:
            cache = self.__dict__.setdefault("_diff_cache", {})
            return write_target(self._config, self._db, who, moved[0], diff_cache=cache), None
        except LandingRefusedError as e:
            return None, _refusal(e)

    def _locate_entry_ids(
        self, spec: dict[str, Any], arguments: dict[str, Any], readable_kbs: set[str] | None
    ) -> dict[str, Any]:
        """``arguments`` with ``kb_name`` set to the KB that holds the entry ids.

        The plugin tools' door to ``KBService.locate`` (#1310, the MCP half of
        #911), the rule the CLI's ``require_entry_kb`` applies: a ``kb_name``
        absent or blank names no KB, and the id is then found in the one
        readable KB that holds it, refused ``AMBIGUOUS`` (with
        ``candidate_kbs``) when several do, ``ENTRY_NOT_FOUND`` when none does.
        A named KB must hold the id. ``spec`` is the tool's ``locates_entry``:
        ``id_args`` (the arguments that carry entry ids, each a string or a
        list of strings) and ``entry_type`` (the one type the tool acts on, or
        absent for a tool that takes any). Every id must be in the one KB.
        Raises ``PyriteError``; the caller renders the refusal.
        """
        ids = _located_ids(spec, arguments)
        if not ids:
            # `_dispatch_tool` refuses this before any check; kept here so no
            # caller of this method hands a handler an id it did not resolve.
            raise ValidationError(_no_id_message(spec))
        kb = named_kb(arguments.get("kb_name"))
        for entry_id in ids:
            found = self.svc.locate(
                entry_id, kb, readable_kbs=readable_kbs, entry_type=spec.get("entry_type")
            )
            kb = kb or found
        return {**arguments, "kb_name": kb}

    def _dispatch_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        client_id: str = "local",
        client_kind: str | None = None,
        readable_kbs: set[str] | None = None,
        writable_kbs: set[str] | None = None,
        principal: Principal | None = None,
    ) -> dict[str, Any]:
        """Execute a tool and return result.

        `client_kind` is the caller's principal kind (`Principal.kind`) and
        `client_id` its id within that kind; the rate-limit bucket is the
        pair, so no name a user registers shares an operator key's bucket.
        Only the `local` kind -- stdio, chosen by `run_stdio` on purpose --
        may be exempt (`mcp_rate_limit_exempt_local`, P-L1); `client_id` is
        never consulted for that.

        `readable_kbs` is the caller's readable set, or None for an unscoped
        caller (a global admin, an operator API key, auth disabled, or local
        stdio). It arrives per call, from the per-connection closure
        `build_sdk_server` creates -- never from state on this instance,
        which is shared by every caller at this tier.

        This is the one runtime chokepoint every tool passes through, so it
        is where scoping is enforced: refuse any KB the call names that the
        caller may not read, then hand the set to the cross-KB handlers that
        declare they take it.

        `writable_kbs` is the caller's writable set (`api.kbs_for_user_at_tier`
        at "write" -- REST's per-KB write rule). It applies only when the
        caller is scoped (`readable_kbs` is not None); a scoped caller with
        no writable set resolved is treated as able to write nowhere. A tool
        registered above the read tier runs only when every KB it names is
        writable, and never when it names none.

        `principal` is who is calling, for the landing rule
        (`AccessPolicy.lands_in`, ADR-0044 §1): a write tool runs only when
        every KB it names lands canonical for this principal. None is the
        local operator (stdio). A write that lands in a change set runs with
        that target bound for this call (`services.write_target.binding`).
        """
        if name not in self.tools:
            return _error(
                "UNKNOWN_TOOL",
                f"Unknown tool: {name}",
                suggestion="Use list_tools to see available tools",
            )

        # Rate limiting (skip for the local principal kind when configured)
        if not (client_kind == "local" and self.config.settings.mcp_rate_limit_exempt_local):
            tool_tier = self._tool_tiers.get(name, "read")
            allowed, info = self.rate_limiter.check(f"{client_kind}:{client_id}", tool_tier)
            if not allowed:
                return _error(
                    "RATE_LIMITED",
                    f"Rate limit exceeded for {tool_tier} tier. Retry after {info['retry_after']}s.",
                    suggestion=f"Wait {info['retry_after']} seconds",
                    retryable=True,
                )

        # A tool that takes an entry id declares `locates_entry` (#1310). The
        # KB its handler works in is `kb_name`, so with `kb_name` not named
        # the id is resolved to the KB that holds it HERE, before `named_kbs`
        # is computed: the found KB is then a named KB like any other, and
        # every check below (read, write, landing) runs on it by
        # construction. Keyed on `kb_name` alone, never on `named_kbs`: a KB
        # named under another argument does not choose the handler's KB, so
        # it must not skip the lookup either. With `kb_name` named, the checks
        # run first (a caller who may not write there is told so, not "not
        # found") and the id is verified in that KB just before the handler.
        #
        # No handler is called with an id the dispatcher has not resolved: a
        # call whose id arguments are all blank (missing, empty, whitespace)
        # is refused here, before any check, whatever KB it names -- else the
        # handler would choose a KB itself (a default KB, or '').
        spec = self.tools[name].get("locates_entry")
        if spec and not _located_ids(spec, arguments):
            return _refusal(ValidationError(_no_id_message(spec)))
        kb_name_named = named_kb(arguments.get("kb_name")) is not None
        if spec and not kb_name_named:
            try:
                arguments = self._locate_entry_ids(spec, arguments, readable_kbs)
            except PyriteError as e:
                return _refusal(e)

        # Every KB the call names goes through the one resolver (#952),
        # for every caller: an unknown name, a KB outside a scoped caller's
        # readable set and a shortname are refused before the handler runs,
        # and for EVERY name -- naming a readable KB alongside a private one
        # must buy nothing.
        named_kbs = _kbs_named_in(arguments)
        for kb_name in named_kbs:
            refusal = _kb_refusal(self.svc, kb_name, readable_kbs)
            if refusal is not None:
                # The one refusal, with the code the old algorithm gave this
                # caller: an unscoped kb_update reached its handler, which
                # answered a missing KB with UPDATE_FAILED (retryable: true)
                # before #761 typed it; a scoped caller was refused here, with
                # NOT_FOUND, as every other tool still is. kb_delete answered
                # NOT_FOUND here for every caller (#984), so its legacy code is
                # the resolver's, not the handler's DELETE_FAILED.
                if name == "kb_update" and readable_kbs is None:
                    refusal["legacy_error_code"] = "UPDATE_FAILED"
                return refusal

        takes_readable = readable_kbs is not None and self._handler_takes_readable_kbs(name)

        # Fail closed. A scoped caller reaching a tool that names no KB, does
        # not filter, and is not declared content-free would be served across
        # every KB in the index -- which is exactly the leak this work closes.
        # Today this is the journalism-investigation plugin's two cross-KB
        # tools, whose handlers live outside this file's footprint; refusing
        # is the honest answer until they take a readable set (#201: the
        # alternative, letting them through, is the bug).
        if (
            readable_kbs is not None
            and not named_kbs
            and not takes_readable
            and name not in NON_KB_CONTENT_TOOLS
        ):
            return _error(
                "NOT_FOUND",
                f"Tool '{name}' is not available",
                suggestion="Name a knowledge base you can read, or use kb_search",
            )

        # Per-KB write check: the REST `requires_kb_tier("write")` rule, over
        # the set resolved from the same helper. Keyed off the tool's
        # registered tier, so every write tool -- core or plugin -- is covered
        # by construction rather than by enumeration. After the fail-closed
        # read check above, so a scoped call naming no KB keeps that refusal.
        if readable_kbs is not None and self._tool_tiers.get(name, "read") != "read":
            writable = writable_kbs or set()
            if not named_kbs:
                return _error(
                    "FORBIDDEN",
                    f"Tool '{name}' writes to a knowledge base; name one you can write",
                )
            for kb_name in named_kbs:
                if kb_name not in writable:
                    return _error(
                        "FORBIDDEN",
                        f"Insufficient permissions on KB '{kb_name}': requires 'write' tier",
                    )

        # Where the write lands: asked of the one rule, for every write tool --
        # core or plugin -- by its registered tier. A change-set landing is
        # bound for this call only (`binding`, below): the handler's
        # `self.svc`/`self.db`/`self.config` and the plugin context read it.
        target = None
        if self._tool_tiers.get(name, "read") != "read" and named_kbs:
            target, refusal = self._landing_target(name, named_kbs, principal)
            if refusal is not None:
                return refusal

        # ADR-0034 rule 2: a truncated body is never valid input to a write.
        # kb_create, kb_update and kb_bulk_create get it from KBService's
        # write pipeline. For every other write tool -- task_create and any
        # plugin tool registered into the write or admin tier -- the check
        # sits here, so it covers them by construction rather than by
        # enumeration.
        refusal = self._refuse_truncated_write(name, arguments)
        if refusal is not None:
            return refusal

        try:
            handler = self.tools[name]["handler"]
            with binding(target):
                if spec and kb_name_named:
                    # Verifies each id in the `kb_name` the caller named (the
                    # checks above ran on it); raises the refusal.
                    arguments = self._locate_entry_ids(spec, arguments, readable_kbs)
                if takes_readable:
                    # A cross-KB handler: nothing was named to refuse, so it
                    # must filter instead. Only handlers that declare the
                    # keyword get it, so plugin handlers are called as before.
                    return handler(arguments, readable_kbs=readable_kbs)
                return handler(arguments)
        except PyriteError as e:
            # A refused request, not a crash: the service said no for a reason
            # the caller can act on, and it is not logged as an exception --
            # except a storage fault, which is the server's problem and is
            # logged once, with its traceback (#431). See _refusal for which
            # refusals are retryable.
            logged = isinstance(e, StorageError)
            if logged:
                logger.error("Tool %s failed: %s", name, e, exc_info=e)
            return _refusal(e, already_logged=logged)
        except Exception as e:
            logger.exception("Tool %s failed with args %s", name, arguments)
            return _error("INTERNAL", str(e), retryable=True)

    def build_sdk_server(
        self,
        *,
        client_id: str = "stdio",
        client_kind: str | None = None,
        readable_kbs: set[str] | None = None,
        writable_kbs: set[str] | None = None,
        principal: Principal | None = None,
    ):
        """Build an mcp.server.Server wired to this instance's business logic.

        Called **per connection**, and it constructs a fresh
        `mcp.server.Server` every time, registering closures over the
        caller's identity. That is the seam per-KB scoping rides: this
        `PyriteMCPServer` is cached per *tier* and shared by every caller at
        that tier, so nothing caller-specific may be stored on it -- but the
        closures below are per connection, which is where `client_id`
        already lives. The per-tier cache therefore needs no change at all
        (#201); its key stays `tier`.

        Parameters
        ----------
        client_id : str
            The connected principal's id within its kind, for rate limiting.
            SSE transport passes the user's id or the operator key's hash.
        client_kind : str | None
            The principal's kind (`Principal.kind`). Only "local" -- which
            `run_stdio()` passes -- can be exempt from rate limits; the
            default, None, never is (P-L1).
        readable_kbs : set[str] | None
            The KBs this connection's caller may read, as resolved by
            `api.readable_kbs_for_user`. None means unscoped -- a global
            admin, an operator API key, auth disabled -- and is the default,
            so `run_stdio()` (local CLI, one user, their own machine) is
            unchanged.
        writable_kbs : set[str] | None
            The KBs a write-tier tool may target for this caller, as resolved
            by `api.kbs_for_user_at_tier(..., "write")`. Consulted only when
            `readable_kbs` is not None; a scoped connection without it can
            write nowhere.
        """
        from mcp.server import Server
        from mcp.server.lowlevel.helper_types import ReadResourceContents
        from mcp.types import (
            GetPromptResult,
            Prompt,
            PromptArgument,
            PromptMessage,
            Resource,
            ResourceTemplate,
            TextContent,
            Tool,
        )

        sdk = Server(f"pyrite-{self.tier}")

        mcp_server = self  # capture for closures
        _client_id = client_id  # capture for closures
        _client_kind = client_kind
        _readable_kbs = readable_kbs  # capture for closures -- per connection, never on self
        _writable_kbs = writable_kbs
        _principal = principal

        @sdk.list_tools()
        async def _list_tools():
            return [
                Tool(
                    name=name,
                    description=meta["description"],
                    inputSchema=meta["inputSchema"],
                )
                for name, meta in mcp_server.tools.items()
            ]

        @sdk.call_tool()
        async def _call_tool(name: str, arguments: dict):
            result = mcp_server._dispatch_tool(
                name,
                arguments or {},
                client_id=_client_id,
                client_kind=_client_kind,
                readable_kbs=_readable_kbs,
                writable_kbs=_writable_kbs,
                principal=_principal,
            )
            return [
                TextContent(
                    type="text", text=json.dumps(result, separators=(",", ":"), default=str)
                )
            ]

        @sdk.list_prompts()
        async def _list_prompts():
            return [
                Prompt(
                    name=p["name"],
                    description=p.get("description"),
                    arguments=[
                        PromptArgument(
                            name=a["name"],
                            description=a.get("description"),
                            required=a.get("required", False),
                        )
                        for a in p.get("arguments", [])
                    ],
                )
                for p in mcp_server.prompts.values()
            ]

        @sdk.get_prompt()
        async def _get_prompt(name: str, arguments: dict[str, str] | None):
            if name not in mcp_server.prompts:
                raise ValueError(f"Unknown prompt: {name}")
            result = mcp_server._get_prompt(name, arguments or {}, readable_kbs=_readable_kbs)
            if "error" in result:
                raise _protocol_refusal(result)
            return GetPromptResult(
                messages=[
                    PromptMessage(
                        role=m["role"],
                        content=TextContent(type="text", text=m["content"]["text"]),
                    )
                    for m in result["messages"]
                ]
            )

        @sdk.list_resources()
        async def _list_resources():
            return [
                Resource(
                    uri=AnyUrl(r["uri"]),
                    name=r["name"],
                    description=r.get("description"),
                    mimeType=r.get("mimeType"),
                )
                for r in mcp_server.resources
            ]

        @sdk.list_resource_templates()
        async def _list_resource_templates():
            return [
                ResourceTemplate(
                    uriTemplate=t["uriTemplate"],
                    name=t["name"],
                    description=t.get("description"),
                    mimeType=t.get("mimeType"),
                )
                for t in mcp_server.resource_templates
            ]

        @sdk.read_resource()
        async def _read_resource(uri: AnyUrl):
            result = mcp_server._read_resource(str(uri), readable_kbs=_readable_kbs)
            if "error" in result:
                raise _protocol_refusal(result)
            return [
                ReadResourceContents(content=c["text"], mime_type=c.get("mimeType"))
                for c in result["contents"]
            ]

        return sdk

    def run_stdio(self):
        """Run the MCP server over stdio using the official MCP SDK."""
        import anyio
        from mcp.server.stdio import stdio_server

        sdk = self.build_sdk_server(client_id="stdio", client_kind="local")

        async def _run():
            async with stdio_server() as (read_stream, write_stream):
                await sdk.run(read_stream, write_stream, sdk.create_initialization_options())

        anyio.run(_run)

    def close(self):
        """Clean up resources."""
        self.db.close()


def main():
    """Entry point for MCP server. Supports --tier flag."""
    import argparse

    parser = argparse.ArgumentParser(prog="pyrite-server", description="Pyrite MCP Server")
    parser.add_argument(
        "--tier",
        choices=list(ROLES),
        # write, as `pyrite mcp` and `pyrite-admin mcp` (ADR-0006; #582).
        default="write",
        help="Access tier (default: write)",
    )
    args = parser.parse_args()

    server = PyriteMCPServer(tier=args.tier)
    try:
        server.run_stdio()
    finally:
        server.close()


if __name__ == "__main__":
    main()

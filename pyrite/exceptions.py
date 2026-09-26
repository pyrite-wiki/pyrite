"""
Pyrite Exception Hierarchy

Typed exceptions for distinct error conditions, replacing generic ValueError/PermissionError.

ADR-0037 theme 2: **codes live on exception classes.** Every ``PyriteError``
subclass carries a class-level ``error_code`` -- a plain string, inherited
from its parent unless it narrows the code itself -- so a transport maps a
code from the class alone, with no external table keyed by type to keep in
sync. Where REST and MCP used to disagree (``NOT_FOUND`` vs
``ENTRY_NOT_FOUND``/``KB_NOT_FOUND``, ``READ_ONLY`` vs ``KB_READ_ONLY``,
``CONFIG_ERROR`` vs ``CONFIG_CONFLICT``), the maintainer's decision
(2026-09-25) is that REST's more specific code is the one on the class; MCP
keeps emitting its old code for one release in a transitional
``legacy_error_code`` field it builds itself (see
``server/mcp_server._refusal``), never on the class.

**The base ``ValidationError`` is the one exception, by conductor decision
(fix round 1, cold read of #501):** REST itself used two different spellings
for the same class before this theme -- the central handler's table said
``VALIDATION_ERROR``, but the write pipeline (`server/endpoints/write_refusal.py`,
`services/kb_service.py`'s bulk per-item results) has always answered
``VALIDATION_FAILED``, and that second spelling is the one
`docs/json-contracts.md` ("Write refusals"), `docs/agent-write-path.md` and
#378 document as the agent-facing contract. Applying "REST's more specific
code wins" here means the *documented, write-pipeline* code wins over the
central handler's less-visited one, not the reverse -- so the base code is
``VALIDATION_FAILED``, and it is the central handler's own answer for a bare
``ValidationError`` that changes (previously ``VALIDATION_ERROR``, an
under-used path few things exercise directly). MCP already said
``VALIDATION_FAILED`` too, so this is the one base class where REST, MCP and
the CLI all already agreed, and continue to agree -- no ``legacy_error_code``
for it.

**Messages are public or private by construction.** ``public_message`` is
``None`` by default -- every transport's
``exc.public_message or str(exc)`` then falls back to ``str(exc)``, which
every refusal family here whose message is *always* written to be safe to
show (not-found, read-only, the ``ValidationError`` family, query errors)
relies on. **A class *can* carry server-side detail in ``str(exc)`` at some
raise site sets a fixed, safe ``public_message`` on the whole class instead**
-- not per raise site, since a class cannot tell which site built a given
instance and a future raise site is not guaranteed to stay safe either:
``ConfigSaveRefusedError``/``ConfigFileUnreadableError`` (#377),
``BrandingInvalidError`` (#445, #408), and, since ADR-0037 theme 2 fix round
1 (conductor cold read of #501), the three base classes whose raise sites
are a genuine mix of safe and unsafe messages: ``StorageError``,
``PluginError`` and ``ConfigError`` (a database driver's text, a plugin's
traceback fragment, a confined-root path -- alongside sites that already
phrase themselves safely, which is exactly the trap: a class-level
``public_message=None`` looked correct until the next raise site embedded a
path).
"""


class PyriteError(Exception):
    """Base exception for all Pyrite errors.

    ``error_code`` is the fallback for any domain error that reaches a
    transport without a more specific class -- REST's central handler and
    MCP's ``_refusal`` both map an unrecognised ``PyriteError`` to
    ``INTERNAL_ERROR`` today; this makes that the same string as a class
    attribute instead of a magic literal duplicated at each site.
    """

    error_code: str = "INTERNAL_ERROR"
    #: Safe to show over HTTP/MCP/CLI. ``None`` means "str(exc) is safe" --
    #: see the module docstring. A subclass whose own text can carry
    #: server-side detail (a path, a traceback fragment) sets a fixed string.
    public_message: str | None = None


class EntryNotFoundError(PyriteError):
    """Raised when an entry cannot be found."""

    error_code = "ENTRY_NOT_FOUND"


class KBNotFoundError(PyriteError):
    """Raised when a knowledge base cannot be found."""

    error_code = "KB_NOT_FOUND"


class KBReadOnlyError(PyriteError):
    """Raised when attempting to write to a read-only KB."""

    error_code = "KB_READ_ONLY"


class ValidationError(PyriteError):
    """Raised when entry data fails validation.

    Every write refusal is a ValidationError, and carries a stable
    ``error_code`` that REST, MCP and the CLI all report unchanged (#378).
    Subclasses below narrow the code; the base code is ``VALIDATION_FAILED``
    -- the write pipeline's long-documented spelling
    (``docs/json-contracts.md``, ``docs/agent-write-path.md``), which REST's
    write routes, MCP and the CLI all already agreed on before this theme.
    The central handler's table used to say ``VALIDATION_ERROR`` for this
    one case (an under-visited path: a bare ``ValidationError`` reaching the
    handler unmapped, rather than through the write pipeline); that
    disagreement is resolved in favour of the documented code, not the
    handler's, so no transport's ``legacy_error_code`` needs to carry
    ``VALIDATION_FAILED`` -- it was never legacy.
    ``suggestion`` is an optional surface-neutral fix hint.
    """

    error_code = "VALIDATION_FAILED"
    suggestion: str | None = None


class UndeclaredTypeError(ValidationError):
    """A create named a type the KB's kb.yaml does not declare (#197).

    Raised only when the KB declares types at all. Core types are not
    exempt: a KB that declares a schema declares its vocabulary.
    """

    error_code = "UNDECLARED_TYPE"

    def __init__(self, message: str, declared_types: list[str]):
        super().__init__(message)
        self.declared_types = declared_types


class EntryExistsError(ValidationError):
    """A create resolved to an id that already exists. Create never replaces."""

    error_code = "ENTRY_EXISTS"


class SchemaViolationError(ValidationError):
    """The KB schema or a plugin validator rejected the entry (enum, required, range...)."""

    error_code = "SCHEMA_VIOLATION"

    def __init__(self, message: str, errors: list[dict] | None = None):
        super().__init__(message)
        self.errors = errors or []


class TruncatedBodyError(ValidationError):
    """ADR-0034 rule 2: a write carried a body marked ``body_truncated``.

    Keeps the base ``VALIDATION_FAILED`` code, which docs/json-contracts.md
    documents for this refusal.
    """

    def __init__(self, message: str, suggestion: str | None = None):
        super().__init__(message)
        self.suggestion = suggestion


class InvalidGitRefError(ValidationError):
    """A caller-supplied git remote, branch or commit id is not acceptable.

    A remote must be one of the repository's configured remotes (never a URL
    or a path); a branch must be a valid branch name that does not begin
    with "-"; a commit id must be 4-64 hex characters (checked before git
    runs) and must name a commit, not a tree or blob (an annotated tag peels
    to its commit). Carries ``error_code`` ``INVALID_REF``.
    """

    error_code = "INVALID_REF"


class FrontmatterError(ValidationError):
    """Raised when YAML frontmatter is malformed or not a mapping.

    A ValidationError subclass so existing ``except ValidationError`` handlers
    continue to catch it, while callers that care specifically about parse
    failures can catch this narrower type. Its own code, not the base
    ``VALIDATION_FAILED``: REST has always answered 422 ``INVALID_FRONTMATTER``
    for this specific case.
    """

    error_code = "INVALID_FRONTMATTER"


class PluginError(PyriteError):
    """Raised when a plugin operation fails.

    ``str()`` can carry server-side detail depending on the raise site --
    a plugin's import traceback fragment, a real filesystem path to the
    failing plugin module (``plugins/registry.py``), a driver's own error
    text (``llm_service.py``). A class cannot tell which raise site built
    it, so ADR-0037 §3 gives the whole class a fixed, safe
    ``public_message`` rather than trusting every existing and future raise
    site to phrase itself safely; the real detail still reaches the server
    log (5xx: with a traceback; sub-500: a plain ``logger.warning`` line --
    see ``server/errors.py``/``mcp_server._refusal``).
    """

    error_code = "PLUGIN_ERROR"
    public_message = (
        "A plugin operation failed. An administrator needs to check the server "
        "log for which plugin and why."
    )


class DroppedHookRefusedError(PluginError):
    """`hook_runner.py` refused a write because a plugin's `before_*` hook
    for this KB type was dropped at registration for not matching the
    `(entry, ctx)` contract (#506, narrowing #501's fixed
    `PluginError.public_message`).

    The message names only the hook name and the KB type -- both
    caller/plugin-registration values, never server-side detail (no
    traceback fragment, no real path) -- so it is safe by construction,
    unlike the base `PluginError`, which also covers raise sites that do
    carry unsafe detail.
    """

    #: Overrides the base PluginError's fixed sentence -- this class's own
    #: message is always safe, so str(exc) should reach every transport.
    public_message = None


class MissingOptionalDependencyError(PluginError):
    """A provider client needed an optional dependency that is not
    installed (`llm_service.py`'s Anthropic/OpenAI SDK checks, #506,
    narrowing #501's fixed `PluginError.public_message`).

    The message is a fixed, safe `pip install` hint naming the package --
    never server-side detail -- so it is safe by construction.
    """

    #: Overrides the base PluginError's fixed sentence -- this class's own
    #: message is always safe, so str(exc) should reach every transport.
    public_message = None


class StorageError(PyriteError):
    """Raised when a storage operation fails.

    ``retryable`` says whether the same call could succeed if made again. It is
    False here: schema drift, a missing table and a corrupt file fail the same
    way every time. Only ``StorageBusyError`` sets it.

    ``str()`` can carry server-side detail depending on the raise site -- a
    database driver's own error text, a real filesystem path
    (``kb_service.py``'s reindex-after-rename messages, ``worktree_service.py``).
    Same reasoning as ``PluginError`` above: ADR-0037 §3 gives the whole class
    a fixed, safe ``public_message``; the real detail still reaches the
    server log.
    """

    error_code = "STORAGE_ERROR"
    public_message = (
        "A storage operation failed. An administrator needs to check the server "
        "log for the underlying error."
    )
    retryable: bool = False


class IndexSyncRecoveryHintError(StorageError):
    """A rename's file operation on disk succeeded, but the index sync that
    should follow it failed or left the new id unresolved (`kb_service.py`'s
    `rename_entry`, #506, narrowing #501's fixed `StorageError.public_message`).

    The message names only the two entry ids -- never a real filesystem path
    or driver detail -- so it is safe by construction; the recovery hint
    ("Run `pyrite index sync`") is real and actionable, unlike the base
    `StorageError`'s generic "check the server log" (written for raise sites
    that genuinely can't say more). The caught exception that triggered this
    (an OSError naming a real path, a driver's own error text -- NOT
    guaranteed safe) is deliberately never interpolated into the message; it
    is logged instead (#509 round 1 cold read caught an earlier version that
    did interpolate it).
    """

    #: Overrides the base StorageError's fixed sentence -- this class's own
    #: message is always safe, so str(exc) should reach every transport.
    public_message = None


class StorageBusyError(StorageError):
    """A transient storage fault: the database was locked or busy (#431).

    The one ``StorageError`` a caller may retry: another connection held a
    lock, and the same call can succeed once it is released. REST still maps
    it to ``STORAGE_ERROR`` 500; MCP reports it with ``retryable: true``.
    """

    retryable = True


class KBProtectedError(PyriteError):
    """Raised when attempting to modify/remove a config-protected KB."""

    error_code = "KB_PROTECTED"


class ConfigError(PyriteError):
    """Raised when configuration is invalid.

    ``str()`` can carry server-side detail depending on the raise site -- a
    real filesystem path (``config.py::refuse_outside_tree``'s confined-root
    check), the raw name/value of an environment variable
    (``body_bounds.py``, already safe, but not every future raise site is
    guaranteed to stay that way). Same reasoning as ``PluginError`` and
    ``StorageError`` above: ADR-0037 §3 gives the whole class a fixed, safe
    ``public_message``; the real detail still reaches the server log.
    Subclasses that already set their own (``ConfigSaveRefusedError`` #377,
    ``ConfigFileUnreadableError``) are unaffected -- their own class
    attribute wins over this base default.
    """

    error_code = "CONFIG_CONFLICT"
    public_message = (
        "The configuration is invalid. An administrator needs to check the "
        "server log for the file and the problem."
    )


class BrandingInvalidError(PyriteError):
    """Raised when ``branding.yaml`` exists but cannot be parsed, or it or
    one of its nested mappings (``meta``, ``mcp``) is not a mapping (#408).

    ``str()`` names the real branding.yaml path and the parser's own text --
    useful in the server log, not safe to return over HTTP or MCP: every
    caller of ``BrandingService`` runs on an anonymous, always-public path
    (``/config/branding``, ``/sitemap.xml``, ``/robots.txt``, the MCP
    ``research_topic`` prompt), not just the admin-only render endpoint
    (#445's cold read). ``public_message`` is safe to show; it names neither.
    """

    error_code = "BRANDING_INVALID"
    public_message = (
        "The server's branding configuration is invalid and could not be loaded. "
        "An administrator needs to fix branding.yaml; the server log names the "
        "file and the problem."
    )


class KBAlreadyExistsError(ConfigError):
    """`KBRegistryService.add_kb` refused: a KB with this name is already
    registered (#506, narrowing #501's fixed `ConfigError.public_message`).

    The message names only the KB's own name -- caller-supplied, never
    server-side detail -- so it is safe by construction; unlike the base
    `ConfigError`, which also covers raise sites that DO carry unsafe detail
    (a confined-root path, a driver's own text), this one never does. REST's
    own `except ConfigError` catch at this call (`admin.py`) already answers
    ``{"code": "CONFLICT", "message": str(e)}`` by hand; giving MCP the same
    code and an unmasked message here is what makes both transports agree.
    """

    error_code = "CONFLICT"
    #: Overrides the base ConfigError's fixed sentence -- this class's own
    #: message is always safe, so str(exc) should reach every transport.
    public_message = None


class KBDefinedInConfigError(ConfigError):
    """`KBRegistryService.update_kb` refused to change the ``default_role`` of
    a KB an operator defined by hand in config.yaml.

    That entry is the KB's policy: the access policy and every anonymous
    surface read it before the registry row. The server rewrites config.yaml
    entries only for KBs it wrote there itself (ephemeral and
    repo-subscribed ones, on evidence only the server writes); an operator's
    own entry is theirs to edit, followed by a restart.

    The message names the KB (caller-supplied) and the config file by its
    basename, never its path, so it is safe to show.
    """

    error_code = "KB_DEFINED_IN_CONFIG"
    public_message = None


class ConfigSaveRefusedError(ConfigError):
    """A config save was refused: it would drop KBs the caller did not name,
    or the file on disk could not be read to check (#377).

    ``str()`` is the operator's message: it names the real config file and the
    KBs. ``public_message`` is safe to return over HTTP.
    """

    error_code = "CONFIG_SAVE_REFUSED"
    public_message = (
        "The configuration was not saved: the config file changed since the server "
        "loaded it. Restart the server so it reads the current file, then re-run the "
        "request; the server log names the file and the knowledge bases."
    )

    def __init__(self, message: str, *, config_file=None, dropped: list[str] | None = None):
        super().__init__(message)
        self.config_file = config_file
        self.dropped = list(dropped or [])


class ConfigFileUnreadableError(ConfigSaveRefusedError):
    """The config file on disk could not be read as a registry (unparseable,
    not a mapping, an entry with no name), so a save cannot check what it
    would drop. Restarting does not help -- the server would fail to load the
    same file -- so the advice is to fix or move it.
    """

    public_message = (
        "The configuration was not saved: the config file on the server could not "
        "be read. An administrator needs to fix or move config.yaml; the server log "
        "names the file and the problem."
    )


class QuerySyntaxError(PyriteError):
    """Raised when a search query cannot be parsed by the backend's query
    engine (e.g. SQLite FTS5's ``no such column: ...`` when a bare
    special-char token reaches MATCH unquoted).

    Deterministic and not retryable — the query needs to change, retrying
    unchanged will fail identically. Carries an ``error_code`` attribute
    (``QUERY_SYNTAX``) so REST/MCP/CLI handlers can surface a stable
    identifier instead of falling through to a generic internal error.
    """

    error_code = "QUERY_SYNTAX"


class QueryTooLongError(ValidationError):
    """A search query is longer than the fixed maximum length.

    Raised before any sanitizing or searching runs, on every surface, so the
    cost of preparing a query is bounded by the cap. A query is never silently
    truncated. Carries ``error_code`` ``QUERY_TOO_LONG``; REST maps it to 422.
    """

    error_code = "QUERY_TOO_LONG"


class ClipperBlockedHostError(PyriteError):
    """Raised when the web clipper refuses to fetch a URL because the
    resolved host is on the SSRF blocklist (loopback, link-local,
    RFC1918 private, reserved IPv4/IPv6 ranges) or because the URL uses
    a non-http(s) scheme.

    Carries an ``error_code`` attribute (``CLIPPER_BLOCKED_HOST``) so
    REST/MCP handlers can surface a stable identifier.
    """

    error_code = "CLIPPER_BLOCKED_HOST"


class LastAdminError(ValidationError):
    """AuthService.set_role refused to demote the last global admin (#416).

    A ValidationError subclass so any generic ``except ValidationError``
    handler still catches it, but with its own ``error_code`` so a route
    that wants to label *this specific* refusal (409 LAST_ADMIN) does not
    also mislabel every other validation failure from the same call the
    same way.
    """

    error_code = "LAST_ADMIN"


class AccessDenied(PyriteError):  # noqa: N818 -- named "AccessDenied" verbatim by ADR-0037 §3
    """ADR-0037 §3: a denial is an exception in the same hierarchy, not a
    bespoke ``HTTPException`` or MCP refusal dict a surface builds by hand.

    Not raised by any surface yet -- theme 1 (the policy point, #383) and
    theme 3b/3c (REST's write and instance routes) are what raises these.
    This theme only needs the classes and their codes to exist, so those
    later themes, and this theme's transports, have something to map.

    A concealment denial is deliberately **not** a subclass here: an
    unreadable KB raises the not-found exception itself
    (``KBNotFoundError``/``EntryNotFoundError``), so no transport can tell a
    private KB apart from a missing one, even by accident (ADR-0037 §4).
    """


class NotAuthenticated(AccessDenied):
    """No principal at all -- the caller sent no credential, or an invalid
    one. REST's 401; MCP and the CLI report the same code.
    """

    error_code = "UNAUTHENTICATED"


class Forbidden(AccessDenied):
    """A principal exists but lacks the action on the resource. REST's 403;
    MCP and the CLI report the same code.
    """

    error_code = "FORBIDDEN"

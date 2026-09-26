"""The surfaces reach storage only through services -- a ratchet (#380).

REST and the other server routes (`pyrite/server`), MCP
(`pyrite/server/mcp_server.py`), the CLIs (`pyrite/cli`,
`pyrite/admin_cli.py`, `pyrite/read_cli.py`) and the Streamlit UI
(`pyrite/ui`) are *surfaces*. ADR-0031 makes the API the product surface;
that holds only if behaviour lives in one layer, so a surface asks a service
and the service asks storage.

**The rules are about where a database handle comes from**, not only what is
done with it: a handle cannot enter a surface except through one of these,
so aliasing it (``d = svc.db``), renaming it (a parameter called
``database``) or passing it to a helper is caught where it enters. Every
module under `pyrite/` outside `pyrite/services` and `pyrite/storage` is
walked (the import and storage rules apply to the surfaces only):

===============  ============================================================
rule             counts
===============  ============================================================
``_raw_conn``    the raw sqlite connection, as an attribute or ``getattr``
``.db``          any read of a ``.db`` attribute (``svc.db.x``, ``d = svc.db``,
                 ``helper(svc.db)``, ``getattr(svc, "db")``)
``db.``          any attribute of a local ``db`` other than ``close``
                 (``db.count_entries()``, ``db.vec_available``)
``.pyrite_db``   the app's shared DB on ``app.state`` (outside ``api.py``)
``get_db``       any reference to or import of ``get_db``, under any name
                 (outside ``api.py``, the provider module)
``AuthService(`` building the auth service in the server (outside ``api.py``,
                 whose ``get_auth_service`` provides it); the CLIs build
                 services from the handle ``cli/context.py`` opens
``PyriteDB``     importing ``PyriteDB`` / ``pyrite.storage.database``, or
                 calling ``PyriteDB(...)``
``sqlite3``      importing or using ``sqlite3``
``storage``      importing anything else from ``pyrite.storage``, and every
                 use of a name so imported (``IndexManager(...)``)
===============  ============================================================

**Two lists, both counted and both pinned.** A reach is allowed only if its
(function, rule) is listed with its exact count:

- ``COMPOSITION_ROOTS``: where a surface opens the database and wires its
  services -- ``api.py``'s providers, ``cli/context.py``, the MCP server's
  constructor and lazy service properties, the UI's cached ``_get_db``.
  That is their job; #382 (one composition root) is where they converge.
- ``ALLOWLIST``: reaches not yet moved, each with the ticket that moves it.

Each entry pins a count, so one more reach in a listed function fails, and
one fewer fails too until the count is lowered. Each list's length is
pinned (``*_SIZE``), so adding an entry means raising a number a reviewer
sees. ``ALLOWLIST_SIZE`` only goes down.

The inventory checks use the shared entry-point list
(`tests/_surface_inventory.py`, ADR-0037): every REST operation and MCP
tool handler is in a module this scan covers, or in an installed plugin
package (extension code is #384's, and outside this ratchet).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._surface_inventory import (
    REPO_ROOT,
    mcp_server,
    mcp_tools,
    plugin_packages,
    rest_operations,
)

PACKAGE = REPO_ROOT / "pyrite"
LAYERS_BELOW_THE_SURFACES = ("pyrite/services/", "pyrite/storage/")
RULES = (
    "_raw_conn",
    ".db",
    "db.",
    ".pyrite_db",
    "get_db",
    "AuthService(",
    "PyriteDB",
    "sqlite3",
    "storage",
)

_API = "pyrite/server/api.py"
_MCP = "pyrite/server/mcp_server.py::PyriteMCPServer"
_CTX = "pyrite/cli/context.py"

# -- Composition roots: (function, rule) -> (count, why) -----------------------

_PROVIDER = "api.py's DB provider family: opens the DB or builds a service from it"
_MCP_ROOT = "the MCP server's composition root: opens the DB, builds its services lazily"
_CLI_ROOT = "the CLI's provider module: opens the index and builds services from it"

COMPOSITION_ROOTS: dict[tuple[str, str], tuple[int, str]] = {
    (f"{_API}::<module>", "PyriteDB"): (1, _PROVIDER),
    (f"{_API}::<module>", "storage"): (1, _PROVIDER + " (IndexManager)"),
    (f"{_API}::get_db", "PyriteDB"): (1, _PROVIDER),
    (f"{_API}::get_index_mgr", "PyriteDB"): (1, _PROVIDER),
    (f"{_API}::get_index_mgr", "storage"): (2, _PROVIDER),
    (f"{_API}::get_index_worker", "PyriteDB"): (1, _PROVIDER),
    (f"{_API}::get_kb_registry", "storage"): (1, _PROVIDER),
    (f"{_API}::create_app._app_db", "PyriteDB"): (1, "the app's one PyriteDB, cached on app state"),
    (f"{_API}::create_app._app_db", "db."): (1, "merges DB-registered KBs into the app config"),
    (f"{_API}::create_app._app_get_db", "db."): (1, "the per-request session handle (#131)"),
    (f"{_API}::create_app._app_get_index_mgr", "storage"): (2, _PROVIDER),
    ("pyrite/server/mcp_server.py::<module>", "PyriteDB"): (1, _MCP_ROOT),
    ("pyrite/server/mcp_server.py::<module>", "storage"): (1, _MCP_ROOT + " (IndexManager)"),
    (f"{_MCP}.__init__", "PyriteDB"): (1, _MCP_ROOT),
    (f"{_MCP}.__init__", "storage"): (1, _MCP_ROOT),
    (f"{_MCP}.__init__", ".db"): (7, _MCP_ROOT),
    (f"{_MCP}.close", ".db"): (1, "closes what __init__ opened"),
    (f"{_MCP}.qa_svc", ".db"): (2, _MCP_ROOT),
    (f"{_MCP}.task_svc", ".db"): (1, _MCP_ROOT),
    (f"{_MCP}.search_svc", ".db"): (1, _MCP_ROOT),
    (f"{_MCP}.link_svc", ".db"): (1, _MCP_ROOT),
    (f"{_MCP}._get_index_worker", ".db"): (1, _MCP_ROOT),
    (f"{_MCP}._register_plugin_tools", ".db"): (1, _MCP_ROOT + " (the plugins' PluginContext)"),
    (f"{_CTX}::<module>", "PyriteDB"): (1, _CLI_ROOT),
    (f"{_CTX}::<module>", "sqlite3"): (1, _CLI_ROOT + " (sqlite3.Error, caught)"),
    (f"{_CTX}::<module>", "storage"): (1, _CLI_ROOT + " (IndexManager for the registry)"),
    (f"{_CTX}::cli_registry_context", "storage"): (1, _CLI_ROOT),
    (f"{_CTX}::get_config_and_db", "PyriteDB"): (1, _CLI_ROOT),
    (f"{_CTX}::get_config_and_db", "db."): (1, _CLI_ROOT + " (merges DB-registered KBs)"),
    (
        f"{_CTX}::open_index_for_validation",
        "storage",
    ): (1, _CLI_ROOT + " (validates index health at the CLI boundary)"),
    (f"{_CTX}::open_index_for_validation", "sqlite3"): (
        2,
        _CLI_ROOT + " (database errors fall back to YAML)",
    ),
    (f"{_CTX}::get_config_with_registered_kbs", "PyriteDB"): (1, _CLI_ROOT),
    (f"{_CTX}::get_config_with_registered_kbs", "db."): (1, _CLI_ROOT),
    (f"{_CTX}::get_config_with_registered_kbs", "sqlite3"): (1, _CLI_ROOT + " (sqlite3.Error)"),
    (f"{_CTX}::open_index_db", "PyriteDB"): (1, _CLI_ROOT),
    ("pyrite/cli/task_commands.py::<module>", "PyriteDB"): (
        1,
        "the return annotation of _get_service, the task commands' get_config_and_db wrapper",
    ),
    ("pyrite/ui/data.py::<module>", "PyriteDB"): (1, "the Streamlit UI's composition root"),
    ("pyrite/ui/data.py::_get_db", "PyriteDB"): (
        1,
        "the UI's cached DB; _get_kb_service is built on it",
    ),
}
COMPOSITION_ROOTS_SIZE = 38  # a new root is a design decision: raise it only with one

# -- Not yet moved: (function, rule) -> (count, why and where it moves) --------

_T382 = "the AI-settings precedence, deferred by #380; moves to SettingsService with #382"
_T383 = (
    "the DB handle an AccessPolicy is built on (#383 moved the rule out); goes when the "
    "transport receives a Principal (ADR-0037 theme 4 for MCP, theme 5 for /ws)"
)
_T384 = "the plugin API's context holds the DB for plugins; the plugin contract is #384's"
_FOLLOW = (
    "predates #380; moves behind a service in backlog item "
    "surfaces-stop-using-storage-classes-index-operations-and-the-remaining-layer"
)
_INDEX = "IndexManager driven from a surface; " + _FOLLOW

ALLOWLIST: dict[tuple[str, str], tuple[int, str]] = {
    (f"{_API}::get_llm_service", "db."): (4, _T382),
    ("pyrite/server/mcp_routes.py::<module>", "PyriteDB"): (1, _T383 + "; #433 rewrote it"),
    ("pyrite/server/websocket.py::<module>", "PyriteDB"): (1, _T383 + " (resolve_socket_scope)"),
    ("pyrite/plugins/context.py::PluginContext.search_semantic", ".db"): (3, _T384),
    ("pyrite/server/endpoints/admin.py::<module>", "storage"): (1, _INDEX),
    ("pyrite/server/endpoints/admin.py::get_stats", "storage"): (1, _INDEX),
    ("pyrite/server/endpoints/admin.py::sync_index", "storage"): (1, _INDEX),
    ("pyrite/server/endpoints/admin.py::sync_index", ".db"): (
        2,
        "hands index_mgr.db to _drain_embed_queue and SiteCacheService; " + _FOLLOW,
    ),
    ("pyrite/server/worktree_resolver.py::<module>", "PyriteDB"): (
        1,
        "a service living in server/ (WorktreeDB overlays); " + _FOLLOW,
    ),
    ("pyrite/server/worktree_resolver.py::<module>", "storage"): (1, "WorktreeDB; " + _FOLLOW),
    ("pyrite/server/worktree_resolver.py::WorktreeResolver.get_read_db", "storage"): (
        1,
        "WorktreeDB; " + _FOLLOW,
    ),
    ("pyrite/server/worktree_resolver.py::WorktreeResolver.get_write_context", "storage"): (
        1,
        "WorktreeDB; " + _FOLLOW,
    ),
    ("pyrite/admin_cli.py::index_build", "storage"): (2, _INDEX),
    ("pyrite/admin_cli.py::index_sync", "storage"): (2, _INDEX),
    ("pyrite/admin_cli.py::index_stats", "storage"): (2, _INDEX),
    ("pyrite/admin_cli.py::index_health", "storage"): (2, _INDEX),
    ("pyrite/cli/index_commands.py::index_build", "storage"): (2, _INDEX),
    ("pyrite/cli/index_commands.py::index_build", "db."): (1, "db.vec_available; " + _FOLLOW),
    ("pyrite/cli/index_commands.py::index_sync", "storage"): (2, _INDEX),
    ("pyrite/cli/index_commands.py::index_sync", "db."): (1, "db.vec_available; " + _FOLLOW),
    ("pyrite/cli/index_commands.py::index_stats", "storage"): (2, _INDEX),
    ("pyrite/cli/index_commands.py::index_health", "storage"): (2, _INDEX),
    ("pyrite/cli/index_commands.py::index_embed", "storage"): (2, _INDEX + " (is_empty, #380)"),
    ("pyrite/cli/index_commands.py::index_embed", "db."): (1, "db.vec_available; " + _FOLLOW),
    ("pyrite/cli/index_commands.py::index_reconcile", "storage"): (
        6,
        "IndexManager, DocumentManager, KBRepository; " + _FOLLOW,
    ),
    ("pyrite/cli/init_command.py::init_kb", "storage"): (2, _INDEX),
    ("pyrite/cli/schema_commands.py::schema_migrate", "storage"): (2, "KBRepository; " + _FOLLOW),
    ("pyrite/cli/search_commands.py::<module>", "storage"): (1, "KBRepository; " + _FOLLOW),
    ("pyrite/cli/search_commands.py::_search_files", "storage"): (1, "KBRepository; " + _FOLLOW),
    ("pyrite/cli/search_commands.py::_warn_if_stale", "storage"): (2, _INDEX),
    ("pyrite/cli/search_commands.py::register_search_command.search", "storage"): (
        2,
        _INDEX + " (is_empty / index_all, #380)",
    ),
    ("pyrite/cli/qa_commands.py::qa_check_urls", ".db"): (1, "URLChecker(ctx.db); " + _FOLLOW),
    ("pyrite/cli/qa_commands.py::qa_compact", ".db"): (
        1,
        "QAService(ctx.config, ctx.db); " + _FOLLOW,
    ),
    ("pyrite/cli/qa_commands.py::qa_stale", ".db"): (
        1,
        "QAService(ctx.config, ctx.db); " + _FOLLOW,
    ),
    ("pyrite/cli/db_commands.py::<module>", "sqlite3"): (
        1,
        "backup/restore of the index file; " + _FOLLOW,
    ),
    ("pyrite/cli/db_commands.py::db_backup", "sqlite3"): (2, "sqlite3 backup API; " + _FOLLOW),
    ("pyrite/cli/db_commands.py::db_restore", "sqlite3"): (
        2,
        "sqlite3 integrity check; " + _FOLLOW,
    ),
}
ALLOWLIST_SIZE = 37  # lower it with every entry removed; never raise it

SURFACES = (
    "pyrite/server/",
    "pyrite/cli/",
    "pyrite/ui/",
    "pyrite/admin_cli.py",
    "pyrite/read_cli.py",
)
PROVIDER_MODULE = "pyrite/server/api.py"

# Attributes that hold a database handle, and the rule each is counted under.
HANDLE_ATTRIBUTES = {"_raw_conn": "_raw_conn", "db": ".db", "pyrite_db": ".pyrite_db"}


def _within(module: str, package: str) -> bool:
    """``module`` is ``package`` or one of its submodules (dotted, not a prefix)."""
    return module == package or module.startswith(package + ".")


def _module_parts(rel: str) -> list[str]:
    parts = rel[: -len(".py")].split("/")
    return parts[:-1] if parts[-1] == "__init__" else parts


def _absolute(rel: str, node: ast.ImportFrom) -> str:
    """The absolute module an ``ImportFrom`` names, relative imports resolved."""
    if node.level == 0:
        return node.module or ""
    package = _module_parts(rel)
    if not rel.endswith("__init__.py"):
        package = package[:-1]
    base = package[: len(package) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def find_reaches(source: str, rel: str) -> dict[tuple[str, str], int]:
    """{(``rel::qualname``, rule): count} for every reach in ``source``."""
    found: dict[tuple[str, str], int] = {}
    stack: list[str] = []
    provider = rel == PROVIDER_MODULE
    surface = rel.startswith(SURFACES)
    tree = ast.parse(source)

    def hit(rule: str) -> None:
        key = (f"{rel}::{'.'.join(stack) or '<module>'}", rule)
        found[key] = found.get(key, 0) + 1

    # Names this module binds to pyrite.storage or sqlite3, so every *use* is
    # counted in the function that makes it, not only the import.
    storage_names: set[str] = set()
    sqlite_names: set[str] = set()
    pyrite_db_names: set[str] = {"PyriteDB"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and _within(_absolute(rel, node), "pyrite.storage"):
            for alias in node.names:
                bound = alias.asname or alias.name
                (pyrite_db_names if alias.name == "PyriteDB" else storage_names).add(bound)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlite3":
                    sqlite_names.add(alias.asname or "sqlite3")
                elif _within(alias.name, "pyrite.storage") and alias.asname:
                    # A plain `import pyrite.storage.x` binds `pyrite`; the
                    # import itself is counted, not every later `pyrite.` use.
                    storage_names.add(alias.asname)

    def import_of(module: str, name: str | None) -> None:
        full = f"{module}.{name}" if name else module
        if name == "get_db" and not provider:
            hit("get_db")
        if not surface:
            return
        if module.split(".")[0] == "sqlite3":
            hit("sqlite3")
        elif name == "PyriteDB" or _within(full, "pyrite.storage.database"):
            hit("PyriteDB")
        elif _within(module, "pyrite.storage"):
            hit("storage")

    class Visitor(ast.NodeVisitor):
        def _scoped(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_FunctionDef(self, node):  # noqa: N802 -- ast.NodeVisitor's names
            self._scoped(node)

        def visit_AsyncFunctionDef(self, node):  # noqa: N802
            self._scoped(node)

        def visit_ClassDef(self, node):  # noqa: N802
            self._scoped(node)

        def visit_Import(self, node):  # noqa: N802
            for alias in node.names:
                import_of(alias.name, None)

        def visit_ImportFrom(self, node):  # noqa: N802
            module = _absolute(rel, node)
            for alias in node.names:
                import_of(module, alias.name)

        def visit_Attribute(self, node):  # noqa: N802
            rule = HANDLE_ATTRIBUTES.get(node.attr)
            if rule and not (rule == ".pyrite_db" and provider):
                hit(rule)
            if isinstance(node.value, ast.Name) and node.value.id == "db" and node.attr != "close":
                hit("db.")
            if node.attr == "get_db" and not provider:
                hit("get_db")
            self.generic_visit(node)

        def visit_Name(self, node):  # noqa: N802
            if node.id == "get_db" and not provider:
                hit("get_db")
            if node.id in storage_names:
                hit("storage")
            if node.id in sqlite_names:
                hit("sqlite3")

        def visit_Call(self, node):  # noqa: N802
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name in pyrite_db_names:
                hit("PyriteDB")
            if name == "AuthService" and rel.startswith("pyrite/server/") and not provider:
                hit("AuthService(")
            if (
                name == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in HANDLE_ATTRIBUTES
            ):
                rule = HANDLE_ATTRIBUTES[node.args[1].value]
                if not (rule == ".pyrite_db" and provider):
                    hit(rule)
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


_SELF_TEST = pytest.mark.control(
    reason="tests the ratchet itself (its scanner, list bookkeeping or the shared "
    "inventory), which this PR adds: true on either side of the change"
)


def _scanned_modules() -> list[Path]:
    return [
        p
        for p in sorted(PACKAGE.rglob("*.py"))
        if not p.relative_to(REPO_ROOT).as_posix().startswith(LAYERS_BELOW_THE_SURFACES)
    ]


def scan() -> dict[tuple[str, str], int]:
    found: dict[tuple[str, str], int] = {}
    for path in _scanned_modules():
        found.update(find_reaches(path.read_text(), path.relative_to(REPO_ROOT).as_posix()))
    return found


@pytest.fixture(scope="module")
def reaches() -> dict[tuple[str, str], int]:
    return scan()


def _listed() -> dict[tuple[str, str], tuple[int, str]]:
    return {**COMPOSITION_ROOTS, **ALLOWLIST}


def check_no_new_reaches(reaches: dict[tuple[str, str], int]) -> None:
    listed = _listed()
    new = sorted(
        f"{where}  [{rule}] x{count}"
        for (where, rule), count in reaches.items()
        if (where, rule) not in listed
    )
    grown = sorted(
        f"{where}  [{rule}] {listed[(where, rule)][0]} -> {count}"
        for (where, rule), count in reaches.items()
        if (where, rule) in listed and count > listed[(where, rule)][0]
    )
    assert not (new or grown), (
        "a surface reaches storage without going through a service -- add or use a "
        "service method (and a provider in api.py / cli/context.py); do not list it:\n  "
        + "\n  ".join(new + [f"{g}  (one more than listed)" for g in grown])
    )


def test_no_surface_reaches_past_the_services(reaches):
    check_no_new_reaches(reaches)


def test_listed_counts_are_exact(reaches):
    """A listed reach that shrank or went away must be lowered or deleted, so
    the room it leaves cannot be reused."""
    stale = sorted(
        f"{where} [{rule}] listed {count}, found {reaches.get((where, rule), 0)}"
        for (where, rule), (count, _) in _listed().items()
        if reaches.get((where, rule), 0) < count
    )
    assert not stale, "lower or delete these entries (and the *_SIZE):\n  " + "\n  ".join(stale)


@_SELF_TEST
def test_the_lists_only_shrink():
    assert len(ALLOWLIST) == ALLOWLIST_SIZE, (
        f"ALLOWLIST has {len(ALLOWLIST)} entries but ALLOWLIST_SIZE is {ALLOWLIST_SIZE}. "
        "Removing an entry: lower ALLOWLIST_SIZE to match. Adding one is not the fix; "
        "route the reach through a service."
    )
    assert len(COMPOSITION_ROOTS) == COMPOSITION_ROOTS_SIZE, (
        f"COMPOSITION_ROOTS has {len(COMPOSITION_ROOTS)} entries but "
        f"COMPOSITION_ROOTS_SIZE is {COMPOSITION_ROOTS_SIZE}. A new root is a design "
        "decision (#382), not a way past this test."
    )
    assert not set(COMPOSITION_ROOTS) & set(ALLOWLIST)
    for (_, rule), (count, reason) in _listed().items():
        assert rule in RULES and count > 0 and reason.strip()


# -- Self-tests: the rules catch what they say, and the lists hold ------------------
_EP = "pyrite/server/endpoints/new_endpoint.py"


def _rules(source: str, rel: str = _EP) -> dict[str, int]:
    out: dict[str, int] = {}
    for (_, rule), count in find_reaches(source, rel).items():
        out[rule] = out.get(rule, 0) + count
    return out


@_SELF_TEST
@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ("def h(svc):\n    return svc.db.get_setting('k')\n", {".db": 1}),
        ("def h(db):\n    return db.count_entries()\n", {"db.": 1}),
        ("def h(db):\n    db._raw_conn.execute('SELECT 1')\n", {"db.": 1, "_raw_conn": 1}),
        ("def h(c):\n    return PyriteDB(c.settings.index_path)\n", {"PyriteDB": 1}),
        ("def h(db=Depends(get_db)):\n    pass\n", {"get_db": 1}),
        ("def h(db, c):\n    return AuthService(db, c.settings.auth)\n", {"AuthService(": 1}),
        ("def h(request):\n    return request.app.state.pyrite_db\n", {".pyrite_db": 1}),
        # The evasions the #461 cold read found, each caught where the handle enters:
        ("def h(svc):\n    d = svc.db\n    return d.get_all_settings()\n", {".db": 1}),
        ("def h(svc):\n    return getattr(svc, 'db').get_all_settings()\n", {".db": 1}),
        ("def h(svc):\n    return getattr(svc, '_raw_conn')\n", {"_raw_conn": 1}),
        ("def h(x=Depends(api.get_db)):\n    pass\n", {"get_db": 1}),
        ("from ..api import get_db as fetch\n", {"get_db": 1}),
        (
            "def h(database=Depends(get_db)):\n    return database.execute_sql('DELETE')\n",
            {"get_db": 1},
        ),
        ("def h(svc):\n    return helper(svc.db)\n", {".db": 1}),
        ("def h(db):\n    return db.vec_available\n", {"db.": 1}),
        ("import sqlite3\ndef h(p):\n    return sqlite3.connect(p)\n", {"sqlite3": 2}),
        ("import sqlite3 as lite\ndef h(p):\n    return lite.connect(p)\n", {"sqlite3": 2}),
        ("from ...storage.database import PyriteDB\n", {"PyriteDB": 1}),
        ("from pyrite.storage import PyriteDB as P\n", {"PyriteDB": 1}),
        ("from pyrite.storage import PyriteDB as P\ndef h(p):\n    return P(p)\n", {"PyriteDB": 2}),
        ("import pyrite.storage.database\n", {"PyriteDB": 1}),
        (
            "from ...storage.index import IndexManager\ndef h(db, c):\n    IndexManager(db, c)\n",
            {"storage": 2},
        ),
        ("def h():\n    from ...storage import IndexManager as IM\n    IM()\n", {"storage": 2}),
    ],
)
def test_each_rule_catches_its_reach(snippet, expected):
    assert _rules(snippet) == expected


@_SELF_TEST
@pytest.mark.parametrize(
    "snippet",
    [
        "def h(db):\n    db.close()\n",
        "def h(svc):\n    return svc.get_setting('k')\n",
        "from ...services.kb_service import KBService\n",
        "from ...storage_helpers import x\n",  # a name that only starts like storage
    ],
)
def test_what_the_rules_do_not_catch(snippet):
    assert _rules(snippet) == {}


@_SELF_TEST
def test_the_import_and_storage_rules_apply_to_surfaces_only():
    src = "import sqlite3\nfrom ..storage.index import IndexManager\n"
    assert _rules(src, "pyrite/cli/x.py") == {"sqlite3": 1, "storage": 1}
    assert _rules(src, "pyrite/plugins/x.py") == {}


@_SELF_TEST
def test_auth_service_rule_is_the_servers():
    src = "def h(db, c):\n    return AuthService(db, c.settings.auth)\n"
    assert _rules(src, "pyrite/admin_cli.py") == {}
    assert _rules(src, "pyrite/server/websocket.py") == {"AuthService(": 1}


@_SELF_TEST
def test_the_provider_module_may_take_get_db_and_build_auth_service():
    src = "def get_x(db=Depends(get_db)):\n    return AuthService(db, None)\n"
    assert _rules(src, _API) == {}
    assert _rules(src) == {"get_db": 1, "AuthService(": 1}


@_SELF_TEST
def test_a_new_endpoint_touching_db_fails_the_ratchet(reaches):
    """The ticket's acceptance: the test fails when a new endpoint touches `.db.`."""
    new = find_reaches(
        "def get_thing(svc=Depends(get_kb_service)):\n    return svc.db.get_all_settings()\n",
        "pyrite/server/endpoints/things.py",
    )
    with pytest.raises(AssertionError, match=r"things.py::get_thing  \[\.db\] x1"):
        check_no_new_reaches({**reaches, **new})


@_SELF_TEST
def test_a_composition_root_is_not_a_free_pass(reaches):
    """One more `.db` read inside the MCP constructor -- say
    ``self.db.execute_sql("DELETE ...")`` -- fails: roots are counted per rule."""
    key = (f"{_MCP}.__init__", ".db")
    grown = {**reaches, key: reaches[key] + 1}
    with pytest.raises(AssertionError, match="one more than listed"):
        check_no_new_reaches(grown)
    # ... and a rule the root is not listed for fails outright.
    other = {**reaches, (f"{_MCP}.__init__", "sqlite3"): 1}
    with pytest.raises(AssertionError, match=r"__init__  \[sqlite3\]"):
        check_no_new_reaches(other)


@_SELF_TEST
def test_an_allowlisted_function_cannot_grow(reaches):
    key = (f"{_API}::get_llm_service", "db.")
    with pytest.raises(AssertionError, match="one more than listed"):
        check_no_new_reaches({**reaches, key: reaches[key] + 1})


# -- Coverage: the scan sees every entry point ---------------------------------


def _is_scanned_module(module: str) -> bool:
    return module.startswith("pyrite.") and not module.startswith(
        ("pyrite.services.", "pyrite.storage.")
    )


@_SELF_TEST
def test_every_rest_operation_is_in_a_scanned_module():
    ops = rest_operations()
    assert len(ops) > 100  # the walk found the app, not a stub
    unscanned = sorted(
        f"{op.name} -> {op.module}" for op in ops if not _is_scanned_module(op.module)
    )
    assert not unscanned, "REST handlers outside the scanned surfaces:\n  " + "\n  ".join(unscanned)


@_SELF_TEST
def test_every_mcp_tool_is_in_a_scanned_module_or_a_plugin_package():
    """By module name, not file path, so an extension installed editable from
    another checkout (or as a wheel) is still recognised."""
    plugins = plugin_packages()
    with mcp_server() as server:
        tools = mcp_tools(server)
    assert len(tools) > 50
    unscanned = sorted(
        f"{t.name} -> {t.module}"
        for t in tools
        if not _is_scanned_module(t.module) and t.module.split(".")[0] not in plugins
    )
    assert not unscanned, "MCP tool handlers outside the scanned surfaces:\n  " + "\n  ".join(
        unscanned
    )

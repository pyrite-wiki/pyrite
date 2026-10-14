"""A test that drives an entry point does not patch the service behind it (test theme Q1).

A test that invokes `pyrite search` through `CliRunner` and patches
`SearchService` tests the patch. On 2026-10-13 the CLI began calling
`search_response()` instead of `search()`; three files that mocked `search()`
broke, and the repair was to hand-write the service's response dict into each
test. A test of an entry point must survive a refactor behind it: seed the world
(`tests/search_verdict_seed.py`, `tests/auth_seed.py`) and let the real service
answer.

The rule, per file. A file *drives an entry point* when it imports `CliRunner`
or `TestClient`, builds a `PyriteMCPServer`, calls MCP dispatch
(`_dispatch_tool`), uses `PluginContext`, defines `make_client` or takes the `make_client` / `client`
fixture. In such a file these fail:

  * `patch("<target>")`, `patch.object(<obj>, "<attr>")`, `monkeypatch.setattr(...)`
    where the target is in `pyrite.services` or `pyrite.storage`, or is a name a
    `pyrite.cli` / `pyrite.server` / `pyrite.plugins` module imported from them
    (`pyrite.cli.search_commands.KBRepository`);
  * `patch.object` / `patch.multiple` / `monkeypatch.setattr` / `mocker.patch...` on a class or
    module the test file imported from `pyrite.services` / `pyrite.storage` (aliases resolved
    from the import: `import unittest.mock as um`, `patch as p`);
  * anything the walk cannot trace to a name, reported as `<unresolved: ...>` and never
    ignored: a local instance (`svc.db.backend`), `type(x)`, `x.__class__`, a target held in a
    variable or an f-string. Such a file is listed, or its patch moves to a named edge target.

The process edge is allowed, by target: a dotted path with a `subprocess`,
`httpx`, `requests`, `urllib`, `socket`, `smtplib`, `time` or `datetime`
component; `_import_anthropic` / `_import_openai` (any `_import_*`);
`is_available`, `_load_model` and `_MODEL_CACHE` of the embedding service (the
model is the edge; a test that needs a model failure returns a failing model
from `_load_model`); module constants (an ALL_CAPS attribute, however spelled, is a tunable,
not a method); and anything outside `pyrite.services` / `pyrite.storage` (config
loading, `load_config`, is how a test points the CLI at its world).

Scanned: every `.py` under `tests/` and `extensions/*/tests`, so `conftest.py` and the seed
helpers are covered too (a patch hidden in a shared helper counts when the file drives).

Known limits: the allowlist is per file, not per site (a listed file can add another service
patch), and the ceiling below lives in this file, which a PR can edit; review is the check on
both.

The failure message names the fix:
`file:line: <target> is the service behind <entry point>; seed the world instead`.

`ALLOWLIST` names every file that violates today with one line of why. It can
only shrink: a file not on it that violates fails, a file on it that no longer
violates fails (remove the line), and `ALLOWLIST_SIZE_AT_LANDING` is a ceiling a
pull request may lower and never raise.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Path components that mark the process edge (see the docstring).
EDGE_COMPONENTS = {
    "subprocess",
    "httpx",
    "requests",
    "urllib",
    "socket",
    "smtplib",
    "time",
    "datetime",
    "os",
    "sys",
    "shutil",
    "webbrowser",
}
#: Attributes of the embedding service that are its edge (the model and its install).
EDGE_ATTRS = {"is_available", "_load_model", "_MODEL_CACHE"}
EDGE_ATTR_PREFIXES = ("_import_",)  # llm_service._import_anthropic / _import_openai

SERVICE_ROOTS = ("pyrite.services", "pyrite.storage")
WRAPPER_ROOTS = ("pyrite.cli", "pyrite.server", "pyrite.plugins")
DRIVER_NAMES = {"CliRunner", "TestClient", "PyriteMCPServer", "PluginContext", "_dispatch_tool"}
#: Fixture names that hand a test a client: the `make_client` factory in tests/conftest.py.
DRIVER_FIXTURES = {"make_client", "client"}
#: Every spelling of unittest.mock's patchers, by the dotted path an import resolves to.
MOCK_CALLS = {
    "unittest.mock.patch": "patch",
    "unittest.mock.patch.object": "object",
    "unittest.mock.patch.multiple": "multiple",
}

#: Files that violate on dev. The migration debt; it can only shrink.
#: path (relative to the repo root) -> why it is listed.
ALLOWLIST: dict[str, str] = {
    "extensions/social/tests/test_social.py": "1 patch(es) behind PluginContext: kb_service.KBService.create_entry",
    "extensions/software-kb/tests/test_software_kb.py": "11 patch(es) behind CliRunner, PluginContext: kb_service.KBService, kb_service.KBService.create_entry",
    "extensions/zettelkasten/tests/test_zettelkasten.py": "1 patch(es) behind CliRunner: kb_service.KBService.create_entry",
    "tests/test_admin_cli.py": "1 patch(es) behind CliRunner: storage.repository.KBRepository._get_file_path",
    "tests/test_anonymous_and_missing_kb_writes.py": "1 patch(es) behind TestClient: clipper.ClipperService.clip_url",
    "tests/test_auth_bootstrap.py": "3 patch(es) behind CliRunner, TestClient: auth_service.AuthService._verify_password, auth_service.AuthService.login",
    "tests/test_auth_endpoints.py": "1 patch(es) behind TestClient: auth_service.AuthService.login",
    "tests/test_auth_user_management.py": "1 patch(es) behind TestClient: auth_service.AuthService.set_role",
    "tests/test_authz_authorize.py": "1 patch(es) behind TestClient: access_policy.AccessPolicy.read_scope",
    "tests/test_cli_finds_db_registered_kbs.py": "3 patch(es) behind CliRunner: storage.database.PyriteDB.close, storage.index.IndexManager.check_health",
    "tests/test_cli_json_output.py": "5 patch(es) behind CliRunner: search_service.SearchService.search, storage.database.PyriteDB.close ...",
    "tests/test_cli_outcome_write_path.py": "2 patch(es) behind CliRunner: kb_service.KBService.add_link",
    "tests/test_default_output_is_the_result.py": "9 patch(es) behind CliRunner, PyriteMCPServer, TestClient, _dispatch_tool: embedding_service.EmbeddingService._get_model, embedding_service.EmbeddingService.embed_all ...",
    "tests/test_default_role_change_takes_effect.py": "1 patch(es) behind TestClient: site_cache.SiteCacheService._render_landing",
    "tests/test_embed_queue_debt_is_honest.py": "5 patch(es) behind TestClient: embedding_worker.EmbeddingWorker, embedding_worker.EmbeddingWorker._get_embedding_svc",
    "tests/test_fields_single_entry.py": "2 patch(es) behind CliRunner: kb_service.KBService.get_entry",
    "tests/test_ids_commands.py": "12 patch(es) behind CliRunner: id_pin_service._rename, id_pin_service._write_pin ...",
    "tests/test_json_stdout.py": "2 patch(es) behind CliRunner: url_checker.URLChecker.check_url, url_checker.URLChecker.collect_urls",
    "tests/test_kb_bulk_create_description.py": "1 patch(es) behind PyriteMCPServer, _dispatch_tool: kb_service.build_entry",
    "tests/test_kb_export_read_scoping.py": "1 patch(es) behind TestClient: export_service.ExportService.export_kb_to_repo",
    "tests/test_kb_policy_availability.py": "5 patch(es) behind TestClient: ephemeral_service.save_config, repo_service.save_config ...",
    "tests/test_kb_scoped_writes.py": "1 patch(es) behind TestClient: clipper.ClipperService.clip_url",
    "tests/test_mcp_server.py": "1 patch(es) behind PyriteMCPServer, _dispatch_tool: <unresolved>:_run_hooks",
    "tests/test_one_reconcile.py": "2 patch(es) behind CliRunner: storage.index._hash_file, storage.repository.KBRepository.load_entry_from_file",
    "tests/test_private_kb_read_scoping.py": "1 patch(es) behind TestClient: search_service.SearchService.search",
    "tests/test_qa_assessment.py": "6 patch(es) behind CliRunner, PyriteMCPServer: pyrite.cli.context.PyriteDB, qa_service.QAService._maybe_create_task ...",
    "tests/test_qa_commands_run.py": "1 patch(es) behind CliRunner: url_checker.URLChecker.check_url",
    "tests/test_repo_subscribe_never_adopts_existing_kb.py": "2 patch(es) behind TestClient: git_service.GitService.clone_with_code, git_service.GitService.fork_repo",
    "tests/test_requested_effect_exit_code.py": "6 patch(es) behind CliRunner: kb_service.KBService.add_link, kb_service.KBService.reconcile_templated_subdirectories ...",
    "tests/test_save_config_never_drops_unnamed_kbs.py": "2 patch(es) behind CliRunner, TestClient: repo_service.GitService.clone_with_code, repo_service.GitService.fork_repo",
    "tests/test_search_contract.py": "13 patch(es) behind CliRunner, PyriteMCPServer, _dispatch_tool: embedding_service.EmbeddingService.has_embeddings, embedding_service.EmbeddingService.search_similar ...",
    "tests/test_server_write_embedding_debt.py": "2 patch(es) behind TestClient: embedding_worker.EmbeddingWorker._get_embedding_svc",
    "tests/test_site_cache.py": "1 patch(es) behind TestClient: site_cache.SiteCacheService._render_entry",
    "tests/test_site_cache_follows_deletes.py": "1 patch(es) behind TestClient: site_cache.SiteCacheService._render_kb_index",
    "tests/test_sync_wait_rerenders_site_cache.py": "3 patch(es) behind TestClient: site_cache.SiteCacheService._render_entry, site_cache.SiteCacheService.render_all",
    "tests/test_untrusted_local_config.py": "1 patch(es) behind CliRunner, TestClient: embedding_service.EmbeddingService.__init__",
    "tests/test_websocket_credential_lifetime.py": "1 patch(es) behind TestClient: clipper.ClipperService.clip_url",
    "tests/test_websocket_kb_policy_lifetime.py": "1 patch(es) behind TestClient: repo_service.save_config",
    "tests/test_websocket_scoping.py": "1 patch(es) behind TestClient: clipper.ClipperService.clip_url",
    "tests/test_write_pipeline.py": "2 patch(es) behind CliRunner, PyriteMCPServer, TestClient, _dispatch_tool: clipper.ClipperService.clip_url, kb_service.KBService._prepare_and_save",
    "tests/conftest.py": "the console_width fixture sets Rich's terminal width on the CLI consoles (a terminal edge, unresolved by the walk)",
    "tests/test_cli_surfaces_through_services.py": "patch target held in a variable; unresolved",
    "tests/test_doc_write_as_patch.py": "patches a class held in a variable (cls.emit); unresolved",
    "tests/test_embedding_prewarm.py": "patches _get_model on a local service instance behind the REST client; unresolved",
    "tests/test_ephemeral_kb_name_is_not_a_path.py": "patches execute_sql on a local service db; unresolved",
    "tests/test_get_reads_the_file.py": "spies on os/glob/Path directory-walk functions to count walks (a filesystem process edge; the owners come from a loop, unresolved)",
    "tests/test_index_worker.py": "patches IndexManager behind the REST client",
    "tests/test_link_asymmetric.py": "patches close on a fixture-held db; unresolved",
    "tests/test_mcp_finders.py": "patches a method named by a variable; unresolved",
    "tests/test_mcp_setup_reads_back.py": "21 patches of names held in variables; unresolved",
    "tests/test_plugin_integration.py": "patches get_hooks_for_kb on a local registry; unresolved",
    "tests/test_repo_error_disclosure.py": "unit tests of GitService's error text patch GitService in a file whose other classes drive REST via make_client; split the file (Q8) or migrate",
    "tests/test_shipped_schemas_accept_workflows.py": "patch target built with an f-string; unresolved",
}
ALLOWLIST_SIZE_AT_LANDING = 53


def _is_service_path(dotted: str) -> bool:
    return any(dotted == r or dotted.startswith(r + ".") for r in SERVICE_ROOTS)


@lru_cache(maxsize=None)  # noqa: UP033
def _imported_from_services(module: str) -> frozenset[str]:
    """Names a pyrite module imports from services/storage (or defines there)."""
    path = ROOT / (module.replace(".", "/") + ".py")
    if not path.exists():
        path = ROOT / module.replace(".", "/") / "__init__.py"
    if not path.exists():
        return frozenset()
    names = set()
    package = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom):
            continue
        # `from ..storage.repository import X` is `pyrite.storage.repository`
        base = package[: len(package) - (node.level - 1)] if node.level else []
        source = ".".join([*base, *([node.module] if node.module else [])])
        if _is_service_path(source):
            names.update(a.asname or a.name for a in node.names)
    return frozenset(names)


def _edge(dotted: str) -> bool:
    parts = dotted.split(".")
    return (
        any(p in EDGE_COMPONENTS for p in parts)
        or parts[-1] in EDGE_ATTRS
        or parts[-1].startswith(EDGE_ATTR_PREFIXES)
    )


def _string_target_is_service(target: str) -> bool:
    if _edge(target) or target.rpartition(".")[2].isupper():  # a tunable constant
        return False
    if _is_service_path(target):
        return True
    module, _, attr = target.rpartition(".")
    if any(module == r or module.startswith(r + ".") for r in WRAPPER_ROOTS):
        return attr in _imported_from_services(module)
    return False


def _dotted(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _file_imports(tree: ast.AST) -> dict[str, str]:
    """local name -> full dotted path, for every import in the file."""
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names[a.asname or a.name.split(".")[0]] = (
                    a.name if a.asname else a.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                names[a.asname or a.name] = f"{node.module}.{a.name}"
    return names


def entry_points(tree: ast.AST) -> list[str]:
    """The entry points a file drives: what it imports, calls, defines or takes as a fixture."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found.update(a.name for a in node.names if a.name in DRIVER_NAMES)
        elif isinstance(node, ast.Name) and node.id in DRIVER_NAMES:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in DRIVER_NAMES:
            found.add(node.attr)
        elif isinstance(node, ast.arg) and node.arg in DRIVER_FIXTURES:
            found.add(node.arg)
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "make_client"
        ):
            found.add("make_client")
    return sorted(found)


def _patch_calls(tree: ast.AST, imports: dict[str, str]):
    """(call, kind) for every patch spelling: patch, patch.object, patch.multiple (however
    imported or aliased), `mocker.patch...`, and `monkeypatch.setattr`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        if name is None:
            continue
        head, _, rest = name.partition(".")
        resolved = (
            f"{imports[head]}.{rest}" if head in imports and rest else imports.get(head, name)
        )
        if resolved in MOCK_CALLS:
            yield node, MOCK_CALLS[resolved]
        elif name.startswith("mocker.") and name[7:] in ("patch", "patch.object", "patch.multiple"):
            yield node, MOCK_CALLS["unittest.mock." + name[7:]]
        elif name.endswith("monkeypatch.setattr") or name == "mp.setattr":
            yield node, "setattr"


def _unresolved(node: ast.AST, attr: str | None = None) -> str:
    text = ast.unparse(node)
    return f"<unresolved: {text}>" + (f".{attr}" if attr else "")


def _object_target(obj: ast.AST, attr: str | None, imports: dict[str, str]) -> str | None:
    """The offending target for patch.object/setattr's first argument, or None.

    A target that cannot be traced to a name (a local instance, `type(x)`, a variable)
    comes back as ``<unresolved: ...>``: reported, never ignored.
    """
    dotted = _dotted(obj)
    if dotted is None:
        return _unresolved(obj, attr)
    head, _, rest = dotted.partition(".")
    full = imports.get(head)
    if full is None:
        return _unresolved(obj, attr)
    full = f"{full}.{rest}" if rest else full
    if not _is_service_path(full):
        return None
    if attr is None:
        return full
    if attr.isupper() or attr in EDGE_ATTRS or attr.startswith(EDGE_ATTR_PREFIXES) or _edge(full):
        return None
    return f"{full}.{attr}"


def violations(source: str) -> list[tuple[int, str]]:
    """``[(line, target)]`` for each service patch in a file that drives an entry point."""
    tree = ast.parse(source)
    if not entry_points(tree):
        return []
    imports = _file_imports(tree)
    out = []
    for call, kind in _patch_calls(tree, imports):
        args = call.args
        if not args:
            continue
        first = args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            if _string_target_is_service(first.value):
                out.append((call.lineno, first.value))
            continue
        if kind == "patch":  # a target held in a variable or an f-string
            out.append((call.lineno, _unresolved(first)))
            continue
        if kind == "multiple":
            attr = call.keywords[0].arg if call.keywords else None
        else:
            if len(args) < 2:
                continue
            attr_node = args[1]
            if not (isinstance(attr_node, ast.Constant) and isinstance(attr_node.value, str)):
                out.append((call.lineno, _unresolved(attr_node)))
                continue
            attr = attr_node.value
        target = _object_target(first, attr, imports)
        if target:
            out.append((call.lineno, target))
    return sorted(out)


def _test_files() -> list[Path]:
    """Every Python file under tests/ and extensions/*/tests: test modules, conftest.py and the
    seed helpers, so a patch hidden in a shared helper is seen too."""
    return sorted({*ROOT.glob("tests/**/*.py"), *ROOT.glob("extensions/*/tests/**/*.py")})


@lru_cache(maxsize=1)
def _scan() -> dict[str, list[tuple[int, str, str]]]:
    """relative path -> [(line, target, entry points)] for every offending file."""
    found = {}
    for path in _test_files():
        if path.name == Path(__file__).name:
            continue
        source = path.read_text(encoding="utf-8")
        hits = violations(source)
        if hits:
            eps = ", ".join(entry_points(ast.parse(source)))
            found[str(path.relative_to(ROOT))] = [(line, tgt, eps) for line, tgt in hits]
    return found


# ------------------------------------------------------------------ the detector


@pytest.mark.control(reason="Pins the detector on planted sources, independent of the tree")
@pytest.mark.parametrize(
    ("source", "target"),
    [
        (
            "from typer.testing import CliRunner\n"
            "from unittest.mock import patch\n"
            "with patch('pyrite.services.search_service.SearchService') as s: pass\n",
            "pyrite.services.search_service.SearchService",
        ),
        (
            "from starlette.testclient import TestClient\n"
            "from unittest.mock import patch\n"
            "patch('pyrite.services.kb_service.KBService.count_entries', return_value=1)\n",
            "pyrite.services.kb_service.KBService.count_entries",
        ),
        (
            "from typer.testing import CliRunner\n"
            "from unittest.mock import patch\n"
            "patch('pyrite.cli.search_commands.KBRepository')\n",
            "pyrite.cli.search_commands.KBRepository",
        ),
        (
            "from typer.testing import CliRunner\n"
            "from pyrite.services.search_service import SearchService\n"
            "def t(monkeypatch):\n"
            "    monkeypatch.setattr(SearchService, 'search', lambda *a: [])\n",
            "pyrite.services.search_service.SearchService.search",
        ),
        (
            "from typer.testing import CliRunner\n"
            "import pyrite.services.embedding_service as es\n"
            "def t(monkeypatch):\n"
            "    monkeypatch.setattr(es.EmbeddingService, 'search_similar', None)\n",
            "pyrite.services.embedding_service.EmbeddingService.search_similar",
        ),
        (
            "from typer.testing import CliRunner\n"
            "def t(monkeypatch, svc):\n"
            "    monkeypatch.setattr(type(svc.db.backend), 'search_semantic', None)\n",
            "<unresolved: type(svc.db.backend)>.search_semantic",
        ),
        (
            "from typer.testing import CliRunner\n"
            "def t(monkeypatch, svc):\n"
            "    monkeypatch.setattr(svc.db.backend, 'search_semantic', None)\n",
            "<unresolved: svc.db.backend>.search_semantic",
        ),
        (
            "from typer.testing import CliRunner\nfrom unittest.mock import patch\n"
            "def t(target):\n    patch(target)\n",
            "<unresolved: target>",
        ),
        (
            "from typer.testing import CliRunner\nfrom unittest.mock import patch\n"
            "def t(n):\n    patch(f'pyrite.services.{n}.X')\n",
            "<unresolved: f'pyrite.services.{n}.X'>",
        ),
        (
            "from typer.testing import CliRunner\n"
            "def t(mocker):\n    mocker.patch('pyrite.services.kb_service.KBService.count_entries')\n",
            "pyrite.services.kb_service.KBService.count_entries",
        ),
        (
            "from typer.testing import CliRunner\n"
            "def t(mocker, svc):\n    mocker.patch.object(svc, 'count_entries')\n",
            "<unresolved: svc>.count_entries",
        ),
        (
            "from typer.testing import CliRunner\nimport unittest.mock as um\n"
            "um.patch('pyrite.services.kb_service.KBService.count_entries')\n",
            "pyrite.services.kb_service.KBService.count_entries",
        ),
        (
            "from typer.testing import CliRunner\nfrom unittest.mock import patch as p\n"
            "p('pyrite.services.kb_service.KBService.count_entries')\n",
            "pyrite.services.kb_service.KBService.count_entries",
        ),
        (
            "from typer.testing import CliRunner\nfrom unittest import mock\n"
            "from pyrite.services.kb_service import KBService\n"
            "mock.patch.multiple(KBService, count_entries=1)\n",
            "pyrite.services.kb_service.KBService.count_entries",
        ),
        (
            "from unittest.mock import patch\ndef test_x(make_client):\n    patch('pyrite.services.kb_service.KBService.x')\n",
            "pyrite.services.kb_service.KBService.x",
        ),
    ],
)
def test_the_detector_flags_a_planted_service_patch(source, target):
    assert [t for _, t in violations(source)] == [target]


@pytest.mark.control(reason="Pins the detector's edge allowlist and driver gate")
@pytest.mark.parametrize(
    "source",
    [
        # no entry point driven: a unit test of the service may patch its edges and its own parts
        "from unittest.mock import patch\n"
        "patch('pyrite.services.search_service.SearchService.search')\n",
        # the process edge
        "from typer.testing import CliRunner\nfrom unittest.mock import patch\n"
        "patch('pyrite.services.git_service.subprocess.run')\n"
        "patch('pyrite.services.llm_service._import_anthropic')\n"
        "patch('pyrite.services.embedding_service.is_available')\n",
        # config is how a test points the CLI at its world
        "from typer.testing import CliRunner\nfrom unittest.mock import patch\n"
        "patch('pyrite.cli.search_commands.load_config')\n"
        "patch('pyrite.services.search_service.VERDICT_NEIGHBOURS', 3)\n",
        # a tunable constant on a service module
        "from typer.testing import CliRunner\n"
        "import pyrite.storage.backends.sqlite_backend as b\n"
        "def t(monkeypatch):\n    monkeypatch.setattr(b, '_SQLITE_VEC_MAX_K', 8)\n",
        # the model load is the edge
        "from typer.testing import CliRunner\n"
        "import pyrite.services.embedding_service as es\n"
        "def t(monkeypatch):\n    monkeypatch.setattr(es, '_load_model', None)\n",
    ],
)
def test_the_detector_allows_the_edge_and_non_drivers(source):
    assert violations(source) == []


# ------------------------------------------------------------------- the tree


def _message(path: str, line: int, target: str, eps: str) -> str:
    if target.startswith("<unresolved"):
        return (
            f"{path}:{line}: {target} cannot be traced to a name, so the walk cannot tell whether it "
            f"is the service behind {eps}; patch a named target at the process edge, or list the file"
        )
    return (
        f"{path}:{line}: {target} is the service behind {eps}; seed the world instead "
        "(tests/search_verdict_seed.py, tests/auth_seed.py)"
    )


def test_no_test_mocks_the_service_behind_the_entry_point_it_drives():
    found = _scan()
    new = [
        _message(path, line, tgt, eps)
        for path, hits in found.items()
        if path not in ALLOWLIST
        for line, tgt, eps in hits
    ]
    assert not new, "\n" + "\n".join(new)


def test_the_allowlist_can_only_shrink():
    found = _scan()
    stale = sorted(set(ALLOWLIST) - set(found))
    assert not stale, f"no longer violate; remove them from ALLOWLIST: {stale}"
    assert len(ALLOWLIST) <= ALLOWLIST_SIZE_AT_LANDING, (
        f"ALLOWLIST grew to {len(ALLOWLIST)} (ceiling {ALLOWLIST_SIZE_AT_LANDING}): "
        "seed the world in the new test instead of listing it"
    )
    assert all(ALLOWLIST.values()), "every entry says why"
    assert all((ROOT / p).exists() for p in ALLOWLIST)

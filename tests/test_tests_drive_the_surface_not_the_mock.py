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
    (`pyrite.cli.search_commands.project_fields`);
  * `patch.object` / `patch.multiple` / `monkeypatch.setattr` / `mocker.patch...` on a class or
    module the test file imported from `pyrite.services` / `pyrite.storage` (aliases resolved
    from the import: `import unittest.mock as um`, `patch as p`);
  * anything the walk cannot trace to a name, reported as `<unresolved: ...>` and never
    ignored: a local instance (`svc.db.backend`), `type(x)`, `x.__class__`, a target held in a
    variable or an f-string. Such a file is listed, or its patch moves to a named edge target.

The process edge is allowed, by target: a dotted path with a `subprocess`,
`httpx`, `requests`, `urllib`, `socket`, `smtplib`, `time` or `datetime`
component; `_import_anthropic` / `_import_openai` (any `_import_*`); a target named in
`EDGES` (below); module constants (an ALL_CAPS attribute, however spelled, is a tunable,
not a method); and anything outside `pyrite.services` / `pyrite.storage` (config
loading, `load_config`, is how a test points the CLI at its world).

`EDGES` is the registry of named edges: fully dotted target -> one line saying which
external I/O it is and why a test cannot seed it instead. Matching is exact on the dotted
target, after the walk resolves aliases (`patch.object(GitService, "fork_repo")`, and
`repo_service.GitService.fork_repo`, a name one module imported from another, both
become `pyrite.services.git_service.GitService.fork_repo`). The rules:

  1. An edge is a method or function whose job is one external I/O operation and nothing
     else: one HTTP request, one git subprocess, one credential read, one OS file call,
     one model load. A function that also holds Pyrite logic (which status counts as
     live, which token wins, what to refuse) is not an edge; patching it would hide that
     logic. Extract the I/O into a thin function and register that one.
  2. A fault-injection edge is allowed (an OS rename or write that fails on cue): a real
     disk cannot be made to fail when the test wants.
  3. The registry only grows by a pull request that names the edge in its description.
     The ceiling and the ALLOWLIST below are separate rules and stay as they are.

Scanned: every `.py` under `tests/` and `extensions/*/tests`, so `conftest.py` and the seed
helpers are covered too (a patch hidden in a shared helper counts when the file drives).

Known limit: the allowlist and its pinned size live in this file, which a PR can edit; review
is the check on both. Each listed file carries its patch count, so a new patch in a listed file
fails too (#1075).

The failure message names the fix:
`file:line: <target> is the service behind <entry point>; seed the world instead`.

`ALLOWLIST` names every file that violates today, with its patch count and one line of why.
It can only shrink, and every number is pinned with `==` (as in tests/test_layer_boundaries.py):
a file not on it that violates fails, a file on it that no longer violates fails (remove the
line), a file whose count differs fails (lower it; raising it is the fix this rule refuses), and
`ALLOWLIST_SIZE` must equal the number of files.
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
#: The named edges (rules in the docstring): fully dotted target -> which external I/O it
#: is, and why a test cannot seed it instead.
EDGES: dict[str, str] = {
    "pyrite.services.embedding_service.is_available": (
        "model install probe (imports the ML library); a test cannot install or remove it"
    ),
    "pyrite.services.embedding_service._load_model": (
        "model load from disk or network; a test needing a failure returns a failing model"
    ),
    "pyrite.services.embedding_service._MODEL_CACHE": (
        "process-wide cache of loaded models; the test swaps it for an empty one"
    ),
    "pyrite.services.git_service.GitService.add_remote": (
        "one `git remote add` subprocess; a test cannot make the remote step fail on cue"
    ),
    "pyrite.services.git_service.GitService.fork_repo": (
        "one GitHub API request (a fork); needs the network and a real token"
    ),
    "pyrite.services.url_checker.URLChecker._urlopen": (
        "one HEAD request via urlopen; needs the network, status mapping stays in check_url"
    ),
    "pyrite.services.id_pin_service._rename": (
        "one os.rename (fault injection: a real disk cannot be made to fail, or a link "
        "swapped in right before the rename, on cue)"
    ),
}
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
#: path (relative to the repo root) -> (patches in it today, why it is listed). Both are
#: pinned with `==`: a new patch in a listed file, or one removed, fails until the count is edited.
ALLOWLIST: dict[str, tuple[int, str]] = {
    "extensions/social/tests/test_social.py": (
        1,
        "behind PluginContext: kb_service.KBService.create_entry",
    ),
    "extensions/software-kb/tests/test_software_kb.py": (
        11,
        "behind CliRunner, PluginContext: kb_service.KBService, kb_service.KBService.create_entry",
    ),
    "extensions/zettelkasten/tests/test_zettelkasten.py": (
        1,
        "behind CliRunner: kb_service.KBService.create_entry",
    ),
    "tests/test_admin_cli.py": (
        2,
        "behind CliRunner: storage.repository.KBRepository._get_file_path",
    ),
    "tests/test_anonymous_and_missing_kb_writes.py": (
        1,
        "behind TestClient: clipper.ClipperService.clip_url",
    ),
    "tests/test_auth_bootstrap.py": (
        3,
        "behind CliRunner, TestClient: auth_service.AuthService._verify_password, auth_service.AuthService.login",
    ),
    "tests/test_auth_endpoints.py": (1, "behind TestClient: auth_service.AuthService.login"),
    "tests/test_auth_user_management.py": (
        1,
        "behind TestClient: auth_service.AuthService.set_role",
    ),
    "tests/test_authz_authorize.py": (
        1,
        "behind TestClient: access_policy.AccessPolicy.read_scope",
    ),
    "tests/test_cli_finds_db_registered_kbs.py": (
        3,
        "behind CliRunner: storage.database.PyriteDB.close, storage.index.IndexManager.check_health",
    ),
    "tests/test_cli_json_output.py": (
        6,
        "behind CliRunner: search_service.SearchService.search, storage.database.PyriteDB.close ...",
    ),
    "tests/test_cli_outcome_write_path.py": (2, "behind CliRunner: kb_service.KBService.add_link"),
    "tests/test_default_output_is_the_result.py": (
        13,
        "behind CliRunner, PyriteMCPServer, TestClient, _dispatch_tool: embedding_service.EmbeddingService._get_model, embedding_service.EmbeddingService.embed_all ...",
    ),
    "tests/test_embed_queue_debt_is_honest.py": (
        5,
        "behind TestClient: embedding_worker.EmbeddingWorker, embedding_worker.EmbeddingWorker._get_embedding_svc",
    ),
    "tests/test_fields_single_entry.py": (2, "behind CliRunner: kb_service.KBService.get_entry"),
    "tests/test_ids_commands.py": (
        12,
        "behind CliRunner: id_pin_service._rename, id_pin_service._write_pin ...",
    ),
    "tests/test_json_stdout.py": (
        2,
        "behind CliRunner: url_checker.URLChecker.check_url, url_checker.URLChecker.collect_urls",
    ),
    "tests/test_kb_bulk_create_description.py": (
        1,
        "behind PyriteMCPServer, _dispatch_tool: kb_service.build_entry",
    ),
    "tests/test_kb_export_read_scoping.py": (
        1,
        "behind TestClient: export_service.ExportService.export_kb_to_repo",
    ),
    "tests/test_kb_policy_availability.py": (
        1,
        "behind TestClient: ephemeral_service.save_config, repo_service.save_config ...",
    ),
    "tests/test_kb_scoped_writes.py": (1, "behind TestClient: clipper.ClipperService.clip_url"),
    "tests/test_mcp_server.py": (
        2,
        "behind PyriteMCPServer, _dispatch_tool: <unresolved>:_run_hooks",
    ),
    "tests/test_one_reconcile.py": (
        3,
        "behind CliRunner: storage.index._hash_file, storage.repository.KBRepository.load_entry_from_file",
    ),
    "tests/test_private_kb_read_scoping.py": (
        1,
        "behind TestClient: search_service.SearchService.search",
    ),
    "tests/test_qa_assessment.py": (
        8,
        "behind CliRunner, PyriteMCPServer: pyrite.cli.context.PyriteDB, qa_service.QAService._maybe_create_task ...",
    ),
    "tests/test_qa_commands_run.py": (1, "behind CliRunner: url_checker.URLChecker.check_url"),
    "tests/test_repo_subscribe_never_adopts_existing_kb.py": (
        1,
        "behind TestClient: git_service.GitService.clone_with_code, git_service.GitService.fork_repo",
    ),
    "tests/test_requested_effect_exit_code.py": (
        7,
        "behind CliRunner: kb_service.KBService.add_link, kb_service.KBService.reconcile_templated_subdirectories ...",
    ),
    "tests/test_save_config_never_drops_unnamed_kbs.py": (
        2,
        "behind CliRunner, TestClient: repo_service.GitService.clone_with_code, repo_service.GitService.fork_repo",
    ),
    "tests/test_search_contract.py": (
        21,
        "behind CliRunner, PyriteMCPServer, _dispatch_tool: embedding_service.EmbeddingService.has_embeddings, embedding_service.EmbeddingService.search_similar ...",
    ),
    "tests/test_server_write_embedding_debt.py": (
        2,
        "behind TestClient: embedding_worker.EmbeddingWorker._get_embedding_svc",
    ),
    "tests/test_untrusted_local_config.py": (
        3,
        "behind CliRunner, TestClient: embedding_service.EmbeddingService.__init__",
    ),
    "tests/test_websocket_credential_lifetime.py": (
        1,
        "behind TestClient: clipper.ClipperService.clip_url",
    ),
    "tests/test_websocket_kb_policy_lifetime.py": (
        1,
        "behind TestClient: repo_service.save_config",
    ),
    "tests/test_websocket_scoping.py": (1, "behind TestClient: clipper.ClipperService.clip_url"),
    "tests/test_write_pipeline.py": (
        9,
        "behind CliRunner, PyriteMCPServer, TestClient, _dispatch_tool: clipper.ClipperService.clip_url, kb_service.KBService._prepare_and_save",
    ),
    "tests/conftest.py": (
        2,
        "the console_width fixture sets Rich's terminal width on the CLI consoles (a terminal edge, unresolved by the walk)",
    ),
    "tests/test_cli_surfaces_through_services.py": (
        1,
        "patch target held in a variable; unresolved",
    ),
    "tests/test_doc_write_as_patch.py": (
        3,
        "patches a class held in a variable (cls.emit); unresolved",
    ),
    "tests/test_embedding_prewarm.py": (
        2,
        "patches _get_model on a local service instance behind the REST client; unresolved",
    ),
    "tests/test_ephemeral_kb_name_is_not_a_path.py": (
        2,
        "patches execute_sql on a local service db; unresolved",
    ),
    "tests/test_get_reads_the_file.py": (
        1,
        "spies on os/glob/Path directory-walk functions to count walks (a filesystem process edge; the owners come from a loop, unresolved)",
    ),
    "tests/test_index_worker.py": (1, "patches IndexManager behind the REST client"),
    "tests/test_link_asymmetric.py": (1, "patches close on a fixture-held db; unresolved"),
    "tests/test_mcp_finders.py": (1, "patches a method named by a variable; unresolved"),
    "tests/test_mcp_setup_reads_back.py": (21, "21 patches of names held in variables; unresolved"),
    "tests/test_plugin_integration.py": (
        2,
        "patches get_hooks_for_kb on a local registry; unresolved",
    ),
    "tests/test_repo_error_disclosure.py": (
        12,
        "unit tests of GitService's error text patch GitService in a file whose other classes drive REST via make_client; split the file (Q8) or migrate",
    ),
    "tests/test_shipped_schemas_accept_workflows.py": (
        1,
        "patch target built with an f-string; unresolved",
    ),
}
ALLOWLIST_SIZE = 49  # lower it with every file removed; never raise it


def _is_service_path(dotted: str) -> bool:
    return any(dotted == r or dotted.startswith(r + ".") for r in SERVICE_ROOTS)


@lru_cache(maxsize=None)  # noqa: UP033
def _module_imports(module: str) -> dict[str, str]:
    """name -> full dotted source, for each `from x import y` in a pyrite module."""
    path = ROOT / (module.replace(".", "/") + ".py")
    if not path.exists():
        path = ROOT / module.replace(".", "/") / "__init__.py"
    if not path.exists():
        return {}
    names: dict[str, str] = {}
    package = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom):
            continue
        # `from ..storage.repository import X` is `pyrite.storage.repository`
        base = package[: len(package) - (node.level - 1)] if node.level else []
        source = ".".join([*base, *([node.module] if node.module else [])])
        for a in node.names:
            names[a.asname or a.name] = f"{source}.{a.name}"
    return names


@lru_cache(maxsize=None)  # noqa: UP033
def _imported_from_services(module: str) -> frozenset[str]:
    """Names a pyrite module imports from services/storage (or defines there)."""
    return frozenset(
        name for name, full in _module_imports(module).items() if _is_service_path(full)
    )


def _canonical(dotted: str) -> str:
    """Follow a name one pyrite module imported from another to where it is defined:
    `pyrite.services.repo_service.GitService.fork_repo` is
    `pyrite.services.git_service.GitService.fork_repo`."""
    parts = dotted.split(".")
    for i in range(len(parts) - 1, 1, -1):
        module = ".".join(parts[:i])
        if module.startswith("pyrite"):
            source = _module_imports(module).get(parts[i])
            if source:
                return ".".join([source, *parts[i + 1 :]])
    return dotted


def _edge(dotted: str) -> bool:
    parts = dotted.split(".")
    return (
        any(p in EDGE_COMPONENTS for p in parts)
        or parts[-1].startswith(EDGE_ATTR_PREFIXES)
        or dotted in EDGES
        or _canonical(dotted) in EDGES
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
    if attr.isupper() or _edge(f"{full}.{attr}"):
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
            "patch('pyrite.cli.search_commands.project_fields')\n",
            "pyrite.cli.search_commands.project_fields",
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


GIT = "from typer.testing import CliRunner\nfrom unittest.mock import patch\n"


@pytest.mark.control(reason="Pins the registry on planted sources, independent of the tree")
@pytest.mark.parametrize(
    "source",
    [
        # string target, canonical module
        GIT + "patch('pyrite.services.git_service.GitService.fork_repo')\n",
        GIT + "patch('pyrite.services.git_service.GitService.add_remote')\n",
        GIT + "patch('pyrite.services.id_pin_service._rename')\n",
        GIT + "patch('pyrite.services.embedding_service._load_model')\n",
        # a name one module imported from another resolves to where it is defined
        GIT + "patch('pyrite.services.repo_service.GitService.fork_repo')\n",
        # patch.object on an imported class, and on a module
        GIT + "from pyrite.services.git_service import GitService\n"
        "patch.object(GitService, 'fork_repo')\n",
        GIT + "from pyrite.services.repo_service import GitService\n"
        "patch.object(GitService, 'add_remote')\n",
        GIT + "import pyrite.services.id_pin_service as pin\npatch.object(pin, '_rename')\n",
        "from typer.testing import CliRunner\n"
        "from pyrite.services import git_service\n"
        "def t(monkeypatch):\n"
        "    monkeypatch.setattr(git_service.GitService, 'fork_repo', None)\n",
    ],
)
def test_a_registered_edge_may_be_patched(source):
    assert violations(source) == []


def test_every_registered_edge_names_why_and_is_a_real_target():
    assert all(why.strip() for why in EDGES.values())
    for target in EDGES:
        module = target
        while module and not (ROOT / (module.replace(".", "/") + ".py")).exists():
            module = module.rpartition(".")[0]
        assert module.startswith("pyrite.services"), f"{target}: no module under pyrite.services"
        assert target.rpartition(".")[2] in (ROOT / (module.replace(".", "/") + ".py")).read_text(
            encoding="utf-8"
        ), f"{target}: the module does not define it"


@pytest.mark.control(reason="Pins that the registry is exact: a neighbour of an edge still fails")
@pytest.mark.parametrize(
    ("source", "target"),
    [
        # the same class, a method that is Pyrite logic
        (
            GIT + "patch('pyrite.services.git_service.GitService.clone_with_code')\n",
            "pyrite.services.git_service.GitService.clone_with_code",
        ),
        (
            GIT + "patch('pyrite.services.repo_service.GitService.clone_with_code')\n",
            "pyrite.services.repo_service.GitService.clone_with_code",
        ),
        (
            GIT + "from pyrite.services.git_service import GitService\n"
            "patch.object(GitService, 'create_pull_request')\n",
            "pyrite.services.git_service.GitService.create_pull_request",
        ),
        # the same module, the other function the seam sits beside
        (
            GIT + "patch('pyrite.services.id_pin_service._write_pin')\n",
            "pyrite.services.id_pin_service._write_pin",
        ),
        # candidates that hold Pyrite logic, refused on reading the code
        (
            GIT + "from pyrite.services.url_checker import URLChecker\n"
            "patch.object(URLChecker, 'check_url')\n",
            "pyrite.services.url_checker.URLChecker.check_url",
        ),
        (
            GIT + "patch('pyrite.services.repo_service.RepoService._get_token')\n",
            "pyrite.services.repo_service.RepoService._get_token",
        ),
        # a registered name on another class or module is not the registered edge
        (
            GIT + "patch('pyrite.services.kb_service.KBService.fork_repo')\n",
            "pyrite.services.kb_service.KBService.fork_repo",
        ),
        (
            GIT + "patch('pyrite.services.llm_rubric_evaluator.is_available')\n",
            "pyrite.services.llm_rubric_evaluator.is_available",
        ),
    ],
)
def test_a_neighbour_of_an_edge_is_still_flagged(source, target):
    assert [t for _, t in violations(source)] == [target]


# --------------------------------------------------------------- the ratchet


def ratchet_problems(
    found: dict[str, list], allowlist: dict[str, tuple[int, str]], size: int
) -> list[str]:
    """Everything wrong with the allowlist against what the scan found; [] when exact."""
    problems = []
    if len(allowlist) != size:
        problems.append(
            f"ALLOWLIST lists {len(allowlist)} files but the pinned size {size}. Removing a "
            "file: lower ALLOWLIST_SIZE to match. Adding one is not the fix; seed the world "
            "in the new test instead of listing it"
        )
    for path, (count, why) in sorted(allowlist.items()):
        if path not in found:
            problems.append(f"{path} no longer violates; remove it from ALLOWLIST")
        elif len(found[path]) > count:
            problems.append(
                f"{path} has {len(found[path])} service patches, ALLOWLIST says {count}; "
                "seed the world instead of adding one"
            )
        elif len(found[path]) < count:
            problems.append(
                f"{path} has {len(found[path])} service patches, ALLOWLIST says {count}; "
                "lower the count"
            )
        if not why.strip():
            problems.append(f"{path} says why it is listed: give a reason")
    return problems


def _hits(n: int) -> list[tuple[int, str, str]]:
    return [(i + 1, "pyrite.services.x.X", "TestClient") for i in range(n)]


@pytest.mark.control(reason="Pins the ratchet on planted data, independent of the tree")
class TestTheRatchetHasNoSlack:
    LISTED = {"tests/test_a.py": (2, "why a"), "tests/test_b.py": (1, "why b")}

    def test_a_tree_that_matches_passes(self):
        found = {"tests/test_a.py": _hits(2), "tests/test_b.py": _hits(1)}
        assert ratchet_problems(found, self.LISTED, size=2) == []

    def test_listing_one_more_file_fails(self):
        listed = {**self.LISTED, "tests/test_c.py": (1, "why c")}
        found = {p: _hits(n) for p, (n, _) in listed.items()}
        problems = ratchet_problems(found, listed, size=2)
        assert any("3 files" in p and "pinned size 2" in p for p in problems)

    def test_one_more_patch_in_a_listed_file_fails(self):
        found = {"tests/test_a.py": _hits(3), "tests/test_b.py": _hits(1)}
        problems = ratchet_problems(found, self.LISTED, size=2)
        assert any("tests/test_a.py" in p and "3" in p and "2" in p for p in problems)

    def test_one_fewer_patch_asks_for_the_count_to_be_lowered(self):
        found = {"tests/test_a.py": _hits(1), "tests/test_b.py": _hits(1)}
        problems = ratchet_problems(found, self.LISTED, size=2)
        assert any("tests/test_a.py" in p and "lower" in p for p in problems)

    def test_a_file_that_no_longer_violates_must_be_removed(self):
        problems = ratchet_problems({"tests/test_a.py": _hits(2)}, self.LISTED, size=2)
        assert any("tests/test_b.py" in p and "remove" in p for p in problems)

    def test_every_entry_says_why(self):
        listed = {"tests/test_a.py": (1, "")}
        problems = ratchet_problems({"tests/test_a.py": _hits(1)}, listed, size=1)
        assert any("why" in p for p in problems)


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


@pytest.mark.guard
def test_no_test_mocks_the_service_behind_the_entry_point_it_drives():
    found = _scan()
    new = [
        _message(path, line, tgt, eps)
        for path, hits in found.items()
        if path not in ALLOWLIST
        for line, tgt, eps in hits
    ]
    assert not new, "\n" + "\n".join(new)


@pytest.mark.guard
def test_the_allowlist_matches_the_tree_exactly():
    assert ratchet_problems(_scan(), ALLOWLIST, ALLOWLIST_SIZE) == []
    assert all((ROOT / p).exists() for p in ALLOWLIST)

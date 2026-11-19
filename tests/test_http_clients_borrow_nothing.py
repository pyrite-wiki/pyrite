"""Every outbound HTTP client Pyrite builds borrows nothing of the server account
it was not granted (ADR-0052 N2, D-U2).

Property: an HTTP request Pyrite makes for a network caller uses no proxy,
netrc or other ambient setting of the account Pyrite runs as, except where the
operator configured the destination and the egress: it either goes through
the one constructor that decides from the reach (``pyrite.utils.egress``,
which uses the operator's ``git_proxy`` grant), or is built with the account's
environment switched off (httpx ``trust_env=False``, urllib
``ProxyHandler({})``), or is listed in `REVIEWED` with why the account's
egress is the operator's own choice for that destination.

The scan reads every ``.py`` in ``pyrite/`` and ``extensions/`` (tests
excluded) for these spellings, under any import alias: ``httpx.Client``,
``httpx.AsyncClient`` and the module-level ``httpx.get/post/...`` functions;
``urllib.request.urlopen`` and ``build_opener``; ``requests.*``;
``aiohttp.ClientSession``; ``urllib3.PoolManager``/``ProxyManager``; and the
LLM SDK clients (``Anthropic``, ``AsyncAnthropic``, ``OpenAI``,
``AsyncOpenAI``), which build an httpx client of their own that reads the
environment. Not seen: a client built through ``getattr`` or a library this
list does not name.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The one constructor: it decides trust_env and the proxy itself.
CONSTRUCTOR = "pyrite/utils/egress.py"

#: (file, enclosing function) -> why the account's egress is acceptable there.
REVIEWED: dict[tuple[str, str], str] = {
    ("pyrite/services/llm_service.py", "_get_anthropic_client"): (
        "operator-configured provider (ai_provider/ai_api_base) with the operator's ai_api_key; "
        "the account's egress is the operator's own choice for it (groom: operator egress)"
    ),
    ("pyrite/services/llm_service.py", "_get_openai_client"): (
        "operator-configured provider (ai_provider/ai_api_base) with the operator's ai_api_key"
    ),
    ("pyrite/services/llm_service.py", "test_connection"): (
        "GET <ai_api_base>/api/tags of the operator's own Ollama; carries no credential"
    ),
    ("pyrite/services/query_expansion_service.py", "_get_client"): (
        "operator-configured provider with the operator's ai_api_key (as llm_service)"
    ),
}

_HTTPX_CLIENTS = {"Client", "AsyncClient"}
_HTTPX_FUNCTIONS = {"get", "post", "put", "patch", "delete", "head", "options", "request", "stream"}
_SDK_CLIENTS = {"Anthropic", "AsyncAnthropic", "OpenAI", "AsyncOpenAI"}


def _dotted(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _imports(tree: ast.AST) -> dict[str, str]:
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names[a.asname or a.name.split(".")[0]] = (
                    a.name if a.asname else a.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                names[a.asname or a.name] = f"{node.module}.{a.name}"
    return names


def _client_kind(call: ast.Call, imports: dict[str, str]) -> str | None:
    """What client a call builds, by its fully resolved name, or None."""
    name = _dotted(call.func)
    if name is None:
        return None
    head, _, rest = name.partition(".")
    full = f"{imports[head]}.{rest}" if head in imports and rest else imports.get(head, name)
    module, _, attr = full.rpartition(".")
    if module == "httpx" and attr in _HTTPX_CLIENTS:
        return "httpx-client"
    if module == "httpx" and attr in _HTTPX_FUNCTIONS:
        return "httpx-function"
    if full in ("urllib.request.urlopen",):
        return "urlopen"
    if full == "urllib.request.build_opener":
        return "opener"
    if module == "requests" or full.startswith("requests."):
        return "requests"
    if full in ("aiohttp.ClientSession", "urllib3.PoolManager", "urllib3.ProxyManager"):
        return "other-client"
    if attr in _SDK_CLIENTS:
        return "sdk"
    return None


def _closed(call: ast.Call, kind: str) -> bool:
    """Whether the call itself switches the account's environment off."""
    if kind == "httpx-client":
        return any(
            k.arg == "trust_env" and isinstance(k.value, ast.Constant) and k.value.value is False
            for k in call.keywords
        )
    if kind == "opener":
        return any(
            _dotted(a.func) in ("urllib.request.ProxyHandler", "ProxyHandler")
            and len(a.args) == 1
            and isinstance(a.args[0], ast.Dict)
            and not a.args[0].keys
            for a in call.args
            if isinstance(a, ast.Call)
        )
    return False


def clients(source: str):
    """(line, kind, enclosing function, closed) for every client built."""
    tree = ast.parse(source)
    imports = _imports(tree)

    def visit(node, func):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield from visit(child, child.name)
                continue
            if isinstance(child, ast.Call):
                kind = _client_kind(child, imports)
                if kind:
                    yield child.lineno, kind, func, _closed(child, kind)
            yield from visit(child, func)

    yield from visit(tree, "<module>")


def violations(source: str, relpath: str) -> list[str]:
    if relpath == CONSTRUCTOR:
        return []
    return [
        f"{relpath}:{line}: {kind} in {func} borrows the account's environment"
        for line, kind, func, closed in clients(source)
        if not closed and (relpath, func) not in REVIEWED
    ]


def _source_files() -> list[Path]:
    out = []
    for root in ("pyrite", "extensions"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            rel = path.relative_to(REPO_ROOT)
            if "tests" in rel.parts or ".venv" in rel.parts or "node_modules" in rel.parts:
                continue
            out.append(path)
    return out


def test_every_http_client_borrows_nothing_of_the_account():
    bad = []
    for path in _source_files():
        bad += violations(path.read_text(), str(path.relative_to(REPO_ROOT)))
    assert bad == []


@pytest.mark.control(reason="keeps the reviewed list honest; no stale entry existed before")
def test_every_reviewed_site_still_builds_a_client():
    live = set()
    for path in _source_files():
        rel = str(path.relative_to(REPO_ROOT))
        live |= {(rel, func) for _, _, func, closed in clients(path.read_text()) if not closed}
    assert set(REVIEWED) - live == set()


@pytest.mark.control(reason="the scan itself: it must keep finding the known clients")
def test_the_scan_finds_the_known_clients():
    found = {}
    for path in _source_files():
        rel = str(path.relative_to(REPO_ROOT))
        for _, kind, func, closed in clients(path.read_text()):
            found[(rel, func)] = (kind, closed)
    assert found[("pyrite/services/clipper.py", "clip_url")] == ("httpx-client", True)
    assert found[("pyrite/services/url_checker.py", "_urlopen")][1] is True
    assert found[(CONSTRUCTOR, "github_client")][0] == "httpx-client"


@pytest.mark.control(
    reason="exercises the checker on planted source; the tree check is the red test"
)
@pytest.mark.parametrize(
    "source",
    [
        "import httpx\ndef f():\n    httpx.Client()",
        "import httpx\ndef f():\n    httpx.AsyncClient(trust_env=True)",
        "from httpx import Client as C\ndef f():\n    C()",
        "import httpx\ndef f():\n    httpx.get('https://api.github.com/user')",
        "import urllib.request\ndef f():\n    urllib.request.urlopen('https://x')",
        "from urllib.request import urlopen\ndef f():\n    urlopen('https://x')",
        "import urllib.request\ndef f():\n    urllib.request.build_opener()",
        "import requests\ndef f():\n    requests.get('https://x')",
        "import anthropic\ndef f():\n    anthropic.Anthropic()",
    ],
)
def test_a_planted_client_is_found(source):
    assert violations(source, "pyrite/services/planted.py") != []


@pytest.mark.control(reason="the shapes the rule allows")
@pytest.mark.parametrize(
    "source",
    [
        "import httpx\ndef f():\n    httpx.Client(trust_env=False)",
        "import urllib.request\ndef f():\n"
        "    urllib.request.build_opener(urllib.request.ProxyHandler({}))",
        "from pyrite.utils.egress import github_client\ndef f():\n    github_client(proxy=None)",
    ],
)
def test_a_closed_client_is_allowed(source):
    assert violations(source, "pyrite/services/planted.py") == []

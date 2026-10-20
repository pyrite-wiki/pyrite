"""One inventory of Pyrite's entry points, shared by the structural tests.

ADR-0037 asks that the layer ratchet (#380, ``test_layer_boundaries.py``)
and the coming access-policy guard (ADR-0037 §5) walk **one** list of what
a surface is, so the two can never disagree about it. This module is that
list. It enumerates what runs a request, from the real objects rather than
from a hand-kept table:

- ``rest_operations()``: every ``APIRoute`` of ``create_app()`` -- ``/api``,
  ``/auth``, the SEO and site routes -- one ``EntryPoint`` per method.
- ``mcp_tools()``: every tool of an admin-tier ``PyriteMCPServer`` (the
  admin tier registers every tool, core and plugin).

What neither walk sees is named in ``NON_ROUTE_ENTRY_POINTS`` (the same two
surfaces ``test_read_scoping_is_structural.py`` records), and the CLI is a
set of modules rather than a registry; the layer ratchet covers it by path.
"""

from __future__ import annotations

import inspect
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Entry points that are not an APIRoute or an MCP tool, with what covers them.
NON_ROUTE_ENTRY_POINTS = {
    "/mcp": "a Mount (the MCP app); its tools are enumerated by mcp_tools()",
    "/ws": "a WebSocketRoute; pyrite/server/websocket.py, scanned by path",
}


@dataclass(frozen=True)
class EntryPoint:
    surface: str  # "rest" | "mcp"
    name: str  # "GET /api/entries" or the tool name
    handler: Callable

    @property
    def source(self) -> Path | None:
        """The file the handler is defined in, relative to the repo root when inside it."""
        fn = inspect.unwrap(self.handler)
        try:
            path = Path(inspect.getsourcefile(fn) or "").resolve()
        except TypeError:
            return None
        try:
            return path.relative_to(REPO_ROOT)
        except ValueError:
            return path

    @property
    def qualname(self) -> str:
        return inspect.unwrap(self.handler).__qualname__

    @property
    def module(self) -> str:
        """The dotted module the handler is defined in -- independent of where
        the package is installed, unlike ``source``."""
        return inspect.unwrap(self.handler).__module__


def plugin_packages() -> set[str]:
    """Top-level packages of the installed Pyrite plugins (the ``pyrite.plugins``
    entry points), however they are installed -- editable from any checkout,
    or as wheels."""
    from importlib.metadata import entry_points

    return {ep.value.split(":")[0].split(".")[0] for ep in entry_points(group="pyrite.plugins")}


def _walk_routes(routes, prefix: str = "") -> list[tuple[str, object]]:
    """(full path, APIRoute) for every route, through included routers.

    FastAPI wraps an ``include_router`` in an ``_IncludedRouter`` whose
    children may or may not already carry the router's prefix; the same
    walk as ``test_read_scoping_is_structural._api_routes``.
    """
    from fastapi.routing import APIRoute

    found = []
    for route in routes:
        if type(route).__name__ == "_IncludedRouter":
            sub = route.original_router
            own = sub.prefix or ""
            children = _walk_routes(sub.routes, "")
            add = "" if (own and all(p.startswith(own) for p, _ in children)) else own
            found += [(prefix + add + p, r) for p, r in children]
        elif isinstance(route, APIRoute):
            found.append((prefix + route.path, route))
    return found


def rest_operations(app=None) -> list[EntryPoint]:
    """Every REST operation of ``app`` (default: ``create_app()``)."""
    if app is None:
        from pyrite.server.api import create_app

        app = create_app()
    return [
        EntryPoint("rest", f"{method} {path}", route.endpoint)
        for path, route in _walk_routes(app.routes)
        for method in sorted(route.methods or ())
    ]


@contextmanager
def mcp_server() -> Iterator:
    """An admin-tier ``PyriteMCPServer`` over a throwaway KB and index."""
    from pyrite.config import KBConfig, PyriteConfig, Settings
    from pyrite.server.mcp_server import PyriteMCPServer

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "inventory-kb").mkdir()
        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name="inventory-kb", path=tmp / "inventory-kb", kb_type="generic")
            ],
            settings=Settings(index_path=tmp / "index.db"),
        )
        server = PyriteMCPServer(config=config, tier="admin")
        try:
            yield server
        finally:
            server.close()


def mcp_tools(server) -> list[EntryPoint]:
    """Every tool ``server`` registers, core and plugin."""
    return [EntryPoint("mcp", name, meta["handler"]) for name, meta in server.tools.items()]

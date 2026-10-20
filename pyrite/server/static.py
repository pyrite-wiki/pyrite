"""
Static file serving for the Pyrite web application.

Serves the built SvelteKit app from web/dist/ with SPA fallback:
- /_app/* → the build's hashed JS, CSS and images
- /api/*, /mcp, /auth, /ws → the API's; never answered by the SPA shell
- /robots.txt, /sitemap.xml, /site/*, /viewer/* → 404 (removed in #953) unless
  the build ships the file
- Everything else → index.html (SPA client-side routing)

The app's responses carry a Content-Security-Policy built here
(``build_site_csp``), which the operator extends with
``settings.site_csp_extra``.
"""

import logging
import re
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketClose

logger = logging.getLogger(__name__)

# Defence in depth for the app's own pages: no inline script and no
# event-handler attributes run, even if a sanitizer bypass ever lets one
# into stored content. Inline styles stay allowed. Operators extend it with
# `settings.site_csp_extra` (see build_site_csp).
_SITE_CSP_DIRECTIVES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("default-src", ("'self'",)),
    ("script-src", ("'self'",)),
    ("style-src", ("'self'", "'unsafe-inline'", "https://fonts.googleapis.com")),
    ("font-src", ("'self'", "https://fonts.gstatic.com")),
    # `https:` (any https host), not just `'self'`/`data:`: an entry body can
    # embed `![](https://example.com/x.png)` and an operator's branding
    # `logo_url`/`og_image_url` is commonly an external https URL. An image
    # cannot execute script, so this widens what renders, not what runs.
    ("img-src", ("'self'", "data:", "https:")),
    ("connect-src", ("'self'",)),
    ("object-src", ("'none'",)),
    ("base-uri", ("'self'",)),
    ("form-action", ("'self'",)),
    ("frame-ancestors", ("'self'",)),
)
_CSP_NAME = re.compile(r"^[a-z][a-z-]*$")
# A source expression: printable ASCII, no separators (";" "," or space).
_CSP_SOURCE = re.compile(r"^[\x21-\x2b\x2d-\x3a\x3c-\x7e]+$")


# Directives site_csp_extra may not widen: a plugin/embed source or a <base>
# href would undo the point of the policy for content pages.
_CSP_LOCKED = frozenset({"object-src", "base-uri"})
# Sources that let injected content run as script; allowed, but warned about.
_CSP_WEAKENING = frozenset({"'unsafe-inline'", "'unsafe-eval'", "*"})
_CSP_SCRIPT_DIRECTIVES = frozenset({"script-src", "default-src"})


@lru_cache(maxsize=16)
def build_site_csp(extra: str = "") -> str:
    """The app's Content-Security-Policy, with ``extra`` merged in.

    ``extra`` is CSP syntax ("script-src https://a.example; connect-src ...").
    Its sources are appended to the built-in directive of the same name, or
    a new directive is added; a directive with no value (for example
    ``upgrade-insecure-requests``) is added as is. A malformed directive
    (bad name, or a source with a separator or control character) is
    skipped with a warning, so a typo can neither break the header nor
    inject another one. ``object-src`` and ``base-uri`` cannot be extended
    (skipped with a warning); ``'unsafe-inline'``, ``'unsafe-eval'`` or
    ``*`` on ``script-src``/``default-src`` is applied but warned about,
    since it would let a script planted in KB content run.
    """
    merged: dict[str, list[str]] = {name: list(srcs) for name, srcs in _SITE_CSP_DIRECTIVES}
    for raw in (extra or "").split(";"):
        tokens = raw.strip().split(" ")
        tokens = [t for t in tokens if t]
        if not tokens:
            continue
        name, sources = tokens[0], tokens[1:]
        if (
            not _CSP_NAME.match(name)
            or not all(_CSP_SOURCE.match(src) for src in sources)
            or any(c in raw for c in "\r\n\t\x00")
        ):
            logger.warning("Ignoring malformed site_csp_extra directive: %r", raw.strip())
            continue
        if name in _CSP_LOCKED and sources:
            logger.warning(
                "Ignoring site_csp_extra directive %r: %s cannot be extended",
                raw.strip(),
                name,
            )
            continue
        if name in _CSP_SCRIPT_DIRECTIVES:
            for src in sources:
                if src in _CSP_WEAKENING:
                    logger.warning(
                        "site_csp_extra adds %s to %s: scripts planted in KB content "
                        "could run in the app; prefer a host or a 'sha256-...' hash",
                        src,
                        name,
                    )
        target = merged.setdefault(name, [])
        target.extend(src for src in sources if src not in target)
    return "; ".join(" ".join([name, *srcs]) for name, srcs in merged.items())


# The hash SvelteKit's build emits in its own <meta http-equiv> CSP tag for
# the inline bootstrap script (`kit.csp.mode: 'hash'` in web/svelte.config.js).
# A nonce is forbidden for prerendered pages (adapter-static prerenders
# everything) and is unsafe there anyway; a hash changes on every build
# because the bootstrap embeds a per-build-randomised `__sveltekit_<id>`
# variable name, so it cannot be hardcoded here -- it is read out of the
# build's own output instead.
_BUILD_SCRIPT_HASH = re.compile(r"'sha256-[A-Za-z0-9+/]+=*'")


def _spa_script_hash_extra(index_content: str) -> str:
    """The ``site_csp_extra``-style fragment adding the build's own inline
    script hash(es) to ``script-src``, or ``""`` if none were found.

    If this is ever empty for a real build (an adapter/config regression),
    the header falls back to a bare ``script-src 'self'`` with no hash --
    that blocks SvelteKit's own bootstrap script and the app loads to a
    blank page with no visible error. ``mount_static`` warns at mount time
    when this happens; see the call site.
    """
    hashes = _BUILD_SCRIPT_HASH.findall(index_content)
    if not hashes:
        return ""
    return "script-src " + " ".join(hashes)


def _spa_csp_headers(index_content: str, site_csp_extra: str = "") -> dict[str, str]:
    """Security headers for an SPA ``index.html`` response (P-B1).

    The baseline policy, plus the build's own inline-script hash(es) added
    to ``script-src`` so the bootstrap SvelteKit itself emits still runs --
    a header CSP and a page's ``<meta http-equiv>`` CSP combine with AND
    semantics, so a header ``script-src 'self'`` with no hash would block it
    even though the meta tag allows it. ``site_csp_extra`` is the operator's
    per-request setting (``build_site_csp``); both are merged into one
    ``script-src`` extra so an operator's directive and the build's own
    hash don't clobber each other.
    """
    parts = [p for p in (_spa_script_hash_extra(index_content), site_csp_extra) if p]
    extra = "; ".join(parts)
    return {
        "Content-Security-Policy": build_site_csp(extra),
        "X-Content-Type-Options": "nosniff",
    }


#: Prefixes the API owns. With the SPA mounted, a request to one of these (or
#: to any path under it) that no real route claims must not fall through to the
#: SPA's catch-all and answer 200 with its HTML shell (#538).
#: `web/src/routes`'s top-level route names must never start with one of these
#: (tests/test_static_routes_with_a_built_dist.py checks it). They are a
#: plain Starlette `Route` and a `Mount`, not `APIRoute`s, so the access-policy
#: guard's inventory (which walks `APIRoute`s) does not see them.
_API_SHAPED_PREFIXES: tuple[str, ...] = ("/api", "/mcp", "/auth", "/ws")

_ALL_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")

#: Paths the server once answered itself and no longer does (#953: the /site
#: renderer, its sitemap and robots, and the /viewer static dir). A dist may
#: still ship a file of that name (SvelteKit's `static/robots.txt`), which
#: the fallback serves as any other file; otherwise these are a 404, never
#: the SPA shell: a crawler asking for robots.txt must not be handed HTML.
_REMOVED_PATHS: tuple[str, ...] = ("robots.txt", "sitemap.xml", "site", "viewer")


def _is_removed_path(path: str) -> bool:
    head = path.split("/", 1)[0]
    return head in _REMOVED_PATHS


def _not_found() -> HTTPException:
    # The shape every REST not-found site raises (ADR-0037 decision 1), so the
    # same exception handling turns it into `{"detail": {...}}`.
    return HTTPException(
        status_code=404,
        detail={"code": "NOT_FOUND", "message": "Not found", "retryable": False},
    )


async def _bare_prefix_404(request: Request) -> None:
    raise _not_found()


async def _under_prefix_404(scope, receive, send) -> None:
    if scope["type"] == "websocket":
        # What Starlette's own "no such websocket route" does: close before
        # accept. An HTTP response on a websocket scope is not something every
        # ASGI server accepts.
        await WebSocketClose()(scope, receive, send)
        return
    raise _not_found()


def _mount_api_shaped_404s(app: FastAPI) -> None:
    """A 404 for each API-shaped prefix and everything under it. Routes
    registered earlier win (Starlette stops at the first full match), so a
    real ``/api/...`` route, or a legitimately mounted ``/mcp``, still
    answers; this only answers what nothing else did. A `Mount` does not
    match its own bare prefix, hence the extra route -- for every method, or
    a POST would 405 against a GET-only route.
    """
    for prefix in _API_SHAPED_PREFIXES:
        slug = prefix.replace("/", "-")
        app.add_route(
            prefix,
            _bare_prefix_404,
            methods=list(_ALL_METHODS),
            name=f"reserved-404-bare{slug}",
            include_in_schema=False,
        )
        app.mount(prefix, _under_prefix_404, name=f"reserved-404{slug}")


def mount_static(app: FastAPI, dist_dir: Path) -> None:
    """Mount SPA static file serving with fallback.

    Args:
        app: The FastAPI application instance.
        dist_dir: Path to web/dist/ directory containing built SvelteKit output.
    """
    index_html = dist_dir / "index.html"
    if not index_html.exists():
        return

    index_content = index_html.read_text()
    hash_extra = _spa_script_hash_extra(index_content)
    if not hash_extra:
        logger.warning(
            "web/dist/index.html has no SvelteKit CSP script hash (no "
            "'sha256-...' <meta http-equiv=\"content-security-policy\"> tag). "
            "The served app's script-src 'self' will have no allowance for "
            "the build's own inline bootstrap script, so it will load to a "
            "blank page. Check kit.csp.mode in web/svelte.config.js and that "
            "the app was built with adapter-static's prerendering."
        )

    def _spa_response_headers(request: Request, *, index_page: bool) -> dict[str, str]:
        # Read per request: `site_csp_extra` can change at runtime (a
        # settings update), and combining it with the build's own hash here
        # (rather than caching the merged header) keeps both current.
        config = getattr(request.app.state, "pyrite_config", None)
        site_extra = config.settings.site_csp_extra if config is not None else ""
        if index_page:
            return _spa_csp_headers(index_content, site_extra)
        # A non-HTML file served from dist (JS/CSS/an image/robots.txt/a
        # favicon): the SPA's CSP doesn't apply to it, but it must still not
        # be sniffable into a different content type (P-B1, finding #1).
        return {"X-Content-Type-Options": "nosniff"}

    # Mount the assets directory for hashed static files
    assets_dir = dist_dir / "_app"
    if assets_dir.is_dir():
        app.mount("/_app", StaticFiles(directory=str(assets_dir)), name="svelte-app")

    # Before the SPA fallback below, so these are answered by a 404, not the
    # SPA's HTML shell (#538).
    _mount_api_shaped_404s(app)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon(request: Request):
        favicon_path = dist_dir / "favicon.ico"
        if favicon_path.exists():
            return FileResponse(
                str(favicon_path), headers=_spa_response_headers(request, index_page=False)
            )
        return HTMLResponse(status_code=404)

    # SPA fallback — catch all non-API routes
    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(request: Request, path: str):
        if path.startswith(("api/", "docs", "redoc", "openapi.json", "health", "auth/")):
            return HTMLResponse(status_code=404)

        file_path = dist_dir / path
        if file_path.is_file() and file_path.resolve().is_relative_to(dist_dir.resolve()):
            # A direct request for a real file in dist -- including an
            # explicit `GET /index.html`, which is this same branch, not
            # the "no route matched" fallback below. Without headers here
            # that request bypasses the CSP/frame-ancestors entirely and
            # the page can be framed (finding #1).
            is_index = file_path.resolve() == index_html.resolve()
            return FileResponse(
                str(file_path),
                headers=_spa_response_headers(request, index_page=is_index),
            )

        if _is_removed_path(path):
            return HTMLResponse(status_code=404)

        # SPA index.html must not be cached — it references hashed JS chunks
        # that change on each build. Stale index.html = mismatched chunk errors.
        # It carries the security headers (P-B1): a CSP so stored-content
        # script (a sanitizer bypass anywhere in the app) still can't run,
        # and nosniff so a served file can't be reinterpreted as a different
        # content type.
        return HTMLResponse(
            content=index_content,
            headers={
                "Cache-Control": "no-cache",
                **_spa_response_headers(request, index_page=True),
            },
        )

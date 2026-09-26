"""
Static file serving for the Pyrite web application.

Serves the built SvelteKit app from web/dist/ with SPA fallback:
- /site/* → pre-rendered static HTML from site-cache/ (SEO-friendly)
- /site/sitemap.xml → dynamic sitemap from index
- /site/robots.txt → crawler directives
- /assets/* → static files (JS, CSS, images)
- /api/* → handled by API router (not intercepted here)
- Everything else → index.html (SPA client-side routing)
"""

import logging
import os
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# Defence in depth for /site (pre-rendered KB content on the app's own origin):
# no inline script and no event-handler attributes run, even if a renderer
# ever emits one. The pages' behaviour lives in /site/_static/*.js. Inline
# styles stay allowed; the pages use style attributes throughout.
# Operators extend it with `settings.site_csp_extra` (see build_site_csp).
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
    """The /site Content-Security-Policy, with ``extra`` merged in.

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
                "Ignoring site_csp_extra directive %r: %s cannot be extended on /site",
                raw.strip(),
                name,
            )
            continue
        if name in _CSP_SCRIPT_DIRECTIVES:
            for src in sources:
                if src in _CSP_WEAKENING:
                    logger.warning(
                        "site_csp_extra adds %s to %s: scripts planted in KB content "
                        "could run on /site; prefer a host or a 'sha256-...' hash",
                        src,
                        name,
                    )
        target = merged.setdefault(name, [])
        target.extend(src for src in sources if src not in target)
    return "; ".join(" ".join([name, *srcs]) for name, srcs in merged.items())


SITE_CSP = build_site_csp()
SITE_SECURITY_HEADERS = {
    "Content-Security-Policy": SITE_CSP,
    "X-Content-Type-Options": "nosniff",
}

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

    Same baseline as ``/site``, plus the build's own inline-script hash(es)
    added to ``script-src`` so the bootstrap SvelteKit itself emits still
    runs -- a header CSP and a page's ``<meta http-equiv>`` CSP combine with
    AND semantics, so a header ``script-src 'self'`` with no hash would block
    it even though the meta tag allows it. ``site_csp_extra`` is the same
    per-request operator setting ``/site`` honours (``build_site_csp``);
    both are merged into one ``script-src`` extra so an operator's directive
    and the build's own hash don't clobber each other.
    """
    parts = [p for p in (_spa_script_hash_extra(index_content), site_csp_extra) if p]
    extra = "; ".join(parts)
    return {
        "Content-Security-Policy": build_site_csp(extra),
        "X-Content-Type-Options": "nosniff",
    }


# The only files /site/_static serves: the /site pages' scripts.
_SITE_STATIC_DIR = Path(__file__).parent / "templates"
_SITE_STATIC_FILES = {"site.js", "site-search.js"}


def _site_404() -> HTMLResponse:
    return HTMLResponse(status_code=404, headers=SITE_SECURITY_HEADERS)


def mount_site_routes(app: FastAPI) -> None:
    """Mount /site and /viewer routes. These work independent of the SPA dist."""
    data_dir = Path(os.environ.get("PYRITE_DATA_DIR", "."))
    site_cache_dir = data_dir / "site-cache"
    viewer_dir = data_dir / "viewer"

    def _public(request: Request) -> set[str]:
        # Evaluated per request: a KB's default_role can change at runtime,
        # and a cache rendered by an earlier version may hold private KBs.
        from ..services.public_kbs import public_kb_names

        config = getattr(request.app.state, "pyrite_config", None)
        return set(public_kb_names(config)) if config is not None else set()

    def _csp(request: Request, response: Response) -> Response:
        # The built-in policy is already on the response; apply the
        # operator's `site_csp_extra`, read per request like _public().
        config = getattr(request.app.state, "pyrite_config", None)
        extra = config.settings.site_csp_extra if config is not None else ""
        response.headers["Content-Security-Policy"] = build_site_csp(extra)
        return response

    # Sitemap
    @app.get("/site/sitemap.xml", include_in_schema=False)
    async def sitemap(request: Request):
        base = str(request.base_url).rstrip("/")
        # Prefer X-Forwarded-Proto for correct scheme behind reverse proxy
        proto = request.headers.get("x-forwarded-proto", "")
        if proto == "https" and base.startswith("http://"):
            base = "https://" + base[7:]
        return _generate_sitemap(site_cache_dir, base, _public(request))

    # Robots.txt
    @app.get("/site/robots.txt", include_in_schema=False)
    @app.get("/robots.txt", include_in_schema=False)
    async def robots(request: Request):
        base = str(request.base_url).rstrip("/")
        body = f"User-agent: *\nAllow: /site/\nDisallow: /api/\nDisallow: /auth/\n\nSitemap: {base}/site/sitemap.xml\n"
        return Response(content=body, media_type="text/plain")

    # Serve /viewer/* from data/viewer/ directory
    @app.get("/viewer/{path:path}", include_in_schema=False)
    async def viewer_page(request: Request, path: str):
        return _serve_static_dir(viewer_dir, path, fallback_spa=True)

    @app.get("/viewer", include_in_schema=False)
    async def viewer_index(request: Request):
        return _serve_static_dir(viewer_dir, "index.html")

    # Scripts for the /site pages (CSP: script-src 'self')
    @app.get("/site/_static/{name}", include_in_schema=False)
    async def site_static(request: Request, name: str):
        if name not in _SITE_STATIC_FILES:
            return _csp(request, _site_404())
        return _csp(
            request,
            FileResponse(
                str(_SITE_STATIC_DIR / name),
                media_type="text/javascript",
                headers={"Cache-Control": "public, max-age=3600", **SITE_SECURITY_HEADERS},
            ),
        )

    # Search page
    @app.get("/site/search", include_in_schema=False)
    async def site_search(request: Request):
        return _csp(request, _serve_search_page(site_cache_dir))

    # Serve /site/* from pre-rendered cache
    @app.get("/site/{path:path}", include_in_schema=False)
    async def site_page(request: Request, path: str):
        return _csp(
            request,
            _serve_site_cached(
                site_cache_dir,
                path,
                "<html><body>Page not yet rendered. Run site cache render.</body></html>",
                public=_public(request),
            ),
        )

    @app.get("/site", include_in_schema=False)
    async def site_index(request: Request):
        return _csp(
            request,
            _serve_site_cached(
                site_cache_dir,
                "",
                "<html><body>Site not yet rendered. Run site cache render.</body></html>",
                public=_public(request),
            ),
        )


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
        # Read per request, like /site's `_csp()`: `site_csp_extra` can
        # change at runtime (a settings update), and combining it with the
        # build's own hash here (rather than caching the merged header)
        # keeps both current.
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

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon(request: Request):
        favicon_path = dist_dir / "favicon.ico"
        if favicon_path.exists():
            return FileResponse(
                str(favicon_path), headers=_spa_response_headers(request, index_page=False)
            )
        return HTMLResponse(status_code=404)

    # SPA fallback — catch all non-API, non-site, non-viewer routes
    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(request: Request, path: str):
        if path.startswith(
            ("api/", "docs", "redoc", "openapi.json", "health", "auth/", "site", "viewer")
        ):
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

        # SPA index.html must not be cached — it references hashed JS chunks
        # that change on each build. Stale index.html = mismatched chunk errors.
        # It carries the same security headers as /site (P-B1): a CSP so
        # stored-content script (a sanitizer bypass anywhere in the app)
        # still can't run, and nosniff so a served file can't be reinterpreted
        # as a different content type.
        return HTMLResponse(
            content=index_content,
            headers={
                "Cache-Control": "no-cache",
                **_spa_response_headers(request, index_page=True),
            },
        )


def _serve_static_dir(
    directory: Path, path: str, fallback_spa: bool = False
) -> HTMLResponse | FileResponse:
    """Serve a file from a static directory, with optional SPA fallback."""
    if not directory.is_dir():
        return HTMLResponse(status_code=404)

    file_path = directory / path
    try:
        resolved = file_path.resolve()
        if not resolved.is_relative_to(directory.resolve()):
            return HTMLResponse(status_code=404)
    except (ValueError, OSError):
        return HTMLResponse(status_code=404)

    if file_path.is_file():
        return FileResponse(str(file_path))

    # SPA fallback — serve index.html for client-side routing
    if fallback_spa:
        index = directory / "index.html"
        if index.is_file():
            return FileResponse(str(index))

    return HTMLResponse(status_code=404)


def _generate_sitemap(cache_dir: Path, base_url: str, public: set[str]) -> Response:
    """Generate sitemap.xml from cached HTML files of the public KBs only.

    A cache rendered by an earlier version can hold private KBs' pages;
    only directories named for a public KB are listed.
    """
    urls = []

    if not cache_dir.is_dir():
        xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n</urlset>'
        return Response(content=xml, media_type="application/xml")

    # Landing page
    if (cache_dir / "index.html").exists():
        urls.append(
            f"  <url>\n    <loc>{base_url}/site</loc>\n    <changefreq>daily</changefreq>\n    <priority>1.0</priority>\n  </url>"
        )

    # Walk KB directories
    for kb_dir in sorted(cache_dir.iterdir()):
        if not kb_dir.is_dir() or kb_dir.name not in public:
            continue
        kb_name = kb_dir.name

        # KB index
        if (kb_dir / "index.html").exists():
            urls.append(
                f"  <url>\n    <loc>{base_url}/site/{kb_name}</loc>\n    <changefreq>weekly</changefreq>\n    <priority>0.8</priority>\n  </url>"
            )

        # Entry pages
        for html_file in sorted(kb_dir.glob("*.html")):
            if html_file.name == "index.html":
                continue
            entry_id = html_file.stem
            stat = html_file.stat()
            lastmod = datetime.fromtimestamp(stat.st_mtime, tz=UTC).strftime("%Y-%m-%d")
            urls.append(
                f"  <url>\n    <loc>{base_url}/site/{kb_name}/{entry_id}</loc>\n    <lastmod>{lastmod}</lastmod>\n    <changefreq>monthly</changefreq>\n    <priority>0.6</priority>\n  </url>"
            )

    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{"".join(urls)}\n</urlset>'
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


def _serve_search_page(cache_dir: Path) -> HTMLResponse:
    """Serve a dedicated search page for the /site/search route."""
    from ..services.site_cache import _render_template

    try:
        html = _render_template(
            "search.html",
            title="Search — Pyrite Knowledge Base",
            description="Search across all knowledge bases",
            og_title="Search — Pyrite Knowledge Base",
            og_type="website",
            canonical='<link rel="canonical" href="/site/search">',
            extra_head='<meta name="robots" content="noindex">',
        )
    except Exception:
        # Fallback to legacy module
        from .static_search_page import SEARCH_PAGE_HTML

        html = SEARCH_PAGE_HTML
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "public, max-age=3600", **SITE_SECURITY_HEADERS},
    )


def _serve_site_cached(
    cache_dir: Path, path: str, fallback_html: str, *, public: set[str]
) -> HTMLResponse:
    """Serve a /site page from the cache directory.

    Cache layout:
        /site           → cache_dir/index.html
        /site/boyd      → cache_dir/boyd/index.html
        /site/boyd/ooda → cache_dir/boyd/ooda.html

    The landing page is served only while every KB it was rendered with is
    still public (`site_cache.landing_is_current`). Anything below it is
    served only when the *resolved* file
    lies in a public KB's directory. ``path`` arrives percent-decoded, so
    ``%2e%2e`` and ``%2F`` are already ``..`` and ``/`` here: a path with a
    ``.``, ``..`` or empty segment is refused before it is joined, and the
    check runs again on the resolved path (symlinks included), because a
    first-segment check alone let ``public-kb/%2e%2e/private-kb/x`` through.
    """
    if not path:
        from ..services.site_cache import landing_is_current

        cache_path = cache_dir / "index.html"
        if not landing_is_current(cache_dir, public):
            # Rendered with a KB that is not public now (or by a version with
            # no manifest): withheld until the next render, like a miss.
            return HTMLResponse(
                content=fallback_html,
                headers={"X-Pyrite-Cache": "MISS", **SITE_SECURITY_HEADERS},
            )
    else:
        parts = path.rstrip("/").split("/")
        if any(p in ("", ".", "..") or "\\" in p or "\x00" in p for p in parts):
            return _site_404()
        if parts[0] not in public:
            return _site_404()
        if len(parts) == 1:
            cache_path = cache_dir / parts[0] / "index.html"
        else:
            cache_path = cache_dir / parts[0] / ("/".join(parts[1:]) + ".html")

    # Security: the resolved file must be inside cache_dir, and (below the
    # landing page) inside a public KB's directory.
    try:
        root = cache_dir.resolve()
        resolved = cache_path.resolve()
        if not resolved.is_relative_to(root):
            return _site_404()
        rel = resolved.relative_to(root).parts
        if path and (len(rel) < 2 or rel[0] not in public):
            return _site_404()
    except (ValueError, OSError):
        return _site_404()

    if cache_path.is_file():
        return HTMLResponse(
            content=cache_path.read_text(encoding="utf-8"),
            headers={
                "Cache-Control": "public, max-age=3600, s-maxage=86400",
                "X-Pyrite-Cache": "HIT",
                **SITE_SECURITY_HEADERS,
            },
        )

    if path and (cache_dir / path.split("/", 1)[0]).is_dir():
        # The KB was rendered and this page is not in it: no such entry (a
        # deleted one's page is removed), not a page still to be rendered.
        return _site_404()

    # Cache miss — return SPA fallback (client-side rendering)
    return HTMLResponse(
        content=fallback_html,
        headers={"X-Pyrite-Cache": "MISS", **SITE_SECURITY_HEADERS},
    )

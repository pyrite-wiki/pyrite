"""Site cache service — renders /site pages to static HTML files for fast serving."""

import json
import logging
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..config import PyriteConfig
from ..storage.database import PyriteDB
from ..utils.metadata import parse_metadata
from ..utils.sanitize import sanitize_filename
from .public_kbs import public_kb_names

logger = logging.getLogger(__name__)

# Jinja2 template environment — loads from pyrite/server/templates/
_TEMPLATE_DIR = Path(__file__).parent.parent / "server" / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,  # We handle escaping explicitly via _esc()
)


def _render_template(template_name: str, **kwargs: object) -> str:
    """Render a Jinja2 template with the given context."""
    tmpl = _jinja_env.get_template(template_name)
    return tmpl.render(**kwargs)


def _render_page(**kwargs: object) -> str:
    """Render a page using the Jinja2 base.html template."""
    return _render_template("base.html", **kwargs)


#: Beside the landing page: the KBs it was rendered with. `/site` serves the
#: landing only while every one of them is still public (`landing_is_current`),
#: so a KB closed by any path -- the default-role endpoint, a config.yaml edit
#: and a restart, another process -- leaves the landing at once, not at the
#: next render (P-S3). Not served: its name is no public KB's.
LANDING_MANIFEST = ".landing-kbs.json"


def landing_is_current(cache_dir: Path, public: set[str]) -> bool:
    """May the cached landing page be served to a visitor who sees `public`?

    False when the manifest is missing or unreadable (a landing rendered by
    an earlier version, or half-written), or names a KB that is not public
    now: fail closed, the page is withheld until the next render.
    """
    try:
        names = json.loads((cache_dir / LANDING_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(names, list) and all(isinstance(n, str) and n in public for n in names)


#: In a KB's cache directory: an entry was deleted since its index pages were
#: rendered. `/site` renders them again before serving them
#: (`SiteCacheService.refresh_kb_index`); a full render clears it too.
INDEX_STALE = ".index-stale"


def kb_index_is_current(cache_dir: Path, kb_name: str) -> bool:
    return not (cache_dir / kb_name / INDEX_STALE).exists()


def _write_atomic(path: Path, text: str) -> None:
    """Write a page a concurrent request may be reading: never half-written."""
    import os
    import tempfile

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def drop_kb_site_pages(config: PyriteConfig, db: PyriteDB, kb_name: str) -> None:
    """Remove a KB's pre-rendered `/site` pages and re-render the landing
    from the KBs public now (P-S3, P-F6). Every path that changes a KB's
    policy or removes a KB calls this, so the landing never lists a KB that
    is gone -- which would withhold it (`landing_is_current`).

    Best effort: `/site` refuses a KB that is not public on every request
    and re-renders a stale landing on demand; this takes the pages off the
    disk. A failure (an unreadable branding.yaml, a read-only cache) is
    logged and never undoes the write it follows.
    """
    try:
        SiteCacheService(config, db).invalidate_kb(kb_name)
    except Exception:
        logger.warning("Could not drop the /site pages of KB %r", kb_name, exc_info=True)


def _remove_path(path: Path) -> None:
    """Remove a file, a symlink or a directory tree; never follow a link."""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


# schema.org type mapping
_SCHEMA_TYPES = {
    "note": "Article",
    "person": "Person",
    "organization": "Organization",
    "event": "Event",
    "source": "ScholarlyArticle",
    "concept": "Article",
    "writing": "Article",
    "era": "Article",
    "component": "SoftwareSourceCode",
}


class SiteCacheService:
    """Renders site pages to static HTML files."""

    def __init__(self, config: PyriteConfig, db: PyriteDB):
        from .branding_service import BrandingService

        self.config = config
        self.db = db
        self.cache_dir = Path(config.settings.index_path).parent / "site-cache"
        self._branding = BrandingService(config.settings.branding_dir).get()

    def render_all(self) -> dict:
        """Render the public KBs' index pages and entry pages. Returns stats.

        `/site` is served to anonymous visitors, so only public KBs
        (`public_kbs.public_kb_names`) are rendered; links into other KBs
        are dropped from the pages.
        """
        from collections import defaultdict

        self.cache_dir.mkdir(parents=True, exist_ok=True)

        public = set(public_kb_names(self.config))
        kbs = [
            {"name": kb.name, "description": getattr(kb, "description", ""), "entry_count": 0}
            for kb in self.config.all_kbs()
            if kb.name in public
        ]
        stats = {"kbs": 0, "entries": 0, "errors": 0}

        # Load all entries per KB (one query each, not per-entry)
        kb_entries: dict[str, list] = {}
        for kb_info in kbs:
            entries = self.db.list_entries(kb_name=kb_info["name"], limit=10000)
            kb_entries[kb_info["name"]] = entries
            kb_info["entry_count"] = len(entries)

        # Render landing page
        self._render_landing(kbs)

        for kb_info in kbs:
            kb_name = kb_info["name"]
            kb_dir = self.cache_dir / kb_name
            kb_dir.mkdir(parents=True, exist_ok=True)

            entries = kb_entries[kb_name]

            # Render KB index and paginated browse pages
            self._render_kb_index(kb_info, entries)
            self._render_paginated_index(kb_name, entries)
            stats["kbs"] += 1

            # Batch-load all backlinks, outlinks, and sources for this KB (3 queries total, not 3N)
            backlinks_map = _only_public_links(self.db.get_all_backlinks_for_kb(kb_name), public)
            outlinks_map = _only_public_links(self.db.get_all_outlinks_for_kb(kb_name), public)
            sources_map = self.db.get_all_sources_for_kb(kb_name)

            # Pre-compute actor->entry_ids and tag->entry_ids for related events
            actor_to_entries: dict[str, set[str]] = defaultdict(set)
            tag_to_entries: dict[str, set[str]] = defaultdict(set)
            entry_by_id: dict[str, dict] = {}
            for entry in entries:
                eid = entry["id"]
                entry_by_id[eid] = entry
                # Parse metadata
                meta = parse_metadata(entry.get("metadata"))
                actors = meta.get("actors") or meta.get("participants") or []
                if isinstance(actors, list):
                    for actor in actors:
                        if actor:
                            actor_to_entries[str(actor)].add(eid)
                for tag in entry.get("tags") or []:
                    if tag:
                        tag_to_entries[str(tag)].add(eid)

            # Compute related entries map: entry_id -> [(entry_dict, score), ...]
            related_map: dict[str, list[tuple[dict, int]]] = {}
            for entry in entries:
                eid = entry["id"]
                meta = parse_metadata(entry.get("metadata"))
                actors = meta.get("actors") or meta.get("participants") or []
                if not isinstance(actors, list):
                    actors = []
                tags = entry.get("tags") or []

                # IDs to exclude: self, backlinks, outlinks
                exclude = {eid}
                for bl in backlinks_map.get(eid, []):
                    exclude.add(bl["id"])
                for ol in outlinks_map.get(eid, []):
                    exclude.add(ol["id"])

                # Score candidates
                scores: dict[str, int] = defaultdict(int)
                for actor in actors:
                    if actor:
                        for candidate_id in actor_to_entries.get(str(actor), set()):
                            if candidate_id not in exclude:
                                scores[candidate_id] += 2
                for tag in tags:
                    if tag:
                        for candidate_id in tag_to_entries.get(str(tag), set()):
                            if candidate_id not in exclude:
                                scores[candidate_id] += 1

                if scores:
                    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
                    related_map[eid] = [
                        (entry_by_id[cid], score) for cid, score in top if cid in entry_by_id
                    ]

            # Check if KB is read-only (hide edit links on public sites)
            kb_config = self.config.get_kb(kb_name)
            is_read_only = kb_config.read_only if kb_config else False

            # Inject pre-loaded sources into entries so _render_entry won't query per-entry
            for entry in entries:
                eid = entry["id"]
                if entry.get("sources") is None:
                    entry["sources"] = sources_map.get(eid, [])

            # Render each entry using pre-loaded data
            for entry in entries:
                try:
                    eid = entry["id"]
                    self._render_entry(
                        kb_name,
                        entry,
                        backlinks_map.get(eid, []),
                        outlinks_map.get(eid, []),
                        read_only=is_read_only,
                        related=related_map.get(eid, []),
                    )
                    stats["entries"] += 1
                except Exception as e:
                    logger.warning("Failed to render %s/%s: %s", kb_name, eid, e)
                    stats["errors"] += 1

        self._prune(kb_entries)
        return stats

    def _prune(self, rendered: dict[str, list[dict]]) -> None:
        """Remove every cached page this render did not write (P-S3).

        After a render the cache holds exactly the current public entry set:
        a KB directory that is not a public KB goes whole; in a public KB,
        an entry page whose entry is gone and a browse page past the last
        goes. Names are compared as the renderer writes them
        (`sanitize_filename`). Top-level files (the landing and its manifest)
        are the renderer's own and are left.
        """
        if not self.cache_dir.is_dir():
            return
        for child in self.cache_dir.iterdir():
            if child.name in rendered and child.is_dir() and not child.is_symlink():
                continue
            if child.is_dir() or child.is_symlink():
                _remove_path(child)
        for kb_name, entries in rendered.items():
            kb_dir = self.cache_dir / kb_name
            if not kb_dir.is_dir():
                continue
            keep = {"index.html", "page"} | {f"{sanitize_filename(e['id'])}.html" for e in entries}
            for child in kb_dir.iterdir():
                if child.name not in keep or (child.name == "page" and child.is_symlink()):
                    _remove_path(child)
            self._prune_pages(kb_name, len(entries))

    def _prune_pages(self, kb_name: str, entry_count: int, page_size: int = 100) -> None:
        page_dir = self.cache_dir / kb_name / "page"
        if not page_dir.is_dir():
            return
        total_pages = max(1, (entry_count + page_size - 1) // page_size)
        keep = {f"{n}.html" for n in range(1, total_pages + 1)}
        for child in page_dir.iterdir():
            if child.name not in keep:
                _remove_path(child)

    def _public_kb_cards(self) -> list[dict]:
        """The landing page's cards for the KBs public now, with counts."""
        public = set(public_kb_names(self.config))
        return [
            {
                "name": kb.name,
                "description": getattr(kb, "description", ""),
                "entry_count": self.db.count_entries(kb_name=kb.name),
            }
            for kb in self.config.all_kbs()
            if kb.name in public
        ]

    def render_entry_by_id(self, entry_id: str, kb_name: str) -> bool:
        """Render a single entry page. Returns True if successful.

        Refuses (False) an entry in a KB that is not public.
        """
        public = set(public_kb_names(self.config))
        if kb_name not in public:
            return False
        entry = self.db.get_entry(entry_id, kb_name)
        if not entry:
            return False
        backlinks = [
            bl for bl in self.db.get_backlinks(entry_id, kb_name) if bl.get("kb_name") in public
        ]
        outlinks = [
            ol
            for ol in self.db.get_outlinks(entry_id, kb_name)
            if ol.get("kb_name", kb_name) in public
        ]
        self._render_entry(kb_name, entry, backlinks, outlinks)
        return True

    def _kb_dir(self, kb_name: str) -> Path | None:
        """The cache directory of `kb_name`, or None when that name could not
        be one the renderer writes (a separator or a dot segment)."""
        if not kb_name or kb_name in (".", "..") or "/" in kb_name or "\\" in kb_name:
            return None
        return self.cache_dir / kb_name

    def invalidate_entry(self, entry_id: str, kb_name: str) -> None:
        """Take a deleted entry off the site: its page goes now; the pages
        that list it are marked stale.

        The page is found by the name the renderer writes
        (`sanitize_filename`), so it is the page that is removed and nothing
        outside the KB's directory can be. Re-rendering the KB's index here
        made a bulk delete cost a full index render per entry; instead the
        KB is marked (`INDEX_STALE`) and `/site` renders its index once, on
        the next visit (`refresh_kb_index`), or the next full render does.
        Nothing is created for a KB that was never rendered.
        """
        kb_dir = self._kb_dir(kb_name)
        if kb_dir is None or not kb_dir.is_dir() or kb_dir.is_symlink():
            return
        (kb_dir / f"{sanitize_filename(entry_id)}.html").unlink(missing_ok=True)
        (kb_dir / INDEX_STALE).touch()

    def refresh_kb_index(self, kb_name: str) -> None:
        """Render a KB's index and browse pages again from the index, and the
        landing's counts, and clear the KB's stale mark. A KB that is not
        public now loses its pages instead."""
        kb_dir = self._kb_dir(kb_name)
        if kb_dir is None or not kb_dir.is_dir() or kb_dir.is_symlink():
            return
        if kb_name not in set(public_kb_names(self.config)):
            self.invalidate_kb(kb_name)
            return
        entries = self.db.list_entries(kb_name=kb_name, limit=10000)
        kb_config = self.config.get_kb(kb_name)
        kb_info = {
            "name": kb_name,
            "description": getattr(kb_config, "description", "") if kb_config else "",
            "entry_count": len(entries),
        }
        self._render_kb_index(kb_info, entries)
        self._render_paginated_index(kb_name, entries)
        self._prune_pages(kb_name, len(entries))
        (kb_dir / INDEX_STALE).unlink(missing_ok=True)
        if (self.cache_dir / "index.html").is_file():
            self.render_landing()

    def render_landing(self) -> None:
        """Render the landing page (and its manifest) from the KBs public now."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._render_landing(self._public_kb_cards())

    def invalidate_kb(self, kb_name: str) -> None:
        """Remove every cached page of a KB that stopped being public (or is
        gone), and re-render the landing without it (P-S3, P-F6)."""
        kb_dir = self._kb_dir(kb_name)
        if kb_dir is not None:
            _remove_path(kb_dir)
        if (self.cache_dir / "index.html").is_file():
            self.render_landing()

    def _render_landing(self, kbs: list[dict]):
        """Render the /site landing page."""
        cards = []
        for kb in kbs:
            desc = f"<p>{_esc(kb.get('description', ''))}</p>" if kb.get("description") else ""
            entries = kb.get("entry_count", 0)
            cards.append(
                f'<a href="/site/{_esc(kb["name"])}" class="kb-card">'
                f"<h2>{_esc(kb['name'])}</h2>{desc}"
                f'<span class="count">{entries} entries</span></a>'
            )

        body = (
            "<h1>Knowledge Bases</h1>"
            '<p style="color:var(--ink-soft);margin-bottom:0.5rem">Curated knowledge bases on systems thinking, lean, agile, and more.</p>'
            '<div id="site-search">'
            '<input type="text" placeholder="Search across all knowledge bases...">'
            '<div class="search-results entry-list" style="margin-top:0.5rem"></div>'
            "</div>"
            f'<div class="kb-grid">{"".join(cards)}</div>'
        )

        _brand_label = f"{self._branding.name} Knowledge Base"
        html = _render_page(
            title=_brand_label,
            description="Browse knowledge bases on systems thinking, lean, agile, and more.",
            og_title=_brand_label,
            og_type="website",
            og_url="/site",
            og_image="/static/favicon.svg",
            twitter_card="summary",
            extra_head="",
            canonical='<link rel="canonical" href="/site">',
            body=body,
        )
        # Manifest out, page, manifest in: until both are written the landing
        # is withheld (no manifest), so a crash between them never pairs a
        # page with a manifest that lists fewer KBs than the page shows.
        (self.cache_dir / LANDING_MANIFEST).unlink(missing_ok=True)
        _write_atomic(self.cache_dir / "index.html", html)
        _write_atomic(self.cache_dir / LANDING_MANIFEST, json.dumps([kb["name"] for kb in kbs]))

    def _render_kb_index(self, kb: dict, entries: list[dict]):
        """Render a KB index page. Uses _homepage entry content if available."""
        kb_name = kb["name"]
        desc = kb.get("description", "")
        total = len(entries)

        # Check for a custom homepage entry
        homepage = self.db.get_entry("_homepage", kb_name)
        if homepage and homepage.get("body"):
            title = homepage.get("title", kb_name)
            has_about = self.db.get_entry("_about", kb_name) is not None
            body = _render_designed_homepage(homepage, kb_name, total, has_about=has_about)
            page_desc = (
                homepage.get("summary")
                or desc
                or f"{total} entries in the {kb_name} knowledge base."
            )
        else:
            # Auto-generated KB index
            title = kb_name
            entry_html = []
            for e in entries:
                entry_html.append(
                    f'<a href="/site/{_esc(kb_name)}/{_esc(e["id"])}">'
                    f"<strong>{_esc(e.get('title', e['id']))}</strong> "
                    f'<span class="badge">{_esc(e.get("entry_type", "note"))}</span>'
                    f"</a>"
                )

            body = (
                f'<div class="breadcrumb"><a href="/site">Home</a><span class="sep">/</span><strong>{_esc(kb_name)}</strong></div>'
                f"<h1>{_esc(kb_name)}</h1>"
                f'<p class="meta">{total} entries</p>'
                + (
                    f'<p style="color:var(--ink-soft);margin:1rem 0 2rem 0">{_esc(desc)}</p>'
                    if desc
                    else ""
                )
                + '<div id="site-search" style="margin-bottom:1.5rem">'
                + f'<input type="text" placeholder="Search {_esc(kb_name)}...">'
                + '<div class="search-results entry-list" style="margin-top:0.5rem"></div></div>'
                + f'<div class="entry-list">{"".join(entry_html)}</div>'
            )
            page_desc = desc or f"Browse {total} entries in the {kb_name} knowledge base."

        _brand_suffix = f"{self._branding.name} Knowledge Base"
        html = _render_page(
            title=f"{_esc(title)} — {_brand_suffix}",
            description=_esc(page_desc),
            og_title=f"{_esc(title)} — {_brand_suffix}",
            og_type="website",
            og_url=f"/site/{_esc(kb_name)}",
            og_image="/static/favicon.svg",
            twitter_card="summary",
            extra_head="",
            canonical=f'<link rel="canonical" href="/site/{_esc(kb_name)}">',
            body=body,
        )
        _write_atomic(self.cache_dir / kb_name / "index.html", html)

    def _render_paginated_index(self, kb_name: str, entries: list[dict], page_size: int = 100):
        """Render paginated HTML index pages for crawlers and AI agents."""
        # Sort by date descending (newest first)
        sorted_entries = sorted(entries, key=lambda e: e.get("date") or "", reverse=True)
        total = len(sorted_entries)
        total_pages = max(1, (total + page_size - 1) // page_size)

        page_dir = self.cache_dir / kb_name / "page"
        page_dir.mkdir(parents=True, exist_ok=True)

        for page_num in range(1, total_pages + 1):
            start = (page_num - 1) * page_size
            page_entries = sorted_entries[start : start + page_size]

            rows = []
            for e in page_entries:
                eid = e["id"]
                title = _esc(e.get("title", eid))
                date = _esc(e.get("date") or "")
                tags = e.get("tags") or []
                tag_html = " ".join(
                    f'<a class="tag" href="/site/search?q={_esc(t)}">{_esc(t)}</a>'
                    for t in tags[:3]
                )
                if len(tags) > 3:
                    tag_html += f' <span class="tag" style="background:var(--surface-overlay);border-color:var(--border);color:var(--ink-muted)">+{len(tags) - 3}</span>'
                date_span = (
                    f"<span style=\"font-family:'JetBrains Mono',monospace;font-size:0.8125rem;color:var(--ink-muted);min-width:6.5rem;display:inline-block\">{date}</span>"
                    if date
                    else ""
                )
                rows.append(
                    f'<div class="index-entry">'
                    f"{date_span}"
                    f'<a href="/site/{_esc(kb_name)}/{_esc(eid)}">{title}</a>'
                    f'<div class="index-tags">{tag_html}</div>'
                    f"</div>"
                )

            # Pagination nav
            nav_parts = []
            if page_num > 1:
                nav_parts.append(
                    f'<a href="/site/{_esc(kb_name)}/page/{page_num - 1}">&larr; Newer</a>'
                )
            nav_parts.append(
                f'<span style="color:var(--ink-muted);font-size:0.875rem">Page {page_num} of {total_pages}</span>'
            )
            if page_num < total_pages:
                nav_parts.append(
                    f'<a href="/site/{_esc(kb_name)}/page/{page_num + 1}">Older &rarr;</a>'
                )
            nav_html = f'<nav style="display:flex;justify-content:center;align-items:center;gap:1.5rem;margin:2rem 0">{" ".join(nav_parts)}</nav>'

            body = (
                f'<div class="breadcrumb"><a href="/site/{_esc(kb_name)}">Home</a><span class="sep">/</span><strong>Browse</strong></div>'
                f"<h1>All Events</h1>"
                f'<p style="color:var(--ink-muted);margin-bottom:1.5rem">{total} events &middot; Page {page_num} of {total_pages} &middot; Sorted by date (newest first)</p>'
                f"{nav_html}"
                f'<div class="index-list">{"".join(rows)}</div>'
                f"{nav_html}"
            )

            html = _render_page(
                title=f"Browse Events (Page {page_num}) — {_esc(kb_name)} | {self._branding.name}",
                description=f"Browse {total} events in {kb_name}, page {page_num} of {total_pages}",
                og_title=f"Browse Events — {_esc(kb_name)}",
                og_type="website",
                og_url=f"/site/{_esc(kb_name)}/page/{page_num}",
                og_image="/static/favicon.svg",
                twitter_card="summary",
                extra_head='<meta name="robots" content="index, follow">',
                canonical=f'<link rel="canonical" href="/site/{_esc(kb_name)}/page/{page_num}">',
                body=body,
            )
            _write_atomic(page_dir / f"{page_num}.html", html)

    def _render_entry(
        self,
        kb_name: str,
        entry: dict,
        backlinks: list,
        outlinks: list,
        *,
        read_only: bool = False,
        related: list[tuple[dict, int]] | None = None,
    ):
        """Render a single entry page."""
        entry_id = entry["id"]
        title = entry.get("title", entry_id)
        entry_type = entry.get("entry_type", "note")
        body_md = entry.get("body") or ""
        summary = entry.get("summary") or ""
        tags = entry.get("tags") or []
        date = entry.get("date") or ""
        status = entry.get("status") or ""
        location = entry.get("location") or ""
        description = summary or (
            body_md[:160].replace("\n", " ") + "..."
            if len(body_md) > 160
            else body_md.replace("\n", " ")
        )

        # Parse metadata (may be a JSON string or already a dict)
        metadata = parse_metadata(entry.get("metadata"))

        # Fetch sources — list_entries doesn't include them, so fetch via get_entry
        sources = entry.get("sources")
        if sources is None:
            full_entry = self.db.get_entry(entry_id, kb_name)
            sources = full_entry.get("sources", []) if full_entry else []

        # Canonical URL
        canonical_path = f"/site/{_esc(kb_name)}/{_esc(entry_id)}"
        canonical = f'<link rel="canonical" href="{canonical_path}">'

        # JSON-LD (enhanced with url, publisher, author)
        schema_type = _SCHEMA_TYPES.get(entry_type, "Article")
        jsonld = {
            "@context": "https://schema.org",
            "@type": schema_type,
            "name": title,
            "description": description,
            "url": canonical_path,
            "publisher": {"@type": "Organization", "name": self._branding.name},
        }
        if date:
            jsonld["datePublished"] = date
        if tags:
            jsonld["keywords"] = ", ".join(tags)
        # Author from created_by or provenance in metadata
        author_name = entry.get("created_by") or ""
        if not author_name:
            prov = metadata.get("provenance") or {}
            if isinstance(prov, dict):
                author_name = prov.get("created_by") or ""
        if author_name:
            jsonld["author"] = {"@type": "Person", "name": author_name}

        extra_head = f'<script type="application/ld+json">{_json_for_script(jsonld)}</script>'

        # Reading time estimate
        word_count = len(body_md.split())
        reading_mins = max(1, round(word_count / 230))

        # Render body markdown to HTML (basic)
        body_html = _md_to_html(body_md, kb_name)

        # Tags (clickable — link to search)
        tags_html = "".join(
            f'<a class="tag" href="/site/search?q={_esc(t)}">{_esc(t)}</a>' for t in tags
        )
        tags_section = f'<div class="tags">{tags_html}</div>' if tags else ""

        # Status badge (displayed next to type badge in h1)
        status_html = ""
        if status:
            status_lower = status.lower()
            if status_lower == "confirmed":
                css_class = "status-confirmed"
            elif status_lower in ("reported", "alleged", "rumored"):
                css_class = f"status-{status_lower}"
            elif status_lower == "disputed":
                css_class = "status-disputed"
            elif status_lower == "draft":
                css_class = "status-draft"
            else:
                css_class = "status-draft"
            status_html = f'<span class="badge-status {css_class}">{_esc(status)}</span>'

        # Actors / Participants
        actors = metadata.get("actors") or metadata.get("participants") or []
        actors_html = ""
        if actors and isinstance(actors, list):
            actor_links = ", ".join(
                f'<a href="/site/search?q={_esc(str(a))}">{_esc(str(a))}</a>' for a in actors if a
            )
            if actor_links:
                actors_html = (
                    f'<div class="actors"><span class="label">Actors:</span>{actor_links}</div>'
                )

        # Capture lanes
        capture_lanes = metadata.get("capture_lanes") or []
        lanes_html = ""
        if capture_lanes and isinstance(capture_lanes, list):
            lane_badges = "".join(
                f'<a class="lane-badge" href="/site/search?q={_esc(str(lane))}">{_esc(str(lane))}</a>'
                for lane in capture_lanes
                if lane
            )
            if lane_badges:
                lanes_html = f'<div class="capture-lanes">{lane_badges}</div>'

        # Sources section (rendered before backlinks)
        sources_html = ""
        if sources:
            source_items = []
            for src in sources:
                if not isinstance(src, dict):
                    continue
                src_title = _esc(str(src.get("title") or "Untitled"))
                src_url = src.get("url") or ""
                src_outlet = _esc(str(src.get("outlet") or ""))
                src_date = _esc(str(src.get("date") or ""))

                # Only render href for http/https URLs
                if (
                    src_url
                    and isinstance(src_url, str)
                    and src_url.lower().startswith(("http://", "https://"))
                ):
                    title_part = (
                        f'<a href="{_esc(src_url)}" target="_blank" rel="noopener">{src_title}</a>'
                    )
                else:
                    title_part = src_title

                parts = [title_part]
                if src_outlet:
                    parts.append(f'<span class="outlet">{src_outlet}</span>')
                if src_date:
                    parts.append(f'<span class="source-date">({src_date})</span>')
                source_items.append(f"<li>{' &mdash; '.join(parts)}</li>")

            if source_items:
                sources_html = (
                    f'<div class="sources-section"><h2>Sources</h2>'
                    f"<ol>{''.join(source_items)}</ol></div>"
                )

        # Related events (entries sharing actors/tags, excluding self/backlinks/outlinks)
        related_html = ""
        if related:
            related_items = []
            for rel_entry, _score in related:
                rel_id = rel_entry["id"]
                rel_title = rel_entry.get("title", rel_id)
                rel_date = rel_entry.get("date") or ""
                date_span = f' <span class="date">{_esc(rel_date)}</span>' if rel_date else ""
                related_items.append(
                    f'<div class="related-item"><a href="/site/{_esc(kb_name)}/{_esc(rel_id)}">{_esc(rel_title)}</a>{date_span}</div>'
                )
            if related_items:
                related_html = (
                    f'<div class="related-section"><h2>Related Events</h2>'
                    f'<div class="related-list">{"".join(related_items)}</div></div>'
                )

        # Coverage — from the entry's own coverage frontmatter field
        # Format: coverage: [{title, url, publication}, ...]
        coverage_html = ""
        coverage_data = metadata.get("coverage") or []
        if isinstance(coverage_data, list) and coverage_data:
            coverage_items = []
            for cov in coverage_data:
                if not isinstance(cov, dict):
                    continue
                cov_title = _esc(cov.get("title", ""))
                cov_url = cov.get("url", "")
                cov_pub = _esc(cov.get("publication", ""))
                if (
                    cov_url
                    and isinstance(cov_url, str)
                    and cov_url.startswith(("http://", "https://"))
                ):
                    pub_span = f' <span class="coverage-pub">— {cov_pub}</span>' if cov_pub else ""
                    coverage_items.append(
                        f'<a href="{_esc(cov_url)}" target="_blank" rel="noopener noreferrer">{cov_title or _esc(cov_url)}</a>{pub_span}'
                    )
            if coverage_items:
                coverage_html = (
                    f'<div class="coverage-section"><h2>Coverage</h2>'
                    f'<div class="coverage-list">{"".join(coverage_items)}</div></div>'
                )

        # Backlinks
        bl_html = ""
        if backlinks:
            links = "".join(
                f'<a href="/site/{_esc(bl.get("kb_name", kb_name))}/{_esc(bl["id"])}">{_esc(bl.get("title", bl["id"]))}</a> '
                for bl in backlinks
            )
            bl_html = f'<div class="links-section"><h2>Linked from</h2>{links}</div>'

        # Outlinks
        ol_html = ""
        if outlinks:
            links = "".join(
                f'<a href="/site/{_esc(ol.get("kb_name", kb_name))}/{_esc(ol["id"])}">{_esc(ol.get("title", ol["id"]))}</a> '
                for ol in outlinks
            )
            ol_html = f'<div class="links-section"><h2>Links to</h2>{links}</div>'

        meta_parts = []
        if date:
            meta_parts.append(_esc(date))
        if location:
            meta_parts.append(f"<span>{_esc(location)}</span>")
        meta_parts.append(f'<span class="reading-time">{reading_mins} min read</span>')
        if not read_only:
            meta_parts.append(
                f'<a href="/entries/{_esc(entry_id)}?kb={_esc(kb_name)}">Edit on Pyrite</a>'
            )
        meta_html = f'<div class="meta">{" &middot; ".join(meta_parts)}</div>'

        body = (
            f'<div class="breadcrumb"><a href="/site/{_esc(kb_name)}">Home</a><span class="sep">/</span>'
            f"<strong>{_esc(title)}</strong></div>"
            f"<h1>{_esc(title)}</h1>"
            f'<div class="entry-badges"><span class="badge">{_esc(_humanize_type(entry_type))}</span>{status_html}</div>'
            f"{tags_section}{lanes_html}{actors_html}{meta_html}"
            f"<article>{body_html}</article>"
            f"{coverage_html}{related_html}{sources_html}{bl_html}{ol_html}"
        )

        html = _render_page(
            title=f"{_esc(title)} — {_esc(kb_name)} | {self._branding.name}",
            description=_esc(description),
            og_title=f"{_esc(title)} — {_esc(kb_name)}",
            og_type="article",
            og_url=canonical_path,
            og_image="/static/favicon.svg",
            twitter_card="summary",
            extra_head=extra_head,
            canonical=canonical,
            body=body,
        )

        kb_dir = self.cache_dir / kb_name
        kb_dir.mkdir(parents=True, exist_ok=True)
        (kb_dir / f"{sanitize_filename(entry_id)}.html").write_text(html, encoding="utf-8")


def _only_public_links(links_map: dict[str, list[dict]], public: set[str]) -> dict[str, list[dict]]:
    """Drop link rows whose other end is in a non-public KB.

    The batch link queries join across KBs, so a public entry's backlinks
    and outlinks can name (and title) entries in private KBs.
    """
    return {
        eid: [row for row in rows if row.get("kb_name") in public]
        for eid, rows in links_map.items()
    }


def _render_designed_homepage(
    homepage: dict, kb_name: str, total: int, *, has_about: bool = False
) -> str:
    """Render a designed homepage from a _homepage entry's structured markdown."""
    body_md = homepage.get("body") or ""
    title = homepage.get("title", kb_name)

    # Parse sections from the markdown
    sections: dict[str, str] = {}
    current_section = "_intro"
    current_lines: list[str] = []

    for line in body_md.split("\n"):
        if line.startswith("## "):
            if current_lines:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections[current_section] = "\n".join(current_lines).strip()

    # --- Hero ---
    intro = sections.get("_intro", "")
    # Find first section that looks like a subtitle
    for key in sections:
        if "documenting" in key.lower() or "systematic" in key.lower():
            intro = sections[key]
            break

    hero = f"""
    <div style="text-align:center;padding:3rem 0 2.5rem 0;border-bottom:1px solid var(--border);margin-bottom:3rem">
        <h1 style="font-size:2.75rem;letter-spacing:-0.03em;margin-bottom:0.75rem;line-height:1.1">{_esc(title)}</h1>
        {f'<p style="font-size:1.125rem;color:var(--ink-soft);max-width:38rem;margin:0 auto 1.5rem auto;line-height:1.6">{_md_inline(intro)}</p>' if intro else ""}
        <div style="display:flex;justify-content:center;gap:2.5rem;margin:2rem 0">
            <div><div style="font-size:2rem;font-weight:700;color:var(--gold)">{total:,}</div><div style="font-size:0.75rem;color:var(--ink-muted);text-transform:uppercase;letter-spacing:0.08em">Events</div></div>
        </div>
        <div style="display:flex;justify-content:center;gap:0.75rem;margin-top:1.5rem">
            <a href="/viewer/" style="display:inline-flex;align-items:center;gap:0.375rem;padding:0.625rem 1.25rem;background:var(--gold);color:var(--surface);border-radius:0.5rem;font-weight:600;font-size:0.875rem;text-decoration:none">Explore the Timeline</a>
            {'<a href="/site/' + _esc(kb_name) + '/_about" style="display:inline-flex;align-items:center;gap:0.375rem;padding:0.625rem 1.25rem;border:1px solid var(--border-light);color:var(--ink-soft);border-radius:0.5rem;font-weight:500;font-size:0.875rem;text-decoration:none">About &amp; Methodology</a>' if has_about else ""}
        </div>
    </div>"""

    # --- Search ---
    search = f"""
    <div id="site-search" style="margin-bottom:3rem">
        <input type="text" placeholder="Search {total:,} events..." style="text-align:center">
        <div class="search-results entry-list" style="margin-top:0.5rem"></div>
    </div>"""

    # --- Cascade Pattern ---
    cascade_html = ""
    cascade_text = sections.get("The Cascade Pattern", "")
    if cascade_text:
        import re

        steps = re.findall(r"\d+\.\s+\*\*(.+?)\*\*\s*[—–-]\s*(.+)", cascade_text)
        if steps:
            step_cards = []
            for i, (name, desc) in enumerate(steps):
                step_cards.append(
                    f'<div style="background:var(--surface-raised);border:1px solid var(--border);border-radius:0.625rem;padding:1.25rem;position:relative">'
                    f'<div style="display:flex;align-items:center;gap:0.625rem;margin-bottom:0.5rem">'
                    f'<span style="display:inline-flex;align-items:center;justify-content:center;width:1.75rem;height:1.75rem;border-radius:50%;background:var(--gold-glow);border:1px solid var(--gold-border);color:var(--gold);font-size:0.75rem;font-weight:700;flex-shrink:0">{i + 1}</span>'
                    f'<strong style="font-size:0.9375rem">{_esc(name)}</strong>'
                    f"</div>"
                    f'<p style="font-size:0.8125rem;color:var(--ink-muted);margin:0;line-height:1.5">{_esc(desc)}</p>'
                    f"</div>"
                )
            # Intro text before the numbered list
            cascade_intro = cascade_text.split("1.")[0].strip()
            cascade_html = f"""
            <section style="margin-bottom:3rem">
                <h2 style="text-align:center;margin-bottom:0.5rem">The Cascade Pattern</h2>
                {f'<p style="text-align:center;color:var(--ink-muted);margin-bottom:1.5rem;font-size:0.9375rem">{_md_inline(cascade_intro)}</p>' if cascade_intro else ""}
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(14rem,1fr));gap:0.75rem">{"".join(step_cards)}</div>
            </section>"""

    # --- Key Findings ---
    findings_html = ""
    findings_text = sections.get("Key Findings", "")
    if findings_text:
        import re

        items = re.findall(r"-\s+\*\*(.+?)\*\*:\s*(.+)", findings_text)
        if items:
            finding_cards = []
            for label, detail in items:
                finding_cards.append(
                    f'<div style="border-left:3px solid var(--gold-border);padding:0.75rem 1rem;background:var(--surface-raised);border-radius:0 0.375rem 0.375rem 0">'
                    f'<strong style="color:var(--gold);font-size:0.8125rem;display:block;margin-bottom:0.25rem">{_esc(label)}</strong>'
                    f'<span style="font-size:0.875rem;color:var(--ink-soft);line-height:1.5">{_esc(detail)}</span>'
                    f"</div>"
                )
            findings_html = f"""
            <section style="margin-bottom:3rem">
                <h2 style="margin-bottom:1rem">Key Findings</h2>
                <div style="display:grid;gap:0.75rem">{"".join(finding_cards)}</div>
            </section>"""

    # --- Data Standards ---
    standards_html = ""
    standards_text = sections.get("Data Standards", "")
    if standards_text:
        standards_html = f"""
        <section style="margin-bottom:3rem;padding:1.5rem;background:var(--surface-raised);border:1px solid var(--border);border-radius:0.625rem">
            <h2 style="font-size:1rem;margin-bottom:0.75rem">Data Standards</h2>
            <div style="font-size:0.875rem;color:var(--ink-soft);line-height:1.6">{_md_to_html(standards_text, kb_name)}</div>
        </section>"""

    # --- Explore links ---
    explore_html = ""
    explore_text = sections.get("Explore", "")
    if explore_text:
        import re

        links = re.findall(r"\[(.+?)\]\((.+?)\)\s*[—–-]\s*(.+)", explore_text)
        if links:
            link_cards = []
            for label, href, desc in links:
                safe_href = _safe_href(href)
                if safe_href is None:
                    continue
                link_cards.append(
                    f'<a href="{safe_href}" class="explore-card">'
                    f'<strong style="color:var(--ink);display:block;margin-bottom:0.25rem">{_esc(label)}</strong>'
                    f'<span style="font-size:0.8125rem;color:var(--ink-muted)">{_esc(desc)}</span>'
                    f"</a>"
                )
            explore_html = f"""
            <section style="margin-bottom:3rem">
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(16rem,1fr));gap:0.75rem">{"".join(link_cards)}</div>
            </section>"""

    # --- Contribute ---
    contrib_html = ""
    contrib_text = sections.get("Contribute", "")
    if contrib_text:
        contrib_html = """
        <section style="text-align:center;padding:2rem 0;border-top:1px solid var(--border);margin-top:2rem">
            <h2 style="font-size:1rem;margin-bottom:0.5rem">Open Source</h2>
            <p style="font-size:0.875rem;color:var(--ink-muted);margin-bottom:1rem">Data: CC BY-SA 4.0 · Code: MIT</p>
            <a href="https://github.com/markramm/cascade-kb" style="display:inline-flex;align-items:center;gap:0.375rem;padding:0.5rem 1rem;border:1px solid var(--border-light);border-radius:0.375rem;font-size:0.8125rem;color:var(--ink-soft);text-decoration:none">View on GitHub</a>
        </section>"""

    # --- Browse index link (for crawlers/AI agents) ---
    browse_html = f"""
    <section style="text-align:center;padding:1.5rem 0;margin-bottom:1rem">
        <a href="/site/{_esc(kb_name)}/page/1" style="font-size:0.875rem;color:var(--ink-muted)">Browse all {total:,} events by date &rarr;</a>
    </section>"""

    return (
        hero
        + search
        + cascade_html
        + findings_html
        + explore_html
        + standards_html
        + browse_html
        + contrib_html
    )


def _md_inline(text: str) -> str:
    """Convert inline markdown (bold, italic, links) without wrapping in paragraphs.

    The text is HTML-escaped before any markdown transform, so raw HTML in
    it is shown, never interpreted.
    """
    import re

    text, links = _extract_links(text, kb_name=None)
    text = _esc(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    return _restore_links(text, links)


def _humanize_type(entry_type: str) -> str:
    """Convert raw entry types to human-friendly display names."""
    return entry_type.replace("_", " ").replace("-", " ").title()


def _esc(text: str) -> str:
    """HTML-escape text for safe insertion into HTML content and attributes."""
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _md_to_html(md: str, kb_name: str) -> str:
    """Convert basic markdown to HTML with wikilink resolution.

    Links and wikilinks are rendered from the raw text first and set aside;
    everything else is HTML-escaped before the markdown transforms run, so
    raw HTML in an entry body is shown as text, never interpreted.
    """
    import re

    md, links = _extract_links(md, kb_name=kb_name)
    md = _esc(md)

    # Headings
    html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", md, flags=re.MULTILINE)
    html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
    html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)

    # Bold/italic
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html)

    # List items
    html = re.sub(r"^- (.+)$", r"<li>\1</li>", html, flags=re.MULTILINE)

    # Paragraphs (double newline)
    html = re.sub(r"\n\n+", "</p><p>", html)
    html = f"<p>{html}</p>"

    # Clean up empty paragraphs around block elements
    html = re.sub(r"<p>\s*(<h[123]>)", r"\1", html)
    html = re.sub(r"(</h[123]>)\s*</p>", r"\1", html)
    html = re.sub(r"<p>\s*</p>", "", html)

    return _restore_links(html, links)


# Placeholder for a link rendered before escaping; NUL never survives input.
_LINK_MARK = "\x00"
_SAFE_SCHEMES = ("http", "https", "mailto", "tel")


def _safe_href(url: str) -> str | None:
    """Return ``url`` escaped for an href attribute, or None if unsafe.

    Character references are decoded first (as a browser would), then any
    explicit scheme must be on an allowlist: ``javascript:``, ``data:``,
    ``vbscript:`` and anything unknown are refused, including spellings
    like ``jav&#x61;script:`` or ``java\tscript:``.
    """
    import html as _html
    import re

    decoded = _html.unescape(url).strip()
    probe = re.sub(r"[\x00-\x20\x7f]", "", decoded)
    m = re.match(r"([a-zA-Z][a-zA-Z0-9+.-]*):", probe)
    if m and m.group(1).lower() not in _SAFE_SCHEMES:
        return None
    return _esc(decoded)


def _extract_links(md: str, kb_name: str | None) -> tuple[str, list[str]]:
    """Render wikilinks and markdown links from raw text; leave placeholders.

    Returns the text with each link replaced by a NUL-delimited index, and
    the rendered (escaped) HTML for each. ``kb_name=None`` leaves wikilinks
    as text (inline contexts have no KB to resolve against).
    """
    import re

    md = (md or "").replace(_LINK_MARK, "")
    rendered: list[str] = []

    def _keep(html: str) -> str:
        rendered.append(html)
        return f"{_LINK_MARK}{len(rendered) - 1}{_LINK_MARK}"

    # Wikilinks: [[kb:id|label]] or [[id|label]] or [[id]]
    def _wikilink(m: re.Match) -> str:
        target = m.group(1)
        label = m.group(2) if m.group(2) else None
        parts = target.split(":", 1)
        if len(parts) == 2:
            return _keep(
                f'<a href="/site/{_esc(parts[0])}/{_esc(parts[1])}">{_esc(label or parts[1])}</a>'
            )
        return _keep(f'<a href="/site/{_esc(kb_name)}/{_esc(target)}">{_esc(label or target)}</a>')

    if kb_name is not None:
        md = re.sub(r"\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]", _wikilink, md)

    # Markdown links [text](url): text escaped, unsafe URLs dropped to text
    def _link(m: re.Match) -> str:
        text = _esc(m.group(1))
        href = _safe_href(m.group(2))
        if href is None:
            return _keep(text)
        return _keep(f'<a href="{href}">{text}</a>')

    md = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link, md)
    return md, rendered


def _restore_links(html: str, rendered: list[str]) -> str:
    """Put the links set aside by ``_extract_links`` back in place."""
    import re

    return re.sub(
        f"{_LINK_MARK}(\\d+){_LINK_MARK}",
        lambda m: rendered[int(m.group(1))],
        html,
    )


def _json_for_script(obj: object) -> str:
    """JSON for embedding inside a ``<script>`` element.

    ``json.dumps`` leaves ``</script>`` intact, which closes the element;
    escaping ``<``, ``>`` and ``&`` as ``\\u003c`` etc. keeps the value
    identical to a JSON parser (the web UI's ``jsonForScriptTag`` rule).
    """
    import json

    return json.dumps(obj).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

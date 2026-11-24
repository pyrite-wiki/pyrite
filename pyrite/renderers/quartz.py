"""
Quartz Renderer

Renders KB entries as Quartz-compatible markdown for static site publishing.
Handles frontmatter mapping, wikilink normalization, and site scaffolding.

Quartz (https://quartz.jzhao.xyz/) is a static site generator for markdown
knowledge bases with built-in wikilinks, backlinks, graph view, and search.
"""

import re
from pathlib import Path
from typing import Any

from ..models.base import Entry
from ..services.qa_records import is_qa_record
from ..utils.sanitize import unique_path_component
from ..utils.yaml import dump_yaml

# Reserved by _write_folder_index() -- an entry whose sanitized id is exactly
# this would otherwise collide with the folder's own index.md (#221
# redispatch: pre-existing in this function, same shape as the entry_type/id
# collisions this theme fixes).
_RESERVED_INDEX_ID = "index"

# Frontmatter fields to carry through to Quartz (from to_frontmatter output)
_QUARTZ_FIELDS = frozenset(
    {
        "title",
        "tags",
        "date",
        "aliases",
    }
)

# Fields that are internal and should not appear in Quartz frontmatter
_SKIP_FIELDS = frozenset(
    {
        "id",
        "type",
        "_schema_version",
        "created_at",
        "updated_at",
        "sources",
        "links",
        "provenance",
    }
)

# Regex for [[kb:target]] or [[kb:target|label]] or [[kb:target#heading]]
_KB_PREFIX_RE = re.compile(r"\[\[kb:([^\]|#]+)(#[^\]|]*)?((\|)([^\]]*))?\]\]")

# Regex for [[ target | label ]] with spaces around pipe (or inside brackets)
_SPACED_PIPE_RE = re.compile(r"\[\[\s*([^\]|]+?)\s*\|\s*([^\]]+?)\s*\]\]")

# Regex for transclusion parameters: ![[id]]{ ... }
_TRANSCLUSION_PARAMS_RE = re.compile(r"(!\[\[[^\]]+\]\])\{[^}]*\}")


def render_entry(entry: Entry) -> str:
    """Render an entry as Quartz-compatible markdown with YAML frontmatter.

    Output structure:
        ---
        title: ...
        tags: [...]
        date: ...
        aliases: [entry-id]
        description: ...
        ---

        Body content (with normalized wikilinks)
    """
    fm = _build_frontmatter(entry)
    body = _normalize_body(entry.body) if entry.body else ""
    yaml_str = dump_yaml(fm)
    return f"---\n{yaml_str}\n---\n\n{body}\n"


def _build_frontmatter(entry: Entry) -> dict[str, Any]:
    """Build Quartz-compatible YAML frontmatter from an entry."""
    raw_fm = entry.to_frontmatter()
    fm: dict[str, Any] = {}

    # Title (required)
    fm["title"] = entry.title

    # Tags
    if entry.tags:
        fm["tags"] = list(entry.tags)

    # Date — pull from various sources
    date = raw_fm.get("date")
    if date:
        fm["date"] = str(date)

    # Aliases — include entry ID for wikilink resolution
    aliases = list(entry.aliases) if entry.aliases else []
    if entry.id not in aliases:
        aliases.insert(0, entry.id)
    fm["aliases"] = aliases

    # Description from summary
    if entry.summary:
        fm["description"] = entry.summary

    return fm


def _normalize_body(body: str) -> str:
    """Normalize wikilinks and transclusion syntax for Quartz compatibility."""
    text = body

    # Strip kb: prefix from wikilinks: [[kb:target]] → [[target]]
    text = _KB_PREFIX_RE.sub(_replace_kb_prefix, text)

    # Normalize spaced pipes: [[ target | label ]] → [[target|label]]
    text = _SPACED_PIPE_RE.sub(r"[[\1|\2]]", text)

    # Strip transclusion parameters: ![[id]]{ view: "table" } → ![[id]]
    text = _TRANSCLUSION_PARAMS_RE.sub(r"\1", text)

    return text


def _replace_kb_prefix(match: re.Match) -> str:
    """Replace [[kb:target...]] with [[target...]]."""
    target = match.group(1)
    heading = match.group(2) or ""
    label = match.group(5)
    if label:
        return f"[[{target}{heading}|{label}]]"
    return f"[[{target}{heading}]]"


def export_site(
    entries: list[Entry],
    output_dir: Path,
    kb_name: str = "",
    kb_description: str = "",
    exclude_types: set[str] | None = None,
    exclude_statuses: set[str] | None = None,
) -> dict[str, Any]:
    """Export entries as a Quartz content directory.

    Args:
        entries: List of Entry objects to export.
        output_dir: Target directory (becomes the Quartz content/ dir).
        kb_name: KB name for the landing page title.
        kb_description: KB description for the landing page.
        exclude_types: Entry types to skip (e.g. {"backlog_item", "note"}).
        exclude_statuses: Status values to skip (e.g. {"draft", "done"}).

    Returns:
        Summary dict with entries_exported, files_created, skipped.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    exclude_types = exclude_types or set()
    exclude_statuses = exclude_statuses or set()

    # Filter entries
    filtered = []
    skipped = 0
    for entry in entries:
        # A QA record is not content (#1752): never a page, whoever calls.
        if is_qa_record(entry.entry_type):
            continue
        if entry.entry_type in exclude_types:
            skipped += 1
            continue
        fm = entry.to_frontmatter()
        status = fm.get("status") or (entry.metadata or {}).get("status")
        if status and status in exclude_statuses:
            skipped += 1
            continue
        filtered.append(entry)

    # Group by entry type for folder structure. entry_type is a stored,
    # caller-controlled value (unknown types pass through verbatim for
    # GenericEntry/plugin support). Two distinct raw types can sanitize to
    # the same OUTPUT folder (e.g. "note" and "note_" both -> note/) -- group
    # by that output folder name (not the raw type) so both types' entries
    # land together instead of one type's folder write and folder index
    # silently overwriting the other's (#221 redispatch cold read).
    folders: dict[str, list[tuple[str, list[Entry]]]] = {}
    by_type: dict[str, list[Entry]] = {}
    for entry in filtered:
        by_type.setdefault(entry.entry_type, []).append(entry)
    for entry_type, type_entries in by_type.items():
        folder_name = unique_path_component(entry_type)
        folders.setdefault(folder_name, []).append((entry_type, type_entries))

    files_created = 0

    for folder_name, type_groups in sorted(folders.items()):
        type_dir = output_dir / folder_name
        type_dir.mkdir(parents=True, exist_ok=True)

        all_entries_in_folder: list[Entry] = []
        for _entry_type, type_entries in type_groups:
            all_entries_in_folder.extend(type_entries)

        # entry.id is a stored, caller-controlled value (ids indexed from git
        # frontmatter are not slugified). unique_path_component (not
        # sanitize_filename directly) so distinct ids that sanitize alike
        # (e.g. "a/b" and "a_b") get distinct filenames instead of one
        # silently overwriting the other (#221 redispatch), and an entry
        # whose id is literally "index" does not collide with this folder's
        # own index.md.
        for entry in all_entries_in_folder:
            content = render_entry(entry)
            component = unique_path_component(entry.id)
            if component == _RESERVED_INDEX_ID:
                # An id that sanitizes to exactly "index" would otherwise
                # collide with this folder's own index.md -- force the
                # hash-suffixed form by feeding unique_path_component a
                # raw value it must change.
                component = unique_path_component(f"{entry.id}/")
            file_path = type_dir / f"{component}.md"
            file_path.write_text(content, encoding="utf-8")
            files_created += 1

        # One folder index per OUTPUT folder, listing every entry that
        # landed in it regardless of which raw type it came from.
        display_type = type_groups[0][0] if len(type_groups) == 1 else folder_name
        _write_folder_index(type_dir, display_type, all_entries_in_folder)
        files_created += 1

    # Generate landing page -- one link per output folder.
    _write_landing_page(output_dir, kb_name, kb_description, folders)
    files_created += 1

    return {
        "entries_exported": len(filtered),
        "files_created": files_created,
        "skipped": skipped,
    }


def _write_folder_index(
    folder: Path,
    entry_type: str,
    entries: list[Entry],
) -> None:
    """Write an index.md for a type folder."""
    title = entry_type.replace("_", " ").title()
    lines = [
        "---",
        f"title: {title}",
        "---",
        "",
        f"# {title}",
        "",
        f"This section contains {len(entries)} {entry_type} entries.",
        "",
    ]

    # Entry listing
    for entry in sorted(entries, key=lambda e: e.title):
        lines.append(f"- [[{entry.id}|{entry.title}]]")

    lines.append("")
    (folder / "index.md").write_text("\n".join(lines), encoding="utf-8")


def _write_landing_page(
    output_dir: Path,
    kb_name: str,
    kb_description: str,
    folders: dict[str, list[tuple[str, list[Entry]]]],
) -> None:
    """Write the root index.md landing page, one link per output folder."""
    title = kb_name or "Knowledge Base"
    total = sum(len(entries) for groups in folders.values() for _t, entries in groups)

    lines = [
        "---",
        f"title: {title}",
        "---",
        "",
        f"# {title}",
        "",
    ]

    if kb_description:
        lines.append(kb_description)
        lines.append("")

    lines.append(
        f"This knowledge base contains **{total}** entries across **{len(folders)}** categories."
    )
    lines.append("")

    # Section links -- one per output folder.
    for folder_name in sorted(folders.keys()):
        type_groups = folders[folder_name]
        display_type = type_groups[0][0] if len(type_groups) == 1 else folder_name
        display = display_type.replace("_", " ").title()
        count = sum(len(entries) for _t, entries in type_groups)
        lines.append(f"- **[{display}]({folder_name}/)** — {count} entries")

    lines.append("")
    (output_dir / "index.md").write_text("\n".join(lines), encoding="utf-8")


def scaffold_quartz_project(
    output_dir: Path,
    kb_name: str = "",
) -> list[str]:
    """Generate Quartz project scaffolding files.

    Creates package.json, quartz config, layout, and GitHub Actions workflow.
    Only run once with --init; subsequent exports just update content/.

    Args:
        output_dir: Root of the Quartz project (content/ will be inside).
        kb_name: KB name for the site title.

    Returns:
        List of files created.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    created = []

    title = kb_name or "Knowledge Base"

    # package.json
    pkg = output_dir / "package.json"
    pkg.write_text(
        _PACKAGE_JSON_TEMPLATE,
        encoding="utf-8",
    )
    created.append("package.json")

    # quartz.config.ts
    config = output_dir / "quartz.config.ts"
    config.write_text(
        _QUARTZ_CONFIG_TEMPLATE.replace("{{TITLE}}", title),
        encoding="utf-8",
    )
    created.append("quartz.config.ts")

    # quartz.layout.ts
    layout = output_dir / "quartz.layout.ts"
    layout.write_text(_QUARTZ_LAYOUT_TEMPLATE, encoding="utf-8")
    created.append("quartz.layout.ts")

    # GitHub Actions workflow
    workflow_dir = output_dir / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    workflow = workflow_dir / "deploy.yml"
    workflow.write_text(_GITHUB_WORKFLOW_TEMPLATE, encoding="utf-8")
    created.append(".github/workflows/deploy.yml")

    return created


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_PACKAGE_JSON_TEMPLATE = """{
  "name": "quartz-kb",
  "private": true,
  "scripts": {
    "quartz": "npx quartz",
    "build": "npx quartz build",
    "serve": "npx quartz build --serve"
  },
  "dependencies": {
    "@jackyzha0/quartz": "^4"
  }
}
"""

_QUARTZ_CONFIG_TEMPLATE = """import { QuartzConfig } from "./quartz/cfg"
import * as Plugin from "./quartz/plugins"

const config: QuartzConfig = {
  configuration: {
    pageTitle: "{{TITLE}}",
    pageTitleSuffix: "",
    enableSPA: true,
    enablePopovers: true,
    analytics: null,
    locale: "en-US",
    baseUrl: "",
    ignorePatterns: ["private", "templates", ".obsidian"],
    defaultDateType: "modified",
    theme: {
      fontOrigin: "googleFonts",
      cdnCaching: true,
      typography: {
        header: "Schibsted Grotesk",
        body: "Source Sans Pro",
        code: "IBM Plex Mono",
      },
      colors: {
        lightMode: {
          light: "#faf8f8",
          lightgray: "#e5e5e5",
          gray: "#b8b8b8",
          darkgray: "#4e4e4e",
          dark: "#2b2b2b",
          secondary: "#284b63",
          tertiary: "#84a59d",
          highlight: "rgba(143, 159, 169, 0.15)",
          textHighlight: "#fff23688",
        },
        darkMode: {
          light: "#161618",
          lightgray: "#393639",
          gray: "#646464",
          darkgray: "#d4d4d4",
          dark: "#ebebec",
          secondary: "#7b97aa",
          tertiary: "#84a59d",
          highlight: "rgba(143, 159, 169, 0.15)",
          textHighlight: "#fff23688",
        },
      },
    },
  },
  plugins: {
    transformers: [
      Plugin.FrontMatter(),
      Plugin.CreatedModifiedDate({ priority: ["frontmatter", "git", "filesystem"] }),
      Plugin.SyntaxHighlighting(),
      Plugin.ObsidianFlavoredMarkdown({ enableInHtmlEmbed: false }),
      Plugin.GitHubFlavoredMarkdown(),
      Plugin.TableOfContents(),
      Plugin.CrawlLinks({ markdownLinkResolution: "shortest" }),
      Plugin.Description(),
      Plugin.Latex({ renderEngine: "katex" }),
    ],
    filters: [Plugin.RemoveDrafts()],
    emitters: [
      Plugin.AliasRedirects(),
      Plugin.ComponentResources(),
      Plugin.ContentPage(),
      Plugin.FolderPage(),
      Plugin.TagPage(),
      Plugin.ContentIndex({ enableSiteMap: true, enableRSS: true }),
      Plugin.Assets(),
      Plugin.Static(),
      Plugin.NotFoundPage(),
    ],
  },
}

export default config
"""

_QUARTZ_LAYOUT_TEMPLATE = """import { PageLayout, SharedLayout } from "./quartz/cfg"
import * as Component from "./quartz/components"

export const sharedPageComponents: SharedLayout = {
  head: Component.Head(),
  header: [],
  afterBody: [],
  footer: Component.Footer({
    links: {},
  }),
}

export const defaultContentPageLayout: PageLayout = {
  beforeBody: [
    Component.Breadcrumbs(),
    Component.ArticleTitle(),
    Component.ContentMeta(),
    Component.TagList(),
  ],
  left: [
    Component.PageTitle(),
    Component.MobileOnly(Component.Spacer()),
    Component.Search(),
    Component.Darkmode(),
    Component.DesktopOnly(Component.Explorer()),
  ],
  right: [
    Component.DesktopOnly(Component.Graph()),
    Component.DesktopOnly(Component.TableOfContents()),
    Component.Backlinks(),
  ],
}

export const defaultListPageLayout: PageLayout = {
  beforeBody: [Component.Breadcrumbs(), Component.ArticleTitle(), Component.ContentMeta()],
  left: [
    Component.PageTitle(),
    Component.MobileOnly(Component.Spacer()),
    Component.Search(),
    Component.Darkmode(),
    Component.DesktopOnly(Component.Explorer()),
  ],
  right: [],
}
"""

_GITHUB_WORKFLOW_TEMPLATE = """name: Deploy Quartz site to GitHub Pages

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: "pages"
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-node@v4
        with:
          node-version: 22
      - run: npm ci
      - run: npx quartz build
      - uses: actions/upload-pages-artifact@v3
        with:
          path: public

  deploy:
    needs: build
    environment:
      name: github-pages
      url: ${{ github.event.repository.html_url }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/deploy-pages@v4
"""

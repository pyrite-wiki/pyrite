---
id: editor-system
type: component
title: "Editor System"
kind: module
path: "web/src/lib/editor/"
owner: "markr"
dependencies: ["@codemirror/state", "@codemirror/view", "@codemirror/autocomplete", "@codemirror/lang-markdown", "@tiptap/core", "@tiptap/starter-kit", "@tiptap/pm", "marked"]
tags: [web, editor]
---

# Editor System

The editor system provides a dual-mode editing experience for knowledge base entries. Users can switch between a **CodeMirror 6** source mode (raw Markdown with line numbers and syntax highlighting) and a **Tiptap** WYSIWYG mode (rich-text ProseMirror-based editing). Both modes share a common wikilink syntax and converge on Markdown as the source of truth.

## Key Files

| File | Purpose |
|------|---------|
| `Editor.svelte` | CodeMirror source-mode wrapper component (Svelte 5 runes API) |
| `TiptapEditor.svelte` | Tiptap WYSIWYG wrapper component with StarterKit, Link, Placeholder, TaskList, and Wikilink extensions |
| `wikilink-utils.ts` | Shared wikilink parsing and HTML rendering (`parseWikilinks`, `renderWikilinks`, `WIKILINK_REGEX`) |
| `callouts.ts` | Post-processor that converts Obsidian-compatible `> [!type] Title` blockquotes into styled callout divs |
| `codemirror/setup.ts` | `createEditorExtensions()` — assembles all CM6 extensions (line numbers, history, markdown lang, slash commands, wikilinks, theme) |
| `codemirror/slash-commands.ts` | Notion-style `/` menu with headings, callouts, code blocks, tables, wikilinks, dates, tasks, quotes |
| `codemirror/theme.ts` | `darkTheme` / `lightTheme` — Zinc-based color scheme for CM6, JetBrains Mono font |
| `codemirror/wikilinks.ts` | CM6 wikilink extension: <code>&#91;&#91; </code> autocomplete (fetches entry titles via API with 30s TTL cache) and `WikilinkWidget` pill decorations that hide when cursor is inside the link |
| `tiptap/wikilink-extension.ts` | Custom Tiptap `Node` for wikilinks — inline atom node, click-to-navigate ProseMirror plugin, input-rule highlighting |
| `tiptap/markdown.ts` | `markdownToHtml` (via `marked`, pre-processes wikilinks into `<span data-wikilink>`) and `htmlToMarkdown` (recursive DOM-to-Markdown converter) |

## API / Key Classes

### Shared Components

- **`Editor.svelte`** — Props: `content: string`, `onchange`, `onsave`, `readonly`. Exposes `getContent()` and `focus()`. Reactively syncs external content changes via `$effect`.
- **`TiptapEditor.svelte`** — Same prop interface. Internally converts Markdown to HTML on mount and back to Markdown on every update. Includes Ctrl/Cmd+S save handler.

### Wikilink Syntax

Both editors support <code>&#91;&#91;target&#93;&#93;</code>, <code>&#91;&#91;target|display text&#93;&#93;</code>, and <code>&#91;&#91;kb:target&#93;&#93;</code> cross-KB syntax via a shared regex pattern `WIKILINK_REGEX`. The `parseWikilinks()` function extracts structured `WikilinkMatch` objects (target, display, kb prefix, offsets). The `renderWikilinks()` function replaces wikilinks in rendered HTML with anchor tags, supporting "red links" for missing entries via an optional `existingIds` set.

### Slash Commands (CodeMirror)

The `slashCommandCompletions` function provides 11 commands across 4 sections (Headings, Blocks, Lists, Inline) that trigger when `/` is typed at line start or after whitespace. Commands include headings (H1-H3), callouts (info/warning/tip), code blocks, tables, wikilinks, dates, dividers, tasks, and blockquotes.

### Callout Rendering

The `renderCallouts()` function post-processes HTML output from `marked` to transform Obsidian-compatible blockquote callouts (`> [!info] Title`) into styled `<div class="callout callout-info">` elements. Supports 12 callout types: info, warning, tip, note, danger, quote, example, bug, question, success, failure, abstract.

## Design Notes

- **Markdown is the source of truth.** The Tiptap editor always round-trips through `markdownToHtml` / `htmlToMarkdown`. This keeps storage format-agnostic and ensures both modes produce identical output.
- **Wikilink pill decorations in CodeMirror** use `MatchDecorator` with `Decoration.replace` to show styled pills, but intelligently hide the decoration when the cursor is inside a wikilink so the user can edit the raw syntax.
- **Entry title autocomplete** in CodeMirror fetches titles from the REST API (`api.getEntryTitles`) with a 30-second TTL cache and limits to 2000 entries / 50 suggestions. The Tiptap extension defines an `onSearch` option for the same purpose but currently relies on the markdown conversion layer.
- **Theme integration** respects the `uiStore.theme` preference, toggling between dark (zinc-900) and light (white) CodeMirror themes.

## Consumers

- **Entry edit page** — switches between `Editor.svelte` and `TiptapEditor.svelte` based on user preference
- **Rendered Markdown views** — use `renderWikilinks()` and `renderCallouts()` to post-process displayed content

## Related

- [[web-frontend]] — parent SvelteKit application
- [[search-service]] — entry title autocomplete relies on search/listing endpoints
- [[entry-model]] — wikilinks reference entry IDs defined by the entry model

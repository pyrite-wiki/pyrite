/**
 * CodeMirror 6 extension for wikilink support.
 *
 * Provides:
 * - Autocomplete triggered by [[ with entry title suggestions
 * - Decorations rendering wikilinks as styled pills when cursor is outside
 */

import type { Completion, CompletionContext, CompletionResult } from '@codemirror/autocomplete';
import type { Extension } from '@codemirror/state';
import {
	Decoration,
	type DecorationSet,
	EditorView,
	MatchDecorator,
	ViewPlugin,
	type ViewUpdate,
	WidgetType
} from '@codemirror/view';
import { api } from '$lib/api/client';
import type { EntryTitle } from '$lib/api/types';
import { wsClient } from '$lib/api/websocket';
import {
	isCircularTransclusion,
	markTransclusionActive,
	CIRCULAR_REF_MESSAGE
} from '$lib/editor/transclusion-utils';
import { entryHref } from '$lib/utils/entry-href';

// =============================================================================
// Autocomplete
// =============================================================================

/** Cache of entry titles for autocomplete, refreshed periodically. */
let titleCache: EntryTitle[] = [];
let cacheTimestamp = 0;
const CACHE_TTL = 30_000; // 30 seconds

async function refreshTitleCache(): Promise<EntryTitle[]> {
	const now = Date.now();
	if (titleCache.length > 0 && now - cacheTimestamp < CACHE_TTL) {
		return titleCache;
	}
	try {
		const res = await api.getEntryTitles({ limit: 2000 });
		titleCache = res.entries;
		cacheTimestamp = now;
	} catch {
		// Keep stale cache on error
	}
	return titleCache;
}

/**
 * Autocomplete source for wikilinks.
 * Triggers when user types `[[` and provides entry title suggestions.
 */
export async function wikilinkCompletions(
	context: CompletionContext
): Promise<CompletionResult | null> {
	// Look for [[ before cursor
	const line = context.state.doc.lineAt(context.pos);
	const textBefore = line.text.slice(0, context.pos - line.from);

	// Find the last [[ that isn't closed
	const openIdx = textBefore.lastIndexOf('[[');
	if (openIdx === -1) return null;

	// Make sure there's no ]] between [[ and cursor
	const afterOpen = textBefore.slice(openIdx + 2);
	if (afterOpen.includes(']]')) return null;

	// Extract what the user has typed after [[
	const query = afterOpen.split('|')[0]; // Don't include display text portion
	const from = line.from + openIdx + 2;

	// Check for fragment syntax (two-stage completion)
	const hashIdx = query.indexOf('#');
	const caretIdx = query.indexOf('^');

	if (hashIdx !== -1 || caretIdx !== -1) {
		// Second stage: complete heading or block ID
		const separatorIdx = hashIdx !== -1 ? hashIdx : caretIdx;
		const typed = query.slice(0, separatorIdx).trim();
		// `kb:id` names the KB; a bare id several KBs hold completes nothing
		// rather than complete against one of them (#528, #912).
		const colon = typed.indexOf(':');
		const typedKb = colon > 0 ? typed.slice(0, colon) : undefined;
		const entryId = typedKb ? typed.slice(colon + 1) : typed;
		const fragmentQuery = query.slice(separatorIdx + 1).toLowerCase();
		const fragmentType = hashIdx !== -1 ? 'heading' : 'block';

		if (!entryId) return null;

		// Find the KB for this entry
		const entries = await refreshTitleCache();
		const matches = entries.filter(
			(e) =>
				(!typedKb || e.kb_name === typedKb) &&
				(e.id === entryId || e.title.toLowerCase() === entryId.toLowerCase())
		);
		if (new Set(matches.map((e) => e.kb_name)).size > 1) return null;
		const entry = matches[0];
		if (!entry) return null;

		try {
			const blockOptions: { heading?: string; block_type?: string; block_id?: string } = {};
			if (fragmentType === 'heading') {
				blockOptions.block_type = 'heading';
			}
			const res = await api.getEntryBlocks(entry.id, entry.kb_name, blockOptions);
			const blocks = res.blocks || [];

			const lowerFragment = fragmentQuery.toLowerCase();
			const filtered = fragmentQuery
				? blocks.filter(
						(b) =>
							(b.block_id?.toLowerCase().includes(lowerFragment)) ||
							(b.heading?.toLowerCase().includes(lowerFragment)) ||
							(b.content?.toLowerCase().includes(lowerFragment))
					)
				: blocks;

			const options: Completion[] = filtered.slice(0, 30).map((b) => ({
				label: fragmentType === 'heading' ? (b.heading || b.block_id) : b.block_id,
				detail: b.content?.slice(0, 60),
				apply: (view: EditorView, _completion: Completion, applyFrom: number, applyTo: number) => {
					// Replace from separator+1 to cursor
					const fragmentFrom = line.from + openIdx + 2 + separatorIdx + 1;
					const fullLine = view.state.doc.lineAt(fragmentFrom);
					const afterCursor = fullLine.text.slice(applyTo - fullLine.from);
					const hasClosing = afterCursor.startsWith(']]');
					const insertValue = fragmentType === 'heading' ? (b.heading || b.block_id) : b.block_id;
					const insert = `${insertValue}${hasClosing ? '' : ']]'}`;
					view.dispatch({ changes: { from: fragmentFrom, to: applyTo, insert } });
				}
			}));

			return {
				from: line.from + openIdx + 2 + separatorIdx + 1,
				to: context.pos,
				options,
				filter: false
			};
		} catch {
			return null;
		}
	}

	const entries = await refreshTitleCache();

	// Filter entries by query (case-insensitive prefix/substring)
	const lowerQuery = query.toLowerCase();
	const filtered = query
		? entries.filter(
				(e) =>
					e.title.toLowerCase().includes(lowerQuery) ||
					e.id.toLowerCase().includes(lowerQuery) ||
					(e.aliases ?? []).some((a) => a.toLowerCase().includes(lowerQuery))
			)
		: entries;

	const options: Completion[] = filtered.slice(0, 50).map((e) => {
		const matchedAlias = (e.aliases ?? []).find((a) => a.toLowerCase().includes(lowerQuery));
		return {
			label: e.title,
			detail: matchedAlias ? `${e.entry_type} (alias: ${matchedAlias})` : e.entry_type,
			apply: (view: EditorView, completion: Completion, from: number, to: number) => {
				const fullLine = view.state.doc.lineAt(from);
				const afterCursor = fullLine.text.slice(to - fullLine.from);
				const hasClosing = afterCursor.startsWith(']]');
				// An id several KBs hold is inserted with its KB, so the link
				// names the entry it was picked for (#528, #912).
				const held = entries.filter((o) => o.id === e.id).length > 1;
				const insert = `${held && e.kb_name ? `${e.kb_name}:` : ''}${e.id}${hasClosing ? '' : ']]'}`;
				view.dispatch({ changes: { from, to, insert } });
			}
		};
	});

	return {
		from,
		to: context.pos,
		options,
		filter: false // We already filtered
	};
}

// =============================================================================
// Wikilink Decorations (pills)
// =============================================================================

/** Widget that renders a wikilink as a styled pill. */
class WikilinkWidget extends WidgetType {
	constructor(
		readonly target: string,
		readonly display: string,
		readonly heading?: string,
		readonly blockId?: string,
		/** The KB the link names (`[[kb:id]]`); absent when the text names none. */
		readonly kb?: string
	) {
		super();
	}

	toDOM(): HTMLElement {
		const span = document.createElement('span');
		span.className =
			'wikilink-pill cursor-pointer rounded bg-blue-500/15 px-1.5 py-0.5 text-blue-600 hover:bg-blue-500/25 dark:text-blue-400';
		span.textContent = this.display;
		span.title = this.target + (this.heading ? `#${this.heading}` : '') + (this.blockId ? `^${this.blockId}` : '');
		span.addEventListener('click', () => {
			let fragment = '';
			if (this.heading) fragment = `#${encodeURIComponent(this.heading)}`;
			else if (this.blockId) fragment = `#block-${encodeURIComponent(this.blockId)}`;
			window.location.href = `${entryHref(this.target, this.kb)}${fragment}`;
		});
		return span;
	}

	eq(other: WikilinkWidget): boolean {
		return this.target === other.target && this.display === other.display
			&& this.heading === other.heading && this.blockId === other.blockId
			&& this.kb === other.kb;
	}

	ignoreEvent(): boolean {
		return false;
	}
}

/** Regex for matching wikilinks in the document (supports [[kb:target#heading^block|display]]). */
const wikilinkPattern = /\[\[(?:([a-z0-9-]+):)?([^\]|#^]+?)(?:#([^\]|^]+?))?(?:\^([^\]|]+?))?(?:\|([^\]]+?))?\]\]/g;

const wikilinkMatcher = new MatchDecorator({
	regexp: wikilinkPattern,
	decoration: (match, _view, pos) => {
		const kb = match[1]?.trim();
		const target = match[2].trim();
		const heading = match[3]?.trim();
		const blockId = match[4]?.trim();
		const displayText = match[5]?.trim();

		let display: string;
		if (displayText) {
			display = displayText;
		} else if (heading) {
			const base = kb ? `${kb}:${target}` : target;
			display = `${base} \u00A7 ${heading}`;
		} else if (blockId) {
			const base = kb ? `${kb}:${target}` : target;
			display = `${base} ^${blockId}`;
		} else {
			display = kb ? `${kb}:${target}` : target;
		}

		return Decoration.replace({
			widget: new WikilinkWidget(target, display, heading, blockId, kb || undefined)
		});
	}
});

/** ViewPlugin that shows wikilink pills when the cursor is not inside the link. */
const wikilinkDecorations = ViewPlugin.fromClass(
	class {
		decorations: DecorationSet;
		view: EditorView;

		constructor(view: EditorView) {
			this.view = view;
			this.decorations = wikilinkMatcher.createDeco(view);
		}

		update(update: ViewUpdate) {
			this.view = update.view;
			this.decorations = wikilinkMatcher.updateDeco(update, this.decorations);
		}
	},
	{
		decorations: (v) => {
			// Hide decorations when cursor is inside a wikilink
			if (!v.view) return v.decorations;
			const { from, to } = v.view.state.selection.main;
			const doc = v.view.state.doc;

			// Find if cursor is inside any wikilink
			const line = doc.lineAt(from);
			const lineText = line.text;
			const regex = new RegExp(wikilinkPattern.source, 'g');
			let match;
			while ((match = regex.exec(lineText)) !== null) {
				const start = line.from + match.index;
				const end = start + match[0].length;
				if (from >= start && to <= end) {
					// Cursor is inside this wikilink — filter it out
					return v.decorations.update({
						filter: (decoFrom, decoTo) => !(decoFrom === start && decoTo === end)
					});
				}
			}
			return v.decorations;
		}
	}
);

// =============================================================================
// Transclusion Decorations (embedded cards)
// =============================================================================

/** Widget that renders a transclusion as a read-only embedded card. */
class TransclusionWidget extends WidgetType {
	private unsubscribeWs: (() => void) | null = null;

	constructor(
		readonly target: string,
		readonly heading?: string,
		readonly blockId?: string,
		readonly options?: Record<string, string | number | boolean>,
		/** The KB the embed names (`![[kb:id]]`); absent when the text names none. */
		readonly kb?: string
	) {
		super();
	}

	toDOM(): HTMLElement {
		const wrapper = document.createElement('div');
		wrapper.className = 'transclusion-card';
		wrapper.style.cssText =
			'border: 1px solid #374151; border-left: 3px solid #3b82f6; padding: 0.75rem; margin: 0.5rem 0; border-radius: 4px; background: rgba(59,130,246,0.05); cursor: default;';
		wrapper.contentEditable = 'false';

		// Header label
		const label = document.createElement('div');
		label.style.cssText = 'color: #9ca3af; font-size: 0.85em; margin-bottom: 0.5rem;';
		const fragment = this.heading ? ` \u00A7 ${this.heading}` : this.blockId ? ` ^${this.blockId}` : '';
		label.textContent = `\u{1F4CE} ${this.target}${fragment}`;
		wrapper.appendChild(label);

		// Content area (loading state)
		const content = document.createElement('div');
		content.className = 'transclusion-content';
		content.style.cssText = 'color: #d1d5db; font-size: 0.9em;';
		content.textContent = 'Loading...';
		wrapper.appendChild(content);

		// Source link
		const cite = document.createElement('div');
		cite.style.cssText = 'margin-top: 0.5rem; font-size: 0.8em;';
		const link = document.createElement('a');
		link.href = entryHref(this.target, this.kb);
		link.textContent = this.target;
		link.style.cssText = 'color: #3b82f6; text-decoration: underline;';
		link.addEventListener('click', (e) => {
			e.preventDefault();
			window.location.href = link.href;
		});
		cite.appendChild(link);
		wrapper.appendChild(cite);

		// Async load content
		this._loadContent(content);

		// Subscribe to WebSocket updates for this entry
		this.unsubscribeWs = wsClient.onEvent((event) => {
			if (event.type === 'entry_updated' && event.entry_id === this.target) {
				content.textContent = 'Loading...';
				this._loadContent(content);
			}
		});

		return wrapper;
	}

	destroy(): void {
		if (this.unsubscribeWs) {
			this.unsubscribeWs();
			this.unsubscribeWs = null;
		}
	}

	private async _loadContent(contentEl: HTMLElement): Promise<void> {
		// Cycle detection: check if this target is already being transcluded
		if (isCircularTransclusion(this.target)) {
			contentEl.textContent = CIRCULAR_REF_MESSAGE;
			return;
		}

		const cleanup = markTransclusionActive(this.target);
		try {
			const res = await api.resolveEntry(this.target, this.kb);
			if (res.resolved && res.entry) {
				// Collection transclusion: render compact entry list
				if (res.entry.entry_type === 'collection') {
					this._renderCollection(contentEl, res.entry.id, res.entry.kb_name, res.entry.title, this.options);
					return;
				}

				let body: string;

				if (this.blockId) {
					// Fetch specific block by ID
					const blocksRes = await api.getEntryBlocks(res.entry.id, res.entry.kb_name, {
						block_id: this.blockId
					});
					const matchingBlock = blocksRes.blocks?.find(
						(b) => b.block_id === this.blockId
					);
					if (matchingBlock) {
						body = matchingBlock.content;
					} else {
						// Fallback to full body if block not found
						const entryRes = await api.getEntry(res.entry.id, { kb: res.entry.kb_name });
						body = entryRes.body || '';
					}
				} else {
					const entryRes = await api.getEntry(res.entry.id, { kb: res.entry.kb_name });
					body = entryRes.body || '';

					if (this.heading) {
						const headingRegex = new RegExp(
							`^##?#?#?#?#?\\s+${this.heading.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*$`,
							'mi'
						);
						const match = headingRegex.exec(body);
						if (match) {
							const start = match.index + match[0].length;
							const nextHeading = body.slice(start).search(/^##?#?#?#?#?\s+/m);
							body =
								nextHeading === -1
									? body.slice(start).trim()
									: body.slice(start, start + nextHeading).trim();
						}
					}
				}
				contentEl.textContent = body.slice(0, 500) + (body.length > 500 ? '...' : '');
			} else {
				contentEl.textContent = '\u26A0 Entry not found';
			}
		} catch {
			contentEl.textContent = '\u26A0 Failed to load';
		} finally {
			cleanup();
		}
	}

	private async _renderCollection(
		contentEl: HTMLElement,
		collectionId: string,
		kb: string,
		title: string,
		options?: Record<string, string | number | boolean>
	): Promise<void> {
		const limit = typeof options?.limit === 'number' ? options.limit : 10;
		const view = typeof options?.view === 'string' ? options.view : 'list';

		// Update the label to show collection indicator
		const label = contentEl.parentElement?.querySelector('div');
		if (label && label.style.fontSize === '0.85em') {
			label.textContent = `\u{1F4C2} ${title}`;
		}

		// Update the source link to point to collection page
		const cite = contentEl.parentElement?.querySelector('a');
		if (cite) {
			cite.href = `/collections/${encodeURIComponent(collectionId)}?kb=${encodeURIComponent(kb)}`;
			cite.textContent = title;
			cite.addEventListener('click', (e) => {
				e.preventDefault();
				window.location.href = cite.href;
			});
		}

		try {
			const collRes = await api.getCollectionEntries(collectionId, kb, { limit });
			contentEl.textContent = '';

			if (collRes.entries.length === 0) {
				contentEl.textContent = 'Empty collection';
				contentEl.style.color = '#9ca3af';
				contentEl.style.fontStyle = 'italic';
				return;
			}

			if (view === 'table') {
				// Table view
				const table = document.createElement('table');
				table.style.cssText = 'width: 100%; border-collapse: collapse; font-size: 0.9em;';
				const thead = document.createElement('thead');
				const headerRow = document.createElement('tr');
				for (const col of ['Title', 'Type', 'Date']) {
					const th = document.createElement('th');
					th.textContent = col;
					th.style.cssText = 'text-align: left; padding: 0.3rem 0.5rem; border-bottom: 1px solid #374151; color: #9ca3af; font-weight: 600;';
					headerRow.appendChild(th);
				}
				thead.appendChild(headerRow);
				table.appendChild(thead);

				const tbody = document.createElement('tbody');
				for (const entry of collRes.entries) {
					const row = document.createElement('tr');

					// Title cell
					const titleCell = document.createElement('td');
					titleCell.style.cssText = 'padding: 0.25rem 0.5rem; border-bottom: 1px solid #1f2937;';
					if (entry.entry_type === 'collection') {
						// Nested collection: collapsible card
						const details = document.createElement('details');
						const summary = document.createElement('summary');
						summary.style.cssText = 'cursor: pointer; color: #3b82f6;';
						summary.textContent = `\u{1F4C2} ${entry.title}`;
						details.appendChild(summary);
						const nestedContent = document.createElement('div');
						nestedContent.style.cssText = 'padding: 0.25rem 0 0 1rem; font-size: 0.9em;';
						nestedContent.textContent = 'Loading...';
						details.appendChild(nestedContent);
						titleCell.appendChild(details);
						// Lazy load nested entries on expand
						details.addEventListener('toggle', async () => {
							if (details.open && nestedContent.textContent === 'Loading...') {
								try {
									const nestedRes = await api.getCollectionEntries(entry.id, kb, { limit: 10 });
									nestedContent.textContent = '';
									if (nestedRes.entries.length === 0) {
										nestedContent.textContent = 'Empty collection';
										nestedContent.style.color = '#9ca3af';
										nestedContent.style.fontStyle = 'italic';
									} else {
										const nestedList = document.createElement('ul');
										nestedList.style.cssText = 'margin: 0; padding: 0 0 0 1rem; list-style: disc;';
										for (const nestedEntry of nestedRes.entries) {
											const nli = document.createElement('li');
											nli.style.cssText = 'margin: 0.1rem 0;';
											const nlink = document.createElement('a');
											nlink.href = entryHref(nestedEntry.id, nestedEntry.kb_name);
											nlink.textContent = nestedEntry.title;
											nlink.style.cssText = 'color: #3b82f6; text-decoration: none;';
											nlink.addEventListener('click', (e) => { e.preventDefault(); window.location.href = nlink.href; });
											nli.appendChild(nlink);
											nestedList.appendChild(nli);
										}
										nestedContent.appendChild(nestedList);
										if (nestedRes.total > nestedRes.entries.length) {
											const more = document.createElement('div');
											more.style.cssText = 'font-size: 0.85em; color: #9ca3af; margin-top: 0.2rem;';
											more.textContent = `${nestedRes.total - nestedRes.entries.length} more...`;
											nestedContent.appendChild(more);
										}
									}
								} catch {
									nestedContent.textContent = '\u26A0 Failed to load';
								}
							}
						});
					} else {
						const link = document.createElement('a');
						link.href = entryHref(entry.id, entry.kb_name);
						link.textContent = entry.title;
						link.style.cssText = 'color: #3b82f6; text-decoration: none;';
						link.addEventListener('mouseenter', () => { link.style.textDecoration = 'underline'; });
						link.addEventListener('mouseleave', () => { link.style.textDecoration = 'none'; });
						link.addEventListener('click', (e) => { e.preventDefault(); window.location.href = link.href; });
						titleCell.appendChild(link);
					}
					row.appendChild(titleCell);

					// Type cell
					const typeCell = document.createElement('td');
					typeCell.style.cssText = 'padding: 0.25rem 0.5rem; border-bottom: 1px solid #1f2937; color: #9ca3af;';
					typeCell.textContent = entry.entry_type || '';
					row.appendChild(typeCell);

					// Date cell
					const dateCell = document.createElement('td');
					dateCell.style.cssText = 'padding: 0.25rem 0.5rem; border-bottom: 1px solid #1f2937; color: #9ca3af;';
					dateCell.textContent = entry.updated_at ? new Date(entry.updated_at).toLocaleDateString() : '';
					row.appendChild(dateCell);

					tbody.appendChild(row);
				}
				table.appendChild(tbody);
				contentEl.appendChild(table);
			} else {
				// List view (default)
				const list = document.createElement('ul');
				list.style.cssText = 'margin: 0; padding: 0 0 0 1.2rem; list-style: disc;';
				for (const entry of collRes.entries) {
					const li = document.createElement('li');
					li.style.cssText = 'margin: 0.15rem 0;';

					if (entry.entry_type === 'collection') {
						// Nested collection: collapsible card
						const details = document.createElement('details');
						const summary = document.createElement('summary');
						summary.style.cssText = 'cursor: pointer; color: #3b82f6;';
						summary.textContent = `\u{1F4C2} ${entry.title}`;
						details.appendChild(summary);
						const nestedContent = document.createElement('div');
						nestedContent.style.cssText = 'padding: 0.25rem 0 0 0.5rem; font-size: 0.9em;';
						nestedContent.textContent = 'Loading...';
						details.appendChild(nestedContent);
						li.appendChild(details);
						// Lazy load nested entries on expand
						details.addEventListener('toggle', async () => {
							if (details.open && nestedContent.textContent === 'Loading...') {
								try {
									const nestedRes = await api.getCollectionEntries(entry.id, kb, { limit: 10 });
									nestedContent.textContent = '';
									if (nestedRes.entries.length === 0) {
										nestedContent.textContent = 'Empty collection';
										nestedContent.style.color = '#9ca3af';
										nestedContent.style.fontStyle = 'italic';
									} else {
										const nestedList = document.createElement('ul');
										nestedList.style.cssText = 'margin: 0; padding: 0 0 0 1rem; list-style: disc;';
										for (const nestedEntry of nestedRes.entries) {
											const nli = document.createElement('li');
											nli.style.cssText = 'margin: 0.1rem 0;';
											const nlink = document.createElement('a');
											nlink.href = entryHref(nestedEntry.id, nestedEntry.kb_name);
											nlink.textContent = nestedEntry.title;
											nlink.style.cssText = 'color: #3b82f6; text-decoration: none;';
											nlink.addEventListener('click', (e) => { e.preventDefault(); window.location.href = nlink.href; });
											nli.appendChild(nlink);
											nestedList.appendChild(nli);
										}
										nestedContent.appendChild(nestedList);
										if (nestedRes.total > nestedRes.entries.length) {
											const more = document.createElement('div');
											more.style.cssText = 'font-size: 0.85em; color: #9ca3af; margin-top: 0.2rem;';
											more.textContent = `${nestedRes.total - nestedRes.entries.length} more...`;
											nestedContent.appendChild(more);
										}
									}
								} catch {
									nestedContent.textContent = '\u26A0 Failed to load';
								}
							}
						});
					} else {
						const link = document.createElement('a');
						link.href = entryHref(entry.id, entry.kb_name);
						link.textContent = entry.title;
						link.style.cssText = 'color: #3b82f6; text-decoration: none;';
						link.addEventListener('mouseenter', () => { link.style.textDecoration = 'underline'; });
						link.addEventListener('mouseleave', () => { link.style.textDecoration = 'none'; });
						link.addEventListener('click', (e) => {
							e.preventDefault();
							window.location.href = link.href;
						});
						li.appendChild(link);
					}
					list.appendChild(li);
				}
				contentEl.appendChild(list);
			}

			// Count and "View all" link
			const footer = document.createElement('div');
			footer.style.cssText = 'margin-top: 0.4rem; font-size: 0.85em; color: #9ca3af;';
			const countText = collRes.total > collRes.entries.length
				? `Showing ${collRes.entries.length} of ${collRes.total} entries. `
				: `${collRes.total} ${collRes.total === 1 ? 'entry' : 'entries'}`;
			footer.textContent = countText;

			if (collRes.total > collRes.entries.length) {
				const viewAll = document.createElement('a');
				viewAll.href = `/collections/${encodeURIComponent(collectionId)}?kb=${encodeURIComponent(kb)}`;
				viewAll.textContent = 'View all \u2192';
				viewAll.style.cssText = 'color: #3b82f6; text-decoration: none;';
				viewAll.addEventListener('click', (e) => {
					e.preventDefault();
					window.location.href = viewAll.href;
				});
				footer.appendChild(viewAll);
			}
			contentEl.appendChild(footer);
		} catch {
			contentEl.textContent = '\u26A0 Failed to load collection entries';
		}
	}

	eq(other: TransclusionWidget): boolean {
		return (
			this.target === other.target &&
			this.kb === other.kb &&
			this.heading === other.heading &&
			this.blockId === other.blockId &&
			JSON.stringify(this.options) === JSON.stringify(other.options)
		);
	}

	ignoreEvent(): boolean {
		return true;
	}
}

/** Regex for matching transclusions in the document (supports ![[kb:target#heading^block|display]]{ options }). */
const transclusionPattern =
	/!\[\[(?:([a-z0-9-]+):)?([^\]|#^]+?)(?:#([^\]|^]+?))?(?:\^([^\]|]+?))?(?:\|([^\]]+?))?\]\](?:\{([^}]*)\})?/g;

/** Parse a transclusion options string (content inside { ... }) into a record. */
function parseOptionsString(optionsStr: string | undefined): Record<string, string | number | boolean> | undefined {
	if (!optionsStr) return undefined;
	try {
		// Quote bare keys: `view: "table"` -> `"view": "table"`
		const jsonStr = optionsStr.replace(/([a-zA-Z_]\w*)\s*:/g, '"$1":');
		const parsed = JSON.parse(`{${jsonStr}}`);
		if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
			return parsed as Record<string, string | number | boolean>;
		}
		return undefined;
	} catch {
		return undefined;
	}
}

const transclusionMatcher = new MatchDecorator({
	regexp: transclusionPattern,
	decoration: (match) => {
		const kb = match[1]?.trim() || undefined;
		const target = match[2].trim();
		const heading = match[3]?.trim();
		const blockId = match[4]?.trim();
		const options = parseOptionsString(match[6]);

		return Decoration.replace({
			widget: new TransclusionWidget(target, heading, blockId, options, kb)
		});
	}
});

/** ViewPlugin that shows transclusion cards. */
const transclusionDecorations = ViewPlugin.fromClass(
	class {
		decorations: DecorationSet;
		view: EditorView;

		constructor(view: EditorView) {
			this.view = view;
			this.decorations = transclusionMatcher.createDeco(view);
		}

		update(update: ViewUpdate) {
			this.view = update.view;
			this.decorations = transclusionMatcher.updateDeco(update, this.decorations);
		}
	},
	{
		decorations: (v) => {
			// Hide decorations when cursor is inside a transclusion
			if (!v.view) return v.decorations;
			const { from, to } = v.view.state.selection.main;
			const doc = v.view.state.doc;

			const line = doc.lineAt(from);
			const lineText = line.text;
			const regex = new RegExp(transclusionPattern.source, 'g');
			let match;
			while ((match = regex.exec(lineText)) !== null) {
				const start = line.from + match.index;
				const end = start + match[0].length;
				if (from >= start && to <= end) {
					return v.decorations.update({
						filter: (decoFrom, decoTo) => !(decoFrom === start && decoTo === end)
					});
				}
			}
			return v.decorations;
		}
	}
);

// =============================================================================
// Export
// =============================================================================

/**
 * CodeMirror extension for wikilink support.
 * Includes autocomplete on [[ and styled pill decorations.
 *
 * Note: autocomplete is configured centrally in setup.ts, which combines
 * the wikilink completion source with slash commands and other sources.
 */
export function wikilinkExtension(): Extension {
	return [wikilinkDecorations, transclusionDecorations];
}

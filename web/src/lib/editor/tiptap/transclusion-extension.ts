/**
 * Custom Tiptap node for transclusions: ![[entry-id#heading]] and ![[entry-id^block-id]]
 *
 * Renders as block-level embedded cards with lazy-loaded content.
 */

import { Node, mergeAttributes } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { wsClient } from '$lib/api/websocket';
import { markdownToHtml } from './markdown';
import {
	isCircularTransclusion,
	markTransclusionActive,
	isDepthExceeded,
	incrementDepth,
	CIRCULAR_REF_MESSAGE
} from '$lib/editor/transclusion-utils';
import { entryHref } from '$lib/utils/entry-href';

export const Transclusion = Node.create({
	name: 'transclusion',
	group: 'block',
	atom: true,

	addAttributes() {
		return {
			target: { default: '' },
			heading: { default: null },
			blockId: { default: null },
			kb: { default: null },
			options: { default: null }
		};
	},

	parseHTML() {
		return [
			{
				tag: 'div[data-transclusion]',
				getAttrs(dom) {
					const el = dom as HTMLElement;
					let options: Record<string, string | number | boolean> | null = null;
					const optionsStr = el.dataset.transclusionOptions;
					if (optionsStr) {
						try {
							options = JSON.parse(optionsStr);
						} catch {
							// ignore malformed options
						}
					}
					return {
						target: el.dataset.transclusion || '',
						heading: el.dataset.transclusionHeading || null,
						blockId: el.dataset.transclusionBlock || null,
						kb: el.dataset.transclusionKb || null,
						options
					};
				}
			}
		];
	},

	renderHTML({ node, HTMLAttributes }) {
		const target = node.attrs.target as string;
		const heading = node.attrs.heading as string | null;
		const blockId = node.attrs.blockId as string | null;
		const options = node.attrs.options as Record<string, string | number | boolean> | null;
		const label = heading
			? `${target} \u00A7 ${heading}`
			: blockId
				? `${target} ^${blockId}`
				: target;
		return [
			'div',
			mergeAttributes(HTMLAttributes, {
				'data-transclusion': target,
				'data-transclusion-heading': heading || '',
				'data-transclusion-block': blockId || '',
				'data-transclusion-options': options ? JSON.stringify(options) : '',
				class: 'transclusion-embed',
				style:
					'border: 1px solid #374151; border-left: 3px solid #3b82f6; padding: 0.75rem; margin: 0.5rem 0; border-radius: 4px; background: rgba(59,130,246,0.05);'
			}),
			[
				'p',
				{ style: 'margin: 0 0 0.5rem 0; color: #9ca3af; font-size: 0.85em;' },
				`\u{1F4CE} ${label}`
			],
			['p', { class: 'transclusion-content', style: 'margin: 0;' }, 'Loading...']
		];
	},

	addProseMirrorPlugins() {
		return [
			new Plugin({
				key: new PluginKey('transclusion-loader'),
				view() {
					// Subscribe to WebSocket updates to refresh transclusions
					const unsubscribeWs = wsClient.onEvent((event) => {
						if (event.type !== 'entry_updated') return;
						const embeds = document.querySelectorAll(
							`.transclusion-embed[data-transclusion="${event.entry_id}"]`
						);
						embeds.forEach((embed) => {
							const contentEl = embed.querySelector('.transclusion-content') as HTMLElement;
							if (!contentEl) return;
							// Reset to trigger reload on next update cycle
							contentEl.textContent = 'Loading...';
							delete contentEl.dataset.loaded;
						});
					});

					return {
						update(view) {
							const nodes = view.dom.querySelectorAll(
								'.transclusion-embed .transclusion-content'
							);
							nodes.forEach(async (node) => {
								const el = node.closest('[data-transclusion]') as HTMLElement;
								if (!el) return;
								const htmlNode = node as HTMLElement;
								if (htmlNode.dataset.loaded || htmlNode.textContent !== 'Loading...')
									return;
								const target = el.dataset.transclusion;
								if (!target) return;

								// Cycle detection
								if (isCircularTransclusion(target)) {
									htmlNode.textContent = CIRCULAR_REF_MESSAGE;
									htmlNode.dataset.loaded = 'true';
									return;
								}

								// Depth limit check
								if (isDepthExceeded()) {
									htmlNode.textContent = '\u26A0 Maximum transclusion depth exceeded';
									htmlNode.dataset.loaded = 'true';
									return;
								}

								htmlNode.dataset.loaded = 'true';
								const cleanup = markTransclusionActive(target);
								const depthCleanup = incrementDepth();
								try {
									const { api } = await import('$lib/api/client');
									const res = await api.resolveEntry(target, el.dataset.transclusionKb || undefined);
									if (res.resolved && res.entry) {
										// Collection transclusion: render compact entry list
										if (res.entry.entry_type === 'collection') {
											const collectionId = res.entry.id;
											const kb = res.entry.kb_name;
											const collTitle = res.entry.title;

											// Parse options from data attribute
											let collOptions: Record<string, string | number | boolean> | undefined;
											const optionsStr = el.dataset.transclusionOptions;
											if (optionsStr) {
												try {
													collOptions = JSON.parse(optionsStr);
												} catch {
													// ignore malformed options
												}
											}
											const limit = typeof collOptions?.limit === 'number' ? collOptions.limit : 10;
											const viewMode = typeof collOptions?.view === 'string' ? collOptions.view : 'list';

											// Update label to collection indicator
											const labelEl = el.querySelector('p');
											if (labelEl && labelEl.textContent?.startsWith('\u{1F4CE}')) {
												labelEl.textContent = `\u{1F4C2} ${collTitle}`;
											}

											try {
												const collRes = await api.getCollectionEntries(collectionId, kb, { limit });
												htmlNode.textContent = '';

												if (collRes.entries.length === 0) {
													htmlNode.textContent = 'Empty collection';
													htmlNode.style.color = '#9ca3af';
													htmlNode.style.fontStyle = 'italic';
													return;
												}

												if (viewMode === 'table') {
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

														const typeCell = document.createElement('td');
														typeCell.style.cssText = 'padding: 0.25rem 0.5rem; border-bottom: 1px solid #1f2937; color: #9ca3af;';
														typeCell.textContent = entry.entry_type || '';
														row.appendChild(typeCell);

														const dateCell = document.createElement('td');
														dateCell.style.cssText = 'padding: 0.25rem 0.5rem; border-bottom: 1px solid #1f2937; color: #9ca3af;';
														dateCell.textContent = entry.updated_at ? new Date(entry.updated_at).toLocaleDateString() : '';
														row.appendChild(dateCell);

														tbody.appendChild(row);
													}
													table.appendChild(tbody);
													htmlNode.appendChild(table);
												} else {
													// List view (default)
													const list = document.createElement('ul');
													list.style.cssText = 'margin: 0; padding: 0 0 0 1.2rem; list-style: disc;';
													for (const entry of collRes.entries) {
														const li = document.createElement('li');
														li.style.cssText = 'margin: 0.15rem 0;';

														if (entry.entry_type === 'collection') {
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
													htmlNode.appendChild(list);
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
												htmlNode.appendChild(footer);
											} catch {
												htmlNode.textContent = '\u26A0 Failed to load collection entries';
											}
											return;
										}

										// Extract relevant section
										const heading = el.dataset.transclusionHeading;
										const blockId = el.dataset.transclusionBlock;
										let content: string;

										if (blockId) {
											// Fetch specific block by ID
											const blocksRes = await api.getEntryBlocks(
												res.entry.id,
												res.entry.kb_name,
												{ block_id: blockId }
											);
											const matchingBlock = blocksRes.blocks?.find(
												(b) => b.block_id === blockId
											);
											if (matchingBlock) {
												content = matchingBlock.content;
											} else {
												// Fallback to full body if block not found
												const entryRes = await api.getEntry(res.entry.id, {
													kb: res.entry.kb_name
												});
												content = entryRes.body || '';
											}
										} else if (heading) {
											// Use blocks API for heading extraction
											const blocksRes = await api.getEntryBlocks(
												res.entry.id,
												res.entry.kb_name,
												{ heading }
											);
											if (blocksRes.blocks && blocksRes.blocks.length > 0) {
												content = blocksRes.blocks.map((b) => b.content).join('\n\n');
											} else {
												// Fallback: get full body
												const entryRes = await api.getEntry(res.entry.id, {
													kb: res.entry.kb_name
												});
												content = entryRes.body || '';
											}
										} else {
											const entryRes = await api.getEntry(res.entry.id, {
												kb: res.entry.kb_name
											});
											content = entryRes.body || '';
										}
										const truncated = content.slice(0, 500) + (content.length > 500 ? '...' : '');
										htmlNode.innerHTML = markdownToHtml(truncated);
									} else {
										htmlNode.textContent = '\u26A0 Entry not found';
									}
								} catch {
									htmlNode.textContent = '\u26A0 Failed to load';
								} finally {
									cleanup();
									depthCleanup();
								}
							});
						},
						destroy() {
							unsubscribeWs();
						}
					};
				}
			})
		];
	}
});

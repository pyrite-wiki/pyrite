<script lang="ts">
	import Topbar from '$lib/components/layout/Topbar.svelte';
	import StarButton from '$lib/components/StarButton.svelte';
	import EntryMeta from '$lib/components/entry/EntryMeta.svelte';
	import BacklinksPanel from '$lib/components/entry/BacklinksPanel.svelte';
	import VersionHistoryPanel from '$lib/components/entry/VersionHistoryPanel.svelte';
	import LocalGraphPanel from '$lib/components/graph/LocalGraphPanel.svelte';
	import SplitPane from '$lib/components/layout/SplitPane.svelte';
	import Editor from '$lib/editor/Editor.svelte';
	import TiptapEditor from '$lib/editor/TiptapEditor.svelte';
	import OutlinePanel from '$lib/components/entry/OutlinePanel.svelte';
	import AIPanel from '$lib/components/entry/AIPanel.svelte';
	import { entryStore } from '$lib/stores/entries.svelte';
	import { uiStore } from '$lib/stores/ui.svelte';
	import { authStore } from '$lib/stores/auth.svelte';
	import { brandStore } from '$lib/stores/brand.svelte';
	import { buildEntrySeo } from '$lib/utils/seo';
	import { jsonForScriptTag } from '$lib/utils/sanitize';
	import { api } from '$lib/api/client';
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { parseWikilinks } from '$lib/editor/wikilink-utils';
	import SkeletonLoader from '$lib/components/common/SkeletonLoader.svelte';
	import DeleteEntryButton from '$lib/components/entry/DeleteEntryButton.svelte';
	import { goto } from '$app/navigation';

	const canEdit = $derived(
		!authStore.authConfig.enabled || authStore.isAuthenticated
	);

	let editing = $state(false);
	let editorContent = $state('');
	let resolvedIds = $state<Set<string>>(new Set());

	const entryId = $derived($page.params.id as string);

	onMount(() => {
		entryStore.loadEntry(entryId);

		function handleKeydown(e: KeyboardEvent) {
			if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === 'B') {
				e.preventDefault();
				uiStore.toggleBacklinksPanel();
			}
			if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === 'O') {
				e.preventDefault();
				uiStore.toggleOutlinePanel();
			}
			if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === 'H') {
				e.preventDefault();
				uiStore.toggleVersionHistoryPanel();
			}
			if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === 'G') {
				e.preventDefault();
				uiStore.toggleLocalGraphPanel();
			}
		}
		window.addEventListener('keydown', handleKeydown);

		return () => {
			window.removeEventListener('keydown', handleKeydown);
		};
	});

	// Sync editor content and resolve wikilinks when entry loads
	$effect(() => {
		if (entryStore.current) {
			editorContent = entryStore.current.body ?? '';
			// Batch-resolve wikilink targets for red link rendering
			const body = entryStore.current.body ?? '';
			const links = parseWikilinks(body);
			if (links.length > 0) {
				const targets = [...new Set(links.map((l) => l.target))];
				api.resolveBatch(targets).then((res) => {
					const existing = new Set<string>();
					for (const [id, exists] of Object.entries(res.resolved)) {
						if (exists) existing.add(id);
					}
					resolvedIds = existing;
				}).catch(() => {
					// On failure, treat all as existing (no red links)
					resolvedIds = new Set();
				});
			} else {
				resolvedIds = new Set();
			}
		}
	});

	// Hydrate transclusion placeholders in the rendered (non-editing) view
	$effect(() => {
		if (!entryStore.current || editing) return;

		// Wait for DOM to render
		const timer = setTimeout(async () => {
			const placeholders = document.querySelectorAll('blockquote.transclusion');
			for (const placeholder of placeholders) {
				const target = (placeholder as HTMLElement).dataset.transclusionTarget;
				if (!target) continue;

				const heading = (placeholder as HTMLElement).dataset.transclusionHeading || undefined;
				const blockId = (placeholder as HTMLElement).dataset.transclusionBlock || undefined;
				const loadingEl = placeholder.querySelector('.transclusion-loading');
				if (!loadingEl) continue;

				try {
					const res = await api.resolveEntry(target);
					if (!res.resolved || !res.entry) {
						loadingEl.textContent = 'Entry not found';
						(loadingEl as HTMLElement).style.color = '#9ca3af';
						continue;
					}

					let content: string;
					if (blockId) {
						const blocksRes = await api.getEntryBlocks(res.entry.id, res.entry.kb_name, { block_id: blockId });
						const block = blocksRes.blocks?.find((b: { block_id: string }) => b.block_id === blockId);
						content = block?.content || '';
					} else if (heading) {
						const blocksRes = await api.getEntryBlocks(res.entry.id, res.entry.kb_name, { heading });
						content = blocksRes.blocks?.map((b: { content: string }) => b.content).join('\n\n') || '';
					} else {
						const entryRes = await api.getEntry(res.entry.id, { kb: res.entry.kb_name });
						content = entryRes.body || '';
					}

					if (content) {
						const { markdownToHtml } = await import('$lib/editor/tiptap/markdown');
						const truncated = content.slice(0, 1000) + (content.length > 1000 ? '...' : '');
						loadingEl.innerHTML = markdownToHtml(truncated);
						(loadingEl as HTMLElement).className = 'transclusion-content';
					} else {
						loadingEl.textContent = 'No content';
						(loadingEl as HTMLElement).style.color = '#9ca3af';
					}
				} catch {
					loadingEl.textContent = 'Failed to load';
					(loadingEl as HTMLElement).style.color = '#ef4444';
				}
			}
		}, 100);

		return () => clearTimeout(timer);
	});

	function toggleEdit() {
		editing = !editing;
		if (editing && entryStore.current) {
			editorContent = entryStore.current.body ?? '';
		}
	}

	function onEditorChange(content: string) {
		editorContent = content;
		entryStore.markDirty();
	}

	async function save() {
		if (!entryStore.current) return;
		try {
			await entryStore.save(entryStore.current.id, entryStore.current.kb_name, {
				body: editorContent
			});
			uiStore.toast('Saved', 'success');
		} catch {
			uiStore.toast('Failed to save', 'error');
		}
	}

	async function handleTagsChanged(newTags: string[]) {
		if (!entryStore.current) return;
		try {
			await entryStore.save(entryStore.current.id, entryStore.current.kb_name, {
				tags: newTags
			});
		} catch {
			uiStore.toast('Failed to add tag', 'error');
		}
	}

	const breadcrumbs = $derived([
		{ label: 'Entries', href: '/entries' },
		{ label: entryStore.current?.title ?? entryId }
	]);

	// Full SEO meta built from the loaded entry + brand config. Null when
	// the entry hasn't loaded yet (e.g. on initial route render) so the
	// head falls back to a plain title.
	const seo = $derived(
		entryStore.current
			? buildEntrySeo({
					entry: entryStore.current,
					brand: {
						name: brandStore.name,
						og_image_url: brandStore.og_image_url,
						site_url: brandStore.site_url
					}
				})
			: null
	);
</script>

<svelte:head>
	{#if seo}
		<title>{seo.title}</title>
		<meta name="description" content={seo.description} />
		<link rel="canonical" href={seo.canonical} />
		<meta property="og:title" content={seo.ogTitle} />
		<meta property="og:description" content={seo.ogDescription} />
		<meta property="og:type" content={seo.ogType} />
		<meta property="og:url" content={seo.ogUrl} />
		{#if seo.ogImage}<meta property="og:image" content={seo.ogImage} />{/if}
		<meta name="twitter:card" content={seo.twitterCard} />
		<meta name="twitter:title" content={seo.ogTitle} />
		<meta name="twitter:description" content={seo.ogDescription} />
		<!-- eslint-disable-next-line svelte/no-at-html-tags -->
		{@html `<script type="application/ld+json">${jsonForScriptTag(seo.jsonLd)}</script>`}
	{:else}
		<title>{entryStore.current?.title ?? 'Entry'} — {brandStore.name}</title>
	{/if}
</svelte:head>

<Topbar {breadcrumbs} />

<div class="flex flex-1 overflow-hidden">
	{#if entryStore.loading}
		<div class="flex flex-1 overflow-hidden">
			<div class="flex-1">
				<SkeletonLoader variant="detail" lines={6} />
			</div>
			<aside class="hidden w-64 shrink-0 border-l border-zinc-200 p-4 dark:border-zinc-800 lg:block">
				<SkeletonLoader lines={4} header />
			</aside>
		</div>
	{:else if entryStore.error}
		<div class="flex flex-1 items-center justify-center text-red-500">{entryStore.error}</div>
	{:else if entryStore.current}
		<SplitPane open={uiStore.backlinksPanelOpen || uiStore.versionHistoryPanelOpen || uiStore.localGraphPanelOpen}>
			{#snippet children()}
				<div class="flex h-full overflow-hidden">
					<!-- Main content -->
					<div class="flex flex-1 flex-col overflow-hidden">
						<!-- Toolbar + AI results -->
						<AIPanel
							entryId={entryStore.current?.id ?? ''}
							kbName={entryStore.current?.kb_name ?? ''}
							entryTitle={entryStore.current?.title ?? ''}
							tags={entryStore.current?.tags ?? []}
							onTagsChanged={handleTagsChanged}
						>
							{#snippet toolbar({ aiMenuButton })}
								<div class="flex items-center justify-between border-b border-zinc-200 px-6 py-2 dark:border-zinc-800">
									<div class="flex items-center gap-2">
										<StarButton entryId={entryStore.current?.id ?? ''} kbName={entryStore.current?.kb_name ?? ''} size="sm" />
										<h1 class="text-xl font-bold">{entryStore.current?.title}</h1>
									</div>
									<div class="flex items-center gap-2">
										<!-- Group 1: Edit actions -->
										{#if editing}
											<button
												onclick={save}
												disabled={entryStore.saving}
												class="rounded-md bg-blue-600 px-3 py-1 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
											>
												{entryStore.saving ? 'Saving...' : 'Save'}
											</button>
										{/if}
										{#if canEdit || editing}
										<button
											onclick={toggleEdit}
											class="rounded-md border border-zinc-300 px-3 py-1 text-sm dark:border-zinc-600"
										>
											{editing ? 'View' : 'Edit'}
										</button>
										{/if}
									{#if canEdit && !editing}
										<DeleteEntryButton
											entryId={entryStore.current?.id ?? ''}
											kbName={entryStore.current?.kb_name ?? ''}
											onDeleted={() => goto('/entries')}
										/>
									{/if}
										{#if editing}
											<button
												onclick={() => uiStore.toggleEditorMode()}
												class="rounded-md border px-3 py-1 text-sm {uiStore.editorMode === 'wysiwyg'
													? 'border-green-500 bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-300'
													: 'border-zinc-300 dark:border-zinc-600'}"
												title="Toggle between source and rich text editing"
											>
												{uiStore.editorMode === 'source' ? 'Rich Text' : 'Source'}
											</button>
										{/if}

										<!-- Divider -->
										<div class="mx-1 hidden h-5 w-px bg-zinc-300 dark:bg-zinc-700 lg:block"></div>

										<!-- Group 2: AI menu -->
										{@render aiMenuButton()}

										<!-- Divider -->
										<div class="mx-1 hidden h-5 w-px bg-zinc-300 dark:bg-zinc-700 lg:block"></div>

										<!-- Group 3: Panel toggles (icon-only) — hidden on mobile -->
										<button
											onclick={() => uiStore.toggleOutlinePanel()}
											class="hidden h-8 w-8 items-center justify-center rounded-md border lg:flex {uiStore.outlinePanelOpen
												? 'border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
												: 'border-zinc-300 dark:border-zinc-600'}"
											title="Toggle outline panel (Cmd+Shift+O)"
											aria-label="Toggle outline panel"
										>
											<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
												<path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 10h16M4 14h16M4 18h16" />
											</svg>
										</button>
										<button
											onclick={() => uiStore.toggleBacklinksPanel()}
											class="hidden h-8 w-8 items-center justify-center rounded-md border lg:flex {uiStore.backlinksPanelOpen
												? 'border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
												: 'border-zinc-300 dark:border-zinc-600'}"
											title="Toggle backlinks panel (Cmd+Shift+B)"
											aria-label="Toggle backlinks panel"
										>
											<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
												<path stroke-linecap="round" stroke-linejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
											</svg>
										</button>
										<button
											onclick={() => uiStore.toggleLocalGraphPanel()}
											class="hidden h-8 w-8 items-center justify-center rounded-md border lg:flex {uiStore.localGraphPanelOpen
												? 'border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
												: 'border-zinc-300 dark:border-zinc-600'}"
											title="Toggle local graph (Cmd+Shift+G)"
											aria-label="Toggle local graph"
										>
											<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
												<path stroke-linecap="round" stroke-linejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
											</svg>
										</button>
										<button
											onclick={() => uiStore.toggleVersionHistoryPanel()}
											class="hidden h-8 w-8 items-center justify-center rounded-md border lg:flex {uiStore.versionHistoryPanelOpen
												? 'border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
												: 'border-zinc-300 dark:border-zinc-600'}"
											title="Toggle version history (Cmd+Shift+H)"
											aria-label="Toggle version history"
										>
											<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
												<path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
											</svg>
										</button>
									</div>
								</div>
							{/snippet}
						</AIPanel>

						<!-- Editor or rendered view -->
						<div class="flex min-h-0 flex-1 overflow-hidden">
							{#if uiStore.outlinePanelOpen && !editing}
								<aside class="w-56 shrink-0 overflow-y-auto border-r border-zinc-200 dark:border-zinc-800">
									<OutlinePanel body={entryStore.current?.body ?? ''} />
								</aside>
							{/if}
							<div class="flex-1 overflow-y-auto p-6">
								{#if editing && uiStore.editorMode === 'wysiwyg'}
									<div class="h-[calc(100vh-12rem)]">
										<TiptapEditor content={editorContent} onchange={onEditorChange} onsave={save} />
									</div>
								{:else if editing}
									<div class="h-[calc(100vh-12rem)]">
										<Editor content={editorContent} onchange={onEditorChange} onsave={save} />
									</div>
								{:else}
									<div class="prose dark:prose-invert max-w-4xl">
										{@html renderMarkdownWithLinks(entryStore.current?.body ?? '', resolvedIds)}
									</div>
									{#if entryStore.current?.participants && entryStore.current.participants.length > 0}
										<div class="mt-8 border-t border-zinc-200 pt-6 dark:border-zinc-700">
											<h2 class="mb-3 text-sm font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">Related</h2>
											<div class="flex flex-wrap gap-2">
												{#each entryStore.current.participants as participant}
													<a
														href="/search?q={encodeURIComponent(participant)}"
														class="inline-flex items-center rounded-md border border-zinc-200 px-2.5 py-1 text-sm text-zinc-700 transition hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700 dark:border-zinc-700 dark:text-zinc-300 dark:hover:border-blue-600 dark:hover:bg-blue-900/30 dark:hover:text-blue-300"
													>{participant}</a>
												{/each}
											</div>
										</div>
									{/if}
								{/if}
							</div>
						</div>
					</div>

					<!-- Metadata: collapsible section on mobile, sidebar on desktop -->
					{#if entryStore.current}
						<div class="border-t border-zinc-200 p-4 dark:border-zinc-800 lg:hidden">
							<details>
								<summary class="cursor-pointer text-sm font-medium text-zinc-500 dark:text-zinc-400">
									Metadata & Links
								</summary>
								<div class="mt-3">
									<EntryMeta entry={entryStore.current} />
								</div>
							</details>
						</div>
					{/if}
					<aside
						class="hidden w-64 shrink-0 overflow-y-auto border-l border-zinc-200 p-4 dark:border-zinc-800 lg:block"
						data-testid="entry-meta-desktop"
					>
						{#if entryStore.current}
							<EntryMeta entry={entryStore.current} />
						{/if}
					</aside>
				</div>
			{/snippet}

			{#snippet panel()}
				{#if uiStore.localGraphPanelOpen}
					<LocalGraphPanel
						entryId={entryStore.current?.id ?? ''}
						kbName={entryStore.current?.kb_name ?? ''}
					/>
				{:else if uiStore.versionHistoryPanelOpen}
					<VersionHistoryPanel
						entryId={entryStore.current?.id ?? ''}
						kbName={entryStore.current?.kb_name ?? ''}
					/>
				{:else}
					<BacklinksPanel
						backlinks={entryStore.current?.backlinks ?? []}
						loading={entryStore.loading}
					/>
				{/if}
			{/snippet}
		</SplitPane>
	{/if}
</div>

<script lang="ts" module>
	import { marked } from 'marked';
	import { renderWikilinks } from '$lib/editor/wikilink-utils';
	import { renderCallouts } from '$lib/editor/callouts';
	import { sanitizeHtml } from '$lib/utils/sanitize';

	// Custom renderer that adds id attributes to headings for outline scroll-to
	const renderer = new marked.Renderer();
	renderer.heading = ({ text, depth }: { text: string; depth: number }) => {
		const slug = text
			.toLowerCase()
			.replace(/<[^>]*>/g, '')
			.replace(/[^\w\s-]/g, '')
			.replace(/\s+/g, '-')
			.replace(/-+/g, '-')
			.trim();
		return `<h${depth} id="${slug}">${text}</h${depth}>`;
	};

	function renderMarkdownWithLinks(md: string, existingIds: Set<string>): string {
		let html = marked.parse(md, { async: false, renderer }) as string;
		html = renderCallouts(html);
		html = renderWikilinks(html, existingIds.size > 0 ? existingIds : undefined);
		// Add block-id anchors: paragraphs ending with ^block-id get id="block-{id}"
		html = html.replace(
			/(<p[^>]*>)(.*?)\s*\^([a-zA-Z0-9_-]+)\s*(<\/p>)/g,
			(_match, open, content, blockId, close) => {
				return `${open.replace('<p', `<p id="block-${blockId}"`)}${content}${close}`;
			}
		);
		// Last, so nothing the passes above emit can bypass it. Entry bodies are
		// written by other users and by agents ingesting web content.
		return sanitizeHtml(html);
	}
</script>

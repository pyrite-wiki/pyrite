<script lang="ts">
	import Topbar from '$lib/components/layout/Topbar.svelte';
	import ErrorState from '$lib/components/common/ErrorState.svelte';
	import TagBadge from '$lib/components/common/TagBadge.svelte';
	import { searchStore } from '$lib/stores/search.svelte';
	import { kbStore } from '$lib/stores/kbs.svelte';
	import { api } from '$lib/api/client';
	import { typeColor } from '$lib/constants';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { savedSearches, type SavedSearch } from '$lib/stores/saved-searches.svelte';
	import SkeletonLoader from '$lib/components/common/SkeletonLoader.svelte';
	import { escapeHtml } from '$lib/utils/sanitize';

	let searchInput = $state<HTMLInputElement | null>(null);
	let selectedKb = $state('');
	let selectedType = $state('');
	let dateFrom = $state('');
	let dateTo = $state('');
	let tagFilter = $state('');
	let showAdvanced = $state(false);
	let entryTypes = $state<string[]>([]);
	let showSaveDialog = $state(false);
	let saveName = $state('');

	function highlightSnippet(snippet: string, query: string): string {
		// Escape HTML first to prevent XSS from snippet content
		const safe = escapeHtml(snippet);
		if (!query.trim()) return safe;
		// Escape regex special characters
		const escaped = query.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
		const words = escaped.split(/\s+/).filter(Boolean);
		if (words.length === 0) return safe;
		const pattern = new RegExp(`(${words.join('|')})`, 'gi');
		return safe.replace(pattern, '<mark class="bg-gold-500/30 text-gold-300 rounded px-0.5">$1</mark>');
	}

	function formatRelativeDate(dateStr: string): string {
		const date = new Date(dateStr);
		if (isNaN(date.getTime())) return dateStr;
		const now = new Date();
		const diffMs = now.getTime() - date.getTime();
		const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
		if (diffDays === 0) return 'today';
		if (diffDays === 1) return 'yesterday';
		if (diffDays < 7) return `${diffDays} days ago`;
		if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`;
		if (diffDays < 365) return `${Math.floor(diffDays / 30)} months ago`;
		return `${Math.floor(diffDays / 365)} years ago`;
	}

	function runSearch() {
		searchStore.execute({
			kb: selectedKb || undefined,
			type: selectedType || undefined,
			mode: searchStore.mode,
			date_from: dateFrom || undefined,
			date_to: dateTo || undefined,
			tags: tagFilter || undefined,
		});
		syncUrlState();
	}

	function syncUrlState() {
		const params = new URLSearchParams();
		if (searchStore.query) params.set('q', searchStore.query);
		if (searchStore.mode !== 'keyword') params.set('mode', searchStore.mode);
		if (selectedKb) params.set('kb', selectedKb);
		if (selectedType) params.set('type', selectedType);
		if (dateFrom) params.set('from', dateFrom);
		if (dateTo) params.set('to', dateTo);
		if (tagFilter) params.set('tags', tagFilter);
		const qs = params.toString();
		const newUrl = qs ? `/search?${qs}` : '/search';
		history.replaceState(history.state, '', newUrl);
	}

	function onSearchInput(e: Event) {
		const value = (e.target as HTMLInputElement).value;
		searchStore.setQuery(value);
	}

	function setMode(m: 'keyword' | 'semantic' | 'hybrid') {
		searchStore.mode = m;
		if (searchStore.query.trim()) {
			runSearch();
		}
	}

	function onKbChange() {
		if (searchStore.query.trim()) runSearch();
	}

	function handleSaveSearch() {
		if (!saveName.trim() || !searchStore.query.trim()) return;
		savedSearches.save({
			name: saveName.trim(),
			query: searchStore.query,
			mode: searchStore.mode,
			kb: selectedKb,
			type: selectedType,
			dateFrom,
			dateTo,
			tags: tagFilter,
		});
		saveName = '';
		showSaveDialog = false;
	}

	function loadSavedSearch(s: SavedSearch) {
		searchStore.query = s.query;
		searchStore.mode = s.mode;
		selectedKb = s.kb ?? '';
		selectedType = s.type ?? '';
		dateFrom = s.dateFrom ?? '';
		dateTo = s.dateTo ?? '';
		tagFilter = s.tags ?? '';
		if (s.dateFrom || s.dateTo || s.tags) showAdvanced = true;
		runSearch();
	}

	function onTypeChange() {
		if (searchStore.query.trim()) runSearch();
	}

	onMount(async () => {
		// Read all search state from URL params (makes search results shareable)
		const params = $page.url.searchParams;
		const initialQ = params.get('q') ?? '';
		const initialMode = params.get('mode') as 'keyword' | 'semantic' | 'hybrid' | null;
		const initialKb = params.get('kb') ?? '';
		const initialType = params.get('type') ?? '';
		const initialFrom = params.get('from') ?? '';
		const initialTo = params.get('to') ?? '';
		const initialTags = params.get('tags') ?? '';

		if (initialMode && ['keyword', 'semantic', 'hybrid'].includes(initialMode)) {
			searchStore.mode = initialMode;
		}
		if (initialKb) selectedKb = initialKb;
		if (initialType) selectedType = initialType;
		if (initialFrom) { dateFrom = initialFrom; showAdvanced = true; }
		if (initialTo) { dateTo = initialTo; showAdvanced = true; }
		if (initialTags) { tagFilter = initialTags; showAdvanced = true; }

		// Focus the input
		searchInput?.focus();

		// Load entry types
		try {
			const res = await api.getEntryTypes();
			entryTypes = res.types;
		} catch {
			// Fall back gracefully
		}

		// Trigger search if there's an initial query
		if (initialQ) {
			searchStore.query = initialQ;
			runSearch();
		}
	});
</script>

<svelte:head><title>Search — Pyrite</title></svelte:head>

<Topbar breadcrumbs={[{ label: 'Search' }]} />

<div class="flex flex-1 flex-col overflow-hidden">
	<!-- Search header area -->
	<div class="border-b border-zinc-200 bg-zinc-50 px-6 py-4 dark:border-zinc-800 dark:bg-zinc-900">
		<!-- Large search input -->
		<div class="relative mb-3">
			<div class="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-4">
				<svg class="h-5 w-5 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
				</svg>
			</div>
			<input
				bind:this={searchInput}
				type="text"
				value={searchStore.query}
				oninput={onSearchInput}
				placeholder="Search entries..."
				class="w-full rounded-lg border border-zinc-300 bg-white py-3 pl-12 pr-4 text-lg text-zinc-900 outline-none transition-colors placeholder:text-zinc-400 focus:border-gold-400 focus:ring-2 focus:ring-gold-400/20 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100 dark:focus:border-gold-500"
			/>
		</div>

		<!-- Filter bar -->
		<div class="flex flex-wrap items-center gap-3">
			<!-- Mode buttons -->
			<div class="flex items-center rounded-lg border border-zinc-300 bg-white dark:border-zinc-700 dark:bg-zinc-800">
				{#each (['keyword', 'semantic', 'hybrid'] as const) as m}
					<button
						onclick={() => setMode(m)}
						aria-pressed={searchStore.mode === m}
						class="rounded-lg px-3 py-1.5 text-sm font-medium transition-colors capitalize {searchStore.mode === m
							? 'bg-gold-500/20 text-gold-400 border border-gold-500/50'
							: 'text-zinc-500 hover:text-zinc-300'}"
					>
						{m}
					</button>
				{/each}
			</div>

			<!-- KB filter -->
			<select
				bind:value={selectedKb}
				onchange={onKbChange}
				aria-label="Filter by knowledge base"
				class="rounded-lg border border-zinc-300 bg-white px-3 py-1.5 text-sm text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
			>
				<option value="">All KBs</option>
				{#each kbStore.kbs as kb}
					<option value={kb.name}>{kb.name}</option>
				{/each}
			</select>

			<!-- Type filter -->
			<select
				bind:value={selectedType}
				onchange={onTypeChange}
				aria-label="Filter by entry type"
				class="rounded-lg border border-zinc-300 bg-white px-3 py-1.5 text-sm text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
			>
				<option value="">All types</option>
				{#each entryTypes as t}
					<option value={t}>{t}</option>
				{/each}
			</select>

			<!-- Advanced toggle -->
			<button
				onclick={() => (showAdvanced = !showAdvanced)}
				class="text-xs text-zinc-500 hover:text-zinc-300"
			>
				{showAdvanced ? 'Less' : 'More'} filters
			</button>

			<!-- Save search button -->
			{#if searchStore.query.trim()}
				<button
					onclick={() => { showSaveDialog = !showSaveDialog; saveName = ''; }}
					class="text-xs text-zinc-500 hover:text-zinc-300"
					title="Save this search"
				>
					Save
				</button>
			{/if}

			<!-- Result count -->
			{#if !searchStore.loading && searchStore.query.trim() && searchStore.results.length > 0}
				<span class="ml-auto text-sm text-zinc-500">
					{searchStore.results.length} result{searchStore.results.length === 1 ? '' : 's'}
				</span>
			{/if}
		</div>

		<!-- Save search dialog -->
		{#if showSaveDialog}
			<div class="mt-2 flex items-center gap-2">
				<input
					type="text"
					bind:value={saveName}
					placeholder="Name this search..."
					onkeydown={(e) => { if (e.key === 'Enter') handleSaveSearch(); if (e.key === 'Escape') showSaveDialog = false; }}
					class="flex-1 rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-800"
				/>
				<button
					onclick={handleSaveSearch}
					disabled={!saveName.trim()}
					class="rounded bg-gold-500 px-3 py-1 text-xs font-medium text-zinc-900 hover:bg-gold-400 disabled:opacity-50"
				>
					Save
				</button>
				<button
					onclick={() => (showSaveDialog = false)}
					class="rounded border border-zinc-300 px-2 py-1 text-xs text-zinc-500 hover:text-zinc-300 dark:border-zinc-700"
				>
					Cancel
				</button>
			</div>
		{/if}

		<!-- Saved searches pills -->
		{#if savedSearches.items.length > 0}
			<div class="mt-2 flex flex-wrap items-center gap-2">
				<span class="text-xs text-zinc-500">Saved:</span>
				{#each savedSearches.items as s}
					<button
						onclick={() => loadSavedSearch(s)}
						class="group flex items-center gap-1 rounded-full border border-zinc-300 bg-white px-2.5 py-0.5 text-xs text-zinc-600 transition-colors hover:border-gold-500/50 hover:text-gold-400 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-400"
					>
						{s.name}
						<span
							role="button"
							tabindex="0"
							onclick={(e) => { e.stopPropagation(); savedSearches.remove(s.name); }}
							onkeydown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); savedSearches.remove(s.name); } }}
							class="ml-0.5 hidden text-zinc-400 hover:text-red-400 group-hover:inline"
						>&times;</span>
					</button>
				{/each}
			</div>
		{/if}

		<!-- Advanced filters -->
		{#if showAdvanced}
			<div class="mt-3 flex flex-wrap items-end gap-3">
				<div>
					<label for="date-from" class="mb-1 block text-xs text-zinc-500">From</label>
					<input
						id="date-from"
						type="date"
						bind:value={dateFrom}
						onchange={() => { if (searchStore.query.trim()) runSearch(); }}
						class="rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-800"
					/>
				</div>
				<div>
					<label for="date-to" class="mb-1 block text-xs text-zinc-500">To</label>
					<input
						id="date-to"
						type="date"
						bind:value={dateTo}
						onchange={() => { if (searchStore.query.trim()) runSearch(); }}
						class="rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-800"
					/>
				</div>
				<div>
					<label for="tag-filter" class="mb-1 block text-xs text-zinc-500">Tags (comma-separated)</label>
					<input
						id="tag-filter"
						type="text"
						bind:value={tagFilter}
						placeholder="tag1, tag2"
						onchange={() => { if (searchStore.query.trim()) runSearch(); }}
						class="rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-800"
					/>
				</div>
				{#if dateFrom || dateTo || tagFilter}
					<button
						onclick={() => { dateFrom = ''; dateTo = ''; tagFilter = ''; if (searchStore.query.trim()) runSearch(); }}
						class="rounded border border-zinc-300 px-2 py-1 text-xs text-zinc-500 hover:text-zinc-300 dark:border-zinc-700"
					>
						Clear filters
					</button>
				{/if}
			</div>
		{/if}
	</div>

	<!-- Results area -->
	<div class="flex-1 overflow-y-auto p-6">
		{#if searchStore.loading}
			<div data-testid="search-skeleton">
				<SkeletonLoader variant="search" lines={4} />
			</div>
		{:else if searchStore.error}
			<ErrorState message={searchStore.error} onretry={runSearch} />
		{:else if searchStore.query.trim() && searchStore.results.length === 0}
			<div data-testid="search-empty-state" class="flex flex-col items-center justify-center py-16 text-center">
				<svg class="mb-4 h-12 w-12 text-zinc-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
				</svg>
				<p class="mb-1 text-lg font-medium text-zinc-300">No results found</p>
				<p class="text-sm text-zinc-500">No results for "{searchStore.query}"</p>
				{#if searchStore.mode === 'keyword'}
					<p class="mt-2 text-xs text-zinc-600">Try switching to <button class="text-gold-400 hover:underline" onclick={() => setMode('hybrid')}>hybrid</button> or <button class="text-gold-400 hover:underline" onclick={() => setMode('semantic')}>semantic</button> mode</p>
				{/if}
			</div>
		{:else if !searchStore.query.trim()}
			<div class="flex flex-col items-center justify-center py-16 text-center">
				<svg class="mb-4 h-12 w-12 text-zinc-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
				</svg>
				<p class="mb-1 text-lg font-medium text-zinc-400">Search your knowledge base</p>
				<p class="text-sm text-zinc-500">Type to search across all entries, notes, and documents</p>
			</div>
		{:else}
			<div class="space-y-2">
				{#each searchStore.results as result (`${result.kb_name}:${result.id}`)}
					<a
						href="/entries/{encodeURIComponent(result.id)}"
						class="block rounded-lg border border-zinc-200 bg-white p-4 transition-colors hover:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-800/50 dark:hover:border-zinc-500"
						style="border-left: 3px solid {typeColor(result.entry_type)}"
					>
						<div class="mb-1.5 flex flex-wrap items-center gap-2">
							<!-- Title -->
							<span class="font-medium text-zinc-900 dark:text-zinc-100">{result.title}</span>

							<!-- Type badge -->
							<span
								class="rounded px-1.5 py-0.5 text-xs font-medium"
								style="background-color: {typeColor(result.entry_type)}20; color: {typeColor(result.entry_type)}"
							>
								{result.entry_type}
							</span>

							<!-- KB badge -->
							<span class="rounded bg-zinc-100 px-1.5 py-0.5 text-xs text-zinc-500 dark:bg-zinc-700 dark:text-zinc-400">
								{result.kb_name}
							</span>

							<!-- Date -->
							{#if result.date}
								<span class="ml-auto text-xs text-zinc-400">{formatRelativeDate(result.date)}</span>
							{/if}
						</div>

						<!-- Snippet with highlighting -->
						{#if result.snippet}
							<p class="mb-2 text-sm leading-relaxed text-zinc-500 dark:text-zinc-400">
								<!-- eslint-disable-next-line svelte/no-at-html-tags -->
								{@html highlightSnippet(result.snippet, searchStore.query)}
							</p>
						{/if}

						<!-- Tags -->
						{#if result.tags.length > 0}
							<div class="flex flex-wrap gap-1">
								{#each result.tags as tag}
									<TagBadge {tag} onclick={(e) => { e.preventDefault(); e.stopPropagation(); goto(`/entries?tag=${encodeURIComponent(tag)}`); }} />
								{/each}
							</div>
						{/if}
					</a>
				{/each}
			</div>
		{/if}
	</div>
</div>

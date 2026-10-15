<script lang="ts">
	import { entryHref } from '$lib/utils/entry-href';
	import Topbar from '$lib/components/layout/Topbar.svelte';
	import EntryList from '$lib/components/entry/EntryList.svelte';
	import { entryStore } from '$lib/stores/entries.svelte';
	import { kbStore } from '$lib/stores/kbs.svelte';
	import { searchStore } from '$lib/stores/search.svelte';
	import { api } from '$lib/api/client';
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';

	let filterType = $state('');
	let filterStatus = $state('');
	let filterMinImportance = $state('');
	let filterParticipant = $state('');
	let entryTypes = $state<string[]>([]);

	const statusOptions = [
		'draft',
		'confirmed',
		'reported',
		'disputed',
		'unverified',
		'verified',
		'rumor',
		'retracted',
		'active',
		'resolved',
		'closed'
	];

	// Get params from URL
	const urlKB = $derived($page.url.searchParams.get('kb') ?? undefined);
	const urlTag = $derived($page.url.searchParams.get('tag') ?? undefined);
	const activeKB = $derived(urlKB ?? kbStore.activeKB ?? undefined);

	onMount(() => {
		// Read initial filters from URL
		filterType = $page.url.searchParams.get('type') ?? '';
		filterStatus = $page.url.searchParams.get('status') ?? '';
		filterMinImportance = $page.url.searchParams.get('min_importance') ?? '';
		filterParticipant = $page.url.searchParams.get('participant') ?? '';
	});

	// Sync filter state to URL params
	function updateUrlParams() {
		const url = new URL($page.url);
		if (filterType) {
			url.searchParams.set('type', filterType);
		} else {
			url.searchParams.delete('type');
		}
		if (filterStatus) {
			url.searchParams.set('status', filterStatus);
		} else {
			url.searchParams.delete('status');
		}
		if (filterMinImportance) {
			url.searchParams.set('min_importance', filterMinImportance);
		} else {
			url.searchParams.delete('min_importance');
		}
		if (filterParticipant) {
			url.searchParams.set('participant', filterParticipant);
		} else {
			url.searchParams.delete('participant');
		}
		goto(url.toString(), { replaceState: true, keepFocus: true });
	}

	// Determine if participant filtering is active (client-side filter)
	const hasParticipantFilter = $derived(filterParticipant.trim().length > 0);

	// Load entries when KB, type filter, status, importance, or tag changes
	$effect(() => {
		const kb = activeKB;
		const type = filterType || undefined;
		const tag = urlTag;
		const status = filterStatus || undefined;
		const minImp = filterMinImportance ? parseInt(filterMinImportance, 10) : undefined;
		// Read filterParticipant to ensure this effect re-runs when it changes
		const _participant = filterParticipant;
		if (kb) {
			entryStore.loadList({ kb, entry_type: type, tag, status, min_importance: minImp });
		}
	});

	// Load entry types when KB changes
	$effect(() => {
		const kb = activeKB;
		if (kb) {
			api.getEntryTypes(kb)
				.then((res) => {
					entryTypes = res.types;
				})
				.catch(() => {
					entryTypes = [];
				});
		}
	});

	function onFilterChange() {
		updateUrlParams();
	}

	let participantDebounceTimer: ReturnType<typeof setTimeout> | undefined;
	function onParticipantInput() {
		clearTimeout(participantDebounceTimer);
		participantDebounceTimer = setTimeout(() => {
			updateUrlParams();
		}, 300);
	}

	function onSortChange(e: Event) {
		const value = (e.target as HTMLSelectElement).value;
		entryStore.sortBy = value;
		// Trigger reload via effect by updating URL
		updateUrlParams();
	}

	function toggleSortOrder() {
		entryStore.sortOrder = entryStore.sortOrder === 'desc' ? 'asc' : 'desc';
		updateUrlParams();
	}

	function clearTag() {
		const url = new URL($page.url);
		url.searchParams.delete('tag');
		goto(url.toString());
	}

	function clearStatus() {
		filterStatus = '';
		updateUrlParams();
	}

	function clearImportance() {
		filterMinImportance = '';
		updateUrlParams();
	}

	function clearParticipant() {
		filterParticipant = '';
		updateUrlParams();
	}

	function clearAllFilters() {
		filterType = '';
		filterStatus = '';
		filterMinImportance = '';
		filterParticipant = '';
		goto('/entries');
	}

	const hasActiveFilters = $derived(
		!!filterType || !!filterStatus || !!filterMinImportance || !!filterParticipant || !!urlTag
	);
</script>

<svelte:head><title>Entries — Pyrite</title></svelte:head>

<Topbar breadcrumbs={[{ label: 'Entries' }]} />

<div class="flex-1 overflow-y-auto p-6">
	<div class="mb-4 flex flex-wrap items-center justify-between gap-2">
		<h1 class="text-2xl font-bold">Entries</h1>
		<div class="flex flex-wrap items-center gap-3">
			<!-- Sort -->
			<select
				value={entryStore.sortBy}
				onchange={onSortChange}
				class="rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-800"
			>
				<option value="updated_at">Updated</option>
				<option value="created_at">Created</option>
				<option value="title">Title</option>
				<option value="entry_type">Type</option>
			</select>
			<button
				onclick={toggleSortOrder}
				class="rounded-md border border-zinc-300 px-2 py-1.5 text-sm dark:border-zinc-700"
				title="Toggle sort direction"
				aria-label="Toggle sort direction"
			>
				{entryStore.sortOrder === 'desc' ? '↓' : '↑'}
			</button>

			<!-- Type filter -->
			<select
				bind:value={filterType}
				onchange={onFilterChange}
				class="rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-800"
			>
				<option value="">All types</option>
				{#each entryTypes as t}
					<option value={t}>{t}</option>
				{/each}
			</select>

			<!-- Status filter -->
			<select
				bind:value={filterStatus}
				onchange={onFilterChange}
				class="rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-800"
			>
				<option value="">All statuses</option>
				{#each statusOptions as s}
					<option value={s}>{s}</option>
				{/each}
			</select>

			<!-- Importance filter -->
			<select
				bind:value={filterMinImportance}
				onchange={onFilterChange}
				class="rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-800"
			>
				<option value="">Any importance</option>
				{#each [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] as n}
					<option value={String(n)}>{n}+</option>
				{/each}
			</select>

			<!-- Participant filter -->
			<input
				type="text"
				placeholder="Participant..."
				bind:value={filterParticipant}
				oninput={onParticipantInput}
				class="w-36 rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-800"
			/>

			<!-- Search -->
			<input
				type="search"
				placeholder="Search..."
				value={searchStore.query}
				oninput={(e) => searchStore.setQuery(e.currentTarget.value)}
				class="rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-800"
			/>

			<!-- New entry -->
			<a
				href="/entries/new"
				class="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
			>
				New Entry
			</a>
		</div>
	</div>

	<!-- Active filter chips -->
	{#if hasActiveFilters}
		<div class="mb-3 flex flex-wrap items-center gap-2">
			<span class="text-sm text-zinc-500">Filters:</span>
			{#if urlTag}
				<span
					class="inline-flex items-center gap-1 rounded-full bg-blue-100 px-2.5 py-0.5 text-xs font-medium text-blue-800 dark:bg-blue-900/30 dark:text-blue-300"
				>
					tag: {urlTag}
					<button
						onclick={clearTag}
						class="ml-0.5 hover:text-blue-600 dark:hover:text-blue-100">&times;</button
					>
				</span>
			{/if}
			{#if filterType}
				<span
					class="inline-flex items-center gap-1 rounded-full bg-purple-100 px-2.5 py-0.5 text-xs font-medium text-purple-800 dark:bg-purple-900/30 dark:text-purple-300"
				>
					type: {filterType}
					<button
						onclick={() => {
							filterType = '';
							onFilterChange();
						}}
						class="ml-0.5 hover:text-purple-600 dark:hover:text-purple-100">&times;</button
					>
				</span>
			{/if}
			{#if filterStatus}
				<span
					class="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-900/30 dark:text-amber-300"
				>
					status: {filterStatus}
					<button
						onclick={clearStatus}
						class="ml-0.5 hover:text-amber-600 dark:hover:text-amber-100">&times;</button
					>
				</span>
			{/if}
			{#if filterMinImportance}
				<span
					class="inline-flex items-center gap-1 rounded-full bg-red-100 px-2.5 py-0.5 text-xs font-medium text-red-800 dark:bg-red-900/30 dark:text-red-300"
				>
					importance: {filterMinImportance}+
					<button
						onclick={clearImportance}
						class="ml-0.5 hover:text-red-600 dark:hover:text-red-100">&times;</button
					>
				</span>
			{/if}
			{#if filterParticipant}
				<span
					class="inline-flex items-center gap-1 rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-medium text-green-800 dark:bg-green-900/30 dark:text-green-300"
				>
					participant: {filterParticipant}
					<button
						onclick={clearParticipant}
						class="ml-0.5 hover:text-green-600 dark:hover:text-green-100">&times;</button
					>
				</span>
			{/if}
			<button
				onclick={clearAllFilters}
				class="text-xs text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
			>
				Clear all
			</button>
		</div>
	{/if}

	<!-- Search results or entry list -->
	{#if searchStore.query && searchStore.results.length > 0}
		<div class="mb-4 text-sm text-zinc-500">
			{searchStore.results.length} search results for "{searchStore.query}"
		</div>
		<div class="space-y-2">
			{#each searchStore.results as result}
				<a
					href={entryHref(result.id, result.kb_name)}
					class="block rounded-lg border border-zinc-200 p-3 hover:border-zinc-400 dark:border-zinc-700"
				>
					<div class="flex items-center justify-between">
						<span class="font-medium">{result.title}</span>
						<span class="text-xs text-zinc-500">{result.entry_type}</span>
					</div>
					{#if result.snippet}
						<p class="mt-1 text-sm text-zinc-400">{result.snippet}</p>
					{/if}
				</a>
			{/each}
		</div>
	{:else}
		<EntryList
			kb={urlKB ?? kbStore.activeKB ?? undefined}
			participantFilter={hasParticipantFilter ? filterParticipant.trim().toLowerCase() : ''}
		/>
	{/if}
</div>

<script lang="ts">
	import Topbar from '$lib/components/layout/Topbar.svelte';
	import EntryCard from '$lib/components/entry/EntryCard.svelte';
	import TableView from '$lib/components/collection/TableView.svelte';
	import KanbanView from '$lib/components/collection/KanbanView.svelte';
	import GalleryView from '$lib/components/collection/GalleryView.svelte';
	import ViewSwitcher from '$lib/components/collection/ViewSwitcher.svelte';
	import { collectionStore } from '$lib/stores/collections.svelte';
	import { onMount } from 'svelte';
	import { page } from '$app/state';

	const collectionId = $derived(page.params.id as string);
	const kb = $derived(page.url.searchParams.get('kb') ?? '');

	let sortBy = $state('title');
	let sortOrder = $state<'asc' | 'desc'>('asc');
	let offset = $state(0);
	const limit = 200;

	onMount(() => {
		if (collectionId && kb) {
			collectionStore.loadCollection(collectionId, kb);
			loadEntries();
		}
	});

	function loadEntries() {
		collectionStore.loadEntries(collectionId, kb, {
			sort_by: sortBy,
			sort_order: sortOrder,
			limit,
			offset
		});
	}

	function handleSort(column: string) {
		if (sortBy === column) {
			sortOrder = sortOrder === 'asc' ? 'desc' : 'asc';
		} else {
			sortBy = column;
			sortOrder = 'asc';
		}
		loadEntries();
	}

	function nextPage() {
		if (offset + limit < collectionStore.total) {
			offset += limit;
			loadEntries();
		}
	}

	function prevPage() {
		if (offset > 0) {
			offset = Math.max(0, offset - limit);
			loadEntries();
		}
	}

	const breadcrumbs = $derived([
		{ label: 'Collections', href: '/collections' },
		{ label: collectionStore.activeCollection?.title ?? collectionId }
	]);

	// Typed accessors for view_config (which is Record<string, unknown>)
	function vcGet<T>(path: string, fallback: T): T {
		const vc = collectionStore.activeCollection?.view_config;
		if (!vc) return fallback;
		const parts = path.split('.');
		let cur: unknown = vc;
		for (const p of parts) {
			if (cur && typeof cur === 'object' && p in cur) {
				cur = (cur as Record<string, unknown>)[p];
			} else {
				return fallback;
			}
		}
		return cur as T;
	}

	const tableColumns = $derived(() => {
		return vcGet<string[]>('table_columns', ['title', 'entry_type', 'tags', 'updated_at']);
	});
</script>

<svelte:head><title>{collectionStore.activeCollection?.title ?? 'Collection'} — Pyrite</title></svelte:head>

<Topbar {breadcrumbs} />

<div class="flex-1 overflow-y-auto p-6">
	<div class="mb-4 flex items-center justify-between">
		<div>
			<h1 class="text-2xl font-bold">
				{#if collectionStore.activeCollection?.icon}
					<span class="mr-2">{collectionStore.activeCollection.icon}</span>
				{/if}
				{collectionStore.activeCollection?.title ?? collectionId}
			</h1>
			{#if collectionStore.activeCollection?.description}
				<p class="mt-1 text-sm text-zinc-400">{collectionStore.activeCollection.description}</p>
			{/if}
		</div>
		<div class="flex items-center gap-3">
			<span class="text-sm text-zinc-400">{collectionStore.total} entries</span>
			<ViewSwitcher />
		</div>
	</div>

	{#if collectionStore.loading}
		<div class="flex items-center justify-center py-12">
			<div class="h-6 w-6 animate-spin rounded-full border-2 border-zinc-300 border-t-blue-600"></div>
		</div>
	{:else if collectionStore.error}
		<div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
			{collectionStore.error}
		</div>
	{:else if collectionStore.viewMode === 'table'}
		<TableView
			entries={collectionStore.entries}
			columns={tableColumns()}
			{sortBy}
			{sortOrder}
			onSort={handleSort}
		/>
	{:else if collectionStore.viewMode === 'kanban'}
		<KanbanView
			entries={collectionStore.entries}
			groupBy={vcGet<string>('kanban.group_by', 'status')}
			columnOrder={vcGet<string[] | undefined>('kanban.column_order', undefined)}
		/>
	{:else if collectionStore.viewMode === 'gallery'}
		<GalleryView
			entries={collectionStore.entries}
			cardFields={vcGet<string[] | undefined>('gallery.card_fields', undefined)}
		/>
	{:else}
		<div class="space-y-2">
			{#each collectionStore.entries as entry}
				<EntryCard {entry} />
			{/each}
			{#if collectionStore.entries.length === 0}
				<div class="py-12 text-center text-zinc-400">
					No entries in this collection
				</div>
			{/if}
		</div>
	{/if}

	<!-- Pagination -->
	{#if collectionStore.total > limit}
		<div class="mt-4 flex items-center justify-between">
			<button
				onclick={prevPage}
				disabled={offset === 0}
				class="rounded-md border border-zinc-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-zinc-700"
			>
				Previous
			</button>
			<span class="text-sm text-zinc-400">
				{offset + 1} - {Math.min(offset + limit, collectionStore.total)} of {collectionStore.total}
			</span>
			<button
				onclick={nextPage}
				disabled={offset + limit >= collectionStore.total}
				class="rounded-md border border-zinc-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-zinc-700"
			>
				Next
			</button>
		</div>
	{/if}
</div>

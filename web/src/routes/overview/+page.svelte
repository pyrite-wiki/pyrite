<script lang="ts">
	import { entryHref } from '$lib/utils/entry-href';
	import Topbar from '$lib/components/layout/Topbar.svelte';
	import TypeDistributionChart from '$lib/components/charts/TypeDistributionChart.svelte';
	import { kbStore } from '$lib/stores/kbs.svelte';
	import { api } from '$lib/api/client';
	import { onMount } from 'svelte';
	import type { StatsResponse, TagCount } from '$lib/api/types';
	import type { EntryResponse } from '$lib/api/types';

	let stats = $state<StatsResponse | null>(null);
	let topTags = $state<TagCount[]>([]);
	let recentEntries = $state<EntryResponse[]>([]);

	function formatRelativeDate(dateStr?: string): string {
		if (!dateStr) return '';
		const date = new Date(dateStr);
		const now = new Date();
		const diffMs = now.getTime() - date.getTime();
		const diffMins = Math.floor(diffMs / 60000);
		if (diffMins < 1) return 'just now';
		if (diffMins < 60) return `${diffMins}m ago`;
		const diffHours = Math.floor(diffMins / 60);
		if (diffHours < 24) return `${diffHours}h ago`;
		const diffDays = Math.floor(diffHours / 24);
		if (diffDays < 7) return `${diffDays}d ago`;
		return date.toLocaleDateString();
	}

	onMount(async () => {
		try {
			stats = await api.getStats();
			const tagsRes = await api.getTags();
			topTags = tagsRes.tags.slice(0, 10);
		} catch {
			// stats may fail if index is empty
		}
		try {
			const entriesRes = await api.listEntries({ limit: 5, sort_by: 'updated_at', sort_order: 'desc' });
			recentEntries = entriesRes.entries;
		} catch {
			// entries may be empty
		}
	});
</script>

<svelte:head><title>Overview — Pyrite</title></svelte:head>

<Topbar title="Overview" />

{#if stats && stats.total_entries === 0}
	<div class="flex flex-1 flex-col items-center justify-center py-16 text-center">
		<img src="/pyrite-mark-light.svg" alt="" class="mb-6 h-16 w-16 dark:hidden" />
		<img src="/pyrite-mark.svg" alt="" class="mb-6 hidden h-16 w-16 dark:block" />
		<h2 class="mb-2 text-2xl font-bold">Welcome to Pyrite</h2>
		<p class="mb-6 max-w-md text-sm text-zinc-400">
			Your knowledge infrastructure is ready. Start by creating your first entry
			or importing existing notes.
		</p>
		<div class="flex gap-3">
			<a href="/entries/new" class="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">
				Create First Entry
			</a>
			<a href="/entries/clip" class="rounded-md border border-zinc-600 px-4 py-2 text-sm font-medium text-zinc-300 hover:border-zinc-400">
				Clip a Web Page
			</a>
		</div>
		<p class="mt-8 text-xs text-zinc-600">
			Or use the CLI: <code class="rounded bg-zinc-800 px-1.5 py-0.5 font-mono">pyrite create -k my-kb -t note --title "My First Note"</code>
		</p>
	</div>
{:else}
<div class="flex-1 overflow-y-auto p-6">
	<h1 class="page-title mb-6 text-2xl font-bold">Overview</h1>

	<!-- Stats Cards -->
	<div class="mb-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
		<!-- Knowledge Bases -->
		<div class="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-700">
			<div class="h-1 bg-amber-500"></div>
			<div class="flex items-center gap-3 p-4">
				<div class="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-500/10">
					<svg class="h-5 w-5 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
						<path stroke-linecap="round" stroke-linejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
					</svg>
				</div>
				<div>
					<div class="text-sm text-zinc-500">Knowledge Bases</div>
					<div class="text-2xl font-bold">{kbStore.kbs.length}</div>
				</div>
			</div>
		</div>

		<!-- Total Entries -->
		<div class="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-700">
			<div class="h-1 bg-blue-500"></div>
			<div class="flex items-center gap-3 p-4">
				<div class="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-500/10">
					<svg class="h-5 w-5 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
						<path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
					</svg>
				</div>
				<div>
					<div class="text-sm text-zinc-500">Total Entries</div>
					<div class="text-2xl font-bold">{stats?.total_entries ?? '-'}</div>
				</div>
			</div>
		</div>

		<!-- Tags -->
		<div class="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-700">
			<div class="h-1 bg-emerald-500"></div>
			<div class="flex items-center gap-3 p-4">
				<div class="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-500/10">
					<svg class="h-5 w-5 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
						<path stroke-linecap="round" stroke-linejoin="round" d="M9.568 3H5.25A2.25 2.25 0 003 5.25v4.318c0 .597.237 1.17.659 1.591l9.581 9.581c.699.699 1.78.872 2.607.33a18.095 18.095 0 005.223-5.223c.542-.827.369-1.908-.33-2.607L11.16 3.66A2.25 2.25 0 009.568 3z M6 6h.008v.008H6V6z" />
					</svg>
				</div>
				<div>
					<div class="text-sm text-zinc-500">Tags</div>
					<div class="text-2xl font-bold">{stats?.total_tags ?? '-'}</div>
				</div>
			</div>
		</div>

		<!-- Links -->
		<div class="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-700">
			<div class="h-1 bg-purple-500"></div>
			<div class="flex items-center gap-3 p-4">
				<div class="flex h-10 w-10 items-center justify-center rounded-lg bg-purple-500/10">
					<svg class="h-5 w-5 text-purple-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
						<path stroke-linecap="round" stroke-linejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
					</svg>
				</div>
				<div>
					<div class="text-sm text-zinc-500">Links</div>
					<div class="text-2xl font-bold">{stats?.total_links ?? '-'}</div>
				</div>
			</div>
		</div>
	</div>

	<!-- Two-column: Recent Entries + Quick Actions/KBs -->
	<div class="mb-8 grid gap-6 lg:grid-cols-5">
		<!-- Recent Entries: 3 cols -->
		<div class="lg:col-span-3">
			<div class="mb-3 flex items-center justify-between">
				<h2 class="text-lg font-semibold">Recent Entries</h2>
				<a href="/entries" class="text-sm text-blue-500 hover:underline">View all</a>
			</div>
			{#if recentEntries.length === 0}
				<p class="text-sm text-zinc-400">No entries yet.</p>
			{:else}
				<div class="space-y-2">
					{#each recentEntries as entry}
						<a
							href={entryHref(entry.id, entry.kb_name)}
							class="flex items-center justify-between rounded-lg border border-zinc-200 px-4 py-3 transition-colors hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-500"
						>
							<div class="min-w-0 flex-1">
								<div class="truncate font-medium">{entry.title}</div>
								<div class="text-xs text-zinc-500">{entry.entry_type} · {entry.kb_name}</div>
							</div>
							<span class="ml-3 shrink-0 text-xs text-zinc-400">{formatRelativeDate(entry.updated_at)}</span>
						</a>
					{/each}
				</div>
			{/if}
		</div>

		<!-- Right sidebar: 2 cols -->
		<div class="space-y-6 lg:col-span-2">
			<!-- Quick Actions -->
			<div>
				<h2 class="mb-3 text-lg font-semibold">Quick Actions</h2>
				<div class="space-y-2">
					<a
						href="/entries/new"
						class="flex items-center gap-2 rounded-lg border border-zinc-200 px-4 py-3 text-sm transition-colors hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-500"
					>
						<svg class="h-4 w-4 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
							<path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4" />
						</svg>
						New Entry
					</a>
					<a
						href="/graph"
						class="flex items-center gap-2 rounded-lg border border-zinc-200 px-4 py-3 text-sm transition-colors hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-500"
					>
						<svg class="h-4 w-4 text-purple-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
							<path stroke-linecap="round" stroke-linejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
						</svg>
						Knowledge Graph
					</a>
					<a
						href="/qa"
						class="flex items-center gap-2 rounded-lg border border-zinc-200 px-4 py-3 text-sm transition-colors hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-500"
					>
						<svg class="h-4 w-4 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
							<path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
						</svg>
						QA Dashboard
					</a>
				</div>
			</div>

			<!-- Entry Types -->
			{#if stats && stats.type_counts && stats.type_counts.length > 0}
				<div>
					<h2 class="mb-3 text-lg font-semibold">Entry Types</h2>
					<div class="rounded-lg border border-zinc-200 p-4 dark:border-zinc-700">
						<TypeDistributionChart data={stats.type_counts} />
					</div>
				</div>
			{/if}

			<!-- Knowledge Bases -->
			<div>
				<h2 class="mb-3 text-lg font-semibold">Knowledge Bases</h2>
				{#if kbStore.kbs.length === 0}
					<p class="text-sm text-zinc-400">No knowledge bases configured. Run <code>pyrite kb add</code> to get started.</p>
				{:else}
					<div class="space-y-2">
						{#each kbStore.kbs as kb}
							<a
								href="/orient?kb={kb.name}"
								class="flex items-center justify-between rounded-lg border border-zinc-200 px-4 py-3 transition-colors hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-500"
							>
								<div>
									<div class="font-medium">{kb.name}</div>
									<div class="text-xs text-zinc-400">{kb.entries} entries</div>
								</div>
								<span class="text-xs text-zinc-500">{kb.type}</span>
							</a>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	</div>

	<!-- Top Tags -->
	{#if topTags.length > 0}
		<div>
			<h2 class="mb-3 text-lg font-semibold">Top Tags</h2>
			<div class="flex flex-wrap gap-2">
				{#each topTags as tag}
					<a
						href="/entries?tag={encodeURIComponent(tag.name)}"
						class="rounded-full bg-zinc-100 px-3 py-1 text-sm transition-colors hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700"
					>
						{tag.name}
						<span class="ml-1 text-zinc-400">({tag.count})</span>
					</a>
				{/each}
			</div>
		</div>
	{/if}
</div>
{/if}

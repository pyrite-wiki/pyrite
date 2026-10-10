<script lang="ts">
	import Topbar from '$lib/components/layout/Topbar.svelte';
	import { api } from '$lib/api/client';
	import { kbStore } from '$lib/stores/kbs.svelte';
	import { typeColor } from '$lib/constants';
	import { page } from '$app/stores';
	import { entryHref } from '$lib/utils/entry-href';
	import { onMount } from 'svelte';
	import type { KBOrientResponse } from '$lib/api/types';

	let orient = $state<KBOrientResponse | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	const urlKB = $derived($page.url.searchParams.get('kb') ?? undefined);
	const activeKB = $derived(urlKB ?? kbStore.activeKB ?? undefined);

	$effect(() => {
		const kb = activeKB;
		if (kb) {
			loading = true;
			error = null;
			api.getKBOrient(kb, 10).then((res) => {
				orient = res;
			}).catch((e) => {
				error = e instanceof Error ? e.message : 'Failed to load orientation';
			}).finally(() => {
				loading = false;
			});
		}
	});

	function formatDate(iso: string): string {
		try {
			const d = new Date(iso);
			return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
		} catch {
			return iso;
		}
	}
</script>

<svelte:head><title>{orient?.kb ?? 'Orient'} — Pyrite</title></svelte:head>

<Topbar breadcrumbs={[{ label: orient?.kb ?? 'Orient' }]} />

<div class="flex-1 overflow-y-auto">
	{#if loading}
		<div class="flex items-center justify-center py-20">
			<span class="text-zinc-400">Loading orientation...</span>
		</div>
	{:else if error}
		<div class="flex items-center justify-center py-20 text-red-500">{error}</div>
	{:else if orient}
		<!-- Hero -->
		<div class="border-b border-zinc-200 bg-zinc-50 px-8 py-10 dark:border-zinc-800 dark:bg-zinc-900/50">
			<div class="max-w-4xl">
				<div class="mb-2 flex items-center gap-3">
					<h1 class="text-3xl font-bold">{orient.kb}</h1>
					<span class="rounded-full bg-zinc-200 px-2.5 py-0.5 text-xs font-medium text-zinc-600 dark:bg-zinc-700 dark:text-zinc-300">
						{orient.kb_type}
					</span>
					{#if orient.read_only}
						<span class="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
							read-only
						</span>
					{/if}
				</div>
				{#if orient.description}
					<p class="text-lg text-zinc-500 dark:text-zinc-400">{orient.description}</p>
				{/if}
				<div class="mt-4 flex items-center gap-6 text-sm text-zinc-500">
					<span><strong class="text-zinc-700 dark:text-zinc-200">{orient.total_entries}</strong> entries</span>
					<span><strong class="text-zinc-700 dark:text-zinc-200">{orient.types.length}</strong> types</span>
					<span><strong class="text-zinc-700 dark:text-zinc-200">{orient.top_tags.length}</strong> top tags</span>
				</div>
				<div class="mt-5 flex flex-wrap gap-2">
					<a href="/entries?kb={orient.kb}" class="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">
						Browse Entries
					</a>
					<a href="/graph?kb={orient.kb}" class="rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800">
						View Graph
					</a>
					<a href="/search" class="rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800">
						Search
					</a>
				</div>
			</div>
		</div>

		<div class="mx-auto max-w-5xl px-8 py-8">
			<div class="grid gap-8 lg:grid-cols-3">
				<!-- Entry Types -->
				<div class="lg:col-span-2">
					<h2 class="mb-4 text-lg font-semibold">Entry Types</h2>
					<div class="grid gap-2 sm:grid-cols-2">
						{#each orient.types as t}
							<a
								href="/entries?kb={orient.kb}&type={t.type}"
								class="flex items-center justify-between rounded-lg border border-zinc-200 p-3 transition-colors hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-500"
							>
								<div class="flex items-center gap-2">
									<span class="inline-block h-3 w-3 rounded-full" style="background-color: {typeColor(t.type)}"></span>
									<span class="text-sm font-medium">{t.type}</span>
								</div>
								<span class="text-sm text-zinc-500">{t.count}</span>
							</a>
						{/each}
					</div>
				</div>

				<!-- Top Tags -->
				<div>
					<h2 class="mb-4 text-lg font-semibold">Top Tags</h2>
					<div class="flex flex-wrap gap-1.5">
						{#each orient.top_tags as tag}
							<a
								href="/entries?kb={orient.kb}&tag={tag.name}"
								class="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700 hover:bg-blue-100 dark:bg-blue-900/20 dark:text-blue-400 dark:hover:bg-blue-900/30"
							>
								{tag.name}
								<span class="text-blue-400 dark:text-blue-600">({tag.count})</span>
							</a>
						{/each}
					</div>
				</div>
			</div>

			<!-- Guidelines -->
			{#if orient.guidelines && Object.keys(orient.guidelines).length > 0}
				<div class="mt-8">
					<h2 class="mb-4 text-lg font-semibold">Guidelines</h2>
					<div class="space-y-4">
						{#each Object.entries(orient.guidelines) as [key, value]}
							<div class="rounded-lg border border-zinc-200 p-4 dark:border-zinc-700">
								<h3 class="mb-2 text-sm font-semibold capitalize text-zinc-600 dark:text-zinc-300">{key}</h3>
								<p class="whitespace-pre-line text-sm text-zinc-500 dark:text-zinc-400">{value}</p>
							</div>
						{/each}
					</div>
				</div>
			{/if}

			<!-- Recent Changes -->
			{#if orient.recent.length > 0}
				<div class="mt-8">
					<h2 class="mb-4 text-lg font-semibold">Recent Changes</h2>
					<div class="space-y-1">
						{#each orient.recent as entry}
							<a
								href={entryHref(entry.id, orient.kb)}
								class="flex items-center justify-between rounded-lg px-3 py-2 transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800"
							>
								<div class="flex items-center gap-3">
									<span class="inline-block h-2 w-2 rounded-full" style="background-color: {typeColor(entry.entry_type)}"></span>
									<span class="text-sm font-medium">{entry.title}</span>
									<span class="text-xs text-zinc-500">{entry.entry_type}</span>
								</div>
								<span class="text-xs text-zinc-400">{formatDate(entry.updated_at)}</span>
							</a>
						{/each}
					</div>
				</div>
			{/if}
		</div>
	{/if}
</div>

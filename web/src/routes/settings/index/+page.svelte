<script lang="ts">
	import Topbar from '$lib/components/layout/Topbar.svelte';
	import ErrorState from '$lib/components/common/ErrorState.svelte';
	import { api } from '$lib/api/client';
	import { kbStore } from '$lib/stores/kbs.svelte';
	import type { EmbedStatusResponse, IndexJob, StatsResponse, KBHealthResponse } from '$lib/api/types';
	import { onMount } from 'svelte';

	let stats = $state<StatsResponse | null>(null);
	let embedStatus = $state<EmbedStatusResponse | null>(null);
	let jobs = $state<IndexJob[]>([]);
	let healthResults = $state<Record<string, KBHealthResponse>>({});

	let loading = $state(true);
	let error = $state<string | null>(null);
	let syncing = $state(false);
	let syncResult = $state<{ added: number; updated: number; removed: number } | null>(null);
	let healthLoading = $state<string | null>(null);
	let reindexing = $state<string | null>(null);

	async function loadAll() {
		loading = true;
		error = null;
		try {
			const [s, e, j] = await Promise.all([
				api.getStats(),
				api.getEmbedStatus().catch(() => null),
				api.getIndexJobs().catch(() => ({ jobs: [] }))
			]);
			stats = s;
			embedStatus = e;
			jobs = j.jobs;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to load index data';
		} finally {
			loading = false;
		}
	}

	async function handleSync() {
		syncing = true;
		syncResult = null;
		try {
			const result = await api.syncIndex();
			syncResult = { added: result.added, updated: result.updated, removed: result.removed };
			await loadAll();
		} catch (err) {
			error = err instanceof Error ? err.message : 'Sync failed';
		} finally {
			syncing = false;
		}
	}

	async function handleReindex(name: string) {
		reindexing = name;
		try {
			await kbStore.reindex(name);
			await loadAll();
		} catch (err) {
			error = err instanceof Error ? err.message : `Reindex of ${name} failed`;
		} finally {
			reindexing = null;
		}
	}

	async function handleHealth(name: string) {
		healthLoading = name;
		try {
			const result = await kbStore.getHealth(name);
			healthResults = { ...healthResults, [name]: result };
		} catch (err) {
			error = err instanceof Error ? err.message : `Health check failed for ${name}`;
		} finally {
			healthLoading = null;
		}
	}

	onMount(() => {
		kbStore.load();
		loadAll();
	});

	const totalEntries = $derived(stats?.total_entries ?? 0);
	const totalTags = $derived(stats?.total_tags ?? 0);
	const totalLinks = $derived(stats?.total_links ?? 0);
	const typeCounts = $derived(stats?.type_counts ?? []);
	const kbStats = $derived(stats?.kbs ?? {});
	const embedPending = $derived(embedStatus?.pending ?? 0);
	const embedProcessing = $derived(embedStatus?.processing ?? 0);
	const embedFailed = $derived(embedStatus?.failed ?? 0);
	const embedTotal = $derived(embedStatus?.total ?? 0);
	const embedComplete = $derived(embedTotal - embedPending - embedProcessing - embedFailed);
</script>

<svelte:head><title>Index Management — Pyrite</title></svelte:head>

<Topbar breadcrumbs={[{ label: 'Settings', href: '/settings' }, { label: 'Index' }]} />

<div class="flex-1 overflow-y-auto p-6">
	<div class="mx-auto max-w-3xl space-y-6">
		<div class="flex items-center justify-between">
			<div>
				<h1 class="text-2xl font-bold text-zinc-900 dark:text-zinc-100">Index Management</h1>
				<p class="mt-1 text-sm text-zinc-500">Monitor and manage the search index and embeddings.</p>
			</div>
			<button
				onclick={handleSync}
				disabled={syncing}
				class="flex items-center gap-2 rounded-lg bg-gold-500 px-4 py-2 text-sm font-medium text-zinc-900 transition-colors hover:bg-gold-400 disabled:opacity-50"
			>
				{#if syncing}
					<svg class="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
						<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
						<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
					</svg>
					Syncing...
				{:else}
					<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
					</svg>
					Sync Index
				{/if}
			</button>
		</div>

		<!-- Sync result feedback -->
		{#if syncResult}
			<div class="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-400">
				Sync complete: +{syncResult.added} added, ~{syncResult.updated} updated, -{syncResult.removed} removed
			</div>
		{/if}

		{#if error}
			<ErrorState message={error} onretry={loadAll} />
		{/if}

		{#if loading}
			<div class="flex items-center justify-center py-16 text-zinc-400">
				<svg class="mr-3 h-5 w-5 animate-spin" fill="none" viewBox="0 0 24 24">
					<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
					<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
				</svg>
				Loading index data...
			</div>
		{:else}
			<!-- Stats overview cards -->
			<div class="grid grid-cols-2 gap-4 sm:grid-cols-4">
				<div class="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-700 dark:bg-zinc-800">
					<div class="text-2xl font-bold text-zinc-900 dark:text-zinc-100">{totalEntries}</div>
					<div class="text-xs text-zinc-500">Entries</div>
				</div>
				<div class="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-700 dark:bg-zinc-800">
					<div class="text-2xl font-bold text-zinc-900 dark:text-zinc-100">{Object.keys(kbStats).length}</div>
					<div class="text-xs text-zinc-500">Knowledge Bases</div>
				</div>
				<div class="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-700 dark:bg-zinc-800">
					<div class="text-2xl font-bold text-zinc-900 dark:text-zinc-100">{totalTags}</div>
					<div class="text-xs text-zinc-500">Tags</div>
				</div>
				<div class="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-700 dark:bg-zinc-800">
					<div class="text-2xl font-bold text-zinc-900 dark:text-zinc-100">{totalLinks}</div>
					<div class="text-xs text-zinc-500">Links</div>
				</div>
			</div>

			<!-- Embedding status -->
			{#if embedStatus}
				<section>
					<h2 class="mb-3 text-lg font-semibold text-zinc-900 dark:text-zinc-100">Embeddings</h2>
					<div class="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-700 dark:bg-zinc-800">
						<div class="mb-3 flex items-center justify-between">
							<span class="text-sm text-zinc-500">
								{embedComplete} of {embedTotal} entries embedded
							</span>
							{#if embedPending > 0 || embedProcessing > 0}
								<span class="rounded-full bg-amber-500/20 px-2 py-0.5 text-xs text-amber-400">
									{embedPending + embedProcessing} queued
								</span>
							{:else if embedFailed > 0}
								<span class="rounded-full bg-red-500/20 px-2 py-0.5 text-xs text-red-400">
									{embedFailed} failed
								</span>
							{:else if embedTotal > 0}
								<span class="rounded-full bg-emerald-500/20 px-2 py-0.5 text-xs text-emerald-400">
									Complete
								</span>
							{/if}
						</div>
						<!-- Progress bar -->
						{#if embedTotal > 0}
							<div class="h-2 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
								<div
									class="h-full rounded-full transition-all duration-500 {embedFailed > 0 ? 'bg-amber-500' : 'bg-emerald-500'}"
									style="width: {Math.round((embedComplete / embedTotal) * 100)}%"
								></div>
							</div>
							<div class="mt-2 flex gap-4 text-xs text-zinc-500">
								<span>{embedComplete} embedded</span>
								{#if embedPending > 0}<span>{embedPending} pending</span>{/if}
								{#if embedProcessing > 0}<span>{embedProcessing} processing</span>{/if}
								{#if embedFailed > 0}<span class="text-red-400">{embedFailed} failed</span>{/if}
							</div>
						{:else}
							<p class="text-sm text-zinc-500">No entries in embedding queue. Configure an AI provider to enable semantic search.</p>
						{/if}
					</div>
				</section>
			{/if}

			<!-- Type distribution -->
			{#if typeCounts.length > 0}
				<section>
					<h2 class="mb-3 text-lg font-semibold text-zinc-900 dark:text-zinc-100">Entry Types</h2>
					<div class="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-700 dark:bg-zinc-800">
						<div class="space-y-2">
							{#each typeCounts as tc}
								<div class="flex items-center justify-between">
									<span class="text-sm text-zinc-700 dark:text-zinc-300">{tc.entry_type}</span>
									<div class="flex items-center gap-2">
										<div class="h-1.5 w-24 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
											<div
												class="h-full rounded-full bg-gold-500"
												style="width: {Math.round(((tc.count as number) / totalEntries) * 100)}%"
											></div>
										</div>
										<span class="w-8 text-right text-xs text-zinc-500">{tc.count}</span>
									</div>
								</div>
							{/each}
						</div>
					</div>
				</section>
			{/if}

			<!-- Per-KB breakdown -->
			<section>
				<h2 class="mb-3 text-lg font-semibold text-zinc-900 dark:text-zinc-100">Per-KB Index</h2>
				<div class="space-y-3">
					{#each kbStore.kbs as kb}
						{@const kbStat = kbStats[kb.name] as Record<string, unknown> | undefined}
						<div class="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-700 dark:bg-zinc-800">
							<div class="flex items-center justify-between">
								<div>
									<span class="font-medium text-zinc-900 dark:text-zinc-100">{kb.name}</span>
									{#if kbStat}
										<span class="ml-2 text-sm text-zinc-500">{kbStat.total ?? 0} entries</span>
									{/if}
								</div>
								<div class="flex gap-2">
									<button
										onclick={() => handleHealth(kb.name)}
										disabled={healthLoading === kb.name}
										class="rounded px-2 py-1 text-xs text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-700 dark:hover:text-zinc-300"
									>
										{healthLoading === kb.name ? 'Checking...' : 'Health'}
									</button>
									<button
										onclick={() => handleReindex(kb.name)}
										disabled={reindexing === kb.name}
										class="rounded px-2 py-1 text-xs text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-700 dark:hover:text-zinc-300"
									>
										{reindexing === kb.name ? 'Reindexing...' : 'Reindex'}
									</button>
								</div>
							</div>
							<!-- Health result -->
							{#if healthResults[kb.name]}
								{@const h = healthResults[kb.name]}
								<div class="mt-3 rounded border border-zinc-100 bg-zinc-50 p-3 text-xs dark:border-zinc-700 dark:bg-zinc-800/50">
									<div class="flex items-center gap-2 {h.healthy ? 'text-emerald-500' : 'text-red-400'}">
										{#if h.healthy}
											<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg>
											Healthy
										{:else}
											<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
											Issues found
										{/if}
									</div>
									<div class="mt-1 text-zinc-500">
										{h.file_count} files, {h.entry_count} indexed
										{#if h.last_indexed}
											| Last indexed: {new Date(h.last_indexed).toLocaleString()}
										{/if}
									</div>
								</div>
							{/if}
						</div>
					{/each}
				</div>
			</section>

			<!-- Active jobs -->
			{#if jobs.length > 0}
				<section>
					<h2 class="mb-3 text-lg font-semibold text-zinc-900 dark:text-zinc-100">Active Jobs</h2>
					<div class="rounded-lg border border-zinc-200 bg-white dark:border-zinc-700 dark:bg-zinc-800">
						{#each jobs as job}
							<div class="flex items-center justify-between border-b border-zinc-100 px-4 py-3 last:border-0 dark:border-zinc-700">
								<div>
									<span class="text-sm font-medium text-zinc-900 dark:text-zinc-100">{job.operation}</span>
									<span class="ml-2 text-xs text-zinc-500">{job.kb}</span>
								</div>
								<span class="rounded-full px-2 py-0.5 text-xs {job.status === 'running' ? 'bg-blue-500/20 text-blue-400' : 'bg-zinc-500/20 text-zinc-400'}">
									{job.status}
								</span>
							</div>
						{/each}
					</div>
				</section>
			{/if}
		{/if}
	</div>
</div>

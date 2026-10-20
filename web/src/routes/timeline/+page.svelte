<script lang="ts">
	import { entryHref } from '$lib/utils/entry-href';
	import Topbar from '$lib/components/layout/Topbar.svelte';
	import SkeletonLoader from '$lib/components/common/SkeletonLoader.svelte';
	import EmptyState from '$lib/components/common/EmptyState.svelte';
	import TagBadge from '$lib/components/common/TagBadge.svelte';
	import { api } from '$lib/api/client';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import type { TimelineEvent } from '$lib/api/types';

	let events = $state<TimelineEvent[]>([]);
	let loading = $state(true);

	// Filters
	let dateFrom = $state('');
	let dateTo = $state('');
	let minImportance = $state(0);

	// Group events by year-month
	interface EventGroup {
		label: string;
		events: TimelineEvent[];
	}

	const filteredEvents = $derived(
		events
			.filter((e) => {
				if (dateFrom && e.date < dateFrom) return false;
				if (dateTo && e.date > dateTo) return false;
				if (minImportance > 0 && e.importance < minImportance) return false;
				return true;
			})
			.sort((a, b) => b.date.localeCompare(a.date))
	);

	const grouped = $derived(groupByMonth(filteredEvents));

	function groupByMonth(items: TimelineEvent[]): EventGroup[] {
		const map = new Map<string, TimelineEvent[]>();
		for (const item of items) {
			const key = item.date.slice(0, 7); // YYYY-MM
			if (!map.has(key)) map.set(key, []);
			map.get(key)!.push(item);
		}
		return Array.from(map.entries()).map(([key, evts]) => ({
			label: formatMonthLabel(key),
			events: evts
		}));
	}

	function formatMonthLabel(ym: string): string {
		const [year, month] = ym.split('-');
		const months = [
			'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
			'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
		];
		return `${months[parseInt(month) - 1]} ${year}`;
	}

	function importanceColor(imp: number): string {
		if (imp >= 8) return 'bg-red-500';
		if (imp >= 6) return 'bg-amber-500';
		if (imp >= 4) return 'bg-blue-500';
		return 'bg-zinc-400';
	}

	async function loadTimeline() {
		loading = true;
		try {
			const opts: Record<string, string | number> = { limit: 500 };
			if (dateFrom) opts.date_from = dateFrom;
			if (dateTo) opts.date_to = dateTo;
			if (minImportance > 0) opts.min_importance = minImportance;
			const res = await api.getTimeline(opts);
			events = res.events;
		} catch {
			events = [];
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		loadTimeline();
	});
</script>

<svelte:head><title>Timeline — Pyrite</title></svelte:head>

<Topbar title="Timeline" />
<div class="flex-1 overflow-y-auto p-6">
	<h1 class="mb-4 text-2xl font-bold">Timeline</h1>

	<!-- Filters -->
	<div class="mb-6 flex flex-wrap items-end gap-4">
		<div>
			<label for="date-from" class="block text-xs font-medium text-zinc-500 dark:text-zinc-400">From</label>
			<input
				id="date-from"
				type="date"
				bind:value={dateFrom}
				class="mt-1 rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-600 dark:bg-zinc-800"
			/>
		</div>
		<div>
			<label for="date-to" class="block text-xs font-medium text-zinc-500 dark:text-zinc-400">To</label>
			<input
				id="date-to"
				type="date"
				bind:value={dateTo}
				class="mt-1 rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-600 dark:bg-zinc-800"
			/>
		</div>
		<div>
			<label for="min-imp" class="block text-xs font-medium text-zinc-500 dark:text-zinc-400">Min importance</label>
			<input
				id="min-imp"
				type="number"
				min="0"
				max="10"
				bind:value={minImportance}
				class="mt-1 w-20 rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-600 dark:bg-zinc-800"
			/>
		</div>
		<button
			onclick={loadTimeline}
			class="rounded-md bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
		>
			Apply
		</button>
		<span class="text-sm text-zinc-400">{filteredEvents.length} events</span>
	</div>

	{#if loading}
		<SkeletonLoader variant="timeline" lines={6} />
	{:else if filteredEvents.length === 0}
		<EmptyState
			icon="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
			title="No timeline events found"
			description="Add dates to your entries to see them on the timeline."
			actionLabel="Browse Entries"
			actionHref="/entries"
		/>
	{:else}
		<!-- Visual timeline -->
		<div class="relative ml-4">
			<!-- Vertical line -->
			<div class="absolute left-3 top-0 bottom-0 w-px bg-zinc-300 dark:bg-zinc-600"></div>

			{#each grouped as group}
				<!-- Month label -->
				<div class="relative mb-4 pl-10">
					<div class="absolute left-0 top-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-zinc-200 dark:bg-zinc-700">
						<span class="text-[10px] font-bold text-zinc-600 dark:text-zinc-300">
							{group.label.slice(0, 3)}
						</span>
					</div>
					<h2 class="text-sm font-semibold text-zinc-500 dark:text-zinc-400">{group.label}</h2>
				</div>

				{#each group.events as event}
					<div class="relative mb-3 pl-10">
						<!-- Dot on the timeline -->
						<div
							class="absolute left-1.5 top-2 h-3 w-3 rounded-full ring-2 ring-white dark:ring-zinc-900 {importanceColor(event.importance)}"
						></div>

						<a
							href={entryHref(event.id, event.kb_name)}
							class="block rounded-lg border border-zinc-200 p-3 transition-colors hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-500"
						>
							<div class="flex items-center gap-3">
								<span class="shrink-0 font-mono text-xs text-zinc-500">{event.date}</span>
								<span class="flex-1 font-medium">{event.title}</span>
								{#if event.importance >= 7}
									<span class="rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-semibold text-red-700 dark:bg-red-900/30 dark:text-red-300">
										{event.importance}
									</span>
								{:else}
									<span class="text-xs text-zinc-400">{event.importance}</span>
								{/if}
							</div>
							{#if event.tags.length > 0}
								<div class="mt-1 flex flex-wrap gap-1">
									{#each event.tags.slice(0, 5) as tag}
										<TagBadge {tag} onclick={(e) => { e.preventDefault(); e.stopPropagation(); goto(`/entries?tag=${encodeURIComponent(tag)}`); }} />
									{/each}
									{#if event.tags.length > 5}
										<span class="inline-flex items-center rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-500 dark:bg-zinc-700 dark:text-zinc-400">
											+{event.tags.length - 5}
										</span>
									{/if}
								</div>
							{/if}
						</a>
					</div>
				{/each}
			{/each}
		</div>
	{/if}
</div>

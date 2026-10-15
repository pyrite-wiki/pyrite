<script lang="ts">
	import { entryHref } from '$lib/utils/entry-href';
	import TagBadge from '$lib/components/common/TagBadge.svelte';
	import type { EntryResponse } from '$lib/api/types';
	import { goto } from '$app/navigation';
	import { typeBgColor } from '$lib/constants';

	interface Props {
		entries: EntryResponse[];
		columns?: string[];
		sortBy?: string;
		sortOrder?: 'asc' | 'desc';
		onSort?: (column: string) => void;
	}

	let {
		entries,
		columns = ['title', 'entry_type', 'tags', 'updated_at'],
		sortBy = 'title',
		sortOrder = 'asc',
		onSort
	}: Props = $props();

	const columnLabels: Record<string, string> = {
		title: 'Title',
		entry_type: 'Type',
		tags: 'Tags',
		updated_at: 'Updated',
		created_at: 'Created',
		date: 'Date',
		importance: 'Importance'
	};

	function handleSort(column: string) {
		if (column !== 'tags' && onSort) {
			onSort(column);
		}
	}

	function formatDate(dateStr?: string): string {
		if (!dateStr) return '';
		try {
			return new Date(dateStr).toLocaleDateString();
		} catch {
			return dateStr;
		}
	}
</script>

<div class="overflow-x-auto">
	<table class="w-full text-sm">
		<thead>
			<tr class="border-b border-zinc-200 dark:border-zinc-700">
				{#each columns as col}
					<th
						class="px-3 py-2 text-left font-medium text-zinc-500 dark:text-zinc-400
							{col !== 'tags' ? 'cursor-pointer hover:text-zinc-700 dark:hover:text-zinc-200' : ''}"
						onclick={() => handleSort(col)}
					>
						<span class="flex items-center gap-1">
							{columnLabels[col] ?? col}
							{#if sortBy === col}
								<span class="text-xs">{sortOrder === 'asc' ? '↑' : '↓'}</span>
							{/if}
						</span>
					</th>
				{/each}
			</tr>
		</thead>
		<tbody>
			{#each entries as entry}
				<tr
					class="cursor-pointer border-b border-zinc-100 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-800/50"
					onclick={() => goto(entryHref(entry.id, entry.kb_name))}
				>
					{#each columns as col}
						<td class="px-3 py-2">
							{#if col === 'title'}
								<span class="font-medium">{entry.title}</span>
							{:else if col === 'entry_type'}
								<span class="inline-block rounded px-1.5 py-0.5 text-xs {typeBgColor(entry.entry_type)}">
									{entry.entry_type}
								</span>
							{:else if col === 'tags'}
								<div class="flex flex-wrap gap-1">
									{#each entry.tags.slice(0, 3) as tag}
										<TagBadge {tag} />
									{/each}
									{#if entry.tags.length > 3}
										<span class="text-xs text-zinc-400">+{entry.tags.length - 3}</span>
									{/if}
								</div>
							{:else if col === 'updated_at' || col === 'created_at' || col === 'date'}
								<span class="text-zinc-500">{formatDate(entry[col] as string | undefined)}</span>
							{:else if col === 'importance'}
								{#if entry.importance}
									<span class="text-zinc-500">{entry.importance}</span>
								{/if}
							{:else}
								<span class="text-zinc-500">{col in entry ? String((entry as unknown as Record<string, unknown>)[col] ?? '') : ''}</span>
							{/if}
						</td>
					{/each}
				</tr>
			{/each}
			{#if entries.length === 0}
				<tr>
					<td colspan={columns.length} class="px-3 py-8 text-center text-zinc-400">
						No entries in this collection
					</td>
				</tr>
			{/if}
		</tbody>
	</table>
</div>

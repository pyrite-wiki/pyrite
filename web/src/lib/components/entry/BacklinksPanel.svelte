<!--
  BacklinksPanel: Shows entries that link to the current entry.
  Displayed in the split pane side panel on the entry detail page.

  The API returns one row per link the source writes (#1009): a frontmatter
  `links:` item and a body `[[x]]` are two rows from one source. The panel
  lists each source once, with each of its links' relation and note.
-->
<script lang="ts">
	interface Backlink {
		id: string;
		title: string;
		kb_name: string;
		entry_type: string;
		snippet?: string;
		note?: string;
		forward_relation?: string;
		[key: string]: unknown;
	}

	interface SourceGroup {
		key: string;
		source: Backlink;
		links: { relation: string; note: string }[];
	}

	interface Props {
		backlinks: Record<string, unknown>[];
		loading?: boolean;
	}

	let { backlinks, loading = false }: Props = $props();

	// One group per source entry (id and KB), in the order the rows came.
	const items = $derived.by(() => {
		const groups = new Map<string, SourceGroup>();
		for (const row of backlinks as unknown as Backlink[]) {
			const key = row.kb_name + '\u0000' + row.id;
			let group = groups.get(key);
			if (!group) {
				group = { key, source: row, links: [] };
				groups.set(key, group);
			}
			group.links.push({ relation: row.forward_relation ?? '', note: row.note ?? '' });
		}
		return [...groups.values()];
	});
</script>

<div class="flex h-full flex-col overflow-hidden">
	<div
		class="flex items-center justify-between border-b border-zinc-200 px-4 py-2 dark:border-zinc-800"
	>
		<h2 class="text-sm font-semibold text-zinc-600 dark:text-zinc-400">
			Backlinks ({items.length})
		</h2>
	</div>

	<div class="flex-1 overflow-y-auto">
		{#if loading}
			<div class="flex items-center justify-center p-4">
				<span class="text-sm text-zinc-400">Loading backlinks...</span>
			</div>
		{:else if items.length === 0}
			<div class="flex items-center justify-center p-6">
				<span class="text-sm text-zinc-400">No entries link here yet</span>
			</div>
		{:else}
			<ul class="divide-y divide-zinc-100 dark:divide-zinc-800/50">
				{#each items as group (group.key)}
					{@const link = group.source}
					<li>
						<a
							href="/entries/{link.id}?kb={link.kb_name}"
							class="block px-4 py-3 hover:bg-zinc-100 dark:hover:bg-zinc-800"
						>
							<div class="flex items-start gap-2">
								<div class="min-w-0 flex-1">
									<div class="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">
										{link.title || link.id}
									</div>
									{#if link.snippet}
										<p class="mt-0.5 line-clamp-2 text-xs text-zinc-500 dark:text-zinc-400">
											{link.snippet}
										</p>
									{/if}
									<div class="mt-1 flex flex-wrap gap-1">
										{#each group.links as written, i (i)}
											{#if written.relation}
												<span
													class="inline-flex items-center rounded-full bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400"
												>
													{written.relation}
												</span>
											{/if}
											{#if written.note}
												<span
													class="inline-flex items-center rounded-full bg-blue-100 px-1.5 py-0.5 text-[10px] font-medium text-blue-600 dark:bg-blue-900 dark:text-blue-400"
												>
													{written.note}
												</span>
											{/if}
										{/each}
									</div>
								</div>
								<span
									class="inline-flex shrink-0 items-center rounded-full bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"
								>
									{link.entry_type}
								</span>
							</div>
						</a>
					</li>
				{/each}
			</ul>
		{/if}
	</div>
</div>

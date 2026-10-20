<script lang="ts">
	import { entryHref } from '$lib/utils/entry-href';
	import TagBadge from '$lib/components/common/TagBadge.svelte';
	import type { EntryResponse } from '$lib/api/types';
	import { goto } from '$app/navigation';
	import { typeColor } from '$lib/constants';

	interface Props {
		entry: EntryResponse;
	}

	let { entry }: Props = $props();

	const borderColor = $derived(typeColor(entry.entry_type));

	function stripWikilinks(text: string): string {
		return text.replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, '$2').replace(/\[\[([^\]]+)\]\]/g, '$1');
	}

	const preview = $derived(
		entry.summary
			? stripWikilinks(entry.summary)
			: entry.body
				? stripWikilinks(entry.body.slice(0, 200))
				: ''
	);

	function onTagClick(tag: string) {
		return (e: MouseEvent) => {
			e.preventDefault();
			e.stopPropagation();
			goto(`/entries?tag=${encodeURIComponent(tag)}`);
		};
	}
</script>

<a
	href={entryHref(entry.id, entry.kb_name)}
	class="block rounded-lg border border-zinc-200 p-5 transition-colors hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-500"
	style="border-left: 3px solid {borderColor}"
>
	<div class="mb-1 flex items-center justify-between">
		<div class="flex items-center gap-2">
			<h3 class="font-medium">{entry.title}</h3>
			{#if entry.status}
				<span class="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
					{entry.status}
				</span>
			{/if}
		</div>
		<div class="flex items-center gap-2">
			{#if entry.importance && entry.importance >= 8}
				<span class="text-[10px] font-semibold text-amber-500" title="Importance: {entry.importance}/10">
					{entry.importance}
				</span>
			{/if}
			<span class="rounded bg-zinc-100 px-1.5 py-0.5 text-xs text-zinc-500 dark:bg-zinc-800">
				{entry.entry_type}
			</span>
		</div>
	</div>

	{#if preview}
		<p class="mb-2 line-clamp-2 text-sm text-zinc-400 dark:text-zinc-400">{preview}</p>
	{/if}

	<div class="flex items-center justify-between">
		<div class="flex flex-wrap gap-1">
			{#each entry.tags.slice(0, 3) as tag}
				<TagBadge {tag} onclick={onTagClick(tag)} />
			{/each}
			{#if entry.tags.length > 3}
				<span class="text-xs text-zinc-400">+{entry.tags.length - 3}</span>
			{/if}
		</div>
		<span class="text-xs text-zinc-400">{entry.kb_name}</span>
	</div>
</a>

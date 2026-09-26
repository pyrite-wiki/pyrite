<script lang="ts">
	import TagBadge from '$lib/components/common/TagBadge.svelte';
	import type { EntryResponse } from '$lib/api/types';
	import { isSafeLinkUrl } from '$lib/utils/safe-url';

	interface Props {
		entry: EntryResponse;
	}

	let { entry }: Props = $props();
</script>

<div class="space-y-3 text-sm">
	<div>
		<span class="text-zinc-500">Type:</span>
		<span class="ml-2 rounded bg-zinc-100 px-1.5 py-0.5 text-xs dark:bg-zinc-800">{entry.entry_type}</span>
	</div>
	<div>
		<span class="text-zinc-500">KB:</span>
		<span class="ml-2">{entry.kb_name}</span>
	</div>
	{#if entry.date}
		<div>
			<span class="text-zinc-500">Date:</span>
			<span class="ml-2">{entry.date}</span>
		</div>
	{/if}
	{#if entry.status}
		<div>
			<span class="text-zinc-500">Status:</span>
			<span class="ml-2 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
				{entry.status}
			</span>
		</div>
	{/if}
	{#if entry.importance}
		<div>
			<span class="text-zinc-500">Importance:</span>
			<span class="ml-2 font-medium {entry.importance >= 8 ? 'text-amber-500' : ''}">{entry.importance}/10</span>
		</div>
	{/if}
	{#if entry.tags.length > 0}
		<div>
			<span class="text-zinc-500">Tags:</span>
			<div class="mt-1 flex flex-wrap gap-1">
				{#each entry.tags as tag}
					<TagBadge {tag} href="/entries?tag={encodeURIComponent(tag)}" />
				{/each}
			</div>
		</div>
	{/if}
	{#if entry.participants.length > 0}
		<div>
			<span class="text-zinc-500">Participants:</span>
			<div class="mt-1 flex flex-wrap gap-1">
				{#each entry.participants as p}
					<a
						href="/search?q={encodeURIComponent(p)}"
						class="inline-flex items-center rounded-md bg-zinc-100 px-1.5 py-0.5 text-xs font-medium text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
					>{p}</a>
				{/each}
			</div>
		</div>
	{/if}
	{#if entry.sources && entry.sources.length > 0}
		<div>
			<span class="text-zinc-500">Sources:</span>
			<div class="mt-1 space-y-1">
				{#each entry.sources as source}
					<div class="rounded border border-zinc-200 px-2 py-1 text-xs dark:border-zinc-700">
						<div class="font-medium">{source.title || 'Untitled'}</div>
						{#if source.url}
							{@const url = String(source.url)}
							{@const label = url.slice(0, 50) + (url.length > 50 ? '...' : '')}
							{#if isSafeLinkUrl(url)}
								<a href={url} target="_blank" rel="noopener noreferrer" class="text-blue-500 hover:underline">
									{label}
								</a>
							{:else}
								<span class="text-zinc-400">{label}</span>
							{/if}
						{/if}
						{#if source.outlet || source.date}
							<span class="text-zinc-400">
								{#if source.outlet}— {source.outlet}{/if}
								{#if source.date} ({source.date}){/if}
							</span>
						{/if}
					</div>
				{/each}
			</div>
		</div>
	{/if}
	{#if entry.outlinks && entry.outlinks.length > 0}
		<div>
			<span class="text-zinc-500">Links to:</span>
			<div class="mt-1 space-y-0.5">
				{#each entry.outlinks as link}
					<a
						href="/entries/{link.id || link.target_id}"
						class="block rounded px-2 py-0.5 text-xs text-blue-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
					>
						{link.title || link.id || link.target_id}
						{#if link.relation}
							<span class="text-zinc-400">({link.relation})</span>
						{/if}
					</a>
				{/each}
			</div>
		</div>
	{/if}
	<div class="text-xs text-zinc-400">
		<div>ID: {entry.id}</div>
		<div>File: {entry.file_path}</div>
		{#if entry.created_at}
			<div>Created: {new Date(entry.created_at).toLocaleString()}</div>
		{/if}
		{#if entry.updated_at}
			<div>Updated: {new Date(entry.updated_at).toLocaleString()}</div>
		{/if}
	</div>
</div>

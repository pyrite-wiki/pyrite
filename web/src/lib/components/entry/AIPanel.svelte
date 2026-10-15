<script lang="ts">
	import { entryHref } from '$lib/utils/entry-href';
	import { api } from '$lib/api/client';
	import { uiStore } from '$lib/stores/ui.svelte';
	import { aiChatStore } from '$lib/stores/ai.svelte';
	import type { AITagSuggestion, AILinkSuggestion } from '$lib/api/types';
	import type { Snippet } from 'svelte';

	let {
		entryId,
		kbName,
		entryTitle,
		tags,
		onTagsChanged,
		toolbar,
	}: {
		entryId: string;
		kbName: string;
		entryTitle: string;
		tags: string[];
		onTagsChanged: (newTags: string[]) => void;
		toolbar: Snippet<[{ aiMenuButton: Snippet }]>;
	} = $props();

	// --- AI state ---
	let aiMenuOpen = $state(false);
	let aiLoading = $state(false);
	let summaryResult = $state<string | null>(null);
	let tagSuggestions = $state<AITagSuggestion[]>([]);
	let linkSuggestions = $state<AILinkSuggestion[]>([]);
	let aiResultType = $state<'summary' | 'tags' | 'links' | null>(null);

	// Close menu on outside click
	function handleClickOutside(e: MouseEvent) {
		if (aiMenuOpen) {
			const target = e.target as HTMLElement;
			if (!target.closest('.ai-menu-container')) {
				aiMenuOpen = false;
			}
		}
	}

	$effect(() => {
		document.addEventListener('click', handleClickOutside);
		return () => {
			document.removeEventListener('click', handleClickOutside);
		};
	});

	// --- AI handlers ---
	async function aiSummarize() {
		aiMenuOpen = false;
		aiLoading = true;
		aiResultType = 'summary';
		summaryResult = null;
		try {
			const res = await api.aiSummarize(entryId, kbName);
			summaryResult = res.summary;
		} catch (e) {
			uiStore.toast(e instanceof Error ? e.message : 'Summarize failed', 'error');
			aiResultType = null;
		} finally {
			aiLoading = false;
		}
	}

	async function aiAutoTag() {
		aiMenuOpen = false;
		aiLoading = true;
		aiResultType = 'tags';
		tagSuggestions = [];
		try {
			const res = await api.aiAutoTag(entryId, kbName);
			tagSuggestions = res.suggested_tags;
		} catch (e) {
			uiStore.toast(e instanceof Error ? e.message : 'Auto-tag failed', 'error');
			aiResultType = null;
		} finally {
			aiLoading = false;
		}
	}

	async function aiSuggestLinks() {
		aiMenuOpen = false;
		aiLoading = true;
		aiResultType = 'links';
		linkSuggestions = [];
		try {
			const res = await api.aiSuggestLinks(entryId, kbName);
			linkSuggestions = res.suggestions;
		} catch (e) {
			uiStore.toast(e instanceof Error ? e.message : 'Suggest links failed', 'error');
			aiResultType = null;
		} finally {
			aiLoading = false;
		}
	}

	async function acceptTag(tag: AITagSuggestion) {
		const currentTags = tags ?? [];
		if (currentTags.includes(tag.name)) return;
		const newTags = [...currentTags, tag.name];
		onTagsChanged(newTags);
		tagSuggestions = tagSuggestions.filter((t) => t.name !== tag.name);
		uiStore.toast(`Tag "${tag.name}" added`, 'success');
	}

	function dismissAIResult() {
		aiResultType = null;
		summaryResult = null;
		tagSuggestions = [];
		linkSuggestions = [];
	}

	function askAboutEntry() {
		aiChatStore.clear();
		uiStore.chatPanelOpen = true;
		aiChatStore.entryContext = {
			id: entryId,
			kb: kbName,
			title: entryTitle
		};
	}
</script>

<!-- AI menu button snippet, rendered by the parent toolbar -->
{#snippet aiMenuButton()}
	<div class="ai-menu-container relative">
		<button
			onclick={() => (aiMenuOpen = !aiMenuOpen)}
			class="rounded-md border px-3 py-1 text-sm {aiMenuOpen
				? 'border-purple-500 bg-purple-50 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300'
				: 'border-zinc-300 dark:border-zinc-600'}"
		>
			AI
		</button>
		{#if aiMenuOpen}
			<div class="absolute right-0 top-full z-50 mt-1 w-48 rounded-md border border-zinc-200 bg-white py-1 shadow-lg dark:border-zinc-700 dark:bg-zinc-800">
				<button
					onclick={aiSummarize}
					class="w-full px-3 py-2 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-700"
				>
					Summarize
				</button>
				<button
					onclick={aiAutoTag}
					class="w-full px-3 py-2 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-700"
				>
					Suggest Tags
				</button>
				<button
					onclick={aiSuggestLinks}
					class="w-full px-3 py-2 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-700"
				>
					Find Links
				</button>
				<hr class="my-1 border-zinc-200 dark:border-zinc-700" />
				<button
					onclick={askAboutEntry}
					class="w-full px-3 py-2 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-700"
				>
					Ask AI about this
				</button>
			</div>
		{/if}
	</div>
{/snippet}

<!-- Toolbar (rendered by parent via snippet prop, receives aiMenuButton snippet) -->
{@render toolbar({ aiMenuButton })}

<!-- AI Results banner -->
{#if aiResultType}
	<div class="border-b border-zinc-200 bg-purple-50 px-6 py-3 dark:border-zinc-800 dark:bg-purple-900/20">
		<div class="flex items-start justify-between">
			<div class="flex-1">
				{#if aiLoading}
					<p class="text-sm text-purple-700 dark:text-purple-300">Processing...</p>
				{:else if aiResultType === 'summary' && summaryResult}
					<p class="mb-1 text-xs font-semibold uppercase text-purple-600 dark:text-purple-400">AI Summary</p>
					<p class="text-sm text-zinc-800 dark:text-zinc-200">{summaryResult}</p>
				{:else if aiResultType === 'tags' && tagSuggestions.length > 0}
					<p class="mb-2 text-xs font-semibold uppercase text-purple-600 dark:text-purple-400">Suggested Tags</p>
					<div class="flex flex-wrap gap-2">
						{#each tagSuggestions as tag}
							<button
								onclick={() => acceptTag(tag)}
								class="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium {tag.is_new
									? 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300'
									: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300'} hover:opacity-80"
								title="{tag.reason}{tag.is_new ? ' (new tag)' : ''}"
							>
								{tag.name}
								<span class="text-[10px] opacity-60">+</span>
							</button>
						{/each}
					</div>
				{:else if aiResultType === 'tags'}
					<p class="text-sm text-zinc-500">No tag suggestions.</p>
				{:else if aiResultType === 'links' && linkSuggestions.length > 0}
					<p class="mb-2 text-xs font-semibold uppercase text-purple-600 dark:text-purple-400">Link Suggestions</p>
					<div class="space-y-1">
						{#each linkSuggestions as link}
							<div class="flex items-center gap-2 text-sm">
								<a
									href={entryHref(link.target_id, link.target_kb)}
									class="font-medium text-blue-600 hover:underline dark:text-blue-400"
								>
									{link.target_title || link.target_id}
								</a>
								<span class="text-xs text-zinc-500">{link.reason}</span>
							</div>
						{/each}
					</div>
				{:else if aiResultType === 'links'}
					<p class="text-sm text-zinc-500">No link suggestions.</p>
				{/if}
			</div>
			{#if !aiLoading}
				<button
					onclick={dismissAIResult}
					class="ml-2 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
				>
					&times;
				</button>
			{/if}
		</div>
	</div>
{/if}

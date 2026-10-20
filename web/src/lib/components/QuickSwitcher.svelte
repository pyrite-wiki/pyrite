<script lang="ts">
	import { entryHref } from '$lib/utils/entry-href';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api/client';
	import type { SearchResult } from '$lib/api/types';
	import { registerShortcut } from '$lib/utils/keyboard';
	import { onMount } from 'svelte';

	let open = $state(false);
	let query = $state('');
	let results = $state<SearchResult[]>([]);
	let selectedIndex = $state(0);
	let loading = $state(false);
	let inputEl = $state<HTMLInputElement | null>(null);

	let debounceTimer: ReturnType<typeof setTimeout> | null = null;

	function show() {
		open = true;
		query = '';
		results = [];
		selectedIndex = 0;
		setTimeout(() => inputEl?.focus(), 10);
	}

	function hide() {
		open = false;
		query = '';
		results = [];
	}

	function doSearch(q: string) {
		if (debounceTimer) clearTimeout(debounceTimer);
		if (!q.trim()) {
			results = [];
			return;
		}
		debounceTimer = setTimeout(async () => {
			loading = true;
			try {
				const res = await api.search(q, { limit: 20 });
				results = res.results;
				selectedIndex = 0;
			} catch {
				results = [];
			} finally {
				loading = false;
			}
		}, 300);
	}

	function onInput(e: Event) {
		const target = e.target as HTMLInputElement;
		query = target.value;
		doSearch(query);
	}

	function navigate(id: string, kb?: string) {
		hide();
		goto(entryHref(id, kb));
	}

	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'ArrowDown') {
			e.preventDefault();
			selectedIndex = Math.min(selectedIndex + 1, results.length - 1);
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			selectedIndex = Math.max(selectedIndex - 1, 0);
		} else if (e.key === 'Enter' && results.length > 0) {
			e.preventDefault();
			navigate(results[selectedIndex].id, results[selectedIndex].kb_name);
		} else if (e.key === 'Escape') {
			e.preventDefault();
			hide();
		}
	}

	function onBackdropClick(e: MouseEvent) {
		if ((e.target as HTMLElement).dataset.backdrop) {
			hide();
		}
	}

	onMount(() => {
		const unsub = registerShortcut('o', ['mod'], () => {
			if (open) {
				hide();
			} else {
				show();
			}
		});
		return unsub;
	});
</script>

{#if open}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-[15vh]"
		data-backdrop="true"
		data-testid="quick-switcher-backdrop"
		onkeydown={onKeydown}
		onclick={onBackdropClick}
	>
		<div
			class="w-full max-w-xl rounded-xl bg-white shadow-2xl dark:bg-zinc-900"
			role="dialog"
			aria-modal="true"
			aria-label="Quick search"
		>
			<div class="flex items-center border-b border-zinc-200 px-4 dark:border-zinc-700">
				<svg class="mr-3 h-5 w-5 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
				</svg>
				<input
					bind:this={inputEl}
					type="text"
					value={query}
					oninput={onInput}
					placeholder="Search entries..."
					class="w-full bg-transparent py-4 text-lg text-zinc-900 outline-none placeholder:text-zinc-400 dark:text-zinc-100"
					data-testid="quick-switcher-input"
					role="combobox"
					aria-expanded="true"
					aria-controls="qs-listbox"
					aria-label="Search entries"
					aria-activedescendant={results.length > 0 ? `qs-option-${selectedIndex}` : undefined}
				/>
				<kbd class="ml-2 hidden rounded bg-zinc-100 px-2 py-0.5 text-xs text-zinc-500 sm:inline dark:bg-zinc-800 dark:text-zinc-400">Esc</kbd>
			</div>

			{#if loading}
				<div class="px-4 py-3 text-sm text-zinc-500">Searching...</div>
			{:else if results.length > 0}
				<ul id="qs-listbox" class="max-h-80 overflow-y-auto py-2" role="listbox" data-testid="quick-switcher-results">
					{#each results as result, i (`${result.kb_name}:${result.id}`)}
						<!-- svelte-ignore a11y_click_events_have_key_events -->
						<li
							id="qs-option-{i}"
							role="option"
							aria-selected={i === selectedIndex}
							class="flex cursor-pointer items-center gap-3 px-4 py-2 text-sm transition-colors {i === selectedIndex ? 'bg-blue-50 text-blue-900 dark:bg-blue-900/30 dark:text-blue-200' : 'text-zinc-700 hover:bg-zinc-50 dark:text-zinc-300 dark:hover:bg-zinc-800'}"
							onclick={() => navigate(result.id, result.kb_name)}
							onmouseenter={() => (selectedIndex = i)}
							data-testid="quick-switcher-result"
						>
							<span class="rounded bg-zinc-100 px-1.5 py-0.5 text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
								{result.entry_type}
							</span>
							<span class="truncate font-medium">{result.title}</span>
							<span class="ml-auto shrink-0 text-xs text-zinc-400">{result.kb_name}</span>
						</li>
					{/each}
				</ul>
			<div class="border-t border-zinc-200 px-4 py-2 dark:border-zinc-700">
				<a
					href="/search?q={encodeURIComponent(query)}"
					onclick={hide}
					class="flex items-center gap-1.5 text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
					data-testid="quick-switcher-see-all"
				>
					<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
					</svg>
					See all results for "{query}"
				</a>
			</div>
			{:else if query.trim()}
				<div class="px-4 py-3 text-sm text-zinc-500" data-testid="quick-switcher-empty">No results found</div>
			{:else}
				<div class="px-4 py-3 text-sm text-zinc-500">Type to search across all entries</div>
			{/if}

			<div class="flex items-center gap-4 border-t border-zinc-200 px-4 py-2 text-xs text-zinc-400 dark:border-zinc-700">
				<span><kbd class="rounded bg-zinc-100 px-1 dark:bg-zinc-800">↑↓</kbd> navigate</span>
				<span><kbd class="rounded bg-zinc-100 px-1 dark:bg-zinc-800">↵</kbd> open</span>
				<span><kbd class="rounded bg-zinc-100 px-1 dark:bg-zinc-800">esc</kbd> close</span>
			</div>
		</div>
	</div>
{/if}

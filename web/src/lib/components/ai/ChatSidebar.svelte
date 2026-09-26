<script lang="ts">
	import { aiChatStore } from '$lib/stores/ai.svelte';
	import { uiStore } from '$lib/stores/ui.svelte';
	import { tick } from 'svelte';
	import { renderCitations } from './renderCitations';

	let inputText = $state('');
	let messagesContainer: HTMLDivElement | undefined = $state();
	let inputEl: HTMLTextAreaElement | undefined = $state();

	// Auto-scroll to bottom when messages change
	$effect(() => {
		// Track message length to trigger scroll
		const _len = aiChatStore.messages.length;
		const _lastContent = aiChatStore.messages[aiChatStore.messages.length - 1]?.content;
		tick().then(() => {
			if (messagesContainer) {
				messagesContainer.scrollTop = messagesContainer.scrollHeight;
			}
		});
	});

	// Auto-focus input when panel opens
	$effect(() => {
		if (uiStore.chatPanelOpen) {
			tick().then(() => inputEl?.focus());
		}
	});

	async function handleSend() {
		const text = inputText.trim();
		if (!text || aiChatStore.loading) return;
		inputText = '';
		await aiChatStore.send(text);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			handleSend();
		}
	}

</script>

<aside class="flex w-96 shrink-0 flex-col border-l border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
	<!-- Header -->
	<div class="flex items-center justify-between border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
		<div class="flex items-center gap-2">
			<h2 class="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Chat with KB</h2>
			{#if aiChatStore.entryContext}
				<span class="rounded bg-purple-100 px-1.5 py-0.5 text-[10px] text-purple-700 dark:bg-purple-900/30 dark:text-purple-300">
					{aiChatStore.entryContext.title}
				</span>
			{/if}
		</div>
		<div class="flex items-center gap-1">
			<button
				onclick={() => aiChatStore.clear()}
				class="rounded p-1 text-xs text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
				title="Clear chat"
			>
				Clear
			</button>
			<button
				onclick={() => (uiStore.chatPanelOpen = false)}
				class="rounded p-1 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
				title="Close"
			>
				&times;
			</button>
		</div>
	</div>

	<!-- Messages -->
	<div bind:this={messagesContainer} class="flex-1 overflow-y-auto p-4 space-y-3">
		{#if aiChatStore.messages.length === 0}
			<div class="flex h-full items-center justify-center">
				<p class="text-center text-sm text-zinc-400">
					Ask a question about your knowledge base.
				</p>
			</div>
		{:else}
			{#each aiChatStore.messages as msg, i}
				{#if msg.role === 'user'}
					<div class="flex justify-end">
						<div class="max-w-[80%] rounded-lg bg-blue-600 px-3 py-2 text-sm text-white">
							{msg.content}
						</div>
					</div>
				{:else}
					<div class="flex justify-start">
						<div class="max-w-[80%] rounded-lg bg-zinc-100 px-3 py-2 text-sm text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200">
							{#if msg.content}
								{@html renderCitations(msg.content)}
							{:else if aiChatStore.loading && i === aiChatStore.messages.length - 1}
								<span class="animate-pulse text-zinc-400">Thinking...</span>
							{/if}
						</div>
					</div>
				{/if}
			{/each}

			<!-- Sources -->
			{#if aiChatStore.sources.length > 0 && !aiChatStore.loading}
				<div class="border-t border-zinc-200 pt-2 dark:border-zinc-700">
					<p class="mb-1 text-[10px] font-semibold uppercase text-zinc-400">Sources</p>
					<div class="space-y-1">
						{#each aiChatStore.sources as src}
							<a
								href="/entries/{src.id}"
								class="block rounded border border-zinc-200 px-2 py-1.5 text-xs hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
							>
								<span class="font-medium text-blue-600 dark:text-blue-400">{src.title}</span>
								{#if src.snippet}
									<p class="mt-0.5 text-zinc-500 line-clamp-2">{src.snippet}</p>
								{/if}
							</a>
						{/each}
					</div>
				</div>
			{/if}
		{/if}
	</div>

	<!-- Error -->
	{#if aiChatStore.error}
		<div class="border-t border-red-200 bg-red-50 px-4 py-2 dark:border-red-800 dark:bg-red-900/20">
			<p class="text-xs text-red-600 dark:text-red-400">{aiChatStore.error}</p>
		</div>
	{/if}

	<!-- Input -->
	<div class="border-t border-zinc-200 p-3 dark:border-zinc-800">
		<div class="flex items-end gap-2">
			<textarea
				bind:this={inputEl}
				bind:value={inputText}
				onkeydown={handleKeydown}
				placeholder="Ask a question..."
				rows={1}
				class="flex-1 resize-none rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none dark:border-zinc-600 dark:bg-zinc-800"
			></textarea>
			<button
				onclick={handleSend}
				disabled={aiChatStore.loading || !inputText.trim()}
				class="rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
			>
				{aiChatStore.loading ? '...' : 'Send'}
			</button>
		</div>
	</div>
</aside>

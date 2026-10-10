<script lang="ts">
	import { api } from '$lib/api/client';
	import { uiStore } from '$lib/stores/ui.svelte';

	interface Props {
		entryId: string;
		kbName: string;
		onDeleted: () => void;
	}

	let { entryId, kbName, onDeleted }: Props = $props();

	let confirming = $state(false);
	let deleting = $state(false);

	async function confirmDelete() {
		deleting = true;
		try {
			await api.deleteEntry(entryId, kbName);
			uiStore.toast('Entry deleted', 'success');
			onDeleted();
		} catch {
			uiStore.toast('Could not delete entry', 'error');
			confirming = false;
		} finally {
			deleting = false;
		}
	}
</script>

{#if confirming}
	<button
		onclick={confirmDelete}
		disabled={deleting}
		aria-label="Confirm delete"
		class="rounded-md border border-red-600 bg-red-600 px-3 py-1 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
	>
		{deleting ? 'Deleting...' : 'Confirm delete'}
	</button>
	<button
		onclick={() => (confirming = false)}
		disabled={deleting}
		aria-label="Cancel delete"
		class="rounded-md border border-zinc-300 px-3 py-1 text-sm dark:border-zinc-600"
	>
		Cancel
	</button>
{:else}
	<button
		onclick={() => (confirming = true)}
		aria-label="Delete entry"
		class="rounded-md border border-zinc-300 px-3 py-1 text-sm dark:border-zinc-600"
	>
		Delete
	</button>
{/if}

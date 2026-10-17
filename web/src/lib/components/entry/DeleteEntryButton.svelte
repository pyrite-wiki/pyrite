<script lang="ts">
	import { tick } from 'svelte';
	import { api, ApiError } from '$lib/api/client';
	import { uiStore } from '$lib/stores/ui.svelte';

	interface Props {
		entryId: string;
		kbName: string;
		entryTitle: string;
		/** Called with the KB the delete request named, so the caller navigates
		 *  to the KB it actually removed from rather than re-reading the store
		 *  after the fact (#117). */
		onDeleted: (kbName: string) => void;
	}

	let { entryId, kbName, entryTitle, onDeleted }: Props = $props();

	let confirming = $state(false);
	let deleting = $state(false);
	let confirmPanel = $state<HTMLElement | null>(null);
	let deleteButton = $state<HTMLButtonElement | null>(null);

	async function startConfirm() {
		if (confirming) return;
		confirming = true;
		// Focus moves to the step itself, never to Confirm. Delete is disabled
		// for the length of the step, so focus has to land somewhere, and
		// landing it on Confirm makes a reader's second Enter -- plain
		// Enter-Enter, which is muscle memory -- destroy the entry before the
		// prompt naming it has been read. The panel is not actionable: Enter on
		// it does nothing, and Tab still reaches both buttons.
		await tick();
		confirmPanel?.focus();
	}

	async function cancel() {
		confirming = false;
		await tick();
		deleteButton?.focus();
	}

	async function confirmDelete() {
		if (deleting) return;
		deleting = true;
		// The KB this request names is the KB the reader is sent back to, even
		// if the store has moved on while the request was in flight.
		const deletedFrom = kbName;
		try {
			await api.deleteEntry(entryId, deletedFrom);
			uiStore.toast('Entry deleted', 'success');
			onDeleted(deletedFrom);
			// No re-enable here: the page navigates away and unmounts this
			// component, so there is no window in which a second click could
			// fire a second DELETE against an entry that is already gone.
		} catch (e) {
			if (e instanceof ApiError && e.status === 404) {
				// Already deleted (another tab, another client): the reader's
				// goal is achieved; say so and leave the entry page.
				uiStore.toast('Entry was already deleted', 'info');
				onDeleted(deletedFrom);
				return;
			}
			const reason = e instanceof ApiError && e.detail ? e.detail : '';
			uiStore.toast(reason ? `Could not delete: ${reason}` : 'Could not delete entry', 'error');
			deleting = false;
			confirming = false;
			await tick();
			deleteButton?.focus();
		}
	}
</script>

<!-- Only Delete sits in the toolbar's flow. The confirm step is positioned
     out of it, so opening the step cannot shift the row the reader is reading,
     and the second click of a double-click lands on a disabled Delete rather
     than on a Confirm that appeared in its slot. -->
<span class="relative inline-flex">
	<button
		bind:this={deleteButton}
		onclick={startConfirm}
		disabled={confirming || deleting}
		class="rounded-md border border-zinc-300 px-3 py-1 text-sm disabled:opacity-50 dark:border-zinc-600"
	>
		Delete
	</button>
	{#if confirming}
		<span
			bind:this={confirmPanel}
			tabindex="-1"
			role="group"
			aria-label="Confirm deleting this entry"
			class="absolute right-0 top-full z-20 mt-2 flex w-72 flex-col gap-2 rounded-md border border-zinc-300 bg-white p-3 text-left shadow-lg dark:border-zinc-600 dark:bg-zinc-900"
		>
			<span class="text-sm text-zinc-700 dark:text-zinc-300">
				Delete this entry from <span class="font-medium" title={kbName}>{kbName}</span>?
			</span>
			<!-- The title truncates; the KB never does, and each carries the
			     full value in `title` for a truncated or long one. -->
			<span class="block truncate text-sm text-zinc-500 dark:text-zinc-400" title={entryTitle}>
				{entryTitle}
			</span>
			<span class="flex items-center gap-2">
				<button
					onclick={confirmDelete}
					disabled={deleting}
					class="rounded-md border border-red-600 bg-red-600 px-3 py-1 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
				>
					{deleting ? 'Deleting...' : 'Confirm delete'}
				</button>
				<button
					onclick={cancel}
					disabled={deleting}
					class="rounded-md border border-zinc-300 px-3 py-1 text-sm dark:border-zinc-600"
				>
					Cancel
				</button>
			</span>
		</span>
	{/if}
</span>
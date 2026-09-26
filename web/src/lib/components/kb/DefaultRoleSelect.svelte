<script lang="ts">
	/**
	 * A KB's default role, as stored on the server.
	 *
	 * The select always shows the stored value: a refused change (a 409 for a
	 * KB set by hand in config.yaml, a failed save) is shown as an error and
	 * the select returns to what the server holds. A KB whose role is not
	 * editable here is shown disabled, with the reason.
	 */
	import { api, ApiError } from '$lib/api/client';

	interface Props {
		kbName: string;
		value: string | null;
		editable?: boolean;
		onSaved?: (role: string | null) => void;
	}

	let { kbName, value, editable = true, onSaved }: Props = $props();

	let saving = $state(false);
	let message = $state<{ ok: boolean; text: string } | null>(null);
	let select: HTMLSelectElement | undefined = $state();

	async function change(next: string) {
		const role = next === '' ? null : next;
		saving = true;
		message = null;
		try {
			await api.updateKBDefaultRole(kbName, role);
			message = { ok: true, text: 'Default role updated' };
			onSaved?.(role);
		} catch (e) {
			message = { ok: false, text: e instanceof ApiError ? e.detail : 'Failed to update' };
			// Back to what the server holds: the user's choice was not applied.
			if (select) select.value = value ?? '';
		} finally {
			saving = false;
		}
	}
</script>

<div class="flex items-center gap-3">
	<select
		bind:this={select}
		id="default-role"
		value={value ?? ''}
		class="px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm"
		disabled={saving || !editable}
		onchange={(e) => change(e.currentTarget.value)}
	>
		<option value="">Use global role</option>
		<option value="read">Read (public)</option>
		<option value="write">Write</option>
		<option value="none">None (private)</option>
	</select>
	{#if !editable}
		<span class="text-xs text-gray-500 dark:text-gray-400" data-testid="role-locked"
			>Set in the server's config.yaml; change it there.</span
		>
	{/if}
	{#if message}
		<span
			role={message.ok ? 'status' : 'alert'}
			class="text-xs {message.ok
				? 'text-green-600 dark:text-green-400'
				: 'text-red-600 dark:text-red-400'}">{message.text}</span
		>
	{/if}
</div>

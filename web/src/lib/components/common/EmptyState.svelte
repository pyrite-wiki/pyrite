<script lang="ts">
	interface Props {
		icon?: string; // SVG path for the icon
		title: string;
		description?: string;
		actionLabel?: string;
		actionHref?: string;
	}
	let { icon, title, description, actionLabel, actionHref }: Props = $props();
</script>

<div class="flex flex-col items-center justify-center py-16 text-center">
	<!-- Icon area -->
	{#if icon}
		<svg class="mb-4 h-12 w-12 text-zinc-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d={icon} />
		</svg>
	{/if}
	<p class="mb-1 text-lg font-medium text-zinc-700 dark:text-zinc-300">{title}</p>
	{#if description}
		<p class="mb-4 max-w-md text-sm text-zinc-500">{description}</p>
	{/if}
	{#if actionLabel && actionHref}
		<a
			href={actionHref}
			class="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
		>
			{actionLabel}
		</a>
	{/if}
</div>

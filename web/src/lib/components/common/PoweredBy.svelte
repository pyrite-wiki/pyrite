<script lang="ts">
	import { brandStore } from '$lib/stores/brand.svelte';
	import { isSafeLinkUrl } from '$lib/utils/safe-url';
</script>

<!--
  Pyrite credit line. Always visible — not gated by branding presence.
  On an un-branded deploy the sidebar title is already "Pyrite", so this
  just repeats it. On a branded deploy, this is how Pyrite gets credited.
-->
<div
	class="border-t border-zinc-200 px-3 py-2 text-[10px] tracking-wide text-zinc-500 dark:border-zinc-800 dark:text-zinc-500"
>
	Powered by
	{#if isSafeLinkUrl(brandStore.footer_credit_url)}
		<a
			href={brandStore.footer_credit_url}
			target="_blank"
			rel="noopener"
			class="text-zinc-400 underline-offset-2 hover:text-zinc-200 hover:underline"
		>
			Pyrite
		</a>
	{:else}
		<span class="text-zinc-400">Pyrite</span>
	{/if}
</div>

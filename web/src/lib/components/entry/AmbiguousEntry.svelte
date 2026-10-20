<script lang="ts">
	// Shown when GET /api/entries/{id} answers 409 AMBIGUOUS: the id is held by
	// more than one readable KB, so the reader picks which one (#910, #528).
	import { entryHref } from '$lib/utils/entry-href';

	let { entryId, kbs }: { entryId: string; kbs: string[] } = $props();
</script>

<div class="flex flex-1 items-center justify-center p-8" data-testid="ambiguous-entry">
	<div class="max-w-md">
		<h2 class="mb-2 text-lg font-semibold">"{entryId}" exists in more than one knowledge base</h2>
		<p class="mb-3 text-sm text-zinc-500">Choose which one to open:</p>
		<ul class="list-disc pl-5">
			{#each kbs as candidate (candidate)}
				<li>
					<a class="text-blue-500 hover:underline" href={entryHref(entryId, candidate)}>{candidate}</a>
				</li>
			{/each}
		</ul>
	</div>
</div>

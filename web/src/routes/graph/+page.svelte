<script lang="ts">
	import Topbar from '$lib/components/layout/Topbar.svelte';
	import GraphView from '$lib/components/graph/GraphView.svelte';
	import GraphControls from '$lib/components/graph/GraphControls.svelte';
	import LoadingState from '$lib/components/common/LoadingState.svelte';
	import { api } from '$lib/api/client';
	import { kbStore } from '$lib/stores/kbs.svelte';
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import type { GraphNode, GraphEdge } from '$lib/api/types';
	import { typeColor } from '$lib/constants';

	let nodes = $state<GraphNode[]>([]);
	let edges = $state<GraphEdge[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	let selectedKb = $state('');
	let selectedType = $state('');
	let depth = $state(2);
	let selectedLayout = $state('cose-bilkent');
	let searchQuery = $state('');
	let entryTypes = $state<string[]>([]);
	let sizeByCentrality = $state(false);

	let graphView: GraphView | undefined = $state();

	async function loadGraph() {
		loading = true;
		error = null;
		try {
			const res = await api.getGraph({
				kb: selectedKb || undefined,
				type: selectedType || undefined,
				depth,
				include_centrality: sizeByCentrality || undefined
			});
			nodes = res.nodes;
			edges = res.edges;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load graph';
		} finally {
			loading = false;
		}
	}

	async function loadEntryTypes() {
		try {
			const res = await api.getEntryTypes(selectedKb || undefined);
			entryTypes = res.types;
		} catch {
			// Keep whatever types we had
		}
	}

	onMount(() => {
		selectedKb = $page.url.searchParams.get('kb') ?? '';
		loadGraph();
		loadEntryTypes();
	});

	const kbNames = $derived(kbStore.kbs.map((kb) => kb.name));

	const hasCentrality = true; // Always show — toggling on triggers API reload with centrality data

	// Derive type counts from nodes for the legend
	const typeCounts = $derived(() => {
		const counts = new Map<string, number>();
		for (const n of nodes) {
			counts.set(n.entry_type, (counts.get(n.entry_type) || 0) + 1);
		}
		return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
	});
</script>

<svelte:head><title>Graph — Pyrite</title></svelte:head>

<Topbar title="Knowledge Graph" />

<div class="flex flex-1 flex-col gap-3 overflow-hidden p-4">
	<GraphControls
		kbs={kbNames}
		{entryTypes}
		{selectedKb}
		{selectedType}
		{depth}
		{selectedLayout}
		{hasCentrality}
		{sizeByCentrality}
		onKbChange={(kb) => { selectedKb = kb; loadGraph(); loadEntryTypes(); }}
		onTypeChange={(type) => { selectedType = type; loadGraph(); }}
		onDepthChange={(d) => { depth = d; loadGraph(); }}
		onLayoutChange={(layout) => { selectedLayout = layout; }}
		onSearch={(q) => { searchQuery = q; }}
		onCentralityToggle={(enabled) => { sizeByCentrality = enabled; loadGraph(); }}
		onResetLayout={() => graphView?.resetLayout()}
		onFit={() => graphView?.fit()}
	/>

	<div class="flex-1 overflow-hidden">
		{#if loading}
			<LoadingState message="Loading graph..." />
		{:else if error}
			<div class="flex h-full items-center justify-center text-red-500">{error}</div>
		{:else if nodes.length === 0}
			<div class="flex h-full items-center justify-center text-zinc-400">
				No linked entries found. Create links between entries to see the graph.
			</div>
		{:else}
			<GraphView bind:this={graphView} {nodes} {edges} layoutName={selectedLayout} {searchQuery} {sizeByCentrality} />
		{/if}
	</div>

	{#if !loading && nodes.length > 0}
		<div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-500">
			<span>{nodes.length} nodes, {edges.length} edges</span>
			<span class="text-zinc-700 dark:text-zinc-600">|</span>
			{#each typeCounts() as [type, count]}
				<span class="flex items-center gap-1">
					<span class="inline-block h-2.5 w-2.5 rounded-full" style="background-color: {typeColor(type)}"></span>
					{type}
					<span class="text-zinc-600">({count})</span>
				</span>
			{/each}
		</div>
	{/if}
</div>

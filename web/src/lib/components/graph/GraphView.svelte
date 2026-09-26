<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import type { GraphNode, GraphEdge } from '$lib/api/types';
	import { typeColor } from '$lib/constants';
	import { buildNodeTooltipLines, buildEdgeTooltipLines, type TooltipLine } from './tooltip';
	import type cytoscape from 'cytoscape';

	interface Props {
		nodes: GraphNode[];
		edges: GraphEdge[];
		centerId?: string;
		compact?: boolean;
		layoutName?: string;
		searchQuery?: string;
		sizeByCentrality?: boolean;
	}

	let { nodes, edges, centerId, compact = false, layoutName = 'cose-bilkent', searchQuery = '', sizeByCentrality = false }: Props = $props();

	let container: HTMLDivElement;
	let tooltipEl: HTMLDivElement;
	let cy: cytoscape.Core | undefined;

	function buildElements() {
		const nodeElements = nodes.map((n) => {
			const centrality = n.centrality ?? 0;
			const useCentrality = sizeByCentrality && centrality > 0;
			const size = useCentrality
				? Math.max(15, Math.min(60, 15 + centrality * 45))
				: Math.max(20, Math.min(50, 20 + n.link_count * 3));
			const nodeOpacity = useCentrality
				? Math.max(0.4, 0.4 + centrality * 0.6)
				: 1;
			return {
				data: {
					id: `${n.kb_name}/${n.id}`,
					label: n.title.length > 30 ? n.title.slice(0, 28) + '...' : n.title,
					fullTitle: n.title,
					entryType: n.entry_type,
					kbName: n.kb_name,
					entryId: n.id,
					linkCount: n.link_count,
					centrality,
					color: typeColor(n.entry_type),
					size,
					nodeOpacity,
					isCenter: n.id === centerId
				}
			};
		});

		const edgeElements = edges.map((e, i) => ({
			data: {
				id: `edge-${i}`,
				source: `${e.source_kb}/${e.source_id}`,
				target: `${e.target_kb}/${e.target_id}`,
				relation: e.relation || ''
			}
		}));

		return [...nodeElements, ...edgeElements];
	}

	function getLayoutConfig(name: string) {
		const base = {
			animate: false,
			nodeDimensionsIncludeLabels: true
		};
		switch (name) {
			case 'circle':
				return { ...base, name: 'circle' };
			case 'grid':
				return { ...base, name: 'grid' };
			case 'concentric':
				return { ...base, name: 'concentric', concentric: (node: cytoscape.NodeSingular) => node.data('linkCount') || 1, levelWidth: () => 2 };
			default:
				return {
					...base,
					name: 'cose-bilkent' as any,
					idealEdgeLength: compact ? 60 : 100,
					nodeRepulsion: compact ? 3000 : 4500,
					tilingPaddingVertical: 10,
					tilingPaddingHorizontal: 10
				};
		}
	}

	function showTooltip(x: number, y: number, lines: TooltipLine[]) {
		if (!tooltipEl) return;
		// Entry-derived text (title, type, KB name, edge relation) is set as
		// text, never as markup (P-B1) -- each line is its own element with
		// `textContent`, not a string concatenated into `innerHTML`.
		tooltipEl.replaceChildren(
			...lines.map((line) => {
				const el = document.createElement('div');
				if (line.className) el.className = line.className;
				el.textContent = line.text;
				return el;
			})
		);
		tooltipEl.style.left = `${x + 12}px`;
		tooltipEl.style.top = `${y - 10}px`;
		tooltipEl.style.display = 'block';
	}

	function hideTooltip() {
		if (!tooltipEl) return;
		tooltipEl.style.display = 'none';
	}

	async function initCytoscape() {
		const cytoscape = (await import('cytoscape')).default;
		const coseBilkent = (await import('cytoscape-cose-bilkent')).default;
		cytoscape.use(coseBilkent);

		cy = cytoscape({
			container,
			elements: buildElements(),
			style: [
				{
					selector: 'node',
					style: {
						'background-color': 'data(color)',
						label: 'data(label)',
						'font-size': compact ? '10px' : '11px',
						'text-valign': 'bottom',
						'text-halign': 'center',
						'text-margin-y': 5,
						color: '#a1a1aa',
						width: 'data(size)',
						height: 'data(size)',
						opacity: 'data(nodeOpacity)',
						'border-width': 0,
						'overlay-padding': 4
					} as unknown as cytoscape.Css.Node
				},
				{
					selector: 'node[?isCenter]',
					style: {
						'border-width': 3,
						'border-color': '#facc15'
					}
				},
				{
					selector: 'node:active',
					style: {
						'overlay-opacity': 0.1
					}
				},
				{
					selector: 'edge',
					style: {
						width: 1.5,
						'line-color': '#3f3f46',
						'target-arrow-color': '#3f3f46',
						'target-arrow-shape': 'triangle',
						'curve-style': 'bezier',
						'arrow-scale': 0.8,
						opacity: 0.6
					}
				},
				{
					selector: 'edge:active',
					style: {
						opacity: 1,
						width: 2.5
					}
				}
			],
			layout: getLayoutConfig(layoutName),
			minZoom: 0.2,
			maxZoom: 4,
			wheelSensitivity: 0.3
		});

		cy.on('tap', 'node', (evt: cytoscape.EventObject) => {
			const data = evt.target.data();
			goto(`/entries/${encodeURIComponent(data.entryId)}`);
		});

		cy.on('mouseover', 'node', (evt: cytoscape.EventObject) => {
			container.style.cursor = 'pointer';
			const data = evt.target.data();
			evt.target.style({
				'border-width': 2,
				'border-color': '#facc15',
				'underlay-color': '#facc15',
				'underlay-opacity': 0.15,
				'underlay-padding': 8
			});
			const pos = evt.renderedPosition;
			showTooltip(pos.x, pos.y, buildNodeTooltipLines(data));
		});

		cy.on('mouseout', 'node', (evt: cytoscape.EventObject) => {
			container.style.cursor = 'default';
			const isCenter = evt.target.data('isCenter');
			if (!isCenter) {
				evt.target.style({
					'border-width': 0,
					'underlay-opacity': 0
				});
			}
			hideTooltip();
		});

		cy.on('mouseover', 'edge', (evt: cytoscape.EventObject) => {
			const relation = evt.target.data('relation');
			if (relation) {
				const pos = evt.renderedPosition || evt.target.midpoint();
				showTooltip(pos.x, pos.y, buildEdgeTooltipLines(relation));
			}
		});

		cy.on('mouseout', 'edge', () => {
			hideTooltip();
		});
	}

	onMount(() => {
		if (nodes.length > 0) {
			initCytoscape();
		}
		return () => cy?.destroy();
	});

	$effect(() => {
		if (cy && nodes.length > 0) {
			cy.json({ elements: buildElements() });
			cy.layout(getLayoutConfig(layoutName)).run();
		} else if (!cy && nodes.length > 0 && container) {
			initCytoscape();
		}
	});

	// Search highlight effect
	$effect(() => {
		if (!cy) return;
		const q = searchQuery.toLowerCase().trim();
		if (!q) {
			// Reset all nodes to full opacity
			cy.nodes().style({ opacity: 1 });
			cy.edges().style({ opacity: 0.6 });
			return;
		}
		cy.nodes().forEach((node: cytoscape.NodeSingular) => {
			const label = (node.data('fullTitle') || '').toLowerCase();
			if (label.includes(q)) {
				node.style({ opacity: 1, 'border-width': 3, 'border-color': '#facc15' });
			} else {
				node.style({ opacity: 0.15, 'border-width': 0 });
			}
		});
		cy.edges().style({ opacity: 0.1 });
	});

	export function resetLayout() {
		cy?.layout({
			...getLayoutConfig(layoutName),
			animate: true,
			animationDuration: 500
		} as cytoscape.LayoutOptions).run();
	}

	export function fit() {
		cy?.fit(undefined, 40);
	}
</script>

<style>
	.graph-container {
		background-color: #09090b;
		background-image: radial-gradient(circle, #27272a 1px, transparent 1px);
		background-size: 24px 24px;
	}
</style>

<div class="relative h-full w-full">
	<div
		bind:this={container}
		class="graph-container h-full w-full {compact ? '' : 'rounded-lg border border-zinc-200 dark:border-zinc-800'}"
	></div>
	<div
		bind:this={tooltipEl}
		class="pointer-events-none absolute z-50 hidden max-w-xs rounded-lg bg-zinc-900 px-3 py-2 text-xs text-zinc-200 shadow-lg ring-1 ring-zinc-700"
		style="display: none;"
	></div>
</div>

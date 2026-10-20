import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/svelte';
import type { Core, CytoscapeOptions } from 'cytoscape';
import type { GraphNode, GraphEdge } from '$lib/api/types';

const graph = vi.hoisted(() => ({ instances: [] as Core[] }));
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('cytoscape-cose-bilkent', () => ({ default: () => {} }));
vi.mock('cytoscape', async () => {
	const actual = await vi.importActual<{ default: (options: CytoscapeOptions) => Core }>('cytoscape');
	const create = Object.assign((options: CytoscapeOptions) => {
		// Keep real graph collections, JSON replacement and stylesheet semantics;
		// only the canvas renderer and layout are unnecessary in jsdom.
		const instance = actual.default({ ...options, container: undefined, headless: true, styleEnabled: true, layout: { name: 'preset' } });
		graph.instances.push(instance);
		return instance;
	}, { use: () => {} });
	return { default: create };
});
import GraphView from './GraphView.svelte';
import { goto } from '$app/navigation';

const nodes: GraphNode[] = [
	{ id: 'hub', kb_name: 'test', title: 'Alpha Hub', entry_type: 'note', link_count: 1, centrality: 0.5 },
	{ id: 'spoke', kb_name: 'test', title: 'Beta Spoke', entry_type: 'note', link_count: 1, centrality: 0.2 }
];
const edges: GraphEdge[] = [{ source_id: 'hub', source_kb: 'test', target_id: 'spoke', target_kb: 'test', relation: null }];
const opacity = (cy: Core, id: string) => Number(cy.getElementById(`test/${id}`).style('opacity'));

async function initialized() {
	await waitFor(() => expect(graph.instances.length).toBeGreaterThan(0));
	return graph.instances.at(-1)!;
}
afterEach(() => { cleanup(); graph.instances = []; });

it('opens a tapped node in its own KB and omits missing KB names', async () => {
	render(GraphView, { nodes, edges, layoutName: 'grid' });
	const cy = await initialized();
	vi.mocked(goto).mockClear();
	cy.getElementById('test/hub').emit('tap');
	expect(goto).toHaveBeenLastCalledWith('/entries/hub?kb=test');
	cy.getElementById('test/hub').data('entryId', 'space / id');
	cy.getElementById('test/hub').removeData('kbName');
	cy.getElementById('test/hub').emit('tap');
	expect(goto).toHaveBeenLastCalledWith('/entries/space%20%2F%20id');
});

describe('GraphView search reactivity', () => {
	it('initializes once for an initially populated graph and destroys it on unmount', async () => {
		const view = render(GraphView, { nodes, edges, layoutName: 'grid', searchQuery: 'alpha' });
		const cy = await initialized();
		await waitFor(() => expect(opacity(cy, 'spoke')).toBe(0.15));
		expect(graph.instances).toHaveLength(1);
		view.unmount();
		expect(cy.destroyed()).toBe(true);
	});

	it('applies a query entered while asynchronous Cytoscape initialization is pending', async () => {
		const view = render(GraphView, { nodes: [], edges: [], layoutName: 'grid', searchQuery: '  ALPHA  ' });
		expect(graph.instances).toHaveLength(0);
		await view.rerender({ nodes, edges });
		const cy = await initialized();
		await waitFor(() => expect(opacity(cy, 'spoke')).toBe(0.15));
		expect(opacity(cy, 'hub')).toBe(1);
		expect(cy.getElementById('test/hub').style('border-width')).toBe('3px');
		expect(Number(cy.edges().style('opacity'))).toBe(0.1);
	});

	it('reacts to changed partial queries, multiple matches and no matches', async () => {
		const view = render(GraphView, { nodes: [], edges: [], layoutName: 'grid', searchQuery: 'alpha' });
		await view.rerender({ nodes, edges });
		const cy = await initialized();
		await view.rerender({ searchQuery: 'SPO' });
		await waitFor(() => expect(opacity(cy, 'hub')).toBe(0.15));
		expect(opacity(cy, 'spoke')).toBe(1);
		await view.rerender({ searchQuery: 'a' });
		await waitFor(() => expect(opacity(cy, 'hub')).toBe(1));
		expect(opacity(cy, 'spoke')).toBe(1);
		await view.rerender({ searchQuery: 'not found' });
		await waitFor(() => expect(opacity(cy, 'spoke')).toBe(0.15));
		expect(opacity(cy, 'hub')).toBe(0.15);
	});

	it('clears search overrides and restores center borders and centrality opacity', async () => {
		const view = render(GraphView, { nodes: [], edges: [], centerId: 'hub', sizeByCentrality: true, layoutName: 'grid', searchQuery: 'beta' });
		await view.rerender({ nodes, edges });
		const cy = await initialized();
		await waitFor(() => expect(opacity(cy, 'spoke')).toBe(1));
		await view.rerender({ searchQuery: '   ' });
		await waitFor(() => expect(opacity(cy, 'hub')).toBeCloseTo(0.7));
		expect(opacity(cy, 'spoke')).toBeCloseTo(0.52);
		expect(cy.getElementById('test/hub').style('border-width')).toBe('3px');
		expect(cy.getElementById('test/spoke').style('border-width')).toBe('0px');
		expect(Number(cy.edges().style('opacity'))).toBe(0.6);
	});

	it('reapplies the unchanged query when nodes and edges are replaced', async () => {
		const view = render(GraphView, { nodes: [], edges: [], layoutName: 'grid', searchQuery: 'alpha' });
		await view.rerender({ nodes, edges });
		const cy = await initialized();
		const replacement = [...nodes, { ...nodes[1], id: 'new', title: 'Gamma New' }];
		const replacementEdges = [...edges, { ...edges[0], target_id: 'new' }];
		await view.rerender({ nodes: replacement, edges: replacementEdges });
		await waitFor(() => expect(cy.nodes()).toHaveLength(3));
		await waitFor(() => expect(opacity(cy, 'new')).toBe(0.15));
		expect(opacity(cy, 'hub')).toBe(1);
		expect(Number(cy.getElementById('edge-1').style('opacity'))).toBe(0.1);
	});

	it('initializes a graph loaded after an empty render and applies the existing query', async () => {
		const view = render(GraphView, { nodes: [], edges: [], layoutName: 'grid', searchQuery: 'beta' });
		expect(graph.instances).toHaveLength(0);
		await view.rerender({ nodes, edges });
		const cy = await initialized();
		await waitFor(() => expect(opacity(cy, 'hub')).toBe(0.15));
		expect(opacity(cy, 'spoke')).toBe(1);
	});
});






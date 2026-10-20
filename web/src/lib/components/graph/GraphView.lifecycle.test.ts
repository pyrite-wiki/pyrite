import { afterEach, beforeAll, expect, it, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/svelte';
import type cytoscape from 'cytoscape';
import type { GraphNode } from '$lib/api/types';
import GraphView from './GraphView.svelte';

const graph = vi.hoisted(() => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    return { gate, release, pluginRequested: false, layouts: 0, instances: [] as cytoscape.Core[] };
});
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('cytoscape-cose-bilkent', async () => {
    graph.pluginRequested = true;
    await graph.gate;
    return { default: () => {} };
});
vi.mock('cytoscape', async () => {
    const actual = await vi.importActual<{ default: typeof cytoscape }>('cytoscape/dist/cytoscape.esm.mjs');
    function LayoutSpy(this: { options: { cy: cytoscape.Core } }, options: { cy: cytoscape.Core }) {
        this.options = options;
    }
    Object.assign(LayoutSpy.prototype, {
        run(this: { options: { cy: cytoscape.Core } }) {
            graph.layouts++;
            this.options.cy.emit('layoutready');
            this.options.cy.emit('layoutstop');
            return this;
        },
        stop() { return this; }
    });
    actual.default('layout', 'grid', LayoutSpy);
    return { default: Object.assign((options: cytoscape.CytoscapeOptions) => {
        const instance = actual.default({ ...options, container: undefined, headless: true });
        graph.instances.push(instance);
        return instance;
    }, { use: () => {} }) };
});

const nodes: GraphNode[] = [{ id: 'hub', kb_name: 'test', title: 'Hub', entry_type: 'note', link_count: 0 }];
beforeAll(async () => { await import('cytoscape'); });
afterEach(() => {
    graph.release(); cleanup(); graph.instances.forEach(cy => cy.destroy());
    graph.instances = []; graph.layouts = 0;
});
async function settle() {
    graph.release();
    await new Promise(resolve => setTimeout(resolve, 0));
}

it('keeps one initialization in flight and uses the latest nodes', async () => {
    const view = render(GraphView, { nodes, edges: [], layoutName: 'grid' });
    await waitFor(() => expect(graph.pluginRequested).toBe(true));
    await view.rerender({ nodes: [...nodes, { ...nodes[0], id: 'latest' }] });
    await settle();
    expect(graph.instances).toHaveLength(1);
    expect(graph.instances[0].nodes()).toHaveLength(2);
    expect(graph.layouts).toBe(1);
});
it('lays out exactly once when initialization becomes ready', async () => {
    render(GraphView, { nodes, edges: [], layoutName: 'grid' });
    await settle();
    expect(graph.instances).toHaveLength(1);
    expect(graph.layouts).toBe(1);
});
it('leaves no live instance when unmounted during initialization', async () => {
    const view = render(GraphView, { nodes, edges: [], layoutName: 'grid' });
    view.unmount();
    await settle();
    expect(graph.instances.every(cy => cy.destroyed())).toBe(true);
});

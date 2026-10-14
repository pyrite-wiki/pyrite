import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, waitFor } from '@testing-library/svelte';
import type { Writable } from 'svelte/store';

vi.mock('$app/stores', async () => {
    const { writable } = await import('svelte/store');
    return { page: writable({ url: new URL('http://localhost/graph') }) };
});
vi.mock('$lib/stores/kbs.svelte', () => ({
    kbStore: { kbs: [{ name: 'otherkb' }, { name: 'linkkb' }], activeKB: 'linkkb' }
}));
import { page } from '$app/stores';
import GraphPage from '../../routes/graph/+page.svelte';
import OrientPage from '../../routes/orient/+page.svelte';

const requests: string[] = [];
const route = page as unknown as Writable<{ url: URL }>;
beforeEach(() => {
    requests.length = 0;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
        requests.push(url);
        const body = url.includes('/orient') ? {
            kb: 'otherkb', kb_type: 'research', total_entries: 1, types: [], top_tags: [],
            recent: [{ id: 'space / id', title: 'Recent note', entry_type: 'note', updated_at: '2026-10-01' }]
        } : url.includes('/graph') ? { nodes: [], edges: [] } : { types: [] };
        return new Response(JSON.stringify(body), { status: 200 });
    }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('scopes the graph request and selection from the URL, then allows dropdown changes', async () => {
    route.set({ url: new URL('http://localhost/graph?kb=otherkb') });
    const view = render(GraphPage);
    await waitFor(() => expect(requests.some(u => u.startsWith('/api/graph?'))).toBe(true));
    const initial = requests.find(u => u.startsWith('/api/graph?'))!;
    expect(new URL(initial, 'http://localhost').searchParams.get('kb')).toBe('otherkb');
    const select = view.getByLabelText('KB');
    expect((select as HTMLSelectElement).value).toBe('otherkb');
    await fireEvent.change(select, { target: { value: 'linkkb' } });
    await waitFor(() => expect(requests.some(u => u.includes('kb=linkkb'))).toBe(true));
    await fireEvent.change(select, { target: { value: '' } });
    await waitFor(() => expect(requests.filter(u => u.startsWith('/api/graph?')).length).toBe(3));
    const last = requests.filter(u => u.startsWith('/api/graph?')).at(-1)!;
    expect(new URL(last, 'http://localhost').searchParams.has('kb')).toBe(false);
});

it('keeps an unscoped graph unscoped even with an active KB', async () => {
    route.set({ url: new URL('http://localhost/graph') });
    render(GraphPage);
    await waitFor(() => expect(requests.some(u => u.startsWith('/api/graph?'))).toBe(true));
    expect(new URL(requests.find(u => u.startsWith('/api/graph?'))!, 'http://localhost').searchParams.has('kb')).toBe(false);
});

it('carries the orient KB and encodes IDs in recent-change links', async () => {
    route.set({ url: new URL('http://localhost/orient?kb=otherkb') });
    const view = render(OrientPage);
    const link = await view.findByRole('link', { name: /Recent note/ });
    expect(link.getAttribute('href')).toBe('/entries/space%20%2F%20id?kb=otherkb');
});

import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';
import { render, cleanup, waitFor, screen } from '@testing-library/svelte';
import type { Writable } from 'svelte/store';

// The entry page under a SvelteKit router that reuses it when only the query
// changes (the layout keys on the pathname, +layout.svelte): the chooser's
// links and a -> b moves must reload (#912).
vi.mock('$app/stores', async () => {
	const { writable } = await import('svelte/store');
	return {
		page: writable({
			url: new URL('http://localhost/entries/dup'),
			params: { id: 'dup' }
		})
	};
});
vi.mock('$lib/api/websocket', () => ({
	wsClient: { follow: vi.fn(), onEvent: vi.fn(() => vi.fn()), disconnect: vi.fn() }
}));
vi.mock('$lib/api/client', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/api/client')>();
	return {
		ApiError: actual.ApiError,
		api: {
			onUnauthorized: vi.fn(),
			getEntry: vi.fn(),
			resolveBatch: vi.fn().mockResolvedValue({ resolved: {} }),
			resolveEntry: vi.fn(),
			getEntryBlocks: vi.fn()
		}
	};
});
for (const heavy of [
	'$lib/components/layout/Topbar.svelte',
	'$lib/components/StarButton.svelte',
	'$lib/components/entry/EntryMeta.svelte',
	'$lib/components/entry/BacklinksPanel.svelte',
	'$lib/components/entry/VersionHistoryPanel.svelte',
	'$lib/components/graph/LocalGraphPanel.svelte',
	'$lib/editor/Editor.svelte',
	'$lib/editor/TiptapEditor.svelte',
	'$lib/components/entry/OutlinePanel.svelte',
	'$lib/components/entry/AIPanel.svelte'
]) {
	vi.doMock(heavy, async () => ({ default: (await import('./test-fixtures/Empty.svelte')).default }));
}

import { api, ApiError } from '$lib/api/client';
import { page } from '$app/stores';
import { entryStore } from '$lib/stores/entries.svelte';

const entry = (kb: string) => ({
	id: 'dup',
	kb_name: kb,
	entry_type: 'note',
	title: `Dup in ${kb}`,
	body: 'body',
	tags: [],
	participants: [],
	sources: [],
	outlinks: [],
	backlinks: [],
	file_path: ''
});

function go(path: string) {
	const url = new URL(`http://localhost${path}`);
	(page as unknown as Writable<unknown>).set({ url, params: { id: 'dup' } });
}

beforeEach(() => {
	vi.clearAllMocks();
	entryStore.current = null;
	entryStore.error = null;
	entryStore.ambiguousKbs = null;
	vi.mocked(api.getEntry).mockImplementation(async (_id, opts) => {
		if (!opts?.kb) {
			throw new ApiError(409, 'ambiguous', 'AMBIGUOUS', ['kb-a', 'kb-b']);
		}
		return entry(opts.kb) as never;
	});
});
afterEach(cleanup);

describe('entry page', () => {
	it('chooser links load the chosen KB without a remount, and kb-a -> kb-b reloads', async () => {
		go('/entries/dup');
		const { default: Page } = await import('./entries/[id]/+page.svelte');
		render(Page);

		const linkB = await screen.findByRole('link', { name: 'kb-b' });
		expect(linkB).toHaveAttribute('href', '/entries/dup?kb=kb-b');
		const linkA = screen.getByRole('link', { name: 'kb-a' });
		expect(api.getEntry).toHaveBeenLastCalledWith('dup', { kb: undefined, with_links: true });

		// What the router does on a click: same path, new query, page reused.
		go(new URL(linkA.getAttribute('href')!, 'http://localhost').pathname + '?kb=kb-a');
		await waitFor(() => expect(entryStore.current?.kb_name).toBe('kb-a'));
		expect(api.getEntry).toHaveBeenLastCalledWith('dup', { kb: 'kb-a', with_links: true });

		go('/entries/dup?kb=kb-b');
		await waitFor(() => expect(entryStore.current?.kb_name).toBe('kb-b'));
		expect(api.getEntry).toHaveBeenLastCalledWith('dup', { kb: 'kb-b', with_links: true });
	});

	it('a transclusion ![[kbb:foo]] is resolved in the KB it names', async () => {
		vi.mocked(api.getEntry).mockResolvedValue({
			...entry('kb-a'),
			body: 'before\n\n![[kbb:foo]]\n\nafter'
		} as never);
		vi.mocked(api.resolveEntry).mockResolvedValue({ resolved: false, entry: null });
		go('/entries/dup?kb=kb-a');
		const { default: Page } = await import('./entries/[id]/+page.svelte');
		render(Page);
		await waitFor(() => expect(api.resolveEntry).toHaveBeenCalledWith('foo', 'kbb'));
	});
});

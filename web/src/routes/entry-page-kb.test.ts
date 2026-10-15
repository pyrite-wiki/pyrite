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
import { kbStore } from '$lib/stores/kbs.svelte';

const kb = (name: string, shortname: string) => ({
	name,
	shortname,
	type: 'generic',
	path: `/tmp/${name}`,
	entries: 0,
	indexed: true,
	source: 'local',
	description: '',
	read_only: false,
	last_indexed: null,
	default_role: null
});

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
	kbStore.kbs = [];
	kbStore.initialized = false;
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

	it('a transclusion ![[kbb:foo]] is resolved in the KB the shortname names', async () => {
		// The read view's hydration is the third place the web turns a link
		// prefix into a KB (#952, #965 round 2): the API takes the full name
		// only, so `kbb` must reach it as `kb-b`, from the KB list.
		kbStore.kbs = [kb('kb-a', 'ka'), kb('kb-b', 'kbb')];
		kbStore.initialized = true;
		vi.mocked(api.getEntry).mockResolvedValue({
			...entry('kb-a'),
			body: 'before\n\n![[kbb:foo]]\n\nafter'
		} as never);
		vi.mocked(api.resolveEntry).mockResolvedValue({ resolved: false, entry: null });
		go('/entries/dup?kb=kb-a');
		const { default: Page } = await import('./entries/[id]/+page.svelte');
		render(Page);
		await waitFor(() => expect(api.resolveEntry).toHaveBeenCalled());
		expect(api.resolveEntry).toHaveBeenCalledWith('foo', 'kb-b');
		expect(api.resolveEntry).not.toHaveBeenCalledWith('foo', 'kbb');
	});

	it('a transclusion on a cold load waits for the KB list before resolving', async () => {
		// Before kbStore.load() settles the prefix cannot be resolved, so the
		// hydration must wait rather than send the shortname and show
		// "Entry not found" (#965 round 3).
		vi.mocked(api.getEntry).mockResolvedValue({
			...entry('kb-a'),
			body: 'before\n\n![[kbb:foo]]\n\nafter'
		} as never);
		vi.mocked(api.resolveEntry).mockResolvedValue({ resolved: false, entry: null });
		go('/entries/dup?kb=kb-a');
		const { default: Page } = await import('./entries/[id]/+page.svelte');
		render(Page);
		await waitFor(() => expect(entryStore.current?.kb_name).toBe('kb-a'));
		await new Promise((r) => setTimeout(r, 250)); // past the hydration delay
		expect(api.resolveEntry).not.toHaveBeenCalled();

		kbStore.kbs = [kb('kb-a', 'ka'), kb('kb-b', 'kbb')];
		kbStore.initialized = true;
		await waitFor(() => expect(api.resolveEntry).toHaveBeenCalledWith('foo', 'kb-b'));
		expect(api.resolveEntry).not.toHaveBeenCalledWith('foo', 'kbb');
	});
});

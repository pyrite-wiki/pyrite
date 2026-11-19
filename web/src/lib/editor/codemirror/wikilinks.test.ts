import { afterEach, describe, expect, it, vi } from 'vitest';
import { EditorState } from '@codemirror/state';
import { EditorView } from '@codemirror/view';

vi.mock('$lib/api/websocket', () => ({
	wsClient: { follow: vi.fn(), onEvent: vi.fn(() => vi.fn()), disconnect: vi.fn() }
}));
vi.mock('$lib/api/client', () => ({
	api: {
		resolveEntry: vi.fn().mockResolvedValue({ resolved: false, entry: null }),
		getEntryTitles: vi.fn().mockResolvedValue({ entries: [] })
	}
}));

import { api } from '$lib/api/client';
import { kbStore } from '$lib/stores/kbs.svelte';
import { wikilinkExtension } from './wikilinks';

let view: EditorView | undefined;
afterEach(() => {
	view?.destroy();
	view = undefined;
	kbStore.kbs = [];
});

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

function mount(doc: string) {
	const parent = document.createElement('div');
	document.body.appendChild(parent);
	view = new EditorView({
		state: EditorState.create({ doc, selection: { anchor: doc.length }, extensions: [wikilinkExtension()] }),
		parent
	});
	return parent;
}

// A KB's shortname in the text is link syntax only (#952): the API takes the
// full name, so the editor resolves the prefix against the KB list first
// (kbStore.resolveName); a prefix the list does not know is passed on as
// written, for the API to refuse by its own rule.
describe('CodeMirror wikilinks name their KB', () => {
	it('an embed ![[kbb:foo]] resolves foo in the KB the shortname names', async () => {
		kbStore.kbs = [kb('kb-a', 'ka'), kb('kb-b', 'kbb')];
		const parent = mount('![[kbb:foo]]\n\nafter');
		await vi.waitFor(() => expect(api.resolveEntry).toHaveBeenCalled());
		expect(api.resolveEntry).toHaveBeenCalledWith('foo', 'kb-b');
		expect(parent.querySelector('a[href="/entries/foo?kb=kb-b"]')).not.toBeNull();
	});

	it('an unknown prefix is passed on as written', async () => {
		const parent = mount('![[kbb:foo]]\n\nafter');
		await vi.waitFor(() => expect(api.resolveEntry).toHaveBeenCalled());
		expect(api.resolveEntry).toHaveBeenCalledWith('foo', 'kbb');
		expect(parent.querySelector('a[href="/entries/foo?kb=kbb"]')).not.toBeNull();
	});

	it('a pill [[kbb:foo]] links with the KB', () => {
		const parent = mount('[[kbb:foo]]\n\nafter');
		const pill = parent.querySelector('.wikilink-pill') as HTMLElement;
		expect(pill).not.toBeNull();
		const assign = vi.fn();
		Object.defineProperty(window, 'location', {
			value: { set href(v: string) { assign(v); } },
			configurable: true
		});
		pill.click();
		expect(assign).toHaveBeenCalledWith('/entries/foo?kb=kbb');
	});
});

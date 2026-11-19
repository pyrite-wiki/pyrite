import { describe, expect, it } from 'vitest';

// Every place the web turns link text into a KB goes through the KB list
// (#952, #965 round 2). A `[[short:id]]` prefix is link syntax only: the API
// takes the full name and refuses a shortname, so each consumer of a prefix
// must resolve it with `kbStore.resolveName` first. Hrefs all come from
// `entryHref`, which resolves inside (wikilink-utils.test.ts); API calls are
// pinned here: a new `api.resolveEntry(...)` site that hands the prefix on as
// written fails this test, whichever file it is in.

// Every source file, as text, minus the tests and the API client (the
// method's own definition).
const sourceText = import.meta.glob(
	['/src/**/*.ts', '/src/**/*.svelte', '!/src/**/*.test.ts', '!/src/lib/api/client.ts'],
	{ query: '?raw', import: 'default', eager: true }
) as Record<string, string>;

/** Each `api.resolveEntry(` call in `text`, as the text of its arguments. */
function resolveEntryCalls(text: string): string[] {
	const calls: string[] = [];
	const marker = 'api.resolveEntry(';
	let at = text.indexOf(marker);
	while (at !== -1) {
		let depth = 1;
		let i = at + marker.length;
		while (i < text.length && depth > 0) {
			if (text[i] === '(') depth++;
			else if (text[i] === ')') depth--;
			i++;
		}
		calls.push(text.slice(at + marker.length, i - 1));
		at = text.indexOf(marker, i);
	}
	return calls;
}

describe('a link prefix becomes a KB through the KB list', () => {
	const sites: Record<string, string[]> = {};
	for (const [file, text] of Object.entries(sourceText)) {
		const calls = resolveEntryCalls(text);
		if (calls.length) sites[file.replace(/^\/src\//, '')] = calls;
	}

	it('covers the three consumers of a link prefix', () => {
		// The editor's live preview, the editor's transclusion node and the
		// read view's hydration. A fourth consumer is checked like the others;
		// this list only says the walk found what it was written for.
		expect(Object.keys(sites).sort()).toEqual([
			'lib/editor/codemirror/wikilinks.ts',
			'lib/editor/tiptap/transclusion-extension.ts',
			'routes/entries/[id]/+page.svelte'
		]);
	});

	for (const [file, calls] of Object.entries(sites)) {
		it(`${file} resolves the KB it names before calling the API`, () => {
			for (const args of calls) {
				expect(args, `api.resolveEntry(${args})`).toContain('kbStore.resolveName(');
			}
		});
	}
});

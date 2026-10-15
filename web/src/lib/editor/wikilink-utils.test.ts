import { describe, it, expect, beforeEach } from 'vitest';
import { parseWikilinks, parseTransclusions, renderWikilinks } from './wikilink-utils';
import {
	activeTransclusions,
	isCircularTransclusion,
	markTransclusionActive,
	CIRCULAR_REF_MESSAGE
} from './transclusion-utils';

describe('parseWikilinks', () => {
	it('parses simple wikilink', () => {
		const matches = parseWikilinks('See [[john-doe]] for details');
		expect(matches).toHaveLength(1);
		expect(matches[0].target).toBe('john-doe');
		expect(matches[0].display).toBeUndefined();
		expect(matches[0].kb).toBeUndefined();
		expect(matches[0].full).toBe('[[john-doe]]');
	});

	it('parses wikilink with display text', () => {
		const matches = parseWikilinks('See [[john-doe|John Doe]] for details');
		expect(matches).toHaveLength(1);
		expect(matches[0].target).toBe('john-doe');
		expect(matches[0].display).toBe('John Doe');
	});

	it('parses multiple wikilinks', () => {
		const matches = parseWikilinks('[[alpha]] and [[beta|B]] together');
		expect(matches).toHaveLength(2);
		expect(matches[0].target).toBe('alpha');
		expect(matches[1].target).toBe('beta');
		expect(matches[1].display).toBe('B');
	});

	it('returns empty array for no wikilinks', () => {
		expect(parseWikilinks('plain text')).toHaveLength(0);
	});

	it('handles whitespace in targets', () => {
		const matches = parseWikilinks('[[  some target  ]]');
		expect(matches[0].target).toBe('some target');
	});

	it('tracks start and end positions', () => {
		const text = 'before [[link]] after';
		const matches = parseWikilinks(text);
		expect(matches[0].start).toBe(7);
		expect(matches[0].end).toBe(15);
	});

	it('parses cross-KB wikilink', () => {
		const matches = parseWikilinks('See [[dev:some-entry]] for details');
		expect(matches).toHaveLength(1);
		expect(matches[0].kb).toBe('dev');
		expect(matches[0].target).toBe('some-entry');
	});

	it('parses cross-KB wikilink with display text', () => {
		const matches = parseWikilinks('See [[ops:deploy-guide|Deploy Guide]] here');
		expect(matches).toHaveLength(1);
		expect(matches[0].kb).toBe('ops');
		expect(matches[0].target).toBe('deploy-guide');
		expect(matches[0].display).toBe('Deploy Guide');
	});

	it('parses mixed local and cross-KB wikilinks', () => {
		const matches = parseWikilinks('[[kb1:entry-a]] and [[entry-b]] and [[kb2:entry-c|Display]]');
		expect(matches).toHaveLength(3);
		expect(matches[0].kb).toBe('kb1');
		expect(matches[0].target).toBe('entry-a');
		expect(matches[1].kb).toBeUndefined();
		expect(matches[1].target).toBe('entry-b');
		expect(matches[2].kb).toBe('kb2');
		expect(matches[2].target).toBe('entry-c');
		expect(matches[2].display).toBe('Display');
	});
});

describe('parseTransclusions', () => {
	it('parses transclusion with block ID', () => {
		const matches = parseTransclusions('![[entry-id^block-1]]');
		expect(matches).toHaveLength(1);
		expect(matches[0].target).toBe('entry-id');
		expect(matches[0].blockId).toBe('block-1');
		expect(matches[0].heading).toBeUndefined();
	});

	it('parses transclusion with heading', () => {
		const matches = parseTransclusions('![[entry-id#Some Heading]]');
		expect(matches).toHaveLength(1);
		expect(matches[0].target).toBe('entry-id');
		expect(matches[0].heading).toBe('Some Heading');
		expect(matches[0].blockId).toBeUndefined();
	});

	it('parses transclusion with KB prefix and block ID', () => {
		const matches = parseTransclusions('![[kb:entry-id^block-1]]');
		expect(matches).toHaveLength(1);
		expect(matches[0].kb).toBe('kb');
		expect(matches[0].target).toBe('entry-id');
		expect(matches[0].blockId).toBe('block-1');
	});

	it('parses simple transclusion without fragment', () => {
		const matches = parseTransclusions('![[entry-id]]');
		expect(matches).toHaveLength(1);
		expect(matches[0].target).toBe('entry-id');
		expect(matches[0].heading).toBeUndefined();
		expect(matches[0].blockId).toBeUndefined();
	});

	it('does not match regular wikilinks', () => {
		const matches = parseTransclusions('[[entry-id^block-1]]');
		expect(matches).toHaveLength(0);
	});

	it('parses transclusion with KB prefix and heading', () => {
		const matches = parseTransclusions('![[my-kb:entry-id#Introduction]]');
		expect(matches).toHaveLength(1);
		expect(matches[0].kb).toBe('my-kb');
		expect(matches[0].target).toBe('entry-id');
		expect(matches[0].heading).toBe('Introduction');
	});

	it('tracks start and end positions', () => {
		const text = 'before ![[entry^blk]] after';
		const matches = parseTransclusions(text);
		expect(matches[0].start).toBe(7);
		expect(matches[0].end).toBe(21);
	});

	it('parses transclusion with view option', () => {
		const matches = parseTransclusions('![[col-id]]{ view: "table" }');
		expect(matches).toHaveLength(1);
		expect(matches[0].target).toBe('col-id');
		expect(matches[0].options).toEqual({ view: 'table' });
	});

	it('parses transclusion with multiple options', () => {
		const matches = parseTransclusions('![[col-id]]{ view: "table", limit: 5 }');
		expect(matches).toHaveLength(1);
		expect(matches[0].target).toBe('col-id');
		expect(matches[0].options).toEqual({ view: 'table', limit: 5 });
	});

	it('parses transclusion without options (backwards compat)', () => {
		const matches = parseTransclusions('![[entry-id]]');
		expect(matches).toHaveLength(1);
		expect(matches[0].target).toBe('entry-id');
		expect(matches[0].options).toBeUndefined();
	});

	it('parses numeric option values as numbers', () => {
		const matches = parseTransclusions('![[col-id]]{ limit: 20 }');
		expect(matches).toHaveLength(1);
		expect(matches[0].options).toEqual({ limit: 20 });
	});

	it('parses boolean option values as booleans', () => {
		const matches = parseTransclusions('![[col-id]]{ compact: true }');
		expect(matches).toHaveLength(1);
		expect(matches[0].options).toEqual({ compact: true });
	});

	it('ignores malformed options block', () => {
		const matches = parseTransclusions('![[col-id]]{ invalid json }');
		expect(matches).toHaveLength(1);
		expect(matches[0].target).toBe('col-id');
		expect(matches[0].options).toBeUndefined();
	});

	it('tracks end position including options block', () => {
		const text = '![[col-id]]{ view: "table" }';
		const matches = parseTransclusions(text);
		expect(matches[0].start).toBe(0);
		expect(matches[0].end).toBe(text.length);
	});
});

describe('block content extraction', () => {
	it('finds matching block by block_id', () => {
		const blocks = [
			{ block_id: 'block-1', content: 'First block content', heading: null, position: 0, block_type: 'paragraph' },
			{ block_id: 'block-2', content: 'Second block content', heading: null, position: 1, block_type: 'paragraph' },
			{ block_id: 'block-3', content: 'Third block content', heading: null, position: 2, block_type: 'paragraph' }
		];
		const targetBlockId = 'block-2';
		const matchingBlock = blocks.find((b) => b.block_id === targetBlockId);
		expect(matchingBlock).toBeDefined();
		expect(matchingBlock!.content).toBe('Second block content');
	});

	it('returns undefined when block_id does not match', () => {
		const blocks = [
			{ block_id: 'block-1', content: 'First block content', heading: null, position: 0, block_type: 'paragraph' }
		];
		const targetBlockId = 'nonexistent';
		const matchingBlock = blocks.find((b) => b.block_id === targetBlockId);
		expect(matchingBlock).toBeUndefined();
	});

	it('handles empty blocks array', () => {
		const blocks: { block_id: string; content: string }[] = [];
		const matchingBlock = blocks.find((b) => b.block_id === 'anything');
		expect(matchingBlock).toBeUndefined();
	});
});

describe('renderWikilinks', () => {
	it('renders simple wikilink as anchor', () => {
		const html = renderWikilinks('<p>See [[john-doe]] here</p>');
		expect(html).toContain('href="/entries/john-doe"');
		expect(html).toContain('class="wikilink"');
		expect(html).toContain('data-wikilink="john-doe"');
		expect(html).toContain('>john-doe</a>');
	});

	it('renders wikilink with display text', () => {
		const html = renderWikilinks('<p>See [[john-doe|John Doe]] here</p>');
		expect(html).toContain('href="/entries/john-doe"');
		expect(html).toContain('>John Doe</a>');
	});

	it('encodes special characters in href', () => {
		const html = renderWikilinks('<p>[[entry with spaces]]</p>');
		expect(html).toContain('href="/entries/entry%20with%20spaces"');
	});

	it('leaves text without wikilinks unchanged', () => {
		const html = '<p>No links here</p>';
		expect(renderWikilinks(html)).toBe(html);
	});

	it('handles multiple wikilinks', () => {
		const html = renderWikilinks('<p>[[a]] and [[b|Beta]]</p>');
		expect(html).toContain('>a</a>');
		expect(html).toContain('>Beta</a>');
	});

	it('renders cross-KB wikilink with kb badge', () => {
		const html = renderWikilinks('<p>See [[dev:my-entry]] here</p>');
		expect(html).toContain('href="/entries/my-entry?kb=dev"');
		expect(html).toContain('data-wikilink="my-entry"');
		expect(html).toContain('data-wikilink-kb="dev"');
		expect(html).toContain('wikilink-kb-badge');
		expect(html).toContain('>dev:my-entry</a>');
	});

	it('renders cross-KB wikilink with display text', () => {
		const html = renderWikilinks('<p>[[ops:deploy|Deploy Guide]]</p>');
		expect(html).toContain('href="/entries/deploy?kb=ops"');
		expect(html).toContain('>Deploy Guide</a>');
	});

	it('marks missing wikilinks when existingIds provided', () => {
		const ids = new Set(['exists']);
		const html = renderWikilinks('<p>[[exists]] and [[missing]]</p>', ids);
		expect(html).toContain('class="wikilink"');
		expect(html).toContain('class="wikilink wikilink-missing"');
	});
});

describe('markdownToHtml wikilink preservation', () => {
	it('preserves heading fragment in wikilink', async () => {
		const { markdownToHtml } = await import('./tiptap/markdown');
		const html = markdownToHtml('See [[entry-id#Introduction]] here');
		expect(html).toContain('data-wikilink="entry-id"');
		expect(html).toContain('data-wikilink-heading="Introduction"');
	});

	it('preserves block-id fragment in wikilink', async () => {
		const { markdownToHtml } = await import('./tiptap/markdown');
		const html = markdownToHtml('See [[entry-id^block-1]] here');
		expect(html).toContain('data-wikilink="entry-id"');
		expect(html).toContain('data-wikilink-block="block-1"');
	});

	it('preserves kb prefix in wikilink', async () => {
		const { markdownToHtml } = await import('./tiptap/markdown');
		const html = markdownToHtml('See [[dev:entry-id]] here');
		expect(html).toContain('data-wikilink="entry-id"');
		expect(html).toContain('data-wikilink-kb="dev"');
	});
});

describe('depth tracking', () => {
	it('exceeds at MAX_TRANSCLUSION_DEPTH', async () => {
		const { isDepthExceeded, incrementDepth, resetDepth, MAX_TRANSCLUSION_DEPTH } = await import('./transclusion-utils');
		resetDepth();
		const cleanups: (() => void)[] = [];
		for (let i = 0; i < MAX_TRANSCLUSION_DEPTH; i++) {
			cleanups.push(incrementDepth());
		}
		expect(isDepthExceeded()).toBe(true);
		cleanups.forEach(c => c());
		resetDepth();
	});

	it('resets after cleanup', async () => {
		const { isDepthExceeded, incrementDepth, resetDepth } = await import('./transclusion-utils');
		resetDepth();
		const c1 = incrementDepth();
		const c2 = incrementDepth();
		c2();
		c1();
		expect(isDepthExceeded()).toBe(false);
		resetDepth();
	});
});

describe('transclusion cycle detection', () => {
	beforeEach(() => {
		// Clear active transclusions between tests
		activeTransclusions.clear();
	});

	it('detects no cycle when set is empty', () => {
		expect(isCircularTransclusion('entry-a')).toBe(false);
	});

	it('detects cycle when target is already active', () => {
		activeTransclusions.add('entry-a');
		expect(isCircularTransclusion('entry-a')).toBe(true);
	});

	it('does not falsely detect cycle for different targets', () => {
		activeTransclusions.add('entry-a');
		expect(isCircularTransclusion('entry-b')).toBe(false);
	});

	it('markTransclusionActive adds and cleanup removes', () => {
		const cleanup = markTransclusionActive('entry-x');
		expect(activeTransclusions.has('entry-x')).toBe(true);
		expect(isCircularTransclusion('entry-x')).toBe(true);

		cleanup();
		expect(activeTransclusions.has('entry-x')).toBe(false);
		expect(isCircularTransclusion('entry-x')).toBe(false);
	});

	it('simulates A -> B -> A cycle detection', () => {
		// A starts loading
		const cleanupA = markTransclusionActive('entry-a');
		expect(isCircularTransclusion('entry-a')).toBe(true);

		// B starts loading (nested inside A)
		const cleanupB = markTransclusionActive('entry-b');
		expect(isCircularTransclusion('entry-b')).toBe(true);

		// B tries to load A again -> circular
		expect(isCircularTransclusion('entry-a')).toBe(true);

		// Cleanup in reverse order
		cleanupB();
		cleanupA();
		expect(activeTransclusions.size).toBe(0);
	});

	it('allows same target after cleanup completes', () => {
		const cleanup = markTransclusionActive('entry-a');
		cleanup();
		// After cleanup, loading the same target again should be fine
		expect(isCircularTransclusion('entry-a')).toBe(false);
	});

	it('exports CIRCULAR_REF_MESSAGE constant', () => {
		expect(CIRCULAR_REF_MESSAGE).toContain('Circular reference');
	});
});

it('wikilinks and transclusions rendered from [[kb:id]] carry the KB', () => {
	const html = renderWikilinks('[[kb-b:dup]] and ![[kb-b:dup]] and [[dup]]');
	expect(html).toContain('href="/entries/dup?kb=kb-b"');
	expect(html).toContain('data-transclusion-kb="kb-b"');
	expect(html).toContain('href="/entries/dup"'); // no KB named: nothing guessed
});

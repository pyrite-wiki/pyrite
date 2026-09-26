import { describe, expect, it } from 'vitest';
import { buildNodeTooltipLines, buildEdgeTooltipLines } from './tooltip';

describe('buildNodeTooltipLines', () => {
	it('returns the title verbatim as text, not markup', () => {
		const lines = buildNodeTooltipLines({
			fullTitle: '<img src=x onerror=alert(1)>',
			entryType: 'note',
			kbName: 'kb',
			linkCount: 2
		});
		expect(lines[0].text).toBe('<img src=x onerror=alert(1)>');
	});

	it('includes entry type and kb name on one line', () => {
		const lines = buildNodeTooltipLines({
			fullTitle: 'Title',
			entryType: 'note',
			kbName: 'my-kb',
			linkCount: 1
		});
		expect(lines[1].text).toContain('note');
		expect(lines[1].text).toContain('my-kb');
	});

	it('pluralizes link count', () => {
		const lines = buildNodeTooltipLines({
			fullTitle: 'T',
			entryType: 'note',
			kbName: 'kb',
			linkCount: 0
		});
		expect(lines[2].text).toBe('0 links');
	});

	it('adds a centrality line only when centrality > 0', () => {
		const withCentrality = buildNodeTooltipLines({
			fullTitle: 'T',
			entryType: 'note',
			kbName: 'kb',
			linkCount: 1,
			centrality: 0.5
		});
		expect(withCentrality).toHaveLength(4);
		expect(withCentrality[3].text).toContain('centrality');

		const withoutCentrality = buildNodeTooltipLines({
			fullTitle: 'T',
			entryType: 'note',
			kbName: 'kb',
			linkCount: 1,
			centrality: 0
		});
		expect(withoutCentrality).toHaveLength(3);
	});
});

describe('buildEdgeTooltipLines', () => {
	it('returns the relation verbatim as text, not markup', () => {
		const lines = buildEdgeTooltipLines('<img src=x onerror=alert(1)>');
		expect(lines[0].text).toBe('<img src=x onerror=alert(1)>');
	});
});

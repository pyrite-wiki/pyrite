import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import BacklinksPanel from './BacklinksPanel.svelte';

afterEach(() => {
	cleanup();
});

// The index holds one backlink row per link the source writes (#1009): a
// frontmatter `links:` item and a body `[[x]]` are two rows from one source.
const twoLinksFromOneSource = [
	{
		id: 's',
		kb_name: 'ka',
		title: 'Source',
		entry_type: 'note',
		relation: 'related_to',
		forward_relation: 'related_to',
		note: ''
	},
	{
		id: 's',
		kb_name: 'ka',
		title: 'Source',
		entry_type: 'note',
		relation: 'related_to',
		forward_relation: 'wikilink',
		note: '#Part'
	},
	{
		id: 'other',
		kb_name: 'ka',
		title: 'Other',
		entry_type: 'note',
		relation: 'related_to',
		forward_relation: 'wikilink',
		note: ''
	}
];

describe('BacklinksPanel', () => {
	it('lists a source with two links to this entry once, with both relations', () => {
		render(BacklinksPanel, { props: { backlinks: twoLinksFromOneSource } });
		expect(screen.getByText('Backlinks (2)')).toBeInTheDocument();
		expect(screen.getAllByRole('listitem')).toHaveLength(2);
		expect(screen.getAllByText('Source')).toHaveLength(1);
		expect(screen.getByText('related_to')).toBeInTheDocument();
		expect(screen.getAllByText('wikilink')).toHaveLength(2);
		expect(screen.getByText('#Part')).toBeInTheDocument();
	});

	it('keeps sources of the same id in two KBs apart', () => {
		render(BacklinksPanel, {
			props: {
				backlinks: [
					{
						id: 's',
						kb_name: 'ka',
						title: 'In ka',
						entry_type: 'note',
						forward_relation: 'wikilink'
					},
					{
						id: 's',
						kb_name: 'kb',
						title: 'In kb',
						entry_type: 'note',
						forward_relation: 'wikilink'
					}
				]
			}
		});
		expect(screen.getAllByRole('listitem')).toHaveLength(2);
		expect(screen.getByText('In ka')).toBeInTheDocument();
		expect(screen.getByText('In kb')).toBeInTheDocument();
	});

	it('shows the empty state', () => {
		render(BacklinksPanel, { props: { backlinks: [] } });
		expect(screen.getByText('No entries link here yet')).toBeInTheDocument();
	});
});

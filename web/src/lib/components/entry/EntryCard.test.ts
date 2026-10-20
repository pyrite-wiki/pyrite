import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

import EntryCard from './EntryCard.svelte';

afterEach(() => {
	cleanup();
});

const sampleEntry = {
	id: 'test-1',
	kb_name: 'my-kb',
	entry_type: 'note',
	title: 'Test Note',
	body: '# Hello world',
	summary: 'A test summary',
	tags: ['tag1', 'tag2', 'tag3', 'tag4', 'tag5'],
	participants: [],
	sources: [],
	outlinks: [],
	backlinks: [],
	file_path: '/tmp/test.md'
};

describe('EntryCard', () => {
	it('renders entry title', () => {
		render(EntryCard, { props: { entry: sampleEntry } });
		expect(screen.getByText('Test Note')).toBeInTheDocument();
	});

	it('renders entry_type badge text', () => {
		render(EntryCard, { props: { entry: sampleEntry } });
		expect(screen.getByText('note')).toBeInTheDocument();
	});

	it('shows summary when available', () => {
		render(EntryCard, { props: { entry: sampleEntry } });
		expect(screen.getByText('A test summary')).toBeInTheDocument();
	});

	it('shows body excerpt when no summary', () => {
		const entryNoSummary = { ...sampleEntry, summary: undefined };
		render(EntryCard, { props: { entry: entryNoSummary } });
		expect(screen.getByText('# Hello world')).toBeInTheDocument();
	});

	it('renders first 3 tags', () => {
		render(EntryCard, { props: { entry: sampleEntry } });
		expect(screen.getByText('tag1')).toBeInTheDocument();
		expect(screen.getByText('tag2')).toBeInTheDocument();
		expect(screen.getByText('tag3')).toBeInTheDocument();
	});

	it('shows overflow count for tags beyond 3', () => {
		render(EntryCard, { props: { entry: sampleEntry } });
		expect(screen.getByText('+2')).toBeInTheDocument();
	});

	it('shows kb_name', () => {
		render(EntryCard, { props: { entry: sampleEntry } });
		expect(screen.getByText('my-kb')).toBeInTheDocument();
	});

	it('card link href names the entry and its KB (#910)', () => {
		render(EntryCard, { props: { entry: sampleEntry } });
		const link = document.querySelector('a[href="/entries/test-1?kb=my-kb"]');
		expect(link).not.toBeNull();
	});
});

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

import GalleryView from './GalleryView.svelte';

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
});

const sampleEntry = {
	id: 'e1',
	kb_name: 'kb1',
	entry_type: 'note',
	title: 'Test Entry',
	body: '# Hello **world** _markdown_ [link](url) `code`',
	summary: undefined,
	tags: ['a', 'b', 'c', 'd'],
	participants: [],
	sources: [],
	outlinks: [],
	backlinks: [],
	file_path: '/tmp/test.md',
	updated_at: '2025-06-15T10:00:00Z'
};

describe('GalleryView', () => {
	it('renders entry title', () => {
		render(GalleryView, { props: { entries: [sampleEntry] } });
		expect(screen.getByText('Test Entry')).toBeInTheDocument();
	});

	it('renders entry_type badge text', () => {
		render(GalleryView, { props: { entries: [sampleEntry] } });
		expect(screen.getByText('note')).toBeInTheDocument();
	});

	it('renders boolean dates as their original value', () => {
		const entry = { ...sampleEntry, date: false, updated_at: undefined };
		render(GalleryView, { props: { entries: [entry] } });
		expect(screen.getByText('false')).toBeInTheDocument();
	});

	it('falls back to updated_at when date is an empty string', () => {
		const entry = { ...sampleEntry, date: '', updated_at: '2025-06-15T10:00:00Z' };
		render(GalleryView, { props: { entries: [entry] } });
		expect(screen.getByText(new Date(entry.updated_at).toLocaleDateString(undefined, {
			year: 'numeric', month: 'short', day: 'numeric'
		}))).toBeInTheDocument();
		expect(screen.queryByText('Invalid Date')).not.toBeInTheDocument();
	});

	it('hides an empty date when updated_at is also absent', () => {
		const entry = { ...sampleEntry, date: '', updated_at: '' };
		const { container } = render(GalleryView, { props: { entries: [entry], cardFields: ['date'] } });
		expect(screen.queryByText('Invalid Date')).not.toBeInTheDocument();
		expect(container.querySelector('span')).toBeNull();
	});

	it('shows body excerpt with markdown chars stripped', () => {
		render(GalleryView, { props: { entries: [sampleEntry] } });
		expect(screen.getByText('Hello world markdown link(url) code')).toBeInTheDocument();
	});

	it('truncates long body with "..." at 120 chars', () => {
		const longBody = 'A'.repeat(200);
		const longEntry = { ...sampleEntry, body: longBody, summary: undefined };
		render(GalleryView, { props: { entries: [longEntry] } });
		const expected = 'A'.repeat(120) + '...';
		expect(screen.getByText(expected)).toBeInTheDocument();
	});

	it('shows tags with max 3', () => {
		render(GalleryView, { props: { entries: [sampleEntry] } });
		expect(screen.getByText('a')).toBeInTheDocument();
		expect(screen.getByText('b')).toBeInTheDocument();
		expect(screen.getByText('c')).toBeInTheDocument();
		expect(screen.queryByText('d')).not.toBeInTheDocument();
	});

	it('shows tag overflow count for >3 tags', () => {
		render(GalleryView, { props: { entries: [sampleEntry] } });
		expect(screen.getByText('+1')).toBeInTheDocument();
	});

	it('empty entries shows "No entries in this collection"', () => {
		render(GalleryView, { props: { entries: [] } });
		expect(screen.getByText('No entries in this collection')).toBeInTheDocument();
	});

	it('renders multiple entry cards', () => {
		const entry2 = { ...sampleEntry, id: 'e2', title: 'Second Entry' };
		const entry3 = { ...sampleEntry, id: 'e3', title: 'Third Entry' };
		render(GalleryView, { props: { entries: [sampleEntry, entry2, entry3] } });
		const buttons = document.querySelectorAll('button');
		expect(buttons.length).toBe(3);
	});
});

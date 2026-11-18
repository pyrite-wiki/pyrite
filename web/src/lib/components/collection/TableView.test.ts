import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

import TableView from './TableView.svelte';

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
});

const sampleEntry = {
	id: 'e1',
	kb_name: 'kb1',
	entry_type: 'note',
	title: 'Test Entry',
	body: 'body text',
	summary: '',
	tags: ['a', 'b', 'c', 'd'],
	participants: [],
	sources: [],
	outlinks: [],
	backlinks: [],
	file_path: '/tmp/test.md',
	updated_at: '2025-06-15T10:00:00Z'
};

describe('TableView', () => {
	it('renders column header "Title"', () => {
		render(TableView, { props: { entries: [sampleEntry] } });
		expect(screen.getByText('Title')).toBeInTheDocument();
	});

	it('renders column header "Type"', () => {
		render(TableView, { props: { entries: [sampleEntry] } });
		expect(screen.getByText('Type')).toBeInTheDocument();
	});

	it('renders column header "Tags"', () => {
		render(TableView, { props: { entries: [sampleEntry] } });
		expect(screen.getByText('Tags')).toBeInTheDocument();
	});

	it('renders column header "Updated"', () => {
		render(TableView, { props: { entries: [sampleEntry] } });
		expect(screen.getByText('Updated')).toBeInTheDocument();
	});

	it('shows entry title in table row', () => {
		render(TableView, { props: { entries: [sampleEntry] } });
		expect(screen.getByText('Test Entry')).toBeInTheDocument();
	});

	it('shows entry_type badge text', () => {
		render(TableView, { props: { entries: [sampleEntry] } });
		expect(screen.getByText('note')).toBeInTheDocument();
	});

	it('shows sort indicator up arrow for default asc sort on title', () => {
		render(TableView, { props: { entries: [sampleEntry], sortBy: 'title', sortOrder: 'asc' } });
		expect(screen.getByText('↑')).toBeInTheDocument();
	});

	it('clicking a sortable header calls onSort', async () => {
		const onSort = vi.fn();
		render(TableView, { props: { entries: [sampleEntry], onSort } });
		const typeHeader = screen.getByText('Type');
		await fireEvent.click(typeHeader.closest('th')!);
		expect(onSort).toHaveBeenCalledWith('entry_type');
	});

	it('clicking tags header does NOT call onSort', async () => {
		const onSort = vi.fn();
		render(TableView, { props: { entries: [sampleEntry], onSort } });
		const tagsHeader = screen.getByText('Tags');
		await fireEvent.click(tagsHeader.closest('th')!);
		expect(onSort).not.toHaveBeenCalled();
	});

	it('renders tags with max 3 and overflow count "+1" for 4 tags', () => {
		render(TableView, { props: { entries: [sampleEntry] } });
		expect(screen.getByText('a')).toBeInTheDocument();
		expect(screen.getByText('b')).toBeInTheDocument();
		expect(screen.getByText('c')).toBeInTheDocument();
		expect(screen.queryByText('d')).not.toBeInTheDocument();
		expect(screen.getByText('+1')).toBeInTheDocument();
	});

	it('empty entries shows "No entries in this collection"', () => {
		render(TableView, { props: { entries: [] } });
		expect(screen.getByText('No entries in this collection')).toBeInTheDocument();
	});

	it('renders formatted date for updated_at column', () => {
		render(TableView, { props: { entries: [sampleEntry] } });
		const formatted = new Date('2025-06-15T10:00:00Z').toLocaleDateString();
		expect(screen.getByText(formatted)).toBeInTheDocument();
	});

	it('renders a numeric date as its original value', () => {
		const entry = { ...sampleEntry, date: 2025 };
		render(TableView, { props: { entries: [entry], columns: ['date'] } });
		expect(screen.getByText('2025')).toBeInTheDocument();
	});

	it('renders an empty date as empty instead of Invalid Date', () => {
		const entry = { ...sampleEntry, date: '' };
		render(TableView, { props: { entries: [entry], columns: ['date'] } });
		expect(screen.queryByText('Invalid Date')).not.toBeInTheDocument();
	});
});

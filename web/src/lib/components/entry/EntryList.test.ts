import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';

// Must mock $app/navigation since EntryCard imports it
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

// Use vi.hoisted so the mock object is available when vi.mock factory runs (hoisted)
const mockEntryStore = vi.hoisted(() => ({
	entries: [] as any[],
	loading: false,
	initialized: true,
	listKB: 'test-kb' as string | undefined,
	error: null as string | null,
	total: 0,
	limit: 50,
	offset: 0,
	loadList: vi.fn()
}));

const mockKBStore = vi.hoisted(() => ({
	activeKB: 'test-kb' as string | null,
	loading: false,
	initialized: true,
	error: null as string | null,
	load: vi.fn(),
	resolveName: (name: string | undefined) => name
}));

vi.mock('$lib/stores/entries.svelte', () => ({
	entryStore: mockEntryStore
}));

vi.mock('$lib/stores/kbs.svelte', () => ({
	kbStore: mockKBStore
}));

import EntryList from './EntryList.svelte';

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
});

beforeEach(() => {
	mockEntryStore.entries = [];
	mockEntryStore.loading = false;
	mockEntryStore.initialized = true;
	mockEntryStore.listKB = 'test-kb';
	mockEntryStore.error = null;
	mockEntryStore.total = 0;
	mockEntryStore.limit = 50;
	mockEntryStore.offset = 0;
	mockKBStore.activeKB = 'test-kb';
	mockKBStore.loading = false;
	mockKBStore.initialized = true;
	mockKBStore.error = null;
});

const sampleEntry = {
	id: 'e1',
	kb_name: 'kb1',
	entry_type: 'note',
	title: 'Test Entry',
	body: 'body text',
	summary: 'A summary',
	tags: ['tag1'],
	participants: [],
	sources: [],
	outlinks: [],
	backlinks: [],
	file_path: '/tmp/test.md'
};

describe('EntryList', () => {
	it('shows a skeleton before the initial KB load settles instead of an empty state', () => {
		mockKBStore.activeKB = null;
		mockKBStore.initialized = false;
		mockEntryStore.initialized = false;
		mockEntryStore.listKB = undefined;
		const { container } = render(EntryList);

		expect(container.querySelector('.animate-pulse')).toBeInTheDocument();
		expect(screen.queryByText('No entries found')).not.toBeInTheDocument();
	});

	it('shows a skeleton until the selected KB entry list has settled', () => {
		mockEntryStore.initialized = false;
		mockEntryStore.listKB = undefined;
		const { container } = render(EntryList);

		expect(container.querySelector('.animate-pulse')).toBeInTheDocument();
		expect(screen.queryByText('No entries found')).not.toBeInTheDocument();
	});

	it('shows a skeleton while the entry list catches up with a changed KB', () => {
		mockKBStore.activeKB = 'new-kb';
		mockEntryStore.listKB = 'old-kb';
		const { container } = render(EntryList);

		expect(container.querySelector('.animate-pulse')).toBeInTheDocument();
		expect(screen.queryByText('No entries found')).not.toBeInTheDocument();
	});

	it('shows the KB load error after the initial load fails', () => {
		mockKBStore.activeKB = null;
		mockKBStore.initialized = true;
		mockKBStore.error = 'KB registry unavailable';
		render(EntryList);

		expect(screen.getByText('Something went wrong')).toBeInTheDocument();
		expect(screen.getByText('KB registry unavailable')).toBeInTheDocument();
		expect(screen.queryByText('No entries found')).not.toBeInTheDocument();
	});

	it('shows skeleton loader when loading is true', () => {
		mockEntryStore.loading = true;
		const { container } = render(EntryList);
		// SkeletonLoader renders animated placeholder divs instead of text
		expect(container.querySelector('.animate-pulse')).toBeInTheDocument();
	});

	it('shows error message when error is set', () => {
		mockEntryStore.error = 'Network failure';
		render(EntryList);
		expect(screen.getByText('Something went wrong')).toBeInTheDocument();
		expect(screen.getByText('Network failure')).toBeInTheDocument();
	});

	it('shows "No entries found" when entries are empty and not loading or error', () => {
		render(EntryList);
		expect(screen.getByText('No entries found')).toBeInTheDocument();
		expect(screen.getByText('New Entry')).toBeInTheDocument();
	});

	it('shows singular "1 entry" count when total is 1', () => {
		mockEntryStore.total = 1;
		mockEntryStore.entries = [sampleEntry];
		render(EntryList);
		expect(screen.getByText('1 entry')).toBeInTheDocument();
	});

	it('shows plural "3 entries" count when total is 3', () => {
		mockEntryStore.total = 3;
		mockEntryStore.entries = [
			{ ...sampleEntry, id: 'e1' },
			{ ...sampleEntry, id: 'e2', title: 'Second Entry' },
			{ ...sampleEntry, id: 'e3', title: 'Third Entry' }
		];
		render(EntryList);
		expect(screen.getByText('3 entries')).toBeInTheDocument();
	});

	it('renders entry titles via EntryCard', () => {
		mockEntryStore.total = 2;
		mockEntryStore.entries = [
			{ ...sampleEntry, id: 'e1', title: 'Alpha Entry' },
			{ ...sampleEntry, id: 'e2', title: 'Beta Entry' }
		];
		render(EntryList);
		expect(screen.getByText('Alpha Entry')).toBeInTheDocument();
		expect(screen.getByText('Beta Entry')).toBeInTheDocument();
	});

	it('shows pagination when totalPages > 1', () => {
		mockEntryStore.total = 100;
		mockEntryStore.limit = 50;
		mockEntryStore.offset = 0;
		mockEntryStore.entries = [sampleEntry];
		render(EntryList);
		expect(screen.getByText('Page 1 of 2')).toBeInTheDocument();
	});

	it('disables Prev button on page 1', () => {
		mockEntryStore.total = 100;
		mockEntryStore.limit = 50;
		mockEntryStore.offset = 0;
		mockEntryStore.entries = [sampleEntry];
		render(EntryList);
		const prevButton = screen.getByText('Prev');
		expect(prevButton).toBeDisabled();
	});

	it('disables Next button on last page', () => {
		mockEntryStore.total = 100;
		mockEntryStore.limit = 50;
		mockEntryStore.offset = 50;
		mockEntryStore.entries = [sampleEntry];
		render(EntryList);
		const nextButton = screen.getByText('Next');
		expect(nextButton).toBeDisabled();
	});

	it('does not show pagination when there is only one page', () => {
		mockEntryStore.total = 5;
		mockEntryStore.limit = 50;
		mockEntryStore.offset = 0;
		mockEntryStore.entries = [sampleEntry];
		render(EntryList);
		expect(screen.queryByText(/Page/)).not.toBeInTheDocument();
	});
});

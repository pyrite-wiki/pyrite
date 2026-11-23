import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$app/state', async () => {
	const { createPageState } = await import('$lib/test-utils/page-state.svelte');
	return { page: createPageState({ url: new URL('http://localhost/search'), params: {} }) };
});
vi.mock('$lib/api/client', () => ({ api: { getEntryTypes: vi.fn().mockResolvedValue({ types: [] }) } }));
vi.mock('$lib/components/layout/Topbar.svelte', async () => ({
	default: (await import('./test-fixtures/Empty.svelte')).default
}));

import SearchPage from './search/+page.svelte';
import { searchStore } from '$lib/stores/search.svelte';
import { savedSearches } from '$lib/stores/saved-searches.svelte';

beforeEach(() => {
	searchStore.query = 'sample';
	searchStore.results = [
		{
			id: 'numeric-date',
			kb_name: 'kb',
			entry_type: 'note',
			title: 'Numeric date',
			date: 2025,
			tags: []
		},
		{
			id: 'empty-date',
			kb_name: 'kb',
			entry_type: 'note',
			title: 'Empty date',
			date: '',
			tags: []
		}
	];
	searchStore.loading = false;
	searchStore.error = null;
	savedSearches.items = [];
});

afterEach(() => {
	cleanup();
	searchStore.clear();
	searchStore.loading = false;
	searchStore.error = null;
});

describe('search result dates', () => {
	it('shows numeric dates as-is and omits empty dates', async () => {
		const { container } = render(SearchPage);
		expect(screen.getByText('2025')).toBeInTheDocument();
		expect(screen.getByText('Numeric date')).toBeInTheDocument();
		expect(screen.getByText('Empty date')).toBeInTheDocument();
		const results = container.querySelectorAll('a.block.rounded-lg');
		expect(results[0].querySelector('span.ml-auto')).not.toBeNull();
		expect(results[1].querySelector('span.ml-auto')).toBeNull();
		expect(screen.queryByText('Invalid Date')).not.toBeInTheDocument();
	});
});

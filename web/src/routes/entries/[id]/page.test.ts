/** Page-level coverage for the entry detail page's delete wiring (#117 review).
 *
 * DeleteEntryButton.test.ts pins the component's own contract; these tests
 * pin the PAGE's wiring around it -- the parts a reverted page (even with a
 * correct component) could still get wrong: the button exists only for an
 * editor, it disappears while editing so unsaved changes are never thrown
 * away, and a delete lands the reader in the KB they deleted from.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

const mockPage = vi.hoisted(() => {
	let params = { id: 'hub' };
	let search = '';
	return {
		get subscribe() {
			return (run: (v: unknown) => void) => {
				run({
					params,
					url: { searchParams: new URLSearchParams(search), pathname: `/entries/${params.id}` }
				});
				return () => {};
			};
		},
		set(id: string, s = '') {
			params = { id };
			search = s;
		}
	};
});
vi.mock('$app/stores', () => ({ page: mockPage }));

const mockAuthStore = vi.hoisted(() => ({
	authConfig: { enabled: false },
	isAuthenticated: false
}));
vi.mock('$lib/stores/auth.svelte', () => ({ authStore: mockAuthStore }));

const entry = {
	id: 'hub',
	kb_name: 'demo',
	title: 'Hub Design',
	entry_type: 'note',
	body: 'The hub entry body.',
	file_path: 'notes/hub.md',
	tags: ['hub'],
	aliases: [],
	importance: 5,
	status: 'active',
	date: null,
	created_at: '2026-01-01T00:00:00Z',
	updated_at: '2026-01-01T00:00:00Z',
	participants: [],
	outlinks: [],
	sources: [],
	links: [],
	backlinks: []
};
const mockEntryStore = vi.hoisted(() => ({
	current: null as typeof entry | null,
	loading: false,
	error: null as string | null,
	saving: false,
	dirty: false,
	loadEntry: vi.fn()
}));
vi.mock('$lib/stores/entries.svelte', () => ({ entryStore: mockEntryStore }));

vi.mock('$lib/stores/brand.svelte', () => ({
	brandStore: { branding: null, loading: false }
}));
const mockUiStore = vi.hoisted(() => ({
	toast: vi.fn(),
	editorMode: 'source',
	backlinksPanelOpen: false,
	outlinePanelOpen: false,
	versionHistoryPanelOpen: false,
	localGraphPanelOpen: false,
	toggleBacklinksPanel: vi.fn(),
	toggleOutlinePanel: vi.fn(),
	toggleVersionHistoryPanel: vi.fn(),
	toggleLocalGraphPanel: vi.fn(),
	toggleEditorMode: vi.fn()
}));
vi.mock('$lib/stores/ui.svelte', () => ({ uiStore: mockUiStore }));

vi.mock('$lib/api/client', () => ({
	api: {
		deleteEntry: vi.fn(),
		getEntry: vi.fn(),
		aiSummarize: vi.fn(),
		aiAutoTag: vi.fn(),
		aiSuggestLinks: vi.fn()
	},
	ApiError: class extends Error {
		status = 0;
		detail = '';
	}
}));

// AIPanel is not mocked: the toolbar (Edit/Delete wiring) renders as its
// toolbar snippet, and a null-mock would discard exactly the markup these
// tests exist to pin. Its own AI calls are no-ops through the api mock.
vi.mock('$lib/stores/ai.svelte', () => ({
	aiChatStore: {
		suggestions: { tags: [], links: [] },
		results: {},
		requestSummarize: vi.fn(),
		clearSuggestions: vi.fn()
	}
}));

// Heavy panels that have their own tests; the wiring under test never
// depends on what they render.
vi.mock('$lib/editor/Editor.svelte', () => ({ default: vi.fn(() => null) }));
vi.mock('$lib/editor/TiptapEditor.svelte', () => ({ default: vi.fn(() => null) }));
vi.mock('$lib/components/graph/LocalGraphPanel.svelte', () => ({ default: vi.fn(() => null) }));
vi.mock('$lib/components/entry/VersionHistoryPanel.svelte', () => ({
	default: vi.fn(() => null)
}));
vi.mock('$lib/components/entry/BacklinksPanel.svelte', () => ({ default: vi.fn(() => null) }));
vi.mock('$lib/components/entry/OutlinePanel.svelte', () => ({ default: vi.fn(() => null) }));

import EntryPage from './+page.svelte';
import { goto } from '$app/navigation';

beforeEach(() => {
	vi.clearAllMocks();
	mockAuthStore.authConfig = { enabled: false };
	mockAuthStore.isAuthenticated = false;
	mockPage.set('hub');
	mockEntryStore.current = { ...entry };
	mockEntryStore.loadEntry.mockImplementation(() => {});
});

afterEach(() => {
	cleanup();
});

describe('entry detail page delete wiring (#117)', () => {
	it('loads the entry from the kb the link named, not whichever KB defaults', () => {
		mockPage.set('hub', 'kb=other');
		render(EntryPage);
		expect(mockEntryStore.loadEntry).toHaveBeenCalledWith('hub', 'other');
	});

	it('shows Delete for an editor, and hides it while editing', async () => {
		render(EntryPage);
		await waitFor(() => expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument());

		await fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
		expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument();

		await fireEvent.click(screen.getByRole('button', { name: 'View' }));
		expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument();
	});

	it('never shows Delete to a signed-out reader when auth is required', () => {
		mockAuthStore.authConfig = { enabled: true };
		mockAuthStore.isAuthenticated = false;
		render(EntryPage);
		expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument();
	});

	it('a delete navigates back to the KB the entry lived in', async () => {
		render(EntryPage);
		await waitFor(() => expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument());
		await fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
		await waitFor(() => expect(vi.mocked(goto)).toHaveBeenCalledWith('/entries?kb=demo'));
	});
});

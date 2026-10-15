import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/api/client', async (importOriginal) => ({
	ApiError: (await importOriginal<typeof import('$lib/api/client')>()).ApiError,
	api: {
		listEntries: vi.fn(),
		getEntry: vi.fn(),
		updateEntry: vi.fn()
	}
}));

import { api, ApiError } from '$lib/api/client';
import { entryStore } from './entries.svelte';

const mockListEntries = vi.mocked(api.listEntries);
const mockGetEntry = vi.mocked(api.getEntry);
const mockUpdateEntry = vi.mocked(api.updateEntry);

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

type ListResponse = Awaited<ReturnType<typeof api.listEntries>>;

const sampleEntry = {
	id: 'test-entry',
	kb_name: 'test-kb',
	entry_type: 'note',
	title: 'Test Note',
	body: '# Hello',
	tags: ['test'],
	participants: [],
	sources: [],
	outlinks: [],
	backlinks: [],
	file_path: '/tmp/test.md'
};

beforeEach(() => {
	vi.clearAllMocks();
	entryStore.entries = [];
	entryStore.current = null;
	entryStore.total = 0;
	entryStore.offset = 0;
	entryStore.loading = false;
	entryStore.initialized = false;
	entryStore.listKB = undefined;
	entryStore.saving = false;
	entryStore.error = null;
	entryStore.ambiguousKbs = null;
	entryStore.recent = [];
	entryStore.dirty = false;
});

describe('EntryStore', () => {
	describe('loadList', () => {
		it.each([
			['different KBs', { kb: 'A' }, { kb: 'B', offset: 50 }],
			[
				'same KB with different filters and offsets',
				{ kb: 'B', tag: 'old' },
				{ kb: 'B', tag: 'new', offset: 50 }
			]
		])('keeps the latest response for %s when requests finish out of order', async (_, first, latest) => {
			const a = deferred<ListResponse>();
			const b = deferred<ListResponse>();
			mockListEntries.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
			const pa = entryStore.loadList(first);
			const pb = entryStore.loadList(latest);
			const newest = { ...sampleEntry, id: 'newest', kb_name: 'B' };
			b.resolve({ entries: [newest], total: 80, limit: 50, offset: 50 });
			await pb;
			a.resolve({ entries: [sampleEntry], total: 1, limit: 50, offset: 0 });
			await pa;
			expect(entryStore.entries).toEqual([newest]);
			expect(entryStore.total).toBe(80);
			expect(entryStore.offset).toBe(50);
			expect(entryStore.listKB).toBe('B');
			expect(entryStore.loading).toBe(false);
			expect(entryStore.initialized).toBe(true);
			expect(entryStore.error).toBeNull();
		});

		it('does not publish an older response or finish loading while the latest request is pending', async () => {
			entryStore.entries = [sampleEntry];
			entryStore.total = 17;
			entryStore.offset = 10;
			const a = deferred<ListResponse>();
			const b = deferred<ListResponse>();
			mockListEntries.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
			const pa = entryStore.loadList({ kb: 'A' });
			const pb = entryStore.loadList({ kb: 'B' });
			a.resolve({ entries: [], total: 0, limit: 50, offset: 0 });
			await pa;
			expect(entryStore.entries).toEqual([sampleEntry]);
			expect(entryStore.total).toBe(17);
			expect(entryStore.offset).toBe(10);
			expect(entryStore.loading).toBe(true);
			expect(entryStore.initialized).toBe(false);
			b.resolve({ entries: [], total: 0, limit: 50, offset: 0 });
			await pb;
		});

		it.each(['pending', 'successful'])('ignores an older error while the latest request is %s', async (state) => {
			const a = deferred<ListResponse>();
			const b = deferred<ListResponse>();
			mockListEntries.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
			const pa = entryStore.loadList({ kb: 'A' });
			const pb = entryStore.loadList({ kb: 'B' });
			if (state === 'successful') {
				b.resolve({ entries: [sampleEntry], total: 1, limit: 50, offset: 0 });
				await pb;
			}
			a.reject(new Error('Stale failure'));
			await pa;
			expect(entryStore.error).toBeNull();
			expect(entryStore.loading).toBe(state === 'pending');
			expect(entryStore.initialized).toBe(state === 'successful');
			if (state === 'pending') {
				b.resolve({ entries: [], total: 0, limit: 50, offset: 0 });
				await pb;
			}
		});

		it.each(['success', 'error'])('preserves the latest failure after an older %s completes', async (outcome) => {
			entryStore.entries = [sampleEntry];
			entryStore.total = 17;
			entryStore.offset = 10;
			const a = deferred<ListResponse>();
			const b = deferred<ListResponse>();
			mockListEntries.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
			const pa = entryStore.loadList({ kb: 'A' });
			const pb = entryStore.loadList({ kb: 'B' });
			b.reject(new Error('Latest failure'));
			await pb;
			if (outcome === 'success') a.resolve({ entries: [], total: 0, limit: 50, offset: 0 });
			else a.reject(new Error('Stale failure'));
			await pa;
			expect(entryStore.entries).toEqual([sampleEntry]);
			expect(entryStore.total).toBe(17);
			expect(entryStore.offset).toBe(10);
			expect(entryStore.error).toBe('Latest failure');
			expect(entryStore.loading).toBe(false);
			expect(entryStore.initialized).toBe(true);
			expect(entryStore.listKB).toBe('B');
		});

		it('populates entries from API', async () => {
			mockListEntries.mockResolvedValueOnce({
				entries: [sampleEntry],
				total: 1,
				limit: 50,
				offset: 0
			});

			await entryStore.loadList({ kb: 'test-kb' });
			expect(entryStore.entries).toHaveLength(1);
			expect(entryStore.total).toBe(1);
			expect(entryStore.initialized).toBe(true);
			expect(entryStore.listKB).toBe('test-kb');
		});

		it('handles API errors gracefully', async () => {
			mockListEntries.mockRejectedValueOnce(new Error('Server error'));
			await entryStore.loadList();
			expect(entryStore.error).toBe('Server error');
			expect(entryStore.initialized).toBe(true);
		});
	});

	describe('loadEntry', () => {
		it('sets current entry and clears dirty flag', async () => {
			mockGetEntry.mockResolvedValueOnce(sampleEntry);

			entryStore.dirty = true;
			await entryStore.loadEntry('test-entry', 'test-kb');
			expect(entryStore.current?.id).toBe('test-entry');
			expect(entryStore.dirty).toBe(false);
		});

		it('keeps the candidate KBs when the API refuses the id as AMBIGUOUS', async () => {
			mockGetEntry.mockRejectedValueOnce(
				new ApiError(409, 'ambiguous', 'AMBIGUOUS', ['kb-a', 'kb-b'])
			);
			await entryStore.loadEntry('dup');
			expect(entryStore.current).toBeNull();
			expect(entryStore.ambiguousKbs).toEqual(['kb-a', 'kb-b']);
		});

		it('clears the candidates on the next load', async () => {
			entryStore.ambiguousKbs = ['kb-a', 'kb-b'];
			mockGetEntry.mockResolvedValueOnce(sampleEntry);
			await entryStore.loadEntry('dup', 'kb-a');
			expect(entryStore.ambiguousKbs).toBeNull();
		});

		it('remembers the KB and title a recent entry was opened from', async () => {
			mockGetEntry.mockResolvedValueOnce(sampleEntry);
			await entryStore.loadEntry('test-entry');
			expect(entryStore.recent[0]).toEqual({
				id: 'test-entry',
				kb: 'test-kb',
				title: 'Test Note'
			});
		});

		it('keeps same-id entries of two KBs as two recents (#912)', async () => {
			mockGetEntry.mockResolvedValueOnce({ ...sampleEntry, id: 'dup', kb_name: 'kb-a' });
			await entryStore.loadEntry('dup', 'kb-a');
			mockGetEntry.mockResolvedValueOnce({ ...sampleEntry, id: 'dup', kb_name: 'kb-b' });
			await entryStore.loadEntry('dup', 'kb-b');
			expect(entryStore.recent.map((r) => `${r.kb}:${r.id}`)).toEqual(['kb-b:dup', 'kb-a:dup']);
		});

		it('ignores a load that a newer one overtook', async () => {
			const slow = deferred<typeof sampleEntry>();
			mockGetEntry.mockReturnValueOnce(slow.promise as never);
			const first = entryStore.loadEntry('dup', 'kb-a');
			mockGetEntry.mockResolvedValueOnce({ ...sampleEntry, id: 'dup', kb_name: 'kb-b' });
			await entryStore.loadEntry('dup', 'kb-b');
			slow.resolve({ ...sampleEntry, id: 'dup', kb_name: 'kb-a' });
			await first;
			expect(entryStore.current?.kb_name).toBe('kb-b');
		});

		it('adds to recent IDs', async () => {
			mockGetEntry.mockResolvedValueOnce(sampleEntry);
			await entryStore.loadEntry('test-entry');
			expect(entryStore.recentIds).toContain('test-entry');
		});

		it('deduplicates recent IDs', async () => {
			mockGetEntry.mockResolvedValue(sampleEntry);
			await entryStore.loadEntry('test-entry');
			await entryStore.loadEntry('test-entry');
			const count = entryStore.recentIds.filter((id) => id === 'test-entry').length;
			expect(count).toBe(1);
		});
	});

	describe('save', () => {
		it('calls updateEntry and reloads', async () => {
			mockUpdateEntry.mockResolvedValueOnce({ updated: true, id: 'test-entry' });
			mockGetEntry.mockResolvedValueOnce(sampleEntry);

			await entryStore.save('test-entry', 'test-kb', { body: 'Updated' });
			expect(mockUpdateEntry).toHaveBeenCalledWith('test-entry', { kb: 'test-kb', body: 'Updated' });
			expect(entryStore.dirty).toBe(false);
		});

		it('propagates errors and keeps dirty flag', async () => {
			mockUpdateEntry.mockRejectedValueOnce(new Error('Save failed'));

			await expect(entryStore.save('x', 'kb', { body: '...' })).rejects.toThrow('Save failed');
			expect(entryStore.error).toBe('Save failed');
		});
	});

	describe('markDirty', () => {
		it('sets dirty flag', () => {
			expect(entryStore.dirty).toBe(false);
			entryStore.markDirty();
			expect(entryStore.dirty).toBe(true);
		});
	});
});

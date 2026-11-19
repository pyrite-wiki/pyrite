import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the API client
vi.mock('$lib/api/client', () => ({
	api: {
		listKBs: vi.fn()
	}
}));

import { api } from '$lib/api/client';
import { kbStore } from './kbs.svelte';

const mockListKBs = vi.mocked(api.listKBs);

beforeEach(() => {
	mockListKBs.mockReset();
	// Reset store state
	kbStore.kbs = [];
	kbStore.activeKB = null;
	kbStore.loading = false;
	kbStore.error = null;
	kbStore.initialized = false;
});

describe('KBStore', () => {
	describe('load', () => {
		it('populates kbs from API response', async () => {
			mockListKBs.mockResolvedValueOnce({
				kbs: [
					{ name: 'events', type: 'events', path: '/tmp/events', entries: 10, indexed: true, source: 'local', description: '', read_only: false, last_indexed: null, shortname: null, default_role: null },
					{ name: 'research', type: 'research', path: '/tmp/research', entries: 5, indexed: true, source: 'local', description: '', read_only: false, last_indexed: null, shortname: null, default_role: null }
				],
				total: 2
			});

			await kbStore.load();
			expect(kbStore.kbs).toHaveLength(2);
			expect(kbStore.kbs[0].name).toBe('events');
			expect(kbStore.initialized).toBe(true);
		});

		it('auto-selects first KB when none active', async () => {
			mockListKBs.mockResolvedValueOnce({
				kbs: [{ name: 'first-kb', type: 'generic', path: '/tmp', entries: 0, indexed: false, source: 'local', description: '', read_only: false, last_indexed: null, shortname: null, default_role: null }],
				total: 1
			});

			await kbStore.load();
			expect(kbStore.activeKB).toBe('first-kb');
		});

		it('preserves existing activeKB on reload', async () => {
			kbStore.activeKB = 'existing';
			mockListKBs.mockResolvedValueOnce({
				kbs: [{ name: 'first-kb', type: 'generic', path: '/tmp', entries: 0, indexed: false, source: 'local', description: '', read_only: false, last_indexed: null, shortname: null, default_role: null }],
				total: 1
			});

			await kbStore.load();
			expect(kbStore.activeKB).toBe('existing');
		});

		it('sets error on API failure', async () => {
			mockListKBs.mockRejectedValueOnce(new Error('Network error'));

			await kbStore.load();
			expect(kbStore.error).toBe('Network error');
			expect(kbStore.kbs).toHaveLength(0);
			expect(kbStore.initialized).toBe(true);
		});

		it('sets loading flag during request', async () => {
			let resolvePromise: (value: unknown) => void;
			mockListKBs.mockReturnValueOnce(new Promise((r) => { resolvePromise = r; }) as never);

			const loadPromise = kbStore.load();
			expect(kbStore.loading).toBe(true);

			resolvePromise!({ kbs: [], total: 0 });
			await loadPromise;
			expect(kbStore.loading).toBe(false);
		});
	});

	describe('setActive', () => {
		it('changes activeKB', () => {
			kbStore.setActive('my-kb');
			expect(kbStore.activeKB).toBe('my-kb');
		});
	});
});

describe('resolveName', () => {
	const kb = (name: string, shortname: string | null) => ({
		name, type: 'generic', path: `/tmp/${name}`, entries: 0, indexed: true, source: 'local',
		description: '', read_only: false, last_indexed: null, shortname, default_role: null
	});

	beforeEach(() => {
		kbStore.kbs = [kb('pub', 'pb'), kb('notes', null), kb('alpha', 'notes')];
	});

	it('maps a shortname to the full name', () => {
		expect(kbStore.resolveName('pb')).toBe('pub');
	});

	it('leaves a full name as it is, even when it is another KB\'s shortname', () => {
		expect(kbStore.resolveName('pub')).toBe('pub');
		expect(kbStore.resolveName('notes')).toBe('notes');
	});

	it('leaves a name it does not know unchanged, for the API to refuse', () => {
		expect(kbStore.resolveName('nope')).toBe('nope');
	});

	it('passes an absent name through', () => {
		expect(kbStore.resolveName(undefined)).toBeUndefined();
		expect(kbStore.resolveName('')).toBe('');
	});
});

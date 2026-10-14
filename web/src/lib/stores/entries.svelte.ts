/** Entry store: current entry, entry list, dirty state */

import { api } from '$lib/api/client';
import type { EntryResponse } from '$lib/api/types';

class EntryStore {
	entries = $state<EntryResponse[]>([]);
	current = $state<EntryResponse | null>(null);
	total = $state(0);
	limit = $state(50);
	offset = $state(0);
	loading = $state(false);
	initialized = $state(false);
	listKB = $state<string | undefined>(undefined);
	saving = $state(false);
	error = $state<string | null>(null);
	dirty = $state(false);
	recentIds = $state<string[]>([]);
	recentTitles = $state<Record<string, string>>({});
	sortBy = $state('updated_at');
	sortOrder = $state<'asc' | 'desc'>('desc');
	private listRequestId = 0;

	async loadList(options: {
		kb?: string;
		entry_type?: string;
		tag?: string;
		status?: string;
		min_importance?: number;
		offset?: number;
	} = {}) {
		const requestId = ++this.listRequestId;
		this.loading = true;
		this.initialized = false;
		this.listKB = options.kb;
		this.error = null;
		try {
			const res = await api.listEntries({
				kb: options.kb,
				entry_type: options.entry_type,
				tag: options.tag,
				status: options.status,
				min_importance: options.min_importance,
				sort_by: this.sortBy,
				sort_order: this.sortOrder,
				limit: this.limit,
				offset: options.offset ?? this.offset
			});
			if (requestId !== this.listRequestId) return;
			this.entries = res.entries;
			this.total = res.total;
			this.offset = res.offset;
		} catch (e) {
			if (requestId === this.listRequestId) {
				this.error = e instanceof Error ? e.message : 'Failed to load entries';
			}
		} finally {
			if (requestId === this.listRequestId) {
				this.loading = false;
				this.initialized = true;
			}
		}
	}

	async loadEntry(id: string, kb?: string) {
		this.loading = true;
		this.error = null;
		try {
			this.current = await api.getEntry(id, { kb, with_links: true });
			this.dirty = false;
			this.addRecent(id);
		} catch (e) {
			this.error = e instanceof Error ? e.message : 'Failed to load entry';
			this.current = null;
		} finally {
			this.loading = false;
		}
	}

	async save(id: string, kb: string, updates: { title?: string; body?: string; tags?: string[] }) {
		this.saving = true;
		this.error = null;
		try {
			await api.updateEntry(id, { kb, ...updates });
			this.dirty = false;
			// Reload to get server-side updates
			await this.loadEntry(id, kb);
		} catch (e) {
			this.error = e instanceof Error ? e.message : 'Failed to save';
			throw e;
		} finally {
			this.saving = false;
		}
	}

	markDirty() {
		this.dirty = true;
	}

	private addRecent(id: string) {
		this.recentIds = [id, ...this.recentIds.filter((r) => r !== id)].slice(0, 10);
		if (this.current?.title) {
			this.recentTitles = { ...this.recentTitles, [id]: this.current.title };
		}
	}
}

export const entryStore = new EntryStore();

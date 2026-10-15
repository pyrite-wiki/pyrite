/** Entry store: current entry, entry list, dirty state */

import { api, ApiError } from '$lib/api/client';
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
	/** KBs holding the requested id when the API refused it as AMBIGUOUS (#910). */
	ambiguousKbs = $state<string[] | null>(null);
	dirty = $state(false);
	/** Recently opened entries, keyed on (kb, id): two KBs' twins stay two rows (#912). */
	recent = $state<{ id: string; kb: string; title: string }[]>([]);
	private loadRequestId = 0;

	get recentIds(): string[] {
		return this.recent.map((r) => r.id);
	}
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
		const requestId = ++this.loadRequestId;
		this.loading = true;
		this.error = null;
		this.ambiguousKbs = null;
		try {
			const entry = await api.getEntry(id, { kb, with_links: true });
			if (requestId !== this.loadRequestId) return; // a newer load owns the page
			this.current = entry;
			this.dirty = false;
			this.addRecent(id);
		} catch (e) {
			if (requestId !== this.loadRequestId) return;
			if (e instanceof ApiError && e.code === 'AMBIGUOUS' && e.candidateKbs?.length) {
				this.ambiguousKbs = e.candidateKbs;
			}
			this.error = e instanceof Error ? e.message : 'Failed to load entry';
			this.current = null;
		} finally {
			if (requestId === this.loadRequestId) this.loading = false;
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
		const kb = this.current?.kb_name ?? '';
		const title = this.current?.title ?? id;
		this.recent = [
			{ id, kb, title },
			...this.recent.filter((r) => !(r.id === id && r.kb === kb))
		].slice(0, 10);
	}
}

export const entryStore = new EntryStore();

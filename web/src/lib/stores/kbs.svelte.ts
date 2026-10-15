/** KB list + active KB store using Svelte 5 runes */

import { api } from '$lib/api/client';
import type { KBHealthResponse, KBInfo, KBReindexResponse } from '$lib/api/types';

class KBStore {
	kbs = $state<KBInfo[]>([]);
	activeKB = $state<string | null>(null);
	loading = $state(false);
	error = $state<string | null>(null);
	initialized = $state(false);

	get activeKBInfo(): KBInfo | undefined {
		return this.kbs.find((kb) => kb.name === this.activeKB);
	}

	/**
	 * The full KB name a `kb:` wikilink prefix means. A shortname is link
	 * syntax only (`[[pb:id]]`): the API takes the full name everywhere and
	 * refuses a shortname, so every href the web builds and every request it
	 * sends resolves the prefix here first. A full name is itself, and wins
	 * over another KB's shortname; a name this store does not know is
	 * returned unchanged, for the API to refuse by its own rule.
	 */
	resolveName(name: string): string;
	resolveName(name: string | undefined): string | undefined;
	resolveName(name: string | undefined): string | undefined {
		if (!name) return name;
		if (this.kbs.some((kb) => kb.name === name)) return name;
		const byShortname = this.kbs.find((kb) => kb.shortname === name);
		return byShortname ? byShortname.name : name;
	}

	async load() {
		this.loading = true;
		this.error = null;
		try {
			const res = await api.listKBs();
			this.kbs = res.kbs;
			// Default to guide KB if available, otherwise first KB
			if (!this.activeKB && res.kbs.length > 0) {
				const guide = res.kbs.find((kb) => kb.name === 'guide');
				this.activeKB = guide ? guide.name : res.kbs[0].name;
			}
		} catch (e) {
			this.error = e instanceof Error ? e.message : 'Failed to load KBs';
		} finally {
			this.loading = false;
			this.initialized = true;
		}
	}

	setActive(name: string) {
		this.activeKB = name;
	}

	async add(req: {
		name: string;
		path: string;
		kb_type?: string;
		description?: string;
	}): Promise<void> {
		await api.createKB(req);
		await this.load();
	}

	async remove(name: string): Promise<void> {
		await api.deleteKB(name);
		await this.load();
	}

	async reindex(name: string): Promise<KBReindexResponse> {
		const result = await api.reindexKB(name);
		await this.load();
		return result;
	}

	async getHealth(name: string): Promise<KBHealthResponse> {
		return api.getKBHealth(name);
	}
}

export const kbStore = new KBStore();

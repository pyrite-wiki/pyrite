import { kbStore } from '$lib/stores/kbs.svelte';

/**
 * The href of an entry, optionally within a KB. `kb` may be a wikilink
 * prefix (a shortname): it is resolved to the full name, which is the only
 * spelling the entry page's `?kb=` and the API accept.
 */
export function entryHref(id: string, kb?: string): string {
	const path = `/entries/${encodeURIComponent(id)}`;
	const full = kbStore.resolveName(kb);
	return full ? `${path}?kb=${encodeURIComponent(full)}` : path;
}

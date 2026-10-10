/** Keep an entry's KB context while encoding both URL components. */
export function entryHref(id: string, kb?: string): string {
	const path = `/entries/${encodeURIComponent(id)}`;
	return kb ? `${path}?kb=${encodeURIComponent(kb)}` : path;
}

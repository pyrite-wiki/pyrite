/**
 * Whether a URL is safe to render as a clickable link.
 *
 * Used for entry-supplied and operator-supplied URLs (source citations,
 * branding links) that reach an `href` without going through the app's own
 * router. A `javascript:`, `data:`, `vbscript:` or other executable scheme
 * there runs on click; only `http`/`https` (and a bare protocol-relative or
 * scheme-less string, which browsers refuse to navigate as script) render
 * as a link (P-B1). Anything else should render as plain text instead.
 */
export function isSafeLinkUrl(url: string | null | undefined): boolean {
	if (!url) return false;
	let parsed: URL;
	try {
		// No base: a relative or protocol-relative string throws here rather
		// than silently resolving to http/https against a placeholder origin
		// (these fields are expected to hold an absolute URL already).
		parsed = new URL(url.trim());
	} catch {
		return false;
	}
	return parsed.protocol === 'http:' || parsed.protocol === 'https:';
}

/**
 * Whether a URL is safe to render as a clickable link.
 *
 * Used for entry-supplied and operator-supplied URLs (source citations,
 * branding links) that reach an `href` without going through the app's own
 * router. A `javascript:`, `data:`, `vbscript:` or other executable scheme
 * there runs on click; only `http`/`https` (absolute), or a same-origin
 * relative reference (an absolute path, a bare relative path, a fragment or
 * a query string), render as a link (P-B1). A protocol-relative reference
 * (`//host/path`) is rejected even though it looks relative: it resolves
 * against a different host, unlike every other relative form. Anything else
 * renders as plain text instead.
 */
const PLACEHOLDER_ORIGIN = 'http://pyrite-safe-url.invalid';

export function isSafeLinkUrl(url: string | null | undefined): boolean {
	if (!url) return false;
	const trimmed = url.trim();
	// A protocol-relative reference resolves to a DIFFERENT host under any
	// base -- reject it before parsing rather than relying on a host
	// comparison, which would also have to handle "//" appearing after
	// whitespace/control-character stripping browsers do during navigation.
	if (trimmed.startsWith('//')) return false;

	let parsed: URL;
	try {
		parsed = new URL(trimmed, PLACEHOLDER_ORIGIN);
	} catch {
		return false;
	}
	if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
		// Either an absolute http(s) URL (any host), or a same-origin
		// relative reference resolved against the placeholder origin.
		return true;
	}
	return false;
}

/**
 * Render a chat message's text, turning `[[entry-id]]` citations into links.
 *
 * `msg.content` is model output that can be steered by KB content a
 * different user wrote (prompt injection, private #12): the system prompt
 * includes up to 500 characters of each retrieved entry body. A citation
 * payload like `[[x" autofocus onfocus="alert(1)]]` must not be able to
 * break out of the `href` attribute the naive `<a href="/entries/$1">`
 * template builds.
 *
 * Two independent guards, not one:
 *  - `escapeHtml` covers the surrounding text and escapes `"`/`'` too (the
 *    prior copy used `div.textContent = text; return div.innerHTML`, which
 *    leaves quotes untouched -- browsers don't escape them in text nodes).
 *  - the citation id must match `ENTRY_ID_PATTERN` before it is linkified
 *    at all; anything else (a space, a quote, `<`, `/`) stays literal
 *    escaped text instead of becoming an `<a>`. `encodeURIComponent` on top
 *    is defence in depth for the path segment.
 */
import { escapeHtml } from '$lib/utils/sanitize';

/** The charset a citation target (an entry id) may use to become a link. */
const ENTRY_ID_PATTERN = /^[A-Za-z0-9._:-]+$/;

const CITATION_PATTERN = /\[\[([^\]]+)\]\]/g;

export function renderCitations(text: string): string {
	const escaped = escapeHtml(text);
	return escaped.replace(CITATION_PATTERN, (match, id: string) => {
		if (!ENTRY_ID_PATTERN.test(id)) {
			// Not a safe id -- leave the (already-escaped) literal text alone
			// rather than linkify it.
			return match;
		}
		const href = `/entries/${encodeURIComponent(id)}`;
		return `<a href="${href}" class="text-blue-600 hover:underline dark:text-blue-400">${id}</a>`;
	});
}

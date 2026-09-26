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
 * There is no safe *charset* to gate the id on: `_validate_entry_id`
 * (`pyrite/storage/repository.py`) only forbids "/", "\", a null byte, a
 * leading ".", and an empty string -- a space, an apostrophe, parentheses
 * and Unicode are all valid entry ids, and the wikilink forms `[[id|label]]`
 * / `[[id#heading]]` are valid citation syntax too (`markdownToHtml`
 * recognises the same forms). Gating on a charset silently unlinks a real
 * citation instead of rejecting an attack. So every id is linkified; safety
 * comes from two independent, context-correct encodings instead:
 *  - the `href` is built from the RAW id via `encodeURIComponent`, which
 *    escapes quotes, angle brackets and path separators for the URL
 *    context -- an id can never break out of the attribute or the
 *    `/entries/` path.
 *  - the visible text (the label, or the id/heading when there is no
 *    label) is escaped for the HTML context via `escapeHtml`, which also
 *    escapes quotes (the prior `div.textContent`/`innerHTML` round-trip did
 *    not) -- it can never break out of the anchor element either.
 * Plain surrounding text is escaped the same way.
 */
import { escapeHtml } from '$lib/utils/sanitize';

// [[id]], [[id|label]], [[id#heading]], [[id#heading|label]]. Heading and
// label are cosmetic here (only the id becomes the href); a bare "[[" with
// no closing "]]" is left untouched by the trailing `]]` requirement.
const CITATION_PATTERN = /\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]/g;

export function renderCitations(text: string): string {
	let result = '';
	let lastIndex = 0;
	for (const match of text.matchAll(CITATION_PATTERN)) {
		const [full, id, heading, label] = match;
		const index = match.index ?? 0;
		result += escapeHtml(text.slice(lastIndex, index));
		const href = `/entries/${encodeURIComponent(id)}`;
		const display = label ?? (heading ? `${id} § ${heading}` : id);
		result += `<a href="${href}" class="text-blue-600 hover:underline dark:text-blue-400">${escapeHtml(display)}</a>`;
		lastIndex = index + full.length;
	}
	result += escapeHtml(text.slice(lastIndex));
	return result;
}

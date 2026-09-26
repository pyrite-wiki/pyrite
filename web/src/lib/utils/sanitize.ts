/**
 * HTML sanitization for every `{@html}` sink that renders KB content.
 *
 * `marked` passes raw HTML in markdown through verbatim, and entry bodies are
 * written by other users (and by agents ingesting untrusted web content), so
 * rendered markdown must be sanitized before it reaches the DOM.
 */
import DOMPurify from 'dompurify';

// `<input>` is otherwise forbidden below (a form-input vector -- `type=image`
// with `onerror`/`formaction`, or a bare text input another page could read
// back via a shared form). The one legitimate use in rendered markdown is a
// task-list checkbox (`marked` emits `<input disabled type="checkbox">` for
// `- [ ] x`), which the Tiptap editor reads back via `htmlToMarkdown`
// (`querySelector('input[type="checkbox"]')`). Narrow the allowance to
// exactly that: strip any `<input>` that isn't `type="checkbox"`, and strip
// every attribute off the ones that remain except `type`/`checked`/`disabled`
// -- so `onfocus`, `name`, `value`, `formaction` etc. can never ride along.
DOMPurify.addHook('uponSanitizeElement', (node, data) => {
	if (data.tagName !== 'input') return;
	const el = node as Element;
	const type = el.getAttribute && el.getAttribute('type');
	if (type !== 'checkbox') {
		el.remove();
		return;
	}
	for (const attr of Array.from(el.attributes ?? [])) {
		if (attr.name !== 'type' && attr.name !== 'checked' && attr.name !== 'disabled') {
			el.removeAttribute(attr.name);
		}
	}
	el.setAttribute('disabled', '');
});

/** Sanitize rendered markdown. Keeps ids, classes and data-* attributes, which
 * heading anchors, block-id anchors, wikilinks and callouts rely on. Keeps a
 * disabled task-list checkbox (see the hook above); forbids every other
 * form-input vector. */
export function sanitizeHtml(html: string): string {
	return DOMPurify.sanitize(html, {
		USE_PROFILES: { html: true },
		FORBID_TAGS: ['style', 'form', 'button', 'textarea', 'select'],
		FORBID_ATTR: ['style']
	});
}

/** Serialize data for embedding inside a <script> element. JSON.stringify
 * leaves "</script>" intact, which would end the element early and let the
 * rest of the string run as markup. Escaping "<" keeps the JSON equivalent. */
export function jsonForScriptTag(data: unknown): string {
	return JSON.stringify(data).replace(/</g, '\\u003c');
}

/** Escape text for the HTML text-node context, including quotes -- so a
 * caller that later interpolates the result into an attribute (a citation
 * link's `href`, a highlighted-match's surrounding text) cannot have a `"`
 * or `'` close that attribute early. `div.textContent = text; div.innerHTML`
 * (the pattern this replaces in ChatSidebar.svelte and search/+page.svelte)
 * escapes `& < >` but leaves quotes untouched, since browsers don't need to
 * escape them in a text node -- only in an attribute value. */
export function escapeHtml(text: string): string {
	return text
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&#39;');
}

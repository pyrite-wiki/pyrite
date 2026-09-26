import { describe, expect, it } from 'vitest';
import { renderCitations } from './renderCitations';

describe('renderCitations', () => {
	it('turns a plain [[entry-id]] into a link', () => {
		const html = renderCitations('See [[my-entry]] for details.');
		expect(html).toContain('href="/entries/my-entry"');
		expect(html).toContain('>my-entry<');
	});

	it('links a real-world entry id containing a space and an apostrophe', () => {
		// _validate_entry_id (pyrite/storage/repository.py) only forbids "/",
		// "\", a null byte, a leading ".", and an empty string -- spaces,
		// apostrophes, parentheses and Unicode are all valid entry ids. A
		// charset gate that rejects these silently unlinks a real citation.
		const id = "o'brien's diary (1922)";
		const html = renderCitations(`See [[${id}]] for details.`);
		expect(html).toContain(`href="/entries/${encodeURIComponent(id)}"`);
		expect(html).toContain('o&#39;brien&#39;s diary (1922)');
	});

	it('links a Unicode entry id', () => {
		const html = renderCitations('See [[café-événement]] for details.');
		const expectedHref = `/entries/${encodeURIComponent('café-événement')}`;
		expect(html).toContain(`href="${expectedHref}"`);
		expect(html).toContain('café-événement');
	});

	it('supports the [[id|label]] form, showing the label but linking the id', () => {
		const html = renderCitations("See [[my-entry|A Nice Title]] for details.");
		expect(html).toContain('href="/entries/my-entry"');
		expect(html).toContain('>A Nice Title<');
		expect(html).not.toContain('|A Nice Title');
	});

	it('supports the [[id#heading]] form, linking only the id', () => {
		const html = renderCitations('See [[my-entry#Intro]] for details.');
		expect(html).toContain('href="/entries/my-entry"');
		expect(html).not.toContain('href="/entries/my-entry#Intro"');
	});

	it('escapes an attribute-injection payload inside an id: quotes never reach a live attribute', () => {
		const html = renderCitations('[[x" autofocus onfocus="alert(document.domain)]]');
		// The id is no longer charset-gated, so it IS linkified -- but the
		// quote must be escaped/encoded so it cannot close the href attribute
		// early: the payload survives only as inert, percent-encoded path
		// text, never as a live, unencoded attribute.
		expect(html).not.toContain('" autofocus');
		expect(html).not.toContain('href="/entries/x" autofocus');
		// Exactly one <a ...> tag's worth of LIVE attributes: href and class.
		const match = html.match(/<a\b[^>]*>/);
		expect(match).not.toBeNull();
		const tag = match![0];
		expect(tag).not.toMatch(/\sonfocus=/);
		expect(tag).not.toMatch(/\sautofocus(\s|>|=)/);
		// The two real attributes are exactly href and class.
		const attrNames = [...tag.matchAll(/\s([a-zA-Z-]+)=/g)].map((m) => m[1]);
		expect(attrNames).toEqual(['href', 'class']);
	});

	it('escapes a literal double quote in message text so it cannot break out of an attribute', () => {
		const html = renderCitations('He said "hello" and [[entry-1]]');
		expect(html).not.toContain('"hello"');
		expect(html).toContain('&quot;hello&quot;');
	});

	it('URL-encodes an id containing a path separator so it cannot escape /entries/', () => {
		const html = renderCitations('[[a/b]]');
		expect(html).toContain(`href="/entries/${encodeURIComponent('a/b')}"`);
		expect(html).not.toContain('href="/entries/a/b"');
	});

	it('escapes an id containing a raw angle bracket in the link text', () => {
		const html = renderCitations('[[<script>]]');
		expect(html).not.toContain('<script>');
		expect(html).toContain('&lt;script&gt;');
	});

	it('still escapes plain HTML in surrounding text', () => {
		const html = renderCitations('<img src=x onerror=alert(1)> [[entry-1]]');
		expect(html).not.toContain('<img');
		expect(html).toContain('&lt;img');
	});
});

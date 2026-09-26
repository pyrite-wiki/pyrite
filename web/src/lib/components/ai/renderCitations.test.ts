import { describe, expect, it } from 'vitest';
import { renderCitations } from './renderCitations';

describe('renderCitations', () => {
	it('turns a plain [[entry-id]] into a link', () => {
		const html = renderCitations('See [[my-entry]] for details.');
		expect(html).toContain('href="/entries/my-entry"');
		expect(html).toContain('>my-entry<');
	});

	it('produces no attribute other than href and class for an attribute-injection payload (private #12)', () => {
		const html = renderCitations('[[x" autofocus onfocus="alert(document.domain)]]');
		// The malicious id contains characters outside the entry-id charset,
		// so it must not become a link at all -- "autofocus"/"onfocus" may
		// still appear as inert escaped text, but never inside a live tag.
		expect(html).not.toContain('<a ');
		expect(html).not.toContain('<a>');
		// The quote is escaped, so it cannot close the (nonexistent) attribute.
		expect(html).toContain('&quot;');
		expect(html).not.toContain('x" autofocus');
	});

	it('escapes a literal double quote in message text so it cannot break out of an attribute', () => {
		const html = renderCitations('He said "hello" and [[entry-1]]');
		expect(html).not.toContain('"hello"');
		expect(html).toContain('&quot;hello&quot;');
	});

	it('URL-encodes the id in the href', () => {
		const html = renderCitations('[[a/b]]');
		// "a/b" is outside the safe entry-id charset (contains "/"), so it
		// must not be linkified -- but if a future charset ever allowed a
		// path separator, encodeURIComponent must still protect the path.
		expect(html).not.toContain('href="/entries/a/b"');
	});

	it('rejects an id with a space or angle bracket', () => {
		const html = renderCitations('[[my entry]] and [[<script>]]');
		expect(html).not.toContain('<a ');
	});

	it('accepts the documented safe id charset', () => {
		const html = renderCitations('[[abc-DEF_123.456:789]]');
		expect(html).toContain('href="/entries/abc-DEF_123.456%3A789"');
	});

	it('still escapes plain HTML in surrounding text', () => {
		const html = renderCitations('<img src=x onerror=alert(1)> [[entry-1]]');
		expect(html).not.toContain('<img');
		expect(html).toContain('&lt;img');
	});
});

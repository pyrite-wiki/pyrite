import { describe, expect, it } from 'vitest';
import { marked } from 'marked';
import { escapeHtml, jsonForScriptTag, sanitizeHtml } from './sanitize';

const render = (md: string) => sanitizeHtml(marked.parse(md, { async: false }) as string);

describe('sanitizeHtml', () => {
	it('strips script tags that markdown passes through verbatim', () => {
		expect(render('hello <script>alert(1)</script>')).not.toContain('<script');
	});

	it('strips inline event handlers', () => {
		const html = render('<img src=x onerror="alert(1)">');
		expect(html).not.toContain('onerror');
	});

	it('strips javascript: URLs from markdown links', () => {
		expect(render('[click](javascript:alert(1))')).not.toContain('javascript:');
	});

	it('strips iframes and embedded objects', () => {
		const html = render('<iframe src="https://evil.example"></iframe><object data="x"></object>');
		expect(html).not.toContain('<iframe');
		expect(html).not.toContain('<object');
	});

	it('keeps a disabled task-list checkbox', () => {
		const html = render('- [ ] todo\n- [x] done');
		expect(html).toContain('type="checkbox"');
		expect(html).toContain('disabled');
	});

	it('strips attributes other than type/checked/disabled from a checkbox input', () => {
		const html = sanitizeHtml(
			'<input type="checkbox" onfocus="alert(1)" name="x" value="y" checked>'
		);
		expect(html).toContain('type="checkbox"');
		expect(html).not.toContain('onfocus');
		expect(html).not.toContain('name=');
		expect(html).not.toContain('value=');
	});

	it('strips a non-checkbox input entirely (a form-submission vector)', () => {
		const html = sanitizeHtml('<input type="image" src="x" onerror="alert(1)" formaction="evil">');
		expect(html).not.toContain('<input');
	});

	it('strips forms entirely', () => {
		const html = sanitizeHtml('<form action="https://evil.example"><input type="submit"></form>');
		expect(html).not.toContain('<form');
		expect(html).not.toContain('<input');
	});

	it('keeps the markup the entry renderer depends on', () => {
		const html = sanitizeHtml(
			'<h2 id="my-heading">T</h2><p id="block-abc">x</p>' +
				'<a href="/entries/foo" class="wikilink" data-wikilink="foo">foo</a>' +
				'<div class="callout callout-info">c</div><pre><code class="language-py">x</code></pre>'
		);
		expect(html).toContain('id="my-heading"');
		expect(html).toContain('id="block-abc"');
		expect(html).toContain('href="/entries/foo"');
		expect(html).toContain('class="wikilink"');
		expect(html).toContain('callout-info');
		expect(html).toContain('language-py');
	});
});

describe('escapeHtml', () => {
	it('escapes the five HTML/attribute-sensitive characters', () => {
		expect(escapeHtml(`<>&"'`)).toBe('&lt;&gt;&amp;&quot;&#39;');
	});

	it('leaves ordinary text untouched', () => {
		expect(escapeHtml('hello world')).toBe('hello world');
	});
});

describe('jsonForScriptTag', () => {
	it('cannot be broken out of with a closing script tag', () => {
		const out = jsonForScriptTag({ name: '</script><script>alert(1)</script>' });
		expect(out).not.toContain('</script');
		expect(JSON.parse(out).name).toBe('</script><script>alert(1)</script>');
	});
});

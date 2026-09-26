import { describe, expect, it } from 'vitest';
import { markdownToHtml } from './markdown';

describe('markdownToHtml', () => {
	it('strips script tags that markdown passes through verbatim', () => {
		expect(markdownToHtml('hello <script>alert(1)</script>')).not.toContain('<script');
	});

	it('strips inline event handlers', () => {
		expect(markdownToHtml('<img src=x onerror="alert(1)">')).not.toContain('onerror');
	});

	it('strips javascript: URLs from markdown links', () => {
		expect(markdownToHtml('[click](javascript:alert(1))')).not.toContain('javascript:');
	});

	it('strips no*-attribute vectors on a transcluded/pasted raw <a>', () => {
		const html = markdownToHtml('<a href="#" onmouseover="alert(document.domain)">x</a>');
		expect(html).not.toContain('onmouseover');
	});

	it('keeps a task-list checkbox so the editor can read it back', () => {
		const html = markdownToHtml('- [ ] todo\n- [x] done');
		expect(html).toContain('type="checkbox"');
	});

	it('keeps wikilink spans it builds itself', () => {
		const html = markdownToHtml('[[my-entry]]');
		expect(html).toContain('data-wikilink="my-entry"');
	});

	it('keeps transclusion divs it builds itself', () => {
		const html = markdownToHtml('![[my-entry]]');
		expect(html).toContain('data-transclusion="my-entry"');
	});
});

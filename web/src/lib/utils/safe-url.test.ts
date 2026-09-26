import { describe, expect, it } from 'vitest';
import { isSafeLinkUrl } from './safe-url';

describe('isSafeLinkUrl', () => {
	it('accepts http URLs', () => {
		expect(isSafeLinkUrl('http://example.com')).toBe(true);
	});

	it('accepts https URLs', () => {
		expect(isSafeLinkUrl('https://example.com/path?q=1')).toBe(true);
	});

	it('rejects javascript: URLs', () => {
		expect(isSafeLinkUrl('javascript:alert(document.domain)')).toBe(false);
	});

	it('rejects data: URLs', () => {
		expect(isSafeLinkUrl('data:text/html,<script>alert(1)</script>')).toBe(false);
	});

	it('rejects vbscript: URLs', () => {
		expect(isSafeLinkUrl('vbscript:msgbox(1)')).toBe(false);
	});

	it('rejects a case/whitespace-obfuscated javascript: URL', () => {
		expect(isSafeLinkUrl(' \n\tJaVaScRiPt:alert(1)')).toBe(false);
	});

	it('rejects protocol-relative and scheme-less strings', () => {
		expect(isSafeLinkUrl('//evil.example/x')).toBe(false);
		expect(isSafeLinkUrl('not a url')).toBe(false);
	});

	it('rejects an empty or missing value', () => {
		expect(isSafeLinkUrl('')).toBe(false);
		expect(isSafeLinkUrl(undefined)).toBe(false);
		expect(isSafeLinkUrl(null)).toBe(false);
	});
});

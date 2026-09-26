import { describe, expect, it } from 'vitest';
import { isSafeLinkUrl } from './safe-url';

describe('isSafeLinkUrl', () => {
	it('accepts http URLs', () => {
		expect(isSafeLinkUrl('http://example.com')).toBe(true);
	});

	it('accepts https URLs', () => {
		expect(isSafeLinkUrl('https://example.com/path?q=1')).toBe(true);
	});

	it('accepts a same-origin absolute path', () => {
		expect(isSafeLinkUrl('/entries/foo')).toBe(true);
	});

	it('accepts a same-origin relative path', () => {
		expect(isSafeLinkUrl('entries/foo')).toBe(true);
	});

	it('accepts a bare fragment or query string', () => {
		expect(isSafeLinkUrl('#section')).toBe(true);
		expect(isSafeLinkUrl('?q=1')).toBe(true);
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

	it('rejects a protocol-relative URL (it changes the host, unlike a relative path)', () => {
		expect(isSafeLinkUrl('//evil.example/x')).toBe(false);
	});

	it('rejects an empty or missing value', () => {
		expect(isSafeLinkUrl('')).toBe(false);
		expect(isSafeLinkUrl(undefined)).toBe(false);
		expect(isSafeLinkUrl(null)).toBe(false);
	});
});

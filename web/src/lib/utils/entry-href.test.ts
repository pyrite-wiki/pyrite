import { expect, it } from 'vitest';
import { entryHref } from './entry-href';

it('encodes an entry ID and its KB independently', () => {
	expect(entryHref('space / id', 'KB & notes')).toBe('/entries/space%20%2F%20id?kb=KB%20%26%20notes');
});

it('omits absent and empty KB values', () => {
	expect(entryHref('plain')).toBe('/entries/plain');
	expect(entryHref('plain', '')).toBe('/entries/plain');
});

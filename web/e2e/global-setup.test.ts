/**
 * Unit tests for the seed's link-creation-order guard
 * (`validateLinkCreationOrder` in `./global-setup`).
 *
 * The guard is the only thing standing between a new `--link` and the
 * silent exit-3 trap: a link whose target is created after its source makes
 * the create a partial success and the link is dropped without a word. These
 * tests assert the MECHANISM — the guard firing on bad orders and staying
 * quiet on good ones — not one literal link list, so a future reorder or new
 * link exercises the guard, not a snapshot.
 */
import { describe, expect, it } from 'vitest';

import { validateLinkCreationOrder } from './global-setup';
import type { SeededEntry, SeededLink } from './fixtures';

function entry(id: string): SeededEntry {
	return { id, title: `E2E ${id}`, type: 'note' };
}

function link(source: string, target: string): SeededLink {
	return { source, target, relation: 'related_to' };
}

describe('validateLinkCreationOrder', () => {
	it('accepts the real seeded world', () => {
		// Defaults are the seed constants. If a future commit adds a link
		// whose target is created after its source, this fails before the
		// seed silently drops it.
		expect(() => validateLinkCreationOrder()).not.toThrow();
	});

	it('fires on a link whose target comes later in the same group', () => {
		const group = [entry('a'), entry('b')];
		expect(() => validateLinkCreationOrder([group], [link('a', 'b')])).toThrow(
			/is uncreatable/
		);
	});

	it('fires on a link whose target is in a later-created group', () => {
		const early = [entry('early')];
		const late = [entry('late')];
		// 'late' group is seeded after 'early', so early → late is uncreatable.
		expect(() => validateLinkCreationOrder([early, late], [link('early', 'late')])).toThrow(
			/is uncreatable/
		);
		// The reverse direction — target created first — is fine.
		expect(() => validateLinkCreationOrder([early, late], [link('late', 'early')])).not.toThrow();
	});

	it('fires on a self-link', () => {
		const group = [entry('a')];
		expect(() => validateLinkCreationOrder([group], [link('a', 'a')])).toThrow(/is uncreatable/);
	});

	it('fires on a link to an unknown entry id', () => {
		const group = [entry('a')];
		expect(() => validateLinkCreationOrder([group], [link('a', 'ghost')])).toThrow(
			/unknown entry id/
		);
		expect(() => validateLinkCreationOrder([group], [link('ghost', 'a')])).toThrow(
			/unknown entry id/
		);
	});
});

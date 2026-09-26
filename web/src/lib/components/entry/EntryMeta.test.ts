import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';
import EntryMeta from './EntryMeta.svelte';
import type { EntryResponse } from '$lib/api/types';

afterEach(() => cleanup());

function baseEntry(overrides: Partial<EntryResponse> = {}): EntryResponse {
	return {
		id: 'e1',
		kb_name: 'kb',
		entry_type: 'note',
		title: 'Entry',
		tags: [],
		participants: [],
		sources: [],
		outlinks: [],
		backlinks: [],
		file_path: 'e1.md',
		...overrides
	};
}

describe('EntryMeta source URLs', () => {
	it('renders an https source URL as a link', () => {
		const entry = baseEntry({ sources: [{ title: 'A Source', url: 'https://example.com/a' }] });
		const { container } = render(EntryMeta, { entry });
		const link = container.querySelector('a[href="https://example.com/a"]');
		expect(link).not.toBeNull();
	});

	it('renders an http source URL as a link', () => {
		const entry = baseEntry({ sources: [{ title: 'A Source', url: 'http://example.com/a' }] });
		const { container } = render(EntryMeta, { entry });
		const link = container.querySelector('a[href="http://example.com/a"]');
		expect(link).not.toBeNull();
	});

	it('does not render a javascript: source URL as a link', () => {
		const entry = baseEntry({
			sources: [{ title: 'A Source', url: 'javascript:alert(document.domain)' }]
		});
		const { container } = render(EntryMeta, { entry });
		expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
		// The text is still shown, just not as a clickable link.
		expect(container.textContent).toContain('javascript:alert(document.domain)');
	});

	it('does not render a data: source URL as a link', () => {
		const entry = baseEntry({
			sources: [{ title: 'A Source', url: 'data:text/html,<script>alert(1)</script>' }]
		});
		const { container } = render(EntryMeta, { entry });
		expect(container.querySelector('a[href^="data:"]')).toBeNull();
	});
});

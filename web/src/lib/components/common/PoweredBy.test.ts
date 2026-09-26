import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';

let mockFooterCreditUrl = 'https://pyrite.wiki';

vi.mock('$lib/stores/brand.svelte', () => ({
	brandStore: {
		get footer_credit_url() {
			return mockFooterCreditUrl;
		}
	}
}));

afterEach(() => {
	cleanup();
	mockFooterCreditUrl = 'https://pyrite.wiki';
});

describe('PoweredBy branding URL', () => {
	it('renders an https footer_credit_url as a link', async () => {
		const PoweredBy = (await import('./PoweredBy.svelte')).default;
		const { container } = render(PoweredBy);
		const link = container.querySelector('a[href="https://pyrite.wiki"]');
		expect(link).not.toBeNull();
	});

	it('does not render a javascript: footer_credit_url as a link', async () => {
		mockFooterCreditUrl = 'javascript:alert(document.domain)';
		const PoweredBy = (await import('./PoweredBy.svelte')).default;
		const { container } = render(PoweredBy);
		expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
	});
});

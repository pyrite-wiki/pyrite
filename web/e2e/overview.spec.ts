import { test, expect } from '@playwright/test';

import { E2E_KB, SEEDED_ENTRIES, SEEDED_DAILY_DATES } from './fixtures';
import { E2E_BACKEND_URL } from './global-setup';

const seededTotal = SEEDED_ENTRIES.length + SEEDED_DAILY_DATES.length;

// Scope the value to its label's parent; Knowledge Bases also has a heading.
function card(page: import('@playwright/test').Page, label: string) {
	return page.locator('div.text-sm.text-zinc-500').filter({ hasText: new RegExp(`^${label}$`) })
		.locator('xpath=..').locator('div.text-2xl');
}

test('overview displays the seeded data and is reachable from the landing page', async ({ page, request }) => {
	const response = await request.get(`${E2E_BACKEND_URL}/api/stats`);
	expect(response.ok()).toBe(true);
	const stats = await response.json();
	expect(stats.total_entries).toBe(seededTotal);

	await page.goto('/');
	const nav = page.getByRole('navigation', { name: 'Main navigation' });
	await nav.locator('a[href="/overview"]').click();
	await expect(page).toHaveURL(/\/overview$/);
	// Wait for the layout's keyed fade transition to remove the outgoing node.
	await expect(page.locator('h1.page-title').filter({ hasText: /^Overview$/ })).toHaveCount(1);
	await expect(page.locator('h1.page-title').filter({ hasText: /^Overview$/ })).toBeVisible();
	await expect(card(page, 'Knowledge Bases')).toHaveText('1');
	await expect(card(page, 'Total Entries')).toHaveText(String(seededTotal));
	await expect(card(page, 'Tags')).toHaveText(String(stats.total_tags));
	await expect(card(page, 'Links')).toHaveText(String(stats.total_links));

	const recent = await request.get(`${E2E_BACKEND_URL}/api/entries`, { params: { limit: 5, sort_by: 'updated_at' } });
	expect(recent.ok()).toBe(true);
	const entries = (await recent.json()).entries;
	expect(entries.length).toBeGreaterThan(0);
	await expect(page.getByRole('heading', { name: 'Recent Entries', exact: true })).toBeVisible();
	for (const entry of entries) {
		await expect(page.locator(`a[href="/entries/${entry.id}?kb=${E2E_KB}"]`)).toContainText(entry.title);
	}
	await expect(page.getByRole('heading', { name: 'Entry Types', exact: true })).toBeVisible();
	await expect(page.getByRole('img', { name: 'Entry type distribution donut chart' })).toBeVisible();
});

test('a broken stats backend cannot satisfy the seeded count assertion', async ({ page }) => {
	await page.route('**/api/stats', route => route.abort('connectionrefused'));
	await page.goto('/overview');
	// Wait for the layout's keyed fade transition to remove the outgoing node.
	await expect(page.locator('h1.page-title').filter({ hasText: /^Overview$/ })).toHaveCount(1);
	await expect(page.locator('h1.page-title').filter({ hasText: /^Overview$/ })).toBeVisible();
	await expect(card(page, 'Total Entries')).toHaveText('-');
	await expect(card(page, 'Total Entries')).not.toHaveText(String(seededTotal));
	await expect(page.getByRole('heading', { name: 'Entry Types', exact: true })).toHaveCount(0);
});

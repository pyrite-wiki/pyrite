import { test, expect } from '@playwright/test';

import { E2E_KB, SEEDED_EVENTS } from './fixtures';

test.describe('Timeline Page', () => {
	test('loads and shows timeline heading', async ({ page }) => {
		await page.goto('/timeline');
		await expect(page.getByRole('heading', { name: 'Timeline' })).toBeVisible();
	});

	test('shows filter controls', async ({ page }) => {
		await page.goto('/timeline');
		await expect(page.getByLabel('From', { exact: true })).toBeVisible();
		await expect(page.getByLabel('To', { exact: true })).toBeVisible();
		await expect(page.getByLabel('Min importance')).toBeVisible();
		await expect(page.getByRole('button', { name: 'Apply' })).toBeVisible();
	});

	test('shows the seeded event count', async ({ page }) => {
		await page.goto('/timeline');
		// global-setup.ts seeds exactly SEEDED_EVENTS.length timeline events.
		await expect(page.getByText(`${SEEDED_EVENTS.length} events`)).toBeVisible();
	});

	test('shows a link for every seeded event, by href and title', async ({ page }) => {
		await page.goto('/timeline');
		await expect(page.getByText(`${SEEDED_EVENTS.length} events`)).toBeVisible();

		for (const event of SEEDED_EVENTS) {
			const link = page.locator(`a[href="/entries/${event.id}?kb=${E2E_KB}"]`);
			await expect(link).toBeVisible();
			await expect(link).toContainText(event.title);
			await expect(link).toContainText(event.date);
		}
	});

	test('date filter narrows to the seeded events within range', async ({ page }) => {
		await page.goto('/timeline');
		await expect(page.getByText(`${SEEDED_EVENTS.length} events`)).toBeVisible();

		// SEEDED_EVENTS: first-light 2020-01-15, second-signal 2021-06-30,
		// third-pass 2022-11-02. A [2021-01-01, 2021-12-31] window keeps only
		// second-signal.
		await page.getByLabel('From', { exact: true }).fill('2021-01-01');
		await page.getByLabel('To', { exact: true }).fill('2021-12-31');
		await page.getByRole('button', { name: 'Apply' }).click();

		await expect(page.getByText('1 events')).toBeVisible();
		await expect(page.locator(`a[href="/entries/e2e-event-second-signal?kb=${E2E_KB}"]`)).toBeVisible();
		await expect(page.locator(`a[href="/entries/e2e-event-first-light?kb=${E2E_KB}"]`)).toHaveCount(0);
		await expect(page.locator(`a[href="/entries/e2e-event-third-pass?kb=${E2E_KB}"]`)).toHaveCount(0);
	});

	test('date filter with no matches shows the empty state', async ({ page }) => {
		await page.goto('/timeline');
		await expect(page.getByText(`${SEEDED_EVENTS.length} events`)).toBeVisible();

		// No seeded event falls in 1999.
		await page.getByLabel('From', { exact: true }).fill('1999-01-01');
		await page.getByLabel('To', { exact: true }).fill('1999-12-31');
		await page.getByRole('button', { name: 'Apply' }).click();

		await expect(page.getByText('No timeline events found')).toBeVisible();
	});

	test('date filter inputs accept values', async ({ page }) => {
		await page.goto('/timeline');
		const fromInput = page.getByLabel('From', { exact: true });
		await fromInput.fill('2025-01-01');
		await expect(fromInput).toHaveValue('2025-01-01');

		const toInput = page.getByLabel('To', { exact: true });
		await toInput.fill('2025-12-31');
		await expect(toInput).toHaveValue('2025-12-31');
	});

	test('importance filter accepts numeric value', async ({ page }) => {
		await page.goto('/timeline');
		const impInput = page.getByLabel('Min importance');
		await impInput.fill('5');
		await expect(impInput).toHaveValue('5');
	});

	test('importance filter above the seeded events importance shows the empty state', async ({ page }) => {
		await page.goto('/timeline');
		await expect(page.getByText(`${SEEDED_EVENTS.length} events`)).toBeVisible();

		// Every seeded event has importance 5 (the create default) — a min of 6
		// excludes all of them.
		await page.getByLabel('Min importance').fill('6');
		await page.getByRole('button', { name: 'Apply' }).click();

		await expect(page.getByText('No timeline events found')).toBeVisible();
	});
});

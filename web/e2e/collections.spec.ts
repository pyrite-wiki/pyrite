import { test, expect } from '@playwright/test';
import { SEEDED_COLLECTION, SEEDED_PEOPLE } from './fixtures';

test.describe('Collections Page', () => {
	test('loads and shows collections heading', async ({ page }) => {
		await page.goto('/collections');
		await expect(page.getByRole('heading', { name: 'Collections' })).toBeVisible();
		// document.title should read "Collections — Pyrite" (svelte:head on this
		// route), but the root layout's branding effect overwrites it with the
		// bare brand name on every route -- see issue #49. Assert the heading
		// instead of a title this app cannot currently produce.
	});

	test('lists exactly the seeded collection', async ({ page }) => {
		await page.goto('/collections');
		const collectionLink = page.getByRole('link', { name: SEEDED_COLLECTION.title });
		await expect(collectionLink).toBeVisible();
		await expect(collectionLink).toHaveAttribute('href', new RegExp(`^/collections/${SEEDED_COLLECTION.id}`));

		// The seeded world has exactly one collection. Exclude the "New Virtual
		// Collection" action link, whose href (/collections/new) also matches
		// the ^/collections/ prefix.
		const collectionCardLinks = page
			.locator('a[href^="/collections/"]')
			.filter({ hasNotText: 'New Virtual Collection' });
		await expect(collectionCardLinks).toHaveCount(1);
	});

	test('has create collection link', async ({ page }) => {
		await page.goto('/collections');
		await expect(page.getByRole('link', { name: 'New Virtual Collection' })).toBeVisible();
	});

	test('create link navigates to the new-collection page', async ({ page }) => {
		await page.goto('/collections');
		await page.getByRole('link', { name: 'New Virtual Collection' }).click();
		await expect(page).toHaveURL(/\/collections\/new/);
	});

	test('opening the seeded collection shows exactly the three seeded people', async ({ page }) => {
		await page.goto('/collections');
		await page.getByRole('link', { name: SEEDED_COLLECTION.title }).click();
		await expect(page).toHaveURL(new RegExp(`/collections/${SEEDED_COLLECTION.id}`));
		await expect(page.getByRole('heading', { name: SEEDED_COLLECTION.title })).toBeVisible();

		for (const person of SEEDED_PEOPLE) {
			// entryHref() appends `?kb=<name>` when the entry carries a kb; the
			// collection page only needs to link to the right entry, so match
			// the path prefix rather than the exact href.
			await expect(page.getByRole('link', { name: person.title })).toHaveAttribute(
				'href',
				new RegExp(`^/entries/${person.id}(\\?|$)`)
			);
		}

		// Exactly the three seeded people -- no more, no fewer.
		const entryLinks = page.locator('a[href^="/entries/"]');
		await expect(entryLinks).toHaveCount(SEEDED_PEOPLE.length);
	});
});

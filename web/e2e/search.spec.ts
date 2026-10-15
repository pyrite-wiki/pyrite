import { test, expect } from '@playwright/test';

import { E2E_KB, SEEDED_NOTES, SEEDED_PEOPLE } from './fixtures';

// The one seeded note the #9 assertion (below) keys on.
const ALPHA = SEEDED_NOTES.find((n) => n.id === 'e2e-note-alpha')!;

test.describe('Search Page', () => {
	test('has search input field', async ({ page }) => {
		await page.goto('/search');
		const input = page.getByPlaceholder('Search entries...');
		await expect(input).toBeVisible();
		await expect(page).toHaveTitle(/Search — Pyrite/);
	});

	test('has mode selector', async ({ page }) => {
		await page.goto('/search');
		await expect(page.getByRole('button', { name: 'keyword' })).toBeVisible();
		await expect(page.getByRole('button', { name: 'semantic' })).toBeVisible();
		await expect(page.getByRole('button', { name: 'hybrid' })).toBeVisible();
	});

	test('search input accepts text', async ({ page }) => {
		await page.goto('/search');
		const input = page.getByPlaceholder('Search entries...');
		await input.fill('test query');
		await expect(input).toHaveValue('test query');
	});

	test('KB filter is available', async ({ page }) => {
		await page.goto('/search');
		await expect(page.getByLabel('Filter by knowledge base')).toBeVisible();
	});

	// #9 criterion 2: a query for a seeded entry renders that entry's title as
	// a result link — not only the "N results" header, which is a mutually
	// exclusive guard with the skeleton (+page.svelte:257 and :360) and so
	// cannot distinguish a rendered list from a page stuck on the skeleton.
	test('a seeded query renders the seeded entry as a result link, skeleton gone', async ({
		page
	}) => {
		await page.goto('/search');
		const input = page.getByPlaceholder('Search entries...');
		await input.fill(ALPHA.title);
		await input.press('Enter');

		const link = page.locator(`a[href="/entries/${ALPHA.id}?kb=${E2E_KB}"]`);
		await expect(link).toBeVisible({ timeout: 10000 });
		await expect(link).toContainText(ALPHA.title);

		// The skeleton must be gone at the moment the result link is visible —
		// the two are mutually exclusive guards, never both true at once.
		await expect(page.getByTestId('search-skeleton')).toHaveCount(0);
	});

	test('zero-result query shows the empty state, no result links, skeleton gone', async ({
		page
	}) => {
		await page.goto('/search');
		const input = page.getByPlaceholder('Search entries...');
		// No seeded title contains this literal string.
		await input.fill('Zzyzx Nonexistent Query String Nowhere');
		await input.press('Enter');

		await expect(page.getByTestId('search-empty-state')).toBeVisible({ timeout: 10000 });
		await expect(page.locator('a[href^="/entries/"]')).toHaveCount(0);
		await expect(page.getByTestId('search-skeleton')).toHaveCount(0);
	});

	test('a query matching multiple seeded entries links every match, no .first() dodge', async ({
		page
	}) => {
		await page.goto('/search');
		const input = page.getByPlaceholder('Search entries...');
		// "E2E Person" matches all three seeded people.
		await input.fill('E2E Person');
		await input.press('Enter');

		await expect(page.getByTestId('search-skeleton')).toHaveCount(0, { timeout: 10000 });
		for (const person of SEEDED_PEOPLE) {
			const link = page.locator(`a[href="/entries/${person.id}?kb=${E2E_KB}"]`);
			await expect(link).toBeVisible();
			await expect(link).toContainText(person.title);
		}
	});

	test('KB filter set to the seeded KB still returns the seeded entry', async ({ page }) => {
		await page.goto('/search');
		const input = page.getByPlaceholder('Search entries...');
		await input.fill(ALPHA.title);
		await input.press('Enter');
		await expect(page.locator(`a[href="/entries/${ALPHA.id}?kb=${E2E_KB}"]`)).toBeVisible({
			timeout: 10000
		});

		// Select the seeded KB by value, not by its option text.
		await page.getByLabel('Filter by knowledge base').selectOption(E2E_KB);

		const link = page.locator(`a[href="/entries/${ALPHA.id}?kb=${E2E_KB}"]`);
		await expect(link).toBeVisible({ timeout: 10000 });
		await expect(link).toContainText(ALPHA.title);
	});

	test('mode selector toggles aria-pressed and keyword/hybrid still return the seeded entry', async ({
		page
	}) => {
		// Semantic mode is excluded from the "still returns the seeded entry"
		// assertion: this worktree's e2e world has auto_embed disabled
		// (.pyrite/config.yaml) and global-setup.ts never computes embeddings,
		// so SearchService._semantic_search (search_service.py:351,
		// has_embeddings() false) deterministically returns zero results —
		// reason "semantic_empty_no_embeddings" (search_service.py:247). That
		// is a real, intentional fallback path, not a product bug: asserting a
		// result here would assert against a world that cannot produce one.
		// Hybrid mode's own fallback (search_service.py:412-417,
		// "hybrid_no_embeddings") drops the empty semantic leg and returns
		// keyword-only results, so hybrid keeps finding the seeded entry.
		await page.goto('/search');
		const input = page.getByPlaceholder('Search entries...');
		await input.fill(ALPHA.title);
		await input.press('Enter');
		await expect(page.locator(`a[href="/entries/${ALPHA.id}?kb=${E2E_KB}"]`)).toBeVisible({
			timeout: 10000
		});

		const semanticBtn = page.getByRole('button', { name: 'semantic' });
		await semanticBtn.click();
		await expect(semanticBtn).toHaveAttribute('aria-pressed', 'true');
		// Deterministic empty state for this world, not a dodge: see comment above.
		await expect(page.getByTestId('search-empty-state')).toBeVisible({ timeout: 10000 });

		const hybridBtn = page.getByRole('button', { name: 'hybrid' });
		await hybridBtn.click();
		await expect(hybridBtn).toHaveAttribute('aria-pressed', 'true');
		await expect(semanticBtn).toHaveAttribute('aria-pressed', 'false');
		await expect(page.locator(`a[href="/entries/${ALPHA.id}?kb=${E2E_KB}"]`)).toBeVisible({
			timeout: 10000
		});

		const keywordBtn = page.getByRole('button', { name: 'keyword' });
		await keywordBtn.click();
		await expect(keywordBtn).toHaveAttribute('aria-pressed', 'true');
		await expect(hybridBtn).toHaveAttribute('aria-pressed', 'false');
		await expect(page.locator(`a[href="/entries/${ALPHA.id}?kb=${E2E_KB}"]`)).toBeVisible({
			timeout: 10000
		});
	});
});

import { test, expect, type APIRequestContext } from '@playwright/test';

import { E2E_KB, SEEDED_PEOPLE, SEEDED_EVENTS, SEEDED_NOTES, uniqueTitle, idForTitle } from './fixtures';
import { E2E_BACKEND_URL } from './global-setup';

/**
 * Entries list, entry creation, and the entry detail page's read-only chrome
 * (title, metadata, breadcrumbs). Panel-toggle and edit-mode behaviour on the
 * detail page belongs to entry-features.spec.ts.
 *
 * This spec CREATES entries (the New Entry flow). Every title it writes comes
 * from `uniqueTitle()` so a second run of the same seeded world, and any
 * sibling spec running in the same pass, never collides with it. Cleanup uses
 * the backend API even though the detail page now has a delete affordance
 * (#117): a spec's own teardown must not depend on UI behaviour under test
 * elsewhere, and the API delete needs no seeded session state.
 */

// Per-worktree, not a hardcoded 8088 — see E2E_BACKEND_URL's own doc (#118).
const API_BASE = E2E_BACKEND_URL;

async function deleteEntry(request: APIRequestContext, id: string): Promise<void> {
	await request.delete(`${API_BASE}/api/entries/${encodeURIComponent(id)}?kb=${E2E_KB}`);
}

// A cold Vite dev server compiles each route's bundle on its first request;
// under `fullyParallel` with several workers all landing on a not-yet-visited
// route at once, that first compile can outrun the default 5s expect timeout
// even though the app itself is fine (same shape other e2e files already
// accommodate -- see e.g. qa.spec.ts's 15000ms, search.spec.ts's 10000ms).
// This constant is used only for the FIRST assertion after a `goto` to a
// route this spec hasn't warmed up yet.
const COLD_ROUTE_TIMEOUT = 15000;

test.describe('Entries Page', () => {
	test('lists the seeded people, events, and notes by their known ids', async ({ page }) => {
		await page.goto('/entries');
		const [first, ...rest] = [...SEEDED_PEOPLE, ...SEEDED_EVENTS, ...SEEDED_NOTES];
		await expect(page.locator(`a[href="/entries/${first.id}?kb=${E2E_KB}"]`)).toBeVisible({
			timeout: COLD_ROUTE_TIMEOUT
		});
		for (const entry of rest) {
			await expect(page.locator(`a[href="/entries/${entry.id}?kb=${E2E_KB}"]`)).toBeVisible();
		}
	});

	test('has type filter dropdown defaulting to All types', async ({ page }) => {
		await page.goto('/entries');
		const typeFilter = page.locator('select').filter({ has: page.locator('option', { hasText: 'All types' }) });
		await expect(typeFilter).toBeVisible({ timeout: COLD_ROUTE_TIMEOUT });
		await expect(typeFilter).toHaveValue('');
	});

	test('has sort controls defaulting to Updated', async ({ page }) => {
		await page.goto('/entries');
		const sortSelect = page.locator('select').filter({ has: page.locator('option', { hasText: 'Updated' }) });
		await expect(sortSelect).toBeVisible({ timeout: COLD_ROUTE_TIMEOUT });
		await expect(sortSelect).toHaveValue('updated_at');
		await expect(page.getByRole('button', { name: 'Toggle sort direction' })).toBeVisible();
	});

	test('has search input', async ({ page }) => {
		await page.goto('/entries');
		const search = page.locator('input[type="search"]');
		await expect(search).toBeVisible({ timeout: COLD_ROUTE_TIMEOUT });
		await expect(search).toHaveAttribute('placeholder', 'Search...');
	});

	// #45 (github.com/pyrite-wiki/pyrite/issues/45): while `kbStore.activeKB` is
	// still resolving, EntryList briefly renders its own EmptyState "New Entry"
	// action link, colliding with the toolbar's permanent one. `toHaveCount(1)`
	// (an auto-retrying assertion) waits out that race instead of grabbing
	// whichever link Playwright saw first. Same race Package B documented
	// against app.spec.ts's "has new entry link" test.
	test('has new entry link to the creation page', async ({ page }) => {
		await page.goto('/entries');
		const newEntryLink = page.getByRole('link', { name: 'New Entry' });
		await expect(newEntryLink).toHaveCount(1, { timeout: COLD_ROUTE_TIMEOUT });
		await expect(newEntryLink).toHaveAttribute('href', '/entries/new');
	});
});

test.describe('New Entry Page', () => {
	// The root layout wraps route content in `{#key $page.url.pathname}` with
	// in/out fade transitions (web/src/routes/+layout.svelte); for a brief
	// window after a client-side navigation the outgoing and incoming pages
	// are both laid out and both report visible -- two real DOM nodes, not a
	// locator that should have been scoped tighter. `toHaveCount(1)` waits out
	// that transition. Same race Package B documented in app.spec.ts's
	// "navigates to entries/timeline page" tests.
	test('new entry link navigates to the creation form', async ({ page }) => {
		await page.goto('/entries');
		// #45 again: while `kbStore.activeKB` is still resolving, EntryList's
		// own EmptyState briefly renders a second "New Entry" link alongside
		// the toolbar's permanent one, which strict-mode-violates a `.click()`
		// taken immediately. Waiting for the count to settle to 1 first (the
		// same wait the "has new entry link" test above performs) rides out
		// that window before clicking.
		const newEntryLink = page.getByRole('link', { name: 'New Entry' });
		await expect(newEntryLink).toHaveCount(1, { timeout: COLD_ROUTE_TIMEOUT });
		await newEntryLink.click();
		await expect(page).toHaveURL(/\/entries\/new/);
		const heading = page.getByRole('heading', { name: 'New Entry', level: 1 });
		await expect(heading).toHaveCount(1);
		await expect(heading).toBeVisible();
	});

	test('creation form has a labelled title input', async ({ page }) => {
		await page.goto('/entries/new');
		const titleInput = page.getByLabel('Title');
		await expect(titleInput).toBeVisible();
		await expect(titleInput).toHaveAttribute('placeholder', 'Entry title...');
	});

	test('creation form shows the template picker with a blank-entry option', async ({ page }) => {
		await page.goto('/entries/new');
		await expect(page.getByRole('heading', { name: 'Choose a template' })).toBeVisible();
		await expect(page.getByTestId('template-picker')).toBeVisible();
		await expect(page.getByTestId('template-blank')).toBeVisible();
	});

	test('can fill in the entry title before picking a template', async ({ page }) => {
		const title = uniqueTitle('E2E Crud Fill');
		await page.goto('/entries/new');
		const titleInput = page.getByLabel('Title');
		await titleInput.fill(title);
		await expect(titleInput).toHaveValue(title);
	});

	test('breadcrumbs link back to Entries and show New Entry as current', async ({ page }) => {
		await page.goto('/entries/new');
		// Topbar's breadcrumb <nav> has no aria-label and its "Entries" link
		// shares an href with the sidebar's own "Entries" nav item, so a
		// role/href locator alone matches both navs. `header` scopes to the
		// Topbar specifically -- the sidebar is an <aside>, not a <header>.
		const breadcrumbs = page.locator('header').getByRole('navigation');
		await expect(breadcrumbs.getByRole('link', { name: 'Entries' })).toHaveAttribute('href', '/entries');
		await expect(breadcrumbs.getByText('New Entry', { exact: true })).toBeVisible();
	});

	test('picking Blank Entry and creating writes a real entry, reachable at its id', async ({
		page,
		request
	}) => {
		const title = uniqueTitle('E2E Crud Create');
		const id = idForTitle(title);
		try {
			await page.goto('/entries/new');
			await page.getByLabel('Title').fill(title);
			await page.getByTestId('template-blank').click();

			// Editor step: the title input here is the untitled one in the header row.
			await expect(page.getByRole('heading', { name: title })).toBeVisible();
			// The Create button is disabled until `kb` (kbStore.activeKB) has
			// resolved (web/src/routes/entries/new/+page.svelte) -- Playwright's
			// .click() auto-waits for that, so this click itself rides out the
			// KB-resolution race. What's left is the POST round-trip, which
			// under heavy shared-machine load (several sibling e2e suites'
			// worth of workers/servers) can outrun the default 5s timeout even
			// though nothing is actually wrong -- hence the longer timeout here,
			// same rationale as COLD_ROUTE_TIMEOUT above.
			await page.getByRole('button', { name: 'Create' }).click();

			await expect(page).toHaveURL(new RegExp(`/entries/${id}\\?kb=${E2E_KB}$`), { timeout: COLD_ROUTE_TIMEOUT });
			await expect(page.getByRole('heading', { name: title, level: 1 })).toBeVisible();
		} finally {
			await deleteEntry(request, id);
		}
	});
});

test.describe('Entry Detail Page', () => {
	// A seeded entry, not a created one -- these tests only read.
	const seeded = SEEDED_PEOPLE[0];

	test('shows the entry title and body', async ({ page }) => {
		await page.goto(`/entries/${seeded.id}`);
		await expect(page.getByRole('heading', { name: seeded.title, level: 1 })).toBeVisible();
		await expect(page.locator('.prose')).toBeVisible();
	});

	test('has an edit button that is present for the admin caller', async ({ page }) => {
		await page.goto(`/entries/${seeded.id}`);
		await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();
	});

	// EntryMeta is rendered twice in the DOM -- once in the mobile
	// `lg:hidden` <details>, once in the desktop `lg:block` <aside> -- both
	// CSS-toggled by breakpoint, both always present. A wide viewport plus the
	// `entry-meta-desktop` testid (added to the desktop <aside> only, since
	// EntryMeta itself has no stable role or href to scope by) picks the one
	// that's actually visible instead of hitting a strict-mode violation.
	test('metadata sidebar shows the entry type and KB', async ({ page }) => {
		await page.setViewportSize({ width: 1280, height: 720 });
		await page.goto(`/entries/${seeded.id}`);
		const meta = page.getByTestId('entry-meta-desktop');
		await expect(meta.getByText('Type:')).toBeVisible({ timeout: COLD_ROUTE_TIMEOUT });
		// This seeded person is tagged "person" (SEEDED_PEOPLE's tags include
		// "e2e,person" -- see global-setup.ts), so `getByText('person')` alone
		// also matches the Tags section's "person" badge link. EntryMeta.svelte
		// renders the type badge as the "Type:" label's next sibling <span>;
		// `xpath=following-sibling::span[1]` (not a `text=` locator -- it walks
		// from the getByText-located label node) picks that specific element.
		const typeBadge = meta.getByText('Type:', { exact: true }).locator('xpath=following-sibling::span[1]');
		await expect(typeBadge).toHaveText(seeded.type);
		await expect(meta.getByText('KB:')).toBeVisible();
		// Every seeded entry also carries an "e2e" tag (global-setup.ts), which
		// renders as its own badge link -- same collision as the type badge
		// above, same fix: scope to the "KB:" label's sibling span.
		const kbValue = meta.getByText('KB:', { exact: true }).locator('xpath=following-sibling::span[1]');
		await expect(kbValue).toHaveText(E2E_KB);
	});

	test('star button toggles star state for the entry', async ({ page }) => {
		await page.goto(`/entries/${seeded.id}`);
		const star = page.getByRole('button', { name: 'Star entry' });
		await expect(star).toBeVisible();
		await star.click();
		const unstar = page.getByRole('button', { name: 'Unstar entry' });
		await expect(unstar).toBeVisible();
		// Restore: this is seeded, shared material -- leave star state as found.
		await unstar.click();
		await expect(page.getByRole('button', { name: 'Star entry' })).toBeVisible();
	});

	test('has panel toggle buttons', async ({ page }) => {
		await page.setViewportSize({ width: 1280, height: 720 });
		await page.goto(`/entries/${seeded.id}`);
		await expect(page.getByRole('button', { name: 'Toggle outline panel' })).toBeAttached();
		await expect(page.getByRole('button', { name: 'Toggle backlinks panel' })).toBeAttached();
		await expect(page.getByRole('button', { name: 'Toggle version history' })).toBeAttached();
		await expect(page.getByRole('button', { name: 'Toggle local graph' })).toBeAttached();
	});

	test('has breadcrumb navigation back to Entries', async ({ page }) => {
		await page.goto(`/entries/${seeded.id}`);
		// See the New Entry Page breadcrumb test above for why this is scoped
		// to <header> rather than by role/href alone.
		const breadcrumbs = page.locator('header').getByRole('navigation');
		await expect(breadcrumbs.getByRole('link', { name: 'Entries' })).toHaveAttribute('href', '/entries');
	});

	// The two ways a reader destroys an entry without reading the prompt. Both
	// are timing, not intent, so both need a real browser: jsdom fires no
	// keydown-to-click default action, so the component test can only pin the
	// mechanism (focus lands on the inert step, not on Confirm).
	test('a double-click and an Enter-Enter on Delete cannot delete the entry', async ({
		page,
		request
	}) => {
		const title = uniqueTitle('E2E Crud Delete Guard');
		const id = idForTitle(title);
		try {
			await page.goto('/entries/new');
			await page.getByLabel('Title').fill(title);
			await page.getByTestId('template-blank').click();
			await page.getByRole('button', { name: 'Create' }).click();
			await expect(page).toHaveURL(new RegExp(`/entries/${id}\\?kb=${E2E_KB}$`), {
				timeout: COLD_ROUTE_TIMEOUT
			});

			const del = page.getByRole('button', { name: 'Delete' });

			// Double-click: the second click lands on a disabled Delete.
			await del.dblclick();
			await expect(page.getByRole('button', { name: 'Confirm delete' })).toBeVisible();

			// Enter-Enter: focus starts on Delete. The first Enter opens the
			// step and focus moves to it, so the second Enter presses nothing.
			await page.getByRole('button', { name: 'Cancel' }).click();
			await expect(del).toBeEnabled();
			await del.focus();
			await page.keyboard.press('Enter');
			await page.keyboard.press('Enter');

			// Still on the entry, still not deleted.
			await expect(page).toHaveURL(new RegExp(`/entries/${id}\\?kb=${E2E_KB}$`));
			await expect(page.getByRole('heading', { name: title, level: 1 })).toBeVisible();
			const stillThere = await request.get(
				`${API_BASE}/api/entries/${encodeURIComponent(id)}?kb=${E2E_KB}`
			);
			expect(stillThere.ok()).toBe(true);
		} finally {
			await deleteEntry(request, id);
		}
	});
});

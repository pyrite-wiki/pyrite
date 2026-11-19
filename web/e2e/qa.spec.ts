import { test, expect } from '@playwright/test';

import { SEEDED_DAILY_DATES, SEEDED_ENTRIES, SEEDED_LINKED_ENTRY_IDS } from './fixtures';

// status.total_entries is the seeded KB's non-daily entries plus the seeded
// daily notes (see fixtures.ts SEEDED_ENTRIES / SEEDED_DAILY_DATES).
const EXPECTED_TOTAL_ENTRIES = SEEDED_ENTRIES.length + SEEDED_DAILY_DATES.length;

test.describe('QA Dashboard Page', () => {
	test('loads and shows QA heading', async ({ page }) => {
		await page.goto('/qa');
		// Topbar rendered via the `title` prop (no breadcrumbs), which already
		// carries data-testid="page-title" — no page edit needed here.
		await expect(page.getByTestId('page-title')).toHaveText('QA Dashboard');
		await expect(page).toHaveTitle(/QA Dashboard — Pyrite/);
	});

	test('loading ends and shows the seeded entry count', async ({ page }) => {
		await page.goto('/qa');
		// Wait for the loading state to end by waiting for a stat card, never by
		// asserting on the spinner's words.
		const totalEntries = page.getByTestId('qa-stat-total-entries');
		await expect(totalEntries).toBeVisible({ timeout: 15000 });
		await expect(totalEntries).toContainText(String(EXPECTED_TOTAL_ENTRIES));
		await expect(page.getByTestId('qa-stat-total-issues')).toBeVisible();
	});

	test('KB filter is available', async ({ page }) => {
		await page.goto('/qa');
		await expect(page.getByLabel('Filter by severity')).toBeVisible();
	});

	// The "QA with an issue" regime: the seeded world links a few entries
	// (#162, SEEDED_LINKS in fixtures.ts), and qa_service.py's
	// `orphan_entry` rule fires for exactly every entry with no links in
	// either direction — deterministically, on every run, one `orphan_entry`
	// row per unlinked seeded entry (info severity).
	test('QA against the seeded world reports an orphan_entry issue for every unlinked seeded entry', async ({
		page
	}) => {
		await page.goto('/qa');
		const totalEntries = page.getByTestId('qa-stat-total-entries');
		await expect(totalEntries).toBeVisible({ timeout: 15000 });
		await expect(totalEntries).toContainText(String(EXPECTED_TOTAL_ENTRIES));

		const totalIssues = page.getByTestId('qa-stat-total-issues');
		const totalIssuesText = (await totalIssues.textContent()) ?? '';
		const issueCount = Number(totalIssuesText.match(/\d+/)?.[0]);
		expect(issueCount).toBeGreaterThan(0);

		await expect(page.getByRole('heading', { name: `Issues (${issueCount})` })).toBeVisible();
		// The clean state must NOT render when there are issues.
		await expect(page.getByTestId('qa-clean-state')).toHaveCount(0);
		// Body rows only — getByRole('row') also matches the header <tr>.
		await expect(page.locator('tbody').getByRole('row')).toHaveCount(issueCount);

		// Every unlinked seeded entry has exactly one orphan_entry row, and
		// every linked one has none — deterministic given the seed's link set
		// (SEEDED_LINKS in fixtures.ts, #162). Some entries also carry
		// rubric_violation rows for the same id, so the row must be narrowed
		// to the one whose Rule cell reads exactly "orphan_entry" (a
		// strict-mode violation otherwise: entries with rubric issues match
		// more than one row on id alone).
		for (const entry of SEEDED_ENTRIES) {
			const row = page
				.getByRole('row', { name: new RegExp(`\\b${entry.id}\\b`) })
				.filter({ has: page.getByRole('cell', { name: 'orphan_entry', exact: true }) });
			await expect(row).toHaveCount(SEEDED_LINKED_ENTRY_IDS.has(entry.id) ? 0 : 1);
		}
	});

	test('severity filter has the three seeded severities', async ({ page }) => {
		await page.goto('/qa');
		const severitySelect = page.getByLabel('Filter by severity');
		await expect(severitySelect).toBeVisible();
		await expect(severitySelect.locator('option:has-text("Error")')).toBeAttached();
		await expect(severitySelect.locator('option:has-text("Warning")')).toBeAttached();
		await expect(severitySelect.locator('option:has-text("Info")')).toBeAttached();
	});
});

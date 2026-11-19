import { test, expect } from '@playwright/test';

import { E2E_BACKEND_URL } from './global-setup';
import { E2E_KB, SEEDED_LINKED_ENTRY_IDS, SEEDED_LINKS } from './fixtures';

test.describe('Graph Page', () => {
	test('navigates to graph page and shows title', async ({ page }) => {
		await page.goto('/graph');
		// The Topbar's title has no heading role (it's a <span>, shared across
		// every route) — data-testid="page-title" added to
		// web/src/lib/components/layout/Topbar.svelte.
		await expect(page.getByTestId('page-title')).toHaveText('Knowledge Graph');
	});

	test('shows graph controls', async ({ page }) => {
		await page.goto('/graph');
		await expect(page.getByLabel('KB')).toBeVisible();
		await expect(page.getByLabel('Type')).toBeVisible();
		await expect(page.locator('input[type="range"]')).toBeVisible();
		await expect(page.getByLabel('Layout')).toBeVisible();
		await expect(page.getByRole('button', { name: 'Fit' })).toBeVisible();
		await expect(page.getByRole('button', { name: 'Reset Layout' })).toBeVisible();
	});

	test('graph API endpoint returns the seeded links as edges', async ({ request }) => {
		// Per-worktree, not a hardcoded 8088 — see E2E_BACKEND_URL's own doc (#118).
		const response = await request.get(`${E2E_BACKEND_URL}/api/graph`);
		expect(response.ok()).toBeTruthy();
		const data = await response.json();
		expect(data).toHaveProperty('nodes');
		expect(data).toHaveProperty('edges');
		expect(Array.isArray(data.nodes)).toBeTruthy();
		expect(Array.isArray(data.edges)).toBeTruthy();
		// The seeded world (global-setup.ts) links entries per SEEDED_LINKS
		// (#162), so the graph carries exactly those edges — asserted by
		// source/target id and relation, not by count alone.
		const edgeKeys = new Set(
			data.edges.map(
				(e: { source_id: string; target_id: string; relation: string }) =>
					`${e.source_id}→${e.target_id}:${e.relation}`
			)
		);
		expect(edgeKeys.size).toBe(SEEDED_LINKS.length);
		for (const link of SEEDED_LINKS) {
			expect(edgeKeys.has(`${link.source}→${link.target}:${link.relation}`)).toBeTruthy();
		}
	});

	test('shows the populated graph: the summary counts match the seeded links', async ({ page }) => {
		await page.goto('/graph');
		// The seeded world has links now (#162), so the empty state is gone and
		// the summary line counts the linked entries and the edges between them.
		await expect(
			page.getByText(`${SEEDED_LINKED_ENTRY_IDS.size} nodes, ${SEEDED_LINKS.length} edges`)
		).toBeVisible({ timeout: 15000 });
		await expect(page.getByText('No linked entries found.')).toBeHidden();
	});

	test('has search input', async ({ page }) => {
		await page.goto('/graph');
		await expect(page.locator('input[type="search"]')).toBeVisible();
	});

	test('search input accepts text', async ({ page }) => {
		await page.goto('/graph');
		const searchInput = page.locator('input[type="search"]');
		await searchInput.fill('test query');
		await expect(searchInput).toHaveValue('test query');
	});

	test('layout selector has the four seeded layout options', async ({ page }) => {
		await page.goto('/graph');
		const layoutSelect = page.getByLabel('Layout');
		await expect(layoutSelect).toBeVisible();
		await expect(layoutSelect.locator('option')).toHaveCount(4);
		await expect(layoutSelect.locator('option')).toHaveText([
			'Force-directed',
			'Circle',
			'Grid',
			'Concentric'
		]);
	});

	test('depth slider is interactive', async ({ page }) => {
		await page.goto('/graph');
		const slider = page.locator('input[type="range"]');
		await expect(slider).toBeVisible();
		await expect(slider).toHaveAttribute('min', '1');
		await expect(slider).toHaveAttribute('max', '3');
	});

	test('type filter dropdown has All plus the seeded world entry types', async ({ page }) => {
		await page.goto('/graph');
		const typeSelect = page.getByLabel('Type');
		await expect(typeSelect).toBeVisible();
		// Default value is "All" (empty option value).
		await expect(typeSelect).toHaveValue('');
		// The seeded world (global-setup.ts) creates exactly these five entry
		// types — collection, event, note, organization, person — confirmed
		// against GET /api/entries/types.
		await expect(typeSelect.locator('option')).toHaveText([
			'All',
			'collection',
			'event',
			'note',
			'organization',
			'person'
		]);
	});

	test('entry types API endpoint returns the seeded world types', async ({ request }) => {
		const response = await request.get(`${E2E_BACKEND_URL}/api/entries/types`);
		expect(response.ok()).toBeTruthy();
		const data = await response.json();
		expect(data).toHaveProperty('types');
		expect(data.types.sort()).toEqual(['collection', 'event', 'note', 'organization', 'person']);
	});
});

test.describe('Local Graph Panel', () => {
	// Navigate straight to a seeded event entry rather than through the
	// /entries list — the list view is out of this package's scope and its
	// default rendering doesn't guarantee a stable `a[href^="/entries/"]`
	// target. e2e-event-first-light is seeded with a --link to
	// e2e-person-ada-lovelace (#162).
	const SEEDED_ENTRY_PATH = '/entries/e2e-event-first-light';

	test('entry page has a local-graph toggle button', async ({ page }) => {
		await page.goto(SEEDED_ENTRY_PATH);
		await expect(page.getByRole('button', { name: 'Toggle local graph' })).toBeVisible();
	});

	test('local-graph toggle opens and closes the panel, which shows the seeded neighbour', async ({
		page
	}) => {
		await page.goto(SEEDED_ENTRY_PATH);
		const graphBtn = page.getByRole('button', { name: 'Toggle local graph' });
		await expect(graphBtn).toBeVisible();

		// Closed state: not the active/open styling.
		await expect(graphBtn).not.toHaveClass(/border-blue-500/);

		// The panel fetches /api/graph?center=<entry> on mount. Register the
		// response waiter BEFORE the click: registering after the click races
		// the panel's fetch and can wait out the 30 s timeout.
		const graphResponse = page.waitForResponse(
			(r) =>
				r.request().method() === 'GET' &&
				r.url().includes('/api/graph') &&
				r.url().includes('center=e2e-event-first-light')
		);
		await graphBtn.click();
		await graphResponse;
		await expect(graphBtn).toHaveClass(/border-blue-500/);
		await expect(page.getByRole('heading', { name: 'Local Graph' })).toBeVisible();
		// Load-bearing positive check first: the cytoscape container is visible.
		// The hidden check below is vacuous while the panel is still loading
		// ("Loading..." shows, and 'No linked entries' is hidden trivially), so
		// it only means something with the response received — which the
		// awaited graphResponse above guarantees. The entry has seeded links
		// (#162), so the panel holds more than the center node and the
		// cytoscape container is visible instead.
		await expect(page.locator('.graph-container')).toBeVisible();
		await expect(page.getByText('No linked entries')).toBeHidden();

		await graphBtn.click();
		await expect(graphBtn).not.toHaveClass(/border-blue-500/);
	});

	test('local-graph API returns the seeded neighbours of a linked entry', async ({ request }) => {
		// The panel behind the test above fetches /api/graph?center=<entry> —
		// the cytoscape canvas is not DOM-assertable, so the neighbour list is
		// asserted on the API shape the panel renders.
		const response = await request.get(`${E2E_BACKEND_URL}/api/graph`, {
			params: { center: 'e2e-event-first-light', center_kb: E2E_KB }
		});
		expect(response.ok()).toBeTruthy();
		const data = await response.json();
		const nodeIds = new Set(data.nodes.map((n: { id: string }) => n.id));
		// The seeded links (#162): first-light links to ada-lovelace, and
		// e2e-note-alpha links back to first-light.
		expect(nodeIds.has('e2e-person-ada-lovelace')).toBeTruthy();
		expect(nodeIds.has('e2e-note-alpha')).toBeTruthy();
	});
});

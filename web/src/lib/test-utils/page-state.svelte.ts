/**
 * A stand-in for SvelteKit's `page` from `$app/state` in unit tests.
 *
 * `$app/state`'s `page` is a reactive object (read `page.url`, not
 * `$page.url`), so a test double must be reactive too: a component that reads
 * `page.url` inside `$derived` or `$effect` has to re-run when a test moves the
 * route. A plain object would render once and never update. Mock with:
 *
 *   vi.mock('$app/state', async () => {
 *     const { createPageState } = await import('$lib/test-utils/page-state.svelte');
 *     return { page: createPageState({ url: new URL('http://localhost/entries') }) };
 *   });
 *
 * and move the route with `setPage(page, { url: ... })`.
 */
export function createPageState<T extends object>(initial: T): T {
	const state = $state(initial);
	return state;
}

/** Replace the named fields of a page double; reactive readers re-run. */
export function setPage(page: object, next: Record<string, unknown>): void {
	Object.assign(page, next);
}

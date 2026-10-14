/**
 * How many Playwright workers a run gets.
 *
 * Locally: Playwright's own default (`undefined`, half the cores) unless
 * `PW_WORKERS` names a positive number; `0`, a negative or a non-number is
 * ignored. A lower default (2) was tried for #168 and reverted:
 * `e2e/seed.spec.ts` times out at 1-2 workers and passes at 5 (#906). Bound
 * it again once #906 is fixed. What keeps several heavy runs off one machine
 * is the lock in scripts/e2e.sh, not this number. CI keeps 1.
 */
export function playwrightWorkers(env: Record<string, string | undefined>): number | undefined {
	if (env.CI) return 1;
	const raw = (env.PW_WORKERS ?? '').trim();
	const asked = /^\d+$/.test(raw) ? Number(raw) : NaN;
	return asked > 0 ? asked : undefined;
}

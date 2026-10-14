/**
 * How many Playwright workers a run gets (#168).
 *
 * Playwright's default (`undefined`) is half the cores: five browser workers
 * on the maintainer's 10-core laptop, beside uvicorn and vite, and on
 * 2026-09-18 that plus several pytest suites ran a 16 GB machine out of
 * memory. Locally the default is 2, and `PW_WORKERS` sets another positive
 * number; `0`, a negative or a non-number falls back to 2 rather than to
 * Playwright's own default. CI keeps 1: the suite is not yet deterministic
 * in parallel.
 */
export const DEFAULT_LOCAL_WORKERS = 2;

export function playwrightWorkers(env: Record<string, string | undefined>): number {
	if (env.CI) return 1;
	const raw = (env.PW_WORKERS ?? '').trim();
	const asked = /^\d+$/.test(raw) ? Number(raw) : NaN;
	return asked > 0 ? asked : DEFAULT_LOCAL_WORKERS;
}

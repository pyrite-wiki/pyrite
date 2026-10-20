import { defineConfig, devices } from '@playwright/test';

import {
	AUTH_BACKEND_PORT,
	AUTH_BASE_URL,
	AUTH_E2E_ENV,
	AUTH_WEB_PORT,
	seedAuthWorld
} from './e2e/auth-setup';
import { E2E_BACKEND_PORT, E2E_ENV, E2E_VITE_PORT, seedE2EWorld } from './e2e/global-setup';
import { playwrightWorkers } from './e2e/workers';

// Build the seeded world NOW, while this config module is evaluated — not from
// Playwright's `globalSetup` hook, which runs after `webServer` has already
// started and the backend has already read its config. See global-setup.ts.
seedE2EWorld();

// Package C's second world: the same timing, for the same reason, for a
// backend with `auth.enabled: true`. Its own data directory, port and dev
// server — see e2e/auth-setup.ts for why each of those is separate.
seedAuthWorld();

/**
 * The specs that belong to the auth-enabled world, named once so that the
 * `chromium` project excludes exactly what the `chromium-auth` project
 * includes. Without the exclusion, `auth.spec.ts` would also run against the
 * auth-disabled backend, where every one of its assertions is false.
 */
const AUTH_SPECS = ['**/auth.spec.ts', '**/auth.setup.ts'];

export default defineConfig({
	testDir: 'e2e',
	// Playwright's default testMatch (`.*(test|spec)\.[jt]s`) also matches
	// `*.test.ts` — the vitest unit tests for e2e/-local modules like
	// `ports.ts` (Package A.1, #118). Without this, Playwright tries to run
	// `ports.test.ts` itself, loading vitest's `expect` into the same worker
	// process as Playwright's own and crashing with "Cannot redefine
	// property: Symbol($$jest-matchers-object)". Every real spec in this
	// suite is already named `*.spec.ts`, so scoping to that costs nothing.
	testMatch: /.*\.spec\.ts/,
	fullyParallel: true,
	forbidOnly: !!process.env.CI,
	// A flake must be visible, not absorbed. Retries hid the fact that the suite
	// had no test-data contract; they only made the job three times slower.
	retries: 0,
	// PW_WORKERS locally (else Playwright's default), 1 in CI: see e2e/workers.ts.
	workers: playwrightWorkers(process.env),
	reporter: 'list',
	use: {
		baseURL: `http://localhost:${E2E_VITE_PORT}`,
		trace: 'on-first-retry'
	},
	projects: [
		{
			name: 'chromium',
			use: { ...devices['Desktop Chrome'] },
			// Everything except the auth-enabled world's specs. Added by Package
			// C; the project's `use` and name are untouched.
			testIgnore: AUTH_SPECS
		},
		// --- Package C: the auth-enabled world ---------------------------------
		{
			// Creates the one user, once, against the auth backend. A separate
			// project rather than a `globalSetup` because it must run AFTER
			// `webServer` (the user is created over HTTP), which globalSetup does
			// not — see e2e/auth.setup.ts.
			name: 'auth-setup',
			testMatch: /auth\.setup\.ts/,
			use: { ...devices['Desktop Chrome'], baseURL: AUTH_BASE_URL }
		},
		{
			name: 'chromium-auth',
			testMatch: /auth\.spec\.ts/,
			dependencies: ['auth-setup'],
			// Its own baseURL: the auth-enabled dev server, whose proxy points at
			// the auth-enabled backend — both derived per worktree (ports.ts),
			// distinct from the base pair. A spec in this project that said
			// `page.goto('/login')` against the base baseURL would be looking at
			// the auth-DISABLED world's login page.
			use: { ...devices['Desktop Chrome'], baseURL: AUTH_BASE_URL }
		}
	],
	webServer: [
		{
			// The FastAPI backend, pointed at the seeded world and nothing else.
			// The port is derived per worktree (see e2e/ports.ts) so two
			// worktrees running `playwright test` at once never collide on 8088.
			command: `cd .. && .venv/bin/uvicorn pyrite.server.api:app --host 127.0.0.1 --port ${E2E_BACKEND_PORT}`,
			url: `http://127.0.0.1:${E2E_BACKEND_PORT}/health`,
			env: E2E_ENV,
			// Never reuse: a backend already running on this port is somebody's
			// real Pyrite (or a sibling worktree's), with their real KBs, and
			// this suite writes. The preflight in global-setup.ts fails fast,
			// before this even starts, when the port is already held.
			reuseExistingServer: false,
			// A cold import of the server stack takes well over 15 s on a loaded
			// or first-run machine.
			timeout: 60000
		},
		{
			// The Vite dev server (proxies /api, /auth, /config, /health to the
			// backend). PLAYWRIGHT_E2E_PORT tells vite.config.ts's proxy which
			// backend port to target — the same derived port this file just
			// started uvicorn on. strictPort (set in vite.config.ts) turns a
			// taken port into a hard error instead of Vite's usual silent
			// fallthrough to the next free one.
			command: `npx vite dev --port ${E2E_VITE_PORT}`,
			url: `http://localhost:${E2E_VITE_PORT}`,
			env: { PLAYWRIGHT_E2E_PORT: String(E2E_BACKEND_PORT) },
			reuseExistingServer: false,
			timeout: 60000,
			stdout: 'pipe'
		},
		// --- Package C: the auth-enabled world's two servers --------------------
		{
			// The same FastAPI app, on its own port, pointed at its own data
			// directory, with `PYRITE_AUTH_ENABLED=true`.
			command: `cd .. && .venv/bin/uvicorn pyrite.server.api:app --host 127.0.0.1 --port ${AUTH_BACKEND_PORT}`,
			url: `http://127.0.0.1:${AUTH_BACKEND_PORT}/health`,
			env: AUTH_E2E_ENV,
			// Same rule as 8088, and the same reason: a backend already on this
			// port is somebody's real Pyrite.
			reuseExistingServer: false,
			timeout: 60000
		},
		{
			// The second Vite dev server, whose only difference from the first is
			// which backend it proxies to. See vite.e2e-auth.config.ts.
			command: `npx vite dev --config vite.e2e-auth.config.ts --port ${AUTH_WEB_PORT}`,
			url: AUTH_BASE_URL,
			reuseExistingServer: false,
			timeout: 60000,
			stdout: 'pipe'
		}
	]
});

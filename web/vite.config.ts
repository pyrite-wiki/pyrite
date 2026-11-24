import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vitest/config';

import { wsProxy } from './vite.ws-proxy';

declare const process: { env: Record<string, string | undefined> };

// The backend this dev server proxies to. Plain `npm run dev` keeps the
// long-standing default of 8088; Playwright's e2e webServer sets
// PLAYWRIGHT_E2E_PORT to this worktree's derived backend port (see
// e2e/ports.ts) so the proxy target and the backend it actually started
// agree, without hardcoding a port two worktrees running at once would
// collide on. See kb/backlog/playwright-package-a-1-per-worktree-ports-*.md.
const backendPort = process.env.PLAYWRIGHT_E2E_PORT ?? '8088';
const backendTarget = `http://127.0.0.1:${backendPort}`;

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
		preprocess: vitePreprocess(),
		adapter: adapter({
			pages: 'dist',
			assets: 'dist',
			fallback: 'index.html'
		}),
		alias: {
			$lib: 'src/lib'
		},
		csp: {
			// The app's own routes are NOT prerendered (root +layout.ts sets
			// `prerender = false`; this is a client-rendered SPA). But
			// `fallback: 'index.html'` above makes adapter-static generate
			// index.html as a prerendered fallback shell regardless -- it's
			// the one page served for every client-side route, and SvelteKit
			// always hashes that shell's own inline bootstrap script into a
			// <meta http-equiv> CSP tag rather than using a nonce (a nonce is
			// rejected by browsers on prerendered output, and this shell is
			// always prerendered even though the routes it boots aren't).
			// The server (pyrite/server/static.py) sets the authoritative
			// header CSP on top of this -- frame-ancestors and report-uri
			// are ignored in a <meta> tag, so they can only take effect there.
			mode: 'hash',
			directives: {
				'script-src': ['self']
			}
		}
	})
	],
	server: {
		// Fail loudly if the derived port is already taken rather than sliding
		// to the next one: a dev server on an unexpected port would silently
		// serve a sibling worktree's world under this one's baseURL.
		strictPort: true,
		// Proxy every top-level prefix the backend actually serves (see
		// pyrite/server/api.py's create_app) -- /auth, /branding, and
		// /config were missing here, so any dev-server or e2e request to
		// them 404'd against Vite itself instead of reaching the backend.
		// /ws (the live-update socket, #336) was missing too, so `npm run dev`
		// never received live updates at all (#421) -- see vite.ws-proxy.ts
		// for why its entry cannot just copy the others' changeOrigin: true.
		proxy: {
			'/api': {
				target: backendTarget,
				changeOrigin: true
			},
			'/health': {
				target: backendTarget,
				changeOrigin: true
			},
			'/auth': {
				target: backendTarget,
				changeOrigin: true
			},
			'/branding': {
				target: backendTarget,
				changeOrigin: true
			},
			'/config': {
				target: backendTarget,
				changeOrigin: true
			},
			...wsProxy(backendTarget)
		}
	},
	resolve: process.env.VITEST
		? { conditions: ['browser', 'svelte'] }
		: undefined,
	test: {
		environment: 'jsdom',
		include: ['src/**/*.test.ts', 'e2e/*.test.ts'],
		setupFiles: ['src/test-setup.ts']
	}
});

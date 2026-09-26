import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),
	kit: {
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
				'script-src': ["'self'"]
			}
		}
	}
};

export default config;

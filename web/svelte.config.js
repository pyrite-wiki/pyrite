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
			// adapter-static prerenders every page, so SvelteKit hashes the
			// inline bootstrap script it emits into a <meta http-equiv> CSP
			// tag on each page (nonces are forbidden for prerendered output).
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

/**
 * Build the deterministic world the e2e suite runs against.
 *
 * `playwright.config.ts` calls `seedE2EWorld()` at module scope, NOT through
 * Playwright's `globalSetup` hook. That is deliberate and load-bearing:
 * Playwright starts `webServer` in a plugin setup task, and plugin setup tasks
 * run BEFORE globalSetup tasks (`runner/tasks.js: createGlobalSetupTasks`).
 * The backend calls `load_config()` once at import, so a globalSetup that
 * wipes and reseeds the data directory would do so after the server had
 * already read an empty one — every entry lookup then 404s. Seeding while the
 * config module is being evaluated happens before any task exists, so the
 * server's first read of the world is a read of the finished world.
 *
 * Everything here is done through the Pyrite CLI against a private data
 * directory; the backend the config then launches points at that directory and
 * sees nothing else.
 *
 * Why this exists: the suite used to start `uvicorn pyrite.server.api:app` with
 * no `PYRITE_*` environment at all. Locally that resolved to the developer's
 * real `~/.pyrite` (dozens of KBs, live daily notes, other sessions writing);
 * on a CI runner it resolved to an empty home with zero KBs. Every spec asserts
 * on data, so the suite was deterministic on its input and its input was
 * undefined — which is what "fails differently every run" actually was.
 *
 * The world it builds is described, entry by entry, in `fixtures.ts`.
 * See kb/backlog/playwright-e2e-suite-non-deterministic-failures-*.md, Package A.
 */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
	E2E_KB,
	SEEDED_COLLECTION,
	SEEDED_DAILY_DATES,
	SEEDED_EVENTS,
	SEEDED_LINKS,
	SEEDED_NOTES,
	SEEDED_ORGANIZATIONS,
	SEEDED_PEOPLE,
	type SeededEntry,
	type SeededLink
} from './fixtures';
import { derivePorts } from './ports';

const here = dirname(fileURLToPath(import.meta.url));
/** Repo root: web/e2e -> web -> repo. */
export const REPO_ROOT = resolve(here, '..', '..');

/**
 * This worktree's four e2e ports, derived from REPO_ROOT (see ports.ts).
 * Computed once at module scope — every consumer (this file, auth-setup.ts,
 * playwright.config.ts) imports the same values, so the backend a webServer
 * entry starts and the port a spec's baseURL points at can never disagree.
 */
export const PORTS = derivePorts(REPO_ROOT, process.env);

export const E2E_BACKEND_PORT = PORTS.backend;
export const E2E_VITE_PORT = PORTS.vite;

/**
 * The base world's backend, reachable directly (not through Vite's proxy).
 *
 * A handful of specs call the API straight rather than through a page's own
 * fetches — e.g. to seed or clean up a fixture over HTTP, or to assert on a
 * health/API-shape endpoint without a browser. Before Package A.1 (#118)
 * those specs each hardcoded `http://localhost:8088` themselves; now they
 * import this instead, so the literal exists in exactly one place and a
 * derived, per-worktree port reaches every caller.
 */
export const E2E_BACKEND_URL = `http://127.0.0.1:${E2E_BACKEND_PORT}`;

/**
 * The private data directory every e2e process uses instead of ~/.pyrite.
 *
 * Suffixed with this worktree's derived backend port (not a fixed name) so
 * two worktrees running the suite at once never share — or race to wipe —
 * the same directory on disk, the same failure mode the ports themselves
 * exist to prevent. `.gitignore` matches the whole `web/.e2e-data-*` family.
 */
export const E2E_DATA_DIR = join(REPO_ROOT, 'web', `.e2e-data-${PORTS.dataDirSuffix}`);
export const E2E_KB_PATH = join(E2E_DATA_DIR, 'kbs', E2E_KB);

const PYRITE_BIN = join(REPO_ROOT, '.venv', 'bin', 'pyrite');

/**
 * Fail fast, naming the port and the owning process, when something outside
 * this worktree already holds `port`. Without this, Playwright's own
 * `webServer` retry/health-check loop is the only signal, and its error
 * ("Timed out waiting ... for the server to start") does not say WHY — a
 * human has to already know to run `lsof` themselves. `lsof -sTCP:LISTEN -i
 * :<port>` is exactly what acceptance criterion #2 asks the error to name;
 * the `-sTCP:LISTEN` filter matters — plain `-i :<port>` also matches an
 * ESTABLISHED client socket that happens to be using that port as its local
 * (ephemeral) port, which is not a listener and not a conflict.
 *
 * A backend this worktree itself started in a previous, still-running
 * `playwright test` invocation is not "outside this worktree" in spirit —
 * but `reuseExistingServer: false` (set unconditionally, see
 * playwright.config.ts) means this project never intends to share a server
 * across invocations either, so any listener on the port is treated as
 * foreign and the preflight fails regardless of whose process it is. In
 * practice the most common trip is a lingering uvicorn from THIS worktree's
 * own previous run that a crashed or Ctrl-C'd `playwright test` failed to
 * clean up — not necessarily another worktree, and not necessarily user
 * error; the message below says that.
 *
 * Best-effort: if `lsof` itself is missing or errors for a reason other than
 * "no listener found" (both look identical here — a non-zero exit with no
 * stdout), this fails OPEN rather than blocking the run. Playwright's own
 * `webServer` health check remains the backstop signal in that case, just
 * without this function's more specific error.
 */
export function preflightPort(port: number, label: string): void {
	let output: string;
	try {
		output = execFileSync('lsof', ['-sTCP:LISTEN', '-i', `:${port}`], {
			encoding: 'utf8',
			stdio: ['ignore', 'pipe', 'ignore']
		});
	} catch {
		// Non-zero exit with no stdout here means either "nothing is listening"
		// (the success case) or "lsof itself is unavailable/errored" (best-effort
		// fail-open — see the doc comment above). Both look the same from here.
		return;
	}
	if (!output.trim()) {
		return;
	}
	throw new Error(
		`e2e preflight: port ${port} (${label}) already has a listener — ` +
			`this worktree's e2e suite refuses to reuse a server it did not start.\n\n` +
			`${output}\n` +
			`This is often a lingering uvicorn/vite from THIS worktree's own previous ` +
			`run that didn't get cleaned up (a crash, or Ctrl-C at the wrong moment) — ` +
			`kill the process above if so. It can also be a sibling worktree's run ` +
			`still in flight; ports are derived per worktree (see web/e2e/ports.ts), ` +
			`so that would mean two worktrees happen to hash to the same port, or one ` +
			`is using a PLAYWRIGHT_E2E_PORT override that collides with another's.`
	);
}

/**
 * The environment every e2e backend process gets. `playwright.config.ts` passes
 * this to the `webServer` command, and the seed below runs under it too, so the
 * CLI that writes the world and the server that reads it agree on where it is.
 *
 * - `PYRITE_DATA_DIR` overrides `~/.pyrite` for config, index and workspace.
 * - `PYRITE_CONFIG_DIR` is the older name for the same thing; set both so the
 *   value does not depend on which one `pyrite.config` happens to check first.
 * - `PYRITE_AUTH_ENABLED=false` makes the auth contract explicit rather than
 *   whatever `load_config()` resolves to. With auth off every caller is
 *   `admin` — see `AUTH_ENABLED` in fixtures.ts for what that means for specs.
 * - `PYRITE_AUTO_EMBED=0` and `HF_HUB_OFFLINE=1` keep the backend from
 *   downloading or loading a sentence-transformers model on first write, which
 *   is both slow and a network dependency a test suite must not have.
 * - `RATELIMIT_ENABLED=false` (slowapi's own switch) turns off the read limiter
 *   for the test backend. Five Playwright workers share one client IP and blow
 *   through `rate_limit_read: 100/minute` partway through a suite, so a page
 *   renders "API Error 429" instead of its content — and WHICH page loses the
 *   race varies run to run. Measured against the seeded backend: 150 reads give
 *   100x200 + 50x429 with the limiter on, and 150x200 with it off. The limiter
 *   is a production behaviour with its own backend tests; it is not what these
 *   specs are asserting.
 * - `PYRITE_CORS_ORIGINS` names this world's Vite dev server. The dev server
 *   proxies with `changeOrigin`, so the backend sees its own Host but the
 *   browser's Origin, `http://localhost:<derived vite port>`; an auth-disabled
 *   backend refuses a state-changing request from an origin that is neither
 *   its host nor configured (pyrite/server/request_guard.py).
 */
export const E2E_ENV: Record<string, string> = {
	PYRITE_DATA_DIR: E2E_DATA_DIR,
	PYRITE_CONFIG_DIR: E2E_DATA_DIR,
	PYRITE_AUTH_ENABLED: 'false',
	PYRITE_AUTO_EMBED: '0',
	PYRITE_SEARCH_MODE: 'keyword',
	HF_HUB_OFFLINE: '1',
	TRANSFORMERS_OFFLINE: '1',
	RATELIMIT_ENABLED: 'false',
	PYRITE_CORS_ORIGINS: `http://localhost:${E2E_VITE_PORT}`
};

function pyrite(args: string[]): string {
	return execFileSync(PYRITE_BIN, args, {
		cwd: REPO_ROOT,
		env: { ...process.env, ...E2E_ENV },
		encoding: 'utf8',
		stdio: ['ignore', 'pipe', 'pipe']
	});
}

function create(args: string[]): void {
	pyrite(['create', '-k', E2E_KB, ...args]);
}

/**
 * `--link` args for one seeded entry, from `SEEDED_LINKS` (#162).
 *
 * The entry id derives from the title (`pyrite.schema.validators.generate_entry_id`),
 * so the seed loops look up links by the same id fixtures use. The trap the
 * groom for #162 names: a `--link` whose target does not exist yet makes the
 * create a partial success (exit 3) with the link dropped — so the seed
 * creates every link target before its source (events after people, notes
 * after events) and lets execFileSync throw on any non-zero exit instead of
 * ignoring it.
 */
function linksFor(entryId: string): string[] {
	return SEEDED_LINKS.filter((l) => l.source === entryId).flatMap((l) => [
		'--link',
		`${l.target}:${l.relation}`
	]);
}

/**
 * Creation order of the seed loops in `seedE2EWorld()` below: people first,
 * then events, notes, organizations. A `--link` whose target's group is
 * created after the source's group — or whose target comes later inside the
 * same group — names an entry that does not exist yet, so the create is a
 * partial success (exit 3) and the link is silently dropped. Validate up
 * front so a future link that breaks the order fails here with a loud error
 * instead of seeding a world with silently missing links.
 *
 * `groups`/`links` default to the seed constants but are injectable so the
 * guard itself is unit-testable (see `global-setup.test.ts`).
 */
export function validateLinkCreationOrder(
	groups: SeededEntry[][] = [SEEDED_PEOPLE, SEEDED_EVENTS, SEEDED_NOTES, SEEDED_ORGANIZATIONS],
	links: SeededLink[] = SEEDED_LINKS
): void {
	const idToPosition = new Map<string, number>();
	for (const group of groups) {
		for (const entry of group) {
			idToPosition.set(entry.id, idToPosition.size);
		}
	}
	for (const link of links) {
		const sourcePos = idToPosition.get(link.source);
		const targetPos = idToPosition.get(link.target);
		if (sourcePos === undefined || targetPos === undefined) {
			throw new Error(
				`seed: SEEDED_LINKS references an unknown entry id ` +
					`(source=${link.source}, target=${link.target})`
			);
		}
		if (targetPos >= sourcePos) {
			throw new Error(
				`seed: link ${link.source} → ${link.target} is uncreatable — the target ` +
					`is created after its source, so the --link target would not exist yet ` +
					`(partial create, exit 3, link dropped). Reorder the seed loops or the link.`
			);
		}
	}
}

/**
 * Marker so the seed runs exactly once per `playwright test` invocation.
 *
 * `playwright.config.ts` is evaluated in the main runner process AND again in
 * every worker process, so an unguarded call wipes and rebuilds the data
 * directory N+1 times concurrently — workers racing each other into
 * `rmSync`/`pyrite create` and failing with "Command failed". Observed exactly
 * that: 118 of 119 tests erroring at 0 ms in the seed call.
 *
 * Workers are forked with `{...process.env}` from the main process
 * (`runner/processHost.js`), so a variable set here on the first evaluation is
 * inherited by every worker and seen as already-set.
 */
const SEED_MARKER = 'PYRITE_E2E_SEEDED';

/** Wipe and rebuild the seeded world. Runs once per test invocation. */
export function seedE2EWorld(): void {
	// A worker process never seeds: the main process already did, and the world
	// it built is the one this worker's backend is serving.
	if (process.env[SEED_MARKER] === '1' || process.env.TEST_WORKER_INDEX !== undefined) {
		return;
	}
	process.env[SEED_MARKER] = '1';

	if (!existsSync(PYRITE_BIN)) {
		throw new Error(
			`Pyrite CLI not found at ${PYRITE_BIN}. Create the venv first ` +
				`(pip install -e ".[all,dev]") — the e2e suite seeds its world through the CLI.`
		);
	}

	// Fail fast, before starting anything, if this worktree's derived ports
	// are already held by a foreign process (acceptance criterion #2).
	preflightPort(E2E_BACKEND_PORT, 'base e2e backend');
	preflightPort(E2E_VITE_PORT, 'base e2e Vite dev server');

	// Fail fast on an uncreatable link (target created after its source)
	// before the seed touches the data dir — see validateLinkCreationOrder.
	validateLinkCreationOrder();

	// A fresh world every run. Without this, a spec that wrote in run N would
	// still be in the world in run N+1, which is the shared-state failure mode
	// this package exists to remove.
	rmSync(E2E_DATA_DIR, { recursive: true, force: true });
	mkdirSync(E2E_DATA_DIR, { recursive: true });

	pyrite(['init', '-t', 'research', '-p', E2E_KB_PATH, '-n', E2E_KB, '--no-examples']);

	for (const p of SEEDED_PEOPLE) {
		create([
			'-t', 'person',
			'--title', p.title,
			'-b', `Seeded person: ${p.title}.`,
			'--tags', 'e2e,person',
			...linksFor(p.id)
		]);
	}
	for (const e of SEEDED_EVENTS) {
		create([
			'-t', 'event',
			'--title', e.title,
			'-d', e.date,
			'-b', `Seeded event on ${e.date}.`,
			'--tags', 'e2e,event',
			...linksFor(e.id)
		]);
	}
	for (const n of SEEDED_NOTES) {
		create([
			'-t', 'note',
			'--title', n.title,
			'-b', `Seeded note: ${n.title}.`,
			'--tags', 'e2e,note',
			...linksFor(n.id)
		]);
	}
	for (const o of SEEDED_ORGANIZATIONS) {
		create([
			'-t', 'organization',
			'--title', o.title,
			'-b', `Seeded organization.`,
			'--tags', 'e2e,org',
			...linksFor(o.id)
		]);
	}

	// `collection` is not one of the `research` template's declared types, so it
	// needs --allow-undeclared; the collections API reads it by `type:` alone.
	create([
		'-t', 'collection',
		'--title', SEEDED_COLLECTION.title,
		'-b', 'Seeded query collection over the seeded people.',
		'-f', 'source_type=query',
		'-f', `query=${SEEDED_COLLECTION.query}`,
		'--tags', 'e2e',
		'--allow-undeclared'
	]);

	// Daily notes. The entry id a daily note must have is `daily-YYYY-MM-DD`, and
	// `pyrite create` derives the id from the title, so the title is chosen to
	// slug to exactly that. The displayed heading comes from the selected date in
	// the UI, not from this title.
	//
	// These are not decoration: with auth disabled every caller is admin, so
	// `GET /daily/{date}` creates a note when none exists. Seeding every date the
	// specs can reach makes each of those GETs a read, and a read-only suite run
	// leaves the world byte-identical.
	for (const date of SEEDED_DAILY_DATES) {
		create([
			'-t', 'note',
			'--title', `daily ${date}`,
			'-b', `# Seeded daily note ${date}\n\nSeeded by the e2e global setup.`,
			'--tags', 'daily,e2e'
		]);
	}

	pyrite(['index', 'sync']);
}

export default seedE2EWorld;

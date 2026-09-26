# Changelog

All notable changes to Pyrite will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Unreleased changes are **not** listed below. Each one is a separate file under
[`changelog.d/`](changelog.d/README.md), named `<slug>.<section>.md`, so that no
two pull requests conflict on this file; `scripts/release.py` assembles them
under the version heading when the release is cut. `tests/test_changelog_fragments.py`
asserts that `[Unreleased]` stays empty.

## [Unreleased]

## [0.25.5] - 2026-09-26

Security release, from the 0.26 multi-user security review. **Upgrade if you run Pyrite with auth enabled, serve `/site`, or connect MCP clients over HTTP.** An MCP session now acts only for the credential that opened it, and ends when that credential is revoked, logged out or expired. Cookie-authenticated writes must come from an allowed origin. Content from knowledge bases is sanitised in the web app, which now sends a content security policy. Changing a knowledge base's access takes effect everywhere at once. Reads of links, lookups, queries, the graph and QA stay within the knowledge bases the caller can read. Config saves no longer write environment-supplied secrets to disk. This release also carries ADR-0037's single authorization policy point and error contract, entry-identity fixes, crash-safe config saves, and five contributor fixes.

**Upgrade notes:**
- **Dependencies:** `fastapi>=0.132` and `mcp>=1.27.2,<2`. With an older MCP library, `/mcp` is not mounted and the server logs the required version.
- **REST errors** from the central handler now use the `{"detail": {"code", "message", ...}}` shape that other endpoints already used. Read `body["detail"]["code"]`.
- **Cookie-authenticated writes need an allowed `Origin`.** Behind a reverse proxy that rewrites `Host`, add the public origin to `cors_origins` (`PYRITE_CORS_ORIGINS`).
- **Access changes on hand-written config KBs:** changing the default access of a KB defined in a hand-written `config.yaml` now returns 409, naming the file. Edit the file instead. KBs the server manages (repo-subscribed and ephemeral) remain editable.
- **Extension authors:** link and lookup reads (`get_backlinks`, `get_outlinks` and related calls) now require `readable_kbs=`. Pass the caller's readable set, or `UNSCOPED` (from `pyrite.services.access_policy`) only when there is no caller.

## [0.25.4] - 2026-09-25

Security release. **Upgrade if you run Pyrite with auth enabled, or if you open Pyrite in directories you did not create** (cloned or downloaded trees). With auth enabled, the instance now stays closed to strangers until its operator decides: sign-up waits for an admin created with `pyrite-admin user create`, self-registered users read only the KBs you open to them, and login and registration are rate-limited. A `.pyrite/config.yaml` found in a working tree is no longer trusted with anything beyond that tree's own KBs and index. See the operator actions below. This release also carries the write-path fixes the investigation workflow depends on, entry version history for server writes, search and site-cache robustness, and three contributor fixes.

### Changed

- **One write pipeline: every create surface refuses the same entry with the same code.** REST, MCP and the CLI now share one create/update pipeline in `KBService` (#378). A refused write reports a stable `error_code` everywhere — `UNDECLARED_TYPE`, `ENTRY_EXISTS`, `SCHEMA_VIOLATION` or `VALIDATION_FAILED` — instead of a per-surface `CREATE_FAILED`/`UPDATE_FAILED`; REST answers a duplicate id with `409`. See docs/json-contracts.md, "Write refusals".
- **MCP `kb_create` no longer exempts core types from the undeclared-type refusal**, matching the CLI (#197): in a KB whose `kb.yaml` declares types, `entry_type: note` is refused with `UNDECLARED_TYPE` unless the type is declared or `allow_undeclared: true` is passed. REST `POST /api/entries` and `/api/entries/import`, `pyrite add` and `pyrite import` apply the same refusal, with an `allow_undeclared` / `--allow-undeclared` override.
- **REST create and update return warnings.** `POST`, `PUT` and `PATCH /api/entries` responses carry a `warnings` list with the schema's non-blocking findings, as MCP already did.
- **`pyrite import` exits 1 when any record is refused**, prints each refusal with its code, and `--dry-run` now reports the records the pipeline would refuse. The JSON importer carries every field of a record through instead of a fixed whitelist, so the fields of core and plugin types (`role`, `kind`, `status`, …) are imported and validated instead of dropped. Together with #386, the fields of a type declared only in `kb.yaml` are kept and validated too.
- **MCP `kb_update` takes its field set from the entry's type** (its model and its `kb.yaml` fields) instead of a fixed core list; a `kb.yaml`-declared field of a custom type is now written instead of silently ignored, and `id`, path and timestamps are never rewritten.
- **Updates refuse fields Pyrite maintains.** REST `PUT`/`PATCH /api/entries/{id}` and `pyrite update --field` refuse `id`, `file_path`, `kb_name`, `links`, `sources` and `provenance` with `VALIDATION_FAILED` (setting `id` used to leave a second file), and so do a task's audit fields (`status_change_log`, `evidence`, `agent_context`, `assigned_at`) and an ADR's `adr_number`; MCP `kb_update` ignores them. Entry types, including plugin types, declare such fields with `managed_fields`.
- **The web app keeps its type behaviour.** The web client sends `allow_undeclared: true` on create, clip and import, so the New-entry form, which offers every core and plugin type, still works in a KB whose `kb.yaml` declares types. `POST /api/clip` now takes `allow_undeclared` like the other create endpoints and reports the pipeline's codes (`ENTRY_EXISTS` is a 409).

- **One plugin validator and hook contract, checked once at registration.** `PluginRegistry` now checks every plugin's `get_validators()` and `get_hooks()` callables against the single supported signature — `(entry_type: str, fields: dict, ctx: dict) -> list[dict]` for validators, `(entry, ctx)` for hooks — the first time that plugin's validators/hooks are needed, and caches the result for the registry's lifetime; a non-conforming callable is refused (logged once, dropped) instead of discovering the mismatch as a `TypeError` swallowed at call time. **A 2-argument `(entry_type, fields)` validator no longer runs at all** — the legacy fallback that used to call it is gone; every validator must accept `ctx` as a third positional argument. A validator that binds the contract but still returns the old `list[str]` shape (instead of `list[dict]`) has its non-dict items dropped with a warning rather than crashing a caller. A `before_*` hook (`before_save`/`before_delete`) that gets dropped as non-conforming now refuses the write for that KB (fail-closed, matching what a raising hook already did) rather than silently proceeding without it; a dropped `after_*` hook only logs a warning, since the operation has already succeeded. `HookRunner` now owns both the core-hook and plugin-hook phases under one raise-before/swallow-after contract.
- **The `cascade` and `journalism-investigation` extensions' validators were on the old, non-conforming signature: their validation was silently disabled** since the 3-argument contract was introduced, so a `cascade-research` or `journalism-investigation`/`known-entities` KB now refuses writes it previously accepted silently — an `actor` with no title, an `asset` with no `asset_type`, an out-of-range `importance`, and similar rules each extension already declared but never actually ran. **Existing entries already in a KB that violate one of these newly-active rules are not touched retroactively — they are refused the next time they are edited**, not flagged automatically. Run `pyrite qa validate -k <kb>` (or `pyrite index health`, which reports the same drift for `status` fields) to find entries that would now fail validation before they're next edited. See `kb/standards/extension-development.md`, "Validator signature".
- **`investigation promote-claim` (CLI) and `investigation_promote_claim` (MCP) accept the promoted edge's endpoint fields.** Promoting a claim to `ownership`/`funding`/`membership` never set the edge type's own required relationship fields (`owner`/`asset`, `funder`/`recipient`, `person`/`organization`), so every promotion was refused once the journalism-investigation validator started actually running (#424). `--owner`/`--asset`/`--funder`/`--recipient`/`--person`/`--organization` CLI options and an `endpoint_fields` MCP argument supply them; a promotion missing the required pair is refused with a clear message (not a raw `SchemaViolationError`), and `--dry-run`/`dry_run` now runs the same endpoint check. The MCP argument accepts only those field names, as strings. A dry run does not yet run the KB's full validation, so a real run can still refuse what it showed (#427).

- **MCP: a locked or busy database is `retryable: true` (#431).** A search that fails because another connection holds the index returns `retryable: true`, so an agent may retry it. Every other storage fault (schema drift, a missing table, a corrupt file) stays `retryable: false`. The `error_code` is unchanged (`REQUEST_REFUSED`), and MCP now logs each storage fault with its traceback. `SearchService.search` takes an optional `semantic_query`, the text the semantic leg embeds when it should differ from the keyword query.
- **Lowercase `and`/`or`/`not` no longer turn off quoting, so write operators in uppercase (#431).** Before, a lowercase operator word made the whole query pass through unquoted. Now the other tokens are quoted as usual, so in `title:foo and bar` or `foo* or bar` the column filter and the prefix match become literal text. Write `title:foo AND bar` or `foo* OR bar` to keep them as FTS5 syntax.

- **`scripts/test-affected --run` and the pre-push hook no longer re-run tests that already passed on the same code.** A pass on a clean tree records a stamp (the tree's SHA, the Python version and the tests run) under the git common directory, shared by every worktree; a later run -- the pre-push hook's, which looks up the tree being pushed -- that the stamp covers prints `already passed on tree <sha> (...); skipping` and exits 0. A dirty tree, a failure and a partial run are never stamped. `--force` or `PYRITE_PUSH_FORCE=1` runs anyway. After a load-caused failure, `scripts/test-affected --run -- --lf` re-runs only the failures. CI's push to `dev` likewise skips the test matrix and the frontend job when the merge queue already passed that exact commit; the smoke layer still runs.

- `verify-red` no longer touches your checkout: it runs a pull request's new and edited tests in a throwaway worktree of the merge base, first with only the PR's test files and then with the whole change, uncommitted work included. It reports one line (`verify-red: 3 red · 0 import-only · 0 unexpected pass · 1 n/a`) and a row only for each test that is not red. `@pytest.mark.control` marks a deliberate negative control. CI also reports advisory diff coverage (80% of changed lines) and keeps both in a `test-evidence` artifact (#368).

### Removed

- **`PluginRegistry.run_hooks` and `PluginRegistry.run_hooks_for_kb` are gone.** Both ran plugin hooks directly; that responsibility moved to `HookRunner`, which now owns the raise-before/swallow-after contract for both core and plugin hooks in one place. `PluginRegistry.get_hooks_for_kb` remains, but it is a pure lookup — it returns the hook-point-to-callables mapping and does not run anything. A caller that ran hooks directly through the registry should construct a `HookRunner(plugin_registry=...)` and call `run_before_save`/`run_after_save`/`run_before_delete`/`run_after_delete` instead.

### Fixed

- **A second relation between the same two entries is now recorded instead of silently dropped (#396).** `pyrite link a b -r implements` followed by `pyrite link a b -r supersedes` used to report `Linked:` for the second call too but write nothing — the duplicate check ignored `relation`. The duplicate key is now `(target, kb, relation)`, normalised so a link written before this fix (or by hand, with no `relation:` key at all) still matches the CLI/MCP default relation rather than looking like a second, distinct relation: an identical link stays a no-op and now prints `Already linked:` rather than `Linked:`; a different relation is appended. The CLI confirmation shows the relation again (it was silently swallowed as Rich markup), including on the `--bidi` line, and now names the stored relation rather than the caller's own — those can differ in spelling on legacy data while still matching under the normalisation above. A legacy link with no `relation:` key is reported as `related`, the value it loads as, though the file itself has no relation line. `add_link` returns `created: bool` and `relation: str` (the stored value), and MCP `kb_link`'s result gains a `created` key (additive); MCP still echoes the caller's relation rather than the stored one (tracked with MCP parity in #455).

- **`pyrite task create` now accepts `--field key=value` (#397).** A KB whose `task` type declares required fields — the desk schema's `project`/`kind` — could not be used with `task create` at all: validation refused the create, and there was no option to supply the fields. `--field` (repeatable, no short flag — `-f` is already `--format` on the task commands) fills them, and is refused for any key that already has its own option, for `status`, for a task's managed audit-trail fields, for the service-level fields `update` already refuses on every entry type (`id`, `links`, `file_path`, `sources`, `provenance`, `extra_frontmatter`), for a key that collides with the create pipeline's own parameters (`kb_name`, `entry_id`, `entry_type`, `allow_undeclared`), and for `type`/`created_at`/`updated_at` — a freshly created task can never actually carry them, so setting them used to report success while writing nothing.

- **CLI `pyrite update -f key=value` and REST `PATCH /api/entries/{id}` no longer silently drop an undeclared key (#407).** A key that was neither a model attribute nor named in the KB's schema — `foo` on a note, `parked_awaiting`/`park_until` on a task — used to be dropped without warning: the command reported `{"updated": true}` (REST: 200) and exited 0, but wrote nothing. Both now store the key wherever `create` would have (metadata, or a top-level frontmatter key), through the same schema validation. `update -f type=...`, `-f entry_type=...` and `-f =value` (an empty key) are refused rather than silently landing under `metadata:` with no effect — `entry_type` previously raised a raw `AttributeError` (a property with no setter). A refusal on one of these keys no longer masks a missing KB, a missing entry or a read-only KB: those still answer their own 404/403 first. MCP `kb_update` is unchanged: it still filters through `updatable_fields` and drops an undeclared key, since #380 is already moving that surface's field handling — MCP parity is tracked in #455.

- Admin user management works again: `GET /auth/users`, `PUT /auth/users/{id}/role` and `GET /auth/users/{id}/permissions` now see the caller's session or API key, so a global admin gets 200 where every caller used to get 403. A non-admin caller still gets 403, and a request with no credential now gets 401. With auth disabled and no API keys configured, these routes answer as admin, the same as every `/api` admin route (#330).
- The last global admin can no longer be demoted, whether by themselves or through `PUT /auth/users/{id}/role`; the request answers `409 LAST_ADMIN` and the role is unchanged. Promote another user to admin first (#330).

- **Bulk create refuses invalid items instead of writing them.** `kb_bulk_create`, `pyrite import` and task decomposition now refuse, per item, an entry the KB schema or a plugin validator rejects (#366), with the message and `error_code` `kb_create` gives. Siblings are still created, in order. Callers that sent off-schema values will now see per-item errors.
- **`pyrite links bulk-create` edits an entry where it lives.** It used to re-derive the source entry's path from its type and write a second copy of any entry kept elsewhere (#375).

- **CLI commands now include knowledge bases added with `pyrite kb add` (#363).** Search and named schema commands resolve database-registered KBs when they are absent from config.yaml. Named lookups use config.yaml first; if index lookup fails, the command warns and continues with the YAML config.

- **Behavior note:** bare `kb validate` also checks `kb add` registrations, so a registration whose directory was deleted now makes it fail. For a registered KB whose directory is missing, `kb schema add-type` and `search -k` can still proceed; `pyrite create` already works this way.

- `pyrite index stats` and `pyrite index health` close the index database; an open handle made Windows temporary-directory cleanup fail (#449).

- **`PYRITE_CONFIG_DIR` now moves the index, and a config save never drops a knowledge base it did not name** (#377).
  - With no `settings.index_path`, the index (`index.db`) and cloned repos (`repos/`) default to the directory holding `config.yaml`. So `PYRITE_CONFIG_DIR=/tmp/x pyrite kb add ...` no longer registers into `~/.pyrite/index.db`. A default `~/.pyrite` install sees no change. `PYRITE_DATA_DIR`, then `settings.index_path`, still take precedence (full order in `docs/configuration.md`).
  - **Check this if you use `PYRITE_CONFIG_DIR` or a repo-local `.pyrite/config.yaml`.** `workspace_path` is neither read from nor written to `config.yaml`, and `index_path` is written only once Pyrite itself saves the file. So a hand-written config there with no `index_path` now uses `<that dir>/index.db` and `<that dir>/repos/` instead of `~/.pyrite/`. Run `pyrite index sync` to rebuild the index in its new place, or add `index_path: ~/.pyrite/index.db` under `settings:` to keep the old one. Subscribed clones in `~/.pyrite/repos` can be moved to `<that dir>/repos/`. (`PYRITE_DATA_DIR=~/.pyrite` would keep both, but it also makes `~/.pyrite` the config directory.)
  - `save_config` now refuses (`ConfigSaveRefusedError`) any write that would remove a KB listed in `config.yaml` unless the caller named it in `removed=[...]`, or passes `allow_drop=True`. It also refuses to overwrite a `config.yaml` it cannot read (`ConfigFileUnreadableError`: unparseable, not a mapping, or an entry with no name). Commands that remove KBs (`kb remove`, `repo remove`, ephemeral expiry, unsubscribe) name what they remove, and they check before deleting anything.
  - On refusal, the CLI prints one error line and exits 1. The REST API returns a generic 409 (`CONFIG_SAVE_REFUSED`) and logs the file and KB names server-side. The advice follows the reason: when the file changed since the process loaded it, restart the server or re-run the command; when the file cannot be read, fix or move it (a restart would fail to load it). The write is still in place, not atomic; see the known limits in `docs/configuration.md`. A write through a symlinked `config.yaml` logs the real path.
  - The test suite now sets `PYRITE_CONFIG_DIR` (and clears `PYRITE_DATA_DIR`) for the whole session. `pyrite` subprocesses spawned by tests can no longer read or write the developer's `~/.pyrite`, including the `ephemeral-*` directories the suite used to leave in `~/.pyrite/repos/ephemeral`.

- Cross-KB investigation search groups results by shared entry ID first, then by normalized title, transitively (#61).

- **Entries wait for the selected knowledge base's list before showing an empty state (#45).** Initial loads and KB switches show a loading state until the list request settles; a genuinely empty list still shows its empty state.

- A server write (REST commit, `KBService.publish`, the CLI's `kb commit`) now records a version for every entry the commit touched, readable immediately through the version list and read endpoints — previously only `index build --with-attribution` and repo sync ever wrote a version row, so most KBs' version history and reads were empty. A renamed entry's pre-rename versions are readable too: each recorded version stores the KB-relative path the entry had *at that commit*. An entry's history holds only its own commits: it starts at the commit that added the entry's file, so a path reused by a different entry does not inherit the earlier entry's commits; a rename counts only when git pairs the files at its default similarity threshold **and** the entry id is the same on both sides; a version whose file states a different `id:` is not served; a stored path must stay inside the KB; and history from outside the KB's directory is never recorded. (#432).
- Known limits of entry version history (#432):
  - A rename bundled with an edit large enough to fall below git's default similarity threshold starts the entry's history at the rename.
  - An entry without an explicit `id:` derives its id from its title, so a rename that also changes the title starts its history at the rename.
  - An entry that is deleted and then restored (a revert, or a checkout of the old file) starts a new history at the restore; its earlier versions are no longer listed (#456).
  - History is walked in commit-date order, not ancestry. When a side branch edits an old entry after its path was reused by a new entry, merging that branch can place the old entry's edit before the new entry's add; that edit can then be recorded as the new entry's version and as its `created_by`/`modified_by`. Skewed or imported commit dates can do the same.

- `PUT /auth/users/{id}/role` now answers `409 LAST_ADMIN` only for the specific refusal to demote the last global admin; any other validation failure from the same call now correctly answers `422 VALIDATION_ERROR` instead of being mislabeled `409 LAST_ADMIN` (#416).

- **`promote-claim`'s dry run refuses what the real run refuses.** A dry run used to check only the edge type's own endpoint fields, so it could show a proposal the real run then rejected -- an existing entry id, an endpoint field outside the KB's schema, or a read-only KB. Both branches now build and submit the same spec, so a dry run and a real run agree on every refusal (#427).

- A schema refusal on a `required` or `enum` field now keeps the validator's own explanation instead of showing only `field: required` or the allowed values -- a conditional rule like journalism's "amount is required for payment transactions" is visible in the error again (#429).
- The journalism-investigation extension's claim-to-edge promotion now treats a whitespace-only endpoint field (e.g. `"   "`) as missing, the same as an empty or absent one.

- **Lowercase `and`, `or` and `not` in a search are plain words (#431).** FTS5 only treats uppercase `AND`/`OR`/`NOT` as operators, but a lowercase one used to switch off auto-quoting, so `how do pre-push hooks and CI interact?` failed with `QUERY_SYNTAX` on REST, MCP and the CLI. It now searches. Uppercase operators behave as before.
- **AI chat retrieval no longer fails silently on ordinary messages (#431).** Chat builds its search from the message's words, quoted and OR-joined like link suggestions, so a message such as `thanks :)`, `1) … 2) …` or one containing `and` always searches, where before it found nothing. Link suggestions keep two-letter words such as `AI` and `UX` and drop common lowercase stop words. In hybrid mode, chat and link suggestions embed the message or title itself, not the OR-joined query.
- **Every search failure keeps the `{code, message}` error contract (#431).** A failure in the semantic leg, or a corrupt index file (`sqlite3.DatabaseError`), used to escape as a plain-text `500`. It is now `500 STORAGE_ERROR` on REST, the structured error on MCP, and logged with its traceback. REST's 5xx log lines now include the traceback (they had none). A quoted, dotted column filter the caller wrote (`x AND "a.b":y`) is `400 QUERY_SYNTAX` instead of a `500`.

- A search that link suggestions or AI chat retrieval derive from a title or a message no longer runs a blank search when there is nothing to search. A query syntax error now answers `400 QUERY_SYNTAX` on REST `GET /api/search` and `POST /api/ai/suggest-links`, matching MCP and the CLI, instead of a raw `500` (#414).
- A genuine search-backend failure (a locked database, a disk I/O error, a missing table, a database file that can't be opened) stays a logged `5xx` instead of being relabeled `400 QUERY_SYNTAX` (#414).
- Link suggestions build their search query from the entry title's own words, quoted and OR-joined, so an ordinary title with `AND`, quotes, a hyphen or a colon (e.g. `The "Big Lie" and e-mail`) can no longer fail to parse (#414).
- A plain multi-word search that finds nothing retries OR-combined, and now quotes each term first: a query like `pre-push selection` or `section 230(c) reform` no longer fails with a syntax error blamed on the user during that retry (#361).

- The per-user session cap (`auth.max_sessions_per_user`) now holds when several logins for one user run at once, whether password, OAuth or both: creating a session and evicting the oldest beyond the cap are one write transaction. Evicted sessions are still announced after the commit, so their live-update sockets close. (#435)

- The site cache render status now means what it says: `rendered` is true when the render ran to completion, and `errors` (always present) counts per-entry page failures separately -- a caller checks `rendered and errors == 0` for "all pages are fresh." `errors` was previously discarded on `POST /api/index/sync?wait=true`, not reported at all; `rendered` itself does not go false just because some entry pages failed (#408).
- `POST /api/site/render` now answers `409 BRANDING_INVALID` for an unreadable or malformed `branding.yaml`, instead of a 500.
- `GET /sitemap.xml` and `/robots.txt` no longer fail at all on a broken `branding.yaml` -- a crawler reading a 5xx there takes it as "don't crawl," so these now degrade to default branding (relative URLs, no `site_url`) instead of erroring. `GET /config/branding` still fails with a named `500 BRANDING_INVALID` (the settings UI already has a fallback for it), with a generic message -- no branding file path or YAML parser text in any of these responses. The MCP `research_topic` prompt gets the same generic, structured refusal instead of a raised exception.
- `branding.yaml` validation is now complete: a list or other non-mapping document, a non-mapping `meta:`/`mcp:` block, and a non-string scalar field (`name`, `site_url`, `tagline`, etc.) are all refused as `BRANDING_INVALID` instead of reaching a route as a bare 500 or a wrong-shaped value (e.g. `site_url: [x]` used to crash the sitemap; `name: [x]` used to serialize out of `/config/branding` as a list). **Behaviour change:** a non-string scalar that YAML happily parses and that used to *load without error* is now refused too -- `name: 2024` or `tagline: 2024-01-01` (an unquoted number or date) previously loaded as an `int`/`date` object with no complaint, silently violating every consumer's assumption that these fields are strings; such a file must now be fixed (quote the value) to load at all.
- `BrandingService.get()` itself now logs a broken `branding.yaml` once per file per modification (a module-level, mtime-and-size-keyed cache), instead of a full traceback on every call -- this covers `GET /sitemap.xml` and `/robots.txt`, which build a fresh `BrandingService` per request. `GET /config/branding` still logs on every request despite that cache: the REST central handler in `pyrite/server/api.py` logs every `PyriteError` it converts to a 5xx response (`logger.error(..., exc_info=exc)`), independent of anything `BrandingService` caches, and that handler runs once per request. Separately, that handler no longer logs the same 5xx exception a second time via its own `public_message` line (unconditional before; now only for a refusal below 500). The cached failure's logged traceback also no longer grows across repeated calls to the same broken file -- a cache hit raises a fresh exception carrying the same message instead of re-raising the same object.

Ignore stale web search responses so older requests cannot overwrite newer results.

- The web client's live-update socket now follows the signed-in user. Logging in or switching user closes the old socket and opens exactly one new one. Logging out closes it and opens none when the server admits no anonymous readers (an anonymous socket otherwise); the sidebar's "Log out" now clears the client's session state. The tab that logged out no longer receives, or toasts, events from the previous user's KBs. Other tabs of the same browser keep their sockets until the server closes them (#411). A handshake that fails without ever opening is retried twice with the backoff, then shown as "Real-time updates unavailable". It is tried once more each time the network comes back or the tab becomes visible, and again when the user changes. A refusal after this user's socket has connected once is indistinguishable from a dropped connection and keeps retrying with the backoff, capped at 30 s. A dismissed connection banner stays dismissed while the client retries, until the connection opens or the user changes. (#336)

### Security

- Turning on auth no longer opens the instance to strangers. With auth enabled, web registration and GitHub sign-up are refused until an admin exists, and nobody becomes admin by signing up first: create the first admin with the new `pyrite-admin user create <name> --role admin`. A self-registered user can read, and never write, KBs whose `default_role` is `read` or `write`, plus KBs an admin grants them; users created with the CLI, an invite code or a GitHub org rule keep their global role on every KB, existing users keep the access they had (a migration marks them), and changing a user's role never widens this -- an admin grants it explicitly, for now only through `PUT /auth/users/{id}/role` (`global_access`), and `GET /auth/users` shows it. Users created by the `deploy/*/create-user.py` scripts get public-KB access only. Migration v26's rollback is a no-op: rolling back and migrating up again grants global access to every user present then. GitHub sign-up obeys `allow_registration` and `require_invite_code` unless the provider's `allowed_orgs` or `org_tier_map` vets the account. A login for an unknown username takes as long as a wrong password. An invite code creates at most one account, however many registrations present it at once. `/auth/login` is rate-limited per client and per username (failed attempts), and `/auth/register` per client; over the limit is 429 with `Retry-After` (`settings.auth.login_rate_limit`, `login_rate_limit_per_username`, `register_rate_limit`, or the matching `PYRITE_AUTH_*` variables). The limits are kept in memory per server process and keyed on the connection's peer address: behind a reverse proxy all clients share one budget, an IPv6 client is keyed per address, and anyone who knows a username can lock its password login for about a minute. `settings.auth.require_invite_code` is now read from `config.yaml`. `pyrite serve` warns when registration is open.

  **Operator action:** on a server with no admin yet, run `pyrite-admin user create <name> --role admin` before anyone can sign up. To share a KB with every signed-in user, set its `default_role` to `read` (this also publishes it on `/site`), or grant it per user.

- A live-update socket (`/ws`) now lives no longer than the credential that opened it: logging out, a session expiring or being evicted, a role change, or a KB grant or revoke closes that user's open sockets, and the client reconnects under its new scope. This covers per-user credential changes made in the server process; KB-wide access changes and changes made from another process are not yet covered and are tracked separately. Proposed as ADR-0036. (#411)

- A `.pyrite/config.yaml` found by searching up from the working directory is no longer trusted: it may come with a cloned or downloaded tree. Pyrite reads only its KB registry (KBs inside that tree, from the file or the tree's own index), `index_path` inside the tree, and a few switches (`auto_embed`, `search_mode`, `summary_length`); a KB's `default_role` is kept only when it is `none`, a `default_role` stored in the tree's own index is ignored unless it is `none` (publishing a KB needs a trusted config), `pyrite kb add` refuses a path outside the tree before creating anything, and saving such a config writes back only those keys -- dropping any others from the file -- never credentials or environment-sourced values; every other key -- the embedding model, editor, AI, server, auth and API-key settings, repositories and subscriptions -- is ignored with a warning, and GitHub credentials are never read from or written to that directory. `~/.pyrite` and an explicit `PYRITE_CONFIG_DIR` or `PYRITE_DATA_DIR` are trusted as before, and the worktree configs `scripts/new-worktree.sh` writes are unaffected. The embedding model loader never enables model-supplied code, refuses a model directory that names code outside sentence-transformers, and refuses a bare model name that is also a directory under the working directory; a local model is named by an absolute path in your own config. The `sentence-transformers` floor is now 2.3.

  **Operator action:** if you relied on a repo-local config for settings beyond the KB registry and index location, point `PYRITE_CONFIG_DIR` at that directory.

- `GET /api/entries/{id}/versions/{commit_hash}` now accepts only a hex git object id (4 to 64 characters, abbreviated or full) that names a commit, and refuses anything else with `400 INVALID_REF`: the format is checked before git runs, and an id that names a tree or blob is refused rather than read. Previously the value reached `git show` unchecked, so a read-tier caller could influence how git ran. `git show` now also ends option parsing before the object name. Symbolic refs such as `HEAD` or a branch name are no longer accepted on this route. No operator action is needed (#333).

- `GET /api/entries/{id}/versions/{commit_hash}` now serves content only when the hash is one of the entry's recorded versions; any other commit id in the KB's repo answers `404`, even if the path happens to exist in that commit's tree. The underlying `git rev-parse` and `git show` calls also now run with the leak-isolated git environment used elsewhere, and a KB that is a subdirectory of its git repo can now have its recorded versions read (previously always `404`).

  **Operator action:** version rows are only written by `pyrite index build --with-attribution` (or a repo the repo service syncs), never by an ordinary `pyrite index sync` or `index_all`. On a KB indexed without attribution, no commit was ever recorded, so this endpoint now answers `404` for every hash where it previously served content from any commit that happened to have the path. Run `pyrite index build --with-attribution` (or re-run it after new commits) to populate `entry_version` rows and restore reads. The web UI is unaffected: it only calls the version-list endpoint, which was already empty for these KBs (#415).

## [0.25.3] - 2026-09-25

Security patch. **Upgrade if your server is reachable by anyone other than you**, and especially if anonymous visitors can read (`anonymous_tier: read`/`write`, or a KB with `default_role: read`). Search queries now have a maximum length of 1,000 characters. A longer query is refused with `QUERY_TOO_LONG` on REST (`422`), MCP and the CLI; it is never truncated. Searches that Pyrite builds from stored content (AI chat retrieval, link suggestions, query expansion) are trimmed to fit instead. It also includes three bug fixes: bulk create no longer overwrites an existing entry, a type declared only in `kb.yaml` keeps its fields on create, and `POST /api/index/sync?wait=true` re-renders the site cache.

### Fixed

- **Bulk create and `pyrite import` no longer overwrite entries with duplicate IDs (#359).** Existing entries and earlier entries in the same batch now fail individually with an “already exists” error; their files remain unchanged while valid siblings are imported.

- **A kb.yaml-only type keeps every field it was given on create.** `build_entry` used to construct a type with no Python model class (declared only in `kb.yaml`) from just `tags`, `summary` and `metadata`, silently dropping every other field -- `pyrite create -f severity=high`, MCP `kb_create`, and bulk/import all lost the value (#386). A field given this way is now honoured the same as `created_at`, `importance`, `links`, `sources` and `id` already were for other entry types: it lands as a top-level frontmatter key (or, for a genuinely unrecognized key, is folded into `metadata` and promoted back to the top level on write) -- not left nested under a `metadata:` block. One consequence for callers who were silently succeeding: a select/enum field on such a type is now visible to schema validation, so a KB with `validation.enforce: true` refuses an off-enum value it previously wrote anyway. That enforcement is only as strong as the surface that calls it: `KBService.create_entry` (CLI `create -f`, MCP `kb_create`) validates before writing, but bulk create and `pyrite import` still skip validation entirely (tracked separately, #366/#381/#395) -- an off-enum value that used to be silently dropped on those two surfaces is now silently written instead, until that gap closes.

- **`POST /api/index/sync?wait=true` now actually re-renders the site cache, and the caller waits for it.** The handler is a plain (sync) route with no running event loop, so the old code's `asyncio.get_running_loop()` always raised, and a broad `except Exception` silently swallowed it; a second bug hid behind it — the config it checked (`request.app.state.config`) was never set anywhere, so the render was skipped even when the loop lookup was patched around. Since `wait=true` already means the caller is blocked on the request, a sync that adds, updates or removes entries now includes a full site render as part of that wait — a real cost, paid once, instead of a silent no-op. The render's own outcome no longer affects the sync's own success (a render failure does not turn a committed sync into an error, and the embed-queue drain and `kb_synced` broadcast still happen either way): `SyncResponse` gains a `site_cache: {rendered: bool, error: string|null}` field so callers can tell the two apart. It is `null` when the sync changed nothing (no render was attempted), and `rendered: true` means the render ran: an entry page that fails on its own is logged and skipped, as `POST /api/site/render` already does.

### Security

- **Search queries have a maximum length.** A query over 1,000 characters is refused with an explicit error on every surface (422 `QUERY_TOO_LONG` on REST, a `QUERY_TOO_LONG` validation error on MCP, a clear CLI error); it is never truncated.

## [0.25.2] - 2026-09-25

Security release, the second batch from the multi-user threat-model pass begun in v0.25.1. It covers five areas: per-KB write checks, anonymous visitors' roles, a Host and Origin guard for credential-free servers, GitHub OAuth sign-in, and ephemeral KB privacy. **Upgrade if you run Pyrite with `anonymous_tier: write`; without a credential (auth off and no API key) on a LAN or behind a reverse proxy; from the Render, Fly.io or Railway deploy templates; or with GitHub sign-in.** A single-user install you reach only at `localhost` sees no change beyond the Host note below.

**Before you upgrade, check these:**

- **Reaching a local, auth-off server by anything other than `localhost`?** That includes a LAN IP, `*.local`, or a proxy hostname. Add that name to `allowed_hosts` (`PYRITE_ALLOWED_HOSTS`) first, or requests get `421`. If the web UI is served from another origin, add it to `cors_origins`. `localhost`, `127.0.0.1` and `[::1]` work unchanged. The CLI, MCP over stdio and agents that send no `Host`/`Origin` headers are unaffected.
- **Deployed from a Render, Fly.io or Railway template?** Those templates now start with auth enabled, and the first account registered becomes admin. **Register your admin account immediately after the first deploy.** Until an admin exists, anyone who reaches the URL first could claim that account. To stop open sign-up afterwards, set `PYRITE_AUTH_ALLOW_REGISTRATION=false`. A later release replaces this with an admin created from the command line. If an existing deployment from these templates ran with auth off, enable auth and register now, or set `PYRITE_ALLOWED_HOSTS` to its public hostname.
- **Running `anonymous_tier: write`?** Review recent writes to KBs with `default_role: none` or `read` for content you did not expect.
- **GitHub sign-in configured?** A sign-in in progress at the moment of upgrade fails once; signing in again works. `/auth/github/status` shows the connected GitHub username.
- **Using ephemeral KBs?** On upgrade, an ephemeral KB whose registry entry has no access policy is treated as private and named in a startup warning. Set `default_role` explicitly if it should be shared.

The details, and the behaviour changes (a KB you cannot read answers `404` on every write route, `anonymous_tier` must be `read`, `write` or `none`, and more), are in the Security and Fixed sections below.

### Added

- CI's new advisory `verify-red` job reverts a pull request's implementation changes to the merge base and reports, test by test in the run summary, whether each changed test fails without the fix; a test that passes anyway gets a warning annotation (#352).

- Pyrite has a logo: a pyrite crystal read as a knowledge graph (an isometric cube inside a hexagonal frame, with nodes and edges along the crystal). It replaces the "Py" placeholder in the web app sidebar, landing page and empty overview, the favicon (a simplified cube that stays legible at 16 px), and heads the README. Light and dark variants follow the theme. The SVGs are generated from exact geometry by `docs/assets/logo/build_logo.py`; `pyrite-icon-512.png` is for avatars.

- `pyrite task create --tags a,b` sets tags on the new task, parsed like `pyrite create --tags` (comma-separated, whitespace stripped) (#308, thanks @ShivanshShukla).

- `pyrite task list --priority N` filters tasks by exact priority and combines with the existing status, assignee, parent, and KB filters (#309).

### Changed

- The pre-push hook now runs `scripts/test-affected --run`: a small `core` smoke set plus every test that imports, directly or transitively, a module the branch changed, on 4 workers (`PYRITE_PUSH_WORKERS`). It falls back to the full suite when a branch touches `conftest.py`, pytest or hook configuration, CI workflows or shared test fixtures; `PYRITE_PUSH_FULL=1` forces it. CI still runs the full suite on every pull request. `scripts/test-affected --explain` says why each test was chosen (#356).

### Fixed

- **MCP bulk create preserves valid entries when a sibling has no title.** Missing or empty titles return per-entry failures in input order instead of rejecting the batch at parameter validation (#95).

- **Neighbor discovery now describes its search scope accurately (#65).** With no target KB, results may include entries from the source KB; the source entry and, by default, existing links are still excluded.

JSON output from investigation commands, admin schema, and QA URL checks remains valid when piped, including long values and forced terminal colors. URL checks emit a JSON report for empty results without mixing in progress messages.

- **MCP `resources/read` now serves content instead of erroring on every call (#217).** The SDK adapter was returning the wrong shape to the installed `mcp` SDK, so every read of a `pyrite://` resource failed; the fix makes `pyrite://kbs`, `pyrite://kbs/{name}/entries` and `pyrite://entries/{id}` work over a real session, with the same private-KB scoping the rest of MCP already enforces.

- **`pyrite schema validate --changed` no longer treats every changed Markdown file as a KB entry when no KB is configured (#346).** With zero KBs registered, it now validates nothing instead of failing on ordinary repo files like `CHANGELOG.md`, `README.md`, or `docs/**` for lacking KB frontmatter.

- **The Postgres backend works with SQLAlchemy 2.1.** SQLAlchemy 2.1 made a bare `postgresql://` URL use the psycopg (v3) driver, which the `postgres` extra does not install, so building a Postgres engine failed with `No module named 'psycopg'`. A bare `postgresql://` or `postgres://` URL now always uses psycopg2; a URL that names its own driver (`postgresql+psycopg://`, ...) is used as given.

- **Live updates now arrive for edits made through the REST API and for index jobs.** `entry_created`, `entry_updated`, `entry_deleted` and `kb_synced` from the sync REST routes, and `index_progress` from background index jobs, were silently dropped before reaching `/ws`; only web clips were delivered. Events are now handed to the server's event loop from any thread. `index_progress` is operator information: it reaches only unscoped sockets (an operator API key, or a server with auth off), whichever KB the job covers (#326, #322).

### Security

- **Ephemeral KBs stay private across restarts.** A user's ephemeral KB is private (`default_role: none`: its creator and global admins only), and that policy is now written to the KB registry and `config.yaml` in the same step that creates the KB, so every process — a restarted server, a second worker, the CLI — applies it. Ephemeral KBs created by an admin or `pyrite kb create --ephemeral` are private by default too. The creator's grant and the KB are created together: if the grant cannot be recorded, the KB is removed. **Operator note:** ephemeral KBs created by earlier versions were readable (and writable, at write tier) by every signed-in user after any restart. On load, an ephemeral KB with no `default_role` is now treated as private and a warning names it; this includes a KB registry entry under the workspace's `ephemeral/` directory whose `config.yaml` entry is gone.

- **`POST /api/kbs/{kb}/push` and the MCP `kb_push` tool accept only names.** `remote` must be the name of a remote configured in the KB's repository — a URL or a path is refused — and `branch` must be a plain branch name: a valid branch name that does not begin with `-` or `+`. The pushed refspec is built by the server (`refs/heads/<branch>:refs/heads/<branch>`), so a branch value never names a tag or requests a forced update. `POST /api/kbs/{kb}/export` applies the same branch rule. A rejected value answers `400 INVALID_REF` on REST and a `VALIDATION_ERROR` result on MCP, and git is not run; `pyrite kb push` refuses it too. Every git call that takes a remote or branch value now passes it after `--end-of-options` (or validates it where git has no such marker), including the per-user worktree, merge and diff calls.

- **Writes are checked against the KB they change, not only the caller's global role.** `POST /api/clip` now requires write access on the target KB, and `DELETE /api/reviews/{id}` checks write access on the review's own KB. On every per-KB write route (entry create/update/patch/delete, review create and delete, collection create, task claim, daily-note create, clip), a KB the caller cannot read answers 404 exactly like a KB that does not exist, and a KB they can read but not write answers 403. `requires_kb_tier` on a route that names no KB is now a test failure.
- **Anonymous visitors are scoped per KB on writes, as they already were on reads.** With `anonymous_tier: write`, an anonymous visitor could write to every KB, including private (`default_role: none`) and public read-only (`default_role: read`) ones. An anonymous visitor's role on a KB is now the lower of `anonymous_tier` and that KB's `default_role`, on reads and writes. `anonymous_tier` is a ceiling: `default_role: write` does not let a `read`-tier visitor write, and `default_role: read` or `none` still refuses a `write`-tier visitor. `anonymous_tier` is also validated when config loads: `read`, `write`, or `none` (no anonymous access). Any other value, including `admin`, stops startup with an error.
- **Starred entries are per user.** Before this, any write-tier user could unstar or reorder everyone's stars. Schema migration v24 adds an owner to each star. **On a multi-user instance, all existing stars become the instance's own list, which only auth-disabled and operator-API-key callers see; every signed-in user starts with an empty personal list.** Starring an entry in a KB the caller cannot read answers 404, and anonymous visitors cannot star.

- **A server that accepts requests without a credential answers only the hostnames it expects and refuses cross-origin writes.** This applies when auth is disabled and no API keys are configured, and when auth is enabled with `anonymous_tier: write`. In those modes, requests must be addressed to `localhost`, `127.0.0.1`, `[::1]`, the bind host if it is not a wildcard, or a name in the new `allowed_hosts` setting (`PYRITE_ALLOWED_HOSTS`). Any other `Host` gets 421. A `POST`, `PUT`, `PATCH` or `DELETE` whose `Origin` (or `Referer`, when there is no `Origin`) is neither the server's own origin nor listed in `cors_origins` gets 403. Both rules cover the whole app, including `/mcp`, `/ws` and `/site`. Clients that send neither header, such as the CLI, `curl` and agents, are unaffected. **Operators:** API-key mode (auth disabled with `api_key` or `api_keys` set) and auth-enabled servers without anonymous writes are unaffected. If you serve a credential-free instance under a LAN or proxy hostname, add that name to `allowed_hosts`. If you serve the web UI from another origin, such as a Vite dev server on a non-default port, add that origin to `cors_origins`. Behind a reverse proxy that rewrites `Host` to the upstream, UI writes (including login) get 403 until the public origin is in `cors_origins` or the proxy keeps the public `Host` (`proxy_set_header Host $host`). The Render, Fly.io and Railway deploy templates now start with auth enabled. On an existing deployment made from one of them with auth disabled, either enable auth and register the first account, which becomes admin, or set `PYRITE_ALLOWED_HOSTS` to its public hostname. See docs/configuration.md.

- **GitHub sign-in and GitHub connect complete only in the browser that started them.** The OAuth `state` is now bound to a short-lived, HttpOnly, `SameSite=Lax` cookie set when the flow starts; the callback refuses a state that arrives without it, clears it whatever the outcome, and consumes each state exactly once even under concurrent callbacks. The connect flow additionally requires the session of the user who started it and otherwise stores no token. **Operators:** users who connected GitHub should open `/auth/github/status` and check that the reported GitHub username is their own; if it is not, disconnect and connect again.

- **Subscribing to or forking a repository never takes over an existing KB or repository.** A KB's name in a subscribed repository comes from that repository's own `kb.yaml`. Subscribe and fork now refuse the whole operation — `400 KB_NAME_CONFLICT` — when a KB in the repository has the name of a KB already registered on the instance, or when two KBs in it share a name; the existing KB's registry entry, path, repository link, permissions and index are unchanged, nothing from the repository is registered, and the clone is removed. A KB name that is not a plain name (1-64 letters, digits, `-` or `_`) is refused with `INVALID_KB_NAME`, and a repository name that is already registered with `REPO_NAME_CONFLICT`. The success body states the access policy of the KBs it registered (`kb_default_role: null`: each user's global role).

- **The pre-rendered `/site` no longer serves private KBs to anonymous visitors.** A site-cache render (`POST /api/site/render`, or an index sync that re-renders) wrote every KB, including `default_role: none` ones, and `/site/*` served the result with no login and CDN-cacheable headers; `/site/sitemap.xml` listed those pages for crawlers. Now only KBs with `default_role: read` are rendered, links and titles from other KBs are left off public pages, `/site/*` answers 404 for any KB that is not public even when a stale cached file exists, and `/site/sitemap.xml` lists only public KBs, by the same rule as `/sitemap.xml`. **Operators:** delete the `site-cache/` directory beside your index (in the containers, `/data/site-cache`), re-render, and purge any CDN or proxy cache in front of `/site`. A KB that should stay on the public site needs `default_role: read`.

- **Stored cross-site scripting in the pre-rendered `/site` pages is fixed.** The site renderer passed raw HTML in entry bodies (and in a `_homepage` entry's intro and sections) straight into the page, and embedded the JSON-LD block with a plain `json.dumps`, so a title containing `</script>` closed the element. Anyone able to write an entry could run script on the application's own origin, where it can call the API as a visiting admin. Entry text is now HTML-escaped before the markdown transforms, link URLs are checked against a scheme allowlist after decoding character references, and the JSON-LD escapes `<`, `>` and `&`. As defence in depth, every `/site` response now sends `Content-Security-Policy` (scripts from the site itself only, no inline script or event-handler attributes) and `X-Content-Type-Options: nosniff`; the pages' scripts moved to `/site/_static/`. **Operators:** re-render the site cache (`POST /api/site/render`) so existing pages pick up the escaping and the new scripts; until you do, pages rendered by an earlier version lose their search box and table of contents under the new policy. If a reverse proxy injects a script into `/site` pages (an analytics snippet, say), the policy blocks it until you allow it with the new `site_csp_extra` setting (`PYRITE_SITE_CSP_EXTRA`), which appends sources in CSP syntax, for example `script-src https://plausible.io 'sha256-…'; connect-src https://plausible.io`; see `docs/configuration.md`.

## [0.25.1] - 2026-09-23

Security release. It fixes authorization gaps in the multi-user path (auth enabled, several users): API-key handling, per-KB checks on export, MCP writes, settings, statistics and repositories, and path handling for entry ids, ephemeral KBs and exports. **If you run Pyrite with auth enabled and more than one user, upgrade**, and read the Security section for what to check on an existing install. Multi-user remains alpha; the 0.26 security review continues (see `kb/roadmap.md`).

### Added

The MCP `task_create` tool now accepts an optional `tags` array and writes it through to the new task, matching the CLI and `TaskService.create_task()`.

### Fixed

- **Concurrent first semantic/hybrid searches on a cold process no longer race to load the embedding model (#207).** `EmbeddingService._get_model()` checked the model cache under a lock but built the (slow, non-thread-safe) `SentenceTransformer` model unlocked, so several concurrent first callers each built their own copy at once — a segfault or hang under real load. The model load now happens inside a double-checked lock: the first caller for a cold model name loads it once, every other concurrent caller waits and reuses the same object, and a failed load leaves the cache empty so the next caller can retry.

### Security

- With auth enabled and no API keys configured (the usual multi-user setup), any `X-API-Key` or `Bearer` value was accepted as an admin key on REST and `/mcp`, giving an anonymous caller read and write access to every KB, private ones included. A key is now accepted only when keys are configured; otherwise the request falls through to the session cookie or the anonymous tier. Installs with auth disabled are unchanged.
  With auth enabled **and** keys configured, every `/mcp` request that was not a valid key (a session cookie, a session-token Bearer, or a bad Bearer) failed with a 500; those now resolve the user or answer 401.
  **Operators:** every release from v0.21.0 through v0.25.0 is affected. If you run a server with auth enabled, upgrade, and check your KBs' git history for entries you did not write.

- **Expiring or removing an ephemeral KB deletes the per-KB permission grants recorded for it.** Garbage collection and forced expiry removed the KB's index rows, files and config but left its `kb_permission` rows — including the admin grant its creator receives — so a KB later registered under the same name inherited them, and the former grantees could read, write or administer a KB nobody had granted them.

- **An ephemeral KB's name can no longer point it at another KB's directory.** `POST /api/kbs/ephemeral` (open to write-role users) used the requested name as a directory under the workspace without validating it, so a name containing `..` or an absolute path created an "ephemeral" KB aliasing an existing directory, including a private KB's, with the caller as its admin. Names are now 1-64 letters, digits, `-` or `_`, starting with a letter or digit, and a name already used by another KB (in config or the KB registry), or whose directory already exists, is refused; both answer 400. A create never overwrites an existing KB registration. Expiring an ephemeral KB (garbage collection or `DELETE /api/kbs/ephemeral/{name}`) now never deletes a directory outside `<workspace>/ephemeral/`.
  **Operators:** if you run a multi-user server, upgrade, then check your config for ephemeral KBs whose `path` is outside `<workspace>/ephemeral/` and remove them; after upgrading they are unregistered on expiry without their directory being deleted.

- **Export and site renderers no longer let a stored `entry_type` or `id` write outside the output directory, or let two distinct unsafe values collide onto the same output file (#221).** `pyrite/services/export_service.py`, `pyrite/renderers/quartz.py`, and `pyrite/renderers/notebooklm.py` each joined a stored, caller-controlled value (an entry's `type` or `id`) directly into a filesystem path. An entry whose `type` or `id` was an absolute path or contained `../` could make `pyrite export` (to a directory, to a repo, `export site`, or `export collection --bundle by-type`) write attacker-controlled content outside the intended directory — reachable via two ordinary write-tier API calls (create an entry, then export) or an id indexed from git frontmatter that was never slugified. A safe `type` or `id` (one that needed no sanitizing) still produces exactly the same file or folder name as before — but a value that needed sanitizing (e.g. `note_`, `note/`, `a/b`) is no longer merged onto the name of another value that sanitizes the same way (e.g. `note`, `a_b`); it now gets a short hash suffix so both survive. `GenericEntry` and plugin types are unaffected functionally, and the exported frontmatter's `type:` still carries the raw value.

- **Exporting a KB to a repository now requires being able to read that KB.** `POST /api/kbs/{kb}/export` checked only the caller's global write tier, so a write-role user with no access to a private KB could push that KB's entries into a repository of their choosing. The route now applies the same per-KB read rule as every read route, and a KB the caller cannot read answers exactly as one that does not exist (404). Admins, operator API keys and installs with auth disabled are unchanged.
  **Operators:** if you run a multi-user server with private KBs (`default_role: none`), upgrade, and review your logs for `POST /api/kbs/*/export` calls by users without access to that KB.

- **An entry id could name a file outside its KB on lookup.** `KBRepository.find_file` turned the id into `<kb>/<id>.md` unvalidated, so the write-tier MCP tool `kb_delete` with `entry_id: "../x"` deleted a `.md` file outside the KB, and an id such as `*` was treated as a glob and deleted whichever entry matched first. Writes were already guarded; lookups (and so delete, load and rename's source) no longer turn a non-plain id into a path, treat an id as a name rather than a glob pattern, and refuse any path that would land outside the KB (including `collection-..`). Ids from frontmatter that are not plain names are still found by the frontmatter scan, inside the KB.
  **Operators:** anyone with write access over MCP could delete `.md` files the server process can reach. Check for missing files outside your KB directories if you exposed `/mcp` with write access.

- **Deleting any KB, or unsubscribing a repository, deletes the per-KB permission grants recorded for its KBs.** Removing a KB through the registry (`DELETE /api/kbs/{name}`, the MCP `kb_registry_remove` tool, `pyrite kb remove`) or unsubscribing a repository (`DELETE /api/repos/{name}`) removed the KB and its entries but left its `kb_permission` rows behind, so a KB later registered under the same name inherited them and the former grantees could read, write or administer it. Grants are now deleted with the KB in the same transaction, on every deletion path. Removing a KB from `config.yaml` alone does not delete it (it stays registered as a user-managed KB), so its grants are kept.

- **The MCP `kb_stats` tool reports only the KBs the connection may read.** Over `/mcp` it returned index-wide statistics to every caller — a private KB's name and row, and totals that counted its entries, tags, links and types — after `GET /api/stats` had been scoped. It now applies the same scoping as the REST route: a scoped connection gets the per-KB map and every total from its readable KBs only; admins, operator API keys and local stdio are unchanged.

- **MCP write tools now enforce the per-KB write permission REST enforces.**
  Over `/mcp`, a session user's global role chose which tools were offered,
  but nothing checked their role on the KB a write tool targeted, so a user
  whose effective role on a KB was `read` (through its `default_role` or an
  explicit grant) could still create, update and delete entries in it --
  `kb_create`, `kb_update`, `kb_delete`, task tools and every write-tier
  plugin tool. The same writes over REST were already refused. A scoped MCP
  connection now resolves the KBs it may write through the same per-KB rule
  REST uses, and every tool registered above the read tier is refused
  (`FORBIDDEN`) unless each KB it names is writable. Operator API keys and
  global admins are unaffected. Plugin read tools are now registered at the
  read tier on write- and admin-tier servers, so they are rate-limited as
  reads, and the journalism-investigation plugin's `investigation_search_all`
  and `investigation_status`, which only read, are now read-tier tools. **Operators:** if you run the HTTP MCP endpoint with users whose
  per-KB role is narrower than their global role, review recent changes to
  those KBs (`git log` in each KB) for writes those users should not have
  made.

- **Operator settings are admin-only, and secret settings are never read
  back.** The settings API let any write-tier caller change instance-wide
  operator settings -- the AI provider, model, base URL and API key -- and
  returned stored credentials such as `ai.apiKey` in plain text to every
  caller who could read settings, including the anonymous tier. Changing an
  `ai.*` setting, or any setting whose name marks it as a credential
  (`apiKey`, `token`, `secret`, `password`, `credential`), now requires the
  admin tier; `GET /api/settings` and `GET /api/settings/{key}` return a
  fixed mask for a secret that is set (listed under `masked`), to every
  caller including admins, and writing the mask back leaves the stored value
  unchanged. Credentials embedded in `ai.baseUrl` (a `user:password@`
  part, or a key or token query parameter) are masked for non-admins. The
  web settings page shows the key as "set on the server", and a change the
  server refuses no longer appears to stick.
  **Operators:** if your instance allowed anonymous or write-tier access and
  an AI API key was stored through the settings page, treat that key as
  exposed and rotate it with your provider, then set the new one as an
  admin. Check that `ai.baseUrl` and `ai.provider` hold the values you
  expect.

- **`GET /api/repos` lists only repositories whose KBs the caller may read.** It listed every subscribed repository to any write-tier user, including those holding a private KB, with their KB names and entry totals. A repository holding a KB the caller may not read is now left out, exactly as if it did not exist — the same rule `GET /api/repos/{name}` applies. Global admins, operator API keys and instances with auth disabled see every repository as before.

- **The `/api/repos/{name}` routes apply per-KB authorization to the KBs a repository contains.** They were guarded by the global `write` tier only, so any write-tier user could read the status of a repository holding a private KB (its KB names, entry count and contributors), sync it, open a pull request from it, or unsubscribe it — which removes its KBs from the instance. Now `GET` requires read on every KB the repository holds, `POST .../sync` and `POST .../pr` require write, and `DELETE` requires admin (the tier `DELETE /api/kbs/{name}` requires, since unsubscribing removes those KBs). A caller who may not read one of the KBs gets exactly the response a nonexistent repository gets on that route; one who may read them but lacks the tier gets 403. A write-tier operator API key can no longer unsubscribe a repository that holds KBs; that now needs an admin key.

- **`GET /api/stats` reports only the KBs the caller may read.** It returned index-wide statistics to every caller, including a logged-in user or anonymous visitor with no grant on a private KB: that KB's name and row in `kbs`, and `total_entries`, `total_tags`, `total_links` and `type_counts` that counted its rows. A scoped caller now gets the per-KB map and every total computed from its readable KBs only (a link counts when both its ends are readable). Global admins, operator API keys and instances with auth disabled see the same index-wide numbers as before.

- The web frontend's transitive `cookie` dependency (under `@sveltejs/kit`) is forced to `^0.7.0` with an npm override, closing CVE-2024-47764 / GHSA-pxg6-pf52-xh8x (low). Pyrite's production web build uses `adapter-static` and never runs SvelteKit's cookie code, so no install was exploitable; this clears the alert and protects `vite dev` / `vite preview`. Even the latest SvelteKit (2.70.3) still pins `cookie ^0.6.0`, so drop the override when it moves.

- **`/ws` now authenticates its handshake and sends each socket only the KBs its
  owner may read (#218).** Before this, anyone who could reach the server could
  open `/ws` with no credential and receive the *names* of private KBs and the
  ids of entries created in them, as they happened (no titles or bodies). The
  handshake now accepts the same credentials as the REST API (an operator API
  key as a header or `?api_key=`, the `pyrite_session` cookie, or the
  anonymous tier when one is configured) and is refused otherwise; a handshake
  whose `Origin` is neither the server's own host nor in `cors_origins` is
  refused too. A socket's readable KBs are fixed when it connects, so a revoked
  grant or a logout takes effect on reconnect. A default local install (auth
  off, no API keys) still needs no credential, but the `Origin` check applies
  there too, so another site open in the same browser can no longer read the
  event stream. If the web UI is served from an origin other than the API's
  host, or a reverse proxy rewrites the `Host` header, add the UI's origin to
  `cors_origins`; a refused handshake is logged as a warning naming both. An
  event that names no KB reaches a scoped socket only if it is a global event
  (`kb_synced`).

## [0.25.0] - 2026-09-22

The community's first release — see the announcement and `kb/roadmap.md`.

### Changed

- **Unreleased changes are now recorded as one file per change under
  `changelog.d/`, not as a bullet in `CHANGELOG.md` (#243).** Every
  `[Unreleased]` bullet was appended at the same spot, so any two pull requests
  in flight conflicted there by construction — five times in one session, three
  of them on first-time contributors' branches, every one resolved by "keep
  both, either order". A fragment is named `<slug>.<section>.md`, so no two
  branches write the same path and neither a rebase nor a merge has anything to
  resolve; `scripts/release.py` assembles the fragments under the version
  heading at release time and deletes them, and refuses an unknown section
  rather than dropping the entry. See `changelog.d/README.md`.

### Fixed

- **The cascade list tools return only rows from KBs the caller may read
  (#223).** `cascade_actors`, `cascade_timeline`, `cascade_capture_lanes`,
  `solidarity_timeline` and `solidarity_infrastructure_types` take `kb_name`
  optionally, and four of them default it to their own KB -- so a scoped caller
  who omitted the name was refused rather than served a KB they may not read.
  Each handler passes the caller's readable set to the storage query alongside
  the name, which means the default KB is served only when it is readable, and
  `cascade_capture_lanes` (which has no default) spans exactly what the caller
  may read rather than the whole index. Unscoped callers are unchanged.

- **A containerised Pyrite now binds `0.0.0.0` and honours the platform's
  `$PORT`, so a Docker or Railway deploy passes its healthcheck (#20).** The
  server defaulted to `127.0.0.1:8088` and ignored `PORT`, so it came up on an
  interface nothing outside the container could reach and the platform killed
  it as unhealthy. Port precedence is now `PYRITE_PORT` > `PORT` >
  config/default, and a non-integer value raises an error naming the variable
  it came from instead of a bare `int()` traceback. Only the image sets
  `PYRITE_HOST=0.0.0.0` — a local `pyrite serve` still binds loopback, so
  nothing on your machine starts listening on every interface because of this
  change. Railway's one-click deploy still needs a volume mounted at `/data`.

- **`pyrite create -t <type>` no longer silently files a different type when the
  KB does not declare the one asked for (#197).** Core types were exempt from
  the CLI's write-side refusal, so `-t note` against a KB whose schema declares
  only `adr | backlog_item | component | standard` skipped the guard, and plugin
  type resolution then promoted it to its most-derived `note` subtype — an ADR
  with `adr_number: 0` under `kb/adrs/`, from a command that asked for a note.
  The refusal now covers every type the KB does not declare; `--allow-undeclared`
  still overrides it.

- **The four protocol finders narrow to the caller's readable KBs in SQL
  (#223).** `find_by_assignee`, `find_overdue`, `find_by_status` and
  `find_by_location` took a single `kb_name`, so the four `kb_find_by_*` MCP
  tools cut the page with `LIMIT` and dropped unreadable rows afterwards -- a
  scoped caller could receive a short page while readable rows existed below
  the cut. Each finder now takes `kb_names` and narrows through the shared
  `kb_names_clause` (a named KB binds, a readable set narrows, an empty set
  matches nothing, `None` is unchanged), and the four handlers pass the
  readable set down. The handler's post-filter stays as the fail-closed
  guard.

- **A `GenericEntry` no longer duplicates its undeclared frontmatter keys into
  a `metadata:` block on save.** `Entry._base_frontmatter` serialized the whole
  `self.metadata` mapping as a nested block while `GenericEntry.to_frontmatter`
  also promoted the same keys to top level, so a no-op load→save grew a
  `metadata:` block the source file never had. Only keys that came from an
  explicit `metadata:` block stay nested now; the rest are promoted once
  (#149). A `metadata:` value that is not a mapping (null, a string, a list, a
  number) is kept verbatim and written back on the next save, with a warning,
  instead of failing the load and saving the file back as a different entry
  type. Deliberate behaviour change: an entry *created* with `metadata={…}` now
  writes those keys top-level only, where `dev` also wrote a nested block --
  except a key the base frontmatter already emits (`title`, `id`, …), which
  stays nested under `metadata:` rather than being dropped.

- **`pyrite index health` no longer reports a `subdirectory_mismatches` false
  positive for every entry of a type whose declared subdirectory ends in `/`.**
  `people/` and `people` are the same directory, but the check compared the
  declared string against a path component, so a KB created exactly as the
  getting-started guide instructs came back `status: warning` with one row per
  entry. Both sides are normalized now, and an entry genuinely in the wrong
  directory is still flagged (#44).

- **`investigation_search_all` and `investigation_find_duplicates` search only
  the KBs the caller may read (#223).** Both take a *list* of KB names, and
  omitting it means "every KB" to the code they delegate to -- so a scoped
  caller who omitted it searched the whole index. Each handler now composes
  what was asked for with what may be read: a list that names KBs keeps the
  readable ones among them, no list means every readable KB, and an empty
  result stays an empty list rather than collapsing back to "every KB".
  Unscoped callers are unchanged. These were the two genuinely cross-KB tools
  in the last extension; the rest resolve to a single KB and are handled
  separately.

- **The single-KB investigation tools serve nothing when the caller may not
  read the KB they resolve to (#223).** Twelve read tools -- `investigation_timeline`,
  `investigation_entities`, `investigation_network`, `investigation_sources`,
  `investigation_claims`, `investigation_evidence_chain`,
  `investigation_export_pack`, `investigation_money_flow`,
  `investigation_qa_report`, `investigation_ownership_chain`,
  `investigation_ftm_export`, `investigation_status` -- settle on one KB when
  none is named: the caller's, or the plugin's own default. A scoped caller was
  refused those calls rather than served a KB they may not read. Each resolves
  through a guard now, which answers with a KB name that matches no entry when
  the resolved KB is not readable, so the tool keeps its normal shape with
  nothing in it. The write-path tools in the same extension keep the
  fail-closed listing, because a caller who may write a KB can read it.

- **Six backlog files no longer carry a folded copy of their own body, or
  another machine's absolute path (#150).** They were committed with a `body:`
  frontmatter key holding the whole entry text as a YAML scalar, and a stray
  `file_path:` key pointing at a worktree on the machine that wrote them. The
  write path already drops both keys on any save, so every load-and-save
  rewrote those files; they have now been re-saved once, deliberately. The
  text is unchanged -- the body section was already the whole entry, and for
  two of them the folded copy was a truncated prefix of it -- and the
  round-trip gate's residual count drops from 69 to 63.

- **`cascade_network` and `investigation_network` page both directions instead of
  returning every neighbour at once (#63).** On a hub node the old response was
  roughly 18k tokens (35 outlinks plus about 130 backlinks for one cascade
  node). Both tools now take `limit` (default 50, `0` means no cap) and `offset`
  per direction, order each direction deterministically so a page never repeats
  or skips a row, and return the true totals plus `truncated` so a caller can
  tell what it did not see. The paging lives in `query_network`;
  `get_outlinks` still has no pagination parameters of its own.

- **The API returned 500s under ordinary concurrent read load: one SQLAlchemy
  `Session` was shared by every request.** `PyriteDB` created a single
  `Session` in its constructor and the server cached one `PyriteDB` on app
  state, so the ~117 plain `def` handlers — which FastAPI runs on anyio's
  40-thread worker pool — all drove one session at once, with
  `check_same_thread=False` silencing SQLite's own guard. The result was
  corrupted result state rather than a clean error, surfacing as
  `IndexError: tuple index out of range`, `InvalidRequestError: This session is
  provisioning a new connection`, `SystemError`, and in CI `This session is in
  'prepared' state` — four messages, one cause. Measured at **75% of 240
  concurrent reads failing** in-process and **9 of 96 requests returning 500**
  at concurrency 8 against a live server; both are now **0**. Each request gets
  its own session via a per-request handle onto the shared engine, closed on
  every exit path, and the connection pool is sized to the threadpool
  (`pool_size=40, max_overflow=20`) so per-request sessions cannot trade the
  corruption for `QueuePool limit ... reached`. `verify_api_key` no longer runs
  synchronous DB work on the event-loop thread, and the shared raw sqlite3
  connection now hands out a private cursor under a lock instead of sharing an
  implicit one. Query results and every endpoint's behaviour are unchanged.
  Fixes #131.

- Postgres: `ensure_schema()` now creates the full-text-search trigger in every schema it sets up. Its guard asked whether a trigger named `trg_entry_fts` existed anywhere in the cluster, so a second Pyrite instance sharing one database in its own schema silently got no trigger — `entry.fts_vector` was never populated and keyword search returned no results, with no error.

- **A `QUERY_SYNTAX` error names the token the caller wrote instead of leading
  with SQLite's fragment (#67).** `detention AND third-party-doctrine` reaches
  MATCH unquoted because the query carries an operator, and SQLite answers
  `no such column: party` (a piece of a token nobody typed), which sent readers
  looking for a schema problem. The message now says the token was read as a
  column reference and shows the quoted form. When the error names a fragment no
  token of the query contains, the previous text is kept unchanged.

- **One field-projection rule for every read surface (#193).** `?fields=` on the
  REST routes, the MCP `fields` argument and `--fields` on the CLI now share
  `pyrite/services/read_shaping.py`: the identity pair (`id`, `kb_name`) is kept
  in every projection, keys the record does not have are never invented, and the
  CLI's `--fields` no longer drops the identity pair (that was #192). A
  parametrised test pins the three surfaces to the same key set for the same
  request, which nothing did before.

- **Release notes credit contributors whose work landed inside someone else's
  pull request (#248).** The notes were built from the authors of *merged* PRs
  only, so a contributor whose PR was closed because another branch carried
  the same fix first was thanked nowhere — #237 fixed a documented-but-missing
  command fourteen minutes before #239 merged the identical line, and shipped
  in 0.24.2 uncredited. `scripts/release.py` now also reads `Co-authored-by:`
  trailers over the release's own commits, which is the form `CONTRIBUTING.md`
  already asks for and the only one that survives the merge queue's squash.

- **A no-op load and save keeps each document's block-sequence indentation
  (#148).** A `links:` block written with its items indented under the key
  (`sequence=4, offset=2`) came back flush with the key, because the single
  shared `YAML()` in `pyrite/utils/yaml.py` never set an indent and ruamel's
  default is `sequence=2, offset=0` -- so a load-and-save with no edit rewrote
  the file. The dumper now reads the numbers out of the parsed tree's own
  line/column records, per document, so nothing is reformatted that was not
  already written that way: hand-written files keep their style, files pyrite
  wrote keep theirs, and new content is emitted exactly as before. A document
  that mixes both styles in one file cannot be reproduced -- the emitter takes
  one setting per document -- and a test says so rather than leaving it
  unsaid.

- **A route's own page title was always overwritten with the brand name.**
  The root layout assigned `document.title = brandStore.name` unconditionally
  in a `$effect`, clobbering whatever `<svelte:head><title>` a route had set,
  regardless of which finished first — a race against when
  `/config/branding` returns. The layout now renders `<svelte:head><title>
  {brandStore.name}</title>` only for the handful of routes that declare no
  title of their own (`UNTITLED_ROUTES` in `web/src/routes/brand-title-routes.ts`,
  pinned against the routes on disk by a structural test); every other route
  never has a layout title effect to be clobbered by. (#49)

- **`social_top` and `social_newest` return only writeups from KBs the caller
  may read (#223).** Both take `kb_name` optionally, so a call that omitted it
  read the whole index. A scoped MCP caller was refused those calls rather than
  served the index (#201) -- safe, but it left the tools unusable for exactly
  the callers they are meant for. Each handler takes the readable set now and
  narrows in SQL, so a scoped caller gets a full page of what they may read,
  and a call that names no KB cannot reach a private one. A named KB binds to
  that KB, an empty readable set matches nothing, and an unscoped caller
  (global admin, operator API key, local stdio) is unaffected. This is the
  first of the six extensions; the rest are still listed in
  `OPTIONAL_KB_TOOLS`.

- **`sw adrs`, `sw components` and `sw standards` are bounded like
  `sw backlog` (#238).** Each returned every matching entry -- no
  `--limit`/`--offset` on the command, no bound on the MCP tool behind it -- so
  on a KB with a few hundred ADRs or components one call could carry the lot
  with no way to page. All six now take the `kb_list_entries` shape: a default
  bound of 50, `--limit 0` (or `limit: null`) for the full list, and `total`,
  `limit`, `offset` and `has_more` on the MCP response. The filter
  (`--status`, `--kind`, `--category`, `path`/`name`) is applied **before** the
  bound, so a filtered page cannot lose a match that sorted past the cut.

- **Every `sw_*` read surface returns only rows from KBs the caller may read
  (#223).** Twelve tools in `extensions/software-kb` take `kb_name` optionally
  -- `sw_adrs`, `sw_backlog`, `sw_board`, `sw_component`, `sw_conventions`,
  `sw_create_adr`, `sw_epics`, `sw_milestones`, `sw_pull_next`,
  `sw_review_queue`, `sw_standards`, `sw_validations` -- so a call that omitted
  it spanned every KB, and a scoped caller was refused such calls rather than
  served the index. Each query now narrows through the same `kb_scope_clause`
  the other extensions use: a named KB binds to it, a readable set narrows to
  itself, an empty set matches nothing, and an unscoped caller (global admin,
  operator API key, local stdio) is unchanged.

- **The task commands can see a KB that is registered in the database (#245).**
  `kb create` and `kb add` register a KB in the database registry, and every
  other CLI path merges that registry into the config before use. The task
  commands read the YAML config directly, so a registered KB answered
  `KB_NOT_FOUND` from `task create` while `kb list` still showed it — success
  and discoverability both lying about write-readiness. They now go through the
  same shared loader as the rest of the CLI.

- **`created_at`/`updated_at`: a file that carried them keeps them, a file
  that did not never grows them.** `_base_frontmatter` re-emits the two keys
  only for entries loaded from a file that had them, as second-precision
  timestamps (`updated_at` only when the caller did not supply one); the
  internal stamp goes through `Entry.touch_updated_at()` so bookkeeping is
  not mistaken for a user edit; and unchanged values keep their source node,
  so a `created_at: 2026-01-15` stays a bare date instead of being rewritten
  as a timestamp, and a value the loader cannot parse (`created_at:` with no
  value, `''`, `Jan 15 2026`) is kept exactly as written rather than replaced
  with the load time (#151).

- **`pyrite update -f` parses its value the same way `create -f` does (#231).**
  The update path coerced ints only, so `-f tags=alpha,beta` wrote the raw
  string; the reader then iterated it as a sequence and tagged the entry with
  the characters of the value, silently dropping it out of tag search,
  `pyrite tags` and every tag-filtered view. Comma-separated lists, JSON arrays
  and objects, floats and booleans now parse identically on both write commands.

- **`wiki_stubs`, `wiki_review_queue` and `wiki_quality_stats` return only
  articles from KBs the caller may read (#223).** All three take `kb_name`
  optionally, so a call that omitted it spanned every KB; a scoped caller was
  refused those calls rather than served the index (#201), which left the tools
  unusable for exactly the callers they are meant for. Each passes the caller's
  readable set into the query -- the same `kb_scope_clause` the social tools
  use -- so the lists and counts describe what the caller may read, and an
  empty set matches nothing instead of everything. Unscoped callers (global
  admin, operator API key, local stdio) are unchanged.

- **`zettel_inbox` returns only notes from KBs the caller may read (#223).**
  `kb_name` is optional on the tool, so a call that omitted it read every KB; a
  scoped caller was refused that call outright rather than served the index
  (#201), which left it unusable for exactly the callers it is meant for. The
  handler takes the caller's readable set now and passes it to
  `list_entries(kb_names=...)`, so the storage query narrows rather than the
  page afterwards -- and an empty set matches nothing instead of everything.
  Unscoped callers (global admin, operator API key, local stdio) are unchanged.

### Security

- **The web clipper re-validates every redirect hop, not just the URL it was
  handed (#219).** `_check_url_safe` refused loopback, link-local, RFC1918 and
  reserved addresses for the first request, and httpx then followed
  `follow_redirects=True` without checking where it landed: a public host could
  answer `302 Location: http://127.0.0.1:8000/api/kbs`, or the cloud metadata
  address, and the clipper returned the internal response to the caller. A
  request hook now runs the same check for every request in the chain, and the
  refusal is the same `ClipperBlockedHostError` a directly blocked URL raises,
  so the two cannot be told apart.

- **The link-discovery routes read every KB a call names, and return
  candidates only from KBs the caller may read (#186).**
  `GET /api/links/discover-neighbors` takes `target_kb` and
  `GET /api/links/batch-suggest` takes `source_kb`/`target_kb`, names the
  per-KB read check in `pyrite/server/api.py` did not look at: a caller could
  name a readable KB and still be served from a private one. Both names are
  resolved like `kb`/`kb_name` now, and
  `LinkDiscoveryService.discover_neighbors`/`batch_suggest` take the caller's
  readable set, so omitting `target_kb` -- which makes the underlying search
  span every KB -- can no longer hand back a private KB's entry as a
  suggestion. The `/mcp` tools `kb_discover_neighbors` and `kb_batch_suggest`
  take the same set, so both surfaces answer alike. Nothing is required of an
  operator.

- **The second, dead `git pull` implementation in `pyrite/github_auth.py` is
  gone, so no unredacted git error text can reach a caller through it (#185).**
  `pull_repo` returned `f"Pull failed: {result.stderr}"` with only the token
  replaced: absolute paths, and whatever else git chose to print, went out
  unredacted, bypassing the path redaction `GitService.sanitize_error` applies.
  Nothing called it — `git grep pull_repo` found only the definition — so there
  is no behaviour change and nothing for an operator to do. A test now pins its
  absence the same way the removed `clone_private_repo` is pinned.

## [0.24.3] - 2026-09-20

"Operational" — see `kb/roadmap.md`.

**The last release from `markramm/pyrite`.** The repository moves to the
`pyrite-wiki` organization next, and 0.25 will be the first release from its
new home. Eight of the 86 pull requests merged into this release came from
outside contributors — @Voyagerroc-Lab, @YaoSong808, @fathirramadhan-web,
@makiaveli1 and @zhongxiao-chang. That is 9%. Of the pull requests open as
this release was cut, 11 of 13 are theirs.

The move is not a rename. It is what makes pull request queues, contributor
permissions and a home for community extensions possible — and the point at
which Pyrite stops being one person's experiment.

### Fixed

- The `pyrite` CLI now configures the package logger at startup, so warnings
  use the standard timestamped stderr format without tracebacks while JSON
  stdout remains parseable. Library imports install only a `NullHandler` and
  leave application and root logging configuration untouched (#196).

### Security

- **Private-KB content was readable over MCP-over-HTTP by any logged-in
  user.** The `/mcp` mount resolved a global tier from the caller's
  credential and applied no per-KB read scoping anywhere in its path — the
  one KB-content surface the REST-side fix did not reach. A plain read-tier
  user could read entry **bodies** from a KB they had no grant on: `kb_get`,
  `kb_read_body`, `kb_list_entries` and `kb_search` all served it, and
  `kb_list` named the KB. MCP now resolves the caller's readable set per
  connection, through the same helper the REST routes use, and enforces it
  at all three content chokepoints: tool dispatch, resources and prompts. A
  KB the caller may not read is reported exactly as one that does not exist,
  so its existence stays private. Anonymous callers were never exposed —
  `/mcp` already rejected them. Operator API keys, global admins and local
  stdio are unscoped, unchanged. Tools that span every KB and cannot yet
  filter (45 extension tools taking an *optional* `kb_name`) are refused for
  a scoped caller rather than served across the whole index; naming a
  readable KB makes them work as before.
  ([#201](https://github.com/markramm/pyrite/issues/201))

- **`web/` dependency bump closes 16 of 19 open Dependabot alerts.** `vite`
  7.3.1 → 7.3.6 (range pinned to `^6.0.0 || ^7.3.5` so it cannot resolve
  below the patched line; GHSA-4w7w-66w2-5vf9, GHSA-v2wj-q39q-566r,
  GHSA-p9ff-h696-f583, GHSA-v6wh-96g9-6wx3, GHSA-fx2h-pf6j-xcff), `svelte`
  5.53.3 → 5.57.0 (direct bump; six SSR/XSS/ReDoS advisories, patched at
  5.53.5 and 5.55.7), `postcss` 8.5.6 → 8.5.28, `picomatch` 4.0.3 → 4.0.7,
  `esbuild` 0.27.3 → 0.28.2 (all three transitive, via `npm update`, no
  direct pin or override needed). `cookie` (GHSA-pxg6-pf52-xh8x) stays open:
  it is pinned to `^0.6.0` by `@sveltejs/kit` 2.x, and the fix needs
  `cookie` ≥0.7.0, which only a breaking `@sveltejs/kit`
  3.x/`adapter-node`/`adapter-static` major would allow.

- CI workflow jobs now declare least-privilege `permissions:` explicitly
  (workflow-level `contents: read`, plus `pull-requests: read` on the
  `changes` job for `dorny/paths-filter`) instead of running with the
  repository's default `GITHUB_TOKEN` scope. Closes the eight CodeQL
  `actions/missing-workflow-permissions` alerts on `ci.yml`.
- **Repo endpoints returned raw git stderr in their *error* bodies, disclosing
  the server's absolute filesystem paths to any write-tier caller.** `POST
  /api/repos/subscribe` on a missing repo answered `400 {"message": "Clone
  failed: Cloning into '/Users/<user>/.pyrite/repos/…'…"}`; `/api/repos/fork`
  and `/api/repos/{name}/pr` had the same shape, and `/api/repos/{name}/sync`
  nested the same text in a 200 body. Every such message is now redacted —
  absolute paths (including ones containing spaces, `~` and Windows drive
  paths) replaced with `<path>`, tokens with `***` — while the full stderr is
  logged at WARNING so the operator loses nothing (CodeQL
  `py/stack-trace-exposure` #51, #52, #53). Clone failures are additionally
  classified into stable, documented codes (`REPO_NOT_FOUND`, `AUTH_REQUIRED`,
  `BRANCH_NOT_FOUND`, `PATH_EXISTS`, `CLONE_TIMEOUT`, `INVALID_REQUEST`,
  falling back to `CLONE_FAILED`); see `docs/json-contracts.md`. Pull and push
  keep returning git's own words, redacted — a merge conflict or a rejected
  push still says so, and remote URLs the caller supplied are preserved.
  Success bodies (`RepoInfo.local_path`, `subscribe`'s `path`) narrowed to
  the workspace-relative form in the entry below.
- **Repo endpoint *success* bodies disclosed the same absolute server paths
  #161 removed from error bodies.** `RepoInfo.local_path` (`GET /repos`, `GET
  /repos/{name}`) and the `path` key in `subscribe`/`fork` responses returned
  the server's real filesystem path (`/Users/<user>/.pyrite/repos/owner/repo`)
  at 200. Both are now relative to the workspace root (`owner/repo_name`) —
  the field stays populated rather than dropped, since `RepoInfo` is public
  REST response shape an external consumer may already read, but discloses
  no server layout or usernames; a path outside the workspace root comes back
  as `"<path>"` rather than leaking the absolute value or raising. Only the
  HTTP boundary changed — the CLI (`pyrite repo list`/`status`) still shows
  the real absolute path for the local operator, and every internal caller
  (fork's `add_remote`, sync, PR, config load) still reads `local_path`
  absolute off the DB row or the service's own dict, unaffected. See
  `docs/json-contracts.md`.
- **Private KBs were readable by any logged-in user, and by anonymous
  visitors on an auth-enabled instance.** Per-KB roles (`default_role: none`,
  explicit grants) were enforced on write routes only; every read route
  returned private content, and search and the KB list disclosed it. Read
  routes now require read on the named KB (404, so a private KB's existence is
  not disclosed either) and cross-KB routes are filtered to the KBs the caller
  may read, in SQL for list, count and keyword search. Operator API keys are
  unaffected (they are the operator's credential). MCP is operator-level and
  unchanged.

  Every route serving KB content is now covered. The first pass reached
  entry by id, list, search, batch read, graph, export and KB
  info/schema/orient; a second pass reached the sixteen endpoint modules
  it had missed — `/tags` and `/tags/tree` (a tag name and its count
  disclose a KB), `/timeline`, `/qa/status`, `/qa/validate`,
  `/qa/validate/{entry_id}`, `/qa/coverage`, both `/entries/{id}/versions`
  routes, `/entries/{id}/blocks`, `/daily/dates` and `/daily/{date}`, all
  four `/collections` reads, `/tasks`, `/starred`, the three
  `/kbs/{kb}/templates*` routes, the three `/reviews` reads, and the four
  `/ai/*` POSTs, whose retrieval now only sees readable KBs. Where a
  route spans KBs the filter is pushed into the query, so `count`,
  `total` and `limit` are computed over readable rows only — a count of
  three for a KB you cannot read is itself a disclosure.

  **A request that names a knowledge base in more than one place is now
  checked against every one of them.** A request can name a KB in its path,
  in either of two query spellings (`kb`, `kb_name`) and in its JSON body,
  and the permission check used to stop at the first place it looked while
  the handler read a different one — so pairing a knowledge base you may
  read with one you may not could return the second one's content, or
  authorise a write to it. Every KB a request names must now be permitted:
  readable for a read route, and at the required tier for a write route.
  A request whose body cannot be parsed is refused rather than treated as
  naming no KB at all.

  `GET /api/kbs/{kb}/changes`, which returns uncommitted entry-level diffs,
  now requires read access to that KB as well as the global read tier.

  `tests/test_read_scoping_is_structural.py` now enforces this: it walks
  the real app's routes and fails for any `/api` route that declares no
  read-scoping dependency and is not on an explicit allowlist where every
  entry carries a reason — and, since a declared check is not the same as a
  check that looked in the right place, it also fails any scoped route that
  reads its KB from somewhere the resolver does not inspect. A new unscoped
  route fails CI with instructions. Meta and admin surfaces (`/stats`,
  `/plugins*`, `/settings*`, `/repos*`, `/worktree*`, the remaining git-ops
  routes, MCP over HTTP) are allowlisted pending the same treatment; they
  are tier-guarded today but not per-KB scoped.

### Fixed

- REST field projections now preserve `id` and `kb_name` (Berkay Byte).

### Added

- **`pyrite --version` (also `-V`), which had never existed.** The CLI
  answered `Error: No such option: --version` for every release up to this
  one. The version itself was never wrong — `pyrite.__version__` reads from
  `pyproject.toml` with an installed-metadata fallback, and a test has pinned
  it since it drifted to `0.12.0` while `pyproject.toml` said `0.24.1`. That
  test covered the package attribute; nothing covered the command line, which
  is the surface a user meets first. Found by the release script's own
  release-layer step on its first real run, against a clean install from the
  release SHA.

- **`scripts/release.py`: a release is one command.** Six ordered steps, with
  every check in front of the first thing that cannot be undone —
  preconditions (clean `dev` at `origin/dev`; `origin` really being the repo
  the `gh` calls name; `vX.Y.Z` existing neither locally, on `origin`, nor as
  a GitHub release, and `origin/main` already an ancestor of the SHA, so the
  publish can only ever fast-forward; the version; a dated CHANGELOG section
  with content; the `release-blocker` label existing, with no open PR carrying
  it), the required CI checks green on that exact SHA (newest run per check
  name, so a rerun to green counts; `--wait-ci` waits, 15 minutes by default),
  the release layer verified *before* the tag exists (install from the SHA
  into a throwaway venv, `pyrite --version`, the getting-started tutorial run
  against that install, a Docker build when docker is present), then `main`,
  the tag and the GitHub release, then reopening `[Unreleased]` on its own
  branch for a PR to `dev`, then a handoff step naming what the release cannot
  do. `--dry-run` is the default and prints every command, writing nothing to
  disk; `--execute` is the only way anything is written. It never passes
  `--no-verify`, never force-pushes, never deletes a ref and never creates a
  label. A failure after the publish step began lists which commands already
  ran rather than claiming nothing was attempted.
  `scripts/run_tutorial.sh` gains `PYRITE_TUTORIAL_VENV` so the tutorial can
  be run against an arbitrary install rather than the checkout's.
- Repo-local configuration: a `.pyrite/config.yaml` in the current directory
  or any parent is used instead of `~/.pyrite` when no `PYRITE_CONFIG_DIR` /
  `PYRITE_DATA_DIR` is set, so a checkout (or a git worktree) can carry its own
  KB registry and index. `scripts/new-worktree.sh` creates one per worktree.
- **A smoke layer: every interface has an end-to-end test in CI.** `tests/e2e/`
  starts `pyrite-server` as a subprocess on a free port against a temp data
  dir and drives it the way a user's client does — REST (`POST /api/kbs`, then
  `POST /api/entries` into it with no restart, then `GET /api/search`), MCP
  over SSE (the advertised `event: endpoint` path, then initialize, tools/list
  and a `kb_search` call over that session), MCP over stdio (`pyrite mcp
  --tier read` as a subprocess speaking JSON-RPC, its tool list asserted equal
  to the SSE one), and embedding prewarm read from `/health` with no write
  issued. `scripts/run_tutorial.sh` runs `docs/getting-started.md` as a test:
  its bash blocks, in order, in one shell session in a temp HOME, against the
  installed package. Each of the three bugs an outside contributor found
  (PRs #3, #4, #5), reintroduced by hand, fails one of these tests while the
  existing 3316 pass. Marked `e2e` and excluded from the default run, so pull
  requests stay at ~3 minutes; a new `smoke` CI job runs it on the push to
  `dev` and on manual dispatch, and is deliberately not a required check
  (ADR-0032 §3a's breadth row).
- `auto_embed` setting (`PYRITE_AUTO_EMBED=0` to disable): embed entries on
  write, on by default. Off means keyword search only, no torch import and no
  model download on the write path; `pyrite index embed` backfills later. The
  first half of #13 (first write on a fresh install blocked on the download).
- `PyriteDB` is a context manager: `with PyriteDB(path) as db:` closes the
  connection on block exit (and on an exception), so callers no longer have to
  remember a manual `db.close()`.
- **The round-trip identity gate.** `tests/test_roundtrip_identity.py` loads
  every entry in a temp copy of the real `kb/` (774 files) through
  `KBRepository`, saves each back untouched, and asserts the bytes are
  byte-identical — the class of bug behind #46, #86 and #87 ("save wrote
  something load did not read") now fails the default suite instead of
  waiting for a human to notice a huge diff in review. Runs in ~1-2s.
  `tests/fixtures/roundtrip/` adds hand-built adversarial shapes (inline vs
  block lists, quoted scalars, a markdown table with `---` dividers, YAML
  anchors/merge keys, a missing trailing newline). 70 real-corpus ids are
  `xfail(strict=True)`, individually and by name, across five classified
  causes: 46 bare-string `links:` items (the residual #69 left, deferred —
  see the fix's own design notes), 11 block-indented `links:` sequences and
  1 `GenericEntry` metadata-duplication case (new findings, filed as #148 and
  #149), 6 files with pre-existing #87 `body:`-fold corruption already
  committed to `kb/` (#150, a data-cleanup issue, not a code bug), and 6
  daily notes normalized by `to_markdown`'s one-trailing-newline rule (not a
  bug). A sixth finding, `created_at`/`updated_at` silently dropped on save
  (issue #151), is pinned by two fixtures rather than a corpus id, since no
  real file uses those keys yet.

### Process

- CI parity: the `ruff check` / `ruff format --check` step now covers
  `extensions/` (54 pre-existing findings fixed: import sorting, unused
  imports/variables, a loop variable, two UP rules), matching the commit-stage
  hook that already lints it — a PR could otherwise go green with lint debt a
  local commit would have blocked. A new `pull_request`-only CI step runs
  `check_fix_commit_has_tests.py --range` over the PR's own commit range, so a
  `fix:` commit without a `tests/` change fails CI even for contributors who
  never ran the local commit-msg hook (all three outside PRs so far were
  fixes without tests). `tests/test_dev_process_config.py` pins both.
- The weekly retrospective: `pyrite-meta-conductor` now says what worked,
  root-causes every failure in its window and fixes what it finds as one
  process change plus one `quality` theme (refactoring, test refactoring) the
  conductor dispatches ahead of features once a week. The conductor leaves a
  tick log in `kb/notes/conductor-log-<week>.md`, files process friction as
  `process` issues as it happens, and stops at the release plan.
- Three skills replace one: `pyrite-dev` is the worker (one theme, one branch,
  one worktree, TDD, a report), `pyrite-conductor` picks work from GitHub
  issues and the roadmap, composes reviewable themes, dispatches a
  `pyrite-worker` agent per theme (Sonnet 5 or Opus 5 by shape of work),
  reviews branches with a `pyrite-reviewer` cold read for risky changes, opens
  and shepherds PRs, and keeps the repo healthy; `pyrite-meta-conductor`
  watches the conductor's loops for the constraint and proposes one measured
  change per run. A PR is one complete theme.
- ADR-0032 is in force: work happens on feature branches, each in its own
  worktree (`scripts/new-worktree.sh <branch>` sets one up with venv and
  hooks); `dev` takes pull requests only, checks green on top of current
  `dev`, no bypass; `main` fast-forwards to CI-verified commits; `v*` tags are
  immutable. Rebase is the default merge. ADR-0033: bugs and requests live in
  GitHub issues, the roadmap in `kb/`.
- The test runner is pinned exactly (`pytest==9.1.1`, `pytest-cov==7.1.0`,
  `pytest-xdist==3.8.0` in the `dev` extra), so every worktree venv and every
  CI leg run the identical version — an unbounded `pytest>=8.0.0` had
  resolved 9.1.1 on 3.12 and 9.0.2 on 3.13, and #81's fixtures passed the
  one-interpreter PR gate under 9.1.1 and broke `dev` on 3.13 under 9.0.2.
  `tests/test_dev_process_config.py` asserts the pins stay `==`. Ruff's `PT`
  (flake8-pytest-style) rule set is now enabled for `tests/` and fixed 14
  mechanical findings (fixture-parentheses, useless-yield, parametrize-tuple);
  the four remaining rule codes need per-call-site judgment and are ignored
  with reasons in `pyproject.toml`. Refresh a worktree venv after this with
  `uv pip install --python .venv/bin/python -e ".[all,dev]"`.

### Documentation

- Six false statements in agent-facing docs, found by a cold read and
  independently re-verified (#229): `CLAUDE.md` documented a `--title` flag
  `pyrite sw new-adr` does not have (title is positional; same fix applied
  to `.claude/skills/kb/SKILL.md` and `.claude/skills/software-kb/SKILL.md`,
  which repeated it) — **@Umar-2026 found and fixed the same `--title` bug
  independently in #237, fourteen minutes before this sweep merged**;
  `docs/json-contracts.md`'s `has_more`/`total` claims
  are replaced with a measured per-surface, per-transport table (`search`,
  `list_entries`, `recent`, `tags`, `backlinks` × CLI/MCP/REST — none of
  them uniform); the 29/11/8 MCP tool-tier counts in `docs/getting-started.md`,
  `README.md` and `pyrite mcp --help` undercounted by 2.4x by omitting 41+
  plugin tools exposed per tier — `pyrite mcp --help`'s counts are now
  generated from the live tool registry (true counts: read 70, write 103,
  admin 112) and the other two docs point to it instead of retyping a
  number; `docs/gemini-mcp-integration.md` and `docs/openai-mcp-integration.md`
  now name which binary (`pyrite mcp` vs `pyrite-server`) each tier default
  applies to, since the two disagree (`write` vs `read`); `kb_bulk_create`'s
  MCP tool description no longer claims best-effort per-entry semantics
  (#95: one malformed entry rejects the whole batch); `AGENTS.md` now says
  `pyrite orient`'s schema block has no field list for plugin-declared
  types (#232) instead of implying it always does.
- `kb/runbooks/setting-up-dev-environment.md` (#212): the troubleshooting
  runbook had drifted from CONTRIBUTING and could not run the suite —
  `pip install -e ".[dev]"` (no `fastapi`, no CLI deps, cannot collect
  tests) is now `.[all]`; the extension install is a `for ext in
  extensions/*/` loop instead of an enumerated list missing `cascade` and
  `journalism-investigation` and naming a nonexistent `task` extension; the
  hard-coded "1780+ tests" is replaced with the `--collect-only` command
  itself, per the counts-drift backlog item.
- CONTRIBUTING: the AI-assisted-contribution note now accepts a git
  `Co-authored-by:` trailer as the declaration (machine-readable, survives
  squash-merge) alongside the existing, still-welcome PR-body mention.
- CONTRIBUTING rewritten for a project with contributors: branch flow and
  required checks (ADR-0032), the three hook stages, where bugs vs roadmap
  items live (ADR-0033), a one-week first-response intent, how to run the
  suite. PR template asks for `Fixes #N` and the failing test; issue templates
  ask for version and install path and route security reports privately.
- README: plugin protocol method names corrected (`get_entry_types`,
  `get_cli_commands`, `get_hooks` incl. `before_index`), eleven built-in types,
  `pyrite/schema/` package, extension list, test and ADR counts, the install
  section shows the git-tag install and its no-web-UI caveat, MCP config uses
  an absolute path, first semantic search notes the model download.
  Contributors credited.
- CODE_OF_CONDUCT names a contact.

### Changed

- **`auto_embed: true` now guarantees that an entry *will be* embedded, not
  that it is embedded when the write returns (ADR-0035).** A write records the
  entry, makes it keyword-searchable immediately, and notes one `pending` row
  in `embed_queue`; it never imports torch and never touches the network.
  The debt is paid on paths that already have a caller willing to wait —
  `pyrite index embed` / `index sync` / `index build`, every `pyrite-server`
  startup, and `POST /api/index/sync?wait=true` — and
  `GET /api/index/embed-status` reports what is outstanding. **Semantic search
  is therefore eventually-consistent:** an entry written a moment ago may not
  be findable by meaning until a drain runs, and a semantic search against a
  KB with no embeddings now says so in `warnings` (naming `pyrite index
  embed`) instead of returning a silent empty list. No background thread is
  introduced (deliberately not copying #102's unjoined daemon thread).
  `auto_embed: false` is unchanged: nothing is enqueued and no embedding code
  is reached at all.
- **MCP reads are bounded by default, and the bound can no longer be
  defeated (ADR-0034 rules 1, 3, 4).** Two behaviour changes for clients
  passing large `body_limit`s: the per-body ceiling drops from **50,000 to
  20,000** characters (92% of measured bodies still fit in one call; the
  rest continue with `kb_read_body`), and multi-entry reads gain a
  **per-response budget of 40,000 body characters** — previously the
  ceiling was per body, so 50 entries at it was a megabyte. Bodies fill in
  request order; an entry reached after the budget is spent comes back in
  place with an empty body, `body_truncated: true` and its true
  `body_length`, never dropped and never reported `not_found`.

  All three numbers are now configuration read at server start —
  `PYRITE_BODY_CHUNK_DEFAULT` (8000), `PYRITE_BODY_CHUNK_MAX` (20000),
  `PYRITE_BODY_RESPONSE_BUDGET` (40000). An invalid value (non-integer,
  ≤ 0, or a default above the max) stops the server with a message naming
  the variable rather than silently falling back, and the MCP tool
  descriptions report the effective values instead of compiled-in ones.

- **A `fields` projection no longer skips body chunking** — the documented
  contract it replaces is retired (ADR-0034 rule 1: a parameter that
  reduces output never disables another bound). `kb_get` and
  `kb_batch_read` with `fields=[..., "body"]` and `body_limit=6000`
  returned 171,189 characters, 28× the explicit cap (#58); the same call
  now returns 12,369. A projection that keeps `body` also keeps
  `body_truncated`, `body_length`, `body_offset` and `body_chunk_size`, so
  a truncated body can still be recognised as one.

  The same hole was open on three read paths the issue did not name, and
  they are closed with it: `kb_search` returned whole bodies for both
  `include_body=true` and `fields=[..., "body"]` (it has no `body_limit`
  at all, so it is bounded at the default chunk within the response
  budget), and `kb_list_entries` and `kb_recent` returned whole bodies for
  up to 200 entries whether or not `fields` was passed.

- Three open process findings fixed: `.claude/THEME.md` is no longer tracked
  (it was gitignored but the already-committed blob kept riding every branch,
  risking add/add conflicts — #122); `scripts/verify-red.sh` now refuses
  (exit 2) when a reverted production file's top-level package resolves
  outside the worktree's interpreter, so a review worktree with a symlinked
  `.venv` can no longer report a suite number measured against the wrong
  checkout — #189; and the PR gate's `changes` classifier gained an `infra`
  output that widens the `test` job's matrix to all three interpreters when a
  PR touches test infrastructure (`conftest.py`, `pyproject.toml`,
  `.pre-commit-config.yaml`, `ci.yml`, `scripts/*`), so a change whose
  behaviour is a property of the interpreter — like #81's `@classmethod`
  fixtures — can't merge green on 3.12 and redden `dev` on 3.13 — #133.
- `web/` dependency bumps (supersedes Dependabot PRs #23-#29, one reviewable
  change): `@sveltejs/kit` 2.53.0→2.70.3 (security fixes — CSRF protection on
  non-production `NODE_ENV` builds, prototype pollution in file-input
  deletion, quadratic-backtracking DoS in `Accept` header negotiation, cookie
  size aligned to RFC 6265bis; also moves `defineEnvVars` to
  `@sveltejs/kit/env`), `@tiptap/core` 3.20.0→3.31.3 (fixes a `mergeAttributes()`
  prototype-pollution advisory and a ReDoS in Markdown attribute parsing;
  pulls `@tiptap/pm` and the prosemirror-* family along in lockstep, and drops
  now-unused transitive deps `linkify-it`/`markdown-it`/`@remirror/*`), vitest
  4.0.18→4.1.11 (with `@vitest/mocker` and the rest of the `@vitest/*`
  family), undici 7.22.0→7.29.1 and nanoid 3.3.11→3.3.19 (both transitive,
  under `jsdom` and `vite`→`postcss` respectively — no direct `package.json`
  entry), devalue 5.6.3→5.9.2 (transitive under `@sveltejs/kit`; also fixes a
  prototype-pollution advisory). `npm audit --omit=dev`: 12 vulnerabilities
  (2 low, 2 moderate, 8 high) before → 8 (4 low, 1 moderate, 3 high) after.
  No source changes required; build, 388 unit tests and `svelte-check`
  (449 files, 0 errors, the 23 pre-existing a11y warnings) all still pass.
- `web/e2e/collections.spec.ts` and `web/e2e/daily.spec.ts` (Package E of the
  Playwright determinism ticket) now assert on the seeded world instead of
  "a list or an empty state": the collection's membership is exactly the
  three seeded people, and `daily.spec.ts` only ever navigates within
  `SEEDED_DAILY_DATES` (auth is disabled in the e2e world, so `GET
  /daily/{date}` creates a note for a date that has none). Found two real
  product bugs in the process (`test.fixme`'d, not papered over): the root
  layout overwrites every route's `<title>` with the bare brand name
  (#49, pre-existing), and `Calendar.svelte`'s month-navigation buttons are
  inert wherever a `selectedDate` is set — an effect immediately snaps the
  view back (#89).
- `web/e2e/search.spec.ts` and `web/e2e/qa.spec.ts` (Package G of the
  Playwright determinism ticket) now assert on the seeded world instead of
  `.or(...)` "results or an empty state" dodges: a seeded query renders the
  seeded entry's title **as a result link** with the loading skeleton gone at
  that moment (`web/e2e/search.spec.ts:44`) — the assertion issue #9 needed to
  be closed, since the "N results" header alone can't distinguish a rendered
  list from one stuck on the skeleton. Removing the `.or()`/`if (count > 0)`
  dodges surfaced that the seeded world is not the zero-issue QA state the
  ticket assumed: `global-setup.ts` creates no links between entries (the
  same fact `graph.spec.ts` already asserts against `/api/graph`), so
  `qa_service.py`'s `orphan_entry` rule fires for every seeded entry, every
  run — `qa.spec.ts` now asserts that deterministic count instead of a
  clean-state dodge. `aria-label` added to the four unlabeled `<select>`s
  (search's KB and type filters, QA's severity filter) and `aria-pressed` to
  the search mode buttons, plus `data-testid` on the search skeleton, the
  search empty state, and the two QA stat cards — additive attributes only,
  no guard logic changed. The two `toHaveTitle` assertions are dropped
  (comment naming #49), following package F's precedent.
- The Playwright e2e suite now seeds and runs against its own KB in a private
  data directory (`web/e2e/global-setup.ts`, contract in `web/e2e/fixtures.ts`)
  with auth explicitly disabled and no server reuse, instead of whatever
  `~/.pyrite` happened to contain — which is why it had been failing
  differently on every run.
- 100 auth tests (`test_auth_endpoints`, `test_auth_service`,
  `test_github_token_storage`, `test_daily_endpoints`, `test_repo_endpoints`,
  `test_ai_quota_enforcement`) now run in CI. Each module carried a stale
  `pytest.importorskip("passlib")` although nothing imports passlib; a fresh
  install never has it, so CI had been reporting those six modules as "1
  skipped" each on every green run.
- The embedding model is loaded once per process and shared by every
  `EmbeddingService` instance (it was loaded per instance; the service is
  constructed in nine places).
- The test suite no longer loads the sentence-transformers model unless a test
  is marked `@pytest.mark.embeddings`. Every entry write in every test had been
  importing torch (~3 s) and calling the Hugging Face hub for metadata; under
  `-n auto` ten workers doing that at once thrashed the machine, and unrelated
  tests showed up at 50+ s.
- CI has one required check, `gate`, so a docs-only PR (matrix skipped) can
  merge; requiring matrix legs by name hung the first such PR.
- CI installs with `uv` (80-130 s of pip resolving per job → ~20 s). A change
  classifier skips the heavy jobs for docs/KB-only pushes and gives KB changes
  a 30 s schema check. Coverage runs in its own job, by manual dispatch only until there is a
  baseline worth enforcing.
  Pull requests test one interpreter (3.12); the push to `dev` after a merge
  runs the full Python matrix (ADR-0032 value chain). Playwright
  runs only by manual dispatch until it is deterministic.
- The test suite runs in parallel (`pytest -n auto`) at pre-push and in CI:
  ~22 min → ~3-5 min. The one xdist-unsafe test (the task-claim race) now uses
  a start barrier and a single group deadline instead of per-process timeouts,
  which also makes it a real race rather than a sequence under load.
- `social`, `zettelkasten` and `encyclopedia` relabelled as example plugins
  (README, `docs/plugins.md`, `docs/getting-started.md`, each extension's new
  `README.md`, `kb/components/*-extension.md`, `pyproject.toml`
  `description`): reference code showing how a Pyrite plugin adds entry
  types, CLI commands, MCP tools and a preset, not supported products.
  Wording only — no package name, entry point, module path, preset name,
  template name, CLI command name, MCP tool name or directory changed.
- CONTRIBUTING: how to claim an issue

### Fixed

- **The first write on a fresh install no longer blocks for over a minute
  downloading the embedding model (#13).** `KBService._auto_embed` took a
  synchronous branch whenever `self._embedding_worker` was unset — and nothing
  in production ever set it, so *every* write on *every* surface imported
  torch and fetched ~90 MB inside the request. Writes now enqueue (see
  ADR-0035 under Changed): measured on a live `pyrite-server` with an empty
  `HF_HOME` and the network blocked, `POST /api/entries` returns in
  milliseconds instead of failing a 2 s budget, and the entry is
  keyword-searchable at once.

- **Writing back a body that a bounded read had truncated silently destroyed
  the rest of the entry.** `kb_get`/`kb_batch_read` return at most the default
  chunk (8,000 characters, `PYRITE_BODY_CHUNK_DEFAULT`) of a long body plus
  `body_truncated: true`; nothing refused that body on the way back in, so an
  agent that edited what it received and saved it replaced a
  170,000-character entry with that fragment — silent, permanent, and produced
  by the safety feature itself. 21% of entries on the maintainer's index are
  long enough to be affected. Every write surface that can receive a
  body now refuses one carrying a truthy `body_truncated` marker, with
  `VALIDATION_FAILED`, `retryable: false` and a message naming `kb_read_body` /
  `body_offset` as the way to assemble the whole body first: MCP's write and
  admin tiers (`kb_create`, `kb_update`, `task_create`, `task_decompose` and
  any plugin write tool, guarded at the dispatcher), `kb_bulk_create`
  per-item, REST `POST`/`PUT`/`PATCH /api/entries` plus
  `POST /api/entries/import` per-record, and the CLI — `pyrite import`
  per-record (exiting 1, so a script cannot read "Imported N" off a run that
  dropped a truncated body), `pyrite create` and `pyrite update`. A write
  without the marker, a write carrying `body_truncated: false`, and a
  metadata-only update that carries the marker but no body are all unaffected.
  ADR-0034 rule 2; the CLI half closes #230.

  `pyrite create --body-file`/`--stdin` lifts a file's YAML frontmatter into
  the entry's fields, so a bounded read saved to a scratch file — the most
  likely way a marker gets persisted before being replayed — is refused too.
  `pyrite update --body-file` does not parse frontmatter and is unchanged: a
  marker there is body content, not a write argument.

  Importer audit: `json` and `markdown` carry the marker through to the
  caller, `yaml` and `csv` strip it through their key whitelists. Both import
  endpoints check every record regardless of format.

- **Extension entry classes silently dropped `aliases` and `_schema_version` on
  every load -> save round trip, and rewrote `importance` back to its default**
  — a `writeup` (social), `zettel`/`literature_note` (zettelkasten), or
  `article`/`talk_page` (encyclopedia) saved at `importance: 9` came back
  `importance: 5` on the next save, because each class's `from_frontmatter`
  hand-rolled its constructor call instead of routing through
  `Entry._base_kwargs`, and `extra_frontmatter` could not rescue the loss since
  all three keys are members of `_BASE_CONSUMED_KEYS`. All six classes now call
  `cls._base_kwargs(meta, body)`; the same hand-rolled-copy pattern in
  `cascade`, `journalism-investigation`, `software-kb` and
  `pyrite/models/task.py` is deleted in favour of the one shared
  implementation. A new registry-wide conformance test in
  `tests/test_frontmatter_round_trip_all_types.py` parametrizes over every
  registered entry type (core + every installed plugin) and pins the
  guarantee for future types automatically.
- **The REST `POST /entries/batch` endpoint now matches the MCP
  `kb_batch_read` contract.** A malformed spec returns a structured
  `VALIDATION_FAILED` (HTTP 400) naming `entries[i]` instead of a 500, a
  `fields` value that is not an array of strings gets the same
  `VALIDATION_FAILED` instead of a near-empty 200, and the `fields` projection
  always keeps `id` and `kb_name`, so `found` can no longer contradict
  `not_found` (#134).

- KBs removed from `config.yaml` become user-managed on the next registry
  sync, so `pyrite kb remove` no longer rejects them indefinitely. Existing
  index data and permissions are preserved; KBs still in the config remain
  protected (#19).
- **`kb_batch_read` no longer crashes on a malformed spec, and every `fields`
  projection keeps the identity pair.** A non-list `entries`, a non-object item,
  or a missing, empty or non-string `entry_id`/`kb_name` used to raise a raw
  `KeyError`, a `TypeError` or a SQL binding error that surfaced as
  `INTERNAL`/`retryable: true`; each now returns `VALIDATION_FAILED` with
  `retryable: false`, naming the offending `entries[i]`. `_project_fields` keeps
  `id` and `kb_name` in every projection (`kb_search`, `kb_get`,
  `kb_list_entries`, `kb_recent`, `kb_batch_read`), so the schema sentence is
  true of all five (#126, #137).
- **A bare YAML date in frontmatter (`created_at: 2026-01-15`) read back as
  the load time instead of the file's date.** The YAML parser produces a
  `datetime.date` for a bare date, and `parse_datetime` only handled
  `datetime`/`str`, so the value fell through to the "now" fallback. Bare
  dates are now anchored to midnight UTC; naive ISO-8601 strings and naive
  datetimes (an unquoted `created_at: 2026-01-15T09:00:00` loads as a naive
  `TimeStamp`) are anchored to UTC as well, so comparisons against `_utcnow()`
  cannot raise `TypeError`. Timestamps are indexed as strings, so an existing
  KB may hold both `2026-01-15T09:00:00` and `2026-01-15T09:00:00+00:00` for
  unchanged files until a full `pyrite index build` makes them uniform;
  ordering and date filters are unaffected ("+" sorts before digits) (#151).
- **Two worktrees running the Playwright e2e suite at once collided on the
  same four ports (8088/5173 base, 8189/5274 auth) and could end up talking
  to each other's world.** Ports and data directories are now derived per
  worktree from a stable hash of its path (`PLAYWRIGHT_E2E_PORT` /
  `PLAYWRIGHT_E2E_VITE_PORT` still override), `strictPort: true` on Vite
  defeats its silent fallback to the next free port, and a preflight in
  `global-setup.ts`/`auth-setup.ts` fails fast — naming the port and the
  owning process — if something outside this worktree already holds it.
  `scripts/new-worktree.sh` records the chosen ports in `.pyrite/e2e-ports`
  for a human running the suite by hand. Side effect worth knowing: `vite dev`
  (including plain `npm run dev`, not just the e2e suite) now also has
  `strictPort: true` — a taken 5173 is a startup error instead of silently
  moving to 5174, which is the same silent-fallback problem this fix exists
  to close, just visible outside the e2e path too.
- **Search filters were silently ignored in semantic and hybrid modes — and
  hybrid is the default.** `entry_type`, `tags`, `state`, `fips` and `status`
  were compiled only into the keyword leg's `WHERE`; the vector leg ran with
  `kb_name` alone and the two were fused, so a filtered search returned
  plausible-looking entries the filter excluded (`--type mechanism` returning
  themes; a bogus type returning a full result set instead of zero). Every
  filter is now applied on every leg in every mode: `SearchBackend.search_semantic`
  takes the keyword leg's filter set, and all backends implement it — SQLite
  escalates sqlite-vec's KNN budget so a selective filter costs no recall,
  Postgres puts the predicates in the same `WHERE` as the distance ordering.
  The backend conformance suite gained the semantic-filter cases. When a leg
  cannot honour a filter it is dropped rather than returning unfiltered rows,
  and the response carries a `warnings` array naming the filters responsible
  (`GET /api/search` and MCP `kb_search`; on stderr for the CLI) — absent, never
  null, when everything was applied, so a caller tests for the key. Whether a
  backend can filter its vector leg is a declared capability
  (`BackendCapability.FILTERED_SEMANTIC`), not a probe: an earlier draft
  inferred it from a `TypeError`, which turned any genuine bug inside the
  vector leg into a silently dropped one. The archived-entry exclusion counts
  as a filter and now holds on the vector leg too. `limit` is validated at the
  service boundary rather than failing as a `TypeError` or a SQLite error deep
  inside a leg. Two notes for operators: SQLite's KNN escalation is capped at
  sqlite-vec's hard ceiling of `k = 4096`, so on an index larger than that a
  filter selective enough to exclude the 4096 nearest neighbours under-returns
  on the vector leg (best-effort recall — the keyword leg has no such ceiling
  and carries hybrid); and the `kb_names` permission allowlist remains a Python
  post-filter (`SearchService._restrict`) rather than a backend predicate,
  unchanged by this work and correct, since it over-fetches before restricting.
  Fixes #56, #53.
- **The New Entry page's Create button could submit before the target KB was
  known.** `kbStore.activeKB` resolves asynchronously on mount; nothing
  disabled Create while it was still empty, so a fast click sent `kb: ''` and
  silently failed (a toast that dismisses in 3s, no navigation) instead of
  creating the entry. The button is now disabled until the KB has resolved,
  same as it already was while saving. Found while rewriting the e2e suite's
  entry-creation coverage against a real, seeded backend.
- **The login and registration forms no longer show the HTTP status to the
  user.** Both rendered `ApiError.message`, the developer-facing string, so a
  mistyped password read "API Error 401: Invalid username or password". They
  now render the server's own `detail`. Found by the new auth-enabled
  end-to-end project, where a real 401 is reachable.
- **The login path is covered end to end.** `web/e2e/auth.spec.ts` runs under
  its own Playwright project against a backend with auth enabled — its own
  data directory, port and dev server — so the gate redirect, the API's 401
  for an anonymous request, a real sign-in, and the redirect away from
  `/login` once signed in are all asserted. Under the auth-disabled world the
  other specs use, 8 of those 18 assertions are false, which is what the
  second world buys.

- `kb_link` now verifies that both endpoints exist before writing a link, so
  a misspelled or deleted target cannot create a dangling outlink. Missing
  source or target entries return a non-retryable `LINK_FAILED`. A caller that
  genuinely wants a forward reference passes `allow_dangling: true`, and the
  response carries `resolved` so it can tell a link that landed from one still
  waiting for its target. `pyrite link` on the CLI goes through the same
  service call and is now strict, with no flag to opt out;
  `pyrite links bulk-create` writes links through the model directly, so it is
  unaffected. The target is looked up in the index first and confirmed on
  disk, so an entry created but not yet indexed still validates. (#97)
- **Typed entries no longer drop frontmatter they do not declare.** A load ->
  save through any typed class (core or plugin) deleted unknown keys —
  `pyrite update -f status=done` stripped `milestone:` and `created:`. The
  base class now round-trips them; every one of the 48 registered types is
  tested. (#15)
- **`pyrite update` no longer rewrites frontmatter it was not asked to touch.**
  Updating one field (`--tags`, `--title`, `-b`) added `body:` (the whole body
  as a YAML string), `file_path:` (an absolute path), `importance: 5` and
  `rank: 0` to the file, and reordered and restyled every remaining key; six KB
  items were corrupted this way in one loop. The loader was injecting two model
  internals into the frontmatter dict that decides which keys are "unknown and
  must be preserved", so they were preserved into the file. A one-field update
  is now a one-line diff, keeping key order, quoting and `tags: [a, b]` flow
  style. The rule holds for every entry type, not just the two originally
  guarded by hand: 32 of the 48 registered types wrote some field at its
  default whatever the file said (`adr_number: 0` and `status:` onto an ADR,
  `maturity:` onto a zettel, `priority: medium` onto a backlog item), and the
  suppression is now applied once at the file-write boundary, so a plugin type
  gets it without declaring anything. Setting such a field to its default on
  purpose still writes it. (#46)
- **`pyrite index health` exits 1 when it reports unhealthy.** It printed
  `"status": "unhealthy"` and exited 0, so every script, CI step and agent
  gating on the exit code read a failure as success; the verdict was also
  skipped entirely on the `--format json` path everyone scripts against.
  `--no-fail` keeps the old behaviour, and new `-k/--kb` scopes every check to
  one KB so another KB's problems cannot decide this project's verdict. (#18)
- **`pyrite db backup` writes beside the index, not into the current
  directory.** The default path was a bare relative filename, so backups landed
  wherever the command was run; the repo root had accumulated 125 of them
  (58 MB), gitignored so nobody noticed. The default is now
  `<data dir>/backups/`; `--output` is unchanged. (#21)
- **Writes are validated.** `create` and `update` refuse a value the KB schema
  or a plugin validator rejects (`status=bogus`, `priority=9999`) and name the
  allowed values, on every surface; the file is not touched. Previously only
  `index health` noticed, afterwards. (#14)
- **Entry ids from any title.** Accents transliterate (`cafe-resume-naive`
  instead of `caf-r-sum-na-ve`), a title with nothing Latin in it gets a
  stable `entry-<hash>` id instead of failing with "Entry must have an ID",
  and ids are capped at 80 characters instead of an `OSError`. ASCII titles
  are unchanged. `sw new-adr` (CLI and MCP) uses the same function, so `/`
  and `:` no longer reach the filename. New entries end with one newline, so
  the end-of-file hook no longer rewrites every freshly created file. (#16, #17)
- Entry files are written atomically (temp file + `os.replace`), preserving the
  file's mode. A concurrent reader could previously see a truncated or empty
  entry while another process was saving it — two agents on one KB (claim vs
  reset, claim vs claim) hit exactly that path.
- Pre-push hooks: every non-pytest hook is pinned to the commit stage, so a
  push runs only the test suite (the file fixers had been running over the
  whole pushed range and aborted the v0.24.1 push of a CI-verified commit)

## [0.24.1] - 2026-09-17

Five months of work across about 250 commits (roughly 175 substantive, 75
KB/docs), from
2026-04-06 to 2026-09-17. Versions 0.21–0.24 were tagged without CHANGELOG
entries; this section covers everything since v0.24.0 and is the first
release note written since 0.20.0.

**Why 0.24.1 and not 0.25.0.** The roadmap defines 0.25 as "Field Hardening &
Shared-Instance Pilot," whose definition of done is *one peer, logged in,
searching the corpus read-only for two weeks with zero operator
interventions.* That has not happened: the epic stands at 7 of 17 subtasks,
with the entire web-UX workstream still open. This release is the accumulated
hardening work, not that milestone. Calling it 0.25.0 would mark a milestone
shipped whose defining goal was never attempted.

**First GitHub release.** None existed before this tag, so nothing was
pinnable and `publish.yml` had never fired.

**No PyPI wheel.** The `pyrite` name on PyPI is held by a pre-2FA account
that is locked. Install from a source checkout (README Quick Start), or
`pip install "pyrite[all] @ git+https://github.com/markramm/pyrite@v0.24.1"`.
The git install gives you the CLI, the REST API and the MCP server, **but no
web UI**: the built frontend is not packaged yet, and the server does not say
so (`installable-from-github-with-a-working-web-ui-package-the-built-frontend`).
For the UI, use a source checkout (`cd web && npm ci && npm run build`) or Docker.

**Frontend caveat.** The Playwright e2e job is currently non-blocking (see
`playwright-e2e-suite-non-deterministic-failures-likely-shared-state-auth-config-gap`),
so CI green means the backend is green. `web-search-results-never-render` is
open and describes the search page showing permanent skeletons for
anonymous/read sessions. Verify the web UI by hand before relying on it.

### Highlights

- **Error handling became a contract** — typed domain errors, a central REST
  exception handler, and a CLI-wide sweep converting every ad-hoc error site
  to a shared helper. Tracebacks no longer leak from CLI commands.
- **Fail-closed sweep** — auth tokens, plugin compatibility checks, index
  drift detection, and push failures stopped converting failure into false
  success at trust boundaries.
- **Index correctness** — read-back verification on rename, content-hash
  staleness detection, DB-registered KBs enumerated in every health loop,
  and warnings for undeclared types and missing `type:` frontmatter.
- **Plugin capability declarations** (ADR-0002 addendum) and **per-entity-type
  state machines** (ADR-0027).
- **KB-type-scoped entry-type resolution** — fixes a per-machine
  nondeterminism where the same code resolved `person` differently depending
  on site-packages enumeration order.
- **White-label branding** across site-cache, web frontend, KB export, and
  MCP prompts.
- **Three community contributions** — first outside PRs to the project.

### Added

- **Task workflow**
  - `pyrite task reset` releases stale claims back to `open`
  - `cancelled` terminal state for obsolete tasks
  - `--comment` flag records why a status transition happened
  - `--reason` / `status_reason` field for relaxed-mode transitions
  - `GET /api/tasks` and a human worklist board at `/tasks`
  - Per-entity-type `state_machine` config with `migrate-relaxed-mode` CLI (ADR-0027)
- **Search & index**
  - `--status` filter wired through CLI, service, all backends, and MCP
  - OR-relax on zero-hit keyword queries to fix brittle recall
  - Observability trace: mode, fallback reason, and latency logged per query
  - Stderr warning when the index is stale, naming the affected KBs
  - `pyrite qa coverage` curation statistics
  - `pyrite rename` for same-KB entry rename with wikilink rewrite
- **Plugins & backends**
  - `BackendCapability` enum with method-capability dispatch
  - Plugin capability declarations with dispatch-skip (ADR-0002 addendum)
  - `HookRunner` extracted as a peer service; `KBService` delegates to it
- **Branding & publication**
  - `BrandingService` with public `/config/branding` and `/branding/{file}`
  - White-label branding in web frontend, site-cache, KB export footer, MCP prompt
  - `sitemap.xml`, `robots.txt`, and complete SEO meta on entry pages
- **Quotas & AI**
  - Per-user LLM usage tracking (`llm_usage` table, REST endpoints)
  - `QuotaService.check_llm_quota`, wired into AI endpoints with tier resolution
  - Anthropic prompt-caching surface on `LLMService`
  - Configurable, visible embedding body truncation
- **CI**
  - Frontend and Playwright e2e jobs
  - pgvector-enabled postgres service exercising both backends
  - mypy strict-ratchet scaffold for `pyrite/storage/`
- **Web**
  - Entry comments panel and submit-for-review flow
  - `fips` and `state` as promoted entry columns with search filters

### Fixed

- **MCP tools that failed on every call** (found by a new smoke test that
  dispatches every registered tool)
  - `task_subtree`, `task_ancestors`, `task_blocked_by`, `task_critical_path`
    (`AttributeError`; also resolve the task's KB when `kb_name` is omitted)
  - `kb_manage` `discover`; zettelkasten's zettel listing (invalid FTS5 `*`
    query); journalism-investigation cross-KB search on hyphenated queries and
    investigation setup against a missing KB (raw `IntegrityError`)
- **Creating an entry whose id already exists silently replaced the existing
  entry** and reported "Created" — on every surface (CLI, REST, MCP, importers).
  Ids come from titles, so two entries with the same title destroyed the first.
  Create now refuses; use update to replace.
- MCP: refused requests (validation, not found, read-only) were reported as
  `INTERNAL, retryable: true`, inviting agents to retry calls that cannot succeed.
  They now return stable codes (`VALIDATION_FAILED`, `NOT_FOUND`, `READ_ONLY`, …)
  with `retryable: false`.
- `pyrite.__version__` reported `0.12.0`; it now reads the packaged version
- `LICENSE` was missing a clause of the MIT text and named no copyright holder
  (GitHub showed the license as "Other")
- `CONTRIBUTING.md` told contributors to install `.[dev]`, which cannot run the
  test suite; it now says `.[all]` plus the extensions
- **Index & storage**
  - New entries of plugin types (e.g. `backlog_item`) were filed under the
    parent core type's directory (`notes/`) when `kb.yaml` declared the type
    without a `subdirectory`; the owning plugin's preset default now applies
  - `index sync` silently skipping modified files
  - Frontmatter delimiter matching inside quoted values; wikilink extractor
    counting code fences and path-like targets as broken links
  - Read-back index verification on rename, hard error on drift
  - Content-hash staleness detection in `check_health()`
  - DB-registered (`kb add`) KBs now indexed by `sync`/`build` and enumerated
    in staleness/health/stats/edge-type loops (#2)
  - Metadata clobbering on partial update; metadata threaded through REST
  - Deliberate subdirectory preserved on update instead of relocating to the
    type default
  - `EventEntry` serializes `actors`, not `participants`
  - `TaskEntry` preserves unknown top-level frontmatter keys across
    load → save. `task claim` / `task update -s` were silently stripping
    conventions like `parked_awaiting:`, turning parked monitors into
    apparently stalled work. `NoteEntry` and `CollectionEntry` still drop
    them (`core-types-silently-drop-unknown-frontmatter-keys`, open)
- **Fail-closed / error handling**
  - Auth fails closed on undecryptable GitHub tokens and API keys
  - Plugin KB-type compatibility check fails closed
  - Real push failures no longer masked as "No remote configured"
  - Invalid-status drift detector and references-extraction fallback now warn
    instead of degrading silently
  - FTS5 syntax errors classified as `QUERY_SYNTAX`, not `INTERNAL`
  - FTS5 terms quoted in `links suggest`/`discover` (fixed `links orphans` crash)
  - Malformed frontmatter, illegal task transitions, and undeclared types
    surface as clean errors rather than tracebacks
  - Malformed files collected as a sync summary instead of traceback spew
- **Type resolution**
  - Entry-type resolution scoped by KB type; most-derived-class tiebreak.
    Previously the first plugin subclass in discovery order won, so `person`
    resolved to `actor` or `user_profile` depending on the host's
    site-packages enumeration (`plugin-type-resolution-scoping` item 1)
- **Auth & web**
  - OAuth CSRF state moved from an in-memory dict to the DB (survives restart)
  - KB store loads after auth init, preventing 401s on protected instances
  - Entry page scroll broken by nested overflow containers
  - Search crash on entries sharing an ID across KBs
  - Checkbox field widget on the new-entry form
  - Daily-notes navigation no longer performs a write for read-tier users
- **Concurrency & tests**
  - `IndexWorker` thread-leak flake root-caused
  - `GitService` subprocess env isolated from a parent git process
  - N-process concurrency race test for the task-claim CAS

### Changed

- CLI error sites converted to a shared `cli_error` helper across all command
  modules; `task status` renamed to `task get`
- `pyrite mcp --tier` flag implemented (previously documented but absent)
- Static renderer path deprecated in favor of the site cache
- `--include-body` contract locked; stdout stays pure JSON in `-f json` mode
- Backlog status vocabulary normalized and enforced at index time

### Security

**If you run `pyrite-server` with a GitHub token configured, or expose it to
more than one user, upgrade.**

- **GitHub token disclosure.** Repo URLs were matched against `github.com` as a
  substring, so `https://evil.example/github.com/a/b` was treated as a GitHub
  repo and the server's token was sent to that host. Reachable by a write-tier
  caller through `POST /repos/subscribe`. The host is now parsed and compared
  for equality; userinfo and non-https schemes are refused; owner and repo
  names are validated.
- **Git argument injection.** `clone` and `git add` passed caller-supplied
  values without `--`, so a value starting with `-` was read as a git option
  (`--upload-pack=<cmd>` executes). Both now use `--` and refuse option-shaped
  input.
- **Mutating routes reachable at read tier.** `POST /kbs/{kb}/export` (clone and
  push a whole KB to a caller-chosen URL), `POST /collections` and
  `POST /ai/test` had no tier guard; all three now require write tier. A new
  test calls every mutating `/api` route with a read-tier key and requires 403,
  so an unguarded route fails CI.
- **Stored XSS in the web UI.** Rendered markdown went into the page
  unsanitized on the entry page and in daily notes, and an entry title could
  break out of the JSON-LD `<script>` block. Rendered HTML now passes through
  DOMPurify; `<` is escaped in JSON-LD.
- **Path traversal through entry ids.** An entry id becomes a filename, and the
  REST import endpoint took `id` from the uploaded file unchecked, so a write-tier
  caller could write a `.md` file outside the KB directory (`../../x`);
  `pyrite rename` had the same hole locally. The repository now refuses ids that
  are not plain filenames and refuses any path that resolves outside the KB root.
- Web clipper SSRF defense: private, loopback, and link-local IPs blocked

### Community contributions

First outside PRs to the project, all from **Ruslan Terekhov (@AsyncLegs)**:

- **#3** — MCP SSE endpoint: pinned `mcp>=1.0.0,<2.0.0` (a fresh install was
  resolving 2.2.0, two majors past the 1.x `Server` API `build_sdk_server()`
  uses) and fixed a doubled `/mcp/mcp/messages/` path where the SSE transport
  was constructed with an endpoint that already included its mount prefix
- **#4** — KB created via `POST /api/kbs` was invisible to entry creation
  until restart: `add_kb()` wrote to the DB without updating the in-process
  `_db_kb_cache`
- **#5** — `EmbeddingService.prewarm()` was never called despite
  `PYRITE_PREWARM_EMBEDDINGS=true`; `/health`'s `embeddings.ready` stayed
  permanently false

### Known gaps at this release

- The former `[Unreleased]` section is now retitled `[0.21.0 – 0.24.0]`: its
  contents all date to 2026-03-23 → 2026-03-26 and shipped in those tags,
  which were cut without CHANGELOG entries. It is not split per-tag, because
  the four versions were cut within days of each other and the log does not
  cleanly attribute features to individual tags.
- `web/package.json` is stranded at `0.20.0`, four minors behind the Python
  package. Whether the frontend versions independently is an open question
  under ADR-0031 (`pyrite-core-ui` as an addressable package), so it was not
  bumped blindly here.
- Stale PyPI claims remain in `kb/designs/launch-staging.md:32` (ticked
  `[x] pip install pyrite works`), `launch-channels.md`, and
  `bhag-self-configuring-knowledge-infrastructure.md`. They describe a path
  that the locked account makes unreachable.

## [0.21.0 – 0.24.0] - 2026-03-23 → 2026-04-06

Reconciled 2026-09-17. This content was written by `ab0707c` ("Document all
post-0.20.0 work in CHANGELOG", 2026-03-26) and sat under `[Unreleased]`
because 0.21.0 through 0.24.0 were tagged without CHANGELOG entries. Every
item below dates to 2026-03-23 → 2026-03-26 and therefore shipped in those
tags; it was never pending work.

Not split per-tag: the four versions were cut within days of each other and
the commit log does not cleanly attribute these features to individual tags.

**Note on the `/site/` cache:** this section records *building* it. The
0.24.1 cycle *deprecated* the static renderer path in favour of custom Hugo
sites (`12d5660`, `3d1d1e1`, 2026-04-23). Both are accurate; they are
different events five months apart.

### Added

- **Static Site Rendering (`/site/`)**
  - Python-served static HTML cache for SEO-friendly KB pages (replaces earlier Node SSR approach)
  - Sitemap.xml generation from cached pages with per-entry lastmod dates
  - robots.txt with crawler directives
  - JSON-LD structured data, Open Graph meta tags, canonical URLs on every page
  - Custom homepage support via `_homepage` KB entries with designed template rendering
  - Progressive JS enhancements: live search widget, auto-generated TOC, heading anchors, back-to-top
  - Editorial dark theme with Source Serif 4 body + DM Sans headings
  - `/site/search` page with live API-backed hybrid search and URL state sync
  - Cache invalidation per-entry and per-KB, auto-render on index sync

- **Web UI Feature Parity (Phase 4-5)**
  - KB orientation page with type breakdown, recent changes, and tag cloud
  - Advanced search filters: date range, tag filter, saved searches with localStorage
  - Daily notes calendar widget
  - User management: list users, role editing, per-KB permission grants/revokes
  - Index management: sync, rebuild, health check, embedding status in settings
  - Entry creation with full metadata fields (type, tags, date, importance, status)
  - Graph centrality sizing (betweenness centrality from API)
  - Review & Publish workflow: pending changes view with entry-level diffs and commit dialog
  - KB landing page at `/` with directory of knowledge bases
  - Dashboard moved to `/overview`

- **Search Improvements**
  - `group_by_kb` and `limit_per_kb` query params for cross-KB result diversity
  - Prevents large-KB dominance by returning top N results per KB with round-robin interleaving

- **Multi-Site Deployment**
  - Shared Docker network (`pyrite-shared`) for multiple Pyrite instances on one VPS
  - Caddy routing for multiple domains (demo.pyrite.wiki + capturecascade.org)
  - Independent container lifecycle per site

- **Export System**
  - NotebookLM renderer with source bundling and manifest generation
  - Quartz static site renderer for KB publishing
  - CLI `export` command group with collection and site subcommands

### Fixed

- **Security**
  - Fix 6 XSS vulnerabilities in site cache (title escaping, search widget, markdown links, ChatSidebar, search highlight)
  - Fix YAML frontmatter injection in export service (string interpolation → proper quoting)
  - Fix path traversal via entry IDs used as filenames (new `sanitize_filename()` utility)
  - Block javascript:/data:/vbscript: URLs in markdown link rendering
  - Add single-quote escaping to HTML `_esc()` function
  - Set `no-cache` on SPA index.html to prevent stale chunk hash errors after deploy

- **Bugs**
  - Fix graph KB filter SQL precedence: `WHERE (A OR B) AND C` not `WHERE A OR (B AND C)`
  - Fix Sidebar.svelte `$derived` value called as function (`{userInitials()}` → `{userInitials}`)
  - Fix QuickSwitcher full page reload (window.location.href → goto())
  - Fix anonymous access when auth not configured (anonymous_tier None handling)
  - Fix editor blank content when switching to edit mode
  - Fix layout clipping issues (flex-col, min-h-0, overflow)
  - Fix graph page zero-height container

- **Performance**
  - Site cache render_all() runs in background thread (asyncio.to_thread) instead of blocking event loop
  - Reduce N+1 queries in site cache: eliminate redundant list_entries and get_entry calls

### Changed

- Decouple `/site` and `/viewer` routes from SPA dist (work without SvelteKit build)
- Update README: MCP tools 14/6/4 → 23/11/8, extension points 15 → 19, tests 1468 → ~2500, ADRs 16 → 22
- Replace `task` with `journalism-investigation` in extensions table

## [0.20.0] - 2026-03-23

First public beta release. This release consolidates 8 milestones of development (0.10–0.18) into a single distributable package with comprehensive documentation, deployment options, and a hardened web UI.

### Highlights

- **GitHub OAuth & per-KB permissions** — multi-user access control with read/write/admin tiers
- **Docker & one-click deploy** — Dockerfile, Docker Compose, Railway, Render, and Fly.io deploy buttons
- **Web UI hardening** — 14 UX fixes, accessibility audit, Playwright E2E tests, mobile responsive
- **Agent DX overhaul** — 8 MCP tool improvements, structured error responses, batch operations
- **Architecture refactors** — KBService decomposed into 4 focused services, schema module split into 6 submodules
- **Two domain plugins** — software-kb and journalism-investigation prove the platform is general-purpose
- **Edge entities** — typed relationships as first-class entities with endpoint schemas
- **Export system** — NotebookLM and Quartz static site renderers

### Added

- **Authentication & Access Control**
  - GitHub OAuth sign-in (`oauth-providers` Phase 1)
  - Per-KB read/write/admin permissions with ephemeral KB sandboxes
  - MCP per-client per-tier rate limiting

- **Deployment**
  - Multi-stage Dockerfile and Docker Compose configuration
  - One-click deploy buttons for Railway, Render, and Fly.io
  - Self-hosted deployment scripts with Caddy reverse proxy
  - Demo site deployment tooling

- **Web UI**
  - Logout button, version history fix, type color consolidation
  - Browser tab page titles, loading state standardization
  - Accessibility fixes (aria-labels, keyboard navigation, screen reader support)
  - Mobile responsive viewport fixes
  - Collection view persistence, first-run onboarding experience
  - Starred entries restoration, dead code cleanup
  - Alpha banner with feedback button and error reporting links
  - Comprehensive Playwright E2E test suite (search, collections, QA, settings, daily, entry CRUD, auth)

- **Agent Developer Experience (MCP + CLI)**
  - `kb_batch_read` — multi-entry retrieval in one call
  - `kb_list_entries` — lightweight KB index browsing
  - `kb_recent` — orientation queries for what changed recently
  - Search `fields` parameter for token-efficient results across CLI, MCP, and REST
  - Smart field routing: top-level vs metadata field mapping clarified
  - Structured JSON error responses with `suggestion` field across all surfaces
  - MCP body chunking with auto-truncation and `kb_read_body` for large entries

- **Architecture**
  - `SearchBackend` protocol — 13-method structural protocol for pluggable storage
  - `SQLiteBackend` — wraps PyriteDB + FTS5 + sqlite-vec (default)
  - `PostgresBackend` — tsvector FTS + pgvector embeddings for server deployments
  - KBService decomposed into `GraphService`, `EphemeralKBService`, `QuotaService`, `ExportService`
  - Schema module split into 6 focused submodules (`enums`, `validators`, `provenance`, `field_schema`, `kb_schema`, `core_types`)
  - `DocumentManager` for write-path coordination
  - Entry protocol mixins for composable field patterns (ADR-0017)
  - Edge entities — typed relationships as first-class entries with endpoint schemas (ADR-0022)
  - Dynamic subdirectory paths with template variables (`{status}`, `{type}`)

- **Export System**
  - `pyrite export collection` — export entries for NotebookLM with bundling and source redaction
  - `pyrite export site` — export KB as Quartz static site for GitHub Pages
  - Quartz renderer with wikilink normalization, frontmatter mapping, project scaffolding

- **KB Quality & Lifecycle**
  - `pyrite schema validate` — frontmatter validation with ID collision detection
  - `pyrite ci` — CI/CD schema and link validation command
  - `pyrite qa fix` — auto-fix safe structural issues
  - `pyrite qa gaps` — structural coverage analysis
  - `pyrite links check` — cross-KB broken link validation
  - `pyrite links suggest` — FTS5-based link suggestions
  - `pyrite links bulk-create` — batch link creation
  - `pyrite db backup` / `pyrite db restore` — database backup and restore
  - `pyrite kb compact` — detect archival candidates with type-aware staleness
  - Entry `lifecycle` field with archive-aware search filtering
  - Intent layer: guidelines, goals, rubrics, deterministic and LLM-assisted evaluation
  - Named rubric checkers with explicit binding and CLI discoverability
  - Source URL liveness checking for QA

- **Agent Workflow (Kanban for Agent Teams)**
  - Milestone entry type with board configuration (`board.yaml`)
  - Review workflow with DoR/DoD quality gates
  - `sw_pull_next`, `sw_claim`, `sw_submit`, `sw_review`, `sw_log` MCP tools
  - `sw_context_for_item` for pulling work context
  - Work session logging with `WorkLogEntry`

- **Plugins**
  - `software-kb` plugin: ADRs, components, backlog items, standards, runbooks, kanban workflow
  - `journalism-investigation` plugin: persons, organizations, events, claims, evidence, sources with reliability tiers, ownership chains, money flow tracking, FtM interop, cross-KB entity correlation
  - `cascade` plugin: timeline events, actors, capture lanes, static JSON export for viewer consumption
  - Plugin preset registration for `pyrite init --template`
  - Init templates: `research`, `software`, `zettelkasten`, `intellectual-biography`, `movement`, `empty`

- **Documentation**
  - Getting Started tutorial
  - Plugin writing tutorial
  - OpenAI / Codex MCP integration guide
  - Gemini CLI / Antigravity MCP integration guide
  - Awesome plugins directory page

- **Infrastructure**
  - Async/queue-based index rebuild with background thread worker
  - Embedding service pre-warming to reduce cold-start latency
  - Import cycle detection guard
  - Plugin discovery strict mode (surfaces load failures during development)
  - Plugin hook atomicity (transactional wrapping for before_save hooks)
  - Bulk import CLI with `--body-file` and `--stdin` support

### Changed
- Default CLI output format changed to JSON for agent-friendly consumption
- Priority field changed from Integer to String across storage and protocols
- API module-level singletons replaced with `app.state` for test isolation
- Plugin registry deduplication on reload
- Factory pattern refactored to open/closed principle
- Incremental link sync (diff-based instead of delete-all/insert-all)
- LanceDB backend evaluated and rejected (49-66x slower indexing — see ADR-0016)

### Fixed
- Entry ID collisions across types (explicit `id` fields added)
- MCP `kb_create` placing entries at KB root instead of type directory
- MCP `kb_update` returning PosixPath serialization errors
- `sw adrs` reading date from metadata instead of DB column
- `sw_*` MCP tools reading status from metadata JSON instead of DB column
- Template filename filter dropping legitimate KB entries
- Duplicate tags and duplicate entry IDs during index sync
- Test suite clobbering `~/.pyrite/config.yaml`
- Collection type safety and endpoint hardening
- `str(None)` safety across enum validation

## [0.12.0] - 2026-03-01

### Added
- **PyPI publishing** — `pip install pyrite` and `pip install pyrite-mcp` now work
- **GitHub Actions publish workflow** — automated PyPI release on GitHub Release creation
- **MANIFEST.in** — controls sdist contents, excludes tests/extensions/web/kb
- **Schema Migration System** (`storage/migrations.py`)
  - Version tracking via `schema_version` table
  - Forward and rollback migration support
  - Auto-migration on database initialization
- **Service Layer** (`services/`)
  - `KBService` for KB operations (CRUD, indexing)
  - `SearchService` for search with FTS5 query sanitization
- **Pre-commit Hooks** (`.pre-commit-config.yaml`)
  - Ruff linting and formatting
  - Basic file checks (trailing whitespace, YAML validation)
  - Pytest quick check on commit
- **GitHub Actions CI** (`.github/workflows/ci.yml`)
  - Python 3.11/3.12/3.13 matrix testing
  - Ruff lint + format, mypy type checking
  - Separate job for full test suite with optional deps
- **Open Source Governance**
  - `CODE_OF_CONDUCT.md` (Contributor Covenant v2.1)
  - `SECURITY.md` (vulnerability reporting policy)
  - GitHub issue templates and PR template
- **SvelteKit Web UI** (`web/`)
  - Entry browser, search, graph visualization
  - Entry editor with live markdown preview
- **Documentation**
  - `CONTRIBUTING.md` with development setup and PR workflow
  - `CHANGELOG.md`, `UPSTREAM_CHANGES.md`

### Changed
- Version bumped from 0.3.0 to 0.12.0
- Python 3.13 classifier added
- Package find excludes `pyrite-mcp/` directory
- Fixed FTS5 query sanitization for hyphenated terms
- Fixed deprecation warnings (`datetime.utcnow()` → `datetime.now(timezone.utc)`)
- Fixed sqlite3 date/datetime adapter warnings for Python 3.12+

### Removed
- Legacy `mcp_server.py` and `setup_mcp.py` root scripts
- Legacy test files importing old `zettelkasten_assistant` package
- Stale `zettelkasten_assistant` references from docs and pyproject.toml

## [0.2.0] - 2025-02-21

### Added
- **Web UI** (`ui/`)
  - Streamlit-based interface with search, timeline, actors pages
  - Entry detail view with links and sources
  - Cached data layer for performance

- **REST API** (`server/api.py`)
  - FastAPI server with OpenAPI documentation
  - Full CRUD endpoints for entries
  - Search, timeline, tags, actors endpoints
  - CORS support for web frontends

- **Agent-Optimized CLIs**
  - `crk-read`: Read-only CLI for AI agents
  - `crk`: Full-access CLI for researchers
  - JSON output format with structured errors
  - Semantic exit codes

- **Claude Code Integration**
  - `.claude/skills/kb/skill.md` for Claude Code discoverability
  - MCP server for Model Context Protocol

### Changed
- Entry points consolidated: `crk`, `crk-read`, `crk-server`, `crk-ui`

## [0.1.0] - 2025-01-15

### Added
- **Multi-KB Architecture**
  - Support for multiple knowledge bases with different types
  - Events KB for timeline entries
  - Research KB for actors, organizations, themes

- **SQLite FTS5 Storage**
  - Full-text search with BM25 ranking
  - Tag and actor indexing
  - Link/relationship storage

- **Entry Models**
  - `EventEntry` for timeline events
  - `ResearchEntry` for research documents
  - YAML frontmatter parsing

- **GitHub OAuth**
  - Private repository access for collaborative research

- **Typer CLI**
  - Rich command-line interface (`pyrite`)

## [0.0.1] - 2024-12-01

### Added
- Initial fork from joshylchen/zettelkasten
- Basic project structure

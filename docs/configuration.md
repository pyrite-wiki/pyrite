# Configuration

Pyrite reads `~/.pyrite/config.yaml` (or `$PYRITE_CONFIG_DIR/config.yaml`), then
applies `PYRITE_*` environment variables on top. An environment variable always
wins when it is set. Nothing here is required: a fresh install works with no
config file at all.

## Where things live

| Variable | Default | Meaning |
|---|---|---|
| `PYRITE_CONFIG_DIR` | `~/.pyrite` | Directory holding `config.yaml`. When unset, a `.pyrite/config.yaml` found in the current directory or any parent is used instead of `~/.pyrite` — a repo-local registry, so a checkout's `kb/` resolves to that checkout. The index and cloned repos default to this directory too (see below). |
| `PYRITE_DATA_DIR` | `~/.pyrite` | Directory for the index (`index.db`) and cloned repos (`repos/`); overrides `settings.index_path` in `config.yaml`. When set it is also where `config.yaml` is read from, ahead of `PYRITE_CONFIG_DIR`. Set this in containers and point a volume at it. |
| `PYRITE_STATIC_DIR` | `<checkout>/web/dist` | Built web UI to serve at `/`. Needed when the package is installed into site-packages rather than run from a checkout. |
| `PYRITE_BRANDING_DIR` | built-in | Folder of white-label branding assets (see `deploy/branding-examples/`) |

**Which directory holds `config.yaml`**, first match wins:

1. `PYRITE_DATA_DIR`
2. `PYRITE_CONFIG_DIR`
3. a `.pyrite/config.yaml` in the current directory or a parent
4. `~/.pyrite`

**A repo-local `.pyrite/config.yaml` (3) is not trusted.** It may have come with
a cloned or downloaded tree, so Pyrite reads only what stays inside that tree:
`knowledge_bases` whose paths resolve inside it (keys `name`, `path`,
`kb_type`, `description`, `read_only`, `shortname`, and `default_role` only
when it is `none`), and `settings.index_path` (inside it), `auto_embed`,
`search_mode` and `summary_length`. (`workspace_path` is never read from any
`config.yaml`.) KBs registered in that tree's index are held to the same rule:
one whose path is outside the tree is not loaded, `pyrite kb add` refuses a
path outside the tree before creating anything, and a `default_role` stored in
the tree's index is ignored unless it is `none`. Under such a config an admin
cannot publish a KB either (`default_role` `read` or `write` is refused):
publishing needs a trusted config. Note that the KB listing (`GET /api/kbs`,
`pyrite kb list`) still shows the `default_role` stored in the index, while
every access decision ignores it. An untrusted config with no `index_path`, or
one whose `index_path` is refused, uses `.pyrite/index.db` inside its own
tree, never your own index; an index your own config publishes KBs from is
only ever used through a trusted config (`~/.pyrite` or `PYRITE_CONFIG_DIR`). Anything else (the
embedding model, editor, AI and server settings, auth, API keys, other
`default_role` values, repositories, subscriptions) is ignored with a warning,
and GitHub credentials are never read from or written to it. When Pyrite saves
such a config (`pyrite kb add` in that tree), it writes back only the KB
registry and the file's own allowed settings -- never credentials or values
that came from the environment -- so any other key in the file is dropped from
it on save. To use a directory's config in full, point `PYRITE_CONFIG_DIR`
at it. The configs `scripts/new-worktree.sh` writes need nothing more.

A local embedding model is named by an absolute path in your own config. A
bare model name that is also a directory under the working directory is
refused, and a model directory whose `modules.json` names code outside
sentence-transformers is not loaded.

**Where the index goes**, first match wins:

1. `$PYRITE_DATA_DIR/index.db`
2. `settings.index_path` in `config.yaml`
3. `index.db` beside the `config.yaml` chosen above — `~/.pyrite/index.db` for a
   default install, `$PYRITE_CONFIG_DIR/index.db` when that is set

Cloned repos follow the same order (`$PYRITE_DATA_DIR/repos`, then `repos/`
beside `config.yaml`). In 0.25.2 and earlier, setting only `PYRITE_CONFIG_DIR` left the
index at `~/.pyrite/index.db`, so a sandboxed run still wrote your real index.

`workspace_path` is neither read from nor written to `config.yaml`, and
`index_path` is written only once Pyrite saves the file. So a hand-written
config under `PYRITE_CONFIG_DIR` or a repo-local `.pyrite/` with no
`index_path` uses that directory for both. To keep the index where it was, add
`index_path: ~/.pyrite/index.db` under `settings:`; otherwise run
`pyrite index sync` to rebuild it in the new place. Clones subscribed under
`~/.pyrite/repos` can be moved into `<config dir>/repos/`.
(`PYRITE_DATA_DIR=~/.pyrite` would keep both, but it also makes `~/.pyrite` the
config directory, above.)

**Pyrite never drops a knowledge base you did not remove.** A save that would
drop a KB `config.yaml` lists fails. The CLI prints one error line naming the
file and the KBs; the REST API returns a generic 409 and logs the details.
The exceptions are commands that remove KBs (`kb remove`, `repo remove`,
ephemeral expiry, unsubscribe), which name what they remove and check before
deleting anything. A `config.yaml` that cannot be parsed is not overwritten
either. Code that means to drop KBs passes `save_config(config, removed=[...])`
or `allow_drop=True`. A write through a symlinked `config.yaml` logs the real
file it changed.

The refusal says which case it is. If `config.yaml` changed since the process
loaded it (for example `pyrite-admin kb add` from a shell while a server runs),
restart the server, or re-run the command, so it reads the current file. If
`config.yaml` cannot be read (it does not parse, is not a mapping, or has an
entry with no name), fix or move it: restarting would only fail to load it.
An entry with a name but no `path` is reported as the first case, though a
restart will fail to load that file too (#405).

Known limits: only the list of knowledge bases is protected. A save from a
process whose config is out of date still overwrites `repositories:` and
`settings` with its own copy, and can bring back a KB another process removed.
There is no lock between two processes saving at the same moment, and a
repository subscribe that is refused at its final save is not rolled back
(unsubscribe it and retry).

The write is not atomic: a crash mid-write can leave a truncated
`config.yaml`. The next save refuses to overwrite it when it cannot be
parsed, and in a few other cases, but most truncations still parse --
including the empty file a crash right after the file is opened leaves -- and
then read as a shorter list of KBs, or none. A command that loads such a file
usually sees only those KBs, with no error (some fail to load with a raw
error instead), and its next save writes the shorter list. A process that
loaded the file before the crash (a running server) usually writes its full
list back on its next save. Keep
a copy of `config.yaml` to restore from.

## Server

| Variable | Default | Meaning |
|---|---|---|
| `PYRITE_HOST` | `127.0.0.1` | Bind address. Containers need `0.0.0.0`. |
| `PYRITE_PORT` | `8088` | Port |
| `PYRITE_CORS_ORIGINS` | localhost dev ports | Comma-separated allowed origins |
| `PYRITE_ALLOWED_HOSTS` | unset | Comma-separated extra hostnames a credential-free server answers (see below) |
| `PYRITE_API_KEY` | unset | Single admin API key (legacy single-key mode). Prefer `api_keys` in `config.yaml` — hashed keys with a role each. |

`config.yaml`:

```yaml
settings:
  host: 127.0.0.1
  port: 8088
  api_keys:
    - key_hash: "<sha256 of the key>"
      role: read          # read | write | admin
      label: "Reader"
```

### Allowed hosts and origins (credential-free servers)

These rules apply when a request can change something without a credential:

- auth is disabled and no `api_key` or `api_keys` are configured, so every
  request is admin; or
- auth is enabled with `anonymous_tier: write`, so anonymous visitors can
  write.

In API-key mode (auth disabled, keys configured), a request needs a key. With
auth enabled and no anonymous writes, a request that changes anything needs
a session. Neither mode is affected by these rules, except that the
`Origin` rule below applies in **every** mode to a state-changing request
signed in with the session cookie: a browser attaches that cookie to a form
post from a same-site page (another port on the same host, a sibling
subdomain), so the cookie alone does not show the user meant the request.
A request authenticated with an API key in the `X-API-Key` header is not
affected. In the two credential-free modes the server trusts
the browser less:

- It answers only requests addressed to `localhost`, `127.0.0.1` or `[::1]`,
  to the bind `host` when that is not a wildcard (`0.0.0.0`, `::`), and to any
  name in `allowed_hosts`. A request for any other `Host` gets
  `421 Misdirected Request` and does nothing.
- A state-changing request (`POST`, `PUT`, `PATCH`, `DELETE`) from a browser
  must come from the server's own origin or one listed in `cors_origins`.
  Otherwise it gets `403`. Requests that send no `Origin` or `Referer`, such as
  the CLI, `curl` and agents, are not affected, unless a browser's
  `Sec-Fetch-Site` header says the request came from another site.

These rules cover the whole app, including `/mcp`, `/ws` and `/site`. If you serve a
credential-free instance under another name, such as a LAN hostname or a reverse
proxy's public name, add that name to `allowed_hosts`. If the web UI is served
from another origin, add the origin to `cors_origins`:

```yaml
settings:
  allowed_hosts: [pyrite.lan]            # or PYRITE_ALLOWED_HOSTS=pyrite.lan
  cors_origins: ["http://pyrite.lan:8088"]
```

`pyrite serve --host <addr>` adds `<addr>` automatically. The Vite dev server
(`npm run dev` on port 5173) is already in the default `cors_origins`. If you
run it on another port, add `http://localhost:<port>`.

**Web development with auth enabled.** The Vite proxy (`changeOrigin: true`)
rewrites `Host` but keeps the browser's `Origin`, so every signed-in write is
checked against `cors_origins`. If `npm run dev` gets `403 Cross-origin
request refused` on writes (a non-default Vite port, or a `cors_origins` you
set yourself), start the backend with the Vite origin included:

```bash
PYRITE_CORS_ORIGINS=http://localhost:5173 pyrite serve   # your Vite port; comma-separate several
```

Behind a reverse proxy, the browser sends `Origin: https://<public name>` on
every write, including login, logout and registration. If the proxy rewrites
`Host` to the upstream address (nginx does unless you set
`proxy_set_header Host $host`), that `Origin` no longer matches the server's
own origin and every UI write gets 403. Either keep the public `Host`
(`proxy_set_header Host $host`) and add the public name to `allowed_hosts`, or
add the public origin (`https://<public name>`) to `cors_origins`. This applies
to an auth-disabled instance without API keys, to one with
`anonymous_tier: write`, and to every signed-in write on an auth-enabled
instance.

## Authentication (multi-user)

| Variable | Default | Meaning |
|---|---|---|
| `PYRITE_AUTH_ENABLED` | `false` | Turn on user accounts and per-KB permissions |
| `PYRITE_AUTH_ANONYMOUS_TIER` | unset | What an unauthenticated request may do when auth is enabled: `read`, `write`, or `none` for nothing. Any other value, including `admin`, is refused at startup. It is a ceiling: on each KB the visitor gets the lower of this and the KB's `default_role` (a `default_role: none` KB stays hidden, a `default_role: read` KB stays read-only, and `default_role: write` never lifts a `read` visitor to write). Unset falls back to the API-key role. |
| `PYRITE_AUTH_ALLOW_REGISTRATION` | `true` | Let people create accounts on the web (still closed until an admin exists; see below) |
| `PYRITE_AUTH_LOGIN_RATE_LIMIT` | `20/minute;200/hour` | `/auth/login` attempts per client (`settings.auth.login_rate_limit`) |
| `PYRITE_AUTH_LOGIN_RATE_LIMIT_PER_USERNAME` | `5/minute;30/hour` | Failed `/auth/login` attempts per username (`settings.auth.login_rate_limit_per_username`) |
| `PYRITE_AUTH_REGISTER_RATE_LIMIT` | `5/minute;20/hour` | `/auth/register` attempts per client (`settings.auth.register_rate_limit`) |
| `PYRITE_GITHUB_CLIENT_ID` / `PYRITE_GITHUB_CLIENT_SECRET` | unset | GitHub OAuth login |
| `PYRITE_ENCRYPTION_KEY` | unset | If set, stored GitHub access tokens are encrypted at rest with it. Set it on any shared instance. |

**The first admin comes from the CLI.** With auth enabled, web registration and
GitHub sign-up are refused until an admin exists, and nobody becomes admin by
signing up first. On the server's data directory:

```bash
pyrite-admin user create alice --role admin     # prompts for the password
```

`settings.auth.require_invite_code: true` makes registration need a code an
admin created; the new user gets the code's role. GitHub sign-up obeys the same
switches: with registration off or needing an invite code, a first GitHub
login creates an account only for a member of the provider's `allowed_orgs` or
of an org in its `org_tier_map`. `pyrite serve` warns at startup when
registration is open, naming the KBs a stranger could read by signing up.

Rate limits use the same syntax as slowapi (`"5/minute"`, several joined by
`;`); an over-limit request gets 429 with `Retry-After`. Know their limits:

- They are counted in memory, per server process, and keyed on the address of
  the connection's peer. Behind a reverse proxy every client arrives from the
  proxy's address and shares one budget; several worker processes each keep
  their own count.
- The per-username limit counts failed logins, so anyone who knows a username
  can lock that account's password login for about a minute.
- An IPv6 client is keyed by its full address, so one host with many
  addresses in its prefix gets a budget per address.

**What a self-registered user can read.** Someone who signs up without an
invite code (or through GitHub without an `allowed_orgs` or `org_tier_map`
match) can read, and never write, KBs whose `default_role` is set to `read` or
`write`, plus whatever an admin grants them per KB. A KB with no `default_role`
stays closed to them. Users an operator created or vetted -- the CLI, an invite
code, an org rule -- keep the old rule: their global role applies to every KB
without a `default_role`. Changing a user's role does not change this; an
admin grants or removes it explicitly, for now only through the role API
(`"global_access": true|false` in `PUT /auth/users/{id}/role`; `GET
/auth/users` shows each user's value). Users inserted by the
`deploy/*/create-user.py` scripts get public-KB access only until an admin
grants it. Migration v26 marks every user that existed before it with global
access; its rollback is a no-op, so rolling back and migrating up again grants
global access to every user present at that time, self-registered ones
included. To share a KB with every signed-in user,
set its `default_role` to `read` (which also puts it on the public site, below),
or grant it per user.

Per-KB access: each KB in `config.yaml` may carry `default_role: read` (public
to any authenticated user), `write`, or `none` (private: explicit grants only).
Grants are managed over the REST API by an admin
(`GET`/`POST /api/kbs/{name}/permissions`) or in the web UI's KB settings; there
is no CLI command for them yet.

**`default_role: read` also publishes the KB to anyone, signed in or not.**
Such a KB is on the pre-rendered public site (`/site`, rendered with
`POST /api/site/render`), in `/site/sitemap.xml` and in `/sitemap.xml`,
whatever the auth settings are. A KB with `default_role` unset, `write` or
`none` is never rendered to `/site`, and `/site` refuses its pages even if an
older cache still holds them. To take a KB off the public site, change its
`default_role` and re-render; purge any CDN in front of `/site`.

### The public site's Content-Security-Policy

`/site` pages are served with a strict policy (`script-src 'self'`, no inline
scripts). If a reverse proxy injects a script into those pages, such as an
analytics snippet, the browser blocks it until you allow it:

| Variable | Default | Meaning |
|---|---|---|
| `PYRITE_SITE_CSP_EXTRA` | unset | Extra sources in CSP syntax, appended to the built-in `/site` policy. Sources go on the directive with the same name; a directive the policy lacks is added. A malformed directive is ignored and logged. |

```yaml
settings:
  # A proxy that injects <script src="https://plausible.io/..."> plus a small
  # inline init script: allow the host, and the inline script by its hash
  # (the browser console's CSP error prints the hash to use).
  site_csp_extra: "script-src https://plausible.io 'sha256-<hash>'; connect-src https://plausible.io"
```

Prefer a hash to `'unsafe-inline'` in `script-src`: `'unsafe-inline'` would
let a script planted in KB content run too.

Limits on what the setting can do:

- `object-src` and `base-uri` cannot be extended. A directive that tries is
  ignored, and a warning is logged.
- Adding `'unsafe-inline'`, `'unsafe-eval'` or `*` to `script-src` or
  `default-src` is applied, but logs a warning (on the first `/site` request), because it lets
  script in KB content run on `/site`.
- A directive with no value, such as `upgrade-insecure-requests`, is added
  as is.
- A malformed directive is ignored and logged. That includes a bad name and
  a source that contains `,` or a control character.

## Search and embeddings

| Variable | Default | Meaning |
|---|---|---|
| `PYRITE_SEARCH_MODE` | `keyword` | Default search mode: `keyword`, `semantic`, `hybrid` |
| `PYRITE_AUTO_EMBED` | `true` | Embed entries when they are written. `0`/`false` turns it off: keyword search only, no torch import and no model download on the write path. `pyrite index embed` backfills later. |
| `PYRITE_PREWARM_EMBEDDINGS` | `false` | Load the embedding model at server start instead of on the first write or semantic search |

`config.yaml` also sets `embedding_model` (default `all-MiniLM-L6-v2`, ~90 MB,
downloaded on first use) and `search_backend` (`sqlite` or `postgres`, with
`database_url` for the latter).

## Bounded reads (MCP)

Every MCP read that returns a body is bounded, so one call cannot exceed what
its caller can hold (ADR-0034). Tune these when your clients' context windows
are smaller or larger than the defaults assume; the MCP tool descriptions
report whatever values are in force, so an agent reads the numbers that
actually apply.

| Variable | Default | Meaning |
|---|---|---|
| `PYRITE_BODY_CHUNK_DEFAULT` | `8000` | Body characters returned when the caller passed no `body_limit`. 79% of measured bodies arrive whole at this size. |
| `PYRITE_BODY_CHUNK_MAX` | `20000` | Per-body ceiling. A caller's `body_limit` above this is clamped to it, on every read path including `fields`. |
| `PYRITE_BODY_RESPONSE_BUDGET` | `40000` | Total body characters one response may carry across all of its entries (`kb_batch_read`, `kb_search` with `include_body` or `fields`, `kb_list_entries`, `kb_recent`). Bodies fill in request order; entries past the budget return an empty body with `body_truncated` and their true `body_length`. |

All three are read at server start. A value that is not a positive integer, or
a `PYRITE_BODY_CHUNK_DEFAULT` above `PYRITE_BODY_CHUNK_MAX`, stops the server
with a message naming the variable rather than silently falling back — a
deployment that ignores its own tuning is worse than one that will not boot.

Continue a truncated body with `kb_read_body` (offset-based); see
[json-contracts.md](json-contracts.md) for the marker keys.

## AI features

| Variable | Default | Meaning |
|---|---|---|
| `PYRITE_AI_PROVIDER` | unset | `openai` or `anthropic` |
| `PYRITE_AI_MODEL` | provider default | Model name |

API keys for providers are the providers' own variables (`OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`); Pyrite is bring-your-own-key.

## Plugins

| Variable | Default | Meaning |
|---|---|---|
| `PYRITE_STRICT_PLUGINS` | `false` | Fail startup if any installed plugin fails to load, instead of skipping it. Recommended in CI. |

## Seeing the effective configuration

```bash
pyrite-admin config show    # merged config with secrets masked
pyrite kb list              # registered KBs and their paths
curl localhost:8088/health  # server: index path, embedding readiness
```

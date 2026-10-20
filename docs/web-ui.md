# Use the web UI

How to sign in, which features need an AI provider, and how a non-admin's
changes reach the main KB. To build and start the UI, see
[Launch the Web UI](getting-started.md#launch-the-web-ui).

## Sign in for the first time

What you see depends on the server's mode (see
[Configuration](configuration.md)):

- **No keys, auth off** (the default): there is no sign-in. The UI opens as an
  admin.
- **API keys, auth off**: browser requests carry no key, so the UI cannot read
  anything. Use the CLI, MCP or [REST](rest-api.md) with the key.
- **Auth on** (`PYRITE_AUTH_ENABLED=true`): the UI opens at `/login`.

With auth on, nobody becomes admin by registering. Create the first admin on the
server, then sign in with it:

```bash
pyrite-admin user create alice --role admin
```

Other people use "Register" on the login page, unless registration needs an
invite code (`settings.auth.require_invite_code`). Without a code, a new
account can read public KBs only. An admin raises its role in Settings →
Users, or gives out an invite code that carries the role.

## Features that need an AI provider

Summarize, auto-tag, link suggestions and the chat sidebar call an LLM. Without
a provider they answer `503` with code `AI_NOT_CONFIGURED`. Everything else
(browse, edit, search, graph, collections) works without one.

Set a provider one of two ways:

- **For everyone (admin):** Settings → AI Provider, or `PYRITE_AI_PROVIDER` and
  the provider's own key variable (see
  [Configuration](configuration.md#ai-features)). Changing these settings
  needs the admin role.
- **For you (any signed-in user, auth on):** Settings → My API Keys. Your key
  is used for your requests only.

## Submit and merge changes (non-admin users)

With auth on, a signed-in user whose role is `write` does not write to the main
KB. Their first write creates a git worktree of their own, and their creates,
updates and deletes go there. Only they see those edits until an admin merges
them. A KB that is not in a git repository has no worktree, and the write is
refused. A request made with an API key, not a session, writes to the main KB.

The UI shows none of this to the writer: its Changes page lists the main KB's
uncommitted changes, not your worktree. You review, submit and discard through
REST, with the session cookie from `POST /auth/login`:

| Step | Request |
|---|---|
| State: `none`, `active`, `submitted`, `merged` or `rejected`, plus reviewer feedback | `GET /api/worktree/status?kb=<kb>` |
| The diff of your branch against main (submit commits your edits to it, so it is empty before) | `GET /api/worktree/changes?kb=<kb>` |
| Submit for review | `POST /api/worktree/submit` with `{"kb": "<kb>"}` |
| Discard all your edits | `POST /api/worktree/reset` with `{"kb": "<kb>"}` |

An admin decides in the UI. Merge Queue in the sidebar lists submissions;
"Diff" shows one, "Merge" merges it into main, and "Reject" sends it back with
optional feedback, which the author reads in `worktree/status`.

Admins write straight to the main KB. Their Changes page lists what is not yet
committed, and "Publish" commits and pushes it.

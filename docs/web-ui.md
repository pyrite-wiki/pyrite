# Use the web UI

How to sign in and which features need an AI provider. To build and start the UI, see
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

### Invite someone

The web app has no screen for invite codes, and the CLI has no command for
them. An admin makes one with three REST routes. They take the admin's
`pyrite_session` cookie and nothing else: an API key is refused with `401`,
even an admin's.

Sign in as the admin and keep the cookie in a file. The server here is
`localhost:8088`; use your own address:

<!-- not-run: needs a running server with auth on; tests/test_invite_code_sessions.py covers the routes -->
```bash
curl -s -c admin.jar -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"..."}' localhost:8088/auth/login
```

Make a code. `role` is the role the new account gets (default `write`; Pyrite does not
check the value, so give a role it knows, such as `read` or `write`), `note` is for you, and `expires_hours` is optional:

<!-- not-run: needs a running server with auth on -->
```bash
curl -s -b admin.jar -H 'Content-Type: application/json' \
  -d '{"role":"write","note":"for bob","expires_hours":24}' \
  localhost:8088/auth/invite-codes
```

```json
{"code": "wC-CMMvbZsHbcdvQPfSpXg", "role": "write", "expires_at": "2026-10-22T13:53:46.019341+00:00", "note": "for bob"}
```

Send the code to your colleague. They choose Register on the login page and
enter it, or `POST /auth/register` with `invite_code` in the body. A code works
once: a second registration with it answers `400` `Invalid or expired invite
code`. A registration with no code, when codes are required, answers `400`
`An invite code is required to register`.

List the codes (each row says who used it and when) and revoke one that is
unused:

<!-- not-run: needs a running server with auth on -->
```bash
curl -s -b admin.jar localhost:8088/auth/invite-codes
curl -s -b admin.jar -X DELETE localhost:8088/auth/invite-codes/wC-CMMvbZsHbcdvQPfSpXg
```

A signed-in user who is not an admin gets `403` `Admin access required` from
all three routes. The switch that makes registration need a code is in
[Configuration](configuration.md#authentication-multi-user).

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

## Changes by non-admin users

With auth on, a signed-in user who is not an admin never writes to the main KB.
Each write (create, update, delete) goes to that user's own copy of the KB, a
git worktree on the branch `user/<username>`. The main KB changes only when an
admin merges. The KB must be a git repository whose main branch is `main`, and
the server's git needs an identity (`git config --global user.name` and
`user.email` for the account that runs `pyrite serve`): submitting commits in the
worktree, and without an identity that commit fails. For a KB that is not in git, see
[the update contract](json-contracts.md#write-refusals-create-import-update).

1. **The user edits.** Their change lands in their worktree. The main KB is
   unchanged, and nobody else sees it. The user sees their own version when they
   read the entry.
2. **The user submits.** The web app has no Submit button yet. `POST
   /api/worktree/submit` with `{"kb": "my-kb"}` and the user's session cookie
   commits the change on `user/<username>` and puts it in the admin's queue. The
   answer is `{"submitted": true, "kb_name": ..., "branch": "user/bob", ...}`.
   `GET /api/worktree/status?kb=my-kb` shows the state (`active`, `submitted`,
   `merged`) and `POST /api/worktree/reset` throws the user's changes away.
3. **An admin reviews.** In the web app, open **Merge Queue** in the sidebar
   (admins only). Each submission lists the user and KB; press **Diff** to read the
   change.
4. **An admin merges or rejects.** **Merge** merges `user/<username>` into
   `main`; the entry changes for everyone. **Reject** asks for feedback, which
   the user sees in `feedback` of the status route. The same actions are
   `POST /api/admin/merge-queue/{username}/merge` and `.../reject`.

**Changes** in the sidebar is a different view: it lists the KB's uncommitted
git changes, and an admin's **Publish** button commits them. It is not where a
non-admin submits.

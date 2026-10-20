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

How a signed-in non-admin user's changes reach the main KB is changing in 0.26.0; this section will describe it then.

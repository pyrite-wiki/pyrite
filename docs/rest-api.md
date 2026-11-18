# Call the REST API

How to start the server, authenticate and read the API's own reference.
Result and error shapes are in [JSON contracts](json-contracts.md); this page
does not repeat them.

## Start the server

```bash
PYRITE_API_KEY=my-key pyrite serve
```

The API listens on `http://127.0.0.1:8088` (`--host`, `--port`). Every
endpoint is under `/api`, except `/auth/*` (sign-in), `/docs`, `/redoc` and
`/openapi.json`.

## Authenticate

Send the key on every request, in a header (preferred) or a query parameter:

```bash
curl -H "X-API-Key: my-key" "http://127.0.0.1:8088/api/kbs"
curl -H "Authorization: Bearer my-key" "http://127.0.0.1:8088/api/kbs"
curl "http://127.0.0.1:8088/api/kbs?api_key=my-key"
```

A URL ends up in logs and browser history; use the header where you can.

What a request needs depends on the server's mode:

| Mode | Set by | A request needs |
|---|---|---|
| No credentials | no key, auth off | nothing: every request is admin |
| API keys | `PYRITE_API_KEY` (one admin key) or `api_keys` in `config.yaml` (a role each: `read`, `write`, `admin`) | a valid key |
| User accounts | `PYRITE_AUTH_ENABLED=true` | a key, or the `pyrite_session` cookie from `POST /auth/login`; with `anonymous_tier` set, no credential for what that tier allows |

`Authorization: Bearer` also carries a session token, as on `/mcp`.

A request without a valid credential gets `401` (`{"detail": "Invalid or
missing API key"}`). A key whose role is too low gets `403`. Keys, roles,
anonymous access and the first admin are in
[Configuration](configuration.md).

**A credential that does not verify gets `401`, never anonymous access.**
Only a request that sends *no* credential is the anonymous visitor. A key
(`X-API-Key`, `Authorization: Bearer`, `?api_key`) or a `pyrite_session`
cookie that is wrong, empty, expired or revoked gets `401` even when
`anonymous_tier` allows reads, on REST, on `/mcp` and on the live-update
socket. When a request carries more than one, the first in the order
`Authorization: Bearer`, `X-API-Key`, `?api_key`, cookie decides: a wrong
key next to a valid cookie is `401`. A `401` caused by the session cookie
also clears the cookie, so a browser's next request is anonymous. An
`Authorization` header with another scheme (a proxy's `Basic`) is not read.

## Read the API reference

The running server documents itself:

| URL | What |
|---|---|
| `/docs` | Swagger UI: every route, its parameters and bodies, with a "Try it out" button |
| `/redoc` | the same routes, read-only |
| `/openapi.json` | the schema, for a client generator or an agent |

These pages need no key. Swagger UI has no field for the key, so its "Try it
out" button works only in the no-credentials mode; otherwise call the route
with `curl`.

## Call it

The complete worked search, get and create calls are below.

## Shapes

- Errors: [Error shape](json-contracts.md#error-shape) and, for writes, the
  [refusal table](json-contracts.md#write-refusals-create-import-update). REST
  reports `{"detail": {"code", "message", "retryable", ...}}`; match on `code`.
- Successes: [Write success shapes](json-contracts.md#write-success-shapes)
  (the REST rows) and the [entry envelope](json-contracts.md#entry-envelope).
- A write that loses a race is `409 CONFLICT`; re-read and repeat it.

## Search, read and create in a scratch KB

These calls assume the full registered KB name `test-kb` and an existing note
`hello`. Replace the sample key with your configured credential; see
[Configuration](configuration.md) for authentication. Search is explicitly
keyword mode, so this example does not require an embedding provider.

```bash
curl -H "X-API-Key: my-key" "http://127.0.0.1:8088/api/search?q=Hello&kb=test-kb&mode=keyword"
curl -H "X-API-Key: my-key" "http://127.0.0.1:8088/api/entries/hello?kb=test-kb"
curl -X POST -H "X-API-Key: my-key" -H "Content-Type: application/json" -d '{"kb":"test-kb","entry_type":"note","title":"REST example","body":"Example body"}' "http://127.0.0.1:8088/api/entries"
```

The entry route needs a full KB name when an ID is ambiguous. Use only the documented body keys and put optional custom entry data in its
documented field; the running OpenAPI schema describes accepted fields. The API reference at `/docs` and `/redoc` owns the route
list. Format negotiation is route-specific: inspect the operation in OpenAPI
instead of assuming every route accepts the CLI's `--format` choices.

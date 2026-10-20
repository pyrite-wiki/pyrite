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
curl "http://127.0.0.1:8088/api/kbs?api_key=my-key"
```

A URL ends up in logs and browser history; use the header where you can.

What a request needs depends on the server's mode:

| Mode | Set by | A request needs |
|---|---|---|
| No credentials | no key, auth off | nothing: every request is admin |
| API keys | `PYRITE_API_KEY` (one admin key) or `api_keys` in `config.yaml` (a role each: `read`, `write`, `admin`) | a valid key |
| User accounts | `PYRITE_AUTH_ENABLED=true` | a key, or the `pyrite_session` cookie from `POST /auth/login`; with `anonymous_tier` set, no credential for what that tier allows |

A request without a valid credential gets `401` (`{"detail": "Invalid or
missing API key"}`). A key whose role is too low gets `403`. Keys, roles,
anonymous access and the first admin are in
[Configuration](configuration.md).

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

```bash
curl -H "X-API-Key: my-key" "http://127.0.0.1:8088/api/entries/hello?kb=notes"

curl -X POST -H "X-API-Key: my-key" -H "Content-Type: application/json" \
  -d '{"kb": "notes", "entry_type": "note", "title": "Hello", "body": "Hi"}' \
  "http://127.0.0.1:8088/api/entries"
```

The KB goes in the `kb` query parameter or body field, by its full name.

## Shapes

- Errors: [Error shape](json-contracts.md#error-shape) and, for writes, the
  [refusal table](json-contracts.md#write-refusals-create-import-update). REST
  reports `{"detail": {"code", "message", "retryable", ...}}`; match on `code`.
- Successes: [Write success shapes](json-contracts.md#write-success-shapes)
  (the REST rows) and the [entry envelope](json-contracts.md#entry-envelope).
- A write that loses a race is `409 CONFLICT`; re-read and repeat it.

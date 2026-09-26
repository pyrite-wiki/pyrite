- A KB-scoped REST write is now authorised against exactly the knowledge bases
  the handler acts on, whatever the request's `Content-Type`; a write that
  names no knowledge base is refused rather than checked against the caller's
  global role. The server extra now requires FastAPI 0.132.0 or later.
- A state-changing request authenticated by the session cookie is now refused
  unless its `Origin` (or `Referer`) is the server's own origin or listed in
  `cors_origins`, in every authentication mode. Requests authenticated with an
  API key in the `X-API-Key` header are unaffected.

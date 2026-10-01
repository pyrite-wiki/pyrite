- A request to `/api`, `/mcp`, `/auth` or `/ws` itself (with or without a
  trailing slash), or to any path under one that no real route handles,
  now answers 404 (JSON) for every HTTP method instead of the web app's
  page (or a 405), with or without a built `web/dist` and with auth on or
  off. Previously, on a checkout with a built `web/dist`, such a request
  (for example `/mcp/info` when MCP is disabled, or a bare `/api`) fell
  through to the SPA and answered 200 with the app's HTML. A WebSocket
  upgrade to such a path is closed before accept, as before. Real routes
  under these prefixes still win, and paths that merely start with `docs`,
  `redoc`, `openapi.json`, `health`, `site` or `viewer` (for example
  `/docs-old`) still answer 404, as before. The static routes
  (`/favicon.ico` and the SPA catch-all) are now registered whether or not
  `web/dist` exists, so the access-policy completeness guard classifies
  them. (#538)

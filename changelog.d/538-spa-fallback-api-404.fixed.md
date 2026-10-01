- When the web UI is built, a request to `/api`, `/mcp`, `/auth` or `/ws` itself,
  or to any path under one that no real route handles, answers 404 in the
  API's error shape (`{"detail": {"code", "message", "retryable"}}`) for every
  HTTP method, instead of the web app's page (for example `/mcp/info` when MCP
  is disabled). Real routes under these prefixes still win, a WebSocket upgrade
  to such a path is closed before accept as before, and `/docs-old`-style paths
  still answer 404. Without a web build nothing changes. The guard side of this
  (the static routes a build adds to the access-policy completeness check) is
  #563. (#538)

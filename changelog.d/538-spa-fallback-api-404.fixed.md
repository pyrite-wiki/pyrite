- An unknown path under `/api`, `/mcp`, `/auth` or `/ws` now answers 404
  (JSON, as the rest of the API does) instead of the web app's own page.
  Previously, on a checkout with a built `web/dist`, a request to one of
  these prefixes with nothing mounted behind it (for example `/mcp/info`
  when MCP is disabled) fell through to the SPA and answered 200 with the
  app's HTML. Client-side routing is unaffected: any other path still gets
  the SPA shell. (#538)

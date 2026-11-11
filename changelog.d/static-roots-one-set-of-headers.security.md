The server resolves `PYRITE_STATIC_DIR` once at startup. Every non-API response, including 404s, redirects and refusals, carries one Content-Security-Policy (with the operator's `site_csp_extra`) and `nosniff`, applied in one place; `/branding` files add `sandbox`. One classification of paths decides both whether the API or the web UI answers a path and which headers it carries. A file served from the web UI's bundle is the one that was checked to lie inside it. Branding `site_url`, `support_url` and `footer_credit_url` keep only http and https URLs (and `mailto:` for the contact). Anonymous WebSocket connections are capped.

**Upgrade actions.**

- A `branding.yaml` whose `site_url` or `footer_credit_url` is not an absolute http(s) URL, or whose `support_url` is neither one nor a `mailto:` address (a schemeless or relative value, for example), is now invalid as a whole, like any other malformed field: the branding endpoints answer with the branding error until it is corrected.
- Files served from the web UI's bundle other than its page (scripts, styles, images, `robots.txt`) now carry the Content-Security-Policy as well as `nosniff`.

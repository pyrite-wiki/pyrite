Registering a knowledge base by path over the network (`POST /api/kbs` and the MCP tool `kb_registry_add` over HTTP, both admin tier) is limited to folders under the roots listed in the new setting `settings.kb_roots` (`PYRITE_KB_ROOTS`). The path and the roots are compared fully resolved, so a symbolic link or `..` cannot lead outside a root; a refused path answers `KB_PATH_NOT_ALLOWED` (HTTP 403) naming the configured roots, and nothing is created. The local operator (`pyrite kb add`, `pyrite-admin`, `pyrite mcp` over stdio) still registers any path. See docs/configuration.md, "Where a network caller may register a KB".

**Upgrade notes:** `kb_roots` is empty by default, so after upgrading no network caller can register a KB path until the operator lists roots. **The web UI's "create knowledge base" returns 403 (`KB_PATH_NOT_ALLOWED`) until `kb_roots` is set.** To allow it, list the folders new KBs may go in, in `config.yaml`:

```yaml
settings:
  kb_roots: [/srv/pyrite/kbs]
```

or set `PYRITE_KB_ROOTS=/srv/pyrite/kbs` (several roots separated by `:`, or `;` on Windows), then restart the server; a KB then goes in a folder under one of them. `pyrite kb add` at the server's own terminal works without it. An operator API key used over the network is held to the roots as well. Ephemeral KBs (`ephemeral: true`) are unaffected.

# Known Issues

What 0.25.7 ships with open.

- **`pyrite orient` lists no fields for plugin-declared types** (#232). It returns the schema for core types only; for a type an extension declares, read the extension's documentation.
- **Journalism-investigation tools are experimental and unsupported** until the alpha plugin contract (#644). Multi-user (accounts, per-KB permissions, the public `/site`) and the REST API are experimental too; [the alpha's supported surface](kb/designs/alpha-supported-surface.md) says what is supported and what is not.
- **A release-tag install has no web UI.** The built frontend is not packaged yet. Clone the repository and run `cd web && npm install && npm run build` (see [Launch the Web UI](docs/getting-started.md#launch-the-web-ui)).
- **Search matches an entry's title and body, not its tags.** An entry that is only tagged `mathematics` is not found by `pyrite search mathematics`.
- **Semantic search needs the `semantic` extra and a one-time ~90 MB model download.** Without the extra, a semantic search prints `semantic leg skipped: sentence-transformers is not installed` and returns nothing. With it but before `pyrite index embed` has run, it prints a warning that names `pyrite index embed`. Keyword search is unaffected. The first warning suggests `pip install pyrite[semantic]`, but there is no PyPI wheel yet: use `pip install -e ".[semantic]"` in a clone, or `pyrite[server,cli,semantic] @ git+...` from a release tag.
- **`pyrite init --template software` does not say that the `pyrite sw` commands come from an extension.** `pip install -e extensions/software-kb` adds them.

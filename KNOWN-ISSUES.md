# Known Issues

What 0.25.7 ships with open.

- **`pyrite orient` lists no fields for plugin-declared types** (#232). It returns the schema for core types only; for a type an extension declares, read the extension's documentation.
- **Journalism-investigation tools are experimental and unsupported** until the alpha plugin contract (#644). Multi-user (accounts, per-KB permissions, the public `/site`) and the REST API are experimental too; [the alpha's supported surface](kb/designs/alpha-supported-surface.md) says what is supported and what is not.
- **A release-tag install has no web UI.** The built frontend is not packaged yet. Clone the repository and run `cd web && npm install && npm run build` (see [Launch the Web UI](docs/getting-started.md#launch-the-web-ui)).
- **Search matches an entry's title and body, not its tags.** An entry that is only tagged `mathematics` is not found by `pyrite search mathematics`.
- **Semantic search needs the `semantic` extra and a one-time ~90 MB model download.** Without them a semantic search returns nothing and prints a warning that names `pyrite index embed`; keyword search is unaffected.
- **`pyrite init --template software` does not say that the `pyrite sw` commands come from an extension.** `pip install -e extensions/software-kb` adds them.

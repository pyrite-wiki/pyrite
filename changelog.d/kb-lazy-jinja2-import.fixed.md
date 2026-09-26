### Fixed
- A `pip install pyrite` CLI-only install (no `server` extra) works again: the CLI's KB-registry path imported `jinja2` at module load through the `/site` cache renderer, even though nothing on that path renders a page.

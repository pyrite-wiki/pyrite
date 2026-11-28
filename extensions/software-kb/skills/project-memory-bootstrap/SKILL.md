---
name: project-memory-bootstrap
description: "Seed a Pyrite software KB from an existing code repository's README, changelog, docs, package layout, and git history."
---

# Project Memory Bootstrap

Use this skill in a code repository to seed a verifiable project memory in `kb/` from what the repository already records: components, architecture decision records (ADRs), and initial backlog items.

See the [software-kb README](../../README.md) for entry types, the backlog lifecycle, and the link vocabulary, and [JSON contracts](../../../../docs/json-contracts.md) for CLI result and refusal shapes.

## Scope and limits

- **Writes under `kb/` only.** This skill creates and updates KB entries through the `pyrite` CLI.
- **Never does:** `git push`, `git commit`, edit files outside `kb/`, delete an entry it did not write in this session, accept a `proposed` backlog item without the developer's instruction, or hand-edit YAML frontmatter.
- **No unsourced rationale.** Every ADR claim must cite a repository source (commit SHA, tag, changelog version, or README/doc section). If the repository does not record why a choice was made, write `"rationale not recorded in <source>"` and add it under `## Open Questions`.

## Steps

### 1. Preflight

Check that Pyrite and the software-kb extension are available:

```bash
pyrite --version
```

```bash
pyrite sw --help
```

- **If `pyrite sw` is missing (exit 2):** print the install line from the [main README](../../../../README.md#install) (`pip install "pyrite-software-kb @ git+https://github.com/pyrite-wiki/pyrite@v0.26.4#subdirectory=extensions/software-kb"`) and stop.

Check whether `./kb` is already registered:

```bash
pyrite kb list
```

- **If `./kb` exists on disk but is not listed:** register and index it:

```bash
pyrite kb discover --add
```

```bash
pyrite index sync
```

- **If `./kb` does not exist yet:** initialize a software KB:

```bash
pyrite init -t software -p ./kb --name <kb>
```

Confirm the index is healthy and inspect the KB contract before writing:

```bash
pyrite index health -k <kb>
```

```bash
pyrite orient -k <kb> --detail brief
```

### 2. Read repository sources

Inspect only what the repository states in its own tree and history:

1. `README.md`, `CHANGELOG.md` (or `CHANGES.rst` / release notes), and `docs/` if present.
2. The top-level package layout (`src/`, package directories, entry points in `pyproject.toml`).
3. Tags and recent commit history (`git tag -l` and `git log --oneline -n 50`).

### 3. Record components

Create one `component` entry per real top-level module, service, or CLI package in the repository. Write the body to a scratch file first, then run:

```bash
pyrite create -k <kb> -t component --title "<title>" -f kind=module -f path=<path> -f owner=<name> --body-file <file>
```

- **If refused with `SCHEMA_VIOLATION`:** read the allowed `kind` values in the refusal message (or in the [software-kb README](../../README.md#entry-types)), fix the `-f kind=<value>` argument, and re-run.

### 4. Record ADRs from sourced decisions

Identify 3 to 6 architectural decisions visible in the sources. For each decision:

1. Create the ADR entry (which generates the `## Context`, `## Decision`, and `## Consequences` sections):

```bash
pyrite sw new-adr "<title>" -k <kb> --status accepted
```

2. Fill the body by keeping the `sw new-adr` sections, adding an `## Alternatives rejected` section (and `## Open Questions` when rationale is missing), citing the commit, changelog entry, or doc section for every claim, and updating the entry from a file:

```bash
pyrite update <adr-id> -k <kb> --body-file <file>
```

(MCP equivalent: `sw_create_adr` followed by `kb_update`.)

### 5. Link components and ADRs

Connect each component to the ADRs it realizes using the declared vocabulary in the [software-kb README](../../README.md#entry-types) (never invent relation names):

```bash
pyrite link <component> <adr-id> -r implements -k <kb>
```

### 6. Seed proposed backlog items

Create 3 to 5 `backlog_item` entries from open TODOs, known gaps, or the changelog's unreleased section. Leave every item in the initial `proposed` status (accepting work is the developer's call):

```bash
pyrite create -k <kb> -t backlog_item --title "<title>" -f kind=improvement -f priority=medium -f effort=S --body-file <file>
```

Link each backlog item to the ADR it tracks:

```bash
pyrite link <id> <adr-id> -r tracks -k <kb>
```

### 7. Verify and report

Run structural QA across the KB and fix any errors on the entries you created:

```bash
pyrite qa validate <kb>
```

- **If `qa validate` or an earlier `pyrite sw` listing returned `[]` unexpectedly:** run `pyrite index sync` and `pyrite index health -k <kb>`, then re-run the check.
- **If an entry you wrote has a validation error:** fix it with `pyrite update <id> -k <kb> -f <field>=<value>`. Only delete an entry with `pyrite delete <id> -k <kb>` if you created that entry in this session — never delete pre-existing entries.

Report the created component, ADR, and backlog item IDs along with `git status kb/`.

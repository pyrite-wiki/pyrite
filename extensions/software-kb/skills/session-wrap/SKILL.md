---
name: session-wrap
description: "Wrap up work on a claimed Pyrite software KB item: log decisions and rejected approaches, record new ADRs, update components, validate, and submit for review."
---

# Session Wrap

Use this skill after implementing and testing the code change for a claimed backlog item to record decisions in the project memory and move the item to `review`.

See the [software-kb README](../../README.md) for the backlog lifecycle and link vocabulary, and [JSON contracts](../../../../docs/json-contracts.md) for CLI output and refusal shapes.

## Scope and limits

- **Writes under `kb/` only** (work logs, ADRs, component descriptions, links, and the backlog item's transition to `review`, alongside the code changes the claimed item itself required).
- **Never does:** `git push`, `git commit`, edit unrelated files outside `kb/`, delete an entry it did not write in this session, accept a `proposed` item without the developer's instruction, edit an existing accepted ADR's decision in place, or hand-edit YAML frontmatter.

## Steps

### 0. Preflight

Confirm the extension is installed, the KB is registered, and the index is current:

```bash
pyrite sw --help
```

```bash
pyrite kb list
```

- **If `pyrite sw` is missing (exit 2):** print the install line from the [main README](../../../../README.md#install) and stop.
- **If `./kb` is not registered:** run `pyrite kb discover --add` and `pyrite index sync`.
- **If a query returns `[]` unexpectedly:** run `pyrite index sync` and `pyrite index health -k <kb>` before continuing.
- **If the session has no claimed item yet:** run `pyrite sw pull-next -k <kb>` and `pyrite sw claim <id> -k <kb> --assignee agent:<name>` first (if `pull-next` is empty or `claim` exits 1, follow the `session-start` skill).

### 1. Log the work session

Record what you built, the design choices you made, approaches you tried and rejected, and any remaining open questions:

```bash
pyrite sw log <id> -k <kb> -s "<summary>" --decisions "<text>" --rejected "<text>" --open-questions "<text>"
```

(MCP equivalent: `sw_log`.)

### 2. Record a new ADR if the work made a lasting decision

If the implementation introduced or changed an architectural decision:

1. Create the ADR skeleton (`## Context`, `## Decision`, `## Consequences`):

```bash
pyrite sw new-adr "<title>" -k <kb> --status proposed
```

2. Add `## Alternatives rejected` and cite the code/commit or ticket source for every claim, then write the body from a file:

```bash
pyrite update <adr-id> -k <kb> --body-file <file>
```

3. Link the backlog item and affected component using the declared vocabulary in the [software-kb README](../../README.md#entry-types):

```bash
pyrite link <id> <adr-id> -r tracks -k <kb>
```

```bash
pyrite link <component> <adr-id> -r implements -k <kb>
```

4. If the new ADR replaces an earlier ADR, link it with `supersedes` — never overwrite the old ADR's decision text:

```bash
pyrite link <adr-id> <old-adr-id> -r supersedes -k <kb>
```

### 3. Update the component entry if its surface changed

If a component's public interface, path, or dependencies changed, read the existing entry first (`--body-file` replaces the whole body), then update it:

```bash
pyrite get <component> -k <kb>
```

```bash
pyrite update <component> -k <kb> --body-file <file>
```

- **If an update or create is refused with `SCHEMA_VIOLATION`:** read the refusal's `errors` list for the allowed field values, fix the `-f <field>=<value>` flag, and retry.

### 4. Validate the KB

Run structural QA and fix any errors on the entries you wrote or touched:

```bash
pyrite qa validate <kb>
```

### 5. Submit the item for review

Move the claimed item from `in_progress` to `review`:

```bash
pyrite sw submit <id> -k <kb>
```

(MCP equivalent: `sw_submit`.)

### 6. Report changed KB files

List the created and updated entry IDs and show the working tree status under `kb/` (`git status kb/`). Leave committing and pushing to the developer's workflow.

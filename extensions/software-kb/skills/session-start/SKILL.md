---
name: session-start
description: "Start a coding session from a Pyrite software KB: verify clone registration and index health, orient, gather ADR context through components, and claim the next item."
---

# Session Start

Use this skill at the beginning of a coding session to orient on the repository's Pyrite software KB, select or resume an accepted backlog item, read its governing ADRs, and claim it.

See the [software-kb README](../../README.md) for the backlog lifecycle and link vocabulary, and [JSON contracts](../../../../docs/json-contracts.md) for CLI output and refusal shapes.

## Scope and limits

- **Writes under `kb/` only** (the claim transition on the selected backlog item, plus index registration/sync if the clone was unindexed).
- **Never does:** `git push`, `git commit`, edit files outside `kb/`, delete entries, transition a `proposed` item to `accepted` without the developer's instruction, or hand-edit YAML frontmatter.

## Steps

### 0. Preflight (clone and index check)

Verify the software-kb extension and KB registration:

```bash
pyrite sw --help
```

```bash
pyrite kb list
```

- **If `pyrite sw` is missing (exit 2):** print the install line from the [main README](../../../../README.md#install) (`pip install "pyrite-software-kb @ git+https://github.com/pyrite-wiki/pyrite@v0.26.4#subdirectory=extensions/software-kb"`) and stop.
- **If `./kb` is not in `pyrite kb list`:** register and index it:

```bash
pyrite kb discover --add
```

```bash
pyrite index sync
```

Check index health before trusting any empty query result:

```bash
pyrite index health -k <kb>
```

- **If a list command returns `[]` on a clone that has files in `kb/`:** an empty list is not a negative answer until `pyrite index health -k <kb>` is clean. Run `pyrite index sync` and retry.

### 1. Orient on the KB

Read the KB's types, recent activity, and operational contracts:

```bash
pyrite orient -k <kb> --detail brief
```

(MCP equivalent: `kb_orient`.)

### 2. Inspect the board and active work

Check lane counts and whether an item is already in progress:

```bash
pyrite sw board -k <kb>
```

```bash
pyrite sw backlog -k <kb> --status in_progress --format json
```

### 3. Pick the item to work

If you already hold an `in_progress` item from an earlier turn, resume it. Otherwise ask for the next unblocked `accepted` item:

```bash
pyrite sw pull-next -k <kb>
```

- **If `pull-next` returns `"recommendation": null`:** check `pyrite sw backlog -k <kb> --status proposed --format json`. If items are sitting in `proposed`, list their IDs and titles for the developer and show the command they can run to accept one:

```bash
pyrite sw transition <id> accepted -k <kb>
```

Do not transition a `proposed` item to `accepted` on your own.

### 4. Gather context and component ADRs

Fetch the item's context bundle:

```bash
pyrite sw context-for-item <id> -k <kb>
```

Then, for each component the item affects, read the component and its backlinks so you catch ADRs linked to the component (`context-for-item` only walks one hop today; tracked in issue `#1863`):

```bash
pyrite get <component> -k <kb>
```

```bash
pyrite backlinks <component> -k <kb>
```

Read every ADR surfaced by `context-for-item` or `backlinks`:

```bash
pyrite get <adr-id> -k <kb>
```

### 5. Claim the item

Claim the item with your agent identifier:

```bash
pyrite sw claim <id> -k <kb> --assignee agent:<name>
```

(MCP equivalent: `sw_claim`.)

- **If `pyrite sw claim` exits 1 (lost claim or invalid status):** another worker claimed the item first, or its status changed. Return to Step 3 (`pyrite sw pull-next -k <kb>`) and retry at most twice.
- **If a gate or field check reports `SCHEMA_VIOLATION`:** inspect the refusal's `errors` list, fix the field with `pyrite update <id> -k <kb> -f <field>=<value>`, and retry.

### 6. Report before editing code

Summarize for the developer:
1. The claimed backlog item (`<id>` and title).
2. The governing ADRs and constraints found in Step 4.
3. Any open questions to resolve before or during implementation.

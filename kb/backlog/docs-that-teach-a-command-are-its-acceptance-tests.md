---
id: docs-that-teach-a-command-are-its-acceptance-tests
title: Docs that teach a command are its acceptance tests
type: backlog_item
tags:
- process
- docs
- testing
importance: 5
kind: improvement
status: proposed
priority: high
effort: M
rank: 0
---

Maintainer direction, 2026-10-02: doc-driven design, paired with testable documentation as a form of acceptance testing. From the root-cause analysis of PR #612.

## Problem

Pyrite's docs tell users what to type and what to write by hand, and nothing runs them. Two costs showed on #612:

- `README.md` teaches a hand-written MCP server entry. `mcp-setup` then treated an entry of exactly that shape as its own and replaced it. No test seeded the file with the README's own example; the fixtures were entries the implementer invented.
- `docs/getting-started.md` still names a config file no client reads. A doc that is executed cannot say that for long.

`scripts/run_tutorial.py` already runs the tutorial in a temp `HOME`, but it asserts exit codes, not what was written or read back.

## Direction

1. **Write the doc first.** For a user-facing command, the groom or the worker's first commit is the passage a user will read: the command, what it prints, what it leaves on disk. That passage is the acceptance criteria.
2. **Run the doc.** Examples in the README, getting-started and the command reference are executed in CI against a scratch `HOME`: each fenced command runs, and each shown output or file is compared, with a marked way to elide what varies (paths, ids, timestamps).
3. **Docs are fixtures.** When a doc teaches a user to write something by hand (a config entry, a `kb.yaml` block, frontmatter), that example is a seeded input for every command that later reads or rewrites the same file.

## To settle (needs a groom; probably a spike on tooling)

- The mechanism: Python's `doctest` over Markdown, a Markdown-fence runner such as `pytest-markdown-docs` or `mktestdocs`, `cram`-style shell transcripts, or extending `scripts/run_tutorial.py`. Which reads best as documentation is the test of the tool.
- Which documents are in from the start. Suggested: `README.md` quick start, `docs/getting-started.md`, `docs/agent-write-path.md`, `docs/json-contracts.md`.
- How JSON output is compared now that JSON is the default output of every command (#303).
- Where it runs: the PR's `docs` check for changed files, the full set in the merge queue.
- The backlog items `docs-counts-generated-or-asserted-from-code` and `docs-operational-contracts-travel-with-tool` cover neighbouring ground; say how the three fit.

## Acceptance (to be sharpened by the groom)

- Changing a command's output without changing the doc that shows it fails CI, and the failure names the doc and the line.
- The README's hand-written MCP entry is a fixture in the `mcp-setup` tests.
- The architect and worker definitions say that a user-facing command's doc passage is written first and is the acceptance.

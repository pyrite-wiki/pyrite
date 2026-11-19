# Troubleshoot reads, writes and upgrades

Start with the error code, then inspect stderr for the cause. The authoritative
[error and write-refusal contracts](json-contracts.md#error-shape) describe the
wire fields. Checks below use full KB name `test-kb`; substitute your own KB.
The examples are reads of an existing KB, except the explicitly described index
repair. No repair deletes a KB or automatically merges entries.

## A hand-edited file is absent from search

Incremental sync rereads files that changed on disk. A read of one entry and a
search of the index can disagree before synchronization. Check:

Health exits1 while the index is unhealthy; its findings remain available.

```bash
pyrite index health
pyrite get hello -k test-kb
pyrite search Hello -k test-kb --mode keyword
```

Run an index sync after ordinary external edits. If health reports stale KB link
resolution after a shortname change or upgrade (`links_stale_kb`), run an index
build instead: sync does not reread unchanged source files.

## Semantic results are empty, or match quality is weak

Check the configured embedding model and whether the KB has embeddings. Use
keyword mode while diagnosing an unavailable semantic leg. `match: none` or
`weak` is result quality, not proof that the file is absent. See
[the search envelope](json-contracts.md#search-result-envelope) and
[getting started](getting-started.md). Provider/network behavior is **not-run**
in the offline example tests: it requires a configured model/provider.

## AMBIGUOUS, or a KB name stopped working

Pass the full KB name and choose from `candidate_kbs` for an ambiguous entry.
A `shortname` is valid inside a wikilink prefix, not in `-k`; the KB_NOT_FOUND
suggestion identifies the full name. Never retry an ambiguous write by selecting
the first candidate. Read the [error contract](json-contracts.md#error-shape).

## The file exists but get refuses it

A malformed YAML header can prevent loading. Check the actual file rather than
creating a replacement entry. Health's `unmodellable_frontmatter` row identifies
a line and reason for tagged, complex or duplicate keys. Repair the file by hand;
this page does not promise one uniform update-refusal code for those shapes.
If health reports `double_spellings`, retain the intended spelling; equal pairs
may be merged by QA fix, while differing values need an operator decision.

## STORAGE_ERROR hides the detailed cause

The public message is intentionally generic. The cause is logged to stderr;
inspect it before retrying. On a hard-linked file, break the hard link before
writing. A FUSE/network mount may not provide the atomic exclusive-create
operation the writer requires: use a supported local filesystem, preserving the
file. Hard-link and FUSE failure scenarios are **not-run** here because they
require filesystem-specific fault conditions.

## Timestamp, title or ID surprises

Send timestamps in YAML-recognized forms such as
`YYYY-MM-DDTHH:MM:SSZ`, with an optional explicit timezone offset. A datetime-local
value `YYYY-MM-DDTHH:MM` is accepted. Quote titles/IDs such as `"true"` or `"2024"`
when writing YAML. If ID pinning reports a collision, choose distinct intended
identities rather than silently accepting one generated ID.

## A KB name is reserved, or discovery registered nothing

API route names cannot be KB names. Choose a distinct full name; the refusal
occurs before registration. For discovery, inspect the candidate directories
and their kb.yaml files, then register the intended KB explicitly instead of
assuming an empty discovery result succeeded.

## Extension commands or removed-extension notices

Check the installed extensions and the process's stderr. A failed plugin CLI
registration is reported there; reinstall compatible extensions in the current
checkout's environment. An upgrade notice naming a removed extension requires
removing that old package and following the documented type/subdirectory
migration. The obsolete-package scenario is **not-run** because it needs the
old plugin; see [Upgrading](upgrading.md).

## CONFLICT while updating

A retryable409 means the file kept changing during the bounded write attempts.
Reread it and retry your intended change; do not use force to discard another
writer's content. This race is **not-run** in the deterministic examples.

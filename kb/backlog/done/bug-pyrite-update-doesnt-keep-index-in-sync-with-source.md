---
id: bug-pyrite-update-doesnt-keep-index-in-sync-with-source
title: "BUG: `pyrite update` writes the source file but does not refresh the index — downstream commands return stale data until manual `index sync`"
type: backlog_item
tags: [bug, cli, update, index, coherence, silent-data-loss, backlog-grooming]
importance: 5
kind: bug
status: wont_do
priority: medium
effort: S
rank: 1025
---

## Problem

`pyrite update <id> -k <kb> -f field=value` writes the change to the source
markdown file in the working tree but does **not** refresh the index
(`pyrite/storage/database.py` SQLite). Every subsequent `pyrite get`,
`pyrite sw backlog`, `pyrite search`, or any other read command goes through
the stale index and returns the pre-update values until somebody manually
runs `pyrite index sync`.

Concrete reproduction from the 2026-06-09 PO-review grooming pass:

The Jun 5 grooming review pass made ~17 `pyrite update` calls (16 ranks +
1 priority promotion). Four days later, the very next read of the backlog
returned numbers that were quietly wrong:

- Total backlog count was 519 in the cached snapshot; an `index sync` removed
  **12 stale entries** the index had been carrying and updated **465 more**.
- The cascade cluster's priorities (3 high, 4 medium per source-of-truth)
  showed as "all high" in the pre-sync snapshot, which materially misled
  the previous review's framing.
- Several status changes from prior `pyrite update -f status=done` calls
  had not propagated to `pyrite sw backlog` output until the manual sync.

## Impact

- **Every grooming pass starts on a confused foot.** A PO review reading
  `pyrite sw backlog` before remembering to `index sync` sees a fictitious
  view of the work. Decisions made on that view are wrong.
- **The conductor workflow** (`/loop /investigation-conductor`, `/pyrite-dev`)
  reads from the same stale index. Every loop tick risks acting on a
  pre-`update`-snapshot. Compounds with
  [[task-index-timestamp-drift]] and
  [[bug-pyrite-get-omits-rank-field-in-json-output]] — three independent
  read-path lies that grooming has to defeat with rituals.
- **Silent data loss is the right frame.** The user expects `pyrite update`
  to *update* — that's its name. Not "update the source and silently leave
  the index lying about it until somebody else notices."

## Root cause (hypothesis to confirm during the fix)

`pyrite update`'s implementation in `pyrite/cli/entry_commands.py` (or wherever
the `update` command's body lives) likely:

1. Loads the entry from the source file.
2. Applies the requested field change.
3. Writes the source file back.
4. **Does not invoke the indexer for the changed file.**

The fix is either (a) call `index_mgr.index_entry(...)` / `index_mgr.sync_one(...)`
inline at the end of `update`, or (b) emit a sync-after-update hook the
existing `kb_service.update_entry` would trigger.

Note that the *protocol-level* update path through `KBService.update_entry`
runs the before_save / after_save hook chain, and the index is supposed to
fall out of that. So this may be a CLI-layer bypass: the `pyrite update`
command may be writing the source file directly rather than going through
`KBService.update_entry`. That's the first check at fix time.

## Fix

Make `pyrite update` keep the index coherent. Either:

### Option A (preferred) — Route through KBService

If `pyrite update` is currently bypassing `KBService.update_entry`, route it
through. KBService already runs the hook chain and triggers reindexing via
the post-save path. This is the principled fix: one update entrypoint, one
contract.

### Option B — Inline reindex on the CLI side

If routing through KBService is too disruptive, add an `index_mgr.sync_one(
file_path)` call at the end of `pyrite update`. Less clean but local.

Either way, add a regression test that:
1. Creates an entry with `rank: 100`.
2. Calls `pyrite update <id> -k <kb> -f rank=200`.
3. Reads back via `pyrite get` (which goes through the index) **without
   calling `pyrite index sync`**.
4. Asserts the read returns `rank=200`, not `rank=100`.

## Acceptance criteria

- `pyrite update` leaves the index coherent with the source: a subsequent
  read via any indexed query returns the new value, with no manual sync.
- A regression test pins the read-after-update contract (no manual sync).
- The behavior is documented in CLAUDE.md / pyrite-dev skill so the
  forensic-grooming ritual ("always `index sync` first") can be removed.

## Related

- [[bug-pyrite-get-omits-rank-field-in-json-output]] — read-path projection
  bug. Together they make rank handling fully unreliable: writes don't
  propagate (this ticket), and reads silently drop the field
  (the projection ticket).
- [[bug-pyrite-sw-prioritize-renumbers-globally-clobbering-existing-ranks]]
  — write-path collision bug. Same Tier-A meta-bug family.
- [[task-index-timestamp-drift]] — sibling index-coherence bug
  (`updated_at` / `created_at` null in task index).
- [[index-watch-mode]] — adjacent but different scope: that ticket proposes
  a continuous-watch daemon. This ticket is "the write command should keep
  its own index honest"; index-watch-mode is "any external file change
  should reindex."
- [[fix-incremental-sync-failing-to-reindex-modified-files]] (done) — prior
  incremental-sync fix; this ticket is the next layer up (the *trigger* for
  that sync isn't firing from `pyrite update`).

## Discovery context

Filed from the 2026-06-09 PO-review grooming pass after a fresh
`pyrite index sync` removed 12 stale entries and updated 465 — a margin
large enough that the previous Jun 5 grooming review's framing of the
cascade cluster's priorities was materially wrong.

## Closure note (2026-06-11) — did not reproduce

Empirically verified during the loop pass on 2026-06-11: the bug described
here does NOT reproduce. The repro steps:

1. Picked a real backlog ticket (`bug-create-silently-accepts-undeclared-types-...`).
2. Ran `pyrite update <id> -k pyrite -f assignee=test-repro-marker`.
3. Without running `pyrite index sync`, called both `pyrite get` and
   `pyrite sw backlog`.

Both reads returned the new `assignee` value immediately. `pyrite update`
DOES reindex the touched entry through `KBService.update_entry` →
`DocumentManager.save_entry` (`pyrite/storage/document_manager.py:65`) →
`IndexManager.index_entry`. The reindex is wired.

The observation that drove this ticket's filing (the Jun 5 grooming review
needed a manual `index sync` to get accurate counts) was real — but the
diagnosis was wrong. The index WAS stale, but `pyrite update` was not the
cause. More likely candidates for the staleness root cause:

- Manual file edits in the working tree (e.g. my own hand-edits during
  the conductor-ticket triage). Pyrite has no file watcher.
- Git operations (checkouts, merges) that change files without going
  through any pyrite write path.
- A separate bug where `pyrite update` succeeds at indexing the *touched*
  entry but leaves *related* entries (e.g. parent-rollup targets) stale.

Closing as `wont_do` because the specific claim (the `update` write path
doesn't reindex) is false. If the underlying "index gets stale over time"
problem proves recurring, file a fresh ticket with the corrected
diagnosis. A `pyrite index health` extension to detect mtime-vs-index
skew would be a reasonable preventive measure.

This is the second false-positive ticket I caught this session-arc; the
first was the duplicate-frontmatter-keys alarm in
[[bug-pyrite-update-appends-instead-of-replacing-frontmatter-keys]]
which was a fenced-code-block grep artifact. Pattern: I've been filing
based on observations without empirical reproduction. Iron Law #2 says
investigate before fixing; the same discipline applies to filing.

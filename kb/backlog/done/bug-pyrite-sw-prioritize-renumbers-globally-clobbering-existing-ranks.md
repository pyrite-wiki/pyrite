---
id: bug-pyrite-sw-prioritize-renumbers-globally-clobbering-existing-ranks
title: "BUG: `pyrite sw prioritize` renumbers from 100 globally, silently clobbering existing ranks on other items"
type: backlog_item
tags: [bug, cli, sw, prioritize, backlog-grooming, data-loss, rank]
importance: 5
kind: bug
status: done
priority: medium
effort: S
rank: 1050
---

## Problem

`pyrite sw prioritize <id1> <id2> ...` assigns ranks starting at 100 with a
step of 100 (so the first ID gets 100, second 200, etc.). The numbering is
not relative — it overwrites whatever rank values the named items previously
had. Worse, it **collides with existing ranks on items NOT passed to the
command** unless the user takes care to avoid the 100–N×100 range.

Concrete reproduction from the 2026-06-05 PO-review grooming pass:

The cascade-cluster tickets already had explicit ranks (100, 200, 300, 400,
500, 600, 700) assigned in commit `402f0a4`. To rank 16 *additional*
unranked high-priority items, I would have called:

```
pyrite sw prioritize \
  plugin-registry-silent-failures task-index-timestamp-drift ... \
  -k pyrite
```

If I had — and the tool would have started numbering from 100 — every
cascade-cluster rank would have been silently shadowed: two items both
holding `rank: 100`, two at `rank: 200`, etc. The cascade cluster's
explicit ordering (which is load-bearing for the cascade-deprecation
sequence) would have been clobbered without warning.

I had to fall back to **16 separate `pyrite update -f rank=N` calls** to
avoid the collision. The workaround works but is slow and easy to forget;
the next agent or human won't know to use it.

## Impact

- **Silent rank-clobbering** is a data-loss-class bug: ranks the user spent
  time assigning vanish into rank-collision pools the user has no easy way
  to detect. Same family as the recent metadata-merge / metadata-string /
  rank-projection bugs we've been finding regularly.
- **Grooming hygiene depends on this tool.** If `prioritize` is unsafe to
  call, every grooming pass goes the slow route or risks losing prior
  work. Compounds with
  [[bug-pyrite-get-omits-rank-field-in-json-output]] — that one hides
  ranks at read time, this one destroys them at write time.
- **The audit caught this pattern previously.** The previous PO-review
  pass (commit `402f0a4`) noted "`pyrite sw prioritize` rewrites every
  ranked ticket and relocates them to `kb/notes/`" — but only flagged the
  relocation, not the renumbering. This ticket nails the second half.

## Solution

Pick one (Option A is principled, B is minimal):

### Option A — `--after <id>` / `--before <id>` mode that preserves existing ranks

The CLI help already advertises `--after TEXT Place item after this ID` and
`--before TEXT Place item before this ID` flags. Make them do what they
say: if `--after remove-cascade-plugin` is passed, the next free rank slot
is `prior_max + 100` (or `anchor_rank + 100`, whichever is greater), and
subsequent items get `+100` from there. No other items are touched.

Acceptance: `pyrite sw prioritize --after remove-cascade-plugin item-a
item-b -k pyrite` assigns `item-a` the next free slot after rank 700
(so 800), `item-b` 900, and leaves cascade-cluster ranks 100-700 intact.

### Option B — Detect collision and refuse

If the no-flag form is called and any of the about-to-assign ranks
already exist on items NOT in the argument list, abort with a clear
error: "rank 100 is already held by `ji-absorb-cascade-types`; pass
`--after <id>` or `--before <id>` to anchor your ranking, or
`--allow-collisions` to override."

Either way, add a regression test that asserts `prioritize` of a new
batch does not modify ranks on already-ranked items outside the batch.

## Acceptance criteria

- `pyrite sw prioritize` cannot silently overwrite an existing rank on
  an item not named in the call. Either it anchors to a free range
  (Option A) or it refuses to run (Option B).
- A regression test seeds two ranked items, calls `prioritize` on a
  third, and asserts the first two ranks are unchanged.
- CLI help text for `--after` / `--before` documents the anchor
  semantics if Option A is taken.

## Related

- [[bug-pyrite-get-omits-rank-field-in-json-output]] — companion meta-bug
  in the same workflow. Together they silently break grooming hygiene
  (read-side projection drops ranks; write-side prioritize clobbers them).
- Commit `402f0a4` — the previous grooming pass that first established
  the cascade-cluster ranks 100-700; would have been clobbered by a
  naive `prioritize` call had the workaround not been used.
- Commit `adbd426` — the 2026-06-05 grooming pass that hit this for the
  second time and used per-item `pyrite update -f rank=N` × 16 as the
  workaround.
- Adjacent: the `pyrite update`/`pyrite sw prioritize` relocation pattern
  (kb/backlog → kb/notes) — separate wart noted in multiple prior reviews,
  not addressed here.

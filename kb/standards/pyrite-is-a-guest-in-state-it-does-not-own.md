---
id: pyrite-is-a-guest-in-state-it-does-not-own
title: 'Pyrite is a guest in state it does not own'
type: standard
tags:
- standards
- cli
- reliability
importance: 5
---

Maintainer decision, 2026-10-02, from the root-cause analysis of PR #612 (`mcp-setup`, #582).

Some files Pyrite writes belong to another program or to the user: an MCP client's config, a repository's git hooks, a `.claude/` directory, a user's `kb.yaml`, a KB registry. In those files Pyrite is a guest. Four properties hold for every command that writes one.

1. **Decide from the file as it is; record nothing outside it.** Pyrite keeps no record of what it wrote and infers nothing from an entry's name or shape: a user who followed the README has an entry with Pyrite's name and Pyrite's shape that Pyrite did not write. (Reworded 2026-10-02: the first version asked for a record; the maintainer decided files are the source of truth and a sidecar record is not, ADR-0041.)
2. **A write does what was asked, loses nothing, and reports it.** Running the command is the request; the ordinary path never needs `--force`. Absent: write it. Equal: write nothing and say so. Differs only in what was asked: change exactly that, and report old and new. Carries something the request would not (env, extra arguments, unknown keys): keep it and change only what was asked, or stop if it cannot be kept. Where a change would discard something Pyrite cannot carry over, stop and name `--force`, which means only "discard what the user wrote". Every replacement prints what it replaced, so it can be undone.
3. **The rest of the file comes back unchanged, or the file is refused untouched.** Whatever Pyrite does not own must survive a load and a save: other entries, key order where the host cares, encoding, a symlinked path, values the serializer cannot represent. If the round trip cannot be shown to be lossless, do not write.
4. **The change is all or nothing.** A failure part-way leaves the file as it was. Remove-then-add is two steps; a file write is atomic.

Why: #612 destroyed user configuration in two successive cold reads. The first fix changed the ownership test from the entry's name to its shape; the second read found that the shape is the one the README teaches users to write by hand. The groom's invariant described only what a new user gets ("what `mcp-setup` writes is what the client reads"), while its acceptance required replacing an entry on re-run and deleting a stale trio, with no rule for telling Pyrite's entries from the user's. The file-format findings (a symlinked config, encoding, a number rewritten as `Infinity`, duplicate keys) arrived one at a time because nothing stated property 3. On 2026-09-23 a write to a default path emptied a KB registry: the same missing model.

How to apply:
- A groom for a command that writes outside Pyrite's own data asks: whose state is this, who else writes it, and what does a user who set it up by hand have on disk? The existing user is a regime, not a footnote under "what the change breaks".
- Tests seed the file with what the docs teach users to write, not only with entries the implementer invented.
- One property test covers 3: for a set of host files, load then save with no change of Pyrite's own is byte-identical, or the command refuses.
- Reviews treat "it has our name", "it looks like ours" and "we recorded that we wrote it" as findings.

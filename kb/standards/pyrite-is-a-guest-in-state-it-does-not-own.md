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

1. **Ownership is recorded, not inferred.** Pyrite changes only what it can show it wrote. A name or a shape is not evidence: a user who followed the README has an entry with Pyrite's name and Pyrite's shape that Pyrite did not write. Where the host format allows a marker, write one; where it does not, keep the record on Pyrite's side.
2. **What Pyrite cannot show is its own, and differs from what it would write, is refused.** The refusal names what differs. `--force` overrides it; nothing else does. Preserving the user's part and saying what changed is also acceptable; replacing it silently is not.
3. **The rest of the file comes back unchanged, or the file is refused untouched.** Whatever Pyrite does not own must survive a load and a save: other entries, key order where the host cares, encoding, a symlinked path, values the serializer cannot represent. If the round trip cannot be shown to be lossless, do not write.
4. **The change is all or nothing.** A failure part-way leaves the file as it was. Remove-then-add is two steps; a file write is atomic.

Why: #612 destroyed user configuration in two successive cold reads. The first fix changed the ownership test from the entry's name to its shape; the second read found that the shape is the one the README teaches users to write by hand. The groom's invariant described only what a new user gets ("what `mcp-setup` writes is what the client reads"), while its acceptance required replacing an entry on re-run and deleting a stale trio, with no rule for telling Pyrite's entries from the user's. The file-format findings (a symlinked config, encoding, a number rewritten as `Infinity`, duplicate keys) arrived one at a time because nothing stated property 3. On 2026-09-23 a write to a default path emptied a KB registry: the same missing model.

How to apply:
- A groom for a command that writes outside Pyrite's own data asks: whose state is this, who else writes it, and what does a user who set it up by hand have on disk? The existing user is a regime, not a footnote under "what the change breaks".
- Tests seed the file with what the docs teach users to write, not only with entries the implementer invented.
- One property test covers 3: for a set of host files, load then save with no change of Pyrite's own is byte-identical, or the command refuses.
- Reviews treat "it has our name" or "it looks like ours" as a finding.

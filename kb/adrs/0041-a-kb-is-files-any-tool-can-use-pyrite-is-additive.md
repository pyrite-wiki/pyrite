---
id: adr-0041
type: adr
title: "A KB is files any tool can use; Pyrite is additive"
adr_number: 41
status: proposed
date: 2026-10-02
tags: [architecture, storage, files, principles]
links:
- target: adr-0001
  relation: related
  kb: pyrite
- target: adr-0038
  relation: related
  kb: pyrite
- target: adr-0039
  relation: related
  kb: pyrite
- target: adr-0042
  relation: related
  kb: pyrite
- target: adr-0043
  relation: related
  kb: pyrite
- target: adr-0044
  relation: related
  kb: pyrite
- target: adr-0045
  relation: related
  kb: pyrite
- target: pyrite-is-a-guest-in-state-it-does-not-own
  relation: related
  kb: pyrite
---

# ADR-0041: A KB is files any tool can use; Pyrite is additive

> **Proposed** (2026-10-02). The maintainer decided the principle below on
> 2026-10-02 and accepts or rejects this text. It states the principle only.
> The mechanics are in ADR-0042 (entry writes and reads, identity), ADR-0039
> (the registry and state), ADR-0043 (authorization), ADR-0044 (where a
> write lands) and ADR-0045 (types), each also proposed. A revision of the
> first draft of this ADR and of ADR-0042, after an adversarial read; what
> that read found is answered in ADR-0042 and ADR-0039.

## Context

ADR-0001 made Markdown files with YAML frontmatter the source of truth and
the database a derived index. ADR-0029 traced every recurrent field bug to
derived state diverging from its source. Both say where the truth is.
Neither says who the files belong to, or what Pyrite owes someone who edits
them without Pyrite.

Three pieces of work on 2026-10-02 failed for want of that statement:

- `pyrite mcp-setup` (#582, PR #612) rewrote other programs' config files,
  inferred which entries were its own from their shape, and failed two cold
  reads at seven times its groomed size.
- A survey reproduced losses on a no-op load and save of an entry file:
  frontmatter comments, a byte-order mark, CRLF line endings and the
  indentation of the body's first line were dropped, and a symlinked file
  was replaced by a regular one. Over 28,123 real entry files, a load and
  save with no change altered 7,206 (25.6%); no value of any key changed
  (measurement in ADR-0042).
- The update path decided what a caller had changed by comparing the request
  with the index row, not the file (#569), so a hand edit not yet synced
  could be overwritten.

The cause is the same in each. Every Pyrite interface (CLI flags, MCP and
REST JSON, the web form) is a non-YAML interface for editing YAML files.
Pyrite parsed the file into a model and wrote the whole file back, as if the
file were Pyrite's own serialisation of that model.

## Decision

**1. A KB is a directory of Markdown files with YAML frontmatter, in git.**
Any tool that handles such files can use it: an editor, `grep`, a script,
another agent. A KB that Pyrite has never touched is still a KB. Publishing
one to a static-site generator is an export: what that generator reserves or
expects is the exporter's concern, not a property of the KB.

**2. The files belong to the user.** Pyrite is a guest in them, as in any
state it does not own (the standard
`pyrite-is-a-guest-in-state-it-does-not-own`).

**3. Pyrite is additive** to someone who maintains the files by hand, enough
that it is their preferred way to work with them. It makes such a KB easier
to work with (search, links, validation, types, agents, a web view) and never
makes maintaining the files by hand worse. Additive means:

- Removing Pyrite loses nothing of the KB. What matters is in the files.
- Hand edits and edits by other tools are first-class. Pyrite reads the file
  as it is when it acts, never a remembered copy.
- Pyrite does not require a file to be spelled its way in order to work with
  it.

**4. A write does what was asked, loses nothing, and reports it.** The
ordinary path does not need `--force`: a flag that is always typed protects
nothing. `--force` means one thing: discard what the user wrote.

**5. Pyrite keeps no record outside a file of what is in it or who wrote
it.** No sidecar, no marker inferred from an entry's shape, and no index row
acting as the tiebreaker.

**6. Spelling is chosen only on create, and in commands whose stated purpose
is to respell** (a format or migrate command). Those produce their own
commit and are never the side effect of an unrelated edit.

**7. Local use by one operator comes first, and contracts are alpha.** Nothing
in this set freezes an interface. Multi-user access stays experimental until
its design is accepted (ADR-0043, ADR-0044).

## Acceptance

Stated as documentation that runs: the passage below is written first, and
its examples are the test. The runner is the one ADR-0042 specifies, over a
corpus of entry files written by hand and by other tools, not by Pyrite
(files Pyrite wrote prove little).

> **What Pyrite does to your files**
>
> Edit one field with Pyrite and `git diff` shows that field's lines and
> nothing else. Your comments, quoting, line endings, blank lines and key
> order stay. Send Pyrite a change that is already true of the file and it
> writes nothing and says so. Upgrade Pyrite and open your files: nothing
> changes. If Pyrite cannot change a file safely it leaves it untouched and
> tells you why. Delete Pyrite's database and rebuild it from your files:
> nothing you wrote is lost.

1. A one-field edit through any interface produces a diff of exactly that
   field's lines.
2. A no-op update produces an empty diff, and the response says nothing was
   written.
3. Upgrading Pyrite, then editing a file, produces no diff beyond the edit.
4. A file Pyrite cannot change safely is refused untouched, and the refusal
   says why.
5. Rebuilding the index from the files loses nothing that is in a file. (What
   a database rebuild does lose, and why that is acceptable, is ADR-0039's
   list.)

## Consequences

- **Entry writes are operations on the file's own value** (ADR-0042). The
  model validates the result and never supplies bytes for an existing file.
- **The guest standard is amended** in one sentence, once this ADR is
  accepted. Rule 1 of `pyrite-is-a-guest-in-state-it-does-not-own` ends "...
  where it does not, keep the record on Pyrite's side." Decision 5 forbids
  that record. The standard's rule 2 ("is refused. ... `--force` overrides
  it") is read through decision 4: refusal is for a file Pyrite cannot show
  it may change; a change the user asked for is not refused.
  This text does not edit the standard before acceptance.
- **`mcp-setup` follows the same rule** for client config files (#582).
- **The local operator is the owner.** Whoever holds the files holds the KB.
  The CLI and a local MCP server act as that person (ADR-0043). Multi-user
  access control is a layer over this, not its foundation.
- **ADR-0001** is extended, not amended: its consequence "Entries are
  readable and editable in any text editor" now binds Pyrite as well.
- **Tests use files Pyrite did not write.** A round-trip test over Pyrite's
  own `kb/` proves little. The corpus is hand-authored shapes and other
  tools' output.
- **A cost:** Pyrite can no longer silently repair or normalise. A stale
  value, an odd spelling or an old layout stays until the user asks for it
  to change; `qa` reports it.

## Alternatives considered

- **Pyrite owns the serialisation of the files** (today's design, with more
  rules to remember what the file looked like). Rejected: 25.6% of untouched
  real files change, after seven issues' worth of rules.
- **"Hugo-style content files" as the definition** (the first draft of this
  ADR). Rejected: Pyrite KBs are not Hugo content. Wikilinks render as
  literal text there, `type`, `aliases` and `url` mean different things in
  Hugo, and a real Hugo site built from a Pyrite KB needed a converter of
  more than a thousand lines. Publishing is an export.
- **Support TOML and JSON frontmatter.** Out of scope. Today both are
  reported as malformed and left untouched.

## Questions for the maintainer

1. **Where the line is for `--force`** in commands that already prompt or
   force (`db restore`, delete, the seven `typer.confirm` sites). To settle
   with the CLI contract (#303, 0.25.8). Blocks nothing in this set.

Decided and recorded rather than asked: the Hugo wording is dropped
(2026-10-02); keys Pyrite needs, and fields Pyrite sets itself, are in
ADR-0042; server-side state and the registry are ADR-0039.

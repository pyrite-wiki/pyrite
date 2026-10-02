---
id: design
title: "Pyrite's design, on one page"
type: note
tags:
- design
- architecture
- principles
---

# Pyrite's design, on one page

> Draft for the maintainer's approval (2026-10-02). It states the design as
> decided that day. Where the code does not match yet, the last section says
> so. Read this before a ticket, a groom or a review; judge the work against
> it.

## What Pyrite is

A knowledge base is a directory of Markdown files with YAML frontmatter, in
git. Pyrite adds types, an index, search, links, validation and an interface
for agents (a CLI, an MCP server) and people (a web view). Its purpose is to
make knowledge that agents write easy for a person to trust.

It is built first for one operator working locally, usually through a
terminal coding agent. Multi-user is experimental. The plugin and API
contracts are alpha.

## The principles

1. **The files are the KB.** Any tool can use them. A KB Pyrite has never
   touched is still a KB, and removing Pyrite loses none of its content.
2. **Pyrite is additive.** It must never make maintaining the files by hand
   worse. Hand edits and other tools' edits are first-class. Pyrite reads a
   file as it is at the moment it acts, and does not require a file to be
   spelled its way.
3. **A write does what was asked, loses nothing, and reports it.** A write is
   an operation on the file's own value: set a field, append an item, remove
   a key. Pyrite emits no bytes for anything it was not asked to change, and
   a request that changes nothing writes nothing. `--force` means only
   "discard what the user wrote"; the ordinary path never needs it.
4. **Derive; do not write.** What can be computed from the files lives in the
   index and is shown as derived: links, rollups, readiness, counts. A hook
   may refuse a write. It may not change one.
5. **The file decides.** Pyrite keeps no record outside a file about what is
   in it or who wrote it. The index is a cache and never the tiebreaker. An
   entry's identity is its path, unless an `id:` pins it.
6. **Types give structure; protocols give behaviour.** A type is a schema. A
   protocol is a set of fields plus the derived and explicit operations the
   platform provides for them. Extensions add types, protocols and tools.
   Serialising a file is not a type's job.
7. **The operator owns everything that is not an entry.** Users read and
   write entries, per KB, by grant. The registry, schemas, settings, grants
   and keys are the operator's, kept in a documented config file the operator
   edits. Whoever holds the files is the operator. The database holds the
   derived index and machinery, never the only copy of content or
   configuration.
8. **Each rule lives in one place.** A property is enforced at the single
   point everything passes through, with a test that fails when new code goes
   around it. A rule enforced call site by call site is not yet a rule.
9. **A small supported surface.** What is supported is listed and works as
   documented. The rest is marked experimental. A doc that teaches a command
   is run as that command's test.

## Questions to ask of any ticket or change

- Would someone who edits these files by hand be glad Pyrite touched them?
- Does it write anything the caller did not ask for?
- Can this value be computed from the files? Then derive it.
- If the database were deleted, what would be lost?
- Is it an entry (users, by grant) or configuration (the operator)?
- Where is the one place this rule is enforced, and what fails if code goes
  around it?
- Is the ticket a symptom of a model nobody has stated? State the model
  before fixing the instance. A ticket's suggested fix is evidence, not the
  plan.

## What Pyrite is not

- Not a site generator. Publishing is an export.
- Not a formatter. It does not normalise or repair files unless asked to.
- Not the owner of your repository, your config, or another program's files.
- Not yet a service for many users who do not trust each other.

## Where to read next

| Topic | Governing ADRs |
|---|---|
| Files, index, what is derived | [[adr-0001]], [[adr-0029]] |
| The principle | ADR-0041 (proposed) |
| Writes, reads, identity, hooks | ADR-0042 (proposed), [[adr-0038]] (proposed) |
| Registry, config, state | ADR-0039 (proposed), [[adr-0029]] |
| Who may do what | [[adr-0037]], ADR-0043 (proposed) |
| Types, protocols, plugins | [[adr-0014]], [[adr-0017]], [[adr-0040]], ADR-0045 (proposed) |
| Review of changes | ADR-0044 (proposed) |
| What the alpha supports | `alpha-supported-surface` (proposed) |
| Releases | [[roadmap]], "The release line" |

Working in a guest file: [[pyrite-is-a-guest-in-state-it-does-not-own]].
`pyrite sw adrs` lists every ADR; `pyrite sw components` maps the code.

## Where the code does not match yet

The design above is ahead of the code in these places. Do not assume the
code follows it; check, and treat a mismatch as the work to do.

- An update re-serialises the whole file from the model (principle 3).
- Save hooks write links and parent status into files (principle 4).
- An entry with no `id:` takes its identity from its title (principle 5).
- A single-entry read comes from the index, not the file (principle 5).
- Entry classes and protocol mixins serialise themselves (principle 6).
- KBs can exist only as database rows; grants and secrets are not in a
  reviewable config file (principle 7).
- Several rules are enforced per call site (principle 8).
- No supported-surface list is published (principle 9).

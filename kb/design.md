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

> Approved by the maintainer, 2026-10-02. Where the code does not match yet,
> the last section says so. Read this before a ticket, a groom or a review; judge the work against
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
2. **Pyrite is additive, and its contract with your files is explicit.** It
   must never make maintaining the files by hand worse. Hand edits and other
   tools' edits are first-class, and Pyrite reads a file as it is at the
   moment it acts. What it asks of a file is written down and small: YAML
   frontmatter, and an `id:`. Beyond the contract it does not require a file
   to be spelled its way. A file outside the contract is reported, never
   silently repaired.
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
   entry's identity is its `id:`. A file with none is addressed by its path
   and reported as missing one.
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

- Not a site generator. Publishing is an export. The built-in `/site`
  ([[adr-0023]]) is experimental.
- Not a formatter. It does not normalise or repair files unless asked to.
- Not a database with instant indexes. Search, lists and links come from the
  index, which catches up with the files after a write or a hand edit; a
  single-entry read reads the file. A result may lag a change made a moment
  ago: the index may lag the files.
- Not a replacement for git. Changes are shared as commits, branches, patches
  and pull requests; Pyrite's own conflict handling covers only local edits
  between commits.
- Not the owner of your repository, your config, or another program's files.
- Not yet a service for many users who do not trust each other.

## Where to read next

| Topic | Governing ADRs |
|---|---|
| Files, index, what is derived | [[adr-0001]], [[adr-0029]] |
| The principle | ADR-0041 (accepted) |
| Writes, reads, identity, hooks | ADR-0042 (accepted), [[adr-0038]] (accepted) |
| Registry, config, state | ADR-0039 (accepted), [[adr-0029]] |
| Who may do what | [[adr-0037]], ADR-0043 (accepted) |
| Types, protocols, plugins | [[adr-0014]], [[adr-0017]], [[adr-0040]], ADR-0045 (accepted) |
| Review of changes | ADR-0044 (proposed) |
| What the alpha supports | `alpha-supported-surface` (approved 2026-10-02) |
| Releases | [[roadmap]], "The release line" |

### Topic maps

One short page per area: the design today, invariants a test could check, the
ADRs in reading order, where the code starts, the tests that pin it, and the
known gaps. Cite one in a groom's "contracts that apply". For the lessons
behind the principles, and one row per ADR, see [[adr-learnings]].

| Question | Map |
|---|---|
| How is an entry written, read, identified? May a hook change it? | [[map-write-read-path-and-identity]] |
| What can I delete and rebuild? What is derived? | [[map-index-database-and-derived-state]] |
| Who can add a KB? Where does config, a secret, state live? | [[map-registry-config-and-state]] |
| Who can do what? What does a refusal look like? | [[map-who-can-do-what]] |
| Where does a user's write land? How does it reach main? | [[map-collaboration-and-review-of-changes]] |
| How do I declare a type, a field, an enum, a state machine? | [[map-types-and-schema]] |
| How do plugins add a type? What is the plugin contract? | [[map-plugins-protocols-and-extensions]] |
| What does search accept? Does a write wait for embedding? | [[map-search-and-embeddings]] |
| What do the CLI, MCP, REST and web return? How large? | [[map-surfaces-and-output-contracts]] |
| How are entries linked? What is a collection or an edge? | [[map-links-blocks-collections-and-formats]] |
| Which LLM providers? Who enforces a rule when another agent runs the work? | [[map-ai-integration-and-agent-runs]] |
| How does an agent claim work? Tasks, kanban, gates? | [[map-work-coordination-and-agent-teams]] |
| What test fails if code goes around this rule? | [[map-validation-qa-and-pinning-tests]] |
| How does a change reach `dev` and `main`? Where do I file it? | [[map-development-process-and-releases]] |

Working in a guest file: [[pyrite-is-a-guest-in-state-it-does-not-own]].
`pyrite sw adrs` lists every ADR; `pyrite sw components` maps the code.

## Where the code does not match yet

The design above is ahead of the code in these places. Do not assume the
code follows it; check, and treat a mismatch as the work to do.

- An update re-serialises the whole file from the model (principle 3).
  [How Pyrite edits your files](../docs/how-pyrite-edits-your-files.md) holds
  the examples a fix must pass; `tests/test_doc_write_as_patch.py` runs them
  and lists each one the code fails today, and how.
- Save hooks write links and parent status into files (principle 4).
- An entry with no `id:` takes its identity from its title, and no command
  lists or pins the files missing one (principle 5).
- A single-entry read comes from the index, not the file, and returns the
  type's defaults and normalised values among the file's own fields, so a
  client that sends a read back writes them into the file (principles 3 to 5).
- Entry classes and protocol mixins serialise themselves (principle 6).
- KBs can exist only as database rows; grants and secrets are not in a
  reviewable config file (principle 7).
- Several rules are enforced per call site (principle 8).
- The supported-surface list is approved but not yet reflected in the
  README, `--help` and tool descriptions, and experimental tests are not yet
  marked (principle 9).

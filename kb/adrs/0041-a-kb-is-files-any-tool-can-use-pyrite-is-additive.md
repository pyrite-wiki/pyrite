---
id: adr-0041
type: adr
title: "A KB is files any tool can use; Pyrite is additive"
adr_number: 41
status: proposed
date: 2026-10-02
tags: [architecture, storage, files, round-trip, principles]
links:
- target: adr-0001
  relation: related
  kb: pyrite
- target: adr-0029
  relation: related
  kb: pyrite
- target: adr-0038
  relation: related
  kb: pyrite
- target: pyrite-is-a-guest-in-state-it-does-not-own
  relation: related
  kb: pyrite
---

# ADR-0041: A KB is files any tool can use; Pyrite is additive

> **Proposed** (2026-10-02). The maintainer accepts or rejects it. It records
> the design he stated that day; the consequences and open questions are for
> him to confirm.

## Context

ADR-0001 made Markdown files with YAML frontmatter the source of truth and
the database a derived index. ADR-0029 traced every recurrent field bug to
derived state diverging from its source. Both say where the truth is. Neither
says who the files belong to, or what Pyrite owes someone who edits them
without Pyrite.

Three pieces of work on 2026-10-02 failed for the lack of that statement:

- `pyrite mcp-setup` (#582, PR #612) rewrote other programs' config files,
  inferred which entries were its own from their shape, and failed two cold
  reads at seven times its groomed size.
- An architecture survey reproduced losses on a no-op load and save of an
  entry file: frontmatter comments, a byte-order mark, CRLF line endings and
  the indentation of the body's first line were dropped, and a symlinked file
  was replaced by a regular one.
- The update path decided what a caller had changed by comparing the request
  with the index row, not the file (#569), so a hand edit not yet synced
  could be overwritten.

The cause is the same in each. Every Pyrite interface (CLI flags, MCP and
REST JSON, the web form) is a non-YAML interface for editing YAML files.
Pyrite parsed the file into a model and wrote the whole file back, as if the
file were Pyrite's own serialisation of that model.

## Decision

**1. A KB is a directory of Hugo-style content files: Markdown with
frontmatter, in git.** It is usable by any tool that handles such files: an
editor, Hugo or another static site generator, `grep`, a script, another
agent. A KB that Pyrite has never touched is still a KB.

**2. The files belong to the user.** Pyrite is a guest in them, as it is in
any state it does not own (the standard
`pyrite-is-a-guest-in-state-it-does-not-own`).

**3. Pyrite is additive.** It makes such a KB easier to work with: search,
links, validation, types, agents, a web view. It must never make maintaining
the files by hand worse. The aim is that someone who could do without Pyrite
prefers to work with it. Additive means:

- Removing Pyrite loses nothing of the KB. What matters is in the files.
- Hand edits and edits by other tools are first-class. Pyrite reads the file
  as it is at the moment it acts, never a remembered copy of it.
- Pyrite does not require a file to be spelled its way in order to work with
  it.

**4. A write does what was asked, loses nothing, and reports it.** A write
operation is a request to change something, so the ordinary path does not
ask for `--force`; a flag that is always needed is always typed and protects
nothing. `--force` is kept for one thing: discarding what the user wrote.

**5. A write changes only what was asked.** Pyrite receives values for
particular fields. It works out what differs from the file as it is now and
applies only those changes to the text. It emits no bytes for a field it was
not asked to change.

**6. Pyrite keeps no record, outside a file, of what is in that file or who
wrote it.** No sidecar, no marker inferred from an entry's shape, no index
row acting as the tiebreaker (ADR-0038).

**7. Choosing how a file is spelled happens only on create, and in commands
whose stated purpose is to respell** (a format or migrate command). Those
produce their own commit, never a side effect of an unrelated edit.

## Acceptance

Stated as documentation that runs (doc-driven: the passage is written first
and is the test). For a corpus of entry files written by hand and by other
tools, not by Pyrite:

1. A one-field edit through any interface produces a git diff of exactly
   that field's lines.
2. A no-op update, including a whole-document echo of a read, produces an
   empty diff.
3. Upgrading Pyrite and then editing a file produces no diff beyond the
   edit.
4. A file Pyrite cannot change safely is refused untouched, and the refusal
   says why.
5. Deleting the database and rebuilding it from the files loses nothing the
   user wrote.

## Consequences

- **Entry writes become patches.** The model no longer serialises itself; the
  style-memory code in `pyrite/models/base.py` and `pyrite/utils/yaml.py`
  shrinks. A spike (2026-10-02) is measuring three ways to do it; its ADR
  cites this one. #569 and #628 are held until it reports.
- **The guest standard is reworded** from "refuse what differs" to decision 4
  above, with entry files as its main case.
- **`mcp-setup` follows the same rule** for client config files (#582).
- **The local operator is the owner.** Whoever holds the files holds the KB;
  the CLI and a local MCP server act as that person. Multi-user access
  control is a layer over this, not its foundation.
- **Every KB that is not ephemeral is listed in the config file**
  (maintainer, 2026-10-02). The registry is the one part of Pyrite still
  split between a file and the database: `add_kb` registers a KB as a
  database row only. ADR-0029 §1 already decided that the config file is the
  registry; it is not implemented. A KB that exists only as a row cannot be
  seen in a clone, diffed or reviewed. The config file is itself the user's
  file, so decisions 4 to 6 apply to writing it.
  - **Setting up the registry is an admin task.** Adding, removing or
    re-pointing a KB in the config file is an instance-admin action on every
    surface, whatever route reaches it (create, add, subscribe, fork).
  - **One file per library, not one per machine.** This amends ADR-0029 §1,
    which says `~/.pyrite/` owns the registry and other config files are
    migrated in and retired. A machine may hold several libraries, each with
    its own config file and its own derived database (ADR-0029 §2).
  - **Not decided, noted as a possible direction:** the file may later be
    decomposed, so that a KB a user is permitted to promote out of ephemeral
    is listed in a config file specific to that user. Nothing here should
    make that harder: code asks "which files define this library", not
    "where is the config file".
- **State that exists only in the database is a defect against decision 3**
  where it is the user's work: reviews, stars, KBs registered by a user.
  ADR-0029 §4 already requires a declared list of such state; this makes
  moving the user's part of it into files a requirement.
- **The plugin contract (ADR-0040) is affected**: a plugin that writes entry
  files is bound by decisions 4 to 7. This should be settled before that
  contract is frozen.
- **Tests use files Pyrite did not write.** A round-trip test over Pyrite's
  own `kb/` proves little; the corpus is hand-authored shapes and other
  tools' output.
- **A cost**: Pyrite can no longer silently repair or normalise. A stale
  value, an odd spelling or an old layout stays until the user asks for it
  to change.

## Open questions (the maintainer's)

1. **How far "Hugo-style" goes.** Hugo also reads TOML (`+++`) and JSON
   frontmatter, `_index.md` and page bundles. Which of these must Pyrite
   read, and which may it decline? What today's code does with each is being
   checked by the spike.
2. **Keys Pyrite needs.** If Pyrite requires or adds keys (`id`, `type`) on
   first touching a file it did not create, is that additive, or should
   identity be derivable from the path when the key is absent (ADR-0038)?
3. **Fields Pyrite sets itself** on save, such as an updated timestamp: keep,
   make opt-in, or drop.
4. **Server-side state.** Which database-only state is the user's work and
   moves to files, and which is machinery (sessions, keys, queues) that may
   stay.
5. **Where the line is for `--force`** in commands that already prompt or
   force today (`db restore`, delete, the seven `typer.confirm` sites); to be
   settled with the CLI contract (#303).

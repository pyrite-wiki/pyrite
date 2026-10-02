---
id: adr-0039
type: adr
title: "The registry is the operator's file; state is not content"
adr_number: 39
status: proposed
date: 2026-10-02
tags: [architecture, registry, config, secrets, state, invariants, testing]
links:
- target: adr-0029
  relation: amends
  kb: pyrite
- target: adr-0036
  relation: related
  kb: pyrite
- target: adr-0037
  relation: related
  kb: pyrite
- target: adr-0038
  relation: related
  kb: pyrite
- target: adr-0041
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
---

# ADR-0039: The registry is the operator's file; state is not content

> **Proposed** (2026-10-02). The maintainer accepts or rejects it. It
> **supersedes the earlier proposed text of this number**, "One KB registry: one
> record per KB, one read path, one change event" (2026-09-26, never merged),
> which made the database the registry of record. The maintainer decided the
> opposite on 2026-10-02. This text keeps that ADR's analysis (several
> processes write at once; a change must reach every reader; invariants
> R1 to R10) and applies it to the file.

## Context

A KB has a **membership** (does KB `x` exist, and where are its files) and a
**policy** (its default role, whether anonymous surfaces may show it, whether
it is read-only). ADR-0029 section 1 already decided that a YAML file is the
registry and the database `kb` table a derived cache. The code did not follow.

### Three stores, written by several processes

| Store | Holds | Written by |
|---|---|---|
| The config file's `knowledge_bases` | name, path, type, description, read-only, shortname, repo, ttl, default role | the operator, **and the server**: `EphemeralService` (`services/ephemeral_service.py:112-113`), `RepoService` on subscribe (`services/repo_service.py:593`), `KBRegistryService.update_kb` (`services/kb_registry_service.py:375`) |
| The `kb` table | the same, plus `source` | `KBRegistryService.add_kb`, which registers **a row and no file entry**; `seed_from_config`; and every indexing path |
| An in-process cache of the rows | a config view per process | the process that registered, only |

(Counts, measured 2026-09-26: 17 direct reads of `.knowledge_bases` outside
`config.py`; 103 `config.get_kb(` calls in 39 files; about 60 `PyriteDB(`
constructions that never build the cache.)

- **The file's lock is per process.** `CONFIG_WRITE_LOCK = threading.RLock()`
  (`config.py:1528`). The server, the CLI and a stdio MCP server are separate
  processes; there is no `flock`, `fcntl` or file lock in `pyrite/`, though
  ADR-0029 section 1 specifies "file lock + atomic rename".
- **Ephemeral KBs are written to the file**, against ADR-0029 section 3
  ("NEVER written to the library YAML").
- **Secrets share the file.** `settings.api_key`, `api_keys` and `ai_api_key`
  load from and save to the same file as the registry (`config.py:769-770`,
  `:920-921`), so a reviewable, versionable registry would commit credentials.
- Measured: indexing rewrites a KB's provenance (`config` becomes `user` after
  one sync); a policy value removed from the file survives in the row; a KB
  registered by another process is seen by some readers and not others.
  The root cause: membership and policy have no owner, three stores are each
  written by several paths, and freshness is a duty each writer must remember.

### Why the earlier text chose rows, and what changes

The earlier ADR chose the database because the registry was written by several
processes at once, because what refers to a KB (grants, entries) is already
rows, because "writing a hand-edited file is where the server's config bugs
came from", and because a change signal must be cheap. Those are reasons not to
have the server write the file, not to move the registry. If **no server path
writes the file**, the first and third disappear; the second is answered by
putting grants in the file; the fourth is a stat of one file.

## Decision

**1. The operator's config file is the registry.** Every non-ephemeral KB is
listed in it: name, path, type, description, and its policy fields
(`default_role`, `read_only`, `published`, `landing` of ADR-0044, repo
fields). There is no other record of a non-ephemeral KB's existence or policy.
The `kb` row is derived from the file, rebuilt at load, never authoritative.

**2. One file per library.** A machine may hold several libraries, each with
its own file and its own derived database (this restates ADR-0029 section 2).
Code asks "which files define this library", not "where is the config file".

**3. The server never writes the file.** No REST, MCP, web, worker or service
path changes a registry entry, a grant or a policy field in it. Registry
changes are the operator editing a documented file. A **CLI convenience**
(`pyrite kb add`, `pyrite repo subscribe`) may write it, and only: under a
cross-process file lock, by ADR-0042's rule (an operation on the file's own
value; comments, order and other keys survive), atomically, and refusing a
file it cannot change safely.

**4. Ephemeral KBs are database rows only** and are never in the file
(ADR-0029 section 3, restated). Their names carry the reserved `eph-`
prefix; a name in the file may not.

**5. Secrets are split out of the registry file.** API keys (`api_key`,
`api_keys`, `ai_api_key` and any other credential the code stores) live in a
separate secrets file in the same config directory, owner-readable only, with
environment variables taking precedence. The registry file holds no
credential, so it can be reviewed and put in git. The file's name is an
implementation detail.

**6. Grants are operator configuration and live in the operator's file**, per
KB, beside `default_role` (a user name and a role). Accounts are not
grants: users, sessions, invite codes and user API keys are database state
(decision 7). A grant naming a user who does not exist is dormant and
reported.

**7. Three classes, and the promise.**

| Class | Where | Rebuild loses it? |
|---|---|---|
| KB content | the KB's files in git | no |
| Operator configuration | the registry file, the secrets file | no |
| Derived | index, search, embeddings, link and version rows | rebuilt |
| State | the database: users and accounts, sessions, invite codes, user API keys, queues, claim lease times, stars, reviews (QA verdicts), usage and quotas, engagement counters | **yes** |

**Rebuilding the database loses nothing that is KB content or operator
configuration, and the docs list what it does lose.** The list is generated
from a declared registry of state tables, which ADR-0029 section 4 requires
and which does not exist in code today (no `STATE_TABLES` or equivalent in
`pyrite/`): a test fails if a table is neither declared derived nor declared
state, and `docs/` shows the generated list. Stars and reviews stay in the
database on purpose: a star file would tell every reader of a shared
repository what an investigator is watching; a review changes the blob hash it
is keyed by.

**8. Edit and restart** (decided by the maintainer, 2026-10-02). A process
reads the config file once, at start, and builds one immutable snapshot; the
operator restarts the server and any running MCP process to apply an edit,
and the docs say so. Hot reload is not built now; the paragraph below
describes it and is kept as the design to return to if restarting proves a
burden. *Not decided for now:* each process
stats the file (`mtime_ns`, size) at the start of a decision and re-reads it
when it changed, building one immutable snapshot. When the snapshot changes,
the process diffs old against new and publishes one `KBRegistryChanged` per KB
that changed. Consumers subscribe once: `/ws` and MCP SSE sessions end when a
KB leaves their readable set (ADR-0036), and `/site` drops that KB's pages. No
write path calls a consumer directly. An idle server observes another
process's edit within one sweep (`EXPIRY_SWEEP_SECONDS`, 60 s). (**Question
1**: or restart to apply.)

**9. One read path: a snapshot per decision.** The policy (`kb_exists`,
`kb_default_role`, `kbs_at_tier`), the public KB list, `/site`, the socket and
MCP handshakes, the MCP tools and the CLI take their facts about KBs from one
snapshot. `PyriteConfig.get_kb` and `all_kbs` remain as a facade over it.
`_db_kb_cache`, `register_db_kbs`, `forget_db_kb`, `merge_registered_kbs` and
`yaml_kb` as a policy source go. An untrusted repo-local config is confined
(paths inside its tree, no default role, not published) once, when the
snapshot is built.

**10. Indexing writes only `last_indexed` and `entry_count`.** It never
writes `source`, policy or `path`.

**11. `published` is a per-KB field in the file and is never read from a KB's
own tree** (`kb.yaml`, entries), which anyone who can write the KB can edit.
What anonymous surfaces show is what the policy says the anonymous principal
may read (ADR-0043 decision 8). The upgrade sets `published` to what
`default_role == read` published before, so nothing changes at the version
boundary.

## Invariants

Named **R1 to R10** so they are not confused with ADR-0038's I1 to I10. Each is
a required property a test can check. "A registry change" means the file
changed, by hand or by the CLI convenience, in any process. "Two views" means
two config and database pairs on one index file, as a server and a CLI command
share one.

- **R1: one record per KB.** Every KB a reader can name is listed in the file
  or is an ephemeral row, and every row of either kind names a KB a reader can
  name. Both views agree.
- **R2: one read path.** Outside the registry module and `storage/kb_ops.py`,
  no code reads `knowledge_bases`, a cache or `FROM kb`. The allowlist only
  shrinks (an AST test).
- **R3: indexing writes no policy.** `index_kb`, `sync_kb`, `sync_incremental`
  and every entry save change only `last_indexed` and `entry_count`.
- **R4: one snapshot per decision.** For every KB, in each view: `kb_exists`,
  `kb_default_role`, the public list and `kbs_at_tier` all answer from the same
  snapshot.
- **R5: the next decision everywhere.** After the file changes, the next
  decision in every process reflects it with no restart (if question 1 is
  answered "hot"); an idle server's sessions reflect it within one sweep.
- **R6: one event per change, and every consumer hears it.** Exactly one
  `KBRegistryChanged` per KB per observing process. No module other than the
  dispatcher calls the consumers.
- **R7: a name is released whole.** Removing a KB from the file removes its
  derived rows and `/site` pages; a KB later listed under the same name
  inherits none of them. An ephemeral name never collides with a listed name.
- **R8: the server never writes the file.** No path reached from REST, MCP,
  the web, a worker or a service writes it (a structural test that `save_config`
  is not called from them). The CLI convenience holds a cross-process lock; two
  concurrent invocations both land.
- **R9: confinement at the loader.** Under an untrusted repo-local config,
  every record in the snapshot is inside the config's tree, has no default role
  and is not published, however the row was written.
- **R10: only the file publishes.** `published` changes only by the file. A
  `kb.yaml` or an entry that claims otherwise publishes nothing.
- **S1: the state promise.** Every database table is declared derived or state;
  dropping the derived tables and rebuilding loses no content and no
  configuration; the generated docs list equals the declared state tables.

## Migration

Each step is one reviewable PR; each removes its expected failures from the
invariant harness.

| # | Step | Invariants |
|---|---|---|
| 1 | Indexing stops writing provenance | R3 |
| 2 | The invariant harness: two views on one index file; one expected failure per known violation | all |
| 3 | The snapshot and the facade; the cache and its merge sites go | R1 (reads), R4, R5 (requests), R9 |
| 4 | One event; the four direct consumer calls go | R5 (idle), R6 |
| 5 | The server stops writing the file: ephemeral rows only; `kb add`, subscribe and fork become CLI conveniences or are removed from REST and the web. One-time report of `source = user` rows with no file entry, with a command that prints the YAML; one-time report of server-written entries in the file, with a command that removes them after a backup | R1, R7, R8 |
| 6 | Secrets split out | (acceptance) |
| 7 | Grants move into the file; the grant routes in `admin.py` and the `kb_permission` writes go | with ADR-0043 |
| 8 | The state-table registry and the generated docs page | S1 |
| 9 | The AST guard for R2 | R2 |

## Acceptance

Doc-driven. The passage is `docs/your-config-file.md`;
`tests/test_doc_config_file.py` runs it with two processes on one library.
Command spellings are illustrative (#303).

> **Your config file is your registry.** Every KB Pyrite knows is listed in
> it, except ephemeral ones, which exist only in the database. Pyrite never
> edits it while a server runs. You edit it, or you run a command that edits
> it while keeping your comments. API keys are not in it. Delete the database
> and rebuild: your KBs, your grants and your settings are all still there.
> `pyrite config state` lists what you would lose.

```config
library: field
knowledge_bases:
  - name: notes
    path: ./notes
    default_role: read
  - name: drafts
    path: ./drafts
grants:
  drafts: {alice: write}
```
- Start a server; `GET /api/kbs` lists `notes` and `drafts`.
- Create an ephemeral KB through the API: the file's bytes are unchanged.
- Add a KB block with a text editor: the next request lists it (or after a
  restart, if question 1 is answered that way).
- `pyrite kb add` keeps your comments and key order, and two concurrent
  invocations both land.
- Delete the index database and rebuild: `GET /api/kbs`, the grant and the
  settings are unchanged; `pyrite config state` lists users, sessions, stars,
  reviews and the other state tables, exactly the declared list.
- `grep` the file for the API key: no match.

## Amends

ADR-0029 is accepted. This ADR amends these sentences, once accepted:

- **Section 1**, "`pyrite kb create` / `kb add` WRITE that file (ruamel
  round-trip, file lock + atomic rename)": the server never writes it; the CLI
  convenience may, under a cross-process lock, by ADR-0042's rule. A round
  trip respells the file.
- **Section 1**, "The user-level dual-YAML drift is resolved by consolidation:
  `~/.pyrite/` owns the registry; `~/kb/config.yaml` is migrated in and
  retired", and "exactly one file": read as one file **per library**
  (section 2). A machine may hold several; none is retired by consolidation.
- **Section 4**, "Row-shaped data -- claim leases, grants, quotas,
  engagement counters -- was never KB content and does not become files":
  **grants** leave the list. They are operator configuration, in the file.
  Claim leases, quotas and counters stay as written.
- **Section 4**, "Grants (hosted multi-user): which user sees which KB is
  state, not registry": replaced by "is operator configuration and is in the
  registry file". The next sentence, "Users never mutate the library file;
  operators version it", stands, and is the reason.
- **Section 4**, "Recovery for state is DB backup/restore (now load-bearing)":
  true of the state class (decision 7); grants now recover from the file.
- **Phasing, 0.25**, "`kb add`/`create` write it; DB demoted to verified
  cache": as section 1 above.

Kept: section 1's "DB `kb` table is demoted to a derived cache"; section 2;
section 3 (including "NEVER written to the library YAML", which the code now
has to follow); section 4's `rebuild` drops derived tables only, and its
deferred `state.db`; section 6. ADR-0001's "engagement data (SQLite,
local-only)" is consistent.

## Consequences

**Easier**
- A registry change is a diff in a file: reviewed, versioned, reconstructable
  from a clone, as ADR-0029's first rejected alternative asked.
- One writer class (the operator, or the CLI acting as them) and no
  multi-process write race at runtime.
- The registry can be reviewed without credentials in it.
- A list of what the database holds that a rebuild loses, generated and tested.

**Deleted**
- `_db_kb_cache`, `register_db_kbs`, `forget_db_kb`, `merge_registered_kbs`,
  `yaml_kb` as a policy source, `_yaml_origin`, the `source` column's
  provenance role; the server's three writes of the file; `_as_loaded`,
  `_file_plus_changes` and `check_config_save`'s server case; the cache-then-row
  fallback in `AccessPolicy.kb_default_role`; the grant routes in `admin.py`
  and their `kb_permission` writes.

**Harder**
- **A behaviour change.** `/repos/subscribe` and `/repos/fork` sit behind the
  write tier today (`repos.py:108`); the web's KB forms and the grant routes
  write rows. They stop registering. The operator edits the file (ADR-0043
  decision 5). Question 2.
- An operator must edit a file to add a KB or a grant. A typo is a load-time
  error with a message, not silent divergence (ADR-0029's consequence).
- Hosted deploys: `deploy/*/seed.sh` write `/data/config.yaml` on a data
  volume. That file is the operator's; no read-only-container case was found.
- A user-created, non-ephemeral KB on a hosted instance cannot exist without
  the operator. Promotion out of ephemeral is the operator listing it
  ("graduate", ADR-0029 section 3). Not implemented today.
- Two upgrade reports (rows with no entry; server-written entries) and a
  backup before any removal.

**Direction, not decided here:** a per-user file, owned by the user, that the
operator's file delegates to, for capabilities (ADR-0043). Nothing here makes
that harder.

## Alternatives considered

- **The `kb` table as the registry of record** (the earlier text of this
  number). Rejected by the maintainer: the registry becomes the one thing that
  cannot be reconstructed from a clone, diffed or reviewed (ADR-0029's first
  alternative). Its multi-writer argument is answered by removing the writers.
- **The server writes the file, under a better lock.** Rejected: the file is
  also written by hand, and "writing a hand-edited file is where the server's
  config bugs came from" (`check_config_save`, #377, the 409 for hand-written
  KBs).
- **Grants as database rows** (ADR-0029 section 4 as written). Rejected:
  "which user sees which KB" is a reviewable fact about the library, and
  rows would be lost on a rebuild.
- **Stars and reviews as files.** Rejected: privacy in a shared repository, and
  a review's key is the hash of the file it would live in.

## Questions for the maintainer

Ranked by what they block.

**Decided by the maintainer, 2026-10-02** (the item is kept below for its
reasoning): question 1, edit and restart. Editing the config is a documented
operator task; no change detection is built now (decision 8). R5's
"no restart" clause does not apply.

1. **Hot reload or restart.** Does a running server and a running MCP process
   pick up an edit of the file's KB list, policy and grants (decision 8), or is
   "edit and restart" the documented rule? *Recommended: hot, by the stat
   check; settings still need a restart.* Otherwise the file is the one input
   whose changes lag. Blocks R5 and step 3.
2. **The web and REST registry forms.** Subscribe, fork, add KB and the grant
   forms stop writing. Remove them for now, or show the block to paste into the
   config file? *Recommended: remove from REST and the web; the CLI convenience
   stays.* Blocks step 5.
3. **Accounts are database state.** Users, sessions, invite codes and user
   API keys stay in the database (they carry password hashes and keys, which
   must not be in a reviewable file); grants name users. *Recommended: yes.*
   Blocks step 7.

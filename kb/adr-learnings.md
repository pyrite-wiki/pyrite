---
id: adr-learnings
title: "What the ADRs taught: key learnings by lesson and by ADR (index of all 45)"
type: note
tags:
- design
- adr
- learnings
- index
- decisions
- principles
---

# What the ADRs taught

Layer 2 of the design docs. [[design]] is the one page (the nine principles,
written P1 to P9 below). This entry is read when you need the *why* behind a
principle, or the ADR that taught a lesson, without reading 45 ADRs in date
order. The [[adr-audit-2026-10]] gives each accepted ADR's true standing; the
topic maps (table in [[design]]) answer "what is the design of X".

How to read it. **Part 1** is the durable lessons, most shaping first, each
with the ADRs that taught it and what happened when it was ignored. **Part 2**
is one row per ADR. **Part 3** lists ADRs whose learnings contradict each other
and places where [[design]] says something no accepted ADR supports.

On 2026-10-03 the maintainer accepted ADR-0038 and ADR-0039 and ADR-0041 to
ADR-0045 except ADR-0044, which stays `proposed`. [[adr-0030]] and [[adr-0028]]
are deferred (status `proposed`, the nearest allowed value); [[adr-0031]] is
withdrawn (`superseded` by ADR-0043).
"New" marks a lesson no ADR states as a lesson; it is read out of two or more
ADRs.

## Part 1. By learning

### A. Where the truth lives

1. **Derived state that diverges from its source is the recurring bug class.**
   Keep one source, derive the rest, and show derived values as derived.
   [[adr-0029]] opens with it ("every recurrent field bug traces to derived
   state diverging from its source of truth": two config files disagreeing on
   about 25 KBs, a DB table bridged by a cache, claim liveness inferred from
   file mtimes). [[adr-0036]] (a readable set resolved at connect outlived the
   credential), [[adr-0038]] (lookup, three index walks and history each decide
   what an entry is; 9 of 10 invariants fail on the code it measured),
   [[adr-0025]] (the version in `pyproject.toml` and `web/package.json`
   drifted), [[adr-0033]] (the same item in two trackers). *Ignored:* the
   registry bug recurred after [[adr-0029]] named it, because the ADR was not
   built ([[adr-0029]] sections 2 to 4). **New:** [[adr-0022]] stated the rule
   in March 2026 ("backlinks are stored in the index only, no cascading
   writes"), eight days after [[adr-0014]] defined `rollup` as a write to the
   parent; the save hooks that write links never followed it;
   ADR-0042 and ADR-0045 (accepted) apply it to hooks.

2. **Parsing a file into a model and writing the model back means Pyrite owns
   the file's bytes, and a hand-maintained file loses.** [[adr-0008]] chose
   ruamel.yaml in February "because noisy diffs undermine the model"; ADR-0042
   (proposed) measured that a no-op load and save still changed 7,206 of
   28,123 real files (25.6%), and that a round-trip library alone leaves 5.5%
   (1,535) changed. Seven issues' worth of "remember what the file looked like"
   rules did not close it (ADR-0041, ADR-0042). **New:** the fix named by 0042
   is not a better serialiser but no serialiser for existing files: operate on
   the file's own value, validate the result.

3. **Classify a thing before modelling it: authored entry, configuration,
   computed view, or runtime state.** [[adr-0020]] did it for the kanban
   (milestone is an entry, lanes are config, the review queue is a view:
   "creating files for 'Backlog', 'In Progress' is make-work"). [[adr-0029]]
   named the fourth class (claim leases, grants, quotas are machinery, "never
   KB content") and ADR-0039 (accepted) lists all four classes and what a
   database rebuild loses. **New:** three ADRs over seven months made the same
   split; P4 and P7 are its general form.

4. **The authority for a rule must live outside what the rule governs.**
   ADR-0043 rejects ownership declared in the KB's own tree ("anyone who can
   write the KB could grant themselves access"); ADR-0039 says `published` is
   never read from a KB's tree; [[adr-0040]] says nothing in a KB's files can
   install, import or enable a plugin; [[adr-0029]] gives a deployment a closed
   library file so a peer-facing server "structurally cannot see KBs outside
   its library, before auth is even consulted". **New.**

5. **Identity comes from the least mutable thing the user controls, and a
   design question answered three ways in a week had no stated model.**
   [[adr-0038]] chose `(kb, id)` and, on 2026-09-26, "derive from the filename,
   not the title"; ADR-0042 (accepted) amends that to the path, because
   `_index.md`, `index.md` and same-named files in two folders share a
   filename, and a hand edit of a title otherwise orphans every link. The same
   pattern: the registry was a YAML file ([[adr-0029]]), then a DB table (the
   first text of ADR-0039, never merged), then the operator's file again
   (ADR-0039 now). **New:** a reversal inside days means the constraint was not
   yet written down; write the invariants first ([[adr-0038]] did, as I1 to
   I10).

6. **Name the one atomic operation everything else defers to, and refuse on
   mismatch rather than merge.** [[adr-0029]] (claim CAS is "the one
   concurrency guard"; a lease sits on top), [[adr-0036]] (a revocation epoch
   closes the handshake race), ADR-0042 (a per-file lock and compare-and-replace;
   no `flock` exists in `pyrite/` today), ADR-0044 (a fast-forward is the
   compare-and-swap: "Not possible to fast-forward" refuses), [[adr-0013]] (two
   connections were not atomic). [[adr-0035]] adds the corollary: no new
   background thread where a path with a waiting caller exists. **New.**

### B. How rules are enforced

7. **A rule enforced call site by call site is not yet a rule: one point, and
   a test that fails when new code goes around it.** [[adr-0037]] (the rule
   was shared and "the decision made in many places": 66 REST read declarations
   in 20 files, 161 hand-raised `HTTPException`s; cold reads kept finding one
   surface where a guard failed open). [[adr-0030]] ("a gate that Gemini can
   walk around is not a gate; it is documentation with an exit code"),
   [[adr-0031]] (a mode flag is runtime state; a build split is a product
   boundary, not a security boundary), [[adr-0036]] (explicit calls from each
   endpoint miss every change made inside the service), [[adr-0032]] (a branch
   rule replaces a list of remembered disciplines), [[adr-0034]] ("it is
   documented that the bound is skipped" is no defence), ADR-0043 (an operation
   with no matrix row fails the build). *Ignored:* [[adr-0013]] moved writes to the ORM in
   Phase 1 and left Phases 2 and 3 open; `db.conn` was only deprecated with a
   warning, and 70 uses in
   core and 89 in extensions remain.

8. **Guard strength has levels, and the weakest level passes while the rule
   fails.** [[adr-0037]]: a test that proves where a guard is attached it can
   see a KB does not prove a guard is attached; a presence check passes a wrong
   declaration; only a generated matrix of principals against readable, private
   and missing KBs catches it. Reads became structural first "because a read has
   one shape"; writes have four, so their tests proved weaker things. The
   allowlist of not-yet-migrated entry points "can only shrink", and
   characterization goldens are written before migrating so a migration PR may
   not change one. **New:** name the shapes of the thing before writing the
   guard for it.

9. **Structural beats nominal for agent-facing contracts.** [[adr-0014]]
   (agents can hold many small contracts, so fine-grained structural ones are
   affordable; "a protocol without platform support is just documentation"),
   [[adr-0028]] (a closed field vocabulary and operator status by position, so
   agents rarely escape), [[adr-0019]] (if you can write a check it is a
   `programmatic_validation`; if not, a `development_convention`: "the type
   split is the trust boundary"), [[adr-0037]] (a closed `Action` vocabulary),
   [[adr-0040]] (conformance by AST walk, not by listing plugins). *Ignored by
   its own implementation:* [[adr-0017]] built the protocols by inheritance,
   which ADR-0045 (accepted) names as the disagreement with [[adr-0014]].

10. **Prefer the failure a caller can see, and refuse rather than guess.**
    [[adr-0034]] rejected a bounded-by-default CLI because its failure is
    silent corruption for every script, where the chosen design fails loudly;
    a truncated body is never valid input to a write. [[adr-0035]] turns a
    silent `[]` from semantic search into a warning. [[adr-0008]]'s amendment:
    a `values:` list was silently dropped and one KB carried 70+ off-list
    values "with every validator reporting clean". [[adr-0022]] refuses
    auto-delete ("silent data loss") and leaves a broken reference plus a QA
    warning. ADR-0041 and ADR-0042: a file Pyrite cannot change safely is left
    untouched with a reason; a flag you always type protects nothing.
    [[adr-0037]] and ADR-0043: "unscoped" is constructed on purpose, never the
    default of a forgotten argument; an unreadable KB answers like a missing
    one. ADR-0044: the landing place unreachable means refuse, never fall back
    to canonical.

11. **Put the seam above the engine, and test the seam.** [[adr-0016]] (the
    `SearchBackend` protocol and its 66 conformance tests survived LanceDB's
    removal), [[adr-0015]], [[adr-0028]] ("a patch over FTS5, not an abstraction
    above it": callers had to know the backend's syntax), [[adr-0013]] (the raw
    connection leaked through the seam). *Ignored:* [[adr-0028]] is accepted
    and unbuilt, so callers still write FTS5.

### C. How to know

12. **Measure before debating; a spike with a benchmark settles in an hour
    what a design discussion does not.** [[adr-0016]] (49 to 66 times slower;
    the backend was removed after the benchmark), [[adr-0035]] (a write takes 17 ms with a worker
    attached; the fast path existed and nothing assigned the attribute that
    reached it), [[adr-0034]] (body-size percentiles gave the numbers; one
    hallway read returned 28 times its cap), [[adr-0032]] (22 minutes serial CI
    against 2m19 parallel decided that strict "up to date" needs parallel CI
    first), ADR-0044 (git's `merge-tree` computes a merge in 7 ms without
    touching a checkout), [[adr-0037]] (counts carry the commands that re-run
    them). Verify what an external thing can do before designing on it:
    [[adr-0030]] leaves ACP adapter claims unverified and makes Phase 0 check
    them.

13. **A test is only as good as its oracle and its corpus.** ADR-0042's first
    spike graded its own output with its own equality ("100% identical"
    measured the splice fed the file's own values) on files Pyrite itself had
    written; the revision requires an independent oracle and files nobody at
    Pyrite wrote. [[adr-0038]]'s state machine uses strict xfails that must
    fail with an `AssertionError` naming the invariant (so a crash does not
    count), a pinned seed (deriving it from a class name hid violations when the
    class was renamed), and mutation checks that turn it red. [[adr-0032]]: ~4000
    tests proved the code on three interpreters and nothing proved the assembled
    artifact, and every bug the outside contributor found lived in that gap;
    [[adr-0025]]: one required Python version is thin protection against a bug
    that depends on plugin discovery order; a required check named per matrix leg hung
    docs-only PRs. [[adr-0021]]: criteria that always pass are guidance, not
    gates. **New (as a set):** oracle independence, corpus provenance and
    "can this test fail" are the three questions to ask of any pinning test.

14. **A decision accepted but never built misleads everyone who reads it, and
    so does a status line that lags the code.** [[adr-0028]] (accepted, no
    parser exists), [[adr-0029]] sections 2 to 4, [[adr-0004]] (its hook check
    returns early because hooks never know the user), [[adr-0040]] (sequenced),
    [[adr-0013]] Phases 2 and 3. The reverse lag: [[adr-0037]] kept a "Proposed"
    banner after acceptance; [[adr-0038]] stayed `proposed` with all five
    questions decided and step 1 landed, until 2026-10-03; [[adr-0018]] stayed `accepted` after
    [[adr-0024]] replaced it. [[adr-0032]] carries a `proposed` section (3b) the
    release script contradicts, inside an accepted ADR. **New:** counts and
    contents written into an ADR rot unless the ADR carries the command that
    regenerates them ([[adr-0006]] says "4 software-kb read tools"; the server
    registers 72 / 103 / 112; [[adr-0007]] lists eight endpoints, four exist).

### D. How the work is organised

15. **Review attention is the constraint; spend it on what a rule cannot
    enforce.** [[adr-0019]] (reviewers facing 40 pending items rubber-stamp;
    WIP limits at review), [[adr-0032]] (a permanently red `dev` trained people
    to ignore the signal; a process that needs every session to remember a list
    spends review on what a branch rule enforces for free), [[adr-0033]]
    (capture has to be cheaper than the thing captured), [[adr-0037]] (each
    migration theme is one PR a reviewer can hold), ADR-0044 (the same flow for
    user changes). The roadmap's thesis ("agents write, humans verify") is
    [[adr-0019]] restated.

16. **The unit of isolation is the unit of work, not the actor.**
    [[adr-0032]] (one session, one branch, one checkout; the unit is the
    branch, not the agent). ADR-0044 finds [[adr-0024]]'s worktree per user
    shares one checkout between two sessions of one user, the hazard
    [[adr-0032]] retired, and moves to a ref and a disposable working copy per
    change set. [[adr-0029]] section 3 leases an ephemeral KB to a task, the same unit.
    **New.**

17. **Size the design to the users who exist, and ship the risk-reducing piece
    first.** [[adr-0018]] (forks, GitHub PRs, org hierarchy) was replaced
    within 25 days by [[adr-0024]] "for a small group of investigators"; that
    was redefined by [[adr-0029]] and is replaced in turn by ADR-0044. Three
    collaboration designs for one need. [[adr-0015]] decoupled schema
    versioning from the ODM refactor "right before launch is unnecessary risk";
    [[adr-0023]] abandoned SvelteKit SSR because it needed Node in the image;
    [[adr-0031]] found one "Web UI" hiding four products. ADR-0041 decision 7
    and the alpha supported surface restate it: one operator, local, first.

## Part 2. By ADR

Standing is the audit's ([[adr-audit-2026-10]]), updated for the acceptances of
2026-10-03.
P1 to P9 are the principles in [[design]]. *supports* = the ADR states the
principle or something it follows from; *amends* = it changes or is changed by
it; *predates* = it states the idea before the principle existed.

| ADR | Title | Date | Standing | Decision | Learning | Principles |
|---|---|---|---|---|---|---|
| [[adr-0001]] | Git-native markdown storage | 2025-06-01 | current | Markdown + YAML in git is the source of truth; SQLite is a derived, rebuildable index. | An index you can delete makes every index bug recoverable; the same promise was never made for the files themselves. | supports P1, P5; predates P2 |
| [[adr-0002]] | Plugin system via entry points | 2025-10-01 | current, detail stale; amended by 0040 | Plugins register through the `pyrite.plugins` entry-point group; a `capabilities` set tells the registry what to dispatch. | A 19-method protocol with about 12 empty returns per plugin cost dispatch and hid silent failures; declare scope structurally and treat an undeclared plugin as inert. | supports P6, P8 |
| [[adr-0003]] | Two-tier data durability | 2025-10-15 | superseded by 0029 | Content in git; engagement data in local SQLite. | The engagement tier was the first sighting of "runtime state": neither content nor derived. | predates P7 |
| [[adr-0004]] | Folder-per-author permissions | 2025-10-15 | accepted, inert at app layer | Author folders; a `before_save` hook checks author against folder; CODEOWNERS at the git layer. | A check that never knows the acting user is inert; identity comes from the session, not an argument. | amended by ADR-0043 (accepted); P7; inert until identity reaches hooks |
| [[adr-0005]] | SQLAlchemy ORM with Alembic | 2025-09-01 | current | ORM plus Alembic migrations; raw connection kept for FTS5 and plugin tables. | The "raw connection for FTS5" exception is the origin of the raw-connection sprawl [[adr-0013]] tried to retire. | predates P8 |
| [[adr-0006]] | MCP three-tier tool model | 2025-08-01 | current, detail stale | Read / write / admin tiers; plugins register tools per tier; the server starts at a chosen tier. | A tier is a visibility floor, not an authorization; the tier contents written in the ADR rotted. | amended by ADR-0043 (accepted); P7, P8 |
| [[adr-0007]] | AI integration: three surfaces, BYOK | 2026-02-23 | current, detail stale | One backend behind CLI, MCP and web; Anthropic and OpenAI SDKs only; keys stay server-side; AI is additive. | Keep the dependency you have and use `base_url` for the rest (LiteLLM rejected); everything must work without a key. | supports P9, P2 |
| [[adr-0008]] | Structured data and schema-as-config | 2026-02-23 | current | Types and fields in `kb.yaml`; three schema layers; ruamel.yaml; 2026-10-01 amendment: enums read, checked by one function, enforced per KB. | A declared constraint nobody evaluates is worse than none; round-trip YAML reduced but did not end rewrite noise. | supports P6, P8; predates P3 (round-trip YAML) |
| [[adr-0009]] | Type metadata and AI instructions | 2026-02-23 | current | Types carry `ai_instructions`, field descriptions and display hints; `kb.yaml` overrides plugin defaults; KB policy stays out of plugins. | Types are opaque to agents unless they say how to use them; policy is local, not the plugin's. | supports P6, P7 |
| [[adr-0010]] | Content negotiation and formats | 2026-02-23 | current | `Accept`-header negotiation and a format registry (json, markdown, toon, csv, yaml). | Agents pay a token tax on verbose output; output formats are a layer, never the file's format. | supports P1 |
| [[adr-0011]] | Collections, folder metadata, views | 2026-02-23 | current (phases unchecked) | `__collection.yaml` makes a folder a collection; virtual collections are query entries. | "Folders already are collections": let structure emerge from the file system rather than lay a layer over it. | supports P1, P4 |
| [[adr-0012]] | Block references and transclusion | 2026-02-24 | current | Obsidian-compatible `[[id#heading]]`, `^block-id`, `![[...]]`; a derived block table. | Compatibility with the format users already have beats a new syntax; headings move, so addressable blocks. | supports P1, P4 |
| [[adr-0013]] | Unified DB connection and transactions | 2026-02-25 | accepted, partly implemented | ORM for all writes; raw SQL only for read-only search; Phases 2 and 3 (services, plugins off `db.conn`) future. | Two connections were not atomic; the escape hatch was only deprecated with a warning, so it stayed in use. | predates P8 |
| [[adr-0014]] | Structural protocols for extension types | 2026-03-01 | current (phases 1 to 2 unbuilt, per ADR-0045) | Types satisfy protocols by structure; small primitives compose; platform-backed behaviour; schema as implementation. | Agents can hold many small contracts, so fine-grained structural ones are affordable. | supports P6; amended by ADR-0045 (accepted) |
| [[adr-0015]] | ODM layer and schema versioning | 2026-03-01 | current | `SearchBackend` protocol; on-load schema migration, reviewable as a git diff. | Ship the risk-reducing piece before the refactor; migration that writes back on load is a write nobody asked for. | supports P4; tension with P3 |
| [[adr-0016]] | LanceDB evaluation | 2026-03-01 | rejected | No-Go on measurement; Postgres adopted as the second backend. | One benchmark settled it; the protocol and its conformance suite outlived the loser. | supports P9 |
| [[adr-0017]] | Entry protocol mixins | 2026-03-03 | current | Five dataclass mixins carrying `_x_to_frontmatter`; protocol fields promoted to indexed columns; `kb.yaml` `protocols:`. | Promoted columns made cross-type queries cheap; inheritance contradicts [[adr-0014]] and puts serialisation in the type. | predates P6; contradicts P6 (serialisation) |
| [[adr-0018]] | Web UI KB management via git forks | 2026-03-05 | superseded in part by 0024 | Per-user shallow forks, GitHub PRs, org hierarchy. | A complete design (forks, PRs, an org tree) for a need a smaller mechanism met; replaced in 25 days. | P7 |
| [[adr-0019]] | Pull-based kanban for agent teams | 2026-03-08 | current | Pull-based kanban over sprints; `milestone` and `review_queue`; `programmatic_validation` versus `development_convention`. | Human review is the bottleneck; WIP limits there; "if you can write a check, it is a validation". | supports P8, P9 |
| [[adr-0020]] | Kanban entity model | 2026-03-08 | current | Milestone is an entry; lanes are config; the review queue is a computed view. | Classify before modelling: authored, configured, or computed. | predates P4, P7 |
| [[adr-0021]] | Definition of ready / done gates | 2026-03-09 | current | Gates in `board.yaml`, evaluated at claim and transition; warn by default. | Judgment criteria always pass, so they are guidance; gates inform before they block. | supports P8 |
| [[adr-0022]] | Typed relationship entries (edge-entities) | 2026-03-09 | current | Relationships that carry data are entries; derived backlinks live in the index only; all endpoints required; a deleted endpoint leaves a broken reference. | First written derive-don't-write: saving an edge must not rewrite two other files. | predates P4; supports P3 |
| [[adr-0023]] | Static HTML site cache | 2026-03-25 | current; predates ADR-0043 decision 8 | Python-rendered static HTML under `/site/`, rebuilt on index sync. | Two designs were tried and rejected (Quartz export; SvelteKit SSR needing Node). | supports P4; tension with "not a site generator" |
| [[adr-0024]] | Git worktree collaboration model | 2026-03-30 | contradicted; amended by 0029; replacement ADR-0044 (proposed) | Worktree per user, branch `user/{name}`, in-app merge queue; V1: every KB readable by every authenticated user. | A long-lived branch per user shares one checkout between sessions; "no per-KB permissions in V1" aged out when grants arrived. | P7, P8; superseded in part by ADR-0043 (accepted) |
| [[adr-0025]] | Release workflow | 2026-04-01 | current, amended by 0032 | `dev` integrates, `main` releases, tags ship; deployment tiers. | A gate the owner can bypass is a convention; one required interpreter missed an environment-dependent bug; two copies of a version drift. | supports P9 |
| [[adr-0026]] | FIPS and state as promoted columns | 2026-04-10 | current | Promote two frontmatter fields to indexed columns and thread them through search. | The promoted-column pattern ([[adr-0017]]) makes a new filter a mechanical change. | supports P4 |
| [[adr-0027]] | Per-entity-type state machine | 2026-06-11 | current | A `state_machine` block on the type; strict or relaxed transitions; state membership always checked. | The question is type-level, not a KB toggle; loosen transitions, not membership. | supports P6 |
| [[adr-0028]] | Backend-agnostic query DSL | 2026-06-23 | deferred 2026-10-03 (status proposed; never built) | One DSL to an AST compiled per backend; operators positional; closed field vocabulary. | Callers must not need the backend's syntax; fix at the seam. Never built. | P8 |
| [[adr-0029]] | Libraries, KB lifecycles, runtime state | 2026-07-03 | contradicted, mostly unbuilt | YAML is the registry; durable versus ephemeral KBs; declared state tables; worktrees as leased ephemerals. | "Every recurrent field bug traces to derived state diverging from its source"; two half-authorities are the disease. | supports P4, P5, P7; amended by ADR-0039, ADR-0042 (accepted) |
| [[adr-0030]] | Agent-agnostic run execution (ACP) | 2026-09-17 | deferred 2026-10-03 (status proposed) | ACP is the harness boundary; enforcement lives in the write path, never the harness. | A gate an agent can walk around is documentation; verify external claims in a Phase 0 spike. | supports P8 |
| [[adr-0031]] | The API is the product surface | 2026-09-17 | withdrawn 2026-10-03 (superseded by ADR-0043) | The API is the only security boundary; grants, not modes; frontends are scoped clients. | A mode flag is runtime state; a build split is not a security boundary. | supports P7, P8; amended by ADR-0043 (accepted) |
| [[adr-0032]] | Branch flow and test progression | 2026-09-17 | current; rebase-versus-squash wording and proposed 3b open | Feature branches; PRs green on top of current `dev`; layered gates; a worktree per session. | Replace remembered disciplines with enforced rules; the assembled artifact needs its own test. | supports P8, P9 |
| [[adr-0033]] | Where work is tracked | 2026-09-17 | current | Bugs and requests in GitHub, the roadmap in the KB, one home per item; process findings in the KB. | Two trackers drift; capture must cost less than the thing captured. | supports P4 (process) |
| [[adr-0034]] | Agent-facing reads are bounded | 2026-09-18 | current | Every agent-reachable read has a bound and a continuation; truncation is visible and never writable. | An unwritten rule applies unevenly; prefer the failure a caller can see. | supports P3, P8 |
| [[adr-0035]] | Writes are eventually embedded | 2026-09-19 | current | `auto_embed` enqueues and returns; drain on existing paths; no new thread. | Measure first: the fast path existed and was unreachable; make the debt visible. | supports P3, P4 |
| [[adr-0036]] | Live socket lifetime | 2026-09-25 | current; lags the code (KB-policy epochs exist) | A socket closes when its credential ends or its user's grants change. | A scope cached at connect outlives its source; enforce server-side. | supports P4, P8 |
| [[adr-0037]] | One authorization policy point, one error contract | 2026-09-25 | accepted, partly implemented | `AccessPolicy`, `Principal`, `Action`; one error contract; a structural guard with a shrinking allowlist. | Shared rule, many deciders; reads were structural because they have one shape. | supports P8; amended by ADR-0043 (accepted) |
| [[adr-0038]] | Entry identity and the file lifecycle | 2026-09-25 | accepted 2026-10-03 (questions decided, step 1 landed) | `(kb, id)` identity, one id reader, one reconcile, sticky location, history by id; invariants I1 to I10. | No single definition of an entry on disk; a state machine plus mutation checks made it measurable. | supports P5; amended by ADR-0042 (accepted) |
| ADR-0039 | The registry is the operator's file; state is not content | 2026-10-02 | accepted 2026-10-03 | The operator's config file is the registry; the server never writes it; secrets split out; grants in the file; declared state tables. | Reverses its own earlier text (DB as registry): "several writers" argued against server writes, not against the file. | supports P5, P7; amends [[adr-0029]] |
| [[adr-0040]] | Extensions out of tree; the plugin contract is the public API | 2026-09-26 | accepted, not implemented; alpha, freezes in 0.28 | A `pyrite.plugin_api` façade, `PLUGIN_API_VERSION`, a conformance kit, extensions in their own repos, cascade deleted. | Gates that enumerate plugin tools by name cannot follow a plugin out of tree; move the property into a kit each plugin runs. | supports P6, P8; amended by ADR-0045 (accepted) |
| ADR-0041 | A KB is files any tool can use; Pyrite is additive | 2026-10-02 | accepted 2026-10-03 | Files belong to the user; additive; a write does what was asked; no record outside a file. | A no-op load and save changed 25.6% of real files. | states P1, P2, P3, P5 |
| ADR-0042 | A write changes what was asked and nothing else | 2026-10-02 | accepted 2026-10-03 | Operations on the file's own value; reads from the file; hooks refuse and never write; identity from the path; lock and compare-and-replace. | Writing the model's after-value deletes what the model does not hold; grade with an independent oracle. | states P2, P3, P4, P5; amends [[adr-0038]], [[adr-0029]] |
| ADR-0043 | Two planes: entries by grant, everything else the operator's | 2026-10-02 | accepted 2026-10-03 | Entries by per-KB grant; the operator's plane for the rest; identity injected by the dispatcher; no anonymous MCP. | Ownership inside the KB lets its writers grant themselves; a per-KB owner role had one use. | states P7, P8; amends [[adr-0037]], [[adr-0006]], [[adr-0004]] |
| ADR-0044 | Internal pull requests | 2026-10-02 | proposed | A user's write lands in a change set (a git ref); a merge is `merge-tree` plus fast-forward; one landing rule. | Mirror [[adr-0032]] for users; measure git first; reading main while writing a stale copy gave two texts. | supports P7, P8; replaces [[adr-0024]] |
| ADR-0045 | Types and protocols are the extension interface; serialisation is not | 2026-10-02 | accepted 2026-10-03 | A protocol is a data contract, derived information, explicit operations and refusals; rollup, unblock and evidence are derived; aliases and migrations live in the schema. | Two accepted ADRs disagree on structural versus nominal; 37 extension classes are structure plus serialisation. | states P4, P6; amends [[adr-0014]], [[adr-0040]] |

## Part 3. Contradictions and gaps

### ADRs whose learnings or texts contradict each other

- **[[adr-0014]] and [[adr-0017]]:** structural, no inheritance, against
  protocols built as inherited mixins that carry their own serialisation.
  ADR-0045 (accepted) names it ("Two accepted ADRs have to be reconciled").
- **[[adr-0014]] `rollup` and [[adr-0022]]:** a protocol that writes the parent
  after save, against "no cascading writes". ADR-0045 decision 7 (accepted)
  makes rollup derived.
- **[[adr-0008]] decision 5 and ADR-0042:** ruamel round-trip as the fix for
  noisy diffs, against the measured 5.5% floor for any re-emit.
- **[[adr-0015]] and ADR-0042 decision 7:** on-load migration that may write
  the migrated entry back, against "migrations are reading rules, never writes".
- **[[adr-0024]] V1 permissions and [[adr-0037]]:** every KB readable by every
  authenticated user, against per-KB grants and concealment. ADR-0043
  (accepted) supersedes the V1 section.
- **[[adr-0024]] "index per worktree" and [[adr-0029]] section 6:** a per-worktree
  index against an overlay diff index; [[adr-0029]] wins today.
- **[[adr-0029]] section 1, ADR-0039 (both texts) and the code:** three
  registry designs; section 3 (ephemerals never in the file) against
  `EphemeralService` writing them.
- **[[adr-0029]] section 4 and ADR-0042 decision 10:** the claim CAS as the
  one guard, with the index row as tiebreaker, against "the row is never the
  tiebreaker" (ADR-0042 asks the maintainer, question 3).
- **[[adr-0029]] section 4 (grants are database state) and ADR-0039 decision 6
  (grants live in the operator's file).**
- **[[adr-0004]] and ADR-0042 decision 4:** a hook that checks the author
  against the folder is fine; one that sets `author_id` is a write. ADR-0043
  injects identity.
- **[[adr-0037]] `KB_ADMIN` and ADR-0043:** the action exists; ADR-0043 deletes
  it. Also "services take a principal: later" is narrowed.
- **[[adr-0031]] withdrawn (`REPO_EGRESS`, grants) and ADR-0043:** egress as a
  capability a grant can carry, against egress as the operator's.
- **[[adr-0038]] sections 1 and 2 and ADR-0042 decision 5:** title-derived id
  in the text, filename in decision 5, path in the amendment (accepted 2026-10-03).
- **[[adr-0040]] section 6 check 3 (round trip of `to_frontmatter`) and
  ADR-0045 decision 6:** the check is replaced if ADR-0045 is accepted.
  [[adr-0040]] schedules the contract for 0.27; the supported-surface entry and
  ADR-0045 say the freeze is 0.28 and the contract is alpha.
- **[[adr-0025]] and [[adr-0032]]:** "no PR reviews" and "all work on `dev`",
  amended. [[adr-0032]] decision 3 says rebase; its own 2026-09-21 note says the
  queue squashes; section 3b is `proposed` and `scripts/release.py` does the
  opposite.
- **[[adr-0006]] and its audit:** the server defaults to the write tier in the
  CLI and the read tier in the class.
- **[[adr-0028]], [[adr-0020]] and [[adr-0030]]:** `status:` is the query
  operator for entry status ([[adr-0028]]), a lane's mapping over task statuses
  ([[adr-0020]]) and a milestone's own field; [[adr-0030]] open question 1 asks
  for a rename before a UI shows them together.
- **[[adr-0014]] and [[adr-audit-2026-10]]:** the audit marks 0014 current;
  ADR-0045 verified that the `protocol` entry type, `pyrite protocol show` and
  `requires_protocols` do not exist.
- **[[adr-0034]] and its own CLI and REST defaults:** "bounded by default" with
  two surfaces complete by default. It states the exception and why; not a
  contradiction, but easy to misread.

### Where [[design]] says something no accepted ADR supports

The page was approved on 2026-10-02. These are the places the accepted ADRs
have not caught up with it; the proposed ADRs named would close most of them.

- **P2, "Pyrite is additive"; P3, "a write does what was asked", `--force` as
  discard only; "removing Pyrite loses none of its content":** only ADR-0041 and
  ADR-0042 (accepted) and the standard `pyrite-is-a-guest-in-state-it-does-not-own`.
  [[adr-0001]] supports editing in any editor; nothing accepted binds Pyrite.
- **P4, "a hook may refuse a write, may not change one":** no accepted ADR.
  [[adr-0002]] and [[adr-0014]] register hooks and a `rollup` that writes.
- **P5, "identity is its path unless an `id:` pins it" and "the index is never
  the tiebreaker":** [[adr-0038]] (accepted) says `(kb, id)` and the filename;
  ADR-0042 says path. [[adr-0029]] section 4 keeps the index as the claim
  tiebreaker.
- **P6, "serialising a file is not a type's job":** [[adr-0017]] does the
  opposite; only ADR-0045 (accepted) supports it.
- **P7, "the registry, grants and keys are in a documented operator config
  file":** [[adr-0029]] section 4 puts grants in database tables; only ADR-0039
  and ADR-0043 (accepted). "Entries by grant, per KB": [[adr-0037]] has grants
  but not "the operator's plane" or where grants are stored. "Whoever holds the
  files is the operator": ADR-0041 and ADR-0043 only.
- **P8, "each rule in one place, with a test that fails":** [[adr-0037]] states
  it for authorization and errors only; [[adr-0038]] section 5 (accepted) for
  identity. Nothing accepted generalises it.
- **P9, "a small supported surface; a doc that teaches a command is its test":**
  no ADR. [[adr-0032]] runs the tutorial in the smoke job; the supported-surface
  list is a proposed design entry; the principle is in the roadmap's release line.
- **"Multi-user is experimental; plugin and API contracts are alpha":** the
  roadmap only. [[adr-0040]] (accepted) describes a compatibility policy with a
  deprecation window; ADR-0045 calls the contract alpha.
- **"Built first for one operator working locally":** ADR-0041 decision 7
  (accepted). [[adr-0007]] and [[adr-0031]] (withdrawn) describe several surfaces
  and audiences.
- **"Not a site generator. Publishing is an export":** contradicted by accepted
  [[adr-0023]], which ships a rendered static site; ADR-0043 decision 8
  (proposed) gives it a job under the policy.

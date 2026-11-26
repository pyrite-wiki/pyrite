# Getting started with Pyrite and agentic development: give your coding agent a project memory

You have a code repository and a coding agent. In this tutorial you will add a Pyrite software knowledge base (`kb/`) next to your code, let an agent seed a project memory from what the repository already records (components, architecture decision records, and a backlog), work one backlog item end to end, have a second agent review the change against the ADRs, and then verify how a teammate or cloud session clones and queries the same memory.

By the end, a fresh agent session with no chat history answers **"why is this module shaped this way?"** and **"what is in progress?"** from `kb/` in a few commands.

<!-- This page is a test. scripts/run_tutorial.py runs every bash block below in order, in a throwaway HOME, and checks the answers the page promises (the "runner:" comments say which). Steps marked "Ask your agent" use runner: seed fixtures in CI so every verification command after them runs for real. -->

**You need:**

- macOS or Linux, `git`, and Python 3.11 or newer.
- Any terminal coding agent (Claude Code is the tested example; the prompts and CLI commands work with any agent that can run shell commands).
- Your own code repository, or the small BSD-3 Python library [`pallets/itsdangerous`](https://github.com/pallets/itsdangerous) pinned at tag `2.2.0` to follow along verbatim (and which CI runs).

See the [software-kb extension guide](../../extensions/software-kb/README.md) for the complete entry-type reference, link vocabulary, and backlog state machine.

---

## Act 1: Install Pyrite and initialize `kb/` in your repository

### 1. Install Pyrite and the software-kb extension

Install Pyrite and the `pyrite-software-kb` extension directly from the release tag (no clone of Pyrite required):

```bash
pip install "pyrite[server,cli] @ git+https://github.com/pyrite-wiki/pyrite@v0.26.4"
pip install "pyrite-software-kb @ git+https://github.com/pyrite-wiki/pyrite@v0.26.4#subdirectory=extensions/software-kb"
```

### 2. Enter your repository (or clone the pinned example)

To follow every output on this page verbatim, clone `pallets/itsdangerous` at release tag `2.2.0`:

```bash
git clone --branch 2.2.0 --depth 1 https://github.com/pallets/itsdangerous.git
cd itsdangerous
```

### 3. Initialize a software KB

Create a software knowledge base in `./kb` named `itsdangerous`:

```bash
pyrite init -t software -p ./kb --name itsdangerous
```

<!-- runner: expect-text "status": "created" -->
<!-- runner: expect-text "template": "software" -->
<!-- runner: health-kb itsdangerous -->

```json
{
  "status": "created",
  "name": "itsdangerous",
  "path": ".../itsdangerous/kb",
  "template": "software",
  "types": [
    "adr",
    "design_doc",
    "standard",
    "programmatic_validation",
    "development_convention",
    "component",
    "backlog_item",
    "runbook",
    "work_log",
    "milestone",
    "task"
  ],
  "entries_indexed": 0
}
```

**Why this works ([`kb/design.md`](../../kb/design.md), Principle 1 — *The files are the KB*):** `kb/kb.yaml` and the subdirectories (`adrs/`, `components/`, `backlog/`, `standards/`, `runbooks/`) are plain files in your repository. If you see an unknown-template error, verify `pyrite sw --help` succeeds.

### 4. Let the KB explain itself (`orient`)

Run `pyrite orient` to see the entry types, recent changes, and operational contracts in one call:

```bash
pyrite orient -k itsdangerous --detail brief
```

<!-- runner: expect-text "name": "itsdangerous" -->
<!-- runner: expect-text "backlog_item" -->

```json
{
  "kb": {
    "name": "itsdangerous",
    "type": "software",
    "entry_count": 0
  },
  "entry_types": {
    "adr": {"count": 0},
    "backlog_item": {"count": 0},
    "component": {"count": 0}
  },
  "outcome": "done"
}
```

**Why this works ([`kb/design.md`](../../kb/design.md), Principle 6 — *Types give structure; protocols give behaviour*):** The schema and operational rules live in the KB itself, so every new agent session reads them in one call instead of learning from trial and error.

### 5. Install the developer skills and `AGENTS.md` block

Copy the three single-file developer skills ([`extensions/software-kb/skills/`](../../extensions/software-kb/skills/)) into your repository (clone Pyrite and copy them until the first release tag carrying these skills is published) and add the project-memory block to `AGENTS.md` (see [Add Pyrite to your project's agent instructions](../agent-instructions.md)). The `.claude/skills/` path below is specific to Claude Code; any other coding agent reads the same three `SKILL.md` files from disk:

```text
$ git clone https://github.com/pyrite-wiki/pyrite.git /tmp/pyrite-src
$ mkdir -p .claude/skills
$ cp -r /tmp/pyrite-src/extensions/software-kb/skills/project-memory-bootstrap .claude/skills/
$ cp -r /tmp/pyrite-src/extensions/software-kb/skills/session-start .claude/skills/
$ cp -r /tmp/pyrite-src/extensions/software-kb/skills/session-wrap .claude/skills/
$ ls .claude/skills
```

---

## Act 2: Seed a project memory from the repository

### 1. Ask your agent to bootstrap the KB

Give your coding agent one prompt to read the repository's existing docs, changelog, package layout, and git history and seed `kb/`:

**Ask your agent:**

```text
Use the project-memory-bootstrap skill to seed the `itsdangerous` software KB in `./kb` from this repository:
1. Read `README.rst`, `CHANGES.rst`, `src/itsdangerous/`, and `git log --oneline -n 50`.
2. Create component entries (`pyrite create -k itsdangerous -t component --title "<title>" -f kind=module -f path=<path> --body-file <file>`) for the core signing modules (`signer-core` for `src/itsdangerous/signer.py`, `timed-serialization` for `src/itsdangerous/timed.py`).
3. Create accepted ADRs (`pyrite sw new-adr "<title>" -k itsdangerous --status accepted`, then `pyrite update <adr-id> -k itsdangerous --body-file <file>`) for 64-bit Unix timestamps in `TimestampSigner` (`adr-0001`) and secret key rotation via ordered `secret_keys` and `fallback_signers` (`adr-0002`). Cite the exact file or `CHANGES.rst` version for every claim, include `## Alternatives rejected`, and mark any unrecorded rationale as an open question.
4. Link components to the ADRs they implement (`pyrite link <component> <adr-id> -r implements -k itsdangerous`).
5. Create two `proposed` backlog items (`fallback-signer-verification` and `url-safe-compression-threshold`) linked to their ADRs with `-r tracks`, then run `pyrite qa validate itsdangerous`.
```

In CI, the tutorial runner seeds the entries that prompt produces:

<!-- runner: seed tests/fixtures/tutorials/agentic-development/kb kb -->

Check the files on disk:

```bash
git status --short -uall kb/
```

<!-- runner: expect-text kb/adrs/ -->
<!-- runner: expect-text kb/components/ -->
<!-- runner: expect-text kb/backlog/ -->

```text
?? kb/adrs/0001-timestamp-signer-epoch-and-64-bit-timestamps.md
?? kb/adrs/0002-secret-key-rotation-via-fallback-signers.md
?? kb/backlog/fallback-signer-verification.md
?? kb/backlog/url-safe-compression-threshold.md
?? kb/components/signer-core.md
?? kb/components/timed-serialization.md
?? kb/kb.yaml
```

**Why this works ([`kb/design.md`](../../kb/design.md), Principle 3 — *A write does what was asked, loses nothing, and reports it*):** Every entry the agent creates is a readable Markdown file in `kb/` that you inspect with `git status` and `git diff` before committing.

### 2. Answer "why is this module shaped this way?" in a fresh session

A new session with zero conversation history can now ask why `TimestampSigner` encodes timestamps the way it does:

```bash
pyrite search "TimestampSigner" -k itsdangerous --fields id,title,entry_type
```

<!-- runner: expect-ids itsdangerous:adr-0001 -->

```json
{
  "results": [
    {
      "id": "adr-0001",
      "kb_name": "itsdangerous",
      "title": "ADR-0001: 64-bit Unix timestamps in TimestampSigner",
      "entry_type": "adr"
    }
  ],
  "count": 1,
  "outcome": "done"
}
```

Inspect what links to `adr-0001`:

```bash
pyrite backlinks adr-0001 -k itsdangerous
```

<!-- runner: expect-text timed-serialization -->
<!-- runner: expect-text implements -->

```json
{
  "entry_id": "adr-0001",
  "kb_name": "itsdangerous",
  "backlinks": [
    {
      "source": "timed-serialization",
      "relation": "implements"
    },
    {
      "source": "url-safe-compression-threshold",
      "relation": "tracks"
    }
  ],
  "count": 2,
  "outcome": "done"
}
```

**Why this works ([`kb/design.md`](../../kb/design.md), Principle 4 — *Derive; do not write*):** The component `timed-serialization` stores one forward link (`implements` -> `adr-0001`), and Pyrite derives the reverse link on `adr-0001` automatically.

---

## Act 3: Work one backlog item end to end

You can run the development cycle either by giving your agent the `session-start` and `session-wrap` prompt below, or by running the exact CLI commands yourself. Both follow the same `pyrite sw` verb sequence (`board` -> `backlog` -> `pull-next` -> `transition` -> `context-for-item` -> `claim` -> `log` -> `submit`).

**Ask your agent (after you accept an item in Step 2 below):**

```text
Use the session-start skill on the `itsdangerous` KB to inspect the board, pull the next accepted item (`fallback-signer-verification`), read its context and component ADRs, and claim it as `agent:dev-1`. After verifying the key-rotation order in `src/itsdangerous/signer.py`, use the session-wrap skill to log the session decisions, validate the KB, and submit the item for review.
```

### 1. Check the board and try `pull-next`

Inspect the kanban board and active `in_progress` items:

```bash
pyrite sw board -k itsdangerous
pyrite sw backlog -k itsdangerous --status in_progress --format json
```

<!-- runner: expect-text "name":"Backlog","count":2 -->
<!-- runner: expect-text [] -->

Now ask `pull-next` for the next item to work:

```bash
pyrite sw pull-next -k itsdangerous
```

<!-- runner: expect-text No accepted items available -->

```json
{
  "recommendation": null,
  "reason": "No accepted items available",
  "wip_status": {
    "current": 0,
    "limit": 5,
    "policy": "warn"
  }
}
```

### 2. Accept the item and pull it

New backlog items start as `proposed`. Accepting work onto the Ready lane (`accepted`) is the developer's decision:

```bash
pyrite sw transition fallback-signer-verification accepted -k itsdangerous
pyrite sw check-ready fallback-signer-verification -k itsdangerous
pyrite sw pull-next -k itsdangerous
```

<!-- runner: expect-text "id": "fallback-signer-verification" -->

```json
{
  "recommendation": {
    "id": "fallback-signer-verification",
    "title": "Document and test key-rotation order with fallback_signers",
    "priority": "high",
    "effort": "S"
  }
}
```

**Why this works ([`kb/design.md`](../../kb/design.md), Principle 6 — *Types give structure; protocols give behaviour*):** Agents can propose follow-up items freely without polluting the work queue; `pull-next` only recommends items that have crossed the `accepted` gate.

### 3. Gather context and component ADRs

Assemble the item's linked ADRs, components, dependencies, and prior reviews in one call:

```bash
pyrite sw context-for-item fallback-signer-verification -k itsdangerous
```

<!-- runner: expect-text adr-0002 -->
<!-- runner: expect-text signer-core -->

Also inspect the affected component and its backlinks (until `#1863` adds two-hop component-to-ADR traversal directly to `context-for-item`):

```bash
pyrite get signer-core -k itsdangerous
pyrite backlinks signer-core -k itsdangerous
```

<!-- runner: expect-text fallback-signer-verification -->

**Why this works ([`kb/design.md`](../../kb/design.md), Principle 4 — *Derive; do not write*):** The agent receives the governing ADR (`adr-0002`) from the link graph instead of guessing how `secret_keys` and `fallback_signers` interact.

### 4. Claim the item (and see a second claim refused)

Lock the item as `agent:dev-1`:

```bash
pyrite sw claim fallback-signer-verification -k itsdangerous --assignee agent:dev-1
```

<!-- runner: expect-text in_progress -->

```text
Claimed: Document and test key-rotation order with fallback_signers
  Assigned to: agent:dev-1
  Status: in_progress
```

If a second agent tries to claim the same item while it is `in_progress`, Pyrite refuses the write with exit code `1`:

<!-- runner: expect-exit 1 -->

```bash
pyrite sw claim fallback-signer-verification -k itsdangerous --assignee agent:dev-2
```

<!-- runner: expect-text Cannot transition from 'in_progress' to 'in_progress' -->

```text
Error: Cannot transition from 'in_progress' to 'in_progress'. Allowed: review, done, accepted
```

**Why this works ([`kb/design.md`](../../kb/design.md), Principle 8 — *Each rule lives in one place*):** Claim transitions are checked atomically against the file's status and assignee so two agents never overwrite each other's claim.

### 5. Log the session, validate, and submit for review

Record what was decided, what was rejected, and any open questions, then validate the KB and move the item to `review`:

```bash
pyrite sw log fallback-signer-verification -k itsdangerous -s "Verified key rotation order against ADR-0002" --decisions "Keep newest key last in Signer.secret_keys and iterate reversed(secret_keys) before fallback_signers" --rejected "Mutating secret_keys in place during verification" --open-questions "None"
pyrite qa validate itsdangerous
pyrite sw submit fallback-signer-verification -k itsdangerous
```

<!-- runner: expect-text review -->

```text
Submitted for review: Document and test key-rotation order with fallback_signers
  Status: in_progress -> review
```

**Why this works ([`kb/design.md`](../../kb/design.md), Principle 5 — *The file decides*):** `sw log` writes a `work_log` Markdown file under `kb/work-logs/` linked to the item via `session_for`, so the next session sees both what worked and what was ruled out.

---

## Act 4: Review the work in a second agent session

Start a separate reviewer session with no memory of Act 3.

**Ask your agent:**

```text
Check `pyrite sw review-queue -k itsdangerous`, inspect the full context and work log for `fallback-signer-verification` with `pyrite sw context-for-item fallback-signer-verification -k itsdangerous`, verify that the recorded decisions comply with `adr-0002`, and record your verdict with `pyrite sw review`.
```

Run the same review commands directly:

```bash
pyrite sw review-queue -k itsdangerous
```

<!-- runner: expect-text fallback-signer-verification -->

```bash
pyrite sw context-for-item fallback-signer-verification -k itsdangerous
```

<!-- runner: expect-text Verified key rotation order against ADR-0002 -->

Approve the item and move it to `done`:

```bash
pyrite sw review fallback-signer-verification -k itsdangerous --outcome approved --reviewer agent:reviewer-1 --feedback "Matches ADR-0002 key rotation contract; work log is complete."
```

<!-- runner: expect-text done -->

```text
Approved: Document and test key-rotation order with fallback_signers
  Status: review -> done
  Reviewed by: agent:reviewer-1
```

Commit `kb/` to the repository so the project memory travels with the code:

```bash
git add kb
git commit -m "kb: seed project memory and complete fallback-signer-verification"
```

**Why this works ([`kb/design.md`](../../kb/design.md), "What Pyrite is not" — *Not a replacement for git*):** Pyrite manages the files and their local index; changes are reviewed and shared as ordinary git commits and pull requests.

---

## Act 5: Scale out — a teammate clone, MCP, and a second tracker

### 1. A teammate or cloud session clones the repository

When a teammate or a cloud worker clones the repository, `kb/` is on disk, but `~/.pyrite/index.db` on that machine has not indexed the clone yet (if they run `pyrite sw backlog` before indexing, it returns `[]`). From the clone, run `pyrite kb discover --add` and `pyrite index sync`:

```bash
cd ..
git clone itsdangerous itsdangerous-teammate
cd itsdangerous-teammate
pyrite kb discover --add
pyrite index sync
```

<!-- runner: expect-text Sync complete -->

Now query the ADR from the clone:

```bash
pyrite search "TimestampSigner" -k itsdangerous --fields id,title
```

<!-- runner: expect-ids itsdangerous:adr-0001 -->

**Why this works ([`kb/design.md`](../../kb/design.md), Principle 5 — *The index is a cache*):** The committed Markdown files carry the entire KB; any machine can rebuild the SQLite index from `kb/` in seconds. (Once `#1861` lands, `pyrite kb discover --add` will also read `kb_type: software` from `kb.yaml` and index the discovered KB in one step.)

### 2. Connect an MCP client

If your coding agent uses MCP tools (`kb_orient`, `sw_pull_next`, `sw_context_for_item`, `sw_claim`, `sw_log`, `sw_submit`) in addition to shell commands, register the MCP server for your user configuration and write a project-scoped `.mcp.json` file:

```bash
pyrite mcp-setup
pyrite mcp-setup --project
```

<!-- runner: expect-text .mcp.json -->

**Why this works ([`kb/design.md`](../../kb/design.md), Principle 10 — *One primary user per surface*):** The CLI and the MCP server call the same service methods and enforce the same schema and lifecycle rules.

### 3. Add a lightweight `task` tracker alongside the software KB

Your software KB holds lasting architecture decisions, components, and the feature backlog. For ephemeral execution queues (such as a release checklist or a batch of file migrations), you can register a separate `task` KB alongside it:

```bash
pyrite init -t task -p ./tasks-kb --name itsdangerous-tasks
pyrite task create "Verify wheel metadata on 2.2.0 tag" -k itsdangerous-tasks --priority 2
pyrite task list -k itsdangerous-tasks
```

<!-- runner: expect-text verify-wheel-metadata-on-2-2-0-tag -->

**Why this works ([`kb/design.md`](../../kb/design.md), Principle 6 — *Types give structure; protocols give behaviour*):** One index spans multiple KBs, keeping transient execution tasks out of your permanent architectural history while letting agents query both.

---

## Where to read next

- **[Add Pyrite to your project's agent instructions](../agent-instructions.md):** the copy-pasteable `AGENTS.md` block and the three developer skills (`project-memory-bootstrap`, `session-start`, `session-wrap`).
- **[Pyrite software-kb guide](../../extensions/software-kb/README.md):** entry types, the backlog lifecycle, `blocked_by` links, and quality gates.
- **[Agent write path](../agent-write-path.md) and [JSON contracts](../json-contracts.md):** how partial updates, item operations (`--append`, `--remove`, `--unset`), `content_hash` preconditions, and refusal envelopes work.
- **[Tasks](../tasks.md) and [Links](../links.md):** task DAG orchestration and cross-KB link management.

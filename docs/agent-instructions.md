# Add Pyrite to your project's agent instructions

When a repository tracks its architecture decisions, components, and backlog in `kb/` with the [software-kb extension](../extensions/software-kb/README.md), your coding agent needs a short block in its instruction file (`AGENTS.md`, `CLAUDE.md`, or `GEMINI.md`) so every session starts by reading the KB and wraps up by recording decisions back into it.

## The `AGENTS.md` block

Paste this block into your repository's `AGENTS.md` (or `CLAUDE.md` / `GEMINI.md`), replacing `<kb>` with your KB's name from `kb/kb.yaml`:

```markdown
## Project memory (Pyrite)

- The KB `<kb>` lives in `kb/`. It needs the pyrite-software-kb extension for `pyrite sw`.
- First session on a fresh clone: `pyrite kb discover --add`, then `pyrite index sync`.
- Start each session with the session-start skill, or: `pyrite orient -k <kb> --detail brief`, then `pyrite sw board -k <kb>`.
- Before editing a module: `pyrite get <component> -k <kb>` and `pyrite backlinks <component> -k <kb>`; read every linked ADR.
- Close work with the session-wrap skill: `pyrite sw log <id> -k <kb> -s "<summary>"`, link the item to its ADRs, `pyrite sw submit <id> -k <kb>`.
- An empty list can mean "not indexed": run `pyrite index sync` and retry before concluding.
- Never hand-edit frontmatter; use `pyrite create`, `pyrite update` and `pyrite link`.
```

Why each line is there:

- **Fresh clones have files on disk, not an index.** The SQLite index lives outside the repository (`~/.pyrite/index.db`). Until `pyrite kb discover --add` and `pyrite index sync` run, commands like `pyrite sw backlog -k <kb>` return `[]` even though `kb/` has files.
- **Component backlinks surface governing ADRs.** Reading a component and its backlinks before editing a module tells a fresh session *why* the code is shaped that way without re-explaining it in chat.
- **The CLI validates every write.** Writing through `pyrite create`, `pyrite update`, `pyrite link`, and `pyrite sw` keeps frontmatter valid against `kb/kb.yaml` and updates the index in one step (see [Agent write path](agent-write-path.md) and [JSON contracts](json-contracts.md)).

## Install the three developer skills

Pyrite publishes three single-file skills alongside the software-kb extension in [`extensions/software-kb/skills/`](../extensions/software-kb/skills/):

| Skill | When to use it | Source file |
|---|---|---|
| `project-memory-bootstrap` | First session in an existing repository: seed components, sourced ADRs, and proposed backlog items from the README, changelog, and git history. | [`extensions/software-kb/skills/project-memory-bootstrap/SKILL.md`](../extensions/software-kb/skills/project-memory-bootstrap/SKILL.md) |
| `session-start` | Start of each coding session: check clone registration and index health, orient, pick or resume work, read linked ADRs, and claim the item. | [`extensions/software-kb/skills/session-start/SKILL.md`](../extensions/software-kb/skills/session-start/SKILL.md) |
| `session-wrap` | End of each coding session: log decisions and rejected approaches, file any new ADR, update affected components, run `pyrite qa validate <kb>`, and submit for review. | [`extensions/software-kb/skills/session-wrap/SKILL.md`](../extensions/software-kb/skills/session-wrap/SKILL.md) |

### Claude Code

Clone the repository and copy the three skills into `.claude/skills/` in your project:

```bash
git clone https://github.com/pyrite-wiki/pyrite.git
mkdir -p .claude/skills
cp -r pyrite/extensions/software-kb/skills/project-memory-bootstrap .claude/skills/
cp -r pyrite/extensions/software-kb/skills/session-start .claude/skills/
cp -r pyrite/extensions/software-kb/skills/session-wrap .claude/skills/
ls .claude/skills
```

```text
project-memory-bootstrap
session-start
session-wrap
```

Pinned `curl` download lines will be added here once the first release carrying these skills is tagged.

### Other coding agents

Any agent that reads markdown instructions can use the same three `SKILL.md` files. Keep them in `.claude/skills/<name>/SKILL.md` in your repository and point to those paths from your `AGENTS.md` or `GEMINI.md` block, or ask the agent to read the skill file for the step you want (`project-memory-bootstrap`, `session-start`, or `session-wrap`).

## CLI and MCP clients

Terminal coding agents can run `pyrite` and `pyrite sw` commands directly in the shell with no extra configuration. If your agent also supports the Model Context Protocol (MCP), configure a project-scoped `.mcp.json` entry at the repository root:

```bash
pyrite mcp-setup --project
```

Over MCP, tell the agent to call `kb_orient` first at the start of every session, then use the `sw_*` and `kb_*` tools (`sw_pull_next`, `sw_context_for_item`, `sw_claim`, `sw_log`, `sw_submit`). Both surfaces call the same service layer and enforce the same schema and lifecycle rules. For client-specific MCP setup details, see [Getting started: Connect an AI via MCP](getting-started.md#connect-an-ai-via-mcp), [OpenAI / Codex MCP integration](openai-mcp-integration.md), and [Gemini CLI MCP integration](gemini-mcp-integration.md).

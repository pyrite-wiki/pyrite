# Pyrite Development Guide

@AGENTS.md

Everything above is the project's agent entry point and applies to every agent.
The process (branches, worktrees, hooks, the pre-push suite, the heavy-run lock,
pull requests) is in [CONTRIBUTING.md](CONTRIBUTING.md), "Branches, hooks and
pull requests" and "Running the tests"; this file does not restate it.

## Claude Code specifics

- **Skill:** `.claude/skills/pyrite-dev/SKILL.md` is the method for a coding
  session (tests first, root cause before fix, evidence before claims, a
  report). The `kb`, `software-kb` and `extension-builder` skills are for using
  and extending Pyrite, not for contributing to it.
- **Sub-agents** share your branch and worktree. Do not give them
  `isolation: "worktree"`; give them disjoint files.
- **Re-read before editing** when the Edit tool reports "modified since
  read": a sub-agent moved the file.
- **Wrong place?** If you are on `dev` in the main checkout with uncommitted
  work, make a worktree (`scripts/new-worktree.sh <branch>`) and move the work
  there (`git stash`, then `git stash pop`).
- **Cloud sessions:** `.claude/settings.json` runs a SessionStart hook
  (`scripts/cloud-session-start.sh`); see CONTRIBUTING, "Developing in Claude
  Code on the web".

# The maintainer's desk

A conductor loop (see `.claude/skills/pyrite-conductor/`) turns work into
pull requests, but some things only a human can do: merge an outside
contribution, accept an ADR, approve a release, agree a reply to a
contributor, answer a design question. Those used to live in three wrong
places — a section of the tick log, an issue assigned to oneself, the
conductor's chat. The **desk** gives them one home that is neither the
repository's history nor its public issue tracker.

The desk is a Pyrite knowledge base in a gitignored `desk/` directory at the
repository root, one per project, so every conductor user has their own and
nothing in it is ever committed or pushed.

## Create it

```bash
pyrite init -t empty -p ./desk -n <project>-desk --schema-file docs/desk-schema.yaml --no-examples
pyrite kb add ./desk --name <project>-desk --type desk
```

`docs/desk-schema.yaml` declares four types: `task` (the queue), `conductor_tick`
(the tick log), `retro`, and `note`. Every entry carries a `project` field, so
one person running several loops can list across desks.

## Use it

```bash
# the whole queue, oldest first (JSON; add --format rich, or set PYRITE_FORMAT=rich, for a table)
pyrite task list -k <project>-desk --status open

# the conductor files a decision when a tick reaches a kept item, in one step
pyrite task create "Merge #184" -k <project>-desk --priority 3 \
  -b "Recommendation and link…" --tags outside,quick \
  --field project=<project> --field kind=merge \
  --field link=https://… --field requested_by=conductor

# the maintainer (or the conductor's reconcile step, when it can observe the outcome)
pyrite task update <id> -k <project>-desk --status done
```

Rules that keep it honest: a desk task **points at** a PR, ADR or issue and never
duplicates it; the conductor's health step closes tasks whose outcome it can see
(the PR merged, the ADR flipped), so the list never rots; the retro measures
"maintainer wait" as created → done on these entries.

## From a worktree

Worktrees made by `scripts/new-worktree.sh` carry their own `.pyrite/config.yaml`
that lists only the worktree's `kb/`. The desk is registered in the global config,
so from a worktree reach it explicitly:

```bash
PYRITE_CONFIG_DIR=~/.pyrite pyrite task list -k <project>-desk --status open
```

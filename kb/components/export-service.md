---
id: export-service
type: component
title: "Export Service"
kind: service
path: "pyrite/services/export_service.py"
owner: "core"
dependencies: ["pyrite.config", "pyrite.storage", "pyrite.services.git_service"]
tags: [core, services, export, git]
---

`ExportService` handles KB export and git operations. Extracted from `KBService` in 0.18 to reduce god-class complexity.

## Methods

| Method | Description |
|--------|-------------|
| `export_kb_to_directory()` | Export all entries as markdown files with YAML frontmatter |
| `export_kb_to_repo(kb_name, repo_url, github_token, branch, commit_message)` | Clone target repo, export entries into `kb_name/` subdirectory, commit, push. `repo_url` must be `https://` on a host in `settings.git_allowed_hosts` (`REMOTE_NOT_ALLOWED`, before any git call); no write passes through a link in the clone (`UNSAFE_DESTINATION`, nothing committed or pushed) |
| `commit_kb()` | Commit changes in a KB's git repository (delegates to `GitService`) |
| `push_kb()` | Push KB commits to a remote repository (delegates to `GitService`) |

## Architecture

Constructor takes `config: PyriteConfig` and `db: PyriteDB`. Git operations lazy-import `GitService` to avoid circular dependencies.

`KBService` retains facade delegator methods with identical signatures, so all consumer callsites remain unchanged.

## Related

- [[kb-service]] — facade delegator for export/git methods
- [[git-service]] — underlying git operations

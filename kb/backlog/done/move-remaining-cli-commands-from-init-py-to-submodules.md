---
id: move-remaining-cli-commands-from-init-py-to-submodules
title: Move remaining CLI commands from __init__.py to submodules
type: backlog_item
tags:
- tech-debt
- cli
- refactor
importance: 5
kind: refactor
status: done
priority: medium
effort: M
rank: 0
---

cli/__init__.py is 1697 lines with ~25 command definitions. Most should be in submodules like the existing _commands.py files. Move entry CRUD to entry_commands.py, auth to auth_commands.py, MCP to mcp_commands.py.

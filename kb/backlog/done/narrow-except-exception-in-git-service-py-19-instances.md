---
id: narrow-except-exception-in-git-service-py-19-instances
title: Narrow except Exception in git_service.py (19 instances)
type: backlog_item
tags:
- tech-debt
- error-handling
importance: 5
kind: refactor
status: done
priority: low
effort: M
rank: 0
---

git_service.py has 19 broad except Exception catches that silently convert PermissionError, ProcessLookupError etc into warnings. Narrow to subprocess.SubprocessError and specific expected exceptions. Add similar treatment to plugins/registry.py (16 instances).

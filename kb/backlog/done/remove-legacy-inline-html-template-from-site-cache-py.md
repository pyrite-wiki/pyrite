---
id: remove-legacy-inline-html-template-from-site-cache-py
title: Remove legacy inline HTML template from site_cache.py
type: backlog_item
tags:
- tech-debt
- site-cache
- cleanup
importance: 5
kind: refactor
status: done
priority: low
effort: S
rank: 0
---

site_cache.py has a 430-line _PAGE_TEMPLATE_LEGACY inline string as fallback for when Jinja2 fails. The Jinja2 path (base.html) is the primary path and works. Verify it never fails, then remove the legacy fallback.

---
id: batch-backlink-outlink-queries-in-site-cache-fix-n-1
title: Batch backlink/outlink queries in site cache (fix N+1)
type: backlog_item
tags:
- tech-debt
- performance
- site-cache
importance: 5
kind: bug
status: done
priority: medium
effort: S
rank: 0
---

render_all() comment says '2 queries total, not 2N' but actually runs N queries (one per entry). Add get_all_backlinks_for_kb() and get_all_outlinks_for_kb() batch methods. Also fix per-entry db.get_entry() for sources in the render loop.

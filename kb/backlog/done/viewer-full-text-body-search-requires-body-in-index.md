---
id: viewer-full-text-body-search-requires-body-in-index
title: 'Viewer: full-text body search (requires body in index)'
type: backlog_item
tags:
- viewer
- search
- performance
importance: 5
kind: feature
status: done
priority: medium
effort: M
rank: 0
---

The viewer search only covers title, tags, and actors. Body text is excluded because the lightweight timeline-index.json omits it. A user searching for 'Bovino perjury' won't find events where that's only in the body. Options: include a truncated body in the index, or add a server-side search fallback.

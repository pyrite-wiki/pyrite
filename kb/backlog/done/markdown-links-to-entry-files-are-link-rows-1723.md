---
id: markdown-links-to-entry-files-are-link-rows-1723
title: Markdown links to entry files are link rows (#1723)
type: backlog_item
tags:
- enhancement
importance: 5
kind: feature
status: done
priority: medium
assignee: agent:cloud-worker
effort: M
rank: 0
---

A Markdown link from an entry body to another entry's file, such as [text](other.md), becomes a link row with relation markdown_link whose target_id is the id of the entry indexed at that path now, so backlinks, the graph and links check see hand-written links. An in-KB .md link with nothing at its path is a broken-link row, and a migration marks every entry for one re-read so a plain index sync picks the rows up.

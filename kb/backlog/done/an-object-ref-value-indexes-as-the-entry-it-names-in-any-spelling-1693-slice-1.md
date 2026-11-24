---
id: an-object-ref-value-indexes-as-the-entry-it-names-in-any-spelling-1693-slice-1
title: An object-ref value indexes as the entry it names, in any spelling (#1693 slice 1)
type: backlog_item
tags:
- bug
importance: 5
kind: bug
status: done
priority: medium
assignee: agent:cloud-worker
effort: S
rank: 0
---

A value in a kb.yaml object-ref field is stored in entry_ref verbatim, so [[x]] and other:x name entries that do not exist and drop out of get_refs_to. Index the value as the entry it names: strip the brackets and resolve the prefix with the wikilink rule.

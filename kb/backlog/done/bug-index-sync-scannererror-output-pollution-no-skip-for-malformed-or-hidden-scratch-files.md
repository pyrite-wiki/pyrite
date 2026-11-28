---
id: bug-index-sync-scannererror-output-pollution-no-skip-for-malformed-or-hidden-scratch-files
title: "INDEX SYNC robustness (re-filed from cascade-research; the drafts-KB DATA cleanup is Mark-gated and stays in cascade-res"
type: backlog_item
tags: [bug, conductor-filed, cli, task-system]
importance: 5
kind: bug
status: done
priority: medium
effort: S
rank: 1080
---

INDEX SYNC robustness (re-filed from cascade-research; the drafts-KB DATA cleanup is Mark-gated and stays in cascade-research, but the pyrite-side behavior is this). 'kb index sync -k drafts' emits ScannerError YAML output for ~13 _triage/_superseded scratch files on EVERY drafts operation — doesn't block valid indexing but pollutes stderr and masks legitimate YAML issues. Pyrite-side asks: (a) indexer should SKIP hidden/dot-prefixed dirs reliably (the documented behavior) so a '_superseded' -> '.superseded' rename suppresses the cluster; (b) malformed-file parse errors should be collected and reported as a SUMMARY (N files skipped, here are paths) rather than raw per-file ScannerError spew; (c) ties to the from_markdown robustness bug above. The per-file frontmatter REPAIR is KB-data (stays Mark-gated in cascade-research). Owner: pyrite repo for (a)(b). Re-filed from cascade-research/notes/issue-yaml-scannererror-on-drafts-kb-index-sync.

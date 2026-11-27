---
id: fallback-signer-verification
title: Document and test key-rotation order with fallback_signers
type: backlog_item
kind: improvement
status: proposed
priority: high
effort: S
tags:
- signing
- key-rotation
links:
- target: adr-0002
  relation: tracks
- target: signer-core
  relation: related_to
---

## Context

`Signer.secret_keys` verifies from newest to oldest (`reversed(self.secret_keys)`), while `Serializer.fallback_signers` are tried after the primary signer fails. Callers combining both during a key + digest migration need an explicit test and docstring example showing the exact verification order.

## Acceptance Criteria

- Add a regression test verifying that a payload signed with an older key in `secret_keys` and a fallback digest in `fallback_signers` unsigns cleanly.
- Record the verification order in the session work log linked to `adr-0002`.

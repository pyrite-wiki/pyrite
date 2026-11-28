---
id: url-safe-compression-threshold
title: Benchmark URLSafeSerializer zlib compression overhead on small payloads
type: backlog_item
kind: spike
status: proposed
priority: medium
effort: S
tags:
- serialization
- url-safe
links:
- target: adr-0001
  relation: tracks
---

## Context

`URLSafeSerializerMixin.dump_payload` attempts `zlib.compress` on every payload and keeps the compressed bytes only when shorter than the uncompressed payload.

## Acceptance Criteria

- Measure compression overhead on payloads under 64 bytes and record whether a minimum length threshold is warranted.

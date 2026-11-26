---
id: adr-0002
title: "ADR-0002: Secret key rotation via ordered secret_keys list and fallback_signers"
type: adr
adr_number: 2
status: accepted
date: "2024-04-16"
tags:
- signing
- key-rotation
---

## Context

Applications need to rotate signing keys or migrate digest algorithms without invalidating active user sessions or signed URLs all at once (`CHANGES.rst`, Version 2.0.0; `src/itsdangerous/signer.py`).

## Decision

Accept an iterable of keys on `Signer(secret_key)` (normalized to `Signer.secret_keys: list[bytes]`, oldest to newest) and an optional `fallback_signers` list on `Serializer`. Signing always uses the newest key (`self.secret_key`, which returns `self.secret_keys[-1]`). Verification (`Signer.verify_signature`) iterates `reversed(self.secret_keys)` from newest to oldest, and `Serializer.loads` falls back through `fallback_signers` when earlier verification fails.

## Consequences

- Operators can append a new key to `secret_keys` to sign all new payloads while still accepting unexpired signatures produced with older keys.
- Removing an old key from the front of `secret_keys` revokes tokens signed with that key immediately.

## Alternatives rejected

- **Single key with external dual-serializer wrapper:** Forces every caller to catch `BadSignature` and retry a second `Serializer` instance by hand instead of keeping rotation policy in configuration (`src/itsdangerous/serializer.py`).

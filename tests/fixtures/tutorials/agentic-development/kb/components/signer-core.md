---
id: signer-core
title: Signer core
type: component
kind: module
path: src/itsdangerous/signer.py
owner: core
tags:
- signing
- core
links:
- target: adr-0002
  relation: implements
---

Core cryptographic signing primitives: `SigningAlgorithm`, `NoneAlgorithm`, `HMACAlgorithm`, and `Signer`.

- Normalizes `secret_key` into `secret_keys: list[bytes]` and derives key material via `derive_key()`.
- `sign(value)` signs with the newest key (`secret_keys[-1]`).
- `verify_signature(value, sig)` checks candidate keys in reverse order (newest to oldest) using constant-time ` hmac.compare_digest`.

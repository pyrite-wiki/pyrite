---
id: timed-serialization
title: Timed signing and serialization
type: component
kind: module
path: src/itsdangerous/timed.py
owner: core
dependencies:
- signer-core
tags:
- signing
- timestamps
links:
- target: adr-0001
  relation: implements
- target: adr-0002
  relation: implements
---

Time-bounded signing and serialization built on `Signer` and `Serializer`: `TimestampSigner` and `TimedSerializer`.

- Appends a 64-bit Unix timestamp separated by `sep` before signing (`TimestampSigner.sign`).
- Validates signature first, then parses the timestamp and checks `max_age` (`TimestampSigner.unsign`), raising `SignatureExpired` when `age > max_age` or `age < 0`.
- Inherits multi-key verification and `fallback_signers` traversal from `Signer` and `Serializer`.

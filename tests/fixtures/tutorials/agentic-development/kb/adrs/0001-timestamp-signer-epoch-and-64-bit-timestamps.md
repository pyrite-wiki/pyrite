---
id: adr-0001
title: "ADR-0001: 64-bit Unix timestamps in TimestampSigner"
type: adr
adr_number: 1
status: accepted
date: "2024-04-16"
tags:
- signing
- timestamps
---

## Context

`TimestampSigner` appends a timestamp to signed payloads so `unsign(..., max_age=...)` can reject expired tokens. Prior to version 2.0.0, timestamps were stored relative to a custom epoch (`2011-01-01`) in 32-bit integers (`CHANGES.rst`, Version 2.0.0).

## Decision

Store timestamps as full 64-bit Unix timestamps (`int(time.time())`) encoded with `int_to_bytes` and decoded with `bytes_to_int` in `src/itsdangerous/timed.py` (`TimestampSigner.get_timestamp` and `TimestampSigner.timestamp_to_datetime`).

## Consequences

- Timestamps do not overflow 32-bit bounds and require no custom epoch offset when converting to UTC `datetime` objects (`datetime.fromtimestamp(ts, tz=timezone.utc)`).
- Tokens remain compact because `int_to_bytes` strips leading zero bytes before Base64 encoding.

## Alternatives rejected

- **Custom moving epoch per major release (Version 2.0.0):** Bumping a custom epoch breaks tokens signed before the upgrade without eliminating the underlying width limit (`CHANGES.rst`, Version 2.0.0).

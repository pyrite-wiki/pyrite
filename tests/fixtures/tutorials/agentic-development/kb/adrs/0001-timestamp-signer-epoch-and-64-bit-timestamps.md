---
id: adr-0001
title: "ADR-0001: Full Unix timestamps in TimestampSigner"
type: adr
adr_number: 1
status: accepted
date: "2024-04-16"
tags:
- signing
- timestamps
---

## Context

`TimestampSigner` appends a timestamp to signed payloads so `unsign(..., max_age=...)` can reject expired tokens. Prior to version 2.0.0, timestamps were stored as offsets relative to a `2011-01-01` epoch (`CHANGES.rst`, Version 2.0.0).

## Decision

Store full Unix timestamps (`int(time.time())`) directly without subtracting an epoch offset, encoded with `int_to_bytes` and decoded with `bytes_to_int` in `src/itsdangerous/timed.py` (`TimestampSigner.get_timestamp` and `TimestampSigner.timestamp_to_datetime`), allowing dates before 2011 (`CHANGES.rst`, Version 2.0.0).

## Consequences

- `TimestampSigner` can represent timestamps before 2011 without custom epoch adjustment (`CHANGES.rst`, Version 2.0.0).
- Decoding maps `bytes_to_int` output directly to UTC `datetime` via `datetime.fromtimestamp(ts, tz=timezone.utc)` (`src/itsdangerous/timed.py`).
- Tokens remain compact because `int_to_bytes` strips leading zero bytes before Base64 encoding (`src/itsdangerous/encoding.py`).

## Alternatives rejected

- Alternative designs and detailed rationale for dropping the 2011 epoch offset beyond supporting dates before 2011 are not recorded in `CHANGES.rst` or the `2.2.0` source tree.

## Open questions

- Were alternatives such as keeping a configurable epoch offset considered before 2.0.0 (rationale not recorded in `CHANGES.rst`)?

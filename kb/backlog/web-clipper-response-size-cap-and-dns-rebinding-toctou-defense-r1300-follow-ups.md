---
id: web-clipper-response-size-cap-and-dns-rebinding-toctou-defense-r1300-follow-ups
title: 'Web clipper: response-size cap and DNS-rebinding TOCTOU defense (r1300 follow-ups)'
type: backlog_item
tags:
- security
- hosting
- ssrf
- clipper
importance: 5
status: in_progress
priority: medium
rank: 0
---

Follow-up from r1300 clipper-ssrf-defense, which closed the primary SSRF vector (private/loopback/link-local/reserved IP blocklist via ipaddress stdlib classifiers). Two acceptance criteria from the original ticket remain open:

1. **Response-body size cap.** A malicious URL serving a multi-GB stream could OOM the worker. Currently httpx buffers the full response into memory via response.text. Fix: stream the response via client.stream() and abort if cumulative bytes exceed MAX_CLIP_BYTES (default 10 MB, configurable via PYRITE_CLIP_MAX_BYTES).

2. **DNS-rebinding TOCTOU defense.** The current validator resolves the hostname once, checks all returned IPs, then httpx independently re-resolves and connects. A malicious host can return a public A record to the validator and a private A record to httpx milliseconds later. Fix: resolve once, pin the connection by passing the resolved IP via httpx transport='ssl' + host header, or by using an httpx custom HTTPCore transport that fixes the destination IP. (Reference: https://github.com/encode/httpx/discussions/2470)

3. **Optional deploy-level hostname allowlist.** Off by default; configurable via PYRITE_CLIP_ALLOWLIST=host1,host2,... for tighter lockdown on hardened deployments.

Acceptance:
  - Streaming clip of a 100 MB upstream response aborts after the configured cap, not after full buffer.
  - DNS rebinding test (mock socket.getaddrinfo to return public IP, but mock httpx connect target to private IP) is rejected.
  - Allowlist test: with PYRITE_CLIP_ALLOWLIST=example.com, clipping example.org returns CLIPPER_BLOCKED_HOST.

Effort: M. Should land before transparencycascade.org gates public clipper access.

---
id: web-clipper-response-size-cap-and-dns-rebinding-toctou-defense-r1300-follow-ups
title: 'Web clipper and URL checker: connect only to the address the destination check judged (DNS-rebinding defense)'
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

Follow-up from r1300 clipper-ssrf-defense, which closed the primary SSRF vector (a destination rule: every address a host resolves to must be globally routable). This item is now the DNS-rebinding (TOCTOU) defense only.

**Property.** For every fetch of a URL a user or an entry supplied (the web clipper and the QA URL checker, which share `check_url_safe` in `pyrite/utils/public_destination.py`), the host is resolved once per hop, every resolved address is judged by the rule, and the connection goes to an address that was judged, on the first hop and on every redirect hop. The Host header and TLS SNI / certificate verification keep the URL's name. No second, independent name resolution happens between the check and the connect.

**Was.** `check_url_safe` resolved, judged and returned nothing; httpx (clipper) and urllib (URL checker) then resolved the name again to connect, so a rebinding host could answer the check with a public address and the connect with an internal one.

**Done by.** `check_url_safe` returns the judged addresses; each hop records them in a per-fetch `PinnedAddresses`; the connection step dials only one of them (an httpcore network backend for the clipper's httpx client, an HTTP(S)Connection dial for the URL checker's urllib opener). A host with no recorded addresses is refused, never resolved. Tests: `tests/test_fetch_connects_to_checked_address.py`.

**Already landed, not in this item: the response-size cap.** `pyrite/services/clipper.py` streams the body through `_read_capped` and refuses past `MAX_CLIP_BYTES` (5 MiB, decoded bytes, compressed codings included) with `ClipperTooLargeError` (`CLIP_TOO_LARGE`, 413); tests in `tests/test_clipper_bounds.py` (`test_reading_stops_at_the_cap_and_does_not_drain_the_stream`, `test_a_page_past_the_cap_is_413_clip_too_large`) and `tests/test_clipper_decoding.py`. The cap is a constant, not the `PYRITE_CLIP_MAX_BYTES` setting first proposed here.

**Not in this item: the optional deploy-level hostname allowlist** (`PYRITE_CLIP_ALLOWLIST`). It is a separate decision, not part of making the connect match the check.

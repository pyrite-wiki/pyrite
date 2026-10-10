"""The shared grammar for indexed and rewritten wikilinks."""

import re

# Groups: KB prefix, target, heading, block id, display text.
WIKILINK_RE = re.compile(
    r"\[\[(?:([a-z0-9-]+):)?([^\]|#^]+?)(?:#([^\]|^]+?))?(?:\^([^\]|]+?))?(?:\|([^\]]+?))?\]\]"
)
TRANSCLUSION_RE = re.compile(r"!" + WIKILINK_RE.pattern)

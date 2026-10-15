"""Which KBs are public: the one rule every anonymous surface uses.

An anonymous visitor may only be shown KBs anyone may read. Until
`kb.yaml: published: true` ships, the signal is ``default_role == "read"``.
A KB with ``default_role`` unset or ``"none"`` is private for anonymous
surfaces, whatever the global auth settings are.

Keep this the only place the rule is written: two copies drifted once
(the former /site renderer used every KB while the sitemap used this rule).
"""

from __future__ import annotations

from ..config import PyriteConfig


def public_kb_names(config: PyriteConfig) -> list[str]:
    """Names of the public KBs, in config order.

    Uses ``all_kbs()`` (config.yaml plus DB-registered KBs), so a KB added
    with ``pyrite kb add`` and ``default_role: read`` is public too.
    """
    return [kb.name for kb in config.all_kbs() if kb.default_role == "read"]


def is_public_kb(config: PyriteConfig, kb_name: str) -> bool:
    """True when ``kb_name`` names a public KB."""
    return kb_name in public_kb_names(config)

"""KB name rules shared by every path that takes a KB name from outside.

An ephemeral KB's name is chosen by a write-role user; a subscribed KB's name
comes from the cloned repository's own ``kb.yaml``. Either becomes a directory
name, a registry key and a config key, so it must be a plain name, and it must
not already belong to another KB.
"""

import re

from ..config import PyriteConfig
from ..storage.database import PyriteDB

# No separators, no dot segments, not absolute, never a leading "-".
PLAIN_KB_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")

PLAIN_KB_NAME_RULE = "1-64 letters, digits, '-' or '_', starting with a letter or digit"


def is_plain_kb_name(name: object) -> bool:
    """True when `name` is a string that satisfies the plain-name rule."""
    return isinstance(name, str) and PLAIN_KB_NAME_RE.fullmatch(name) is not None


def kb_name_in_use(config: PyriteConfig, db: PyriteDB, name: str) -> bool:
    """True when config or the KB registry table already has this name."""
    if config.get_kb(name) is not None:
        return True
    return bool(db.execute_sql("SELECT 1 FROM kb WHERE name = :name", {"name": name}))


# Fixed first segments under /api/kbs; reserving a name keeps every KB route reachable.
RESERVED_KB_NAMES = frozenset({"ephemeral", "gc"})


def refuse_reserved_kb_name(name: str) -> None:
    """Refuse route-literal collisions when a new KB is registered."""
    if name in RESERVED_KB_NAMES:
        from ..exceptions import ConfigError

        raise ConfigError(f"KB name '{name}' is reserved for an API route; choose another name")

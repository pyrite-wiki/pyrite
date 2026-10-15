"""The one KB resolver: a caller's KB string becomes one KB, a KB set, or one refusal (#952).

Every surface asks this function and none decides on its own: REST through
`server/authz.py` (reads) and `AccessPolicy.authorize` (the write tier), MCP
in `_dispatch_tool`, the CLI through `cli.context.require_kb`, and the
services through `PyriteConfig.require_kb`. `tests/test_kb_resolver.py` pins
the function; `tests/test_kb_walk.py` pins that each surface asks it, for
every REST route, MCP tool and CLI command that takes a KB name.

The rule (`kb/design.md` principle 8; ADR-0037 §4; ADR-0049 Q3):

- A blank name (`access_policy.named_kb`) names no KB: "not named", an
  unplaced id (#846/#528). Never a refusal.
- A full name the caller may read is that KB.
- Anything else is one `KBNotFoundError` (`KB_NOT_FOUND`): an unknown name, a
  KB the caller may not read (the same answer, byte for byte, so the refusal
  is not an oracle for private KB names), and a **shortname**, which is link
  syntax only (`[[short:id]]`; the maintainer, 2026-10-15). The refusal
  carries `did_you_mean`, near matches drawn only from the KBs the caller may
  read; for a shortname whose KB the caller may read, that is the full name,
  and the suggestion says so.
- Existence is one lookup: config.yaml, then the registry cache
  (`PyriteConfig.get_kb`), then the live `kb` table when a database is given,
  so a KB registered after a long-running server loaded its config is found
  by every caller that asks (#925).

`readable_kbs` is the caller's `ReadScope.as_set()`: `UNSCOPED` (None) for a
caller that may read every KB. It is a required keyword, like the services'
own, so forgetting it is a `TypeError` and never "see everything".
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

from ..exceptions import KBNotFoundError
from .access_policy import named_kb

if TYPE_CHECKING:
    from ..config import KBConfig, PyriteConfig
    from ..storage.database import PyriteDB

#: The way forward in every unknown-KB refusal, on every surface. Names no KB.
HOW_TO_LIST_KBS = "Call orient with no KB name to list the KBs you can read."

#: Near matches offered at most (the cap `KBService.kb_name_suggestions` had).
NEAR_MATCH_LIMIT = 3


def readable_kb_names(config: PyriteConfig, readable_kbs: set[str] | None) -> list[str]:
    """The names of the KBs the caller may read, sorted."""
    return sorted(
        kb.name for kb in config.all_kbs() if readable_kbs is None or kb.name in readable_kbs
    )


def near_kb_names(config: PyriteConfig, name: object, readable_kbs: set[str] | None) -> list[str]:
    """Near matches for a name that is not a KB, from the readable set only.

    A name offered from outside that set would confirm a private KB exists.
    """
    if not isinstance(name, str):
        return []
    names = readable_kb_names(config, readable_kbs)
    return difflib.get_close_matches(name, names, n=NEAR_MATCH_LIMIT)


def _lookup(config: PyriteConfig, name: str, db: PyriteDB | None) -> KBConfig | None:
    """The one existence lookup: config, the registry cache, then the live table."""
    kb = config.get_kb(name)
    if kb is not None or db is None:
        return kb
    from .kb_registry_service import KBRegistryService

    return KBRegistryService(config, db).get_kb_config(name)


def resolve_kb(
    config: PyriteConfig,
    name: object,
    *,
    readable_kbs: set[str] | None,
    db: PyriteDB | None = None,
) -> KBConfig | None:
    """The KB `name` means for a caller who may read `readable_kbs`.

    None when `name` names no KB (blank). The KB when `name` is its full name
    and the caller may read it. Otherwise `KBNotFoundError`, as the module
    docstring says.
    """
    name = named_kb(name)
    if name is None:
        return None
    kb = _lookup(config, name, db)
    if kb is not None and (readable_kbs is None or kb.name in readable_kbs):
        return kb
    raise _refusal(config, name, readable_kbs)


def kb_set(
    config: PyriteConfig,
    name: object,
    *,
    readable_kbs: set[str] | None,
    db: PyriteDB | None = None,
) -> set[str] | None:
    """The KB set a read spans: the one KB `name` resolves to, else the
    caller's readable set (a fresh copy; None when the caller is unscoped).

    The one spelling of ``None if kb else scope.as_set()``.
    """
    kb = resolve_kb(config, name, readable_kbs=readable_kbs, db=db)
    if kb is not None:
        return {kb.name}
    return None if readable_kbs is None else set(readable_kbs)


def _refusal(config: PyriteConfig, name: str, readable_kbs: set[str] | None) -> KBNotFoundError:
    """The refusal for `name`: the same class and message whether the name is
    unknown, unreadable or a shortname; only the suggestion knows which."""
    message = f"KB '{name}' not found"
    short = config.get_kb_by_shortname(name)
    if short is not None and (readable_kbs is None or short.name in readable_kbs):
        return KBNotFoundError(
            message,
            suggestion=(
                f"'{name}' is the shortname of KB '{short.name}'. A shortname is link "
                f"syntax only ([[{name}:<id>]]); name the KB '{short.name}'."
            ),
            did_you_mean=[short.name],
        )
    matches = near_kb_names(config, name, readable_kbs)
    how = HOW_TO_LIST_KBS
    if matches:
        how = f"Did you mean: {', '.join(matches)}? {how}"
    return KBNotFoundError(message, suggestion=how, did_you_mean=matches)

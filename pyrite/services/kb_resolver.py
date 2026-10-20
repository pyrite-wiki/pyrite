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
  carries `did_you_mean`: for a shortname whose KB the caller may read, the
  full name, and the suggestion says so; for an unscoped caller, near
  matches; for a scoped caller, nothing but the static hint (0.25.7's
  decision: no name in a scoped refusal).
- Existence is one lookup: config.yaml, then the registry cache
  (`PyriteConfig.get_kb`), then the live `kb` table when a database is given,
  so a KB registered after a long-running server loaded its config is found
  by every caller that asks (#925). Only a row `pyrite kb add` wrote counts
  there: the row the server seeds for a config.yaml KB carries none of the
  entry's attributes, and a resolved KB carries every attribute of the
  configured KB it names.

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

#: The way forward in every unknown-KB refusal, on every surface. Names no KB,
#: and names the tool by the name each surface knows it by (MCP's is
#: `kb_orient`, the CLI's `pyrite orient`; REST lists them at `/api/kbs`).
HOW_TO_LIST_KBS = (
    "To list the KBs you can read, call kb_orient (MCP) or `pyrite orient` (CLI) with no KB name."
)

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
    raise _not_a_kb(config, name, readable_kbs)


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


def readable_kb_with_shortname(
    config: PyriteConfig, name: str, readable_kbs: set[str] | None
) -> KBConfig | None:
    """The KB among the caller's readable ones whose shortname is `name`
    (`PyriteConfig.readable_kb_with_shortname`: looked up in the readable set
    only, so a private KB sharing the shortname never changes the answer,
    #965 blocking 3). The implementation is on the config so that the index,
    which may not import services, stores the KB a link prefix names."""
    return config.readable_kb_with_shortname(name, readable_kbs=readable_kbs)


def readable_kb_named(
    config: PyriteConfig, name: str, readable_kbs: set[str] | None
) -> KBConfig | None:
    """The KB a link prefix (`[[name:id]]`) means for a caller who may read
    `readable_kbs`: the readable KB with that full name, else the readable
    KB with that shortname, else None (`PyriteConfig.readable_kb_named`).
    Neither step consults a KB outside the readable set, so an unreadable
    KB never changes the answer, not even one whose full name is a readable
    KB's shortname (#965 round 3)."""
    return config.readable_kb_named(name, readable_kbs=readable_kbs)


def _not_a_kb(config: PyriteConfig, name: str, readable_kbs: set[str] | None) -> KBNotFoundError:
    """The refusal for `name`: the same class and message whether the name is
    unknown, unreadable or a shortname; only the suggestion knows which."""
    message = f"KB '{name}' not found"
    short = readable_kb_with_shortname(config, name, readable_kbs)
    if short is not None:
        return KBNotFoundError(
            message,
            suggestion=(
                f"'{name}' is the shortname of KB '{short.name}'. A shortname is link "
                f"syntax only ([[{name}:<id>]]); name the KB '{short.name}'."
            ),
            did_you_mean=[short.name],
        )
    # Near matches for an unscoped caller only. A scoped caller gets the
    # static hint and no names (0.25.7, #66 round 1 item 4): a match would
    # be drawn from outside its readable set or confirm what is inside it,
    # and the readable set is one `orient` call away. The shortname case
    # above is the issue's own exception (#952): the KB named is readable.
    matches = near_kb_names(config, name, readable_kbs) if readable_kbs is None else []
    how = HOW_TO_LIST_KBS
    if matches:
        how = f"Did you mean: {', '.join(matches)}? {how}"
    return KBNotFoundError(message, suggestion=how, did_you_mean=matches)

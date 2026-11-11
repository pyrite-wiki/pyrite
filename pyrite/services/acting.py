"""Who is acting for the write in progress: an account, the operator, or nobody.

The KB write pipeline (`KBService`) holds every surface to rules that depend
on the caller -- today, that only the holder of a held entry can change its
assignee -- so it must know the caller without each surface passing it down
through every service. The surfaces bind it once per call, from the
principal (`acting_for`):

- MCP: `PyriteMCPServer._dispatch_tool`, around the handler;
- REST: an async dependency on every `/api` route (`api.bind_acting_account`).
  It is async on purpose: an async dependency runs in the request's own task,
  so the value it sets is copied into the worker thread a sync endpoint runs
  in. A value set in a sync dependency would be lost. Every request starts
  with nobody bound (`api`'s ASGI wrapper), so a route that binds nothing is
  never the operator;
- the CLI: its root command binds the operator for the command it runs.

Three values:

- ``user:<id>``, `identity.person_key` of a signed-in account;
- `OPERATOR`, bound only explicitly: the local process (the CLI, stdio MCP)
  and an operator API key or auth disabled (`Principal.from_api_key`). The
  operator is not held to the holder rule;
- None, the default: nobody identified -- the anonymous visitor, an MCP call
  with no principal, a job or hook with nothing bound. Nobody is never the
  operator: where the holder rule applies, nobody's write is refused.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .access_policy import Principal


class _Operator:
    """The type of `OPERATOR`; one instance."""

    def __repr__(self) -> str:
        return "OPERATOR"


#: The operator, bound explicitly. Never a string, so no account key or
#: caller-supplied value can equal it.
OPERATOR: Final = _Operator()

Acting = str | _Operator | None

_ACTING: contextvars.ContextVar[Acting] = contextvars.ContextVar(
    "pyrite_acting_account", default=None
)


def acting() -> Acting:
    """Who the current write acts for: an account key, `OPERATOR`, or None."""
    return _ACTING.get()


def acting_account() -> str | None:
    """The account the current write acts for, or None (the operator or
    nobody; `acting` tells them apart)."""
    value = _ACTING.get()
    return value if isinstance(value, str) else None


def acting_for(principal: Principal | None) -> Acting:
    """What a surface binds for ``principal``: its account key when signed
    in, `OPERATOR` for the local process or an operator key (auth disabled
    included), None for anyone else (the anonymous visitor, no principal)."""
    from ..plugins.identity import person_key

    key = person_key(principal)
    if key is not None:
        return key
    if principal is not None and principal.kind in ("local", "operator_key"):
        return OPERATOR
    return None


_LOCAL_PROCESS: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "pyrite_local_process", default=False
)


def is_local_process() -> bool:
    """Whether the current call is the operator's own local process: the CLI
    (its root command binds it), and stdio MCP under it. False for every
    network caller -- a REST request (``NobodyActingMiddleware`` unbinds it)
    and an MCP call made with a principal of any other kind -- whatever role
    it holds, an operator API key included; and False by default, so a
    surface that binds nothing is never local.

    `OPERATOR` says who a write acts for; this says where the call comes
    from. Rules that depend on reach rather than role ask this: registering
    a KB path (``settings.kb_roots``) and which git credentials a network
    operation may use (``GitService._credentials``)."""
    return _LOCAL_PROCESS.get()


@contextlib.contextmanager
def local_process(value: bool = True) -> Iterator[None]:
    """Bind ``is_local_process`` to ``value`` for the duration of the block."""
    token = _LOCAL_PROCESS.set(value)
    try:
        yield
    finally:
        _LOCAL_PROCESS.reset(token)


def bind_acting_account(value: Acting) -> None:
    """Bind ``value`` for the rest of the current context (one request's task)."""
    _ACTING.set(value)


@contextlib.contextmanager
def acting_as(value: Acting) -> Iterator[None]:
    """Bind ``value`` for the duration of the block."""
    token = _ACTING.set(value)
    try:
        yield
    finally:
        _ACTING.reset(token)

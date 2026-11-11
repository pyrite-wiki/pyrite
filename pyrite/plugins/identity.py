"""Who a tool records, for tools that record a person. Alpha contract.

A tool that records who did something -- who voted, wrote, reviewed, claimed
or is assigned -- never takes that person from its arguments when the caller
has a session identity (maintainer decision, 2026-10-02). The tool declares
which arguments name a person, under `PERSON_ARGUMENTS` in its tool dict, and
the MCP dispatcher (and the REST routes that write the same records) sets
them from the session's `access_policy.Principal` before the handler runs.
The handler only ever sees the resolved value, so how it is written --
wrapped, partial, ``**kwargs`` -- cannot make it trust an argument.

- A signed-in user is recorded under `person_key` -- ``user:<account id>``,
  never a username, so no username (a digits-only one included) and no
  free-text label can be taken for an account. An argument naming anyone
  else is refused (`IdentityMismatchError`, ``FORBIDDEN``, with a
  suggestion).
- A scoped caller with no account (the anonymous visitor) records no one:
  the call is refused.
- A caller with no session identity -- the local operator (CLI, stdio), an
  operator API key, auth disabled -- names the person with the argument,
  which is a label.

Records stored before this rule hold whatever the caller passed; they are
labels, and no account inherits them.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from ..exceptions import Forbidden, ValidationError

if TYPE_CHECKING:
    from ..services.access_policy import Principal

#: The tool-dict key declaring a tool's person arguments: ``{path: mode}``.
#: A path is an argument name, or ``"<list argument>[].<field>"`` for a field
#: of each item of a list argument.
PERSON_ARGUMENTS = "person_arguments"

#: Mode: left out, the argument records the caller (who claims, who votes).
CALLER = "caller"
#: Mode: left out, the argument records no one (a task with no assignee).
OPTIONAL = "optional"

#: The tool-dict key asking the dispatcher to pass the caller's key to the
#: handler as ``acting=`` -- for a handler that must compare it with what a
#: record already holds (whose claim this is).
ACTING_KEYWORD = "acting_keyword"


class IdentityMismatchError(Forbidden):
    """An argument names someone other than the signed-in caller."""

    def __init__(self, message: str, *, suggestion: str) -> None:
        super().__init__(message)
        self.suggestion = suggestion


class IdentityRequiredError(ValidationError):
    """No session identity and no argument: there is no one to record."""

    def __init__(self, message: str, *, suggestion: str) -> None:
        super().__init__(message)
        self.suggestion = suggestion


def person_key(principal: Principal | None) -> str | None:
    """The key a principal's records are stored under, or None when it has
    no account: ``user:<id>`` for a signed-in user, the one derivation."""
    if principal is not None and principal.kind == "user" and principal.user_id is not None:
        return f"user:{principal.user_id}"
    return None


def resolve_person_arguments(
    arguments: dict[str, Any],
    spec: dict[str, str],
    principal: Principal | None,
    *,
    scoped: bool | None = None,
) -> dict[str, Any]:
    """``arguments`` with every person argument in ``spec`` set from
    ``principal`` (a copy; the caller's dict is not changed).

    ``scoped`` says whether the connection has a per-user identity at all;
    it defaults to ``principal.scoped``. A scoped connection with no account
    key is refused rather than allowed to name a person.
    """
    key = person_key(principal)
    if scoped is None:
        scoped = principal is not None and principal.scoped
    resolved = copy.deepcopy(arguments)
    for path, mode in spec.items():
        for container, field in _containers(resolved, path):
            value = container.get(field)
            given = value is not None and str(value).strip() != ""
            if key is not None:
                if given and str(value) != key:
                    raise IdentityMismatchError(
                        f"'{field}' names '{value}', but this connection is signed in as {key}",
                        suggestion=f"Omit '{field}' to act as yourself",
                    )
                if given or mode == CALLER:
                    container[field] = key
            elif scoped:
                if given or mode == CALLER:
                    raise Forbidden(f"'{field}' records a person; sign in to act as yourself")
            elif not given and mode == CALLER:
                raise IdentityRequiredError(
                    f"'{field}' is required: this connection has no signed-in user to record",
                    suggestion=f"Pass '{field}' naming the person to record",
                )
    return resolved


def _containers(arguments: dict[str, Any], path: str):
    """``(dict, field)`` for each place ``path`` addresses in ``arguments``."""
    if "[]." not in path:
        yield arguments, path
        return
    list_name, field = path.split("[].", 1)
    for item in arguments.get(list_name) or []:
        if isinstance(item, dict):
            yield item, field

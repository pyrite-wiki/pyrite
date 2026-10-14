"""What a CLI command did, as a value (ADR-0046).

A command computes what happened and returns it. ``PyriteCLIGroup.invoke``
(``pyrite/utils/errors.py``), the root group every leaf command runs inside, is
the one point that renders the returned ``Outcome`` in the requested format
and maps its kind to the exit code of docs/json-contracts.md, "Exit codes
(CLI)":

    done     0   every effect asked for happened
    partial  3   some did; the data says which
    nothing  1   none did (a lost claim, every child refused)
    refusal  1   a raised ``PyriteError``; its code is on the class (ADR-0037 §3)
    usage    2   click's, while it is parsing

There is no ``error`` kind: a refusal is raised, not returned, so there is one
representation of it. Machine formats see ``"outcome": "error"`` on the error
shape the root prints for it.

A command that declares the shared ``--format`` option cannot print its own
result or pick its own exit code: the root checks that when the command runs
(``PyriteCLIGroup``, which also names what it cannot see). A plugin command may
return an ``Outcome`` without declaring the option and get the same rendering,
or keep returning ``None`` and own its output and exit code (decision 5).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

#: A rich renderer: ``(console, data) -> None``. Prints for a person; the
#: machine formats never see it.
RichRenderer = Callable[[Any, Any], None]


class OutcomeKind(StrEnum):
    DONE = "done"
    PARTIAL = "partial"
    NOTHING = "nothing"


# The one table, read by the root and by nothing else that decides an exit
# code. PARTIAL_EXIT in pyrite.utils.errors is the same 3.
EXIT_CODES = MappingProxyType(
    {
        OutcomeKind.DONE: 0,
        OutcomeKind.PARTIAL: 3,
        OutcomeKind.NOTHING: 1,
    }
)

#: The ``outcome`` value of the error shape a refusal is printed as.
REFUSED = "error"


@dataclass(frozen=True)
class Outcome:
    """What a command did: its kind, the data machine formats print, and how a
    person sees it (``rich``; a field-per-line listing when omitted)."""

    kind: OutcomeKind
    data: Any = None
    rich: RichRenderer | None = None

    def __post_init__(self) -> None:
        # Fail at the line that built it, not when the root renders it.
        _check(self)

    @classmethod
    def done(cls, data: Any = None, rich: RichRenderer | None = None) -> Outcome:
        return cls(OutcomeKind.DONE, data, rich)

    @classmethod
    def partial(cls, data: Any = None, rich: RichRenderer | None = None) -> Outcome:
        return cls(OutcomeKind.PARTIAL, data, rich)

    @classmethod
    def nothing(cls, data: Any = None, rich: RichRenderer | None = None) -> Outcome:
        return cls(OutcomeKind.NOTHING, data, rich)

    @classmethod
    def from_counts(
        cls, done: int, failed: int, data: Any = None, rich: RichRenderer | None = None
    ) -> Outcome:
        """A command that loops over items: none failed is done, some of each is
        partial, none done is nothing (``exit_unless_whole``'s rule)."""
        if not failed:
            kind = OutcomeKind.DONE
        elif done:
            kind = OutcomeKind.PARTIAL
        else:
            kind = OutcomeKind.NOTHING
        return cls(kind, data, rich)

    @property
    def exit_code(self) -> int:
        """The exit code of this kind. For a caller's convenience: the root
        reads ``EXIT_CODES`` itself, so overriding this changes nothing."""
        return EXIT_CODES[self.kind]

    def payload(self) -> dict[str, Any]:
        """What a machine format prints: the data plus an additive ``outcome``
        key (maintainer, 2026-10-08), so a caller that cannot see the exit code
        can tell a partial result from a whole one. Always a mapping."""
        _check(self)
        return {**(self.data or {}), "outcome": self.kind.value}


def _check(outcome: Outcome) -> None:
    """An Outcome the root can print without losing or overwriting anything.

    ``outcome`` is the root's key: data that brought its own would have it
    overwritten, and data that is not a mapping (a list) has nowhere to carry
    it, so a partial list would read as a whole one. Both are refused. A list
    goes under a key: ``{"tasks": [...]}``.
    """
    if type(outcome.kind) is not OutcomeKind:
        raise TypeError(f"an Outcome's kind is an OutcomeKind, not {outcome.kind!r}")
    data = outcome.data
    if data is None:
        return
    if not isinstance(data, dict):
        raise TypeError(
            f"an Outcome's data is a mapping or None, not {type(data).__name__}: "
            "put a list under a key, so the outcome key has somewhere to go"
        )
    if "outcome" in data:
        raise TypeError("an Outcome's data may not have an 'outcome' key: the root adds it")


def print_fields(console: Any, data: Any) -> None:
    """The rich view of an outcome whose command gave no renderer."""
    from rich.text import Text

    for key, value in (data or {}).items():
        shown = value if isinstance(value, str) else json.dumps(value, default=str)
        console.print(Text(f"{key}: {shown}"))


def document(outcome: Outcome, fmt: str) -> str:
    """``outcome`` as one document in a machine format. Prints nothing."""
    from ..formats import format_response

    return format_response(outcome.payload(), fmt)[0]

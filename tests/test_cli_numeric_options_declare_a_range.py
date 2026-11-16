"""Every numeric CLI option declares its range where it is declared (#1541).

The property: every ``int`` or ``float`` option or argument of every registered
command has ``min`` or ``max`` on it (so click refuses an out-of-range value as a
usage error, exit 2, before any code runs), or sits on the reviewed ``UNBOUNDED``
list with the reason no range makes sense, or on the seeded ``NOT_YET`` list. A
new numeric option without a range fails here.

The list of commands is the registry, not a scan of the source: the click tree
Typer builds from the three root apps, extension groups included, through the
walk the other CLI structure tests use (``_commands``, ``outcome_walk.leaves``).
The checker takes any such mapping, so the planted tests below point it at a
small app built in the test.

What counts, read from the click parameter type: INT, FLOAT, IntRange and
FloatRange (so ``Optional[int]``, ``int | None``, ``Annotated[int, typer.Option()]``,
``list[int]`` and the members of a ``tuple[int, int]`` are all seen). Bounded means
a Range with ``min`` or ``max`` set. One exclusion: a ``count=True`` option
(``-v -v``). Typer gives it a plain INT type, but the user types no number: the
value is how many times the flag was repeated and cannot be out of range.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Optional

import pytest
import typer

from tests.outcome_walk import leaves, root_app

sys.path.insert(0, str(Path(__file__).parent))
from test_requested_effect_exit_code import _commands  # noqa: E402

ROOTS = ("pyrite", "pyrite-read", "pyrite-admin")

_INT = "IntParamType"
_FLOAT = "FloatParamType"


@dataclass(frozen=True)
class NumericParam:
    command: str
    name: str
    kind: str  # "int" or "float"
    bounded: bool

    @property
    def row(self) -> tuple[str, str]:
        return (self.command, self.name)


def _class_names(obj) -> set[str]:
    return {k.__name__ for k in type(obj).__mro__}


def _numeric_members(click_type) -> list:
    """The INT/FLOAT types inside a parameter type (a tuple type has several)."""
    members = getattr(click_type, "types", None)
    if members is not None:
        return [m for t in members for m in _numeric_members(t)]
    return [click_type] if _class_names(click_type) & {_INT, _FLOAT} else []


def numeric_params(commands: Mapping[str, object]) -> list[NumericParam]:
    """Every numeric parameter of the given click commands (path -> command)."""
    found = []
    for path, cmd in commands.items():
        for param in getattr(cmd, "params", ()):
            if getattr(param, "count", False):
                continue
            members = _numeric_members(param.type)
            if not members:
                continue
            bounded = all(
                getattr(m, "min", None) is not None or getattr(m, "max", None) is not None
                for m in members
            )
            kind = "float" if _FLOAT in _class_names(members[0]) else "int"
            found.append(NumericParam(path, param.name, kind, bounded))
    return found


def commands_of(app: typer.Typer, prog: str = "app") -> dict[str, object]:
    """The mapping ``registry()`` gives for the real roots, for a small app: the
    root group (its own options) and every leaf under it, nested groups included."""
    root = typer.main.get_command(app)
    found = {prog: root}
    found.update({" ".join((prog, *path)): cmd for path, cmd in leaves(root)})
    return found


def registry() -> dict[str, object]:
    """Every leaf of the three roots, and the roots' own options (``--verbose``)."""
    found = dict(_commands())
    for prog in ROOTS:
        found[prog] = typer.main.get_command(root_app(prog))
    return found


# --------------------------------------------------------------------------
# The lists
# --------------------------------------------------------------------------

#: (command path, parameter name) -> why no range makes sense. A row is added
#: only with a true reason, never to make the test pass. Empty today.
UNBOUNDED: dict[tuple[str, str], str] = {}

#: #1541: unbounded on ``dev`` today, grouped by the module that declares them.
#: Each module is a good first issue: add the range where the option is
#: declared, delete its rows here (test (b) requires it) and lower
#: NOT_YET_PINNED_SIZE. The ``pyrite-read`` rows are the same declarations as
#: their ``pyrite`` twins, except ``timeline``: a second copy in read_cli.py.
NOT_YET: set[tuple[str, str]] = {
    # pyrite/cli/search_commands.py
    ("pyrite search", "limit"),
    ("pyrite-read search", "limit"),
    # pyrite/cli/entry_commands.py (see also #1537)
    ("pyrite create", "importance"),
    ("pyrite update", "importance"),
    # pyrite/cli/browse_commands.py
    ("pyrite orient", "recent"),
    # pyrite/cli/__init__.py
    ("pyrite ci", "tier"),
    ("pyrite serve", "port"),
    # pyrite/cli/kb_commands.py
    ("pyrite kb create", "ttl"),
    # pyrite/cli/collection_commands.py
    ("pyrite collections query", "limit"),
    # pyrite/cli/qa_commands.py
    ("pyrite qa assess", "tier"),
    ("pyrite qa assess", "max_age"),
    # pyrite/cli/link_commands.py
    ("pyrite links batch-suggest", "limit_per_entry"),
    ("pyrite links orphans", "min_importance"),
    # pyrite/cli/task_commands.py
    ("pyrite task list", "priority"),
    # pyrite/read_cli.py
    ("pyrite-read timeline", "min_importance"),
    ("pyrite-read timeline", "limit"),
    # extensions/software-kb/src/pyrite_software_kb/cli.py
    ("pyrite sw adrs", "limit"),
    ("pyrite sw adrs", "offset"),
    ("pyrite sw backlog", "limit"),
    ("pyrite sw backlog", "offset"),
    ("pyrite sw standards", "limit"),
    ("pyrite sw standards", "offset"),
    ("pyrite sw components", "limit"),
    ("pyrite sw components", "offset"),
    # extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py
    ("pyrite investigation timeline", "min_importance"),
    ("pyrite investigation timeline", "limit"),
    ("pyrite investigation entities", "min_importance"),
    ("pyrite investigation entities", "limit"),
    ("pyrite investigation sources", "limit"),
    ("pyrite investigation claims", "min_importance"),
    ("pyrite investigation claims", "limit"),
    ("pyrite investigation qa", "stale_days"),
    ("pyrite investigation search", "limit"),
    ("pyrite investigation ownership", "depth"),
    ("pyrite investigation money-flow", "hops"),
    ("pyrite investigation export", "min_importance"),
    ("pyrite investigation dedup", "threshold"),
    # extensions/encyclopedia/src/pyrite_encyclopedia/cli.py
    ("pyrite wiki stubs", "limit"),
    # extensions/social/src/pyrite_social/cli.py
    ("pyrite social newest", "limit"),
    ("pyrite social top", "limit"),
}

#: May shrink, never grow (#1541). Lower it in the same commit that deletes rows
#: from NOT_YET; raising it is the thing this test exists to refuse.
NOT_YET_PINNED_SIZE = 40

FIX = "declare min= or max= in the option where it is declared, see #1541"


# --------------------------------------------------------------------------
# The real registry
# --------------------------------------------------------------------------


def test_the_walk_reaches_the_core_and_every_extension():
    """CI installs all five extensions; a walk without one would shrink silently
    and the stale-row test would then fail for the wrong reason. Say it here."""
    commands = registry()
    for expected in (
        "pyrite search",
        "pyrite-read search",
        "pyrite sw backlog",
        "pyrite investigation timeline",
        "pyrite wiki stubs",
        "pyrite social top",
        "pyrite zettel new",
    ):
        assert expected in commands, (
            f"{expected!r} is not in the registry: is its extension installed in this "
            "environment? (the ratchet needs all five, as CI has)"
        )
    params = numeric_params(commands)
    assert len(params) >= 50, f"the walk saw only {len(params)} numeric parameters"
    assert any(p.bounded for p in params) and any(not p.bounded for p in params)


def test_every_numeric_option_declares_a_range_or_is_listed():
    offenders = [
        p
        for p in numeric_params(registry())
        if not p.bounded and p.row not in UNBOUNDED and p.row not in NOT_YET
    ]
    assert not offenders, "\n".join(
        f"`{p.command}` option/argument `{p.name}` ({p.kind}) declares no min or max: {FIX}"
        for p in offenders
    )


def test_listed_rows_are_not_stale():
    """A fix must delete its row: a row naming a command or parameter that is gone,
    or one that is now bounded, fails."""
    by_row = {p.row: p for p in numeric_params(registry())}
    problems = []
    for label, rows in (("NOT_YET", NOT_YET), ("UNBOUNDED", UNBOUNDED)):
        for command, name in sorted(rows):
            found = by_row.get((command, name))
            if found is None:
                problems.append(
                    f"{label} row `{command}` `{name}`: no such numeric option; delete the row"
                )
            elif found.bounded:
                problems.append(
                    f"{label} row `{command}` `{name}` is bounded now; delete the row "
                    "(and lower NOT_YET_PINNED_SIZE)"
                )
    assert not problems, "\n".join(problems)


def test_not_yet_may_shrink_and_never_grow():
    assert len(NOT_YET) <= NOT_YET_PINNED_SIZE, (
        f"NOT_YET has {len(NOT_YET)} rows, the pinned size is {NOT_YET_PINNED_SIZE}: "
        f"it may shrink and never grow (#1541). Give the new option a range instead: {FIX}"
    )


def test_the_pin_is_not_slack():
    """The pin follows the list down: a smaller NOT_YET with the old pin would let
    a later PR add rows back."""
    assert len(NOT_YET) == NOT_YET_PINNED_SIZE, (
        f"NOT_YET has {len(NOT_YET)} rows; lower NOT_YET_PINNED_SIZE ({NOT_YET_PINNED_SIZE}) to match"
    )


def test_the_lists_do_not_overlap_and_every_reason_is_a_sentence():
    both = sorted(NOT_YET & set(UNBOUNDED))
    assert not both, f"on both NOT_YET and UNBOUNDED, pick one: {both}"
    for row, reason in UNBOUNDED.items():
        assert isinstance(reason, str) and len(reason.split()) >= 3, (
            f"UNBOUNDED row {row} needs a reason that says why no range makes sense, got {reason!r}"
        )


# --------------------------------------------------------------------------
# Planted violations: the checker, on a small app
# --------------------------------------------------------------------------


def _app(*functions, nested=()) -> typer.Typer:
    app = typer.Typer()
    for fn in functions:
        app.command()(fn)
    # a Typer with one command collapses into that command; keep a group
    app.command("filler")(lambda: None)
    if nested:
        sub = typer.Typer()
        for fn in nested:
            sub.command()(fn)
        sub.command("filler")(lambda: None)
        app.add_typer(sub, name="grp")
    return app


def _report(app: typer.Typer) -> dict[tuple[str, str], bool]:
    """(command, parameter) -> bounded, for every numeric parameter."""
    return {p.row: p.bounded for p in numeric_params(commands_of(app))}


def plant_int(n: int = 5): ...
def plant_float(x: float = 1.0): ...
def plant_argument(n: int = typer.Argument(1)): ...
def plant_optional(n: Optional[int] = None): ...  # noqa: UP045
def plant_union(n: int | None = None): ...
def plant_annotated(n: Annotated[int, typer.Option()] = 3): ...
def plant_list(n: list[int] = typer.Option([])): ...  # noqa: B008
def plant_tuple(n: tuple[int, int] = (1, 2)): ...


@pytest.mark.parametrize(
    ("function", "param"),
    [
        (plant_int, "n"),
        (plant_float, "x"),
        (plant_argument, "n"),
        (plant_optional, "n"),
        (plant_union, "n"),
        (plant_annotated, "n"),
        (plant_list, "n"),
        (plant_tuple, "n"),
    ],
    ids=lambda v: getattr(v, "__name__", v),
)
def test_planted_unbounded_numeric_parameter_is_reported(function, param):
    name = function.__name__.removeprefix("plant_")
    report = _report(_app(function))
    assert report == {(f"app {function.__name__.replace('_', '-')}", param): False}, (
        f"{name}: expected the planted parameter to be seen and unbounded, got {report}"
    )


def test_planted_unbounded_option_in_a_nested_group_is_reported():
    report = _report(_app(nested=(plant_int,)))
    assert report == {("app grp plant-int", "n"): False}


def test_planted_unbounded_option_on_the_root_group_is_reported():
    app = _app()

    @app.callback()
    def root(depth: int = 1): ...

    assert _report(app) == {("app", "depth"): False}


@pytest.mark.control(reason="a bounded or non-numeric option must not be reported")
def test_controls_are_not_reported_unbounded():
    def min_only(n: int = typer.Option(1, min=0)): ...
    def max_only(n: int = typer.Option(1, max=9)): ...
    def float_bounded(x: float = typer.Option(0.5, min=0.0, max=1.0)): ...
    def text(s: str = "x"): ...
    def flag(f: bool = False): ...
    def verbose(v: int = typer.Option(0, "--verbose", "-v", count=True)): ...

    report = _report(_app(min_only, max_only, float_bounded, text, flag, verbose))
    assert report == {
        ("app min-only", "n"): True,
        ("app max-only", "n"): True,
        ("app float-bounded", "x"): True,
    }, report


@pytest.mark.control(
    reason="the count exclusion is a decision, not an accident: pin what Typer gives"
)
def test_a_count_option_is_a_plain_int_to_click_and_is_excluded_by_the_checker():
    def verbose(v: int = typer.Option(0, "--verbose", "-v", count=True)): ...

    cmd = commands_of(_app(verbose))["app verbose"]
    param = next(p for p in cmd.params if p.name == "v")
    assert param.count and getattr(param.type, "min", None) is None  # Typer: not an IntRange
    assert numeric_params({"app verbose": cmd}) == []

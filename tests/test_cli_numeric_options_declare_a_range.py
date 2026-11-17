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
a Range whose type REFUSES a value just outside each declared bound (#1541: "Typer
refuses an out-of-range value as a usage error"). One exclusion: a ``count=True`` option
(``-v -v``). Typer gives it a plain INT type, but the user types no number: the
value is how many times the flag was repeated and cannot be out of range.

What decides whether a Range refuses (locked click 8.5.0, ``_NumberRangeBase``;
Typer exposes only ``min``, ``max`` and ``clamp``, a hand-built click type all five):
``min`` and ``max``: a declared bound, none declared means unbounded; ``min_open``
and ``max_open``: the bound itself is outside, so it is the value probed; ``clamp``:
``convert`` returns the bound instead of failing, so the type does not refuse and
the option is NOT bounded (the FIX says to drop ``clamp=True``). Nothing else in
click's range types affects refusal. The check is behavioural, not an attribute
scan: for each declared bound the type's own ``convert`` is called on a value just
outside it (int: one step; float: ``math.nextafter``) and must raise click's
bad-parameter error. Where no value can be stepped to (an infinite bound) the
declared attribute stands. Limit: a hand-written type that subclasses a known
non-numeric one (a ``StringParamType`` whose ``convert`` returns an int) is classified
by its base class and counted non-numeric.

Where a parameter can be declared between a root and a leaf, and what the walk
(``every_command``: roots, every group at every depth, every leaf, hidden ones
included) does with each; "planted" means a test below goes red without it:

- a root's own options (``--verbose``): seen, planted (``..._on_the_root_group``);
- a group's callback, three ways (``@sub.callback()``, ``add_typer(callback=)``,
  two groups deep): seen, planted;
- a leaf, hidden or not, with a hidden or envvar-fed option: seen, planted (an
  envvar-only option cannot occur: click and Typer always give an option a flag);
- a ``click.Command``/``click.Group`` grafted on without Typer: seen, planted;
- ``typer.Context`` and ``--help`` are not parameters of a user's value.

Ways Typer hands click a number: ``int``/``float``, ``Optional``, ``| None``,
``Annotated``, ``list[...]`` (``multiple``), ``tuple[...]`` (a mixed tuple is
numeric, and bounded only if every numeric member is): seen, planted. ``min=``
and ``max=``: a Range. ``count=True``: excluded, see above. ``parser=``:
click gets a ``FuncParamType`` (locked Typer 0.27.3), so neither
the number nor a range given that way is visible: a stated limit, refused by
``test_every_parameter_type_is_classified_or_listed`` (``UNCLASSIFIED``, empty),
pinned by a control. An ``Enum`` (an int-valued one too) is a ``Choice`` of text:
a closed set, not a range. Any click type outside the known numeric and
non-numeric ones is "unknown" and refused the same way.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Optional

import pytest
import typer

from tests.outcome_walk import root_app

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
    clamps: bool = False

    @property
    def row(self) -> tuple[str, str]:
        return (self.command, self.name)


def _class_names(obj) -> set[str]:
    return {k.__name__ for k in type(obj).__mro__}


#: Click types whose values are not numbers, so no range applies. Anything not here
#: and not INT/FLOAT is "unknown" (see ``unclassified_params``). ``TyperPath`` is Typer's own
#: path type; an int-valued ``Enum`` reaches click as a ``Choice`` of text.
_NOT_NUMERIC = {
    "StringParamType",
    "BoolParamType",
    "Choice",
    "Path",
    "TyperPath",
    "File",
    "DateTime",
    "UUIDParamType",
    "UnprocessedParamType",
}


def _members(click_type) -> list:
    """The leaf types inside a parameter type (a tuple type has several)."""
    members = getattr(click_type, "types", None)
    if members is not None:
        return [m for t in members for m in _members(t)]
    return [click_type]


def _numeric_members(click_type) -> list:
    return [m for m in _members(click_type) if _class_names(m) & {_INT, _FLOAT}]


def _is_unknown(member) -> bool:
    names = _class_names(member)
    return not (names & {_INT, _FLOAT} or names & _NOT_NUMERIC)


def _refuses(click_type, value) -> bool:
    """Does the type's own ``convert`` refuse ``value`` with click's bad-parameter
    error? No command runs: this is the check click makes while parsing."""
    try:
        click_type.convert(value, None, None)
    except Exception as exc:
        if "BadParameter" in _class_names(exc):
            return True
        raise
    return False


def _just_outside(member, bound, direction: int):
    """A value just outside ``bound`` (``direction`` -1 below, +1 above), or None
    when none can be stepped to (an infinite bound)."""
    if getattr(member, "min_open" if direction < 0 else "max_open", False):
        return bound  # an open bound excludes itself
    if "IntRange" in _class_names(member):
        return bound + direction
    stepped = math.nextafter(bound, direction * math.inf)
    return None if stepped == bound else stepped


def _refuses_outside_its_bounds(member) -> bool:
    """Bounded means refused: a declared bound whose just-outside value ``convert``
    refuses. ``clamp=True`` converts it instead, so it is not bounded. Where no value
    can be stepped to (an infinite bound) the declared attribute stands."""
    declared = [
        (b, d)
        for b, d in ((getattr(member, "min", None), -1), (getattr(member, "max", None), 1))
        if b is not None
    ]
    if not declared:
        return False
    for bound, direction in declared:
        probe = _just_outside(member, bound, direction)
        if probe is not None and not _refuses(member, probe):
            return False
    return True


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
            bounded = all(_refuses_outside_its_bounds(m) for m in members)
            clamps = any(getattr(m, "clamp", False) for m in members)
            kind = "float" if _FLOAT in _class_names(members[0]) else "int"
            found.append(NumericParam(path, param.name, kind, bounded, clamps))
    return found


@dataclass(frozen=True)
class Unclassified:
    command: str
    name: str
    type_name: str

    @property
    def row(self) -> tuple[str, str]:
        return (self.command, self.name)


def unclassified_params(commands: Mapping[str, object]) -> list[Unclassified]:
    """Parameters whose click type is neither numeric nor a known non-numeric one,
    so the checker cannot say whether a number is typed there. Typer hands click a
    ``FuncParamType`` for ``parser=`` (locked Typer 0.27.3), and
    a hand-written ``click.ParamType`` is the same: not guessed from the name."""
    found = []
    for path, cmd in commands.items():
        for param in getattr(cmd, "params", ()):
            if getattr(param, "count", False):
                continue
            if any(_is_unknown(m) for m in _members(param.type)):
                found.append(Unclassified(path, param.name, type(param.type).__name__))
    return found


def every_command(roots: Mapping[str, object]) -> dict[str, object]:
    """Every node of the click trees under ``roots`` (name -> click command): the
    roots, every group at every depth, and every leaf. ``outcome_walk.leaves`` yields
    leaves only, which is why a group's own callback options were invisible."""
    found: dict[str, object] = {}

    def walk(cmd, path: tuple[str, ...]) -> None:
        found[" ".join(path)] = cmd
        if hasattr(cmd, "list_commands"):  # Typer vendors click: no isinstance
            ctx = cmd.make_context(path[-1], [], resilient_parsing=True)
            for name in cmd.list_commands(ctx):
                walk(cmd.get_command(ctx, name), (*path, name))

    for prog, root in roots.items():
        walk(root, (prog,))
    return found


def commands_of(app: typer.Typer, prog: str = "app") -> dict[str, object]:
    """The mapping ``registry()`` gives for the real roots, for a small app."""
    return every_command({prog: typer.main.get_command(app)})


def registry() -> dict[str, object]:
    """Every command of the three roots, groups and leaves, with their options."""
    return every_command({prog: typer.main.get_command(root_app(prog)) for prog in ROOTS})


# --------------------------------------------------------------------------
# The lists
# --------------------------------------------------------------------------

#: (command path, parameter name) -> why no range makes sense. A row is added
#: only with a true reason, never to make the test pass. Empty today.
UNBOUNDED: dict[tuple[str, str], str] = {}

#: (command path, parameter name) -> why the checker cannot classify the type (a
#: ``parser=`` option) and why that is safe. One row: `qa duplicates --threshold`.
UNCLASSIFIED: dict[tuple[str, str], str] = {
    ("pyrite qa duplicates", "threshold"): (
        "a click float range accepts NaN; its ``parser=`` function refuses a non-finite or "
        "out-of-range value at parse time (usage error, exit 2); to be replaced by the "
        "shared finite-float type tracked on #1541"
    ),
}

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
NOT_YET_PINNED_SIZE = 30

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


def test_the_walk_sees_the_same_leaves_as_the_other_cli_structure_tests():
    """``registry()`` also holds the groups; its leaves must be exactly the
    commands ``_commands()`` (the walk the other tests share) finds."""
    leaves_here = {name for name, cmd in registry().items() if not hasattr(cmd, "list_commands")}
    assert leaves_here == set(_commands())


def test_every_parameter_type_is_classified_or_listed():
    """A parameter the checker cannot classify (``parser=``, ``click_type=``, a
    custom ``click.ParamType``) may hide a number or a range from it: refused unless
    on UNCLASSIFIED with a reason. Declare the option with a plain Python type
    (``int``, ``float``, ``min=``, ``max=``) instead, see #1541."""
    offenders = [u for u in unclassified_params(registry()) if u.row not in UNCLASSIFIED]
    assert not offenders, "\n".join(
        f"`{u.command}` option/argument `{u.name}` has click type {u.type_name}, which the "
        f"range check cannot classify: use a plain int/float with min= or max=, see #1541"
        for u in offenders
    )


def test_every_numeric_option_declares_a_range_or_is_listed():
    offenders = [
        p
        for p in numeric_params(registry())
        if not p.bounded and p.row not in UNBOUNDED and p.row not in NOT_YET
    ]
    assert not offenders, "\n".join(
        f"`{p.command}` option/argument `{p.name}` ({p.kind}) has a range with clamp=True, "
        "which changes an out-of-range value without a word instead of refusing it: "
        "drop clamp=True, see #1541"
        if p.clamps
        else f"`{p.command}` option/argument `{p.name}` ({p.kind}) declares no min or max: {FIX}"
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
    unclassified = {u.row for u in unclassified_params(registry())}
    for command, name in sorted(UNCLASSIFIED):
        if (command, name) not in unclassified:
            problems.append(
                f"UNCLASSIFIED row `{command}` `{name}` is not an unclassifiable option; delete the row"
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
    for row, reason in {**UNBOUNDED, **UNCLASSIFIED}.items():
        assert isinstance(reason, str) and len(reason.split()) >= 3, (
            f"UNBOUNDED/UNCLASSIFIED row {row} needs a reason that says why no range makes sense, got {reason!r}"
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


# --------------------------------------------------------------------------
# Places a parameter can be declared, and ways Typer hands click a number
# (fix round 1). Planted on a small app; each runs with the one command
#   pytest tests/test_cli_numeric_options_declare_a_range.py -k <name>
# --------------------------------------------------------------------------

import click  # noqa: E402


def _depth_one_callback(depth: int = 1): ...


def test_planted_int_on_a_nested_groups_own_callback_is_reported():
    app = _app()
    sub = typer.Typer()
    sub.callback()(_depth_one_callback)
    sub.command("leaf")(lambda: None)
    sub.command("leaf2")(lambda: None)
    app.add_typer(sub, name="grp")
    assert _report(app) == {("app grp", "depth"): False}


def test_planted_int_on_add_typer_callback_is_reported():
    app = _app()
    sub = typer.Typer()
    sub.command("leaf")(lambda: None)
    sub.command("leaf2")(lambda: None)
    app.add_typer(sub, name="grp", callback=_depth_one_callback)
    assert _report(app) == {("app grp", "depth"): False}


def test_planted_int_two_groups_deep_is_reported():
    app, mid, inner = _app(), typer.Typer(), typer.Typer()
    inner.callback()(_depth_one_callback)
    inner.command("leaf")(lambda: None)
    inner.command("leaf2")(lambda: None)
    mid.add_typer(inner, name="inner")
    mid.command("leaf")(lambda: None)
    app.add_typer(mid, name="mid")
    assert _report(app) == {("app mid inner", "depth"): False}


def test_planted_hidden_command_with_hidden_envvar_option_is_reported():
    """Hidden and envvar-fed options are still typed by a user, and hidden commands
    are still listed by click: both are seen."""
    app = _app()

    @app.command(hidden=True)
    def secret(n: int = typer.Option(1, envvar="SOME_NUMBER", hidden=True)): ...

    assert _report(app) == {("app secret", "n"): False}


def test_planted_click_command_added_without_typer_is_reported():
    """A click.Command (and a click.Group holding one) grafted onto the tree, the way
    a plugin could, is walked: the walk reads the click tree, not Typer's registry."""

    def noop(): ...

    root = typer.main.get_command(_app(noop))  # two commands: a group, not a collapsed command
    grafted = click.Group(
        "grafted", commands={"c": click.Command("c", params=[click.Option(["--n"], type=int)])}
    )
    root.add_command(grafted)
    root.add_command(
        click.Command("bare", params=[click.Option(["--m"], type=click.IntRange(min=0))])
    )
    found = {p.row: p.bounded for p in numeric_params(every_command({"app": root}))}
    assert found == {("app grafted c", "n"): False, ("app bare", "m"): True}


def test_planted_mixed_tuple_is_numeric_and_unbounded_but_a_text_tuple_is_not():
    def mixed(t: tuple[int, str] = (1, "x")): ...
    def text(t: tuple[str, str] = ("a", "b")): ...

    assert _report(_app(mixed, text)) == {("app mixed", "t"): False}


def test_planted_custom_click_type_cannot_be_classified():
    class Odd(click.ParamType):
        name = "odd"

        def convert(self, value, param, ctx):
            return int(value)

    cmd = click.Command("c", params=[click.Option(["--n"], type=Odd())])
    assert [(u.command, u.name) for u in unclassified_params({"app c": cmd})] == [("app c", "n")]


@pytest.mark.parametrize(
    "how", ["parser=int", "click_type=INT", "click_type=IntRange()", "click_type=IntRange(min=0)"]
)
def test_planted_typer_parser_or_click_type_cannot_be_classified(how):
    """With the locked Typer (0.27.3), ``parser=`` and ``click_type=`` both hand click
    a FuncParamType: neither the number nor a range given that way is visible, so
    the shape is refused unless a reviewed row says why (UNCLASSIFIED, empty)."""
    option = {
        "parser=int": typer.Option(1, parser=int),
        "click_type=INT": typer.Option(1, click_type=click.INT),
        "click_type=IntRange()": typer.Option(1, click_type=click.IntRange()),
        "click_type=IntRange(min=0)": typer.Option(1, click_type=click.IntRange(min=0)),
    }[how]

    def command(n: int = option): ...

    commands = commands_of(_app(command))
    assert [(u.command, u.name) for u in unclassified_params(commands)] == [("app command", "n")]


@pytest.mark.control(
    reason="pins what the locked Typer gives for parser=/click_type=, and that a classified type is not flagged"
)
def test_typer_gives_a_func_param_type_for_parser_and_click_type_and_plain_types_are_classified():
    def command(
        a: int = typer.Option(1, parser=int),
        b: int = typer.Option(1, click_type=click.IntRange(min=0)),
        c: int = 1,
        d: str = "x",
        e: bool = False,
        f: float = typer.Option(0.5, min=0.0),
    ): ...

    cmd = commands_of(_app(command))["app command"]
    by_name = {p.name: type(p.type).__name__ for p in cmd.params}
    assert by_name["a"] == by_name["b"] == "FuncParamType", by_name
    assert [u.name for u in unclassified_params({"app command": cmd})] == ["a", "b"]


# --------------------------------------------------------------------------
# Bounded means refused (fix round 2): planted on a small app
# --------------------------------------------------------------------------


def _one_param(**kw):
    def command(limit: int = typer.Option(5, **kw)): ...

    return command


def _params_for(app) -> list[NumericParam]:
    return numeric_params(commands_of(app))


@pytest.mark.parametrize(
    "kw",
    [
        {"min": 1, "clamp": True},
        {"max": 9, "clamp": True},
        {"min": 1, "max": 9, "clamp": True},
    ],
    ids=["min+clamp", "max+clamp", "min+max+clamp"],
)
def test_planted_clamped_int_range_is_not_bounded(kw):
    (param,) = _params_for(_app(_one_param(**kw)))
    assert not param.bounded and param.clamps, param


def test_planted_clamped_float_range_is_not_bounded():
    def command(x: float = typer.Option(0.5, min=0.0, max=1.0, clamp=True)): ...

    (param,) = _params_for(_app(command))
    assert param.kind == "float" and not param.bounded and param.clamps, param


@pytest.mark.control(reason="a plain min= is refused at min-1 and accepted at min")
def test_a_plain_min_is_refused_just_outside_and_accepted_at_the_bound():
    command = _one_param(min=1)
    (param,) = _params_for(_app(command))
    assert param.bounded and not param.clamps
    click_type = next(
        p for p in commands_of(_app(command))["app command"].params if p.name == "limit"
    ).type
    assert click_type.convert(1, None, None) == 1
    with pytest.raises(Exception, match="not in the range") as refused:
        click_type.convert(0, None, None)
    assert "BadParameter" in {k.__name__ for k in type(refused.value).__mro__}


@pytest.mark.control(reason="a plain float range and an open bound are refused outside, so bounded")
def test_a_float_range_and_an_open_bound_are_bounded_and_a_clamped_open_bound_is_not():
    def tenth(x: float = typer.Option(0.5, min=0.0, max=1.0)): ...

    root = typer.main.get_command(_app(tenth))
    root.add_command(
        click.Command(
            "open", params=[click.Option(["--n"], type=click.IntRange(min=0, min_open=True))]
        )
    )
    root.add_command(
        click.Command(
            "openclamp",
            params=[click.Option(["--n"], type=click.IntRange(min=0, min_open=True, clamp=True))],
        )
    )
    found = {p.row: p.bounded for p in numeric_params(every_command({"app": root}))}
    assert found == {
        ("app tenth", "x"): True,
        ("app open", "n"): True,
        ("app openclamp", "n"): False,
    }

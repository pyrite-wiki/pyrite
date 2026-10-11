"""A CLI command returns its outcome; one point renders it and sets the exit code
(ADR-0046, slice 1).

The user's model: "Exit 0 means it did what I asked. If it did part, the output
says which part, in the format I asked for. The format changes how the answer
looks, never what the command does."

The implementation model: a command returns an ``Outcome`` (done, partial,
nothing) or raises a ``PyriteError``. ``PyriteCLIGroup.invoke`` -- the root
group every leaf of ``pyrite``, ``pyrite-admin`` and ``pyrite-read`` runs
inside -- is the only place that prints the result in the requested format and
picks the exit code (docs/json-contracts.md, "Exit codes (CLI)").

The root is also the only way *out* of a command on the contract, and that is
checked when the command runs, not by reading its source (ADR-0046, decisions
6 to 8). "The walk" below is the acceptance test for it: every command of the
three CLIs that declares the shared ``--format`` option, found in the registry,
has each class of way out planted as its body and is run through the real
root. There is no list of commands here; a command that declares the option is
walked.

The first sections enter through a real ``PyriteCLIGroup`` root with a nested
sub-app, the shape ``pyrite task ...`` has, under ``CliRunner``. The task
module's behaviour through the real entry point, in a subprocess, is pinned in
``tests/test_requested_effect_exit_code.py::TestTasks``.
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from pyrite.cli.outcome import EXIT_CODES, Outcome, OutcomeKind
from pyrite.cli.output import LEGACY_FORMAT_SHORT, OUTPUT_FORMAT, declares_shared_format
from pyrite.exceptions import EntryNotFoundError, KBNotFoundError, PyriteError
from pyrite.utils.errors import PARTIAL_EXIT, PyriteCLIGroup, exit_unless_whole
from tests.outcome_walk import (
    ALLOWED,
    MARK,
    ROOTS,
    WAYS_OUT,
    args_for,
    click_command,
    contract_commands,
    planted,
    root_app,
)

REPO = Path(__file__).resolve().parent.parent

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

runner = CliRunner()


def _stdout_identity() -> tuple:
    try:
        on_descriptor_1 = os.fstat(1)[:2]
    except OSError:
        on_descriptor_1 = None
    return sys.stdout, on_descriptor_1


@pytest.fixture(autouse=True)
def stdout_is_given_back():
    """Whatever a command did, the root leaves ``sys.stdout`` and descriptor 1
    as it found them: the watch is the root's, and it ends with the root."""
    before = _stdout_identity()
    yield
    assert _stdout_identity() == before


# --------------------------------------------------------------------------
# The type
# --------------------------------------------------------------------------


def _exit_of_legacy_rule(failed: int, done: int) -> int:
    try:
        exit_unless_whole(failed, done)
    except typer.Exit as e:
        return e.exit_code
    return 0


@given(done=st.integers(min_value=0, max_value=10_000), failed=st.integers(0, 10_000))
def test_from_counts_agrees_with_exit_unless_whole(done, failed):
    """The batch rule moves from a function that exits to a value; the truth
    table is the same for every count."""
    assert Outcome.from_counts(done, failed).exit_code == _exit_of_legacy_rule(failed, done)


def test_each_kind_exits_as_the_contract_says():
    """docs/json-contracts.md, "Exit codes (CLI)": 0 done, 3 part, 1 none."""
    assert Outcome.done({}).exit_code == 0
    assert Outcome.partial({}).exit_code == PARTIAL_EXIT == 3
    assert Outcome.nothing({}).exit_code == 1
    assert {k.value for k in OutcomeKind} == {"done", "partial", "nothing"}


@given(
    data=st.dictionaries(
        st.text(min_size=1) | st.just("outcome"),
        st.one_of(st.integers(), st.text(), st.booleans(), st.none()),
    ),
    kind=st.sampled_from(list(OutcomeKind)),
)
def test_the_payload_adds_outcome_and_changes_nothing_else(data, kind):
    """The JSON `outcome` key is additive (maintainer, 2026-10-08): every key the
    command returned is there, unchanged. The one input where that cannot be
    true, data with an `outcome` key of its own, is refused when the Outcome is
    built, not overwritten."""
    if "outcome" in data:
        with pytest.raises(TypeError, match="outcome"):
            Outcome(kind, data)
        return
    payload = Outcome(kind, data).payload()
    assert payload.pop("outcome") == kind.value
    assert payload == data


@pytest.mark.parametrize("data", [[1, 2], "text", 3, ({"n": 1},)])
def test_data_that_cannot_carry_the_outcome_key_is_refused(data):
    """A list has nowhere to put `outcome`, so a partial list would print as a
    whole one: an Outcome's data is a mapping or nothing."""
    with pytest.raises(TypeError, match="mapping"):
        Outcome.partial(data)


def test_a_kind_is_one_of_the_three():
    with pytest.raises(TypeError, match="OutcomeKind"):
        Outcome("done", {})  # type: ignore[arg-type]


def test_the_exit_code_table_cannot_be_written_to():
    with pytest.raises(TypeError):
        EXIT_CODES[OutcomeKind.NOTHING] = 0  # type: ignore[index]


# --------------------------------------------------------------------------
# The one point: a root group with a nested sub-app, as `pyrite task` is
# --------------------------------------------------------------------------

RENDERED: list[dict] = []


def _rich(console, data):
    RENDERED.append(data)
    console.print(f"rich says {data['n']}")


def _app(result=None, raises: BaseException | None = None, body=None, sub_kwargs=None):
    """`root sub leaf`, `root sub inner deep`, `root legacy` and `root plugin`.

    `leaf` and `deep` are on the contract (they declare the shared option);
    `legacy` and `plugin` are not. `body`, when given, is `leaf`'s body.
    """
    app = typer.Typer(cls=PyriteCLIGroup)
    sub = typer.Typer(**(sub_kwargs or {}))
    inner = typer.Typer()

    @sub.command("leaf")
    def leaf(
        output_format: str = OUTPUT_FORMAT, legacy_format: str | None = LEGACY_FORMAT_SHORT
    ) -> Outcome:
        if body is not None:
            return body()
        if raises is not None:
            raise raises
        return result

    @inner.command("deep")
    def deep(output_format: str = OUTPUT_FORMAT) -> Outcome:
        return result

    @app.command("legacy")
    def legacy():
        if body is not None:
            return body()
        if raises is not None:
            raise raises
        typer.echo("legacy printed itself")

    @app.command("plugin")
    def plugin():
        """Decision 5: no shared option, but it returns an Outcome."""
        return result

    sub.add_typer(inner, name="inner")
    app.add_typer(sub, name="sub")
    return app


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (Outcome.done({"n": 1}), 0),
        (Outcome.partial({"n": 1}), 3),
        (Outcome.nothing({"n": 1}), 1),
        (Outcome.from_counts(2, 1, {"n": 1}), 3),
        (Outcome.from_counts(0, 2, {"n": 1}), 1),
    ],
)
def test_a_returned_outcome_sets_the_exit_code_through_nested_sub_apps(outcome, code):
    """The groom's riskiest assumption: a leaf's return value reaches the root
    group's `invoke` under CliRunner, through one and two levels of sub-app."""
    for args in (["sub", "leaf"], ["sub", "inner", "deep"]):
        out = runner.invoke(_app(outcome), args)
        assert out.exit_code == code, (args, out.output)
        assert json.loads(out.stdout) == {"n": 1, "outcome": outcome.kind.value}


def test_json_is_the_default_format():
    out = runner.invoke(_app(Outcome.done({"n": 1})), ["sub", "leaf"])
    assert json.loads(out.stdout) == {"n": 1, "outcome": "done"}


def test_rich_runs_the_renderer_and_prints_no_json():
    RENDERED.clear()
    out = runner.invoke(
        _app(Outcome.partial({"n": 7}, rich=_rich)), ["sub", "leaf", "--format", "rich"]
    )
    assert out.exit_code == 3, out.output
    assert out.stdout.strip() == "rich says 7"
    assert RENDERED == [{"n": 7}]


def test_rich_without_a_renderer_prints_each_field():
    out = runner.invoke(
        _app(Outcome.done({"title": "T", "n": 2})), ["sub", "leaf", "--format", "rich"]
    )
    assert out.exit_code == 0, out.output
    assert "title: T" in out.stdout and "n: 2" in out.stdout


def test_pyrite_format_sets_the_default_and_format_overrides_it():
    app = _app(Outcome.done({"n": 3}, rich=_rich))
    assert (
        "rich says 3" in runner.invoke(app, ["sub", "leaf"], env={"PYRITE_FORMAT": "rich"}).stdout
    )
    out = runner.invoke(app, ["sub", "leaf", "--format", "json"], env={"PYRITE_FORMAT": "rich"})
    assert json.loads(out.stdout)["n"] == 3


def test_another_registered_format_is_rendered_by_the_registry():
    out = runner.invoke(_app(Outcome.done({"n": 4})), ["sub", "leaf", "--format", "yaml"])
    assert out.exit_code == 0, out.output
    assert "n: 4" in out.stdout and "outcome: done" in out.stdout


@pytest.mark.parametrize("fmt", ["xml", "", "csv", "markdown"])
def test_an_unknown_format_is_a_usage_error(fmt):
    """`csv` and `markdown` are registered for the shapes REST serves; for any
    other payload they print a Python repr, so the shared option refuses them
    instead of advertising them."""
    out = runner.invoke(_app(Outcome.done({"n": 1})), ["sub", "leaf", "--format", fmt])
    assert out.exit_code == 2, out.output


def test_short_f_still_works_for_one_release_and_says_so_on_stderr():
    """#303: `-f` stops meaning `--format`, deprecated with a warning for one release."""
    out = runner.invoke(_app(Outcome.done({"n": 5}, rich=_rich)), ["sub", "leaf", "-f", "rich"])
    assert out.exit_code == 0, out.output
    assert out.stdout.strip() == "rich says 5"
    assert "deprecated" in out.stderr and "--format" in out.stderr


def test_a_raised_refusal_is_rendered_in_the_requested_format():
    err = KBNotFoundError("KB not found: nope")
    out = runner.invoke(_app(raises=err), ["sub", "leaf"])
    assert out.exit_code == 1, out.output
    assert json.loads(out.stdout) == {
        "error": "KB not found: nope",
        "error_code": "KB_NOT_FOUND",
        "retryable": False,
        "outcome": "error",
    }
    rich = runner.invoke(_app(raises=err), ["sub", "leaf", "--format", "rich"])
    assert rich.exit_code == 1
    assert "ERROR [KB_NOT_FOUND]: KB not found: nope" in rich.stdout


def test_a_missing_entry_keeps_the_cli_spelling_not_found():
    """json-contracts' CLI reads answer NOT_FOUND; #610 owns the spelling."""
    out = runner.invoke(_app(raises=EntryNotFoundError("Task 'x' not found")), ["sub", "leaf"])
    assert out.exit_code == 1
    assert json.loads(out.stdout)["error_code"] == "NOT_FOUND"


def test_a_value_error_is_an_error_line_not_a_traceback():
    """Every command catches (PyriteError, ValueError) today and prints code ERROR."""
    out = runner.invoke(_app(raises=ValueError("bad value")), ["sub", "leaf"])
    assert out.exit_code == 1
    assert json.loads(out.stdout)["error_code"] == "ERROR"


@pytest.mark.control(reason="no behaviour change for a legacy command (slice 1 scope)")
def test_a_legacy_command_keeps_its_own_output_and_exceptions():
    out = runner.invoke(_app(), ["legacy"])
    assert (out.exit_code, out.stdout) == (0, "legacy printed itself\n")
    raised = runner.invoke(_app(raises=PyriteError("boom")), ["legacy"])
    assert isinstance(raised.exception, PyriteError)


def test_pyrite_read_runs_inside_the_same_root_group():
    from pyrite import read_cli

    assert isinstance(typer.main.get_command(read_cli.app), PyriteCLIGroup)


def test_a_migrated_command_that_returns_nothing_is_an_internal_error():
    """A command that declared the shared option and returned None would exit 0
    with nothing on stdout: a success that said nothing (#793 cold read)."""
    out = runner.invoke(_app(None), ["sub", "leaf"])
    assert out.exit_code == 1, out.output
    body = json.loads(out.stdout)
    assert (body["error_code"], body["outcome"]) == ("NO_OUTCOME", "error")
    rich = runner.invoke(_app(None), ["sub", "leaf", "--format", "rich"])
    assert rich.exit_code == 1 and "NO_OUTCOME" in rich.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["sub", "leaf", "-f", "rich", "--format", "json"],
        ["sub", "leaf", "--format", "json", "-f", "rich"],
    ],
)
def test_short_f_together_with_format_is_a_usage_error(args):
    """Two answers to one question: refuse rather than let one silently win."""
    out = runner.invoke(_app(Outcome.done({"n": 1})), args)
    assert out.exit_code == 2, out.output


@pytest.mark.control(reason="PYRITE_FORMAT is a default, not a second answer")
def test_short_f_with_pyrite_format_set_is_not_a_conflict():
    out = runner.invoke(
        _app(Outcome.done({"n": 1})), ["sub", "leaf", "-f", "json"], env={"PYRITE_FORMAT": "rich"}
    )
    assert out.exit_code == 0, out.output
    assert json.loads(out.stdout)["n"] == 1


def test_a_value_error_leaves_its_traceback_in_the_debug_log(caplog):
    """Transitional (ADR-0046): rendered as ERROR, but the cause is not lost."""
    import logging

    with caplog.at_level(logging.DEBUG, logger="pyrite.utils.errors"):
        runner.invoke(_app(raises=ValueError("bad value")), ["sub", "leaf"])
    records = [r for r in caplog.records if r.exc_info and "bad value" in str(r.exc_info[1])]
    assert records, caplog.records


def test_a_refusal_is_printed_in_the_format_asked_for_yaml_too():
    """Not a JSON error under `--format yaml`: one document, in that format."""
    import yaml

    out = runner.invoke(_app(raises=KBNotFoundError("KB not found: nope")), _LEAF + ["yaml"])
    assert out.exit_code == 1, out.output
    assert not out.stdout.lstrip().startswith("{")
    assert yaml.safe_load(out.stdout) == {
        "outcome": "error",
        "error": "KB not found: nope",
        "error_code": "KB_NOT_FOUND",
        "retryable": False,
    }


# --------------------------------------------------------------------------
# The root is the only way out: what one toy root shows that the walk cannot
# --------------------------------------------------------------------------

_LEAF = ["sub", "leaf", "--format"]


def _no_outcome(out) -> dict:
    assert out.exit_code == 1, out.output
    doc = json.loads(out.stdout)  # the whole of stdout is one document
    assert (doc["error_code"], doc["outcome"]) == ("NO_OUTCOME", "error"), doc
    return doc


def test_the_root_takes_only_the_outcome_the_body_returned():
    """A group's result callback sits between the command and the root. One
    that turns `nothing` into `done` would change the exit code after the
    command had said what happened."""
    swap = {"result_callback": lambda *args, **kwargs: Outcome.done({"swapped": True})}
    out = runner.invoke(_app(Outcome.nothing({"n": 1}), sub_kwargs=swap), _LEAF + ["json"])
    assert "swapped" not in out.stdout
    _no_outcome(out)


def test_a_refusal_that_cannot_be_rendered_is_still_one_answer():
    class HostileError(PyriteError):
        @property
        def public_message(self):
            raise KeyError("no message")

    out = runner.invoke(_app(raises=HostileError("x")), _LEAF + ["json"])
    _no_outcome(out)


def test_a_value_error_from_the_root_s_own_rendering_is_not_the_command_s_refusal():
    """`ValueError` is allowed out of the *body* (transitional). The same type
    raised while the root serialises the payload is not a refusal the command
    made: it is NO_OUTCOME, not code ERROR."""
    loop: dict = {}
    loop["loop"] = loop
    out = runner.invoke(_app(Outcome.done(loop)), _LEAF + ["json"])
    _no_outcome(out)


def test_what_the_body_wrote_is_moved_to_stderr_not_lost():
    def body():
        print(MARK)
        return Outcome.done({"n": 1})

    out = runner.invoke(_app(body=body), _LEAF + ["json"])
    doc = _no_outcome(out)
    assert "stdout" in doc["error"]
    assert MARK in out.stderr and MARK not in out.stdout


def test_an_unexpected_exception_is_named_and_its_traceback_is_in_the_debug_log(caplog):
    import logging

    with caplog.at_level(logging.DEBUG, logger="pyrite.utils.errors"):
        out = runner.invoke(_app(raises=KeyError("boom")), _LEAF + ["json"])
    assert "KeyError" in _no_outcome(out)["error"]
    assert [r for r in caplog.records if r.exc_info and isinstance(r.exc_info[1], KeyError)]


def test_the_watch_starts_once_when_the_body_is_entered_again():
    """`ctx.invoke` of the running command goes through the same wrapper. The
    second entry must not start a second watch over the first: the stream it
    would save as "the real stdout" is the first watch's."""
    from tests.outcome_walk import _current_context

    calls = []

    def body():
        calls.append(1)
        if len(calls) == 1:
            ctx = _current_context()
            return ctx.invoke(ctx.command.callback, **ctx.params)
        print(MARK)
        return Outcome.done({"n": 1})

    out = runner.invoke(_app(body=body), _LEAF + ["json"])
    assert len(calls) == 2
    _no_outcome(out)
    assert MARK in out.stderr
    # ...and with a body that re-enters and behaves, the outcome is the root's to render.
    calls.clear()

    def quiet():
        calls.append(1)
        if len(calls) == 1:
            ctx = _current_context()
            return ctx.invoke(ctx.command.callback, **ctx.params)
        return Outcome.partial({"n": 2})

    again = runner.invoke(_app(body=quiet), _LEAF + ["json"])
    assert again.exit_code == 3, again.output
    assert json.loads(again.stdout) == {"n": 2, "outcome": "partial"}


def test_a_body_that_runs_the_app_again_printed_through_the_inner_root():
    """A nested root renders into the outer watch, which is the outer body
    writing its own result: NO_OUTCOME. Each root gives back what it found."""
    holder = {}

    def body():
        holder["app"](["sub", "inner", "deep"], standalone_mode=False)
        return Outcome.done({"outer": True})

    holder["app"] = _app(Outcome.done({"inner": True}), body=body)
    out = runner.invoke(holder["app"], _LEAF + ["json"])
    _no_outcome(out)
    assert '"inner": true' in out.stderr


def test_a_root_group_mounted_under_another_answers_once():
    """Both are PyriteCLIGroup, so both `invoke`s run. The outer one decides;
    the inner one must not render the outcome a second time."""
    app = typer.Typer(cls=PyriteCLIGroup)
    sub = typer.Typer(cls=PyriteCLIGroup)

    @sub.command("leaf")
    def leaf(output_format: str = OUTPUT_FORMAT) -> Outcome:
        return Outcome.done({"n": 1})

    @sub.command("printing")
    def printing(output_format: str = OUTPUT_FORMAT) -> Outcome:
        print(MARK)
        return Outcome.done({})

    app.add_typer(sub, name="sub")
    out = runner.invoke(app, ["sub", "leaf"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.stdout) == {"n": 1, "outcome": "done"}  # one document
    _no_outcome(runner.invoke(app, ["sub", "printing"]))


def test_the_guard_watches_only_a_command_that_declares_the_shared_option():
    """`declares_shared_format` is the one definition of "on the contract": the
    guard asks it, whoever calls the guard."""
    from types import SimpleNamespace

    from pyrite.utils.errors import _BodyGuard

    tree = typer.main.get_command(_app())
    for name, watched in (("legacy", False), ("sub", None)):
        command = tree.commands[name] if watched is False else tree.commands[name].commands["leaf"]
        before = command.callback
        _BodyGuard().watch_body_of(SimpleNamespace(command=command, meta={}))
        assert (command.callback is not before) is (watched is None), name


@pytest.mark.control(reason="decision 5: a plugin's Outcome was rendered before the guard too")
@pytest.mark.parametrize(("outcome", "code"), [(Outcome.done({"n": 1}), 0), (Outcome.partial(), 3)])
def test_a_command_off_the_contract_that_returns_an_outcome_is_rendered(outcome, code):
    out = runner.invoke(_app(outcome), ["plugin"])
    assert out.exit_code == code, out.output
    assert json.loads(out.stdout)["outcome"] == outcome.kind.value


def test_only_a_command_on_the_contract_is_watched():
    """Stdout is held back from body entry for a command that declares the
    shared option, and never for one that does not: a legacy command prompts,
    streams and shows progress as before."""
    seen = {}

    def body():
        seen["stdout"], seen["descriptor 1"] = _stdout_identity()
        seen["under the runner"] = sys.stdout
        return Outcome.done({})

    outside = _stdout_identity()[1]
    runner.invoke(_app(body=body), ["legacy"])
    assert seen["descriptor 1"] == outside
    legacy_stream = seen["under the runner"]
    out = runner.invoke(_app(body=body), _LEAF + ["json"])
    assert out.exit_code == 0, out.output
    assert seen["descriptor 1"] != outside
    assert type(seen["under the runner"]) is not type(legacy_stream)


# --------------------------------------------------------------------------
# The walk: every command on the contract, every class of way out, the real root
# --------------------------------------------------------------------------

CONTRACT = [(root, path) for root in ROOTS for path in contract_commands(root)]


def _id(case) -> str:
    return " ".join(str(part) for part in case) if isinstance(case, tuple) else str(case)


@functools.cache
def _args(root: str, path: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(args_for(click_command(root_app(root), path)))


def _run(root, path, body, fmt, args=None):
    app = root_app(root)
    args = _args(root, path) if args is None else args
    with planted(app, path, body):
        return runner.invoke(app, [*path, *args, "--format", fmt])


def test_the_walk_is_not_empty_and_is_the_registry_s_own_list():
    """An empty parametrisation is silently green. `task` is on the contract,
    so the walk has commands; and it finds them the way the root does."""
    assert ("pyrite", ("task", "claim")) in CONTRACT
    for root, path in CONTRACT:
        assert declares_shared_format(click_command(root_app(root), path))


WALK = [
    (root, path, name, fmt)
    for root, path in CONTRACT
    for name, shape in sorted(WAYS_OUT.items())
    for fmt in shape.formats
    if not shape.real_process_only
]


@pytest.mark.parametrize(
    ("root", "path", "shape", "fmt"), WALK, ids=[_id((r, *p, s, f)) for r, p, s, f in WALK]
)
def test_the_root_is_the_only_way_out_of_a_command_on_the_contract(root, path, shape, fmt):
    """ADR-0046, decision 6. Whatever the body does that is not a returned
    Outcome or a raised refusal, the answer is NO_OUTCOME, exit 1, in the
    format asked for, and nothing the body wrote is on stdout."""
    way_out = WAYS_OUT[shape]
    if not way_out.needs():
        pytest.skip("not available on this platform")
    out = _run(root, path, way_out.body, fmt)
    assert out.exit_code == 1, (out.exit_code, out.stdout, out.stderr)
    if fmt == "json":
        doc = json.loads(out.stdout)  # one document and nothing else
        assert (doc["error_code"], doc["outcome"]) == ("NO_OUTCOME", "error"), doc
    else:
        assert "ERROR [NO_OUTCOME]" in out.stdout
    assert MARK not in out.stdout
    if way_out.writes:
        assert MARK in out.stderr


CONTROLS = [(root, path, name) for root, path in CONTRACT for name in sorted(ALLOWED)]


@pytest.mark.control(
    reason="the allow-list: what the root rendered before the guard, it still renders"
)
@pytest.mark.parametrize(
    ("root", "path", "allowed"), CONTROLS, ids=[_id((r, *p, a)) for r, p, a in CONTROLS]
)
def test_what_the_root_allows_out_of_a_command_on_the_contract(root, path, allowed):
    body, code = ALLOWED[allowed]
    out = _run(root, path, body, "json")
    assert out.exit_code == code, (out.exit_code, out.stdout, out.stderr)
    if allowed == "interrupted":
        assert out.stdout == ""
        return
    doc = json.loads(out.stdout)
    expected = {
        "returns_done": ("done", None),
        "returns_partial": ("partial", None),
        "returns_nothing": ("nothing", None),
        "refuses": ("error", "KB_NOT_FOUND"),
        "raises_value_error": ("error", "ERROR"),
        "warns_on_stderr_then_returns": ("done", None),
        "switches_the_format_then_returns": ("done", None),
        "renderer_raises": ("done", None),
    }[allowed]
    assert (doc["outcome"], doc.get("error_code")) == expected
    if allowed == "switches_the_format_then_returns":
        assert MARK not in out.stdout + out.stderr  # its renderer never ran
    if allowed == "warns_on_stderr_then_returns":
        assert MARK in out.stderr and MARK not in out.stdout


@pytest.mark.control(reason="before the body is entered click is parsing, and nothing changes")
@pytest.mark.parametrize(("root", "path"), CONTRACT, ids=[_id((r, *p)) for r, p in CONTRACT])
def test_before_the_body_is_entered_nothing_changes(root, path):
    """`--help` exits 0 and usage errors exit 2, with a body planted that would
    be NO_OUTCOME (exit 1) if it ran."""
    crash = WAYS_OUT["crash"].body
    app, args = root_app(root), _args(root, path)
    with planted(app, path, crash):
        helped = runner.invoke(app, [*path, "--help"])
        assert helped.exit_code == 0 and "Usage" in helped.stdout, helped.output
        # After `--format`, whose callback has already run: still click's.
        late = runner.invoke(app, [*path, "--format", "json", *args, "--no-such-option"])
        assert late.exit_code == 2, late.output
        for unknown in ("xml", "csv"):
            assert runner.invoke(app, [*path, *args, "--format", unknown]).exit_code == 2
        assert runner.invoke(app, [*path, *args, "-f", "rich", "--format", "json"]).exit_code == 2
        if args:
            missing = runner.invoke(app, [*path, "--format", "json"])
            assert missing.exit_code == 2, missing.output


@pytest.mark.control(reason="before any command: the root's own options are click's")
@pytest.mark.parametrize("root", sorted(ROOTS))
def test_the_root_s_help_and_version_are_untouched(root):
    app = root_app(root)
    assert runner.invoke(app, ["--help"]).exit_code == 0
    if any("--version" in p.opts for p in typer.main.get_command(app).params):
        out = runner.invoke(app, ["--version"])
        assert out.exit_code == 0 and out.stdout.strip(), out.output


@pytest.mark.parametrize("root", sorted(ROOTS))
@pytest.mark.parametrize("shape", ["exit_zero", "writes_descriptor_1", "returns_none"])
def test_each_of_the_three_roots_is_the_way_out(root, shape):
    """`pyrite-admin` and `pyrite-read` have no command on the contract yet, so
    the walk has nothing of theirs. A command registered on each root for the
    length of this test shows the guard is the root's, not `pyrite`'s."""
    app = root_app(root)

    def probe(output_format: str = OUTPUT_FORMAT) -> Outcome:
        return Outcome.done({})

    app.command("zz-outcome-probe")(probe)
    try:
        assert ("zz-outcome-probe",) in contract_commands(root)
        out = _run(root, ("zz-outcome-probe",), WAYS_OUT[shape].body, "json")
    finally:
        app.registered_commands.pop()
    assert out.exit_code == 1, out.output
    assert json.loads(out.stdout)["error_code"] == "NO_OUTCOME"
    assert MARK not in out.stdout


# --------------------------------------------------------------------------
# A real process: descriptor 1 is the process's, and so are the streams a
# module took at import
# --------------------------------------------------------------------------

REAL = [(root, paths[0]) for root in sorted(ROOTS) if (paths := contract_commands(root))]
REAL_IDS = [_id((r, *p)) for r, p in REAL]


def _real(tmp_path, root, path, shape, *, fmt="json", closed=None, say_first=None):
    command = [
        sys.executable,
        "-m",
        "tests.outcome_walk",
        *(["--say-first", say_first] if say_first else []),
        root,
        shape,
        *path,
        "--",
        *_args(root, path),
        "--format",
        fmt,
    ]
    if closed:  # ">&-" closes descriptor 1, "2>&-" descriptor 2
        command = ["/bin/sh", "-c", f'exec "$@" {closed}', "sh", *command]
    (tmp_path / "home").mkdir(exist_ok=True)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=120,
        env={
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "PYRITE_CONFIG_DIR": str(tmp_path / "cfg"),
            "NO_COLOR": "1",
        },
    )


@pytest.mark.cli
@pytest.mark.parametrize(("root", "path"), REAL, ids=REAL_IDS)
@pytest.mark.parametrize(
    "shape",
    [
        "prints",
        "writes_descriptor_1",
        "child_process_prints",
        "console_kept_from_import_prints",
        "stream_kept_from_import_unflushed",
        "logs_to_a_handler_on_the_stream_kept_from_import",
        "c_stdio_prints_unflushed",
        "exit_zero",
    ],
)
def test_in_a_real_process_the_root_is_the_only_way_out(tmp_path, root, path, shape):
    """Under pytest a stream taken at import is pytest's, and descriptor 1 is
    its capture file; here both are the process's stdout, a pipe. A write that
    is still in a buffer when the body returns must not land after the root's
    document."""
    if not WAYS_OUT[shape].needs():
        pytest.skip("not available on this platform")
    out = _real(tmp_path, root, path, shape)
    assert out.returncode == 1, (out.returncode, out.stdout, out.stderr)
    assert json.loads(out.stdout)["error_code"] == "NO_OUTCOME"
    assert MARK not in out.stdout
    if WAYS_OUT[shape].writes:
        assert MARK in out.stderr


@pytest.mark.cli
@pytest.mark.control(reason="the allow-list in a real process: rendered before the guard too")
@pytest.mark.parametrize(("root", "path"), REAL, ids=REAL_IDS)
def test_in_a_real_process_an_outcome_is_rendered_and_ctrl_c_is_130(tmp_path, root, path):
    out = _real(tmp_path, root, path, "returns_partial")
    assert out.returncode == 3, out.stdout + out.stderr
    assert json.loads(out.stdout) == {"n": 1, "outcome": "partial"}
    assert _real(tmp_path, root, path, "interrupted").returncode == 130


@pytest.mark.cli
@pytest.mark.parametrize(("root", "path"), REAL, ids=REAL_IDS)
def test_text_buffered_before_the_body_is_not_the_body_s(tmp_path, root, path):
    """Into a pipe, text written before the command ran is still in the
    stream's buffer when the body starts. The root flushes it to the real
    stdout first; otherwise the watch would take it for the body's and answer
    NO_OUTCOME for a command that did nothing wrong. (Under CliRunner the same
    holds for `sys.__stdout__`, which is not the stream the runner installed.)"""
    out = _real(tmp_path, root, path, "returns_partial", say_first="said before\n")
    assert out.returncode == 3, (out.returncode, out.stdout, out.stderr)
    first, _, document = out.stdout.partition("\n")
    assert first == "said before"
    assert json.loads(document) == {"n": 1, "outcome": "partial"}


@pytest.mark.cli
@pytest.mark.skipif(sys.platform == "win32", reason="closes descriptor 1 through /bin/sh")
@pytest.mark.parametrize(("root", "path"), REAL, ids=REAL_IDS)
def test_with_stdout_closed_the_exit_code_is_still_the_root_s(tmp_path, root, path):
    """`>&-`: descriptor 1 cannot be duplicated, so only `sys.stdout` is
    watched. The outcome still sets the exit code, and a body that prints is
    still NO_OUTCOME, with what it wrote on stderr."""
    assert _real(tmp_path, root, path, "returns_partial", closed=">&-").returncode == 3
    printed = _real(tmp_path, root, path, "prints", closed=">&-")
    assert printed.returncode == 1, printed.stderr
    assert MARK in printed.stderr


@pytest.mark.cli
@pytest.mark.skipif(sys.platform == "win32", reason="closes descriptor 2 through /bin/sh")
@pytest.mark.parametrize(("root", "path"), REAL, ids=REAL_IDS)
def test_with_stderr_closed_what_the_body_wrote_still_makes_it_no_outcome(tmp_path, root, path):
    """`2>&-`: there is nowhere to move what the body wrote. The answer is
    still the root's, not a traceback from trying."""
    out = _real(tmp_path, root, path, "prints", closed="2>&-")
    assert out.returncode == 1, out.stdout
    assert json.loads(out.stdout)["error_code"] == "NO_OUTCOME"

"""The five reproduced slice-1 boundaries in #838, through actual roots."""

import copy
import json

import pytest
import typer
from typer.testing import CliRunner

from pyrite.cli.outcome import Outcome
from pyrite.cli.output import OUTPUT_FORMAT
from pyrite.exceptions import KBNotFoundError
from pyrite.utils.errors import PyriteCLIGroup
from tests.cli_help import plain
from tests.outcome_walk import (
    ROOTS,
    _current_context,
    _registration,
    args_for,
    click_command,
    contract_commands,
    planted,
    root_app,
)

runner = CliRunner()
CONTRACT = [(root, path) for root in ROOTS for path in contract_commands(root)]


def _root_close_prints():
    _current_context().find_root().call_on_close(lambda: print("ROOT-CLOSE-MARK"))
    return Outcome.nothing({"why": "none"})


def _root_close_exits():
    def close():
        raise typer.Exit(0)

    _current_context().find_root().call_on_close(close)
    return Outcome.nothing({"why": "none"})


@pytest.mark.parametrize(("root", "path"), CONTRACT)
@pytest.mark.parametrize("body", [_root_close_prints, _root_close_exits])
def test_root_cleanup_cannot_replace_the_document_or_exit(root, path, body):
    app = root_app(root)
    with planted(app, path, body):
        result = runner.invoke(
            app, [*path, *args_for(click_command(app, path)), "--format", "json"]
        )
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["error_code"] == "NO_OUTCOME"
    if body is _root_close_prints:
        assert "ROOT-CLOSE-MARK" in result.stderr


def _app(cls=PyriteCLIGroup):
    app = typer.Typer(cls=cls)

    @app.callback()
    def root():
        pass

    @app.command()
    def probe(output_format: str = OUTPUT_FORMAT):
        print("BODY-MARK")
        raise KBNotFoundError("the original refusal")

    return app


@pytest.mark.parametrize(("root", "path"), CONTRACT)
def test_a_stdout_violation_keeps_the_original_refusal_reason(root, path):
    def refuses_after_print():
        print("BODY-MARK")
        raise KBNotFoundError("the original refusal")

    app = root_app(root)
    with planted(app, path, refuses_after_print):
        result = runner.invoke(
            app, [*path, *args_for(click_command(app, path)), "--format", "json"]
        )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error_code"] == "NO_OUTCOME"
    assert "BODY-MARK" in result.stderr
    assert "the original refusal" in result.stderr


@pytest.mark.parametrize(("root", "path"), CONTRACT)
def test_shared_option_without_a_pyrite_root_refuses_before_the_body(root, path):
    called = []

    def body():
        called.append(True)
        return Outcome.done({"effect": "must not run"})

    app = root_app(root)
    with planted(app, path, body):
        registration = copy.copy(_registration(app, path))
        registration.name = "probe"
        detached = typer.Typer()

        @detached.callback()
        def root_callback():
            pass

        detached.registered_commands.append(registration)
        args = args_for(click_command(app, path))
        result = runner.invoke(detached, ["probe", *args, "--format", "json"])
    assert result.exit_code == 2, result.output
    assert called == []
    assert "PyriteCLIGroup" in plain(result.stderr)


@pytest.mark.control(
    reason="legacy plugin format parameters are explicitly outside the shared contract"
)
def test_a_legacy_outcome_uses_the_root_default_not_a_private_format_option():
    app = typer.Typer(cls=PyriteCLIGroup)

    @app.callback()
    def root():
        pass

    @app.command()
    def plugin(format: str = typer.Option("rich", "--format")):
        return Outcome.done({"value": 1})

    result = runner.invoke(app, ["plugin", "--format", "yaml"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["outcome"] == "done"


@pytest.mark.control(
    reason="shared option and legacy fallback intentionally validate different formats"
)
def test_unsupported_environment_format_has_named_shared_and_legacy_limits():
    shared = runner.invoke(_app(), ["probe"], env={"PYRITE_FORMAT": "markdown"})
    assert shared.exit_code == 2
    app = typer.Typer(cls=PyriteCLIGroup)

    @app.callback()
    def root():
        pass

    @app.command()
    def plugin():
        return Outcome.done()

    legacy = runner.invoke(app, ["plugin"], env={"PYRITE_FORMAT": "markdown"})
    assert legacy.exit_code == 0
    assert json.loads(legacy.stdout)["outcome"] == "done"


def test_keyboard_interrupt_in_body_is_not_overwritten_by_cleanup_failure():
    app = typer.Typer(cls=PyriteCLIGroup)

    @app.callback()
    def root():
        pass

    @app.command()
    def probe(output_format: str = OUTPUT_FORMAT):
        _current_context().find_root().call_on_close(lambda: 1 / 0)
        raise KeyboardInterrupt()

    # Typer's real command entry point turns Ctrl-C into exit 130; CliRunner
    # does not propagate KeyboardInterrupt even with catch_exceptions=False.
    result = runner.invoke(app, ["probe"], catch_exceptions=False)
    assert result.exit_code == 130
    assert result.stdout == ""

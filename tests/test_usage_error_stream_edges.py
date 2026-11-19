"""Actual Typer commands exercise binary output, parsed formats and restoration."""

import json
import os
import subprocess
import sys

import pytest
import typer
from typer.testing import CliRunner

from pyrite.utils.errors import PyriteCLIGroup


def _app():
    app = typer.Typer(cls=PyriteCLIGroup)

    @app.callback()
    def root():
        pass

    return app


@pytest.mark.parametrize("write", ["buffer", "bytes", "rewrap"])
def test_binary_text_before_noncontract_error_has_no_appended_document(write):
    app = _app()

    @app.command()
    def emit():
        if write == "buffer":
            sys.stdout.buffer.write(b"before\n")
        elif write == "bytes":
            typer.echo(b"before")
        else:
            import io

            wrapper = io.TextIOWrapper(sys.stdout.buffer, encoding="ascii", write_through=True)
            try:
                wrapper.write("before\n")
            finally:
                wrapper.detach()
        raise typer.BadParameter("bad body")

    result = CliRunner().invoke(app, ["emit"])
    assert result.exit_code == 2
    assert result.stdout == "before\n"
    assert "bad body" in result.stderr


def test_field_short_option_is_not_misread_as_yaml_format():
    app = _app()

    @app.command()
    def emit(ctx: typer.Context, field: str = typer.Option("", "-f")):
        raise typer.BadParameter("bad body", ctx=ctx)

    result = CliRunner().invoke(app, ["emit", "-f", "yaml"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error_code"] == "USAGE_ERROR"


@pytest.mark.parametrize("interrupt", [False, True])
def test_root_restores_stdout_after_error_or_interrupt(interrupt):
    code = """
import sys, typer
from pyrite.utils.errors import PyriteCLIGroup
app = typer.Typer(cls=PyriteCLIGroup)
@app.callback()
def root(): pass
@app.command()
def emit():
    if INTERRUPT: raise KeyboardInterrupt()
    raise typer.BadParameter('bad body')
original = sys.stdout
try:
    app(['emit'], standalone_mode=True)
except (SystemExit, KeyboardInterrupt):
    pass
assert sys.stdout is original
print('restored')
""".replace("INTERRUPT", repr(interrupt))
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("restored\n")


@pytest.mark.control(reason="The Outcome contract keeps body usage errors as implementation bugs")
def test_contract_text_then_usage_error_is_one_no_outcome_document():
    from pyrite.cli.output import OUTPUT_FORMAT

    app = _app()

    @app.command()
    def emit(output_format: str = OUTPUT_FORMAT):
        print("body text")
        raise typer.BadParameter("body usage")

    result = CliRunner().invoke(app, ["emit"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error_code"] == "NO_OUTCOME"
    assert "body text" in result.stderr


def test_ascii_process_counts_typer_binary_echo():
    code = """
import typer
from pyrite.utils.errors import PyriteCLIGroup
app = typer.Typer(cls=PyriteCLIGroup)
@app.callback()
def root(): pass
@app.command()
def emit():
    typer.echo(b'before')
    raise typer.BadParameter('bad body')
app(['emit'])
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=dict(os.environ, PYTHONIOENCODING="ascii"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2
    assert result.stdout == "before\n"
    assert "bad body" in result.stderr


@pytest.mark.parametrize("fmt", ["yaml", "rich"])
def test_legacy_format_beats_default_for_late_parse_error(fmt):
    from pyrite.cli.output import LEGACY_FORMAT_SHORT, OUTPUT_FORMAT

    app = _app()

    def refuse(ctx: typer.Context, value: str):
        raise typer.BadParameter("late refusal", ctx=ctx)

    @app.command()
    def emit(
        output_format: str = OUTPUT_FORMAT,
        legacy_format: str | None = LEGACY_FORMAT_SHORT,
        invalid: str = typer.Option("bad", callback=refuse),
    ):
        raise AssertionError("parse refusal must prevent the body")

    result = CliRunner().invoke(app, ["emit", "-f", fmt])
    assert result.exit_code == 2
    if fmt == "yaml":
        import yaml

        assert not result.stdout.lstrip().startswith("{")
        assert yaml.safe_load(result.stdout)["error_code"] == "USAGE_ERROR"
    else:
        assert result.stdout.startswith("ERROR [USAGE_ERROR]:")
        assert "late refusal" in result.stdout

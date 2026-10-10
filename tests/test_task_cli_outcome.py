"""The task commands on the outcome contract (ADR-0046 slice 1), through `pyrite`.

The format changes how the answer looks, never what runs: JSON by default,
`PYRITE_FORMAT` for a person's default, `-f` deprecated (#303), an unknown
format a usage error, and a refusal printed in the format asked for. These
enter at the real `pyrite` app under CliRunner; the exit codes through the
real process are `tests/test_requested_effect_exit_code.py::TestTasks`.
"""

import json

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from tests.test_task_cli_get import task_cli_env  # noqa: F401  (fixture)

runner = CliRunner()


@pytest.mark.cli
def test_task_get_prints_json_by_default_with_its_outcome(task_cli_env):  # noqa: F811
    out = runner.invoke(app, ["task", "get", task_cli_env["task_id"], "-k", "test-tasks"])
    assert out.exit_code == 0, out.output
    body = json.loads(out.stdout)
    assert (body["id"], body["outcome"]) == (task_cli_env["task_id"], "done")


@pytest.mark.cli
def test_pyrite_format_sets_the_default(task_cli_env):  # noqa: F811
    out = runner.invoke(
        app,
        ["task", "get", task_cli_env["task_id"], "-k", "test-tasks"],
        env={"PYRITE_FORMAT": "yaml"},
    )
    assert out.exit_code == 0, out.output
    assert "outcome: done" in out.stdout and not out.stdout.lstrip().startswith("{")


@pytest.mark.cli
def test_short_f_still_means_format_and_warns_on_stderr(task_cli_env):  # noqa: F811
    out = runner.invoke(app, ["task", "list", "-k", "test-tasks", "-f", "json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.stdout)["count"] == 1
    assert "-f is deprecated" in out.stderr


@pytest.mark.cli
def test_an_unknown_format_is_a_usage_error(task_cli_env):  # noqa: F811
    out = runner.invoke(app, ["task", "list", "-k", "test-tasks", "--format", "xml"])
    assert out.exit_code == 2, out.output


@pytest.mark.cli
def test_a_missing_task_is_refused_as_not_found_in_json(task_cli_env):  # noqa: F811
    out = runner.invoke(app, ["task", "get", "nope", "-k", "test-tasks"])
    assert out.exit_code == 1, out.output
    body = json.loads(out.stdout)
    assert (body["error_code"], body["outcome"]) == ("NOT_FOUND", "error")
    assert "task list" in body["suggestion"]


@pytest.mark.cli
def test_a_refused_field_is_printed_in_the_format_asked_for(task_cli_env):  # noqa: F811
    out = runner.invoke(app, ["task", "create", "T", "-k", "test-tasks", "--field", "status=done"])
    assert out.exit_code == 1, out.output
    body = json.loads(out.stdout)
    assert body["error_code"] == "VALIDATION_FAILED"
    assert "status" in body["error"]


@pytest.mark.cli
def test_update_with_nothing_to_update_is_refused_in_json(task_cli_env):  # noqa: F811
    out = runner.invoke(app, ["task", "update", task_cli_env["task_id"], "-k", "test-tasks"])
    assert out.exit_code == 1, out.output
    assert json.loads(out.stdout)["error_code"] == "VALIDATION_FAILED"


@pytest.mark.cli
@pytest.mark.control(reason="rich output keeps its text across the move to a renderer")
def test_rich_is_still_a_person_s_view(task_cli_env):  # noqa: F811
    out = runner.invoke(
        app, ["task", "get", task_cli_env["task_id"], "-k", "test-tasks", "--format", "rich"]
    )
    assert out.exit_code == 0, out.output
    assert "CLI lookup task" in out.stdout and "Completion (derived)" in out.stdout

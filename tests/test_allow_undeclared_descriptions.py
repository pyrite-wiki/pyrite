"""Test allow_undeclared descriptions across MCP, CLI, and REST surfaces (#1507).

Only a KB whose kb.yaml declares types refuses undeclared types.
A KB that declares no types accepts any type.
Every description of allow_undeclared must state this rule accurately.
"""

from __future__ import annotations

import inspect

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.server.endpoints.entries import import_entries
from pyrite.server.tool_schemas import WRITE_TOOLS
from tests.cli_help import plain

EXPECTED_RULE_SNIPPET = "Only a KB whose kb.yaml declares types refuses others"
EXPECTED_EMPTY_RULE = "A KB that declares no types accepts any type."
EXPECTED_FULL_DESCRIPTION = (
    "Allow an entry_type the KB does not declare. Only a KB whose kb.yaml declares "
    "types refuses others (core types included); `pyrite index health` then lists "
    "the entry. A KB that declares no types accepts any type."
)


def _cli_option_help(command_name: str, opt_name: str = "--allow-undeclared") -> str:
    click_app = get_command(app)
    cmd = click_app.commands[command_name]
    for param in cmd.params:
        if opt_name in param.opts:
            return param.help
    raise AssertionError(f"Option {opt_name} not found on command {command_name}")


@pytest.fixture(scope="module")
def descriptions() -> dict[str, str]:
    sig = inspect.signature(import_entries)
    rest_desc = sig.parameters["allow_undeclared"].default.description

    return {
        "mcp_kb_create": WRITE_TOOLS["kb_create"]["inputSchema"]["properties"]["allow_undeclared"][
            "description"
        ],
        "mcp_kb_bulk_create": WRITE_TOOLS["kb_bulk_create"]["inputSchema"]["properties"][
            "allow_undeclared"
        ]["description"],
        "cli_create": _cli_option_help("create"),
        "cli_add": _cli_option_help("add"),
        "cli_import": _cli_option_help("import"),
        "rest_import": rest_desc,
    }


@pytest.mark.parametrize(
    "surface",
    [
        "mcp_kb_create",
        "mcp_kb_bulk_create",
        "cli_create",
        "cli_add",
        "cli_import",
        "rest_import",
    ],
)
def test_allow_undeclared_descriptions_state_rule(
    descriptions: dict[str, str], surface: str
) -> None:
    desc = descriptions[surface]
    assert desc == EXPECTED_FULL_DESCRIPTION
    assert EXPECTED_RULE_SNIPPET in desc
    assert EXPECTED_EMPTY_RULE in desc
    assert "refused otherwise" not in desc


@pytest.mark.parametrize("command_name", ["create", "add", "import"])
def test_cli_help_plain_output_contains_rule(command_name: str) -> None:
    runner = CliRunner()
    result = runner.invoke(app, [command_name, "--help"])
    assert result.exit_code == 0, result.output
    import re

    text = re.sub(r"[│\s]+", " ", plain(result.stdout))
    assert EXPECTED_RULE_SNIPPET in text
    assert EXPECTED_EMPTY_RULE in text
    assert "refused otherwise" not in text


@pytest.mark.parametrize("tool_name", ["kb_create", "kb_update"])
def test_participants_description_mentions_actors(tool_name: str) -> None:
    desc = WRITE_TOOLS[tool_name]["inputSchema"]["properties"]["participants"]["description"]
    assert "written to the file as `actors`" in desc
    assert desc == "Participants involved (for events; written to the file as `actors`)"

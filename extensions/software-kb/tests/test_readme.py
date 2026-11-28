"""The extension's README says what the code does.

Its lifecycle table, type list, command table and MCP names are compared with
`BACKLOG_WORKFLOW`, the plugin's types, the `sw` command registry and the MCP
tool registry, so a code change that the README does not follow fails here.
"""

import re
from pathlib import Path

from pyrite_software_kb.cli import sw_app
from pyrite_software_kb.plugin import SoftwareKBPlugin
from pyrite_software_kb.workflows import BACKLOG_WORKFLOW

README = (Path(__file__).resolve().parents[1] / "README.md").read_text()


def _section(heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", README, re.S | re.M)
    assert m, f"README has no '## {heading}' section"
    return m.group(1)


def _rows(section: str) -> list[list[str]]:
    rows = []
    for line in section.splitlines():
        if line.startswith("|") and not re.match(r"^\|[-| ]+\|$", line):
            rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows[1:]  # drop the header


def _name(cell: str) -> str:
    return cell.strip("`")


def test_lifecycle_table_is_the_workflow_row_for_row():
    documented = {
        (_name(f), _name(t)): reason == "required"
        for f, t, reason, _cmd in _rows(_section("The backlog lifecycle"))
    }
    declared = {
        (t["from"], t["to"]): bool(t.get("requires_reason"))
        for t in BACKLOG_WORKFLOW["transitions"]
    }
    assert documented == declared


def test_entry_type_table_is_the_plugin_types():
    documented = {_name(row[0]) for row in _rows(_section("Entry types"))}
    assert documented == set(SoftwareKBPlugin().get_entry_types())


def test_command_table_is_the_sw_registry_and_mcp_names_exist():
    registered = {
        c.name or c.callback.__name__.removeprefix("sw_").replace("_", "-")
        for c in sw_app.registered_commands
    }
    rows = _rows(_section("Command reference"))
    documented = {_name(row[0]).split(" ")[0] for row in rows}
    assert documented == registered
    tools = set()
    plugin = SoftwareKBPlugin()
    for tier in ("read", "write", "admin"):
        tools |= set(plugin.get_mcp_tools(tier))
    named = {_name(row[2]) for row in rows if row[2] != "none"}
    assert named <= tools
    assert "sw_create_backlog_item" in README and "sw_create_backlog_item" in tools


def test_every_relation_the_readme_names_is_declared():
    declared = set(SoftwareKBPlugin().get_relationship_types())
    for name in re.findall(r"-r (\w+)", README):
        assert name in declared, name

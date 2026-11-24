"""Every tool argument that names a person is declared, or says why not.

The dispatcher sets a tool's person arguments from the session's principal,
but only the ones the tool declares (`PERSON_ARGUMENTS`). A tool that records
a person and forgets to declare it would take the person from its arguments
-- and every behavioural test would still pass, because no test names a tool
that does not exist yet. So this gate walks the registry, core and plugin, at
the admin tier, and fails on any argument that looks like it names a person
and is neither declared nor listed below with a reason. It is the inventory
the inventory of every argument that may name a person.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pyrite.config import PyriteConfig, Settings
from pyrite.plugins.identity import PERSON_ARGUMENTS
from pyrite.server.mcp_server import PyriteMCPServer

LOOKS_LIKE_A_PERSON = re.compile(
    r"user|author|review|assign|owner|actor|person|decider|creator|member|voter|editor|claimant"
)

# (tool, argument path) -> why it is not a person this tool records.
NOT_RECORDED: dict[tuple[str, str], str] = {
    ("social_reputation", "user_id"): "read filter: whose reputation to sum; records nothing",
    ("kb_find_by_assignee", "assignee"): "read filter; records nothing",
    ("task_list", "assignee"): "read filter; records nothing",
    ("solidarity_timeline", "actor"): "read filter on the subjects of the timeline",
    ("investigation_timeline", "actor"): "read filter on the subjects of the investigation",
    ("sw_create_adr", "deciders"): (
        "document content: the people a decision record names, written by whoever "
        "takes the minutes -- not who is acting"
    ),
}


def _paths(properties: dict, prefix: str = ""):
    for name, schema in properties.items():
        yield prefix + name
        items = schema.get("items") if isinstance(schema, dict) else None
        if isinstance(items, dict) and items.get("properties"):
            yield from _paths(items["properties"], f"{prefix}{name}[].")


@pytest.fixture(scope="module")
def server(tmp_path_factory) -> PyriteMCPServer:
    tmp = tmp_path_factory.mktemp("person-gate")
    s = PyriteMCPServer(
        config=PyriteConfig(knowledge_bases=[], settings=Settings(index_path=Path(tmp) / "i.db")),
        tier="admin",
    )
    yield s
    s.close()


def test_every_person_argument_is_declared_or_explained(server):
    undeclared = []
    for name, tool in sorted(server.tools.items()):
        declared = tool.get(PERSON_ARGUMENTS) or {}
        for path in _paths(tool["inputSchema"].get("properties", {})):
            if not LOOKS_LIKE_A_PERSON.search(path.rsplit(".", 1)[-1]):
                continue
            if path in declared or (name, path) in NOT_RECORDED:
                continue
            undeclared.append(f"{name}: {path}")
    assert undeclared == [], undeclared


def test_every_declaration_names_an_argument_the_tool_has(server):
    stale = []
    for name, tool in server.tools.items():
        paths = set(_paths(tool["inputSchema"].get("properties", {})))
        stale += [f"{name}: {p}" for p in tool.get(PERSON_ARGUMENTS) or {} if p not in paths]
    stale += [
        f"{t}: {p}"
        for (t, p) in NOT_RECORDED
        if t in server.tools
        and p not in set(_paths(server.tools[t]["inputSchema"].get("properties", {})))
    ]
    assert stale == [], stale

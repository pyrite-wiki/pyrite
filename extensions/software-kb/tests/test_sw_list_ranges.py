"""Tests for sw list commands declaring min=0 range on --limit and --offset (#1565)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from pyrite_software_kb import cli
from typer.testing import CliRunner

import pyrite.cli  # noqa: F401
from pyrite.config import KBConfig
from tests._world import build_world

# Windows compatibility: pyrite.utils.private_files tighten_file calls os.getuid,
# and pyrite.storage.index reads os.O_NONBLOCK, neither of which exists on Windows.
if not hasattr(os, "getuid"):
    os.getuid = lambda: 0
if not hasattr(os, "O_NONBLOCK"):
    os.O_NONBLOCK = 0

COMMANDS = ["adrs", "backlog", "standards", "components"]


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.parametrize("cmd", COMMANDS)
def test_sw_list_commands_reject_negative_limit(runner: CliRunner, cmd: str) -> None:
    result = runner.invoke(cli.sw_app, [cmd, "--limit", "-1"])
    assert result.exit_code == 2
    normalized = " ".join(re.sub(r"[│|┌┐└┘─]", "", result.output).split())
    assert "Invalid value for '--limit'" in normalized
    assert "is not in the range x>=0" in normalized


@pytest.mark.parametrize("cmd", COMMANDS)
def test_sw_list_commands_reject_negative_offset(runner: CliRunner, cmd: str) -> None:
    result = runner.invoke(cli.sw_app, [cmd, "--offset", "-1"])
    assert result.exit_code == 2
    normalized = " ".join(re.sub(r"[│|┌┐└┘─]", "", result.output).split())
    assert "Invalid value for '--offset'" in normalized
    assert "is not in the range x>=0" in normalized


def test_sw_backlog_help_text(runner: CliRunner) -> None:
    result = runner.invoke(cli.sw_app, ["backlog", "--help"])
    assert result.exit_code == 0
    assert "pass 0 for no limit" in result.output
    assert "negative number" not in result.output


@pytest.mark.parametrize("cmd", ["adrs", "standards", "components"])
def test_sw_help_text_mentions_zero_all(runner: CliRunner, cmd: str) -> None:
    result = runner.invoke(cli.sw_app, [cmd, "--help"])
    assert result.exit_code == 0
    normalized = " ".join(re.sub(r"[│|┌┐└┘─]", "", result.output).split())
    assert "0 = all" in normalized


@pytest.mark.parametrize("cmd", COMMANDS)
def test_sw_list_commands_limit_zero_and_paging(
    tmp_path: Path, runner: CliRunner, cmd: str
) -> None:
    kb_path = tmp_path / "sw-kb"
    world = build_world(
        tmp_path,
        kbs=[KBConfig(name="sw-kb", path=kb_path, kb_type="software")],
        rest=False,
    )
    try:
        # Seed 3 items for each command type
        if cmd == "adrs":
            for i in range(1, 4):
                world.add_entry(
                    "sw-kb",
                    f"---\nid: adr-000{i}\ntype: adr\ntitle: ADR {i}\nstatus: proposed\nadr_number: {i}\n---\nBody",
                )
        elif cmd == "backlog":
            for i in range(1, 4):
                world.add_entry(
                    "sw-kb",
                    f"---\nid: item-000{i}\ntype: backlog_item\ntitle: Item {i}\nstatus: planned\npriority: medium\nkind: feature\n---\nBody",
                )
        elif cmd == "standards":
            for i in range(1, 4):
                world.add_entry(
                    "sw-kb",
                    f"---\nid: std-000{i}\ntype: standard\ntitle: Standard {i}\ncategory: architecture\n---\nBody",
                )
        elif cmd == "components":
            for i in range(1, 4):
                world.add_entry(
                    "sw-kb",
                    f"---\nid: comp-000{i}\ntype: component\ntitle: Component {i}\nkind: service\n---\nBody",
                )

        expected_titles = {
            "adrs": {"ADR 1", "ADR 2", "ADR 3"},
            "backlog": {"Item 1", "Item 2", "Item 3"},
            "standards": {"Standard 1", "Standard 2", "Standard 3"},
            "components": {"Component 1", "Component 2", "Component 3"},
        }[cmd]

        with world.pointed():
            # --limit 0 returns all 3 items
            res0 = runner.invoke(cli.sw_app, [cmd, "-k", "sw-kb", "--limit", "0", "-f", "json"])
            assert res0.exit_code == 0, res0.output
            items0 = json.loads(res0.output)
            assert len(items0) == 3
            assert {i["title"] for i in items0} == expected_titles

            # --limit 1 --offset 1 returns 1 item, matching the second item of items0
            res1 = runner.invoke(
                cli.sw_app,
                [cmd, "-k", "sw-kb", "--limit", "1", "--offset", "1", "-f", "json"],
            )
            assert res1.exit_code == 0, res1.output
            items1 = json.loads(res1.output)
            assert len(items1) == 1
            assert items1 == items0[1:2]
    finally:
        world.close()

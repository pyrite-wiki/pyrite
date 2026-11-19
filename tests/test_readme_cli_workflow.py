"""Run README's local Quick Start and CLI commands in an isolated HOME.

The generic tutorial runner skips the whole Quick Start block because it
contains an install/clone, so extract its Pyrite commands here. Installation,
model download and MCP client setup are outside this local CLI example.
"""

import json
import os
from pathlib import Path
import re
import shlex

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _commands(section):
    block = re.search(r"```bash\n(.*?)```", section, re.S).group(1)
    for line in block.replace("\\\n", " ").splitlines():
        args = shlex.split(line, comments=True)
        if args and args[0] == "pyrite":
            yield args[1:]


@pytest.mark.skipif(os.name == "nt", reason="Real entry publication requires POSIX")
def test_readme_cli_continues_from_quick_start(cli_world, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    world = cli_world()
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    quick = text.split("## Quick Start\n", 1)[1]
    cli = text.split("## CLI\n", 1)[1]
    for section in (quick, cli):
        for args in _commands(section):
            if args[0] == "mcp-setup" or "--mode=semantic" in args:
                continue
            result = world.cli(*args)
            assert result.exit_code == 0, (args, result.output)
            if args[:2] == ["get", "sarah-chen"]:
                assert json.loads(result.stdout)["id"] == "sarah-chen"
    result = world.cli("get", "release-checklist", "-k", "my-kb")
    assert result.exit_code == 0
    assert "Review the upgrade guide" in json.loads(result.stdout)["body"]

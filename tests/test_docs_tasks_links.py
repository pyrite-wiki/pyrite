"""Execute the task/link how-tos through actual CLI and MCP registries."""

import json
import os
from pathlib import Path
import re
import shlex

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _blocks(page):
    text = (ROOT / "docs" / page).read_text(encoding="utf-8")
    pattern = r"(?:(?:<!-- expect-exit: (\d+) -->)\s*)?```bash\n(.*?)```"
    return [(int(code or 0), block) for code, block in re.findall(pattern, text, re.S)]


@pytest.mark.skipif(os.name == "nt", reason="The documented entry writes require POSIX publication")
@pytest.mark.parametrize("page", ["tasks.md", "links.md"])
def test_howto_commands_run_in_order(page, cli_world, tmp_path, monkeypatch):
    world = cli_world()
    monkeypatch.chdir(tmp_path)
    if page == "links.md":
        text = (ROOT / "docs/links.md").read_text(encoding="utf-8")
        yaml = re.search(r"```yaml\n(.*?)```", text, re.S).group(1)
        (tmp_path / "links.yaml").write_text(yaml, encoding="utf-8")
    last = None
    for expected, block in _blocks(page):
        for line in block.strip().splitlines():
            args = shlex.split(line)
            assert args.pop(0) == "pyrite"
            result = world.cli(*args)
            assert result.exit_code == expected, (page, line, result.output)
            last = json.loads(result.stdout)
            if args[:2] == ["task", "claim"] and expected == 1:
                assert last["claimed"] is False
            if args[:2] == ["links", "bulk-create"] and expected == 3:
                assert last["outcome"] == "partial"
            if args[0] == "backlinks":
                assert any(row["id"] == "alpha" for row in last["entries"])
    if page == "tasks.md":
        assert last["status"] != "done"
        assert last["derived"]["completion"]["complete"] is True


def test_task_mcp_table_matches_registered_tools_and_tiers(cli_world):
    server = cli_world().mcp()
    page = (ROOT / "docs/tasks.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\|[^|]+\|[^|]+\| (task_\w+) \| (read|write) \|", page, re.M)
    assert len(rows) >= 9
    for name, tier in rows:
        assert name in server.tools, name
        assert server._tool_tiers[name] == tier


def test_howtos_have_executable_examples_and_contract_links():
    for page in ("tasks.md", "links.md"):
        assert len(_blocks(page)) >= 3
        assert "json-contracts.md" in (ROOT / "docs" / page).read_text(encoding="utf-8")
        assert f"({page})" in (ROOT / "docs/README.md").read_text(encoding="utf-8")

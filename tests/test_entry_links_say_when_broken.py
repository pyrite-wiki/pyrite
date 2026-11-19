"""An entry's `links` say when a Markdown link is broken (#1765).

Follow-up from #1736 (fixes #1723): a Markdown link whose path matched no
entry file is a link row whose `target_id` is its path text. If an entry
happens to have that hand-set id, a client that followed the row by id
landed on that unrelated entry: `links check`, `index health`, QA and
backlinks already treat the row as broken, but the `links` payload of a
single-entry read carried no signal.

The invariant under test, on every surface that returns the read (CLI
`get --format json`, REST `GET /api/entries/{id}`, MCP `kb_get`):

- an item for a Markdown path row carries `target_path` and
  `broken: true|false`;
- an item for a link that names an id is unchanged (no `target_path`,
  no `broken`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from tests._world import build_world

KB = "test-kb"


@pytest.fixture
def world(tmp_path: Path):
    w = build_world(
        tmp_path / "w",
        kbs=[KBConfig(name=KB, path=tmp_path / "w" / KB, kb_type="generic")],
    )
    # The trap: an entry whose hand-set id is exactly the path text of the
    # broken link below. It lives at another path, so the link names a
    # path, not this entry.
    w.add_entry(
        KB,
        "---\nid: twin.md\ntype: note\ntitle: Twin\n---\n\nAn unrelated entry.\n",
        filename="files/twin-entry.md",
    )
    w.add_entry(
        KB,
        "---\nid: real\ntype: note\ntitle: Real\n---\n\nThe resolved target.\n",
        filename="real.md",
    )
    w.add_entry(
        KB,
        "---\nid: src\ntype: note\ntitle: Source\n---\n\n"
        "A path link that resolves, [ok](real.md); a path link that is "
        "broken, [bad](twin.md); and a link that names an id, [[real]].\n",
    )
    yield w
    w.close()


def _items(links: list[dict]) -> dict[tuple[str, str | None], dict]:
    return {(item["target_id"], item.get("target_path")): item for item in links}


def _assert_links_shape(links: list[dict]) -> None:
    items = _items(links)
    broken = items[("twin.md", "twin.md")]
    assert broken["broken"] is True, broken
    resolved = items[("real", "real.md")]
    assert resolved["target_path"] == "real.md", resolved
    assert resolved["broken"] is False, resolved
    id_row = next(
        item for item in links if item["target_id"] == "real" and "target_path" not in item
    )
    assert "broken" not in id_row, id_row


def test_cli_get_json_links_say_broken(world):
    result = world.cli("get", "src", "-k", KB, "--format", "json")
    assert result.exit_code == 0, result.output
    _assert_links_shape(json.loads(result.output)["links"])


def test_rest_entry_links_say_broken(world):
    resp = world.client.get("/api/entries/src", params={"kb": KB})
    assert resp.status_code == 200, resp.text
    _assert_links_shape(resp.json()["links"])


def test_mcp_kb_get_links_say_broken(world):
    result = world.mcp()._dispatch_tool("kb_get", {"entry_id": "src", "kb_name": KB})
    _assert_links_shape(result["entry"]["links"])

"""A file whose frontmatter Pyrite cannot model answers VALIDATION_FAILED on every surface (#1035).

The property: for every shape #760 lists (a tagged key, a complex key, a
duplicate key, a root that is not a mapping), ``get`` and ``update`` on the CLI,
MCP and REST answer ``VALIDATION_FAILED`` naming the reason and the line,
never a traceback (``INTERNAL``, ``NO_OUTCOME``, a 500) and never ``NOT_FOUND``,
and an update writes nothing.

The grid is shape × surface × operation. The expected reason word and the
1-based line are written by hand from each file's text.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app as cli_app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

KB = "grid"
ID = "shape"

#: name -> (file text, 1-based line the reason names, words of the reason)
SHAPES: dict[str, tuple[str, int, str]] = {
    "tagged-key": (
        f"---\nid: {ID}\ntitle: S\ntype: note\n!!str flag: 1\n---\n\nText.\n",
        5,
        "tagged key",
    ),
    "complex-key": (
        f"---\nid: {ID}\ntitle: S\ntype: note\n? [a, b]\n: 1\n---\n\nText.\n",
        5,
        "complex key",
    ),
    "duplicate-key": (
        f"---\nid: {ID}\ntitle: S\ntype: note\nyear: 2025\nyear: 2026\n---\n\nText.\n",
        6,
        "duplicate key",
    ),
    "non-mapping-root": (
        "---\n- a\n- b\n---\n\nText.\n",
        2,
        "not a mapping",
    ),
}


def _build(tmp_path: Path, text: str) -> tuple[PyriteConfig, Path]:
    kb = tmp_path / "kb"
    (kb / "notes").mkdir(parents=True)
    path = kb / "notes" / f"{ID}.md"
    path.write_text(text, encoding="utf-8")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config, path


# Each surface returns (error_code, message), or ("OK", "") when it answered.


def _cli(config: PyriteConfig, args: list[str]) -> tuple[str, str]:
    with (
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        result = CliRunner().invoke(cli_app, [*args, "--format", "json"])
    text = result.output.strip()
    start = text.find("{")
    if start < 0:
        return "TRACEBACK", f"{result.output} {result.exception!r}"
    data = json.loads(text[start:])
    if result.exit_code == 0:
        return "OK", ""
    return data.get("error_code", "?"), str(data.get("error", ""))


def cli_get(config):
    return _cli(config, ["get", ID, "-k", KB])


def cli_update(config):
    return _cli(config, ["update", ID, "-k", KB, "--title", "T"])


def _mcp(config: PyriteConfig, tool: str, args: dict) -> tuple[str, str]:
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(config, tier="write")
    try:
        result = server._dispatch_tool(tool, {"kb_name": KB, "entry_id": ID, **args})
    finally:
        server.close()
    if result.get("error"):
        return result.get("error_code", "?"), str(result["error"])
    return "OK", ""


def mcp_get(config):
    return _mcp(config, "kb_get", {})


def mcp_update(config):
    return _mcp(config, "kb_update", {"title": "T"})


def _rest(config: PyriteConfig, method: str, **kwargs) -> tuple[str, str]:
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from fastapi.testclient import TestClient

    from pyrite.server.api import create_app, get_config, get_db, get_index_mgr

    db = PyriteDB(config.settings.index_path)
    index_mgr = IndexManager(db, config)
    api = create_app(config)
    api.dependency_overrides[get_config] = lambda: config
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_index_mgr] = lambda: index_mgr
    client = TestClient(api, base_url="http://localhost", raise_server_exceptions=False)
    try:
        res = client.request(method, f"/api/entries/{ID}", **kwargs)
    finally:
        client.close()
        db.close()
    if res.status_code == 200:
        return "OK", ""
    try:
        body = res.json()
    except ValueError:
        return f"HTTP_{res.status_code}", res.text
    error = body.get("error", body.get("detail", body))
    if isinstance(error, dict):
        return error.get("code", error.get("error_code", f"HTTP_{res.status_code}")), str(error)
    return body.get("error_code", body.get("code", f"HTTP_{res.status_code}")), str(error)


def rest_get(config):
    return _rest(config, "GET", params={"kb": KB})


def rest_put(config):
    return _rest(config, "PUT", json={"kb": KB, "title": "T"})


def rest_patch(config):
    return _rest(config, "PATCH", json={"kb": KB, "field": "title", "value": "T"})


SURFACES = {
    "cli.get": cli_get,
    "mcp.kb_get": mcp_get,
    "rest.get": rest_get,
    "cli.update": cli_update,
    "mcp.kb_update": mcp_update,
    "rest.put": rest_put,
    "rest.patch": rest_patch,
}


READ_SURFACES = ("cli.get", "mcp.kb_get", "rest.get")
UPDATE_SURFACES = ("cli.update", "mcp.kb_update", "rest.put", "rest.patch")

#: The read wording says nothing about editing or writing: a read edits nothing.
EDIT_WORDS = ("edit", "written", "not edited")


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_an_unmodellable_file_answers_validation_failed_with_reason_and_line(
    tmp_path: Path, shape: str, surface: str
):
    text, line, words = SHAPES[shape]
    config, path = _build(tmp_path, text)
    before = path.read_bytes()

    code, message = SURFACES[surface](config)

    assert code == "VALIDATION_FAILED", (code, message)
    assert words in message, message
    assert f"line {line}" in message, message
    assert path.read_bytes() == before


@pytest.mark.parametrize("surface", READ_SURFACES)
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_refused_read_does_not_talk_about_editing(tmp_path: Path, shape: str, surface: str):
    config, _path = _build(tmp_path, SHAPES[shape][0])
    _code, message = SURFACES[surface](config)
    assert not any(word in message for word in EDIT_WORDS), message


#: Files whose frontmatter has readable fields and that ``dev`` reads: a read
#: of each still reads (a read is refused only when the file has no readable
#: fields). name -> file text, each the entry ``shape``.
READABLE: dict[str, str] = {
    "flow-style-root": f"---\n{{id: {ID}, title: S, type: note}}\n---\n\nText.\n",
    "anchor-and-alias": f"---\nid: {ID}\ntitle: S\ntype: note\nbase: &b 1\nref: *b\n---\n\nText.\n",
    "merge-key": (
        f"---\nid: {ID}\ntitle: S\ntype: note\nbase: &b {{a: 1}}\nm:\n  <<: *b\n  c: 2\n---\n\nText.\n"
    ),
    "flow-value": f"---\nid: {ID}\ntitle: S\ntype: note\ntags: [a, b]\nextra: {{k: v}}\n---\n\nText.\n",
    "int-key": f"---\nid: {ID}\ntitle: S\ntype: note\nextra:\n  1: one\n---\n\nText.\n",
    "comments-crlf": (
        f"---\r\n# c\r\nid: {ID}\r\ntitle: S  # t\r\ntype: note\r\n---\r\n\r\nText.\r\n"
    ),
    "bom": f"\ufeff---\nid: {ID}\ntitle: S\ntype: note\n---\n\nText.\n",
}


@pytest.mark.parametrize("surface", READ_SURFACES)
@pytest.mark.parametrize("name", sorted(READABLE))
def test_a_file_dev_reads_still_reads(tmp_path: Path, name: str, surface: str):
    config, _path = _build(tmp_path, READABLE[name])
    code, message = SURFACES[surface](config)
    assert code == "OK", (code, message)


@pytest.mark.parametrize("surface", UPDATE_SURFACES)
def test_a_flow_style_root_is_still_refused_for_update_with_its_line(tmp_path: Path, surface: str):
    config, path = _build(tmp_path, READABLE["flow-style-root"])
    before = path.read_bytes()
    code, message = SURFACES[surface](config)
    assert code == "VALIDATION_FAILED", (code, message)
    assert "flow-style" in message and "line 2" in message, message
    assert path.read_bytes() == before


SET_VALUE = f"---\nid: {ID}\ntitle: S\ntype: note\ns: !!set {{a, b}}\n---\n\nText.\n"


@pytest.mark.parametrize("surface", READ_SURFACES)
def test_a_set_value_is_a_refusal_not_a_traceback(tmp_path: Path, surface: str):
    config, _path = _build(tmp_path, SET_VALUE)
    code, message = SURFACES[surface](config)
    assert code == "VALIDATION_FAILED", (code, message)
    assert f"'{ID}'" in message and not any(w in message for w in EDIT_WORDS), message

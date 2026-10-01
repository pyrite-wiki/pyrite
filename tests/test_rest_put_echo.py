"""REST PUT echoes a whole read back without corrupting a value (#569 item 1).

MCP ``kb_update`` routes updates through ``KBService.split_echoed_update`` so
a client that echoes a ``kb_get`` result back does not rewrite
``importance: high`` as ``5`` or ``tags: Foo`` as ``['Foo']`` (#561). REST
``PUT /api/entries/{id}`` (``pyrite/server/endpoints/entries.py``) built its
``updates`` dict straight from the request body instead, so the same echo
hazard applied to any REST caller (plausibly the web editor) that reads an
entry and PUTs it back with one field changed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.server.api import create_app, get_config, get_db, get_index_mgr
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

KB = "work"

DRAFT = "---\nid: d\ntitle: D\ntype: note\nimportance: high\ntags: Foo\n---\n\nBody.\n"


@pytest.fixture
def rest_env():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        tmpdir = Path(tmpdir)
        kb_path = tmpdir / "kb"
        kb_path.mkdir()
        (kb_path / "kb.yaml").write_text("name: work\nkb_type: generic\n", encoding="utf-8")
        (kb_path / "notes").mkdir()
        (kb_path / "notes" / "d.md").write_text(DRAFT, encoding="utf-8")

        db_path = tmpdir / "index.db"
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type=KBType.GENERIC)],
            settings=Settings(index_path=db_path),
        )
        db = PyriteDB(db_path)
        index_mgr = IndexManager(db, config)
        index_mgr.index_all()

        app = create_app(config)
        app.dependency_overrides[get_config] = lambda: config
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_index_mgr] = lambda: index_mgr
        client = TestClient(app)
        try:
            yield {"client": client, "kb_path": kb_path}
        finally:
            db.close()
            client.close()


def test_put_echoing_a_read_result_keeps_the_files_importance_and_tags(rest_env):
    client = rest_env["client"]
    path = rest_env["kb_path"] / "notes" / "d.md"

    got = client.get("/api/entries/d", params={"kb": KB})
    assert got.status_code == 200, got.text
    entry = got.json()

    # An echoing client: a deliberate title change, plus the read's own
    # importance/tags sent back unmodified -- the UpdateEntryRequest shape
    # (kb/title/body/importance/tags/metadata) is all a REST caller can send.
    res = client.put(
        "/api/entries/d",
        json={
            "kb": KB,
            "title": "Renamed",
            "importance": entry["importance"],
            "tags": entry["tags"],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["updated"] is True

    text = path.read_text(encoding="utf-8")
    assert "title: Renamed" in text, text
    assert "importance: high" in text, text
    assert "tags: Foo" in text, text


def test_put_still_writes_a_deliberate_importance_change(rest_env):
    """The echo guard must not swallow a value the caller actually sent."""
    client = rest_env["client"]
    path = rest_env["kb_path"] / "notes" / "d.md"

    res = client.put("/api/entries/d", json={"kb": KB, "importance": 9})
    assert res.status_code == 200, res.text
    assert res.json()["updated"] is True
    assert "importance: 9" in path.read_text(encoding="utf-8")


def test_put_response_reports_echoed_fields_as_unchanged(rest_env):
    client = rest_env["client"]

    entry = client.get("/api/entries/d", params={"kb": KB}).json()
    res = client.put(
        "/api/entries/d",
        json={"kb": KB, "importance": entry["importance"], "tags": entry["tags"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body.get("unchanged", [])) >= {"importance", "tags"}, body

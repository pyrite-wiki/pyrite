"""REST import runs the importer and the bulk write off the event loop.

``import_entries`` is ``async`` and used to parse the upload and write every
entry on the loop, so one large import held up every other request.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import httpx
import pytest

from pyrite.config import KBConfig
from pyrite.formats.importers import get_importer_registry
from pyrite.plugins import get_registry
from tests._world import build_world

WAIT = 30


@pytest.fixture
def world(tmp_path: Path):
    w = build_world(
        tmp_path / "w", kbs=[KBConfig(name="k", path=tmp_path / "w" / "k", kb_type="generic")]
    )
    registry = get_importer_registry()
    original = registry.get("json")
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    seen = {}

    def slow_importer(content, **kwargs):
        seen["thread"] = threading.get_ident()
        started.set()
        try:
            assert release.wait(timeout=WAIT), "the other request did not release the importer"
            return []
        finally:
            finished.set()

    registry.register("json", slow_importer)
    try:
        yield w, started, release, finished, seen
    finally:
        release.set()
        registry.register("json", original)
        w.close()


def test_a_slow_import_does_not_hold_up_another_request(world):
    w, started, release, finished, seen = world

    async def go():
        transport = httpx.ASGITransport(app=w.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            loop_thread = threading.get_ident()
            slow = asyncio.create_task(
                client.post(
                    "/api/entries/import?kb=k&format=json",
                    files={"file": ("a.json", b"[]", "application/json")},
                )
            )
            try:
                assert await asyncio.to_thread(started.wait, timeout=WAIT), "importer never started"
                other = await asyncio.wait_for(client.get("/api/kbs"), timeout=WAIT)
                assert other.status_code == 200
                assert not finished.is_set(), "another request waited for the importer to finish"
                assert seen["thread"] != loop_thread
            finally:
                release.set()
                resp = await asyncio.wait_for(slow, timeout=WAIT)
            assert resp.status_code == 200

    asyncio.run(go())


class _SlowWritePlugin:
    name = "slow_write_1642"

    def __init__(self, hook):
        self._hook = hook

    def get_hooks(self) -> dict:
        return {"before_save": [self._hook]}


@pytest.fixture
def write_world(tmp_path: Path):
    w = build_world(
        tmp_path / "w", kbs=[KBConfig(name="k", path=tmp_path / "w" / "k", kb_type="generic")]
    )
    reg = get_registry()
    reg.discover()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    seen = {}

    def slow_hook(entry, ctx):
        seen["thread"] = threading.get_ident()
        started.set()
        try:
            assert release.wait(timeout=WAIT), "the other request did not release the write"
            return entry
        finally:
            finished.set()

    plugin = _SlowWritePlugin(slow_hook)
    reg.register(plugin)
    try:
        yield w, started, release, finished, seen
    finally:
        release.set()
        reg._plugins.pop(plugin.name, None)
        reg._conformance_cache.pop(plugin.name, None)
        reg._aliases_changed()
        w.close()
        assert release.is_set()


def test_a_slow_bulk_write_does_not_hold_up_another_request(write_world):
    w, started, release, finished, seen = write_world

    async def go():
        transport = httpx.ASGITransport(app=w.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            loop_thread = threading.get_ident()
            upload_content = json.dumps(
                {
                    "entries": [
                        {
                            "title": "Imported Note",
                            "entry_type": "note",
                            "body": "Hello world",
                        }
                    ]
                }
            ).encode("utf-8")
            slow = asyncio.create_task(
                client.post(
                    "/api/entries/import?kb=k&format=json",
                    files={"file": ("entries.json", upload_content, "application/json")},
                )
            )
            try:
                assert await asyncio.to_thread(started.wait, timeout=WAIT), "write never started"
                other = await asyncio.wait_for(client.get("/api/kbs"), timeout=WAIT)
                assert other.status_code == 200
                assert not finished.is_set(), "another request waited for the write to finish"
                assert seen["thread"] != loop_thread
            finally:
                release.set()
                resp = await asyncio.wait_for(slow, timeout=WAIT)
            assert resp.status_code == 200
            data = resp.json()
            assert data["imported"] == 1
            entry_id = data["entries"][0]["id"]
            get_resp = await asyncio.wait_for(
                client.get(f"/api/entries/{entry_id}?kb=k"), timeout=WAIT
            )
            assert get_resp.status_code == 200
            assert get_resp.json()["title"] == "Imported Note"
            assert list((w.root / "k").rglob("*.md"))

    asyncio.run(go())

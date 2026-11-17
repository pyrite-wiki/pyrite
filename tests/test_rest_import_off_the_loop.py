"""REST import runs the importer off the event loop.

``import_entries`` is ``async`` and used to parse the upload on the loop, so
one large import held up every other request. The bulk write is not covered
here: the stand-in importer returns an empty list, so only the importer's
concurrency is exercised.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import httpx
import pytest

from pyrite.config import KBConfig
from pyrite.formats.importers import get_importer_registry
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

"""REST import runs the importer and the bulk write off the event loop.

``import_entries`` is ``async`` and used to parse the upload and write every
entry on the loop, so one large import held up every other request.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import httpx
import pytest

from pyrite.config import KBConfig
from pyrite.formats.importers import get_importer_registry
from tests._world import build_world

SEEN: dict[str, int] = {}


def _slow_importer(content, **kwargs):
    SEEN["thread"] = threading.get_ident()
    time.sleep(1.0)
    return []


@pytest.fixture
def world(tmp_path: Path):
    w = build_world(
        tmp_path / "w", kbs=[KBConfig(name="k", path=tmp_path / "w" / "k", kb_type="generic")]
    )
    registry = get_importer_registry()
    original = registry.get("json")
    registry.register("json", _slow_importer)
    yield w
    registry.register("json", original)
    w.close()


def test_a_slow_import_does_not_hold_up_another_request(world):
    async def go():
        transport = httpx.ASGITransport(app=world.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            loop_thread = threading.get_ident()
            started = time.monotonic()
            slow = asyncio.create_task(
                client.post(
                    "/api/entries/import?kb=k&format=json",
                    files={"file": ("a.json", b"[]", "application/json")},
                )
            )
            await asyncio.sleep(0.2)  # the importer is running
            other = await client.get("/api/kbs")
            other_took = time.monotonic() - started
            resp = await slow
            return loop_thread, other.status_code, other_took, resp.status_code

    loop_thread, other_status, other_took, import_status = asyncio.run(go())
    assert import_status == 200
    assert other_status == 200
    assert SEEN["thread"] != loop_thread
    assert other_took < 0.8, f"another request waited {other_took:.2f}s behind the import"

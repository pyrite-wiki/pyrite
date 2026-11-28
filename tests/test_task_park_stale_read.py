"""A task completed between park's status check and its write is refused (#1817).

`TaskService.park_task` reads the task, verifies it is not done or cancelled,
then writes through `KBService.apply_operations`. If another write completes or
cancels the task between the read and the replace, the guarded write must re-check
status against the bytes it replaces and refuse with the same typed error, leaving
the file's bytes unchanged.

Its own file because it injects that other write at the publish step, and
`tests/test_tests_drive_the_surface_not_the_mock.py` forbids that in a file
that drives an entry point; this one drives the service, as the race is the
service's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyrite.config import KBConfig
from pyrite.exceptions import ValidationError
from tests._world import build_world

KB = "work"


def _task(status: str) -> str:
    return f"---\ntype: task\nid: t\ntitle: T\nstatus: {status}\nassignee: agent:w\n---\n\nWork.\n"


@pytest.mark.parametrize("status", ["done", "cancelled"])
def test_a_task_completed_between_read_and_write_is_not_parked(tmp_path, monkeypatch, status):
    import pyrite.services.kb_service as kb_service
    from pyrite.services.task_service import TaskService
    from pyrite.utils.atomic_write import FileChanged

    world = build_world(
        tmp_path, kbs=[KBConfig(name=KB, path=tmp_path / "kb", kb_type="generic")], rest=False
    )
    try:
        path = world.add_entry(KB, _task("in_progress"))
        edited = _task(status)
        real = kb_service.publish_entry_text
        calls = {"n": 0}

        def racing(target, text, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                Path(target).write_text(edited)  # someone else completes or cancels the task first
                raise FileChanged(str(target), edited.encode())
            return real(target, text, **kw)

        monkeypatch.setattr(kb_service, "publish_entry_text", racing)

        with pytest.raises(
            ValidationError,
            match=f"^Task 't' is {status}: a {status} task is not parked\\. Nothing was written\\.$",
        ):
            TaskService(world.config, world.db).park_task(
                "t", KB, reason="waiting for review", by="agent:w"
            )
        assert calls["n"] == 1
        assert path.read_text() == edited
    finally:
        world.close()

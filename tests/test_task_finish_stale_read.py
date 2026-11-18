"""A walk computed from a stale read is never applied (#1685).

`TaskService.finish_task` reads the task, computes its walk, then writes once
through `KBService.apply_operations`. If another write lands between the read
and the replace, the one update re-reads the file and re-applies (ADR-0042
decision 10); the walk is fixed, so the hook finds it no longer starts at the
status the file holds and refuses. Nothing is written over the other write.

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


def test_a_hand_edit_between_read_and_write_refuses_the_walk(tmp_path, monkeypatch):
    import pyrite.services.kb_service as kb_service
    from pyrite.services.task_service import TaskService
    from pyrite.utils.atomic_write import FileChanged

    world = build_world(
        tmp_path, kbs=[KBConfig(name=KB, path=tmp_path / "kb", kb_type="generic")], rest=False
    )
    try:
        path = world.add_entry(KB, _task("in_progress"))
        edited = _task("blocked")
        real = kb_service.publish_entry_text
        calls = {"n": 0}

        def racing(target, text, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                Path(target).write_text(edited)  # someone else's write lands first
                raise FileChanged(str(target), edited.encode())
            return real(target, text, **kw)

        monkeypatch.setattr(kb_service, "publish_entry_text", racing)

        with pytest.raises(ValidationError, match="the file's status is 'blocked'"):
            TaskService(world.config, world.db).finish_task("t", KB, walk=True, by="agent:w")
        assert calls["n"] == 1
        assert path.read_text() == edited
    finally:
        world.close()

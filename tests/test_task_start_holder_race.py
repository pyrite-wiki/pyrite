"""Only the holder moves a claimed task on (#1685, fix round 2).

`task start` on a task its caller already holds as `claimed` reads the
holder, then retries the claim compare-and-set from `claimed`. If the claim
changes hands between that read and the write (reset, then claimed by
another agent), the retry must be refused, naming who holds it now, and the
other agent's claim must stay in the file and the index row. Every check
the retry passes matches the holder it read: the index compare-and-set, the
change set's read, and the file write.

The other agent's claim is made at the one point it can race: just before
the retry's `claim_entry`. That injection wraps a service call, so the file
drives the service, not an entry point
(`tests/test_tests_drive_the_surface_not_the_mock.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pyrite.config import KBConfig
from tests._world import build_world

KB = "work"
A = "agent:a"
B = "agent:b"


def _task(status: str, assignee: str) -> str:
    return (
        f"---\ntype: task\nid: t\ntitle: T\nstatus: {status}\nassignee: {assignee}\n---\n\nWork.\n"
    )


@pytest.fixture
def world(tmp_path):
    w = build_world(
        tmp_path, kbs=[KBConfig(name=KB, path=tmp_path / "kb", kb_type="generic")], rest=False
    )
    yield w
    w.close()


def _front(path: Path) -> dict:
    return yaml.safe_load(path.read_text().split("---", 2)[1])


def _row(world) -> dict:
    [row] = world.db.execute_sql(
        "SELECT status, assignee FROM entry WHERE id = 't' AND kb_name = :kb", {"kb": KB}
    )
    return dict(row)


def _service_with_a_claim_taken_before_the_retry(world, path: Path, *, reindex: bool):
    """A TaskService whose retry from `claimed` finds B's claim made first:
    in the file, and in the index too when ``reindex``."""
    from pyrite.services.task_service import TaskService

    svc = TaskService(world.config, world.db)
    real = svc.kb_svc.claim_entry

    def claim_entry(*args, **kwargs):
        if kwargs.get("from_status") == "claimed":
            path.write_text(_task("claimed", B))  # reset, then claimed by B
            if reindex:
                world.index(KB)
        return real(*args, **kwargs)

    svc.kb_svc.claim_entry = claim_entry
    return svc


def test_a_claim_taken_between_the_read_and_the_retry_is_refused_and_kept(world):
    path = world.add_entry(KB, _task("claimed", A))
    svc = _service_with_a_claim_taken_before_the_retry(world, path, reindex=True)

    result = svc.start_task("t", KB, A)

    assert result["claimed"] is False, result
    assert result["current_status"] == "claimed"
    assert B in result["error"], result
    front = _front(path)
    assert (front["status"], front["assignee"]) == ("claimed", B)
    assert not front.get("status_change_log")
    assert _row(world) == {"status": "claimed", "assignee": B}


def test_a_claim_taken_in_the_file_only_is_refused_by_the_file_write(world):
    """The index still names A (not yet re-indexed), so its compare-and-set
    passes; the file write reads B and refuses. The file keeps B's claim."""
    path = world.add_entry(KB, _task("claimed", A))
    svc = _service_with_a_claim_taken_before_the_retry(world, path, reindex=False)

    result = svc.start_task("t", KB, A)

    assert result["claimed"] is False, result
    assert B in result["error"], result
    front = _front(path)
    assert (front["status"], front["assignee"]) == ("claimed", B)
    assert not front.get("status_change_log")
    # The row is put back as it was before the compare-and-set.
    assert _row(world) == {"status": "claimed", "assignee": A}


def test_the_change_set_check_matches_the_holder_too(world):
    """A KB with `landing: review` claims through `_claim_in_change_set`,
    whose check is a read of the entry: it matches the holder as well."""
    from pyrite.services.kb_service import KBService

    path = world.add_entry(KB, _task("claimed", B))
    before = path.read_bytes()

    result = KBService(world.config, world.db)._claim_in_change_set(
        "t", KB, A, "claimed", "in_progress", (), ("claimed", "in_progress"), A
    )

    assert result["claimed"] is False, result
    assert result["current_status"] == "claimed"
    assert B in result["error"], result
    assert path.read_bytes() == before

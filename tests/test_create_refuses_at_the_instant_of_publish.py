"""Outside git a create is exclusive at the instant of publish (#974 cold read, item 2).

Kept apart from ``tests/test_create_writes_through_apply.py``: this test makes
the existence check lie (a monkeypatch on ``entry_writer``), and the suite's
rule (``tests/test_tests_drive_the_surface_not_the_mock.py``) forbids that in a
file that also drives an entry point. The race tests, which patch nothing, stay
beside the other create tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_a_create_outside_git_refuses_at_the_instant_of_publish(tmp_path: Path, monkeypatch):
    """Item 2 of the cold read, the deterministic half: outside git the
    publish used to be `exists()` then `os.replace`, so a file created by
    another writer between the two was overwritten (#391 reopened). Here the
    existence check is made to lie, as a racer makes it lie, and the publish
    must still refuse: exclusivity comes from the filesystem call that
    publishes, not from a check before it."""
    from pyrite.storage import entry_writer

    target = tmp_path / "plain" / "entries" / "twice.md"
    target.parent.mkdir(parents=True)
    target.write_text("first\n", encoding="utf-8")
    monkeypatch.setattr(entry_writer.Path, "exists", lambda self: False)
    monkeypatch.setattr(entry_writer.os.path, "exists", lambda p: False)
    with pytest.raises(FileExistsError):
        entry_writer.create_entry_text(target, "second\n")
    assert target.read_text(encoding="utf-8") == "first\n"
    assert sorted(p.name for p in target.parent.iterdir()) == ["twice.md"], "a temp file was left"

"""A rename refuses when the source changed between the read and the move (#978 cold read, item 3).

Kept apart from ``tests/test_remaining_writers_write_through_apply.py``: this
test edits the source through a hook on the id rewrite (a monkeypatch on
``repository``), and the suite's rule
(``tests/test_tests_drive_the_surface_not_the_mock.py``) forbids that in a file
that also drives an entry point. The world it renames is the same ``SOURCE``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyrite.exceptions import ConflictError
from pyrite.storage import repository
from pyrite.storage.repository import KBRepository
from tests.test_remaining_writers_write_through_apply import LINKER, SOURCE, _kb, _text


def test_a_rename_refuses_when_the_source_changed_under_it(tmp_path: Path, monkeypatch):
    """Item 3 through rename: the source edited between the read and the
    move (here by a hook on the id rewrite, as a second writer would) is
    not removed; the rename is a `ConflictError` and both paths are as
    they were, the edit included. A rename that cannot move its entry
    changes nothing: the linking file is as it was too (#978 delta review,
    round 1: linkers used to be written first, so a refused move left them
    pointing at an id that did not exist)."""
    config, kb_path = _kb(tmp_path, {"notes/old-one.md": SOURCE, "notes/linker.md": LINKER})
    src = kb_path / "notes" / "old-one.md"
    linker = kb_path / "notes" / "linker.md"
    linker_before = linker.read_bytes()
    real = repository._renamed_text

    def edit_then_rename(text, split, new_id):
        src.write_bytes(src.read_bytes() + b"\nEdited meanwhile.\n")
        return real(text, split, new_id)

    monkeypatch.setattr(repository, "_renamed_text", edit_then_rename)
    with pytest.raises(ConflictError):
        KBRepository(config.knowledge_bases[0]).rename("old-one", "new-one")

    assert src.exists() and _text(src).endswith("Edited meanwhile.\n")
    assert not (kb_path / "notes" / "new-one.md").exists()
    assert linker.read_bytes() == linker_before, "a linker was written before the move"

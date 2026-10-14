"""Delete removes every file that states the id, found by the reconcile (#494).

ADR-0038 section 2: "Delete removes the files that hold the id, and only
those." Step 1 (#497) did not walk the KB on a delete by an explicit id, so a
second file stating it -- a hand copy written since the last sync -- stayed on
disk with no row and came back at the next sync (I9).
`DocumentManager.delete_entry` now asks `IndexManager.plan_reconcile`, which
reads only files the index does not know or whose stat moved.
"""

from __future__ import annotations

import pytest

from tests.test_one_reconcile import KB, Env, _note


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.db.close()


def _service(env):
    from pyrite.services.kb_service import KBService

    return KBService(env.config, env.db)


def test_delete_removes_every_file_stating_the_id(env):
    """ADR-0038 §2: delete removes the files that hold the id. A copy the
    index never saw (written by hand, no sync since) is found too."""
    env.write("notes/gamma.md", _note("gamma", "Gamma"))
    env.im.sync_incremental(KB)
    env.write("x.md", _note("gamma", "Copy"))
    env.write("keep.md", _note("keep", "Keep"))
    assert _service(env).delete_entry("gamma", KB)
    assert not (env.root / "notes/gamma.md").exists()
    assert not (env.root / "x.md").exists()
    assert (env.root / "keep.md").exists()
    assert "gamma" not in env.rows()


def test_delete_refuses_when_a_holder_only_derives_the_id(env):
    """One file states the id, another derives it from its title: which one
    the caller means is not certain, so nothing is removed."""
    from pyrite.exceptions import ValidationError

    env.write("notes/gamma.md", _note("gamma", "Gamma"))
    env.im.sync_incremental(KB)
    env.write("other.md", _note(None, "Gamma"))
    before = env.snapshot()
    with pytest.raises(ValidationError) as err:
        _service(env).delete_entry("gamma", KB)
    assert "notes/gamma.md" in str(err.value) and "other.md" in str(err.value)
    assert env.snapshot() == before

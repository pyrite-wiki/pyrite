"""Every reader that walks a KB's files skips what is not a regular file (#990).

A FIFO named ``*.md`` blocks the process that opens it until a writer comes,
which in a KB folder is never. The reconcile skips such a path and says so
(``skipped: not a regular file``); the other walkers over ``list_files``
must not open it either: ``list_entries`` and ``read_id`` (through
``find_file``) since #972, and, this file's red cases, ``search_files`` and
the link rewrite of ``rename_entry``. Each reader runs on a thread with a
bound; a reader that blocks is the failure.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pyrite.config import KBConfig
from pyrite.storage.repository import KBRepository

pytestmark = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes are POSIX-only")

ALPHA = "---\nid: alpha\ntitle: Alpha\ntype: note\n---\n\nSee [[beta]].\n"
BETA = "---\nid: beta\ntitle: Beta\ntype: note\n---\n\nText.\n"


def _kb(tmp_path: Path) -> KBRepository:
    kb_path = tmp_path / "kb"
    (kb_path / "notes").mkdir(parents=True)
    (kb_path / "notes" / "alpha.md").write_text(ALPHA, encoding="utf-8")
    (kb_path / "notes" / "beta.md").write_text(BETA, encoding="utf-8")
    os.mkfifo(kb_path / "notes" / "pipe.md")
    return KBRepository(KBConfig(name="fifo", path=kb_path, kb_type="generic"))


def _bounded(action: Callable[[], Any], seconds: float = 20) -> Any:
    """Run ``action`` on a thread; a reader that blocks on the FIFO never
    returns, and the thread is left behind (daemon) with the failure named."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["result"] = action()
        except BaseException as e:  # reported, not swallowed
            box["error"] = e

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), "the reader blocked on the FIFO"
    if "error" in box:
        raise box["error"]
    return box["result"]


@pytest.mark.control(reason="skipped since #972 round 1 (_is_regular_file in list_entries)")
def test_list_entries(tmp_path: Path):
    repo = _kb(tmp_path)
    ids = _bounded(lambda: sorted(e.id for e, _p in repo.list_entries()))
    assert ids == ["alpha", "beta"]


@pytest.mark.control(reason="skipped since #972 round 1 (_is_regular_file in read_id)")
def test_find_file_by_a_derived_id(tmp_path: Path):
    repo = _kb(tmp_path)
    # No file is named like the id, so every file is read for its id.
    found = _bounded(lambda: repo.find_file("nobody-has-this-id"))
    assert found is None


def test_search_files(tmp_path: Path):
    repo = _kb(tmp_path)
    hits = _bounded(lambda: sorted(e.id for e, _p in repo.search_files("Text")))
    assert hits == ["beta"]


def test_renames_link_rewrite(tmp_path: Path):
    repo = _kb(tmp_path)
    _bounded(lambda: repo.rename("beta", "gamma", update_links=True))
    assert "[[gamma]]" in (repo.path / "notes" / "alpha.md").read_text(encoding="utf-8")
    assert not (repo.path / "notes" / "beta.md").exists()

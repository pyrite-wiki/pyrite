"""``pyrite search -k X`` must not stat any file outside KB X (#1041).

The staleness probe walks and stats every file of every KB. Scoped to one KB
it used to do that work for all of them and filter afterwards, so a search of a
small KB paid for the whole machine. The all-KB warning is for searches
without ``-k``.
"""

import os
import time
from pathlib import Path

import pytest

from pyrite.cli.search_commands import _warn_if_stale
from pyrite.config import PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

ENTRY = "---\ntitle: {t}\ntype: note\n---\n\nBody.\n"


@pytest.fixture
def two_kbs(tmp_path):
    db = PyriteDB(tmp_path / "index.db")
    kbs = []
    for name in ("alpha", "beta"):
        path = tmp_path / name
        path.mkdir()
        for i in range(3):
            (path / f"{name}-{i}.md").write_text(ENTRY.format(t=f"{name} {i}"))
        db.register_kb(name=name, kb_type="generic", path=str(path), description="", source="user")
        kbs.append({"name": name, "path": str(path), "kb_type": "generic", "description": ""})
    config = PyriteConfig(knowledge_bases=[], settings=Settings(index_path=tmp_path / "index.db"))
    config.register_db_kbs(kbs)
    mgr = IndexManager(db, config)
    mgr.index_all()
    yield {"db": db, "config": config, "root": tmp_path, "mgr": mgr}
    db.close()


@pytest.fixture
def stat_log(monkeypatch):
    """Every path stat'ed through os.stat or Path.stat."""
    seen: list[str] = []
    real_os_stat = os.stat
    real_path_stat = Path.stat

    def os_stat(path, *a, **kw):
        seen.append(str(path))
        return real_os_stat(path, *a, **kw)

    def path_stat(self, *a, **kw):
        seen.append(str(self))
        return real_path_stat(self, *a, **kw)

    monkeypatch.setattr(os, "stat", os_stat)
    monkeypatch.setattr(Path, "stat", path_stat)
    return seen


def _touched(seen, root, kb):
    prefix = str(root / kb)
    return [p for p in seen if p.startswith(prefix)]


def test_scoped_search_probe_touches_no_file_outside_the_kb(two_kbs, stat_log):
    _warn_if_stale(two_kbs["config"], two_kbs["db"], "alpha")
    assert _touched(stat_log, two_kbs["root"], "alpha"), "the probe must still check alpha"
    assert _touched(stat_log, two_kbs["root"], "beta") == []


@pytest.mark.control(reason="unscoped search must keep checking every KB; passes before and after")
def test_unscoped_search_probe_still_checks_every_kb(two_kbs, stat_log):
    _warn_if_stale(two_kbs["config"], two_kbs["db"], None)
    assert _touched(stat_log, two_kbs["root"], "alpha")
    assert _touched(stat_log, two_kbs["root"], "beta")


@pytest.mark.control(reason="the reported result was already correct; only the work done changed")
def test_stale_kbs_scoped_reports_only_the_named_kb(two_kbs):
    future = time.time() + 120
    for name in ("alpha", "beta"):
        target = next((two_kbs["root"] / name).glob("*.md"))
        os.utime(target, (future, future))
    svc = KBService(two_kbs["config"], two_kbs["db"])
    assert svc.stale_kbs("alpha") == ["alpha"]
    assert sorted(svc.stale_kbs()) == ["alpha", "beta"]


def test_check_staleness_kb_names_restricts_the_walk(two_kbs, stat_log):
    two_kbs["mgr"].check_staleness(kb_names=["beta"])
    assert _touched(stat_log, two_kbs["root"], "alpha") == []
    assert _touched(stat_log, two_kbs["root"], "beta")

"""No after_save hook writes another entry (ADR-0042 decision 4).

kb/design.md principle 4: what can be computed from the files is derived, not
written; a hook may refuse a write, it may not change one. The parent rollup
was the after_save hook that wrote another entry's file (a parent's
`status: done`, cascading to the grandparent). It is deleted; a parent's
completion is derived (tests/test_derived_task_completion.py).

The check runs EVERY after_save hook Pyrite has -- the core hooks on
KBService's runner and every installed plugin's, whatever KB type it is
registered for -- on entries of several types that have just been saved,
with the context KBService builds, and reports any hook after which another
file in the KB changed. A planted hook that writes a parent proves the check
catches what it is for; without it, an empty hook list would pass against
nothing.

Limit: the KB is `generic` and the saved entries are a task, a note and a
cascade actor. A plugin hook that writes only for its own KB type or its own
entry types (say, a cascade-only writer that checks `kb_type == "cascade"`,
or a social hook acting on a post) returns early here and would go unnoticed.
Each hook is called directly, so the KB-type filter of `get_hooks_for_kb` is
not what hides it; the hook's own guard is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.plugins.registry import get_registry
from pyrite.services.kb_service import KBService
from pyrite.services.task_service import TaskService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

KB = "work"


def _task(tid: str, status: str, parent: str = "") -> str:
    parent_line = f"parent: {parent}\n" if parent else ""
    return (
        f"---\nid: {tid}\ntitle: Task {tid}\ntype: task\nstatus: {status}\n"
        f"{parent_line}---\n\nWork for {tid}.\n"
    )


FILES = {
    "tasks/gp.md": _task("gp", "in_progress"),
    "tasks/p.md": _task("p", "in_progress", "gp"),
    "tasks/c1.md": _task("c1", "done", "p"),
    # Saved as resolved: after_save runs after persistence, so the file and
    # the index already say done, exactly as KBService.update_entry leaves
    # them. (A first version changed it only in memory; the rollup read the
    # index, saw nothing resolved, and the check passed against it.)
    "tasks/c2.md": _task("c2", "done", "p"),
    "notes/n1.md": "---\nid: n1\ntitle: A note\ntype: note\n---\n\nA note.\n",
    "actors/a1.md": "---\nid: a1\ntitle: An actor\ntype: actor\n---\n\nAn actor.\n",
}


@pytest.fixture
def env(tmp_path):
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text("name: work\nkb_type: generic\n", encoding="utf-8")
    for name, text in FILES.items():
        (kb_path / name).parent.mkdir(parents=True, exist_ok=True)
        (kb_path / name).write_text(text, encoding="utf-8")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)
    IndexManager(db, config).index_all()
    yield {"config": config, "db": db, "kb_path": kb_path}
    db.close()


def _files(kb_path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(kb_path)): p.read_bytes() for p in kb_path.rglob("*.md")}


def _writers(env, hooks, saved_id: str) -> list[str]:
    """Run each hook on the saved entry; name every hook after which a file
    other than the saved entry's changed."""
    config, db, kb_path = env["config"], env["db"], env["kb_path"]
    svc = KBService(config, db)
    kb_config = config.get_kb(KB)
    entry = KBRepository(kb_config).load(saved_id)
    assert entry is not None, saved_id
    ctx = svc._hook_ctx(KB, kb_config, "update", {"old_status": "in_progress"})

    def others() -> dict[str, bytes]:
        return {k: v for k, v in _files(kb_path).items() if Path(k).stem != saved_id}

    writers = []
    for hook in hooks:
        before = others()
        try:
            hook(entry, ctx)
        except Exception:  # an after_save failure is logged by the runner, never a write
            pass
        if others() != before:
            writers.append(getattr(hook, "__name__", repr(hook)))
    return writers


def _installed_after_save_hooks(env) -> list:
    svc = KBService(env["config"], env["db"])
    hooks = list(svc.hook_runner.core_hooks("after_save"))
    hooks += list(get_registry().get_all_hooks().get("after_save", []))
    return hooks


def _planted_rollup(env):
    """A hook that does what the deleted rollup did: write the parent."""

    def planted_rollup(entry, context):
        if getattr(entry, "parent", ""):
            KBService(env["config"], env["db"]).update_entry(entry.parent, KB, title="Rolled up")

    return planted_rollup


@pytest.mark.control(reason="proves the check catches a writer; independent of the fix")
def test_the_check_catches_a_hook_that_writes_another_entry(env):
    assert _writers(env, [_planted_rollup(env)], "c2") == ["planted_rollup"]


_NON_TASK = pytest.mark.control(
    reason="no hook wrote another entry on a non-task save before either"
)


@pytest.mark.parametrize(
    "saved_id", ["c2", pytest.param("n1", marks=_NON_TASK), pytest.param("a1", marks=_NON_TASK)]
)
def test_every_installed_after_save_hook_leaves_other_entries_alone(env, saved_id):
    # The extensions that register after_save hooks today; CI installs them.
    pytest.importorskip("pyrite_cascade")
    pytest.importorskip("pyrite_social")
    hooks = _installed_after_save_hooks(env)
    names = {getattr(h, "__name__", "") for h in hooks}
    assert hooks, "no after_save hooks found: the check would pass against nothing"
    assert {"_on_actor_saved", "after_save_update_counts"} <= names, names

    assert _writers(env, hooks, saved_id) == []


@pytest.mark.parametrize(
    "planted",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.control(reason="proves the end-to-end assertion catches a writer"),
        ),
    ],
    ids=["installed_hooks", "with_planted_rollup"],
)
def test_resolving_the_last_child_through_the_write_path(env, planted):
    """End to end through KBService's own runner. The planted case proves the
    assertion would catch a hook that writes the parent."""
    config, db, kb_path = env["config"], env["db"], env["kb_path"]
    (kb_path / "tasks" / "c2.md").write_text(_task("c2", "in_progress", "p"), encoding="utf-8")
    IndexManager(db, config).index_all()
    kb_svc = KBService(config, db)
    if planted:
        kb_svc.hook_runner.register_core_hook("after_save", _planted_rollup(env))
    parent_before = (kb_path / "tasks" / "p.md").read_bytes()

    TaskService(config, db, kb_svc=kb_svc).update_task("c2", KB, status="done")

    assert ((kb_path / "tasks" / "p.md").read_bytes() != parent_before) is planted
    if not planted:
        derived = TaskService(config, db).derived_completion(KB)
        assert derived["p"]["effective_status"] == "done"
        assert derived["gp"]["effective_status"] == "done"


def test_the_rollup_is_deleted_not_disabled():
    import pyrite.services.task_service as task_mod

    assert not hasattr(task_mod, "_parent_rollup")
    assert not hasattr(task_mod.TaskService, "rollup_parent")

"""No index row is retired on the evidence of a walk or a read that could not tell (#1546).

``kb/design.md`` principle 5: the index is a cache and the files decide, so a
row may be retired only when the files say its entry is gone. A folder the
process could not list says nothing about the files in it, and neither does a
file it could not stat or read (an ``OSError`` other than "absent"). #1551
made that true for a whole KB (``KB_UNREADABLE``); this is the same rule one
level down:

- a path under a folder the walk could not list keeps its row, and so does a
  path it could not stat or read; the row is neither rewritten nor removed,
  on every reconcile entry point (``RECONCILES``, checked against every
  ``IndexManager`` method that calls ``reconcile_kb``);
- every surface that reconciles or checks names what it could not reach:
  ``unreadable_folders`` rows on the local CLI, a count over MCP and REST
  (never a path, never the OS's words); and a sync, build or reindex that
  could not reach something does not exit 0;
- ``index health`` is unhealthy and names the folder;
- a folder the walk would never list anyway (under a hidden or ``_templates``
  folder) is not reported and changes no exit code;
- an in-KB link into a folder that cannot be searched is unreadable, never a
  dangling link to a missing file (maintainer, 2026-11-17);
- a row kept on faith never beats a file read now.

A file that reads and does not parse still loses its row:
``tests/test_one_reconcile.py::test_unparseable_file_is_reported_and_its_row_retired``.

Medium: real files and modes, the real ``IndexManager`` over a real database,
the CLI in-process, ``PyriteMCPServer._dispatch_tool`` and the REST app under
a ``TestClient``. Mode 000 does not stop root: each case skips for a user it
does not stop, as ``tests/test_an_unreadable_kb_answers_kb_unreadable.py``
does.
"""

from __future__ import annotations

import ast
import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from tests._world import build_world
from tests.test_one_reconcile import KB, PATHS, Env, _note

#: Pyrite's fixed text for files the OS would not let it read: the
#: ``KB_UNREADABLE`` message, written out here, not read from the code.
FIXED = (
    "Pyrite could not read this knowledge base's files: the operating "
    "system refused (for example, a folder or file Pyrite may not open)."
)

COLLECTION = "title: C\n"

#: The permission each mode takes away, as ``os.access`` asks it.
_TAKES = {0o000: os.R_OK, 0o100: os.R_OK, 0o600: os.X_OK}


def _shut(shut: list[Path], path: Path, mode: int) -> None:
    """chmod ``path`` to ``mode`` (restored at teardown), or skip for a user
    whom that mode does not stop."""
    path.chmod(mode)
    shut.append(path)
    if os.access(path, _TAKES[mode]):
        pytest.skip(
            f"running as a user (uid {os.getuid()}) whom mode {mode:03o} does not stop: "
            "the could-not-tell case is not entered"
        )


def _restore(shut: list[Path]) -> None:
    for path in reversed(shut):
        path.chmod(0o755 if path.is_dir() else 0o644)


def _seed(root: Path) -> None:
    """``top`` at the root; ``inner``, a nested ``d`` and a collection under ``sub``."""
    (root / "sub" / "deeper").mkdir(parents=True)
    (root / "sub" / "c").mkdir()
    (root / "top.md").write_text(_note("top", "Top"))
    (root / "sub" / "inner.md").write_text(_note("inner", "Inner"))
    (root / "sub" / "deeper" / "d.md").write_text(_note("d", "D"))
    (root / "sub" / "c" / "__collection.yaml").write_text(COLLECTION)
    # Folders the walk never lists: shut below, never reported.
    (root / ".hidden" / "locked").mkdir(parents=True)
    (root / "_templates" / "locked").mkdir(parents=True)


#: Every row the seeded KB holds, written by hand.
SEEDED_IDS = {"top", "inner", "d", "collection-c"}


# Each shape: what to shut, and what the reconcile must name: folders
# (KB-relative, sorted by path whatever order the filesystem lists them in)
# and files (KB-relative; reported with their absolute path).
def _folder_000(e, shut):
    _shut(shut, e.root / "sub", 0o000)


def _folder_100(e, shut):
    # Searchable, not listable: each file's stat would work if the walk knew
    # its name, so only the walk can tell that it could not see them.
    _shut(shut, e.root / "sub", 0o100)


def _folder_600(e, shut):
    # Listable, not searchable: the names are listed, every stat fails, and
    # the folders inside cannot be listed.
    _shut(shut, e.root / "sub", 0o600)


def _file_000_moved(e, shut):
    inner = e.root / "sub" / "inner.md"
    os.utime(inner, (1_000_000_000, 1_000_000_000))
    _shut(shut, inner, 0o000)


def _hidden_and_templates(e, shut):
    _shut(shut, e.root / ".hidden" / "locked", 0o000)
    _shut(shut, e.root / "_templates" / "locked", 0o000)


SHAPES: dict[str, tuple[Callable, list[str], list[str]]] = {
    "folder-000": (_folder_000, ["sub"], []),
    "folder-100": (_folder_100, ["sub"], []),
    "folder-600": (_folder_600, ["sub/c", "sub/deeper"], ["sub/inner.md"]),
    "file-000-mtime-moved": (_file_000_moved, [], ["sub/inner.md"]),
    "hidden-and-templates": (_hidden_and_templates, [], []),
}

_CONTROL = "a folder the walk never lists was never reported and kept no row on dev either"


def _shapes(names=SHAPES):
    return [
        pytest.param(n, marks=pytest.mark.control(reason=_CONTROL))
        if n == "hidden-and-templates"
        else n
        for n in names
    ]


def _attribution(e: Env, report: dict | None = None):
    from pyrite.services.git_service import GitService

    if report is None:
        return e.im.index_with_attribution(KB, GitService())
    return e.im.index_with_attribution(KB, GitService(), report=report)


#: Every reconcile entry point: ``tests/test_one_reconcile.py``'s ``PATHS``
#: plus ``index_with_attribution``. ``test_every_reconcile_entry_point_is_here``
#: fails when ``IndexManager`` gains a fifth.
RECONCILES: dict[str, Callable[[Env], object]] = {
    **PATHS,
    "index_with_attribution": _attribution,
}


def _reported(e: Env, name: str) -> dict:
    """The reconcile's result dict through entry point ``name``: the syncs
    return it; a build fills the ``report`` it is given."""
    if name == "index_kb":
        report: dict = {}
        e.im.index_kb(KB, report=report)
        return report
    if name == "index_with_attribution":
        report = {}
        _attribution(e, report)
        return report
    return RECONCILES[name](e)


@pytest.fixture
def kb(tmp_path):
    e = Env(tmp_path)
    _seed(e.root)
    shut: list[Path] = []
    e.shut = shut
    try:
        yield e
    finally:
        _restore(shut)
        e.db.close()


def _rows(e: Env) -> dict[str, tuple[str, str]]:
    """id -> (KB-relative path, content_hash) of every row."""
    return {
        i: (Path(r["file_path"]).relative_to(e.root).as_posix(), r["content_hash"])
        for i, r in e.im._load_indexed_state(KB).items()
    }


# ---------------------------------------------------------------------------
# The rule, on every reconcile entry point
# ---------------------------------------------------------------------------


@pytest.mark.control(reason="a structural pin: dev already has exactly these four entry points")
def test_every_reconcile_entry_point_is_here():
    """``RECONCILES`` names every ``IndexManager`` method whose body calls
    ``self.reconcile_kb``, so a fifth entry point cannot be added without
    these tests covering it."""
    import pyrite.storage.index as index_module

    tree = ast.parse(Path(index_module.__file__).read_text(encoding="utf-8"))
    (cls,) = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "IndexManager"]
    callers = set()
    for fn in cls.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name == "reconcile_kb":
            continue
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "reconcile_kb"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"
            ):
                callers.add(fn.name)
    assert callers == set(RECONCILES)


@pytest.mark.parametrize("name", RECONCILES)
@pytest.mark.parametrize("shape", _shapes())
def test_the_rows_it_could_not_reach_are_kept(kb, shape, name):
    RECONCILES[name](kb)
    before = _rows(kb)
    assert set(before) == SEEDED_IDS
    SHAPES[shape][0](kb, kb.shut)
    RECONCILES[name](kb)
    # The row set and each row's path and content_hash, unchanged.
    assert _rows(kb) == before


def _named_cells():
    """Shape x entry point. The hidden-and-templates cells of the syncs are
    controls; a build's are not, since dev's builds report nothing at all."""
    return [
        pytest.param(shape, name, marks=pytest.mark.control(reason=_CONTROL))
        if shape == "hidden-and-templates" and name in ("sync_incremental", "sync_kb")
        else pytest.param(shape, name)
        for shape in SHAPES
        for name in RECONCILES
    ]


@pytest.mark.parametrize(("shape", "name"), _named_cells())
def test_the_reconcile_names_what_it_could_not_reach(kb, shape, name):
    RECONCILES[name](kb)
    shut, folders, files = SHAPES[shape]
    shut(kb, kb.shut)
    result = _reported(kb, name)
    assert result.get("unreadable_folders", []) == [
        {"kb": KB, "path": folder, "error": FIXED} for folder in folders
    ]
    assert sorted(row["path"] for row in result.get("unreadable_files", [])) == [
        str(kb.root / f) for f in files
    ]
    if name != "index_kb" and name != "index_with_attribution":
        assert result["removed"] == 0


@pytest.mark.parametrize("shape", _shapes())
def test_readable_again_the_next_sync_reports_nothing(kb, shape):
    kb.im.sync_incremental(KB)
    before = _rows(kb)
    SHAPES[shape][0](kb, kb.shut)
    kb.im.sync_incremental(KB)
    _restore(kb.shut)
    kb.shut.clear()
    again = kb.im.sync_incremental(KB)
    assert (again["added"], again["updated"], again["removed"]) == (0, 0, 0)
    assert _rows(kb) == before


def test_a_kept_row_never_beats_a_file_read_now(kb):
    """``a-locked/x.md`` sorts first, but its row could only be kept on
    faith: the file read now (``z.md``) takes the id's row. The kept row is
    then not kept, so its path is no duplicate (and never reaches a network
    caller that way); its folder is named (round 2 of #1647)."""
    (kb.root / "a-locked").mkdir()
    (kb.root / "a-locked" / "x.md").write_text(_note("x", "X"))
    kb.im.sync_incremental(KB)
    _shut(kb.shut, kb.root / "a-locked", 0o000)
    (kb.root / "z.md").write_text(_note("x", "X again"))
    result = kb.im.sync_incremental(KB)
    assert _rows(kb)["x"][0] == "z.md"
    assert [d for d in result["duplicates"] if d["id"] == "x"] == []
    assert [f["path"] for f in result["unreadable_folders"]] == ["a-locked"]


# The other half of the rule: a row is retired exactly when the files say the
# file is gone. The walk's listing and ``KBFiles.present`` are the one answer
# (round 2 of #1647, from the cold read of 3784c390).


@pytest.mark.parametrize("name", RECONCILES)
def test_a_file_gone_from_a_folder_it_can_list_is_retired(kb, name):
    """At mode 600 the folder's listing works and ``gone.md`` is not in it:
    the files say it is gone, so its row is retired (as on dev), and it is
    named nowhere. ``inner.md``, listed but not stat-able, keeps its row."""
    (kb.root / "sub" / "gone.md").write_text(_note("gone", "Gone"))
    RECONCILES[name](kb)
    assert "gone" in _rows(kb)
    (kb.root / "sub" / "gone.md").unlink()
    _shut(kb.shut, kb.root / "sub", 0o600)
    result = _reported(kb, name)
    assert set(_rows(kb)) == SEEDED_IDS
    assert sorted(row["path"] for row in result["unreadable_files"]) == [
        str(kb.root / "sub" / "inner.md")
    ]
    if name in ("sync_incremental", "sync_kb"):
        assert result["removed"] == 1


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is not reliable on Windows")
@pytest.mark.parametrize("name", RECONCILES)
def test_a_file_replaced_by_a_link_loop_is_retired(kb, name):
    """A link that names itself: ``KBFiles.present`` says absent (ELOOP), so
    the row is retired (as on dev) and the sync is not counted incomplete; the
    loop stays reported under ``malformed`` (#1119)."""
    RECONCILES[name](kb)
    inner = kb.root / "sub" / "inner.md"
    inner.unlink()
    inner.symlink_to("inner.md")
    result = _reported(kb, name)
    assert set(_rows(kb)) == SEEDED_IDS - {"inner"}
    assert result["unreadable_files"] == []
    assert result["unreadable_folders"] == []
    assert str(inner) in {row["path"] for row in result["malformed"]}


# A link whose TARGET cannot be stat-ed is a link the walk never enters, not a
# folder it could not list: no row can lie under it, so it is never named and
# never makes a sync incomplete. Dev answered exit 0 / healthy for each
# (round 3 of #1647, from the delta cold read of 75dcd447).


def _self_link(root: Path, shut: list[Path], outside: Path) -> None:
    (root / "loop").symlink_to("loop")


def _link_into_a_folder_it_cannot_search(root: Path, shut: list[Path], outside: Path) -> None:
    (outside / "x").mkdir(parents=True)
    (root / "ext").symlink_to(outside / "x")
    _shut(shut, outside, 0o000)


def _link_through_a_file(root: Path, shut: list[Path], outside: Path) -> None:
    (root / "thru").symlink_to("top.md/x")


LINKS = {
    "self-link": _self_link,
    "link-into-an-unsearchable-folder": _link_into_a_folder_it_cannot_search,
    "link-through-a-file": _link_through_a_file,
}


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is not reliable on Windows")
@pytest.mark.parametrize("name", RECONCILES)
@pytest.mark.parametrize("link", LINKS)
def test_a_link_whose_target_cannot_be_stat_ed_is_not_an_unreadable_folder(
    kb, tmp_path, link, name
):
    from pyrite.services.index_health import health_verdict

    RECONCILES[name](kb)
    LINKS[link](kb.root, kb.shut, tmp_path / "outside")
    result = _reported(kb, name)
    assert result["unreadable_folders"] == []
    assert result["unreadable_files"] == []
    assert set(_rows(kb)) == SEEDED_IDS
    health = kb.im.check_health(KB)
    assert health["unreadable_folders"] == []
    assert health_verdict(health)["is_unhealthy"] is False


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is not reliable on Windows")
@pytest.mark.parametrize("link", LINKS)
def test_a_link_whose_target_cannot_be_stat_ed_syncs_whole_and_healthy(world, tmp_path, link):
    """Through the CLI: ``index sync`` exits 0 and ``index health`` is not
    unhealthy (exit 0), with ``unreadable_folders`` empty, as on dev."""
    LINKS[link](world.root_kb, world.shut, tmp_path / "outside")
    sync = world.cli("index", "sync", "-k", KB, "--no-embed")
    assert sync.exit_code == 0, sync.output
    assert "Unreadable folders" not in sync.output
    health = world.cli("index", "health", "-k", KB, "--format", "json")
    assert health.exit_code == 0, health.output
    body = json.loads(health.output)
    body = body.get("data", body)
    assert body["unreadable_folders"] == []


def test_a_name_the_os_will_not_type_is_named_as_a_folder(kb, monkeypatch):
    """A folder whose listing gives no type, and whose type the OS will not
    tell (DT_UNKNOWN in a folder this process may not search): the walk
    cannot know it is a folder, so it keeps the rows under it and names it,
    rather than reading it as a file and the rows as gone."""
    import pyrite.storage.repository as repository

    kb.im.sync_incremental(KB)
    real_scandir = os.scandir
    untyped = str(kb.root / "sub" / "c")

    class Untyped:
        def __init__(self, item):
            self._item = item
            self.name = item.name
            self.path = item.path

        def is_dir(self, **kw):
            raise PermissionError(13, "Permission denied")

        def is_symlink(self):
            raise PermissionError(13, "Permission denied")

    class Listing:
        def __init__(self, top):
            self._it = real_scandir(top)

        def __enter__(self):
            return (Untyped(i) if i.path == untyped else i for i in self._it)

        def __exit__(self, *exc):
            self._it.close()

    monkeypatch.setattr(repository.os, "scandir", Listing)
    result = kb.im.sync_incremental(KB)
    monkeypatch.undo()
    assert set(_rows(kb)) == SEEDED_IDS
    assert result["unreadable_folders"] == [{"kb": KB, "path": "sub/c", "error": FIXED}]
    assert result["removed"] == 0


def test_a_folder_named_dot_md_the_os_will_not_type_is_named_as_a_folder(kb, monkeypatch):
    """A folder named *.md whose listing gives DT_UNKNOWN keeps the rows under it
    and is named under unreadable_folders (#1720)."""
    import pyrite.storage.repository as repository

    folder_md = kb.root / "sub" / "folder.md"
    folder_md.mkdir(parents=True, exist_ok=True)
    (folder_md / "nested.md").write_text(_note("nested", "Nested"))

    kb.im.sync_incremental(KB)
    assert "nested" in _rows(kb)

    real_scandir = os.scandir
    untyped = str(folder_md)

    class Untyped:
        def __init__(self, item):
            self._item = item
            self.name = item.name
            self.path = item.path

        def is_dir(self, **kw):
            raise PermissionError(13, "Permission denied")

        def is_symlink(self):
            raise PermissionError(13, "Permission denied")

    class Listing:
        def __init__(self, top):
            self._it = real_scandir(top)

        def __enter__(self):
            return (Untyped(i) if i.path == untyped else i for i in self._it)

        def __exit__(self, *exc):
            self._it.close()

    monkeypatch.setattr(repository.os, "scandir", Listing)
    result = kb.im.sync_incremental(KB)
    monkeypatch.undo()
    assert "nested" in set(_rows(kb))
    assert {"kb": KB, "path": "sub/folder.md", "error": FIXED} in result["unreadable_folders"]
    assert result["removed"] == 0


# ---------------------------------------------------------------------------
# The surfaces: CLI, health, MCP and REST
# ---------------------------------------------------------------------------


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "k"
    root.mkdir()
    _seed(root)
    w = build_world(tmp_path / "w", kbs=[KBConfig(name=KB, path=root, kb_type="generic")])
    w.root_kb = root
    w.shut = []
    try:
        yield w
    finally:
        _restore(w.shut)
        w.close()


def _world_shut(world, shape: str) -> None:
    class _E:  # the shapes take anything with a `root`
        root = world.root_kb

    SHAPES[shape][0](_E, world.shut)


def _world_rows(world) -> set[str]:
    return {
        r["id"] for r in world.db.execute_sql("SELECT id FROM entry WHERE kb_name = :k", {"k": KB})
    }


@pytest.mark.parametrize("shape", _shapes(["folder-000", "file-000-mtime-moved"]))
def test_cli_index_sync_names_it_and_exits_3(world, shape):
    _world_shut(world, shape)
    result = world.cli("index", "sync", "-k", KB, "--no-embed")
    assert result.exit_code == 3, result.output
    assert _world_rows(world) == SEEDED_IDS
    if shape == "folder-000":
        assert "Unreadable folders: 1" in result.output
        assert "k: sub" in result.output
    else:
        assert "Malformed: 1" in result.output
        assert "sub/inner.md" in result.output


@pytest.mark.parametrize("shape", ["folder-000", "file-000-mtime-moved"])
def test_cli_index_build_names_it_and_exits_3(world, shape):
    _world_shut(world, shape)
    result = world.cli("index", "build", "-k", KB, "--no-embed")
    assert result.exit_code == 3, result.output
    assert _world_rows(world) == SEEDED_IDS
    if shape == "folder-000":
        assert "k: sub" in result.output
    else:
        assert "sub/inner.md" in result.output


def test_cli_kb_reindex_names_it_and_exits_3(world):
    _world_shut(world, "folder-000")
    result = world.cli("kb", "reindex", "-k", KB, "--format", "json")
    assert result.exit_code == 3, result.output
    body = json.loads(result.output)
    assert body["unreadable_folders"] == [{"kb": KB, "path": "sub", "error": FIXED}]
    assert body["removed"] == 0
    assert _world_rows(world) == SEEDED_IDS


@pytest.mark.parametrize("shape", _shapes(["folder-000", "folder-100", "hidden-and-templates"]))
def test_health_after_a_sync_is_unhealthy_and_names_the_folder(world, shape):
    _world_shut(world, shape)
    world.cli("index", "sync", "-k", KB, "--no-embed")
    result = world.cli("index", "health", "-k", KB, "--format", "json")
    body = json.loads(result.output)
    body = body.get("data", body)
    if shape == "hidden-and-templates":
        assert result.exit_code == 0, result.output
        assert body.get("unreadable_folders", []) == []
        return
    assert result.exit_code == 1, result.output
    assert body["status"] == "unhealthy"
    assert body["unreadable_folders"] == [{"kb": KB, "path": "sub", "error": FIXED}]


def test_mcp_and_rest_get_a_count_never_a_path(world):
    """Over the network the folders are a count: no path and no OS text
    (ADR-0052 N3; the maintainer's #1552 decision followed for this key)."""
    _world_shut(world, "folder-000")
    where = str(world.root_kb)
    mcp = world.mcp()

    answers = {
        "mcp kb_index_sync": mcp._dispatch_tool("kb_index_sync", {"kb_name": KB}),
        "mcp kb_registry_reindex": mcp._dispatch_tool("kb_registry_reindex", {"name": KB}),
        "rest sync": world.client.post("/api/index/sync", params={"wait": "true"}).json(),
        "rest reindex": world.client.post(f"/api/kbs/{KB}/reindex").json(),
    }
    for surface, body in answers.items():
        assert body.get("unreadable_folders") == 1, (surface, body)
        assert body.get("removed") == 0, (surface, body)
        text = json.dumps(body)
        assert where not in text, (surface, body)
        assert "Permission denied" not in text, (surface, body)
        assert "Errno" not in text, (surface, body)
    assert _world_rows(world) == SEEDED_IDS


def test_network_health_is_unhealthy_with_a_count_never_a_path(world):
    """REST and MCP ``kb health`` (shaped in ``KBRegistryService.health_kb``):
    unhealthy, and the folders as a count, as sync and reindex give them."""
    _world_shut(world, "folder-000")
    world.cli("index", "sync", "-k", KB, "--no-embed")
    where = str(world.root_kb)
    answers = {
        "mcp kb_registry_health": world.mcp()._dispatch_tool("kb_registry_health", {"name": KB}),
        "rest kb health": world.client.get(f"/api/kbs/{KB}/health").json(),
    }
    for surface, body in answers.items():
        assert body.get("healthy") is False, (surface, body)
        assert body.get("unreadable_folders") == 1, (surface, body)
        text = json.dumps(body)
        assert f"{where}/sub" not in text, (surface, body)
        assert "Permission denied" not in text, (surface, body)


def test_an_alias_into_a_shut_folder_is_unreadable_not_dangling(tmp_path):
    """The maintainer's line (2026-11-17): an in-KB link into a folder that
    cannot be searched is reported as unreadable, not as a dangling link to a
    missing file, and after a sync ``index health`` is unhealthy, exit 1."""
    root = tmp_path / "k"
    (root / "locked").mkdir(parents=True)
    (root / "top.md").write_text(_note("top", "Top"))
    (root / "locked" / "b.md").write_text(_note("b", "B"))
    (root / "a.md").symlink_to("locked/b.md")
    world = build_world(
        tmp_path / "w", kbs=[KBConfig(name=KB, path=root, kb_type="generic")], rest=False
    )
    shut: list[Path] = []
    try:
        _shut(shut, root / "locked", 0o000)
        sync = world.cli("index", "sync", "-k", KB, "--no-embed")
        assert "Dangling" not in sync.output, sync.output
        assert "missing" not in sync.output, sync.output
        assert sync.exit_code == 3, sync.output
        assert _world_rows(world) == {"top", "b"}

        health = world.cli("index", "health", "-k", KB, "--format", "json")
        assert health.exit_code == 1, health.output
        body = json.loads(health.output)
        body = body.get("data", body)
        assert body["status"] == "unhealthy"
        assert body["dangling_links"] == []
        assert body["unreadable_folders"] == [{"kb": KB, "path": "locked", "error": FIXED}]
        assert str(root / "a.md") in {row["path"] for row in body["unreadable_files"]}
    finally:
        _restore(shut)
        world.close()

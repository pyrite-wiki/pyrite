"""An in-KB link to an entry file is an alias, indexed once (#1262).

The property: one entry per real file. A walked path that is a symbolic link
whose resolved target is another walked entry file of the same KB is an
*alias* of that entry. It is indexed and counted once, at the real file, and
reported as an alias (``aliases: [{kb, path, target}]``), not as a duplicate
id and not as malformed. ``index sync``, ``index health`` and ``find_file``
agree. The link is the user's file: nothing here writes or removes it
(standard ``pyrite-is-a-guest-in-state-it-does-not-own``).

Before this, ``sub/alias.md -> ../a.md`` made ``index sync`` report
"Duplicate ids: 1", the first path by sort order won the row (so the alias
could be indexed instead of the real file), and ``find_file`` could return the
alias.

Controls, as before: a link to a file the walk skips is the only walked path
and is indexed once; a link that leaves the KB is skipped as outside; a
dangling or looping link is malformed; a hard link is two names for one inode
with no way to tell which is "real", so both are skipped (nlink > 1), as the
reconcile has done since #637.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.index_health import health_verdict
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

pytestmark = pytest.mark.skipif(os.name == "nt", reason="symlinks are not reliable on Windows")

KB = "k"
TARGET = "t/target.md"  # the real file; every alias below sorts before it


def _note(id_: str | None, title: str, body: str = "body") -> str:
    id_line = f"id: {id_}\n" if id_ else ""
    return f"---\n{id_line}type: note\ntitle: {title}\n---\n\n{body}\n"


class Env:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path / KB
        self.root.mkdir(parents=True, exist_ok=True)
        self.outside = tmp_path / "outside"
        self.outside.mkdir()
        self.config = PyriteConfig(
            knowledge_bases=[KBConfig(name=KB, path=self.root, kb_type="generic")],
            settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
        )
        self.db = PyriteDB(tmp_path / "index.db")
        self.im = IndexManager(self.db, self.config)
        self.kb_config = self.config.get_kb(KB)
        self.repo = KBRepository(self.kb_config)

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def link(self, rel: str, to: str, *, absolute: bool) -> Path:
        """Make ``rel`` a symbolic link to the KB path ``to``."""
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        dest = self.root / to
        p.symlink_to(dest if absolute else os.path.relpath(dest, p.parent))
        return p

    def rows(self) -> dict[str, str]:
        """id -> KB-relative path of its row."""
        return {
            i: Path(r["file_path"]).relative_to(self.root).as_posix()
            for i, r in self.im._load_indexed_state(KB).items()
        }

    def links(self) -> dict[str, str]:
        """Every symbolic link under the root -> what it says (its bytes)."""
        return {
            p.relative_to(self.root).as_posix(): os.readlink(p)
            for p in sorted(self.root.rglob("*"))
            if p.is_symlink()
        }


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.db.close()


# Each shape: the links to make, in order, as (link, what it points at).
# Every link name sorts before TARGET, so a walk that let the first path win
# indexed the alias, not the real file.
SHAPES = {
    "same folder": [("t/alias.md", TARGET)],
    "another folder": [("o/alias.md", TARGET)],
    "nested folder": [("n/deep/er/alias.md", TARGET)],
    "link to a link": [("o/mid.md", TARGET), ("n/deep/alias.md", "o/mid.md")],
}


def _aliases(shape: str) -> list[dict[str, str]]:
    """What the report must list: every link in the shape, aliasing TARGET
    (a chain is reported at the file it ends at), sorted by path."""
    return sorted(
        ({"kb": KB, "path": link, "target": TARGET} for link, _ in SHAPES[shape]),
        key=lambda a: a["path"],
    )


def _build(env: Env, shape: str, absolute: bool) -> None:
    env.write(TARGET, _note("x", "X"))
    env.write("b.md", _note("b", "B", "see [[x]]"))
    for link, to in SHAPES[shape]:
        env.link(link, to, absolute=absolute)


@pytest.mark.parametrize("absolute", [False, True], ids=["relative", "absolute"])
@pytest.mark.parametrize("shape", list(SHAPES))
def test_sync_indexes_the_entry_once_at_its_real_file_and_lists_the_alias(env, shape, absolute):
    _build(env, shape, absolute)
    links_before = env.links()

    result = env.im.sync_incremental(KB)

    assert result["duplicates"] == []
    assert result["malformed"] == []
    assert result["skipped"] == []
    assert result["aliases"] == _aliases(shape)
    assert result["added"] == 2  # x and b, each once
    assert env.rows() == {"x": TARGET, "b": "b.md"}
    # A second sync with nothing changed reports the same and moves nothing.
    again = env.im.sync_incremental(KB)
    assert (again["added"], again["updated"], again["removed"]) == (0, 0, 0)
    assert again["aliases"] == _aliases(shape)
    # The user's links are untouched.
    assert env.links() == links_before


@pytest.mark.parametrize("absolute", [False, True], ids=["relative", "absolute"])
@pytest.mark.parametrize("shape", list(SHAPES))
def test_health_agrees_with_sync(env, shape, absolute):
    _build(env, shape, absolute)
    env.im.sync_incremental(KB)

    health = env.im.check_health(kb_name=KB)

    assert health["duplicates"] == []
    assert health["unindexed_files"] == []
    assert health["unreadable_files"] == []
    assert health["malformed_frontmatter"] == []
    assert health["aliases"] == _aliases(shape)
    assert health_verdict(health)["status"] == "healthy"


@pytest.mark.parametrize("absolute", [False, True], ids=["relative", "absolute"])
@pytest.mark.parametrize("shape", list(SHAPES))
def test_the_walk_lists_the_real_file_and_not_the_alias(env, shape, absolute):
    _build(env, shape, absolute)

    listed = sorted(p.relative_to(env.root).as_posix() for p in env.repo.list_files())

    assert listed == ["b.md", TARGET]
    # The whole-KB scan behind find_file, files_to_delete and a get miss.
    assert list(env.repo._files_holding("x")) == [env.root / TARGET]


@pytest.mark.parametrize("absolute", [False, True], ids=["relative", "absolute"])
def test_find_file_never_picks_the_alias_over_the_target(env, absolute):
    """The alias is named like the id and sits at the root, so a lookup that
    tries filenames first met it before the real file."""
    env.write(TARGET, _note("x", "X"))
    env.link("x.md", TARGET, absolute=absolute)

    assert env.repo.find_file("x") == env.root / TARGET
    assert env.repo.exists("x")


def test_an_id_less_aliased_file_is_one_entry(env):
    """An id derived from the title: the alias would derive the same id, and
    was reported as a collision of one file with itself."""
    env.write(TARGET, _note(None, "Derived Title"))
    env.link("o/alias.md", TARGET, absolute=False)

    result = env.im.sync_incremental(KB)

    assert result["duplicates"] == []
    assert list(env.rows().values()) == [TARGET]
    assert [a["path"] for a in result["aliases"]] == ["o/alias.md"]


def _cli_world(cli_world, env: Env):
    """The real CLI (real ``load_config``) over this test's KB folder."""
    return cli_world(kbs=[KBConfig(name=KB, path=env.root, kb_type="generic")])


def test_cli_index_sync_prints_the_alias_and_no_duplicate(env, cli_world):
    _build(env, "another folder", absolute=False)
    result = _cli_world(cli_world, env).cli("index", "sync", "-k", KB, "--no-embed")

    assert result.exit_code == 0, result.output
    assert "Duplicate ids" not in result.output
    assert "Aliases: 1" in result.output
    assert "o/alias.md -> t/target.md" in result.output


def test_a_rename_of_the_target_keeps_the_link_resolving(env):
    """#811's rule (landed via #1355): a rename changes the id, never the
    path, so the link still names the real file and nothing is malformed."""
    from pyrite.services.kb_service import KBService

    _build(env, "link to a link", absolute=False)
    env.im.sync_incremental(KB)
    links_before = env.links()

    KBService(env.config, env.db).rename_entry("x", "y", KB)

    assert env.links() == links_before
    for link, _ in SHAPES["link to a link"]:
        assert (env.root / link).resolve() == (env.root / TARGET).resolve()
    assert env.repo.id_of_file(env.root / TARGET) == "y"
    assert "[[y]]" in (env.root / "b.md").read_text()
    result = env.im.sync_incremental(KB)
    assert result["malformed"] == []
    assert result["duplicates"] == []
    assert result["aliases"] == _aliases("link to a link")
    assert env.rows() == {"y": TARGET, "b": "b.md"}


# --- controls: as before this change -------------------------------------


@pytest.mark.control(reason="a link to a file the walk skips is the only walked path, as before")
@pytest.mark.parametrize(
    "skipped", [".hidden/real.md", "_templates/real.md", "README.md", "notes/real.txt"]
)
def test_a_link_to_a_file_the_walk_skips_is_indexed_once_at_the_link(env, skipped):
    env.write(skipped, _note("h", "H"))
    env.link("alias.md", skipped, absolute=False)

    result = env.im.sync_incremental(KB)

    assert result["duplicates"] == []
    assert result.get("aliases", []) == []
    assert env.rows() == {"h": "alias.md"}
    assert env.repo.find_file("h") == env.root / "alias.md"


@pytest.mark.control(reason="a link that leaves the KB is skipped as outside, as before")
def test_a_link_that_leaves_the_kb_is_skipped_not_an_alias(env):
    (env.outside / "o.md").write_text(_note("o", "O"))
    (env.root / "out.md").symlink_to(env.outside / "o.md")

    result = env.im.sync_incremental(KB)

    assert result.get("aliases", []) == []
    assert [s["path"] for s in result["skipped"]] == [str(env.root / "out.md")]
    assert env.rows() == {}


@pytest.mark.control(reason="a looping link is malformed and unreadable, as before")
def test_a_looping_link_is_malformed_not_an_alias(env):
    (env.root / "l1.md").symlink_to("l2.md")
    (env.root / "l2.md").symlink_to("l1.md")

    result = env.im.sync_incremental(KB)
    health = env.im.check_health(kb_name=KB)

    assert result.get("aliases", []) == [] and health.get("aliases", []) == []
    assert result.get("dangling_links", []) == []
    assert sorted(Path(m["path"]).name for m in result["malformed"]) == ["l1.md", "l2.md"]
    assert env.rows() == {}


def test_a_dangling_in_kb_link_is_reported_as_dangling_and_is_a_warning(env):
    """A link inside the KB whose file is gone holds no entry, so the index
    answers from the files exactly: it is not an unreadable entry and does
    not make health unhealthy (the verdict's own rule: unhealthy means a read
    would answer from something other than the files). It is listed under
    ``dangling_links`` (``{kb, path, target}``, the KB-relative name it
    points at), and health is ``warning``. The link is left as it is."""
    env.write("b.md", _note("b", "B"))
    env.link("sub/gone.md", "t/nowhere.md", absolute=False)

    result = env.im.sync_incremental(KB)
    health = env.im.check_health(kb_name=KB)

    want = [{"kb": KB, "path": "sub/gone.md", "target": "t/nowhere.md"}]
    assert result["malformed"] == [] and result["skipped"] == []
    assert result["dangling_links"] == want
    assert health["unreadable_files"] == [] and health["malformed_frontmatter"] == []
    assert health["dangling_links"] == want
    assert health_verdict(health)["status"] == "warning"
    assert (env.root / "sub" / "gone.md").is_symlink()


@pytest.mark.control(reason="a hard link has no real name to prefer: both are skipped, as before")
def test_a_hard_link_is_two_names_and_both_are_skipped(env):
    real = env.write(TARGET, _note("x", "X"))
    (env.root / "o").mkdir()
    os.link(real, env.root / "o" / "hard.md")

    result = env.im.sync_incremental(KB)

    assert result.get("aliases", []) == []
    assert result["duplicates"] == []
    assert sorted(Path(s["path"]).relative_to(env.root).as_posix() for s in result["skipped"]) == [
        "o/hard.md",
        TARGET,
    ]
    assert env.rows() == {}
    assert env.repo.find_file("x") is None  # neither name is the KB's to answer for


@pytest.mark.control(reason="rglob does not descend into a linked folder, as before")
def test_a_linked_folder_is_not_walked(env):
    """Python's ``rglob`` (3.13: ``recurse_symlinks=False``) does not enter a
    folder that is a link, so the files under it are never walked: no alias
    and no duplicate to report. Pinned so a change of walk shows up here."""
    env.write(TARGET, _note("x", "X"))
    (env.root / "mirror").symlink_to("t", target_is_directory=True)

    result = env.im.sync_incremental(KB)

    assert result.get("aliases", []) == [] and result["duplicates"] == []
    assert env.rows() == {"x": TARGET}


def test_cli_index_health_reports_the_alias_and_is_healthy(env, cli_world):
    import json

    _build(env, "another folder", absolute=True)
    world = _cli_world(cli_world, env)
    assert world.cli("index", "sync", "-k", KB, "--no-embed").exit_code == 0
    result = world.cli("index", "health", "-k", KB, "--format", "json")

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["status"] == "healthy"
    assert report["duplicates"] == []
    assert report["aliases"] == _aliases("another folder")


def test_rest_reindex_response_carries_the_alias(env):
    """``POST /api/kbs/{name}/reindex`` returns ``KBReindexResponse(**result)``:
    a model without the field would drop the list silently."""
    from pyrite.server.schemas import KBReindexResponse

    _build(env, "nested folder", absolute=False)
    response = KBReindexResponse(name=KB, **env.im.sync_kb(env.kb_config))

    assert response.duplicates == []
    assert response.aliases == _aliases("nested folder")


@pytest.mark.control(
    reason="a link to a folder named like an entry is not a regular file, as before"
)
def test_a_link_to_a_folder_named_like_an_entry_is_skipped_not_an_alias(env):
    (env.root / "d.md").mkdir()
    (env.root / "x.md").symlink_to("d.md", target_is_directory=True)

    result = env.im.sync_incremental(KB)

    assert result.get("aliases", []) == []
    assert sorted(Path(s["path"]).name for s in result["skipped"]) == ["d.md", "x.md"]


def test_alias_target_agrees_with_kbfiles_on_every_link(env):
    """ADR-0052 N1: "does this name lead out of the KB" has one answer,
    ``KBFiles``'s. Every link kind in one KB, the expected target written by
    hand; then each answer is checked against ``KBFiles`` itself: an alias
    names exactly the bytes ``KBFiles.read_bytes`` reads for its target, and
    a link ``KBFiles`` refuses to read is never an alias."""
    from pyrite.utils.kb_files import KBFileReadOutsideRootError, KBFiles

    env.write(TARGET, _note("x", "X"))
    env.write(".hidden/h.md", _note("h", "H"))
    (env.outside / "o.md").write_text(_note("o", "O"))
    env.link("rel.md", TARGET, absolute=False)
    env.link("abs.md", TARGET, absolute=True)
    env.link("chain.md", "rel.md", absolute=False)
    env.link("to-hidden.md", ".hidden/h.md", absolute=False)
    (env.root / "out.md").symlink_to(env.outside / "o.md")
    (env.root / "dangling.md").symlink_to("nowhere.md")
    (env.root / "dangling-out.md").symlink_to(env.outside / "nowhere.md")
    (env.root / "loop1.md").symlink_to("loop2.md")
    (env.root / "loop2.md").symlink_to("loop1.md")
    # KBFiles's stated limit: a link out whose outside target links back in
    # resolves inside, so KBFiles reads it, and it is an alias of that file.
    (env.outside / "back.md").symlink_to(env.root / TARGET)
    (env.root / "out-and-back.md").symlink_to(env.outside / "back.md")

    expected = {
        "rel.md": TARGET,
        "abs.md": TARGET,
        "chain.md": TARGET,
        "out-and-back.md": TARGET,
        "to-hidden.md": None,  # the target is not walked: the link is the entry
        "out.md": None,
        "dangling.md": None,
        "dangling-out.md": None,
        "loop1.md": None,
        "loop2.md": None,
    }
    files = KBFiles(env.root)
    for name, want in expected.items():
        link = env.root / name
        got = env.repo.alias_target(link)
        assert got == (None if want is None else env.root / want), name
        if got is not None:
            assert files.contains(link) and files.present(got), name
            assert files.read_bytes(link) == files.read_bytes(got), name
        elif not files.contains(link):
            with pytest.raises(KBFileReadOutsideRootError):
                files.read_bytes(link)
    # The real file and a non-link are never aliases.
    assert env.repo.alias_target(env.root / TARGET) is None


def test_a_name_outside_the_kb_is_never_probed_as_an_alias(env):
    """``alias_target`` is asked about names ``_filename_candidates`` builds
    from a caller's id, so it decides on the name first: a name that is not
    under the KB's root is no alias, and is never ``lstat``-ed, even when it
    is a link that leads to a KB file."""
    env.write(TARGET, _note("x", "X"))
    (env.outside / "x.md").symlink_to(env.root / TARGET)

    assert env.repo.alias_target(env.outside / "x.md") is None
    assert env.repo.alias_target(env.root / ".." / "outside" / "x.md") is None
    with patch("os.path.islink", side_effect=AssertionError("probed")):
        assert env.repo.alias_target(env.root / ".." / "outside" / "x.md") is None


# --- writes that remove or move a real file --------------------------------


@pytest.mark.parametrize(
    "link", ["sub/alias.md", "x.md"], ids=["other folder", "named like the id"]
)
def test_a_delete_keeps_the_link_and_reports_it_left(env, link):
    """The link is the user's file (guest standard): a delete removes the
    entry's real file only, and the result names every in-KB link it left
    pointing at nothing (``links_left``). The next sync and health report it
    as a dangling link, a warning, never an unreadable entry."""
    from pyrite.services.kb_service import KBService

    env.write(TARGET, _note("x", "X"))
    env.link(link, TARGET, absolute=False)
    env.im.sync_incremental(KB)
    said = os.readlink(env.root / link)

    deleted = KBService(env.config, env.db).delete_entry("x", KB, readable_kbs={KB})

    assert deleted.deleted is True
    assert not (env.root / TARGET).exists()
    assert (env.root / link).is_symlink() and os.readlink(env.root / link) == said
    left = [{"path": link, "target": TARGET}]
    assert deleted.links_left == left
    body = deleted.as_dict()
    assert body["links_left"] == left
    assert any(link in w for w in body["warnings"]), body["warnings"]

    result = env.im.sync_incremental(KB)
    health = env.im.check_health(kb_name=KB)
    assert result["malformed"] == []
    assert result["dangling_links"] == [{"kb": KB, **left[0]}]
    assert health["unreadable_files"] == []
    assert health_verdict(health)["status"] == "warning"


def test_a_delete_with_no_link_leaves_none(env):
    from pyrite.services.kb_service import KBService

    env.write(TARGET, _note("x", "X"))
    env.im.sync_incremental(KB)

    deleted = KBService(env.config, env.db).delete_entry("x", KB, readable_kbs={KB})

    assert deleted.deleted is True and deleted.links_left == []
    assert deleted.as_dict()["links_left"] == [] and deleted.as_dict()["warnings"] == []


def test_the_rest_delete_response_carries_the_links_left():
    """``DELETE /api/entries/{id}`` returns ``EntryDeleteResponse(id=...,
    **deleted.as_dict())``: a model without the field would drop it."""
    from pyrite.server.schemas import EntryDeleteResponse
    from pyrite.services.kb_service import DeleteResult

    left = [{"path": "sub/alias.md", "target": TARGET}]
    response = EntryDeleteResponse(id="x", **DeleteResult(True, [], left).as_dict())

    assert response.links_left == left


def test_the_cli_delete_prints_the_links_left(env, cli_world):
    env.write(TARGET, _note("x", "X"))
    env.link("sub/alias.md", TARGET, absolute=False)
    world = _cli_world(cli_world, env)
    assert world.cli("index", "sync", "-k", KB, "--no-embed").exit_code == 0

    result = world.cli("delete", "x", "-k", KB, "--force")

    assert result.exit_code == 0, result.output
    assert "sub/alias.md" in result.output
    assert (env.root / "sub" / "alias.md").is_symlink()


TEMPLATED_KB_YAML = (
    "name: k\nkb_type: generic\ntypes:\n  thing:\n"
    "    subdirectory: things/{status}\n    fields:\n      status:\n        type: text\n"
)


def _templated(tmp_path: Path, status_on_disk: str, folder: str) -> Env:
    """A KB whose `thing` type is filed by status, one pinned thing at
    ``things/<folder>/t.md`` holding ``status_on_disk``, and an in-KB link
    ``sub/alias.md`` to it; indexed."""
    root = tmp_path / KB
    root.mkdir(parents=True)
    (root / "kb.yaml").write_text(TEMPLATED_KB_YAML)
    e = Env(tmp_path)
    e.write(
        f"things/{folder}/t.md",
        f"---\nid: t\ntitle: T\ntype: thing\nstatus: {status_on_disk}\n---\n\nText.\n",
    )
    e.link("sub/alias.md", f"things/{folder}/t.md", absolute=False)
    e.im.sync_incremental(KB)
    return e


def test_an_update_does_not_move_a_file_an_in_kb_link_names(tmp_path):
    """A templated move would leave the user's link pointing at nothing, so
    the file keeps its place, the update is written in place, and
    ``not_moved`` names the link (as it names a hard link or a taken
    target)."""
    from pyrite.services.kb_service import KBService

    e = _templated(tmp_path, "open", "open")
    try:
        written = KBService(e.config, e.db).update("t", KB, {"status": "done"})
        assert written.not_moved is not None and "sub/alias.md" in written.not_moved
        assert "status: done" in (e.root / "things/open/t.md").read_text()
        assert not (e.root / "things/done/t.md").exists()
        assert (e.root / "sub/alias.md").resolve() == (e.root / "things/open/t.md").resolve()
    finally:
        e.db.close()


def test_the_templated_reconcile_does_not_move_a_file_an_in_kb_link_names(tmp_path):
    """On dev the reconcile planned the link as an entry of its own (refused
    by the move) and moved the real file, leaving the link dangling. Now the
    real file stays, and the result says why, naming the link."""
    from pyrite.services.kb_service import KBService

    e = _templated(tmp_path, "done", "open")  # misplaced: done, under open/
    try:
        result = KBService(e.config, e.db).reconcile_templated_subdirectories(KB, apply=True)
        assert result.moved == 0 and result.errors == []
        assert [eid for eid, _ in result.kept] == ["t"]
        assert "sub/alias.md" in result.kept[0][1]
        assert (e.root / "things/open/t.md").is_file()
        assert (e.root / "sub/alias.md").resolve() == (e.root / "things/open/t.md").resolve()
    finally:
        e.db.close()


def test_the_cli_prints_aliases_and_dangling_links_in_sync_and_kb_reindex(env, cli_world):
    env.write(TARGET, _note("x", "X"))
    env.link("o/alias.md", TARGET, absolute=False)
    env.link("o/gone.md", "t/nowhere.md", absolute=False)
    world = _cli_world(cli_world, env)

    synced = world.cli("index", "sync", "-k", KB, "--no-embed")
    reindexed = world.cli("kb", "reindex", "-k", KB, "--format", "rich")

    for result in (synced, reindexed):
        assert result.exit_code == 0, result.output
        assert "Aliases: 1" in result.output and "o/alias.md -> t/target.md" in result.output
        assert "Dangling links: 1" in result.output
        assert "o/gone.md -> t/nowhere.md" in result.output
        assert "Malformed" not in result.output


def _point_the_row_at(env: Env, entry_id: str, rel: str) -> None:
    """Set the index row's file to ``rel``: the row a dev-built index holds
    for a KB whose link sorts before its real file ("the first path by name
    could be indexed in place of the real file"), or any stale row."""
    conn = env.db._raw_conn
    conn.execute(
        "UPDATE entry SET file_path = ? WHERE id = ? AND kb_name = ?",
        (str(env.root / rel), entry_id, KB),
    )
    conn.commit()
    assert env.rows()[entry_id] == rel


@pytest.mark.parametrize(
    ("real", "link", "to"),
    [
        ("notes/zz.md", "a.md", "notes/zz.md"),  # the link is named like the id
        ("a.md", "sub/al.md", "a.md"),  # the real file is
    ],
    ids=["link named like the id", "real file named like the id"],
)
def test_a_delete_never_removes_a_link_the_index_row_names(env, real, link, to):
    """An index is a cache: a row naming the link (dev's sync wrote one for a
    link-first KB) must never make the link the entry. The delete removes the
    real file, keeps the link, and ``links_left`` names exactly the link that
    is still there and points at nothing."""
    from pyrite.services.kb_service import KBService

    env.write(real, _note("a", "A"))
    env.link(link, to, absolute=False)
    env.im.sync_incremental(KB)
    _point_the_row_at(env, "a", link)
    said = os.readlink(env.root / link)

    deleted = KBService(env.config, env.db).delete_entry("a", KB, readable_kbs={KB})

    assert deleted.deleted is True
    assert not (env.root / real).exists(), "the entry's real file is still there"
    assert (env.root / link).is_symlink(), "the delete removed the user's link"
    assert os.readlink(env.root / link) == said
    assert deleted.links_left == [{"path": link, "target": real}]


def test_links_left_names_only_links_still_on_disk(env, monkeypatch):
    """``links_left`` is what is on disk after the delete, not the plan: a
    link that is gone by then (here, removed by whoever else, between the
    plan and the check) is not listed."""
    from pyrite.services.kb_service import KBService
    from pyrite.storage.repository import KBRepository

    env.write(TARGET, _note("x", "X"))
    env.link("o/one.md", TARGET, absolute=False)
    env.link("o/two.md", TARGET, absolute=False)
    env.im.sync_incremental(KB)
    real_delete = KBRepository.delete

    def delete_and_lose_a_link(self, *args, **kwargs):
        done = real_delete(self, *args, **kwargs)
        (env.root / "o" / "two.md").unlink()
        return done

    monkeypatch.setattr(KBRepository, "delete", delete_and_lose_a_link)
    deleted = KBService(env.config, env.db).delete_entry("x", KB, readable_kbs={KB})

    assert deleted.links_left == [{"path": "o/one.md", "target": TARGET}]


def test_the_cli_reconcile_says_a_link_keeps_the_file_and_exits_partial(tmp_path, cli_world):
    """The path was checked: a link holds it. `index reconcile` says that
    (never "could not be checked") and exits partial (3), dry-run as apply."""
    e = _templated(tmp_path, "done", "open")
    try:
        world = cli_world(kbs=[KBConfig(name=KB, path=e.root, kb_type="generic")])
        for args in (("index", "reconcile", KB), ("index", "reconcile", KB, "--apply")):
            result = world.cli(*args)
            assert result.exit_code == 3, result.output
            assert "could not be checked" not in result.output
            assert "Kept in place t" in result.output and "sub/alias.md" in result.output
            assert "stay where they are because an in-KB link names each" in result.output
        assert (e.root / "things/open/t.md").is_file()
    finally:
        e.db.close()


def test_a_dangling_collection_link_is_reported_as_dangling(env):
    """`__collection.yaml` files are walked by name (``_named``), a link
    included: one that points at nothing inside the KB is a dangling link
    like a markdown one, not an unreadable file."""
    (env.root / "c").mkdir()
    (env.root / "c" / "__collection.yaml").symlink_to("gone.yaml")

    result = env.im.sync_incremental(KB)

    assert result["dangling_links"] == [
        {"kb": KB, "path": "c/__collection.yaml", "target": "c/gone.yaml"}
    ]
    assert result["malformed"] == []

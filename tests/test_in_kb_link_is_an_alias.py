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
        self.root.mkdir(parents=True)
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
    assert env.repo.find_file("x", inside=False) == env.root / TARGET
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


@pytest.mark.control(reason="a dangling or looping link is malformed, as before")
@pytest.mark.parametrize("form", ["dangling", "loop"])
def test_a_dangling_or_looping_link_is_malformed_not_an_alias(env, form):
    if form == "dangling":
        (env.root / "gone.md").symlink_to("nowhere.md")
        expected = ["gone.md"]
    else:
        (env.root / "l1.md").symlink_to("l2.md")
        (env.root / "l2.md").symlink_to("l1.md")
        expected = ["l1.md", "l2.md"]

    result = env.im.sync_incremental(KB)
    health = env.im.check_health(kb_name=KB)

    assert result.get("aliases", []) == [] and health.get("aliases", []) == []
    assert sorted(Path(m["path"]).name for m in result["malformed"]) == expected
    assert env.rows() == {}


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

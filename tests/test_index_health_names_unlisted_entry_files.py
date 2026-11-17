"""`index health` names an entry file an earlier version left where the walk
does not list it (#1552, theme 2).

Before #1552, a create titled README wrote ``notes/readme.md``; the next sync
dropped its row and nothing said so (``index health`` answered ``healthy``).
A KB may still hold such files. The property:

- a ``*.md`` file the walk meets and skips (a README in any case, a
  dot-prefixed part, a ``_templates`` part) whose frontmatter states an
  ``id:`` is reported under ``unlisted_entry_files``, rows
  ``{kb, path, reason}`` with reason ``readme | hidden | templates``;
- status ``warning``, the exit code unchanged (0 unless something else is
  unhealthy); the file is never indexed and never repaired;
- a hand-kept README, dot-folder or ``_templates/`` file with no ``id:`` is
  the user's and is not reported (this repository's ``kb/backlog/README.md``
  and ``kb/positioning/README.md`` carry frontmatter without an ``id:``);
- the scan reads only files the walk skipped, through the KB's contained
  read: a link, a hard-linked file, a file that cannot be decoded is not this
  key.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.index_health import health_verdict
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

KB = "k"
REPO = Path(__file__).resolve().parent.parent


def _note(id_: str | None, title: str) -> str:
    id_line = f"id: {id_}\n" if id_ else ""
    return f"---\n{id_line}type: note\ntitle: {title}\n---\n\nbody\n"


class Env:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.root = tmp_path / KB
        self.root.mkdir()
        self.config = PyriteConfig(
            knowledge_bases=[KBConfig(name=KB, path=self.root, kb_type="generic")],
            settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
        )
        self.db = PyriteDB(tmp_path / "index.db")
        self.im = IndexManager(self.db, self.config)

    def write(self, rel: str, text: str | bytes) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text)
        return p

    def health(self) -> dict:
        return self.im.check_health(KB)


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    e.write("notes/a.md", _note("a", "A"))
    yield e
    e.db.close()


#: Each skipped shape that states an id, and the reason it is reported under.
REPORTED = {
    "notes/readme.md": "readme",
    "README.md": "readme",
    "notes/ReadMe.md": "readme",
    ".hidden/x.md": "hidden",
    "notes/.x.md": "hidden",
    "_templates/t.md": "templates",
    # Round 1 (F1): a folder that folds to `_templates` is skipped and named.
    "notes/_Templates/t.md": "templates",
}

#: Skipped shapes that state no id: the user's, never reported.
NOT_REPORTED = {
    "README.md": "# Hello\n\nNo frontmatter.\n",
    "notes/README.md": _note(None, "Notes"),
    "_templates/t.md": _note(None, "{{title}}"),
    ".hidden/x.md": "just text\n",
}


@pytest.mark.parametrize("rel", list(REPORTED))
def test_a_skipped_file_stating_an_id_is_named_with_its_reason(env, rel):
    env.write(rel, _note("old", "Old"))
    env.im.sync_incremental(KB)
    before = (env.root / rel).read_bytes()

    health = env.health()

    assert health["unlisted_entry_files"] == [{"kb": KB, "path": rel, "reason": REPORTED[rel]}]
    verdict = health_verdict(health)
    assert verdict["status"] == "warning", verdict
    # Reported, never indexed, never repaired.
    assert "old" not in env.im._load_indexed_state(KB)
    assert (env.root / rel).read_bytes() == before


@pytest.mark.parametrize("rel", list(NOT_REPORTED))
def test_a_skipped_file_with_no_id_is_the_users_and_is_not_reported(env, rel):
    env.write(rel, NOT_REPORTED[rel])
    env.im.sync_incremental(KB)

    health = env.health()

    assert health["unlisted_entry_files"] == []
    assert health_verdict(health)["status"] == "healthy"


def test_this_repositorys_frontmatter_readmes_are_not_reported(env):
    """The groom's fixture: two tracked READMEs carry frontmatter with no
    ``id:``. Copied at their own paths, they are not reported."""
    for rel in ("backlog/README.md", "positioning/README.md"):
        src = REPO / "kb" / rel
        text = src.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{rel} no longer carries frontmatter"
        assert "\nid:" not in text.split("\n---", 1)[0], f"{rel} now states an id"
        dst = env.root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    env.im.sync_incremental(KB)

    assert env.health()["unlisted_entry_files"] == []


def test_git_contents_with_no_id_are_silent(env):
    env.write(".git/info/notes.md", "# not an entry\n")
    env.write(".git/x/README.md", _note(None, "R"))
    assert env.health()["unlisted_entry_files"] == []


def test_an_undecodable_skipped_file_is_not_this_key(env):
    env.write(".hidden/bad.md", b"---\nid: x\n\xff\xfe---\n")
    assert env.health()["unlisted_entry_files"] == []


@pytest.mark.skipif(os.name == "nt", reason="links are not reliable on Windows")
def test_a_link_or_a_hard_link_is_not_read_as_an_unlisted_entry(env):
    """Through the contained read only: a skipped name that is a link (to a
    listed entry, or out of the KB) or a second hard link is not this key."""
    outside = env.tmp / "outside"
    outside.mkdir()
    (outside / "o.md").write_text(_note("o", "O"))
    (env.root / ".hidden").mkdir()
    (env.root / ".hidden" / "in.md").symlink_to(env.root / "notes" / "a.md")
    (env.root / ".hidden" / "out.md").symlink_to(outside / "o.md")
    os.link(outside / "o.md", env.root / ".hidden" / "hard.md")

    assert env.health()["unlisted_entry_files"] == []


class _RecordingRepo(KBRepository):
    """A repository that records every contained read (no patching: this
    file also drives MCP, behind which nothing may be patched)."""

    def __init__(self, config):
        super().__init__(config)
        self.read: list[Path] = []

    def _inside_text(self, file_path):
        self.read.append(Path(file_path))
        return super()._inside_text(file_path)


def test_the_scan_reads_only_the_files_the_walk_skipped(env):
    """The listed files are read once, by the reconcile's plan; the new key
    reads only the skipped ones, through ``KBRepository._inside_text``."""
    env.write("notes/b.md", _note("b", "B"))
    env.write("notes/readme.md", _note("readme", "README"))
    env.write("_templates/t.md", _note(None, "T"))
    repo = _RecordingRepo(env.config.get_kb(KB))
    found: list = []
    listed = sorted(p for p, _alias in repo.walk_files(unlisted=found))
    assert sorted(p.relative_to(env.root).as_posix() for p, _ in found) == [
        "_templates/t.md",
        "notes/readme.md",
    ]

    rows = repo.unlisted_entry_rows(found)

    assert rows == [{"kb": KB, "path": "notes/readme.md", "reason": "readme"}]
    assert sorted(repo.read) == sorted(p for p, _ in found)
    assert not set(repo.read) & set(listed)


@pytest.mark.control(reason="a sync's result keeps its shape; the key is health's alone")
def test_index_sync_result_has_no_new_key(env):
    env.write("notes/readme.md", _note("readme", "README"))
    result = env.im.sync_incremental(KB)
    assert "unlisted_entry_files" not in result


# --------------------------------------------------------------------------
# Surfaces: CLI `index health`, REST and MCP `kb health`
# --------------------------------------------------------------------------


def _world_kb(tmp_path: Path) -> Path:
    root = tmp_path / "kbw"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "a.md").write_text(_note("a", "A"))
    (root / "notes" / "readme.md").write_text(_note("readme", "README"))
    (root / "README.md").write_text("# Mine\n")
    return root


def test_cli_index_health_names_the_file_and_exits_zero(tmp_path, cli_world):
    root = _world_kb(tmp_path)
    world = cli_world(kbs=[KBConfig(name=KB, path=root, kb_type="generic")])

    result = world.cli("index", "health", "--format", "json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["status"] == "warning"
    assert report["unlisted_entry_files"] == [
        {"kb": KB, "path": "notes/readme.md", "reason": "readme"}
    ]

    text = world.cli("index", "health")
    assert text.exit_code == 0, text.output
    assert "notes/readme.md" in text.output and "readme" in text.output
    assert "README.md (" not in text.output  # the hand-kept README is not named


def test_rest_kb_health_carries_the_key(tmp_path):
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from tests._world import build_world

    root = _world_kb(tmp_path)
    world = build_world(tmp_path / "w", kbs=[KBConfig(name=KB, path=root, kb_type="generic")])
    try:
        res = world.client.get(f"/api/kbs/{KB}/health")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["unlisted_entry_files"] == [
            {"kb": KB, "path": "notes/readme.md", "reason": "readme"}
        ]
        assert body["healthy"] is True  # a warning, not unhealthy
    finally:
        world.close()


def test_mcp_kb_health_carries_the_key(tmp_path):
    from tests._world import build_world

    root = _world_kb(tmp_path)
    world = build_world(
        tmp_path / "w", kbs=[KBConfig(name=KB, path=root, kb_type="generic")], rest=False
    )
    try:
        result = world.mcp()._dispatch_tool("kb_registry_health", {"name": KB})
        assert result["unlisted_entry_files"] == [
            {"kb": KB, "path": "notes/readme.md", "reason": "readme"}
        ], result
    finally:
        world.close()

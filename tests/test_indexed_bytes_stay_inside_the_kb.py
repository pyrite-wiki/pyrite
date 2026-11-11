"""Every byte Pyrite exports comes from a file whose real path is inside its KB.

Most exports write from the index, not from the files (REST export to a repo,
``GET /api/entries/export``, a read by id), so the index may hold only bytes
read inside the KB's root. The indexer reads each file the way an export
does, through ``KBFiles.read_bytes``: a file reached through a symbolic link
that leaves the KB, or a file with more than one hard link (its other name may
be anywhere on the filesystem, and the inode cannot say where), is not read,
and no row holds its bytes.

The surfaces below are the ones that hand indexed or loaded entry bytes to
someone else. Each is driven through its own entry point against one KB that
holds such files, and none may return the secret those files point at.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from pyrite.utils.kb_files import KBFileOutsideRootError, KBFiles
from tests._world import build_world

SECRET = "secret-outside-the-kb-5c1e"
KB = "k"


def _note(entry_id: str, body: str) -> str:
    return f"---\nid: {entry_id}\ntitle: {entry_id.title()}\ntype: note\n---\n{body}\n"


def _texts(root: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in root.rglob("*") if p.is_file())


def _seed(tmp_path: Path) -> Path:
    """A KB with one ordinary note and three ways to reach outside it:
    a file that is a link out, a folder that is a link out, and a hard link
    to a file outside."""
    outside = tmp_path / "outside"
    (outside / "folder").mkdir(parents=True)
    (outside / "linked.md").write_text(_note("linked", SECRET))
    (outside / "folder" / "infolder.md").write_text(_note("infolder", SECRET))
    (outside / "hard.md").write_text(_note("hard", SECRET))

    kb = tmp_path / "kb"
    (kb / "notes").mkdir(parents=True)
    (kb / "notes" / "inside.md").write_text(_note("inside", "an ordinary note"))
    (kb / "notes" / "linked.md").symlink_to(outside / "linked.md")
    (kb / "away").symlink_to(outside / "folder", target_is_directory=True)
    os.link(outside / "hard.md", kb / "notes" / "hard.md")
    return kb


@pytest.fixture
def world(tmp_path):
    kb = _seed(tmp_path)
    w = build_world(tmp_path / "world", kbs=[KBConfig(name=KB, path=kb)])
    try:
        yield w, tmp_path
    finally:
        w.close()


#: A file under a folder that is a link out: the walk never descended such a
#: folder (``rglob`` does not follow a folder link), so this case held before
#: the contained read too. Kept so a walk that starts following one is caught.
_FOLDER_LINK = pytest.param(
    "infolder", marks=pytest.mark.control(reason="the walk never descended a folder link")
)


def _index_bodies(w) -> str:
    rows = w.db.execute_sql("SELECT id, title, body FROM entry WHERE kb_name = :k", {"k": KB})
    return repr(rows)


# -- the index itself, through each path that fills it ---------------------


def test_index_build_holds_no_byte_from_outside(world):
    w, _ = world
    text = _index_bodies(w)
    assert "an ordinary note" in text  # the KB was indexed
    assert SECRET not in text


def test_index_sync_after_a_link_appears_holds_no_byte_from_outside(world):
    """The file was indexed while it was the KB's own; it becomes a link out,
    and ``pyrite index sync`` drops what it held rather than reading it."""
    w, tmp_path = world
    kb = w.kb_path(KB)
    (kb / "notes" / "later.md").write_text(_note("later", "mine for now"))
    w.index(KB)
    assert "mine for now" in _index_bodies(w)
    (kb / "notes" / "later.md").unlink()
    (tmp_path / "outside" / "later.md").write_text(_note("later", SECRET))
    (kb / "notes" / "later.md").symlink_to(tmp_path / "outside" / "later.md")
    result = w.cli("index", "sync", "-k", KB)
    assert result.exit_code in (0, 1, 3), result.output
    assert SECRET not in _index_bodies(w)


def test_index_verify_holds_no_byte_from_outside(world):
    w, _ = world
    from pyrite.storage.index import IndexManager

    IndexManager(w.db, w.config).reconcile_kb(w.config.get_kb(KB), verify=True)
    IndexManager(w.db, w.config).reconcile_kb(w.config.get_kb(KB), force=True)
    assert SECRET not in _index_bodies(w)


# -- the surfaces that hand entry bytes to someone else --------------------


def test_export_kb_to_directory_writes_no_byte_from_outside(world):
    """The export behind REST export-to-repo writes entry bodies from the index."""
    from pyrite.services.export_service import ExportService

    w, tmp_path = world
    out = tmp_path / "exported"
    ExportService(w.config, w.db).export_kb_to_directory(KB, out)
    text = _texts(out)
    assert "an ordinary note" in text
    assert SECRET not in text


def test_rest_entries_export_returns_no_byte_from_outside(world):
    """The JSON export carries each entry's body (the markdown one does not)."""
    w, _ = world
    r = w.client.get("/api/entries/export", params={"kb": KB, "format": "json"})
    assert r.status_code == 200, r.text
    assert "Inside" in r.text
    assert SECRET not in r.text


@pytest.mark.parametrize("entry_id", ["linked", _FOLDER_LINK, "hard"])
def test_rest_get_entry_returns_no_byte_from_outside(world, entry_id):
    w, _ = world
    r = w.client.get(f"/api/entries/{entry_id}", params={"kb": KB})
    assert SECRET not in r.text, r.text


@pytest.mark.parametrize("entry_id", ["linked", _FOLDER_LINK, "hard"])
def test_load_entry_from_disk_reads_nothing_from_outside(world, entry_id):
    """The by-id load the daily-note route indexes from and the export's
    fallback lookup exports from."""
    from pyrite.services.kb_service import KBService

    w, _ = world
    entry = KBService(w.config, w.db).load_entry_from_disk(entry_id, KB)
    assert entry is None or SECRET not in (entry.body or "")


def test_export_collection_fallback_reads_nothing_from_outside(world):
    """`pyrite export collection` falls back to a lookup by id when the
    indexed path does not load: that lookup reads only inside the KB."""
    w, tmp_path = world
    from pyrite.cli import export_commands
    from pyrite.services.kb_service import KBService

    svc = KBService(w.config, w.db)
    for entry_id in ("linked", "infolder", "hard"):
        row = {"id": entry_id, "kb_name": KB, "file_path": ""}
        entry = export_commands._load_entry_from_result(row, svc, KB)
        assert entry is None or SECRET not in (entry.body or ""), entry_id
    # A KB the config does not know is never read by id at all.
    assert export_commands._load_entry_from_result({"id": "inside"}, svc, "nope") is None


def test_rest_pending_changes_return_no_byte_from_outside(tmp_path):
    """`GET /api/kbs/{kb}/changes` returns each changed file's current text: a
    new file that is a link out of the KB is listed, never read."""
    import subprocess

    kb = _seed(tmp_path)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q"], cwd=kb, check=True, capture_output=True, timeout=60)
    subprocess.run([*git, "add", "notes/inside.md"], cwd=kb, check=True, timeout=60)
    subprocess.run([*git, "commit", "-qm", "kb"], cwd=kb, check=True, timeout=60)
    w = build_world(tmp_path / "world", kbs=[KBConfig(name=KB, path=kb)])
    try:
        r = w.client.get(f"/api/kbs/{KB}/changes")
    finally:
        w.close()
    assert r.status_code == 200, r.text
    assert "notes/linked.md" in r.text or "notes/hard.md" in r.text, r.text
    assert SECRET not in r.text


# -- the contained read ----------------------------------------------------


class TestReadBytesRefusesHardLinks:
    """`KBFiles.read_bytes` refuses a file with more than one name: the other
    name may be outside the KB, and nothing on the inode says where. Proven
    here by the case that escapes, so the refusal is the rule, not a guess."""

    def test_a_hard_link_to_a_file_outside_is_refused(self, tmp_path):
        (tmp_path / "kb").mkdir()
        (tmp_path / "secret.md").write_text(SECRET)
        os.link(tmp_path / "secret.md", tmp_path / "kb" / "a.md")
        with pytest.raises(KBFileOutsideRootError):
            KBFiles(tmp_path / "kb").read_bytes(tmp_path / "kb" / "a.md")

    def test_a_hard_link_inside_the_kb_is_refused_too(self, tmp_path):
        """Two names inside the KB cannot be told from one inside and one
        outside without searching the whole filesystem; both are refused."""
        (tmp_path / "kb").mkdir()
        (tmp_path / "kb" / "a.md").write_text("inside")
        os.link(tmp_path / "kb" / "a.md", tmp_path / "kb" / "b.md")
        with pytest.raises(KBFileOutsideRootError):
            KBFiles(tmp_path / "kb").read_bytes(tmp_path / "kb" / "a.md")

    @pytest.mark.control(reason="a file with one name inside the KB was always read")
    def test_a_file_with_one_name_is_read(self, tmp_path):
        (tmp_path / "kb").mkdir()
        (tmp_path / "kb" / "a.md").write_text("inside")
        assert KBFiles(tmp_path / "kb").read_bytes(tmp_path / "kb" / "a.md") == b"inside"

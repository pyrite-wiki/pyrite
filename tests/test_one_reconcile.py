"""One reconcile (ADR-0038 step 2): `index_kb`, `sync_kb` and `sync_incremental`
are one walk-and-reconcile, so each of them leaves the index equal to the files.

Before this, each path decided for itself (#485, #486, #487, #495): a full
build never retired a row, `kb reindex` ignored a moved file, both syncs judged
staleness by mtime alone, and two files claiming one id were indexed in walk
order -- the row flipped on every sync and nothing said so.

The rule, tested here against hand-authored KBs through each path:

- rows are exactly the ids of parseable files (I2); a deleted file's row, an
  unparseable file's row and a re-id'd file's old row are retired;
- a moved file's row follows it;
- a known file is re-read when its mtime OR size differs from what was
  recorded at index time, and the content hash decides whether it changed
  (a touch is not an update);
- two files claiming one id are reported, and the lexicographically first
  KB-relative path wins, whatever order the files were found or indexed in;
- a second reconcile with nothing changed reports nothing and moves no row
  (I3); no reconcile writes a file (I4).

The id derivation for id-less files is NOT part of this (title-derived until
0.26, ADR-0042): the derived-duplicate test only relies on two files with one
title deriving one id.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

KB = "k"


def _note(id_: str | None, title: str, body: str = "body") -> str:
    id_line = f"id: {id_}\n" if id_ else ""
    return f"---\n{id_line}type: note\ntitle: {title}\n---\n\n{body}\n"


class Env:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path / KB
        self.root.mkdir(parents=True)
        self.config = PyriteConfig(
            knowledge_bases=[KBConfig(name=KB, path=self.root, kb_type="generic")],
            settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
        )
        self.db = PyriteDB(tmp_path / "index.db")
        self.im = IndexManager(self.db, self.config)
        self.kb_config = self.config.get_kb(KB)

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def rows(self) -> dict[str, str]:
        """id -> KB-relative path of its row."""
        return {
            i: str(Path(r["file_path"]).relative_to(self.root))
            for i, r in self.im._load_indexed_state(KB).items()
        }

    def row_hash(self, entry_id: str) -> str | None:
        return self.im._load_indexed_state(KB)[entry_id]["content_hash"]

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in sorted(self.root.rglob("*"))
            if p.is_file()
        }


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.db.close()


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# Every reconcile path, as its callers run it. `index_kb` returns a count, the
# syncs return the result dict; tests that read the dict use SYNCS.
PATHS = {
    "index_kb": lambda e: e.im.index_kb(KB),
    "sync_incremental": lambda e: e.im.sync_incremental(KB),
    "sync_kb": lambda e: e.im.sync_kb(e.kb_config),
}
SYNCS = {k: v for k, v in PATHS.items() if k != "index_kb"}


def _paths(names, **already_right: str):
    """Parametrize over ``names``; a path dev already got right for this case
    is a negative control (verify-red expects it to pass without the fix),
    with the reason given."""
    return [
        pytest.param(n, marks=pytest.mark.control(reason=already_right[n]))
        if n in already_right
        else n
        for n in names
    ]


def _counts(result) -> dict[str, int]:
    return {k: result[k] for k in ("added", "updated", "removed") if result[k]}


def _assert_quiet(e: Env) -> None:
    """I3: with nothing changed, neither sync reports a change or moves a row."""
    before = e.rows()
    for name, run in SYNCS.items():
        assert _counts(run(e)) == {}, f"{name} reported a change with no file changed"
        assert e.rows() == before, f"{name} moved a row with no file changed"


# ---------------------------------------------------------------------------
# Duplicates: reported, and the first KB-relative path wins every time.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
def test_duplicate_explicit_id_first_path_wins(env, path):
    env.write("notes/a.md", _note("a", "A one"))
    env.write("b.md", _note("a", "A two"))
    PATHS[path](env)
    assert env.rows() == {"a": "b.md"}  # "b.md" < "notes/a.md"
    PATHS[path](env)
    assert env.rows() == {"a": "b.md"}
    _assert_quiet(env)


@pytest.mark.parametrize("path", PATHS)
def test_duplicate_derived_id_first_path_wins(env, path):
    # No `id:` line: both derive "same" from the title (title derivation is
    # unchanged by this theme).
    env.write("z/one.md", _note(None, "Same"))
    env.write("m.md", _note(None, "Same"))
    PATHS[path](env)
    assert env.rows() == {"same": "m.md"}
    _assert_quiet(env)


@pytest.mark.parametrize("path", PATHS)
def test_duplicate_winner_does_not_depend_on_index_order(env, path):
    """The later, lexicographically first file takes the row; the reverse
    order leaves it where it is. Walk order and history never decide."""
    env.write("notes/a.md", _note("a", "A"))
    PATHS[path](env)
    assert env.rows() == {"a": "notes/a.md"}
    env.write("b.md", _note("a", "A copy"))
    PATHS[path](env)
    assert env.rows() == {"a": "b.md"}
    _assert_quiet(env)


@pytest.mark.parametrize("path", PATHS)
def test_duplicate_added_after_the_winner_leaves_the_row(env, path):
    env.write("b.md", _note("a", "A"))
    PATHS[path](env)
    hash_before = env.row_hash("a")
    env.write("notes/a.md", _note("a", "A later copy"))
    PATHS[path](env)
    assert env.rows() == {"a": "b.md"}
    assert env.row_hash("a") == hash_before
    _assert_quiet(env)


@pytest.mark.parametrize("path", SYNCS)
def test_duplicates_are_in_the_result_every_time(env, path):
    env.write("notes/a.md", _note("a", "A"))
    env.write("b.md", _note("a", "A copy"))
    expected = [{"kb": KB, "id": "a", "winner": "b.md", "paths": ["b.md", "notes/a.md"]}]
    first = SYNCS[path](env)
    assert first["duplicates"] == expected
    assert _counts(first) == {"added": 1}  # one id, one row: not one per file
    second = SYNCS[path](env)
    assert second["duplicates"] == expected
    assert _counts(second) == {}


@pytest.mark.parametrize("path", SYNCS)
def test_no_duplicates_reports_an_empty_list(env, path):
    env.write("a.md", _note("a", "A"))
    assert SYNCS[path](env)["duplicates"] == []


# ---------------------------------------------------------------------------
# Deleted, moved, re-id'd, unparseable.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    _paths(
        PATHS,
        sync_incremental="dev's sync walked by path and retired unseen ids",
        sync_kb="dev's reindex retired unseen ids",
    ),
)
def test_deleted_file_row_is_retired(env, path):
    env.write("a.md", _note("a", "A"))
    gone = env.write("g.md", _note("g", "G"))
    PATHS[path](env)
    gone.unlink()
    result = PATHS[path](env)
    assert env.rows() == {"a": "a.md"}
    if path in SYNCS:
        assert _counts(result) == {"removed": 1}
    _assert_quiet(env)


@pytest.mark.parametrize(
    "path",
    _paths(
        PATHS,
        index_kb="dev's build upserted the new path (and kept no other row here)",
        sync_incremental="dev's sync walked by path and retired unseen ids",
    ),
)
def test_moved_file_row_follows_it(env, path):
    """`mv` / `git mv`: same bytes, same mtime, new path (#487)."""
    src = env.write("notes/a.md", _note("a", "A"))
    PATHS[path](env)
    dst = env.root / "elsewhere" / "x.md"
    dst.parent.mkdir()
    os.rename(src, dst)
    result = PATHS[path](env)
    assert env.rows() == {"a": "elsewhere/x.md"}
    if path in SYNCS:
        assert _counts(result) == {"updated": 1}
    _assert_quiet(env)


@pytest.mark.parametrize(
    "path",
    _paths(
        PATHS,
        sync_incremental="dev's sync retired a re-id'd path's old id (#391)",
        sync_kb="dev's reindex retired unseen ids",
    ),
)
def test_id_changed_in_place_retires_the_old_row(env, path):
    f = env.write("a.md", _note("a", "A"))
    PATHS[path](env)
    f.write_text(_note("hand-1", "A"))
    PATHS[path](env)
    assert env.rows() == {"hand-1": "a.md"}
    _assert_quiet(env)


@pytest.mark.parametrize(
    "path", _paths(PATHS, sync_kb="dev's reindex dropped an unparseable file's row")
)
def test_unparseable_file_is_reported_and_its_row_retired(env, path):
    env.write("a.md", _note("a", "A"))
    bad = env.write("b.md", _note("b", "B"))
    PATHS[path](env)
    bad.write_text("---\nid: [\n---\n")
    result = PATHS[path](env)
    assert env.rows() == {"a": "a.md"}
    if path in SYNCS:
        assert [m["path"] for m in result["malformed"]] == [str(bad)]
        assert _counts(result) == {"removed": 1}
        # Reported again on the next run (it is still broken); nothing moves.
        again = SYNCS[path](env)
        assert [m["path"] for m in again["malformed"]] == [str(bad)]
        assert _counts(again) == {}


# ---------------------------------------------------------------------------
# Staleness: mtime or size, the hash as tiebreaker.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
def test_same_size_edit_with_a_newer_mtime_is_indexed(env, path):
    f = env.write("a.md", _note("a", "A", body="xxxx"))
    PATHS[path](env)
    st = f.stat()
    f.write_text(_note("a", "A", body="yyyy"))
    assert f.stat().st_size == st.st_size
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    result = PATHS[path](env)
    assert env.row_hash("a") == _sha(f)
    assert "yyyy" in env.db.get_entry("a", KB)["body"]
    if path in SYNCS:
        assert _counts(result) == {"updated": 1}
    _assert_quiet(env)


@pytest.mark.parametrize("path", _paths(PATHS, index_kb="a rebuild reads every file"))
def test_size_change_keeping_the_old_mtime_is_indexed(env, path):
    """rsync -a, cp -p, tar: the content changes, the mtime does not (#495)."""
    f = env.write("a.md", _note("a", "A", body="short"))
    PATHS[path](env)
    st = f.stat()
    f.write_text(_note("a", "A", body="a longer body"))
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    result = PATHS[path](env)
    assert env.row_hash("a") == _sha(f)
    if path in SYNCS:
        assert _counts(result) == {"updated": 1}
    _assert_quiet(env)


@pytest.mark.parametrize("path", SYNCS)
def test_older_mtime_same_size_different_content_is_indexed(env, path):
    """The mtime is compared for difference, not for being newer: a restore
    from a backup carries an older mtime than the last index."""
    f = env.write("a.md", _note("a", "A", body="xxxx"))
    SYNCS[path](env)
    st = f.stat()
    f.write_text(_note("a", "A", body="yyyy"))
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns - 60_000_000_000))
    assert _counts(SYNCS[path](env)) == {"updated": 1}
    assert env.row_hash("a") == _sha(f)


@pytest.mark.parametrize("path", SYNCS)
def test_touch_without_a_content_change_is_not_an_update(env, path):
    """`git checkout` sets mtime to now on unchanged bytes: the hash decides."""
    f = env.write("a.md", _note("a", "A"))
    SYNCS[path](env)
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert _counts(SYNCS[path](env)) == {}
    _assert_quiet(env)


@pytest.mark.control(reason="the old mtime check also skipped unchanged files; this keeps that")
def test_a_known_unchanged_file_is_not_read(env, monkeypatch):
    """The cheap path stays cheap: stat only, no parse, no hash."""
    env.write("a.md", _note("a", "A"))
    env.im.sync_incremental(KB)
    from pyrite.storage import index as index_mod
    from pyrite.storage.repository import KBRepository

    def boom(*a, **k):
        raise AssertionError("an unchanged known file was read")

    monkeypatch.setattr(KBRepository, "load_entry_from_file", boom)
    monkeypatch.setattr(index_mod, "_hash_file", boom)
    assert _counts(env.im.sync_incremental(KB)) == {}


# ---------------------------------------------------------------------------
# I4 and cross-path agreement.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    _paths(
        PATHS,
        index_kb="I4 held on dev; kept",
        sync_incremental="I4 held on dev; kept",
        sync_kb="I4 held on dev; kept",
    ),
)
def test_reconcile_writes_no_file(env, path):
    env.write("notes/a.md", _note("a", "A"))
    env.write("b.md", _note("a", "A copy"))
    env.write("c.md", _note(None, "No id"))
    env.write("bad.md", "---\nid: [\n---\n")
    before = env.snapshot()
    PATHS[path](env)
    PATHS[path](env)
    assert env.snapshot() == before


@pytest.mark.control(
    reason="on dev all three took the last-walked file, so they agreed by walk order"
)
def test_the_three_paths_agree(tmp_path):
    """Same KB, three fresh indexes, one answer."""
    answers = {}
    for name, run in PATHS.items():
        e = Env(tmp_path / name)
        e.write("notes/a.md", _note("a", "A"))
        e.write("b.md", _note("a", "A copy"))
        e.write("z/same.md", _note(None, "Same"))
        e.write("m.md", _note(None, "Same"))
        e.write("c.md", _note("c", "C"))
        e.write("bad.md", "---\nid: [\n---\n")
        run(e)
        answers[name] = (e.rows(), {i: e.row_hash(i) for i in e.rows()})
        e.db.close()
    assert answers["index_kb"] == answers["sync_incremental"] == answers["sync_kb"]


def test_index_build_then_sync_is_quiet(env):
    env.write("notes/a.md", _note("a", "A"))
    env.write("b.md", _note("a", "A copy"))
    env.write("c.md", _note("c", "C"))
    env.im.index_kb(KB)
    _assert_quiet(env)


# ---------------------------------------------------------------------------
# Health and the CLI print the duplicates.
# ---------------------------------------------------------------------------


def test_health_reports_duplicates_and_not_the_loser_as_changed(env):
    env.write("notes/a.md", _note("a", "A"))
    env.write("b.md", _note("a", "A copy"))
    env.im.sync_incremental(KB)
    health = env.im.check_health(kb_name=KB)
    assert health["duplicates"] == [
        {"kb": KB, "id": "a", "winner": "b.md", "paths": ["b.md", "notes/a.md"]}
    ]
    # The losing copy is not the indexed file: it is neither stale nor changed.
    assert health["content_changed"] == []
    assert health["stale_entries"] == []
    assert health["unindexed_files"] == []


def test_health_stale_uses_the_reconcile_rule(env):
    """A size change that keeps the mtime is stale to health as to sync."""
    f = env.write("a.md", _note("a", "A", body="short"))
    env.im.sync_incremental(KB)
    st = f.stat()
    f.write_text(_note("a", "A", body="a longer body"))
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    health = env.im.check_health(kb_name=KB)
    assert [s["id"] for s in health["stale_entries"]] == ["a"]
    env.im.sync_incremental(KB)
    assert env.im.check_health(kb_name=KB)["stale_entries"] == []


runner = CliRunner()


def _cli(config, *args):
    from pyrite.cli import app

    with patch("pyrite.cli.context.load_config", return_value=config):
        return runner.invoke(app, list(args))


def test_cli_index_sync_prints_duplicates(env):
    env.write("notes/a.md", _note("a", "A"))
    env.write("b.md", _note("a", "A copy"))
    env.db.close()
    result = _cli(env.config, "index", "sync", "-k", KB, "--no-embed")
    assert result.exit_code == 0, result.output
    assert "Duplicate ids: 1" in result.output
    assert "a: b.md (indexed), notes/a.md" in result.output


def test_cli_index_health_is_unhealthy_with_a_duplicate(env):
    """Maintainer, 2026-10-03: a duplicate id is unhealthy, not a warning --
    the losing file is invisible to search. Exit 1, both paths and the winner."""
    env.write("notes/a.md", _note("a", "A"))
    env.write("b.md", _note("a", "A copy"))
    env.im.sync_incremental(KB)
    env.db.close()
    result = _cli(env.config, "index", "health", "-k", KB, "--format", "json")
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)
    assert data["status"] == "unhealthy"
    assert data["duplicates"] == [
        {"kb": KB, "id": "a", "winner": "b.md", "paths": ["b.md", "notes/a.md"]}
    ]


def test_rows_indexed_before_v27_are_read_once_and_reported_quiet(env):
    """An upgraded index has rows with no recorded stat: the first sync reads
    each file, the hash says nothing changed, and nothing is reported."""
    env.write("a.md", _note("a", "A"))
    env.im.sync_incremental(KB)
    env.db._raw_conn.execute("UPDATE entry SET file_mtime_ns = NULL, file_size = NULL")
    env.db._raw_conn.commit()
    assert _counts(env.im.sync_incremental(KB)) == {}
    row = env.im._load_indexed_state(KB)["a"]
    assert row["file_size"] == (env.root / "a.md").stat().st_size
    _assert_quiet(env)


def test_migration_v27_adds_the_stat_columns(tmp_path):
    import sqlite3

    from pyrite.storage.migrations import MigrationManager

    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE entry (id TEXT, kb_name TEXT, content_hash TEXT)")
    conn.commit()
    MigrationManager(conn)._apply_v27()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(entry)")}
    assert {"file_mtime_ns", "file_size"} <= columns
    MigrationManager(conn)._apply_v27()  # idempotent
    conn.close()


def test_kb_reindex_reports_duplicates_through_the_registry_and_rest_model(env):
    """`pyrite kb reindex`, REST and MCP reindex all return the registry's
    dict; the REST model must carry `duplicates`, not drop it."""
    from pyrite.server.schemas import KBReindexResponse
    from pyrite.services.kb_registry_service import KBRegistryService

    env.write("notes/a.md", _note("a", "A"))
    env.write("b.md", _note("a", "A copy"))
    result = KBRegistryService(env.config, env.db, env.im).reindex_kb(KB)
    expected = [{"kb": KB, "id": "a", "winner": "b.md", "paths": ["b.md", "notes/a.md"]}]
    assert result["duplicates"] == expected
    assert KBReindexResponse(name=KB, **result).duplicates == expected


# ---------------------------------------------------------------------------
# index_with_attribution (pyrite index build --with-attribution, repo pull)
# goes through the same reconcile.
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    import subprocess

    env = {**os.environ, "GIT_AUTHOR_NAME": "Ann", "GIT_AUTHOR_EMAIL": "ann@example.com"}
    env |= {"GIT_COMMITTER_NAME": "Ann", "GIT_COMMITTER_EMAIL": "ann@example.com"}
    env |= {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    out = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)
    return out.stdout.decode().strip()


def test_attribution_retires_a_deleted_files_row_and_dedupes(env):
    from pyrite.services.git_service import GitService

    _git(env.root, "init", "-q", "-b", "main")
    env.write("notes/a.md", _note("a", "A"))
    env.write("b.md", _note("a", "A copy"))
    gone = env.write("g.md", _note("g", "G"))
    _git(env.root, "add", "-A")
    _git(env.root, "commit", "-qm", "one")
    assert env.im.index_with_attribution(KB, GitService()) == 2  # one per id
    assert env.rows() == {"a": "b.md", "g": "g.md"}
    assert env.db.get_entry("a", KB)["created_by"] == "Ann"
    gone.unlink()
    env.im.index_with_attribution(KB, GitService())
    assert env.rows() == {"a": "b.md"}
    _assert_quiet(env)


@pytest.mark.control(reason="dev upserted every git-changed file; the reconcile must keep that")
def test_attribution_since_commit_reads_history_of_changed_files_already_synced(env):
    """A sync after a pull indexes the changed file first; attribution since
    the old head must still read its history (the reconcile forces it)."""
    from pyrite.services.git_service import GitService

    _git(env.root, "init", "-q", "-b", "main")
    f = env.write("a.md", _note("a", "A"))
    _git(env.root, "add", "-A")
    _git(env.root, "commit", "-qm", "one")
    old_head = _git(env.root, "rev-parse", "HEAD")
    env.im.sync_incremental(KB)
    f.write_text(_note("a", "A", body="edited"))
    _git(env.root, "commit", "-qam", "two")
    env.im.sync_incremental(KB)  # the row is current before attribution runs
    assert env.im.index_with_attribution(KB, GitService(), since_commit=old_head) == 1
    messages = sorted(v["message"] for v in env.db.get_entry_versions("a", KB))
    assert messages == ["one", "two"]


@pytest.mark.parametrize("path", SYNCS)
def test_a_file_that_parses_but_cannot_be_indexed_is_reported(env, path):
    """Found on the pyrite KB: `title:` as a YAML list parses, then the row
    write fails. It used to vanish with one log line; it is reported, and a
    row it had before is retired, as for any file that cannot be read."""
    env.write("a.md", _note("a", "A"))
    f = env.write("b.md", _note("b", "B"))
    SYNCS[path](env)
    f.write_text("---\nid: b\ntype: note\ntitle:\n- one\n- two\n---\n\nbody\n")
    result = SYNCS[path](env)
    assert [m["path"] for m in result["malformed"]] == [str(f)]
    assert env.rows() == {"a": "a.md"}
    assert _counts(result) == {"removed": 1}


def test_cli_index_health_rich_names_both_paths_and_the_winner(env):
    env.write("notes/a.md", _note("a", "A"))
    env.write("b.md", _note("a", "A copy"))
    env.im.sync_incremental(KB)
    env.db.close()
    result = _cli(env.config, "index", "health", "-k", KB, "--format", "rich")
    assert result.exit_code == 1, result.output
    assert "k/a: b.md (indexed), notes/a.md" in result.output


@pytest.mark.parametrize(
    "path",
    _paths(["index_kb", "sync_incremental"], sync_incremental="dev's sync made a final call"),
)
def test_progress_is_reported_once_at_the_end_even_for_an_empty_kb(env, path):
    """The index worker's `index_progress` event comes from this callback;
    dev's sync always made a final call, an empty KB included."""
    calls = []
    if path == "index_kb":
        env.im.index_kb(KB, lambda c, t: calls.append((c, t)))
    else:
        env.im.sync_incremental(KB, progress_callback=lambda c, t: calls.append((c, t)))
    assert calls == [(0, 0)]

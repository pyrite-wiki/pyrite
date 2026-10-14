"""An entry's version history contains only commits of that entry, and the
KB's confinement to its own subtree fails closed (#432, third round).

Every test here runs against a real git repository: rename pairing,
`--follow` and `rev-parse --show-prefix` are git's behaviour, not ours, and
a mocked output would only restate our assumptions about them.
"""

import logging
import subprocess
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.git_service import GitService
from pyrite.services.version_service import VersionService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.models import EntryVersion


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout.strip()


def _note(entry_id, title, body):
    return f"---\nid: {entry_id}\ntitle: {title}\ntype: note\ntags: [x]\n---\n\n{body}\n"


def _boilerplate_note(entry_id, title, body):
    """Frontmatter heavy enough, and a body short enough, that git's DEFAULT
    rename detection (50%) pairs a delete of one such file with an add of
    another -- the shared boilerplate is most of each file's bytes."""
    return (
        f"---\nid: {entry_id}\ntitle: {title}\ntype: note\nstatus: draft\n"
        "importance: 5\nsources: []\naliases: []\nlifecycle: active\n"
        f"tags:\n- alpha\n- beta\n- gamma\n---\n\n{body}\n"
    )


class _Repo:
    """A git repo with a KB at `repo/<kb_dir>` (or the repo root when
    `kb_dir` is empty), an index, and a VersionService over it."""

    def __init__(self, tmp_path: Path, kb_dir: str = "kb"):
        self.root = tmp_path / "repo"
        self.root.mkdir()
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "T")
        self.kb = self.root / kb_dir if kb_dir else self.root
        self.kb.mkdir(exist_ok=True)
        self.config = PyriteConfig(
            knowledge_bases=[KBConfig(name="k", path=self.kb)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        self.db = PyriteDB(tmp_path / "index.db")
        self.svc = VersionService(self.config, self.db)

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self, message):
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", message)
        return _git(self.root, "rev-parse", "HEAD")

    def index(self):
        IndexManager(self.db, self.config).index_all()

    def attribute(self):
        IndexManager(self.db, self.config).index_with_attribution("k", GitService)

    def versions(self, entry_id):
        return [
            (v["commit_hash"], v["change_type"]) for v in self.db.get_entry_versions(entry_id, "k")
        ]

    def change_type(self, entry_id, commit):
        row = (
            self.db.session.query(EntryVersion)
            .filter_by(entry_id=entry_id, kb_name="k", commit_hash=commit)
            .first()
        )
        return row.change_type if row else None

    def close(self):
        self.db.close()


@pytest.fixture
def repo(tmp_path):
    r = _Repo(tmp_path)
    yield r
    r.close()


# ---------------------------------------------------------------------------
# 1. A delete plus an unrelated add is not a rename of one entry.
# ---------------------------------------------------------------------------


class TestUnrelatedDeleteAndAddAreNotLinked:
    """`secret.md` deleted and `public.md` added in one commit share only
    frontmatter boilerplate. Linking them serves a deleted entry's content
    as another entry's version, and calls the add a modification."""

    @pytest.fixture
    def m10(self, repo):
        repo.write("kb/secret.md", _note("s1", "Secret", "Private draft: text only s1 has."))
        c_secret = repo.commit("add secret")
        (repo.kb / "secret.md").unlink()
        repo.write("kb/public.md", _note("p1", "Public", "Press release text, nothing else."))
        c_public = repo.commit("delete secret, add public")
        return repo, c_secret, c_public

    @pytest.mark.control(
        reason="Guards a regression this PR's earlier round introduced (-M10%), "
        "not a bug on dev: dev's --follow uses git's default threshold. "
        "Red when -M10% is put back (mutation-checked)."
    )
    def test_file_log_of_the_added_file_starts_at_its_add(self, m10):
        repo, c_secret, c_public = m10
        log = GitService.get_file_log(repo.kb, "public.md")
        assert [e["hash"] for e in log] == [c_public]

    def test_the_add_is_reported_as_an_add(self, m10):
        repo, c_secret, c_public = m10
        changes = GitService.get_commit_file_changes(repo.kb, c_public)
        assert ("A", "public.md", None) in changes

    @pytest.mark.control(
        reason="Guards a regression this PR's earlier round introduced (-M10%), "
        "not a bug on dev: dev's --follow uses git's default threshold. "
        "Red when -M10% is put back (mutation-checked)."
    )
    def test_attribution_does_not_give_the_new_entry_the_deleted_ones_history(self, m10):
        repo, c_secret, c_public = m10
        repo.index()
        repo.attribute()
        assert repo.versions("p1") == [(c_public, "created")]
        assert repo.svc.get_entry_at_version("p1", "k", c_secret) is None

    def test_record_commit_calls_the_add_created(self, m10):
        repo, c_secret, c_public = m10
        repo.index()
        assert repo.svc.record_commit("k", c_public) == 1
        assert repo.change_type("p1", c_public) == "created"


class TestSamePathReusedByAnotherEntry:
    """Entry s1 at `notes.md` is deleted; later a different entry p1 is
    created at the same path (filenames come from titles, so this is
    ordinary). git's history of the path runs through both lives, and no
    rename is involved, so only the add (and the id) separates them."""

    @pytest.fixture
    def reused(self, repo):
        repo.write("kb/notes.md", _note("s1", "Notes", "Private draft: text only s1 has."))
        c_s1 = repo.commit("s1")
        (repo.kb / "notes.md").unlink()
        repo.commit("delete s1")
        repo.write("kb/notes.md", _note("p1", "Notes", "Public text."))
        c_p1 = repo.commit("p1 at the same path")
        return repo, c_s1, c_p1

    def test_file_log_reports_each_commits_status(self, reused):
        repo, c_s1, c_p1 = reused
        log = GitService.get_file_log(repo.kb, "notes.md")
        assert [(e["hash"], e["status"]) for e in log][0] == (c_p1, "A")

    def test_attribution_stops_at_the_add(self, reused):
        repo, c_s1, c_p1 = reused
        repo.index()
        repo.attribute()
        assert repo.versions("p1") == [(c_p1, "created")]
        assert repo.svc.get_entry_at_version("p1", "k", c_s1) is None

    def test_a_row_for_the_earlier_entrys_commit_is_not_served(self, reused):
        """A row an earlier build recorded for the path's previous life: read
        at the entry's current path, but the file then held another id."""
        repo, c_s1, c_p1 = reused
        repo.index()
        repo.db.upsert_entry_version(
            entry_id="p1",
            kb_name="k",
            commit_hash=c_s1,
            author_name="T",
            author_email="t@example.com",
            commit_date="2026-01-01T00:00:00+00:00",
            change_type="created",
        )
        assert repo.svc.get_entry_at_version("p1", "k", c_s1) is None

    @pytest.mark.control(
        reason="Guards against an over-strict fix: dev serves this too. An "
        "entry with no explicit id: derives it from its title, so a title "
        "edit must not hide its earlier versions at the same path."
    )
    def test_a_title_edit_without_an_explicit_id_keeps_its_versions(self, repo):
        repo.write("kb/n.md", "---\ntitle: Old\ntype: note\n---\n\nfirst\n")
        c1 = repo.commit("v1")
        repo.write("kb/n.md", "---\ntitle: New\ntype: note\n---\n\nsecond\n")
        c2 = repo.commit("retitle")
        repo.index()
        repo.attribute()
        (entry_id,) = [e["id"] for e in repo.db.get_entries_for_indexing("k")]
        assert [h for h, _ in repo.versions(entry_id)] == [c2, c1]
        assert "first" in repo.svc.get_entry_at_version(entry_id, "k", c1)


class TestARenameMustKeepTheEntryId:
    """Frontmatter-heavy notes with short bodies: git's DEFAULT threshold
    pairs the delete of `s2.md` with the add of `p2.md` (checked in the
    fixture). Only the entry id tells a rename from a replacement."""

    @pytest.fixture
    def paired(self, repo):
        repo.write("kb/s2.md", _boilerplate_note("s2", "S", "Alice."))
        c_s2 = repo.commit("add s2")
        (repo.kb / "s2.md").unlink()
        repo.write("kb/p2.md", _boilerplate_note("p2", "P", "Bob."))
        c_p2 = repo.commit("delete s2, add p2")
        status = _git(repo.root, "show", "--name-status", "--format=", c_p2)
        assert status.startswith("R"), f"fixture: git did not pair the files: {status!r}"
        return repo, c_s2, c_p2

    def test_attribution_stops_at_a_rename_that_changes_the_id(self, paired):
        repo, c_s2, c_p2 = paired
        repo.index()
        repo.attribute()
        assert repo.versions("p2") == [(c_p2, "created")]
        assert repo.svc.get_entry_at_version("p2", "k", c_s2) is None

    def test_record_commit_calls_a_rename_that_changes_the_id_created(self, paired):
        repo, c_s2, c_p2 = paired
        repo.index()
        repo.svc.record_commit("k", c_p2)
        assert repo.change_type("p2", c_p2) == "created"

    def test_a_recorded_row_pointing_at_another_entrys_file_is_not_served(self, paired):
        """A row an earlier build recorded (or any row) whose stored path
        holds a different entry at that commit is refused at read time."""
        repo, c_s2, c_p2 = paired
        repo.index()
        repo.db.upsert_entry_version(
            entry_id="p2",
            kb_name="k",
            commit_hash=c_s2,
            author_name="T",
            author_email="t@example.com",
            commit_date="2026-01-01T00:00:00+00:00",
            change_type="created",
            file_path="s2.md",
        )
        assert repo.svc.get_entry_at_version("p2", "k", c_s2) is None

    def test_a_real_rename_keeps_its_history_and_is_a_modification(self, repo):
        repo.write("kb/a.md", _boilerplate_note("e1", "E", "Original body."))
        c1 = repo.commit("add a")
        _git(repo.root, "mv", "kb/a.md", "kb/b.md")
        repo.write("kb/b.md", _boilerplate_note("e1", "E", "Original body, edited."))
        c2 = repo.commit("rename a to b")
        repo.index()
        repo.attribute()
        assert repo.versions("e1") == [(c2, "modified"), (c1, "created")]
        assert "Original body." in repo.svc.get_entry_at_version("e1", "k", c1)

        # record_commit on the rename commit agrees.
        repo.db.session.query(EntryVersion).delete()
        repo.db.session.commit()
        repo.svc.record_commit("k", c2)
        assert repo.change_type("e1", c2) == "modified"


class TestLowSimilarityRenameLimit:
    """Documented limit: a rename bundled with an edit that drops similarity
    below git's default threshold is a new file to git, so the entry's
    history starts at the rename. Its earlier versions are not listed --
    losing them is the price of never linking unrelated files."""

    @pytest.mark.control(
        reason="Documents the accepted limit; it passes on dev too, where "
        "plain --follow already stops at a low-similarity rename."
    )
    def test_history_starts_at_a_low_similarity_rename(self, repo):
        body = [f"line {i}" for i in range(50)]
        repo.write("kb/a.md", _note("e1", "E", "\n".join(body)))
        c1 = repo.commit("add")
        (repo.kb / "a.md").unlink()
        rewritten = [line + " REWRITTEN FROM SCRATCH" for line in body]
        repo.write("kb/z.md", _note("e1", "E", "\n".join(rewritten)))
        c2 = repo.commit("rename with rewrite")
        status = _git(repo.root, "show", "--name-status", "--format=", c2)
        assert not status.startswith("R"), f"fixture: git paired the files: {status!r}"

        hashes = [e["hash"] for e in GitService.get_file_log(repo.kb, "z.md")]
        assert hashes == [c2]
        assert c1 not in hashes


# ---------------------------------------------------------------------------
# 2. Confinement to the KB's subtree fails closed.
# ---------------------------------------------------------------------------


class TestFollowHopsOutsideTheKB:
    """A KB in a subdirectory. `x.md` lived outside the KB (`other/x.md`)
    and was moved in; `git log --follow` walks back to the outside path.

    The KB also held a file at `kb/other/x.md` with the same id before the
    move: without the refusal, the outside path `other/x.md` is taken as
    KB-relative and resolves to that file, so even the id check passes --
    only the refusal stands between the outside history and this KB."""

    @pytest.fixture
    def hop(self, repo):
        repo.write("other/x.md", _note("x1", "X", "Outside the KB."))
        repo.write("kb/other/x.md", _note("x1", "X", "An older copy inside the KB."))
        c0 = repo.commit("outside file, and an in-KB namesake")
        _git(repo.root, "mv", "other/x.md", "kb/x.md")
        _git(repo.root, "rm", "-q", "kb/other/x.md")
        c1 = repo.commit("move x into the KB")
        status = _git(repo.root, "log", "--follow", "--format=%H", "--", "kb/x.md")
        assert c0 in status, "fixture: --follow did not reach the outside path"
        return repo, c0, c1

    def test_file_log_drops_the_outside_commit(self, hop):
        repo, c0, c1 = hop
        log = GitService.get_file_log(repo.kb, "x.md")
        assert [(e["hash"], e["file_path"]) for e in log] == [(c1, "x.md")]

    def test_attribution_records_only_the_in_kb_commit(self, hop):
        repo, c0, c1 = hop
        repo.index()
        repo.attribute()
        assert repo.versions("x1") == [(c1, "created")]
        assert repo.svc.get_entry_at_version("x1", "k", c0) is None


class TestKbPrefixFailsClosed:
    def test_prefix_is_none_when_rev_parse_fails(self, tmp_path):
        not_a_repo = tmp_path / "plain"
        not_a_repo.mkdir()
        assert GitService.get_kb_prefix(not_a_repo) is None

    def test_no_prefix_means_no_paths(self, repo, monkeypatch):
        repo.write("kb/a.md", _note("a1", "A", "a"))
        c = repo.commit("a")
        monkeypatch.setattr(GitService, "get_kb_prefix", staticmethod(lambda _path: None))
        assert GitService.get_file_log(repo.kb, "a.md") == []
        assert GitService.get_commit_file_changes(repo.kb, c) == []

    def test_a_leading_space_in_the_kb_directory_is_kept(self, tmp_path):
        """`" kb"` and `"kb"` are different directories; stripping the
        prefix's whitespace would make `kb/` files look like `" kb"`'s."""
        r = _Repo(tmp_path, kb_dir=" kb")
        try:
            r.write(" kb/own.md", _note("o1", "O", "own"))
            r.commit("own")
            r.write("kb/a.md", _note("a1", "A", "a sibling's file"))
            c = r.commit("sibling")
            assert GitService.get_kb_prefix(r.kb) == " kb/"
            assert GitService.get_commit_file_changes(r.kb, c) == []
            assert GitService.get_file_log(r.kb, "a.md") == []
        finally:
            r.close()


class TestStoredPathShapeIsRefusedAtReadTime:
    """A stored version path is KB-relative. One that climbs out of the KB
    (`..`) or is absolute is refused, not resolved. Each file below carries
    the entry's own id, so only the shape check refuses it."""

    @pytest.fixture
    def stored(self, repo):
        repo.write("kb/a.md", _note("e1", "E", "current"))
        repo.write("outside.md", _note("e1", "E", "OUTSIDE THE KB"))
        repo.write("kb/b.md", _note("e1", "E", "A FILE THE ROW DOES NOT NAME"))
        c = repo.commit("files")
        (repo.kb / "b.md").unlink()
        repo.commit("drop b")
        repo.index()

        def read_with_stored_path(path):
            repo.db.upsert_entry_version(
                entry_id="e1",
                kb_name="k",
                commit_hash=c,
                author_name="T",
                author_email="t@example.com",
                commit_date="2026-01-01T00:00:00+00:00",
                change_type="modified",
                file_path=path,
            )
            return repo.svc.get_entry_at_version("e1", "k", c)

        return read_with_stored_path

    def test_parent_segments_are_refused(self, stored):
        assert stored("../outside.md") is None

    def test_parent_segments_inside_the_kb_are_refused(self, stored):
        assert stored("sub/../b.md") is None

    def test_an_absolute_path_outside_the_kb_is_refused(self, stored):
        # git anchors "./" + "/b.md" inside the KB, reading kb/b.md.
        assert stored("/b.md") is None


# ---------------------------------------------------------------------------
# 3. record_commit skips a bad index row, not the whole commit.
# ---------------------------------------------------------------------------


class TestRecordCommitSkipsABadIndexRow:
    @pytest.mark.parametrize("bad_path", ["", "/somewhere/else/z.md"])
    def test_other_entries_are_still_recorded(self, repo, caplog, bad_path):
        repo.write("kb/a.md", _note("a1", "A", "a"))
        repo.index()
        repo.db.upsert_entry(
            {
                "id": "bad",
                "kb_name": "k",
                "entry_type": "note",
                "title": "Bad",
                "body": "",
                "file_path": bad_path,
            }
        )
        c = repo.commit("a")
        with caplog.at_level(logging.WARNING):
            assert repo.svc.record_commit("k", c) == 1
        assert repo.db.entry_version_exists("a1", "k", c)
        assert "bad" in caplog.text


class TestReadFileAtRefusesUnsafePaths:
    def test_read_file_at_refuses_parent_and_absolute_paths(self, repo):
        repo.write("kb/a.md", _note("a1", "A", "inside"))
        repo.write("outside.md", _note("a1", "A", "outside"))
        c = repo.commit("files")
        assert GitService.read_file_at(repo.kb, c, "a.md") is not None
        assert GitService.read_file_at(repo.kb, c, "../outside.md") is None
        assert GitService.read_file_at(repo.kb, c, "/a.md") is None

    @pytest.mark.control(
        reason="Dev reads the absolute index path, which git cannot find; the "
        "stand-in read only became possible with this PR's KB-relative "
        "rewrite. Red when the rel_path None check is removed (mutation-checked)."
    )
    def test_an_index_path_outside_the_kb_is_not_read_as_a_kb_file(self, repo):
        """An entry whose indexed path is not under the KB, with a version
        row that has no stored path, has no KB-relative path to read: it is
        refused, not read at some stand-in name inside the KB."""
        repo.write("kb/None", "not an entry\n")
        c = repo.commit("a file named None")
        repo.index()
        repo.db.upsert_entry(
            {
                "id": "stray",
                "kb_name": "k",
                "entry_type": "note",
                "title": "Stray",
                "body": "",
                "file_path": "/somewhere/else/stray.md",
            }
        )
        repo.db.upsert_entry_version(
            entry_id="stray",
            kb_name="k",
            commit_hash=c,
            author_name="T",
            author_email="t@example.com",
            commit_date="2026-01-01T00:00:00+00:00",
        )
        assert repo.svc.get_entry_at_version("stray", "k", c) is None


# ---------------------------------------------------------------------------
# 5. A version list offers only versions a read can serve (#453).
# ---------------------------------------------------------------------------


def _seed_row(repo, entry_id, commit, **kw):
    """A row an earlier build recorded (a stale one, when the walk now
    rejects the commit)."""
    repo.db.upsert_entry_version(
        entry_id=entry_id,
        kb_name="k",
        commit_hash=commit,
        author_name="T",
        author_email="t@example.com",
        commit_date=kw.pop("commit_date", "2026-01-01T00:00:00+00:00"),
        change_type="created",
        **kw,
    )


class TestVersionListOffersOnlyReadableVersions:
    """`entry_version` is derived from git, so an attribution pass replaces an
    entry's rows with the commits the history walk accepts. A row the walk
    rejects (another entry's commit at a reused path, #432) must not stay
    listed while the read refuses it with a 404 (#453). The rows gate which
    commits a read may serve (#415): removing one only narrows."""

    def test_an_entry_with_only_stale_rows_ends_with_none(self, repo):
        repo.write("kb/a.md", _note("a1", "A", "text"))
        c_a = repo.commit("a")
        repo.write("kb/other.md", _note("o1", "Other", "text"))
        c_o = repo.commit("other")
        repo.index()
        # a1's only row is a commit that never touched a1's file.
        _seed_row(repo, "a1", c_o, file_path="other.md")
        repo.attribute()
        assert repo.versions("a1") == [(c_a, "created")]

    @pytest.mark.control(
        reason="Guards against an over-eager replace: dev keeps these rows too; "
        "rows the walk accepts must survive a rebuild unchanged."
    )
    def test_rows_the_walk_accepts_are_untouched(self, repo):
        repo.write("kb/n.md", _note("n1", "N", "one"))
        c1 = repo.commit("v1")
        repo.write("kb/n.md", _note("n1", "N", "two"))
        c2 = repo.commit("v2")
        repo.index()
        assert repo.svc.record_commit("k", c2) == 1  # a row from the write path
        repo.attribute()
        before = repo.versions("n1")
        repo.attribute()
        assert before == repo.versions("n1") == [(c2, "modified"), (c1, "created")]

    def test_since_commit_replaces_only_the_re_read_entries_rows(self, tmp_path):
        # KB at the repo root: `git diff --name-only` is repo-relative, so a
        # KB in a subdirectory re-reads nothing under since_commit (#898).
        repo = _Repo(tmp_path, kb_dir="")
        try:
            repo.write("a.md", _note("a1", "A", "text"))
            c_a = repo.commit("a")
            repo.write("b.md", _note("b1", "B", "text"))
            c_b = repo.commit("b")
            repo.index()
            _seed_row(repo, "a1", c_b, file_path="b.md")
            _seed_row(repo, "b1", c_a, file_path="a.md")
            # Only b.md changed since c_a: only b1 is re-read.
            IndexManager(repo.db, repo.config).index_with_attribution(
                "k", GitService, since_commit=c_a
            )
            assert [h for h, _ in repo.versions("b1")] == [c_b]
            assert c_b in [h for h, _ in repo.versions("a1")]  # not re-read: left alone
        finally:
            repo.close()

    @pytest.mark.control(
        reason="Guards against an over-eager delete: an empty walk is absence "
        "of evidence (not a git repo / file git does not know), so rows stay."
    )
    def test_an_empty_walk_deletes_nothing(self, tmp_path):
        r = _Repo(tmp_path)
        try:
            r.write("kb/n.md", _note("n1", "N", "text"))  # never committed
            r.index()
            _seed_row(r, "n1", "a" * 40, file_path="n.md")
            r.attribute()
            assert [h for h, _ in r.versions("n1")] == ["a" * 40]
        finally:
            r.close()

    @pytest.mark.control(reason="Not a git repository: nothing was learned, so nothing is deleted.")
    def test_a_kb_outside_git_keeps_its_rows(self, tmp_path):
        kb = tmp_path / "plain"
        kb.mkdir()
        (kb / "n.md").write_text(_note("n1", "N", "text"))
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="k", path=kb)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        db = PyriteDB(tmp_path / "index.db")
        try:
            IndexManager(db, config).index_all()
            db.upsert_entry_version(
                entry_id="n1",
                kb_name="k",
                commit_hash="b" * 40,
                author_name="T",
                author_email="t@example.com",
                commit_date="2026-01-01T00:00:00+00:00",
            )
            IndexManager(db, config).index_with_attribution("k", GitService)
            assert [v["commit_hash"] for v in db.get_entry_versions("n1", "k")] == ["b" * 40]
        finally:
            db.close()


class TestAttributionNeverDropsAServableRow:
    """Property (#453, round 1): attribution deletes a version row only when
    the version read would refuse it. Every way a row can be servable yet
    absent from the current walk is spared by the servability check."""

    @pytest.mark.control(
        reason="Dev keeps every row, so this passes there; it fails against a "
        "delete-everything-outside-the-walk implementation (round 0 of #453)."
    )
    def test_a_row_recorded_between_the_walk_and_the_delete_survives(self, repo):
        """`record_commit` (the server's write path) can add a row after
        `get_file_log` ran and before stale rows are dropped. Deterministic
        stand-in: a subclass hook makes the commit at that exact point."""
        repo.write("kb/n.md", _note("n1", "N", "one"))
        c1 = repo.commit("v1")
        repo.index()
        late = {}

        class Racing(IndexManager):
            def _drop_unservable_versions(self, *a, **kw):
                repo.write("kb/n.md", _note("n1", "N", "two"))
                late["hash"] = repo.commit("written during the pass")
                assert repo.svc.record_commit("k", late["hash"]) == 1
                super()._drop_unservable_versions(*a, **kw)

        Racing(repo.db, repo.config).index_with_attribution("k", GitService)
        hashes = [h for h, _ in repo.versions("n1")]
        assert late["hash"] in hashes and c1 in hashes
        assert repo.svc.get_entry_at_version("n1", "k", late["hash"]) is not None

    @pytest.mark.control(
        reason="Dev keeps every row, so this passes there; it fails against a "
        "delete-everything-outside-the-walk implementation (round 0 of #453)."
    )
    def test_an_undetected_rename_keeps_the_older_rows(self, repo):
        repo.write("kb/q.md", _note("n1", "N", "alpha beta gamma " * 40))
        c1 = repo.commit("c1")
        repo.index()
        repo.attribute()
        (repo.kb / "q.md").unlink()
        repo.write("kb/p.md", _note("n1", "N", "totally different words here " * 40))
        c2 = repo.commit("move and rewrite")
        repo.index()
        repo.attribute()
        assert {h for h, _ in repo.versions("n1")} == {c1, c2}
        assert repo.svc.get_entry_at_version("n1", "k", c1) is not None


class TestAttributionDeletesOnlyOnAPositiveFact:
    """Attribution deletes a version row only on a positive fact: the stored
    path is unsafe, or git read the blob at that path in that commit and it
    holds another entry (#453, restart). Every other outcome keeps the row:
    git's stderr is never classified, because "exists on disk, but not in"
    covers both "path absent from this commit" and "commit unreadable".
    Failures are injected at the git subprocess edge or in `.git/objects`,
    with a real git KB underneath; no service is patched."""

    @pytest.fixture
    def undetected_rename(self, repo):
        repo.write("kb/q.md", _note("n1", "N", "alpha beta gamma " * 40))
        c1 = repo.commit("c1")
        repo.index()
        repo.attribute()
        (repo.kb / "q.md").unlink()
        repo.write("kb/p.md", _note("n1", "N", "totally different words here " * 40))
        c2 = repo.commit("move and rewrite")
        repo.index()
        return repo, c1, c2

    @staticmethod
    def _failing_read(outcome):
        """A `subprocess.run` that fails every version read (a git command
        naming `<rev>:./<path>`, however the read spells the rest) and passes
        all other git to real git."""
        real = subprocess.run

        def run(cmd, *a, **kw):
            if cmd[:1] == ["git"] and any(":./" in str(arg) for arg in cmd):
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome
            return real(cmd, *a, **kw)

        return run

    @pytest.mark.control(
        reason="Dev keeps every row. Guards against acting on a git failure; "
        "the does-not-exist stderr case fails on round 2, which classified it."
    )
    @pytest.mark.parametrize(
        "outcome",
        [
            subprocess.TimeoutExpired(["git"], 10),
            OSError("git vanished"),
            subprocess.CompletedProcess([], 128, b"", b"fatal: unable to read tree abc"),
            subprocess.CompletedProcess([], 128, b"", b"fatal: path 'q.md' does not exist in 'x'"),
        ],
        ids=["timeout", "oserror", "unreadable-tree", "does-not-exist-stderr"],
    )
    def test_a_git_failure_keeps_the_row_and_a_clean_rerun_still_has_it(
        self, undetected_rename, outcome
    ):
        from unittest.mock import patch

        repo, c1, _ = undetected_rename
        with patch("pyrite.services.git_service.subprocess.run", self._failing_read(outcome)):
            repo.attribute()
        assert c1 in [h for h, _ in repo.versions("n1")]
        repo.attribute()
        assert c1 in [h for h, _ in repo.versions("n1")]
        assert repo.svc.get_entry_at_version("n1", "k", c1) is not None

    @staticmethod
    def _side_commit(repo):
        """A commit of n.md that HEAD's history does not reach (an earlier
        build recorded it, then the branch moved): the walk never accepts it,
        so only the servability check decides its row."""
        _git(repo.root, "checkout", "-q", "-b", "side")
        repo.write("kb/n.md", _note("n1", "N", "side edit"))
        c_side = repo.commit("side")
        _git(repo.root, "checkout", "-q", "-")
        return c_side

    @pytest.mark.control(
        reason="Dev keeps every row; 'missing' fails on round 2, which deleted it."
    )
    @pytest.mark.parametrize("damage", ["missing", "corrupt"])
    def test_an_unreadable_commit_object_keeps_the_row(self, repo, damage):
        """A missing commit fails on round 2: git's stderr for it matched
        what round 2 read as "definitely absent", and the row was deleted."""
        repo.write("kb/n.md", _note("n1", "N", "one"))
        c1 = repo.commit("v1")
        c_side = self._side_commit(repo)
        repo.index()
        _seed_row(repo, "n1", c_side, file_path="n.md")
        obj = repo.root / ".git" / "objects" / c_side[:2] / c_side[2:]
        obj.chmod(0o644)
        if damage == "missing":
            obj.unlink()
        else:
            obj.write_bytes(b"garbage")
        repo.attribute()
        assert {h for h, _ in repo.versions("n1")} == {c1, c_side}
        assert repo.svc.get_entry_at_version("n1", "k", c_side) is None  # still refused

    @pytest.mark.control(reason="Dev keeps every row; fails on round 2, which deleted it.")
    def test_a_shallow_clone_boundary_keeps_rows_older_than_the_clone(self, tmp_path):
        """Fails on round 2: the commit past the shallow boundary is absent,
        which round 2 classified as a definite refusal."""
        (tmp_path / "src_root").mkdir()
        src = _Repo(tmp_path / "src_root")
        try:
            src.write("kb/n.md", _note("n1", "N", "one"))
            c1 = src.commit("v1")
            src.write("kb/n.md", _note("n1", "N", "two"))
            c2 = src.commit("v2")
        finally:
            src.close()
        clone_parent = tmp_path / "clone_root"
        clone_parent.mkdir()
        subprocess.run(
            [
                "git",
                "clone",
                "-q",
                "--depth",
                "1",
                f"file://{src.root}",
                str(clone_parent / "repo"),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        kb = clone_parent / "repo" / "kb"
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="k", path=kb)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        db = PyriteDB(tmp_path / "index.db")
        try:
            IndexManager(db, config).index_all()
            # A full-history build recorded c1 before the KB was re-cloned shallow.
            db.upsert_entry_version(
                entry_id="n1",
                kb_name="k",
                commit_hash=c1,
                author_name="T",
                author_email="t@example.com",
                commit_date="2026-01-01T00:00:00+00:00",
                file_path="n.md",
            )
            IndexManager(db, config).index_with_attribution("k", GitService)
            hashes = {v["commit_hash"] for v in db.get_entry_versions("n1", "k")}
            assert hashes == {c1, c2}
        finally:
            db.close()

    def test_a_non_utf8_old_version_reads_as_none_and_keeps_its_row(self, repo):
        """Fails on round 2: its read decoded with text=True outside any
        handler, so both the read and attribution raised UnicodeDecodeError."""
        (repo.kb / "q.md").write_bytes(
            b"---\nid: n1\ntitle: N\n---\n" + b"caf\xe9 latin-1 body " * 40 + b"\n"
        )
        c1 = repo.commit("latin-1 version")
        repo.index()
        (repo.kb / "q.md").unlink()
        repo.write("kb/p.md", _note("n1", "N", "totally different words here " * 40))
        c2 = repo.commit("move and rewrite")
        repo.index()
        _seed_row(repo, "n1", c1, file_path="q.md")
        assert repo.svc.get_entry_at_version("n1", "k", c1) is None
        repo.attribute()
        assert {h for h, _ in repo.versions("n1")} == {c1, c2}

    @pytest.mark.control(
        reason="Dev keeps every row; fails on round 2, which read git's "
        "'does not exist in' as a refusal and deleted it."
    )
    def test_a_path_absent_from_a_readable_commit_keeps_its_row(self, repo):
        """Not a positive fact under the narrowed rule: the commit was read,
        but nothing at the stored path says another entry is there. The read
        refuses this version anyway, so keeping the row is the safe side."""
        repo.write("kb/n.md", _note("n1", "N", "text"))
        c1 = repo.commit("v1")
        c_side = self._side_commit(repo)
        repo.index()
        _seed_row(repo, "n1", c_side, file_path="gone.md")  # c_side has no gone.md
        repo.attribute()
        assert {h for h, _ in repo.versions("n1")} == {c1, c_side}
        assert repo.svc.get_entry_at_version("n1", "k", c_side) is None

    def test_a_readable_commit_holding_another_id_deletes_the_row(self, repo):
        repo.write("kb/n.md", _note("n1", "N", "text"))
        c1 = repo.commit("v1")
        repo.write("kb/other.md", _note("o1", "Other", "text"))
        c_o = repo.commit("other")
        repo.index()
        _seed_row(repo, "n1", c_o, file_path="other.md")
        repo.attribute()
        assert [h for h, _ in repo.versions("n1")] == [c1]

    def test_an_unsafe_stored_path_deletes_the_row(self, repo):
        repo.write("kb/n.md", _note("n1", "N", "text"))
        c1 = repo.commit("v1")
        repo.index()
        _seed_row(repo, "n1", "c" * 40, file_path="../n.md")  # never resolved
        repo.attribute()
        assert [h for h, _ in repo.versions("n1")] == [c1]

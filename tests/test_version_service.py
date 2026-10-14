"""Tests for VersionService (extracted from KBService)."""

import subprocess

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import InvalidGitRefError
from pyrite.services.version_service import VersionService
from pyrite.storage.database import PyriteDB


@pytest.fixture
def version_setup(tmp_path):
    """Set up VersionService with a git-backed KB."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()

    # Init git repo
    subprocess.run(["git", "init"], cwd=str(kb_path), capture_output=True, timeout=120)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(kb_path),
        capture_output=True,
        timeout=120,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=str(kb_path), capture_output=True, timeout=120
    )

    # Write and commit an entry
    entry_file = kb_path / "entry-1.md"
    entry_file.write_text("---\nid: entry-1\ntitle: V1\ntype: note\n---\n\nVersion 1")
    subprocess.run(["git", "add", "."], cwd=str(kb_path), capture_output=True, timeout=120)
    subprocess.run(
        ["git", "commit", "-m", "v1"], cwd=str(kb_path), capture_output=True, timeout=120
    )

    # Get commit hash
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(kb_path),
        capture_output=True,
        text=True,
        timeout=120,
    )
    commit1 = result.stdout.strip()

    # Update and commit again
    entry_file.write_text("---\nid: entry-1\ntitle: V2\ntype: note\n---\n\nVersion 2")
    subprocess.run(["git", "add", "."], cwd=str(kb_path), capture_output=True, timeout=120)
    subprocess.run(
        ["git", "commit", "-m", "v2"], cwd=str(kb_path), capture_output=True, timeout=120
    )

    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="test-kb", path=kb_path)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")

    # Index entries
    from pyrite.storage.index import IndexManager

    idx = IndexManager(db, config)
    idx.index_all()

    # Record commit1 as one of entry-1's known versions (#415: a version is
    # only servable when it is a recorded version of the entry, not merely
    # any commit in the repo).
    db.upsert_entry_version(
        entry_id="entry-1",
        kb_name="test-kb",
        commit_hash=commit1,
        author_name="Test",
        author_email="test@test.com",
        commit_date="2026-01-01T00:00:00+00:00",
    )

    svc = VersionService(config, db)
    yield svc, db, commit1
    db.close()


class TestVersionService:
    def test_get_entry_versions_empty_without_saves(self, version_setup):
        svc, db, commit1 = version_setup
        # Versions are only tracked when entries are saved through KBService,
        # not from raw git commits, so this returns empty.
        versions = svc.get_entry_versions("entry-1", "test-kb")
        assert isinstance(versions, list)

    def test_get_entry_at_version(self, version_setup):
        svc, db, commit1 = version_setup
        content = svc.get_entry_at_version("entry-1", "test-kb", commit1)
        assert content is not None
        assert "Version 1" in content

    def test_get_entry_at_version_nonexistent_kb(self, version_setup):
        svc, db, commit1 = version_setup
        content = svc.get_entry_at_version("entry-1", "nonexistent", commit1)
        assert content is None

    def test_get_entry_at_version_bad_commit(self, version_setup):
        svc, db, commit1 = version_setup
        content = svc.get_entry_at_version("entry-1", "test-kb", "0" * 40)
        assert content is None


# ---------------------------------------------------------------------------
# #333: the commit hash is a hex object id, checked before any git call
# ---------------------------------------------------------------------------

REFUSED_HASHES = [
    "-x",
    "--output=pwned",
    "HEAD",
    "main",
    "abc",  # too short
    "a" * 65,  # too long
    "abcd\n",  # a trailing newline must not slip past an anchored match
    "abcd:entry-1.md",
    "",
]


class TestCommitHashValidation:
    @pytest.mark.parametrize("bad", REFUSED_HASHES)
    def test_refused_before_any_git_call(self, version_setup, monkeypatch, bad):
        svc, db, commit1 = version_setup

        def _no_subprocess(*args, **kwargs):
            raise AssertionError(f"subprocess.run called for refused hash: {args!r}")

        monkeypatch.setattr(subprocess, "run", _no_subprocess)
        with pytest.raises(InvalidGitRefError):
            svc.get_entry_at_version("entry-1", "test-kb", bad)

    def test_short_hash_still_resolves(self, version_setup):
        svc, db, commit1 = version_setup
        content = svc.get_entry_at_version("entry-1", "test-kb", commit1[:7])
        assert content is not None
        assert "Version 1" in content

    def test_uppercase_hex_accepted(self, version_setup):
        svc, db, commit1 = version_setup
        content = svc.get_entry_at_version("entry-1", "test-kb", commit1.upper())
        assert content is not None
        assert "Version 1" in content

    def test_git_show_ends_options_before_the_object(self, version_setup, monkeypatch):
        svc, db, commit1 = version_setup
        real_run = subprocess.run
        seen = []

        def _spy(argv, *args, **kwargs):
            seen.append(list(argv))
            return real_run(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", _spy)
        # An abbreviated id: the read must use the peeled full commit id.
        assert svc.get_entry_at_version("entry-1", "test-kb", commit1[:7]) is not None
        from pyrite.services.git_service import READ_BLOB_ARGV

        show = [a for a in seen if tuple(a[: len(READ_BLOB_ARGV)]) == READ_BLOB_ARGV]
        assert len(show) == 1, seen
        argv = show[0]
        assert argv.index("--end-of-options") == len(argv) - 2
        # `./` anchors the path to cwd (the KB dir) rather than the repo
        # root, so a KB that is a subdirectory of its repo still resolves
        # (#415).
        assert argv[-1] == f"{commit1}:./entry-1.md"
        peels = [a for a in seen if a[:2] == ["git", "rev-parse"] and commit1[:7] in a[-1]]
        assert peels, seen
        for a in peels:
            assert a.index("--end-of-options") == len(a) - 2, a


def _git(kb_path, *args):
    return subprocess.run(
        ["git", *args],
        cwd=str(kb_path),
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout.strip()


class TestCommitObjectsOnly:
    """A hex id must name a commit: a tree or blob id is refused (#333 cold read)."""

    @pytest.mark.parametrize("kind", ["tree", "blob"])
    def test_non_commit_object_refused(self, version_setup, kind):
        svc, db, commit1 = version_setup
        kb_path = svc.config.get_kb("test-kb").path
        obj = _git(kb_path, "rev-parse", f"{commit1}^{{tree}}")
        if kind == "blob":
            obj = _git(kb_path, "rev-parse", f"{commit1}:entry-1.md")
        with pytest.raises(InvalidGitRefError):
            svc.get_entry_at_version("entry-1", "test-kb", obj)

    def test_annotated_tag_peels_to_its_commit(self, version_setup):
        svc, db, commit1 = version_setup
        kb_path = svc.config.get_kb("test-kb").path
        _git(kb_path, "tag", "-a", "v1", "-m", "v1", commit1)
        tag = _git(kb_path, "rev-parse", "v1")
        assert tag != commit1
        content = svc.get_entry_at_version("entry-1", "test-kb", tag)
        assert content is not None and "Version 1" in content

    def test_unknown_object_is_not_found_not_refused(self, version_setup):
        svc, db, commit1 = version_setup
        assert svc.get_entry_at_version("entry-1", "test-kb", "0" * 40) is None


class TestVersionEndpointHash:
    @pytest.fixture
    def client(self, version_setup):
        from fastapi.testclient import TestClient

        from pyrite.server.api import create_app, get_config, get_db

        svc, db, commit1 = version_setup
        application = create_app(config=svc.config)
        application.dependency_overrides[get_config] = lambda: svc.config
        application.dependency_overrides[get_db] = lambda: db
        kb_path = svc.config.get_kb("test-kb").path
        return TestClient(application), kb_path, commit1

    @staticmethod
    def _tree(kb_path):
        return sorted(p.relative_to(kb_path).as_posix() for p in kb_path.rglob("*"))

    @pytest.mark.parametrize("bad", ["--output=pwned", "-x", "HEAD"])
    def test_refused_with_400_and_nothing_written(self, client, bad):
        c, kb_path, _ = client
        before = self._tree(kb_path)
        r = c.get(f"/api/entries/entry-1/versions/{bad}", params={"kb": "test-kb"})
        assert self._tree(kb_path) == before, "the request created a file in the KB"
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "INVALID_REF"

    def test_tree_id_refused_with_400(self, client):
        c, kb_path, commit1 = client
        tree = _git(kb_path, "rev-parse", f"{commit1}^{{tree}}")
        r = c.get(f"/api/entries/entry-1/versions/{tree}", params={"kb": "test-kb"})
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "INVALID_REF"

    def test_real_hash_still_served(self, client):
        c, _, commit1 = client
        for h in (commit1, commit1[:7]):
            r = c.get(f"/api/entries/entry-1/versions/{h}", params={"kb": "test-kb"})
            assert r.status_code == 200, r.text
            assert "Version 1" in r.json()["content"]

    def test_unknown_but_valid_hash_is_404(self, client):
        c, _, _ = client
        r = c.get(f"/api/entries/entry-1/versions/{'0' * 40}", params={"kb": "test-kb"})
        assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# #415: a version is served only when it is one of this entry's recorded
# versions (db.get_entry_versions), and the read works for a KB that is a
# subdirectory of its repo.
# ---------------------------------------------------------------------------


class TestRecordedVersionMembership:
    def test_foreign_commit_is_not_found(self, version_setup):
        """A commit in the KB's repo that never touched this entry -- it
        touched a sibling file -- must not be served, even though the path
        happens to exist in that commit's tree."""
        svc, db, commit1 = version_setup
        kb_path = svc.config.get_kb("test-kb").path

        # A commit that only touches a sibling file, not entry-1.md.
        sibling = kb_path / "sibling.md"
        sibling.write_text("---\nid: sibling\ntitle: Sibling\ntype: note\n---\n\nSibling")
        _git(kb_path, "add", ".")
        _git(kb_path, "commit", "-m", "add sibling")
        foreign = _git(kb_path, "rev-parse", "HEAD")

        # entry-1.md exists in that commit's tree (unchanged from commit1),
        # so a naive rev-parse + git-show would happily serve it.
        assert svc.get_entry_at_version("entry-1", "test-kb", foreign) is None

    def test_foreign_commit_is_404_over_http(self, version_setup):
        from fastapi.testclient import TestClient

        from pyrite.server.api import create_app, get_config, get_db

        svc, db, commit1 = version_setup
        kb_path = svc.config.get_kb("test-kb").path
        sibling = kb_path / "sibling.md"
        sibling.write_text("---\nid: sibling\ntitle: Sibling\ntype: note\n---\n\nSibling")
        _git(kb_path, "add", ".")
        _git(kb_path, "commit", "-m", "add sibling")
        foreign = _git(kb_path, "rev-parse", "HEAD")

        application = create_app(config=svc.config)
        application.dependency_overrides[get_config] = lambda: svc.config
        application.dependency_overrides[get_db] = lambda: db
        c = TestClient(application)
        r = c.get(f"/api/entries/entry-1/versions/{foreign}", params={"kb": "test-kb"})
        assert r.status_code == 404, r.text

    @pytest.mark.control(
        reason="commit1 is HEAD~1 in the fixture's repo, so it was already "
        "servable before the membership check (#415) was added; this pins "
        "that the fix does not regress the ordinary case, not the fix itself."
    )
    def test_recorded_version_is_served(self, version_setup):
        """commit1 is recorded by the version_setup fixture, and is servable."""
        svc, db, commit1 = version_setup
        content = svc.get_entry_at_version("entry-1", "test-kb", commit1)
        assert content is not None
        assert "Version 1" in content

    @pytest.mark.control(
        reason="commit1 was already resolvable pre-fix (see "
        "test_recorded_version_is_served); this pins that an abbreviated "
        "form of a recorded hash still matches post-fix, not the fix itself."
    )
    def test_recorded_abbreviated_hash_still_matches(self, version_setup):
        """Membership is checked on the peeled full commit id, so a caller
        using an abbreviated form of a recorded hash still resolves."""
        svc, db, commit1 = version_setup
        content = svc.get_entry_at_version("entry-1", "test-kb", commit1[:7])
        assert content is not None
        assert "Version 1" in content


class TestSubdirectoryKB:
    """A KB whose configured path is a subdirectory of its git repo must
    still be able to read a recorded version's content (#415)."""

    @pytest.fixture
    def subdir_setup(self, tmp_path):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo_root), capture_output=True, timeout=120)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo_root),
            capture_output=True,
            timeout=120,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(repo_root),
            capture_output=True,
            timeout=120,
        )

        kb_path = repo_root / "sub" / "kb"
        kb_path.mkdir(parents=True)
        entry_file = kb_path / "entry-1.md"
        entry_file.write_text("---\nid: entry-1\ntitle: V1\ntype: note\n---\n\nVersion 1")
        subprocess.run(["git", "add", "."], cwd=str(repo_root), capture_output=True, timeout=120)
        subprocess.run(
            ["git", "commit", "-m", "v1"], cwd=str(repo_root), capture_output=True, timeout=120
        )
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        commit1 = result.stdout.strip()

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="sub-kb", path=kb_path)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        db = PyriteDB(tmp_path / "index.db")

        from pyrite.storage.index import IndexManager

        idx = IndexManager(db, config)
        idx.index_all()

        db.upsert_entry_version(
            entry_id="entry-1",
            kb_name="sub-kb",
            commit_hash=commit1,
            author_name="Test",
            author_email="test@test.com",
            commit_date="2026-01-01T00:00:00+00:00",
        )

        svc = VersionService(config, db)
        yield svc, db, commit1, kb_path
        db.close()

    def test_subdirectory_kb_serves_recorded_version(self, subdir_setup):
        svc, db, commit1, kb_path = subdir_setup
        content = svc.get_entry_at_version("entry-1", "sub-kb", commit1)
        assert content is not None
        assert "Version 1" in content


# ---------------------------------------------------------------------------
# #432: a server write records a version, readable immediately -- and a
# renamed entry's pre-rename version is readable.
# ---------------------------------------------------------------------------


class TestRecordCommit:
    """VersionService.record_commit(kb_name, commit_hash) turns a commit's
    changed entry files into entry_version rows, called from
    ExportService.commit_kb after a successful commit (#432)."""

    @pytest.fixture
    def kb_setup(self, tmp_path):
        """A git-backed KB with one committed entry, indexed but with no
        entry_version rows recorded -- as if committed through the server
        before #432, where nothing wrote a version row."""
        kb_path = tmp_path / "test-kb"
        kb_path.mkdir()
        subprocess.run(["git", "init"], cwd=str(kb_path), capture_output=True, timeout=120)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(kb_path),
            capture_output=True,
            timeout=120,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(kb_path),
            capture_output=True,
            timeout=120,
        )

        entry_file = kb_path / "a.md"
        entry_file.write_text("---\nid: entry-1\ntitle: V1\ntype: note\n---\n\nVersion 1")
        subprocess.run(["git", "add", "."], cwd=str(kb_path), capture_output=True, timeout=120)
        subprocess.run(
            ["git", "commit", "-m", "v1"], cwd=str(kb_path), capture_output=True, timeout=120
        )
        commit1 = _git(kb_path, "rev-parse", "HEAD")

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="test-kb", path=kb_path)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        db = PyriteDB(tmp_path / "index.db")

        from pyrite.storage.index import IndexManager

        idx = IndexManager(db, config)
        idx.index_all()

        svc = VersionService(config, db)
        yield svc, db, config, kb_path, commit1
        db.close()

    def test_server_commit_recorded_and_readable_immediately(self, kb_setup):
        svc, db, config, kb_path, commit1 = kb_setup

        # No reindex -- record_commit is called right after the commit,
        # the way ExportService.commit_kb will call it.
        n = svc.record_commit("test-kb", commit1)
        assert n == 1

        versions = svc.get_entry_versions("entry-1", "test-kb")
        assert len(versions) == 1
        assert versions[0]["commit_hash"] == commit1

        content = svc.get_entry_at_version("entry-1", "test-kb", commit1)
        assert content is not None
        assert "Version 1" in content

    def test_commit_touching_two_entries_records_two_rows(self, kb_setup):
        """A server write indexes on save (KBService._prepare_and_save ->
        DocumentManager.save_entry), before the commit happens -- so by the
        time record_commit runs, a newly-written entry is already in the
        index. Reindex here mirrors that ordering, not a raw file write."""
        svc, db, config, kb_path, commit1 = kb_setup
        svc.record_commit("test-kb", commit1)

        (kb_path / "b.md").write_text("---\nid: entry-2\ntitle: B\ntype: note\n---\n\nB1")
        (kb_path / "a.md").write_text("---\nid: entry-1\ntitle: V2\ntype: note\n---\n\nVersion 2")

        from pyrite.storage.index import IndexManager

        IndexManager(db, config).index_all()

        _git(kb_path, "add", ".")
        _git(kb_path, "commit", "-m", "two entries")
        commit2 = _git(kb_path, "rev-parse", "HEAD")

        n = svc.record_commit("test-kb", commit2)
        assert n == 2
        assert svc.db.entry_version_exists("entry-1", "test-kb", commit2)
        assert svc.db.entry_version_exists("entry-2", "test-kb", commit2)

    def test_commit_touching_only_non_entry_files_records_none(self, kb_setup):
        svc, db, config, kb_path, commit1 = kb_setup

        (kb_path / "README.md.txt").write_text("not an entry")
        (kb_path / "notes.txt").write_text("also not an entry")
        _git(kb_path, "add", ".")
        _git(kb_path, "commit", "-m", "non-entry files")
        commit2 = _git(kb_path, "rev-parse", "HEAD")

        n = svc.record_commit("test-kb", commit2)
        assert n == 0

    def test_commit_touching_an_md_file_that_is_not_an_indexed_entry_records_none(self, kb_setup):
        """A .md file the KB repository skips or never indexed (here:
        README.md, which KBRepository.list_files always excludes) must not
        produce a row -- it never resolves to an entry id, unlike the
        .md-suffix filter alone, which this isolates from."""
        svc, db, config, kb_path, commit1 = kb_setup

        (kb_path / "README.md").write_text("# Not an entry\n")
        _git(kb_path, "add", ".")
        _git(kb_path, "commit", "-m", "add README")
        commit2 = _git(kb_path, "rev-parse", "HEAD")

        n = svc.record_commit("test-kb", commit2)
        assert n == 0

    def test_recording_is_idempotent(self, kb_setup):
        svc, db, config, kb_path, commit1 = kb_setup
        svc.record_commit("test-kb", commit1)
        svc.record_commit("test-kb", commit1)

        versions = svc.get_entry_versions("entry-1", "test-kb")
        assert len(versions) == 1

    def test_renamed_entry_pre_rename_version_is_readable(self, kb_setup):
        """git mv a.md b.md, commit, run attribution indexing ONLY -- no
        record_commit call for commit1 -- so commit1's stored path comes
        entirely from get_file_log's rename-following --name-status parse,
        not from a fresher record_commit call made while the file still
        lived at its original path. The list shows both commits, and
        reading the pre-rename hash returns the content as of that commit
        (#432 -- today this 404s because get_entry_at_version reads
        `git show <c>:./<current path>`, and the entry's current path is
        now b.md, which didn't exist at commit1)."""
        svc, db, config, kb_path, commit1 = kb_setup

        _git(kb_path, "mv", "a.md", "b.md")
        _git(kb_path, "commit", "-m", "rename a.md to b.md")
        commit2 = _git(kb_path, "rev-parse", "HEAD")

        from pyrite.services.git_service import GitService
        from pyrite.storage.index import IndexManager

        idx = IndexManager(db, config)
        idx.index_with_attribution("test-kb", GitService)

        versions = svc.get_entry_versions("entry-1", "test-kb")
        hashes = {v["commit_hash"] for v in versions}
        assert commit1 in hashes
        assert commit2 in hashes

        pre_rename_content = svc.get_entry_at_version("entry-1", "test-kb", commit1)
        assert pre_rename_content is not None
        assert "Version 1" in pre_rename_content

        post_rename_content = svc.get_entry_at_version("entry-1", "test-kb", commit2)
        assert post_rename_content is not None
        assert "Version 1" in post_rename_content

    @pytest.mark.control(
        reason="Documents an accepted limit (#432): on dev, plain --follow "
        "already stops at a rename below git's default similarity."
    )
    def test_history_starts_at_a_rename_below_default_similarity(self, kb_setup):
        """Limit, not a goal: a rename bundled with an edit that drops
        similarity below git's default threshold is a delete plus an add to
        git. The entry's history starts at the rename; the pre-rename
        commit is neither listed nor served. A lower threshold would keep
        it, but pairs unrelated entries that share frontmatter boilerplate,
        serving one entry's content as another's version."""
        svc, db, config, kb_path, commit1 = kb_setup
        frontmatter = "---\nid: entry-1\ntitle: V1\ntype: note\n---\n\n"
        body_lines = [f"line {i}" for i in range(50)]
        (kb_path / "a.md").write_text(frontmatter + "\n".join(body_lines))
        _git(kb_path, "add", ".")
        _git(kb_path, "commit", "-m", "bulk content")
        commit1 = _git(kb_path, "rev-parse", "HEAD")

        _git(kb_path, "mv", "a.md", "b.md")
        for i in range(0, len(body_lines), 2):
            body_lines[i] = body_lines[i] + " CHANGED"
        (kb_path / "b.md").write_text(frontmatter + "\n".join(body_lines))
        _git(kb_path, "add", "-A")
        _git(kb_path, "commit", "-m", "rename with partial rewrite")
        commit2 = _git(kb_path, "rev-parse", "HEAD")

        status = _git(kb_path, "show", "--name-status", "--format=", commit2)
        assert not status.startswith("R"), f"fixture: git paired the files: {status!r}"

        from pyrite.services.git_service import GitService
        from pyrite.storage.index import IndexManager

        IndexManager(db, config).index_with_attribution("test-kb", GitService)

        versions = svc.get_entry_versions("entry-1", "test-kb")
        assert [(v["commit_hash"], v["change_type"]) for v in versions] == [(commit2, "created")]
        assert svc.get_entry_at_version("entry-1", "test-kb", commit1) is None

    def test_foreign_commit_to_different_entrys_old_path_still_404s(self, kb_setup):
        """#415 must hold across the rename fix: a commit that touched a
        *different* entry's old path is not this entry's version, even if
        that old path happens to collide with something readable."""
        svc, db, config, kb_path, commit1 = kb_setup
        svc.record_commit("test-kb", commit1)

        (kb_path / "c.md").write_text("---\nid: entry-3\ntitle: C\ntype: note\n---\n\nC1")
        _git(kb_path, "add", ".")
        _git(kb_path, "commit", "-m", "add entry-3")
        _git(kb_path, "mv", "c.md", "d.md")
        _git(kb_path, "commit", "-m", "rename c.md to d.md")
        foreign_rename_commit = _git(kb_path, "rev-parse", "HEAD")

        from pyrite.services.git_service import GitService
        from pyrite.storage.index import IndexManager

        idx = IndexManager(db, config)
        idx.index_with_attribution("test-kb", GitService)

        # entry-1 never had this commit as one of its own versions.
        assert svc.get_entry_at_version("entry-1", "test-kb", foreign_rename_commit) is None

    def test_created_change_type_only_for_the_add_commit(self, kb_setup):
        """record_commit must report change_type="created" for the commit
        that adds the file, and "modified" for every other commit -- the
        same distinction index_with_attribution already makes from the
        full log (coordinator cold read on #432: every row from
        record_commit was hardcoded "modified", including the add)."""
        from pyrite.storage.models import EntryVersion

        svc, db, config, kb_path, commit1 = kb_setup

        svc.record_commit("test-kb", commit1)
        row = db.session.query(EntryVersion).filter_by(commit_hash=commit1).first()
        assert row.change_type == "created"

        (kb_path / "a.md").write_text("---\nid: entry-1\ntitle: V2\ntype: note\n---\n\nVersion 2")
        _git(kb_path, "add", ".")
        _git(kb_path, "commit", "-m", "edit")
        commit2 = _git(kb_path, "rev-parse", "HEAD")

        svc.record_commit("test-kb", commit2)
        row2 = db.session.query(EntryVersion).filter_by(commit_hash=commit2).first()
        assert row2.change_type == "modified"


class TestRecordCommitSubdirectoryKB:
    """record_commit against a KB that is a subdirectory of its git repo --
    git reports paths relative to the repo ROOT, not the KB (coordinator
    cold read: joining a repo-relative path onto kb_path double-prefixes
    the subdirectory, e.g. repo/kbdir/kbdir/a.md)."""

    @pytest.fixture
    def subdir_kb_setup(self, tmp_path):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _git(repo_root, "init")
        _git(repo_root, "config", "user.email", "test@test.com")
        _git(repo_root, "config", "user.name", "Test")

        kb_path = repo_root / "kbdir"
        kb_path.mkdir()
        (kb_path / "a.md").write_text("---\nid: entry-1\ntitle: V1\ntype: note\n---\n\nVersion 1")
        _git(repo_root, "add", ".")
        _git(repo_root, "commit", "-m", "v1")
        commit1 = _git(repo_root, "rev-parse", "HEAD")

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="sub-kb", path=kb_path)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        db = PyriteDB(tmp_path / "index.db")

        from pyrite.storage.index import IndexManager

        IndexManager(db, config).index_all()

        svc = VersionService(config, db)
        yield svc, db, config, repo_root, kb_path, commit1
        db.close()

    def test_record_commit_resolves_subdir_entry(self, subdir_kb_setup):
        svc, db, config, repo_root, kb_path, commit1 = subdir_kb_setup

        n = svc.record_commit("sub-kb", commit1)
        assert n == 1

        content = svc.get_entry_at_version("entry-1", "sub-kb", commit1)
        assert content is not None
        assert "Version 1" in content

    def test_record_commit_refuses_a_path_outside_the_kb(self, subdir_kb_setup):
        """A commit that touches a file elsewhere in the repo (another KB,
        or a directory outside this one) must never resolve to an entry in
        THIS KB, even by coincidence of a matching relative suffix. An
        entry moved into the KB from outside must not have its pre-move
        (outside-KB) content readable through this KB."""
        svc, db, config, repo_root, kb_path, commit1 = subdir_kb_setup

        outside_dir = repo_root / "other-kbdir"
        outside_dir.mkdir()
        (outside_dir / "a.md").write_text(
            "---\nid: entry-1\ntitle: Outside\ntype: note\n---\n\nOutside content, not this KB"
        )
        _git(repo_root, "add", ".")
        _git(repo_root, "commit", "-m", "add file outside the kb")
        outside_commit = _git(repo_root, "rev-parse", "HEAD")

        n = svc.record_commit("sub-kb", outside_commit)
        assert n == 0, "a path outside the KB's subtree must never be recorded as this KB's version"

    def test_record_commit_refuses_a_path_that_would_collide_after_naive_prefix_slicing(
        self, subdir_kb_setup
    ):
        """A weaker implementation than "refuse if the path does not start
        with the KB's prefix" -- e.g. always slicing off len(prefix)
        characters, regardless of whether the path actually starts with
        that prefix -- can accidentally produce a KB-relative path that
        collides with a real entry, purely by character count. The KB's
        prefix here is "kbdir/" (6 chars); an outside path is constructed
        so that path[6:] == "a.md", the real entry's KB-relative path, to
        catch exactly that weaker implementation."""
        svc, db, config, repo_root, kb_path, commit1 = subdir_kb_setup

        from pyrite.services.git_service import GitService

        assert GitService.get_kb_prefix(kb_path) == "kbdir/"
        # Repo-relative "aaaaaaa.md" (7 a's + ".md" = 10 chars); slicing
        # off the first 6 characters (len("kbdir/")) leaves "a.md" --
        # colliding with entry-1's real KB-relative path, but this file is
        # NOT under kbdir/ at all.
        colliding_name = "aaaaaaa.md"
        assert colliding_name[len("kbdir/") :] == "a.md"
        (repo_root / colliding_name).write_text(
            "---\nid: entry-1\ntitle: Outside\ntype: note\n---\n\nOutside content via slice collision"
        )
        _git(repo_root, "add", ".")
        _git(repo_root, "commit", "-m", "add colliding file outside the kb")
        outside_commit = _git(repo_root, "rev-parse", "HEAD")

        n = svc.record_commit("sub-kb", outside_commit)
        assert n == 0, (
            "a path outside the KB, even one that collides with a real "
            "entry's path after naive prefix slicing, must never be recorded"
        )

    def test_moving_a_file_into_the_kb_does_not_expose_its_pre_move_content(self, subdir_kb_setup):
        svc, db, config, repo_root, kb_path, commit1 = subdir_kb_setup

        outside_dir = repo_root / "other-kbdir"
        outside_dir.mkdir()
        (outside_dir / "b.md").write_text(
            "---\nid: entry-2\ntitle: Outside\ntype: note\n---\n\nOutside content"
        )
        _git(repo_root, "add", ".")
        _git(repo_root, "commit", "-m", "add outside")

        _git(repo_root, "mv", "other-kbdir/b.md", "kbdir/b.md")
        _git(repo_root, "commit", "-m", "move into kb")
        move_commit = _git(repo_root, "rev-parse", "HEAD")

        from pyrite.storage.index import IndexManager

        IndexManager(db, config).index_all()
        n = svc.record_commit("sub-kb", move_commit)
        assert n == 1

        # entry-2 is now in the KB, but its ONLY recorded version is the
        # move commit -- the pre-move commit (outside the KB) was never
        # recorded as one of this KB's entry's versions.
        content = svc.get_entry_at_version("entry-2", "sub-kb", move_commit)
        assert content is not None
        assert "Outside content" in content
        versions = svc.get_entry_versions("entry-2", "sub-kb")
        assert len(versions) == 1
        assert versions[0]["commit_hash"] == move_commit


class TestFilePathStoredKBRelative:
    """entry_version.file_path is stored KB-relative, not absolute
    (coordinator cold read: an absolute path breaks the moment the KB's
    directory moves, and the model's own docstring says "relative")."""

    def test_stored_path_is_kb_relative_not_absolute(self, tmp_path):
        kb_path = tmp_path / "kb"
        kb_path.mkdir()
        _git(kb_path, "init")
        _git(kb_path, "config", "user.email", "test@test.com")
        _git(kb_path, "config", "user.name", "Test")
        (kb_path / "a.md").write_text("---\nid: entry-1\ntitle: V1\ntype: note\n---\n\nV1")
        _git(kb_path, "add", ".")
        _git(kb_path, "commit", "-m", "v1")
        commit1 = _git(kb_path, "rev-parse", "HEAD")

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="k", path=kb_path)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        db = PyriteDB(tmp_path / "index.db")
        from pyrite.storage.index import IndexManager

        IndexManager(db, config).index_all()
        VersionService(config, db).record_commit("k", commit1)

        stored = db.get_entry_version_file_path("entry-1", "k", commit1)
        assert stored == "a.md", f"expected a KB-relative path, got {stored!r}"
        db.close()

    def test_reading_a_version_survives_the_kb_directory_moving(self, tmp_path):
        """The reader must be tolerant of a stored path -- KB-relative for
        every version recorded after this fix -- surviving a move of the
        KB's own directory: only the KBConfig.path changes, the stored
        rel path does not need to."""
        old_repo = tmp_path / "old"
        old_repo.mkdir()
        _git(old_repo, "init")
        _git(old_repo, "config", "user.email", "test@test.com")
        _git(old_repo, "config", "user.name", "Test")
        (old_repo / "a.md").write_text("---\nid: entry-1\ntitle: V1\ntype: note\n---\n\nV1")
        _git(old_repo, "add", ".")
        _git(old_repo, "commit", "-m", "v1")
        commit1 = _git(old_repo, "rev-parse", "HEAD")

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="k", path=old_repo)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        db = PyriteDB(tmp_path / "index.db")
        from pyrite.storage.index import IndexManager

        IndexManager(db, config).index_all()
        VersionService(config, db).record_commit("k", commit1)

        import shutil

        new_repo = tmp_path / "new"
        shutil.move(str(old_repo), str(new_repo))

        moved_config = PyriteConfig(
            knowledge_bases=[KBConfig(name="k", path=new_repo)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        IndexManager(db, moved_config).index_all()

        content = VersionService(moved_config, db).get_entry_at_version("entry-1", "k", commit1)
        assert content is not None
        assert "V1" in content
        db.close()

    def test_reader_tolerates_a_stored_absolute_path(self, tmp_path):
        """A row whose file_path is still absolute (from before the
        migration's normalisation ran, or before this coordinator round)
        must still be readable -- the reader relativizes it against the
        KB's own current path rather than trying it as-is."""
        kb_path = tmp_path / "kb"
        kb_path.mkdir()
        _git(kb_path, "init")
        _git(kb_path, "config", "user.email", "test@test.com")
        _git(kb_path, "config", "user.name", "Test")
        (kb_path / "a.md").write_text("---\nid: entry-1\ntitle: V1\ntype: note\n---\n\nV1")
        _git(kb_path, "add", ".")
        _git(kb_path, "commit", "-m", "v1")
        commit1 = _git(kb_path, "rev-parse", "HEAD")

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="k", path=kb_path)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        db = PyriteDB(tmp_path / "index.db")
        from pyrite.storage.index import IndexManager

        IndexManager(db, config).index_all()
        # Insert the row directly with an absolute path -- what a
        # pre-normalisation row looks like -- rather than through
        # record_commit, which always stores KB-relative now.
        db.upsert_entry_version(
            entry_id="entry-1",
            kb_name="k",
            commit_hash=commit1,
            author_name="Test",
            author_email="test@test.com",
            commit_date="2026-01-01T00:00:00+00:00",
            change_type="created",
            file_path=str(kb_path / "a.md"),
        )

        content = VersionService(config, db).get_entry_at_version("entry-1", "k", commit1)
        assert content is not None
        assert "V1" in content
        db.close()


class TestGitEnvIsolation:
    """Both subprocess calls in get_entry_at_version must run with the
    leak-isolated git environment (#415), so a parent git process's
    repo-scoped env vars (GIT_DIR, GIT_INDEX_FILE, ...) cannot leak in."""

    def test_both_git_calls_get_isolated_env(self, version_setup, monkeypatch, tmp_path):
        svc, db, commit1 = version_setup
        # Plant the leakable vars in *this* process's environment first, so
        # the assertion below is a real check that they were stripped from
        # the subprocess env -- not just that they happen to be absent from
        # the test process already.
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "somewhere-else" / ".git"))
        monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "somewhere-else" / "index"))

        real_run = subprocess.run
        seen_envs = []

        def _spy(argv, *args, **kwargs):
            seen_envs.append((list(argv), kwargs.get("env")))
            return real_run(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", _spy)
        content = svc.get_entry_at_version("entry-1", "test-kb", commit1)
        assert content is not None

        git_calls = [(argv, env) for argv, env in seen_envs if argv[:1] == ["git"]]
        assert len(git_calls) >= 2, seen_envs
        for argv, env in git_calls:
            assert env is not None, f"no env passed for {argv}"
            assert "GIT_DIR" not in env, f"GIT_DIR leaked into {argv}"
            assert "GIT_INDEX_FILE" not in env, f"GIT_INDEX_FILE leaked into {argv}"

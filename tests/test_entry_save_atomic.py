"""Entry.save() must be atomic: a reader never sees a half-written file.

Two agents on one KB race constantly (claim vs reset, claim vs claim). With a
plain write_text() a concurrent repo.load() can read a truncated or empty file
and fail to parse it; the loser then bails out of a multi-step operation with
file and index disagreeing. Write to a temp file in the same directory and
os.replace() it: atomic on POSIX and Windows, and a failed write leaves the old
content untouched.
"""

import os

import pytest

from pyrite.models.core_types import NoteEntry


def test_failed_write_leaves_previous_content_intact(tmp_path, monkeypatch):
    path = tmp_path / "note.md"
    NoteEntry(id="note", title="v1", body="first").save(path)
    before = path.read_text()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        NoteEntry(id="note", title="v2", body="second").save(path)

    assert path.read_text() == before, "a failed save must not corrupt the file"
    assert [p.name for p in tmp_path.iterdir()] == ["note.md"], "no temp file left behind"


def test_save_goes_through_replace(tmp_path, monkeypatch):
    calls = []
    real = os.replace

    def spy(src, dst):
        calls.append((str(src), str(dst)))
        return real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    path = tmp_path / "note.md"
    NoteEntry(id="note", title="t", body="b").save(path)
    assert calls and calls[-1][1] == str(path)
    assert os.path.dirname(calls[-1][0]) == str(tmp_path), "temp file must be in the same dir"
    assert "first" not in path.read_text() and path.read_text().endswith("b\n")


def test_new_file_mode_follows_umask_not_mkstemp(tmp_path):
    # mkstemp creates 0600; a KB served or shared by other users must get the
    # same mode a plain write would have produced under the current umask.
    old = os.umask(0o022)
    try:
        path = tmp_path / "note.md"
        NoteEntry(id="note", title="t", body="b").save(path)
        assert oct(path.stat().st_mode & 0o777) == oct(0o644)
    finally:
        os.umask(old)


def test_existing_file_mode_is_preserved(tmp_path):
    path = tmp_path / "note.md"
    NoteEntry(id="note", title="t", body="b").save(path)
    os.chmod(path, 0o600)
    NoteEntry(id="note", title="t2", body="b2").save(path)
    assert oct(path.stat().st_mode & 0o777) == oct(0o600)


class TestExclusiveSave:
    """#391 cold read round 2 item 2: `exclusive=True` closes the TOCTOU
    window between the write pipeline's own exists() check and the write
    that follows it -- `os.replace` always succeeds even if the target
    exists, so two truly concurrent creates could both pass the check and
    the second would silently overwrite the first. `os.link` fails with
    `FileExistsError` if the target already exists, atomically.
    """

    def test_exclusive_save_succeeds_when_target_does_not_exist(self, tmp_path):
        path = tmp_path / "note.md"
        NoteEntry(id="note", title="t", body="b").save(path, exclusive=True)
        assert path.exists()
        assert "b" in path.read_text()

    def test_exclusive_save_refuses_an_existing_target(self, tmp_path):
        path = tmp_path / "note.md"
        NoteEntry(id="note", title="t", body="ORIGINAL").save(path)
        original = path.read_text()

        with pytest.raises(FileExistsError):
            NoteEntry(id="note", title="t", body="REPLACED").save(path, exclusive=True)

        assert path.read_text() == original, "an exclusive save must never overwrite"

    def test_exclusive_save_leaves_no_temp_file_on_refusal(self, tmp_path):
        path = tmp_path / "note.md"
        NoteEntry(id="note", title="t", body="ORIGINAL").save(path)

        with pytest.raises(FileExistsError):
            NoteEntry(id="note", title="t", body="REPLACED").save(path, exclusive=True)

        assert [p.name for p in tmp_path.iterdir()] == ["note.md"], "no temp file left behind"

    @pytest.mark.control(
        reason="exclusive=False is the default and was the only behavior "
        "before this change, so save() overwriting is exactly what dev "
        "already did -- this is a regression guard for the untouched path, "
        "not a test of the new exclusive=True behavior"
    )
    def test_non_exclusive_save_still_overwrites(self, tmp_path):
        """Regression guard: exclusive=False (the default, every update)
        must keep working exactly as before -- this is what makes create
        different from update, not a global behavior change."""
        path = tmp_path / "note.md"
        NoteEntry(id="note", title="t", body="ORIGINAL").save(path)
        NoteEntry(id="note", title="t", body="REPLACED").save(path)
        assert "REPLACED" in path.read_text()


class TestExclusiveSaveFallsBackWithoutHardLinks:
    """#391 cold read round 3 item 1(a): `os.link` raises `OSError`
    (errno EPERM/ENOTSUP, not EEXIST) on filesystems with no hard-link
    support -- FAT/exFAT, and likely SMB/FUSE mounts. Before this fix,
    EVERY exclusive (create-path) save on such a filesystem raised a raw
    OSError, not just a colliding one. `_publish_exclusive` falls back to
    claiming the name with O_CREAT|O_EXCL (still exclusive, and supported
    by any filesystem that can create files at all) instead.
    """

    def _make_link_raise(self, monkeypatch, err):
        import os

        real_link = os.link

        def fake_link(src, dst):
            raise OSError(err, "simulated: hard links not supported")

        monkeypatch.setattr(os, "link", fake_link)
        return real_link

    def test_create_succeeds_when_os_link_raises_eperm(self, tmp_path, monkeypatch):
        import errno

        self._make_link_raise(monkeypatch, errno.EPERM)
        path = tmp_path / "note.md"

        NoteEntry(id="note", title="t", body="b").save(path, exclusive=True)

        assert path.exists()
        assert "b" in path.read_text()

    def test_a_second_create_of_the_same_path_is_still_refused(self, tmp_path, monkeypatch):
        """The whole point: falling back from os.link must not fall all the
        way back to a silent overwrite -- it must stay exclusive."""
        import errno

        self._make_link_raise(monkeypatch, errno.EPERM)
        path = tmp_path / "note.md"

        NoteEntry(id="note", title="t", body="ORIGINAL").save(path, exclusive=True)
        original = path.read_text()

        with pytest.raises(FileExistsError):
            NoteEntry(id="note", title="t", body="REPLACED").save(path, exclusive=True)

        assert path.read_text() == original, "still exclusive: must not overwrite"

    def test_no_temp_file_left_behind_on_the_fallback_path(self, tmp_path, monkeypatch):
        import errno

        self._make_link_raise(monkeypatch, errno.EPERM)
        path = tmp_path / "note.md"

        NoteEntry(id="note", title="t", body="b").save(path, exclusive=True)

        assert [p.name for p in tmp_path.iterdir()] == ["note.md"]

    def test_falls_back_again_when_o_excl_is_also_unsupported(self, tmp_path, monkeypatch):
        """Last resort: neither hard links nor O_EXCL work on this
        filesystem. The save must still succeed (ordinary os.replace),
        guarded only by the write pipeline's own exists() check -- exactly
        the pre-#391 behavior, not a crash."""
        import errno
        import os

        self._make_link_raise(monkeypatch, errno.EPERM)
        real_open = os.open
        path = tmp_path / "note.md"
        target = str(path)

        def fake_open(path_arg, flags, *a, **kw):
            # Only the O_EXCL claim on the TARGET path is unsupported here --
            # tempfile.mkstemp's own O_EXCL-based temp-file creation (an
            # unrelated, real use of the same flag) must keep working, or
            # this test would fail for a reason that has nothing to do with
            # the fallback under test.
            if flags & os.O_EXCL and os.fspath(path_arg) == target:
                raise OSError(errno.ENOTSUP, "simulated: O_EXCL not supported")
            return real_open(path_arg, flags, *a, **kw)

        monkeypatch.setattr(os, "open", fake_open)

        NoteEntry(id="note", title="t", body="b").save(path, exclusive=True)

        assert path.exists()
        assert "b" in path.read_text()


class TestExclusiveSaveFailurePaths:
    """#477: every fallback failure must preserve exclusive-publish semantics."""

    def _make_link_raise(self, monkeypatch, err):
        def fail_link(src, dst):
            raise OSError(err, "simulated: hard links not supported")

        monkeypatch.setattr(os, "link", fail_link)

    def test_failed_replace_removes_the_placeholder_claim(self, tmp_path, monkeypatch):
        import errno

        self._make_link_raise(monkeypatch, errno.EPERM)
        path = tmp_path / "note.md"
        real_replace = os.replace

        def fail_publish(src, dst):
            if os.fspath(dst) == os.fspath(path):
                raise OSError(errno.ENOSPC, "simulated full filesystem")
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", fail_publish)
        with pytest.raises(OSError) as exc:
            NoteEntry(id="note", title="t", body="payload").save(path, exclusive=True)

        assert exc.value.errno == errno.ENOSPC
        assert not path.exists(), "a failed publish must not leave an empty claimed path"
        assert list(tmp_path.iterdir()) == []

    def test_failed_replace_preserves_a_concurrent_replacement(self, tmp_path, monkeypatch):
        import errno

        self._make_link_raise(monkeypatch, errno.EPERM)
        path = tmp_path / "note.md"
        winner = tmp_path / "winner.tmp"
        real_replace = os.replace

        def publish_winner_then_fail(src, dst):
            winner.write_text("concurrent winner", encoding="utf-8")
            real_replace(winner, dst)
            raise OSError(errno.ENOSPC, "simulated publish failure after replacement")

        monkeypatch.setattr(os, "replace", publish_winner_then_fail)
        with pytest.raises(OSError) as exc:
            NoteEntry(id="note", title="t", body="candidate").save(path, exclusive=True)

        assert exc.value.errno == errno.ENOSPC
        assert path.read_text(encoding="utf-8") == "concurrent winner"
        assert [p.name for p in tmp_path.iterdir()] == ["note.md"]

    def test_transient_unlink_error_after_successful_link_still_succeeds(
        self, tmp_path, monkeypatch
    ):
        import errno
        from pathlib import Path

        path = tmp_path / "note.md"
        real_unlink = os.unlink
        state = {"attempts": 0}

        def fail_temp_unlink_once(path_arg, *args, **kwargs):
            candidate = Path(path_arg)
            if candidate.parent == tmp_path and candidate.suffix == ".tmp":
                state["attempts"] += 1
                if state["attempts"] == 1:
                    raise OSError(errno.EIO, "simulated transient temp unlink failure")
            return real_unlink(path_arg, *args, **kwargs)

        monkeypatch.setattr(os, "unlink", fail_temp_unlink_once)
        result = NoteEntry(id="note", title="t", body="published").save(
            path, exclusive=True
        )

        assert result == path
        assert state["attempts"] == 2
        assert "published" in path.read_text(encoding="utf-8")
        assert [p.name for p in tmp_path.iterdir()] == ["note.md"]

    @pytest.mark.parametrize(
        ("platform_name", "should_fallback"), [("nt", True), ("posix", False)]
    )
    def test_einval_from_hard_link_falls_back_only_on_windows(
        self, tmp_path, monkeypatch, platform_name, should_fallback
    ):
        import errno
        import os

        path = tmp_path / "note.md"
        temp = tmp_path / "source.tmp"
        temp.write_text("payload", encoding="utf-8")
        real_open = os.open
        open_calls = []

        def fail_link(src, dst):
            raise OSError(errno.EINVAL, "simulated unsupported Windows hard link")

        def observe_open(path_arg, flags, *args, **kwargs):
            if os.fspath(path_arg) == os.fspath(path) and flags & os.O_EXCL:
                open_calls.append(path_arg)
            return real_open(path_arg, flags, *args, **kwargs)

        monkeypatch.setattr(os, "link", fail_link)
        monkeypatch.setattr(os, "open", observe_open)
        with monkeypatch.context() as platform:
            platform.setattr(os, "name", platform_name)
            if should_fallback:
                NoteEntry._publish_exclusive(str(temp), path)
            else:
                with pytest.raises(OSError) as exc:
                    NoteEntry._publish_exclusive(str(temp), path)
                assert exc.value.errno == errno.EINVAL

        if should_fallback:
            assert open_calls
            assert path.read_text(encoding="utf-8") == "payload"
            assert not temp.exists()
        else:
            assert open_calls == []
            assert not path.exists()
            assert temp.read_text(encoding="utf-8") == "payload"

    def test_enospc_on_exclusive_claim_propagates_and_preserves_winner(self, tmp_path, monkeypatch):
        import errno

        self._make_link_raise(monkeypatch, errno.EPERM)
        path = tmp_path / "note.md"
        real_open = os.open

        def fail_claim_after_racer(path_arg, flags, *args, **kwargs):
            if os.fspath(path_arg) == os.fspath(path) and flags & os.O_EXCL:
                path.write_text("concurrent winner", encoding="utf-8")
                raise OSError(errno.ENOSPC, "simulated full filesystem")
            return real_open(path_arg, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", fail_claim_after_racer)
        with pytest.raises(OSError) as exc:
            NoteEntry(id="note", title="t", body="candidate").save(path, exclusive=True)

        assert exc.value.errno == errno.ENOSPC
        assert path.read_text(encoding="utf-8") == "concurrent winner"

    def test_edquot_on_exclusive_claim_propagates_and_preserves_winner(self, tmp_path, monkeypatch):
        import errno

        error_number = getattr(errno, "EDQUOT", None)
        if error_number is None:
            pytest.skip("EDQUOT is not available on this platform")

        self._make_link_raise(monkeypatch, errno.EPERM)
        path = tmp_path / "note.md"
        real_open = os.open

        def fail_claim_after_racer(path_arg, flags, *args, **kwargs):
            if os.fspath(path_arg) == os.fspath(path) and flags & os.O_EXCL:
                path.write_text("concurrent winner", encoding="utf-8")
                raise OSError(error_number, "simulated quota exceeded")
            return real_open(path_arg, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", fail_claim_after_racer)
        with pytest.raises(OSError) as exc:
            NoteEntry(id="note", title="t", body="candidate").save(path, exclusive=True)

        assert exc.value.errno == error_number
        assert path.read_text(encoding="utf-8") == "concurrent winner"

    def test_unrelated_link_error_does_not_enter_fallback(self, tmp_path, monkeypatch):
        import errno

        path = tmp_path / "note.md"
        open_calls = []
        real_open = os.open

        def fail_link(src, dst):
            raise OSError(errno.EIO, "simulated I/O failure")

        def observe_open(path_arg, flags, *args, **kwargs):
            if os.fspath(path_arg) == os.fspath(path) and flags & os.O_EXCL:
                open_calls.append(path_arg)
            return real_open(path_arg, flags, *args, **kwargs)

        monkeypatch.setattr(os, "link", fail_link)
        monkeypatch.setattr(os, "open", observe_open)
        with pytest.raises(OSError) as exc:
            NoteEntry(id="note", title="t", body="candidate").save(path, exclusive=True)

        assert exc.value.errno == errno.EIO
        assert open_calls == []
        assert not path.exists()

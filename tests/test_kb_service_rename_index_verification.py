"""Regression tests for read-back index verification on KBService.rename_entry.

verify-after-write-on-the-index-path (second checklist item): a rename
that succeeds on disk but whose index sync silently fails or misses the
new entry currently only logs a warning — old_id keeps resolving in
search/lookups and new_id stays invisible, discovered only at the next
manual `index sync`. This must surface in the result, not just a log line.
"""

from unittest.mock import patch

import pytest

from pyrite.exceptions import StorageError, ValidationError
from pyrite.services.kb_service import KBService
from pyrite.storage.repository import KBRepository


def _kb_service(indexed_test_env):
    return KBService(indexed_test_env["config"], indexed_test_env["db"])


class TestRenameIndexVerification:
    def test_same_id_rename_is_rejected_before_index_sync(self, indexed_test_env, sample_events):
        kb_service = _kb_service(indexed_test_env)
        old_id = sample_events[0].id
        kb_name = indexed_test_env["events_kb"].name
        db = indexed_test_env["db"]
        repo = KBRepository(indexed_test_env["events_kb"])
        entry = repo.load(old_id)
        assert entry is not None
        source_path = entry.file_path
        original = source_path.read_bytes()

        with patch.object(kb_service._index_mgr, "sync_incremental") as sync_incremental:
            with pytest.raises(ValidationError, match="new id equals old id"):
                kb_service.rename_entry(old_id, old_id, kb_name)

        sync_incremental.assert_not_called()
        assert source_path.read_bytes() == original
        assert db.get_entry(old_id, kb_name) is not None

    def test_successful_rename_reports_index_verified(self, indexed_test_env, sample_events):
        """The common case: rename + sync succeed, and the result records
        that the new id was confirmed resolvable in the index — not just
        that sync_incremental didn't raise."""
        kb_service = _kb_service(indexed_test_env)
        old_id = sample_events[0].id
        kb_name = indexed_test_env["events_kb"].name

        result = kb_service.rename_entry(old_id, "renamed-entry-id", kb_name)

        assert result["renamed"] is True
        assert result["index_verified"] is True

    def test_successful_rename_new_id_resolves_in_index(self, indexed_test_env, sample_events):
        db = indexed_test_env["db"]
        kb_service = _kb_service(indexed_test_env)
        old_id = sample_events[0].id
        kb_name = indexed_test_env["events_kb"].name

        kb_service.rename_entry(old_id, "renamed-entry-id", kb_name)

        assert db.get_entry("renamed-entry-id", kb_name) is not None
        assert db.get_entry(old_id, kb_name) is None

    def test_index_sync_failure_raises_storage_error_not_silent_warning(
        self, indexed_test_env, sample_events
    ):
        """Before this fix: sync_incremental raising was caught and
        downgraded to logger.warning, so the caller (CLI/MCP/REST) got a
        200/success response even though the index never learned about
        the rename. That's exactly the derived-state-sync bug class this
        epic exists to close -- the file operation succeeding while the
        index silently drifts must not look like success to the caller."""
        kb_service = _kb_service(indexed_test_env)
        old_id = sample_events[0].id
        kb_name = indexed_test_env["events_kb"].name

        with patch.object(
            kb_service._index_mgr,
            "sync_incremental",
            side_effect=RuntimeError("simulated index failure"),
        ):
            with pytest.raises(StorageError, match="index"):
                kb_service.rename_entry(old_id, "renamed-entry-id", kb_name)

    def test_index_sync_failure_leaves_file_rename_intact(self, indexed_test_env, sample_events):
        """The file rename already succeeded before sync_incremental ran --
        raising StorageError must not attempt to undo it (that would be a
        new failure mode). The file system state is the source of truth;
        only the index is degraded, and a follow-up `index sync` recovers."""
        kb_service = _kb_service(indexed_test_env)
        old_id = sample_events[0].id
        kb_name = indexed_test_env["events_kb"].name
        events_kb = indexed_test_env["events_kb"]

        with patch.object(
            kb_service._index_mgr,
            "sync_incremental",
            side_effect=RuntimeError("simulated index failure"),
        ):
            with pytest.raises(StorageError):
                kb_service.rename_entry(old_id, "renamed-entry-id", kb_name)

        # The file itself was already renamed on disk before the index
        # sync ran -- that part of the operation is not rolled back.
        remaining_ids = {e.id for e, _path in KBRepository(events_kb).list_entries()}
        assert "renamed-entry-id" in remaining_ids
        assert old_id not in remaining_ids

    def test_index_sync_succeeds_but_new_id_missing_raises_storage_error(
        self, indexed_test_env, sample_events
    ):
        """A subtler failure than sync_incremental raising: sync completes
        without error, but the new id still doesn't resolve (e.g. a
        malformed-frontmatter skip, or the file landed somewhere the
        indexer didn't scan). The read-back check must catch this too --
        "sync didn't throw" is not the same guarantee as "sync worked"."""
        kb_service = _kb_service(indexed_test_env)
        old_id = sample_events[0].id
        kb_name = indexed_test_env["events_kb"].name

        with patch.object(
            kb_service._index_mgr, "sync_incremental", return_value={"added": 0, "updated": 0}
        ):
            with pytest.raises(StorageError, match="index"):
                kb_service.rename_entry(old_id, "renamed-entry-id", kb_name)

    def test_index_sync_failure_message_reaches_callers_unmasked(
        self, indexed_test_env, sample_events
    ):
        """#506 item 2: the "Run `pyrite index sync` to recover" hint is safe
        by construction -- it names only the two entry ids, and (since #509
        round 1's cold read) never the caught exception's own text, which is
        NOT guaranteed safe (an OSError names a real path; a driver raises
        its own error text) -- so it should reach REST/MCP/CLI callers as-is,
        not the base StorageError's fixed, generic public_message from #501.
        A narrow StorageError subclass with public_message=None is the fix;
        the base StorageError's masking (for OTHER raise sites that DO carry
        unsafe detail) is untouched."""
        kb_service = _kb_service(indexed_test_env)
        old_id = sample_events[0].id
        kb_name = indexed_test_env["events_kb"].name

        with patch.object(
            kb_service._index_mgr,
            "sync_incremental",
            side_effect=RuntimeError("simulated index failure"),
        ):
            with pytest.raises(StorageError) as excinfo:
                kb_service.rename_entry(old_id, "renamed-entry-id", kb_name)

        exc = excinfo.value
        assert exc.public_message is None
        assert "pyrite index sync" in str(exc)
        # The caught exception's own text is logged, not interpolated --
        # see test_index_sync_failure_never_interpolates_the_caught_exceptions_text
        # for the case where that text would be unsafe to show.
        assert "simulated index failure" not in str(exc)

    def test_index_sync_missing_id_message_reaches_callers_unmasked(
        self, indexed_test_env, sample_events
    ):
        """Same fix, the other raise site in this method (sync succeeds but
        the new id still doesn't resolve)."""
        kb_service = _kb_service(indexed_test_env)
        old_id = sample_events[0].id
        kb_name = indexed_test_env["events_kb"].name

        with patch.object(
            kb_service._index_mgr, "sync_incremental", return_value={"added": 0, "updated": 0}
        ):
            with pytest.raises(StorageError) as excinfo:
                kb_service.rename_entry(old_id, "renamed-entry-id", kb_name)

        exc = excinfo.value
        assert exc.public_message is None
        assert "pyrite index sync" in str(exc)

    def test_index_sync_failure_never_interpolates_the_caught_exceptions_text(
        self, indexed_test_env, sample_events
    ):
        """#509 round 1 cold read: the message used to interpolate `({e})`
        from whatever sync_incremental raised. That's the caught exception's
        OWN text, which is not guaranteed to be safe -- an OSError carries a
        real filesystem path, a driver raises its own error text -- so
        IndexSyncRecoveryHintError.public_message=None was unmasking
        server-side detail, not a safe-by-construction message. The fixed
        message names only the two entry ids; `e` itself is logged, not
        interpolated."""
        kb_service = _kb_service(indexed_test_env)
        old_id = sample_events[0].id
        kb_name = indexed_test_env["events_kb"].name

        real_path = "/srv/secret/kb/x.md"
        with patch.object(
            kb_service._index_mgr,
            "sync_incremental",
            side_effect=OSError(2, "No such file or directory", real_path),
        ):
            with pytest.raises(StorageError) as excinfo:
                kb_service.rename_entry(old_id, "renamed-entry-id", kb_name)

        exc = excinfo.value
        assert real_path not in str(exc)
        assert real_path not in (exc.public_message or "")
        assert "pyrite index sync" in str(exc)

    def test_dry_run_skips_index_verification(self, indexed_test_env, sample_events):
        """A dry run never touches the filesystem or the index -- there is
        nothing to verify, and it must not raise."""
        kb_service = _kb_service(indexed_test_env)
        old_id = sample_events[0].id
        kb_name = indexed_test_env["events_kb"].name

        result = kb_service.rename_entry(old_id, "renamed-entry-id", kb_name, dry_run=True)
        assert result["dry_run"] is True
        assert "index_verified" not in result

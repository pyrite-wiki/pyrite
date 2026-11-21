"""Preserve existing folder casing on case-insensitive filesystems (#1624).

When an entry is created, moved via templated subdirectory, or reconciled into
a folder that already exists on disk under another letter case, the reported
file_path and index row must preserve the disk's spelling.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.storage.entry_paths import resolve_existing_folder_casing
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository


def _is_case_insensitive_fs(path: Path) -> bool:
    probe = path / ".case_probe_test_dir"
    probe.mkdir(parents=True, exist_ok=True)
    try:
        upper = path / ".CASE_PROBE_TEST_DIR"
        return upper.exists()
    finally:
        if probe.exists():
            probe.rmdir()


# -----------------------------------------------------------------------------
# Unit tests with stand-ins (run everywhere, including case-sensitive CI)
# -----------------------------------------------------------------------------


def test_resolve_existing_folder_casing_none_and_empty():
    root = Path("/fake/root")
    assert resolve_existing_folder_casing(root, None) is None
    assert resolve_existing_folder_casing(root, "") == ""
    assert resolve_existing_folder_casing(root, "   ") == ""


def test_resolve_existing_folder_casing_standin_case_insensitive():
    """When the filesystem acts case-insensitively, resolve_existing_folder_casing
    picks the existing on-disk spelling."""
    root = Path("/fake/root")

    # Simulate existing lowercase folder 'notes' and 'area/notes' in root:
    # Any query for 'notes' or 'Notes' reports is_dir() == True,
    # but the physical directory listing returns ['notes'].
    def is_dir(p: Path) -> bool:
        rel = p.relative_to(root).as_posix().lower()
        return rel in ("notes", "area", "area/notes")

    def iter_dir(p: Path) -> list[str]:
        if p == root:
            return ["notes", "area"]
        if p == root / "area":
            return ["notes"]
        return []

    # Single folder
    resolved = resolve_existing_folder_casing(root, "Notes", is_dir=is_dir, iter_dir=iter_dir)
    assert resolved == "notes"

    # Multi-level folders
    resolved_multi = resolve_existing_folder_casing(
        root, "Area/Notes", is_dir=is_dir, iter_dir=iter_dir
    )
    assert resolved_multi == "area/notes"

    # Non-existent folder keeps requested spelling
    resolved_new = resolve_existing_folder_casing(
        root, "BrandNew", is_dir=is_dir, iter_dir=iter_dir
    )
    assert resolved_new == "BrandNew"


def test_resolve_existing_folder_casing_standin_case_sensitive_control():
    """On a case-sensitive disk where only 'notes' exists, querying 'Notes'
    is not an existing dir, so it keeps the requested spelling."""
    root = Path("/fake/root")

    # Only 'notes' exists; 'Notes' is_dir is False
    def is_dir(p: Path) -> bool:
        rel = p.relative_to(root).as_posix()
        return rel == "notes"

    def iter_dir(p: Path) -> list[str]:
        if p == root:
            return ["notes"]
        return []

    resolved = resolve_existing_folder_casing(root, "Notes", is_dir=is_dir, iter_dir=iter_dir)
    assert resolved == "Notes"


# -----------------------------------------------------------------------------
# Real disk tests (run on APFS / Windows, skipped on case-sensitive Linux)
# -----------------------------------------------------------------------------


KB_YAML = """\
name: kb_case
kb_type: generic
types:
  memo:
    description: a note in Notes
    subdirectory: Notes
  item:
    description: an item in items category
    subdirectory: items/{category}
"""


class CaseEnv:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "kb_case"
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "kb.yaml").write_text(KB_YAML)
        self.config = PyriteConfig(
            knowledge_bases=[KBConfig(name="kb_case", path=self.root, kb_type="generic")],
            settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
        )
        self.db = PyriteDB(tmp_path / "index.db")
        self.im = IndexManager(self.db, self.config)
        self.im.index_all()

    @property
    def svc(self):
        from pyrite.services.kb_service import KBService

        return KBService(self.config, self.db)

    @property
    def repo(self) -> KBRepository:
        return KBRepository(self.config.get_kb("kb_case"))

    def rows(self) -> dict[str, str]:
        return {
            i: Path(r["file_path"]).relative_to(self.root).as_posix()
            for i, r in self.im._load_indexed_state("kb_case").items()
        }

    def sync(self) -> dict:
        return self.im.sync_incremental("kb_case")

    def close(self):
        self.db.close()


def test_create_preserves_existing_folder_casing_on_disk(tmp_path):
    """When 'notes/' exists on a case-insensitive disk, creating a 'memo'
    (whose schema specifies 'Notes') reports 'notes/<id>.md' and indexes it."""
    env = CaseEnv(tmp_path)
    try:
        if not _is_case_insensitive_fs(env.root):
            pytest.skip("the filesystem here is case-sensitive")

        # Create folder 'notes' in lowercase on disk
        (env.root / "notes").mkdir(exist_ok=True)

        created = env.svc.create(
            "kb_case",
            {"entry_type": "memo", "title": "First Memo", "body": "Hello world"},
        )
        file_path_str = created.entry.file_path.relative_to(env.root).as_posix()
        assert file_path_str.startswith("notes/"), (
            f"Expected file_path to start with existing 'notes/', got: {file_path_str}"
        )

        # Index row must also record the existing disk casing
        rows = env.rows()
        assert rows[created.entry.id] == file_path_str
        assert (env.root / file_path_str).exists()

        # Incremental sync must agree with the row (no drift between create and sync)
        env.sync()
        assert env.rows()[created.entry.id] == file_path_str
    finally:
        env.close()


def test_templated_move_preserves_existing_folder_casing(tmp_path):
    """When 'items/archive/' exists on disk, a templated move to category 'Archive'
    places and reports the file under 'items/archive/'."""
    env = CaseEnv(tmp_path)
    try:
        if not _is_case_insensitive_fs(env.root):
            pytest.skip("the filesystem here is case-sensitive")

        # Create items/archive on disk in lowercase
        (env.root / "items" / "archive").mkdir(parents=True, exist_ok=True)
        (env.root / "items" / "active").mkdir(parents=True, exist_ok=True)

        res = env.svc.create(
            "kb_case",
            {
                "entry_type": "item",
                "id": "i1",
                "title": "Item 1",
                "body": "Body",
                "category": "active",
            },
        )
        assert res.entry.file_path.relative_to(env.root).as_posix() == "items/active/i1.md"

        # Update category to 'Archive' (title-case). Because 'items/archive' exists,
        # templated move should resolve to 'items/archive/i1.md'.
        updated = env.svc.update_entry("i1", "kb_case", category="Archive")
        new_rel = updated.file_path.relative_to(env.root).as_posix()
        assert new_rel == "items/archive/i1.md", f"Expected 'items/archive/i1.md', got: {new_rel}"
        assert (env.root / new_rel).exists()
    finally:
        env.close()


def test_reconcile_preserves_existing_folder_casing(tmp_path):
    """Reconcile against a type whose subdirectory is 'Notes' recognizes
    that an entry in existing 'notes/' is correctly placed."""
    env = CaseEnv(tmp_path)
    try:
        if not _is_case_insensitive_fs(env.root):
            pytest.skip("the filesystem here is case-sensitive")

        (env.root / "notes").mkdir(exist_ok=True)
        res = env.svc.create(
            "kb_case",
            {"entry_type": "memo", "id": "m1", "title": "Memo 1", "body": "Content"},
        )
        assert res.entry.file_path.relative_to(env.root).as_posix() == "notes/m1.md"

        # Reconcile should find no moves needed because expected_dir matches notes
        reconcile_result = env.svc.reconcile_templated_subdirectories("kb_case", apply=True)
        assert len(reconcile_result.moves) == 0
    finally:
        env.close()


@pytest.mark.control(
    reason="on a case-sensitive disk with only 'Notes/' present, 'Notes/' is retained"
)
def test_case_sensitive_disk_control_keeps_notes(tmp_path):
    env = CaseEnv(tmp_path)
    try:
        if _is_case_insensitive_fs(env.root):
            (env.root / "Notes").mkdir(exist_ok=True)
            res = env.svc.create(
                "kb_case",
                {"entry_type": "memo", "id": "m2", "title": "Memo 2", "body": "Content"},
            )
            assert res.entry.file_path.relative_to(env.root).as_posix() == "Notes/m2.md"
        else:
            (env.root / "Notes").mkdir(exist_ok=True)
            res = env.svc.create(
                "kb_case",
                {"entry_type": "memo", "id": "m2", "title": "Memo 2", "body": "Content"},
            )
            assert res.entry.file_path.relative_to(env.root).as_posix() == "Notes/m2.md"
    finally:
        env.close()

"""Tests for collection-scoped export via ExportService."""

import json
from pathlib import Path

import pytest

from pyrite.models.core_types import NoteEntry, PersonEntry
from pyrite.renderers.notebooklm import BundleStrategy, SourceMode
from pyrite.schema import Link, Source
from pyrite.services.export_service import ExportService


@pytest.fixture
def tmp_export_dir(tmp_path):
    return tmp_path / "export"


@pytest.fixture
def kb_with_entries(tmp_path):
    """Set up a minimal KB with entries on disk for testing."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()

    entries = [
        NoteEntry(
            id="note-1",
            title="Research Note",
            body="Some research content.",
            tags=["research"],
            links=[Link(target="person-1", relation="mentions")],
        ),
        PersonEntry(
            id="person-1",
            title="Alice Smith",
            body="A researcher.",
            role="Researcher",
            affiliations=["MIT"],
            tags=["researcher"],
            sources=[
                Source(title="Public Bio", url="https://example.com/alice", access="public"),
                Source(
                    title="Confidential Source",
                    url="https://secret.example.com",
                    access="restricted",
                ),
            ],
        ),
        NoteEntry(
            id="note-2",
            title="Background Note",
            body="Background context.",
            tags=["background"],
        ),
    ]

    # Save entries to disk, as a create writes them
    from pyrite.storage.repository import new_entry_text

    for entry in entries:
        entry_dir = kb_path / entry.entry_type
        entry_dir.mkdir(exist_ok=True)
        (entry_dir / f"{entry.id}.md").write_text(new_entry_text(entry), encoding="utf-8")

    return kb_path, entries


class TestExportCollection:
    """Test export_collection() static method."""

    def test_export_entries_to_directory(self, kb_with_entries, tmp_export_dir):
        _, entries = kb_with_entries
        result = ExportService.export_collection_entries(
            entries=entries,
            output_dir=tmp_export_dir,
        )
        assert result["entries_exported"] == 3
        assert result["files_created"] >= 1
        assert tmp_export_dir.exists()
        # Manifest should be created
        assert (tmp_export_dir / "_manifest.md").exists()

    def test_export_with_bundle_none(self, kb_with_entries, tmp_export_dir):
        _, entries = kb_with_entries
        result = ExportService.export_collection_entries(
            entries=entries,
            output_dir=tmp_export_dir,
            bundle_strategy=BundleStrategy.NONE,
        )
        # One file per entry + manifest
        assert result["files_created"] == 3
        assert (tmp_export_dir / "note-1.md").exists()
        assert (tmp_export_dir / "person-1.md").exists()

    def test_export_with_bundle_by_type(self, kb_with_entries, tmp_export_dir):
        _, entries = kb_with_entries
        result = ExportService.export_collection_entries(
            entries=entries,
            output_dir=tmp_export_dir,
            bundle_strategy=BundleStrategy.BY_TYPE,
        )
        # Two type files (note, person) + manifest
        assert result["files_created"] == 2
        assert (tmp_export_dir / "note.md").exists()
        assert (tmp_export_dir / "person.md").exists()

    def test_export_with_bundle_single(self, kb_with_entries, tmp_export_dir):
        _, entries = kb_with_entries
        result = ExportService.export_collection_entries(
            entries=entries,
            output_dir=tmp_export_dir,
            bundle_strategy=BundleStrategy.SINGLE,
        )
        assert result["files_created"] == 1
        assert (tmp_export_dir / "all_entries.md").exists()

    def test_export_with_source_redaction(self, kb_with_entries, tmp_export_dir):
        _, entries = kb_with_entries
        ExportService.export_collection_entries(
            entries=entries,
            output_dir=tmp_export_dir,
            bundle_strategy=BundleStrategy.NONE,
            source_mode=SourceMode.REDACT,
        )
        person_content = (tmp_export_dir / "person-1.md").read_text()
        assert "Public Bio" in person_content
        assert "[source redacted]" in person_content
        assert "Confidential Source" not in person_content

    def test_export_with_source_public(self, kb_with_entries, tmp_export_dir):
        _, entries = kb_with_entries
        ExportService.export_collection_entries(
            entries=entries,
            output_dir=tmp_export_dir,
            bundle_strategy=BundleStrategy.NONE,
            source_mode=SourceMode.PUBLIC,
        )
        person_content = (tmp_export_dir / "person-1.md").read_text()
        assert "Public Bio" in person_content
        assert "Confidential Source" not in person_content
        assert "[source redacted]" not in person_content

    def test_export_manifest_content(self, kb_with_entries, tmp_export_dir):
        _, entries = kb_with_entries
        ExportService.export_collection_entries(
            entries=entries,
            output_dir=tmp_export_dir,
            title="Test Story Research",
        )
        manifest = (tmp_export_dir / "_manifest.md").read_text()
        assert "# Test Story Research" in manifest
        assert "Research Note" in manifest
        assert "Alice Smith" in manifest
        assert "3" in manifest  # total count

    def test_export_empty_list(self, tmp_export_dir):
        result = ExportService.export_collection_entries(
            entries=[],
            output_dir=tmp_export_dir,
        )
        assert result["entries_exported"] == 0
        assert result["files_created"] == 0

    def test_export_preserves_entry_content(self, kb_with_entries, tmp_export_dir):
        _, entries = kb_with_entries
        ExportService.export_collection_entries(
            entries=entries,
            output_dir=tmp_export_dir,
            bundle_strategy=BundleStrategy.NONE,
        )
        note_content = (tmp_export_dir / "note-1.md").read_text()
        assert "Some research content." in note_content
        assert "# Research Note (note)" in note_content

"""Tests for KBRepository.rename — Tier A r1700 rename-move-support.

Pyrite has no first-class command for renaming an entry. In practice
that means internal wikilinks break silently when an entry is renamed,
and users avoid renaming even when the new name is clearly better,
because the cleanup cost is high enough that the KB accumulates
slug-drift. This test pins the contract for the smallest useful slice:

  - rename file on disk
  - rewrite frontmatter `id:` field
  - rewrite `[[<old-id>]]` and `[[<old-id>|alias]]` wikilinks within
    the same KB
  - dry-run mode that shows the plan without executing
  - error on target collision and missing source

Cross-KB wikilink rewrite, redirect-stub creation, and the `move`
variant (subdir change) are filed as r1700 follow-ups.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from pyrite.config import KBConfig, KBType
from pyrite.exceptions import EntryNotFoundError, ValidationError
from pyrite.models import NoteEntry
from pyrite.storage.repository import KBRepository


@pytest.fixture
def repo_with_links():
    """Repo with 3 entries; entry-b and entry-c link to entry-a."""
    with tempfile.TemporaryDirectory() as tmpdir:
        kb_path = Path(tmpdir) / "kb"
        kb_path.mkdir()
        kb = KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)
        repo = KBRepository(kb)

        repo.save(NoteEntry(id="entry-a", title="A", body="I am A."))
        repo.save(
            NoteEntry(
                id="entry-b",
                title="B",
                body="See [[entry-a]] for context.",
            )
        )
        repo.save(
            NoteEntry(
                id="entry-c",
                title="C",
                body=(
                    "Mentions [[entry-a|the original A]] and a separate "
                    "[[entry-a]] link, plus an unrelated [[entry-b]]."
                ),
            )
        )
        yield repo


class TestRename:
    """Core rename: stable path + frontmatter id rewrite + wikilink rewrite."""

    def test_rename_preserves_file_path(self, repo_with_links):
        result = repo_with_links.rename("entry-a", "renamed-a")
        # File stays at its original path.
        assert (repo_with_links.path / "notes" / "entry-a.md").exists()
        assert not (repo_with_links.path / "notes" / "renamed-a.md").exists()
        # Result reports the rename
        assert result["renamed"] is True
        assert result["old_id"] == "entry-a"
        assert result["new_id"] == "renamed-a"

    def test_rename_rewrites_frontmatter_id(self, repo_with_links):
        repo_with_links.rename("entry-a", "renamed-a")
        loaded = repo_with_links.load("renamed-a")
        assert loaded is not None
        assert loaded.id == "renamed-a"

    def test_rename_rewrites_bare_wikilinks(self, repo_with_links):
        """[[entry-a]] in entry-b's body becomes [[renamed-a]]."""
        repo_with_links.rename("entry-a", "renamed-a")
        b = repo_with_links.load("entry-b")
        assert b is not None
        assert "[[renamed-a]]" in b.body
        assert "[[entry-a]]" not in b.body

    def test_rename_rewrites_aliased_wikilinks(self, repo_with_links):
        """[[entry-a|the original A]] becomes [[renamed-a|the original A]] —
        alias text is preserved."""
        repo_with_links.rename("entry-a", "renamed-a")
        c = repo_with_links.load("entry-c")
        assert c is not None
        assert "[[renamed-a|the original A]]" in c.body
        # Bare link in same body also rewrote
        assert "[[renamed-a]]" in c.body
        # Unrelated link untouched
        assert "[[entry-b]]" in c.body
        # No leftover old-id links
        assert "[[entry-a]]" not in c.body
        assert "[[entry-a|" not in c.body

    def test_rename_returns_link_rewrite_count(self, repo_with_links):
        """Result dict reports how many files had wikilinks rewritten so
        the CLI can surface it."""
        result = repo_with_links.rename("entry-a", "renamed-a")
        # entry-b (1 rewrite) + entry-c (2 rewrites in one file)
        assert result["files_rewritten"] == 2
        assert result["links_rewritten"] >= 3


class TestRenameDryRun:
    """Dry-run mode: surface the plan, change nothing."""

    def test_dry_run_does_not_modify_filesystem(self, repo_with_links):
        result = repo_with_links.rename("entry-a", "renamed-a", dry_run=True)
        # Nothing changed on disk
        assert (repo_with_links.path / "notes" / "entry-a.md").exists()
        assert not (repo_with_links.path / "notes" / "renamed-a.md").exists()
        # entry-b body still references the OLD id
        b = repo_with_links.load("entry-b")
        assert "[[entry-a]]" in b.body
        # Result still reports what WOULD happen
        assert result["dry_run"] is True
        assert result["old_id"] == "entry-a"
        assert result["new_id"] == "renamed-a"
        assert result["files_rewritten"] == 2  # planned, not actual


class TestRenameNoUpdateLinks:
    """update_links=False: rename the file but leave wikilinks alone.

    Rare case — sometimes the user intentionally wants old references
    preserved (e.g., for historical study). The default is True.
    """

    def test_no_update_links_leaves_wikilinks_unchanged(self, repo_with_links):
        result = repo_with_links.rename("entry-a", "renamed-a", update_links=False)
        assert (
            repo_with_links.find_file("renamed-a") == repo_with_links.path / "notes" / "entry-a.md"
        )
        b = repo_with_links.load("entry-b")
        # Old link still there — now dangling
        assert "[[entry-a]]" in b.body
        assert result["files_rewritten"] == 0
        assert result["links_rewritten"] == 0


class TestRenameErrors:
    """Error paths: missing source, target collision."""

    def test_rename_missing_source_raises(self, repo_with_links):
        with pytest.raises(EntryNotFoundError):
            repo_with_links.rename("no-such-entry", "anything")

    def test_rename_target_exists_raises(self, repo_with_links):
        """Refuse to clobber an existing entry."""
        with pytest.raises(ValidationError):
            repo_with_links.rename("entry-a", "entry-b")

    def test_rename_same_id_is_refused(self, repo_with_links):
        """A same-id rename is refused without changing the source."""
        source_path = repo_with_links.path / "notes" / "entry-a.md"
        original = source_path.read_bytes()

        with pytest.raises(ValidationError, match="new id equals old id"):
            repo_with_links.rename("entry-a", "entry-a")

        assert source_path.read_bytes() == original


@pytest.fixture
def adr_repo():
    """A software-kb-shaped KB whose `adr` type declares `file_pattern`
    (#391): `{adr_number:04d}-{title}.md`, no `{id}`/`{slug}` placeholder."""
    with tempfile.TemporaryDirectory() as tmpdir:
        kb_path = Path(tmpdir) / "kb"
        kb_path.mkdir()
        (kb_path / "kb.yaml").write_text(
            "name: sw\n"
            "kb_type: software\n"
            "types:\n"
            "  adr:\n"
            "    subdirectory: adrs/\n"
            "    file_pattern: '{adr_number:04d}-{title}.md'\n"
        )
        kb = KBConfig(name="sw", path=kb_path, kb_type="software")
        repo = KBRepository(kb)

        from pyrite_software_kb.entry_types import ADREntry

        repo.save(ADREntry(id="adr-x", title="Keep Me", adr_number=9, status="proposed"))
        yield repo


class TestRenameKeepsAFilePatternTypesFilename:
    """#391 cold read round 2 MUST: renaming a `file_pattern` type's id used
    to delete its file. `resolve_filename` on the SAME entry data (only
    `id` changed) re-resolves to the SAME path as the source when the
    pattern has no `{id}`/`{slug}` (e.g. `{adr_number:04d}-{title}.md`);
    the unconditional `src.unlink()` then deleted the file it had just
    written under that identical path -- `renamed: True`, zero files left.
    """

    def test_renaming_an_adr_keeps_its_file(self, adr_repo):
        original = adr_repo.path / "adrs" / "0009-keep-me.md"
        assert original.exists()

        result = adr_repo.rename("adr-x", "adr-y")

        assert result["renamed"] is True
        assert original.exists(), "the file must survive the rename"
        content = original.read_text()
        assert "id: adr-y" in content
        assert "adr_number: 9" in content
        assert len(list(adr_repo.path.rglob("*.md"))) == 1, (
            "exactly one file must exist after the rename -- not zero, not two"
        )

    @pytest.mark.control(
        reason="dev's resolve_filename has no entry-field placeholder support "
        "at all, so {adr_number:04d} in the fixture's file_pattern KeyErrors "
        "and resolve_filename always returns None there -- rename resolves a "
        "genuinely different <new_id>.md path on dev (no collision to lose "
        "data over), so the renamed entry is trivially findable by its new "
        "id without this PR's fix. Pins the same outcome now reached "
        "deliberately by keeping the existing filename, not by accident."
    )
    def test_renaming_an_adr_is_findable_by_the_new_id(self, adr_repo):
        adr_repo.rename("adr-x", "adr-y")
        found = adr_repo.find_file("adr-y")
        assert found is not None
        assert found.exists()
        assert adr_repo.find_file("adr-x") is None

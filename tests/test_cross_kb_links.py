"""Tests for cross-KB wikilink resolution."""

import pytest

from pyrite.storage.index import _WIKILINK_RE
from pyrite.services.access_policy import UNSCOPED


class TestWikilinkRegex:
    """Test the updated wikilink regex with cross-KB prefix support."""

    def test_simple_wikilink(self):
        match = _WIKILINK_RE.search("See [[some-entry]] for details")
        assert match is not None
        assert match.group(1) is None  # No KB prefix
        assert match.group(2) == "some-entry"

    def test_wikilink_with_display(self):
        match = _WIKILINK_RE.search("See [[some-entry|display text]] here")
        assert match is not None
        assert match.group(1) is None
        assert match.group(2) == "some-entry"

    def test_cross_kb_wikilink(self):
        match = _WIKILINK_RE.search("See [[dev:some-entry]] for details")
        assert match is not None
        assert match.group(1) == "dev"
        assert match.group(2) == "some-entry"

    def test_cross_kb_wikilink_with_display(self):
        match = _WIKILINK_RE.search("See [[ops:deploy-guide|Deploy Guide]] here")
        assert match is not None
        assert match.group(1) == "ops"
        assert match.group(2) == "deploy-guide"

    def test_multiple_wikilinks(self):
        text = "See [[kb1:entry-a]] and [[entry-b]] and [[kb2:entry-c|Display]]"
        matches = list(_WIKILINK_RE.finditer(text))
        assert len(matches) == 3
        assert matches[0].group(1) == "kb1"
        assert matches[0].group(2) == "entry-a"
        assert matches[1].group(1) is None
        assert matches[1].group(2) == "entry-b"
        assert matches[2].group(1) == "kb2"
        assert matches[2].group(2) == "entry-c"

    def test_no_match_for_urls(self):
        """URLs with colons should not match as cross-KB links in regex."""
        text = "Visit https://example.com for details"
        matches = list(_WIKILINK_RE.finditer(text))
        assert len(matches) == 0


class TestCrossKBResolution:
    """Test cross-KB entry resolution via KBService."""

    @pytest.fixture
    def config_with_shortnames(self, tmp_path):
        from pyrite.config import KBConfig, PyriteConfig, Settings

        kb1_path = tmp_path / "kb1"
        kb1_path.mkdir()
        kb2_path = tmp_path / "kb2"
        kb2_path.mkdir()

        return PyriteConfig(
            knowledge_bases=[
                KBConfig(name="dev-kb", path=kb1_path, shortname="dev"),
                KBConfig(name="ops-kb", path=kb2_path, shortname="ops"),
            ],
            settings=Settings(index_path=tmp_path / "index.db"),
        )

    def test_get_kb_by_shortname(self, config_with_shortnames):
        kb = config_with_shortnames.get_kb_by_shortname("dev")
        assert kb is not None
        assert kb.name == "dev-kb"

    def test_get_kb_by_shortname_not_found(self, config_with_shortnames):
        kb = config_with_shortnames.get_kb_by_shortname("nonexistent")
        assert kb is None

    def test_resolve_cross_kb_entry(self, config_with_shortnames):
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB

        config = config_with_shortnames
        db = PyriteDB(config.settings.index_path)
        svc = KBService(config, db)

        # Register KBs and insert a test entry
        db.register_kb(
            name="dev-kb",
            kb_type="generic",
            path=str(config.knowledge_bases[0].path),
            description="",
        )
        db.upsert_entry(
            {
                "id": "my-entry",
                "kb_name": "dev-kb",
                "entry_type": "note",
                "title": "My Entry",
                "body": "",
                "tags": [],
                "links": [],
                "sources": [],
            }
        )

        # Resolve using shortname
        result = svc.resolve_entry("dev:my-entry", readable_kbs=UNSCOPED)
        assert result is not None
        assert result["id"] == "my-entry"
        assert result["kb_name"] == "dev-kb"

    def test_resolve_cross_kb_by_full_name(self, config_with_shortnames):
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB

        config = config_with_shortnames
        db = PyriteDB(config.settings.index_path)
        svc = KBService(config, db)

        db.register_kb(
            name="ops-kb",
            kb_type="generic",
            path=str(config.knowledge_bases[1].path),
            description="",
        )
        db.upsert_entry(
            {
                "id": "runbook",
                "kb_name": "ops-kb",
                "entry_type": "note",
                "title": "Runbook",
                "body": "",
                "tags": [],
                "links": [],
                "sources": [],
            }
        )

        # Resolve using full KB name
        result = svc.resolve_entry("ops-kb:runbook", readable_kbs=UNSCOPED)
        assert result is not None
        assert result["id"] == "runbook"

    def test_resolve_batch_with_cross_kb(self, config_with_shortnames):
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB

        config = config_with_shortnames
        db = PyriteDB(config.settings.index_path)
        svc = KBService(config, db)

        db.register_kb(
            name="dev-kb",
            kb_type="generic",
            path=str(config.knowledge_bases[0].path),
            description="",
        )
        db.upsert_entry(
            {
                "id": "entry-a",
                "kb_name": "dev-kb",
                "entry_type": "note",
                "title": "Entry A",
                "body": "",
                "tags": [],
                "links": [],
                "sources": [],
            }
        )

        result = svc.resolve_batch(
            ["dev:entry-a", "dev:nonexistent", "entry-a"], kb_name="dev-kb", readable_kbs=UNSCOPED
        )
        assert result["dev:entry-a"] is True
        assert result["dev:nonexistent"] is False
        assert result["entry-a"] is True


class TestWikilinkExtraction:
    """Test that cross-KB wikilinks are correctly extracted during indexing."""

    def test_cross_kb_link_extraction(self, tmp_path):
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.models import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name="test-kb", path=tmp_path),
            ],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        db = PyriteDB(config.settings.index_path)
        mgr = IndexManager(db, config)

        entry = NoteEntry(
            id="source-entry",
            title="Source",
            body="Links to [[other-kb:target-entry]] and [[local-entry]]",
        )

        data = mgr._entry_to_dict(entry, "test-kb", tmp_path / "source-entry.md")
        links = data["links"]

        # Should have two wikilinks
        targets = {link["target"] for link in links}
        assert "target-entry" in targets
        assert "local-entry" in targets

        # Cross-KB link should have the kb prefix
        cross_kb_link = next(link for link in links if link["target"] == "target-entry")
        assert cross_kb_link["kb"] == "other-kb"

        # Local link should have the current KB
        local_link = next(link for link in links if link["target"] == "local-entry")
        assert local_link["kb"] == "test-kb"

    def test_wikilinks_in_fenced_code_blocks_are_ignored(self, tmp_path):
        """Wikilink-shaped tokens inside fenced code blocks are code, not links."""
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.models import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="test-kb", path=tmp_path)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        mgr = IndexManager(PyriteDB(config.settings.index_path), config)

        body = (
            "Real link: [[real-target]]\n\n"
            "```\n"
            "Syntax example: [[bug:123]]\n"
            "Use [[kb:entry-id]] to cross-link\n"
            "```\n\n"
            "More: [[another-real]]\n"
        )
        entry = NoteEntry(id="src", title="S", body=body)
        data = mgr._entry_to_dict(entry, "test-kb", tmp_path / "src.md")
        targets = {l["target"] for l in data["links"]}
        assert "real-target" in targets
        assert "another-real" in targets
        assert "123" not in targets
        assert "entry-id" not in targets

    def test_wikilinks_in_inline_code_are_ignored(self, tmp_path):
        """Wikilink-shaped tokens inside backtick spans are code, not links."""
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.models import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="test-kb", path=tmp_path)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        mgr = IndexManager(PyriteDB(config.settings.index_path), config)

        body = (
            "Real link: [[real-target]]. "
            "Write `[[some-placeholder]]` to create a link. "
            "Also `[[wiki:PageName]]` syntax."
        )
        entry = NoteEntry(id="src", title="S", body=body)
        data = mgr._entry_to_dict(entry, "test-kb", tmp_path / "src.md")
        targets = {l["target"] for l in data["links"]}
        assert "real-target" in targets
        assert "some-placeholder" not in targets
        assert "PageName" not in targets

    def test_path_like_targets_are_rejected(self, tmp_path):
        """Targets containing `/` cannot be valid entry IDs."""
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.models import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="test-kb", path=tmp_path)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        mgr = IndexManager(PyriteDB(config.settings.index_path), config)

        body = "See [[jan6/witnesses]] and [[kb/positioning/software-teams]] and [[valid-id]]"
        entry = NoteEntry(id="src", title="S", body=body)
        data = mgr._entry_to_dict(entry, "test-kb", tmp_path / "src.md")
        targets = {l["target"] for l in data["links"]}
        assert "valid-id" in targets
        assert "jan6/witnesses" not in targets
        assert "kb/positioning/software-teams" not in targets

    def test_transclusions_in_code_blocks_are_ignored(self, tmp_path):
        """Transclusion-shaped tokens inside code blocks are code, not links."""
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.models import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="test-kb", path=tmp_path)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        mgr = IndexManager(PyriteDB(config.settings.index_path), config)

        body = "Real transclusion: ![[real-target]]\n\n```\nExample: ![[bug:123]]\n```\n"
        entry = NoteEntry(id="src", title="S", body=body)
        data = mgr._entry_to_dict(entry, "test-kb", tmp_path / "src.md")
        targets = {l["target"] for l in data["links"]}
        assert "real-target" in targets
        assert "123" not in targets

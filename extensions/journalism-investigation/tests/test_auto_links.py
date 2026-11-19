"""Tests for auto-generated bidirectional links from connection entries."""

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB


@pytest.fixture
def setup(tmp_path):
    """Set up a temporary KB with KBService for link testing."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()
    kb = KBConfig(name="test", path=kb_path, kb_type="journalism-investigation")
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    kb_service = KBService(config, db)

    yield {"db": db, "svc": kb_service, "config": config}
    db.close()


class TestOwnershipAutoLinks:
    def test_ownership_generates_owns_link(self, setup):
        """Creating an ownership entry should add owns/owned_by links."""
        svc = setup["svc"]
        entry = svc.create_entry(
            "test",
            "ownership-doe-shell",
            "Doe owns Shell Corp",
            "ownership",
            owner="[[john-doe]]",
            asset="[[shell-corp]]",
        )
        # The entry should have links added by the before_save hook
        link_targets = {(l.target, l.relation) for l in entry.links}
        assert ("john-doe", "owned_by") in link_targets
        assert ("shell-corp", "owns") in link_targets

    def test_ownership_links_indexed(self, setup):
        """Ownership links should appear in backlinks queries."""
        svc, db = setup["svc"], setup["db"]
        svc.create_entry(
            "test",
            "ownership-doe-shell",
            "Doe owns Shell Corp",
            "ownership",
            owner="[[john-doe]]",
            asset="[[shell-corp]]",
        )
        # Check outlinks from the ownership entry
        outlinks = db.get_outlinks("ownership-doe-shell", "test", readable_kbs=UNSCOPED)
        outlink_ids = {o["id"] for o in outlinks}
        assert "john-doe" in outlink_ids
        assert "shell-corp" in outlink_ids


class TestMembershipAutoLinks:
    def test_membership_generates_links(self, setup):
        svc = setup["svc"]
        entry = svc.create_entry(
            "test",
            "membership-doe-acme",
            "Doe at ACME",
            "membership",
            person="[[john-doe]]",
            organization="[[acme-corp]]",
        )
        link_targets = {(l.target, l.relation) for l in entry.links}
        assert ("john-doe", "has_member") in link_targets
        assert ("acme-corp", "member_of") in link_targets


class TestFundingAutoLinks:
    def test_funding_generates_links(self, setup):
        svc = setup["svc"]
        entry = svc.create_entry(
            "test",
            "funding-doe-pac",
            "Doe funds PAC",
            "funding",
            funder="[[john-doe]]",
            recipient="[[super-pac]]",
        )
        link_targets = {(l.target, l.relation) for l in entry.links}
        assert ("john-doe", "funded_by") in link_targets
        assert ("super-pac", "funds") in link_targets


class TestNoDoubleLinks:
    def test_no_duplicate_links_on_update(self, setup):
        """Updating a connection entry should not duplicate auto-links."""
        svc = setup["svc"]
        entry = svc.create_entry(
            "test",
            "ownership-doe-shell",
            "Doe owns Shell Corp",
            "ownership",
            owner="[[john-doe]]",
            asset="[[shell-corp]]",
        )
        initial_count = len(entry.links)

        # Update the entry
        updated = svc.update_entry("ownership-doe-shell", "test", body="Updated body")
        # Should not have doubled links
        assert len(updated.links) <= initial_count


class TestTheHooksLinkOnUpdateIsAKnownGap:
    def test_an_update_does_not_write_the_hooks_link_to_the_file(self, setup):
        """Known issue (KNOWN-ISSUES.md; ADR-0042 decision 4, #955 B6 P3a): on
        an update a `before_save` hook's return is ignored and what it sets on
        the entry never reaches the file. `enrich_connection_links` adds the
        link for a changed `person` to the entry: it is in this write's index
        row, until the next reindex, and the stale link stays in the file. On
        the base the link was written. P6's index-time derivation closes it;
        until then this pins the gap."""
        from pyrite.storage.index import IndexManager

        svc, db, config = setup["svc"], setup["db"], setup["config"]
        entry = svc.create_entry(
            "test",
            "membership-doe-acme",
            "Doe at ACME",
            "membership",
            person="[[john-doe]]",
            organization="[[acme-corp]]",
        )
        path = entry.file_path
        assert "target: john-doe" in path.read_text(encoding="utf-8")

        svc.update_entry("membership-doe-acme", "test", person="[[jane-roe]]")

        after = path.read_text(encoding="utf-8")
        assert "person: '[[jane-roe]]'" in after or 'person: "[[jane-roe]]"' in after, after
        assert "target: jane-roe" not in after, "the hook's link reached the file: close the issue"
        assert "target: john-doe" in after, "the stale link is still the file's"
        outlinks = {
            o["id"] for o in db.get_outlinks("membership-doe-acme", "test", readable_kbs=UNSCOPED)
        }
        assert "jane-roe" in outlinks, "this write's row carries the hook's link"
        IndexManager(db, config).index_all()
        outlinks = {
            o["id"] for o in db.get_outlinks("membership-doe-acme", "test", readable_kbs=UNSCOPED)
        }
        assert "jane-roe" not in outlinks, "a reindex reads the file, which never got the link"


class TestNonConnectionEntriesUnaffected:
    def test_asset_no_extra_links(self, setup):
        """Non-connection entry types should not get auto-links."""
        svc = setup["svc"]
        entry = svc.create_entry(
            "test",
            "mansion-belgravia",
            "London Mansion",
            "asset",
            asset_type="real_estate",
        )
        # Should have no auto-generated links
        assert len(entry.links) == 0

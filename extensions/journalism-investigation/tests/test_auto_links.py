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

"""Tests for the journalism-investigation plugin class."""

from pyrite_journalism_investigation.plugin import JournalismInvestigationPlugin


class TestPlugin:
    def test_name(self):
        plugin = JournalismInvestigationPlugin()
        assert plugin.name == "journalism_investigation"

    def test_get_entry_types(self):
        plugin = JournalismInvestigationPlugin()
        types = plugin.get_entry_types()
        assert "asset" in types
        assert "account" in types
        assert "document_source" in types
        assert "investigation_event" in types
        assert "transaction" in types
        assert "legal_action" in types
        assert "claim" in types
        assert "evidence" in types
        assert "ownership" in types
        assert "membership" in types
        assert "funding" in types

    def test_get_kb_types(self):
        plugin = JournalismInvestigationPlugin()
        kb_types = plugin.get_kb_types()
        assert "journalism-investigation" in kb_types

    def test_get_relationship_types(self):
        plugin = JournalismInvestigationPlugin()
        rels = plugin.get_relationship_types()
        # Check bidirectional pairs
        assert rels["owns"]["inverse"] == "owned_by"
        assert rels["owned_by"]["inverse"] == "owns"
        assert rels["sourced_from"]["inverse"] == "source_for"
        assert rels["corroborates"]["inverse"] == "corroborated_by"
        assert rels["beneficial_owner_of"]["inverse"] == "beneficially_owned_by"
        assert rels["party_to"]["inverse"] == "has_party"
        assert rels["supports"]["inverse"] == "supported_by"
        assert rels["supported_by"]["inverse"] == "supports"
        # The relations the connection hook writes (moved here from cascade, #953)
        assert rels["member_of"]["inverse"] == "has_member"
        assert rels["has_member"]["inverse"] == "member_of"
        assert rels["funded_by"]["inverse"] == "funds"
        assert rels["funds"]["inverse"] == "funded_by"
        # cascade-only relations went with the cascade extension
        assert "investigated" not in rels
        assert "capture_mechanism" not in rels

    def test_get_validators(self):
        plugin = JournalismInvestigationPlugin()
        validators = plugin.get_validators()
        assert len(validators) == 1
        assert callable(validators[0])

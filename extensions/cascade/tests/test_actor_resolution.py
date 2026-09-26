"""Tests for actor string-to-link resolution in Cascade events.

Tests the before_save hook that resolves string actor names to actor_reference
links, supporting both plain strings and wikilinks in the actors field.
"""

import pytest
from pyrite_cascade.plugin import CascadePlugin

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB


@pytest.fixture
def setup(tmp_path):
    """Set up a temporary KB with test data for actor resolution."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()
    kb = KBConfig(name="test", path=kb_path, kb_type="cascade-timeline")
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    svc = KBService(config, db)

    yield {"db": db, "svc": svc, "config": config}
    db.close()


class TestActorResolutionHook:
    """Test that string actor names create actor_reference links."""

    def test_string_actor_creates_link(self, setup):
        """A string actor name matching an actor entry creates a link."""
        svc = setup["svc"]
        # Create actor entry first
        svc.create_entry("test", "donald-trump", "Donald Trump", "actor")

        # Create timeline event referencing actor by string name
        svc.create_entry(
            "test",
            "event-1",
            "Something happened",
            "timeline_event",
            date="2025-01-20",
            actors=["Donald Trump"],
        )

        # The event should have an outlink to the actor
        outlinks = setup["db"].get_outlinks("event-1", "test", readable_kbs=UNSCOPED)
        actor_links = [o for o in outlinks if o.get("id") == "donald-trump"]
        assert len(actor_links) >= 1
        assert any(o.get("relation") == "actor_reference" for o in actor_links)

    def test_wikilink_actor_creates_link(self, setup):
        """A wikilink actor [[actor-id]] also creates a link."""
        svc = setup["svc"]
        svc.create_entry("test", "elon-musk", "Elon Musk", "actor")

        svc.create_entry(
            "test",
            "event-2",
            "Musk does thing",
            "timeline_event",
            date="2025-02-01",
            actors=["[[elon-musk]]"],
        )

        outlinks = setup["db"].get_outlinks("event-2", "test", readable_kbs=UNSCOPED)
        actor_links = [o for o in outlinks if o.get("id") == "elon-musk"]
        assert len(actor_links) >= 1

    def test_mixed_formats_in_same_event(self, setup):
        """Both string and wikilink actors in the same event create links."""
        svc = setup["svc"]
        svc.create_entry("test", "donald-trump", "Donald Trump", "actor")
        svc.create_entry("test", "elon-musk", "Elon Musk", "actor")

        svc.create_entry(
            "test",
            "event-3",
            "Joint event",
            "timeline_event",
            date="2025-03-01",
            actors=["Donald Trump", "[[elon-musk]]"],
        )

        outlinks = setup["db"].get_outlinks("event-3", "test", readable_kbs=UNSCOPED)
        linked_ids = {o.get("id") for o in outlinks}
        assert "donald-trump" in linked_ids
        assert "elon-musk" in linked_ids

    def test_actor_matched_by_alias(self, setup):
        """Actor resolved via alias, not just title."""
        svc = setup["svc"]
        # Use an entry_id that doesn't match the alias to prove alias lookup works
        svc.create_entry(
            "test",
            "federal-bureau-of-investigation",
            "Federal Bureau of Investigation",
            "actor",
            aliases=["FBI", "F.B.I."],
        )

        svc.create_entry(
            "test",
            "event-4",
            "FBI investigation",
            "timeline_event",
            date="2025-04-01",
            actors=["FBI"],
        )

        outlinks = setup["db"].get_outlinks("event-4", "test", readable_kbs=UNSCOPED)
        actor_links = [o for o in outlinks if o.get("id") == "federal-bureau-of-investigation"]
        assert len(actor_links) >= 1

    def test_unresolved_actor_no_link(self, setup):
        """Actor string with no matching entry creates no link (but no error)."""
        svc = setup["svc"]

        # No actor entry exists for "Unknown Person"
        svc.create_entry(
            "test",
            "event-5",
            "Mystery event",
            "timeline_event",
            date="2025-05-01",
            actors=["Unknown Person"],
        )

        outlinks = setup["db"].get_outlinks("event-5", "test", readable_kbs=UNSCOPED)
        # Should have no actor_reference links (the actor doesn't exist)
        actor_ref_links = [o for o in outlinks if o.get("relation") == "actor_reference"]
        assert len(actor_ref_links) == 0

    def test_no_duplicate_links(self, setup):
        """Same actor referenced twice doesn't create duplicate links."""
        svc = setup["svc"]
        svc.create_entry("test", "donald-trump", "Donald Trump", "actor")

        svc.create_entry(
            "test",
            "event-6",
            "Double reference",
            "timeline_event",
            date="2025-06-01",
            actors=["Donald Trump", "[[donald-trump]]"],
        )

        outlinks = setup["db"].get_outlinks("event-6", "test", readable_kbs=UNSCOPED)
        trump_links = [
            o
            for o in outlinks
            if o.get("id") == "donald-trump" and o.get("relation") == "actor_reference"
        ]
        assert len(trump_links) == 1

    def test_solidarity_event_actors_resolved(self, setup):
        """Actor resolution works for solidarity_event entries too."""
        svc = setup["svc"]
        svc.create_entry("test", "aclu", "ACLU", "actor")

        svc.create_entry(
            "test",
            "sol-1",
            "ACLU files lawsuit",
            "solidarity_event",
            date="2025-07-01",
            actors=["ACLU"],
        )

        outlinks = setup["db"].get_outlinks("sol-1", "test", readable_kbs=UNSCOPED)
        aclu_links = [o for o in outlinks if o.get("id") == "aclu"]
        assert len(aclu_links) >= 1

    def test_backlinks_from_actor_show_events(self, setup):
        """Actor entry's backlinks include events that reference it."""
        svc = setup["svc"]
        svc.create_entry("test", "donald-trump", "Donald Trump", "actor")

        svc.create_entry(
            "test",
            "event-7",
            "Event A",
            "timeline_event",
            date="2025-01-01",
            actors=["Donald Trump"],
        )
        svc.create_entry(
            "test",
            "event-8",
            "Event B",
            "timeline_event",
            date="2025-02-01",
            actors=["Donald Trump"],
        )

        backlinks = setup["db"].get_backlinks("donald-trump", "test", readable_kbs=UNSCOPED)
        backlink_ids = {b.get("id") for b in backlinks}
        assert "event-7" in backlink_ids
        assert "event-8" in backlink_ids

    def test_case_insensitive_matching(self, setup):
        """Actor matching is case-insensitive."""
        svc = setup["svc"]
        svc.create_entry("test", "donald-trump", "Donald Trump", "actor")

        svc.create_entry(
            "test",
            "event-9",
            "Case test",
            "timeline_event",
            date="2025-09-01",
            actors=["donald trump"],
        )

        outlinks = setup["db"].get_outlinks("event-9", "test", readable_kbs=UNSCOPED)
        trump_links = [o for o in outlinks if o.get("id") == "donald-trump"]
        assert len(trump_links) >= 1


class TestActorReferenceRelationship:
    """Test that actor_reference is registered as a relationship type."""

    def test_relationship_type_registered(self):
        plugin = CascadePlugin()
        rels = plugin.get_relationship_types()
        assert "actor_reference" in rels
        assert rels["actor_reference"]["inverse"] == "has_actor"

    def test_inverse_registered(self):
        plugin = CascadePlugin()
        rels = plugin.get_relationship_types()
        assert "has_actor" in rels
        assert rels["has_actor"]["inverse"] == "actor_reference"


class TestHookRegistration:
    """Test that the before_save hook is registered."""

    def test_before_save_hook_registered(self):
        plugin = CascadePlugin()
        hooks = plugin.get_hooks()
        assert "before_save" in hooks
        assert len(hooks["before_save"]) >= 1

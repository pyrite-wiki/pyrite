"""The standard MCP test seed: an events KB and a research KB, three events and
one person. Used by `tests/test_mcp_server.py` and the dispatch smoke test,
with `tests._world.mcp_server`."""

from pyrite.config import KBType
from pyrite.models import EventEntry, PersonEntry
from pyrite.storage.repository import KBRepository


def populate_events_and_person(_config, kb_map):
    """Populate standard test entries (3 events + 1 person)."""
    events_repo = KBRepository(kb_map["test-events"])
    for i in range(3):
        event = EventEntry.create(
            date=f"2025-01-{10 + i:02d}",
            title=f"Test Event {i}",
            body=f"This is test event {i} about immigration policy.",
            importance=5 + i,
        )
        event.tags = ["test", "immigration"]
        event.actors = ["Stephen Miller", "Joe Biden"]
        events_repo.save(event)

    research_repo = KBRepository(kb_map["test-research"])
    actor = PersonEntry.create(
        name="Stephen Miller", role="Immigration policy architect", importance=9
    )
    actor.body = "Stephen Miller is the architect of Trump's immigration policy."
    actor.tags = ["trump-admin", "immigration"]
    research_repo.save(actor)


STANDARD_KB_DEFS = [
    {"name": "test-events", "kb_type": KBType.EVENTS, "description": "Test events KB"},
    {
        "name": "test-research",
        "kb_type": KBType.RESEARCH,
        "description": "Test research KB",
        "subdirs": ["actors"],
    },
]

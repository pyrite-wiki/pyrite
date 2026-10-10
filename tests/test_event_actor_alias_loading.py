"""#698: event aliases populate the canonical attribute, not just the serializer."""

from dataclasses import fields

import pytest

from pyrite.models.core_types import EventEntry
from pyrite.plugins.registry import PluginRegistry
from pyrite_cascade import CascadePlugin
from pyrite_journalism_investigation.plugin import JournalismInvestigationPlugin


def _event_alias_cases():
    registry = PluginRegistry()
    registry.register(CascadePlugin())
    registry.register(JournalismInvestigationPlugin())
    metadata = registry.get_all_type_metadata()
    cases = []
    for name, cls in sorted(registry.get_all_entry_types().items()):
        if not issubclass(cls, EventEntry):
            continue
        attributes = {f.name for f in fields(cls)}
        for alias, target in metadata[name].get("field_aliases", {}).items():
            if target == "actors" and target in attributes:
                cases.append(pytest.param(cls, name, alias, id=f"{name}.{alias}"))
    return cases


@pytest.mark.parametrize(("cls", "type_name", "alias"), _event_alias_cases())
def test_event_alias_populates_actors(cls, type_name, alias):
    entry = cls.from_frontmatter(
        {"id": "event", "title": "Event", "type": type_name, alias: ["actor-a"]}, "Body"
    )
    assert entry.actors == ["actor-a"]
    assert entry.participants == entry.actors


@pytest.mark.control(reason="canonical spelling and empty actors already load correctly")
@pytest.mark.parametrize(("cls", "type_name", "alias"), _event_alias_cases())
@pytest.mark.parametrize("actors", [[], ["actor-b"], None])
def test_canonical_actors_keeps_precedence(cls, type_name, alias, actors):
    entry = cls.from_frontmatter(
        {
            "id": "event",
            "title": "Event",
            "type": type_name,
            "actors": actors,
            alias: ["legacy-actor"],
        },
        "Body",
    )
    assert entry.actors == (actors or [])
    assert entry.participants == entry.actors

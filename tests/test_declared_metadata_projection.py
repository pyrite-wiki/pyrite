"""Reading collection fields must not write them into the entry's metadata bag."""

from copy import deepcopy

import pytest

from pyrite.utils.metadata import declared_over_bag


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("query", "type:note"),
        ("folder_path", "notes"),
        ("view_config", {"columns": ["title"]}),
        ("entry_filter", {}),
        ("limit", 0),
        ("enabled", False),
    ],
)
def test_projection_leaves_the_complete_entry_unchanged(key, value):
    entry = {"id": "collection-1", key: value, "metadata": {"custom": "keep"}}
    before = deepcopy(entry)
    projection = declared_over_bag(entry, (key,))
    assert projection[key] == value
    assert projection["custom"] == "keep"
    assert entry == before


def test_declared_precedence_does_not_overwrite_a_legacy_bag_in_place():
    entry = {"query": "type:note", "metadata": {"query": "old", "custom": 3}}
    projection = declared_over_bag(entry, ("query",))
    assert projection == {"query": "type:note", "custom": 3}
    assert entry["metadata"] == {"query": "old", "custom": 3}


def test_even_an_empty_projection_does_not_return_the_callers_dictionary():
    entry = {"metadata": {"custom": "keep"}}
    projection = declared_over_bag(entry, ())
    projection.pop("custom")
    assert entry == {"metadata": {"custom": "keep"}}


def test_repeated_reads_of_different_declared_fields_are_independent():
    entry = {"query": "type:note", "icon": "book", "metadata": {}}
    first = declared_over_bag(entry, ("query",))
    second = declared_over_bag(entry, ("icon",))
    assert first == {"query": "type:note"}
    assert second == {"icon": "book"}
    assert entry["metadata"] == {}


@pytest.mark.parametrize("raw", ['{"custom": 4}', None, "invalid"])
def test_json_and_missing_bags_still_accept_declared_fields(raw):
    entry = {"query": "type:note", "metadata": raw}
    projection = declared_over_bag(entry, ("query",))
    assert projection["query"] == "type:note"
    assert entry["metadata"] == raw


def test_collection_endpoint_and_service_read_twice_without_mutating_source_entry():
    """Reading a collection through get_collection and KBService.get_collection_entries twice leaves the source entry unchanged."""
    from starlette.requests import Request
    from pyrite.server.endpoints.collections import get_collection
    from pyrite.services.kb_service import KBService

    source_entry = {
        "id": "col-1",
        "kb_name": "main",
        "entry_type": "collection",
        "title": "My Collection",
        "description": "Declared desc",
        "source_type": "folder",
        "icon": "folder",
        "view_config": {"columns": ["title"]},
        "folder_path": "",
        "query": "type:note",
        "tags": ["t1"],
        "metadata": {"custom": "keep"},
    }
    before = deepcopy(source_entry)

    class _StubScope:
        def as_set(self):
            return {"main"}

    class _StubService:
        def get_entry(self, entry_id, kb_name=None, readable_kbs=None):
            return source_entry if entry_id == "col-1" else None

    req = Request(
        {"type": "http", "method": "GET", "path": "/api/collections/col-1", "headers": []}
    )
    svc = _StubService()
    r1 = get_collection(req, "col-1", kb="main", svc=svc, scope=_StubScope())
    r2 = get_collection(req, "col-1", kb="main", svc=svc, scope=_StubScope())
    assert r1.query == "type:note"
    assert r2.description == "Declared desc"
    assert source_entry == before

    KBService.get_collection_entries(svc, "col-1", "main", readable_kbs={"main"})
    KBService.get_collection_entries(svc, "col-1", "main", readable_kbs={"main"})
    assert source_entry == before

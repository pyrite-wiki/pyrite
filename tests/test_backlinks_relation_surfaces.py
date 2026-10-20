"""A backlink says what the source's file wrote, on every surface (#527).

`informs` is not a declared relationship type. A backlinks row for it carries
`forward_relation` ("informs", source -> this entry, as written),
`relation` (this entry's reading, the stored inverse; `related_to` for an
unknown relation) and `inverse_relation` (the declared inverse, null when
there is none). The CLI path is pinned in test_link_relation_distinct.py; this
file enters through REST (`TestClient`) and MCP (`kb_backlinks`).
"""

from __future__ import annotations

import pytest

from pyrite.services.kb_service import KBService
from tests.characterization.world import build_world
from tests.link_scope_seed import READABLE

TARGET = "relation-target"


@pytest.fixture(scope="module")
def w(tmp_path_factory):
    world = build_world(tmp_path_factory, label="backlink-relations")
    svc = KBService(world.config, world.db)
    svc.create_entry(READABLE, TARGET, "Relation target", "note", "target")
    svc.create_entry(READABLE, "informer", "Informer", "note", "x")
    svc.create_entry(READABLE, "supporter", "Supporter", "note", "x")
    svc.add_link("informer", READABLE, TARGET, relation="informs")
    svc.add_link("supporter", READABLE, TARGET, relation="supports")
    world.index_worker.wait_for_idle(timeout=10)
    try:
        yield world
    finally:
        world.close()


def _by_source(rows):
    return {r["id"]: r for r in rows}


def _assert_rows(rows):
    rows = _by_source(rows)
    informer, supporter = rows["informer"], rows["supporter"]
    assert informer["forward_relation"] == "informs"
    assert informer["relation"] == "related_to"
    assert informer["inverse_relation"] is None
    assert supporter["forward_relation"] == "supports"
    assert supporter["relation"] == "supported_by"
    assert supporter["inverse_relation"] == "supported_by"


def test_rest_entry_backlinks_carry_both_relations(w):
    p = w.principals["admin_key"]
    r = w.client.get(
        f"/api/entries/{TARGET}",
        params={"kb": READABLE},
        headers=p.rest_headers,
        cookies=p.rest_cookies,
    )
    assert r.status_code == 200, r.text
    _assert_rows(r.json()["backlinks"])


def test_mcp_kb_backlinks_carry_both_relations(w):
    result = w.dispatch_tool(
        "kb_backlinks",
        {"entry_id": TARGET, "kb_name": READABLE},
        client_kind="local",
        readable_kbs=None,
    )
    _assert_rows(result["backlinks"])


@pytest.mark.control(
    reason="pins behaviour the first fix commit already has: inverse_relation is "
    "computed at query time while relation is the stored column"
)
def test_a_row_indexed_before_the_type_was_declared_keeps_relation_and_gains_the_inverse(w):
    """Stale row: `informs` was indexed while undeclared, so the link row stores
    `related_to` as its inverse. When a plugin later declares the type,
    `relation` stays `related_to` until a reindex and `inverse_relation`
    answers with the declared inverse (docs/json-contracts.md, Backlink rows).
    """
    from unittest.mock import patch

    from pyrite.schema import provenance

    declared = dict(provenance.get_all_relationship_types())
    declared["informs"] = {"inverse": "informed_by", "description": "declared later"}
    with patch.object(provenance, "get_all_relationship_types", return_value=declared):
        result = w.dispatch_tool(
            "kb_backlinks",
            {"entry_id": TARGET, "kb_name": READABLE},
            client_kind="local",
            readable_kbs=None,
        )
    row = _by_source(result["backlinks"])["informer"]
    assert row["forward_relation"] == "informs"
    assert row["relation"] == "related_to"
    assert row["inverse_relation"] == "informed_by"


@pytest.mark.parametrize("relation", ["transclusion", "references", "related"])
@pytest.mark.control(
    reason="pins the documented meaning of null: these relations are written by "
    "Pyrite itself and are undeclared today; declaring them is a follow-up"
)
def test_inverse_relation_is_null_for_built_in_relations_with_no_declared_inverse(relation):
    """`null` means "no inverse is declared", not "custom": the indexer writes
    `transclusion` and `references` (storage/index.py) and `parse_links` writes
    the legacy `related` (models/base.py), and none is a declared type.
    If this fails because one was declared, update docs/json-contracts.md.
    """
    from pyrite.schema.provenance import known_inverse_relation

    assert known_inverse_relation(relation) is None


def test_kb_backlinks_tool_description_names_the_relation_fields():
    from pyrite.server.tool_schemas import READ_TOOLS

    description = READ_TOOLS["kb_backlinks"]["description"]
    assert "forward_relation" in description and "inverse_relation" in description

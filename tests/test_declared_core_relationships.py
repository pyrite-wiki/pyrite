"""Core-written relations have declared inverses across real read surfaces."""

import json

import pytest

from pyrite.schema.provenance import get_all_relationship_types, known_inverse_relation
from pyrite.storage.index import IndexManager
from pyrite.storage.models import Link

PAIRS = [
    ("transclusion", "transcluded_by"),
    ("references", "referenced_by"),
    ("related", "related_to"),
]


def _seed(config, db):
    folder = config.get_kb("test-kb").path
    (folder / "target.md").write_text(
        "---\nid: target\ntitle: Target\ntype: note\n---\n", encoding="utf-8"
    )
    for relation, _ in PAIRS:
        (folder / f"{relation}.md").write_text(
            f"---\nid: {relation}\ntitle: {relation}\ntype: note\n"
            f"links:\n- target: target\n  relation: {relation}\n---\nBody\n",
            encoding="utf-8",
        )
    IndexManager(db, config).index_all()


def _assert_rows(rows):
    by_id = {row["id"]: row for row in rows}
    for relation, inverse in PAIRS:
        assert by_id[relation]["forward_relation"] == relation
        assert by_id[relation]["inverse_relation"] == inverse
        assert by_id[relation]["relation"] == inverse


@pytest.mark.parametrize("surface", ["cli", "mcp"])
def test_core_backlinks_on_cli_and_mcp(cli_world, surface):
    world = cli_world()
    _seed(world.config, world.db)
    if surface == "cli":
        result = world.cli("backlinks", "target", "-k", "test-kb", "--format", "json")
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)["entries"]
    else:
        result = world.mcp()._dispatch_tool(
            "kb_backlinks", {"entry_id": "target", "kb_name": "test-kb"}, client_kind="local"
        )
        rows = result["backlinks"]
    _assert_rows(rows)


def test_core_backlinks_on_rest(make_client):
    client, config, db = make_client()
    _seed(config, db)
    result = client.get("/api/entries/target", params={"kb": "test-kb"})
    assert result.status_code == 200, result.text
    _assert_rows(result.json()["backlinks"])


def test_old_inverse_column_stays_stored_until_reindex(cli_world):
    world = cli_world()
    _seed(world.config, world.db)
    world.db.session.query(Link).update({"inverse_relation": "related_to"})
    world.db.session.commit()
    rows = world.db.get_backlinks("target", "test-kb", readable_kbs=None)
    for row in rows:
        assert row["relation"] == "related_to"
        assert row["inverse_relation"] == dict(PAIRS)[row["forward_relation"]]
    assert {row.inverse_relation for row in world.db.session.query(Link)} == {"related_to"}


@pytest.mark.control(reason="Plugin declarations already override core names")
def test_plugin_declaration_still_overrides_a_core_relation():
    from pyrite.plugins import get_registry
    from pyrite.plugins.capabilities import Capability

    class RelationOverride:
        name = "core-relation-override-test"
        capabilities = {Capability.DOMAIN}

        def get_relationship_types(self):
            return {"transclusion": {"inverse": "plugin_inverse", "description": "Override"}}

    registry = get_registry()
    registry.register(RelationOverride())
    try:
        assert get_all_relationship_types()["transclusion"]["inverse"] == "plugin_inverse"
        assert known_inverse_relation("transclusion") == "plugin_inverse"
    finally:
        registry._plugins.pop(RelationOverride.name)
        registry._aliases_changed()


@pytest.mark.control(reason="Custom relations still have no declared inverse")
def test_custom_relation_keeps_the_unknown_inverse():
    assert known_inverse_relation("custom_unknown_relation_621") is None


@pytest.mark.parametrize("tool", ["kb_schema", "kb_orient"])
def test_declared_relations_are_discoverable_on_mcp(cli_world, tool):
    world = cli_world()
    result = world.mcp()._dispatch_tool(tool, {"kb_name": "test-kb"}, client_kind="local")
    schema = result["schema"] if tool == "kb_orient" else result
    _assert_declarations(schema["relationship_types"])


@pytest.mark.parametrize("endpoint", ["schema", "orient"])
def test_declared_relations_are_discoverable_on_rest(make_client, endpoint):
    client, _, _ = make_client()
    result = client.get(f"/api/kbs/test-kb/{endpoint}")
    assert result.status_code == 200, result.text
    body = result.json()
    schema = body["schema"] if endpoint == "orient" else body
    _assert_declarations(schema["relationship_types"])


def _assert_declarations(relationships):
    from pyrite.schema.provenance import RELATIONSHIP_TYPES

    assert "related" not in relationships
    assert known_inverse_relation("related") == "related_to"
    from pyrite.schema.provenance import get_inverse_relation

    assert get_inverse_relation("related") == "related_to"
    for name in ("transclusion", "transcluded_by", "references", "referenced_by"):
        assert relationships[name] == RELATIONSHIP_TYPES[name]["description"]

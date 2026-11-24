"""Software relation declarations across docs and CLI."""

import json
from pathlib import Path

import pytest
import yaml

from pyrite.cli import app
from pyrite.plugins import PluginRegistry, registry
from pyrite.plugins.capabilities import Capability
from pyrite.storage.index import IndexManager
from pyrite_software_kb.plugin import SoftwareKBPlugin


@pytest.fixture(autouse=True)
def software_plugin(monkeypatch):
    plugins = PluginRegistry()
    plugins._discovered = True
    plugins.register(SoftwareKBPlugin())
    monkeypatch.setattr(registry, "_registry", plugins)
    monkeypatch.setattr(app, "_groups", [group for group in app._groups if group.name != "sw"])
    monkeypatch.setattr(app, "_plugins_mounted", False)


def test_software_backlink_affects(cli_world):
    world = cli_world()
    folder = world.config.get_kb("test-kb").path
    (folder / "component.md").write_text(
        "---\nid: component\ntitle: Component\ntype: component\n---\n", encoding="utf-8"
    )
    (folder / "item.md").write_text(
        "---\nid: item\ntitle: Item\ntype: backlog_item\n"
        "links:\n- target: component\n  relation: affects\n---\n",
        encoding="utf-8",
    )
    IndexManager(world.db, world.config).index_all()
    result = world.cli("backlinks", "component", "-k", "test-kb", "--format", "json")
    assert result.exit_code == 0, result.output
    [row] = json.loads(result.stdout)["entries"]
    assert row["forward_relation"] == "affects"
    assert row["relation"] == row["inverse_relation"] == "affected_by"


def test_software_relation_listing_and_docs(cli_world, monkeypatch):
    monkeypatch.delenv("PYRITE_FORMAT", raising=False)
    schema = SoftwareKBPlugin().get_relationship_types()
    assert schema["affects"]["inverse"] == "affected_by"
    assert schema["implements"]["description"] == "Component implements an ADR or design doc"
    world = cli_world()

    result = world.cli("sw", "relations", "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"outcome": "done", "relations": schema}

    default_result = world.cli("sw", "relations")
    assert default_result.exit_code == 0, default_result.output
    assert json.loads(default_result.stdout) == {"outcome": "done", "relations": schema}

    yaml_result = world.cli("sw", "relations", "--format", "yaml")
    assert yaml_result.exit_code == 0, yaml_result.output
    assert yaml.safe_load(yaml_result.stdout) == {"outcome": "done", "relations": schema}

    rich_result = world.cli("sw", "relations", "--format", "rich")
    assert rich_result.exit_code == 0, rich_result.output
    for header in ("Relation", "Inverse", "Source types", "Target types"):
        assert header in rich_result.stdout
    for name, relation in schema.items():
        assert name in rich_result.stdout
        assert relation["inverse"] in rich_result.stdout
        for entry_type in (*relation["source_types"], *relation["target_types"]):
            assert entry_type in rich_result.stdout

    invalid = world.cli("sw", "relations", "--format", "csv")
    assert invalid.exit_code == 2, invalid.output

    docs = Path(__file__).resolve().parents[1] / "docs/software-kb-relations.md"
    rows = [
        line.split("|")[1:-1]
        for line in docs.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `")
    ]
    assert [name.strip(" `") for name, *_ in rows] == sorted(schema)
    for name, inverse, sources, targets in rows:
        relation = schema[name.strip(" `")]
        assert inverse.strip(" `") == relation["inverse"]
        assert sources.strip().replace("`", "").split(", ") == relation["source_types"]
        assert targets.strip().replace("`", "").split(", ") == relation["target_types"]


def test_software_relations_uses_registered_plugin_declarations(cli_world, monkeypatch):
    monkeypatch.delenv("PYRITE_FORMAT", raising=False)
    custom_relations = {
        "verifies": {
            "source_types": ["backlog_item"],
            "target_types": ["component"],
            "inverse": "verified_by",
            "description": "Backlog item verifies a component",
        },
        "verified_by": {
            "source_types": ["component"],
            "target_types": ["backlog_item"],
            "inverse": "verifies",
            "description": "Component verified by a backlog item",
        },
        "outcome": {
            "source_types": ["backlog_item"],
            "target_types": ["adr"],
            "inverse": "outcome_of",
            "description": "Backlog item produces an ADR outcome",
        },
        "outcome_of": {
            "source_types": ["adr"],
            "target_types": ["backlog_item"],
            "inverse": "outcome",
            "description": "ADR recorded as an outcome of a backlog item",
        },
    }

    class CustomSoftwareKBPlugin(SoftwareKBPlugin):
        def get_relationship_types(self) -> dict[str, dict]:
            return custom_relations

    active_registry = registry.get_registry()
    active_registry.register(CustomSoftwareKBPlugin())
    aggregated = active_registry.get_all_relationship_types()
    assert custom_relations.items() <= aggregated.items()
    assert "affects" not in aggregated

    world = cli_world()
    result = world.cli("sw", "relations")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload == {"outcome": "done", "relations": custom_relations}
    assert payload["outcome"] == "done"
    assert payload["relations"]["outcome"] == custom_relations["outcome"]
    assert payload["relations"]["outcome_of"] == custom_relations["outcome_of"]

    yaml_result = world.cli("sw", "relations", "--format", "yaml")
    assert yaml_result.exit_code == 0, yaml_result.output
    yaml_payload = yaml.safe_load(yaml_result.stdout)
    assert yaml_payload == {"outcome": "done", "relations": custom_relations}
    assert yaml_payload["outcome"] == "done"
    assert yaml_payload["relations"]["outcome"] == custom_relations["outcome"]
    assert yaml_payload["relations"]["outcome_of"] == custom_relations["outcome_of"]

    rich_result = world.cli("sw", "relations", "--format", "rich")
    assert rich_result.exit_code == 0, rich_result.output
    for header in ("Relation", "Inverse", "Source types", "Target types"):
        assert header in rich_result.stdout
    for text in (
        "verifies",
        "verified_by",
        "outcome",
        "outcome_of",
        "backlog_item",
        "component",
        "adr",
    ):
        assert text in rich_result.stdout
    assert "affects" not in rich_result.stdout

    class DomainlessSoftwareKBPlugin(SoftwareKBPlugin):
        capabilities = {Capability.SURFACE}

        def get_relationship_types(self) -> dict[str, dict]:
            return custom_relations

    active_registry.register(DomainlessSoftwareKBPlugin())
    domainless_aggregated = active_registry.get_all_relationship_types()
    assert "verifies" not in domainless_aggregated
    assert "outcome" not in domainless_aggregated
    dropped = world.cli("sw", "relations")
    assert dropped.exit_code == 0, dropped.output
    assert json.loads(dropped.stdout) == {"outcome": "done", "relations": {}}

    empty_registry = PluginRegistry()
    empty_registry._discovered = True
    monkeypatch.setattr(registry, "_registry", empty_registry)
    missing = world.cli("sw", "relations")
    assert missing.exit_code == 1, missing.output
    missing_payload = json.loads(missing.stdout)
    assert missing_payload["outcome"] == "error"
    assert missing_payload["error_code"] == "PLUGIN_ERROR"

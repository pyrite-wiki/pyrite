"""Pin SDK-published MCP input schemas and fixed valid argument examples (#609).

These examples validate the wire contract, not handler semantics or live KB data.
Core always runs; the explicit in-tree profile requires the five shipped plugins.
Snapshots are reviewed files. They are rewritten only by `test_regenerate_goldens`, and only
when `PYRITE_CHARACTERIZATION_REGENERATE=1` (the switch the other goldens use); read the diff.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest
from jsonschema import validators
from mcp.types import ListToolsRequest

from pyrite.config import PyriteConfig, Settings
from pyrite.plugins.registry import PluginRegistry
from pyrite.server.mcp_server import PyriteMCPServer
from tests.characterization.golden_io import REGENERATE_ENV, regenerating

GOLDENS = Path(__file__).parent / "characterization" / "goldens"
PLUGINS = {
    "pyrite_zettelkasten.plugin": "ZettelkastenPlugin",
    "pyrite_software_kb.plugin": "SoftwareKBPlugin",
    "pyrite_journalism_investigation.plugin": "JournalismInvestigationPlugin",
    "pyrite_encyclopedia.plugin": "EncyclopediaPlugin",
    "pyrite_social.plugin": "SocialPlugin",
}


def schema_registry(profile):
    registry = PluginRegistry()
    # Explicit profiles must not lazily discover installed entry points.
    registry._discovered = True
    if profile == "in-tree":
        for module, cls in PLUGINS.items():
            registry.register(getattr(pytest.importorskip(module), cls)())
    return registry


def published_schemas(root, tier, registry, monkeypatch):
    monkeypatch.setattr("pyrite.plugins.get_registry", lambda: registry)
    monkeypatch.setattr("pyrite.plugins.registry.get_registry", lambda: registry)
    config = PyriteConfig(
        knowledge_bases=[], settings=Settings(index_path=root / (tier + ".db"), auto_embed=False)
    )
    server = PyriteMCPServer(config, tier=tier)
    try:
        sdk = server.build_sdk_server()
        result = asyncio.run(
            sdk.request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
        )
        tools = result.root.tools
        assert tools
        return {tool.name: tool.inputSchema for tool in sorted(tools, key=lambda t: t.name)}
    finally:
        server.db.close()


@pytest.fixture(params=["core", "in-tree"])
def profile(request):
    return request.param


@pytest.mark.parametrize("tier", ["read", "write", "admin"])
def test_sdk_published_input_schemas_are_pinned(profile, tier, tmp_path, monkeypatch):
    expected = json.loads(
        (GOLDENS / f"mcp-input-schemas.{profile}.json").read_text(encoding="utf-8")
    )
    actual = published_schemas(tmp_path, tier, schema_registry(profile), monkeypatch)
    assert actual == {name: item["inputSchema"] for name, item in expected[tier].items()}


@pytest.mark.parametrize("tier", ["read", "write", "admin"])
def test_fixed_known_good_arguments_validate_for_every_published_tool(
    profile, tier, tmp_path, monkeypatch
):
    examples = json.loads(
        (GOLDENS / f"mcp-input-schemas.{profile}.json").read_text(encoding="utf-8")
    )[tier]
    schemas = published_schemas(tmp_path, tier, schema_registry(profile), monkeypatch)
    assert schemas.keys() == examples.keys()
    for name, schema in schemas.items():
        validator = validators.validator_for(schema)
        validator.check_schema(schema)
        validator(schema).validate(examples[name]["arguments"])


def _example_value(schema):
    """A value the schema accepts, picked from the schema alone (never from a tool's handler)."""
    for key in ("anyOf", "oneOf"):
        if schema.get(key):
            return _example_value(schema[key][0])
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "string":
        return "example"
    if kind in ("integer", "number"):
        return schema.get("minimum", 1)
    if kind == "boolean":
        return True
    if kind == "array":
        return [_example_value(schema.get("items", {}))] * schema.get("minItems", 0)
    if kind == "object":
        return _required_arguments(schema)
    return None


def _required_arguments(schema):
    properties = schema.get("properties", {})
    return {
        name: _example_value(properties.get(name, {}))
        for name in sorted(schema.get("required", []))
    }


def _valid(schema, arguments):
    return validators.validator_for(schema)(schema).is_valid(arguments)


@pytest.mark.skipif(
    not regenerating(), reason=f"rewrites the goldens; run with {REGENERATE_ENV}=1, without -n"
)
def test_regenerate_goldens(tmp_path, monkeypatch):
    """Rewrite both goldens from the live `tools/list`.

    Each tool's `inputSchema` is replaced with what the SDK publishes. A tool's pinned
    arguments are kept while they still validate; a new tool, or one whose old arguments no
    longer validate, gets its required fields filled from its schema. A tool that is no longer
    published is dropped. Review the diff, then run the file again without the switch.
    """
    assert os.environ[REGENERATE_ENV] == "1"
    for profile in ("core", "in-tree"):
        path = GOLDENS / f"mcp-input-schemas.{profile}.json"
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        new = {}
        for tier in ("read", "write", "admin"):
            schemas = published_schemas(tmp_path, tier, schema_registry(profile), monkeypatch)
            new[tier] = {}
            for name, schema in schemas.items():
                arguments = old.get(tier, {}).get(name, {}).get("arguments")
                if arguments is None or not _valid(schema, arguments):
                    arguments = _required_arguments(schema)
                new[tier][name] = {"arguments": arguments, "inputSchema": schema}
        path.write_text(json.dumps(new, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.mark.parametrize("tier", ["read", "write", "admin"])
def test_generated_arguments_validate_for_every_published_tool(
    profile, tier, tmp_path, monkeypatch
):
    """Regeneration can always give a new tool a valid example, whatever shape its schema has."""
    schemas = published_schemas(tmp_path, tier, schema_registry(profile), monkeypatch)
    invalid = [n for n, s in schemas.items() if not _valid(s, _required_arguments(s))]
    assert not invalid

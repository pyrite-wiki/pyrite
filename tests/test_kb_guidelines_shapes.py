"""A KB-level `guidelines` that is not a mapping never breaks a schema export (#1498).

`kb.yaml` can hold `guidelines:` as a string (the journalism `known-entities`
preset wrote one before #1482). The agent schema merged it into Pyrite's
default mapping and failed: MCP `kb_schema` answered INTERNAL and `orient`
returned `"schema": {}`. A string is now one guideline under `general`; a
list or a number is left out with a warning naming the key; a mapping is
merged as before.
"""

import logging

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.schema.core_types import SYSTEM_INTENT
from pyrite.schema.kb_schema import KBSchema
from pyrite.server.mcp_server import PyriteMCPServer
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB

DEFAULTS = SYSTEM_INTENT["guidelines"]


@pytest.fixture
def kb_with(tmp_path):
    opened = []

    def make(guidelines_yaml: str):
        kb_dir = tmp_path / "kb"
        kb_dir.mkdir(exist_ok=True)
        (kb_dir / "kb.yaml").write_text(
            f"name: k\nguidelines: {guidelines_yaml}\ntypes:\n  note:\n    description: A note\n"
        )
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="k", path=kb_dir)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        svc = KBService(config, PyriteDB(tmp_path / "index.db"))
        server = PyriteMCPServer(config=config, tier="read")
        opened.append((svc, server))
        return svc, server

    yield make
    for svc, server in opened:
        svc.db.close()
        server.close()


def _exports(svc, server):
    """The schema `orient` and MCP `kb_schema` export for KB `k`."""
    oriented = svc.orient("k")["schema"]
    result = server._dispatch_tool("kb_schema", {"kb_name": "k"})
    assert "error" not in result, result
    return oriented, result


def test_a_string_is_one_guideline(kb_with):
    for schema in _exports(*kb_with('"Keep records canonical."')):
        assert schema["guidelines"] == {**DEFAULTS, "general": "Keep records canonical."}
        assert "note" in schema["types"]


def test_the_string_is_still_returned_as_written(kb_with):
    svc, _ = kb_with('"Keep records canonical."')
    assert svc.orient("k")["kb_authored"]["guidelines"] == "Keep records canonical."


@pytest.mark.parametrize("value", ["[a, b]", "3"])
def test_another_shape_is_left_out_and_named(kb_with, caplog, value):
    with caplog.at_level(logging.WARNING, logger="pyrite.schema.kb_schema"):
        for schema in _exports(*kb_with(value)):
            assert schema["guidelines"] == DEFAULTS
            assert "note" in schema["types"]
    assert any("`guidelines`" in r.getMessage() for r in caplog.records)


@pytest.mark.control(reason="a mapping merges exactly as before")
def test_a_mapping_merges_as_before(kb_with):
    for schema in _exports(*kb_with("{tone: Be careful}")):
        assert schema["guidelines"] == {**DEFAULTS, "tone": "Be careful"}


def test_extract_tags_from_string_guidelines_and_goals():
    schema = KBSchema.from_dict(
        {
            "name": "k",
            "guidelines": "Keep all records #canonical and #verified.",
            "goals": "Ensure #core topics are covered.",
        }
    )
    from pyrite.services.qa_analytics_service import QAAnalyticsService

    tags = QAAnalyticsService._extract_tags_from_schema(schema)
    assert tags == {"canonical", "verified", "core"}


@pytest.mark.parametrize("bad_val", [["#bad1", "#bad2"], 42])
def test_extract_tags_handles_non_mapping_without_crash(bad_val):
    schema = KBSchema.from_dict(
        {
            "name": "k",
            "guidelines": bad_val,
            "goals": bad_val,
        }
    )
    from pyrite.services.qa_analytics_service import QAAnalyticsService

    tags = QAAnalyticsService._extract_tags_from_schema(schema)
    assert tags == set()


def test_analyze_gaps_with_string_guidelines(tmp_path):
    kb_dir = tmp_path / "k"
    kb_dir.mkdir(exist_ok=True)
    (kb_dir / "kb.yaml").write_text(
        "name: k\nguidelines: 'Use #canon tags'\ntypes:\n  note:\n    description: A note\n"
    )
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="k", path=kb_dir)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    try:
        from pyrite.services.qa_service import QAService

        report = QAService(config, db).analyze_gaps("k")
        assert report["kb_name"] == "k"
    finally:
        db.close()

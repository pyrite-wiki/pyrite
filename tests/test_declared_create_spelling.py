"""New declared fields use one top-level spelling for every entry class (#1108)."""

import copy
import json
import os

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.models.core_types import ENTRY_TYPE_REGISTRY
from pyrite.models.factory import build_entry
from pyrite.models.generic import GenericEntry
from pyrite.models.task import TaskEntry
from pyrite.plugins.registry import get_registry
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import new_entry_text
from pyrite.utils.frontmatter import load_frontmatter


def registered_types():
    registry = get_registry()
    registry.discover()
    return sorted(set(ENTRY_TYPE_REGISTRY) | set(registry.get_all_entry_types()))


@pytest.fixture
def world(tmp_path):
    opened = []

    def build(entry_type):
        folder = tmp_path / "kb"
        folder.mkdir()
        (folder / "kb.yaml").write_text(
            json.dumps(
                {
                    "name": "notes",
                    "kb_type": "generic",
                    "types": {
                        entry_type: {
                            "fields": {
                                "x_text": {"type": "text"},
                                "x_multi": {"type": "multi-select", "options": ["a", "b"]},
                                "x_num": {"type": "number"},
                                "x_bool": {"type": "checkbox"},
                            }
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        kb = KBConfig(name="notes", path=folder, kb_type="generic")
        config = PyriteConfig(
            knowledge_bases=[kb],
            settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
        )
        db = PyriteDB(config.settings.index_path)
        opened.append(db)
        IndexManager(db, config).index_all()
        return KBService(config, db), kb

    yield build
    for db in opened:
        db.close()


VALUES = {"x_text": "mine", "x_multi": ["a"], "x_num": 0, "x_bool": False}


@pytest.mark.control(
    reason="pre-existing; #468 only removed the resolve_type kwarg _prepare no longer takes"
)
@pytest.mark.parametrize("entry_type", registered_types())
@pytest.mark.parametrize("nested", [False, True])
def test_prepared_create_serializes_declared_values_canonically(world, entry_type, nested):
    svc, kb = world(entry_type)
    supplied = {**VALUES, "caller_extra": "keep"}
    fields = {"metadata": supplied} if nested else supplied
    spec = {"entry_type": entry_type, "id": "created", "title": "Field survey", **fields}
    if entry_type in {"event", "investigation_event", "legal_action", "transaction"}:
        spec["date"] = "2026-01-01"
    original_request = copy.deepcopy(spec)
    entry, _ = svc._prepare("notes", kb, spec, allow_undeclared=False)
    metadata_before = copy.deepcopy(entry.metadata)
    parsed = load_frontmatter(new_entry_text(entry))
    assert parsed is not None
    frontmatter = parsed[0]
    for key, value in VALUES.items():
        assert frontmatter[key] == value
        assert key not in (frontmatter.get("metadata") or {})
    assert entry.metadata == metadata_before
    assert spec == original_request
    if isinstance(entry, GenericEntry | TaskEntry):
        assert frontmatter["caller_extra"] == "keep"
    else:
        assert frontmatter["metadata"]["caller_extra"] == "keep"


def test_factory_without_a_kb_declaration_keeps_legacy_nested_extras():
    entry = build_entry("note", title="Field survey", metadata={"x_text": "mine"})
    assert entry.declared_frontmatter()["metadata"]["x_text"] == "mine"


@pytest.mark.skipif(os.name == "nt", reason="Public create requires POSIX file locking")
@pytest.mark.parametrize("entry_type", ["note", "event"])
def test_public_create_persists_declared_top_level_values(world, entry_type):
    svc, kb = world(entry_type)
    extra = {"date": "2026-01-01"} if entry_type == "event" else {}
    svc.create_entry("notes", "created", "Field survey", entry_type, **VALUES, **extra)
    files = list(kb.path.rglob("created.md"))
    assert len(files) == 1
    parsed = load_frontmatter(files[0].read_text(encoding="utf-8"))
    assert parsed is not None
    assert {key: parsed[0][key] for key in VALUES} == VALUES

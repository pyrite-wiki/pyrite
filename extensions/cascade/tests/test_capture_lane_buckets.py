"""Schema-backed vocabulary/drift split, driven by real KB files and index."""

import json

import pytest
from pyrite_cascade.plugin import CascadePlugin

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.plugins.context import PluginContext
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

CANONICAL = ["detention-industrial-complex", *[f"unused-{i}" for i in range(21)]]
SENTENCE = "Election-capture — SAVE database / noncitizen-voter-roll verification pipeline"


@pytest.fixture
def world(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "kb.yaml").write_text(
        json.dumps(
            {
                "name": "drift",
                "types": {
                    "note": {
                        "fields": {
                            "capture_lanes": {
                                "type": "list",
                                "options": CANONICAL,
                                "allow_other": True,
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="drift", path=kb)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)
    plugin = CascadePlugin()
    plugin.set_context(PluginContext(config=config, db=db))

    def seed(lanes):
        for i, values in enumerate(lanes):
            fm = {"id": f"n-{i}", "title": f"Note {i}", "type": "note", "capture_lanes": values}
            (kb / f"n-{i}.md").write_text(
                "---\n" + json.dumps(fm) + "\n---\n\nbody\n", encoding="utf-8"
            )
        IndexManager(db, config).index_all()
        return plugin._mcp_capture_lanes({"kb_name": "drift"})

    yield seed, plugin, config, db
    db.close()


@pytest.mark.parametrize(
    "value",
    [
        "detention-industrial-complex",
        "DETENTION-INDUSTRIAL-COMPLEX",
        "detention_industrial_complex",
        " Detention Industrial Complex ",
    ],
)
def test_variants_fold_onto_schema_vocabulary(world, value):
    seed, *_ = world
    result = seed([[value], []])
    assert len(result["canonical"]) == 22
    assert result["canonical"][0] == {"lane": CANONICAL[0], "count": 1}
    assert all(row["count"] == 0 for row in result["canonical"][1:])
    assert result["other"] == []
    assert "lowercase" in result["normalization"]
    assert result["scanned_entries"] == 2


def test_off_list_values_are_not_fuzzy_matched(world):
    seed, *_ = world
    result = seed([[SENTENCE, "ideological", "academic", "political"], ["academic"]])
    assert all(row["count"] == 0 for row in result["canonical"])
    assert result["other"][0] == {"lane": "academic", "count": 2}
    assert {row["lane"] for row in result["other"]} == {
        SENTENCE,
        "ideological",
        "academic",
        "political",
    }
    assert result["lanes"]  # legacy field retained


def test_other_bucket_is_ordered_bounded_and_reports_total(world):
    seed, *_ = world
    result = seed([[f"tail-{i:02d}"] for i in range(70)])
    assert result["other_total"] == 70
    assert result["other_has_more"] is True
    assert len(result["other"]) == 50
    assert [row["lane"] for row in result["other"]] == [f"tail-{i:02d}" for i in range(50)]


def test_empty_read_scope_exposes_no_schema_or_values(world):
    seed, plugin, *_ = world
    seed([[CANONICAL[0]]])
    result = plugin._mcp_capture_lanes({}, readable_kbs=set())
    assert result["canonical"] == []
    assert result["other"] == []
    assert result["lanes"] == []


def test_schema_changes_define_vocabulary_not_tool_constants(world):
    seed, plugin, config, _ = world
    seed([["custom_lane"]])
    path = config.get_kb("drift").path / "kb.yaml"
    schema = json.loads(path.read_text(encoding="utf-8"))
    schema["types"]["note"]["fields"]["capture_lanes"]["options"] = ["custom-lane"]
    path.write_text(json.dumps(schema), encoding="utf-8")
    config.get_kb("drift").invalidate_schema_cache()
    result = plugin._mcp_capture_lanes({"kb_name": "drift"})
    assert result["canonical"] == [{"lane": "custom-lane", "count": 1}]
    assert result["other"] == []


def test_variants_in_one_entry_count_once_and_legacy_counts_remain(world):
    seed, *_ = world
    result = seed([[CANONICAL[0], CANONICAL[0], "Detention Industrial Complex"]])
    assert result["canonical"][0]["count"] == 1
    assert next(row for row in result["lanes"] if row["lane"] == CANONICAL[0])["count"] == 2


def test_zero_entry_kb_still_lists_its_schema_vocabulary(world):
    seed, *_ = world
    result = seed([])
    assert len(result["canonical"]) == 22
    assert all(row["count"] == 0 for row in result["canonical"])
    assert result["entries_has_more"] is False


def test_registered_only_schema_is_used_and_private_schema_is_excluded(world, tmp_path):
    seed, plugin, _, db = world
    seed([])
    for name in ("registered-only", "private"):
        path = tmp_path / name
        path.mkdir()
        (path / "kb.yaml").write_text(
            json.dumps(
                {
                    "types": {
                        "note": {
                            "fields": {
                                "capture_lanes": {"type": "list", "items": {"values": [name]}}
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        db.register_kb(name, "generic", str(path))
    result = plugin._mcp_capture_lanes({}, readable_kbs={"registered-only"})
    assert result["canonical"] == [{"lane": "registered-only", "count": 0}]


def test_entry_scan_cap_is_explicit(world, monkeypatch):
    seed, plugin, _, db = world
    seed([])

    def capped(**kwargs):
        assert kwargs["limit"] == 5001
        return [{"metadata": {"capture_lanes": [CANONICAL[0]]}}] * 5001

    monkeypatch.setattr(db, "list_entries", capped)
    result = plugin._mcp_capture_lanes({"kb_name": "drift"})
    assert result["scanned_entries"] == 5000
    assert result["entries_has_more"] is True
    assert result["canonical"][0]["count"] == 5000

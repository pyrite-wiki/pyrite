"""Health groups formatting variants per KB without rewriting their values."""

import json
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.services.kb_registry_service import KBRegistryService


@pytest.fixture
def drift(tmp_path):
    kbs = []
    for name, types in [
        ("one", ["research-note"] * 3 + ["research_note"] * 2 + ["research note"]),
        ("two", ["research_note"]),
    ]:
        path = tmp_path / name
        path.mkdir()
        (path / "kb.yaml").write_text(
            yaml.safe_dump({"name": name, "types": {"research-note": {"required": ["title"]}}}),
            encoding="utf-8",
        )
        for i, typ in enumerate(types):
            (path / f"entry-{i}.md").write_text(
                "---\n"
                + yaml.safe_dump({"id": f"entry-{i}", "title": f"Entry {i}", "type": typ})
                + "---\nBody.\n",
                encoding="utf-8",
            )
        kbs.append(KBConfig(name=name, path=path))
    config = PyriteConfig(
        knowledge_bases=kbs, settings=Settings(index_path=tmp_path / "index.db", auto_embed=False)
    )
    db = PyriteDB(config.settings.index_path)
    index = IndexManager(db, config)
    index.index_all()
    try:
        yield index, config
    finally:
        db.close()


def test_group_counts_declared_spelling_and_no_writes(drift):
    index, config = drift
    before = {
        p: p.read_bytes()
        for kb in config.knowledge_bases
        for p in kb.path.rglob("*")
        if p.is_file()
    }
    report = index.check_health("one")
    assert report["type_name_drift"] == [
        {
            "kb": "one",
            "normalized": "research-note",
            "spellings_total": 3,
            "spellings_has_more": False,
            "spellings": [
                {"type": "research note", "count": 1, "declared": False},
                {"type": "research-note", "count": 3, "declared": True},
                {"type": "research_note", "count": 2, "declared": False},
            ],
        }
    ]
    assert before == {p: p.read_bytes() for p in before}


def test_all_kbs_do_not_combine_spelling_counts(drift):
    index, _ = drift
    assert len(index.check_health()["type_name_drift"]) == 1
    assert index.check_health("two")["type_name_drift"] == []


def test_registry_health_uses_same_per_kb_report(drift):
    index, config = drift
    registry = KBRegistryService(config, index.db)
    assert (
        registry.health_kb("one")["type_name_drift"] == index.check_health("one")["type_name_drift"]
    )


def test_group_report_is_bounded_and_names_total(drift):
    index, config = drift
    for i in range(187):
        for spelling in [f"custom-note-{i}", f"custom_note_{i}"]:
            index.db.upsert_entry(
                {
                    "id": spelling,
                    "kb_name": "two",
                    "entry_type": spelling,
                    "title": spelling,
                    "body": "",
                    "file_path": str(config.knowledge_bases[1].path / "entry-0.md"),
                }
            )
    result = index.check_health("two")
    assert len(result["type_name_drift"]) == 100
    assert result["type_name_drift_total"] == 187
    assert result["type_name_drift_has_more"] is True


def test_case_variants_and_spelling_bound(drift):
    import itertools

    index, config = drift
    for i, separators in enumerate(itertools.product("-_ ", repeat=4)):
        spelling = "".join("a" + separator for separator in separators) + "a"
        index.db.upsert_entry(
            {
                "id": f"variant-{i}",
                "kb_name": "two",
                "entry_type": spelling,
                "title": spelling,
                "body": "",
                "file_path": str(config.knowledge_bases[1].path / "entry-0.md"),
            }
        )
    index.db.upsert_entry(
        {
            "id": "uppercase",
            "kb_name": "two",
            "entry_type": "A-A-A-A-A",
            "title": "uppercase",
            "body": "",
            "file_path": str(config.knowledge_bases[1].path / "entry-0.md"),
        }
    )
    result = index.check_health("two")["type_name_drift"]
    group = next(g for g in result if g["normalized"] == "a-a-a-a-a")
    assert len(group["spellings"]) == 50
    assert group["spellings_total"] == 82
    assert group["spellings_has_more"] is True


@pytest.mark.parametrize("fmt", ["json", "rich"])
def test_cli_surfaces_drift_as_warning_not_failure(drift, fmt):
    index, config = drift
    with patch("pyrite.cli.context.load_config", return_value=config):
        result = CliRunner().invoke(app, ["index", "health", "-k", "one", "--format", fmt])
    assert result.exit_code == 0, result.output
    if fmt == "json":
        data = json.loads(result.stdout)
        assert data["status"] == "warning"
        assert data["checks"]["type_name_drift"]
    else:
        assert "Type name drift" in result.stdout
        assert "research_note" in result.stdout


BROKEN_KB_YAML = {
    "types-null": "name: one\ntypes: null\n",
    "types-list": "name: one\ntypes: [a, b]\n",
    "invalid-yaml": "name: one\ntypes: {research-note: [\n",
}


HEALTH_SURFACES = {
    "kb-health": ["kb", "health", "-k", "one"],
    "index-health-k": ["index", "health", "-k", "one"],
    "index-health-all": ["index", "health"],
}


@pytest.mark.parametrize("fmt", ["json", "rich"])
@pytest.mark.parametrize("surface", sorted(HEALTH_SURFACES))
@pytest.mark.parametrize("shape", sorted(BROKEN_KB_YAML))
def test_health_answers_for_a_kb_whose_schema_fails_to_load(drift, shape, surface, fmt):
    """A health check must not fail on the broken KB it is meant to diagnose:
    a kb.yaml that does not load counts as declaring no types, and the report
    says it failed to load. Without -k, one broken kb.yaml must not take down
    the report for every other KB."""
    _, config = drift
    kb = config.get_kb("one")
    kb.kb_yaml_path.write_text(BROKEN_KB_YAML[shape], encoding="utf-8")
    kb.invalidate_schema_cache()
    with patch("pyrite.cli.context.load_config", return_value=config):
        result = CliRunner().invoke(app, [*HEALTH_SURFACES[surface], "--format", fmt])
    assert result.exception is None or isinstance(result.exception, SystemExit), repr(
        result.exception
    )
    assert result.exit_code == 0, result.output
    if fmt == "json":
        data = json.loads(result.stdout)
        assert [e["kb"] for e in data["schema_errors"]] == ["one"]
        assert data["schema_errors"][0]["error"]
        group = next(g for g in data["type_name_drift"] if g["kb"] == "one")
        spellings = group["spellings"]
        assert {s["type"] for s in spellings} == {"research-note", "research_note", "research note"}
        assert not any(s["declared"] for s in spellings)
        if surface.startswith("index"):
            assert data["status"] == "warning"
            assert data["checks"]["schema_errors"] == data["schema_errors"]
            # No declared types means unrestricted (#595): nothing is
            # undeclared; the failed load shows in schema_errors above.
            assert not [r for r in data["undeclared_types"] if r["kb"] == "one"]
    else:
        assert "kb.yaml failed to load" in result.stdout

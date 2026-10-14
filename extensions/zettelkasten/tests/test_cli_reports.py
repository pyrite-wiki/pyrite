"""Exercise zettel reports through the root CLI and a real SQLite index (#866)."""

import re

import pytest
from typer.testing import CliRunner

import pyrite.config as config_module
from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings, save_config
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository


@pytest.fixture
def report_kbs(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_FILE", tmp_path / "config.yaml")
    kbs = []
    for name in ("first", "second", "empty"):
        path = tmp_path / name
        path.mkdir()
        kbs.append(KBConfig(name=name, path=path, kb_type="zettelkasten"))
    config = PyriteConfig(
        knowledge_bases=kbs,
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    save_config(config)
    db = PyriteDB(config.settings.index_path)
    IndexManager(db, config).index_all()
    try:
        yield KBService(config, db)
    finally:
        db.close()


def run_report(command, kb=None):
    args = ["zettel", command]
    if kb is not None:
        args.extend(["-k", kb])
    result = CliRunner().invoke(app, args, terminal_width=120)
    assert result.exit_code == 0, f"{result.output}\n{result.exception}"
    return result.output


@pytest.mark.parametrize("kb", ["first", None])
def test_inbox_filters_notes_in_selected_kbs(report_kbs, kb):
    for name in ("first", "second"):
        report_kbs.create_entry(
            name,
            "pending",
            f"{name} pending",
            "zettel",
            zettel_type="fleeting",
            processing_stage="capture",
        )
        report_kbs.create_entry(
            name,
            "processed",
            f"{name} processed",
            "zettel",
            zettel_type="fleeting",
            processing_stage="connect",
        )
        report_kbs.create_entry(
            name, "permanent", f"{name} permanent", "zettel", zettel_type="permanent"
        )
        report_kbs.create_entry(
            name, "literature", f"{name} literature", "literature_note", source_work="Example book"
        )
    output = run_report("inbox", kb)
    assert "first pending" in output
    assert ("second pending" in output) == (kb is None)
    assert "processed" not in output
    assert "permanent" not in output
    assert "literature" not in output
    assert "capture" in output


@pytest.mark.parametrize("kb, total", [("first", 3), (None, 4)])
def test_maturity_counts_selected_kbs(report_kbs, kb, total):
    for maturity in ("seed", "sapling", "evergreen"):
        report_kbs.create_entry(
            "first", maturity, maturity, "zettel", maturity=maturity, processing_stage="capture"
        )
    report_kbs.create_entry(
        "first", "literature", "Literature", "literature_note", source_work="Example book"
    )
    report_kbs.create_entry(
        "second", "extra", "Extra", "zettel", maturity="seed", processing_stage="capture"
    )
    output = run_report("maturity", kb)
    assert f"Total zettels: {total}" in output
    for maturity, count in (("seed", total - 2), ("sapling", 1), ("evergreen", 1)):
        assert re.search(rf"{maturity}\s+\S\s+{count}\s+\S", output)


@pytest.mark.parametrize("kb", ["empty", "missing"])
@pytest.mark.parametrize(
    "command, message", [("inbox", "Inbox empty"), ("maturity", "No zettels found")]
)
def test_empty_reports_exit_successfully(report_kbs, kb, command, message):
    assert message in run_report(command, kb)


def test_reports_include_zettels_beyond_first_page(report_kbs):
    report_kbs.create_entry(
        "first",
        "note-0000",
        "Note 0000",
        "zettel",
        zettel_type="fleeting",
        processing_stage="capture",
    )
    # Seed through the service, then index a batch of real files to avoid
    # quadratic per-create existence scans in this read-only report test.
    repo = KBRepository(report_kbs.config.get_kb("first"))
    source = repo.find_file("note-0000")
    template = source.read_text(encoding="utf-8")
    for index in range(1, 1001):
        text = template.replace("note-0000", f"note-{index:04d}").replace(
            "Note 0000", f"Note {index:04d}"
        )
        source.with_name(f"note-{index:04d}.md").write_text(text, encoding="utf-8")
    IndexManager(report_kbs.db, report_kbs.config).sync_incremental("first")
    output = run_report("inbox", "first")
    ids = re.findall(r"note-\d{4}", output)
    assert len(ids) == 1001
    assert len(set(ids)) == 1001
    assert "Total zettels: 1001" in run_report("maturity", "first")


def test_reports_default_missing_metadata_fields(report_kbs):
    report_kbs.create_entry("first", "legacy", "Legacy note", "zettel", processing_stage="capture")
    # Older indexes can omit fields that today's entry model fills by default.
    report_kbs.db.execute_write_sql(
        "UPDATE entry SET metadata = :metadata WHERE id = :id AND kb_name = :kb",
        {"metadata": '{"zettel_type": "fleeting"}', "id": "legacy", "kb": "first"},
    )
    assert "capture" in run_report("inbox", "first")
    output = run_report("maturity", "first")
    assert re.search(r"seed\s+\S\s+1\s+\S", output)

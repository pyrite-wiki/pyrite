import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from pyrite_software_kb import cli
from pyrite_software_kb.plugin import SoftwareKBPlugin
from rich.console import Console

from pyrite.storage.database import PyriteDB

KB = "public-kb"


def _make_db(tmp_path: Path, entries: list[dict], links: list[dict] | None = None) -> PyriteDB:
    db = PyriteDB(tmp_path / "index.db")
    kb_names = {entry["kb_name"] for entry in entries}
    for kb_name in kb_names:
        db._raw_conn.execute(
            "INSERT INTO kb (name, path, kb_type) VALUES (?, ?, ?)",
            (kb_name, str(tmp_path), "software"),
        )
    for entry in entries:
        metadata = {}
        if "adr_number" in entry:
            metadata["adr_number"] = entry["adr_number"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, metadata, importance, "
            "status, priority, created_at, updated_at) "
            "VALUES (?, ?, 'adr', ?, '', ?, 5, ?, 'medium', ?, ?)",
            (
                entry["id"],
                entry["kb_name"],
                entry["title"],
                json.dumps(metadata),
                entry.get("status", "proposed"),
                entry["created_at"],
                entry["created_at"],
            ),
        )
    for link in links or []:
        db._raw_conn.execute(
            "INSERT INTO link (source_id, source_kb, target_id, target_kb, relation, "
            "inverse_relation) VALUES (?, ?, ?, ?, ?, ?)",
            (
                link["source_id"],
                link["source_kb"],
                link["target_id"],
                link["target_kb"],
                link["relation"],
                link["inverse_relation"],
            ),
        )
    db._raw_conn.commit()
    return db


def _entries():
    return [
        {
            "id": "adr-0023",
            "kb_name": KB,
            "title": "ADR 23",
            "adr_number": 23,
            "created_at": "2026-04-01T00:00:00",
        },
        {
            "id": "adr-0003",
            "kb_name": KB,
            "title": "ADR 3",
            "adr_number": 3,
            "created_at": "2026-01-01T00:00:00",
        },
        {
            "id": "adr-0005",
            "kb_name": KB,
            "title": "ADR 5",
            "adr_number": 5,
            "created_at": "2026-03-01T00:00:00",
        },
        {
            "id": "adr-unknown",
            "kb_name": KB,
            "title": "ADR without a number",
            "created_at": "2026-05-01T00:00:00",
        },
    ]


def _links():
    return [
        {
            "source_id": "adr-0005",
            "source_kb": KB,
            "target_id": "adr-0003",
            "target_kb": KB,
            "relation": "supersedes",
            "inverse_relation": "superseded_by",
        },
        {
            "source_id": "adr-0023",
            "source_kb": KB,
            "target_id": "adr-0003",
            "target_kb": KB,
            "relation": "amends",
            "inverse_relation": "amended_by",
        },
    ]


def _plugin(db: PyriteDB) -> SoftwareKBPlugin:
    plugin = SoftwareKBPlugin()
    plugin._get_db = lambda: (db, False)
    return plugin


def _assert_order_and_relations(adrs: list[dict]) -> None:
    assert [adr["id"] for adr in adrs] == [
        "adr-0003",
        "adr-0005",
        "adr-0023",
        "adr-unknown",
    ]
    by_id = {adr["id"]: adr for adr in adrs}
    assert by_id["adr-0005"]["kb_name"] == KB
    assert by_id["adr-0005"]["relations"]["supersedes"] == [
        {"id": "adr-0003", "kb_name": KB, "title": "ADR 3"}
    ]
    assert by_id["adr-0003"]["relations"]["superseded_by"] == [
        {"id": "adr-0005", "kb_name": KB, "title": "ADR 5"}
    ]
    assert by_id["adr-0023"]["relations"]["amends"] == [
        {"id": "adr-0003", "kb_name": KB, "title": "ADR 3"}
    ]
    assert by_id["adr-0003"]["relations"]["amended_by"] == [
        {"id": "adr-0023", "kb_name": KB, "title": "ADR 23"}
    ]


def test_mcp_adrs_are_numbered_and_show_standing_relations(tmp_path):
    db = _make_db(tmp_path, _entries(), _links())
    try:
        result = _plugin(db)._mcp_adrs({"kb_name": KB}, readable_kbs={KB})
        _assert_order_and_relations(result["adrs"])
        page = _plugin(db)._mcp_adrs({"kb_name": KB, "limit": 2, "offset": 1}, readable_kbs={KB})
        assert [adr["id"] for adr in page["adrs"]] == ["adr-0005", "adr-0023"]
        assert page["total"] == 4 and page["has_more"]
    finally:
        db.close()


def test_cli_adrs_are_numbered_and_show_standing_relations(tmp_path, monkeypatch, capsys):
    db = _make_db(tmp_path, _entries(), _links())
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda: SimpleNamespace(settings=SimpleNamespace(index_path=tmp_path / "index.db")),
    )
    monkeypatch.setattr(cli, "PyriteDB", lambda _path: db)
    cli.sw_adrs(status=None, limit=None, offset=0, kb_name=KB, fmt="json")
    result = json.loads(capsys.readouterr().out)
    _assert_order_and_relations(result)


def test_amendment_relations_are_registered_as_inverses():
    relations = SoftwareKBPlugin().get_relationship_types()
    assert relations["amends"]["inverse"] == "amended_by"
    assert relations["amended_by"]["inverse"] == "amends"


def test_mcp_adrs_do_not_reveal_unreadable_link_sources(tmp_path):
    entries = [
        {
            "id": "adr-public",
            "kb_name": "public",
            "title": "Public ADR",
            "adr_number": 1,
            "created_at": "2026-01-01T00:00:00",
        },
        {
            "id": "adr-private",
            "kb_name": "private",
            "title": "Private ADR",
            "adr_number": 2,
            "created_at": "2026-02-01T00:00:00",
        },
    ]
    links = [
        {
            "source_id": "adr-private",
            "source_kb": "private",
            "target_id": "adr-public",
            "target_kb": "public",
            "relation": "supersedes",
            "inverse_relation": "superseded_by",
        }
    ]
    db = _make_db(tmp_path, entries, links)
    try:
        result = _plugin(db)._mcp_adrs({"kb_name": "public"}, readable_kbs={"public"})
        assert [adr["id"] for adr in result["adrs"]] == ["adr-public"]
        assert result["adrs"][0]["relations"]["superseded_by"] == []
    finally:
        db.close()


def test_cli_rich_adrs_show_ids_and_standing_relations(tmp_path, monkeypatch):
    db = _make_db(tmp_path, _entries(), _links())
    output = StringIO()
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda: SimpleNamespace(settings=SimpleNamespace(index_path=tmp_path / "index.db")),
    )
    monkeypatch.setattr(cli, "PyriteDB", lambda _path: db)
    monkeypatch.setattr(cli, "console", Console(file=output, width=180))

    cli.sw_adrs(status=None, limit=None, offset=0, kb_name=KB, fmt="rich")

    rendered = output.getvalue()
    assert "KB" in rendered and "ID" in rendered and "Relations" in rendered
    assert "adr-unknown" in rendered
    assert "supersedes: public-kb:adr-0003" in rendered
    assert "amended_by: public-kb:adr-0023" in rendered

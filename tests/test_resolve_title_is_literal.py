"""Title wikilinks are literal SQLite LIKE patterns (#1054)."""

from pyrite.services.wikilink_service import WikilinkService


def _seed_title(db, entry_id: str, title: str) -> None:
    db._raw_conn.execute(
        "INSERT OR IGNORE INTO kb (name, path, kb_type) VALUES (?, ?, 'generic')",
        ("literal-titles", "/test/literal-titles"),
    )
    db._raw_conn.execute(
        "INSERT INTO entry (id, kb_name, entry_type, title, body, created_at, updated_at) "
        "VALUES (?, 'literal-titles', 'note', ?, '', '2026-01-01T00:00:00', "
        "'2026-01-01T00:00:00')",
        (entry_id, title),
    )
    db._raw_conn.commit()


def test_title_resolution_treats_like_wildcards_as_literal(pyrite_config, pyrite_db):
    """A title lookup must not turn ``%`` or ``_`` into a wildcard."""
    _seed_title(pyrite_db, "meeting", "Meeting Notes")
    _seed_title(pyrite_db, "literal-percent", "100% done")
    _seed_title(pyrite_db, "literal-underscore", "Build_Notes")
    _seed_title(pyrite_db, "literal-backslash", "C:\\Notes")
    resolver = WikilinkService(pyrite_config, pyrite_db)

    assert resolver.resolve_entry("Meeting_Notes", readable_kbs=None) is None
    assert resolver.resolve_entry("%", readable_kbs=None) is None
    assert resolver.resolve_entry("C:\\N_tes", readable_kbs=None) is None
    assert resolver.resolve_entry("Build_Notes", readable_kbs=None)["id"] == "literal-underscore"
    assert resolver.resolve_entry("100% done", readable_kbs=None)["id"] == "literal-percent"
    assert resolver.resolve_entry("C:\\Notes", readable_kbs=None)["id"] == "literal-backslash"


def test_title_resolution_remains_case_insensitive(pyrite_config, pyrite_db):
    _seed_title(pyrite_db, "literal-case", "Meeting Notes")

    found = WikilinkService(pyrite_config, pyrite_db).resolve_entry(
        "meeting notes", readable_kbs=None
    )

    assert found is not None
    assert found["id"] == "literal-case"

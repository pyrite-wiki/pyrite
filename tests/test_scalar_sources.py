"""A bare-string `sources:` scalar reads as a one-item list (#569 item 2).

`parse_sources` returned `[]` for any non-list `sources` value, so a scalar
source silently disappeared from what the index, search and the API see.
#557's changelog named `sources: a book` as exactly this case; #566 only
fixed URL handling for *list* items, not a bare scalar.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

KB = "work"


def _env(tmp_path: Path, text: str):
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text("name: work\nkb_type: generic\n", encoding="utf-8")
    (kb_path / "notes").mkdir()
    (kb_path / "notes" / "n.md").write_text(text, encoding="utf-8")
    db_path = tmp_path / "index.db"
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=db_path),
    )
    db = PyriteDB(db_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config, kb_path


def _with_db(config: PyriteConfig, fn):
    db = PyriteDB(config.settings.index_path)
    try:
        return fn(db)
    finally:
        db.close()


@pytest.mark.parametrize(
    ("value", "want_title", "want_url"),
    [
        ("https://x.org/a", "https://x.org/a", "https://x.org/a"),
        ("a book", "a book", ""),
    ],
    ids=["url", "title"],
)
def test_a_scalar_sources_value_reads_as_a_one_item_list(tmp_path, value, want_title, want_url):
    text = f"---\nid: n\ntitle: N\ntype: note\nsources: {value}\n---\n\nBody.\n"
    config, kb_path = _env(tmp_path, text)

    entry = KBRepository(config.get_kb(KB)).load("n")
    assert [(s.title, s.url) for s in entry.sources] == [(want_title, want_url)]

    # The read path search/API uses: KBService.get_entry (what REST GET and
    # MCP kb_get both call).
    result = _with_db(
        config, lambda db: KBService(config, db).get_entry("n", KB, readable_kbs=UNSCOPED)
    )
    assert [(s["title"], s["url"]) for s in result["sources"]] == [(want_title, want_url)]


def test_a_mapping_sources_value_reads_as_a_one_item_list(tmp_path):
    """`sources:` holding a single mapping (not a list, not a string) is one
    source too, parsed the same way a dict list item is: `Source.from_dict`."""
    text = (
        "---\nid: n\ntitle: N\ntype: note\n"
        "sources:\n  title: A Report\n  url: https://x.org/r\n"
        "---\n\nBody.\n"
    )
    config, _ = _env(tmp_path, text)

    entry = KBRepository(config.get_kb(KB)).load("n")
    assert [(s.title, s.url) for s in entry.sources] == [("A Report", "https://x.org/r")]


def test_a_non_string_scalar_sources_value_reads_as_a_title(tmp_path):
    """A bare non-string scalar (e.g. a year typed as a number) is still one
    source, read as a title the way any other non-URL scalar would be."""
    text = "---\nid: n\ntitle: N\ntype: note\nsources: 1999\n---\n\nBody.\n"
    config, _ = _env(tmp_path, text)

    entry = KBRepository(config.get_kb(KB)).load("n")
    assert [(s.title, s.url) for s in entry.sources] == [("1999", "")]


def test_an_unrelated_update_does_not_rewrite_the_files_scalar_sources_line(tmp_path):
    """#557's rule: an update never loses or reshapes a key it wasn't asked
    to change -- a scalar `sources:` line stays a scalar, not reshaped into
    a block list, when only another field changes."""
    text = "---\nid: n\ntitle: N\ntype: note\nsources: a book\n---\n\nBody.\n"
    config, kb_path = _env(tmp_path, text)
    path = kb_path / "notes" / "n.md"

    _with_db(config, lambda db: KBService(config, db).update("n", KB, {"title": "Renamed"}))

    assert path.read_text(encoding="utf-8") == text.replace("title: N", "title: Renamed")

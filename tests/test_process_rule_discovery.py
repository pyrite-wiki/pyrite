"""A fresh KB index can locate the rule home, and its heading exists."""

import re
import shutil
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig
from pyrite.services.search_service import SearchService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "kb/maps/development-process-and-releases.md"
QUERIES = [
    "verify-red regression evidence",
    "test driven development red green",
    "claim scope before building",
    "finished worker report testing limits",
    "bookkeeping feedback",
]


def test_rule_pointers_resolve_to_existing_skill_headings():
    body = MAP.read_text(encoding="utf-8").split("## Find an agent process rule", 1)[1]
    body = body.split("\n## ", 1)[0]
    targets = re.findall(r"\]\((\.\./\.\./\.claude/skills/[^)]+)\)", body)
    assert len(targets) == len(QUERIES)
    for target in targets:
        filename, anchor = target.split("#")
        text = (MAP.parent / filename).read_text(encoding="utf-8")
        headings = re.findall(r"^#{1,6} (.+)$", text, re.MULTILINE)
        slugs = {re.sub(r"[^\w -]", "", h.lower()).replace(" ", "-") for h in headings}
        assert anchor in slugs, target


@pytest.fixture
def process_search(tmp_path):
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    shutil.copyfile(MAP, kb_path / MAP.name)
    config = PyriteConfig(knowledge_bases=[KBConfig(name="pyrite", path=kb_path)])
    db = PyriteDB(tmp_path / "index.db")
    try:
        IndexManager(db, config).sync_incremental("pyrite")
        yield SearchService(db)
    finally:
        db.close()


@pytest.mark.parametrize("query", QUERIES)
def test_rule_questions_find_the_process_map(process_search, query):
    results = process_search.search(query, kb_name="pyrite")
    assert any(item["id"] == "map-development-process-and-releases" for item in results)

"""A component/map citation is a backlink, and its target exists."""

import re
import shutil
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager, _strip_code_regions

ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "kb"
# B6 and the task-rollup theme own these component narratives.
DEFERRED = {"kb-service.md", "document-manager.md", "task-service.md"}


def body(path):
    text = path.read_text(encoding="utf-8")
    return text.split("---", 2)[2] if text.startswith("---") else text


def navigation_pages():
    return [
        KB / "design.md",
        *sorted((KB / "maps").glob("*.md")),
        *sorted((KB / "components").glob("*.md")),
    ]


def test_adr_mentions_are_navigable():
    offenders = []
    for path in navigation_pages():
        if path.name in DEFERRED:
            continue
        offenders.extend(
            (str(path.relative_to(KB)), match.group())
            for match in re.finditer(r"\bADR-\d{4}\b", body(path))
        )
    assert offenders == []


def test_component_and_map_wikilinks_have_targets():
    ids = set()
    for path in KB.rglob("*.md"):
        match = re.search(r"^id: (.+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            ids.add(match[1].strip("\"'"))
    missing = []
    for path in navigation_pages()[1:]:
        for target in re.findall(r"\[\[([^\]|#^]+)(?:[^\]]*)\]\]", _strip_code_regions(body(path))):
            if ":" not in target and target not in ids:
                missing.append((str(path.relative_to(KB)), target))
    assert missing == []


@pytest.mark.parametrize("adr", ["adr-0039", "adr-0041", "adr-0042", "adr-0043", "adr-0045"])
def test_real_index_exposes_map_and_component_backlinks(tmp_path, adr):
    path = tmp_path / "kb"
    path.mkdir()
    for directory in ("maps", "components", "adrs"):
        shutil.copytree(KB / directory, path / directory)
    config = PyriteConfig(knowledge_bases=[KBConfig(name="pyrite", path=path)])
    db = PyriteDB(tmp_path / "index.db")
    try:
        IndexManager(db, config).sync_incremental("pyrite")
        rows = db.get_backlinks(adr, "pyrite", readable_kbs=None)
        ids = {row["id"] for row in rows}
        assert any(item.startswith("map-") for item in ids)
        # 0041 describes file ownership; registry/config components name its dependency.
        assert ids & {
            "kb-registry-service",
            "config-system",
            "entry-model",
            "protocols-module",
            "pyrite-plugin-protocol",
            "plugin-system",
            "access-policy",
            "auth-service",
            "index-manager",
        }
    finally:
        db.close()

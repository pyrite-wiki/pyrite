"""A broken Markdown link is never read as resolved, whatever ids exist (#1723).

A Markdown link row names a path; when nothing is indexed at that path its
``target_id`` is the path text (decision D1). Pyrite accepts any hand-set id,
so an entry may have the id ``missing.md`` while stored at ``other.md``. Every
reader joins ``link.target_id`` to ``entry.id``, so without more that broken
row reads as a link to that entry: ``links check`` silent, ``index health``
clean, ``backlinks`` listing it (cold read of #1736, round 2, property 2).

The property: a Markdown path row whose path matched no entry file is never
counted as resolved by any reader. The resolver marks each path row
(``link.target_resolved``: 1 matched, 0 broken, NULL for every row that names
an id), and every SQL that reads ``link`` by ``target_id`` honours the mark,
which ``test_every_link_target_reader_honours_the_mark`` checks in the source.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.qa_analytics_service import QAAnalyticsService
from pyrite.services.qa_service import QAService
from pyrite.services.wikilink_service import WikilinkService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

ROOT = Path(__file__).resolve().parents[1]
KB = "demo"


def _note(entry_id: str, body: str = "") -> str:
    return f"---\nid: {entry_id}\ntitle: {entry_id}\ntype: note\n---\n{body}\n"


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    # The decoy: an entry whose hand-set id is the broken link's path text,
    # stored at another file.
    (root / "other.md").write_text(_note("missing.md", "Decoy."), encoding="utf-8")
    (root / "real.md").write_text(_note("real", "Real."), encoding="utf-8")
    (root / "src.md").write_text(
        _note("src", "Broken [t](missing.md), resolved [r](real.md)."), encoding="utf-8"
    )
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=root, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(tmp_path / "index.db")
    im = IndexManager(db, config)
    im.index_all()
    yield config, db, im
    db.close()


def test_links_check_health_and_qa_report_the_broken_link(world):
    config, db, im = world
    missing = WikilinkService(config, db).check_links(kb_name=KB)
    assert [(m["target_id"], [r["source_id"] for r in m["references"]]) for m in missing] == [
        ("missing.md", ["src"])
    ]
    assert im.check_health(KB)["broken_links"] == 1
    issues = QAService(config, db).validate_kb(KB, readable_kbs=UNSCOPED)["issues"]
    assert [(i["entry_id"], i["rule"]) for i in issues if i["rule"] == "broken_link"] == [
        ("src", "broken_link")
    ]
    wanted = WikilinkService(config, db).get_wanted_pages(kb_name=KB, readable_kbs=UNSCOPED)
    assert [w["target_id"] for w in wanted] == ["missing.md"]


def test_the_decoy_entry_has_no_backlink_and_the_real_one_does(world):
    _, db, _ = world
    assert db.get_backlinks("missing.md", KB, readable_kbs=UNSCOPED) == []
    # The resolved link is still a link: the mark drops only broken rows.
    assert [
        (b["id"], b["forward_relation"])
        for b in db.get_backlinks("real", KB, readable_kbs=UNSCOPED)
    ] == [("src", "markdown_link")]
    assert "missing.md" not in db.get_all_backlinks_for_kb(KB)
    assert [b["id"] for b in db.get_all_backlinks_for_kb(KB)["real"]] == ["src"]


def test_an_outlink_to_the_broken_path_is_listed_as_broken(world):
    _, db, _ = world
    out = {o["id"]: o for o in db.get_outlinks("src", KB, readable_kbs=UNSCOPED)}
    assert out["missing.md"]["title"] is None  # not the decoy's title
    assert out["real"]["title"] == "real"
    all_out = {o["id"]: o for o in db.get_all_outlinks_for_kb(KB)["src"]}
    assert all_out["missing.md"]["title"] is None and all_out["real"]["title"] == "real"


def test_the_graph_counts_and_orphans_do_not_count_the_broken_link(world):
    config, db, _ = world
    most = {r["id"]: r["link_count"] for r in db.get_most_linked(KB)}
    assert most["missing.md"] == 0 and most["real"] == 1
    orphans = {r["id"] for r in db.get_orphans(KB, readable_kbs=UNSCOPED)}
    assert "missing.md" in orphans and "real" not in orphans
    graph = db.get_graph_data(kb_name=KB, readable_kbs=UNSCOPED)
    edges = {(e["source_id"], e["target_id"]) for e in graph["edges"]}
    assert edges == {("src", "real")}
    centred = db.get_graph_data(center="src", center_kb=KB, depth=1, readable_kbs=UNSCOPED)
    assert {(e["source_id"], e["target_id"]) for e in centred["edges"]} >= {("src", "real")}
    decoy = db.get_graph_data(center="missing.md", center_kb=KB, depth=1, readable_kbs=UNSCOPED)
    assert decoy["edges"] == []
    gaps = QAAnalyticsService(config, db).analyze_gaps(KB)
    assert "missing.md" in {e["id"] for e in gaps["no_inlinks"]}
    assert "real" not in {e["id"] for e in gaps["no_inlinks"]}


def test_renaming_the_decoy_leaves_the_broken_link_broken(world):
    """The decoy's id changes: the broken row names a path, not the decoy, so it
    stays broken and the renamed entry gets no backlink (delta cold read of
    51636e68, finding 1)."""
    config, db, im = world
    other = config.knowledge_bases[0].path / "other.md"
    other.write_text(_note("x", "Decoy renamed."), encoding="utf-8")
    stat = other.stat()
    os.utime(other, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    im.sync_incremental()
    missing = WikilinkService(config, db).check_links(kb_name=KB)
    assert [m["target_id"] for m in missing] == ["missing.md"]
    assert db.get_backlinks("x", KB, readable_kbs=UNSCOPED) == []
    assert im.check_health(KB)["broken_links"] == 1


# ---------------------------------------------------------------------------
# The source rule: every SQL that reads link by target_id honours the mark
# ---------------------------------------------------------------------------

#: SQL that reads ``link`` and names ``target_id`` without the mark, with the
#: reason it need not honour it. Everything else must carry ``target_resolved``.
EXEMPT: dict[tuple[str, str], str] = {
    (
        "pyrite/services/checker_entry.py",
        "checker_entry",
    ): "the links the entry's own file writes, as written, for its checkers: no join to entry, "
    "no claim that a target exists",
}

_READS_LINK = re.compile(r"\b(?:FROM|JOIN)\s+link\b", re.IGNORECASE)


def _sql_strings(tree: ast.AST):
    """Each string expression (adjacent literals, an f-string's text) with the
    qualified name of the function it is in."""

    def walk(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                yield from walk(child, [*scope, child.name])
            elif isinstance(child, ast.JoinedStr):
                text = "".join(
                    v.value
                    for v in child.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                )
                yield ".".join(scope), text
            elif isinstance(child, ast.Constant) and isinstance(child.value, str):
                yield ".".join(scope), child.value
            else:
                yield from walk(child, scope)

    yield from walk(tree, [])


def _unmarked_readers(paths, base: Path = ROOT) -> set[tuple[str, str]]:
    found = set()
    for path in paths:
        rel = path.relative_to(base).as_posix()
        for scope, text in _sql_strings(ast.parse(path.read_text(encoding="utf-8"))):
            if _READS_LINK.search(text) and "target_id" in text and "target_resolved" not in text:
                found.add((rel, scope))
    return found


def _sources():
    return [*ROOT.glob("pyrite/**/*.py"), *ROOT.glob("extensions/*/src/**/*.py")]


def test_every_link_target_reader_honours_the_mark():
    unmarked = _unmarked_readers(_sources())
    assert unmarked <= set(EXEMPT), (
        "SQL reads link by target_id without `COALESCE(l.target_resolved, 1) = 1` (a broken "
        "Markdown link would read as resolved); add it, or list the function in EXEMPT with the "
        f"reason: {sorted(unmarked - set(EXEMPT))}"
    )
    assert set(EXEMPT) <= unmarked, (
        f"exempt but no longer unmarked: {sorted(set(EXEMPT) - unmarked)}"
    )


@pytest.mark.control(reason="a check of the source rule's own detector")
def test_the_source_rule_catches_a_new_unmarked_join(tmp_path):
    module = tmp_path / "probe.py"
    module.write_text(
        "def f():\n"
        '    return "SELECT e.id FROM link l JOIN entry e ON l.target_id = e.id"\n'
        "def g():\n"
        '    return f"SELECT e.id FROM link l JOIN entry e ON l.target_id = e.id {1}"\n'
        "def h():\n"
        '    return "SELECT 1 FROM link l WHERE COALESCE(l.target_resolved, 1) = 1 AND l.target_id = :x"\n',
        encoding="utf-8",
    )
    assert _unmarked_readers([module], base=tmp_path) == {("probe.py", "f"), ("probe.py", "g")}

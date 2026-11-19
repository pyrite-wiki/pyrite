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
The check counts joins, not strings: a text that joins on ``target_id``
twice needs the predicate twice, SQL split across ``+`` operands is folded
into the one text it builds before scanning, and an ORM join from ``Link``
to ``Entry`` on ``target_id`` must filter on ``target_resolved`` too (#1764).
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
# The source rule: every reader that joins link to entry on target_id, in
# SQL or in ORM code, honours the mark
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

#: Words that can follow a table name in SQL without being its alias.
_SQL_KEYWORDS = {
    "where",
    "on",
    "left",
    "right",
    "inner",
    "outer",
    "full",
    "cross",
    "join",
    "group",
    "order",
    "limit",
    "offset",
    "and",
    "or",
    "not",
    "exists",
    "in",
    "as",
    "by",
    "having",
    "union",
    "select",
    "from",
    "set",
    "values",
}


def _folded_text(node: ast.AST) -> str | None:
    """The text of a string expression: a literal, an f-string's constant
    text, or constants joined by ``+``. None when any part is not constant,
    so the caller walks into the parts instead."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _folded_text(node.left)
        right = _folded_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _sql_strings(tree: ast.AST):
    """Each string expression with the qualified name of the function it is
    in. Constants joined by ``+`` are folded into the one text they build, so
    SQL split across operands is scanned whole."""

    def walk(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                yield from walk(child, [*scope, child.name])
            elif (text := _folded_text(child)) is not None:
                yield ".".join(scope), text
            else:
                yield from walk(child, scope)

    yield from walk(tree, [])


def _table_aliases(text: str, table: str) -> set[str]:
    """The names ``table`` is read under in one SQL text: its aliases, or the
    table name itself when it is read unaliased or not at all."""
    aliases: set[str] = set()
    unaliased = False
    for match in re.finditer(
        rf"\b(?:FROM|JOIN)\s+{table}\b(?:\s+(?:AS\s+)?(\w+))?", text, re.IGNORECASE
    ):
        alias = match.group(1)
        if alias is None or alias.lower() in _SQL_KEYWORDS:
            unaliased = True
        else:
            aliases.add(alias)
    if unaliased or not aliases:
        aliases.add(table)
    return aliases


def _target_joins(text: str) -> int:
    """How many times one SQL text joins ``link`` to ``entry`` on
    ``target_id``: an equality between a link alias's ``target_id`` and an
    entry alias's ``id``, in either order, in an ON clause or a WHERE
    correlation alike."""
    joins = 0
    for link_alias in _table_aliases(text, "link"):
        for entry_alias in _table_aliases(text, "entry"):
            joins += len(
                re.findall(
                    rf"\b{re.escape(link_alias)}\.target_id\s*=\s*{re.escape(entry_alias)}\.id\b",
                    text,
                    re.IGNORECASE,
                )
            )
            joins += len(
                re.findall(
                    rf"\b{re.escape(entry_alias)}\.id\s*=\s*{re.escape(link_alias)}\.target_id\b",
                    text,
                    re.IGNORECASE,
                )
            )
    return joins


def _sql_unmarked(text: str) -> bool:
    """One SQL text reads ``link`` by ``target_id`` without honouring the
    mark: it carries fewer ``target_resolved`` predicates than it has joins
    on ``target_id`` (and a text that joins none still needs one predicate,
    as before)."""
    if not _READS_LINK.search(text) or "target_id" not in text:
        return False
    marks = len(re.findall(r"\btarget_resolved\b", text))
    return marks < max(_target_joins(text), 1)


def _is_link_target(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "target_id"
        and isinstance(node.value, ast.Name)
        and node.value.id == "Link"
    )


def _is_entry_id(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "id"
        and isinstance(node.value, ast.Name)
        and node.value.id == "Entry"
    )


def _orm_unmarked(tree: ast.AST) -> set[str]:
    """Qualified scopes whose ORM code joins ``Link`` to ``Entry`` on
    ``target_id`` (``Link.target_id == Entry.id``, in a ``.join`` or a
    ``.filter`` alike) without filtering on ``Link.target_resolved``
    anywhere in the same scope."""

    def walk(node, scope):
        yield scope, node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                yield from walk(child, [*scope, child.name])
            else:
                yield from walk(child, scope)

    joins: set[str] = set()
    marked: set[str] = set()
    for scope, node in walk(tree, []):
        name = ".".join(scope)
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            if any(
                (_is_link_target(a) and _is_entry_id(b)) or (_is_entry_id(a) and _is_link_target(b))
                for a, b in zip(operands, operands[1:], strict=False)
            ):
                joins.add(name)
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == "target_resolved"
            and isinstance(node.value, ast.Name)
            and node.value.id == "Link"
        ):
            marked.add(name)
    return joins - marked


def _unmarked_readers(paths, base: Path = ROOT) -> set[tuple[str, str]]:
    found = set()
    for path in paths:
        rel = path.relative_to(base).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for scope, text in _sql_strings(tree):
            if _sql_unmarked(text):
                found.add((rel, scope))
        for scope in _orm_unmarked(tree):
            found.add((rel, scope))
    return found


def _sources():
    return [*ROOT.glob("pyrite/**/*.py"), *ROOT.glob("extensions/*/src/**/*.py")]


def test_every_link_target_reader_honours_the_mark():
    unmarked = _unmarked_readers(_sources())
    assert unmarked <= set(EXEMPT), (
        "a reader joins link to entry on target_id without honouring the mark (one "
        "`COALESCE(l.target_resolved, 1) = 1` per join in SQL, a filter on "
        "`Link.target_resolved` in ORM code; a broken Markdown link would read as resolved); "
        f"add it, or list the function in EXEMPT with the reason: {sorted(unmarked - set(EXEMPT))}"
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
        '    return "SELECT 1 FROM link l WHERE COALESCE(l.target_resolved, 1) = 1 AND l.target_id = :x"\n'
        "def i():\n"
        '    return "SELECT e.id FROM link l JOIN entry e ON l.target_id = e.id '
        'JOIN entry e2 ON l.target_id = e2.id AND COALESCE(l.target_resolved, 1) = 1"\n'
        "def j():\n"
        '    return "SELECT e.id FROM link l " + "JOIN entry e ON l.target_id = e.id"\n'
        "def k():\n"
        "    return session.query(Link).join(Entry, Link.target_id == Entry.id)\n"
        "def m():\n"
        "    return session.query(Link).join(Entry, Link.target_id == Entry.id)"
        ".filter(Link.target_resolved == 1)\n",
        encoding="utf-8",
    )
    assert _unmarked_readers([module], base=tmp_path) == {
        ("probe.py", "f"),
        ("probe.py", "g"),
        ("probe.py", "i"),
        ("probe.py", "j"),
        ("probe.py", "k"),
    }

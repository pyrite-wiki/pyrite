"""A `title:` that cannot be indexed is reported by `pyrite qa` (#711).

A title that YAML reads as a list, a mapping or NaN cannot become an entry
id, so the loader refuses the file; `validate_kb` still has to name it,
which means reading the file's own frontmatter rather than the index the
file never reached. These tests go through the real paths (`validate_kb`
and `IndexManager.index_kb`) because an earlier version tested only the
private helper on a fake entry and passed with the helper disconnected.
"""

import math

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models.core_types import NoteEntry
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.kb_service import KBService
from pyrite.services.qa_service import QAService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

KB = "notes"

FILES = {
    "good.md": "---\ntype: note\ntitle: Fine\n---\nbody\n",
    "list.md": "---\ntype: note\ntitle: [a, b]\n---\nbody\n",
    "dict.md": "---\ntype: note\ntitle: {a: 1}\n---\nbody\n",
    "nan.md": "---\ntype: note\ntitle: .nan\n---\nbody\n",
    # An explicit id does not make an unindexable title indexable.
    "id-list.md": "---\nid: pinned\ntype: note\ntitle: [a, b]\n---\nbody\n",
    # Frontmatter that will not parse at all. There is no title to judge here,
    # and one such file must not take the whole KB-wide pass down with it.
    "unparseable.md": "---\ntype: note\ntitle: [a, b\n---\nbody\n",
}


@pytest.fixture
def kb(tmp_path):
    kb_path = tmp_path / KB
    kb_path.mkdir()
    for name, text in FILES.items():
        (kb_path / name).write_text(text, encoding="utf-8")
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name=KB, path=kb_path, kb_type=KBType.GENERIC),
        ],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(tmp_path / "index.db")
    svc = KBService(config, db)
    repo = KBRepository(config.get_kb(KB))
    yield config, db, svc, repo
    db.close()


def test_validate_kb_reports_every_unindexable_title(kb):
    config, db, _svc, _repo = kb
    qa = QAService(config, db)
    result = qa.validate_kb(KB, readable_kbs=UNSCOPED)
    findings = [i for i in result["issues"] if i["rule"] == "non_string_title"]
    # The path is the handle in the message; no index row exists for any of
    # these files, so no id could be reported.
    reported = {i["message"].split(":")[0] for i in findings}
    assert reported == {"list.md", "dict.md", "nan.md", "id-list.md"}
    assert all(i["severity"] == "error" for i in findings)
    assert all("cannot index it" in i["message"] for i in findings)
    # The readable file is not reported.
    assert not any("good.md" in i["message"] for i in result["issues"])


def test_validate_kb_says_which_kind_of_title_it_is(kb):
    config, db, _svc, _repo = kb
    qa = QAService(config, db)
    result = qa.validate_kb(KB, readable_kbs=UNSCOPED)
    messages = {i["message"] for i in result["issues"] if i["rule"] == "non_string_title"}
    assert any("title is a list" in m for m in messages)
    assert any("title is a mapping" in m for m in messages)
    assert any("title is NaN" in m for m in messages)


def test_a_file_whose_frontmatter_will_not_parse_does_not_stop_the_run(kb):
    config, db, _svc, _repo = kb
    qa = QAService(config, db)
    # `title: [a, b` never parses, so there is no title to judge and the
    # malformed-file rule is the one that reports it. What must not happen is
    # the exception escaping and the rest of the KB going unreported.
    result = qa.validate_kb(KB, readable_kbs=UNSCOPED)
    findings = [i for i in result["issues"] if i["rule"] == "non_string_title"]
    reported = {i["message"].split(":")[0] for i in findings}
    assert reported == {"list.md", "dict.md", "nan.md", "id-list.md"}


def test_the_index_holds_only_the_file_whose_title_loads(kb):
    config, db, _svc, _repo = kb
    # Asserts the row set: a file whose title cannot load is skipped by the
    # index path, and so is one whose frontmatter will not parse.
    IndexManager(db, config).index_kb(KB)
    rows = {r["id"]: r for r in db.list_entries(kb_name=KB)}
    assert set(rows) == {"fine"}
    assert rows["fine"]["title"] == "Fine"


def test_entry_validate_agrees_with_the_qa_rule():
    # One answer for both checks (#762 review): scalar titles are stringified
    # at load (#704) and never flagged; collections and NaN are.
    assert NoteEntry(id="x", title="2024", body="b").validate() == []
    assert NoteEntry(id="x", title=["a"], body="b").validate() == [
        "Entry title must be a string, got a list (#711)"
    ]
    assert NoteEntry(id="x", title={"a": 1}, body="b").validate() == [
        "Entry title must be a string, got a mapping (#711)"
    ]
    assert NoteEntry(id="x", title=math.nan, body="b").validate() == [
        "Entry title must be a string, got NaN (#711)"
    ]

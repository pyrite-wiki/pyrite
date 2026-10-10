"""A YAML-list title is reported before any index write (#711).

``title:`` with a list (often the old comma split in ``-f``/``--title``
parsing) used to crash ``generate_entry_id`` (TypeError) or, once
tolerated, fail the row write (CommentedSeq binding) with only a log
line. The loader now stringifies for id purposes so the file loads,
``Entry.validate()`` flags the non-string title, and ``pyrite qa``
reports it via ``non_string_title`` before any index write.
"""

from types import SimpleNamespace

from pyrite.models.core_types import NoteEntry
from pyrite.schema import generate_entry_id
from pyrite.services.qa_service import QAService


def test_list_title_yields_a_safe_id_instead_of_crashing():
    entry_id = generate_entry_id(["a", "b"])
    assert entry_id
    assert entry_id == generate_entry_id(["a", "b"])


def test_scalar_titles_still_stringified():
    assert generate_entry_id("Hello World") == "hello-world"
    assert generate_entry_id(2024) == "2024"
    assert generate_entry_id(True) == "true"


def test_list_title_fails_entry_validation():
    entry = NoteEntry(id="x", title=["a", "b"], body="b")
    errors = entry.validate()
    assert any("title" in e.lower() for e in errors)


def test_qa_reports_list_title_from_file_values():
    entry = SimpleNamespace(
        id="x",
        title=["a", "b"],
        entry_type="note",
        _source_frontmatter={"title": ["a", "b"]},
    )
    issues: list = []
    QAService._check_non_string_title(issues, "kb", entry, "f.md")
    assert len(issues) == 1
    assert issues[0]["rule"] == "non_string_title"
    assert issues[0]["severity"] == "error"


def test_qa_quiet_for_string_title():
    entry = SimpleNamespace(
        id="x",
        title="hello",
        entry_type="note",
        _source_frontmatter={"title": "hello"},
    )
    issues: list = []
    QAService._check_non_string_title(issues, "kb", entry, "f.md")
    assert issues == []

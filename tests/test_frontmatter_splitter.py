"""One function decides where a file's frontmatter starts and ends.

``pyrite.utils.frontmatter.split_frontmatter`` is that function (design.md,
principles 1 and 8). Its reference is the convention other tools follow -- Hugo
and YAML -- not Pyrite's old loader, which was the code under test, not the
property (Andon #745). ``ORACLE`` below records, per input class, what Hugo
0.151.2 does (recorded from a run; ``test_oracle_matches_live_hugo`` re-runs it
when ``hugo`` is installed), what YAML does, and what Pyrite does, classed as
``agrees``, ``fixed`` (the old Pyrite disagreed with Hugo and now agrees) or
``departs`` (a stated choice, with the reason, that refuses visibly).

Where the two old Pyrite splitters disagreed (a BOM; blanks after the closing
``---``; the repository fell back to ``EventEntry`` and lost the type) the
answer kept is Hugo's and YAML's, which also was the old loader's.
"""

from __future__ import annotations

import ast
import re
import shutil
from pathlib import Path

import pytest

from pyrite.exceptions import FrontmatterError


def _mod():
    # Imported inside the tests, so a missing module fails each test on its
    # own, and the reader tests below fail on behaviour, not on import.
    from pyrite.utils import frontmatter

    return frontmatter


# (id, text, yaml_text, body, open_start, yaml_start, close_start, close_end)
OK_CASES = [
    ("lf", "---\ntitle: A\n---\nbody\n", "title: A\n", "body", 0, 4, 13, 17),
    ("crlf", "---\r\ntitle: A\r\n---\r\nbody\r\n", "title: A\r\n", "body", 0, 5, 15, 20),
    ("mixed-endings", "---\r\ntitle: A\n---\nbody", "title: A\n", "body", 0, 5, 14, 18),
    ("bom", "\ufeff---\ntitle: A\n---\nbody", "title: A\n", "body", 1, 5, 14, 18),
    ("no-final-newline", "---\ntitle: A\n---", "title: A\n", "", 0, 4, 13, 16),
    (
        "indented-dashes-in-block-scalar",
        "---\nnote: |\n  a\n  ---\n  b\ntitle: A\n---\nbody\n",
        "note: |\n  a\n  ---\n  b\ntitle: A\n",
        "body",
        0,
        4,
        35,
        39,
    ),
    (
        "keep-scalar-last",
        "---\ntitle: A\nnote: |+\n  keep\n\n---\nbody",
        "title: A\nnote: |+\n  keep\n\n",
        "body",
        0,
        4,
        30,
        34,
    ),
    (
        "trailing-spaces-after-close",
        "---\ntitle: A\n---   \nbody",
        "title: A\n",
        "body",
        0,
        4,
        13,
        20,
    ),
    ("trailing-tab-after-close", "---\ntitle: A\n---\t\nbody", "title: A\n", "body", 0, 4, 13, 18),
    ("leading-blank-lines", "\n  \n---\ntitle: A\n---\nbody", "title: A\n", "body", 4, 8, 17, 21),
    ("open-trailing-blanks", "--- \t\ntitle: A\n---\nbody", "title: A\n", "body", 0, 6, 15, 19),
    ("open-comment", "--- # c\ntitle: A\n---\nbody", "title: A\n", "body", 0, 8, 17, 21),
    ("close-comment", "---\ntitle: A\n--- # c\nbody", "title: A\n", "body", 0, 4, 13, 21),
    ("empty-frontmatter", "---\n---\nbody", "", "body", 0, 4, 4, 8),
    (
        "body-starts-with-rule",
        "---\ntitle: A\n---\n---\nmore\n",
        "title: A\n",
        "---\nmore",
        0,
        4,
        13,
        17,
    ),
    (
        "dots-are-not-a-terminator-a-later-rule-closes",
        "---\ntitle: A\n...\nbody\n---\nmore\n",
        "title: A\n...\nbody\n",
        "more",
        0,
        4,
        22,
        26,
    ),
    (
        "dashes-inside-a-quoted-value",
        '---\ntitle: "a --- b"\n---\nbody',
        'title: "a --- b"\n',
        "body",
        0,
        4,
        21,
        25,
    ),
]

# (id, text, expected result type)
NONE_CASES = [
    ("no-frontmatter", "# Title\n\nbody\n", "NoFrontmatter"),
    ("empty-file", "", "NoFrontmatter"),
    ("four-dashes-open", "----\ntitle: A\n----\n", "NoFrontmatter"),
    ("text-after-open-dashes", "---x\ntitle: A\n---\nbody", "NoFrontmatter"),
    ("nbsp-after-open-dashes", "---\u00a0\ntitle: A\n---\nbody", "NoFrontmatter"),
    ("prose-first", "Intro\n---\ntitle: A\n---\nbody\n", "NoFrontmatter"),
    ("dots-terminator", "---\ntitle: A\n...\nbody\n", "Unterminated"),
    ("tab-before-close", "---\ntitle: A\n\t---\nbody\n", "Unterminated"),
    ("indented-close", "---\ntitle: A\n  ---\nbody\n", "Unterminated"),
    ("only-opening", "---\n", "Unterminated"),
    ("close-with-text", "---\ntitle: A\n---x\nbody\n", "Malformed"),
    ("close-four-dashes", "---\ntitle: A\n----\nbody\n---\nx\n", "Malformed"),
    ("close-with-nbsp", "---\ntitle: A\n---\u00a0\nbody\n", "Malformed"),
    ("toml", '+++\ntitle = "A"\n+++\nbody\n', "Unsupported"),
    ("json", '{"title": "A"}\nbody\n', "Unsupported"),
]


@pytest.mark.parametrize("case", OK_CASES, ids=[c[0] for c in OK_CASES])
def test_split_table(case):
    _id, text, yaml_text, body, open_start, yaml_start, close_start, close_end = case
    m = _mod()
    got = m.split_frontmatter(text)
    assert isinstance(got, m.Frontmatter), got
    assert (got.text, got.body) == (yaml_text, body)
    assert (got.open_start, got.yaml_start, got.close_start, got.close_end) == (
        open_start,
        yaml_start,
        close_start,
        close_end,
    )
    # The span is offsets into the text as given, BOM included.
    assert text[got.yaml_start : got.close_start] == got.text
    assert text[got.close_start : got.close_end].startswith("---")
    assert text[got.open_start : got.yaml_start].startswith("---")
    assert text[got.close_end :].strip() == got.body


@pytest.mark.parametrize("case", NONE_CASES, ids=[c[0] for c in NONE_CASES])
def test_no_frontmatter_is_typed(case):
    _id, text, kind = case
    got = _mod().split_frontmatter(text)
    assert type(got).__name__ == kind, got


def test_load_frontmatter():
    assert _mod().load_frontmatter("---\ntype: note\n---\nhi\n") == ({"type": "note"}, "hi")
    assert _mod().load_frontmatter("---\n---\nbody") == ({}, "body")
    assert _mod().load_frontmatter("plain") is None
    with pytest.raises(FrontmatterError):  # YAML that is not a mapping
        _mod().load_frontmatter("---\n- a\n- b\n---\nbody")
    assert _mod().load_frontmatter("---\ntitle: A\n...\nbody") is None
    for refused in ("---\ntitle: A\n---x\nbody", '+++\ntitle = "A"\n+++\n', '{"title": "A"}'):
        with pytest.raises(FrontmatterError):
            _mod().load_frontmatter(refused)


# ---- the readers, through their own entry points -------------------------


def _write(tmp_path, text: str) -> Path:
    p = tmp_path / "e.md"
    p.write_bytes(text.encode("utf-8"))
    return p


@pytest.fixture
def repo(tmp_path):
    from pyrite.config import KBConfig
    from pyrite.storage.repository import KBRepository

    return KBRepository(KBConfig(name="t", path=tmp_path, kb_type="generic"))


# The rows where the two old splitters disagreed (the repository fell back to an
# EventEntry); on every other row they agreed, and the row only pins that.
DISAGREED = {"bom", "trailing-spaces-after-close", "trailing-tab-after-close"}
_AGREED = pytest.mark.control(reason="the old repository loader and from_markdown agreed here")


@pytest.mark.parametrize(
    "case",
    [pytest.param(c, id=c[0], marks=() if c[0] in DISAGREED else _AGREED) for c in OK_CASES],
)
def test_repository_reads_every_row_as_the_splitter_does(repo, tmp_path, case):
    """The repository loader and the table agree on body and type for each row."""
    _id, text, yaml_text, body, *_ = case
    if "\n...\n" in yaml_text:  # `...` is not a terminator, so the YAML is not valid
        with pytest.raises(FrontmatterError):
            repo._load_entry(_write(tmp_path, text))
        return
    entry = repo._load_entry(_write(tmp_path, text))
    assert entry.body == body
    # A mapping with no `type:` loads as a note; an empty one falls back to an event.
    assert type(entry).__name__ == ("NoteEntry" if yaml_text.strip() else "EventEntry")


@pytest.mark.parametrize(
    "text",
    [
        "\ufeff---\ntype: note\ntitle: A\n---\nbody\n",
        "---\ntype: note\ntitle: A\n---   \nbody\n",
        "---\ntype: note\ntitle: A\n---\t\nbody\n",
    ],
    ids=["bom", "trailing-spaces", "trailing-tab"],
)
def test_repository_keeps_the_type_the_loader_keeps(repo, tmp_path, text):
    entry = repo._load_entry(_write(tmp_path, text))
    assert type(entry).__name__ == "NoteEntry"
    assert (entry.title, entry.body) == ("A", "body")


def test_repository_and_from_markdown_agree(repo, tmp_path):
    from pyrite.models.core_types import NoteEntry

    for text in (
        "---\ntype: note\ntitle: A\n---\nbody\n",
        "---\r\ntype: note\r\ntitle: A\r\n---\r\nbody\r\n",
        "\ufeff---\ntype: note\ntitle: A\n---   \nbody",
        "---\ntype: note\ntitle: A\n---\n---\nmore\n",
    ):
        a = NoteEntry.from_markdown(text)
        b = repo._load_entry(_write(tmp_path, text))
        assert (type(a).__name__, a.title, a.body) == (type(b).__name__, b.title, b.body)


# A `---` inside a quoted value used to close the frontmatter in every reader
# that looked for the first "---" anywhere (text.find("---", 3)).
TRICKY = '---\ntype: note\ntitle: "Rental --- Chicago"\naliases: [Foo, Bar]\n---\nBody here.\n'


def test_schema_check_reads_quoted_dashes(tmp_path):
    from pyrite.cli.schema_commands import _parse_frontmatter

    fm, body, errors = _parse_frontmatter(_write(tmp_path, TRICKY))
    assert errors == []
    assert fm["title"] == "Rental --- Chicago"
    assert body == "Body here."


def test_add_entry_from_file_reads_quoted_dashes(kb_service, tmp_path):
    entry, result = kb_service.add_entry_from_file(
        "test-events", _write(tmp_path, TRICKY), validate_only=True
    )
    assert entry.title == "Rental --- Chicago"
    assert entry.body == "Body here."


@pytest.mark.control(
    reason="the old reader refused an unterminated file too; this keeps the message"
)
def test_add_entry_from_file_unterminated_is_refused(kb_service, tmp_path):
    from pyrite.exceptions import ValidationError

    with pytest.raises(ValidationError, match="closing"):
        kb_service.add_entry_from_file(
            "test-events", _write(tmp_path, "---\ntype: note\ntitle: A\n"), validate_only=True
        )


def test_git_change_summary_reads_quoted_dashes():
    from pyrite.services.kb_service import KBService

    assert KBService._extract_frontmatter(TRICKY)["title"] == "Rental --- Chicago"


def test_template_file_reads_quoted_dashes(tmp_path):
    from pyrite.services.template_service import TemplateService

    parsed = TemplateService._parse_template_file(
        _write(tmp_path, '---\ntemplate_name: T\nentry_type: note\ntitle: "a --- b"\n---\nBody\n')
    )
    assert parsed["frontmatter"]["title"] == "a --- b"
    assert parsed["body"] == "Body\n"


def test_export_does_not_build_an_entry_from_prose_between_two_rules(tmp_path):
    from pyrite.cli.export_commands import _load_entry_from_result

    class Svc:
        def load_entry_from_disk(self, entry_id, kb):
            return "from-disk"

    # No frontmatter: the file opens with prose, then has two `---` rules.
    path = _write(tmp_path, "Intro.\n\n---\ntitle: Not Frontmatter\n---\nmore\n")
    got = _load_entry_from_result({"file_path": str(path), "id": "x", "kb_name": "k"}, Svc(), None)
    assert got == "from-disk"


def test_cascade_alias_hook_reads_quoted_dashes(tmp_path):
    from pyrite_cascade.hooks import _load_aliases_for_actor

    path = _write(tmp_path, TRICKY)
    lookup: dict[str, str] = {}
    _load_aliases_for_actor(None, "kb", "e1", {"file_path": str(path)}, lookup)
    assert lookup == {"foo": "e1", "bar": "e1"}


def test_journalism_known_entities_reads_quoted_dashes(tmp_path):
    from pyrite_journalism_investigation.known_entities import _extract_aliases

    path = _write(tmp_path, TRICKY)
    assert _extract_aliases({"file_path": str(path)}) == ["Foo", "Bar"]


def test_cascade_inject_ids_sees_an_id_after_a_dashed_value(tmp_path):
    from pyrite_cascade.migration import inject_ids

    text = "---\ntitle: Wait ---\nid: keep\n---\nbody\n"
    (tmp_path / "e.md").write_text(text, encoding="utf-8")
    assert inject_ids(tmp_path) == {}
    assert (tmp_path / "e.md").read_text(encoding="utf-8") == text


def test_cascade_migration_keeps_a_bom_and_converts_crlf_to_lf(tmp_path):
    """BOM files are reached; the scripts write with write_text, so CRLF becomes LF (stated in
    migration.py's docstring)."""
    from pyrite_cascade.migration import inject_ids

    (tmp_path / "e.md").write_bytes("\ufeff---\r\ntitle: T\r\n---   \r\nbody\r\n".encode())
    assert inject_ids(tmp_path)
    assert (tmp_path / "e.md").read_bytes().decode() == "\ufeff---\nid: e\ntitle: T\n---   \nbody\n"


def test_markdown_importer_reads_a_file_with_no_body_and_no_final_newline():
    from pyrite.formats.importers.markdown_importer import import_markdown

    [entry] = import_markdown("---\ntype: note\ntitle: Only Frontmatter\n---")
    assert entry["title"] == "Only Frontmatter"
    assert entry["entry_type"] == "note"


@pytest.mark.control(
    reason="the old importer read a stream of entries; this pins that the rewrite keeps it"
)
def test_markdown_importer_still_reads_a_stream_of_entries():
    from pyrite.formats.importers.markdown_importer import import_markdown

    data = "---\ntitle: A\n---\nfirst\n---\ntitle: B\n---\nsecond\n"
    assert [e["title"] for e in import_markdown(data)] == ["A", "B"]


@pytest.mark.control(
    reason="the old pin regex took trailing blanks too; this pins that the move kept BOM and CRLF"
)
def test_id_pin_inserts_before_a_closing_line_with_trailing_blanks():
    from pyrite.services.id_pin_service import insert_id_line

    out = insert_id_line("\ufeff---\r\ntitle: A\r\n---  \r\nbody\r\n", "a")
    assert out == "\ufeff---\r\ntitle: A\r\nid: a\r\n---  \r\nbody\r\n"


# ---- the oracle: Hugo and YAML, not the old loader ----------------------------
#
# (id, text, hugo, hugo_body, yaml, pyrite, body, status, why)
#   hugo       "fm" | "none" | "err": Hugo 0.151.2 building the text as content/p.md
#              (RawContent stripped = hugo_body)
#   yaml       what a YAML parser says about the same line, in words
#   pyrite     the result class of split_frontmatter; body when it is Frontmatter
#   status     agrees | fixed (old Pyrite disagreed with Hugo, now agrees) | departs
#   why        required for departs: the choice, and how it refuses
# Every text has `type: note` and `title: A` so each reader can be run on it.
B = "﻿"
Y = "type: note\ntitle: A\n"
YC = Y.replace("\n", "\r\n")
FM, NONE, ERR = "fm", "none", "err"
ORACLE = [
    # -- the plain shapes
    ("plain", f"---\n{Y}---\nbody\n", FM, "body", "fine", "Frontmatter", "body", "agrees", ""),
    (
        "crlf",
        f"---\r\n{YC}---\r\nbody\r\n",
        FM,
        "body",
        "fine",
        "Frontmatter",
        "body",
        "agrees",
        "",
    ),
    (
        "bom",
        f"{B}---\n{Y}---\nbody\n",
        FM,
        "body",
        "n/a",
        "Frontmatter",
        "body",
        "fixed",
        "the repository did not strip a BOM",
    ),
    # -- opening line
    (
        "open-trailing-spaces",
        f"--- \n{Y}---\nbody\n",
        FM,
        "body",
        "marker + blanks",
        "Frontmatter",
        "body",
        "fixed",
        "the old loader refused it",
    ),
    (
        "open-trailing-tab",
        f"---\t\n{Y}---\nbody\n",
        ERR,
        None,
        "marker + blanks",
        "Frontmatter",
        "body",
        "departs",
        "Hugo errors; YAML allows a tab after the marker, as it does after a closer",
    ),
    (
        "open-comment",
        f"--- # c\n{Y}---\nbody\n",
        FM,
        "body",
        "marker + comment",
        "Frontmatter",
        "body",
        "fixed",
        "the old loader refused it",
    ),
    (
        "open-leading-blank-line",
        "\n---\n" + Y + "---\nbody\n",
        FM,
        "body",
        "blank lines are fine",
        "Frontmatter",
        "body",
        "fixed",
        "the old loader read it as no frontmatter",
    ),
    (
        "open-four-dashes",
        f"----\n{Y}---\nbody\n",
        ERR,
        None,
        "a plain scalar",
        "NoFrontmatter",
        None,
        "agrees",
        "",
    ),
    (
        "open-dashes-then-text",
        f"---x\n{Y}---\nbody\n",
        ERR,
        None,
        "a plain scalar",
        "NoFrontmatter",
        None,
        "agrees",
        "",
    ),
    (
        "open-dashes-nbsp",
        f"--- \n{Y}---\nbody\n",
        ERR,
        None,
        "a plain scalar",
        "NoFrontmatter",
        None,
        "agrees",
        "",
    ),
    (
        "prose-first",
        f"Intro\n---\n{Y}---\nbody\n",
        NONE,
        None,
        "n/a",
        "NoFrontmatter",
        None,
        "agrees",
        "",
    ),
    # -- closing line
    (
        "close-trailing-spaces",
        f"---\n{Y}---  \nbody\n",
        FM,
        "body",
        "marker + blanks",
        "Frontmatter",
        "body",
        "fixed",
        "the repository refused it",
    ),
    (
        "close-trailing-tab",
        f"---\n{Y}---\t\nbody\n",
        FM,
        "body",
        "marker + blanks",
        "Frontmatter",
        "body",
        "fixed",
        "the repository refused it",
    ),
    (
        "close-comment",
        f"---\n{Y}--- # c\nbody\n",
        FM,
        "# c\nbody",
        "marker + comment",
        "Frontmatter",
        "body",
        "departs",
        "the comment belongs to the delimiter line (YAML); Hugo leaves it at the start of the body",
    ),
    (
        "close-four-dashes",
        f"---\n{Y}----\nbody\n",
        FM,
        "-\nbody",
        "a plain scalar",
        "Malformed",
        None,
        "departs",
        "Hugo closes on the --- prefix and reads the tail as body; here the file is refused with its line number",
    ),
    (
        "close-dashes-text",
        f"---\n{Y}---x\nbody\n",
        FM,
        "x\nbody",
        "a plain scalar",
        "Malformed",
        None,
        "departs",
        "as close-four-dashes",
    ),
    (
        "close-dashes-nbsp",
        f"---\n{Y}--- \nbody\n",
        FM,
        " \nbody",
        "a plain scalar",
        "Malformed",
        None,
        "departs",
        "as close-four-dashes (NBSP is not YAML whitespace)",
    ),
    (
        "close-dots",
        f"---\n{Y}...\nbody\n",
        ERR,
        None,
        "document end",
        "Unterminated",
        None,
        "agrees",
        "",
    ),
    (
        "close-indented",
        f"---\n{Y} ---\nbody\n",
        ERR,
        None,
        "a plain scalar",
        "Unterminated",
        None,
        "agrees",
        "",
    ),
    ("unterminated", f"---\n{Y}", ERR, None, "fine", "Unterminated", None, "agrees", ""),
    ("close-at-eof", f"---\n{Y}---", FM, "", "fine", "Frontmatter", "", "agrees", ""),
    # -- body and scalars
    (
        "body-rule",
        f"---\n{Y}---\nbody\n\n---\n\nmore\n",
        FM,
        "body\n\n---\n\nmore",
        "fine",
        "Frontmatter",
        "body\n\n---\n\nmore",
        "agrees",
        "",
    ),
    (
        "body-ends-with-rule",
        f"---\n{Y}---\nbody\n\n---\n",
        FM,
        "body\n\n---",
        "fine",
        "Frontmatter",
        "body\n\n---",
        "agrees",
        "",
    ),
    (
        "indented-in-block-scalar",
        f"---\n{Y}desc: |\n  line\n  ---\n  after\n---\nbody\n",
        FM,
        "body",
        "scalar content",
        "Frontmatter",
        "body",
        "fixed",
        "readers that used find('---') closed here",
    ),
    (
        "column-0-after-block-scalar",
        f"---\n{Y}desc: |\n  line\n---\nmore: 1\n---\nbody\n",
        FM,
        "more: 1\n---\nbody",
        "fine",
        "Frontmatter",
        "more: 1\n---\nbody",
        "agrees",
        "",
    ),
    (
        "dashes-in-quoted-value",
        '---\ntype: note\ntitle: "Rent --- Chicago"\n---\nbody\n',
        FM,
        "body",
        "fine",
        "Frontmatter",
        "body",
        "fixed",
        "readers that used find('---') closed here",
    ),
    (
        "quoted-value-spanning-a-fence",
        '---\ntype: note\ntitle: "a\n---\nb"\n---\nbody\n',
        ERR,
        None,
        "fine",
        "Frontmatter",
        'b"\n---\nbody',
        "agrees",
        "closes at the column-0 fence like Hugo; the YAML then fails to load",
    ),
    (
        "empty-frontmatter",
        "---\n---\nbody\n",
        FM,
        "body",
        "fine",
        "Frontmatter",
        "body",
        "agrees",
        "",
    ),
    ("empty-file", "", NONE, None, "n/a", "NoFrontmatter", None, "agrees", ""),
    # -- other formats
    (
        "toml",
        '+++\ntype = "note"\ntitle = "A"\n+++\nbody\n',
        FM,
        "body",
        "n/a",
        "Unsupported",
        None,
        "departs",
        "TOML frontmatter is refused with a message naming the format",
    ),
    (
        "json",
        '{"type": "note", "title": "A"}\nbody\n',
        FM,
        "body",
        "n/a",
        "Unsupported",
        None,
        "departs",
        "JSON frontmatter is refused with a message naming the format",
    ),
]
_IDS = [r[0] for r in ORACLE]
SPANS_FENCE = (
    "quoted-value-spanning-a-fence"  # Hugo errors; ours closes the same way and the YAML fails
)


def _category(kind: str) -> str:
    return {"Frontmatter": FM, "NoFrontmatter": NONE}.get(kind, ERR)


@pytest.mark.parametrize("row", ORACLE, ids=_IDS)
def test_oracle_pyrite_result(row):
    _id, text, _hugo, _hbody, _yaml, pyrite, body, _status, _why = row
    got = _mod().split_frontmatter(text)
    assert type(got).__name__ == pyrite, got
    if pyrite == "Frontmatter":
        assert got.body == body


@pytest.mark.control(
    reason="checks the ORACLE table against itself (recorded Hugo column vs the recorded Pyrite class)"
)
@pytest.mark.parametrize("row", ORACLE, ids=_IDS)
def test_oracle_status_is_honest(row):
    """`agrees`/`fixed` rows match Hugo's category and body; `departs` rows differ and say why."""
    _id, text, hugo, hbody, _yaml, pyrite, body, status, why = row
    same = _category(pyrite) == hugo and (hugo != FM or hbody == body)
    if _id == SPANS_FENCE:
        same = True
    if status == "departs":
        assert not same and why, f"{_id}: a departure needs a reason, and must actually differ"
    elif hugo == ERR:
        # Hugo refuses: we must refuse too (a non-Frontmatter result), unless a departure says why not
        assert pyrite != "Frontmatter" or _id == SPANS_FENCE, _id
    else:
        assert same, f"{_id}: marked {status} but differs from Hugo"


@pytest.mark.control(
    reason="checks the recorded Hugo column against a live Hugo run; no Pyrite code"
)
@pytest.mark.skipif(shutil.which("hugo") is None, reason="hugo is not installed")
@pytest.mark.parametrize("row", ORACLE, ids=_IDS)
def test_oracle_matches_live_hugo(row, tmp_path):
    """The recorded Hugo column is still what Hugo does."""
    import json
    import subprocess

    _id, text, hugo, hbody, *_ = row
    (tmp_path / "content").mkdir()
    (tmp_path / "layouts" / "_default").mkdir(parents=True)
    (tmp_path / "hugo.toml").write_text(
        'baseURL="http://x/"\ndisableKinds=["taxonomy","term","RSS","sitemap","robotsTXT","404","home","section"]\n'
    )
    (tmp_path / "layouts/_default/single.html").write_text(
        '{{ jsonify (dict "title" .Title "raw" .RawContent) }}'
    )
    (tmp_path / "content/p.md").write_bytes(text.encode())
    r = subprocess.run(
        ["hugo", "--quiet", "-d", "out"], cwd=tmp_path, capture_output=True, text=True
    )
    out = tmp_path / "out/p/index.html"
    if r.returncode or not out.exists():
        assert hugo == ERR, _id
        return
    got = json.loads(out.read_text().replace("&#34;", '"'))
    if hugo == NONE:
        assert got["title"] == ""
    else:
        assert hugo == FM, _id
        assert got["raw"].strip() == hbody.strip()


# ---- every reader, on every row -------------------------------------------------


def _readers(tmp_path, kb_service):
    """name -> fn(text) -> ('loaded', title, body) | ('plain',) | ('refused', why)."""
    from pyrite.cli import export_commands as ec
    from pyrite.cli.schema_commands import _parse_frontmatter
    from pyrite.config import KBConfig
    from pyrite.exceptions import PyriteError
    from pyrite.formats.importers.markdown_importer import import_markdown
    from pyrite.services.id_pin_service import PinRefusedError, insert_id_line
    from pyrite.services.kb_service import KBService
    from pyrite.services.template_service import TemplateService
    from pyrite.storage.repository import KBRepository

    def guard(fn):
        def run(text):
            try:
                return fn(text)
            except (PyriteError, PinRefusedError) as e:
                return ("refused", type(e).__name__)

        return run

    def path_of(text):
        p = tmp_path / "e.md"
        p.write_bytes(text.encode("utf-8"))
        return p

    def repo(text):
        e = KBRepository(KBConfig(name="t", path=tmp_path))._load_entry(path_of(text))
        return ("loaded", e.title, e.body)

    def schema(text):
        fm, body, errors = _parse_frontmatter(path_of(text))
        return ("refused", "errors") if errors else ("loaded", fm["title"], body)

    def add_from_file(text):
        e, _ = kb_service.add_entry_from_file("test-events", path_of(text), validate_only=True)
        return ("loaded", e.title, e.body)

    def extract(text):
        m = KBService._extract_frontmatter(text)
        return ("refused", "None") if m is None else ("loaded", m["title"], "")

    def template(text):
        t = TemplateService._parse_template_file(path_of(text))
        if t["frontmatter"] == {} and t["body"] == text:
            return ("plain",)
        return ("loaded", t["frontmatter"]["title"], t["body"].strip())

    def importer(text):
        entries = import_markdown(text)
        if not entries:
            return ("plain",)
        e = entries[0]
        return ("plain",) if e["title"] == "Untitled" else ("loaded", e["title"], e["body"])

    def export(text):
        class Svc:
            def load_entry_from_disk(self, *_):
                return None

        e = ec._load_entry_from_result({"file_path": str(path_of(text))}, Svc(), None)
        return ("refused", "None") if e is None else ("loaded", e.title, e.body)

    def pin(text):
        out = insert_id_line(text, "abc")
        return ("loaded", "A", "") if "id: abc" in out else ("refused", "no id")

    def inject(text):
        from pyrite_cascade.migration import inject_ids

        d = tmp_path / "inj"
        d.mkdir(exist_ok=True)
        (d / "e.md").write_bytes(text.encode("utf-8"))
        return ("loaded", "A", "") if inject_ids(d) else ("refused", "skipped")

    def aliases(which):
        def run(text):
            p = path_of(text.replace("type: note", "type: note\naliases: [Zed]"))
            if which == "hooks":
                from pyrite_cascade.hooks import _load_aliases_for_actor

                lk: dict = {}
                _load_aliases_for_actor(None, "k", "i", {"file_path": str(p)}, lk)
                found = list(lk)
            else:
                from pyrite_journalism_investigation.known_entities import _extract_aliases

                found = [a.lower() for a in _extract_aliases({"file_path": str(p)})]
            return ("loaded", "A", "") if found == ["zed"] else ("refused", "no aliases")

        return run

    return {
        "repository": guard(repo),
        "schema-check": guard(schema),
        "add_entry_from_file": guard(add_from_file),
        "git-change-summary": guard(extract),
        "template": guard(template),
        "importer": guard(importer),
        "export": guard(export),
        "ids-pin": guard(pin),
        "cascade-inject_ids": guard(inject),
        "cascade-aliases": guard(aliases("hooks")),
        "journalism-aliases": guard(aliases("known")),
    }


# Rows every old reader already read as Hugo does; here they only pin that.
_OLD_AGREED = {
    "plain",
    "crlf",
    "body-rule",
    "body-ends-with-rule",
    "column-0-after-block-scalar",
    "empty-file",
}
_AGREED = pytest.mark.control(reason="the old readers read this shape as Hugo does")


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(r, id=r[0], marks=_AGREED if r[0] in _OLD_AGREED else ())
        for r in ORACLE
        if r[0]
        != "empty-frontmatter"  # not an entry: the repository and from_markdown differ on it
    ],
)
def test_every_reader_follows_the_rule_or_refuses(row, tmp_path, kb_service):
    """Each reader reads the file as the oracle says or refuses it with a typed result. None
    reads refused YAML as body, none raises an untyped error."""
    _id, text, _h, _hb, _y, pyrite, body, _status, _why = row
    if _id == "empty-frontmatter":
        pytest.skip(
            "a frontmatter with no fields is not an entry; the repository and from_markdown differ on it"
        )
    title = {"dashes-in-quoted-value": "Rent --- Chicago"}.get(_id, "A")
    for name, read in _readers(tmp_path, kb_service).items():
        for f in tmp_path.glob("**/*.md"):
            f.unlink()
        got = read(text)
        if pyrite == "Frontmatter" and _id != SPANS_FENCE:
            if name in (
                "git-change-summary",
                "ids-pin",
                "cascade-inject_ids",
                "cascade-aliases",
                "journalism-aliases",
            ):
                # these read part of the file: they find what is there, from the right place
                assert got[0] == "loaded" or (name == "ids-pin" and _id == "close-at-eof"), (
                    name,
                    got,
                )
            else:
                assert got == ("loaded", title, body), (name, got)
        elif pyrite == "NoFrontmatter" and name in ("template", "importer"):
            assert got == ("plain",) or got[0] == "refused", (name, got)
        else:
            # inject_ids edits text and never parses the YAML, so it does not see the bad quote
            assert got[0] == "refused" or (_id == SPANS_FENCE and name == "cascade-inject_ids"), (
                name,
                got,
            )


def test_a_bad_template_does_not_break_the_listing(tmp_path):
    from pyrite.config import KBConfig, PyriteConfig, Settings
    from pyrite.services.template_service import TemplateService

    kb = KBConfig(name="k", path=tmp_path)
    (tmp_path / "_templates").mkdir()
    (tmp_path / "_templates/good.md").write_text("---\ntemplate_name: Good\n---\nBody\n")
    (tmp_path / "_templates/four.md").write_text("---\ntemplate_name: Four\n----\nBody\n")
    (tmp_path / "_templates/text.md").write_text("---\ntemplate_name: Text\n---x\nBody\n")
    (tmp_path / "_templates/toml.md").write_text('+++\ntemplate_name = "T"\n+++\n')
    svc = TemplateService(PyriteConfig(knowledge_bases=[kb], settings=Settings()))
    assert [t["name"] for t in svc.list_templates("k")] == ["Good"]


def test_importer_does_not_invent_an_entry_from_a_body_rule():
    from pyrite.formats.importers.markdown_importer import import_markdown

    for body in ("body\n\n---\n", "body\n\n---\n\nmore prose\n", "body\n---\nnot: frontmatter\n"):
        [entry] = import_markdown(f"---\ntype: note\ntitle: A\n---\n{body}")
        assert entry["title"] == "A"


# ---- the rule lives in one place -----------------------------------------------
#
# The scan flags, in `pyrite/` and `extensions/*/src` outside utils/frontmatter.py:
#   * a string constant spelling a fence: contains `---` and has no word in it
#     (not a message, not a markdown table separator);
#   * a regex quantifier on dashes (`-{3}`), a computed `"-" * 3`;
#   * a YAML multi-document reader (`load_all`, `safe_load_all`).
# It does not see a fence built from `chr(45)` or read from config. A file that
# legitimately writes a fence is listed in WRITERS with the number of lines
# it may hold; going over (or under) fails, so a new use in a writer is seen too.

ROOT = Path(__file__).resolve().parent.parent
ONE_MODULE = ROOT / "pyrite" / "utils" / "frontmatter.py"
WRITERS = {
    "pyrite/formats/markdown_fmt.py": (2, "writes a fence"),
    "pyrite/formats/importers/markdown_importer.py": (
        1,
        "re-adds an opening fence to a stream segment",
    ),
    "pyrite/models/base.py": (1, "Entry.to_markdown writes the fences"),
    "pyrite/cli/entry_commands.py": (1, "prints a template's fences"),
    "pyrite/renderers/notebooklm.py": (2, "writes a fence"),
    "pyrite/renderers/quartz.py": (5, "writes fences"),
    "pyrite/services/export_service.py": (2, "writes a fence"),
    "pyrite/services/kb_registry_service.py": (2, "writes a fence"),
    "pyrite/services/kb_service.py": (1, "writes a fence"),
    "pyrite/server/endpoints/ai_ep.py": (1, "a horizontal rule in prompt text"),
}


def _scanned_files():
    for base in [ROOT / "pyrite", *sorted((ROOT / "extensions").glob("*/src"))]:
        yield from sorted(base.rglob("*.py"))


def _docstring_ids(tree) -> set[int]:
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = n.body[0] if n.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def fence_findings(source: str) -> list[int]:
    """Line numbers of constants and calls that spell or read a frontmatter fence."""
    tree = ast.parse(source)
    docs = _docstring_ids(tree)
    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            v = n.value
            if "-{3" in v:
                hits.append(n.lineno)
            elif "---" in v and "|" not in v and not re.search(r"[A-Za-z]{2,}", v):
                hits.append(n.lineno)
        elif (
            isinstance(n, ast.BinOp)
            and isinstance(n.op, ast.Mult)
            and any(isinstance(x, ast.Constant) and x.value == "-" for x in (n.left, n.right))
            and any(isinstance(x, ast.Constant) and x.value == 3 for x in (n.left, n.right))
        ):
            hits.append(n.lineno)
        elif isinstance(n, ast.Attribute) and n.attr in ("load_all", "safe_load_all"):
            hits.append(n.lineno)
    return sorted(set(hits))


def test_no_other_module_reads_or_splits_a_fence_of_its_own():
    found = {}
    for path in _scanned_files():
        if path == ONE_MODULE:
            continue
        rel = str(path.relative_to(ROOT))
        lines = fence_findings(path.read_text(encoding="utf-8"))
        allowed = WRITERS.get(rel, (0, ""))[0]
        if len(lines) != allowed:
            found[rel] = f"{len(lines)} fence constants/readers at {lines}, allowed {allowed}"
    assert not found, (
        "A module spells or reads a frontmatter fence outside pyrite/utils/frontmatter.py. "
        "Readers call split_frontmatter (design.md, principle 8); a writer is listed in WRITERS "
        f"with its count: {found}"
    )


MUTANT_SPLITTERS = [
    'import re\nFENCE_RE = re.compile(r"^-{3}\\s*$", re.M)\n',
    'FENCE = "---"\n',
    'ok = lines[0].strip() == "---"\n',
    'ok = t[:3] == "---"\n',
    't.removeprefix("---")\n',
    't.rpartition("---")\n',
    'x = "\\n---\\n" in t\n',
    "import yaml\ndocs = list(yaml.safe_load_all(t))\n",
    't.split("\\n---")\n',
    't.index("---", 3)\n',
    't.startswith(("---\\n", "---\\r\\n"))\n',
    'import re\nre.match(r"\\A---", t)\n',
    'F = "-" * 3\n',
    't.find("---", 3)\n',
    'import re\nre.split(r"^---\\s*$", t, flags=re.M)\n',
]


@pytest.mark.control(reason="tests the scanner in this file; the scan of the tree is the guard")
@pytest.mark.parametrize("source", MUTANT_SPLITTERS)
def test_the_scan_catches_each_mutant_splitter(source):
    assert fence_findings(source), source


@pytest.mark.control(reason="tests the scanner in this file, not production code")
def test_the_scan_leaves_messages_tables_and_docstrings_alone():
    src = 'def f():\n    """A --- fence."""\n    return "missing closing ---", "|---|---|"\n'
    assert fence_findings(src) == []


def test_the_writers_allowlist_is_exact():
    """Every listed writer still holds exactly its count (a stale entry hides a new use)."""
    for rel, (count, _why) in WRITERS.items():
        assert len(fence_findings((ROOT / rel).read_text(encoding="utf-8"))) == count, rel

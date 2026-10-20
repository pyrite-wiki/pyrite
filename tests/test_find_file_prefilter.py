"""A lookup by id reads no more YAML than it must (#135).

``find_file``'s scan, ``files_to_delete``'s walk and ``get``'s miss used to
parse every file's frontmatter to learn the id it holds: about 1 ms a file,
seconds on a large KB, twice per create. ``could_hold_id`` is a raw-text
prefilter in front of that parse. It may answer "cannot hold" only when
``read_entry_id`` -- THE answer to "which id does this file hold" (ADR-0038
decision 1) -- could not return the wanted id; a false negative would make a
create write a second file for an id that exists, or a delete miss the file.

So the first tests compare the prefilter with the full parse over a corpus of
awkward files, each with its id written by hand from YAML's own rules. The
rest count parses and walks on real KBs, never wall-clock time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import pyrite.models.core_types as core_types
import pyrite.storage.repository as repo_mod
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.models.core_types import read_entry_id
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.repository import KBRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
KB = "pfkb"


def could_hold_id(text: str, entry_id: str) -> bool:
    """Imported per call, so the repository tests below fail on behaviour,
    not on the import, where the prefilter does not exist."""
    return core_types.could_hold_id(text, entry_id)


def _held(text: str) -> str | None:
    """The id ``id_of_file`` reports: ``read_entry_id``, None when it raises."""
    try:
        return read_entry_id(text)
    except Exception:
        return None


def fm(yaml: str, body: str = "body\n", nl: str = "\n") -> str:
    return f"---{nl}{yaml.replace(chr(10), nl)}---{nl}{nl}{body}"


# (name, text, the id read_entry_id returns -- by hand, from YAML's rules)
CORPUS: list[tuple[str, str, str | None]] = [
    ("plain", fm("id: alpha\ntitle: A\n"), "alpha"),
    ("plain, comment after", fm("id: alpha # the id\ntitle: A\n"), "alpha"),
    ("after simple keys", fm("type: note\ntitle: Plain one\nid: alpha\n"), "alpha"),
    ("after a comment line", fm("# header\nid: alpha\n"), "alpha"),
    ("after an empty key", fm("tags:\nid: alpha\n"), "alpha"),
    ("double-quoted", fm('id: "alpha"\n'), "alpha"),
    ("single-quoted", fm("id: 'alpha'\n"), "alpha"),
    ("escaped: the id is not in the text", fm('id: "\\x61lpha"\n'), "alpha"),
    ("alias", fm("ref: &a alpha\nid: *a\n"), "alpha"),
    ("anchored id", fm("id: &x alpha\n"), "alpha"),
    ("tagged id", fm("id: !!str 123\n"), "123"),
    ("CRLF", fm("id: alpha\ntitle: A\n", nl="\r\n"), "alpha"),
    ("BOM", "\ufeff" + fm("id: alpha\ntitle: A\n"), "alpha"),
    ("BOM and CRLF", "\ufeff" + fm("id: alpha\n", nl="\r\n"), "alpha"),
    ("lone CR breaks the line for YAML", fm("title: x\rid: alpha\n"), "alpha"),
    ("id in a comment only", fm("# id: alpha\ntitle: Beta\n"), "beta"),
    ("id in the body only", fm("title: Beta\n", body="id: alpha\n"), "beta"),
    # A quoted scalar may continue at column 0: the `id:` line is part of the
    # title, there is no id key, and the derived id is not in the text.
    ("id line inside a quoted title", fm('title: "abc\nid: foo"\n'), "abc-id-foo"),
    # A lone CR ends a line for YAML: what follows it opens the quoted title.
    ("CR inside a comment", fm('# c\rtitle: "abc\nid: foo\n"\n'), "abc-id-foo"),
    ("CR inside a plain value", fm('type: note\rtitle: "abc\nid: foo\n"\n'), "abc-id-foo"),
    ("a plain id line inside a quoted title", fm('title: "abc\nid: foo\n"\n'), "abc-id-foo"),
    ("id line inside a flow sequence", fm("title: T\ntags: [a,\nid: foo\n]\n"), "t"),
    ("id line inside a flow mapping", fm("title: T\nref: {a: 1,\nid: foo\n}\n"), "t"),
    ("id line inside a block scalar", fm("title: |\n  id: foo\n"), "id-foo"),
    ("continued plain id", fm("id: foo\n  bar\n"), "foo bar"),
    ("continued after a blank line", fm("id: foo\n\n  bar\n"), "foo\nbar"),
    ("id: true", fm("id: true\n"), "1"),
    ("id: hex int", fm("id: 0x1A\n"), "26"),
    ("id: float", fm("id: 1e3\n"), "1000.0"),
    ("id: underscored int", fm("id: 1_000\n"), "1000"),
    ("id: date", fm("id: 2026-01-01\n"), "2026-01-01"),
    ("id: timestamp", fm("id: 2026-01-01T10:00:00\n"), "2026-01-01 10:00:00"),
    ("id: null, derived", fm("id: null\ntitle: Gamma\n"), "gamma"),
    ("id: ~, derived", fm("id: ~\ntitle: Gamma\n"), "gamma"),
    ("id: false, derived", fm("id: false\ntitle: Gamma\n"), "gamma"),
    ("id empty, derived", fm("id:\ntitle: Gamma\n"), "gamma"),
    ("id: yes is a string in YAML 1.2", fm("id: yes\n"), "yes"),
    ("merge key supplies the id", fm("base: &b {id: zeta}\n<<: *b\ntitle: T\n"), "zeta"),
    ("explicit id beats a merge", fm("base: &b {id: zeta}\nid: alpha\n<<: *b\n"), "alpha"),
    ("flow mapping root", fm("{id: alpha, title: A}\n"), "alpha"),
    ("key spelled with a space", fm("id : alpha\n"), "alpha"),
    ("ID is another key", fm("ID: alpha\ntitle: Beta\n"), "beta"),
    ("no id line, derived", fm("title: Delta Force\n"), "delta-force"),
    ("non-ascii id", fm("id: café\n"), "café"),
    # Unmodellable or absent frontmatter: no id at all.
    ("duplicate id keys", fm("id: alpha\nid: beta\n"), None),
    ("duplicate, quoted key", fm('id: alpha\n"id": beta\n'), None),
    ("tab after the colon", fm("id:\talpha\n"), None),
    ("root is a list", fm("- id: alpha\n"), None),
    ("u2028 inside a value", fm("title: x\u2028id: alpha\n"), None),
    ("no frontmatter", "id: alpha\n\nbody\n", None),
    ("unterminated", "---\nid: alpha\n", None),
    ("malformed opening", "---x\nid: alpha\n---\n", None),
    ("toml", "+++\nid = 'alpha'\n+++\n", None),
]


_NO_ID = pytest.mark.control(
    reason="no file holds an id here; the row pins the corpus to the parser"
)


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [pytest.param(*c, id=c[0], marks=[_NO_ID] if c[2] is None else []) for c in CORPUS],
)
def test_the_prefilter_never_skips_the_file_that_holds_the_id(name, text, expected):
    """The property: whatever id the full parse reads, the prefilter lets the
    file through for it. ``expected`` is written by hand; the first assert
    checks the corpus against the parser, the second is the prefilter's."""
    assert _held(text) == expected
    if expected is not None:
        assert could_hold_id(text, expected), f"{name}: a false negative"


@pytest.mark.parametrize(
    "text",
    [
        fm("id: alpha\ntitle: A\n"),
        fm("type: note\ntitle: Plain one\nid: alpha\n"),
        fm("id: alpha\ntitle: A\n", nl="\r\n"),
        "\ufeff" + fm("id: alpha\n"),
        fm("type: note\nid: alpha # c\ntags:\n- x\n"),
        "no frontmatter at all\n",
        fm("id: alpha\nid: beta\n"),
    ],
)
def test_the_prefilter_skips_a_plain_file_holding_another_id(text):
    """Without this a prefilter that lets every file through would pass the
    property above and save nothing."""
    assert not could_hold_id(text, "omega")


def _corpus_files() -> list[Path]:
    roots = [REPO_ROOT / "kb", REPO_ROOT / "tests" / "fixtures", REPO_ROOT / "extensions"]
    return sorted(p for r in roots if r.is_dir() for p in r.rglob("*.md"))


def test_the_prefilter_agrees_with_the_parse_on_every_real_file():
    """Every markdown file in the repository's own KB, fixtures and extensions:
    the prefilter never skips a file for the id it holds, and does skip nearly
    every file for an id no file holds (2 of kb/'s 952 files have no id line)."""
    files = _corpus_files()
    assert len(files) > 900
    skipped = 0
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        held = _held(text)
        if held is not None:
            assert could_hold_id(text, held), f"false negative on {f}"
        skipped += not could_hold_id(text, "no-such-id-135")
    assert skipped / len(files) > 0.9, (skipped, len(files))


hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

# Lines that are each harmless or awkward; joined at random they make
# frontmatter the hand corpus did not think of.
_LINES = [
    "id: alpha",
    "id: beta",
    "id: alpha # c",
    'id: "alpha"',
    "id: 'al''pha'",
    'id: "\\x61lpha"',
    "id: 0x1A",
    "id: true",
    "id: null",
    "id: &a alpha",
    "id: *a",
    "id:",
    "id : alpha",
    "title: Alpha",
    "title: 'Alpha",
    'title: "Alpha',
    "title: |",
    "title: >-",
    "title: [alpha,",
    "title: {id: alpha,",
    "ref: &a alpha",
    "<<: {id: alpha}",
    "  alpha",
    "  id: alpha",
    "id: alpha'",
    'alpha"',
    "alpha]",
    "alpha}",
    "- alpha",
    "# id: alpha",
    "",
    "   ",
    "...",
    "type: note",
    "tags:",
    "? id",
    ": alpha",
    # The cases the line patterns' comments name: Unicode line breaks, NBSP,
    # tabs inside a line, a directive, a BOM after the first line.
    "title: x\x85id: alpha",
    "title: x\u2028id: alpha",
    "title: x\u2029id: alpha",
    "id: alpha\u00a0",
    "title:\u00a0alpha",
    "title: a\tb",
    "id:\talpha",
    "id: alpha\t# c",
    "%YAML 1.1",
    "\ufeffid: alpha",
]


@settings(max_examples=3000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    lines=st.lists(st.sampled_from(_LINES), min_size=1, max_size=7),
    nl=st.sampled_from(["\n", "\r\n", "\r"]),
    bom=st.booleans(),
)
def test_no_arrangement_of_lines_makes_a_false_negative(lines, nl, bom):
    text = ("\ufeff" if bom else "") + "---\n" + nl.join(lines) + "\n---\n\nbody\n"
    held = _held(text)
    if held is not None:
        assert could_hold_id(text, held), text


# -- the repository: parses counted, not timed ------------------------------


@pytest.fixture
def parses(monkeypatch):
    """How many times the full id parse ran."""
    calls: list[int] = []
    real = core_types.read_entry_id

    def counting(text, migrate=None):
        calls.append(1)
        return real(text, migrate)

    monkeypatch.setattr(repo_mod, "read_entry_id", counting)
    return calls


@pytest.fixture
def walks(monkeypatch):
    """How many times a whole-KB walk (``list_files``) started."""
    calls: list[int] = []
    real = KBRepository.list_files

    def counting(self):
        calls.append(1)
        return real(self)

    monkeypatch.setattr(KBRepository, "list_files", counting)
    return calls


def _kb(tmp_path: Path, n: int, kb_yaml: str | None = None):
    kb_path = tmp_path / f"{KB}-{n}"
    kb_path.mkdir()
    if kb_yaml:
        (kb_path / "kb.yaml").write_text(kb_yaml)
    for i in range(n):
        sub = kb_path / f"d{i % 3}"
        sub.mkdir(exist_ok=True)
        (sub / f"n-{i}.md").write_text(f"---\nid: n-{i}\ntitle: Note {i}\ntype: note\n---\n\nx\n")
    # One file whose id is derived from its title, one renamed by hand.
    (kb_path / "untitled.md").write_text("---\ntitle: Derived Here\ntype: note\n---\n\nx\n")
    (kb_path / "moved.md").write_text("---\nid: hand-moved\ntitle: M\ntype: note\n---\n\nx\n")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=kb_path.parent / f"{n}.db", auto_embed=False),
    )
    db = PyriteDB(kb_path.parent / f"{n}.db")
    return kb_path, KBService(config, db), KBRepository(config.get_kb(KB)), db


@pytest.mark.parametrize("n", [5, 40])
def test_a_miss_parses_only_the_files_the_text_cannot_rule_out(tmp_path, parses, n):
    """Only the file with no ``id:`` line is parsed, whatever the KB's size."""
    kb_path, _svc, repo, db = _kb(tmp_path, n)
    try:
        assert repo.find_file("no-such-id") is None
        assert len(parses) == 1
    finally:
        db.close()


@pytest.mark.control(reason="dev parses every file; guards that the prefilter still finds them")
@pytest.mark.parametrize("n", [5, 40])
def test_a_scan_hit_and_a_derived_id_are_still_found(tmp_path, n):
    kb_path, _svc, repo, db = _kb(tmp_path, n)
    try:
        assert repo.find_file("hand-moved") == kb_path / "moved.md"
        assert repo.find_file("derived-here") == kb_path / "untitled.md"
    finally:
        db.close()


@pytest.mark.parametrize("n", [5, 40])
def test_a_create_walks_the_kb_at_most_once(tmp_path, parses, walks, n):
    """``exists`` is the create's one lookup; ``save_entry`` takes its answer
    instead of walking again (#135: two walks, 2 s each on 2,000 files)."""
    kb_path, svc, _repo, db = _kb(tmp_path, n)
    try:
        svc.create_entry(KB, "brand-new", "Brand new", "note", body="x")
        assert len(walks) <= 1, walks
        assert len(parses) <= 2, parses  # the derived file; nothing grows with n
    finally:
        db.close()


@pytest.mark.parametrize("n", [5, 40])
def test_a_delete_miss_parses_no_more_as_the_kb_grows(tmp_path, parses, n):
    kb_path, svc, _repo, db = _kb(tmp_path, n)
    try:
        assert svc.delete_entry("no-such-id", KB) is False
        assert len(parses) <= 2, parses
    finally:
        db.close()


@pytest.mark.control(reason="dev parses every file; guards the prefiltered delete walk")
def test_delete_of_a_derived_id_still_finds_its_file(tmp_path):
    kb_path, svc, repo, db = _kb(tmp_path, 5)
    try:
        assert repo.files_to_delete("derived-here") == [kb_path / "untitled.md"]
        assert repo.files_to_delete("hand-moved") == [kb_path / "moved.md"]
        assert repo.files_to_delete("no-such-id") == []
    finally:
        db.close()


@pytest.mark.control(reason="dev has no prefilter; guards its migration check")
def test_a_migration_that_sets_the_id_turns_the_prefilter_off(tmp_path, monkeypatch):
    """A migration runs before the id is read and may write ``id`` itself, so
    the raw text says nothing about the id: in a KB with migrations to apply,
    every file is parsed (the prefilter's one blind spot, closed by not using
    it there)."""
    from pyrite.migrations import MigrationRegistry

    reg = MigrationRegistry()

    @reg.register("note", 1, 2)
    def _legacy_to_id(data):
        data["id"] = "mig-" + str(data.pop("legacy"))
        return data

    monkeypatch.setattr(repo_mod, "get_migration_registry", lambda: reg)
    kb_path, _svc, repo, db = _kb(
        tmp_path, 3, kb_yaml=f"name: {KB}\ntypes:\n  note:\n    version: 2\n"
    )
    try:
        f = kb_path / "old.md"
        f.write_text("---\nid: old\ntype: note\n_schema_version: 1\nlegacy: 7\n---\n\nb\n")
        assert repo.find_file("mig-7") == f
        assert repo.files_to_delete("mig-7") == [f]
    finally:
        db.close()


def test_get_and_create_agree_on_a_renamed_unindexed_file(tmp_path):
    """Maintainer, 2026-10-15: ``get`` falls back to the same prefiltered scan
    on a miss, so read and write answer "does a file hold this id" alike. A
    file renamed by hand and not yet synced is found by both."""
    from pyrite.exceptions import EntryExistsError

    kb_path, svc, _repo, db = _kb(tmp_path, 5)
    try:
        got = svc.get_entry("hand-moved", KB, readable_kbs=None)
        assert got is not None and got["title"] == "M"
        with pytest.raises(EntryExistsError):
            svc.create_entry(KB, "hand-moved", "Again", "note", body="x")
    finally:
        db.close()


@pytest.mark.control(reason="dev's get never scans; guards the cost of the scan it gains")
@pytest.mark.parametrize("n", [5, 40])
def test_a_get_miss_parses_no_more_as_the_kb_grows(tmp_path, parses, n):
    kb_path, svc, _repo, db = _kb(tmp_path, n)
    try:
        assert svc.get_entry("no-such-id", KB, readable_kbs=None) is None
        assert len(parses) <= 1, parses
    finally:
        db.close()


@pytest.mark.control(reason="the rule is dev's; guards the string-prefix fast path in _listed")
def test_the_walk_skips_what_it_always_skipped(tmp_path):
    """``_listed``'s rule, by hand: hidden parts and ``_templates`` below the
    KB root are skipped, a hidden directory above it is not, README is skipped
    in any case, and a path outside the root is never listed."""
    kb_path = tmp_path / ".pyrite" / "kbs" / "k"
    for rel in [
        "a.md",
        "deep/x/y.md",
        ".hidden/b.md",
        "sub/.c.md",
        "_templates/t.md",
        "sub/_templates/u.md",
        "README.md",
        "sub/readme.MD",
        "sub/notreadme.md",
    ]:
        (kb_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (kb_path / rel).write_text("---\nid: x\n---\n")
    repo = KBRepository(KBConfig(name=KB, path=kb_path, kb_type="generic"))
    listed = sorted(p.relative_to(kb_path).as_posix() for p in repo.list_files())
    assert listed == ["a.md", "deep/x/y.md", "sub/notreadme.md"]
    assert not repo._listed(tmp_path / "elsewhere.md")
    assert not repo._listed(tmp_path / ".pyrite" / "kbs" / "kk" / "a.md")  # a sibling, same prefix


@pytest.mark.parametrize(
    ("raw", "held"),
    [
        (b"---\r\nid: crlf-id\r\ntitle: C\r\n---\r\n\r\nx\r\n", "crlf-id"),
        (b"\xef\xbb\xbf---\nid: bom-id\n---\n\nx\n", "bom-id"),
        # A lone CR is a line break to read_text (universal newlines) and
        # to YAML: the id is on its own line.
        (b"---\ntitle: x\rid: cr-id\n---\n\nx\n", "cr-id"),
        (b'---\ntitle: "abc\nid: foo"\n---\n\nx\n', "abc-id-foo"),
        (b"---\ntitle: Derived On Disk\n---\n\nx\n", "derived-on-disk"),
    ],
)
def test_a_file_on_disk_is_found_by_the_id_it_holds(tmp_path, raw, held):
    """The scan reads each file as ``read_text`` does (UTF-8, universal
    newlines) before the prefilter and the parse see it."""
    kb_path, _svc, repo, db = _kb(tmp_path, 3)
    try:
        f = kb_path / "sub" / "on-disk.md"
        f.parent.mkdir()
        f.write_bytes(raw)
        assert repo_mod._regular_file_text(f) == f.read_text(encoding="utf-8")
        assert repo.find_file(held) == f
        assert repo.files_to_delete(held) == [f]
        assert repo.find_file("no-such-id") is None
    finally:
        db.close()


@pytest.mark.control(reason="dev passed these over too; guards the one-open reader")
def test_a_fifo_or_undecodable_file_is_passed_over(tmp_path):
    import os

    kb_path, _svc, repo, db = _kb(tmp_path, 3)
    try:
        if hasattr(os, "mkfifo"):
            os.mkfifo(kb_path / "pipe.md")
        (kb_path / "latin1.md").write_bytes(b"---\nid: caf\xe9\n---\n\nx\n")
        assert repo.find_file("no-such-id") is None  # returns: nothing blocked
        assert repo.find_file("hand-moved") == kb_path / "moved.md"
    finally:
        db.close()


@pytest.mark.control(reason="an id-named file was already a filename hit on dev; pins it")
@pytest.mark.parametrize("n", [5, 40])
def test_an_update_of_an_id_named_file_reads_no_other_file(tmp_path, parses, monkeypatch, n):
    reads: list[Path] = []
    real = repo_mod._regular_file_text
    monkeypatch.setattr(repo_mod, "_regular_file_text", lambda p: reads.append(p) or real(p))
    kb_path, svc, _repo, db = _kb(tmp_path, n)
    try:
        svc.update_entry("n-1", KB, body="changed")
        assert reads == [] and len(parses) <= 1, (reads, parses)
    finally:
        db.close()


# -- fix round 1 (conductor review of 6dbdb3d) ------------------------------


def _kbs(tmp_path: Path, count: int, n: int = 5):
    """``count`` KBs of ``n`` files each; the first holds a file renamed by
    hand (``moved.md`` holds ``hand-moved``) and nothing is indexed."""
    kbcs = []
    for k in range(count):
        kb_path = tmp_path / f"kb{k}"
        kb_path.mkdir()
        for i in range(n):
            (kb_path / f"k{k}-{i}.md").write_text(
                f"---\nid: k{k}-{i}\ntitle: T\ntype: note\n---\n\nx\n"
            )
        (kb_path / "untitled.md").write_text(f"---\ntitle: Derived {k}\ntype: note\n---\n\nx\n")
        kbcs.append(KBConfig(name=f"kb{k}", path=kb_path, kb_type="generic"))
    (tmp_path / "kb0" / "moved.md").write_text(
        "---\nid: hand-moved\ntitle: M\ntype: note\n---\n\nx\n"
    )
    config = PyriteConfig(
        knowledge_bases=kbcs, settings=Settings(index_path=tmp_path / "m.db", auto_embed=False)
    )
    db = PyriteDB(tmp_path / "m.db")
    return KBService(config, db), db


def test_a_get_miss_with_a_kb_named_scans_that_kb(tmp_path):
    """Maintainer, 2026-10-20: with ``-k`` the miss scans the one KB named."""
    svc, db = _kbs(tmp_path, 2)
    try:
        got = svc.get_entry("hand-moved", "kb0", readable_kbs=None)
        assert got is not None and got["title"] == "M"
    finally:
        db.close()


@pytest.mark.parametrize("count", [1, 3])
def test_a_get_with_no_kb_named_looks_by_filename_only(tmp_path, parses, count):
    """Maintainer, 2026-10-20: with no KB named, a miss tries the files named
    like the id in every readable KB and scans none of them, so its cost does
    not grow with the number of KBs. A renamed, unindexed file is not found
    that way; ``-k`` finds it."""
    svc, db = _kbs(tmp_path, count)
    try:
        assert svc.get_entry("hand-moved", None, readable_kbs=None) is None
        assert svc.get_entry("no-such-id", None, readable_kbs=None) is None
        assert parses == []
        assert svc.get_entry("k0-1", None, readable_kbs=None)["id"] == "k0-1"  # by name
    finally:
        db.close()


def test_a_before_save_hook_that_renames_into_an_existing_id_is_refused(tmp_path):
    """create's one lookup ran for the id it was given; a ``before_save``
    hook that changes the id is looked up again, so a create never leaves two
    files holding one id (on dev the second lookup deleted the other file)."""
    from pyrite.exceptions import EntryExistsError

    kb_path, svc, repo, db = _kb(tmp_path, 3)
    moved = kb_path / "moved.md"
    before = moved.read_bytes()

    def rename_to_existing(entry, context):
        entry.id = "hand-moved"
        return entry

    svc.hook_runner.register_core_hook("before_save", rename_to_existing)
    try:
        with pytest.raises(EntryExistsError):
            svc.create_entry(KB, "fresh-id", "Fresh", "note", body="x")
        assert moved.read_bytes() == before
        assert [p for p in kb_path.rglob("*.md") if repo.id_of_file(p) == "hand-moved"] == [moved]
    finally:
        db.close()


@pytest.mark.control(reason="a hook that keeps the id costs no second lookup; pins the cheap path")
def test_a_before_save_hook_that_keeps_the_id_costs_no_second_walk(tmp_path, walks):
    kb_path, svc, _repo, db = _kb(tmp_path, 3)
    svc.hook_runner.register_core_hook("before_save", lambda entry, context: entry)
    try:
        svc.create_entry(KB, "fresh-id", "Fresh", "note", body="x")
        assert len(walks) <= 1, walks
    finally:
        db.close()

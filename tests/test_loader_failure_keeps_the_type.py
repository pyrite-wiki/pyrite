"""A file Pyrite cannot load as the type it declares is reported as unreadable;
it never becomes a different type (#1261).

``KBRepository._load_entry`` used to catch whatever a type's
``from_frontmatter`` raised and return ``EventEntry`` of the same file: a
``backlog_item`` whose ``rank`` would not convert was indexed as an ``event``
and dropped out of ``sw backlog`` with nothing reported. The rule now is the
one #777 states for files that cannot be read: the load raises
``EntryLoadError`` (a ``FrontmatterError``), the reconcile lists the file under
``malformed`` with its path and the loader's message, ``index health`` names
it, and no row of any type holds its id.

The failing loader is injected, not found in the tree: the in-tree triggers
(``int()`` on ``rank``, ``adr_number``, ``reputation``) are being fixed one by
one (#1247, #1263), and the rule must hold for the next one too. Each shape is
a real call that raises the way a loader does, run inside the type's own
``from_frontmatter`` for files that carry ``probe: bad``; every other file of
the type loads as before. Types come from the registries, core and plugin, so
a new plugin type is covered without editing this file.
"""

import json
from datetime import date

import pytest

from pyrite.config import KBConfig
from pyrite import exceptions
from pyrite.exceptions import FrontmatterError, ValidationError
from pyrite.models.core_types import ENTRY_TYPE_REGISTRY, read_entry_id
from pyrite.plugins import get_registry
from pyrite.storage.repository import KBRepository


def _all_types() -> dict[str, tuple[str, type]]:
    """Every registered entry type: name -> (origin, class)."""
    types = {name: ("core", cls) for name, cls in ENTRY_TYPE_REGISTRY.items()}
    for name, cls in get_registry().get_all_entry_types().items():
        types.setdefault(name, ("plugin", cls))
    return types


TYPES = _all_types()
PLUGIN_TYPES = sorted(n for n, (origin, _) in TYPES.items() if origin == "plugin")
CORE_TYPES = sorted(n for n, (origin, _) in TYPES.items() if origin == "core")


def _int(cls):
    int("notanumber")


def _overflow(cls):
    int(float("inf"))  # `rank: .inf`


def _float(cls):
    float("notanumber")


def _date(cls):
    date.fromisoformat("not-a-date")


def _missing_required(cls):
    {}["required_field"]  # noqa: B018 -- `meta["x"]` on a file without `x:`


def _validator(cls):
    raise ValidationError("status must be one of: open, closed")


def _constructor(cls):
    cls(no_such_field=1)  # a dataclass constructor refusing a keyword


SHAPES = {
    "int": (_int, "invalid literal for int()"),
    "overflow": (_overflow, "cannot convert float infinity to integer"),
    "float": (_float, "could not convert string to float"),
    "date": (_date, "Invalid isoformat string"),
    "missing-required": (_missing_required, "required_field"),
    "validator": (_validator, "status must be one of"),
    "constructor": (_constructor, "no_such_field"),
}


def _inject(monkeypatch, entry_type: str, shape: str) -> None:
    """Make ``entry_type``'s loader raise ``shape`` for files with ``probe: bad``."""
    cls = TYPES[entry_type][1]
    original = cls.from_frontmatter.__func__
    raiser = SHAPES[shape][0]

    def from_frontmatter(klass, meta, body=""):
        if meta.get("probe") == "bad":
            raiser(klass)
        return original(klass, meta, body)

    monkeypatch.setattr(cls, "from_frontmatter", classmethod(from_frontmatter))


def _text(entry_type: str, entry_id: str, *, bad: bool) -> str:
    probe = "probe: bad\n" if bad else ""
    return f"---\ntype: {entry_type}\nid: {entry_id}\ntitle: T {entry_id}\n{probe}---\n\nBody\n"


@pytest.fixture
def repo(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    return KBRepository(KBConfig(name="t", path=kb, kb_type="generic"))


# -- the seam: _load_entry ---------------------------------------------------


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("entry_type", sorted(TYPES))
def test_a_loader_that_raises_is_reported_never_retyped(repo, monkeypatch, entry_type, shape):
    _inject(monkeypatch, entry_type, shape)
    path = repo.path / "x.md"
    path.write_text(_text(entry_type, "x", bad=True), encoding="utf-8")

    with pytest.raises(FrontmatterError) as caught:
        repo._load_entry(path)

    err = caught.value
    # Looked up here, not imported: on a tree without the class the test
    # must fail on the behaviour above, not at collection.
    assert isinstance(err, exceptions.EntryLoadError)
    assert err.entry_type == entry_type
    assert err.path == path
    assert SHAPES[shape][1] in str(err)
    assert err.__cause__ is not None


@pytest.mark.control(reason="the injected loader leaves files without `probe: bad` alone")
@pytest.mark.parametrize("entry_type", sorted(TYPES))
def test_a_valid_file_of_each_type_still_loads_as_its_type(repo, monkeypatch, entry_type):
    _inject(monkeypatch, entry_type, "int")
    path = repo.path / "x.md"
    path.write_text(_text(entry_type, "x", bad=False), encoding="utf-8")

    assert repo._load_entry(path).entry_type == entry_type


@pytest.mark.parametrize(
    "text",
    ["---\n---\n\nBody\n", "---\n{}\n---\n\nBody\n", "---\nnull\n---\n\nBody\n"],
    ids=["empty", "empty-mapping", "null"],
)
def test_frontmatter_with_nothing_in_it_loads_as_a_note_like_any_untyped_file(repo, text):
    # An empty mapping is a note, exactly as `foo: bar` is, and with the id
    # `read_entry_id` reports for it. Hand-kept KBs depend on it (design
    # principle 5; ADR-0038, ADR-0042); the shared derived id (#639) is the
    # path-addressed id's to fix (#730 P7a), not a reason to drop the file.
    path = repo.path / "empty.md"
    path.write_text(text, encoding="utf-8")

    entry = repo._load_entry(path)

    assert entry.entry_type == "note"
    assert entry.id == read_entry_id(text) == "entry-da39a3ee"


# -- the reconcile and its typed rows ----------------------------------------


def _reconcile(tmp_path, kb_type: str, files: dict[str, str]):
    from pyrite.config import PyriteConfig, Settings
    from pyrite.storage.database import PyriteDB
    from pyrite.storage.index import IndexManager

    kb_path = tmp_path / "kb"
    kb_path.mkdir(exist_ok=True)
    for name, text in files.items():
        (kb_path / name).write_text(text, encoding="utf-8")
    kb = KBConfig(name="t", path=kb_path, kb_type=kb_type)
    db = PyriteDB(tmp_path / "index.db")
    config = PyriteConfig(knowledge_bases=[kb], settings=Settings(index_path=tmp_path / "index.db"))
    return db, IndexManager(db, config), kb


def _assert_reported_and_absent(db, result, path, entry_type, shape):
    rows = [m for m in result["malformed"] if m["path"] == str(path)]
    assert len(rows) == 1, result["malformed"]
    assert SHAPES[shape][1] in rows[0]["error"]
    indexed = {(r["id"], r["entry_type"]) for r in db.list_entries(kb_name="t", limit=1000)}
    assert ("bad", "event") not in indexed
    assert {i for i, _ in indexed} == {"good"}, indexed
    assert ("good", entry_type) in indexed


# `event` itself: the old fallback re-ran the same (raising) loader, so the
# file was already reported; it is the one type the fallback could not retype.
_EVENT_CONTROL = pytest.mark.control(reason="the event fallback re-raised the event loader's error")


@pytest.mark.parametrize(
    "entry_type",
    [pytest.param(t, marks=_EVENT_CONTROL if t == "event" else ()) for t in sorted(TYPES)],
)
def test_index_sync_lists_the_file_as_malformed_for_every_type(tmp_path, monkeypatch, entry_type):
    _inject(monkeypatch, entry_type, "int")
    files = {
        "good.md": _text(entry_type, "good", bad=False),
        "bad.md": _text(entry_type, "bad", bad=True),
    }
    db, mgr, kb = _reconcile(tmp_path, "generic", files)
    try:
        result = mgr.sync_incremental("t")
        _assert_reported_and_absent(db, result, kb.path / "bad.md", entry_type, "int")
    finally:
        db.close()


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("entry_type", ["note", "backlog_item"])
def test_index_sync_lists_the_file_as_malformed_for_every_shape(
    tmp_path, monkeypatch, entry_type, shape
):
    _inject(monkeypatch, entry_type, shape)
    files = {
        "good.md": _text(entry_type, "good", bad=False),
        "bad.md": _text(entry_type, "bad", bad=True),
    }
    db, mgr, kb = _reconcile(tmp_path, "generic", files)
    try:
        result = mgr.sync_incremental("t")
        _assert_reported_and_absent(db, result, kb.path / "bad.md", entry_type, shape)
    finally:
        db.close()


def test_an_indexed_entry_that_stops_loading_is_retired_not_retyped(tmp_path, monkeypatch):
    # Regime: the row exists as a backlog_item, then an edit makes the file
    # unloadable. The next sync must drop the row and report the file, not
    # rewrite the row as an event.
    _inject(monkeypatch, "backlog_item", "overflow")
    files = {
        "good.md": _text("backlog_item", "good", bad=False),
        "bad.md": _text("backlog_item", "bad", bad=False),
    }
    db, mgr, kb = _reconcile(tmp_path, "generic", files)
    try:
        mgr.sync_incremental("t")
        assert db.get_entry("bad", "t")["entry_type"] == "backlog_item"

        (kb.path / "bad.md").write_text(_text("backlog_item", "bad", bad=True), encoding="utf-8")
        result = mgr.sync_incremental("t")

        assert db.get_entry("bad", "t") is None
        _assert_reported_and_absent(db, result, kb.path / "bad.md", "backlog_item", "overflow")
    finally:
        db.close()


# -- through the CLI: sync, health and the typed view --------------------------


def test_the_cli_reports_the_file_and_the_typed_view_does_not_show_an_event(
    cli_world, monkeypatch, tmp_path
):
    world = cli_world(kbs=[KBConfig(name="sw", path=tmp_path / "sw", kb_type="software")])
    folder = world.kb_path("sw")
    (folder / "good.md").write_text(_text("backlog_item", "good", bad=False), encoding="utf-8")
    bad = folder / "bad.md"
    bad.write_text(_text("backlog_item", "bad", bad=True), encoding="utf-8")
    _inject(monkeypatch, "backlog_item", "overflow")

    sync = world.cli("index", "sync")
    assert sync.exit_code == 0, sync.output
    assert "Malformed: 1 file(s) skipped" in sync.output
    # The path is asserted on `index health --format json` below: Rich wraps a
    # long path in the human output, so a substring check on it fails on CI.

    health = world.cli("index", "health", "--format", "json")
    report = json.loads(health.stdout)
    rows = [r for r in report["checks"]["malformed_frontmatter"] if r["path"] == str(bad)]
    assert len(rows) == 1, report["checks"]["malformed_frontmatter"]
    assert "cannot convert float infinity to integer" in rows[0]["error"]

    backlog = world.cli("sw", "backlog", "-k", "sw", "--limit", "0")
    assert backlog.exit_code == 0, backlog.output
    listed = json.loads(backlog.stdout)
    items = listed["items"] if isinstance(listed, dict) else listed
    ids = {item["id"] for item in items}
    assert ids == {"good"}, listed
    assert world.db.get_entry("bad", "sw") is None


def test_an_update_whose_result_would_not_load_is_refused_not_written(
    cli_world, monkeypatch, tmp_path
):
    # The update re-loads the bytes it is about to publish. Under the event
    # fallback that load "succeeded" as an event, so the write went through
    # and the next sync retyped the entry. Now it is refused and the file is
    # left as it was.
    world = cli_world(kbs=[KBConfig(name="sw", path=tmp_path / "sw", kb_type="software")])
    path = world.kb_path("sw") / "good.md"
    path.write_text(_text("backlog_item", "good", bad=False), encoding="utf-8")
    world.index("sw")
    before = path.read_bytes()
    _inject(monkeypatch, "backlog_item", "validator")

    result = world.cli("update", "good", "-k", "sw", "-f", "probe=bad")

    assert result.exit_code != 0, result.output
    assert "status must be one of" in result.output
    assert path.read_bytes() == before
    assert world.db.get_entry("good", "sw")["entry_type"] == "backlog_item"


# -- what dev could load, the loader still loads; what it cannot, it reads -----
#
# The property (ADR-0042 8a): loading never loses an entry dev could load and
# never retypes one; a file its type cannot load is read as that type and
# written not at all. Six frontmatter shapes, each through the entry points a
# user meets: the reconcile and search, `get`, and `update`.

_BODY = "Zebra marker body.\n"

#: shape -> (file text, id, loads). `loads`: the loader gives a typed entry.
FILES = {
    "empty": (f"---\n---\n\n{_BODY}", "entry-da39a3ee", True),
    "foo-bar": (f"---\nfoo: bar\n---\n\n{_BODY}", "entry-da39a3ee", True),
    "tags": (f"---\ntags: [a]\n---\n\n{_BODY}", "entry-da39a3ee", True),
    "bad-int": (
        f"---\ntype: backlog_item\nid: bad\ntitle: Bad\nrank: abc\n---\n\n{_BODY}",
        "bad",
        False,
    ),
    "inf": (
        f"---\ntype: backlog_item\nid: bad\ntitle: Bad\nrank: .inf\n---\n\n{_BODY}",
        "bad",
        False,
    ),
    # No loader in the tree raises on a bad date (dates are read leniently), so
    # this one loads on dev and on the head alike: a control for the shape.
    "bad-date": (
        f"---\ntype: note\nid: bad\ntitle: Bad\ncreated_at: not-a-date\n---\n\n{_BODY}",
        "bad",
        True,
    ),
}
UNLOADABLE = [n for n, (_, _, loads) in FILES.items() if not loads]
UNDECLARED = ["empty", "foo-bar", "tags"]  # no `type:`: a note, the id from the empty title


def _params(names, green_on_the_old_head):
    """``names`` as parameters; the shapes the round-0 head already handled are
    controls (they guard the property but cannot be red against that head)."""
    control = pytest.mark.control(
        reason="the round-0 head already handled this shape; it guards the property"
    )
    return [pytest.param(n, marks=control if n in green_on_the_old_head else ()) for n in names]


FILE_PARAMS_RECONCILE = _params(FILES, {"foo-bar", "tags", "bad-date", "bad-int", "inf"})
FILE_PARAMS_GET = _params(FILES, {"foo-bar", "tags", "bad-date"})
UNDECLARED_PARAMS = _params(UNDECLARED, {"foo-bar", "tags", "empty"})


def _sw_world(cli_world, tmp_path, name):
    world = cli_world(kbs=[KBConfig(name="sw", path=tmp_path / "sw", kb_type="software")])
    path = world.kb_path("sw") / "f.md"
    path.write_text(FILES[name][0], encoding="utf-8")
    return world, path


@pytest.mark.parametrize("name", FILE_PARAMS_RECONCILE)
def test_the_reconcile_indexes_what_loads_and_reports_what_does_not(cli_world, tmp_path, name):
    world, path = _sw_world(cli_world, tmp_path, name)
    _, entry_id, loads = FILES[name]

    sync = world.cli("index", "sync")
    assert sync.exit_code == 0, sync.output

    row = world.db.get_entry(entry_id, "sw")
    health = json.loads(world.cli("index", "health", "--format", "json").stdout)
    malformed = [r for r in health["checks"]["malformed_frontmatter"] if r["path"] == str(path)]
    if loads:
        assert row is not None, "an entry dev could load is missing from the index"
        assert row["entry_type"] == "note"
        assert malformed == []
        found = world.db.search("Zebra", kb_name="sw")
        assert [r["id"] for r in found] == [entry_id], found
    else:
        assert row is None
        assert len(malformed) == 1, health["checks"]["malformed_frontmatter"]
        assert world.db.list_entries(kb_name="sw") == []


@pytest.mark.parametrize("name", FILE_PARAMS_GET)
def test_get_returns_the_file_never_not_found(cli_world, tmp_path, name):
    world, _ = _sw_world(cli_world, tmp_path, name)
    _, entry_id, loads = FILES[name]

    got = world.cli("get", entry_id, "-k", "sw", "--format", "json")

    assert got.exit_code == 0, got.output
    data = json.loads(got.stdout)
    assert data["id"] == entry_id
    assert "Zebra marker" in (data.get("body") or "")
    if loads:
        assert data["entry_type"] == "note"
        assert not data.get("load_problems")
    else:
        # The declared type, with the loader's own message; never an event.
        assert data["entry_type"] == "backlog_item"
        problems = data["load_problems"]
        assert len(problems) == 1, problems
        assert problems[0]["field"] == "rank"
        assert problems[0]["message"]


@pytest.mark.parametrize("name", UNLOADABLE)
@pytest.mark.parametrize("field", ["rank=3", "title=Renamed"])
def test_an_update_is_refused_whether_or_not_it_names_the_bad_field(
    cli_world, tmp_path, name, field
):
    # ADR-0042 8a: a file its type cannot load is written not at all; there is
    # no ordinary update that repairs it (rounds 1-2 let one through, and
    # warned on any other). Repair is by hand. The full matrix of writes is
    # tests/test_unloadable_file_is_never_written.py.
    world, path = _sw_world(cli_world, tmp_path, name)
    before = path.read_bytes()

    result = world.cli("update", "bad", "-k", "sw", "-f", field)

    assert result.exit_code == 1, result.output
    assert "ENTRY_UNREADABLE" in result.output
    assert path.read_bytes() == before


@pytest.mark.parametrize("name", UNDECLARED_PARAMS)
def test_an_update_of_a_file_that_declares_nothing_succeeds(cli_world, tmp_path, name):
    world, path = _sw_world(cli_world, tmp_path, name)

    result = world.cli("update", FILES[name][1], "-k", "sw", "-f", "title=Named")

    assert result.exit_code == 0, result.output
    assert "title: Named" in path.read_text(encoding="utf-8")


# -- the other callers of the loader -------------------------------------------


def _two_files(cli_world, tmp_path):
    world = cli_world(kbs=[KBConfig(name="sw", path=tmp_path / "sw", kb_type="software")])
    root = world.kb_path("sw")
    (root / "bad.md").write_text(FILES["inf"][0], encoding="utf-8")
    (root / "ok.md").write_text(_text("backlog_item", "ok", bad=False), encoding="utf-8")
    world.index("sw")
    return world, root


@pytest.mark.control(reason="dev linked to this target (as an event); red only against round 0")
def test_a_link_to_a_file_that_does_not_load_is_not_a_link_to_nothing(cli_world, tmp_path):
    world, root = _two_files(cli_world, tmp_path)

    result = world.cli("link", "ok", "bad", "-k", "sw")

    assert result.exit_code == 0, result.output
    assert "bad" in (root / "ok.md").read_text(encoding="utf-8")


def test_a_link_from_a_file_that_does_not_load_says_so_not_not_found(cli_world, tmp_path):
    world, root = _two_files(cli_world, tmp_path)
    before = (root / "bad.md").read_bytes()

    result = world.cli("link", "bad", "ok", "-k", "sw")

    assert result.exit_code == 1
    assert "not found" not in result.output.lower()
    assert "ENTRY_UNREADABLE" in result.output
    assert "bad.md does not load" in result.output.replace("\n", " ")
    assert (root / "bad.md").read_bytes() == before


def test_export_site_includes_an_empty_frontmatter_note_and_names_what_it_cannot_load(
    cli_world, tmp_path
):
    world, root = _two_files(cli_world, tmp_path)
    (root / "empty.md").write_text(FILES["empty"][0], encoding="utf-8")
    world.index("sw")
    # `ok` was indexed; an edit since the sync makes its `rank` unloadable.
    (root / "ok.md").write_text(FILES["inf"][0].replace("id: bad", "id: ok"), encoding="utf-8")
    out = tmp_path / "site"

    result = world.cli("export", "site", "-k", "sw", "-o", str(out))

    assert result.exit_code == 0, result.output
    assert "Exported 1 entries" in result.output
    said = result.output.replace("\n", " ")
    assert "could not be loaded" in said and "exported: ok" in said

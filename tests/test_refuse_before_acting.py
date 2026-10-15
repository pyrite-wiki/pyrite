"""A malformed request is refused before anything is written (#846).

The user's model: "If the command exits 0, the thing I named exists and what I
asked for happened. If I typed something malformed, I am told before anything
changes."

The rule lives at the services every surface reaches (``kb/design.md``,
principle 8), so each test enters through a surface -- the CLI's Typer app, the
MCP server's ``_dispatch_tool``, the REST app -- and the service behind it runs
for real:

- a link the caller is creating is checked by ``kb_service.check_link_spec``,
  which ``KBService.add_link`` and ``KBService.add_links`` call before they
  load or append anything (and ``pyrite create --link`` before it creates);
  a link that arrives in a file or frontmatter (``add``, a create's
  ``links:``) gets only ``check_link_target``: a target the index cannot
  store is refused, nothing else (fix round 1);
- a KB a link read names is looked up at the top of each
  ``LinkDiscoveryService`` read and ``WikilinkService.check_links``;
- an input path is read through one CLI helper,
  ``pyrite.cli.context.read_input_file``.

A file already on disk with a malformed link is not repaired here (principle
2): it is reported, by ``pyrite index sync`` as a malformed file.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import NoteEntry
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

REFUSED = 1
PARTIAL = 3


@pytest.fixture
def env(tmp_path):
    """Two KBs with entries, and a config the CLI loads in this process."""
    kbs = []
    for name in ("rs", "other"):
        path = tmp_path / name
        path.mkdir()
        kbs.append(KBConfig(name=name, path=path, kb_type=KBType.GENERIC))
    config = PyriteConfig(knowledge_bases=kbs, settings=Settings(index_path=tmp_path / "index.db"))
    for kb, ids in ((kbs[0], ("fresh", "hello", "third")), (kbs[1], ("elsewhere",))):
        repo = KBRepository(kb)
        for eid in ids:
            repo.save(NoteEntry(id=eid, title=eid.title(), body=f"Body of {eid}.", tags=["t"]))
    db = PyriteDB(tmp_path / "index.db")
    IndexManager(db, config).index_all()
    db.close()
    with (
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        yield {"config": config, "tmp": tmp_path, "rs": kbs[0].path, "other": kbs[1].path}


def _file(env, kb: str, entry_id: str) -> Path:
    found = list(env[kb].rglob(f"{entry_id}.md"))
    assert len(found) == 1, found
    return found[0]


@contextmanager
def _unchanged(*paths: Path):
    """Assert every path's bytes, and the set of files beside it, are unchanged."""
    before = {p: p.read_bytes() for p in paths}
    dirs = {p.parent for p in paths}
    listing = {d: sorted(x.name for x in d.iterdir()) for d in dirs}
    yield
    for p, b in before.items():
        assert p.read_bytes() == b, f"{p} changed"
    for d, names in listing.items():
        assert sorted(x.name for x in d.iterdir()) == names, f"files added in {d}"


def cli(*args: str, input: str | None = None):
    from pyrite.cli import app

    return CliRunner().invoke(app, [*args, "--format", "json"], input=input)


def _json(result) -> dict:
    return json.loads(result.stdout)


# --------------------------------------------------------------------------
# (2) a malformed link spec writes nothing
# --------------------------------------------------------------------------


class TestBulkCreateMalformedSpec:
    @pytest.mark.parametrize("target", [None, "", "   "], ids=["null", "empty", "blank"])
    def test_bad_target_leaves_the_source_file_byte_identical(self, env, target):
        source = _file(env, "rs", "fresh")
        spec = json.dumps([{"source": "fresh", "target": target}])
        with _unchanged(source):
            out = cli("links", "bulk-create", "-", "-k", "rs", input=spec)
        assert out.exit_code == REFUSED, out.output
        item = _json(out)["results"][0]
        assert item["status"] == "failed"
        assert item["error_code"] == "VALIDATION_FAILED"
        assert "SQL" not in item["error"]

    def test_one_bad_spec_among_good_ones_is_partial_and_the_others_are_written(self, env):
        source = _file(env, "rs", "fresh")
        spec = json.dumps(
            [
                {"source": "fresh", "target": "hello"},
                {"source": "fresh", "target": None},
                {"source": "fresh", "target": "third", "relation": ""},
            ]
        )
        out = cli("links", "bulk-create", "-", "-k", "rs", input=spec)
        assert out.exit_code == PARTIAL, out.output
        statuses = [(r["status"], r.get("error_code")) for r in _json(out)["results"]]
        assert statuses == [
            ("created", None),
            ("failed", "VALIDATION_FAILED"),
            ("failed", "VALIDATION_FAILED"),
        ]
        text = source.read_text()
        assert "target: hello" in text
        assert "third" not in text
        assert "target:\n" not in text and "target: null" not in text

    def test_dry_run_reports_a_bad_spec_and_writes_nothing(self, env):
        source = _file(env, "rs", "fresh")
        spec = json.dumps([{"source": "fresh", "target": "hello"}, {"source": "fresh"}])
        with _unchanged(source):
            out = cli("links", "bulk-create", "-", "-k", "rs", "--dry-run", input=spec)
        assert out.exit_code == PARTIAL, out.output
        results = _json(out)["results"]
        assert [r["status"] for r in results] == ["created", "failed"]
        assert results[1]["error_code"] == "VALIDATION_FAILED"

    def test_a_target_kb_that_does_not_exist_fails_that_item_only(self, env):
        source = _file(env, "rs", "fresh")
        spec = json.dumps([{"source": "fresh", "target": "hello", "target_kb": "nokb"}])
        with _unchanged(source):
            out = cli("links", "bulk-create", "-", "-k", "rs", input=spec)
        assert out.exit_code == REFUSED, out.output
        item = _json(out)["results"][0]
        assert (item["status"], item["error_code"]) == ("failed", "KB_NOT_FOUND")
        assert "nokb" in item["error"]

    def test_a_spec_that_is_not_a_mapping_fails_that_item(self, env):
        source = _file(env, "rs", "fresh")
        spec = json.dumps(["fresh", {"source": "fresh", "target": "hello"}])
        out = cli("links", "bulk-create", "-", "-k", "rs", input=spec)
        assert out.exit_code == PARTIAL, out.output
        results = _json(out)["results"]
        assert (results[0]["status"], results[0]["error_code"]) == ("failed", "VALIDATION_FAILED")
        assert results[1]["status"] == "created"
        assert "target: hello" in source.read_text()

    @pytest.mark.parametrize(
        "note", [["a", "list"], {"a": 1}, 3], ids=["list", "mapping", "number"]
    )
    def test_a_note_that_is_not_text_is_refused_before_the_write(self, env, note):
        source = _file(env, "rs", "fresh")
        spec = json.dumps([{"source": "fresh", "target": "hello", "note": note}])
        with _unchanged(source):
            out = cli("links", "bulk-create", "-", "-k", "rs", input=spec)
        assert out.exit_code == REFUSED, out.output
        assert _json(out)["results"][0]["error_code"] == "VALIDATION_FAILED"


class TestCreateLinkSpec:
    @pytest.mark.parametrize(
        "spec",
        ["hello:", "hello:b:c", ":related_to", " : "],
        ids=["no-rel", "3-part", "no-target", "blank"],
    )
    def test_a_malformed_link_creates_nothing(self, env, spec):
        notes = env["rs"] / "notes"
        with _unchanged(*notes.iterdir()):
            out = cli("create", "-k", "rs", "-t", "note", "--title", "Brand New", "--link", spec)
        assert out.exit_code == REFUSED, out.output
        doc = _json(out)
        assert doc["error_code"] == "VALIDATION_FAILED"
        assert not list(env["rs"].rglob("brand-new.md"))

    def test_the_three_part_form_says_what_to_write_instead(self, env):
        out = cli("create", "-k", "rs", "-t", "note", "--title", "X", "--link", "hello:b:c")
        doc = _json(out)
        assert "target:relation" in doc["error"]
        assert "--target-kb" in doc["error"] + doc.get("suggestion", "")

    @pytest.mark.control(reason="control: a well-formed --link still creates and links")
    def test_a_good_link_still_creates_and_links(self, env):
        out = cli(
            "create", "-k", "rs", "-t", "note", "--title", "Brand New", "--link", "hello:informs"
        )
        assert out.exit_code == 0, out.output
        assert "relation: informs" in _file(env, "rs", "brand-new").read_text()


class TestNewEntryCarryingALink:
    """A create or add whose own `links:` holds a malformed link.

    Not named in #846: found answering its open question ("does any other
    input still reach file written, index failed?"). Both wrote the file,
    then the index insert raised and the command answered NO_OUTCOME.
    """

    def test_create_with_a_links_field_holding_a_null_target(self, env):
        notes = env["rs"] / "notes"
        with _unchanged(*notes.iterdir()):
            out = cli(
                "create", "-k", "rs", "-t", "note", "--title", "Two",
                "-f", 'links=[{"target": null}]',
            )  # fmt: skip
        assert out.exit_code == REFUSED, out.output
        assert _json(out)["error_code"] == "VALIDATION_FAILED"
        assert not list(env["rs"].rglob("two.md"))

    def test_add_of_a_file_whose_link_has_no_target(self, env):
        src = env["tmp"] / "three.md"
        src.write_text("---\ntype: note\ntitle: Three\nlinks:\n- target:\n---\nbody\n")
        notes = env["rs"] / "notes"
        with _unchanged(*notes.iterdir()):
            out = cli("add", str(src), "-k", "rs")
        assert out.exit_code == REFUSED, out.output
        assert _json(out)["error_code"] == "VALIDATION_FAILED"
        assert not list(env["rs"].rglob("three.md"))

    @pytest.mark.control(
        reason="control: pins the docstring claim; sync already reported it on dev"
    )
    def test_index_sync_reports_a_null_target_link_on_disk_as_malformed(self, env):
        """The module docstring's claim: reported, not repaired, not a crash."""
        path = _file(env, "rs", "hello")
        path.write_text(path.read_text().replace("tags:", "links:\n- target:\ntags:"))
        with _unchanged(path):
            out = CliRunner().invoke(__import__("pyrite.cli", fromlist=["app"]).app,
                                     ["index", "sync"])  # fmt: skip
        assert out.exit_code == 0, out.output
        assert "Malformed: 1 file(s) skipped" in out.output
        assert "hello.md" in out.output.replace("\n", "")

    @pytest.mark.control(
        reason="control: an update of a file already holding a bad link is not refused for it"
    )
    def test_update_of_a_hand_file_with_an_empty_relation_still_works(self, env):
        """Principle 2: a link already on disk is not the update's to judge."""
        path = _file(env, "rs", "hello")
        path.write_text(
            path.read_text().replace("tags:", "links:\n- target: fresh\n  relation: ''\ntags:")
        )
        out = cli("update", "hello", "-k", "rs", "--title", "Hi")
        assert out.exit_code == 0, out.output


#: A file's own `links:` item, as a hand would write it, and whether the index
#: insert can store it. `add` refuses exactly the shapes `index sync` reports as
#: malformed (fix round 2): what is refused is what the insert cannot store --
#: a null target or relation (NOT NULL columns), and a list or mapping in a
#: field that is part of the row's key or bound as a value. Everything else is
#: accepted as the file spells it.
INGEST_SHAPES = [
    # refused: the index insert fails
    ("null-target", "- target:\n", False),
    ("list-target", "- target: [a, b]\n", False),
    ("map-target", "- target: {a: 1}\n", False),
    ("null-relation", "- target: fresh\n  relation:\n", False),
    ("list-relation", "- target: fresh\n  relation: [x]\n", False),
    ("map-relation", "- target: fresh\n  relation: {a: 1}\n", False),
    ("list-note", "- target: fresh\n  note: [x]\n", False),
    ("map-note", "- target: fresh\n  note: {a: 1}\n", False),
    ("list-kb", "- target: fresh\n  kb: [x]\n", False),
    ("map-kb", "- target: fresh\n  kb: {a: 1}\n", False),
    # accepted: the index stores them
    ("int-target", "- target: 2024\n  relation: related_to\n", True),
    ("float-target", "- target: 3.5\n", True),
    ("bool-target", "- target: true\n", True),
    ("date-target", "- target: 2024-01-01\n", True),
    ("blank-target", "- target: ''\n", True),
    ("no-target-key", "- relation: informs\n", True),
    ("blank-relation", "- target: fresh\n  relation: ''\n", True),
    ("int-relation", "- target: fresh\n  relation: 5\n", True),
    ("int-note", "- target: fresh\n  note: 5\n", True),
    ("null-note", "- target: fresh\n  note:\n", True),
    ("int-kb", "- target: fresh\n  kb: 5\n", True),
    ("null-kb", "- target: fresh\n  kb:\n", True),
    ("bare-string", "- fresh\n", True),
]


class TestIngestMatchesIndexSync:
    """The same file gets the same answer from `add` and `index sync`."""

    @staticmethod
    def _text(links: str) -> str:
        return f"---\nid: four\ntype: note\ntitle: Four\nlinks:\n{links}---\nbody\n"

    @pytest.mark.parametrize(
        ("links", "storable"),
        [
            pytest.param(
                links,
                ok,
                id=name,
                # dev added every storable shape; these guard rounds 0-1 of
                # this branch, which refused some of them.
                marks=[pytest.mark.control(reason="control: dev added this storable shape")]
                if ok
                else [],
            )
            for name, links, ok in INGEST_SHAPES
        ],
    )
    def test_add_refuses_exactly_what_the_index_cannot_store(self, env, links, storable):
        src = env["tmp"] / "four.md"
        src.write_text(self._text(links))
        notes = env["rs"] / "notes"
        before = sorted(p.name for p in notes.iterdir())
        out = cli("add", str(src), "-k", "rs")
        if storable:
            assert out.exit_code == 0, out.output
            # Not rewritten in Pyrite's spelling.
            assert links.splitlines()[0].strip(" -") in _file(env, "rs", "four").read_text()
        else:
            assert out.exit_code == REFUSED, out.output
            assert _json(out)["error_code"] == "VALIDATION_FAILED"
            assert sorted(p.name for p in notes.iterdir()) == before

    @pytest.mark.control(reason="control: index sync already reported these on dev")
    @pytest.mark.parametrize(
        ("links", "storable"),
        [pytest.param(links, ok, id=name) for name, links, ok in INGEST_SHAPES],
    )
    def test_index_sync_gives_the_same_answer(self, env, links, storable):
        from pyrite.cli import app

        (env["other"] / "four.md").write_text(self._text(links))
        out = CliRunner().invoke(app, ["index", "sync"])
        assert out.exit_code == 0, out.output
        assert ("Malformed: 1 file(s) skipped" in out.output) is (not storable), out.output


class TestReplayAgainstAHandIntTarget:
    """Replaying a link the file already holds is a no-op, whatever type the
    target arrives as (fix round 2): the duplicate key compares targets as
    text, so a hand file's `target: 2024` and a typed "2024" are one link."""

    @pytest.fixture
    def hand(self, env):
        from pyrite.cli import app

        path = _file(env, "rs", "fresh")
        path.write_text(
            path.read_text().replace(
                "tags:", "links:\n- target: 2024\n  relation: related_to\ntags:"
            )
        )
        assert CliRunner().invoke(app, ["index", "sync"]).exit_code == 0
        return path

    @staticmethod
    def _rows(env) -> int:
        import sqlite3

        con = sqlite3.connect(env["config"].settings.index_path)
        try:
            return con.execute(
                "select count(*) from link where source_id='fresh' and source_kb='rs'"
            ).fetchone()[0]
        finally:
            con.close()

    def test_bulk_create(self, env, hand):
        assert self._rows(env) == 1
        with _unchanged(hand):
            out = cli("links", "bulk-create", "-", "-k", "rs",
                      input=json.dumps([{"source": "fresh", "target": "2024"}]))  # fmt: skip
        assert out.exit_code == 0, out.output
        assert _json(out)["results"][0]["status"] == "skipped"
        assert self._rows(env) == 1

    def test_cli_link(self, env, hand):
        with _unchanged(hand):
            out = cli("link", "fresh", "2024", "-k", "rs")
        assert out.exit_code == 0, out.output
        assert _json(out)["links"][0]["created"] is False
        assert self._rows(env) == 1

    def test_mcp_kb_link_replay_is_a_no_op(self, env, hand, mcp):
        with _unchanged(hand):
            out = mcp("kb_link", {"source_id": "fresh", "source_kb": "rs", "target_id": "2024"})
        assert out.get("created") is False, out
        # The link asked for, as the schema types it (fix round 3).
        assert out["target_id"] == "2024", out
        assert self._rows(env) == 1


class TestMCPKbLinkReplyMatchesItsSchema:
    """`kb_link`'s reply types its fields as `tool_schemas.py` types the
    arguments they echo: `target_id` and `relation` are strings, on create and
    on replay, whatever YAML made of a hand file's target (fix round 3). Round
    2 echoed the stored value, so a replay against `target: 2024` answered an
    int, `true` a bool and `2024-01-01` a `datetime.date`."""

    @pytest.mark.parametrize(
        ("on_disk", "typed"),
        [("2024", "2024"), ("true", "True"), ("2024-01-01", "2024-01-01")],
        ids=["int", "bool", "date"],
    )
    def test_target_id_and_relation_are_strings(self, env, mcp, on_disk, typed):
        from pyrite.cli import app

        path = _file(env, "rs", "fresh")
        path.write_text(
            path.read_text().replace(
                "tags:", f"links:\n- target: {on_disk}\n  relation: related_to\ntags:"
            )
        )
        assert CliRunner().invoke(app, ["index", "sync"]).exit_code == 0
        args = {"source_kb": "rs", "target_id": typed, "allow_dangling": True}

        created = mcp("kb_link", {**args, "source_id": "hello"})
        assert created.get("created") is True, created
        with _unchanged(path):
            replay = mcp("kb_link", {**args, "source_id": "fresh"})
        assert replay.get("created") is False, replay

        for out in (created, replay):
            assert out["target_id"] == typed and type(out["target_id"]) is str, out
            assert type(out["relation"]) is str, out
            assert type(out["source_id"]) is str, out

    def test_a_null_relation_replies_with_the_relation_written(self, env, mcp):
        out = mcp(
            "kb_link",
            {"source_id": "fresh", "source_kb": "rs", "target_id": "hello", "relation": None},
        )
        assert out.get("created") is True, out
        assert out["relation"] == "related_to", out


class TestExplicitNonStringTarget:
    """On a surface where the caller types the link, a target YAML or JSON read
    as something other than text is refused with a hint to quote it, never
    converted: YAML 1.1 reads `yes` as true and `010` as 8 (fix round 2)."""

    @pytest.mark.parametrize(
        "raw",
        ["yes", "010", "2024", "3.5", "2024-01-01"],
    )
    def test_bulk_create(self, env, raw):
        source = _file(env, "rs", "fresh")
        spec = f"- source: fresh\n  target: {raw}\n"
        with _unchanged(source):
            out = cli("links", "bulk-create", "-", "-k", "rs", input=spec)
        assert out.exit_code == REFUSED, out.output
        item = _json(out)["results"][0]
        assert item["error_code"] == "VALIDATION_FAILED"
        assert "quote" in item["error"].lower()

    @pytest.mark.parametrize("target", [2024, 3.5, True], ids=["int", "float", "bool"])
    def test_mcp_kb_link(self, env, mcp, target):
        source = _file(env, "rs", "fresh")
        with _unchanged(source):
            out = mcp(
                "kb_link",
                {
                    "source_id": "fresh",
                    "source_kb": "rs",
                    "target_id": target,
                    "allow_dangling": True,
                },
            )
        assert out.get("error_code") == "VALIDATION_FAILED", out
        assert "quote" in out["error"].lower()


class TestLinkCommandSpec:
    @pytest.mark.parametrize("relation", ["", "  "], ids=["empty", "blank"])
    def test_an_empty_relation_is_refused_and_writes_nothing(self, env, relation):
        source = _file(env, "rs", "fresh")
        with _unchanged(source):
            out = cli("link", "fresh", "hello", "-k", "rs", "-r", relation)
        assert out.exit_code == REFUSED, out.output
        assert _json(out)["error_code"] == "VALIDATION_FAILED"

    def test_an_empty_target_is_refused_and_writes_nothing(self, env):
        source = _file(env, "rs", "fresh")
        with _unchanged(source):
            out = cli("link", "fresh", "", "-k", "rs")
        assert out.exit_code == REFUSED, out.output
        assert _json(out)["error_code"] == "VALIDATION_FAILED"


# --------------------------------------------------------------------------
# (1) a links read naming a KB that does not exist
# --------------------------------------------------------------------------


def _links_kb_cases():
    """Every KB option of every `pyrite links` subcommand, from the registry.

    A subcommand that gains a KB option is walked without editing this test.
    Each case names that option `nokb` and gives every other required
    parameter a value that exists, so the only thing wrong is the KB.
    """
    import re

    import typer.main

    from pyrite.cli import app

    root = typer.main.get_command(app)
    links = root.commands["links"]
    # The two options that make one choice together (`links asymmetric`).
    companions = {"kb_a": "kb_b", "kb_b": "kb_a"}
    cases = []
    for name, command in sorted(links.commands.items()):
        kb_params = [p for p in command.params if p.name and re.search(r"(^|_)kb(_|$)", p.name)]
        for param in kb_params:
            argv = [name]
            for other in command.params:
                if other.param_type_name == "argument" and other.required:
                    argv.append("hello")
            argv += [param.opts[0], "nokb"]
            for other in kb_params:
                if other is param:
                    continue
                if other.required or companions.get(param.name) == other.name:
                    argv += [other.opts[0], "rs"]
            longest = max(param.opts, key=len)
            # `bulk-create` reached `_writable_kb` before this change: walked
            # so a regression shows, but it cannot fail without the change.
            marks = (
                [pytest.mark.control(reason="control: bulk-create was already KB_NOT_FOUND")]
                if name == "bulk-create"
                else []
            )
            cases.append(pytest.param(argv, id=f"{name}{longest}", marks=marks))
    return cases


LINKS_KB_CASES = _links_kb_cases()


class TestLinksReadsOnAMissingKB:
    @pytest.mark.parametrize("argv", LINKS_KB_CASES)
    def test_is_kb_not_found(self, env, argv):
        out = cli("links", *argv, input="[]")
        assert out.exit_code == REFUSED, out.output
        doc = _json(out)
        assert doc["error_code"] == "KB_NOT_FOUND", doc
        assert "nokb" in doc["error"]

    @pytest.mark.control(reason="control: guards the registry walk, not the change")
    def test_the_walk_found_the_commands_the_issue_names(self):
        ids = {p.id for p in LINKS_KB_CASES}
        for expected in (
            "check--kb",
            "orphans--kb",
            "asymmetric--kb",
            "asymmetric--kb-a",
            "batch-suggest--source-kb",
            "batch-suggest--target-kb",
            "discover--target-kb",
            "suggest--target-kb",
        ):
            assert expected in ids, sorted(ids)

    @pytest.mark.control(reason="control: links check with no -k still checks every KB")
    def test_check_with_no_kb_is_unchanged(self, env):
        out = cli("links", "check")
        assert out.exit_code == 0, out.output
        assert _json(out)["missing_targets"] == 0

    @pytest.mark.control(reason="control: a DB-registered KB is not refused")
    def test_a_kb_registered_in_the_index_only_is_found(self, env, tmp_path):
        """config.get_kb covers DB-registered KBs: they are not refused."""
        from pyrite.services.kb_registry_service import KBRegistryService

        path = tmp_path / "dbonly"
        path.mkdir()
        KBRepository(KBConfig(name="dbonly", path=path, kb_type=KBType.GENERIC)).save(
            NoteEntry(id="solo", title="Solo", body="b")
        )
        db = PyriteDB(env["config"].settings.index_path)
        try:
            KBRegistryService(env["config"], db).add_kb("dbonly", str(path), "generic")
        finally:
            db.close()
        out = cli("links", "orphans", "-k", "dbonly")
        assert out.exit_code == 0, out.output


# --------------------------------------------------------------------------
# (3) update naming no field
# --------------------------------------------------------------------------


class TestEmptyUpdate:
    def test_is_refused_and_the_file_is_unchanged(self, env):
        path = _file(env, "rs", "hello")
        path.write_text(path.read_text().replace("title: Hello", "title:   'Hello'"))
        with _unchanged(path):
            out = cli("update", "hello", "-k", "rs")
        assert out.exit_code == REFUSED, out.output
        assert _json(out)["error_code"] == "VALIDATION_FAILED"

    @pytest.mark.control(reason="control: an update naming a field still updates")
    def test_an_update_with_a_field_still_writes(self, env):
        out = cli("update", "hello", "-k", "rs", "--title", "Hi")
        assert out.exit_code == 0, out.output


# --------------------------------------------------------------------------
# (4) a directory where a file was expected
# --------------------------------------------------------------------------


class TestDirectoryAsInputFile:
    def _cases(self, d: str):
        return {
            "create --body-file": [
                "create",
                "-k",
                "rs",
                "-t",
                "note",
                "--title",
                "D",
                "--body-file",
                d,
            ],
            "update --body-file": ["update", "hello", "-k", "rs", "--body-file", d],
            "add": ["add", d, "-k", "rs"],
            "links bulk-create --file": ["links", "bulk-create", "--file", d, "-k", "rs"],
            "links bulk-create <file>": ["links", "bulk-create", d, "-k", "rs"],
        }

    @pytest.mark.parametrize(
        "case",
        [
            "create --body-file",
            "update --body-file",
            "add",
            "links bulk-create --file",
            "links bulk-create <file>",
        ],
    )
    def test_is_validation_failed_naming_the_path(self, env, case):
        d = env["tmp"] / "a-directory"
        d.mkdir()
        notes = env["rs"] / "notes"
        with _unchanged(*notes.iterdir()):
            out = cli(*self._cases(str(d))[case])
        assert out.exit_code == REFUSED, out.output
        doc = _json(out)
        assert doc["error_code"] == "VALIDATION_FAILED", doc
        assert str(d) in doc["error"]

    @pytest.mark.control(reason="control: a missing file stays NOT_FOUND")
    def test_a_missing_file_is_still_not_found(self, env):
        out = cli("update", "hello", "-k", "rs", "--body-file", str(env["tmp"] / "nope.md"))
        assert out.exit_code == REFUSED
        assert _json(out)["error_code"] in ("NOT_FOUND", "ENTRY_NOT_FOUND")


# --------------------------------------------------------------------------
# The same services through MCP and REST
# --------------------------------------------------------------------------


@pytest.fixture
def mcp(env):
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(config=env["config"], tier="write")
    return server._dispatch_tool


class TestMCP:
    @pytest.mark.parametrize("relation", ["", "   "], ids=["empty", "blank"])
    def test_kb_link_with_an_empty_relation_is_refused(self, env, mcp, relation):
        source = _file(env, "rs", "fresh")
        with _unchanged(source):
            out = mcp(
                "kb_link",
                {
                    "source_id": "fresh",
                    "source_kb": "rs",
                    "target_id": "hello",
                    "relation": relation,
                },
            )
        assert out.get("error_code") == "VALIDATION_FAILED", out
        assert out["retryable"] is False

    @pytest.mark.parametrize(
        "args",
        [
            {"source_kb": "nokb", "target_kb": "rs"},
            {"source_kb": "rs", "target_kb": "nokb"},
        ],
        ids=["source", "target"],
    )
    def test_kb_batch_suggest_on_a_missing_kb(self, env, mcp, args):
        out = mcp("kb_batch_suggest", args)
        assert out.get("error_code") == "KB_NOT_FOUND", out

    @pytest.mark.parametrize(
        "args",
        [
            {"entry_id": "hello", "kb_name": "rs", "target_kb": "nokb"},
            {"entry_id": "hello", "kb_name": "nokb"},
        ],
        ids=["target", "source"],
    )
    def test_kb_discover_neighbors_on_a_missing_kb(self, env, mcp, args):
        out = mcp("kb_discover_neighbors", {**args, "mode": "keyword"})
        assert out.get("error_code") == "KB_NOT_FOUND", out


class TestMCPKbLinkOtherRefusals:
    def test_only_the_spec_check_answers_validation_failed(self):
        """A ValidationError subclass raised inside add_link (storage's id and
        containment checks, FrontmatterError) keeps the LINK_FAILED answer it
        had on dev (fix round 1): the handler maps VALIDATION_FAILED only
        around its own `check_link_spec` call, never around `add_link`.

        Structural, because no input found reaches such a subclass through
        add_link today (tried: `../x` and `.hidden` ids, a templated
        subdirectory escape, malformed frontmatter -- `load` answers None and
        save writes in place), and patching storage behind `_dispatch_tool` is
        what tests/test_tests_drive_the_surface_not_the_mock.py forbids.
        """
        import ast
        import inspect
        import textwrap

        from pyrite.server.mcp_server import PyriteMCPServer

        tree = ast.parse(textwrap.dedent(inspect.getsource(PyriteMCPServer._kb_link)))

        def calls(node, name):
            return any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute | ast.Name)
                and getattr(n.func, "attr", getattr(n.func, "id", None)) == name
                for n in ast.walk(node)
            )

        def catches_validation(handler):
            names = [
                n.id if isinstance(n, ast.Name) else getattr(n, "attr", "")
                for n in ast.walk(handler.type)
                if isinstance(n, ast.Name | ast.Attribute)
            ] if handler.type is not None else ["<bare>"]  # fmt: skip
            return "ValidationError" in names or "<bare>" in names

        tries = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
        around_add = [t for t in tries if any(calls(b, "add_link") for b in t.body)]
        around_check = [t for t in tries if any(calls(b, "check_link_spec") for b in t.body)]
        assert around_add and around_check
        for t in around_add:
            assert not any(catches_validation(h) for h in t.handlers)
            assert not any(calls(b, "check_link_spec") for b in t.body)

    @pytest.mark.control(
        reason="control: a missing source answered LINK_FAILED on dev and still does"
    )
    def test_an_unknown_source_still_answers_link_failed(self, env, mcp):
        """The spec check runs first, but a good spec on a missing source
        keeps its dev answer."""
        out = mcp("kb_link", {"source_id": "ghost", "source_kb": "rs", "target_id": "hello"})
        assert (out.get("error_code"), out["retryable"]) == ("LINK_FAILED", False), out


class TestREST:
    @pytest.mark.parametrize(
        ("path", "params"),
        [
            ("/api/links/batch-suggest", {"source_kb": "nokb", "target_kb": "test-events"}),
            ("/api/links/batch-suggest", {"source_kb": "test-events", "target_kb": "nokb"}),
            (
                "/api/links/discover-neighbors",
                {"entry_id": "x", "kb": "test-events", "target_kb": "nokb"},
            ),
        ],
        ids=["batch-source", "batch-target", "discover-target"],
    )
    def test_a_missing_kb_is_404_kb_not_found(self, rest_api_env, path, params):
        out = rest_api_env["client"].get(path, params=params)
        assert out.status_code == 404, out.text
        assert "KB_NOT_FOUND" in out.text

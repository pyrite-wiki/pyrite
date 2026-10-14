"""A link's duplicate key is (target, kb, relation), not (target, kb) (#396).

`pyrite link a b -r implements` then `pyrite link a b -r supersedes` used to
report `Linked:` for the second call too but write nothing -- the duplicate
check in `KBService.add_link` ignored `relation`, so a second, different
relation between the same two entries could never be recorded. `backlinks`
then showed only the first relation.

This file also pins the reporting and rendering bugs the groom named:
- a genuine no-op (identical target+kb+relation) is reported as one
  (`Already linked:` / `created: False`), not as `Linked:` / `created: True`;
- re-issuing that no-op after the target is deleted stays a no-op (the
  existing idempotency the duplicate check was written for);
- the relation name survives in the CLI confirmation, including on the
  `--bidi` inverse line -- Rich was parsing `--[implements]-->` as a style
  tag and swallowing it.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from tests.cli_help import plain

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import NoteEntry
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

runner = CliRunner()


@pytest.fixture
def link_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"
        notes_path = tmpdir / "notes"
        notes_path.mkdir()
        notes_kb = KBConfig(name="lk", path=notes_path, kb_type=KBType.GENERIC)
        config = PyriteConfig(knowledge_bases=[notes_kb], settings=Settings(index_path=db_path))
        repo = KBRepository(notes_kb)
        for eid in ("link-a", "link-b"):
            repo.save(NoteEntry(id=eid, title=eid, body=f"Body for {eid}."))
        db = PyriteDB(db_path)
        IndexManager(db, config).index_all()
        db.close()
        yield {"config": config, "tmpdir": tmpdir, "notes_path": notes_path, "db_path": db_path}


def _patch_config(env):
    import contextlib

    @contextlib.contextmanager
    def _multi_patch():
        with patch("pyrite.cli.load_config", return_value=env["config"]):
            with patch("pyrite.cli.context.load_config", return_value=env["config"]):
                yield

    return _multi_patch()


# ---------------------------------------------------------------------------
# Service-level: KBService.add_link
# ---------------------------------------------------------------------------


def test_add_link_with_a_different_relation_is_created_not_skipped(link_env):
    db = PyriteDB(link_env["db_path"])
    try:
        svc = KBService(link_env["config"], db)
        r1 = svc.add_link("link-a", "lk", "link-b", relation="implements")
        assert r1["created"] is True

        r2 = svc.add_link("link-a", "lk", "link-b", relation="supersedes")
        assert r2["created"] is True

        repo = KBRepository(link_env["config"].get_kb("lk"))
        entry = repo.load("link-a")
        relations = {lnk.relation for lnk in entry.links}
        assert relations == {"implements", "supersedes"}, relations
    finally:
        db.close()


def test_add_link_identical_triple_is_a_noop_and_reports_created_false(link_env):
    db = PyriteDB(link_env["db_path"])
    try:
        svc = KBService(link_env["config"], db)
        svc.add_link("link-a", "lk", "link-b", relation="implements")

        path = list(link_env["notes_path"].rglob("link-a.md"))[0]
        before = path.read_text(encoding="utf-8")

        result = svc.add_link("link-a", "lk", "link-b", relation="implements")
        assert result["created"] is False

        after = path.read_text(encoding="utf-8")
        assert after == before
    finally:
        db.close()


def test_add_link_default_relation_matches_a_legacy_links_without_relation(link_env):
    """A hand-written (or pre-#396) `links: [{target: b}]` entry -- no
    `relation` key at all -- loads with `Link.from_dict`'s own default,
    `"related"`. `add_link`'s caller-facing default is `"related_to"`
    (matching the CLI, MCP and RELATIONSHIP_TYPES). Before this fix the
    duplicate key compared these two default strings literally and treated
    them as different relations, so `pyrite link a b` (no -r) on legacy data
    added a second link where `dev` did nothing -- round-1 cold read.
    """
    notes_path = link_env["notes_path"]
    (notes_path / "link-a.md").write_text(
        "---\nid: link-a\ntitle: link-a\ntype: note\nlinks:\n- target: link-b\n---\nx\n",
        encoding="utf-8",
    )
    db = PyriteDB(link_env["db_path"])
    try:
        from pyrite.storage.index import IndexManager

        IndexManager(db, link_env["config"]).index_all()
        svc = KBService(link_env["config"], db)

        path = notes_path / "link-a.md"
        before = path.read_text(encoding="utf-8")

        # No -r/relation given: this is add_link's own default, exactly what
        # `pyrite link a b` and MCP `kb_link` send with no --relation.
        result = svc.add_link("link-a", "lk", "link-b")
        assert result["created"] is False, "legacy 'related' must match the related_to default"

        after = path.read_text(encoding="utf-8")
        assert after == before, after
    finally:
        db.close()


def test_add_links_bulk_default_relation_matches_legacy_data(link_env):
    """The bulk path (`add_links`) must normalise the same way as `add_link`."""
    notes_path = link_env["notes_path"]
    (notes_path / "link-a.md").write_text(
        "---\nid: link-a\ntitle: link-a\ntype: note\nlinks:\n- target: link-b\n---\nx\n",
        encoding="utf-8",
    )
    db = PyriteDB(link_env["db_path"])
    try:
        from pyrite.storage.index import IndexManager

        IndexManager(db, link_env["config"]).index_all()
        svc = KBService(link_env["config"], db)

        results = svc.add_links("lk", [{"source": "link-a", "target": "link-b"}])
        assert results[0]["status"] == "skipped", results
    finally:
        db.close()


def test_cli_link_no_relation_flag_is_a_noop_on_legacy_data(link_env):
    """`pyrite link a b` (no -r, so the CLI's own default applies) against
    legacy data with no `relation` key must print `Already linked:`."""
    notes_path = link_env["notes_path"]
    (notes_path / "link-a.md").write_text(
        "---\nid: link-a\ntitle: link-a\ntype: note\nlinks:\n- target: link-b\n---\nx\n",
        encoding="utf-8",
    )
    db = PyriteDB(link_env["db_path"])
    IndexManager(db, link_env["config"]).index_all()
    db.close()

    with _patch_config(link_env):
        result = runner.invoke(app, ["link", "link-a", "link-b", "-k", "lk", "--format", "rich"])
        assert result.exit_code == 0, result.output
    clean = plain(result.output)
    assert "Already linked:" in clean, clean


def test_cli_link_already_linked_names_the_relation_actually_on_disk(link_env):
    """Round-2 cold read: `_norm_relation` (round 1) makes a caller's
    `related_to` match an on-disk legacy `related` link as the same
    no-op -- but the confirmation line named the *caller's* relation
    (`related_to`), not what the file actually says (`related`). A caller
    reading `Already linked: a --[related_to]--> b` and then grepping the
    file for `related_to` would find nothing."""
    notes_path = link_env["notes_path"]
    (notes_path / "link-a.md").write_text(
        "---\nid: link-a\ntitle: link-a\ntype: note\nlinks:\n- target: link-b\n"
        "  relation: related\n---\nx\n",
        encoding="utf-8",
    )
    db = PyriteDB(link_env["db_path"])
    IndexManager(db, link_env["config"]).index_all()
    db.close()

    with _patch_config(link_env):
        # No -r: the CLI's default is related_to, which normalises to match
        # the file's explicit "related" -- still a no-op, but the message
        # must say what is actually stored, not the caller's default.
        result = runner.invoke(app, ["link", "link-a", "link-b", "-k", "lk", "--format", "rich"])
        assert result.exit_code == 0, result.output
    clean = plain(result.output)
    assert "Already linked:" in clean, clean
    assert "[related]" in clean, clean
    assert "[related_to]" not in clean, clean


def test_cli_link_bidi_already_linked_names_the_relation_actually_on_disk(link_env):
    """Same bug as `test_cli_link_already_linked_names_the_relation_actually_on_disk`,
    on the `--bidi` inverse leg: the inverse confirmation must also name what
    is on disk for the target->source direction, not the CLI's computed
    `inverse` value."""
    notes_path = link_env["notes_path"]
    (notes_path / "link-a.md").write_text(
        "---\nid: link-a\ntitle: link-a\ntype: note\nlinks:\n- target: link-b\n"
        "  relation: related\n---\nx\n",
        encoding="utf-8",
    )
    (notes_path / "link-b.md").write_text(
        "---\nid: link-b\ntitle: link-b\ntype: note\nlinks:\n- target: link-a\n"
        "  relation: related\n---\ny\n",
        encoding="utf-8",
    )
    db = PyriteDB(link_env["db_path"])
    IndexManager(db, link_env["config"]).index_all()
    db.close()

    with _patch_config(link_env):
        # No -r: forward default related_to matches on-disk "related" on
        # link-a; the --bidi inverse (also related_to, since related_to is
        # self-inverse) must likewise match link-b's on-disk "related".
        result = runner.invoke(
            app, ["link", "link-a", "link-b", "-k", "lk", "--bidi", "--format", "rich"]
        )
        assert result.exit_code == 0, result.output
    clean = plain(result.output)
    assert clean.count("Already linked:") == 2, clean
    assert clean.count("[related]") == 2, clean
    assert "[related_to]" not in clean, clean


def test_add_link_identical_triple_stays_a_noop_after_target_deleted(link_env):
    """The idempotency the duplicate check exists for: a replayed link set
    must not fail just because the target has since gone."""
    db = PyriteDB(link_env["db_path"])
    try:
        svc = KBService(link_env["config"], db)
        svc.add_link("link-a", "lk", "link-b", relation="implements")

        # Through the service's own delete, so the index row goes with the
        # file -- an `unlink()` on disk alone would leave the index stale and
        # `resolved` would read the deleted target as still there.
        assert svc.delete_entry("link-b", "lk") is True

        result = svc.add_link("link-a", "lk", "link-b", relation="implements")
        assert result["created"] is False
        assert result["resolved"] is False
    finally:
        db.close()


# ---------------------------------------------------------------------------
# CLI: pyrite link
# ---------------------------------------------------------------------------


def test_cli_link_second_relation_is_appended(link_env):
    with _patch_config(link_env):
        r1 = runner.invoke(
            app, ["link", "link-a", "link-b", "-k", "lk", "-r", "implements", "--format", "rich"]
        )
        assert r1.exit_code == 0, r1.output
        r2 = runner.invoke(
            app, ["link", "link-a", "link-b", "-k", "lk", "-r", "supersedes", "--format", "rich"]
        )
        assert r2.exit_code == 0, r2.output

    repo = KBRepository(link_env["config"].get_kb("lk"))
    entry = repo.load("link-a")
    relations = {lnk.relation for lnk in entry.links}
    assert relations == {"implements", "supersedes"}, relations


def test_cli_link_duplicate_prints_already_linked(link_env):
    with _patch_config(link_env):
        r1 = runner.invoke(
            app, ["link", "link-a", "link-b", "-k", "lk", "-r", "implements", "--format", "rich"]
        )
        assert r1.exit_code == 0, r1.output
        assert "Linked:" in plain(r1.output)

        r2 = runner.invoke(
            app, ["link", "link-a", "link-b", "-k", "lk", "-r", "implements", "--format", "rich"]
        )
        assert r2.exit_code == 0, r2.output
        clean = plain(r2.output)
        assert "Already linked:" in clean
        assert "Linked:" not in clean.replace("Already linked:", "")


def test_cli_link_confirmation_shows_the_relation(link_env):
    with _patch_config(link_env):
        result = runner.invoke(
            app, ["link", "link-a", "link-b", "-k", "lk", "-r", "implements", "--format", "rich"]
        )
        assert result.exit_code == 0, result.output
    clean = plain(result.output)
    assert "implements" in clean, clean


def test_cli_link_bidi_confirmation_shows_the_inverse_relation(link_env):
    with _patch_config(link_env):
        result = runner.invoke(
            app,
            [
                "link",
                "link-a",
                "link-b",
                "-k",
                "lk",
                "-r",
                "implements",
                "--bidi",
                "--format",
                "rich",
            ],
        )
        assert result.exit_code == 0, result.output
    clean = plain(result.output)
    # Two confirmation lines: forward relation and its inverse.
    assert "implements" in clean, clean
    from pyrite.schema import get_inverse_relation

    assert get_inverse_relation("implements") in clean, clean


def _backlink_rows(link_env, relations):
    db = PyriteDB(link_env["db_path"])
    try:
        svc = KBService(link_env["config"], db)
        for rel in relations:
            svc.add_link("link-a", "lk", "link-b", relation=rel)
    finally:
        db.close()

    with _patch_config(link_env):
        result = runner.invoke(app, ["backlinks", "link-b", "-k", "lk", "--format", "json"])
    assert result.exit_code == 0, result.output
    return {row["forward_relation"]: row for row in json.loads(result.output)["entries"]}


def test_cli_backlinks_report_the_stored_relation_for_a_custom_relation(link_env):
    """#527: `informs` is not a known relation; the backlink must still say it.

    `forward_relation` is what the source's file says (source -> target);
    `relation` is the target's reading and keeps its related_to fallback;
    `inverse_relation` is the known inverse, null when none is known.
    """
    rows = _backlink_rows(link_env, ["informs"])
    row = rows["informs"]
    assert row["relation"] == "related_to"
    assert row["inverse_relation"] is None


def test_cli_backlinks_report_both_directions_for_a_known_relation(link_env):
    rows = _backlink_rows(link_env, ["supports"])
    row = rows["supports"]
    assert row["relation"] == "supported_by"
    assert row["inverse_relation"] == "supported_by"


def test_cli_link_bidirectional_unknown_relation_never_writes_the_reversed_claim(link_env):
    with _patch_config(link_env):
        result = runner.invoke(
            app,
            ["link", "link-a", "link-b", "-k", "lk", "-r", "informs", "--bidi", "--format", "rich"],
        )
    assert result.exit_code == 0, result.output

    repo = KBRepository(link_env["config"].get_kb("lk"))
    target_links = {(lnk.target, lnk.relation) for lnk in repo.load("link-b").links}
    assert target_links == {("link-a", "related_to")}, target_links
    # The notice is a diagnostic, not the result: it goes to stderr, so stdout
    # holds only the two result lines (#590).
    notice = plain(result.stderr)
    assert "No inverse is known for 'informs'" in notice, notice
    assert "related_to" in notice, notice
    stdout = plain(result.stdout)
    assert "No inverse is known" not in stdout, stdout
    assert [ln for ln in stdout.splitlines() if ln.strip()] == [
        "Linked: link-a --[informs]--> link-b (in lk)",
        "Linked: link-b --[related_to]--> link-a (in lk)",
    ], stdout


def test_cli_link_bidirectional_notice_keeps_a_bracketed_relation(link_env):
    """Rich reads `[dim]` as a style tag; the notice must print it, as `_report` does (#396)."""
    with _patch_config(link_env):
        result = runner.invoke(
            app,
            ["link", "link-a", "link-b", "-k", "lk", "-r", "[dim]", "--bidi", "--format", "rich"],
        )
    assert result.exit_code == 0, result.output
    assert "No inverse is known for '[dim]'" in plain(result.stderr), result.stderr


def _link_then_backlinks_table(link_env, cli, relations):
    cli_app, table_args = cli
    db = PyriteDB(link_env["db_path"])
    try:
        svc = KBService(link_env["config"], db)
        for rel in relations:
            svc.add_link("link-a", "lk", "link-b", relation=rel)
    finally:
        db.close()
    with (
        _patch_config(link_env),
        patch("pyrite.read_cli.load_config", return_value=link_env["config"]),
    ):
        result = runner.invoke(cli_app, ["backlinks", "link-b", "-k", "lk", *table_args])
    assert result.exit_code == 0, result.output
    return plain(result.stdout)


def _table_clis():
    """Both CLIs' table: `pyrite-read` prints it by default, `pyrite` (JSON by
    default) under `--format rich`."""
    from pyrite.read_cli import app as read_app

    return [
        pytest.param((read_app, []), id="pyrite-read-default"),
        pytest.param((app, ["--format", "rich"]), id="pyrite-format-rich"),
    ]


@pytest.mark.parametrize("cli", _table_clis())
def test_cli_backlinks_table_shows_the_relation_as_written(link_env, cli):
    """#527's symptom was the table: `related_to` shown for `informs`.

    The table keeps `Relation` (this entry's reading) and adds the relation as
    the source's file wrote it, the same way in both CLIs.
    """
    table = _link_then_backlinks_table(link_env, cli, ["informs"])
    assert "Written as" in table, table
    row = next(ln for ln in table.splitlines() if "link-a" in ln)
    assert "informs" in row and "related_to" in row, table


@pytest.mark.parametrize("cli", _table_clis())
def test_cli_backlinks_table_keeps_a_bracketed_relation(link_env, cli):
    table = _link_then_backlinks_table(link_env, cli, ["[dim]"])
    assert "[dim]" in table, table

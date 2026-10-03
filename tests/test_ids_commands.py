"""`pyrite ids missing` / `pyrite ids pin` (#700, ADR-0042 decision 5).

The property: an operator pins every id-less file's *current* id before the
identity switch, and each file gains exactly one line, ``id: <id>``. No other
byte changes. A collision is never resolved silently.

Medium tests: hand-authored fixture files (not Pyrite's output) in a real
git repository, driven through the CLI entry point (``pyrite.cli.app``) with
a real index. The ``git diff`` of each pinned file is the evidence an
operator would read.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings

runner = CliRunner()

# Exit codes the docs promise (docs/pinning-entry-ids.md).
OUTSIDE_CONTRACT = 3


# --- fixtures ---------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(cwd)},
    ).stdout.decode("utf-8")  # not text=True: that drops the \r of a CRLF line


@pytest.fixture
def kb(tmp_path):
    """A KB directory under git, and a config naming it ``notes``."""
    root = tmp_path / "notes"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "core.autocrlf", "false")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="notes", path=root, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    return root, config


def _write(root: Path, files: dict[str, bytes | str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))


def _commit(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixtures")


def _run(config: PyriteConfig, *args: str):
    # `ids missing` and `pin --dry-run` load the config themselves, without
    # cli_context (which would open, and so create, the index).
    with (
        patch("pyrite.cli.context.load_config", return_value=config),
        patch("pyrite.services.id_pin_service.load_config", return_value=config),
    ):
        return runner.invoke(app, list(args))


def _json(result) -> dict:
    assert result.output.strip(), result.output
    return json.loads(result.output)


def _numstat(root: Path) -> dict[str, tuple[str, str]]:
    out = _git(root, "diff", "--numstat")
    rows = {}
    for line in out.split("\n"):
        if not line:
            continue
        added, removed, path = line.split("\t")
        rows[path] = (added, removed)
    return rows


def _added_lines(root: Path, rel: str) -> list[str]:
    out = _git(root, "diff", "--no-color", "-U0", "--", rel)
    return [
        line[1:] for line in out.split("\n") if line.startswith("+") and not line.startswith("+++")
    ]


# --- hand-authored fixtures: one added line and nothing else ---------------

ONE_LINE_FIXTURES = {
    # name: (path, bytes, expected id)
    "plain": ("notes/plain.md", b"---\ntitle: Plain Note\ntype: note\n---\nBody.\n", "plain-note"),
    "crlf": (
        "crlf.md",
        b"---\r\ntitle: Windows Note\r\ntags: [a, b]\r\n---\r\nBody\r\nline two\r\n",
        "windows-note",
    ),
    "bom": ("bom.md", "﻿---\ntitle: Bom Note\n---\nx\n".encode(), "bom-note"),
    "comment_first": (
        "comment.md",
        b"---\n# written by hand, keep this comment\ntitle: Commented   # trailing\n"
        b"type: note\n---\n\nBody\n",
        "commented",
    ),
    "trailing_comment": (
        "trail.md",
        b"---\ntitle: Trailing\n# a comment as the last frontmatter line\n---\nB\n",
        "trailing",
    ),
    "flow_style": (
        "flow.md",
        b"---\ntitle: 'Flow, styled'\ntags: [x,   y]\nmeta: {a: 1, b: \"two\"}\n---\n",
        "flow-styled",
    ),
    "unknown_keys": (
        "people/unknown.md",
        b'---\ntype: widget\nTitle_Case: kept\ntitle: "Caf\xc3\xa9 r\xc3\xa9sum\xc3\xa9"\n'
        b"zzz_custom:\n  - one\n  -   two\n---\nbody with --- inside\n",
        "cafe-resume",
    ),
    "block_scalar_last": (
        "block.md",
        b"---\ntitle: Block Last\nsummary: |\n  line one\n  line two\n---\nBody\n",
        "block-last",
    ),
    "no_trailing_newline_body": ("nonl.md", b"---\ntitle: No Newline\n---\nbody", "no-newline"),
    "no_title": ("notitle.md", b"---\ntype: note\n---\nbody only\n", None),
    "index_page": ("section/_index.md", b"---\ntitle: Section Home\n---\n", "section-home"),
}


@pytest.mark.parametrize("name", sorted(ONE_LINE_FIXTURES))
def test_pin_adds_exactly_one_id_line_and_nothing_else(kb, name):
    root, config = kb
    rel, content, expected = ONE_LINE_FIXTURES[name]
    _write(root, {rel: content})
    _commit(root)
    before = _json(_run(config, "ids", "missing", "-k", "notes", "--format", "json"))
    [row] = before["missing"]
    if expected is None:
        # no title: the current rule's hash fallback (`entry-xxxxxxxx`)
        expected = row["id"]
        assert expected.startswith("entry-")
    assert row == {"path": rel, "id": expected, "status": "missing"}

    result = _run(config, "ids", "pin", "-k", "notes")
    assert result.exit_code == 0, result.output

    assert _numstat(root) == {rel: ("1", "0")}
    eol = "\r\n" if b"\r\n" in content else "\n"
    assert _added_lines(root, rel) == [f"id: {expected}" + ("\r" if eol == "\r\n" else "")]

    # Byte for byte: the old file with one line inserted before the closing `---`.
    after = (root / rel).read_bytes()
    line = f"id: {expected}{eol}".encode()
    at = after.index(line)
    assert after[:at] + after[at + len(line) :] == content
    assert after[at + len(line) :].startswith(b"---")

    # And the file now states the id it had.
    again = _json(_run(config, "ids", "missing", "-k", "notes", "--format", "json"))
    assert again["missing"] == []


def test_pinned_id_is_the_id_the_index_holds_today(kb):
    """The pin reuses the derivation: what `get` resolves before the pin is
    what the file states after it."""
    root, config = kb
    _write(
        root,
        {
            "a.md": "---\ntype: event\ntitle: Big Thing\ndate: 2025-01-02\n---\nx\n",
            "b.md": "---\ntype: person\ntitle: Ada Lovelace\n---\n",
            "c.md": "---\ntitle: 日本語\n---\n",
        },
    )
    assert _run(config, "index", "sync", "-k", "notes").exit_code == 0
    before = _json(_run(config, "ids", "missing", "-k", "notes", "--format", "json"))
    for row in before["missing"]:
        got = _run(config, "get", row["id"], "-k", "notes", "--format", "json")
        assert got.exit_code == 0, (row, got.output)
    assert _run(config, "ids", "pin", "-k", "notes").exit_code == 0
    for row in before["missing"]:
        text = (root / row["path"]).read_text()
        assert f"\nid: {row['id']}\n---" in text


# --- missing: what is reported, and the exit code -------------------------


def test_missing_exits_zero_when_every_file_has_an_id(kb):
    root, config = kb
    _write(root, {"a.md": "---\nid: a\ntitle: A\n---\n", "README.md": "no frontmatter\n"})
    result = _run(config, "ids", "missing", "-k", "notes", "--format", "json")
    assert result.exit_code == 0, result.output
    data = _json(result)
    assert data["missing"] == [] and data["skipped"] == [] and data["collisions"] == []


def test_missing_exits_three_and_reports_each_kind(kb):
    root, config = kb
    _write(
        root,
        {
            "has.md": "---\nid: has\ntitle: Has\n---\n",
            "none.md": "---\ntitle: None Here\n---\n",
            "empty.md": "---\nid: ''\ntitle: Empty Id\n---\n",
            "nullid.md": "---\nid:\ntitle: Null Id\n---\n",
            "bad.md": "---\ntitle: [unclosed\n---\n",
            "nofm.md": "just text\n",
            "latin1.md": b"---\ntitle: caf\xe9\n---\n",
            # a title YAML reads as a number: today's derivation raises
            "numtitle.md": "---\ntitle: 1e3\n---\n",
            ".hidden/x.md": "---\ntitle: Hidden\n---\n",
            "_templates/t.md": "---\ntitle: Template\n---\n",
        },
    )
    result = _run(config, "ids", "missing", "-k", "notes", "--format", "json")
    assert result.exit_code == OUTSIDE_CONTRACT, result.output
    data = _json(result)
    assert {(r["path"], r["status"]) for r in data["missing"]} == {
        ("none.md", "missing"),
        ("empty.md", "empty_id"),
        ("nullid.md", "empty_id"),
    }
    assert {r["path"] for r in data["skipped"]} == {"bad.md", "nofm.md", "latin1.md", "numtitle.md"}
    assert all(r["reason"] for r in data["skipped"])


def test_missing_text_output_names_paths_and_ids(kb):
    root, config = kb
    _write(root, {"n/one.md": "---\ntitle: One Two\n---\n"})
    result = _run(config, "ids", "missing", "-k", "notes", "--format", "text")
    assert result.exit_code == OUTSIDE_CONTRACT
    assert "n/one.md" in result.output and "one-two" in result.output


def test_unknown_kb_is_an_error_not_a_finding(kb):
    _, config = kb
    result = _run(config, "ids", "missing", "-k", "nope")
    assert result.exit_code == 1


# --- collisions -------------------------------------------------------------

COLLIDING = {
    "c1.md": "---\ntitle: Same\n---\none\n",
    "sub/c2.md": "---\ntitle: Same\n---\ntwo\n",
    "other.md": "---\ntitle: Other\n---\n",
}


def test_missing_groups_collisions_including_an_explicit_holder(kb):
    root, config = kb
    _write(root, {**COLLIDING, "c3.md": "---\nid: same\ntitle: Explicit\n---\n"})
    data = _json(_run(config, "ids", "missing", "-k", "notes", "--format", "json"))
    [group] = data["collisions"]
    assert group["id"] == "same"
    assert sorted((c["path"], c["explicit"]) for c in group["claimants"]) == [
        ("c1.md", False),
        ("c3.md", True),
        ("sub/c2.md", False),
    ]


def test_pin_refuses_an_unresolved_group_and_pins_the_rest(kb):
    root, config = kb
    _write(root, COLLIDING)
    _commit(root)
    result = _run(config, "ids", "pin", "-k", "notes", "--format", "json")
    assert result.exit_code == OUTSIDE_CONTRACT, result.output
    data = _json(result)
    assert [p["path"] for p in data["pinned"]] == ["other.md"]
    [refused] = data["refused"]
    assert refused["id"] == "same"
    assert sorted(refused["unrenamed"]) == ["c1.md", "sub/c2.md"]
    assert _numstat(root) == {"other.md": ("1", "0")}


def test_pin_resolves_a_group_with_a_rename(kb):
    root, config = kb
    _write(root, COLLIDING)
    _commit(root)
    result = _run(config, "ids", "pin", "-k", "notes", "--rename", "sub/c2.md=same-2")
    assert result.exit_code == 0, result.output
    assert _numstat(root) == {
        "c1.md": ("1", "0"),
        "sub/c2.md": ("1", "0"),
        "other.md": ("1", "0"),
    }
    assert _added_lines(root, "c1.md") == ["id: same"]
    assert _added_lines(root, "sub/c2.md") == ["id: same-2"]
    # pin synced the index: both files are reachable now
    for eid in ("same", "same-2"):
        assert _run(config, "get", eid, "-k", "notes", "--format", "json").exit_code == 0
    assert _run(config, "ids", "missing", "-k", "notes").exit_code == 0


def test_an_explicit_holder_keeps_the_id_so_every_idless_claimant_needs_a_rename(kb):
    root, config = kb
    _write(root, {**COLLIDING, "c3.md": "---\nid: same\ntitle: Explicit\n---\n"})
    _commit(root)
    one = _run(config, "ids", "pin", "-k", "notes", "--rename", "c1.md=same-a")
    assert one.exit_code == OUTSIDE_CONTRACT
    assert _numstat(root) == {"other.md": ("1", "0")}
    both = _run(
        config,
        "ids",
        "pin",
        "-k",
        "notes",
        "--rename",
        "c1.md=same-a",
        "--rename",
        "sub/c2.md=same-b",
    )
    assert both.exit_code == 0, both.output
    assert _added_lines(root, "c1.md") == ["id: same-a"]
    assert _added_lines(root, "sub/c2.md") == ["id: same-b"]
    assert "c3.md" not in _numstat(root)


@pytest.mark.parametrize(
    ("rename", "why"),
    [
        ("sub/c2.md=other", "taken"),  # another file's current id
        ("sub/c2.md=same", "taken"),
        ("sub/c2.md=../x", "invalid"),
        ("sub/c2.md=a/b", "invalid"),
        ("sub/c2.md=.hidden", "invalid"),
        ("sub/c2.md=-", "invalid"),
        ("sub/c2.md=", "invalid"),
        ("other.md=fresh", "not in a collision"),
        ("missing.md=fresh", "not in a collision"),
        ("no-equals-sign", "invalid"),
    ],
)
def test_a_bad_rename_refuses_the_whole_run(kb, rename, why):
    root, config = kb
    _write(root, COLLIDING)
    _commit(root)
    result = _run(config, "ids", "pin", "-k", "notes", "--rename", rename)
    assert result.exit_code == 1, result.output
    assert why in result.output.lower(), result.output
    assert _numstat(root) == {}


def test_two_renames_to_one_id_are_refused(kb):
    root, config = kb
    _write(root, {**COLLIDING, "c3.md": "---\ntitle: Same\n---\n"})
    _commit(root)
    result = _run(config, "ids", "pin", "-k", "notes", "--rename", "c1.md=x", "--rename", "c3.md=x")
    assert result.exit_code == 1
    assert _numstat(root) == {}


# --- dry run, untouched files, safety ---------------------------------------


def test_dry_run_writes_nothing_and_returns_the_real_runs_exit_code(kb):
    root, config = kb
    _write(root, COLLIDING)
    _commit(root)
    stamps = {p: p.stat().st_mtime_ns for p in root.rglob("*.md")}
    result = _run(config, "ids", "pin", "-k", "notes", "--dry-run", "--format", "json")
    assert result.exit_code == OUTSIDE_CONTRACT, result.output
    data = _json(result)
    assert data["dry_run"] is True
    assert [p["path"] for p in data["pinned"]] == ["other.md"]
    assert _numstat(root) == {}
    assert {p: p.stat().st_mtime_ns for p in root.rglob("*.md")} == stamps

    text = _run(config, "ids", "pin", "-k", "notes", "--dry-run", "--format", "text")
    assert "other.md" in text.output and "same" in text.output
    assert _numstat(root) == {}


def test_files_pin_cannot_change_safely_are_reported_and_untouched(kb):
    root, config = kb
    files = {
        "has.md": "---\nid: has\ntitle: Has\n---\n",
        "empty.md": "---\nid: ''\ntitle: Empty Id\n---\n",
        "bad.md": "---\ntitle: [unclosed\n---\n",
        "flowmap.md": "---\n{title: Flow Map, type: note}\n---\n",
        "indented.md": "---\n  title: Indented\n  type: note\n---\n",
        "nofm.md": "just text\n",
        "README.md": "---\ntitle: Readme\n---\n",
        "ok.md": "---\ntitle: Fine\n---\n",
    }
    _write(root, files)
    _commit(root)
    result = _run(config, "ids", "pin", "-k", "notes", "--format", "json")
    assert result.exit_code == OUTSIDE_CONTRACT, result.output
    data = _json(result)
    assert [p["path"] for p in data["pinned"]] == ["ok.md"]
    skipped = {s["path"] for s in data["skipped"]}
    # flow-mapping and indented frontmatter: a top-level `id:` line would not
    # parse there, so the re-parse check refuses them before anything is written
    assert skipped == {"empty.md", "bad.md", "nofm.md", "flowmap.md", "indented.md"}
    reasons = {r["path"]: r["reason"] for r in data["skipped"]}
    assert "empty id: line" in reasons["empty.md"], reasons["empty.md"]
    assert _numstat(root) == {"ok.md": ("1", "0")}


def test_ambiguous_ids_are_quoted_so_they_read_back_as_the_same_text(kb):
    root, config = kb
    _write(
        root,
        {
            "n.md": "---\ntitle: '123'\n---\n",
            "e.md": "---\ntitle: '1e3'\n---\n",
            "y.md": "---\ntitle: 'yes'\n---\n",
            "nl.md": "---\ntitle: 'null'\n---\n",
        },
    )
    _commit(root)
    before = _json(_run(config, "ids", "missing", "-k", "notes", "--format", "json"))
    ids = {r["path"]: r["id"] for r in before["missing"]}
    assert _run(config, "ids", "pin", "-k", "notes").exit_code == 0
    for rel, eid in ids.items():
        [line] = _added_lines(root, rel)
        assert line == f"id: '{eid}'", line
    after = _json(_run(config, "ids", "missing", "-k", "notes", "--format", "json"))
    assert after["missing"] == []
    for eid in ids.values():
        assert _run(config, "get", eid, "-k", "notes", "--format", "json").exit_code == 0


def test_a_file_changed_at_the_last_moment_is_not_overwritten(kb, monkeypatch):
    """Pyrite is a guest: an edit made between computing the pin and writing
    it wins (the check inside ``_write_pin``)."""
    from pyrite.services import id_pin_service

    root, config = kb
    _write(root, {"a.md": "---\ntitle: A\n---\n"})
    real = id_pin_service._write_pin

    def edit_first(root_, path, planned_original, new_text):
        path.write_text("---\ntitle: A\n---\nedited by a human\n")
        return real(root_, path, planned_original, new_text)

    monkeypatch.setattr(id_pin_service, "_write_pin", edit_first)
    result = _run(config, "ids", "pin", "-k", "notes", "--format", "json")
    assert result.exit_code == OUTSIDE_CONTRACT, result.output
    assert (root / "a.md").read_text() == "---\ntitle: A\n---\nedited by a human\n"
    assert _json(result)["skipped"][0]["path"] == "a.md"


def test_pin_never_goes_through_update_or_to_frontmatter(kb, monkeypatch):
    from pyrite.models.base import Entry
    from pyrite.services.kb_service import KBService

    def boom(*a, **k):
        raise AssertionError("pin must be a textual insertion")

    monkeypatch.setattr(KBService, "update", boom)
    monkeypatch.setattr(KBService, "update_entry", boom)
    monkeypatch.setattr(Entry, "to_markdown", boom)
    monkeypatch.setattr(Entry, "to_frontmatter", boom)
    root, config = kb
    _write(root, {"a.md": "---\ntitle: A\n---\n"})
    result = _run(config, "ids", "pin", "-k", "notes")
    assert result.exit_code == 0, result.output


def test_pin_with_nothing_to_do_exits_zero_and_writes_nothing(kb):
    root, config = kb
    _write(root, {"a.md": "---\nid: a\n---\n"})
    _commit(root)
    result = _run(config, "ids", "pin", "-k", "notes")
    assert result.exit_code == 0, result.output
    assert _numstat(root) == {}


def test_files_with_no_title_share_the_hash_id_and_are_a_collision(kb):
    """#639: every file with no id and no title derives `entry-da39a3ee`
    today. Pin does not pick one for the operator."""
    root, config = kb
    _write(root, {"a.md": "---\ntype: note\n---\none\n", "b.md": "---\ntype: note\n---\ntwo\n"})
    _commit(root)
    data = _json(_run(config, "ids", "missing", "-k", "notes", "--format", "json"))
    [group] = data["collisions"]
    assert group["id"].startswith("entry-")
    result = _run(config, "ids", "pin", "-k", "notes", "--rename", "b.md=note-b")
    assert result.exit_code == 0, result.output
    assert _added_lines(root, "a.md") == [f"id: {group['id']}"]
    assert _added_lines(root, "b.md") == ["id: note-b"]


def test_the_reparse_check_refuses_a_line_that_reads_back_as_something_else(monkeypatch):
    """Defence behind the quoting rule: if the added line ever read back as
    another value (``id: 123`` is an int), the file is refused, not written."""
    from pyrite.services import id_pin_service

    monkeypatch.setattr(id_pin_service, "id_line", lambda eid, eol="\n": f"id: {eid}{eol}")
    with pytest.raises(id_pin_service.PinRefusedError):
        id_pin_service.insert_id_line("---\ntitle: '123'\n---\n", "123")


# --- fix round 1: the write reaches only what was scanned, inside the KB ----


def test_a_file_retitled_between_scan_and_write_keeps_the_edit_and_gets_no_stale_id(
    kb, monkeypatch
):
    """The cold read's probe, through the CLI: the scan derives `alpha`, a
    human retitles the file before the write; the file must not gain
    `id: alpha` (an id it no longer holds), and the edit is kept."""
    from pyrite.services import id_pin_service

    root, config = kb
    _write(root, {"a.md": "---\ntitle: Alpha\ntype: note\n---\nx\n"})
    real_plan = id_pin_service.plan

    def plan_then_retitle(found, renames):
        result = real_plan(found, renames)
        (root / "a.md").write_text("---\ntitle: Beta Renamed\ntype: note\n---\nx\n")
        return result

    monkeypatch.setattr(id_pin_service, "plan", plan_then_retitle)
    result = _run(config, "ids", "pin", "-k", "notes")
    assert result.exit_code == OUTSIDE_CONTRACT, result.output
    data = _json(result)
    assert data["pinned"] == []
    assert data["skipped"][0]["path"] == "a.md"
    assert "changed" in data["skipped"][0]["reason"]
    assert (root / "a.md").read_text() == "---\ntitle: Beta Renamed\ntype: note\n---\nx\n"


def test_a_symlink_to_a_file_outside_the_kb_is_reported_and_never_written(kb):
    root, config = kb
    outside = root.parent / "outside.md"
    outside.write_text("---\ntitle: Outside Thing\ntype: note\n---\nsecret\n")
    os.symlink(outside, root / "link.md")
    _write(root, {"a.md": "---\ntitle: Alpha\n---\n"})
    before = _json(_run(config, "ids", "missing", "-k", "notes"))
    assert [r["path"] for r in before["missing"]] == ["a.md"]
    [link] = before["skipped"]
    assert link["path"] == "link.md" and "symbolic link" in link["reason"]
    assert "outside-thing" in link["reason"]  # the id it has today, for a hand edit

    result = _run(config, "ids", "pin", "-k", "notes")
    assert result.exit_code == OUTSIDE_CONTRACT, result.output
    assert [p["path"] for p in _json(result)["pinned"]] == ["a.md"]
    assert outside.read_text() == "---\ntitle: Outside Thing\ntype: note\n---\nsecret\n"
    assert (root / "link.md").is_symlink()


def test_a_symlink_to_a_file_inside_the_kb_pins_the_target_once_and_no_false_collision(kb):
    root, config = kb
    _write(root, {"real.md": "---\ntitle: Real\n---\n"})
    os.symlink(root / "real.md", root / "alias.md")
    data = _json(_run(config, "ids", "missing", "-k", "notes"))
    assert data["collisions"] == []
    result = _run(config, "ids", "pin", "-k", "notes")
    assert [p["path"] for p in _json(result)["pinned"]] == ["real.md"]
    assert (root / "real.md").read_text() == "---\ntitle: Real\nid: real\n---\n"
    assert (root / "alias.md").is_symlink()


def test_files_under_a_symlinked_directory_are_never_written(kb, tmp_path):
    root, config = kb
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "far.md").write_text("---\ntitle: Far Away\n---\n")
    os.symlink(elsewhere, root / "linked")
    _write(root, {"a.md": "---\ntitle: A\n---\n"})
    result = _run(config, "ids", "pin", "-k", "notes")
    assert result.exit_code == 0, result.output
    assert (elsewhere / "far.md").read_text() == "---\ntitle: Far Away\n---\n"


def test_write_refusal_refuses_a_path_through_a_symlinked_directory(tmp_path):
    """``rglob`` does not descend into a symlinked directory today; the guard
    does not rely on that, so it is tested on the path directly."""
    from pyrite.services.id_pin_service import write_refusal

    root = tmp_path / "kb"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "far.md").write_text("---\ntitle: Far\n---\n")
    os.symlink(tmp_path / "elsewhere", root / "linked")
    reason = write_refusal(root, root / "linked" / "far.md")
    assert reason and "symlinked directory" in reason
    assert write_refusal(root, tmp_path / "elsewhere" / "far.md") == "outside the KB"


def test_a_hard_linked_file_is_reported_and_never_written(kb):
    root, config = kb
    outside = root.parent / "shared.md"
    outside.write_text("---\ntitle: Shared\n---\n")
    os.link(outside, root / "shared.md")
    data = _json(_run(config, "ids", "pin", "-k", "notes"))
    [row] = data["skipped"]
    assert row["path"] == "shared.md" and "hard links" in row["reason"]
    assert outside.read_text() == "---\ntitle: Shared\n---\n"


def test_a_fifo_named_like_an_entry_is_skipped_without_being_read(kb):
    root, config = kb
    os.mkfifo(root / "pipe.md")  # reading it would block forever
    _write(root, {"a.md": "---\ntitle: A\n---\n"})
    # --dry-run and the service's apply: a real `ids pin` then syncs the index,
    # and `index sync` itself blocks on a FIFO (#709, not this command's read).
    data = _json(_run(config, "ids", "pin", "-k", "notes", "--dry-run"))
    assert {r["path"]: r["reason"] for r in data["skipped"]} == {"pipe.md": "not a regular file"}
    assert [p["path"] for p in data["pinned"]] == ["a.md"]

    from pyrite.services import id_pin_service

    found = id_pin_service.scan(config.get_kb("notes"))
    done = id_pin_service.apply(found, id_pin_service.plan(found, {}))
    assert [p["path"] for p in done["pinned"]] == ["a.md"]


def test_a_kb_whose_root_is_a_symlink_still_pins(tmp_path):
    real = tmp_path / "real-kb"
    real.mkdir()
    (real / "a.md").write_text("---\ntitle: A\n---\n")
    os.symlink(real, tmp_path / "kb-link")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="notes", path=tmp_path / "kb-link", kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    result = _run(config, "ids", "pin", "-k", "notes")
    assert result.exit_code == 0, result.output
    assert (real / "a.md").read_text() == "---\ntitle: A\nid: a\n---\n"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        # the line takes the ending of the frontmatter line it follows
        (b"---\ntitle: Mixed\r\ntype: note\r\n---\r\nb\r\n", b"id: mixed\r\n"),
        (b"---\r\ntitle: Mixed\ntype: note\n---\nb\n", b"id: mixed\n"),
        # empty frontmatter: the opening line's ending
        (b"---\r\n---\r\nb\r\n", b"id: empty\r\n"),
    ],
)
def test_the_added_line_ends_like_the_line_before_it(content, expected):
    from pyrite.services.id_pin_service import insert_id_line

    text = content.decode()
    eid = expected.split(b" ")[1].strip().decode()
    new = insert_id_line(text, eid).encode()
    assert expected in new
    assert new.replace(expected, b"", 1) == content


@pytest.mark.parametrize(
    "content",
    [
        "---\n---\nbody\n",
        "---\n---",
        "---\r\n---\r\nb\r\n",
        "---\n\n---\nb\n",
        "---\n# only a comment\n---\nb\n",
    ],
)
def test_empty_frontmatter_is_missing_in_the_report_and_pinned_by_pin(kb, content):
    """`ids missing` and `ids pin` agree on an empty frontmatter: it is a
    file with no id (the hash fallback), and one line pins it."""
    root, config = kb
    _write(root, {"e.md": content})
    _commit(root)
    data = _json(_run(config, "ids", "missing", "-k", "notes"))
    assert [r["path"] for r in data["missing"]] == ["e.md"], data
    assert data["skipped"] == []
    result = _run(config, "ids", "pin", "-k", "notes")
    assert result.exit_code == 0, result.output
    assert _numstat(root) == {"e.md": ("1", "0")}


def test_read_commands_default_to_json(kb):
    """docs/json-contracts.md: read commands default to `--format json`
    (`get`, `list-entries`, `tags`, `kb list`, `qa validate` do)."""
    root, config = kb
    _write(root, {"a.md": "---\ntitle: A\n---\n"})
    assert "missing" in _json(_run(config, "ids", "missing", "-k", "notes"))
    assert _json(_run(config, "ids", "pin", "-k", "notes", "--dry-run"))["dry_run"] is True


# --- `ids missing` and `ids pin --dry-run` write nothing anywhere -----------


def _tree(top: Path) -> dict[str, tuple[int, int]]:
    """Every path under ``top`` (files, dirs, links) with size and mtime."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(top):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            st = os.lstat(p)
            out[str(p.relative_to(top))] = (st.st_size, st.st_mtime_ns)
    return out


def _pyrite(tmp_path: Path, cfg: Path, *args: str) -> subprocess.CompletedProcess:
    import sys

    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "PYRITE_CONFIG_DIR": str(cfg),
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    env.pop("PYRITE_DATA_DIR", None)
    return subprocess.run(
        ["pyrite", *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )


READ_ONLY_RUNS = [  # (argv, exit code)
    (("ids", "missing", "-k", "notes"), OUTSIDE_CONTRACT),
    (("ids", "missing", "-k", "notes", "--format", "text"), OUTSIDE_CONTRACT),
    (("ids", "pin", "-k", "notes", "--dry-run"), OUTSIDE_CONTRACT),
    (("ids", "pin", "-k", "notes", "--dry-run", "--rename", "b.md=beta-2"), 0),
]


def _notes_kb(tmp_path: Path) -> Path:
    kb = tmp_path / "notes"
    _write(kb, {"a.md": "---\ntitle: Alpha\n---\n", "b.md": "---\ntitle: Alpha\n---\n"})
    (tmp_path / "home").mkdir()
    return kb


def test_missing_and_dry_run_create_no_index_and_no_config_dir(tmp_path):
    """A KB named in config.yaml, no index yet: nothing appears, not even
    the index cli_context would have created (with -wal/-shm)."""
    kb = _notes_kb(tmp_path)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "config.yaml").write_text(
        f"knowledge_bases:\n- name: notes\n  path: {kb}\n"
        f"settings:\n  index_path: {tmp_path / 'idx' / 'index.db'}\n  auto_embed: false\n"
    )
    before = _tree(tmp_path)
    for args, code in READ_ONLY_RUNS:
        run = _pyrite(tmp_path, cfg, *args)
        assert run.returncode == code, (args, run.stdout, run.stderr)
        assert _tree(tmp_path) == before, args
    assert not (tmp_path / "idx").exists()


def test_missing_finds_a_kb_registered_in_the_index_without_touching_it(tmp_path):
    """`pyrite kb add` registers the KB in the index, not config.yaml; the
    read-only lookup opens the index immutable: no -wal/-shm, no change."""
    kb = _notes_kb(tmp_path)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "config.yaml").write_text(
        f"knowledge_bases: []\nsettings:\n  index_path: {tmp_path / 'index.db'}\n"
        "  auto_embed: false\n"
    )
    added = _pyrite(tmp_path, cfg, "kb", "add", str(kb), "--name", "notes")
    assert added.returncode == 0, added.stdout + added.stderr
    before = _tree(tmp_path)
    for args, code in READ_ONLY_RUNS:
        run = _pyrite(tmp_path, cfg, *args)
        assert run.returncode == code, (args, run.stdout, run.stderr)
        assert _tree(tmp_path) == before, args
    data = json.loads(_pyrite(tmp_path, cfg, "ids", "missing", "-k", "notes").stdout)
    assert {r["path"] for r in data["missing"]} == {"a.md", "b.md"}


def test_missing_with_no_config_dir_creates_none(tmp_path):
    _notes_kb(tmp_path)
    cfg = tmp_path / "no-such-config"
    before = _tree(tmp_path)
    run = _pyrite(tmp_path, cfg, "ids", "missing", "-k", "notes")
    assert run.returncode == 1, run.stdout + run.stderr
    assert _tree(tmp_path) == before
    assert not cfg.exists()


SAME = "---\ntitle: A\n---\n"


def _swap_for_link_to_identical_outside(root: Path) -> Path:
    """Replace a.md with a link to a file outside the KB holding the same
    bytes, so only the containment check (not the bytes check) can refuse."""
    outside = root.parent / "outside-same.md"
    outside.write_text(SAME)
    (root / "a.md").unlink()
    os.symlink(outside, root / "a.md")
    return outside


def test_a_file_swapped_for_a_link_after_the_scan_is_not_written_through(kb, monkeypatch):
    from pyrite.services import id_pin_service

    root, config = kb
    _write(root, {"a.md": SAME})
    real_plan = id_pin_service.plan
    swapped = {}

    def plan_then_swap(found, renames):
        result = real_plan(found, renames)
        swapped["outside"] = _swap_for_link_to_identical_outside(root)
        return result

    monkeypatch.setattr(id_pin_service, "plan", plan_then_swap)
    data = _json(_run(config, "ids", "pin", "-k", "notes"))
    assert data["pinned"] == [] and "symbolic link" in data["skipped"][0]["reason"]
    assert swapped["outside"].read_text() == SAME


def test_a_file_swapped_for_a_link_at_the_last_moment_is_not_written_through(kb, monkeypatch):
    from pyrite.services import id_pin_service

    root, config = kb
    _write(root, {"a.md": SAME})
    real = id_pin_service._write_pin
    swapped = {}

    def swap_first(root_, path, planned_original, new_text):
        swapped["outside"] = _swap_for_link_to_identical_outside(root)
        return real(root_, path, planned_original, new_text)

    monkeypatch.setattr(id_pin_service, "_write_pin", swap_first)
    data = _json(_run(config, "ids", "pin", "-k", "notes"))
    assert data["pinned"] == [] and "symbolic link" in data["skipped"][0]["reason"]
    assert swapped["outside"].read_text() == SAME


def test_write_refusal_refuses_a_path_that_climbs_out_of_the_kb(tmp_path):
    from pyrite.services.id_pin_service import write_refusal

    root = tmp_path / "kb"
    root.mkdir()
    (tmp_path / "x.md").write_text(SAME)
    assert write_refusal(root, root / ".." / "x.md") == "resolves outside the KB"


def test_a_dry_run_reports_a_file_swapped_for_a_link_after_the_scan(kb, monkeypatch):
    """--dry-run never reaches _write_pin, so its own check must refuse."""
    from pyrite.services import id_pin_service

    root, config = kb
    _write(root, {"a.md": SAME})
    real_plan = id_pin_service.plan

    def plan_then_swap(found, renames):
        result = real_plan(found, renames)
        _swap_for_link_to_identical_outside(root)
        return result

    monkeypatch.setattr(id_pin_service, "plan", plan_then_swap)
    data = _json(_run(config, "ids", "pin", "-k", "notes", "--dry-run"))
    assert data["pinned"] == [] and "symbolic link" in data["skipped"][0]["reason"]

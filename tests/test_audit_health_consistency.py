"""Health consumers must agree about unreadable paths and index defects."""

import json
import os

import pytest
from typer.testing import CliRunner

from pyrite.admin_cli import app as admin_app
from pyrite.cli import app
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.qa_service import QAService

KB = "test-kb"


def _world(cli_world):
    world = cli_world()
    folder = world.kb_path(KB)
    (folder / "a.md").write_text("---\nid: a\ntype: note\ntitle: A\n---\nBody\n", encoding="utf-8")
    world.index()
    return world, folder


def _assert_unhealthy(world, surface, *, unreadable=None):
    if surface == "qa":
        result = QAService(world.config, world.db).validate_kb(KB, readable_kbs=UNSCOPED)
        assert any(
            row["field"] == "index" and row["severity"] == "error" for row in result["issues"]
        ), result
    else:
        runner = CliRunner()
        result = runner.invoke(admin_app if surface == "admin" else app, ["index", "health"])
        assert result.exit_code == 1, (result.output, result.exception)
        if surface == "cli":
            report = json.loads(result.stdout)
            assert report["status"] == "unhealthy"
            if unreadable is not None:
                rows = report["unreadable_files"]
                assert sum(row["path"] == str(unreadable) for row in rows) == 1


@pytest.mark.parametrize("surface", ["cli", "admin", "qa"])
@pytest.mark.parametrize("kind", ["directory", "decode", "permission"])
def test_unreadable_new_path_is_never_healthy(cli_world, monkeypatch, surface, kind):
    world, folder = _world(cli_world)
    bad = folder / "unreadable.md"
    if kind == "directory":
        bad.mkdir()
    elif kind == "decode":
        bad.write_bytes(b"\xff")
    else:
        bad.write_text("not accessible", encoding="utf-8")
        real_stat = os.stat

        def denied(path, *args, **kwargs):
            if path == bad or path == str(bad):
                raise PermissionError("synthetic OS permission boundary")
            return real_stat(path, *args, **kwargs)

        monkeypatch.setattr(os, "stat", denied)
    _assert_unhealthy(world, surface, unreadable=bad)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks/FIFO required")
@pytest.mark.parametrize("surface", ["cli", "admin", "qa"])
@pytest.mark.parametrize("kind", ["loop", "fifo"])
def test_unreadable_posix_paths_do_not_pass_or_block(cli_world, surface, kind):
    world, folder = _world(cli_world)
    bad = folder / "unreadable.md"
    if kind == "fifo":
        os.mkfifo(bad)
    else:
        bad.symlink_to(bad.name)
    _assert_unhealthy(world, surface, unreadable=bad)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks required")
@pytest.mark.parametrize("surface", ["cli", "admin", "qa"])
def test_a_dangling_in_kb_link_is_a_warning_everywhere_never_unhealthy(cli_world, surface):
    """#1119 put a dangling link with the unreadable files (it was silently
    dropped before). #1262: a link inside the KB with nothing there holds no
    entry, so the index answers from the files exactly; it is reported on
    every surface as a dangling link, a warning, never unhealthy. A delete
    that keeps the user's link to the removed file leaves exactly this."""
    world, folder = _world(cli_world)
    bad = folder / "dangling.md"
    bad.symlink_to("missing-target.md")
    if surface == "qa":
        result = QAService(world.config, world.db).validate_kb(KB, readable_kbs=UNSCOPED)
        assert not any(
            row["field"] == "index" and row["severity"] == "error" for row in result["issues"]
        ), result
        return
    result = CliRunner().invoke(admin_app if surface == "admin" else app, ["index", "health"])
    assert result.exit_code == 0, (result.output, result.exception)
    if surface == "cli":
        report = json.loads(result.stdout)
        assert report["status"] == "warning"
        assert report["unreadable_files"] == []
        assert [row["path"] for row in report["dangling_links"]] == [
            bad.relative_to(world.config.knowledge_bases[0].path).as_posix()
        ]
    else:
        assert "warnings" in result.output


@pytest.mark.parametrize("surface", ["cli", "admin", "qa"])
def test_duplicate_ids_are_unhealthy_everywhere(cli_world, surface):
    world, folder = _world(cli_world)
    (folder / "copy.md").write_bytes((folder / "a.md").read_bytes())
    _assert_unhealthy(world, surface)


def test_an_orphan_alone_is_a_qa_index_error(cli_world):
    world, _ = _world(cli_world)
    conn = world.db._raw_conn
    columns = [row[1] for row in conn.execute("PRAGMA table_info(entry)")]
    select = ", ".join("'ghost'" if col == "id" else col for col in columns)
    conn.execute(f"INSERT INTO entry ({', '.join(columns)}) SELECT {select} FROM entry")
    conn.commit()
    _assert_unhealthy(world, "qa")


def test_content_hash_change_alone_is_unhealthy_for_admin(cli_world):
    world, folder = _world(cli_world)
    target = folder / "a.md"
    before = target.stat()
    target.write_bytes(target.read_bytes().replace(b"Body", b"Edit"))
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    _assert_unhealthy(world, "admin")


@pytest.mark.parametrize("surface", ["cli", "admin", "qa"])
@pytest.mark.control(reason="A readable unchanged file must remain healthy in every consumer")
def test_unchanged_regular_file_remains_healthy(cli_world, surface):
    world, _ = _world(cli_world)
    if surface == "qa":
        result = QAService(world.config, world.db).validate_kb(KB, readable_kbs=UNSCOPED)
        assert not any(row.get("field") == "index" for row in result["issues"])
    else:
        result = CliRunner().invoke(admin_app if surface == "admin" else app, ["index", "health"])
        assert result.exit_code == 0, result.output


@pytest.mark.parametrize("enforce", [True, False])
def test_admin_preserves_enforced_enum_severity(cli_world, enforce):
    world = cli_world()
    folder = world.kb_path(KB)
    (folder / "kb.yaml").write_text(
        f"name: {KB}\nvalidation:\n  enforce_enums: {str(enforce).lower()}\n"
        "types:\n  note:\n    fields:\n      region:\n        type: select\n"
        "        values: [north, south]\n",
        encoding="utf-8",
    )
    (folder / "a.md").write_text(
        "---\nid: a\ntype: note\ntitle: A\nregion: unknown\n---\nBody\n",
        encoding="utf-8",
    )
    world.index()
    cli = CliRunner().invoke(app, ["index", "health"])
    report = json.loads(cli.stdout)
    assert report["status"] == ("unhealthy" if enforce else "warning"), report
    admin = CliRunner().invoke(admin_app, ["index", "health"])
    assert admin.exit_code == (1 if enforce else 0), admin.output
    assert "Index is healthy" not in admin.output
    if enforce:
        _assert_unhealthy(world, "qa")
        findings = QAService(world.config, world.db).validate_kb(KB, readable_kbs=UNSCOPED)
        index_issues = [row for row in findings["issues"] if row.get("field") == "index"]
        assert index_issues[0]["rule"] == "index_unhealthy"
        assert "correct the reported values" in index_issues[0]["message"]


def test_shared_verdict_retains_independent_warning_flag():
    from pyrite.services.index_health import health_verdict

    findings = {"duplicates": [{"id": "x"}], "broken_links": 2}
    verdict = health_verdict(findings)
    assert verdict["is_unhealthy"] and verdict["has_warning"] and verdict["status"] == "unhealthy"
    assert findings == {"duplicates": [{"id": "x"}], "broken_links": 2}
    assert health_verdict({"off_list_values": [{"severity": "info"}]})["status"] == "healthy"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink required")
@pytest.mark.control(reason="Symlinks targeting regular files remain valid indexed entries")
def test_regular_file_symlink_remains_readable(cli_world):
    world = cli_world()
    folder = world.kb_path(KB)
    target = folder / "target.txt"
    target.write_text("---\nid: a\ntype: note\ntitle: A\n---\nBody\n", encoding="utf-8")
    (folder / "a.md").symlink_to(target.name)
    world.index()
    cli = CliRunner().invoke(app, ["index", "health"])
    assert cli.exit_code == 0, cli.output
    assert json.loads(cli.stdout)["unreadable_files"] == []


@pytest.mark.parametrize(
    "surface",
    [
        "cli",
        "admin",
        pytest.param(
            "qa",
            marks=pytest.mark.control(
                reason="QA reports only index errors; it must not turn this warning into one"
            ),
        ),
    ],
)
def test_an_unlisted_entry_file_is_a_warning_everywhere_never_unhealthy(cli_world, surface):
    """#1552: an entry file an earlier version wrote where the walk does not
    list it (a create titled README wrote `readme.md`) is named under
    `unlisted_entry_files`, a warning on every surface: the index answers
    from the files it lists, and the user should know the file is there."""
    world, folder = _world(cli_world)
    (folder / "notes").mkdir()
    (folder / "notes" / "readme.md").write_text(
        "---\nid: readme\ntype: note\ntitle: README\n---\nBody\n", encoding="utf-8"
    )
    if surface == "qa":
        result = QAService(world.config, world.db).validate_kb(KB, readable_kbs=UNSCOPED)
        assert not any(
            row["field"] == "index" and row["severity"] == "error" for row in result["issues"]
        ), result
        return
    result = CliRunner().invoke(admin_app if surface == "admin" else app, ["index", "health"])
    assert result.exit_code == 0, (result.output, result.exception)
    if surface == "admin":
        assert "Index has warnings" in result.output, result.output
    if surface == "cli":
        report = json.loads(result.stdout)
        assert report["status"] == "warning"
        assert report["unlisted_entry_files"] == [
            {"kb": KB, "path": "notes/readme.md", "reason": "readme"}
        ]

"""`pyrite create` and `pyrite update` refuse two body sources (#1780).

Property: given more than one of --body, --body-file (a path or `-`) and
--stdin, create and update refuse with a usage error (exit 2, USAGE_ERROR)
naming the sources given, before any KB is opened or anything is written --
the check `task create`/`task update` already had (#1687), reused through
``_BodySourceCommand``. One source behaves exactly as before. The conflict
tests are the guard against the old behaviour returning: it exited 0 and
silently wrote one of the sources (stdin, then the file, then --body).
"""

import json

import pytest


@pytest.fixture
def world(cli_world):
    return cli_world()


def _error(result):
    return json.loads(result.stdout)


def _note_file(world, entry_id):
    return world.config.get_kb("test-kb").path / "notes" / f"{entry_id}.md"


def _create(world, title, *extra, stdin=None):
    return world.cli("create", "-k", "test-kb", "-t", "note", "--title", title, *extra, input=stdin)


CONFLICTS = {
    # name: (extra argv builder, stdin text, flags the refusal must name)
    "body+file": (
        lambda p: ["--body", "inline", "--body-file", str(p)],
        None,
        ("--body", "--body-file"),
    ),
    "body+stdin": (
        lambda p: ["--body", "inline", "--stdin"],
        "from stdin",
        ("--body", "--stdin"),
    ),
    "file+stdin": (
        lambda p: ["--body-file", str(p), "--stdin"],
        "from stdin",
        ("--body-file", "--stdin"),
    ),
    "dash+body": (
        lambda p: ["--body", "inline", "--body-file", "-"],
        "from stdin",
        ("--body", "--stdin"),
    ),
    "triple": (
        lambda p: ["--body", "inline", "--body-file", str(p), "--stdin"],
        "from stdin",
        ("--body", "--body-file", "--stdin"),
    ),
}


@pytest.mark.parametrize("conflict", sorted(CONFLICTS))
def test_create_with_two_body_sources_is_refused(world, tmp_path, conflict):
    path = tmp_path / "body.md"
    path.write_text("from file")
    build, stdin, flags = CONFLICTS[conflict]
    extra = build(path)
    r = _create(world, "fresh-note", *extra, stdin=stdin)
    assert r.exit_code == 2, r.output
    err = _error(r)
    assert err["error_code"] == "USAGE_ERROR"
    assert "one body source" in err["error"]
    for flag in flags:
        assert flag in err["error"]
    assert not _note_file(world, "fresh-note").exists()


@pytest.mark.parametrize("conflict", sorted(CONFLICTS))
def test_update_with_two_body_sources_is_refused(world, tmp_path, conflict):
    made = _create(world, "target-note", "--body", "original")
    assert made.exit_code == 0, made.output
    entry_id = json.loads(made.stdout)["entry_id"]
    before = _note_file(world, entry_id).read_bytes()
    path = tmp_path / "body.md"
    path.write_text("from file")
    build, stdin, flags = CONFLICTS[conflict]
    extra = build(path)
    r = world.cli("update", entry_id, "-k", "test-kb", *extra, input=stdin)
    assert r.exit_code == 2, r.output
    err = _error(r)
    assert err["error_code"] == "USAGE_ERROR"
    assert "one body source" in err["error"]
    for flag in flags:
        assert flag in err["error"]
    assert _note_file(world, entry_id).read_bytes() == before


@pytest.mark.parametrize("command", ["create", "update"])
def test_two_body_sources_refuse_before_any_kb_is_opened(world, tmp_path, command):
    """The refusal is parse-time: an unknown KB still answers USAGE_ERROR,
    not KB_NOT_FOUND, so nothing was looked up or written anywhere."""
    path = tmp_path / "body.md"
    path.write_text("from file")
    argv = (
        ["create", "-k", "no-such-kb", "-t", "note", "--title", "fresh-note"]
        if command == "create"
        else ["update", "some-id", "-k", "no-such-kb"]
    )
    r = world.cli(*argv, "--body", "inline", "--body-file", str(path))
    assert r.exit_code == 2, r.output
    assert _error(r)["error_code"] == "USAGE_ERROR"


# One source each. `--body-file -` is not a single source here: the entry
# commands read `-` as a literal path today (only the task commands read it
# as stdin), and one source behaving exactly as today keeps that. The dash
# still counts as a source when combined with another (CONFLICTS above),
# because the refusal is judged from the flags, before any file is read.
SOURCES = {
    "body": lambda p: (["--body", "the body text"], None),
    "body-file": lambda p: (["--body-file", str(p)], None),
    "stdin-flag": lambda p: (["--stdin"], "the body text"),
}


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_create_with_one_body_source_still_writes(world, tmp_path, source):
    path = tmp_path / "body.md"
    path.write_text("the body text")
    extra, stdin = SOURCES[source](path)
    r = _create(world, f"one-{source}", *extra, stdin=stdin)
    assert r.exit_code == 0, r.output
    entry_id = json.loads(r.stdout)["entry_id"]
    assert "the body text" in _note_file(world, entry_id).read_text()


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_update_with_one_body_source_still_writes(world, tmp_path, source):
    made = _create(world, f"up-{source}", "--body", "old text")
    assert made.exit_code == 0, made.output
    entry_id = json.loads(made.stdout)["entry_id"]
    path = tmp_path / "body.md"
    path.write_text("the body text")
    extra, stdin = SOURCES[source](path)
    r = world.cli("update", entry_id, "-k", "test-kb", *extra, input=stdin)
    assert r.exit_code == 0, r.output
    text = _note_file(world, entry_id).read_text()
    assert "the body text" in text
    assert "old text" not in text

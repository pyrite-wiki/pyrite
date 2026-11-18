"""`task create` and `task update` take --body-file and --stdin (#1687).

Property: for the same text, --body, --body-file, --stdin and `-` write
byte-identical bodies, on create and on update. Bad sources are refused as
usage errors (exit 2) before any write.
"""

import json

import pytest

SHAPES = {
    "plain": b"Write the outline.\n\nThen the draft.\n",
    "shell-hostile": b"Run `make test` with $HOME and \"quotes\" and 'single'.\n",
    "no-trailing-newline": b"just text",
    "dashes-not-frontmatter": b"intro\n---\nnot frontmatter\n---\n",
}
SOURCES = ["body", "body-file", "stdin-flag", "stdin-dash"]


def _task_file(world, task_id):
    return world.config.get_kb("test-kb").path / "tasks" / f"{task_id}.md"


def _body_of(world, task_id) -> bytes:
    raw = _task_file(world, task_id).read_bytes()
    assert raw.startswith(b"---\n")
    return raw.split(b"\n---\n", 1)[1]


def _args(source, data: bytes, tmp_path):
    """(extra argv, stdin text) for one body source carrying ``data``."""
    text = data.decode("utf-8")
    if source == "body":
        return ["--body", text], None
    path = tmp_path / "body.md"
    path.write_bytes(data)
    if source == "body-file":
        return ["--body-file", str(path)], None
    if source == "stdin-flag":
        return ["--stdin"], text
    return ["--body-file", "-"], text


def _run(world, argv, stdin):
    # CliRunner decodes stdin as text; newline handling is tested via files.
    return world.cli(*argv, input=stdin)


@pytest.fixture
def world(cli_world):
    return cli_world()


def _error(result):
    return json.loads(result.stdout)


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_create_sources_write_identical_bodies(world, tmp_path, shape):
    bodies = {}
    for source in SOURCES:
        extra, stdin = _args(source, SHAPES[shape], tmp_path)
        title = f"t-{source}"
        r = _run(world, ["task", "create", title, "-k", "test-kb", *extra], stdin)
        assert r.exit_code == 0, r.output
        bodies[source] = _body_of(world, json.loads(r.stdout)["entry_id"])
    assert len(set(bodies.values())) == 1, bodies


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_update_sources_write_identical_bodies(world, tmp_path, shape):
    bodies = {}
    for source in SOURCES:
        title = f"u-{source}"
        r = world.cli("task", "create", title, "-k", "test-kb", "--body", "old")
        task_id = json.loads(r.stdout)["entry_id"]
        extra, stdin = _args(source, SHAPES[shape], tmp_path)
        r = _run(world, ["task", "update", task_id, "-k", "test-kb", *extra], stdin)
        assert r.exit_code == 0, r.output
        bodies[source] = _body_of(world, task_id)
    assert len(set(bodies.values())) == 1, bodies
    assert b"old" not in next(iter(bodies.values()))


def test_crlf_file_is_read_as_pyrite_create_reads_it(world, tmp_path):
    """The reader is shared, so a CRLF file lands as `create --body-file` lands it
    (read_text's universal newlines); file, `-` and --stdin agree with each other."""
    data = b"line one\r\nline two\r\n"
    path = tmp_path / "crlf.md"
    path.write_bytes(data)
    ref = world.cli(
        "create", "-k", "test-kb", "-t", "note", "--title", "ref", "--body-file", str(path)
    )
    assert ref.exit_code == 0, ref.output
    ref_raw = (world.config.get_kb("test-kb").path / "notes" / "ref.md").read_bytes()
    expected = ref_raw.split(b"\n---\n", 1)[1].strip()
    got = {}
    for source in ("body-file", "stdin-flag", "stdin-dash"):
        extra, stdin = _args(source, data, tmp_path)
        r = _run(world, ["task", "create", f"crlf-{source}", "-k", "test-kb", *extra], stdin)
        assert r.exit_code == 0, r.output
        got[source] = _body_of(world, json.loads(r.stdout)["entry_id"]).strip()
    assert set(got.values()) == {expected}, (got, expected)


def test_empty_file_matches_empty_body(world, tmp_path):
    path = tmp_path / "empty.md"
    path.write_bytes(b"")
    a = world.cli("task", "create", "empty-file", "-k", "test-kb", "--body-file", str(path))
    b = world.cli("task", "create", "empty-inline", "-k", "test-kb", "--body", "")
    assert a.exit_code == 0 and b.exit_code == 0, (a.output, b.output)
    assert _body_of(world, "empty-file") == _body_of(world, "empty-inline")


def test_empty_file_on_update_clears_body(world, tmp_path):
    world.cli("task", "create", "clear-me", "-k", "test-kb", "--body", "something")
    path = tmp_path / "empty.md"
    path.write_bytes(b"")
    r = world.cli("task", "update", "clear-me", "-k", "test-kb", "--body-file", str(path))
    assert r.exit_code == 0, r.output
    assert b"something" not in _body_of(world, "clear-me")


def test_non_utf8_is_refused_as_create_refuses(world, tmp_path):
    path = tmp_path / "bad.md"
    path.write_bytes(b"ab\xff\xfe")
    ref = world.cli(
        "create", "-k", "test-kb", "-t", "note", "--title", "x", "--body-file", str(path)
    )
    r = world.cli("task", "create", "bad-bytes", "-k", "test-kb", "--body-file", str(path))
    assert ref.exit_code != 0
    assert r.exit_code == ref.exit_code
    assert _error(r)["error_code"] == _error(ref)["error_code"]
    assert not _task_file(world, "bad-bytes").exists()


def test_update_non_utf8_leaves_task_unchanged(world, tmp_path):
    world.cli("task", "create", "keep", "-k", "test-kb", "--body", "original")
    before = _task_file(world, "keep").read_bytes()
    path = tmp_path / "bad.md"
    path.write_bytes(b"ab\xff\xfe")
    r = world.cli("task", "update", "keep", "-k", "test-kb", "--body-file", str(path))
    assert r.exit_code != 0
    assert _task_file(world, "keep").read_bytes() == before


@pytest.mark.parametrize("command", ["create", "update"])
@pytest.mark.parametrize("bad", ["missing", "directory"])
def test_bad_path_is_a_usage_refusal(world, tmp_path, command, bad):
    world.cli("task", "create", "target", "-k", "test-kb")
    path = tmp_path / "nope.md" if bad == "missing" else tmp_path
    argv = (
        ["task", "create", "fresh", "-k", "test-kb"]
        if command == "create"
        else ["task", "update", "target", "-k", "test-kb"]
    )
    r = world.cli(*argv, "--body-file", str(path))
    assert r.exit_code == 2, r.output
    err = _error(r)
    assert err["outcome"] == "error"
    assert err["error_code"] == "USAGE_ERROR"
    assert "--body-file" in err["error"]
    assert str(path) in err["error"]
    assert err["retryable"] is False
    if command == "create":
        assert not _task_file(world, "fresh").exists()


CONFLICTS = {
    "body+file": lambda p: (["--body", "x", "--body-file", str(p)], None),
    "file+stdin": lambda p: (["--body-file", str(p), "--stdin"], "x"),
    "body+stdin": lambda p: (["--body", "x", "--stdin"], "x"),
    "dash+body": lambda p: (["--body", "x", "--body-file", "-"], "x"),
}


@pytest.mark.parametrize("conflict", sorted(CONFLICTS))
@pytest.mark.parametrize("command", ["create", "update"])
def test_two_body_sources_are_refused_before_any_write(world, tmp_path, command, conflict):
    world.cli("task", "create", "target", "-k", "test-kb", "--body", "original")
    path = tmp_path / "f.md"
    path.write_text("from file")
    extra, stdin = CONFLICTS[conflict](path)
    before = _task_file(world, "target").read_bytes()
    argv = (
        ["task", "create", "fresh", "-k", "test-kb"]
        if command == "create"
        else ["task", "update", "target", "-k", "test-kb", "--priority", "9"]
    )
    r = _run(world, [*argv, *extra], stdin)
    assert r.exit_code == 2, r.output
    err = _error(r)
    assert err["error_code"] == "USAGE_ERROR"
    assert "one body source" in err["error"]
    assert not _task_file(world, "fresh").exists()
    assert _task_file(world, "target").read_bytes() == before


FRONTMATTER = "---\nstatus: done\npriority: 1\n---\nReal body\n"


@pytest.mark.parametrize("source", ["body-file", "stdin-flag", "stdin-dash"])
def test_frontmatter_body_is_refused_on_create(world, tmp_path, source):
    extra, stdin = _args(source, FRONTMATTER.encode(), tmp_path)
    r = _run(world, ["task", "create", "fm", "-k", "test-kb", *extra], stdin)
    assert r.exit_code == 2, r.output
    err = _error(r)
    assert err["error_code"] == "USAGE_ERROR"
    assert "pyrite create -t task --body-file" in err["error"]
    assert not _task_file(world, "fm").exists()


@pytest.mark.parametrize("source", ["body-file", "stdin-flag", "stdin-dash"])
def test_frontmatter_body_is_refused_on_update(world, tmp_path, source):
    world.cli("task", "create", "fm-up", "-k", "test-kb", "--body", "original")
    before = _task_file(world, "fm-up").read_bytes()
    extra, stdin = _args(source, FRONTMATTER.encode(), tmp_path)
    r = _run(world, ["task", "update", "fm-up", "-k", "test-kb", *extra], stdin)
    assert r.exit_code == 2, r.output
    assert "pyrite create -t task --body-file" in _error(r)["error"]
    assert _task_file(world, "fm-up").read_bytes() == before


def test_update_with_only_body_file_updates_the_body(world, tmp_path):
    world.cli("task", "create", "only-body", "-k", "test-kb", "--body", "old")
    path = tmp_path / "new.md"
    path.write_text("new text\n")
    r = world.cli("task", "update", "only-body", "-k", "test-kb", "--body-file", str(path))
    assert r.exit_code == 0, r.output
    assert _body_of(world, "only-body").strip() == b"new text"


def test_update_nothing_message_lists_body_options(world):
    world.cli("task", "create", "nothing", "-k", "test-kb")
    r = world.cli("task", "update", "nothing", "-k", "test-kb")
    assert r.exit_code != 0
    msg = _error(r)["error"]
    for option in ("--body", "--body-file", "--stdin"):
        assert option in msg

"""ADR-0034 rule 2 on the CLI -- the third untrusted input surface (#230).

Most CLI callers today are agents (ADR-0034 rule 5 says so), so the loss rule 2
prevents -- read bounded, edit what came back, write it back, lose the rest --
is as reachable here as over MCP. The CLI is also where a marker is most likely
to be *persisted* before being replayed: an agent saves a bounded read to a
scratch file, and the file carries `body_truncated` into the next write.

Three write paths take a body: `pyrite import`, `pyrite create` and
`pyrite update`. Refusals exit 1 with the `cli_error` envelope
(docs/json-contracts.md "Exit codes (CLI)").
"""

import contextlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

runner = CliRunner()

BIG_CHUNK = "A" * 8000


@pytest.fixture
def cli_env():
    """A writable single-KB environment for CLI write tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"
        kb_path = tmpdir / "test-kb"
        kb_path.mkdir()

        kb_config = KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)
        config = PyriteConfig(knowledge_bases=[kb_config], settings=Settings(index_path=db_path))

        db = PyriteDB(db_path)
        IndexManager(db, config).index_all()
        db.close()

        yield {"config": config, "tmpdir": tmpdir, "kb_path": kb_path}


@contextlib.contextmanager
def _patched(env):
    with patch("pyrite.cli.load_config", return_value=env["config"]):
        with patch("pyrite.cli.context.load_config", return_value=env["config"]):
            yield


def _entry_files(env):
    return sorted(env["kb_path"].rglob("*.md"))


def _read_entry(env, stem):
    for path in _entry_files(env):
        if path.stem == stem:
            return path.read_text(encoding="utf-8")
    raise AssertionError(f"no entry file named {stem} in {_entry_files(env)}")


def _assert_cli_refusal(result):
    """Exit 1, and the message teaches -- json-contracts.md's exit-code contract."""
    assert result.exit_code == 1, result.output
    out = result.output
    assert "body_truncated" in out, out
    # Name how to get the whole body, and how to write other fields instead.
    assert "body_offset" in out or "kb_read_body" in out, out


# ---------------------------------------------------------------------------
# `pyrite import` -- per record, clean siblings still imported
# ---------------------------------------------------------------------------


def test_cli_import_json_refuses_marked_record_and_imports_the_rest(cli_env):
    payload = {
        "entries": [
            {"title": "CLI Clean One", "entry_type": "note", "body": "fine"},
            {
                "title": "CLI Marked",
                "entry_type": "note",
                "body": BIG_CHUNK,
                "body_truncated": True,
                "body_length": 20000,
            },
            {"title": "CLI Clean Two", "entry_type": "note", "body": "also fine"},
        ]
    }
    src = cli_env["tmpdir"] / "entries.json"
    src.write_text(json.dumps(payload))

    with _patched(cli_env):
        result = runner.invoke(app, ["import", str(src), "--kb", "test-kb"])

    assert "body_truncated" in result.output, result.output
    stems = {p.stem for p in _entry_files(cli_env)}
    assert "cli-clean-one" in stems
    assert "cli-clean-two" in stems
    assert "cli-marked" not in stems


def test_cli_import_yaml_marker_never_reaches_the_write(cli_env):
    """YAML's importer strips the marker, so the record imports -- safely.

    `pyrite import` accepts JSON, YAML, and Markdown. The yaml importer's key whitelist
    drops `body_truncated` before the write, so there is nothing for the guard
    to refuse: the record is written, and the marker is not persisted. The
    audit test below pins that stripping, and the guard runs on every record
    regardless of format, so the day yaml starts carrying the key it is caught.
    """
    src = cli_env["tmpdir"] / "entries.yaml"
    src.write_text(
        "entries:\n"
        '  - title: "YAML Marked"\n'
        "    entry_type: note\n"
        '    body: "partial text"\n'
        "    body_truncated: true\n"
        "    body_length: 20000\n"
    )

    with _patched(cli_env):
        result = runner.invoke(app, ["import", str(src), "--kb", "test-kb"])

    assert result.exit_code == 0, result.output
    text = _read_entry(cli_env, "yaml-marked")
    assert "body_truncated" not in text, text


def test_cli_import_without_markers_still_works(cli_env):
    """The negative control: a normal import is unaffected."""
    payload = {
        "entries": [
            {"title": "Plain One", "entry_type": "note", "body": "a"},
            {"title": "Plain Two", "entry_type": "note", "body": "b"},
        ]
    }
    src = cli_env["tmpdir"] / "plain.json"
    src.write_text(json.dumps(payload))

    with _patched(cli_env):
        result = runner.invoke(app, ["import", str(src), "--kb", "test-kb"])

    assert result.exit_code == 0, result.output
    assert "Imported 2 entries" in result.output
    stems = {p.stem for p in _entry_files(cli_env)}
    assert {"plain-one", "plain-two"} <= stems


def test_cli_import_false_marker_is_allowed_and_not_persisted(cli_env):
    """`body_truncated: false` imports, and does not become frontmatter."""
    payload = {
        "entries": [
            {
                "title": "False Marker Import",
                "entry_type": "note",
                "body": "whole body",
                "body_truncated": False,
                "body_length": 10,
            }
        ]
    }
    src = cli_env["tmpdir"] / "false.json"
    src.write_text(json.dumps(payload))

    with _patched(cli_env):
        result = runner.invoke(app, ["import", str(src), "--kb", "test-kb"])

    assert result.exit_code == 0, result.output
    text = _read_entry(cli_env, "false-marker-import")
    assert "body_truncated" not in text, text
    assert "body_length" not in text, text


# ---------------------------------------------------------------------------
# `pyrite create`
# ---------------------------------------------------------------------------


def test_cli_create_with_marker_field_is_refused(cli_env):
    """`-f body_truncated=true` alongside a body is refused."""
    with _patched(cli_env):
        result = runner.invoke(
            app,
            [
                "create",
                "--kb",
                "test-kb",
                "--type",
                "note",
                "--title",
                "Field Marker Create",
                "--body",
                BIG_CHUNK,
                "--field",
                "body_truncated=true",
            ],
        )

    _assert_cli_refusal(result)
    stems = {p.stem for p in _entry_files(cli_env)}
    assert "field-marker-create" not in stems


def test_cli_create_body_file_with_marker_frontmatter_is_refused(cli_env):
    """The scratch-file path: a saved bounded read carries the marker in.

    `pyrite create --body-file` parses the file's YAML frontmatter into the
    entry's extra fields, so a file written straight from a bounded read hands
    `body_truncated: true` to the write.
    """
    scratch = cli_env["tmpdir"] / "bounded-read.md"
    scratch.write_text(
        "---\n"
        "title: Saved Bounded Read\n"
        "body_truncated: true\n"
        "body_length: 20000\n"
        "body_offset: 0\n"
        "body_chunk_size: 8000\n"
        "---\n\n" + BIG_CHUNK
    )

    with _patched(cli_env):
        result = runner.invoke(
            app,
            [
                "create",
                "--kb",
                "test-kb",
                "--type",
                "note",
                "--title",
                "Saved Bounded Read",
                "--body-file",
                str(scratch),
            ],
        )

    _assert_cli_refusal(result)
    stems = {p.stem for p in _entry_files(cli_env)}
    assert "saved-bounded-read" not in stems


def test_cli_create_without_marker_still_works(cli_env):
    with _patched(cli_env):
        result = runner.invoke(
            app,
            [
                "create",
                "--kb",
                "test-kb",
                "--type",
                "note",
                "--title",
                "Ordinary Create",
                "--body",
                "an ordinary body",
            ],
        )

    assert result.exit_code == 0, result.output
    text = _read_entry(cli_env, "ordinary-create")
    assert "an ordinary body" in text


def test_cli_create_false_marker_is_allowed_and_not_persisted(cli_env):
    """An explicit false marker creates, and is not written as frontmatter."""
    with _patched(cli_env):
        result = runner.invoke(
            app,
            [
                "create",
                "--kb",
                "test-kb",
                "--type",
                "note",
                "--title",
                "False Marker Create CLI",
                "--body",
                "whole body",
                "--field",
                "body_truncated=false",
            ],
        )

    assert result.exit_code == 0, result.output
    text = _read_entry(cli_env, "false-marker-create-cli")
    assert "body_truncated" not in text, text


# ---------------------------------------------------------------------------
# `pyrite update`
# ---------------------------------------------------------------------------


def _seed(cli_env, title, body):
    with _patched(cli_env):
        result = runner.invoke(
            app,
            ["create", "--kb", "test-kb", "--type", "note", "--title", title, "--body", body],
        )
    assert result.exit_code == 0, result.output
    return title.lower().replace(" ", "-")


def test_cli_update_with_marker_field_is_refused_and_body_survives(cli_env):
    """The round trip that matters: the stored body must survive the refusal."""
    entry_id = _seed(cli_env, "Update Target", "the whole original body")

    with _patched(cli_env):
        result = runner.invoke(
            app,
            [
                "update",
                entry_id,
                "--kb",
                "test-kb",
                "--body",
                BIG_CHUNK,
                "--field",
                "body_truncated=true",
            ],
        )

    _assert_cli_refusal(result)
    assert "the whole original body" in _read_entry(cli_env, entry_id)


def test_cli_update_body_file_treats_frontmatter_as_literal_body_text(cli_env):
    """`update --body-file` does not parse frontmatter, so no marker arrives.

    Unlike `create --body-file`, which lifts a file's YAML frontmatter into the
    entry's fields, `update --body-file` takes the file verbatim as body text.
    A marker in such a file is therefore body *content*, not a write argument,
    and refusing on it would be the heuristic "this body looks cut off"
    detection ADR-0034 and the backlog item both rule out -- the marker is the
    contract, not the prose.

    Pinned because it is the one asymmetry between the two commands, and
    because the safe path for this shape is `--field body_truncated=true`,
    which IS refused (see the test above).
    """
    entry_id = _seed(cli_env, "Update From File", "the whole original body")

    scratch = cli_env["tmpdir"] / "read.md"
    scratch.write_text("---\nbody_truncated: true\nbody_length: 20000\n---\n\nreplacement text")

    with _patched(cli_env):
        result = runner.invoke(
            app, ["update", entry_id, "--kb", "test-kb", "--body-file", str(scratch)]
        )

    assert result.exit_code == 0, result.output
    stored = _read_entry(cli_env, entry_id)
    assert "replacement text" in stored


def test_cli_update_marker_without_a_body_is_allowed(cli_env):
    """A metadata-only update carrying the marker writes no body -- allowed."""
    entry_id = _seed(cli_env, "Metadata Only Update", "the whole original body")

    with _patched(cli_env):
        result = runner.invoke(
            app,
            [
                "update",
                entry_id,
                "--kb",
                "test-kb",
                "--importance",
                "7",
                "--field",
                "body_truncated=true",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "the whole original body" in _read_entry(cli_env, entry_id)


def test_cli_update_without_marker_still_works(cli_env):
    entry_id = _seed(cli_env, "Ordinary Update", "before")

    with _patched(cli_env):
        result = runner.invoke(
            app, ["update", entry_id, "--kb", "test-kb", "--body", "after the rewrite"]
        )

    assert result.exit_code == 0, result.output
    assert "after the rewrite" in _read_entry(cli_env, entry_id)


# ---------------------------------------------------------------------------
# The importer audit (#230): which formats carry the marker at all?
# ---------------------------------------------------------------------------


def test_importer_marker_passthrough_is_pinned():
    """Pin which importers carry ADR-0034's marker through to the caller.

    The audit #230 asked for, measured rather than read off the source, and
    kept executable so the answer cannot rot:

    - **json** carries it (this branch's `MARKER_KEYS` passthrough).
    - **markdown** carries it, and always has: `_parse_single_md` splats every
      frontmatter key it does not consume, so a saved bounded read has been an
      unguarded write input on REST's `/entries/import` since before this
      branch. That is why the refusal must live at the import *endpoints*, per
      record, rather than in the importers.
    - **yaml** and **csv** strip it through their key whitelists, so a marker
      in those formats never reaches a write.

    `pyrite import` accepts JSON, YAML, and Markdown; REST `/entries/import` also accepts
    markdown and csv. Both call the guard on every record regardless of format,
    so a whitelist that gains the key later is already covered.
    """
    from pyrite.formats.importers.csv_importer import import_csv
    from pyrite.formats.importers.json_importer import import_json
    from pyrite.formats.importers.markdown_importer import import_markdown
    from pyrite.formats.importers.yaml_importer import import_yaml

    marked = {"title": "T", "body": "partial", "body_truncated": True}

    # Carries the marker.
    assert import_json(json.dumps([marked]))[0].get("body_truncated") is True
    assert (
        import_markdown("---\ntitle: T\nbody_truncated: true\n---\n\npartial")[0].get(
            "body_truncated"
        )
        is True
    )

    # Strips the marker (whitelist importers).
    assert (
        "body_truncated"
        not in import_yaml("entries:\n  - title: T\n    body: partial\n    body_truncated: true\n")[
            0
        ]
    )
    assert "body_truncated" not in import_csv("title,body,body_truncated\nT,partial,true\n")[0]

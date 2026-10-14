"""#820: report possible frontmatter leaks, and keep body text through the CLI."""

import json
from datetime import date
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.utils.frontmatter import Frontmatter, split_frontmatter
from pyrite.utils.frontmatter import leaked_frontmatter_line


@pytest.fixture
def cli_env(tmp_path):
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(
        "name: notes\nkb_type: generic\ntypes:\n  note:\n    description: A note\n",
        encoding="utf-8",
    )
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="notes", path=kb_path)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    runner = CliRunner()

    def invoke(args):
        with patch("pyrite.cli.context.load_config", return_value=config):
            result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        return result

    return invoke, kb_path


def create_note(cli_env, body):
    invoke, kb_path = cli_env
    invoke(["create", "-k", "notes", "-t", "note", "--title", "Probe", "-b", body])
    path = next(kb_path.rglob("*.md"))
    return path


def body_bytes(path):
    """Keep whitespace and line endings: compare the actual file body bytes."""
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    split = split_frontmatter(text)
    assert isinstance(split, Frontmatter)
    offset = len(text[: split.close_end].encode("utf-8"))
    return raw[offset:]


@pytest.mark.control(reason="pins the already-landed #636 loader fix through CLI update")
@pytest.mark.parametrize(
    "body", ["title: this line is body text\nsecond line", "title: Probe", "type: note"]
)
def test_cli_update_tags_preserves_a_body_that_starts_like_frontmatter(cli_env, body):
    invoke, _ = cli_env
    path = create_note(cli_env, body)
    before = body_bytes(path)
    assert before.strip() == body.encode("utf-8")

    invoke(["update", path.stem, "-k", "notes", "--tags", "x"])

    assert body_bytes(path) == before
    result = invoke(["get", path.stem, "-k", "notes", "--format", "json"])
    assert json.loads(result.output)["tags"] == ["x"]


@pytest.mark.control(reason="pins the already-landed #636 loader fix through CLI sync/get")
@pytest.mark.parametrize(
    "body", ["title: this line is body text\nsecond line", "title: Probe", "type: note"]
)
def test_index_sync_then_get_preserves_frontmatter_looking_body(cli_env, body):
    invoke, kb_path = cli_env
    path = create_note(cli_env, body)
    # Create indexes its entry already. Remove that row so sync must load the
    # file, rather than passing by reusing create's cached body.
    with PyriteDB(kb_path.parent / "index.db") as db:
        db.delete_entry(path.stem, "notes")
    before = path.read_bytes()
    invoke(["index", "sync", "-k", "notes"])
    result = invoke(["get", path.stem, "-k", "notes", "--format", "json"])

    assert json.loads(result.output)["body"] == body
    assert path.read_bytes() == before


@pytest.mark.parametrize("single_entry", [False, True], ids=["kb", "entry"])
@pytest.mark.parametrize(
    ("body", "field"),
    [
        ("title: Probe", "title"),
        ("type: note\nsecond line", "type"),
        pytest.param(
            "title: different text",
            None,
            marks=pytest.mark.control(reason="a different value must not trigger a leak warning"),
        ),
        pytest.param(
            "unknown: Probe",
            None,
            marks=pytest.mark.control(reason="an unknown key must not trigger a leak warning"),
        ),
        pytest.param(
            "ordinary first line\ntitle: Probe",
            None,
            marks=pytest.mark.control(reason="only the first body line is inspected"),
        ),
    ],
)
def test_qa_validate_reports_matching_first_body_line_without_repair(
    cli_env, body, field, single_entry
):
    invoke, _ = cli_env
    path = create_note(cli_env, "Original body indexed before the hand edit.")
    # Exercise the file-value check with a changed, unsynced body too.
    raw = path.read_text(encoding="utf-8")
    split = split_frontmatter(raw)
    assert isinstance(split, Frontmatter)
    path.write_text(raw[: split.close_end] + "\n" + body + "\n", encoding="utf-8")
    before = path.read_bytes()
    args = ["qa", "validate", "notes", "--format", "json"]
    if single_entry:
        args.extend(["--entry", path.stem])

    result = invoke(args)

    findings = [
        i for i in json.loads(result.output)["issues"] if i["rule"] == "frontmatter_line_in_body"
    ]
    if field is None:
        assert findings == []
    else:
        assert len(findings) == 1
        assert findings[0]["entry_id"] == path.stem
        assert findings[0]["kb_name"] == "notes"
        assert findings[0]["field"] == field
        assert findings[0]["severity"] == "warning"
    assert path.read_bytes() == before


@pytest.mark.timeout(3)
def test_leaked_frontmatter_line_handles_a_long_value_in_linear_time():
    line = "title: " + "a" + " " * 32_000 + "Probe"

    assert leaked_frontmatter_line({"title": "Probe"}, line) is None


@pytest.mark.parametrize(
    ("frontmatter", "body"),
    [
        ({"date": date(2025, 2, 1)}, "date: 2025-02-01"),
        ({"date": "2025-02-01"}, "date: 2025-02-01"),
        ({"created_at": date(2025, 2, 1)}, "created_at: 2025-02-01"),
    ],
)
def test_leaked_frontmatter_line_matches_date_scalars(frontmatter, body):
    key = next(iter(frontmatter))

    assert leaked_frontmatter_line(frontmatter, body) == (key, body)


def test_leaked_frontmatter_line_compares_scalar_types_and_returns_source_line():
    assert leaked_frontmatter_line({"importance": 1}, "importance: true") is None
    assert leaked_frontmatter_line({"title": "Probe"}, 'title: "Probe"') == (
        "title",
        'title: "Probe"',
    )


def test_qa_validate_warning_quotes_the_exact_body_line(cli_env):
    invoke, _ = cli_env
    path = create_note(cli_env, 'title: "Probe"')

    result = invoke(["qa", "validate", "notes", "--entry", path.stem, "--format", "json"])

    issue = next(
        item
        for item in json.loads(result.output)["issues"]
        if item["rule"] == "frontmatter_line_in_body"
    )
    assert "body starts with 'title: \"Probe\"'" in issue["message"]

"""Every leaf command's --help renders cleanly and contains no dev changelog (#1290)."""

from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from tests.outcome_walk import leaves, root_app

_TREE = typer.main.get_command(root_app("pyrite"))
COMMANDS = [path for path, _ in leaves(_TREE)]


@pytest.mark.parametrize("path", COMMANDS, ids=[" ".join(p) for p in COMMANDS])
def test_command_help_exits_zero_and_renders_cleanly(path: tuple[str, ...]) -> None:
    runner = CliRunner()
    result = runner.invoke(root_app("pyrite"), [*path, "--help"])

    command_name = "pyrite " + " ".join(path)
    assert result.exit_code == 0, (
        f"{command_name} --help exited with {result.exit_code}: {result.output}"
    )
    assert "[]" not in result.output, (
        f"{command_name} --help contains empty '[]', indicating unescaped markup tokens eaten by Rich:\n"
        f"{result.output}"
    )
    for line in result.output.splitlines():
        assert not line.strip().startswith("Was:"), (
            f"{command_name} --help contains developer changelog starting with 'Was:':\n{line}"
        )


def test_reported_help_texts_read_as_described() -> None:
    import re

    runner = CliRunner()
    app = root_app("pyrite")

    def _normalize(text: str) -> str:
        return re.sub(r"[│\s]+", " ", text)

    # 1. pyrite rename --help: escaped wikilink brackets rendered properly
    res_rename = runner.invoke(app, ["rename", "--help"])
    assert "[[old_id]]" in res_rename.output
    assert "[[old_id|alias]]" in res_rename.output
    assert "[]" not in res_rename.output

    # 2. pyrite sw new-adr --help: no Was: in help
    res_adr = runner.invoke(app, ["sw", "new-adr", "--help"])
    assert "Was:" not in res_adr.output

    # 3. pyrite sw review --help: describes approved and changes_requested
    res_review = runner.invoke(app, ["sw", "review", "--help"])
    review_plain = _normalize(res_review.output)
    assert "approved" in review_plain and "done" in review_plain
    assert "changes_requested" in review_plain and "in_progress" in review_plain

    # 4. pyrite create --help: target knowledge base (required unless --template)
    res_create = runner.invoke(app, ["create", "--help"])
    create_plain = _normalize(res_create.output)
    assert "Target knowledge base (required unless --template)" in create_plain

    # 5. pyrite qa validate & qa gaps: orphan vs outbound links
    res_validate = runner.invoke(app, ["qa", "validate", "--help"])
    validate_plain = _normalize(res_validate.output)
    assert "orphan has neither incoming nor outgoing links" in validate_plain

    res_gaps = runner.invoke(app, ["qa", "gaps", "--help"])
    gaps_plain = _normalize(res_gaps.output)
    assert "outbound links" in gaps_plain
    assert "qa validate" in gaps_plain

"""The error reference follows real MCP refusals and declared domain codes."""

import inspect
from pathlib import Path
import re

import pytest

from pyrite import exceptions
from pyrite.exceptions import PyriteError

PAGE = Path(__file__).resolve().parents[1] / "docs/json-contracts.md"


def test_every_declared_domain_error_code_has_a_reference():
    text = PAGE.read_text(encoding="utf-8")
    codes = {
        cls.error_code
        for _, cls in inspect.getmembers(exceptions, inspect.isclass)
        if issubclass(cls, PyriteError)
    }
    assert codes - set(re.findall(r"`([A-Z_]+)`", text)) == set()


@pytest.mark.parametrize("tool", ["kb_get", "kb_read_body", "kb_update", "kb_delete"])
@pytest.mark.parametrize("missing", ["entry", "kb"])
def test_transition_row_matches_real_mcp_not_found(tool, missing, cli_world):
    server = cli_world().mcp()
    args = {"entry_id": "absent", "kb_name": "test-kb" if missing == "entry" else "absent"}
    if tool == "kb_update":
        args["title"] = "New title"
    refusal = server._dispatch_tool(tool, args)
    text = PAGE.read_text(encoding="utf-8")
    table = text.split("### MCP code transitions\n", 1)[1].split("### Deprecated", 1)[0]
    condition = "Missing entry" if missing == "entry" else "Unknown KB"
    rows = [line for line in table.splitlines() if line.startswith(f"| `{tool}` | {condition}")]
    if tool == "kb_update" and missing == "kb":
        rows = [row for row in rows if ", unscoped caller" in row]
    assert len(rows) == 1
    fields = [cell.strip().strip("`") for cell in rows[0].strip("|").split("|")]
    assert fields[2:5] == [refusal["error_code"], refusal["legacy_error_code"], "false"]
    assert refusal["retryable"] is False


@pytest.mark.parametrize(
    ("page", "heading"),
    [
        ("json-contracts.md", "### Deprecated, still accepted"),
        ("upgrading.md", "### 8. Still deprecated"),
    ],
)
def test_removal_versions_follow_the_maintainer_decision(page, heading):
    text = (PAGE.parent / page).read_text(encoding="utf-8")
    section = text.split(heading, 1)[1].split("\n#", 1)[0]
    assert not re.search(r"for one\s+release|this release|next release|a\s+later release", section)
    bullets = re.findall(r"(?m)^- .*?(?=\n- |\n\n|\Z)", section, re.S)
    for name in (
        "`legacy_error_code`",
        "`distance`",
        "`-f`",
        "export --format",
        "`task status`",
        "`db.conn`",
        "`FRONTMATTER_ALIASES`",
    ):
        matching = [bullet for bullet in bullets if name in bullet]
        assert matching, name
        assert all("0.27" in bullet for bullet in matching), name
    if page == "upgrading.md":
        paragraph = text.split("`legacy_error_code`", 1)[1].split("\n- ", 1)[0]
        assert "0.27" in paragraph
        assert "json-contracts.md#mcp-code-transitions" in paragraph
        assert not re.search(r"for one\s+release|a\s+later release", paragraph)


@pytest.mark.control(reason="the central REST mappings keep their existing documented statuses")
def test_server_account_table_statuses_match_the_central_rest_mapping():
    from pyrite.server.errors import _STATUS_BY_CODE

    text = PAGE.read_text(encoding="utf-8")
    table = text.split("## Server and account codes\n", 1)[1].split("\n## ", 1)[0]
    checked = set()
    for line in table.splitlines():
        row = re.match(r"\| `([A-Z_]+)` \| (.*?) \|", line)
        if row is None:
            continue
        code, surface = row.groups()
        status = re.search(r"\b([1-5][0-9]{2})\b", surface)
        assert status is not None, line
        assert _STATUS_BY_CODE[code] == int(status.group(1)), line
        checked.add(code)
    assert len(checked) == 12

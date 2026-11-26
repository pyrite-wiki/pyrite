"""sw new-adr body inputs, deciders, section flags, and CLI/MCP equality (#1862, #1283)."""

from __future__ import annotations

import contextlib
import os
import sys

import pytest
import yaml

from pyrite.config import KBConfig
from tests._world import build_world


@contextlib.contextmanager
def _noop_file_lock(*paths, **kwargs):
    yield [str(p) for p in paths]


@pytest.fixture
def make_world(tmp_path, monkeypatch):
    if sys.platform == "win32":
        if not hasattr(os, "O_NONBLOCK"):
            monkeypatch.setattr(os, "O_NONBLOCK", 0, raising=False)
        if not hasattr(os, "getuid"):
            monkeypatch.setattr(os, "getuid", lambda: 0, raising=False)
        import pyrite.utils.file_lock
        import pyrite.utils.kb_files

        monkeypatch.setattr(pyrite.utils.file_lock, "file_lock", _noop_file_lock)
        monkeypatch.setattr(pyrite.utils.kb_files, "file_lock", _noop_file_lock)

    worlds = []

    def _factory(name: str = "proj"):
        path = tmp_path / name
        w = build_world(
            path,
            kbs=[KBConfig(name=name, path=path / "kb", kb_type="software")],
            rest=False,
        )
        worlds.append(w)
        return w

    yield _factory

    for w in worlds:
        w.close()


def _read_adr_file(world, kb_name: str = "proj") -> tuple[dict, str]:
    paths = list(world.kb_path(kb_name).rglob("*.md"))
    assert len(paths) == 1, f"Expected 1 ADR file, found: {paths}"
    raw = paths[0].read_text(encoding="utf-8")
    parts = raw.split("---", 2)
    assert len(parts) >= 3, f"Not a valid frontmatter file: {raw}"
    frontmatter = yaml.safe_load(parts[1])
    body = parts[2].lstrip("\r\n")
    return frontmatter, body


def test_new_adr_body_file(make_world, tmp_path):
    world = make_world("test-body-file")
    body_file = tmp_path / "decision.md"
    body_file.write_text(
        "## Context\n\nSpecific issue.\n\n## Decision\n\nWe propose X.\n", encoding="utf-8"
    )

    result = world.cli(
        "sw", "new-adr", "Keyed Digest", "-k", "test-body-file", "--body-file", str(body_file)
    )
    assert result.exit_code == 0, result.output

    fm, body = _read_adr_file(world, "test-body-file")
    assert fm["id"] == "adr-0001"
    assert fm["adr_number"] == 1
    assert fm["title"] == "Keyed Digest"
    assert "TODO:" not in body
    assert body.startswith("# ADR-0001: Keyed Digest\n\n")
    assert "Specific issue." in body
    assert "We propose X." in body


def test_new_adr_stdin(make_world):
    world = make_world("test-stdin")
    stdin_text = "## Context\n\nStdin context.\n\n## Decision\n\nStdin decision.\n"

    result = world.cli(
        "sw", "new-adr", "Stdin Decision", "-k", "test-stdin", "--stdin", input=stdin_text
    )
    assert result.exit_code == 0, result.output

    fm, body = _read_adr_file(world, "test-stdin")
    assert fm["id"] == "adr-0001"
    assert "TODO:" not in body
    assert body.startswith("# ADR-0001: Stdin Decision\n\n")
    assert "Stdin context." in body


def test_new_adr_stdin_dash(make_world):
    world = make_world("test-stdin-dash")
    stdin_text = "## Context\n\nDash context.\n"

    result = world.cli(
        "sw",
        "new-adr",
        "Dash Decision",
        "-k",
        "test-stdin-dash",
        "--body-file",
        "-",
        input=stdin_text,
    )
    assert result.exit_code == 0, result.output

    fm, body = _read_adr_file(world, "test-stdin-dash")
    assert fm["id"] == "adr-0001"
    assert "TODO:" not in body
    assert "Dash context." in body


def test_new_adr_section_flags_partial(make_world):
    world = make_world("test-section-flags")
    result = world.cli(
        "sw",
        "new-adr",
        "Section Test",
        "-k",
        "test-section-flags",
        "--context",
        "Custom context motivation.",
    )
    assert result.exit_code == 0, result.output

    fm, body = _read_adr_file(world, "test-section-flags")
    assert fm["id"] == "adr-0001"
    assert "Custom context motivation." in body
    # Decision and Consequences remain default TODO skeleton
    assert "TODO: What is the change that we're proposing and/or doing?" in body
    assert "TODO: What becomes easier or more difficult to do because of this change?" in body


def test_new_adr_section_flags_all(make_world):
    world = make_world("test-all-sections")
    result = world.cli(
        "sw",
        "new-adr",
        "Full Sections",
        "-k",
        "test-all-sections",
        "--context",
        "Context line.",
        "--decision",
        "Decision line.",
        "--consequences",
        "Consequences line.",
    )
    assert result.exit_code == 0, result.output

    fm, body = _read_adr_file(world, "test-all-sections")
    assert fm["id"] == "adr-0001"
    assert "Context line." in body
    assert "Decision line." in body
    assert "Consequences line." in body
    assert "TODO:" not in body


def test_new_adr_deciders(make_world):
    world = make_world("test-deciders")
    result = world.cli(
        "sw",
        "new-adr",
        "Deciders Test",
        "-k",
        "test-deciders",
        "--deciders",
        "alice, bob, charlie",
    )
    assert result.exit_code == 0, result.output

    fm, body = _read_adr_file(world, "test-deciders")
    assert fm["deciders"] == ["alice", "bob", "charlie"]


def test_new_adr_deciders_with_body_file(make_world, tmp_path):
    world = make_world("test-deciders-body-file")
    body_file = tmp_path / "custom.md"
    body_file.write_text("Custom decision content.\n", encoding="utf-8")

    result = world.cli(
        "sw",
        "new-adr",
        "Combined Test",
        "-k",
        "test-deciders-body-file",
        "--body-file",
        str(body_file),
        "--deciders",
        "alice,bob",
    )
    assert result.exit_code == 0, result.output

    fm, body = _read_adr_file(world, "test-deciders-body-file")
    assert fm["deciders"] == ["alice", "bob"]
    assert "Custom decision content." in body


@pytest.mark.parametrize(
    "flags",
    [
        ["--body-file", "dummy.md", "--stdin"],
        ["--body-file", "dummy.md", "--context", "Why"],
        ["--body-file", "dummy.md", "--decision", "What"],
        ["--body-file", "dummy.md", "--consequences", "Outcome"],
        ["--stdin", "--context", "Why"],
        ["--body-file", "-", "--context", "Why"],
    ],
)
def test_body_file_conflicts_are_usage_errors(make_world, tmp_path, flags):
    world = make_world("test-conflicts")
    dummy = tmp_path / "dummy.md"
    dummy.write_text("something", encoding="utf-8")
    actual_flags = [str(dummy) if f == "dummy.md" else f for f in flags]

    result = world.cli(
        "sw", "new-adr", "Conflict Test", "-k", "test-conflicts", *actual_flags, input="stdin text"
    )
    assert result.exit_code == 2, result.output


def test_bad_body_file_path_refusal(make_world, tmp_path):
    world = make_world("test-bad-path")
    missing = tmp_path / "nonexistent.md"

    result = world.cli(
        "sw", "new-adr", "Bad Path", "-k", "test-bad-path", "--body-file", str(missing)
    )
    assert result.exit_code == 1, result.output
    assert "not found" in result.output.lower() or "not_found" in result.output.lower()

    # Directory refusal
    result_dir = world.cli(
        "sw", "new-adr", "Bad Dir", "-k", "test-bad-path", "--body-file", str(tmp_path)
    )
    assert result_dir.exit_code == 1, result_dir.output
    assert (
        "directory" in result_dir.output.lower() or "validation_failed" in result_dir.output.lower()
    )


def test_frontmatter_in_body_is_refused(make_world, tmp_path):
    world = make_world("test-frontmatter")
    fm_file = tmp_path / "fm.md"
    fm_file.write_text("---\nstatus: accepted\n---\nBody here\n", encoding="utf-8")

    result = world.cli(
        "sw", "new-adr", "FM Test", "-k", "test-frontmatter", "--body-file", str(fm_file)
    )
    assert result.exit_code == 1, result.output
    assert "frontmatter block" in result.output


def test_empty_string_section_flag_treated_as_unset(make_world):
    from pyrite_software_kb.adr_listing import ADR_SECTION_PLACEHOLDERS

    world = make_world("test-empty-str")
    result = world.cli("sw", "new-adr", "Empty Sec", "-k", "test-empty-str", "--context", "")
    assert result.exit_code == 0, result.output
    _, body = _read_adr_file(world, "test-empty-str")
    assert ADR_SECTION_PLACEHOLDERS["context"] in body


def test_mcp_refuses_body_with_sections(make_world):
    world = make_world("mcp-conflict")
    server = world.mcp("write")
    server._dispatch_tool("kb_orient", {"kb_name": "mcp-conflict"})
    result = server._dispatch_tool(
        "sw_create_adr",
        {
            "kb_name": "mcp-conflict",
            "title": "Conflict",
            "body": "Some body",
            "context": "Some context",
        },
    )
    assert result.get("created") is False
    assert result.get("error_code") == "VALIDATION_FAILED"
    assert "Cannot combine body with section fields" in result.get("error", "")
    assert len(list(world.kb_path("mcp-conflict").rglob("*.md"))) == 0


def test_mcp_refuses_frontmatter_in_body(make_world):
    world = make_world("mcp-fm")
    server = world.mcp("write")
    server._dispatch_tool("kb_orient", {"kb_name": "mcp-fm"})
    result = server._dispatch_tool(
        "sw_create_adr",
        {
            "kb_name": "mcp-fm",
            "title": "FM ADR",
            "body": "---\nstatus: accepted\n---\nBody here\n",
        },
    )
    assert result.get("created") is False
    assert result.get("error_code") == "VALIDATION_FAILED"
    assert "frontmatter block" in result.get("error", "")
    assert len(list(world.kb_path("mcp-fm").rglob("*.md"))) == 0


SHAPES = [
    ("skeleton", {}),
    ("body-file", {"body": "## Context\n\nFull custom body text from file."}),
    (
        "body-and-deciders",
        {
            "body": "## Context\n\nCustom body.\n\n## Decision\n\nCustom decision.",
            "deciders": ["alice", "bob"],
        },
    ),
    ("stdin", {"stdin": "## Context\n\nFull custom body text from stdin."}),
    ("context-only", {"context": "Only the context section."}),
    ("decision-only", {"decision": "Only the decision section."}),
    ("consequences-only", {"consequences": "Only the consequences section."}),
    (
        "all-sections",
        {
            "context": "Context part.",
            "decision": "Decision part.",
            "consequences": "Consequences part.",
        },
    ),
    (
        "sections-and-deciders",
        {
            "context": "Context part.",
            "decision": "Decision part.",
            "consequences": "Consequences part.",
            "deciders": ["alice", "bob"],
        },
    ),
]


@pytest.mark.parametrize("shape_name, shape_data", SHAPES)
def test_cli_and_mcp_equivalence(make_world, tmp_path, shape_name, shape_data):
    """A test that creates the same ADR through the CLI and through the MCP handler
    and compares the two written files (frontmatter and body) for equality (#1862, #1283)."""
    # 1. Run via CLI
    world_cli = make_world(f"cli-{shape_name}")
    cli_args = ["sw", "new-adr", "Compare ADR", "-k", f"cli-{shape_name}"]
    cli_kwargs = {}
    if "body" in shape_data:
        p = tmp_path / f"body_{shape_name}.md"
        p.write_text(shape_data["body"], encoding="utf-8")
        cli_args.extend(["--body-file", str(p)])
    if "stdin" in shape_data:
        cli_args.append("--stdin")
        cli_kwargs["input"] = shape_data["stdin"]
    if "context" in shape_data:
        cli_args.extend(["--context", shape_data["context"]])
    if "decision" in shape_data:
        cli_args.extend(["--decision", shape_data["decision"]])
    if "consequences" in shape_data:
        cli_args.extend(["--consequences", shape_data["consequences"]])
    if "deciders" in shape_data:
        cli_args.extend(["--deciders", ",".join(shape_data["deciders"])])

    cli_result = world_cli.cli(*cli_args, **cli_kwargs)
    assert cli_result.exit_code == 0, cli_result.output
    cli_fm, cli_body = _read_adr_file(world_cli, f"cli-{shape_name}")

    # 2. Run via MCP
    world_mcp = make_world(f"mcp-{shape_name}")
    server = world_mcp.mcp("write")
    server._dispatch_tool("kb_orient", {"kb_name": f"mcp-{shape_name}"})

    mcp_args = {"kb_name": f"mcp-{shape_name}", "title": "Compare ADR"}
    mcp_data = dict(shape_data)
    if "stdin" in mcp_data:
        mcp_data["body"] = mcp_data.pop("stdin")
    mcp_args.update(mcp_data)
    mcp_result = server._dispatch_tool("sw_create_adr", mcp_args)
    assert mcp_result.get("created") is True, mcp_result
    mcp_fm, mcp_body = _read_adr_file(world_mcp, f"mcp-{shape_name}")

    # 3. Assert exact equality
    assert cli_fm == mcp_fm, f"Frontmatter mismatch for {shape_name}: {cli_fm} != {mcp_fm}"
    assert cli_body == mcp_body, (
        f"Body mismatch for {shape_name}:\nCLI:\n{cli_body}\nMCP:\n{mcp_body}"
    )

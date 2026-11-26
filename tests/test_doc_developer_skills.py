"""The developer-track skills and AGENTS.md how-to parse and run against the CLI (#1869)."""

import re
import shlex
import subprocess
from pathlib import Path

from pyrite.config import KBConfig
from tests.test_doc_kb_lifecycle_skill import (
    _typers_click_exception,
    commands,
    parse_command,
)

REPO = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO / "extensions" / "software-kb" / "skills"
HOWTO = REPO / "docs" / "agent-instructions.md"
EXPECTED_SKILLS = ("project-memory-bootstrap", "session-start", "session-wrap")


def test_three_developer_skills_exist_and_are_single_compact_files():
    for name in EXPECTED_SKILLS:
        skill_dir = SKILLS_DIR / name
        files = sorted(
            p.relative_to(skill_dir).as_posix() for p in skill_dir.rglob("*") if p.is_file()
        )
        assert files == ["SKILL.md"], f"{name} must be a single SKILL.md file, found {files}"
        lines = (skill_dir / "SKILL.md").read_text(encoding="utf-8").splitlines()
        assert len(lines) <= 165, f"{name}/SKILL.md is {len(lines)} lines (expected <= ~150 lines)"


def test_all_developer_skill_and_howto_commands_parse():
    paths = [*(SKILLS_DIR / name / "SKILL.md" for name in EXPECTED_SKILLS), HOWTO]
    errors = []
    count = 0
    for path in paths:
        for example in commands(path.read_text(encoding="utf-8")):
            count += 1
            try:
                parse_command(example)
            except _typers_click_exception() as exc:
                errors.append(f"{path.relative_to(REPO)}: {example}: {exc.format_message()}")
    assert count >= 30, (
        f"Expected at least 30 pyrite commands across skills and how-to, found {count}"
    )
    assert not errors, "\n".join(errors)


def test_skills_state_limits_refusals_and_link_to_contracts():
    bootstrap = (SKILLS_DIR / "project-memory-bootstrap" / "SKILL.md").read_text(encoding="utf-8")
    start = (SKILLS_DIR / "session-start" / "SKILL.md").read_text(encoding="utf-8")
    wrap = (SKILLS_DIR / "session-wrap" / "SKILL.md").read_text(encoding="utf-8")

    # Bootstrap ADR sourcing rules.
    assert "## Alternatives rejected" in bootstrap
    assert "rationale not recorded in" in bootstrap

    for name, text in (
        ("project-memory-bootstrap", bootstrap),
        ("session-start", start),
        ("session-wrap", wrap),
    ):
        assert "README.md" in text, f"{name} must link to software-kb README.md"
        assert "json-contracts.md" in text, f"{name} must link to docs/json-contracts.md"
        assert "Never does" in text, f"{name} must state what it never does"
        assert "frontmatter" in text, f"{name} must forbid hand-editing frontmatter"
        assert "SCHEMA_VIOLATION" in text, f"{name} must give a next step for SCHEMA_VIOLATION"
        assert "index sync" in text, f"{name} must cover unindexed KBs"


def test_agents_md_block_has_no_chained_commands_and_runs_on_fresh_software_kb(
    cli_world, tmp_path, monkeypatch
):
    howto_text = HOWTO.read_text(encoding="utf-8")
    block_match = re.search(
        r"```markdown\n(## Project memory \(Pyrite\)\n.*?)```", howto_text, re.S
    )
    assert block_match, "AGENTS.md block not found in docs/agent-instructions.md"
    block = block_match.group(1)
    assert "&&" not in block, "AGENTS.md block must not contain && command lists"

    extracted = list(commands(block))
    assert extracted, "No pyrite commands extracted from AGENTS.md block"

    monkeypatch.chdir(tmp_path)
    kb_dir = tmp_path / "kb"
    world = cli_world(kbs=[])
    init_res = world.cli("init", "-t", "software", "-p", str(kb_dir), "--name", "demo-sw")
    assert init_res.exit_code == 0, init_res.output

    # Seed one component, one ADR, and one claimed backlog item so every concrete
    # command in the AGENTS.md block executes cleanly against the fresh software KB.
    assert (
        world.cli(
            "create",
            "-k",
            "demo-sw",
            "-t",
            "component",
            "--title",
            "Core API",
            "-b",
            "Core module.",
            "-f",
            "kind=module",
            "-f",
            "path=src/core.py",
        ).exit_code
        == 0
    )
    assert (
        world.cli("sw", "new-adr", "Use SQLite", "-k", "demo-sw", "--status", "accepted").exit_code
        == 0
    )
    assert (
        world.cli("link", "core-api", "adr-0001", "-r", "implements", "-k", "demo-sw").exit_code
        == 0
    )
    assert (
        world.cli(
            "create",
            "-k",
            "demo-sw",
            "-t",
            "backlog_item",
            "--title",
            "Add timeout",
            "-b",
            "Timeout.",
            "-f",
            "kind=feature",
            "-f",
            "priority=high",
            "-f",
            "effort=S",
        ).exit_code
        == 0
    )
    assert world.cli("sw", "transition", "add-timeout", "accepted", "-k", "demo-sw").exit_code == 0
    assert (
        world.cli(
            "sw", "claim", "add-timeout", "-k", "demo-sw", "--assignee", "agent:test"
        ).exit_code
        == 0
    )

    substitutions = {
        "<kb>": "demo-sw",
        "<component>": "core-api",
        "<id>": "add-timeout",
        "<summary>": "Implemented timeout",
    }
    ran = 0
    for cmd in extracted:
        args = shlex.split(cmd)[1:]
        # Skip bare reference mentions (`pyrite sw`, `pyrite create`, `pyrite update`, `pyrite link`).
        if len(args) <= 1 and "<" not in cmd and not any(a.startswith("-") for a in args):
            continue
        concrete = cmd
        for k, v in substitutions.items():
            concrete = concrete.replace(k, v)
        run_args = shlex.split(concrete)[1:]
        res = world.cli(*run_args)
        assert res.exit_code == 0, f"{concrete} failed ({res.exit_code}): {res.output}"
        ran += 1
    assert ran >= 7, f"Expected at least 7 concrete commands in AGENTS.md block to run, ran {ran}"


def test_raw_github_urls_in_howto_exist_in_repo():
    """Every pinned raw.githubusercontent.com URL in the how-to points to a real file."""
    howto_text = HOWTO.read_text(encoding="utf-8")
    urls = re.findall(r"https://raw\.githubusercontent\.com/[^\s)`\"']+", howto_text)
    for url in urls:
        match = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)$", url)
        assert match, f"Malformed raw.githubusercontent.com URL: {url}"
        owner, repo, ref, rel_path = match.groups()
        assert (owner, repo) == ("pyrite-wiki", "pyrite"), f"Unexpected repo in {url}"
        assert (REPO / rel_path).exists(), f"{url} -> {rel_path} does not exist on disk"
        ref_check = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        if ref_check.returncode == 0:
            blob_check = subprocess.run(
                ["git", "cat-file", "-e", f"{ref}:{rel_path}"],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            assert blob_check.returncode == 0, (
                f"{url} returned 404 at pinned ref {ref}: {rel_path} missing at {ref}"
            )

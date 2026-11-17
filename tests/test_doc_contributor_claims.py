"""Contract tests for contributor skills and documentation claims (#1580).

Ensures that contributor and plugin documentation remains aligned with code:
- No documentation claims that core types are mapped to plugin types by subclassing.
- The list of CLI walk tests in gotchas.md stays synchronized with tests/ on disk.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills" / "pyrite-dev"
DOCS_DIR = REPO_ROOT / "docs"
README_PATH = REPO_ROOT / "README.md"
TESTS_DIR = REPO_ROOT / "tests"


def test_contributor_skills_do_not_describe_subclass_type_mapping():
    """data-pipelines.md and gotchas.md must not describe type mapping by subclassing."""
    data_pipelines_path = SKILLS_DIR / "data-pipelines.md"
    gotchas_path = SKILLS_DIR / "gotchas.md"

    for path in (data_pipelines_path, gotchas_path):
        assert path.exists(), f"Expected skill doc {path} to exist"
        text = path.read_text(encoding="utf-8")
        assert "→ article" not in text and "-> article" not in text, (
            f"{path.name} still contains obsolete subclass example '→ article'"
        )
        assert "parent is a core type" not in text, (
            f"{path.name} still claims type mapping occurs when 'parent is a core type'"
        )
        assert "get_type_mappings" in text, (
            f"{path.name} must name 'get_type_mappings' for explicit type mapping"
        )


def test_gotchas_walk_tests_list_matches_disk_and_grep():
    """CLI walk tests listed in gotchas.md must exist and match all outcome_walk/every_command tests."""
    gotchas_path = SKILLS_DIR / "gotchas.md"
    assert gotchas_path.exists(), f"Expected {gotchas_path} to exist"
    gotchas_text = gotchas_path.read_text(encoding="utf-8")

    section_header = "## A new CLI option: the tests that walk every command"
    assert section_header in gotchas_text, (
        f"{gotchas_path.name} must contain section '{section_header}'"
    )

    section = gotchas_text.split(section_header)[1].split("\n## ")[0]
    listed_tests = set(re.findall(r"`(tests/test_[^`]+\.py)`", section))
    assert listed_tests, f"No test files found under '{section_header}' in {gotchas_path.name}"

    for rel_path in listed_tests:
        full_path = REPO_ROOT / rel_path
        assert full_path.exists(), (
            f"Test file {rel_path} named in {gotchas_path.name} does not exist on disk"
        )

    # Discover all tests in tests/ that use outcome_walk or every_command
    this_file = Path(__file__).resolve()
    actual_walk_tests: set[str] = set()
    for test_file in TESTS_DIR.glob("test_*.py"):
        if test_file.resolve() == this_file:
            continue
        content = test_file.read_text(encoding="utf-8")
        if "outcome_walk" in content or "every_command" in content:
            rel = test_file.relative_to(REPO_ROOT).as_posix()
            actual_walk_tests.add(rel)

    missing = actual_walk_tests - listed_tests
    extra = listed_tests - actual_walk_tests
    assert actual_walk_tests == listed_tests, (
        f"CLI walk tests in gotchas.md do not match walk tests in tests/.\n"
        f"Missing from gotchas.md: {sorted(missing)}\n"
        f"Extra in gotchas.md: {sorted(extra)}\n"
        f"To fix: edit the list under '{section_header}' in .claude/skills/pyrite-dev/gotchas.md"
    )


def test_docs_and_skills_do_not_claim_subclass_mapping():
    """Grep across skills, docs, and README for subclass-mapping vocabulary."""
    target_files: list[Path] = [README_PATH]

    for root_dir in (SKILLS_DIR, DOCS_DIR):
        if root_dir.exists():
            target_files.extend(p for p in root_dir.rglob("*.md") if p.is_file())

    prohibited_patterns = [
        re.compile(r"\bparent is a core type\b", re.IGNORECASE),
        re.compile(r"\bplugin subtype\b", re.IGNORECASE),
    ]

    violations: list[str] = []
    for file_path in target_files:
        content = file_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        for idx, line in enumerate(lines, start=1):
            for pattern in prohibited_patterns:
                if pattern.search(line):
                    rel = file_path.relative_to(REPO_ROOT).as_posix()
                    violations.append(f"{rel}:{idx}: {line.strip()}")

    assert not violations, (
        f"Found {len(violations)} prohibited subclass-mapping reference(s):\n"
        + "\n".join(violations)
    )

"""Paths that run on their own, or instruct agents, stay owned and inert (#1369).

`.gitignore` is the only thing keeping editor autoload files out of a clone,
and `git add -f` bypasses it. Hook revisions are tags a repository owner can
move. CODEOWNERS names the maintainer for every path that executes without
being asked to or that an agent reads as instructions.
"""

import fnmatch
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CODEOWNERS = ROOT / ".github" / "CODEOWNERS"

#: One concrete path under each pattern in #1369 item 2.
OWNED_PATHS = [
    ".claude/settings.json",
    ".claude/skills/pyrite-dev/SKILL.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".github/workflows/ci.yml",
    "scripts/cloud-session-start.sh",
    "scripts/cloud-env-setup.sh",
    "scripts/setup-checkout.sh",
    "scripts/cloud-postgres.sh",
    "scripts/known-cloud-failures.json",
    "conftest.py",
    "tests/conftest.py",
    "extensions/software-kb/tests/conftest.py",
    ".pre-commit-config.yaml",
    ".vscode/tasks.json",
    "pyrite.code-workspace",
    "sub/dir/x.code-workspace",
    ".devcontainer/devcontainer.json",
]


def _rules():
    rules = []
    for line in CODEOWNERS.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            pattern, *owners = line.split()
            rules.append((pattern, owners))
    return rules


def _matches(pattern: str, path: str) -> bool:
    """GitHub CODEOWNERS matching for the forms this file uses."""
    anchored = pattern.startswith("/")
    pat = pattern.lstrip("/")
    if pat.endswith("/"):
        return path.startswith(pat) if anchored else (path.startswith(pat) or f"/{pat}" in path)
    if "/" not in pat and not anchored:
        return fnmatch.fnmatch(path.rsplit("/", 1)[-1], pat)
    return fnmatch.fnmatch(path, pat)


def _owners(path: str) -> list[str]:
    owners: list[str] = []
    for pattern, o in _rules():  # the last matching rule wins
        if _matches(pattern, path):
            owners = o
    return owners


@pytest.mark.parametrize("path", OWNED_PATHS)
def test_every_auto_run_and_agent_path_has_the_maintainer_as_owner(path):
    assert _owners(path) == ["@markramm"]


def test_an_ordinary_file_is_not_owned_by_the_rule():
    assert _owners("pyrite/cli/__init__.py") == []


def test_scripts_the_cloud_hook_runs_are_owned():
    lines = (ROOT / "scripts" / "cloud-session-start.sh").read_text().splitlines()
    hook = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    called = set(re.findall(r"scripts/([\w.-]+\.(?:sh|py))", hook))
    called.add("cloud-session-start.sh")
    assert called
    for name in sorted(called):
        assert _owners(f"scripts/{name}") == ["@markramm"], name


# --- editor autoload files -------------------------------------------------


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True, timeout=30
    ).stdout
    return [p for p in out.split("\0") if p]


def editor_autoload_files(paths: list[str]) -> list[str]:
    """Tracked paths an editor runs or loads without being asked."""
    found = []
    for p in paths:
        parts = p.split("/")
        name = parts[-1]
        if ".vscode" in parts or ".devcontainer" in parts or name.endswith(".code-workspace"):
            found.append(p)
        elif name == "tasks.json" and (ROOT / p).is_file():
            if "runOn" in (ROOT / p).read_text(errors="replace"):
                found.append(p)
    return found


def test_the_tree_tracks_no_editor_autoload_file():
    assert editor_autoload_files(_tracked()) == []


def test_the_check_names_each_kind_of_autoload_file(tmp_path, monkeypatch):
    import tests.test_agent_path_guards as me

    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "tasks.json").write_text('{"tasks":[{"runOptions":{"runOn":"folderOpen"}}]}')
    (tmp_path / "u").mkdir()
    (tmp_path / "u" / "tasks.json").write_text('{"tasks": []}')
    monkeypatch.setattr(me, "ROOT", tmp_path)
    paths = [
        ".vscode/settings.json",
        "a/.vscode/tasks.json",
        "x.code-workspace",
        ".devcontainer/devcontainer.json",
        "t/tasks.json",
        "u/tasks.json",
        "README.md",
    ]
    assert editor_autoload_files(paths) == paths[:5]


# --- pre-commit pins ---------------------------------------------------------


def test_pre_commit_remote_hooks_are_pinned_to_full_commit_shas():
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    remote = [r for r in config["repos"] if r["repo"] not in ("local", "meta")]
    assert remote
    for repo in remote:
        assert re.fullmatch(r"[0-9a-f]{40}", str(repo["rev"])), repo["repo"]


def test_each_pin_keeps_its_tag_as_a_comment():
    text = (ROOT / ".pre-commit-config.yaml").read_text()
    pins = re.findall(r"^\s*rev:\s*[0-9a-f]{40}(.*)$", text, re.MULTILINE)
    assert pins
    for tail in pins:
        assert re.search(r"#\s*v\d", tail), tail

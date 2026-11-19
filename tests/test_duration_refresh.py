"""Real timing files and junit reports exercise the scheduled refresh decision."""

import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.refresh_test_durations import dev_run, prepare


def gz(path, values):
    with gzip.open(path, "wt", encoding="utf-8") as output:
        json.dump(values, output)


def seed(tmp_path, times=(36, 6, 6)):
    root = tmp_path / "repo"
    root.mkdir()
    old = dict.fromkeys("abcdefghi", 1)
    fresh = {node: (12 if node in "adg" else 2) for node in old}
    gz(root / ".test_durations.gz", old)
    source = tmp_path / "fresh.gz"
    gz(source, fresh)
    reports = tmp_path / "reports"
    reports.mkdir()
    for index, seconds in enumerate(times, 1):
        (reports / f"junit-test-3.13-{index}.xml").write_text(
            f'<testsuite><testcase classname="x" name="n" time="{seconds}"/></testsuite>'
        )
    return root, source, reports, tmp_path / "body.md"


def test_imbalanced_refresh_changes_only_timings_and_reports_both_estimates(tmp_path):
    root, source, reports, body = seed(tmp_path)
    (root / "sentinel.txt").write_text("untouched")
    assert prepare(root, source, reports, body, "https://example.invalid/run/1")
    with gzip.open(root / ".test_durations.gz", "rt") as stream:
        assert json.load(stream)["a"] == 12
    text = body.read_text()
    assert "36.000" in text and "16.000" in text
    assert "187.5%" in text
    assert "https://example.invalid/run/1" in text
    assert (root / "sentinel.txt").read_text() == "untouched"
    assert {p.name for p in root.iterdir()} == {"sentinel.txt", ".test_durations.gz"}


@pytest.mark.parametrize("times", [(10, 10, 10), (0, 0, 0)])
def test_balanced_or_empty_reports_do_not_rewrite_the_cache(tmp_path, times):
    root, source, reports, body = seed(tmp_path, times)
    before = (root / ".test_durations.gz").read_bytes()
    assert not prepare(root, source, reports, body, "https://example.invalid/run/1")
    assert (root / ".test_durations.gz").read_bytes() == before
    assert not body.exists()


def test_bad_or_incomplete_artifacts_never_replace_the_baseline(tmp_path):
    root, source, reports, body = seed(tmp_path)
    before = (root / ".test_durations.gz").read_bytes()
    gz(source, {"bad": "not seconds"})
    with pytest.raises(ValueError, match="finite"):
        prepare(root, source, reports, body, "https://example.invalid/run/1")
    assert (root / ".test_durations.gz").read_bytes() == before
    (reports / "junit-test-3.13-3.xml").unlink()
    with pytest.raises(ValueError, match="three"):
        prepare(root, source, reports, body, "https://example.invalid/run/1")
    assert (root / ".test_durations.gz").read_bytes() == before


def test_cli_emits_the_workflow_decision_and_preserves_unchanged_timings(tmp_path):
    root, source, reports, body = seed(tmp_path)
    script = Path(__file__).parents[1] / "scripts/refresh_test_durations.py"
    command = [
        sys.executable,
        str(script),
        "--root",
        str(root),
        "--fresh",
        str(source),
        "--reports",
        str(reports),
        "--body",
        str(body),
        "--run-url",
        "https://example.invalid/run/2",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "changed=true\n"
    before = (root / ".test_durations.gz").read_bytes()
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "changed=false\n"
    assert (root / ".test_durations.gz").read_bytes() == before


def test_run_selection_ignores_another_queue_branch_and_preserves_newest_order():
    rows = [
        {"id": 20, "head_branch": "gh-readonly-queue/main/pr-1-new"},
        {"id": 19, "head_branch": "refs/heads/gh-readonly-queue/dev/pr-2-new"},
        {"id": 18, "head_branch": "dev"},
    ]
    assert dev_run(rows) == 19
    assert dev_run(rows[:1]) is None


@pytest.mark.skipif(sys.platform == "win32", reason="the workflow runs Bash on Ubuntu")
def test_workflow_yaml_and_all_bash_steps_parse_without_executing_them():
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/refresh-test-durations.yml").read_text())
    assert workflow.get(True, workflow.get("on"))["schedule"]
    for step in [step for job in workflow["jobs"].values() for step in job["steps"]]:
        if "run" in step:
            result = subprocess.run(
                ["bash", "-n"],
                input=step["run"],
                text=True,
                capture_output=True,
                timeout=10,
            )
            assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="the workflow runs Bash on Ubuntu")
@pytest.mark.parametrize("existing", ["", "42"])
def test_notification_step_creates_or_updates_one_issue_without_pushing_a_branch(
    tmp_path, existing
):
    import os

    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/refresh-test-durations.yml").read_text())
    step = workflow["jobs"]["notify"]["steps"][-1]
    proposal = tmp_path / "proposal"
    proposal.mkdir()
    body = proposal / "duration-refresh.md"
    body.write_text("Source timing evidence\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:3] == ['issue', 'list']: print(os.environ['EXISTING'])\n"
    )
    gh.chmod(0o755)
    log = tmp_path / "calls.jsonl"
    env = dict(
        os.environ,
        PATH=str(binary) + os.pathsep + os.environ["PATH"],
        CALLS=str(log),
        EXISTING=existing,
        GITHUB_REPOSITORY="owner/repo",
        GITHUB_RUN_ID="123",
    )
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 2
    assert calls[0][:2] == ["issue", "list"]
    assert calls[1][:2] == ["issue", "edit" if existing else "create"]
    if existing:
        assert calls[1][2] == existing
    sent = Path(calls[1][calls[1].index("--body-file") + 1])
    assert (tmp_path / sent).read_text().startswith("Source timing evidence")
    assert "https://github.com/owner/repo/actions/runs/123" in body.read_text()
    for job in workflow["jobs"].values():
        for candidate in job["steps"]:
            command = candidate.get("run", "")
            assert "git push" not in command and "gh pr create" not in command
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    assert workflow["jobs"]["notify"]["permissions"]["issues"] == "write"

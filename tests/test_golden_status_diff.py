"""Two real git revisions exercise the warning-only golden transition report."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/golden_status_diff.py"


def _git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, timeout=60).strip()


def _write(root, filename, data):
    folder = root / "tests/characterization/goldens"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_text(json.dumps(data), encoding="utf-8")


def test_report_real_revisions_and_job_summary(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _write(tmp_path, "access_deciding.json", {"route | reader": {"status": 404}})
    _write(
        tmp_path,
        "rest.reader.json",
        {"old | reader | kb": {"status": 200}, "deny | reader | kb": {"status": 200}},
    )
    _write(tmp_path, "mcp.reader.json", {"tool | reader | kb": {"error_code": "KB_NOT_FOUND"}})
    _write(
        tmp_path,
        "unscoped.json",
        {"web | unknown | kb": {"status": 404}, "tool | unknown | kb": {"error_code": "FORBIDDEN"}},
    )
    _write(tmp_path, "rest.removed.json", {"gone | old | kb": {"status": 403}})
    _write(tmp_path, "error_bodies.json", {"Exception": {"status": 403}})
    _git(tmp_path, "add", "tests/characterization/goldens")
    _git(tmp_path, "commit", "-m", "before")
    base = _git(tmp_path, "rev-parse", "HEAD")
    _write(tmp_path, "access_deciding.json", {"route | reader": {"status": 200}})
    _write(
        tmp_path,
        "rest.reader.json",
        {"new | reader | kb": {"status": 200}, "deny | reader | kb": {"status": 404}},
    )
    _write(tmp_path, "mcp.reader.json", {"tool | reader | kb": {"entries": []}})
    _write(
        tmp_path,
        "unscoped.json",
        {"web | unknown | kb": {"status": 200}, "tool | unknown | kb": {"entries": []}},
    )
    _write(tmp_path, "rest.new.json", {"added | newcomer | kb": {"status": 200}})
    (tmp_path / "tests/characterization/goldens/rest.removed.json").unlink()
    _write(tmp_path, "error_bodies.json", {"Exception": {"status": 200}})
    _git(tmp_path, "add", "tests/characterization/goldens")
    _git(tmp_path, "commit", "-m", "after")
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(tmp_path), "--base", base],
        env=dict(os.environ, GITHUB_STEP_SUMMARY=str(summary)),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    text = summary.read_text(encoding="utf-8")
    assert result.stdout.count("::warning::") == 4
    assert "404 → 200 **DENY → ALLOW**" in text
    assert "KB_NOT_FOUND → ok **DENY → ALLOW**" in text
    assert "200 → 404" in text
    assert "200 → absent" in text
    assert "absent → 200" in text
    assert "403 → absent" in text
    assert "newcomer" in text
    assert "Error mapping changed" in text
    assert "Exception" not in result.stdout.split("::warning::", 1)[1]
    unchanged = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(tmp_path), "--base", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert unchanged.returncode == 0
    assert "No outcome-class changes" in unchanged.stdout
    assert "::warning::" not in unchanged.stdout


def test_refusal_codes_follow_declared_exception_hierarchy():
    spec = importlib.util.spec_from_file_location("golden_status_diff", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    codes = module.refusal_codes()
    assert {
        "KB_NOT_FOUND",
        "ENTRY_NOT_FOUND",
        "KB_READ_ONLY",
        "FORBIDDEN",
        "UNAUTHENTICATED",
    } <= codes
    assert "INTERNAL_ERROR" not in codes


def test_workflow_filters_and_limits_warning_job():
    import yaml

    config = yaml.safe_load((SCRIPT.parents[1] / ".github/workflows/ci.yml").read_text())
    jobs = config["jobs"]
    job = jobs["golden-status"]
    assert "pull_request" in job["if"]
    assert "outputs.goldens" in job["if"]
    assert job["steps"][0]["with"]["fetch-depth"] == 2
    assert "--base HEAD^1" in job["steps"][-1]["run"]
    assert "golden-status" not in jobs["gate"]["needs"]
    filters = next(
        step["with"]["filters"] for step in jobs["changes"]["steps"] if step.get("id") == "filter"
    )
    assert yaml.safe_load(filters)["goldens"] == ["tests/characterization/goldens/**"]


def test_code_less_mcp_error_is_not_reported_as_success():
    spec = importlib.util.spec_from_file_location("golden_status_diff", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    key = ("mcp", "tool | reader | kb")
    text, warnings = module.report(
        {key: {"error": "Tool unavailable"}}, {key: {"entries": []}}, module.refusal_codes()
    )
    assert "error → ok" in text
    # A legacy error string alone does not establish an access refusal.
    assert not warnings

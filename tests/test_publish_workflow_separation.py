"""The release workflow builds without the token that publishes (#1369).

A job holding `id-token: write` can mint a PyPI upload credential. Anything
that job installs or builds runs with it, so the build happens in a job
without it and the publish job only downloads the result and runs the pinned
publish action.
"""

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.yml"))
PUBLISH = WORKFLOWS[0].parent / "publish.yml"
#: What a job that holds the publish token must not run.
BUILD_OR_INSTALL = re.compile(
    r"\b(pip3?|pipx|uv|poetry|npm|yarn|pnpm|make|conda)\b|\bsetup\.py\b|-m\s+build\b|\bbuild\b"
)
INSTALLERS = ("actions/setup-python", "actions/setup-node")


def _load(path):
    return yaml.safe_load(path.read_text())


def _holds_token(workflow, job):
    perms = job.get("permissions", workflow.get("permissions"))
    return isinstance(perms, dict) and perms.get("id-token") == "write"


def _token_holders_that_build(workflow):
    bad = []
    for name, job in workflow["jobs"].items():
        if not _holds_token(workflow, job):
            continue
        for step in job.get("steps", []):
            run = step.get("run", "")
            uses = step.get("uses", "")
            if BUILD_OR_INSTALL.search(run) or uses.startswith(INSTALLERS):
                bad.append((name, step.get("name") or run or uses))
    return bad


@pytest.mark.parametrize(
    "workflow",
    [
        w
        if w == PUBLISH
        else pytest.param(
            w, marks=pytest.mark.control(reason="no id-token job in this workflow before or after")
        )
        for w in WORKFLOWS
    ],
    ids=lambda p: p.name,
)
def test_no_job_with_id_token_installs_or_builds(workflow):
    assert _token_holders_that_build(_load(workflow)) == []


@pytest.mark.control(reason="self-test of the checker on a planted workflow")
def test_the_check_catches_a_build_in_the_token_job():
    sample = {
        "permissions": {"id-token": "write"},
        "jobs": {
            "one": {"steps": [{"run": "pip install build"}]},
            "two": {"permissions": {"contents": "read"}, "steps": [{"run": "pip install x"}]},
            "three": {
                "permissions": {"id-token": "write"},
                "steps": [{"uses": "actions/setup-python@abc"}],
            },
        },
    }
    assert [name for name, _ in _token_holders_that_build(sample)] == ["one", "three"]


def test_publish_workflow_has_a_tokenless_build_and_publishing_jobs():
    wf = _load(PUBLISH)
    jobs = wf["jobs"]
    holders = {n for n, j in jobs.items() if _holds_token(wf, j)}
    assert holders, "a publish job must hold id-token: write"
    builders = [n for n, j in jobs.items() if n not in holders]
    assert builders, "there must be a build job without id-token"
    for name in holders:
        job = jobs[name]
        assert job.get("environment") == "pypi", name
        uses = [s.get("uses", "") for s in job["steps"]]
        assert any(u.startswith("actions/download-artifact@") for u in uses), name
        assert any(u.startswith("pypa/gh-action-pypi-publish@") for u in uses), name
        assert not any(s.get("run") for s in job["steps"]), name
    assert not any(_holds_token(wf, jobs[n]) for n in builders)


def test_build_is_pinned_to_an_exact_version_and_dist_is_uploaded():
    text = PUBLISH.read_text()
    assert re.search(r"pip install .*\bbuild==\d+(\.\d+)+", text)
    assert "pip install build\n" not in text
    assert "actions/upload-artifact@" in text


@pytest.mark.control(reason="the mcp job already needed the pyrite job; kept true across the split")
def test_pyrite_mcp_still_waits_for_pyrite():
    jobs = _load(PUBLISH)["jobs"]
    mcp = [n for n, j in jobs.items() if "mcp" in n and j.get("environment") == "pypi"]
    assert mcp
    for name in mcp:
        needs = jobs[name].get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        assert any(n.startswith("publish-pyrite") and "mcp" not in n for n in needs), name


def _pins(path):
    """{action: {sha}} for every `uses: owner/action@sha` in one workflow."""
    out: dict[str, set[str]] = {}
    for action, sha in re.findall(r"uses:\s*([\w.-]+/[\w./-]+)@([0-9a-f]{40})", path.read_text()):
        out.setdefault(action, set()).add(sha)
    return out


@pytest.mark.control(
    reason="dev's publish.yml has no artifact steps; seen red against this branch's old v4 pins"
)
def test_publish_pins_each_shared_action_where_the_other_workflows_do():
    """Dependabot bumps the workflows it can see together; a release workflow
    left on an older SHA of an action the rest of the repo moved is the one
    nobody runs until release day. For every action publish.yml shares with
    another workflow, its pin is one the other workflows use."""
    others: dict[str, set[str]] = {}
    for w in WORKFLOWS:
        if w != PUBLISH:
            for action, shas in _pins(w).items():
                others.setdefault(action, set()).update(shas)
    stale = {
        action: sorted(shas - others[action])
        for action, shas in _pins(PUBLISH).items()
        if action in others and shas - others[action]
    }
    assert stale == {}, "use the SHA (and version comment) the other workflows pin"

"""The release workflow builds without the token that publishes (#1369).

A job that can obtain the upload credential (`id-token: write`, or
`permissions: write-all`, at the job or, when the job sets none, the workflow
level) runs everything in it with that credential. So such a job may run
nothing but the artifact download and the publish action, each pinned to a
full SHA (`TOKEN_JOB_ACTIONS`), and may have no container, services or
reusable-workflow call: an allowlist, not a list of things to refuse. The
build happens in a job without the credential. `PLANTED` holds one workflow
per shape the allowlist must refuse.
"""

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.yml"))
PUBLISH = WORKFLOWS[0].parent / "publish.yml"


def _load(path):
    return yaml.safe_load(path.read_text())


#: The only actions a job that can obtain the upload credential may run,
#: each pinned to a full commit SHA: fetch what the tokenless build made, and
#: publish it.
TOKEN_JOB_ACTIONS = ("actions/download-artifact", "pypa/gh-action-pypi-publish")
#: The only keys such a job may have: no `container` or `services` (an image
#: that runs with the token), no job-level `uses` (a reusable workflow).
TOKEN_JOB_KEYS = {
    "name",
    "runs-on",
    "needs",
    "if",
    "environment",
    "permissions",
    "steps",
    "timeout-minutes",
    "concurrency",
}
TOKEN_STEP_KEYS = {"name", "id", "if", "uses", "with"}
PINNED_USES = re.compile(r"^(?P<action>[\w.-]+/[\w./-]+)@[0-9a-f]{40}$")


def _effective_permissions(workflow, job):
    """A job's own `permissions`, else the workflow's (GitHub: a job-level
    block replaces the workflow's whole). Either may be a mapping or one of
    the strings `read-all` / `write-all`; absent is the repository default,
    which never includes `id-token`."""
    return job["permissions"] if "permissions" in job else workflow.get("permissions")


def _holds_token(workflow, job):
    perms = _effective_permissions(workflow, job)
    if isinstance(perms, str):
        return perms.strip() == "write-all"
    return isinstance(perms, dict) and perms.get("id-token") == "write"


def _token_job_violations(workflow):
    """(job, what) for everything a job that can obtain the upload credential
    does beyond downloading the artifact and running the pinned publish
    action."""
    bad = []
    for name, job in (workflow.get("jobs") or {}).items():
        if not _holds_token(workflow, job):
            continue
        for key in sorted(set(job) - TOKEN_JOB_KEYS):
            bad.append((name, f"job key {key!r}"))
        for step in job.get("steps") or []:
            for key in sorted(set(step) - TOKEN_STEP_KEYS):
                bad.append((name, f"step key {key!r}"))
            m = PINNED_USES.match(str(step.get("uses", "")))
            if not m or m["action"] not in TOKEN_JOB_ACTIONS:
                bad.append((name, f"step {step.get('uses') or step.get('run') or step!r}"))
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
def test_a_token_job_only_downloads_and_publishes(workflow):
    assert _token_job_violations(_load(workflow)) == []


_SHA = "a" * 40
_DOWNLOAD = {"uses": f"actions/download-artifact@{_SHA}", "with": {"name": "d"}}
_PUBLISH_STEP = {"uses": f"pypa/gh-action-pypi-publish@{_SHA}"}
_TOKEN = {"id-token": "write"}


def _token_job(**extra):
    job = {"runs-on": "ubuntu-latest", "environment": "pypi", "permissions": _TOKEN}
    job["steps"] = [_DOWNLOAD, _PUBLISH_STEP]
    job.update(extra)
    return job


PLANTED = {
    "checkout-in-token-job": {
        "jobs": {
            "p": _token_job(steps=[{"uses": f"actions/checkout@{_SHA}"}, _DOWNLOAD, _PUBLISH_STEP])
        }
    },
    "local-composite-action": {
        "jobs": {"p": _token_job(steps=[_DOWNLOAD, {"uses": "./.github/actions/x"}, _PUBLISH_STEP])}
    },
    "unpinned-third-party-action": {
        "jobs": {"p": _token_job(steps=[_DOWNLOAD, {"uses": "someone/thing@v1"}, _PUBLISH_STEP])}
    },
    "unpinned-publish-action": {
        "jobs": {
            "p": _token_job(steps=[_DOWNLOAD, {"uses": "pypa/gh-action-pypi-publish@release/v1"}])
        }
    },
    "job-container": {"jobs": {"p": _token_job(container="python:3.12")}},
    "job-services": {"jobs": {"p": _token_job(services={"db": {"image": "postgres"}})}},
    "reusable-workflow-call": {
        "jobs": {"p": {"permissions": _TOKEN, "uses": "./.github/workflows/other.yml"}}
    },
    "workflow-write-all-build-installs": {
        "permissions": "write-all",
        "jobs": {"build": {"runs-on": "u", "steps": [{"run": "pip install build"}]}},
    },
    "job-write-all-build-installs": {
        "jobs": {
            "build": {
                "runs-on": "u",
                "permissions": "write-all",
                "steps": [{"run": "pip install x"}],
            }
        },
    },
    "run-step-in-token-job": {
        "jobs": {"p": _token_job(steps=[_DOWNLOAD, {"run": "echo hi"}, _PUBLISH_STEP])}
    },
}


@pytest.mark.parametrize("shape", PLANTED)
def test_the_check_refuses_every_other_step_in_a_token_job(shape):
    assert _token_job_violations(PLANTED[shape]) != [], shape


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
    assert sorted({name for name, _ in _token_job_violations(sample)}) == ["one", "three"]


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


PIN = re.compile(r"uses:\s*([\w.-]+/[\w./-]+)@([0-9a-f]{40})[ \t]*(?:#[ \t]*(\S+))?")


def _version(comment):
    """`v7.0.1` -> (7, 0, 1); anything else -> None."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", comment or "")
    return tuple(int(p) for p in m[1].split(".")) if m else None


def _newest(pins):
    """{action: (version, sha)}: the highest version pinned for each action."""
    best = {}
    for action, sha, comment in pins:
        v = _version(comment)
        if v is not None and (action not in best or v > best[action][0]):
            best[action] = (v, sha)
    return best


def behind_the_rest(publish_text, other_texts):
    """{action: why} for each action publish.yml shares with another workflow
    whose pin is not the newest version any other workflow pins, or whose pin
    has no version comment to compare."""
    newest = _newest(p for text in other_texts for p in PIN.findall(text))
    bad = {}
    for action, sha, comment in PIN.findall(publish_text):
        if action not in newest:
            continue
        if _version(comment) is None:
            bad[action] = f"no version comment on {sha[:8]}"
        elif (_version(comment), sha) != newest[action]:
            v, want = newest[action]
            bad[action] = (
                f"{comment} {sha[:8]}; newest elsewhere is v{'.'.join(map(str, v))} {want[:8]}"
            )
    return bad


def test_publish_is_not_behind_the_rest_on_any_shared_action():
    """Dependabot bumps the workflows it can see together; a workflow on a pull
    request branch is invisible to a bump on dev, and a release workflow left
    on an older pin is the one nobody runs until release day. For every action
    publish.yml shares with another workflow, it pins the newest version any of
    them pins, at that version's SHA (#1363 moved the artifact actions while
    this branch's publish.yml still pinned v4)."""
    others = [w.read_text() for w in WORKFLOWS if w != PUBLISH]
    assert behind_the_rest(PUBLISH.read_text(), others) == {}


def test_moving_publish_back_to_an_older_pin_still_used_elsewhere_fails():
    new = "a" * 40
    old = "b" * 40
    others = [
        f"- uses: actions/checkout@{new} # v7.0.1\n",
        f"- uses: actions/checkout@{old} # v4\n",
    ]
    assert behind_the_rest(f"- uses: actions/checkout@{new} # v7.0.1\n", others) == {}
    assert "actions/checkout" in behind_the_rest(f"- uses: actions/checkout@{old} # v4\n", others)
    assert "actions/checkout" in behind_the_rest(f"- uses: actions/checkout@{new}\n", others)

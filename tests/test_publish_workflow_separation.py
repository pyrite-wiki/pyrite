"""The release workflow builds without the token that publishes (#1369).

`publish.yml` is held to `PUBLISH_SPEC`: its trigger, the exact grant at the
workflow and in every job, which jobs name the `pypi` environment, each job's
keys, machine, actions and the inputs each action takes, and the build's
commands. Anything else is a violation. `ENUMERATION` has one row per key, at
workflow, job and step level, that can change who can obtain a credential or
write to the repository, or what a credential job runs or publishes, each with
a planted mutation of the present file; `PLANTED_GRANTS` is generated from
GitHub's documented list of permission scopes, so every scope at every level
is planted at every place a grant can sit. The present file is the one green
control. `PUBLISH_SPEC` also states, for each job, the exact ordered list of
steps that use an action: the full `uses` (action and SHA) and the exact `with`
values, compared with `==`, so a moved SHA, a changed artifact name, path or
`packages-dir`, or an extra step goes red. A Dependabot bump of an action used
in `publish.yml` therefore needs the pinned SHA in this file changed in the
same pull request, by a person who has looked at what the new version does.

For any other workflow:

A job that can obtain the upload credential (`id-token: write`, or
`permissions: write-all`, at the job or, when the job sets none, the workflow
level) runs everything in it with that credential. So such a job may run
nothing but the artifact download and the publish action, each pinned to a
full SHA (`TOKEN_JOB_ACTIONS`), and may have no container, services or
reusable-workflow call: an allowlist, not a list of things to refuse. The
build happens in a job without the credential. `PLANTED` holds one workflow
per shape the allowlist must refuse.
"""

import copy
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
TOKEN_JOB_KEYS = {"name", "runs-on", "needs", "environment", "permissions", "steps"}
TOKEN_STEP_KEYS = {"name", "id", "uses", "with"}
#: The inputs each allowed action may take in a token job. Any other input can
#: change where its bytes come from (`run-id`, `repository`, `github-token`,
#: `pattern`) or go (`repository-url`, `user`, `password`).
TOKEN_STEP_WITH = {
    "actions/download-artifact": {"name", "path"},
    "pypa/gh-action-pypi-publish": {"packages-dir"},
}
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
                continue
            for key in sorted(set(step.get("with") or {}) - TOKEN_STEP_WITH[m["action"]]):
                bad.append((name, f"input {key!r} to {m['action']}"))
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


@pytest.mark.control(
    reason="self-test of the allowlist in this file; all ten shapes passed the old checker"
)
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


# --- publish.yml, held to its spec ------------------------------------------

#: Every permission scope GitHub documents for `permissions:`, with the levels
#: each accepts. Source: docs.github.com/en/actions/reference/workflows-and-actions/
#: workflow-syntax#permissions, read 2026-11-16; `models` and
#: `repository-projects` from earlier versions of that page are kept, so a
#: scope GitHub still accepts is still planted. The planted grants below are
#: generated from this list, not from the spec, so a scope nobody thought of
#: is planted too.
DOCUMENTED_SCOPES = {
    "actions": ("read", "write", "none"),
    "artifact-metadata": ("read", "write", "none"),
    "attestations": ("read", "write", "none"),
    "checks": ("read", "write", "none"),
    "code-quality": ("read", "write", "none"),
    "contents": ("read", "write", "none"),
    "copilot-requests": ("write", "none"),
    "deployments": ("read", "write", "none"),
    "discussions": ("read", "write", "none"),
    "id-token": ("write", "none"),
    "issues": ("read", "write", "none"),
    "models": ("read", "none"),
    "packages": ("read", "write", "none"),
    "pages": ("read", "write", "none"),
    "pull-requests": ("read", "write", "none"),
    "repository-projects": ("read", "write", "none"),
    "security-events": ("read", "write", "none"),
    "statuses": ("read", "write", "none"),
    "vulnerability-alerts": ("read", "none"),
}
#: The shorthand forms `permissions:` also takes (same source).
PERMISSION_SHORTHANDS = ("read-all", "write-all", {})

_ABSENT = object()
# The pinned SHAs, written out here and not read from publish.yml: a Dependabot
# bump of an action used in publish.yml now needs the SHA below changed in the
# same pull request, by a person who has looked at what the new version does.
_SHA_CHECKOUT = "3d3c42e5aac5ba805825da76410c181273ba90b1"  # v7.0.1
_SHA_SETUP_PYTHON = "6d4fd22b20465ccc0f7354476588e8df7c9ca0e0"  # v7.1.0
_SHA_UPLOAD = "cf430e030ddbb5b0abf93d22962f4752f3646cd9"  # v7.0.2
_SHA_DOWNLOAD = "9000827ccba6bdab643e8b6fd33ac0654aef8333"  # v8.0.2
_SHA_PUBLISH = "7245906fb61fa33a13554f9d5fb856fe61758086"  # release/v1
_PUBLISH_PYRITE_STEPS = [
    {
        "uses": f"actions/download-artifact@{_SHA_DOWNLOAD}",
        "with": {"name": "dist-pyrite", "path": "dist/"},
    },
    {"uses": f"pypa/gh-action-pypi-publish@{_SHA_PUBLISH}", "with": _ABSENT},
]
_PUBLISH_MCP_STEPS = [
    {
        "uses": f"actions/download-artifact@{_SHA_DOWNLOAD}",
        "with": {"name": "dist-pyrite-mcp", "path": "pyrite-mcp/dist/"},
    },
    {
        "uses": f"pypa/gh-action-pypi-publish@{_SHA_PUBLISH}",
        "with": {"packages-dir": "pyrite-mcp/dist/"},
    },
]
_TOKEN_PERMS = {"contents": "read", "id-token": "write"}
_TOKEN_JOB = {
    "keys": {"name", "runs-on", "needs", "environment", "permissions", "steps"},
    "permissions": _TOKEN_PERMS,
    "environment": "pypi",
    "step_keys": {"name", "uses", "with"},
    "actions": TOKEN_STEP_WITH,
    "runs": (),
}
#: What publish.yml is: the trigger, every grant at every level, which jobs
#: name the `pypi` environment, what each job may run and with which inputs.
PUBLISH_SPEC = {
    "workflow_keys": {"name", "on", "permissions", "jobs"},
    "on": {"workflow_dispatch": None},
    "permissions": {"contents": "read"},
    "runs_on": "ubuntu-latest",
    "jobs": {
        "build": {
            "keys": {"name", "runs-on", "steps"},
            "permissions": _ABSENT,  # inherits the workflow's contents: read
            "environment": _ABSENT,
            "needs": _ABSENT,
            "step_keys": {"name", "uses", "with", "run", "working-directory"},
            "actions": {
                "actions/checkout": set(),
                "actions/setup-python": {"python-version"},
                "actions/upload-artifact": {"name", "path", "if-no-files-found"},
            },
            # What is built is what is published: the commands are the spec.
            "runs": ("pip install build==1.6.1", "python -m build", "python -m build"),
            # Every `uses` step, in order, exactly: what is checked out, which
            # Python builds it, and which bytes are uploaded under which name.
            "steps": [
                {"uses": f"actions/checkout@{_SHA_CHECKOUT}", "with": _ABSENT},
                {
                    "uses": f"actions/setup-python@{_SHA_SETUP_PYTHON}",
                    "with": {"python-version": "3.12"},
                },
                {
                    "uses": f"actions/upload-artifact@{_SHA_UPLOAD}",
                    "with": {
                        "name": "dist-pyrite",
                        "path": "dist/",
                        "if-no-files-found": "error",
                    },
                },
                {
                    "uses": f"actions/upload-artifact@{_SHA_UPLOAD}",
                    "with": {
                        "name": "dist-pyrite-mcp",
                        "path": "pyrite-mcp/dist/",
                        "if-no-files-found": "error",
                    },
                },
            ],
        },
        "publish-pyrite": {**_TOKEN_JOB, "needs": "build", "steps": _PUBLISH_PYRITE_STEPS},
        "publish-pyrite-mcp": {
            **_TOKEN_JOB,
            "needs": ["build", "publish-pyrite"],
            "steps": _PUBLISH_MCP_STEPS,
        },
    },
}


def _on(workflow):
    """`on:`, which PyYAML reads as the key True (YAML 1.1 boolean)."""
    return workflow.get("on", workflow.get(True, _ABSENT))


_FIX = "; if this change is intended, change the literal in PUBLISH_SPEC in this file"


def _show(value):
    return "no `with:`" if value is _ABSENT else repr(value)


def _step_violations(name, used, want):
    """The `uses` steps of one job against its exact ordered list: the count,
    and for each step the full `uses` (action and SHA) and the whole `with`
    mapping (an absent `with` is its own value). Each line names the job, the
    step index in the job, the field and the value found."""
    bad = []
    if len(used) != len(want):
        bad.append(
            f"{name}: {len(used)} steps with `uses`, spec has {len(want)} "
            f"(found {[s.get('uses') for _, s in used]!r}){_FIX}"
        )
    for (index, step), spec_step in zip(
        used, want, strict=False
    ):  # a count mismatch is reported above
        if step.get("uses") != spec_step["uses"]:
            bad.append(
                f"{name}: step {index} uses {step.get('uses')!r}, "
                f"spec has {spec_step['uses']!r}{_FIX}"
            )
        if step.get("with", _ABSENT) != spec_step["with"]:
            bad.append(
                f"{name}: step {index} with {_show(step.get('with', _ABSENT))}, "
                f"spec has {_show(spec_step['with'])}{_FIX}"
            )
    return bad


def publish_violations(workflow, spec=PUBLISH_SPEC):
    """Every way `workflow` differs from `spec` in anything that decides who
    can obtain a credential or write to the repository, or what a credential
    job runs or publishes. Exact: a grant, key or input the spec does not name
    is a violation, in any job."""
    bad = []
    keys = {("on" if k is True else k) for k in workflow}
    if keys != spec["workflow_keys"]:
        bad.append(f"workflow keys {sorted(map(str, keys ^ spec['workflow_keys']))}")
    if _on(workflow) != spec["on"]:
        bad.append(f"trigger {_on(workflow)!r}")
    if workflow.get("permissions", _ABSENT) != spec["permissions"]:
        bad.append(f"workflow permissions {workflow.get('permissions', _ABSENT)!r}")
    if "${{" in yaml.safe_dump(workflow):
        bad.append("an expression (${{ }}): inputs, secrets or context reach a job")
    jobs = workflow.get("jobs") or {}
    if set(jobs) != set(spec["jobs"]):
        bad.append(f"jobs {sorted(set(jobs) ^ set(spec['jobs']))}")
    for name, want in spec["jobs"].items():
        job = jobs.get(name)
        if not isinstance(job, dict):
            continue
        if set(job) != want["keys"]:
            bad.append(f"{name}: keys {sorted(set(job) ^ want['keys'])}")
        for field in ("permissions", "environment", "needs"):
            if job.get(field, _ABSENT) != want[field]:
                bad.append(f"{name}: {field} {job.get(field, _ABSENT)!r}")
        if job.get("runs-on") != spec["runs_on"]:
            bad.append(f"{name}: runs-on {job.get('runs-on')!r}")
        runs = []
        used = []  # (index, step) of every step with a `uses`
        for index, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                bad.append(f"{name}: step {step!r}")
                continue
            if set(step) - want["step_keys"]:
                bad.append(f"{name}: step keys {sorted(set(step) - want['step_keys'])}")
            if "run" in step:
                runs.append(step["run"])
                continue
            used.append((index, step))
            m = PINNED_USES.match(str(step.get("uses", "")))
            if not m or m["action"] not in want["actions"]:
                bad.append(f"{name}: action {step.get('uses')!r}")
                continue
            extra = set(step.get("with") or {}) - want["actions"][m["action"]]
            if extra:
                bad.append(f"{name}: inputs {sorted(extra)} to {m['action']}")
        if tuple(runs) != want["runs"]:
            bad.append(f"{name}: commands {runs!r}")
        bad.extend(_step_violations(name, used, want["steps"]))
    return bad


def test_publish_yml_is_its_spec():
    """The one green control for every planted shape below."""
    assert publish_violations(_load(PUBLISH)) == []


#: Where a grant can sit. A job's own block replaces the workflow's.
_GRANT_PLACES = ("workflow", "build", "publish-pyrite", "publish-pyrite-mcp")


def _expected_grant(place):
    if place == "workflow":
        return PUBLISH_SPEC["permissions"]
    want = PUBLISH_SPEC["jobs"][place]["permissions"]
    return {} if want is _ABSENT else want


def _planted_grants():
    """Every documented scope at every level it accepts, and every shorthand,
    at every place a grant can sit, differing from the spec there. A level
    `none` on a scope the place does not grant changes nothing and is left
    out; `none` on one it grants (taking `id-token` away) is kept."""
    out = {}
    for place in _GRANT_PLACES:
        base = _expected_grant(place)
        for scope, levels in DOCUMENTED_SCOPES.items():
            for level in levels:
                if base.get(scope) == level or (level == "none" and scope not in base):
                    continue
                out[f"{place}:{scope}:{level}"] = (place, {**base, scope: level})
        for short in PERMISSION_SHORTHANDS:
            out[f"{place}:{short or '{}'}"] = (place, short)
    return out


PLANTED_GRANTS = _planted_grants()


@pytest.mark.control(
    reason="self-test of publish_violations over the present file; 144 of these 148 grants passed the previous checks"
)
@pytest.mark.parametrize("plant", PLANTED_GRANTS)
def test_every_other_grant_anywhere_is_refused(plant):
    place, grant = PLANTED_GRANTS[plant]
    wf = copy.deepcopy(_load(PUBLISH))
    target = wf if place == "workflow" else wf["jobs"][place]
    target["permissions"] = grant
    assert publish_violations(wf) != [], plant


def _set(path, value):
    def mutate(wf):
        node = wf
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = value

    return mutate


def _add_step(job, step, at=None):
    def mutate(wf):
        steps = wf["jobs"][job]["steps"]
        steps.insert(len(steps) if at is None else at, step)

    return mutate


_SECRET = "${{ secrets.PYPI_TOKEN }}"
_DOWNLOAD_AT = ("jobs", "publish-pyrite", "steps", 0)
_PUBLISH_AT = ("jobs", "publish-pyrite", "steps", 1)
_BUILD_STEP = ("jobs", "build", "steps", 2)

#: Every key at workflow, job and step level that can change (a) which job can
#: obtain a credential or write to the repository, or (b) what a credential job
#: runs or publishes. One planted mutation per row, each applied to the present
#: file. (level, key, what it can change, mutation)
ENUMERATION = [
    ("workflow", "on", "who starts a release, from which ref", _set(("on",), {"push": None})),
    (
        "workflow",
        "on (release)",
        "an automatic publish",
        _set(("on",), {"release": {"types": ["published"]}}),
    ),
    ("workflow", "permissions", "every job's grant", None),  # PLANTED_GRANTS
    ("workflow", "env", "a secret or setting in every job", _set(("env",), {"T": _SECRET})),
    (
        "workflow",
        "defaults",
        "the shell every run step uses",
        _set(("defaults",), {"run": {"shell": "sh"}}),
    ),
    (
        "workflow",
        "jobs (a new job)",
        "a job outside the spec, with its own grant",
        _set(
            ("jobs", "extra"),
            {
                "runs-on": "ubuntu-latest",
                "permissions": {"contents": "write"},
                "steps": [{"run": "true"}],
            },
        ),
    ),
    ("job", "permissions", "a job's grant", None),  # PLANTED_GRANTS
    (
        "job",
        "environment (build)",
        "environment secrets in the job that runs the build",
        _set(("jobs", "build", "environment"), "pypi"),
    ),
    (
        "job",
        "environment (token job)",
        "the gate the maintainer's reviewer sits on",
        _set(("jobs", "publish-pyrite", "environment"), "other"),
    ),
    (
        "job",
        "runs-on",
        "a self-hosted machine holding the credential",
        _set(("jobs", "publish-pyrite", "runs-on"), "self-hosted"),
    ),
    (
        "job",
        "runs-on (build)",
        "a self-hosted machine making the artifact",
        _set(("jobs", "build", "runs-on"), "self-hosted"),
    ),
    (
        "job",
        "container",
        "an image that runs with the credential",
        _set(("jobs", "publish-pyrite", "container"), "python:3.12"),
    ),
    (
        "job",
        "services",
        "images running beside the credential",
        _set(("jobs", "publish-pyrite", "services"), {"db": {"image": "postgres"}}),
    ),
    (
        "job",
        "uses + secrets",
        "a reusable workflow given every secret",
        _set(("jobs", "build"), {"uses": "./.github/workflows/ci.yml", "secrets": "inherit"}),
    ),
    ("job", "env (build)", "a secret in the build", _set(("jobs", "build", "env"), {"T": _SECRET})),
    (
        "job",
        "env (token job)",
        "settings the publish action reads",
        _set(("jobs", "publish-pyrite", "env"), {"PYPI_URL": "https://example.test"}),
    ),
    (
        "job",
        "needs",
        "publishing without waiting for the build",
        _set(("jobs", "publish-pyrite", "needs"), []),
    ),
    (
        "job",
        "needs (mcp)",
        "pyrite-mcp published before pyrite",
        _set(("jobs", "publish-pyrite-mcp", "needs"), "build"),
    ),
    (
        "job",
        "if",
        "a token job skipped or made conditional",
        _set(("jobs", "publish-pyrite", "if"), False),
    ),
    (
        "job",
        "strategy",
        "a matrix that multiplies the token job",
        _set(("jobs", "publish-pyrite", "strategy"), {"matrix": {"x": [1, 2]}}),
    ),
    (
        "job",
        "continue-on-error",
        "a failed publish reported as success",
        _set(("jobs", "publish-pyrite", "continue-on-error"), True),
    ),
    (
        "job",
        "defaults",
        "the shell a job's run steps use",
        _set(("jobs", "build", "defaults"), {"run": {"shell": "sh"}}),
    ),
    (
        "job",
        "outputs",
        "values passed to a token job",
        _set(("jobs", "build", "outputs"), {"x": "y"}),
    ),
    (
        "step",
        "uses (another action in a token job)",
        "code run with the credential",
        _add_step("publish-pyrite", {"uses": "actions/checkout@" + "a" * 40}, at=0),
    ),
    (
        "step",
        "uses (unpinned publish)",
        "a moved tag runs with the credential",
        _set(_PUBLISH_AT + ("uses",), "pypa/gh-action-pypi-publish@release/v1"),
    ),
    (
        "step",
        "uses (local action)",
        "repository code run with the credential",
        _add_step("publish-pyrite", {"uses": "./.github/actions/x"}, at=1),
    ),
    (
        "step",
        "uses (docker action)",
        "an image run with the credential",
        _add_step("publish-pyrite", {"uses": "docker://python:3.12"}, at=1),
    ),
    (
        "step",
        "uses (another action in the build)",
        "what gets built",
        _add_step("build", {"uses": "someone/thing@" + "a" * 40}, at=1),
    ),
    (
        "step",
        "run (token job)",
        "a command run with the credential",
        _add_step("publish-pyrite", {"run": "echo hi"}, at=1),
    ),
    (
        "step",
        "run (build)",
        "what gets built",
        _add_step("build", {"run": "pip install other"}, at=3),
    ),
    (
        "step",
        "with: run-id",
        "an artifact from another run",
        _set(_DOWNLOAD_AT + ("with", "run-id"), "123"),
    ),
    (
        "step",
        "with: repository",
        "an artifact from another repository",
        _set(_DOWNLOAD_AT + ("with", "repository"), "someone/else"),
    ),
    (
        "step",
        "with: github-token",
        "the token that reaches another run",
        _set(_DOWNLOAD_AT + ("with", "github-token"), "x"),
    ),
    (
        "step",
        "with: pattern",
        "which artifacts are downloaded",
        _set(_DOWNLOAD_AT + ("with", "pattern"), "*"),
    ),
    (
        "step",
        "with: repository-url",
        "another index",
        _set(_PUBLISH_AT + ("with",), {"repository-url": "https://test.pypi.org/legacy/"}),
    ),
    (
        "step",
        "with: password",
        "a stored token instead of trusted publishing",
        _set(_PUBLISH_AT + ("with",), {"password": "x"}),
    ),
    (
        "step",
        "with: ref (checkout)",
        "which commit is built",
        _set(("jobs", "build", "steps", 0, "with"), {"ref": "other"}),
    ),
    (
        "step",
        "with: an expression",
        "a dispatch input choosing the bytes",
        _set(_DOWNLOAD_AT + ("with", "path"), "${{ github.event.inputs.dir }}"),
    ),
    (
        "step",
        "env",
        "settings the publish action reads",
        _set(_PUBLISH_AT + ("env",), {"PYPI_URL": "x"}),
    ),
    (
        "step",
        "shell",
        "the program a run step runs under",
        _set(_BUILD_STEP + ("shell",), "python {0}"),
    ),
    ("step", "if", "a token step skipped", _set(_PUBLISH_AT + ("if",), False)),
    (
        "step",
        "continue-on-error",
        "a failed publish reported as success",
        _set(_PUBLISH_AT + ("continue-on-error",), True),
    ),
    (
        "step",
        "working-directory (token job)",
        "which files the publish action reads",
        _set(_PUBLISH_AT + ("working-directory",), "elsewhere"),
    ),
]


def _normal(wf):
    """The loaded file with `on` under its own name, so a mutation can set it."""
    wf = copy.deepcopy(wf)
    if True in wf:
        wf["on"] = wf.pop(True)
    return wf


@pytest.mark.control(
    reason="self-test of publish_violations over the present file; 27 of these 41 passed the previous checks"
)
@pytest.mark.parametrize(
    "row", [r for r in ENUMERATION if r[3] is not None], ids=lambda r: f"{r[0]}:{r[1]}"
)
def test_every_enumerated_key_changed_is_refused(row):
    wf = _normal(_load(PUBLISH))
    row[3](wf)
    assert publish_violations(wf) != [], row[1]


_OTHER_SHA = "b" * 40
_DL = "actions/download-artifact@"
_PUB = "pypa/gh-action-pypi-publish@"
_UP = "actions/upload-artifact@"


def _set_sha(path):
    """Move the `uses` at `path` to another full-length SHA, same action."""

    def mutate(wf):
        node = wf
        for part in path[:-1]:
            node = node[part]
        action = node[path[-1]].split("@")[0]
        node[path[-1]] = f"{action}@{_OTHER_SHA}"

    return mutate


def _drop(path):
    def mutate(wf):
        node = wf
        for part in path[:-1]:
            node = node[part]
        del node[path[-1]]

    return mutate


def _dup_step(job, index):
    def mutate(wf):
        steps = wf["jobs"][job]["steps"]
        steps.insert(index, copy.deepcopy(steps[index]))

    return mutate


_P = ("jobs", "publish-pyrite", "steps")
_M = ("jobs", "publish-pyrite-mcp", "steps")
_B = ("jobs", "build", "steps")

#: The five ways `publish.yml` could change what is downloaded, published or
#: uploaded that the key-and-name checks above passed: a value, a SHA, a count.
#: (id, mutation), each applied to the present file.
VALUE_ROWS = [
    # 1. a download step's artifact name or path, in each publishing job
    ("download-name:publish-pyrite", _set(_P + (0, "with", "name"), "dist-pyrite-mcp")),
    ("download-path:publish-pyrite", _set(_P + (0, "with", "path"), "elsewhere/")),
    ("download-name:publish-pyrite-mcp", _set(_M + (0, "with", "name"), "dist-pyrite")),
    ("download-path:publish-pyrite-mcp", _set(_M + (0, "with", "path"), "dist/")),
    # 2. packages-dir on a publish step, including adding one where there is none
    ("packages-dir-added:publish-pyrite", _set(_P + (1, "with"), {"packages-dir": "dist/"})),
    ("packages-dir-changed:publish-pyrite-mcp", _set(_M + (1, "with", "packages-dir"), "dist/")),
    ("packages-dir-removed:publish-pyrite-mcp", _drop(_M + (1, "with"))),
    # 3. the build job's upload steps
    ("upload-path:dist-pyrite", _set(_B + (5, "with", "path"), "pyrite-mcp/dist/")),
    ("upload-name:dist-pyrite", _set(_B + (5, "with", "name"), "dist-other")),
    ("upload-path:dist-pyrite-mcp", _set(_B + (6, "with", "path"), "dist/")),
    ("upload-name:dist-pyrite-mcp", _set(_B + (6, "with", "name"), "dist-other")),
    ("upload-if-no-files-found", _set(_B + (6, "with", "if-no-files-found"), "ignore")),
    # 4. an action moved to another full-length SHA
    ("sha:publish:publish-pyrite", _set_sha(_P + (1, "uses"))),
    ("sha:publish:publish-pyrite-mcp", _set_sha(_M + (1, "uses"))),
    ("sha:download:publish-pyrite", _set_sha(_P + (0, "uses"))),
    ("sha:download:publish-pyrite-mcp", _set_sha(_M + (0, "uses"))),
    ("sha:upload:build", _set_sha(_B + (5, "uses"))),
    ("sha:checkout:build", _set_sha(_B + (0, "uses"))),
    # 5. a second download step in a publishing job
    ("second-download:publish-pyrite", _dup_step("publish-pyrite", 0)),
    ("second-download:publish-pyrite-mcp", _dup_step("publish-pyrite-mcp", 0)),
]


@pytest.mark.control(
    reason="self-test of publish_violations over the present file; every one of these passed the checks that came before the step lists"
)
@pytest.mark.parametrize("row", VALUE_ROWS, ids=lambda r: r[0])
def test_a_changed_step_value_sha_or_count_is_refused(row):
    wf = _normal(_load(PUBLISH))
    row[1](wf)
    assert publish_violations(wf) != [], row[0]


def test_the_normalised_present_file_is_still_green():
    assert publish_violations(_normal(_load(PUBLISH))) == []


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


@pytest.mark.control(
    reason="dev's publish.yml has no artifact steps; seen red with this branch's old v4 upload pin"
)
def test_publish_is_not_behind_the_rest_on_any_shared_action():
    """Dependabot bumps the workflows it can see together; a workflow on a pull
    request branch is invisible to a bump on dev, and a release workflow left
    on an older pin is the one nobody runs until release day. For every action
    publish.yml shares with another workflow, it pins the newest version any of
    them pins, at that version's SHA (#1363 moved the artifact actions while
    this branch's publish.yml still pinned v4)."""
    others = [w.read_text() for w in WORKFLOWS if w != PUBLISH]
    assert behind_the_rest(PUBLISH.read_text(), others) == {}


@pytest.mark.control(reason="self-test of behind_the_rest, which lives in this file")
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

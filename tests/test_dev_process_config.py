"""The commit/push/CI split is load-bearing, so it is pinned by tests.

See kb/backlog/fast-commit-hooks-full-suite-at-pre-push-ci-is-the-gate.md.

Commit-stage hooks stash every unstaged edit in the working tree while they
run. With several sessions sharing one tree, a multi-minute hook makes other
sessions' edits vanish for minutes and lets one session's untracked RED test
block everyone's commits. So: nothing slow at the commit stage, the full
suite at pre-push, CI as the authority.
"""

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def precommit() -> dict:
    return yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text())


@pytest.fixture(scope="module")
def ci() -> dict:
    return yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())


@pytest.fixture(scope="module")
def pyproject() -> dict:
    import tomllib

    return tomllib.loads((REPO / "pyproject.toml").read_text())


def _hooks(config: dict) -> list[dict]:
    return [hook for repo in config["repos"] for hook in repo["hooks"]]


def _stages(hook: dict, config: dict) -> set[str]:
    # A hook with no `stages` runs at every installed stage.
    return set(hook.get("stages") or config.get("default_stages") or ["pre-commit"])


def _runs_tests(hook: dict) -> bool:
    entry = str(hook.get("entry", ""))
    return "pytest" in entry or "test-affected" in entry


def _local_hooks(config: dict) -> list[dict]:
    return [h for repo in config["repos"] if repo["repo"] == "local" for h in repo["hooks"]]


class TestPreCommitConfig:
    def test_no_commit_stage_hook_runs_pytest(self, precommit):
        offenders = [
            hook["id"]
            for hook in _hooks(precommit)
            if _runs_tests(hook) and "pre-commit" in _stages(hook, precommit)
        ]
        assert offenders == [], f"pytest must not run at the commit stage: {offenders}"

    def test_full_suite_runs_at_pre_push(self, precommit):
        pushed = [
            hook
            for hook in _hooks(precommit)
            if _runs_tests(hook) and _stages(hook, precommit) == {"pre-push"}
        ]
        assert len(pushed) == 1, "expected exactly one pre-push pytest hook"

    def test_pre_push_suite_is_scoped_to_code_changes(self, precommit):
        (hook,) = [h for h in _hooks(precommit) if _runs_tests(h)]
        assert not hook.get("always_run"), "always_run defeats the docs-only skip"
        assert hook.get("files"), "pre-push pytest needs a `files:` filter"

    def test_local_hooks_do_not_discard_output(self, precommit):
        offenders = [h["id"] for h in _local_hooks(precommit) if "/dev/null" in h["entry"]]
        assert offenders == [], f"hooks must show why they failed: {offenders}"

    def test_local_hooks_do_not_require_an_activated_venv(self, precommit):
        offenders = [h["id"] for h in _local_hooks(precommit) if "activate" in h["entry"]]
        assert offenders == [], f"`source .venv/bin/activate` is not portable: {offenders}"

    def test_all_three_hook_types_install_by_default(self, precommit):
        # Without this, plain `pre-commit install` skips commit-msg and pre-push,
        # and the fix-commit-has-tests rule silently never runs for new clones.
        assert set(precommit.get("default_install_hook_types", [])) >= {
            "pre-commit",
            "commit-msg",
            "pre-push",
        }

    def test_kb_schema_validation_stays_at_commit_stage(self, precommit):
        (hook,) = [h for h in _hooks(precommit) if h["id"] == "pyrite-schema-validate"]
        assert "pre-commit" in _stages(hook, precommit)


class TestCIWorkflow:
    def test_superseded_runs_are_cancelled(self, ci):
        assert ci["concurrency"]["cancel-in-progress"] is True

    def test_protected_branches_get_a_run_per_commit(self, ci):
        """`dev` and `main` must not cancel each other's runs.

        The group key decides what counts as "the same run". Keyed on the ref
        alone, a second merge to `dev` cancels the first one's matrix: on
        2026-09-21 #173 merged seven minutes after #276 and killed its run, so
        38beda9 -- the commit that changed the release path -- never got a
        verdict, and `gate` went red meaning only "superseded" (#279).

        That matters on `dev` and `main` specifically, because every PR rebases
        onto `dev`, and `main` only ever fast-forwards to a commit CI has
        proven. A commit nothing verified breaks both promises, and a red
        `gate` that means "superseded" teaches people to ignore red.

        Including the SHA for protected refs gives each commit its own group;
        PR branches keep cancelling, which is what makes CI answer quickly.
        """
        group = ci["concurrency"]["group"]
        assert "github.ref_protected" in group, (
            "the concurrency group does not distinguish protected branches, so "
            "a merge to dev cancels the previous merge's matrix and leaves that "
            "commit unverified (#279)"
        )
        assert "github.sha" in group, (
            "the concurrency group has no per-commit component, so two commits "
            "on dev share one group and the older run is cancelled (#279)"
        )

    def test_ci_runs_the_checks_that_local_hooks_run(self, ci):
        # Outside contributors' PRs never run local hooks; CI has to.
        steps = "\n".join(
            str(step.get("run", "")) for job in ci["jobs"].values() for step in job["steps"]
        )
        assert "check_import_cycles.py" in steps
        assert "pyrite schema validate" in steps

    def test_no_duplicate_full_suite_job(self, ci):
        assert "test-optional-deps" not in ci["jobs"]

    def test_matrix_is_one_interpreter_on_pull_requests_and_all_on_pushes(self, ci):
        # `test (3.12)` is the required check for PRs; dev and main pushes run
        # the whole matrix (ADR-0032 §3 value chain).
        matrix = str(ci["jobs"]["test"]["strategy"]["matrix"]["python-version"])
        assert "github.event_name == 'pull_request'" in matrix
        assert '["3.12"]' in matrix
        assert '["3.11", "3.12", "3.13"]' in matrix


class TestExtensionsLintParity:
    """extensions/ must be linted in CI exactly like pyrite/ tests/ (CI parity).

    The commit-stage ruff hook already covers extensions/ -- the first person
    to touch one file there inherited the whole extension's lint debt in an
    unrelated commit (2026-09-17). CI's ruff step covered pyrite/ tests/ only,
    so a PR could go green with extensions/ lint errors a local commit would
    have blocked. See
    kb/backlog/done/ci-parity-lint-extensions-and-enforce-the-fix-needs-a-test-rule.md.
    """

    def test_ruff_check_covers_extensions(self, ci):
        runs = "\n".join(str(s.get("run", "")) for s in ci["jobs"]["test"]["steps"])
        assert "ruff check pyrite/ tests/ extensions/" in runs, runs

    def test_ruff_format_check_covers_extensions(self, ci):
        runs = "\n".join(str(s.get("run", "")) for s in ci["jobs"]["test"]["steps"])
        assert "ruff format --check pyrite/ tests/ extensions/" in runs, runs


class TestFixCommitCheckRunsInCI:
    """A `fix:` commit without a tests/ change must fail CI on a PR.

    `check_fix_commit_has_tests.py` only ran as a local commit-msg hook --
    outside contributors' PRs never run local hooks, and all three outside
    PRs so far were fixes without tests. The check has to run in CI, over the
    PR's commit range, and only on `pull_request` (a `push` has no PR range
    to walk).
    """

    def _step(self, ci: dict) -> dict:
        steps = [
            s
            for s in ci["jobs"]["test"]["steps"]
            if "check_fix_commit_has_tests.py" in str(s.get("run", ""))
        ]
        assert len(steps) == 1, "expected exactly one step running the fix-commit check"
        return steps[0]

    def test_a_step_invokes_the_script(self, ci):
        self._step(ci)  # raises if missing or duplicated

    def test_the_step_only_runs_on_pull_request(self, ci):
        step = self._step(ci)
        cond = str(step.get("if", ""))
        assert "pull_request" in cond, cond

    def test_checkout_fetches_full_history(self, ci):
        # A shallow checkout can't walk base.sha..head across the PR's commits.
        checkout = [
            s
            for s in ci["jobs"]["test"]["steps"]
            if s.get("uses", "").startswith("actions/checkout")
        ][0]
        assert checkout.get("with", {}).get("fetch-depth") == 0, checkout

    def test_the_step_walks_the_pr_commit_range(self, ci):
        step = self._step(ci)
        run = str(step.get("run", ""))
        assert "github.event.pull_request.base.sha" in run, run


class TestPrePushStage:
    def test_only_pytest_runs_at_pre_push(self, precommit):
        # `default_stages` does NOT apply to hooks whose upstream manifest sets
        # its own `stages` (the pre-commit-hooks fixers list pre-push). Left
        # implicit, end-of-file-fixer ran over the whole dev..main range on the
        # v0.24.1 release push, rewrote two old KB files, and aborted the push
        # of a CI-verified commit. Every non-pytest hook must pin its stages.
        offenders = [
            hook["id"]
            for hook in _hooks(precommit)
            if not _runs_tests(hook) and hook.get("stages") is None
        ]
        assert offenders == [], f"hooks relying on default_stages (pin `stages:`): {offenders}"


class TestParallelSuite:
    # Serial: tests/ alone took 7m41s locally and ~22 min in CI. Parallel:
    # tests/ + extensions/ in ~2-3 min. CI pins -n auto; the pre-push hook
    # runs a selection (below) on a capped number of workers.
    def test_pre_push_runs_the_affected_selection_not_the_full_suite(self, precommit):
        """#356: the full suite at every push filled the disk and pushed load
        past 25 on 2026-09-24 (one push took ~35 minutes, several worktrees
        pushing at once). Locally, a fast feedback loop: the core set plus
        the tests the branch can affect. CI on the PR runs everything."""
        (hook,) = [h for h in _hooks(precommit) if _runs_tests(h)]
        assert "scripts/test-affected --run" in hook["entry"], hook["entry"]

    def test_pre_push_workers_are_capped_and_overridable(self, precommit):
        (hook,) = [h for h in _hooks(precommit) if _runs_tests(h)]
        assert "-n auto" not in hook["entry"], "uncapped workers per push (#356)"
        assert "${PYRITE_PUSH_WORKERS:-4}" in hook["entry"], hook["entry"]

    @pytest.mark.parametrize(
        ("value", "full"),
        [("1", True), ("true", True), ("yes", True), ("0", False), ("false", False)],
    )
    def test_pre_push_full_suite_is_one_variable_away(self, precommit, value, full):
        # PYRITE_PUSH_FULL=1 forces the full suite; 0/false do not (a shell
        # `${VAR:+--full}` would have treated any non-empty value as yes).
        import os
        import subprocess
        import sys

        (hook,) = [h for h in _hooks(precommit) if _runs_tests(h)]
        assert "${PYRITE_PUSH_FULL:+" not in hook["entry"]
        out = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "test-affected"), "--run", "--dry-run"]
            + ["--files", "docs/index.md"],
            capture_output=True,
            text=True,
            env={**os.environ, "PYRITE_PUSH_FULL": value},
        )
        assert out.returncode == 0, out.stderr
        assert ("tests/ extensions/" in out.stdout) is full, out.stdout

    @pytest.mark.parametrize(
        "path",
        [
            "scripts/test-affected",
            "scripts/verify-red.sh",
            "scripts/new-worktree.sh",
            "pyrite/x.py",
        ],
    )
    def test_pre_push_runs_for_scripts_without_a_py_suffix(self, precommit, path):
        # The hook's files: filter decides whether it runs at all. It matched
        # `\.py$`, so a push changing only scripts/test-affected (no suffix)
        # skipped the tests of the script that selects the tests.
        import re

        (hook,) = [h for h in _hooks(precommit) if _runs_tests(h)]
        assert re.search(hook["files"], path), (path, hook["files"])

    @pytest.mark.parametrize(
        "path",
        [
            # every path scripts/test-affected answers with the full suite ...
            "conftest.py",
            "tests/conftest.py",
            "pyproject.toml",
            "extensions/foo/pyproject.toml",
            ".pre-commit-config.yaml",
            ".github/workflows/ci.yml",
            "pytest.ini",
            "setup.cfg",
            "tox.ini",
            "tests/fixtures/roundtrip/entry.md",
            # ... and non-Python code and data it selects tests for
            "pyrite/server/templates/page.html",
            "pyrite/storage/alembic.ini",
            "pyrite/storage/alembic/script.py.mako",
            "extensions/foo/src/foo/types.yaml",
        ],
    )
    def test_pre_push_runs_for_every_code_path_test_affected_acts_on(self, precommit, path):
        # A path the selector would act on but the hook's files: filter does not
        # match is a push that runs no tests at all, reported only as "Skipped".
        import re

        (hook,) = [h for h in _hooks(precommit) if _runs_tests(h)]
        assert re.search(hook["files"], path), (path, hook["files"])

    @pytest.mark.parametrize("path", ["docs/guide.md", "kb/backlog/x.md", "README.md"])
    def test_pre_push_still_skips_docs_only_pushes(self, precommit, path):
        import re

        (hook,) = [h for h in _hooks(precommit) if _runs_tests(h)]
        assert not re.search(hook["files"], path), (path, hook["files"])

    def test_the_full_selection_covers_extensions(self):
        # --full (and every fallback) must still mean tests/ AND extensions/.
        script = (REPO / "scripts" / "test-affected").read_text()
        assert '"tests/", "extensions/"' in script

    def test_pre_push_refuses_a_worktree_with_no_venv(self, precommit):
        """No `.venv` here means the suite would test another checkout's code.

        The hook used to fall back to whatever `python` was on PATH. In a
        worktree made with a bare `git worktree add` -- no venv -- that
        resolves to the main checkout's interpreter, whose editable installs
        point at the MAIN CHECKOUT. The suite then passes or fails on code the
        push does not contain.

        Seen twice on 2026-09-21: `extensions/software-kb` reported
        `15 failed, 270 passed` against a stale main checkout and `285 passed`
        against the worktree's own source, and #269's push was blocked by
        `14 failed, 93 errors` that did not exist in the tree being pushed
        (#210, #242). Failing loudly is the whole fix: the wrong answer was
        silent, and silence is what cost the time.
        """
        (hook,) = [h for h in _hooks(precommit) if _runs_tests(h)]
        entry = hook["entry"]
        assert "|| PY=python" not in entry, (
            "the pre-push hook still falls back to system python when a "
            "worktree has no .venv, which silently runs the suite against the "
            "main checkout's code (#210)"
        )
        assert ".venv/bin/python" in entry
        assert "exit 1" in entry, "the hook must refuse rather than continue when .venv is missing"

    def test_ci_runs_the_suite_in_parallel(self, ci):
        runs = [
            str(step.get("run", ""))
            for job in ci["jobs"].values()
            for step in job["steps"]
            if "pytest" in str(step.get("run", ""))
        ]
        assert runs, "no pytest step in CI"
        assert all("-n auto" in r for r in runs), runs


class TestCIInstall:
    def test_python_jobs_install_with_uv(self, ci):
        # pip spent 80-130 s per job resolving and building seven editable
        # installs even with a warm wheel cache; uv does the same in seconds.
        # It is also the install path the README documents (uv tool install).
        job = ci["jobs"]["test"]
        install = [s for s in job["steps"] if s.get("name") == "Install dependencies"]
        assert install, "no 'Install dependencies' step"
        run = str(install[0].get("run", ""))
        assert "uv pip install" in run, run
        assert "pip install -e" not in run.replace("uv pip install -e", ""), run
        assert any("setup-uv" in str(s.get("uses", "")) for s in job["steps"])


class TestPinnedTestRunner:
    """One pytest and xdist version for every venv and every CI leg (#128).

    CI resolved pytest 9.1.1 on 3.12 and 9.0.2 on 3.13 from an unbounded
    `pytest>=8.0.0`; every worktree venv got 9.1.1, the main checkout 9.0.2.
    #81's `@classmethod` fixtures passed the one-interpreter PR gate on
    whichever pytest resolved there and broke `dev` twice on 3.13 (fixed in
    #129). Pinning `==` makes the runner identical on every interpreter and
    every venv, and makes loosening the pin a visible diff instead of a
    silent `uv pip install` drift.
    """

    _PINNED = {"pytest", "pytest-cov", "pytest-xdist"}

    def _dev_extra(self, pyproject: dict) -> list[str]:
        return pyproject["project"]["optional-dependencies"]["dev"]

    def test_dev_extra_pins_the_test_runner_exactly(self, pyproject):
        specs = self._dev_extra(pyproject)
        pinned = {}
        for spec in specs:
            for name in self._PINNED:
                if spec == name or spec.startswith(name + "=="):
                    pinned[name] = spec

        missing = self._PINNED - set(pinned)
        assert not missing, f"not pinned at all in dev extras: {missing}"

        loose = [spec for spec in pinned.values() if "==" not in spec]
        assert not loose, f"pinned package without an exact '==' pin: {loose}"

    def test_no_other_version_operator_survives_for_pinned_packages(self, pyproject):
        # >=, <=, ~=, != on a pinned package would defeat the point silently.
        specs = self._dev_extra(pyproject)
        for spec in specs:
            for name in self._PINNED:
                if spec.split("=")[0].split(">")[0].split("<")[0].split("~")[0].strip() == name:
                    assert spec.count("==") == 1 and not any(
                        op in spec for op in (">=", "<=", "~=", "!=")
                    ), spec


class TestChangeClassifier:
    """Docs/KB-only pushes must not wait for the Python suite (ADR-0032 §2).

    A required check cannot simply be path-filtered out of the workflow --
    GitHub then reports it "pending" forever and the PR can never merge -- so
    the workflow always triggers, one job classifies the change, and the heavy
    jobs skip. A skipped job satisfies a required check.
    """

    def test_a_classifier_job_exists(self, ci):
        job = ci["jobs"]["changes"]
        assert any("paths-filter" in str(s.get("uses", "")) for s in job["steps"])
        assert set(job["outputs"]) >= {"backend", "web", "kb"}

    @pytest.mark.parametrize("name", ["test", "frontend"])
    def test_heavy_jobs_are_gated_on_the_classifier(self, ci, name):
        job = ci["jobs"][name]
        assert "changes" in job.get("needs", []), f"{name} must need: changes"
        assert "needs.changes.outputs" in str(job.get("if", "")), f"{name} has no if:"

    def test_release_branch_always_runs_everything(self, ci):
        # main only moves by fast-forward to a CI-verified SHA; never let a
        # docs-only classification on main skip the proof.
        for name in ("test", "frontend"):
            assert "refs/heads/main" in str(ci["jobs"][name]["if"])

    def test_kb_changes_get_their_own_fast_check(self, ci):
        job = ci["jobs"]["kb"]
        assert "needs.changes.outputs.kb" in str(job["if"])
        steps = "\n".join(str(s.get("run", "")) for s in job["steps"])
        assert "pyrite schema validate" in steps


class TestCoverageAndE2EPolicy:
    def test_matrix_jobs_collect_coverage_only_on_the_pr_312_leg(self, ci):
        # Coverage doubled the 3.12 test step (214 s vs ~90 s) under the old
        # tracer, so the matrix ran without it. Diff coverage (0.26) needs it on
        # one leg: the pull request's 3.12 leg, with 3.12's sysmon tracer, and
        # only there; every other leg and every push stays fast.
        (step,) = [s for s in ci["jobs"]["test"]["steps"] if "--cov" in str(s.get("run", ""))]
        assert 'if [ "$COVER" = true ]' in step["run"]
        cover = step["env"]["COVER"]
        assert "github.event_name == 'pull_request'" in cover
        assert "matrix.python-version == '3.12'" in cover
        assert step["env"]["COVERAGE_CORE"] == "sysmon"

    def test_coverage_has_its_own_job_and_is_manual_for_now(self, ci):
        job = ci["jobs"]["coverage"]
        runs = "\n".join(str(s.get("run", "")) for s in job["steps"])
        assert "--cov=pyrite" in runs and "-n auto" in runs
        assert str(job["if"]).strip() == "github.event_name == 'workflow_dispatch'"

    def test_e2e_is_manual_only_until_deterministic(self, ci):
        # Non-deterministic today: a different set of specs fails every run,
        # so it carries no signal. Manual dispatch only; back on every push
        # when playwright-e2e-suite-non-deterministic-failures-... lands.
        cond = str(ci["jobs"]["e2e"]["if"]).strip()
        assert cond == "github.event_name == 'workflow_dispatch'", cond
        assert "workflow_dispatch" in ci[True] if True in ci else ci["on"]


class TestSessionSetupScript:
    def test_new_worktree_script_is_present_and_parses(self):
        import os
        import subprocess

        script = REPO / "scripts" / "new-worktree.sh"
        assert script.exists(), "ADR-0032 migration step 3: scripts/new-worktree.sh"
        assert os.access(script, os.X_OK), "must be executable"
        subprocess.run(["bash", "-n", str(script)], check=True)
        text = script.read_text()
        assert "git worktree add" in text and "pre-commit install" in text
        # The hook shim embeds the installing Python's path. Installing from a
        # worktree's venv breaks every checkout's hooks when that worktree is
        # removed; the script must install from the main checkout's venv.
        assert '"$repo_root/.venv/bin/pre-commit"' in text

    def test_new_worktree_installs_through_the_shared_setup_script(self):
        # One install path for a laptop worktree and a cloud session: a
        # second copy of the venv/extensions/config steps drifts.
        text = (REPO / "scripts" / "new-worktree.sh").read_text()
        assert "scripts/setup-checkout.sh" in text
        assert "pip install" not in text, "installs belong in setup-checkout.sh"


class TestCloudSessionSetup:
    """Claude Code on the web: a cloud session sets itself up from the repo.

    A SessionStart hook in the checked-in .claude/settings.json runs
    scripts/cloud-session-start.sh, which does nothing outside a cloud
    session (CLAUDE_CODE_REMOTE unset) and otherwise sets up the checkout
    through scripts/setup-checkout.sh -- the same script new-worktree.sh uses.
    """

    SETUP = REPO / "scripts" / "setup-checkout.sh"
    START = REPO / "scripts" / "cloud-session-start.sh"

    @pytest.mark.parametrize("name", ["setup-checkout.sh", "cloud-session-start.sh"])
    def test_script_is_present_executable_and_parses(self, name):
        import os
        import subprocess

        script = REPO / "scripts" / name
        assert script.exists()
        assert os.access(script, os.X_OK), "must be executable"
        subprocess.run(["bash", "-n", str(script)], check=True)

    def test_setup_installs_pyrite_every_extension_and_a_local_config(self):
        text = self.SETUP.read_text()
        assert "extensions/*/" in text
        assert ".pyrite/config.yaml" in text
        assert "index sync" in text

    def test_session_start_is_wired_into_checked_in_settings(self):
        import json

        settings = json.loads((REPO / ".claude" / "settings.json").read_text())
        commands = [hook for group in settings["hooks"]["SessionStart"] for hook in group["hooks"]]
        wired = [h for h in commands if "scripts/cloud-session-start.sh" in h["command"]]
        assert wired, "SessionStart must run scripts/cloud-session-start.sh"
        # Relative to the project, not to whatever directory the session is in.
        assert "$CLAUDE_PROJECT_DIR" in wired[0]["command"]
        # The default 60s hook timeout is shorter than a cold venv install.
        assert wired[0].get("timeout", 0) >= 600

    def test_session_start_is_a_no_op_outside_the_cloud(self, tmp_path):
        import os
        import subprocess

        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_REMOTE"}
        env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
        result = subprocess.run(
            ["bash", str(self.START)], env=env, capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr
        assert list(tmp_path.iterdir()) == [], "a laptop session must not be touched"

    def test_session_start_puts_the_venv_on_path_for_the_session(self):
        # A SessionStart hook's environment dies with it; CLAUDE_ENV_FILE is
        # how it reaches the session's Bash tool.
        text = self.START.read_text()
        assert "CLAUDE_ENV_FILE" in text and ".venv/bin" in text

    def test_skills_and_agents_carry_no_machine_specific_paths(self):
        # A cloud session's (or a contributor's) checkout is not one
        # maintainer's home directory. (A bare "/Users/" grep pattern is fine.)
        import re

        home = re.compile(r"/(Users|home)/[A-Za-z]")
        offenders = [
            f"{path.relative_to(REPO)}:{n}"
            for base in (REPO / ".claude" / "skills", REPO / ".claude" / "agents")
            for path in base.rglob("*.md")
            for n, line in enumerate(path.read_text().splitlines(), 1)
            if home.search(line)
        ]
        assert offenders == []

    def test_skills_and_agents_name_no_private_kb_or_plugin(self):
        # The loop must run for any contributor: the tick log's location is a
        # setting (PYRITE_CONDUCTOR_LOG_DIR), and the maintainer's private KB
        # and plugin are not part of the project's workflow.
        import re

        private = re.compile(r"tcp-kb-internal|tcp-skills|pyrite-desk|desk/notes")
        offenders = [
            f"{path.relative_to(REPO)}:{n}"
            for base in (REPO / ".claude" / "skills", REPO / ".claude" / "agents")
            for path in base.rglob("*.md")
            for n, line in enumerate(path.read_text().splitlines(), 1)
            if private.search(line)
        ]
        assert offenders == []

    def test_conductor_log_dir_setting_is_stated_once_and_gitignored(self):
        conductor = (REPO / ".claude/skills/pyrite-conductor/SKILL.md").read_text()
        meta = (REPO / ".claude/skills/pyrite-meta-conductor/SKILL.md").read_text()
        assert conductor.count("PYRITE_CONDUCTOR_LOG_DIR") >= 1
        assert "PYRITE_CONDUCTOR_LOG_DIR" in meta, "the retro must find the log"
        ignored = (REPO / ".gitignore").read_text().splitlines()
        assert ".pyrite-conductor/" in ignored, "the default log dir must be gitignored"


class TestCloudEnvironment:
    """The cloud environment is two halves (see scripts/cloud-env-setup.sh):

    a setup script, pasted once into the environment, does the slow cacheable
    install; the SessionStart hook does the cheap per-session step and starts
    the Postgres container, so the Postgres conformance tests (skipped unless
    PYRITE_TEST_PG_URL is set) run in cloud sessions with the image, user,
    password and database CI uses. Everything here runs with a stubbed uv and
    docker: no network, no Docker.
    """

    SETUP = REPO / "scripts" / "cloud-env-setup.sh"
    PG = REPO / "scripts" / "cloud-postgres.sh"

    @pytest.mark.parametrize("name", ["cloud-env-setup.sh", "cloud-postgres.sh"])
    def test_script_is_present_executable_and_parses(self, name):
        import os
        import subprocess

        script = REPO / "scripts" / name
        assert script.exists()
        assert os.access(script, os.X_OK), "must be executable"
        subprocess.run(["bash", "-n", str(script)], check=True)

    def test_postgres_image_and_credentials_match_ci(self, ci):
        import re

        service = ci["jobs"]["test"]["services"]["postgres"]
        text = self.PG.read_text()

        def value(name):
            return re.search(rf'^{name}="([^"]*)"', text, re.M).group(1)

        assert value("IMAGE") == service["image"]
        assert value("PG_PASSWORD") == service["env"]["POSTGRES_PASSWORD"]
        assert value("PG_DB") == service["env"]["POSTGRES_DB"]
        # CI's URL is the one the tests read; the scripts must build the same one.
        step_env = {
            k: v
            for step in ci["jobs"]["test"]["steps"]
            for k, v in (step.get("env") or {}).items()
            if k == "PYRITE_TEST_PG_URL"
        }
        ci_url = step_env["PYRITE_TEST_PG_URL"]
        built = f"postgresql://{value('PG_USER')}:{value('PG_PASSWORD')}@localhost:5432/{value('PG_DB')}"
        assert built == ci_url

    # -- stubs ---------------------------------------------------------

    @pytest.fixture
    def fake_docker(self, tmp_path):
        """A docker that records its calls; FAKE_PG_READY=1 makes pg_isready succeed."""
        import stat

        calls = tmp_path / "docker-calls.log"
        path = tmp_path / "bin" / "docker"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$*" >> "{calls}"\n'
            'case "$1" in\n'
            "  info|pull|start|run) exit 0 ;;\n"
            "  image) exit 1 ;;\n"
            '  ps) [ "${FAKE_RUNNING:-0}" = 1 ] && echo abc123; exit 0 ;;\n'
            '  exec) [ "${FAKE_PG_READY:-0}" = 1 ] ;;\n'
            "esac\n"
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path, calls

    @pytest.fixture
    def listener(self):
        """Something listening on a free local port, standing in for Postgres."""
        import socket

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(5)
        yield sock.getsockname()[1]
        sock.close()

    def _start(self, fake_docker, port, **extra):
        import os
        import subprocess

        docker, _ = fake_docker
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYRITE_")}
        env.update(PYRITE_DOCKER=str(docker), PYRITE_PG_PORT=str(port), PYRITE_PG_WAIT="3")
        env.update(extra)
        return subprocess.run(
            ["bash", str(self.PG), "start"], env=env, capture_output=True, text=True, timeout=60
        )

    def test_postgres_url_is_exported_only_when_the_database_is_reachable(
        self, fake_docker, listener
    ):
        result = self._start(fake_docker, listener, FAKE_PG_READY="1")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == (
            f"export PYRITE_TEST_PG_URL=postgresql://postgres:postgres@localhost:{listener}/pyrite_test"
        )

    def test_postgres_that_never_comes_up_warns_and_exports_nothing(self, fake_docker, listener):
        result = self._start(fake_docker, listener, FAKE_PG_READY="0")
        assert result.returncode == 0, "a session must not fail to start because Postgres did"
        assert result.stdout == ""
        assert "postgres tests off" in result.stderr

    def test_missing_docker_warns_and_exports_nothing(self, fake_docker, listener, tmp_path):
        result = self._start(fake_docker, listener, PYRITE_DOCKER=str(tmp_path / "no-docker"))
        assert result.returncode == 0
        assert result.stdout == ""
        assert "no docker" in result.stderr

    def test_a_running_container_is_reused_not_started_again(self, fake_docker, listener):
        _, calls = fake_docker
        result = self._start(fake_docker, listener, FAKE_PG_READY="1", FAKE_RUNNING="1")
        assert "PYRITE_TEST_PG_URL" in result.stdout
        assert not any(line.startswith("run ") for line in calls.read_text().splitlines())

    def test_opt_out_never_touches_docker(self, fake_docker, listener):
        _, calls = fake_docker
        result = self._start(fake_docker, listener, PYRITE_SETUP_POSTGRES="0", FAKE_PG_READY="1")
        assert result.returncode == 0
        assert result.stdout == ""
        assert not calls.exists(), "opted out: not even `docker info`"

    # -- the setup script, run twice in a scratch checkout ---------------

    @pytest.fixture
    def scratch_checkout(self, tmp_path, fake_docker):
        """A tiny git checkout holding the real scripts, with a stub uv."""
        import os
        import shutil
        import stat
        import subprocess

        repo = tmp_path / "checkout"
        (repo / "scripts").mkdir(parents=True)
        for name in ("cloud-env-setup.sh", "setup-checkout.sh", "cloud-postgres.sh"):
            shutil.copy(REPO / "scripts" / name, repo / "scripts" / name)
        (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
        (repo / "extensions" / "ext").mkdir(parents=True)
        (repo / "extensions" / "ext" / "pyproject.toml").write_text("[project]\nname='e'\n")
        (repo / "kb").mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)

        uv_log = tmp_path / "uv-calls.log"
        uv = fake_docker[0].parent / "uv"
        uv.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$*" >> "{uv_log}"\n'
            'if [ "$1" = venv ]; then\n'
            "  mkdir -p .venv/bin\n"
            "  for t in python pyrite pre-commit; do printf '#!/bin/sh\\nexit 0\\n' > .venv/bin/$t; chmod +x .venv/bin/$t; done\n"
            "fi\n"
            '[ "${FAKE_UV_FAIL:-0}" = 1 ] && [ "$1" = pip ] && exit 1\n'
            "exit 0\n"
        )
        uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PYRITE_", "CLAUDE"))}
        env["PATH"] = f"{uv.parent}:{env['PATH']}"
        env["PYRITE_DOCKER"] = str(fake_docker[0])
        env["PYRITE_REPO_DIR"] = str(repo)
        return repo, env, uv_log

    def _run_setup(self, repo, env, **extra):
        import subprocess

        return subprocess.run(
            ["bash", str(repo / "scripts" / "cloud-env-setup.sh")],
            cwd=repo,
            env={**env, **extra},
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_setup_script_is_idempotent(self, scratch_checkout, fake_docker):
        repo, env, uv_log = scratch_checkout
        first = self._run_setup(repo, env)
        assert first.returncode == 0, first.stderr
        calls_after_first = uv_log.read_text().splitlines()
        assert sum(c.startswith("venv") for c in calls_after_first) == 1
        assert any("-e .[server,cli,ai,dev,postgres]" in c for c in calls_after_first)
        assert any("extensions/ext/" in c for c in calls_after_first), "in-repo extensions"
        assert (repo / ".venv" / ".pyrite-install-stamp").exists()
        pulls = [c for c in fake_docker[1].read_text().splitlines() if c.startswith("pull")]
        assert pulls == ["pull -q pgvector/pgvector:pg16"]

        second = self._run_setup(repo, env)
        assert second.returncode == 0, second.stderr
        calls = uv_log.read_text().splitlines()
        assert sum(c.startswith("venv") for c in calls) == 1, "the venv is reused, not rebuilt"
        assert (repo / ".pyrite" / "config.yaml").exists()

    def test_setup_script_exits_zero_when_an_install_fails(self, scratch_checkout):
        # A non-zero exit fails the cloud session; the hook retries instead.
        repo, env, _ = scratch_checkout
        result = self._run_setup(repo, env, FAKE_UV_FAIL="1")
        assert result.returncode == 0
        assert "install failed" in result.stderr
        assert not (repo / ".venv" / ".pyrite-install-stamp").exists(), (
            "a failed install must not be stamped as done"
        )

    def test_setup_script_honours_the_postgres_opt_out(self, scratch_checkout, fake_docker):
        repo, env, _ = scratch_checkout
        result = self._run_setup(repo, env, PYRITE_SETUP_POSTGRES="0")
        assert result.returncode == 0
        assert not fake_docker[1].exists(), "opted out: no image pull"

    def test_session_hook_reinstalls_only_when_the_install_inputs_changed(self, scratch_checkout):
        import subprocess

        repo, env, uv_log = scratch_checkout
        self._run_setup(repo, env)
        before = len(uv_log.read_text().splitlines())
        hook_env = {**env, "CLAUDE_CODE_REMOTE": "true", "CLAUDE_PROJECT_DIR": str(repo)}
        hook_env["PYRITE_SETUP_POSTGRES"] = "0"

        def run_hook():
            return subprocess.run(
                ["bash", str(REPO / "scripts" / "cloud-session-start.sh")],
                env=hook_env,
                capture_output=True,
                text=True,
                timeout=60,
            )

        result = run_hook()
        assert result.returncode == 0, result.stderr
        assert len(uv_log.read_text().splitlines()) == before, "nothing changed: no reinstall"
        (repo / "pyproject.toml").write_text("[project]\nname='x'\ndependencies=['y']\n")
        assert run_hook().returncode == 0
        assert len(uv_log.read_text().splitlines()) > before, "pyproject changed: reinstall"

    def test_session_hook_exports_the_url_through_the_env_file(
        self, scratch_checkout, fake_docker, listener, tmp_path
    ):
        import subprocess

        repo, env, _ = scratch_checkout
        env_file = tmp_path / "claude-env"
        hook_env = {
            **env,
            "CLAUDE_CODE_REMOTE": "true",
            "CLAUDE_PROJECT_DIR": str(repo),
            "CLAUDE_ENV_FILE": str(env_file),
            "PYRITE_PG_PORT": str(listener),
            "PYRITE_PG_WAIT": "3",
            "FAKE_PG_READY": "1",
        }
        result = subprocess.run(
            ["bash", str(REPO / "scripts" / "cloud-session-start.sh")],
            env=hook_env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        lines = env_file.read_text().splitlines()
        assert any(l.startswith('export PATH="') and ".venv/bin" in l for l in lines)
        assert any("PYRITE_TEST_PG_URL=" in l and f":{listener}/pyrite_test" in l for l in lines)

    def test_contributing_pastes_the_line_the_script_documents_and_names_the_opt_out(self):
        line = "curl -fsSL https://raw.githubusercontent.com/pyrite-wiki/pyrite/dev/scripts/cloud-env-setup.sh | bash"
        assert line in (REPO / "CONTRIBUTING.md").read_text()
        assert line in self.SETUP.read_text()
        assert "PYRITE_SETUP_POSTGRES=0" in (REPO / "CONTRIBUTING.md").read_text()

    def test_session_hook_header_says_it_works_without_the_setup_script(self):
        header = (REPO / "scripts" / "cloud-session-start.sh").read_text().split("set -euo")[0]
        assert "no setup script ran" in header


class TestGateJob:
    """One required check that always reports (ADR-0032 §2).

    A skipped matrix job reports as `test`, not `test (3.12)`, so a docs-only
    PR whose classifier skipped the matrix could never satisfy a required
    `test (3.12)` and hung BLOCKED (PR #36, 2026-09-18). `gate` needs every
    job, runs `if: always()`, fails only on a real failure or cancellation,
    and is the only required check on dev and main.
    """

    def test_gate_needs_every_gating_job_and_always_runs(self, ci):
        job = ci["jobs"]["gate"]
        assert set(job["needs"]) >= {"changes", "kb", "test", "frontend"}
        assert str(job.get("if", "")).strip() == "always()"

    def test_gate_fails_on_failure_or_cancellation_only(self, ci):
        run = "\n".join(str(s.get("run", "")) for s in ci["jobs"]["gate"]["steps"])
        pattern = next(line for line in run.splitlines() if "grep" in line)
        assert "failure" in pattern and "cancelled" in pattern
        assert "skipped" not in pattern, "skipped must count as passing"


class TestJobPermissions:
    """Least privilege, stated (CodeQL actions/missing-workflow-permissions x8).

    Every job in ci.yml used to run with the repository's default GITHUB_TOKEN
    scope -- broader than any job here needs. A workflow-level `contents: read`
    covers checkout; a job gets its own `permissions:` block only when a step
    needs something more, so a future job that skips this is a silent
    escalation, not an oversight this suite would have caught.
    """

    def test_workflow_level_permissions_default_to_read_only(self, ci):
        assert ci["permissions"] == {"contents": "read"}

    def test_every_job_is_covered_by_a_permissions_block(self, ci):
        # Workflow-level `contents: read` covers a job with no block of its
        # own; a job that needs more must say so explicitly so the grant is
        # visible at the job, not just inherited silently. The classifier
        # job's dorny/paths-filter step needs to read PR metadata on
        # pull_request events; every other job's steps (checkout, setup-*,
        # pytest, npm, upload-artifact) work fine read-only.
        changes_job = ci["jobs"]["changes"]
        assert changes_job.get("permissions", {}).get("pull-requests") == "read", (
            "dorny/paths-filter needs pull-requests: read on pull_request events"
        )
        for name, job in ci["jobs"].items():
            if name == "changes":
                continue
            # No other job may claim more than the workflow-level default;
            # if one does, it must be a job-level block with a comment
            # justifying it (reviewed by hand, not by this test).
            assert job.get("permissions", {}).get("contents", "read") == "read", name

    def test_paths_filter_job_permissions_block_is_commented(self):
        # A grant with no reason attached is indistinguishable from a mistake
        # six months later. Require the comment live next to the block.
        text = (REPO / ".github" / "workflows" / "ci.yml").read_text()
        # Anchor on the step that USES paths-filter, not any prose mentioning
        # it (a comment above the permissions: block would otherwise match
        # first and cut the block out of `before`).
        idx = text.index("uses: dorny/paths-filter")
        before = text[:idx]
        changes_idx = before.rindex("\n  changes:")
        block = text[changes_idx:idx]
        assert "permissions:" in block
        assert "pull-requests: read" in block
        assert "#" in block, "the grant needs a one-line reason in a comment"


class TestSmokeLayer:
    """ADR-0032 §3a's "breadth" row: prove the assembled thing, not the units.

    The matrix proves the code on three interpreters. Nothing proved a real
    server process, a real client and the documented CLI worked together --
    and all three bugs the outside contributor found (PRs #3, #4, #5) lived in
    exactly that gap. The smoke layer closes it on the push to dev, where it
    costs minutes that no pull request has to wait for.
    """

    def test_e2e_marker_is_declared(self, pyproject):
        markers = pyproject["tool"]["pytest"]["ini_options"]["markers"]
        assert any(m.startswith("e2e:") for m in markers), markers

    def test_default_run_excludes_e2e(self, pyproject):
        # tests/e2e lives under testpaths, so without this every `pytest
        # tests/` -- including the pre-push hook and the PR matrix -- would
        # start server subprocesses and blow the ~3 min PR budget.
        addopts = pyproject["tool"]["pytest"]["ini_options"]["addopts"]
        assert "not e2e" in addopts, addopts

    def test_smoke_job_is_gated_on_dev_or_dispatch(self, ci):
        cond = str(ci["jobs"]["smoke"]["if"])
        assert "refs/heads/dev" in cond, cond
        assert "workflow_dispatch" in cond, cond
        assert "pull_request" not in cond, "smoke must never run on a PR"

    def test_smoke_job_needs_the_classifier(self, ci):
        assert "changes" in ci["jobs"]["smoke"]["needs"]

    def test_smoke_is_not_a_required_check(self, ci):
        # `gate` is the one required check. Adding smoke to its needs would
        # make a minutes-long job block every PR -- the opposite of the point.
        assert "smoke" not in ci["jobs"]["gate"]["needs"]

    def test_smoke_job_runs_the_e2e_marker_and_the_tutorial(self, ci):
        runs = "\n".join(str(s.get("run", "")) for s in ci["jobs"]["smoke"]["steps"])
        assert "-m e2e" in runs and "tests/e2e" in runs, runs
        assert "run_tutorial.sh" in runs, runs

    def test_smoke_keeps_each_file_on_one_worker(self, ci):
        # The server fixtures are module-scoped and xdist re-runs those per
        # worker. Under the default `load` distribution one file's tests scatter
        # across workers and each starts its own server: 46 s instead of 22 s.
        runs = "\n".join(str(s.get("run", "")) for s in ci["jobs"]["smoke"]["steps"])
        assert "--dist loadfile" in runs, runs

    def test_smoke_job_installs_the_full_surface_like_test(self, ci):
        runs = "\n".join(str(s.get("run", "")) for s in ci["jobs"]["smoke"]["steps"])
        assert "uv pip install" in runs, runs
        assert ".[all]" in runs, runs
        assert "extensions/*/" in runs, "extensions are separate distributions"

    def test_tutorial_runner_exists_and_parses(self):
        import os
        import subprocess

        script = REPO / "scripts" / "run_tutorial.sh"
        assert script.exists(), "docs-as-tests runner for docs/getting-started.md"
        assert os.access(script, os.X_OK), "must be executable"
        subprocess.run(["bash", "-n", str(script)], check=True)


class TestNoTrackedFileIsGitignored:
    """A file both tracked and gitignored produces add/add conflicts (#122/#107).

    `.claude/THEME.md` was gitignored (`.gitignore:101`) after it was already
    committed, so the ignore rule only ever blocked *new* adds -- the tracked
    blob kept riding every branch cut from `dev`, and two branches that both
    modify it can still hit an add/add conflict. `git rm --cached` is the fix;
    this test pins that no path under `.claude/` is ever tracked-and-ignored
    again.
    """

    def test_no_tracked_path_under_claude_is_gitignored(self):
        import subprocess

        tracked_and_ignored = subprocess.run(
            ["git", "ls-files", "-ci", "--exclude-standard", "--", ".claude/"],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert tracked_and_ignored == "", (
            f"tracked but gitignored under .claude/ (add/add-conflict risk): "
            f"{tracked_and_ignored!r}"
        )


class TestInfraChangesRunTheFullMatrixOnAPR:
    """A test-infrastructure change is a property of the interpreter, not the code (#133).

    PR #81 changed class-scoped fixtures to `@classmethod` over `@pytest.fixture`
    to clear a deprecation warning. It registered and worked on 3.12 -- the PR
    gate's only interpreter -- and silently failed to register the fixture at
    all on 3.13, going undetected until the push to `dev` ran the full matrix
    two minutes after merge. A single-interpreter gate cannot see a change
    whose behaviour is a property of the interpreter and pytest version, not
    of the project's own logic -- fixture declarations, collection hooks,
    pytest config, the hooks and CI that enforce them. Those diffs widen the
    PR gate's matrix to the same three interpreters `dev` already runs;
    everything else keeps the fast one-interpreter gate.
    """

    _INFRA_PATHS = [
        "tests/conftest.py",
        "tests/sub/conftest.py",
        "pyproject.toml",
        ".pre-commit-config.yaml",
        ".github/workflows/ci.yml",
        "scripts/new-worktree.sh",
        "scripts/check_import_cycles.py",
    ]

    def test_classifier_has_an_infra_output(self, ci):
        job = ci["jobs"]["changes"]
        assert "infra" in job["outputs"]

    @pytest.mark.parametrize("path", _INFRA_PATHS)
    def test_infra_filter_matches_test_infrastructure_paths(self, ci, path):
        import fnmatch

        filters = yaml.safe_load(
            next(
                s["with"]["filters"]
                for s in ci["jobs"]["changes"]["steps"]
                if "with" in s and "filters" in s.get("with", {})
            )
        )
        patterns = filters["infra"]
        assert any(fnmatch.fnmatch(path, pat.lstrip("- ")) for pat in patterns), (
            f"{path!r} not matched by any infra pattern: {patterns}"
        )

    def test_test_job_matrix_widens_on_infra_changes(self, ci):
        # Normally: one interpreter on a PR, the full matrix on a push. An
        # infra-touching PR must get the full matrix too, same as a push.
        matrix = str(ci["jobs"]["test"]["strategy"]["matrix"]["python-version"])
        assert "needs.changes.outputs.infra" in matrix, matrix
        assert '["3.12"]' in matrix
        assert '["3.11", "3.12", "3.13"]' in matrix

    def test_dev_push_behaviour_is_unchanged(self, ci):
        # The push to dev already runs the full matrix unconditionally; the
        # infra classifier must only ever WIDEN a pull_request's matrix, never
        # narrow a push's.
        matrix = str(ci["jobs"]["test"]["strategy"]["matrix"]["python-version"])
        assert "github.event_name == 'pull_request'" in matrix


class TestRunningTheTestsDoc:
    """CONTRIBUTING's "Running the tests" names a marker, two variables and a
    script; each must still exist where the doc says it does (#356)."""

    @pytest.fixture(scope="class")
    def section(self) -> str:
        text = (REPO / "CONTRIBUTING.md").read_text()
        start = text.index("## Running the tests")
        return text[start : text.index("\n## ", start + 1)]

    def test_the_core_marker_is_registered(self, section, pyproject):
        assert "-m core" in section
        markers = pyproject["tool"]["pytest"]["ini_options"]["markers"]
        assert any(m.startswith("core:") for m in markers), markers

    def test_the_push_variables_are_the_ones_the_hook_and_script_read(self, section, precommit):
        (hook,) = [h for h in _hooks(precommit) if _runs_tests(h)]
        assert "PYRITE_PUSH_WORKERS" in section and "PYRITE_PUSH_WORKERS" in hook["entry"]
        script = (REPO / "scripts" / "test-affected").read_text()
        assert "PYRITE_PUSH_FULL" in section and '"PYRITE_PUSH_FULL"' in script
        assert "PYRITE_PUSH_FORCE" in section and '"PYRITE_PUSH_FORCE"' in script

    def test_the_last_failed_rerun_is_documented(self, section):
        assert "scripts/test-affected --run -- --lf" in section

    def test_the_documented_commands_exist(self, section):
        assert "scripts/test-affected --run" in section
        assert (REPO / "scripts" / "test-affected").exists()
        assert (REPO / "scripts" / "run_tutorial.sh").exists()
        assert "--dist loadfile" in section and (REPO / "tests" / "e2e").is_dir()


class TestDevPushReusesTheMergeQueuesPass:
    """Each commit is tested once (maintainer, 2026-09-25).

    The merge queue runs the full matrix and the frontend on the exact commit
    that then lands on dev -- its head_sha is the push's SHA. The push to dev
    used to run both again on that same commit. Now a successful merge_group
    run for the SHA skips them on the push; smoke still runs (the queue does
    not run it), and main and workflow_dispatch never skip.
    """

    @pytest.fixture(scope="class")
    def step(self, ci) -> dict:
        (step,) = [s for s in ci["jobs"]["changes"]["steps"] if s.get("id") == "queue"]
        return step

    def test_the_check_runs_only_on_a_push_to_dev(self, step):
        # Not on main (it only moves to a proven SHA and always runs
        # everything), not on workflow_dispatch, never on a PR.
        cond = " ".join(str(step["if"]).split())
        assert cond == "github.event_name == 'push' && github.ref == 'refs/heads/dev'", cond

    def test_the_classifier_exposes_the_answer(self, ci):
        # Like every classifier output, it reads the merge_group step first
        # (tests/test_ci_workflow_triggers.py), which says "false": the queue
        # itself never skips the matrix it exists to run.
        assert ci["jobs"]["changes"]["outputs"]["queue_passed"] == (
            "${{ steps.all.outputs.queue_passed || steps.queue.outputs.passed }}"
        )
        (fallback,) = [s for s in ci["jobs"]["changes"]["steps"] if s.get("id") == "all"]
        assert 'echo "queue_passed=false"' in fallback["run"]

    def test_the_classifier_may_read_workflow_runs(self, ci):
        assert ci["jobs"]["changes"]["permissions"].get("actions") == "read"

    def test_it_asks_for_a_successful_merge_group_run_of_this_sha(self, step):
        run = step["run"]
        assert "actions/workflows/ci.yml/runs" in run
        assert "event=merge_group" in run and "head_sha=$GITHUB_SHA" in run
        assert '.conclusion == \\"success\\"' in run

    @pytest.mark.parametrize("name", ["test", "frontend"])
    def test_test_and_frontend_skip_when_the_queue_passed(self, ci, name):
        cond = str(ci["jobs"][name]["if"])
        assert "needs.changes.outputs.queue_passed != 'true'" in cond, cond
        # The skip is ANDed onto the existing condition, never ORed.
        assert cond.startswith("(") and ") && needs.changes.outputs.queue_passed" in cond, cond

    @pytest.mark.control(reason="pins that the skip stays off jobs that never had it")
    @pytest.mark.parametrize("name", ["smoke", "kb", "gate"])
    def test_smoke_kb_and_gate_do_not_skip(self, ci, name):
        assert "queue_passed" not in str(ci["jobs"][name].get("if", "")), name

    @pytest.fixture
    def run_step(self, step, tmp_path):
        """Run the step's script with a stub `gh` that prints a given count,
        or fails; return what it wrote to GITHUB_OUTPUT."""
        import os
        import subprocess

        def run(gh_body: str) -> str:
            bindir = tmp_path / "bin"
            bindir.mkdir(exist_ok=True)
            gh = bindir / "gh"
            gh.write_text(f"#!/bin/sh\n{gh_body}\n")
            gh.chmod(0o755)
            out = tmp_path / "out"
            out.write_text("")
            env = {
                **os.environ,
                "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                "GITHUB_REPOSITORY": "o/r",
                "GITHUB_SHA": "a" * 40,
                "GITHUB_OUTPUT": str(out),
                "GH_TOKEN": "x",
            }
            subprocess.run(["bash", "-e", "-o", "pipefail", "-c", step["run"]], env=env, check=True)
            return out.read_text()

        return run

    def test_a_successful_queue_run_skips(self, run_step):
        assert run_step("echo 1") == "passed=true\n"

    def test_no_queue_run_runs_everything(self, run_step):
        assert run_step("echo 0") == "passed=false\n"

    def test_an_api_failure_runs_everything(self, run_step):
        assert run_step("echo 'Not Found' >&2; exit 1") == "passed=false\n"

    def test_garbage_from_the_api_runs_everything(self, run_step):
        assert run_step('echo \'{"message": "x"}\'') == "passed=false\n"


class TestExperimentalLayer:
    """A red required check means the core broke (#657).

    Tests of experimental surfaces (tests/experimental_surface.py) are
    deselected from `test`, the job `gate` needs, and run in `experimental`,
    which `gate` does not need. That job fails only on a failure missing from
    tests/experimental_known_failures.txt, so it is red only for news. On a
    push to `dev` a separate job, the only one with `issues: write`, files the
    news as an `experimental-broken` issue; on a pull request the news goes to
    the job summary only.
    """

    NOT_EXPERIMENTAL = '-m "not slow and not e2e and not experimental"'

    def _runs(self, job: dict) -> str:
        return "\n".join(str(s.get("run", "")) for s in job["steps"])

    def test_the_gating_job_deselects_experimental_tests(self, ci):
        assert self.NOT_EXPERIMENTAL in self._runs(ci["jobs"]["test"])

    @pytest.mark.control(reason="a still-true guard: #657 must not change it")
    def test_the_gating_matrix_is_unchanged(self, ci):
        # The merge queue's full matrix covers the core, as before.
        matrix = str(ci["jobs"]["test"]["strategy"]["matrix"]["python-version"])
        assert "3.11" in matrix and "3.13" in matrix and "pull_request" in matrix

    @pytest.mark.control(reason="a still-true guard: #657 must not change it")
    def test_gate_needs_neither_experimental_job(self, ci):
        needs = set(ci["jobs"]["gate"]["needs"])
        assert "experimental" not in needs and "experimental-issues" not in needs

    def test_the_experimental_job_runs_on_pull_requests_and_dev(self, ci):
        job = ci["jobs"]["experimental"]
        cond = str(job["if"])
        assert "changes" in job["needs"]
        assert "github.event_name == 'pull_request'" in cond
        assert "refs/heads/dev" in cond
        assert "merge_group" not in cond, "the queue proves the core; this is not a gate"

    def test_the_experimental_job_runs_the_marker_into_a_junit_report(self, ci):
        runs = self._runs(ci["jobs"]["experimental"])
        assert '-m "experimental and not slow and not e2e"' in runs
        assert "junit_family=xunit1" in runs and "--junitxml=experimental.xml" in runs
        assert "--continue-on-collection-errors" in runs

    def test_only_the_ratchet_decides_the_job(self, ci):
        steps = ci["jobs"]["experimental"]["steps"]
        (tests,) = [s for s in steps if "pytest" in str(s.get("run", ""))]
        assert tests.get("continue-on-error") is True or "|| true" in tests["run"]
        (check,) = [s for s in steps if "experimental_ratchet.py check" in str(s.get("run", ""))]
        assert "tests/experimental_known_failures.txt" in check["run"]
        assert "$GITHUB_STEP_SUMMARY" in check["run"]
        assert "--base-known" in check["run"], "the known-failures list can only shrink"
        assert not check.get("continue-on-error")

    def test_the_experimental_job_has_postgres_like_test(self, ci):
        assert ci["jobs"]["experimental"]["services"] == ci["jobs"]["test"]["services"]

    def test_the_experimental_job_cannot_write_issues(self, ci):
        perms = ci["jobs"]["experimental"].get("permissions", {})
        assert perms.get("issues", "none") in ("none", "read")

    def test_issues_are_filed_only_on_a_push_to_dev(self, ci):
        job = ci["jobs"]["experimental-issues"]
        cond = str(job["if"])
        assert "github.event_name == 'push'" in cond and "refs/heads/dev" in cond
        assert "experimental" in job["needs"]
        assert job["permissions"] == {"contents": "read", "issues": "write"}
        runs = self._runs(job)
        assert "experimental_ratchet.py file-issues" in runs
        envs = [s.get("env", {}) for s in job["steps"]]
        tokens = {v for e in envs for k, v in e.items() if "TOKEN" in k}
        assert tokens == {"${{ github.token }}"}, "no secret beyond the workflow token"

    def test_no_other_job_may_write_issues(self, ci):
        writers = [
            n for n, j in ci["jobs"].items() if j.get("permissions", {}).get("issues") == "write"
        ]
        assert writers == ["experimental-issues"]

    def test_the_pre_push_hook_runs_the_core_only(self):
        script = (REPO / "scripts" / "test-affected").read_text()
        assert "not experimental" in script
        assert '"PYRITE_PUSH_EXPERIMENTAL"' in script

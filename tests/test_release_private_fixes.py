"""A release stops while the private fixes repository holds work dev lacks.

The rule: a release stops while the private fixes branch holds commits dev
lacks. Security fixes are developed privately and carried to `dev` before a
release. Step (a) asks the private remote (`--private-remote`, default
`security`) whether its `dev` holds any commit whose change is not on the
commit being released, and stops when it does. A checkout with no such remote
stops too, unless the run says this project keeps none
(`--no-private-remote`): a check that passes because it could not look
checks nothing.

Driven against real git repositories: the property is about what git says
is on which branch.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import release  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, timeout=60
    ).stdout.strip()


def _commit(repo: Path, name: str, text: str, message: str) -> str:
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    """A public checkout on `dev`, and a private repository whose `dev`
    starts at the same commit, added as the remote `security`."""
    public = tmp_path / "public"
    public.mkdir()
    _git(public, "init", "-q", "-b", "dev")
    _commit(public, "a.txt", "a\n", "base")
    private = tmp_path / "private.git"
    _git(tmp_path, "clone", "-q", "--bare", str(public), str(private))
    _git(public, "remote", "add", "security", str(private))
    work = tmp_path / "private-work"
    _git(tmp_path, "clone", "-q", "-b", "dev", str(private), str(work))
    return public, work


def _push_private(work: Path) -> None:
    _git(work, "push", "-q", "origin", "dev")


def test_a_private_fix_not_on_dev_stops_the_release(repos):
    public, work = repos
    _commit(work, "fix.txt", "fixed\n", "fix: a stranded private fix")
    _push_private(work)
    sha = _git(public, "rev-parse", "HEAD")
    with pytest.raises(release.ReleaseError) as e:
        release.check_private_fixes_are_on_dev(public, sha, "security")
    assert "a stranded private fix" in str(e.value)
    assert "1 commit" in str(e.value)


def test_a_fix_carried_to_dev_as_the_same_change_lets_it_pass(repos):
    public, work = repos
    fix = _commit(work, "fix.txt", "fixed\n", "fix: a private fix")
    _push_private(work)
    # Carried across by cherry-pick: a new commit with the same change.
    _git(public, "fetch", "-q", "security", "dev")
    _git(
        public,
        "-c",
        "user.email=t@example.invalid",
        "-c",
        "user.name=t",
        "cherry-pick",
        fix,
    )
    sha = _git(public, "rev-parse", "HEAD")
    release.check_private_fixes_are_on_dev(public, sha, "security")


@pytest.mark.control(reason="a private dev equal to dev was never refused")
def test_a_private_dev_with_nothing_new_passes(repos):
    public, _ = repos
    release.check_private_fixes_are_on_dev(public, _git(public, "rev-parse", "HEAD"), "security")


def test_a_checkout_without_the_private_remote_stops_and_says_how_to_proceed(repos):
    public, _ = repos
    _git(public, "remote", "remove", "security")
    with pytest.raises(release.ReleaseError) as e:
        release.check_private_fixes_are_on_dev(
            public, _git(public, "rev-parse", "HEAD"), "security"
        )
    assert "--no-private-remote" in str(e.value)


def test_a_dry_run_names_the_stranded_fix_and_no_private_remote_skips_it(
    repos, monkeypatch, capsys
):
    public, work = repos
    _commit(work, "fix.txt", "fixed\n", "fix: stranded")
    _push_private(work)
    sha = _git(public, "rev-parse", "HEAD")

    def step(argv):
        args = release.parse_args(["0.24.2", "--repo", str(public), *argv])
        ctx = release.Context(
            repo=public,
            version="0.24.2",
            runner=release.Runner(execute=False),
            wait_ci_minutes=0,
            private_remote=args.private_remote,
            sha=sha,
        )
        release.step_private_fixes(ctx)
        return ctx

    assert any("stranded" in why for _, why in step([]).problems)
    skipped = step(["--no-private-remote"])
    assert skipped.problems == []
    assert "not checked" in capsys.readouterr().out

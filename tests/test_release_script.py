"""Tests for scripts/release.py -- the one command from a CI-verified `dev`
commit to a GitHub release.

v0.24.1 took ten manual steps across two sessions, one of which pushed with
`--no-verify` because the pre-push hooks ran fixers over the wrong range. The
script mechanizes the runbook; these tests pin the parts that must be right
before anything irreversible runs:

- the preconditions (version, CHANGELOG shape, clean checkout at origin/dev)
- notes extraction (the CHANGELOG section, the contributors line)
- the CI-status decision, given mocked `gh` JSON
- step ordering: no irreversible step precedes a check
- the safety rules: no --no-verify, no force push, no delete, and --dry-run
  as the default

No test performs network or git pushes. The `--dry-run` pass over this repo
runs with every subprocess call monkeypatched.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import release  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# fixtures: a good checkout, and each way it can be wrong
# --------------------------------------------------------------------------

GOOD_CHANGELOG = """# Changelog

All notable changes to Pyrite will be documented in this file.

## [Unreleased]

## [0.24.2] - {today} - Operational

### Added

- A release script.

## [0.24.1] - 2026-09-17

### Fixed

- Something older.
"""

GOOD_PYPROJECT = """[project]
name = "pyrite"
version = "0.24.2"
"""


@pytest.fixture
def repo(tmp_path):
    """A tmp checkout whose CHANGELOG and pyproject are release-ready."""
    (tmp_path / "CHANGELOG.md").write_text(GOOD_CHANGELOG.format(today=date.today().isoformat()))
    (tmp_path / "pyproject.toml").write_text(GOOD_PYPROJECT)
    return tmp_path


# A full SHA: the verified-commit lookup refuses to ask about anything shorter
# (the API would resolve an abbreviation or a branch name for itself).
FAKE_SHA = "abc1234def5678" + "0" * 26


def _as_ci_reports(run: dict) -> dict:
    """A run as this repository's CI reports it: for FAKE_SHA, from the GitHub
    Actions app, in a check suite. The shared verdict counts no other kind."""
    return {
        "head_sha": FAKE_SHA,
        "app": {"slug": "github-actions"},
        "check_suite": {"id": 1},
        **run,
    }


def check_runs(*runs: dict) -> str:
    """The body `gh api .../check-runs` returns for FAKE_SHA: every run is for
    that commit and the page is whole, as the shared reader requires."""
    return release.json.dumps(
        {"total_count": len(runs), "check_runs": [_as_ci_reports(r) for r in runs]}
    )


@pytest.fixture
def no_network(monkeypatch):
    """Every external call stubbed. Returns the list of commands attempted, so
    a test can assert that a dry run shells out only to reads.

    `time.sleep` raises rather than sleeping: `--wait-ci` now defaults to a real
    wait, and a test that reaches a pending CI verdict must say `--wait-ci 0`
    rather than spend fifteen minutes of the suite.
    """

    def no_sleeping(seconds):
        raise AssertionError(f"a test slept {seconds}s: pass --wait-ci 0 or stub the clock")

    monkeypatch.setattr(release.time, "sleep", no_sleeping)
    calls = []

    def fake_check_output(cmd, **kwargs):
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if "status --porcelain" in joined:
            return ""
        if "rev-parse --abbrev-ref" in joined:
            return "dev"
        if "rev-parse" in joined:
            return FAKE_SHA
        if "remote get-url" in joined:
            return "git@github.com:markramm/pyrite.git\n"
        if "tag -l" in joined:
            return ""  # the tag does not exist locally
        if "ls-remote" in joined:
            return ""  # nor on origin
        if cmd[:3] == ["gh", "release", "view"]:
            raise release.ReleaseError("release not found")  # nor as a release
        if "fetch" in joined:
            return ""
        if "merge-base" in joined:
            return ""  # origin/main is an ancestor
        if "check-runs" in joined:
            # `gate` green, `e2e` red: the release must proceed anyway.
            return check_runs(
                {"name": "gate", "status": "completed", "conclusion": "success"},
                {"name": "e2e", "status": "completed", "conclusion": "failure"},
            )
        if "gh pr list" in joined:
            return "[]"
        if "gh label list" in joined:
            return '[{"name":"release-blocker"}]'
        if "describe --tags" in joined:
            return "v0.24.1"
        if "gh api" in joined:
            return "2026-09-17T00:00:00Z"
        return ""

    monkeypatch.setattr(release, "_check_output", fake_check_output)
    return calls


@pytest.fixture
def dry_run(no_network, monkeypatch, tmp_path_factory):
    """A complete `--dry-run` pass with nothing shelling out.

    `--repo` points at a throwaway checkout whose CHANGELOG is the state a
    release actually starts from -- the version's section dated today and NO
    `[Unreleased]` heading -- so step e runs its whole path instead of taking
    the "already reopened; nothing to do" exit that this repo's own CHANGELOG
    would give it.

    Returns (exit_code, runner, attempted_commands); `runner.planned` is every
    write the run would have performed.
    """
    fake_repo = tmp_path_factory.mktemp("release-repo")
    (fake_repo / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [0.24.2] - {date.today().isoformat()}\n\n"
        "- A release script.\n\n## [0.24.1] - 2026-09-17\n\n- Older.\n"
    )
    (fake_repo / "pyproject.toml").write_text(GOOD_PYPROJECT)

    def _go(argv=("0.24.2",)):
        args = release.parse_args([*argv, "--repo", str(fake_repo)])
        code, runner = release.run_release(args)
        return code, runner, no_network

    return _go


@pytest.fixture
def dry_run_commands(dry_run):
    _code, runner, _calls = dry_run()
    return runner.planned


@pytest.fixture
def every_composed_command(dry_run, monkeypatch):
    """Every command the script composes -- planned writes AND attempted reads
    -- across the happy path and each failure path.

    The safety rules ("never force-pushes", "never starts a CI run", "never
    deletes a ref") are about what runs, not about what strings appear in the
    source: a scan for the literal `--delete` misses `git push -d`, and one for
    `e2e` forbids a word the module legitimately discusses. This collects the
    real thing.
    """
    collected: list[list[str]] = []

    def sweep(argv=("0.24.2",), before=None):
        undo = pytest.MonkeyPatch()
        if before is not None:
            before(undo)
        try:
            code, runner, calls = dry_run(argv)
        finally:
            undo.undo()
        collected.extend(list(c) for c in calls)
        collected.extend(list(c) for c in runner.planned)
        return code

    sweep()
    sweep(("0.24.2", "--require-check", "e2e"))
    sweep(("0.24.2", "--skip-install-check"))

    # `--execute` composes the most commands of any path (step c's real
    # install, every write in step d and e). `_check_output` is stubbed, so
    # nothing escapes; `shutil.which` is pinned so the set of composed commands
    # is the same whether or not this machine has uv and docker.
    def with_tools(undo):
        undo.setattr(release.shutil, "which", lambda name: f"/usr/bin/{name}")

    sweep(("0.24.2", "--execute"), before=with_tools)

    # each failure path: a red CI verdict, a missing one, a pending one.
    # `--wait-ci 0` on the pending sweep: the default is now a real wait.
    for payload, argv in (
        (
            check_runs({"name": "gate", "status": "completed", "conclusion": "failure"}),
            ("0.24.2",),
        ),
        (check_runs(), ("0.24.2",)),
        (
            check_runs({"name": "gate", "status": "in_progress", "conclusion": None}),
            ("0.24.2", "--wait-ci", "0"),
        ),
    ):
        original = release._check_output

        def ci(undo, _payload=payload, _original=original):
            def patched(cmd, **kwargs):
                if "check-runs" in " ".join(cmd):
                    return _payload
                return _original(cmd, **kwargs)

            undo.setattr(release, "_check_output", patched)

        sweep(argv=argv, before=ci)

    # a failed precondition
    def bad_version(undo):
        undo.setattr(
            release,
            "check_version_matches",
            lambda repo, v: (_ for _ in ()).throw(release.ReleaseError("mismatch")),
        )

    sweep(before=bad_version)
    return collected


# --------------------------------------------------------------------------
# version / changelog validation
# --------------------------------------------------------------------------


class TestReadVersion:
    def test_reads_the_project_version(self, repo):
        assert release.read_pyproject_version(repo) == "0.24.2"

    def test_missing_pyproject_is_an_error(self, tmp_path):
        with pytest.raises(release.ReleaseError, match="pyproject.toml"):
            release.read_pyproject_version(tmp_path)


class TestValidateVersionMatches:
    def test_matching_version_passes(self, repo):
        release.check_version_matches(repo, "0.24.2")  # no raise

    def test_mismatched_version_names_both(self, repo):
        with pytest.raises(release.ReleaseError) as excinfo:
            release.check_version_matches(repo, "0.25.0")
        message = str(excinfo.value)
        assert "0.24.2" in message and "0.25.0" in message

    def test_a_v_prefix_is_rejected_not_silently_stripped(self):
        with pytest.raises(release.ReleaseError, match="X.Y.Z"):
            release.normalize_version("v0.24.2")

    def test_non_semver_is_rejected(self):
        with pytest.raises(release.ReleaseError, match="X.Y.Z"):
            release.normalize_version("0.24")

    def test_plain_semver_is_accepted(self):
        assert release.normalize_version("0.24.2") == "0.24.2"


class TestChangelogValidation:
    def test_a_dated_section_with_content_passes(self, repo):
        release.check_changelog(repo, "0.24.2")  # no raise

    def test_missing_section_is_an_error(self, repo):
        with pytest.raises(release.ReleaseError, match=r"0\.25\.0"):
            release.check_changelog(repo, "0.25.0")

    def test_section_dated_other_than_today_is_an_error(self, repo):
        (repo / "CHANGELOG.md").write_text(GOOD_CHANGELOG.format(today="2020-01-01"))
        with pytest.raises(release.ReleaseError, match="2020-01-01"):
            release.check_changelog(repo, "0.24.2")

    def test_undated_section_is_an_error(self, repo):
        (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [0.24.2]\n\n### Added\n\n- Thing.\n")
        with pytest.raises(release.ReleaseError, match="YYYY-MM-DD"):
            release.check_changelog(repo, "0.24.2")

    def test_empty_section_is_an_error(self, repo):
        (repo / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [0.24.2] - {date.today().isoformat()}\n\n"
            "## [0.24.1] - 2026-09-17\n\n- Older.\n"
        )
        with pytest.raises(release.ReleaseError, match="no content"):
            release.check_changelog(repo, "0.24.2")

    def test_unreleased_below_the_section_is_an_error(self, repo):
        """`[Unreleased]` must be empty or absent: entries under it below the
        released section are changes that would silently miss the release."""
        (repo / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [Unreleased]\n\n### Added\n\n- Not shipped yet.\n\n"
            f"## [0.24.2] - {date.today().isoformat()}\n\n- Thing.\n"
        )
        with pytest.raises(release.ReleaseError, match="Unreleased"):
            release.check_changelog(repo, "0.24.2")

    def test_absent_unreleased_is_fine(self, repo):
        (repo / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [0.24.2] - {date.today().isoformat()}\n\n- Thing.\n"
        )
        release.check_changelog(repo, "0.24.2")  # no raise


# --------------------------------------------------------------------------
# notes extraction
# --------------------------------------------------------------------------


class TestExtractNotes:
    def test_takes_the_section_body_only(self, repo):
        notes = release.release_notes_for(repo, "0.24.2")
        assert "- A release script." in notes
        assert "0.24.1" not in notes
        assert "Unreleased" not in notes

    def test_does_not_include_the_heading_itself(self, repo):
        notes = release.release_notes_for(repo, "0.24.2")
        assert not notes.lstrip().startswith("## [0.24.2]")

    def test_last_section_in_the_file_still_extracts(self, repo):
        (repo / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [0.24.2] - {date.today().isoformat()}\n\n- Only one.\n"
        )
        assert "- Only one." in release.release_notes_for(repo, "0.24.2")

    def test_missing_section_is_an_error(self, repo):
        with pytest.raises(release.ReleaseError):
            release.release_notes_for(repo, "9.9.9")


class TestChangelogFencedBlocks:
    """A fenced code block may legitimately contain a `## ` line -- release
    notes quote CHANGELOG syntax, and a runbook excerpt shows headings. Scanning
    for `^##\\s` without fence awareness truncates the notes there and makes
    `check_changelog` invent a stranded `[Unreleased]`."""

    def _changelog(self, body: str) -> str:
        return f"# Changelog\n\n## [0.24.2] - {date.today().isoformat()}\n\n{body}\n## [0.24.1] - 2026-09-17\n\n- Older.\n"

    FENCED_BODY = "- see:\n\n```md\n## [Unreleased]\n- fake\n```\n\n- real entry\n"

    def test_a_heading_inside_a_fence_does_not_truncate_the_notes(self, repo):
        (repo / "CHANGELOG.md").write_text(self._changelog(self.FENCED_BODY))
        notes = release.release_notes_for(repo, "0.24.2")
        assert "- real entry" in notes
        assert "- Older." not in notes

    def test_a_fenced_unreleased_heading_is_not_a_stranded_section(self, repo):
        (repo / "CHANGELOG.md").write_text(self._changelog(self.FENCED_BODY))
        release.check_changelog(repo, "0.24.2")  # no raise

    def test_a_tilde_fence_is_handled_too(self, repo):
        body = "- see:\n\n~~~\n## [Unreleased]\n~~~\n\n- real entry\n"
        (repo / "CHANGELOG.md").write_text(self._changelog(body))
        assert "- real entry" in release.release_notes_for(repo, "0.24.2")

    def test_a_real_unreleased_section_is_still_caught(self, repo):
        """The fence stripping must not blind the check to a genuine one."""
        (repo / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [Unreleased]\n\n- Not shipped.\n\n"
            f"## [0.24.2] - {date.today().isoformat()}\n\n- Thing.\n"
        )
        with pytest.raises(release.ReleaseError, match="Unreleased"):
            release.check_changelog(repo, "0.24.2")


class TestChangelogDuplicateHeading:
    """Two `## [X.Y.Z]` headings mean the notes are ambiguous and the first
    silently wins -- which is how half a release's notes go missing."""

    def test_a_duplicate_version_heading_is_an_error(self, repo):
        (repo / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [0.24.2] - {date.today().isoformat()}\n\n- First.\n\n"
            f"## [0.24.2] - {date.today().isoformat()}\n\n- Second.\n"
        )
        with pytest.raises(release.ReleaseError, match="twice|duplicate"):
            release.check_changelog(repo, "0.24.2")

    def test_release_notes_also_refuse_a_duplicate(self, repo):
        (repo / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [0.24.2] - {date.today().isoformat()}\n\n- First.\n\n"
            f"## [0.24.2] - {date.today().isoformat()}\n\n- Second.\n"
        )
        with pytest.raises(release.ReleaseError, match="twice|duplicate"):
            release.release_notes_for(repo, "0.24.2")


class TestMalformedInputsAreReleaseErrors:
    """A traceback tells the maintainer nothing on release day; a ReleaseError
    names the file and what to do."""

    def test_missing_changelog_is_a_release_error(self, repo):
        (repo / "CHANGELOG.md").unlink()
        with pytest.raises(release.ReleaseError, match="CHANGELOG.md"):
            release.check_changelog(repo, "0.24.2")

    def test_malformed_pyproject_is_a_release_error(self, repo):
        (repo / "pyproject.toml").write_text("this is not = = toml [[[")
        with pytest.raises(release.ReleaseError, match="pyproject.toml"):
            release.read_pyproject_version(repo)

    def test_pyproject_without_a_project_table_is_a_release_error(self, repo):
        (repo / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
        with pytest.raises(release.ReleaseError, match="pyproject.toml"):
            release.read_pyproject_version(repo)

    def test_pyproject_without_a_version_is_a_release_error(self, repo):
        (repo / "pyproject.toml").write_text('[project]\nname = "pyrite"\n')
        with pytest.raises(release.ReleaseError, match="version"):
            release.read_pyproject_version(repo)


class TestContributorsLine:
    def test_credits_outside_authors_sorted_and_deduped(self):
        line = release.contributors_line(["zoe", "amy", "zoe"])
        assert line == "Thanks to @amy, @zoe for their contributions."

    def test_maintainer_and_bots_are_excluded(self):
        assert release.contributors_line(["markramm", "dependabot[bot]"]) is None

    def test_no_outside_authors_yields_no_line(self):
        assert release.contributors_line([]) is None

    def test_one_author_reads_naturally(self):
        assert release.contributors_line(["amy"]) == ("Thanks to @amy for their contributions.")

    def test_co_authors_in_the_range_are_credited(self):
        """A contributor whose patch landed inside someone else's commit.

        CONTRIBUTING asks for a `Co-authored-by:` trailer precisely so this is
        machine-readable, and it survives a squash -- which, now that the merge
        queue squashes, is how most outside work lands. Crediting only PR
        authors drops them (#248).
        """
        trailers = [
            "Ada Lovelace <ada@example.com>",
            "markramm <mark.ramm@gmail.com>",
            "Claude Opus 5 (1M context) <noreply@anthropic.com>",
        ]
        assert release.co_author_logins(trailers) == ["ada@example.com"]

    def test_bots_and_the_maintainer_are_dropped_from_co_authors(self):
        trailers = [
            "Copilot App <223556219+Copilot@users.noreply.github.com>",
            "opencode <noreply@opencode.ai>",
            "dependabot[bot] <support@github.com>",
        ]
        assert release.co_author_logins(trailers) == []

    def test_a_github_noreply_address_yields_the_login(self):
        """`12345+octocat@users.noreply.github.com` is octocat, not an email."""
        trailers = ["Octocat <12345+octocat@users.noreply.github.com>"]
        assert release.co_author_logins(trailers) == ["octocat"]

    def test_an_email_is_never_published_as_a_credit(self):
        """A co-author trailer without a GitHub noreply address yields an email.
        The notes are public, so an email must never be printed as a credit;
        it goes to `unresolved_contributors` for a human to credit by hand."""
        logins = ["amy", "ada@example.com"]
        assert release.contributors_line(logins) == "Thanks to @amy for their contributions."
        assert release.unresolved_contributors(logins) == ["ada@example.com"]

    def test_only_emails_yields_no_line(self):
        assert release.contributors_line(["ada@example.com"]) is None

    def test_notes_with_contributors_appends_the_line(self, repo):
        notes = release.compose_notes(repo, "0.24.2", ["amy"])
        assert notes.rstrip().endswith("Thanks to @amy for their contributions.")
        assert "- A release script." in notes

    def test_notes_without_contributors_are_the_section_alone(self, repo):
        notes = release.compose_notes(repo, "0.24.2", [])
        assert "Thanks to" not in notes


class TestContributorWindow:
    """The window that feeds `contributors_line`.

    Cutting 0.24.3 produced notes crediting three of five outside
    contributors. `contributors_line` was right; the *window* handed to it
    was wrong, in two independent ways. Both are pinned here because a
    dropped contributor is invisible -- the notes read perfectly well
    without them.
    """

    def test_window_is_a_timestamp_not_a_truncated_date(self, monkeypatch):
        """`merged:>2026-09-18` excludes everything merged ON 09-18.

        GitHub's `>` over a bare date means "after that day ends". The
        previous tag was published at 09-18T09:22Z, so truncating it to a
        date silently dropped #116 and #108, merged 42 and 26 minutes
        earlier.
        """
        captured = {}

        def fake_check_output(cmd, **kw):
            return "2026-09-18T09:22:47Z"

        def fake_gh_json(cmd):
            captured["search"] = cmd[cmd.index("--search") + 1]
            return []

        monkeypatch.setattr(release, "_check_output", fake_check_output)
        monkeypatch.setattr(release, "_gh_json", fake_gh_json)
        release._contributor_logins("v0.24.1", "markramm/pyrite")

        assert captured["search"] == "merged:>2026-09-18T09:22:47Z", (
            "the window must keep the time component; a bare date loses a day"
        )

    def test_previous_tag_skips_a_tag_main_never_moved_to(self, tmp_path, monkeypatch):
        """`git describe` picked `v0.24.2`, which was never a release.

        It pointed at a mid-development commit whose own pyproject said
        0.24.1, and `main` never moved to it. A published GitHub release is
        NOT the test -- that accidental tag had one, empty and unnamed. The
        boundary is step (d): the commit `main` fast-forwarded to.
        """

        def fake_check_output(cmd, **kw):
            if "tag" in cmd:
                return "v0.24.3\nv0.24.2\nv0.24.1\n"
            raise AssertionError(f"unexpected: {cmd}")

        monkeypatch.setattr(release, "_check_output", fake_check_output)
        monkeypatch.setattr(release, "_tag_is_released", lambda repo, tag: tag == "v0.24.1")

        assert release._previous_tag(tmp_path, exclude="v0.24.3") == "v0.24.1"

    def test_a_tag_off_main_is_not_a_release_boundary(self, tmp_path, monkeypatch):
        """`_tag_is_released` asks git, not the releases API."""

        def fake_check_output(cmd, **kw):
            if "rev-list" in cmd:
                return "47ea84c\n"
            if "merge-base" in cmd:
                raise release.ReleaseError("not an ancestor")
            raise AssertionError(f"unexpected: {cmd}")

        monkeypatch.setattr(release, "_check_output", fake_check_output)
        assert release._tag_is_released(tmp_path, "v0.24.2") is False


# --------------------------------------------------------------------------
# CI status decision, from mocked `gh run list` JSON
# --------------------------------------------------------------------------


def _run(status, conclusion, name="gate"):
    return _as_ci_reports({"status": status, "conclusion": conclusion, "name": name})


class TestCiDecision:
    """The verdict is over NAMED checks, not "everything on the SHA".

    `gate` is the required check (ADR-0032 §3a); `e2e` runs on pushes to main
    and is deliberately advisory -- it is not in `gate`'s needs, and a red e2e
    must not block a tag. Gating on every check run would make it block one.
    """

    def test_the_required_check_passing_passes(self):
        assert release.ci_decision([_run("completed", "success")]) == release.CI_PASSED

    def test_the_required_check_failing_fails(self):
        assert release.ci_decision([_run("completed", "failure")]) == release.CI_FAILED

    def test_in_progress_required_check_is_pending(self):
        assert release.ci_decision([_run("in_progress", None)]) == release.CI_PENDING

    def test_queued_required_check_is_pending(self):
        assert release.ci_decision([_run("queued", None)]) == release.CI_PENDING

    def test_no_checks_at_all_is_missing(self):
        assert release.ci_decision([]) == release.CI_MISSING

    def test_the_required_check_absent_is_missing(self):
        """Other checks ran but `gate` did not: that is not a green light."""
        assert release.ci_decision([_run("completed", "success", "smoke")]) == release.CI_MISSING

    def test_cancelled_is_a_failure_not_a_pass(self):
        assert release.ci_decision([_run("completed", "cancelled")]) == release.CI_FAILED

    def test_timed_out_is_a_failure(self):
        assert release.ci_decision([_run("completed", "timed_out")]) == release.CI_FAILED

    def test_a_skipped_required_check_is_not_a_pass(self):
        """This pinned the opposite until fix round 1 of #854. `gate` runs
        `if: always()`, so a skipped `gate` means the workflow is no longer
        the one the rule was written against; and for any required check,
        "it did not run" is not "it passed". Jobs `gate` NEEDS may still be
        skipped by the classifier -- that is `gate`'s business, and it
        reports success when they are."""
        assert release.ci_decision([_run("completed", "skipped")]) == release.CI_FAILED

    def test_an_advisory_red_check_does_not_block(self):
        """The coupling that matters: `e2e` runs on pushes to main, is not in
        `gate`'s needs, and must never block a tag."""
        checks = [_run("completed", "success", "gate"), _run("completed", "failure", "e2e")]
        assert release.ci_decision(checks) == release.CI_PASSED

    def test_an_advisory_pending_check_does_not_hold_the_release(self):
        checks = [_run("completed", "success", "gate"), _run("in_progress", None, "e2e")]
        assert release.ci_decision(checks) == release.CI_PASSED

    def test_several_required_checks_all_must_pass(self):
        checks = [_run("completed", "success", "gate"), _run("completed", "failure", "kb")]
        assert release.ci_decision(checks, required=("gate", "kb")) == release.CI_FAILED

    def test_several_required_checks_pending_beats_passed(self):
        checks = [_run("completed", "success", "gate"), _run("queued", None, "kb")]
        assert release.ci_decision(checks, required=("gate", "kb")) == release.CI_PENDING

    def test_a_required_check_is_matched_by_its_exact_name(self):
        """Requiring `test` used to match `test (3.12)`, which made a lone
        `gate (docs)` a green `gate`. A matrix leg is required by its own
        name."""
        checks = [_run("completed", "success", "test (3.12)")]
        assert release.ci_decision(checks, required=("test",)) == release.CI_MISSING
        assert release.ci_decision(checks, required=("test (3.12)",)) == release.CI_PASSED
        lone_leg = [_run("completed", "success", "gate (docs)")]
        assert release.ci_decision(lone_leg) == release.CI_MISSING

    def test_the_default_required_check_is_gate(self):
        assert release.DEFAULT_REQUIRED_CHECKS == ("gate",)

    def test_require_check_is_configurable_on_the_command_line(self):
        args = release.parse_args(["0.24.2", "--require-check", "gate", "--require-check", "kb"])
        assert args.require_check == ["gate", "kb"]

    def test_require_check_defaults_to_gate(self):
        assert release.parse_args(["0.24.2"]).require_check == ["gate"]

    def test_a_rerun_to_green_is_the_verdict(self):
        """A `gate` rerun is a second check run with the SAME name. Reading
        both and failing on the older one means a rerun can never unblock a
        release -- the common case after a flaky failure."""
        checks = [
            _run("completed", "failure") | {"started_at": "2026-09-18T10:00:00Z"},
            _run("completed", "success") | {"started_at": "2026-09-18T11:00:00Z"},
        ]
        assert release.ci_decision(checks) == release.CI_PASSED

    def test_a_rerun_to_red_is_also_the_verdict(self):
        checks = [
            _run("completed", "success") | {"started_at": "2026-09-18T10:00:00Z"},
            _run("completed", "failure") | {"started_at": "2026-09-18T11:00:00Z"},
        ]
        assert release.ci_decision(checks) == release.CI_FAILED

    def test_runs_that_disagree_with_no_started_at_cannot_be_ordered(self):
        """`completed_at` used to break the tie. A rerun is ordered by when it
        STARTED, and only by a `started_at` every run carries; without one the
        answer is unknown, never the green one."""
        checks = [
            _run("completed", "failure") | {"completed_at": "2026-09-18T10:05:00Z"},
            _run("completed", "success") | {"completed_at": "2026-09-18T11:05:00Z"},
        ]
        assert release.ci_decision(checks) == release.CI_UNKNOWN

    def test_a_rerun_in_progress_is_pending_not_the_old_pass(self):
        checks = [
            _run("completed", "success") | {"started_at": "2026-09-18T10:00:00Z"},
            _run("in_progress", None) | {"started_at": "2026-09-18T11:00:00Z"},
        ]
        assert release.ci_decision(checks) == release.CI_PENDING

    @pytest.mark.control(
        reason="pre-existing test, respelled for exact names; its property held before and after"
    )
    def test_matrix_legs_are_still_all_required_together(self):
        """Each required name is judged on its own: `test (3.11)` and
        `test (3.12)` are different names and both must pass."""
        checks = [
            _run("completed", "success", "test (3.11)") | {"started_at": "2026-09-18T11:00:00Z"},
            _run("completed", "failure", "test (3.12)") | {"started_at": "2026-09-18T10:00:00Z"},
        ]
        required = ("test (3.11)", "test (3.12)")
        assert release.ci_decision(checks, required=required) == release.CI_FAILED

    def test_e2e_is_never_required_by_default(self):
        assert "e2e" not in release.DEFAULT_REQUIRED_CHECKS

    def test_e2e_is_never_required_by_a_composed_command(self, every_composed_command):
        """The literal scan this replaced forbade the string `e2e` anywhere in
        the module, which `--require-check e2e` makes a lie at runtime and which
        says nothing about what the run actually does. This asserts over the
        commands composed on the happy path and every failure path."""
        for cmd in every_composed_command:
            assert "e2e" not in " ".join(cmd), cmd

    def test_never_dispatches_a_run(self, every_composed_command):
        """A run this script started is not the run the merge gate saw, so it
        must never be able to start one. The literal scan this replaced forbade
        the word `workflow` and would have missed `gh run rerun` entirely."""
        for cmd in every_composed_command:
            joined = " ".join(cmd)
            assert "workflow run" not in joined, cmd
            assert cmd[:2] != ["gh", "workflow"], cmd
            assert cmd[:3] not in (["gh", "run", "rerun"], ["gh", "run", "watch"]), cmd
            assert not (cmd[:2] == ["gh", "run"] and cmd[2:3] != ["list"]), cmd

    def test_a_commit_github_has_never_seen_says_so_in_english(self, monkeypatch):
        """Found running it by hand: the likeliest real failure is a commit
        that was never pushed, and `gh` answers HTTP 422 'No commit found'.
        That must become the CI_MISSING advice, not a raw API error."""

        def explode(cmd, **kwargs):
            raise release.ReleaseError(
                "command failed (1): gh api repos/markramm/pyrite/commits/deadbeef/check-runs\n"
                '{"message":"No commit found for SHA: deadbeef","status":"422"}'
            )

        monkeypatch.setattr(release, "_check_output", explode)
        verdict = release.commit_verdict(FAKE_SHA, "markramm/pyrite")
        assert verdict.state == release.CI_MISSING

    def test_other_gh_errors_still_surface(self, monkeypatch):
        """Only 'no commit' is translated: a network or auth failure must not
        be mistaken for 'CI has not run yet'."""

        def explode(cmd, **kwargs):
            raise release.ReleaseError("command failed (1): gh api ...\nHTTP 401 Bad credentials")

        monkeypatch.setattr(release, "_check_output", explode)
        verdict = release.commit_verdict(FAKE_SHA, "markramm/pyrite")
        assert verdict.state == release.CI_UNKNOWN and "401" in verdict.detail

    def test_a_lookup_that_failed_stops_the_release_and_says_why(
        self, dry_run, monkeypatch, capsys
    ):
        """...and it reaches the maintainer as itself: not as "CI is red", and
        not as "CI has not run yet"."""
        original = release._check_output

        def unauthenticated(cmd, **kwargs):
            if "check-runs" in " ".join(cmd):
                raise release.ReleaseError(
                    "command failed (1): gh api ...\nHTTP 401 Bad credentials"
                )
            return original(cmd, **kwargs)

        monkeypatch.setattr(release, "_check_output", unauthenticated)
        code, runner, _calls = dry_run()
        assert code != 0 and runner.planned == []
        err = capsys.readouterr().err
        assert "could not establish" in err and "401" in err


# --------------------------------------------------------------------------
# step ordering: no irreversible step before a check
# --------------------------------------------------------------------------


class TestStepOrdering:
    def test_every_step_is_named_and_classified(self):
        for step in release.STEPS:
            assert step.name
            assert isinstance(step.irreversible, bool)
            assert callable(step.run)

    def test_no_irreversible_step_precedes_a_check(self):
        """The whole point of the script: every check runs in front of the
        first thing that cannot be undone.

        Steps after the irreversible ones are not checks -- nothing they could
        say would stop a tag that already exists -- so `checks` is what this
        asserts over, not "everything not irreversible"."""
        names = [s.name for s in release.STEPS]
        first_irreversible = next(i for i, s in enumerate(release.STEPS) if s.irreversible)
        last_check = max(i for i, s in enumerate(release.STEPS) if s.is_check)
        assert first_irreversible > last_check, (
            f"a check runs after the first irreversible step: {names}"
        )

    def test_the_documented_order_of_steps(self):
        assert [s.key for s in release.STEPS] == [
            "preconditions",
            "ci",
            "push_rehearsal",
            "release_layer",
            "publish",
            "post_release",
            "handoff",
        ]

    def test_only_publish_and_post_release_are_irreversible(self):
        irreversible = {s.key for s in release.STEPS if s.irreversible}
        assert irreversible == {"publish", "post_release"}

    def test_the_checks_are_the_four_steps_before_the_tag(self):
        assert {s.key for s in release.STEPS if s.is_check} == {
            "preconditions",
            "ci",
            "push_rehearsal",
            "release_layer",
        }

    def test_the_handoff_is_neither_a_check_nor_irreversible(self):
        """It only tells the maintainer what is left for them; it changes
        nothing and can stop nothing."""
        handoff = next(s for s in release.STEPS if s.key == "handoff")
        assert handoff.is_check is False
        assert handoff.irreversible is False


class TestHandoffReminders:
    """pyrite.wiki lives outside this repo and carries version-specific claims
    (tool counts, test counts, the current version). Nothing in the release
    path can update it, so the release must at least say so out loud."""

    def test_the_run_ends_by_naming_pyrite_wiki(self, dry_run, capsys):
        dry_run()
        out = capsys.readouterr().out
        assert "pyrite.wiki" in out

    def test_the_reminder_comes_after_the_release_is_cut(self, dry_run, capsys):
        dry_run()
        out = capsys.readouterr().out
        assert out.index("gh release create") < out.index("pyrite.wiki")

    def test_it_names_the_version_being_released(self, dry_run, capsys):
        dry_run()
        out = capsys.readouterr().out
        reminder = out[out.index("pyrite.wiki") - 400 : out.index("pyrite.wiki") + 400]
        assert "0.24.2" in reminder

    def test_it_reminds_about_the_deploys_too(self, dry_run, capsys):
        """The site mapping in the runbook: the tag does not deploy itself."""
        dry_run()
        out = capsys.readouterr().out.lower()
        assert "deploy" in out

    def test_the_reminder_changes_nothing(self, dry_run):
        """It must not be able to touch the site, only mention it."""
        _code, runner, calls = dry_run()
        joined = " ".join(" ".join(c) for c in calls)
        assert "pyrite.wiki" not in joined


# --------------------------------------------------------------------------
# safety rules
# --------------------------------------------------------------------------


def _string_literals(module) -> list[str]:
    """Every string literal in the module that is not a docstring.

    The dangerous flags appear in this module's prose (its whole point is that
    v0.24.1 was pushed with --no-verify), so a substring scan of the source
    would be a test of the documentation. Command arguments are string
    literals; docstrings are not arguments.
    """
    import ast

    tree = ast.parse(Path(module.__file__).read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docstrings
    ]


class TestSafety:
    def test_no_command_argument_is_no_verify(self):
        assert not [s for s in _string_literals(release) if "--no-verify" in s]

    def test_no_command_argument_force_pushes(self):
        dangerous = ("--force", "force-with-lease", "-f")
        literals = _string_literals(release)
        assert not [s for s in literals if s in dangerous or "--force" in s]

    def test_no_composed_command_deletes_a_ref(self, every_composed_command):
        """The literal scan this replaced checked `--delete` only, so `git push
        -d`, `git tag -d` and `gh release delete` all slipped through."""
        for cmd in every_composed_command:
            assert not [a for a in cmd if a in ("-d", "-D", "--delete")], cmd
            assert "delete" not in " ".join(cmd), cmd

    def test_the_only_push_refspecs_are_forward(self, dry_run_commands):
        """As actually composed in a dry run: `<sha>:refs/heads/main` and a tag
        name. A refspec starting with `:` deletes the remote ref; one starting
        with `+` forces. Neither may ever be built."""
        for cmd in dry_run_commands:
            if "push" not in cmd:
                continue
            for arg in cmd[cmd.index("push") + 1 :]:
                assert not arg.startswith(":"), f"deleting refspec: {cmd}"
                assert not arg.startswith("+"), f"forcing refspec: {cmd}"

    def test_dry_run_is_the_default(self):
        args = release.parse_args(["0.24.2"])
        assert args.execute is False

    def test_execute_flag_turns_it_off(self):
        args = release.parse_args(["0.24.2", "--execute"])
        assert args.execute is True

    def test_dry_run_flag_is_accepted_and_redundant(self):
        args = release.parse_args(["0.24.2", "--dry-run"])
        assert args.execute is False

    def test_dry_run_and_execute_together_is_refused(self, capsys):
        with pytest.raises(SystemExit):
            release.parse_args(["0.24.2", "--execute", "--dry-run"])

    def test_gh_writes_are_never_run_in_dry_run(self):
        """`gh release create` and `gh label create` are writes: in dry-run
        they are printed, never executed."""
        runner = release.Runner(execute=False)
        printed = runner.run_write(["gh", "release", "create", "v0.24.2"])
        assert printed is None  # nothing ran

    def test_a_write_in_execute_mode_calls_through(self, monkeypatch):
        calls = []
        monkeypatch.setattr(release, "_check_output", lambda cmd, **kw: calls.append(cmd) or "")
        runner = release.Runner(execute=True)
        runner.run_write(["gh", "release", "create", "v0.24.2"])
        assert calls == [["gh", "release", "create", "v0.24.2"]]

    def test_dirty_checkout_is_refused(self, monkeypatch):
        monkeypatch.setattr(release, "git_status_porcelain", lambda repo: " M foo.py")
        with pytest.raises(release.ReleaseError, match="dirty|uncommitted"):
            release.check_clean_checkout(REPO)

    def test_wrong_branch_is_refused(self, monkeypatch):
        monkeypatch.setattr(release, "git_status_porcelain", lambda repo: "")
        monkeypatch.setattr(release, "current_branch", lambda repo: "feature/x")
        with pytest.raises(release.ReleaseError, match="dev"):
            release.check_clean_checkout(REPO)

    def test_behind_origin_dev_is_refused(self, monkeypatch):
        monkeypatch.setattr(release, "git_status_porcelain", lambda repo: "")
        monkeypatch.setattr(release, "current_branch", lambda repo: "dev")
        monkeypatch.setattr(
            release, "rev_parse", lambda repo, ref: "aaa" if ref == "HEAD" else "bbb"
        )
        with pytest.raises(release.ReleaseError, match="origin/dev"):
            release.check_clean_checkout(REPO)

    def test_clean_dev_at_origin_dev_passes(self, monkeypatch):
        monkeypatch.setattr(release, "git_status_porcelain", lambda repo: "")
        monkeypatch.setattr(release, "current_branch", lambda repo: "dev")
        monkeypatch.setattr(release, "rev_parse", lambda repo, ref: "aaa")
        assert release.check_clean_checkout(REPO) == "aaa"


class TestReleaseBlockers:
    def test_an_open_blocker_pr_refuses(self):
        with pytest.raises(release.ReleaseError, match="#7"):
            release.check_no_release_blockers([{"number": 7, "title": "the index is corrupt"}])

    def test_no_blockers_passes(self):
        release.check_no_release_blockers([])  # no raise

    def test_a_missing_label_is_a_hard_failure_not_a_note(self, dry_run, no_network, capsys):
        """The label does not exist in the repo today. Treating that as a note
        and skipping the blocker query means the precondition is inert on first
        use -- the run proceeds to the tag having checked nothing."""
        original = release._check_output

        def without_the_label(cmd, **kwargs):
            if "gh label list" in " ".join(cmd):
                return "[]"
            return original(cmd, **kwargs)

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(release, "_check_output", without_the_label)
        try:
            code, runner, _calls = dry_run()
        finally:
            monkeypatch.undo()
        assert code != 0
        assert runner.planned == [], "a write was planned though the blocker check never ran"
        err = capsys.readouterr().err
        assert "gh label create release-blocker" in err

    def test_the_script_never_plans_to_create_a_label(self, dry_run_commands):
        """It is a documented prerequisite, not something the release creates."""
        assert not [c for c in dry_run_commands if c[:3] == ["gh", "label", "create"]]


class TestTagAndRemotePreconditions:
    """Step d moves `main` first and tags second: if the tag already exists, or
    `main` has diverged, the failure lands halfway through something
    irreversible. Both are preconditions."""

    def _dry_run_with(self, dry_run, monkeypatch, override):
        original = release._check_output

        def patched(cmd, **kwargs):
            answer = override(cmd)
            return original(cmd, **kwargs) if answer is None else answer

        monkeypatch.setattr(release, "_check_output", patched)
        return dry_run()

    def test_an_existing_local_tag_stops_the_release(self, dry_run, monkeypatch, capsys):
        code, runner, _calls = self._dry_run_with(
            dry_run, monkeypatch, lambda cmd: "v0.24.2\n" if "tag" in cmd and "-l" in cmd else None
        )
        assert code != 0
        assert runner.planned == []
        assert "v0.24.2" in capsys.readouterr().err

    def test_an_existing_remote_tag_stops_the_release(self, dry_run, monkeypatch, capsys):
        code, runner, _calls = self._dry_run_with(
            dry_run,
            monkeypatch,
            lambda cmd: "deadbeef\trefs/tags/v0.24.2\n" if "ls-remote" in cmd else None,
        )
        assert code != 0
        assert runner.planned == []
        assert "v0.24.2" in capsys.readouterr().err

    def test_an_existing_github_release_stops_the_release(self, dry_run, monkeypatch, capsys):
        code, runner, _calls = self._dry_run_with(
            dry_run,
            monkeypatch,
            lambda cmd: '{"tagName":"v0.24.2"}' if cmd[:3] == ["gh", "release", "view"] else None,
        )
        assert code != 0
        assert runner.planned == []
        assert "v0.24.2" in capsys.readouterr().err

    def test_main_not_an_ancestor_stops_the_release(self, dry_run, monkeypatch, capsys):
        def diverged(cmd):
            if "merge-base" in cmd:
                raise release.ReleaseError("command failed (1): git merge-base --is-ancestor")
            return None

        code, runner, _calls = self._dry_run_with(dry_run, monkeypatch, diverged)
        assert code != 0
        assert runner.planned == []
        err = capsys.readouterr().err
        assert "main" in err

    def test_the_happy_path_checks_the_tag_and_the_ancestry(self, dry_run):
        _code, _runner, calls = dry_run()
        joined = [" ".join(c) for c in calls]
        assert any("tag -l v0.24.2" in c for c in joined), joined
        assert any("ls-remote" in c and "v0.24.2" in c for c in joined), joined
        assert any(c.startswith("gh release view v0.24.2") for c in joined), joined
        assert any("merge-base --is-ancestor" in c for c in joined), joined

    def test_an_unrecognisable_remote_stops_the_release(self, dry_run, monkeypatch, capsys):
        """The repo now comes FROM `origin`, so a fork releasing itself is
        legitimate (that is the org move). What must still stop the release is
        an `origin` the script cannot resolve to a GitHub repo -- it will not
        guess which repository to cut a release on."""
        code, runner, _calls = self._dry_run_with(
            dry_run,
            monkeypatch,
            lambda cmd: "/srv/git/bare.git\n" if "get-url" in cmd else None,
        )
        assert code != 0
        assert runner.planned == []
        assert "origin" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/markramm/pyrite",
            "https://github.com/markramm/pyrite.git",
            "git@github.com:markramm/pyrite.git",
            "ssh://git@github.com/markramm/pyrite.git",
        ],
    )
    def test_both_remote_spellings_are_accepted(self, url):
        assert release.remote_slug(url) == "markramm/pyrite"


class TestPublishFailureSaysWhatHappened:
    """`main` moves first. "Nothing further was attempted" after that is false
    and leaves the maintainer guessing what state the repo is in."""

    def test_a_failure_after_main_moved_lists_what_ran(self, dry_run, monkeypatch, capsys):
        original = release._check_output

        def fail_the_tag(cmd, **kwargs):
            if cmd[:1] == ["git"] and "tag" in cmd and "-a" in cmd:
                raise release.ReleaseError("tag refused")
            return original(cmd, **kwargs)

        monkeypatch.setattr(release, "_check_output", fail_the_tag)
        code, _runner, _calls = dry_run(("0.24.2", "--execute", "--skip-install-check"))
        assert code != 0
        err = capsys.readouterr().err
        assert "Nothing further was attempted." not in err
        assert "ALREADY RAN" in err
        assert "refs/heads/main" in err

    def test_a_failure_before_anything_ran_still_says_nothing_happened(self, dry_run, capsys):
        release_error = release.ReleaseError("version mismatch")
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            release,
            "check_version_matches",
            lambda repo, v: (_ for _ in ()).throw(release_error),
        )
        try:
            code, _runner, _calls = dry_run()
        finally:
            monkeypatch.undo()
        assert code != 0
        assert "Nothing further was attempted" in capsys.readouterr().err


class TestPostReleaseUsesABranch:
    """Step e must not commit on local `dev`: the ruleset refuses a direct push
    and a commit there leaves the checkout ahead of `origin/dev`, which is
    exactly the state step a refuses on the next release."""

    def test_it_creates_a_branch_before_committing(self, dry_run_commands):
        planned = [" ".join(c) for c in dry_run_commands]
        branch = "release/reopen-unreleased-0.24.2"
        checkout = [i for i, p in enumerate(planned) if f"checkout -b {branch}" in p]
        commits = [i for i, p in enumerate(planned) if "commit -m" in p]
        assert checkout, planned
        assert commits, planned
        assert checkout[0] < commits[0], planned

    def test_the_branch_is_cut_from_the_release_commit(self, dry_run_commands):
        planned = [" ".join(c) for c in dry_run_commands]
        assert any(
            f"checkout -b release/reopen-unreleased-0.24.2 {FAKE_SHA}" in p for p in planned
        ), planned

    def test_it_prints_the_push_and_pr_commands(self, dry_run, capsys):
        dry_run()
        out = capsys.readouterr().out
        assert "git push -u origin release/reopen-unreleased-0.24.2" in out
        assert "gh pr create --base dev --fill" in out

    def test_it_never_commits_on_dev(self, dry_run_commands):
        """No commit may be planned while the checkout is still on dev."""
        planned = [" ".join(c) for c in dry_run_commands]
        for i, p in enumerate(planned):
            if "commit -m" in p:
                assert any("checkout -b" in earlier for earlier in planned[:i]), planned


# --------------------------------------------------------------------------
# --dry-run over this repo, with every subprocess call monkeypatched
# --------------------------------------------------------------------------


class TestDryRunOverThisRepo:
    def test_dry_run_exits_zero(self, dry_run):
        code, _runner, _calls = dry_run()
        assert code == 0

    @pytest.mark.control(
        reason="pre-existing test, adapted to the shared lookup's seam; its property held before and after"
    )
    def test_dry_run_shells_out_only_to_reads(self, dry_run):
        """Every subprocess the dry run attempted must be a read. A write that
        slipped past the Runner would show up here.

        `git tag -l`, `git tag --list` and `git ls-remote --tags` ARE reads:
        the precondition that `v<version>` is still free has to ask, and the
        contributor window has to find the previous released tag. `git tag -a`,
        which creates one, must not appear.
        """
        _code, _runner, calls = dry_run()
        reads_named_tag = (["tag", "-l"], ["tag", "--list"], ["ls-remote", "--tags"])
        for cmd in calls:
            joined = " ".join(cmd)
            for write in ("release create", "label create", "uv venv", "uv pip"):
                assert write not in joined, f"dry run ran a write: {write!r} in {joined}"
            # As an argument, not a substring: asking git where the pre-push
            # hook lives (`rev-parse --git-path hooks/pre-push`) is a read.
            assert "push" not in cmd, f"dry run pushed: {joined}"
            if "tag" in cmd and not any(
                all(part in cmd for part in read) for read in reads_named_tag
            ):
                raise AssertionError(f"dry run ran a tag write: {joined}")
            assert "checkout" not in cmd, f"dry run changed branches: {joined}"
            assert "commit" not in cmd, f"dry run committed: {joined}"

    def test_gh_release_create_names_the_repo_explicitly(self, dry_run_commands):
        """`gh` infers the repo from the cwd, which is not necessarily the repo
        being released. A release cut against the wrong repo is not undoable."""
        create = [c for c in dry_run_commands if c[:3] == ["gh", "release", "create"]]
        assert create, "no release create was planned"
        for cmd in create:
            assert "--repo" in cmd
            assert cmd[cmd.index("--repo") + 1] == f"{release.MAINTAINER}/pyrite"

    def test_dry_run_planned_every_irreversible_command(self, dry_run_commands):
        planned = [" ".join(c) for c in dry_run_commands]
        assert any(f"push origin {FAKE_SHA}:refs/heads/main" in p for p in planned)
        assert any("tag -a v0.24.2" in p for p in planned)
        assert any("push origin v0.24.2" in p for p in planned)
        assert any("gh release create v0.24.2" in p for p in planned)

    def test_dry_run_prints_every_irreversible_command(self, dry_run, capsys):
        dry_run()
        out = capsys.readouterr().out
        assert f"push origin {FAKE_SHA}:refs/heads/main" in out
        assert "tag -a v0.24.2" in out
        assert "push origin v0.24.2" in out
        assert "gh release create v0.24.2" in out
        assert "WOULD RUN" in out

    def test_dry_run_prints_the_install_check_it_skipped(self, dry_run, capsys):
        """Step c is minutes of network; a dry run prints it and says how to
        rehearse it for real."""
        dry_run()
        out = capsys.readouterr().out
        assert "uv pip install" in out
        assert "--install-check" in out

    def test_printed_commands_are_copy_pasteable(self, dry_run, capsys):
        """Found running it by hand: an argument with spaces printed bare, so
        `gh label create ... --description Must not ship in the next release`
        could not be pasted into a shell. The maintainer pastes these."""
        import shlex

        dry_run()
        for line in capsys.readouterr().out.splitlines():
            for prefix in ("WOULD RUN: ", "RUN: "):
                if prefix in line:
                    rendered = line.split(prefix, 1)[1]
                    # every multi-word argument must be quoted: re-splitting the
                    # printed line must not invent extra arguments
                    assert shlex.split(rendered), rendered

    def test_an_argument_with_spaces_is_quoted_when_printed(self, capsys):
        runner = release.Runner(execute=False)
        runner.run_write(["gh", "label", "create", "x", "--description", "two words"])
        assert "'two words'" in capsys.readouterr().out

    def test_dry_run_says_it_is_a_dry_run(self, dry_run, capsys):
        dry_run()
        out = capsys.readouterr().out.lower()
        assert "dry run" in out
        assert "--execute" in out

    def test_a_failing_precondition_stops_before_later_steps(self, dry_run, monkeypatch, capsys):
        monkeypatch.setattr(release, "read_pyproject_version", lambda repo: "0.24.1")
        monkeypatch.setattr(
            release,
            "check_version_matches",
            lambda repo, v: (_ for _ in ()).throw(release.ReleaseError("version mismatch")),
        )
        code, runner, _calls = dry_run()
        assert code != 0
        assert runner.planned == [], "a write was planned after a failed check"
        captured = capsys.readouterr()
        assert "gh release create" not in captured.out

    def test_the_failure_reads_in_order_when_piped(self, tmp_path):
        """Found running it by hand: piped, stdout block-buffers while stderr
        does not, so `FAIL at preconditions` printed ABOVE the header and the
        step it failed in -- unreadable in a log or a CI transcript.

        Runs the real script as a subprocess with both streams on one pipe.
        `--repo` points at an empty tmp dir, so the very first check (git
        status) fails there: no network, no git writes, nothing reached.
        """
        import subprocess

        proc = subprocess.run(
            f'"{sys.executable}" "{REPO / "scripts" / "release.py"}" 0.0.1 '
            f'--repo "{tmp_path}" 2>&1',
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=120,
        )
        out = proc.stdout
        assert proc.returncode != 0
        assert "FAIL at preconditions" in out
        assert out.index("pyrite release 0.0.1") < out.index("FAIL at")
        assert out.index("a. preconditions") < out.index("FAIL at")


# --------------------------------------------------------------------------
# the tutorial runner must be targetable at the temp venv (step c)
# --------------------------------------------------------------------------


class TestTutorialVenvHook:
    """These run the shell script. Asserting that the string
    `PYRITE_TUTORIAL_VENV` appears in a file passes against a deleted
    implementation -- the variable is named in the comments of both files.
    """

    @staticmethod
    def _stub_venv(tmp_path):
        """A venv-shaped directory whose `bin/python` records its argv and
        exits 0 without running anything."""
        venv = tmp_path / "venv"
        (venv / "bin").mkdir(parents=True)
        argv_log = tmp_path / "argv.txt"
        python = venv / "bin" / "python"
        python.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$0" "$@" > "{argv_log}"\nexit 0\n')
        python.chmod(0o755)
        return venv, argv_log

    def _run(self, env_overrides, tmp_path):
        import os
        import subprocess

        env = dict(os.environ)
        env.pop("PYRITE_TUTORIAL_VENV", None)
        env.update(env_overrides)
        return subprocess.run(
            [str(REPO / "scripts" / "run_tutorial.sh"), "--nonexistent-doc-argument"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
            timeout=120,
        )

    def test_the_named_venvs_python_is_the_interpreter_invoked(self, tmp_path):
        venv, argv_log = self._stub_venv(tmp_path)
        proc = self._run({"PYRITE_TUTORIAL_VENV": str(venv)}, tmp_path)
        assert proc.returncode == 0, proc.stderr
        recorded = argv_log.read_text().splitlines()
        assert recorded[0] == str(venv / "bin" / "python")
        assert recorded[1].endswith("run_tutorial.py")

    def test_the_venvs_bin_is_prepended_to_path(self, tmp_path):
        """The tutorial drives `pyrite` off PATH; pointing the interpreter at
        the venv is not enough if PATH still finds the checkout's."""
        venv, _argv_log = self._stub_venv(tmp_path)
        path_log = tmp_path / "path.txt"
        python = venv / "bin" / "python"
        python.write_text(f'#!/usr/bin/env bash\nprintf "%s" "$PATH" > "{path_log}"\nexit 0\n')
        python.chmod(0o755)
        proc = self._run({"PYRITE_TUTORIAL_VENV": str(venv)}, tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert path_log.read_text().split(":")[0] == str(venv / "bin")

    def test_a_bad_path_fails_loudly_instead_of_falling_back(self, tmp_path):
        """Silently running the checkout's venv would make the release's
        install check verify the wrong thing."""
        proc = self._run({"PYRITE_TUTORIAL_VENV": str(tmp_path / "nope")}, tmp_path)
        assert proc.returncode == 1
        assert "has no bin/python" in proc.stderr

    def test_unset_leaves_the_developer_and_ci_case_unchanged(self, tmp_path):
        """Unset, it must still reach run_tutorial.py with the repo's venv --
        here proven by the doc argument getting through to the runner, which
        rejects it."""
        proc = self._run({}, tmp_path)
        combined = proc.stdout + proc.stderr
        assert "has no bin/python" not in combined
        assert "--nonexistent-doc-argument" in combined or proc.returncode != 0

    def test_the_release_script_passes_the_venv_it_installed_into(self, monkeypatch, tmp_path):
        """Step c must hand the tutorial the temp venv, as an environment
        variable on the call -- not merely mention the name in a comment."""
        seen = {}

        def fake(cmd, cwd=None, env=None):
            if cmd and str(cmd[0]).endswith("run_tutorial.sh"):
                seen["env"] = dict(env or {})
                return ""
            if cmd and str(cmd[0]).endswith("pyrite"):
                return "pyrite 0.24.2"
            return ""

        monkeypatch.setattr(release, "_check_output", fake)
        monkeypatch.setattr(
            release.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None
        )
        ctx = release.Context(
            repo=REPO,
            version="0.24.2",
            runner=release.Runner(execute=False),
            wait_ci_minutes=0,
            rehearse_install_check=True,
            sha=FAKE_SHA,
        )
        release.step_release_layer(ctx)
        assert "PYRITE_TUTORIAL_VENV" in seen.get("env", {})
        assert Path(seen["env"]["PYRITE_TUTORIAL_VENV"]).name.startswith("pyrite-release-venv-")


class TestNotesFileIsNotLitter:
    """A dry run wrote `pyrite-release-notes-*/notes.md` into the system temp
    dir, outside the Runner choke point, and never removed it -- a dry run must
    change nothing on disk."""

    def test_a_dry_run_writes_no_notes_file(self, dry_run, monkeypatch):
        made = []
        real_mkdtemp = release.tempfile.mkdtemp

        def spy(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            made.append(Path(path))
            return path

        monkeypatch.setattr(release.tempfile, "mkdtemp", spy)
        dry_run()
        leftovers = [p for p in made if p.exists()]
        assert not leftovers, f"a dry run left temp directories behind: {leftovers}"

    def test_the_dry_run_still_shows_where_the_notes_go(self, dry_run, capsys):
        dry_run()
        out = capsys.readouterr().out
        assert "notes" in out.lower()
        assert "--notes-file" in out


class TestWaitCiDefault:
    """`--wait-ci 0` makes the commonest case -- "I just merged, CI is running"
    -- fail immediately, which teaches the maintainer to re-run rather than to
    trust the check."""

    def test_the_default_waits(self):
        assert release.parse_args(["0.24.2"]).wait_ci >= 5

    def test_it_is_still_overridable_including_to_zero(self):
        assert release.parse_args(["0.24.2", "--wait-ci", "0"]).wait_ci == 0
        assert release.parse_args(["0.24.2", "--wait-ci", "40"]).wait_ci == 40

    @pytest.mark.control(
        reason="pre-existing test, adapted to the shared lookup's seam; its property held before and after"
    )
    def test_the_poll_never_sleeps_past_the_deadline(self, monkeypatch):
        """A fixed 30s sleep past a deadline that is 5s away wastes 25s and
        reports a timeout later than it found one."""
        slept = []
        monkeypatch.setattr(release.time, "sleep", lambda s: slept.append(s))
        # deadline is computed from the first reading; then one per poll.
        clock = iter([0.0, 4.0, 9.0, 100.0])
        monkeypatch.setattr(release.time, "monotonic", lambda: next(clock))
        monkeypatch.setattr(
            release,
            "_check_output",
            lambda cmd, **kw: check_runs(
                {"name": "gate", "status": "in_progress", "conclusion": None}
            ),
        )
        ctx = release.Context(
            repo=REPO,
            version="0.24.2",
            runner=release.Runner(execute=False),
            wait_ci_minutes=0,
            sha=FAKE_SHA,
            slug="markramm/pyrite",
        )
        ctx.wait_ci_minutes = 10 / 60  # a 10-second deadline
        with pytest.raises(release.ReleaseError, match="still running"):
            release.step_ci(ctx)
        assert slept, "it never waited at all"
        assert all(s <= 30 for s in slept), slept
        assert sum(slept) <= 10.001, slept


class TestInstallCheckMatchesTheTutorial:
    """Step (c) must install what getting-started.md tells a user to install.

    It installed `pyrite[server,cli]` and then ran the tutorial against that
    venv. The tutorial says `pip install -e ".[all]"`, and its `pyrite index
    embed` block needs sentence-transformers, which lives in the `semantic`
    extra. So the release layer failed on a perfectly good candidate: the
    check was narrower than the document it was checking.
    """

    def test_the_extras_are_the_ones_the_tutorial_names(self):
        tutorial = (REPO / "docs" / "getting-started.md").read_text()
        assert f'".[{release.INSTALL_CHECK_EXTRAS}]"' in tutorial, (
            f"the install check uses [{release.INSTALL_CHECK_EXTRAS}] but "
            "getting-started.md does not tell users to install that"
        )

    def test_the_planned_install_uses_those_extras(self, dry_run_commands, dry_run):
        _code, _runner, calls = dry_run(("0.24.2", "--execute"))
        installs = [c for c in calls if c[:2] == ["uv", "pip"]]
        assert installs, "no install was composed"
        assert any(f"pyrite[{release.INSTALL_CHECK_EXTRAS}]" in " ".join(c) for c in installs)


class TestDockerBuildIsOptIn:
    """No image is published, so the build must not gate a release.

    Neither CI nor this script pushes to a registry -- `gh api
    repos/.../packages` is empty and no workflow runs docker/build-push.
    The build therefore verified an artifact that never left the machine,
    while being able to fail the release: on 0.24.3 it did so twice, once on
    a stale `web/node_modules` and once on a corrupted local container store.

    Maintainer, 2026-09-20: "we are not publishing that image... remove docker
    builds from the release process for the next couple of releases -- no one
    is using that path now."
    """

    def test_no_docker_build_by_default(self, every_composed_command):
        for cmd in every_composed_command:
            assert cmd[:2] != ["docker", "build"], (
                f"a docker build was composed without --docker-check: {cmd}"
            )

    def test_docker_check_brings_it_back(self, dry_run, monkeypatch, capsys):
        """The `dry_run` fixture's repo has no Dockerfile, and the
        no-Dockerfile branch ALSO skips the build -- so assert on the note,
        which distinguishes the two reasons, rather than on the absent
        command. A bare `docker build not in calls` would pass for the wrong
        reason."""
        monkeypatch.setattr(release.shutil, "which", lambda name: f"/usr/bin/{name}")
        dry_run(("0.24.2", "--execute", "--docker-check"))
        out = capsys.readouterr().out
        assert "pass --docker-check" not in out, (
            "with --docker-check passed, the opt-out note must not be printed"
        )

    def test_the_flag_defaults_to_off(self):
        args = release.parse_args(["0.24.2"])
        assert args.docker_check is False


class TestTheReleaseRepoIsDerivedFromOrigin:
    """The script must release whatever `origin` is, not a hard-coded slug.

    `REPO_SLUG` was `markramm/pyrite`, and step (a) refused to run when
    `origin` disagreed. Correct while the repo lived there; the moment it
    moved to `pyrite-wiki/pyrite` (#182) that guard fired on the *legitimate*
    case and there was no release path at all. `gh release view --repo` and
    `gh label list --repo` also kept reading the old address, which GitHub's
    redirects make silently appear to work.

    The guard is kept, but it now asserts INTERNAL CONSISTENCY: the repo the
    `gh` calls name is the repo `git push` will write to. That is the property
    that actually matters -- a fork releasing itself is fine; a checkout that
    pushes to one repo and cuts the release on another is not.
    """

    def test_slug_comes_from_origin_not_a_literal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            release,
            "_check_output",
            lambda cmd, **kw: "git@github.com:pyrite-wiki/pyrite.git\n",
        )
        assert release.resolve_repo_slug(tmp_path) == "pyrite-wiki/pyrite"

    def test_a_fork_releases_itself_without_complaint(self, tmp_path, monkeypatch):
        """The old guard refused this. It is the org move, and it is fine."""
        monkeypatch.setattr(
            release,
            "_check_output",
            lambda cmd, **kw: "https://github.com/someone/fork.git\n",
        )
        assert release.resolve_repo_slug(tmp_path) == "someone/fork"

    def test_an_unrecognisable_remote_is_refused(self, tmp_path, monkeypatch):
        """Better to stop than to guess which repo a release is cut on."""
        monkeypatch.setattr(release, "_check_output", lambda cmd, **kw: "/srv/git/bare-repo.git\n")
        with pytest.raises(release.ReleaseError, match="origin"):
            release.resolve_repo_slug(tmp_path)

    def test_no_github_url_literal_remains_in_the_module(self):
        """A second source of truth is how this bug comes back."""
        source = (REPO / "scripts" / "release.py").read_text()
        offenders = [
            line
            for line in source.splitlines()
            if "github.com/markramm" in line and not line.strip().startswith("#")
        ]
        assert offenders == [], offenders

    def test_the_install_spec_uses_the_derived_remote(self, dry_run, monkeypatch):
        """Step (c) installs `pyrite[all] @ git+<url>@<sha>`. Pinned to the old
        URL it would install from the pre-move address -- which redirects, so
        it would pass while proving nothing about the new one."""
        monkeypatch.setattr(release.shutil, "which", lambda name: f"/usr/bin/{name}")
        _code, _runner, calls = dry_run(("0.24.2", "--execute"))
        installs = [" ".join(c) for c in calls if c[:2] == ["uv", "pip"]]
        assert installs, "no install was composed"
        # The fixture's `origin` is markramm/pyrite, so a spec naming it
        # proves nothing by itself -- that is also what a hard-coded literal
        # would print. Point `origin` somewhere else and require the spec to
        # follow it.
        assert any("git+https://github.com/" in c for c in installs), installs

    def test_the_install_spec_follows_a_moved_origin(self, dry_run, monkeypatch):
        """The org move, as step (c) would see it."""
        monkeypatch.setattr(release.shutil, "which", lambda name: f"/usr/bin/{name}")
        original = release._check_output

        def moved(cmd, **kw):
            if "get-url" in " ".join(cmd):
                return "git@github.com:pyrite-wiki/pyrite.git\n"
            return original(cmd, **kw)

        monkeypatch.setattr(release, "_check_output", moved)
        _code, _runner, calls = dry_run(("0.24.2", "--execute"))
        installs = [" ".join(c) for c in calls if c[:2] == ["uv", "pip"]]
        assert installs, "no install was composed"
        assert all("pyrite-wiki/pyrite" in c for c in installs), installs


# --------------------------------------------------------------------------
# changelog fragments: assembly, validation, and the section they land in (#243)
# --------------------------------------------------------------------------


def write_fragment(repo, name, body):
    directory = repo / "changelog.d"
    directory.mkdir(exist_ok=True)
    (directory / name).write_text(body)
    return directory / name


class TestFragmentCollection:
    def test_no_directory_means_no_fragments(self, repo):
        assert release.collect_fragments(repo) == []

    def test_an_empty_directory_means_no_fragments(self, repo):
        (repo / "changelog.d").mkdir()
        assert release.collect_fragments(repo) == []

    def test_a_fragment_is_collected_with_its_section_and_body(self, repo):
        write_fragment(repo, "a-thing.fixed.md", "- A thing was fixed.\n")
        (fragment,) = release.collect_fragments(repo)
        assert fragment.slug == "a-thing"
        assert fragment.section == "fixed"
        assert fragment.body == "- A thing was fixed."

    def test_the_readme_is_skipped(self, repo):
        write_fragment(repo, "README.md", "how to write a fragment\n")
        write_fragment(repo, "a-thing.fixed.md", "- A thing.\n")
        assert [f.slug for f in release.collect_fragments(repo)] == ["a-thing"]

    def test_a_gitkeep_is_skipped(self, repo):
        write_fragment(repo, ".gitkeep", "")
        assert release.collect_fragments(repo) == []

    def test_an_unknown_section_is_a_release_error(self, repo):
        """A dropped entry is how a security fix goes unannounced: the release
        must stop, not skip the file."""
        write_fragment(repo, "a-thing.fixd.md", "- A thing.\n")
        with pytest.raises(release.ReleaseError) as exc:
            release.collect_fragments(repo)
        assert "a-thing.fixd.md" in str(exc.value)

    def test_an_empty_fragment_is_a_release_error(self, repo):
        """An empty file would assemble to a heading with nothing under it --
        the author meant to say something."""
        write_fragment(repo, "a-thing.fixed.md", "   \n\n")
        with pytest.raises(release.ReleaseError, match="empty"):
            release.collect_fragments(repo)


class TestFragmentAssembly:
    def test_sections_come_out_in_keep_a_changelog_order(self, repo):
        write_fragment(repo, "s.security.md", "- Security.\n")
        write_fragment(repo, "f.fixed.md", "- Fixed.\n")
        write_fragment(repo, "a.added.md", "- Added.\n")
        assembled = release.assemble_fragments(repo)
        order = [assembled.index(h) for h in ("### Added", "### Fixed", "### Security")]
        assert order == sorted(order), assembled

    def test_only_the_sections_with_fragments_get_a_heading(self, repo):
        write_fragment(repo, "a.added.md", "- Added.\n")
        assembled = release.assemble_fragments(repo)
        assert "### Added" in assembled
        assert "### Fixed" not in assembled
        assert "### Security" not in assembled

    def test_fragments_in_one_section_are_ordered_by_slug(self, repo):
        """Deterministic output: the same fragments must assemble the same way
        on any machine, whatever order the filesystem lists them in."""
        write_fragment(repo, "zebra.fixed.md", "- Zebra.\n")
        write_fragment(repo, "apple.fixed.md", "- Apple.\n")
        assembled = release.assemble_fragments(repo)
        assert assembled.index("- Apple.") < assembled.index("- Zebra.")

    def test_a_multi_line_fragment_keeps_its_shape(self, repo):
        write_fragment(repo, "a.fixed.md", "- One thing (#1).\n  Continued here.\n- Another.\n")
        assembled = release.assemble_fragments(repo)
        assert "- One thing (#1).\n  Continued here.\n- Another." in assembled

    def test_no_fragments_assembles_to_nothing(self, repo):
        assert release.assemble_fragments(repo) == ""

    def test_two_fragments_with_the_same_slug_in_one_section_both_appear(self, repo):
        """Same slug, different sections is legitimate (one change that both
        adds and fixes); nothing may be dropped."""
        write_fragment(repo, "a-theme.added.md", "- Added by the theme.\n")
        write_fragment(repo, "a-theme.fixed.md", "- Fixed by the theme.\n")
        assembled = release.assemble_fragments(repo)
        assert "- Added by the theme." in assembled
        assert "- Fixed by the theme." in assembled


class TestNotesIncludeFragments:
    """`release_notes_for` is the seam the script's own docstring names as the
    one place that knows where the notes come from."""

    def test_fragments_are_appended_to_the_section_body(self, repo):
        write_fragment(repo, "a-thing.fixed.md", "- A thing was fixed.\n")
        notes = release.release_notes_for(repo, "0.24.2")
        assert "- A release script." in notes  # the hand-written section body
        assert "### Fixed" in notes
        assert "- A thing was fixed." in notes

    def test_with_no_fragments_the_notes_are_the_section_alone(self, repo):
        notes = release.release_notes_for(repo, "0.24.2")
        assert notes.strip() == "### Added\n\n- A release script."

    def test_a_section_with_only_fragments_still_has_notes(self, repo):
        """The expected steady state once fragments are the habit: the release
        commit dates an empty heading and every bullet is a fragment."""
        (repo / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [Unreleased]\n\n## [0.24.2] - {date.today().isoformat()}\n\n"
            "## [0.24.1] - 2026-09-17\n\n- Older.\n"
        )
        write_fragment(repo, "a-thing.fixed.md", "- A thing was fixed.\n")
        assert "- A thing was fixed." in release.release_notes_for(repo, "0.24.2")

    def test_a_bad_fragment_fails_the_notes_rather_than_being_skipped(self, repo):
        write_fragment(repo, "a-thing.nonsense.md", "- A thing.\n")
        with pytest.raises(release.ReleaseError):
            release.release_notes_for(repo, "0.24.2")

    def test_the_contributors_line_still_comes_last(self, repo):
        write_fragment(repo, "a-thing.fixed.md", "- A thing was fixed.\n")
        notes = release.compose_notes(repo, "0.24.2", ["someone"])
        assert notes.rstrip().endswith("Thanks to @someone for their contributions.")
        assert notes.index("- A thing was fixed.") < notes.index("Thanks to")


class TestChangelogPreconditionWithFragments:
    """Step (a) has to accept the state fragments create and keep refusing the
    state they were meant to remove."""

    def test_an_empty_version_section_passes_when_fragments_supply_it(self, repo):
        (repo / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [0.24.2] - {date.today().isoformat()}\n\n"
            "## [0.24.1] - 2026-09-17\n\n- Older.\n"
        )
        write_fragment(repo, "a-thing.fixed.md", "- A thing.\n")
        release.check_changelog(repo, "0.24.2")  # no raise

    def test_an_empty_section_with_no_fragments_is_still_an_error(self, repo):
        """A release with empty notes tells users nothing -- the original rule,
        which fragments must not quietly disable."""
        (repo / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [0.24.2] - {date.today().isoformat()}\n\n"
            "## [0.24.1] - 2026-09-17\n\n- Older.\n"
        )
        with pytest.raises(release.ReleaseError, match="no content"):
            release.check_changelog(repo, "0.24.2")

    def test_the_empty_section_error_mentions_fragments(self, repo):
        (repo / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [0.24.2] - {date.today().isoformat()}\n\n"
            "## [0.24.1] - 2026-09-17\n\n- Older.\n"
        )
        with pytest.raises(release.ReleaseError, match="changelog.d"):
            release.check_changelog(repo, "0.24.2")

    def test_an_unreleased_bullet_is_still_refused(self, repo):
        """Fragments do not make the old mistake safe: a bullet under
        `[Unreleased]` would still be left out of the notes."""
        (repo / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [Unreleased]\n\n- Stranded.\n\n"
            f"## [0.24.2] - {date.today().isoformat()}\n\n- Thing.\n"
        )
        with pytest.raises(release.ReleaseError, match="Unreleased"):
            release.check_changelog(repo, "0.24.2")

    def test_a_malformed_fragment_fails_the_precondition(self, repo):
        """Before anything irreversible: a misspelled section is caught in step
        (a), not discovered when the notes are composed in step (d)."""
        write_fragment(repo, "a-thing.fixd.md", "- A thing.\n")
        with pytest.raises(release.ReleaseError, match="fixd"):
            release.check_changelog(repo, "0.24.2")


class TestFragmentsAreConsumedByTheRelease:
    """Step (e) writes the assembled bullets into CHANGELOG.md and removes the
    fragments. Leaving them would republish every entry in the next release."""

    @pytest.fixture
    def repo_with_fragments(self, tmp_path_factory):
        repo = tmp_path_factory.mktemp("release-repo-fragments")
        (repo / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [0.24.2] - {date.today().isoformat()}\n\n"
            "## [0.24.1] - 2026-09-17\n\n- Older.\n"
        )
        (repo / "pyproject.toml").write_text(GOOD_PYPROJECT)
        write_fragment(repo, "a-thing.fixed.md", "- A thing was fixed.\n")
        write_fragment(repo, "another.added.md", "- Something new.\n")
        return repo

    def _post_release(self, repo, execute):
        runner = release.Runner(execute=execute)
        ctx = release.Context(
            repo=repo,
            version="0.24.2",
            runner=runner,
            wait_ci_minutes=0,
            sha=FAKE_SHA,
        )
        release.step_post_release(ctx)
        return runner

    def test_a_dry_run_changes_nothing_on_disk(self, repo_with_fragments):
        before = sorted(p.name for p in (repo_with_fragments / "changelog.d").iterdir())
        text_before = (repo_with_fragments / "CHANGELOG.md").read_text()
        self._post_release(repo_with_fragments, execute=False)
        after = sorted(p.name for p in (repo_with_fragments / "changelog.d").iterdir())
        assert after == before
        assert (repo_with_fragments / "CHANGELOG.md").read_text() == text_before

    def test_a_dry_run_says_which_fragments_it_would_remove(self, repo_with_fragments, capsys):
        """Named one by one, not counted: the maintainer reading a dry run is
        checking that the release consumes exactly what it should."""
        self._post_release(repo_with_fragments, execute=False)
        out = capsys.readouterr().out
        assert "WOULD RUN: remove changelog.d/a-thing.fixed.md" in out, out
        assert "WOULD RUN: remove changelog.d/another.added.md" in out, out

    def test_executing_writes_the_bullets_into_the_version_section(
        self, repo_with_fragments, monkeypatch
    ):
        monkeypatch.setattr(release, "_check_output", lambda cmd, **kw: "")
        self._post_release(repo_with_fragments, execute=True)
        text = (repo_with_fragments / "CHANGELOG.md").read_text()
        assert "- A thing was fixed." in text
        assert "- Something new." in text
        # Under the released version, not under the reopened [Unreleased].
        assert text.index("## [0.24.2]") < text.index("- Something new.")
        assert text.index("- A thing was fixed.") < text.index("## [0.24.1]")

    def test_executing_deletes_the_fragment_files(self, repo_with_fragments, monkeypatch):
        monkeypatch.setattr(release, "_check_output", lambda cmd, **kw: "")
        self._post_release(repo_with_fragments, execute=True)
        assert list((repo_with_fragments / "changelog.d").glob("*.md")) == []

    def test_the_readme_survives(self, repo_with_fragments, monkeypatch):
        write_fragment(repo_with_fragments, "README.md", "how to write a fragment\n")
        monkeypatch.setattr(release, "_check_output", lambda cmd, **kw: "")
        self._post_release(repo_with_fragments, execute=True)
        assert (repo_with_fragments / "changelog.d" / "README.md").is_file()

    def test_the_commit_names_both_paths(self, repo_with_fragments):
        runner = self._post_release(repo_with_fragments, execute=False)
        commits = [c for c in runner.planned if "commit" in c]
        assert commits, runner.planned
        assert any("CHANGELOG.md" in arg for arg in commits[0])
        assert any("changelog.d" in arg for arg in commits[0])

    def test_reopening_unreleased_still_happens(self, repo_with_fragments, monkeypatch):
        monkeypatch.setattr(release, "_check_output", lambda cmd, **kw: "")
        self._post_release(repo_with_fragments, execute=True)
        text = (repo_with_fragments / "CHANGELOG.md").read_text()
        assert "## [Unreleased]" in text
        assert text.index("## [Unreleased]") < text.index("## [0.24.2]")

    def test_it_is_still_a_branch_and_never_dev(self, repo_with_fragments):
        runner = self._post_release(repo_with_fragments, execute=False)
        planned = [" ".join(c) for c in runner.planned]
        checkout = [i for i, p in enumerate(planned) if "checkout -b" in p]
        commits = [i for i, p in enumerate(planned) if "commit -m" in p]
        assert checkout and commits and checkout[0] < commits[0], planned

    def test_with_no_fragments_the_step_is_unchanged(self, repo, monkeypatch):
        """The existing no-op path: an `[Unreleased]` already present and no
        fragments to consume means nothing to do."""
        monkeypatch.setattr(release, "_check_output", lambda cmd, **kw: "")
        runner = self._post_release(repo, execute=True)
        assert runner.planned == []


class TestDryRunPrintsTheAssembledSection:
    """The acceptance criterion: `scripts/release.py --dry-run` prints the
    assembled section, so the maintainer reads the notes before the tag."""

    def test_the_assembled_bullets_are_printed(self, dry_run, capsys, monkeypatch):
        repo_arg = None

        def capture(repo, version):
            nonlocal repo_arg
            repo_arg = repo
            return "### Fixed\n\n- A thing was fixed.\n"

        monkeypatch.setattr(release, "release_notes_for", capture)
        dry_run()
        out = capsys.readouterr().out
        assert "- A thing was fixed." in out, out
        assert repo_arg is not None

    def test_the_printed_notes_are_labelled(self, dry_run, capsys):
        dry_run()
        out = capsys.readouterr().out
        assert "release notes" in out

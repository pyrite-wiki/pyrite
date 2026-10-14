"""The changelog-fragment discipline (#243).

`CHANGELOG.md` conflicted five times in one session and on nothing else --
three of them on first-time contributors' PRs. Every `[Unreleased]` bullet was
appended at the same spot, so any two PRs in flight conflicted there *by
construction*, and the resolution was always "keep both, either order": no
judgement, which is the definition of a conflict that should not exist.

The fix is the towncrier pattern, hand-rolled (no new dependency): each change
adds `changelog.d/<slug>.<section>.md`, a path no other PR writes. The release
script assembles the fragments under the version heading and deletes them.

These tests pin the half of that which is about the repository's own state --
the fragments that exist on this branch, the `[Unreleased]` section staying
empty, and the claim the whole design rests on: two branches each adding a
fragment rebase onto each other with no conflict. The assembly itself lives in
tests/test_release_script.py, next to the script that does it.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import release  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
FRAGMENT_DIR = REPO / "changelog.d"


# --------------------------------------------------------------------------
# the naming convention
# --------------------------------------------------------------------------


class TestFragmentNames:
    def test_the_sections_are_keep_a_changelog_sections(self):
        """CHANGELOG.md declares itself Keep a Changelog, and the assembled
        sections land in that file: a section it does not define would read as
        a heading no format owns."""
        assert release.FRAGMENT_SECTIONS == (
            "added",
            "changed",
            "deprecated",
            "removed",
            "fixed",
            "security",
        )

    @pytest.mark.parametrize(
        ("name", "slug", "section"),
        [
            ("changelog-fragments.changed.md", "changelog-fragments", "changed"),
            ("create-type-guard.fixed.md", "create-type-guard", "fixed"),
            ("a.b.c.security.md", "a.b.c", "security"),
        ],
    )
    def test_a_well_formed_name_parses(self, name, slug, section):
        parsed = release.parse_fragment_name(name)
        assert (parsed.slug, parsed.section) == (slug, section)

    def test_an_unknown_section_is_an_error_naming_the_sections(self):
        """A dropped changelog entry is how a security fix goes unannounced,
        so a misspelled section must fail the release, never be skipped."""
        with pytest.raises(release.ReleaseError) as exc:
            release.parse_fragment_name("something.fixd.md")
        assert "fixd" in str(exc.value)
        assert "fixed" in str(exc.value)

    def test_a_name_with_no_section_at_all_is_an_error(self):
        with pytest.raises(release.ReleaseError, match="<slug>.<section>.md"):
            release.parse_fragment_name("justaslug.md")

    def test_a_non_markdown_name_is_an_error(self):
        with pytest.raises(release.ReleaseError, match="<slug>.<section>.md"):
            release.parse_fragment_name("something.fixed.txt")

    def test_an_empty_slug_is_an_error(self):
        with pytest.raises(release.ReleaseError, match="slug"):
            release.parse_fragment_name(".fixed.md")


# --------------------------------------------------------------------------
# this repository's own fragments
# --------------------------------------------------------------------------


class TestThisRepositorysFragments:
    def test_the_fragment_directory_exists_with_a_readme(self):
        """The README is where a contributor looks: the directory has to
        explain itself, because nothing else in a checkout will."""
        assert FRAGMENT_DIR.is_dir(), f"{FRAGMENT_DIR} does not exist"
        readme = FRAGMENT_DIR / "README.md"
        assert readme.is_file()
        text = readme.read_text()
        assert "<slug>.<section>.md" in text
        for section in release.FRAGMENT_SECTIONS:
            assert section in text, f"the README does not name the {section!r} section"

    def test_every_fragment_on_this_branch_is_valid(self):
        """Unknown section, empty body, or a name that does not parse -- caught
        here, where the fix is cheap, rather than at release time."""
        for fragment in release.fragment_paths(REPO):
            parsed = release.parse_fragment_name(fragment.name)
            assert parsed.section in release.FRAGMENT_SECTIONS
            assert fragment.read_text().strip(), f"{fragment.name} is empty"

    def test_the_readme_is_not_collected_as_a_fragment(self):
        """`README.md` sits in the directory and is not an entry; collecting it
        would put the instructions into the release notes."""
        assert (FRAGMENT_DIR / "README.md").is_file()
        assert all(p.name != "README.md" for p in release.fragment_paths(REPO))


class TestUnreleasedStaysEmpty:
    """The discipline this whole change exists to enforce.

    An `[Unreleased]` bullet is the conflict. Once fragments are the way to
    record a change, a bullet appearing under `[Unreleased]` on `dev` means
    someone did the conflicting thing -- and it will be invisible until the
    next contributor's rebase fails on it.
    """

    @staticmethod
    def _unreleased_body(text: str) -> str:
        masked = release.mask_fenced_blocks(text)
        heading = re.search(r"^##\s*\[Unreleased\][^\n]*$", masked, re.M)
        if not heading:
            return ""
        following = re.compile(r"^##\s", re.M).search(masked, heading.end())
        return text[heading.end() : following.start() if following else len(text)].strip()

    def test_the_repository_changelog_has_an_empty_unreleased(self):
        body = self._unreleased_body((REPO / "CHANGELOG.md").read_text())
        assert body == "", (
            "CHANGELOG.md `## [Unreleased]` is not empty. Unreleased changes "
            "are recorded as fragments under changelog.d/ (see its README); a "
            "bullet here is the merge conflict this convention removes:\n"
            f"{body}"
        )

    def test_the_helper_would_catch_a_bullet(self):
        """The assertion above passes trivially against a broken helper, so
        prove the helper sees a bullet when there is one."""
        text = "# Changelog\n\n## [Unreleased]\n\n- a bullet\n\n## [0.1.0] - 2026-01-01\n\n- old\n"
        assert self._unreleased_body(text) == "- a bullet"


# --------------------------------------------------------------------------
# the claim the design rests on
# --------------------------------------------------------------------------


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture
def scratch_repo(tmp_path):
    """A real git repository with a CHANGELOG and a changelog.d/, on `dev`."""
    repo = tmp_path / "scratch"
    repo.mkdir()
    for args in (
        ("init", "-q", "-b", "dev"),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "Test"),
        ("config", "commit.gpgsign", "false"),
    ):
        assert _git(*args, cwd=repo).returncode == 0
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [0.1.0] - 2026-01-01\n\n### Added\n\n- The first.\n"
    )
    (repo / "changelog.d").mkdir()
    (repo / "changelog.d" / "README.md").write_text("fragments live here\n")
    assert _git("add", "-A", cwd=repo).returncode == 0
    assert _git("commit", "-qm", "base", cwd=repo).returncode == 0
    return repo


class TestTwoBranchesDoNotConflict:
    """The measured failure, reproduced both ways round in a real repository.

    The `[Unreleased]` case is here on purpose: without it the fragment case
    proves only that two files with different names do not collide, which is
    true of any two files. What has to be shown is that the *same change*,
    recorded the old way, does conflict -- and the new way does not.
    """

    @staticmethod
    def _two_branches(repo, make_change):
        """Branch `a` and branch `b` off `dev`, each recording one change."""
        shas = {}
        for name, payload in (("a", "alpha"), ("b", "beta")):
            assert _git("checkout", "-q", "dev", cwd=repo).returncode == 0
            assert _git("checkout", "-q", "-b", name, cwd=repo).returncode == 0
            make_change(repo, name, payload)
            assert _git("add", "-A", cwd=repo).returncode == 0
            assert _git("commit", "-qm", f"change from {name}", cwd=repo).returncode == 0
            shas[name] = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
        return shas

    @staticmethod
    def _rebase_b_onto_a(repo):
        assert _git("checkout", "-q", "b", cwd=repo).returncode == 0
        return _git("rebase", "a", cwd=repo)

    def test_the_old_way_conflicts(self, scratch_repo):
        """The bug, reproduced: two bullets appended at the same spot."""

        def append_a_bullet(repo, name, payload):
            text = (repo / "CHANGELOG.md").read_text()
            text = text.replace(
                "## [Unreleased]\n",
                f"## [Unreleased]\n\n### Fixed\n\n- A fix from {payload}.\n",
                1,
            )
            (repo / "CHANGELOG.md").write_text(text)

        self._two_branches(scratch_repo, append_a_bullet)
        proc = self._rebase_b_onto_a(scratch_repo)
        assert proc.returncode != 0, "expected the CHANGELOG rebase to conflict"
        assert "CONFLICT" in proc.stdout + proc.stderr
        _git("rebase", "--abort", cwd=scratch_repo)

    def test_fragments_do_not_conflict(self, scratch_repo):
        """The fix: each branch writes a path the other never touches."""

        def add_a_fragment(repo, name, payload):
            (repo / "changelog.d" / f"{payload}-thing.fixed.md").write_text(
                f"- A fix from {payload}.\n"
            )

        self._two_branches(scratch_repo, add_a_fragment)
        proc = self._rebase_b_onto_a(scratch_repo)
        combined = proc.stdout + proc.stderr
        assert proc.returncode == 0, f"the rebase failed:\n{combined}"
        assert "CONFLICT" not in combined

        # Both fragments survive the rebase: "no conflict" would be worthless
        # if one of them had been silently dropped.
        names = sorted(p.name for p in (scratch_repo / "changelog.d").glob("*.fixed.md"))
        assert names == ["alpha-thing.fixed.md", "beta-thing.fixed.md"]

    def test_it_holds_in_the_other_direction_too(self, scratch_repo):
        """Rebasing `a` onto `b` rather than `b` onto `a`. Order-dependence
        would mean the guarantee holds only for whoever merges first."""

        def add_a_fragment(repo, name, payload):
            (repo / "changelog.d" / f"{payload}-thing.added.md").write_text(
                f"- Something from {payload}.\n"
            )

        self._two_branches(scratch_repo, add_a_fragment)
        assert _git("checkout", "-q", "a", cwd=scratch_repo).returncode == 0
        proc = _git("rebase", "b", cwd=scratch_repo)
        combined = proc.stdout + proc.stderr
        assert proc.returncode == 0, f"the rebase failed:\n{combined}"
        assert "CONFLICT" not in combined

    def test_a_merge_of_the_two_branches_is_also_clean(self, scratch_repo):
        """Auto-merge merges rather than rebases when the branch carries merge
        commits (#174's actual failure path), so the merge must be clean too."""

        def add_a_fragment(repo, name, payload):
            (repo / "changelog.d" / f"{payload}-thing.changed.md").write_text(
                f"- Changed by {payload}.\n"
            )

        self._two_branches(scratch_repo, add_a_fragment)
        assert _git("checkout", "-q", "a", cwd=scratch_repo).returncode == 0
        proc = _git("merge", "--no-edit", "b", cwd=scratch_repo)
        combined = proc.stdout + proc.stderr
        assert proc.returncode == 0, f"the merge failed:\n{combined}"
        assert "CONFLICT" not in combined


class TestNoTestReadsAFragmentByName:
    """A release deletes every fragment after moving its text into CHANGELOG.md,
    so a test that opens `changelog.d/<slug>.<section>.md` breaks at every
    release (the v0.25.7 chore PR's merge queue). Fixture names inside a
    temporary directory are fine; the real directory is not."""

    FRAGMENT_PATH = re.compile(
        r"changelog\.d/[\w.-]*?\.(?:" + "|".join(release.FRAGMENT_SECTIONS) + r")\.md"
    )
    # Test files that only use fragment names as fixtures or as strings under
    # test, never as a path into the real directory.
    EXEMPT = {
        "test_changelog_fragments.py",
        "test_changelog_fragment_check.py",
        "test_release_script.py",
        "test_test_affected.py",
        "test_verify_red_ci.py",
    }

    def _offenders(self, files):
        found = []
        for f in files:
            if f.name in self.EXEMPT:
                continue
            for n, line in enumerate(f.read_text().splitlines(), 1):
                if self.FRAGMENT_PATH.search(line):
                    found.append(f"{f}:{n}: {line.strip()}")
        return found

    def test_no_test_names_a_real_fragment(self):
        files = list((REPO / "tests").rglob("test_*.py"))
        files += list((REPO / "extensions").glob("*/tests/**/*.py"))
        assert self._offenders(files) == [], (
            "a test reads a changelog fragment by name; fragments are deleted at "
            "release (see changelog.d/README.md)"
        )

    def test_the_check_would_catch_the_old_line(self, tmp_path):
        planted = tmp_path / "test_planted.py"
        planted.write_text(
            'text = pathlib.Path("changelog.d/66-orient-true-as-first-call.changed.md").read_text()\n'
        )
        assert len(self._offenders([planted])) == 1

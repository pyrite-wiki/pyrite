#!/usr/bin/env python3
"""One command from a CI-verified `dev` commit to a GitHub release.

    scripts/release.py 0.24.2              # dry run: prints every step, changes nothing
    scripts/release.py 0.24.2 --execute    # actually cuts the release
    scripts/release.py 0.24.2 --wait-ci 20 # wait up to 20 min for a pending CI run

v0.24.1 took ten manual steps across two sessions, one of which pushed with
`--no-verify` because the pre-push hooks ran fixers over the wrong range. The
maintainer keeps the *decision* to cut a release; this script makes executing
that decision one command, with every check in front of the first thing that
cannot be undone.

The order is the point (ADR-0032 §3a). Six steps, and the only two that can
change the world come after every check:

  a. preconditions  -- clean `dev` at `origin/dev`; the repo is taken FROM
                       `origin` (never hard-coded) and the `gh` calls and the
                       push agree on it; `v<version>` exists nowhere
                       yet (locally, on origin, or as a GitHub release) and
                       `origin/main` is an ancestor of the SHA, so step d can
                       only ever fast-forward; the version in pyproject.toml is
                       <version>; CHANGELOG has a dated section with content
                       and no stranded `[Unreleased]` entries; no open PR
                       labelled `release-blocker`
  b. ci             -- the REQUIRED checks for that exact SHA concluded
                       `success` (default `gate`; `--require-check` to change
                       it). Checks not named are advisory and never block --
                       the breadth jobs that run after the merge gate must not
                       hold a tag. Never starts a run; waits, with `--wait-ci`.
  c. release layer  -- what a *user* gets, checked before the tag exists:
                       install from the SHA into a fresh temp venv with `uv`,
                       `pyrite --version`, and the getting-started tutorial run
                       against that install. The Docker build is OPT-IN
                       (`--docker-check`): no image is published, so building
                       one gated the release on an artifact nobody receives.
  d. publish        -- IRREVERSIBLE. Fast-forward `main` to the SHA, tag it,
                       push the tag, `gh release create` with the CHANGELOG
                       section plus the contributors line.
  e. post-release   -- IRREVERSIBLE (a commit). Reopen `[Unreleased]` on a
                       fresh `release/reopen-unreleased-<version>` branch cut
                       from the release commit, and print the push and
                       `gh pr create` lines. Local `dev` is never committed on:
                       `dev` takes pull requests only.
  f. handoff        -- what the release does NOT do, said out loud: pyrite.wiki
                       (outside this repo, and it names the version and quotes
                       counts that go stale), the deploys the tag does not
                       trigger, the [Unreleased] PR. Changes nothing.

Safety rules, pinned by tests/test_release_script.py:

  * `--dry-run` is the default. `--execute` is the only way anything is written.
  * It never passes `--no-verify`, never force-pushes, never deletes a ref.
  * It refuses a dirty checkout, a branch other than `dev`, or a `dev` that is
    not exactly `origin/dev`.
  * Every irreversible command is printed verbatim before it runs, and printed
    *instead of* running without `--execute`.
  * `gh` reads are allowed; `gh` writes are dry-run-printed. This script never
    creates labels: the `release-blocker` label is a one-time prerequisite, and
    a missing one is a hard failure with the `gh label create` line to run --
    never a note the release then proceeds past. See the runbook.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BLOCKER_LABEL = "release-blocker"

# The repository is NOT hard-coded. It is resolved from `origin` at run time by
# `resolve_repo_slug`, so the script releases whatever checkout it is run from.
# It used to be the literal `markramm/pyrite`, and step (a) refused to run when
# `origin` disagreed -- correct while the repo lived there, and a total block on
# releasing the moment it moved to the pyrite-wiki org (#182, #259). The guard
# that mattered is kept and sharpened: see `check_remote_is_the_release_repo`.

# Who is filtered out of the contributors line. This is a *person*, not a
# repository owner, which is why it no longer builds the slug: after the org
# move the owner is an organisation and the maintainer is still a human.
MAINTAINER = "markramm"

SEMVER = re.compile(r"^\d+\.\d+\.\d+([-.][0-9A-Za-z.]+)?$")

# The checks a release waits on, by name. `gate` is the required check on dev
# and main: it needs the jobs that must pass and fails on any of their
# failures, so requiring it requires them. Advisory checks (breadth jobs that
# run after the gate) are deliberately not here -- see ci_decision.
DEFAULT_REQUIRED_CHECKS = ("gate",)

# The commonest case is "I just merged to dev, CI is running". Defaulting to no
# wait made that an immediate failure and a re-run, which teaches the
# maintainer to retry rather than to trust the check; 15 minutes comfortably
# covers the full matrix on dev (~3 min for one leg). `--wait-ci 0` still
# fails immediately for a scripted check.
DEFAULT_WAIT_CI_MINUTES = 15

# The extras the release layer installs. This must match what
# docs/getting-started.md tells a user to install, because step (c) then runs
# that very tutorial against this install: `[server,cli]` omits
# sentence-transformers, so the tutorial's `pyrite index embed` failed on a
# release candidate that was fine -- the check was narrower than the document
# it was checking.
INSTALL_CHECK_EXTRAS = "all"

# The second document step (c) runs against the install, after getting-started:
# the tutorial a newcomer is sent to first (docs/tutorials/pyrite-in-20-minutes.md).
TUTORIAL_20_MIN = "docs/tutorials/pyrite-in-20-minutes.md"

CI_PASSED = "passed"
CI_FAILED = "failed"
CI_PENDING = "pending"
CI_MISSING = "missing"

# --- changelog fragments (#243) --------------------------------------------
#
# `CHANGELOG.md` conflicted five times in one session and on nothing else --
# three of them on first-time contributors' PRs -- because every `[Unreleased]`
# bullet is appended at the same spot, so any two PRs in flight collide there
# by construction. The resolution was always "keep both, either order": no
# judgement, which is the definition of a conflict that should not exist.
#
# The towncrier pattern, hand-rolled rather than depended on: each change adds
# `changelog.d/<slug>.<section>.md`, a path no other PR writes. This script
# assembles them under the version heading at release time and deletes them.
FRAGMENT_DIR_NAME = "changelog.d"

# Keep a Changelog's sections, because that is the format CHANGELOG.md declares
# and the assembled headings land in that file. A section outside this tuple is
# a hard error, never a skipped file: a dropped entry is how a security fix
# goes unannounced.
FRAGMENT_SECTIONS = ("added", "changed", "deprecated", "removed", "fixed", "security")

# Files that live in the directory without being entries.
FRAGMENT_NON_ENTRIES = frozenset({"README.md", ".gitkeep", ".gitignore"})


class ReleaseError(Exception):
    """A check said no. The message is what the maintainer needs to do."""


# ---------------------------------------------------------------------------
# process plumbing -- one seam (`_check_output`) so tests never shell out
# ---------------------------------------------------------------------------


def _check_output(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> str:
    """Run a READ-ONLY command and return its stdout. The single subprocess
    seam in this module: tests monkeypatch this and nothing escapes."""
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ReleaseError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"{proc.stdout}\n{proc.stderr}".rstrip()
        )
    return proc.stdout


def _gh_json(cmd: list[str]) -> list | dict:
    raw = _check_output(cmd).strip()
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"could not parse JSON from {' '.join(cmd)}: {exc}") from exc


class Runner:
    """Prints every command; runs the writes only with `--execute`.

    A "write" is anything that changes the world outside this process: a push,
    a tag, `gh release create`, a commit. Reads go through `_check_output`
    directly -- they are safe in both modes and the checks need their answers.
    """

    def __init__(self, execute: bool):
        self.execute = execute
        # Every write this run planned, in order -- printed or performed. The
        # safety tests read it back and assert what was composed, not what the
        # source text happens to say.
        self.planned: list[list[str]] = []
        # Only what actually RAN, and only once it returned. A failure after
        # the first irreversible command must be able to say which of them
        # happened -- "nothing further was attempted" is no help when `main`
        # has already moved.
        self.performed: list[list[str]] = []

    def run_write(self, cmd: list[str], cwd: Path | None = None) -> str | None:
        self.planned.append(list(cmd))
        # shlex.join, not " ".join: the maintainer pastes these lines into a
        # shell, and an unquoted `--description Must not ship in the next
        # release` is five arguments there and one here.
        rendered = shlex.join(cmd)
        if not self.execute:
            print(f"    WOULD RUN: {rendered}")
            return None
        print(f"    RUN: {rendered}")
        out = _check_output(cmd, cwd=cwd)
        self.performed.append(list(cmd))
        return out

    def note(self, message: str) -> None:
        print(f"    {message}")


# ---------------------------------------------------------------------------
# git reads
# ---------------------------------------------------------------------------


def git_status_porcelain(repo: Path) -> str:
    return _check_output(["git", "-C", str(repo), "status", "--porcelain"]).strip()


def current_branch(repo: Path) -> str:
    return _check_output(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"]).strip()


def rev_parse(repo: Path, ref: str) -> str:
    return _check_output(["git", "-C", str(repo), "rev-parse", ref]).strip()


# ---------------------------------------------------------------------------
# version + CHANGELOG (pure; the bulk of the tests)
# ---------------------------------------------------------------------------


def normalize_version(version: str) -> str:
    """Accept `0.24.2`, refuse `v0.24.2` and `0.24`.

    A `v` prefix is refused rather than stripped: the tag is `v<version>` and
    the pyproject version is `<version>`, and quietly accepting both spellings
    is how one of them ends up wrong.
    """
    if not SEMVER.match(version):
        raise ReleaseError(
            f"{version!r} is not a version: give X.Y.Z with no 'v' prefix "
            "(the tag gets the v, you do not)."
        )
    return version


def read_pyproject_version(repo: Path) -> str:
    """The declared version, or a ReleaseError naming the file.

    Every failure here is a ReleaseError, not a traceback: on release day the
    maintainer needs to know which file is wrong and what to do about it, and a
    `KeyError: 'project'` says neither.
    """
    path = repo / "pyproject.toml"
    if not path.exists():
        raise ReleaseError(f"no pyproject.toml at {path}")
    try:
        parsed = tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ReleaseError(f"{path} could not be read as TOML: {exc}") from exc
    project = parsed.get("project")
    if not isinstance(project, dict):
        raise ReleaseError(f"{path} has no `[project]` table, so it declares no version.")
    version = project.get("version")
    if not isinstance(version, str):
        raise ReleaseError(
            f"{path} has no `version` under `[project]`. The release version is "
            "written there and nowhere else."
        )
    return version


def check_version_matches(repo: Path, version: str) -> None:
    declared = read_pyproject_version(repo)
    if declared != version:
        raise ReleaseError(
            f"pyproject.toml says version = {declared!r} but you asked to release "
            f"{version!r}. The release commit belongs on dev: bump pyproject.toml "
            "and date the CHANGELOG section there first."
        )


# ---------------------------------------------------------------------------
# changelog fragments (pure, over the files in `changelog.d/`)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fragment:
    """One `changelog.d/<slug>.<section>.md`, parsed."""

    slug: str
    section: str
    body: str = ""
    path: Path | None = None


def parse_fragment_name(name: str) -> Fragment:
    """`<slug>.<section>.md` -> Fragment(slug, section), or a ReleaseError.

    The slug may itself contain dots (`pyrite.cli.fixed.md`), so the section is
    the *last* component before `.md` -- and it must be one this project knows.
    Every failure names the file and the sections, because the person reading
    it is a contributor who has just had their fragment refused.
    """
    shape = (
        f"a fragment is named `<slug>.<section>.md`, where <section> is one of "
        f"{', '.join(FRAGMENT_SECTIONS)} (see {FRAGMENT_DIR_NAME}/README.md)."
    )
    if not name.endswith(".md"):
        raise ReleaseError(f"{name!r} is not a changelog fragment: {shape}")
    stem = name[: -len(".md")]
    slug, _, section = stem.rpartition(".")
    if not section or not _:
        raise ReleaseError(f"{name!r} names no section: {shape}")
    if not slug:
        raise ReleaseError(f"{name!r} has an empty slug: {shape}")
    if section not in FRAGMENT_SECTIONS:
        raise ReleaseError(
            f"{name!r} names the section {section!r}, which is not a changelog "
            f"section. {shape} Rename the file -- this is refused rather than "
            "skipped, because an entry silently dropped from the release notes "
            "is how a security fix goes unannounced."
        )
    return Fragment(slug=slug, section=section)


def fragment_paths(repo: Path) -> list[Path]:
    """Every file under `changelog.d/` that is meant to be an entry, sorted.

    The README and a `.gitkeep` live there without being entries; assembling
    the README would put the instructions into the release notes.
    """
    directory = repo / FRAGMENT_DIR_NAME
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.name not in FRAGMENT_NON_ENTRIES
    )


def collect_fragments(repo: Path) -> list[Fragment]:
    """Every fragment, parsed and read, or a ReleaseError naming the bad one.

    Sorted by (section order, slug) so the assembled notes are byte-identical
    on any machine: `iterdir` order is the filesystem's, not the project's.
    """
    fragments: list[Fragment] = []
    for path in fragment_paths(repo):
        parsed = parse_fragment_name(path.name)
        try:
            body = path.read_text().strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise ReleaseError(f"cannot read the changelog fragment {path}: {exc}") from exc
        if not body:
            raise ReleaseError(
                f"the changelog fragment {path.name} is empty. It would assemble "
                "to a heading with nothing under it; write the entry or delete "
                "the file."
            )
        fragments.append(Fragment(slug=parsed.slug, section=parsed.section, body=body, path=path))
    return sorted(fragments, key=lambda f: (FRAGMENT_SECTIONS.index(f.section), f.slug))


def assemble_fragments(repo: Path) -> str:
    """The fragments as CHANGELOG markdown: `### Section` then its entries.

    Only sections with fragments get a heading -- an empty `### Removed` in
    every release is noise -- and the order is Keep a Changelog's, not the
    order the files happened to be written in.
    """
    fragments = collect_fragments(repo)
    if not fragments:
        return ""
    chunks: list[str] = []
    for section in FRAGMENT_SECTIONS:
        entries = [f for f in fragments if f.section == section]
        if not entries:
            continue
        chunks.append(f"### {section.capitalize()}\n\n" + "\n\n".join(f.body for f in entries))
    return "\n\n".join(chunks) + "\n"


def _read_changelog(repo: Path) -> str:
    """The CHANGELOG text, or a ReleaseError naming the file it wanted."""
    path = repo / "CHANGELOG.md"
    try:
        return path.read_text()
    except OSError as exc:
        raise ReleaseError(
            f"cannot read {path}: {exc}. The release notes come from that file; "
            "it must exist on the commit being released."
        ) from exc


def mask_fenced_blocks(text: str) -> str:
    """The same text with fenced code blocks blanked out, offsets preserved.

    A CHANGELOG entry may legitimately quote a heading -- release notes that
    show CHANGELOG syntax, a runbook excerpt. Scanning raw text for `^##\\s`
    then truncates the notes at that quoted line and reports a stranded
    `[Unreleased]` that does not exist. Blanking rather than deleting keeps
    every offset in the masked copy usable against the original, so the notes
    are still sliced out of the real text, fences and all.

    Both ``` and ~~~ fences, three characters or more, per CommonMark.
    """
    out: list[str] = []
    fence: str | None = None

    def blanked(line: str) -> str:
        return " " * (len(line) - 1) + "\n" if line.endswith("\n") else " " * len(line)

    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if fence is None:
            if marker:
                fence = marker.group(1)[0]
                out.append(blanked(line))
                continue
            out.append(line)
        else:
            # A closing fence is the same character, at least as long, and
            # carries no info string after it.
            closes = (
                marker
                and marker.group(1)[0] == fence
                and not stripped[len(marker.group(1)) :].strip()
            )
            if closes:
                fence = None
            out.append(blanked(line))
    return "".join(out)


def _section_span(text: str, version: str) -> tuple[re.Match, int]:
    """Return the heading match for `## [<version>]` and where its body ends.

    Headings are found in a fence-masked copy (`mask_fenced_blocks`), but the
    offsets index the original text position-for-position, so the body sliced
    out is verbatim -- fences included.
    """
    masked = mask_fenced_blocks(text)
    pattern = re.compile(rf"^##\s*\[{re.escape(version)}\](?P<rest>[^\n]*)$", re.M)
    matches = list(pattern.finditer(masked))
    if not matches:
        raise ReleaseError(
            f"CHANGELOG.md has no `## [{version}]` section. Add one on dev, "
            f"dated today: `## [{version}] - {date.today().isoformat()}`."
        )
    if len(matches) > 1:
        lines = ", ".join(str(masked.count("\n", 0, m.start()) + 1) for m in matches)
        raise ReleaseError(
            f"CHANGELOG.md has a `## [{version}]` heading twice (lines {lines}). "
            "The notes would silently be the first one only -- merge them into "
            "one section on dev before releasing."
        )
    heading = matches[0]
    following = re.compile(r"^##\s", re.M).search(masked, heading.end())
    return heading, following.start() if following else len(text)


def check_changelog(repo: Path, version: str) -> None:
    """The section exists, is dated today, has content, and nothing is
    stranded under `[Unreleased]` below it."""
    text = _read_changelog(repo)
    heading, end = _section_span(text, version)

    rest = heading.group("rest")
    dated = re.search(r"(\d{4}-\d{2}-\d{2})", rest)
    if not dated:
        raise ReleaseError(
            f"`## [{version}]` is undated. The runbook wants "
            f"`## [{version}] - YYYY-MM-DD` so the release notes and the tag agree."
        )
    if dated.group(1) != date.today().isoformat():
        raise ReleaseError(
            f"`## [{version}]` is dated {dated.group(1)}, today is "
            f"{date.today().isoformat()}. Re-date it on dev (and let CI pass on "
            "that commit) before releasing."
        )

    # Parse the fragments here, in the first check, rather than when the notes
    # are composed in step (d): a misspelled section discovered there would be
    # discovered after `main` has already moved.
    fragments = collect_fragments(repo)

    body = text[heading.end() : end].strip()
    if not body and not fragments:
        raise ReleaseError(
            f"`## [{version}]` has no content and there are no fragments under "
            f"{FRAGMENT_DIR_NAME}/. A release with empty notes tells users "
            f"nothing; add the entries as fragments (see {FRAGMENT_DIR_NAME}/README.md) "
            "on dev first."
        )

    masked = mask_fenced_blocks(text)
    unreleased = re.search(r"^##\s*\[Unreleased\][^\n]*$", masked, re.M)
    if unreleased:
        u_end = re.compile(r"^##\s", re.M).search(masked, unreleased.end())
        u_body = text[unreleased.end() : u_end.start() if u_end else len(text)].strip()
        if u_body:
            raise ReleaseError(
                "`## [Unreleased]` still has entries. Either they ship in "
                f"{version} (move them into its section) or they do not (move "
                "them to a later release) -- as written they would be silently "
                "left out of the notes."
            )


def release_notes_for(repo: Path, version: str) -> str:
    """This version's notes: its CHANGELOG section, then the fragments.

    THE seam for where release notes come from, and now it has two sources
    (#243). The hand-written section body is the lede a release sometimes
    carries ("Operational -- see kb/roadmap.md"); the fragments are the
    per-change entries every PR contributed without touching a shared file.
    Keep this the only place that knows the notes' origin.
    """
    text = _read_changelog(repo)
    heading, end = _section_span(text, version)
    written = text[heading.end() : end].strip()
    assembled = assemble_fragments(repo).strip()
    parts = [part for part in (written, assembled) if part]
    return "\n\n".join(parts) + "\n"


def co_author_logins(trailers: list[str]) -> list[str]:
    """Outside people named in `Co-authored-by:` trailers, as logins.

    Crediting only the authors of *merged* PRs misses two real cases (#248):

    - a contributor whose PR is closed rather than merged because someone
      else's branch carried the same fix first -- #237 (Umar-2026) fixed the
      `sw new-adr --title` bug fourteen minutes before #239 merged the
      character-identical line, so their work shipped in 0.24.2 uncredited;
    - anyone whose commit was squashed into another PR, which is now the
      normal path: the merge queue squashes, and a squash keeps the trailers
      and drops the individual commits.

    CONTRIBUTING asks for the trailer precisely because it is machine-readable
    and survives a squash, so this reads what it already tells people to write.

    A GitHub noreply address (`12345+octocat@users.noreply.github.com`) carries
    the login, so it is unwrapped; anything else falls back to the email, which
    a human checks before the notes go out.
    """
    logins: list[str] = []
    for trailer in trailers:
        email = trailer.rpartition("<")[2].rstrip(">").strip()
        if not email:
            continue
        local = email.partition("@")[0]
        login = local.partition("+")[2] if "+" in local else local
        if email.endswith("users.noreply.github.com") and "+" in local:
            candidate = login
        elif "noreply" in email or "no-reply" in email:
            # A bot address with no login in it (opencode, anthropic): skip.
            continue
        else:
            candidate = email
        if not candidate:
            continue
        # Match against the WHOLE trailer, not just the address: the maintainer
        # appears as `markramm <mark.ramm@gmail.com>` (login in the name, not
        # the email) and `dependabot[bot] <support@github.com>` puts the bot
        # name outside the address entirely.
        whole = trailer.lower()
        if (
            MAINTAINER.lower() in whole
            or "dependabot" in whole
            or "copilot" in whole
            or "noreply@anthropic.com" in whole
        ):
            continue
        if candidate not in logins:
            logins.append(candidate)
    return sorted(logins)


def contributors_line(logins: list[str]) -> str | None:
    """Every outside author of a merged PR since the previous tag, credited.

    Contributors are why the project is not a solo project; the runbook says
    to say so.
    """
    outside = sorted(
        {
            login
            for login in logins
            if login
            and "@" not in login  # an email is never published (see below)
            and login != MAINTAINER
            and "dependabot" not in login.lower()
        }
    )
    if not outside:
        return None
    names = ", ".join(f"@{login}" for login in outside)
    return f"Thanks to {names} for their contributions."


def unresolved_contributors(logins: list[str]) -> list[str]:
    """Co-authors known only by email: never published, shown to the releaser.

    `co_author_logins` falls back to the email when a trailer carries no GitHub
    login. Release notes are public, so a personal address must not reach them
    (0.25.1's dry run would have printed one); the releaser credits the person
    by hand, or confirms they are already credited by login as a PR author.
    """
    return sorted({login for login in logins if login and "@" in login})


def compose_notes(repo: Path, version: str, logins: list[str]) -> str:
    notes = release_notes_for(repo, version)
    line = contributors_line(logins)
    if line:
        notes = notes.rstrip() + "\n\n" + line + "\n"
    return notes


# ---------------------------------------------------------------------------
# CI decision (pure, over `gh run list` JSON)
# ---------------------------------------------------------------------------


def _matches(check_name: str, required: str) -> bool:
    """`test` must match the matrix leg `test (3.12)` and the bare `test` a
    skipped matrix reports -- requiring one by name cannot mean requiring the
    exact string, or a docs-only run hangs the release the way it hung PRs."""
    return check_name == required or check_name.startswith(f"{required} (")


def _run_age(check: dict) -> tuple[str, str]:
    """Sort key for "which run of this check is the newest".

    `started_at` first (a rerun starts later even when it finishes sooner),
    `completed_at` as the fallback when the API omits it. Both are ISO-8601 in
    UTC, so string order is time order; a run missing both sorts oldest, which
    is the conservative reading when the newest run is the one with timestamps.
    """
    return (str(check.get("started_at") or ""), str(check.get("completed_at") or ""))


def ci_decision(checks: list[dict], required: tuple[str, ...] | None = None) -> str:
    """Verdict over the NAMED checks a release requires, for one SHA.

    Gating on "every check on the commit" is wrong and would get worse: `e2e`
    runs on pushes to `main` and is deliberately *not* in `gate`'s needs
    (ADR-0032 §3a keeps breadth out of the merge gate), so a red or slow e2e
    must not block a tag. What must be green is `gate` -- and `gate` is green
    only when the jobs it needs are.

    Conservative within that set: a required check still running outranks a
    sibling that passed, and any non-success conclusion is a failure. A
    `skipped` check passes (ADR-0032: the classifier saying "nothing to test
    here"). A required check that is absent entirely is CI_MISSING, not a pass.

    This function only reads a verdict; it cannot start a run.
    """
    required = required or DEFAULT_REQUIRED_CHECKS
    if not checks:
        return CI_MISSING

    verdicts = []
    for name in required:
        matching = [c for c in checks if _matches(c.get("name", ""), name)]
        if not matching:
            return CI_MISSING
        # Newest run PER CHECK NAME. A rerun of `gate` is a second check run
        # with the same name, so reading every run and failing on any red one
        # means a rerun to green can never unblock a release. Per name, not
        # overall: `test (3.11)` and `test (3.12)` are different names and both
        # must pass, however long ago each ran.
        for leg in sorted({c.get("name", "") for c in matching}):
            runs = [c for c in matching if c.get("name", "") == leg]
            newest = max(runs, key=_run_age)
            if newest.get("status") != "completed":
                verdicts.append(CI_PENDING)
            elif newest.get("conclusion") not in ("success", "skipped"):
                verdicts.append(CI_FAILED)
            else:
                verdicts.append(CI_PASSED)

    if CI_FAILED in verdicts:
        return CI_FAILED
    if CI_PENDING in verdicts:
        return CI_PENDING
    return CI_PASSED


def check_no_release_blockers(prs: list[dict]) -> None:
    if prs:
        listed = ", ".join(f"#{pr['number']} {pr.get('title', '')}".strip() for pr in prs)
        raise ReleaseError(
            f"open PR(s) labelled {BLOCKER_LABEL}: {listed}. Land or unlabel them before releasing."
        )


def remote_slug(url: str) -> str | None:
    """`owner/repo` from a git remote URL, in either spelling, or None.

    Both forms have to be understood because both are in use on the
    maintainer's machines: `https://github.com/owner/repo(.git)` and
    `git@github.com:owner/repo.git` (and its `ssh://` spelling).
    """
    url = url.strip()
    if not url:
        return None
    match = re.search(r"github\.com[:/](?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?/?$", url)
    return match.group("slug") if match else None


def resolve_repo_slug(repo: Path) -> str:
    """The `owner/name` this checkout releases, from `origin`.

    Every `gh --repo` call and the install spec read this, so the script
    releases the repository it is actually run from. Hard-coding it meant the
    org move (#182) would have left no release path at all: step (a) refused
    when `origin` disagreed, and `gh release view --repo` kept asking about
    the old address, which GitHub's redirects make *appear* to work -- the
    worse failure of the two, because it answers.

    A remote that is not a recognisable GitHub URL is a hard failure: a
    release cut against a guessed repository is not undoable.
    """
    url = _check_output(["git", "-C", str(repo), "remote", "get-url", "origin"]).strip()
    slug = remote_slug(url)
    if not slug:
        raise ReleaseError(
            f"`origin` is {url!r}, which is not a GitHub repository URL. This "
            "script cuts a GitHub release, so it needs to know which repo "
            "`origin` is; it will not guess. Point `origin` at the GitHub "
            "remote, or run it from a checkout that does."
        )
    return slug


def check_remote_is_the_release_repo(repo: Path, slug: str) -> None:
    """The push target and the `gh` target must be the same repository.

    Everything irreversible is split between the two: `git push origin ...`
    moves `main` and pushes the tag, `gh release create --repo <slug>` cuts
    the release. The guard is about them AGREEING, not about which repo it is
    -- a fork releasing itself is legitimate, and after the org move so is
    `pyrite-wiki/pyrite`. What must never happen is pushing to one repo and
    cutting the release on another.

    Since both now derive from the same `origin`, this re-reads it rather than
    trusting the value it was handed: the resolve happens once at startup and
    a release is long enough for a remote to be re-pointed under it.
    """
    url = _check_output(["git", "-C", str(repo), "remote", "get-url", "origin"]).strip()
    current = remote_slug(url)
    if current != slug:
        raise ReleaseError(
            f"`origin` now resolves to {current or 'unrecognised'} ({url!r}), "
            f"but this run is releasing {slug}: it would push main and the tag "
            "to one repo and cut the release on the other. Nothing was "
            "changed; re-run from a settled checkout."
        )


def check_tag_is_free(repo: Path, version: str, slug: str) -> None:
    """`v<version>` exists nowhere yet -- locally, on the remote, or as a release.

    Step d pushes `main` BEFORE it tags. Without this, a tag that already
    exists is discovered after `main` has already moved, which is the one
    failure mode the ordering was supposed to make impossible.
    """
    tag = f"v{version}"
    local = _check_output(["git", "-C", str(repo), "tag", "-l", tag]).strip()
    if local:
        raise ReleaseError(
            f"the tag {tag} already exists locally. Either this release is "
            f"already cut, or a stale local tag needs removing by hand "
            f"(`git tag -d {tag}`) -- this script never deletes refs."
        )
    remote = _check_output(
        ["git", "-C", str(repo), "ls-remote", "--tags", "origin", f"refs/tags/{tag}"]
    ).strip()
    if remote:
        raise ReleaseError(
            f"the tag {tag} already exists on origin:\n{remote}\n"
            "That release is cut. Release the next version instead."
        )
    try:
        _check_output(["gh", "release", "view", tag, "--repo", slug, "--json", "tagName"])
    except ReleaseError:
        pass  # `gh release view` exits non-zero when there is no such release
    else:
        raise ReleaseError(
            f"a GitHub release for {tag} already exists on {slug}. "
            "Release the next version instead."
        )


def check_main_can_fast_forward(repo: Path, sha: str) -> None:
    """`origin/main` must be an ancestor of the commit being released.

    The ruleset on `main` refuses a non-fast-forward push, so divergence would
    otherwise surface as a rejected push in the middle of step d. It is a
    precondition: divergence means `main` has commits `dev` lacks (a hotfix
    never merged back), and that needs a person, not a retry.
    """
    _check_output(["git", "-C", str(repo), "fetch", "origin", "main", "--tags"])
    try:
        _check_output(["git", "-C", str(repo), "merge-base", "--is-ancestor", "origin/main", sha])
    except ReleaseError as exc:
        raise ReleaseError(
            f"origin/main is not an ancestor of {sha[:12]}: the fast-forward in "
            "step d would be refused by the ruleset. main has commits dev lacks "
            "-- a hotfix that was never merged back? Find out why before "
            f"releasing (`git log --oneline {sha}..origin/main`).\n{exc}"
        ) from exc


def check_clean_checkout(repo: Path) -> str:
    """Clean `dev`, exactly at `origin/dev`. Returns the SHA to release."""
    dirty = git_status_porcelain(repo)
    if dirty:
        raise ReleaseError(
            "the checkout is dirty -- uncommitted changes would not be in the "
            f"released commit:\n{dirty}"
        )
    branch = current_branch(repo)
    if branch != "dev":
        raise ReleaseError(
            f"on branch {branch!r}; a release is cut from dev. "
            "`git checkout dev && git pull --ff-only` first."
        )
    head = rev_parse(repo, "HEAD")
    upstream = rev_parse(repo, "origin/dev")
    if head != upstream:
        raise ReleaseError(
            f"HEAD ({head[:12]}) is not origin/dev ({upstream[:12]}). "
            "Only a commit CI has already seen may be released: "
            "`git fetch origin && git pull --ff-only`."
        )
    return head


# ---------------------------------------------------------------------------
# the five steps
# ---------------------------------------------------------------------------


@dataclass
class Context:
    repo: Path
    version: str
    runner: Runner
    wait_ci_minutes: int
    required_checks: tuple[str, ...] = DEFAULT_REQUIRED_CHECKS
    skip_install_check: bool = False
    rehearse_install_check: bool = False
    docker_check: bool = False
    sha: str = ""
    notes: str = ""
    # `owner/name` from `origin`, resolved once at startup. Every `gh --repo`
    # call and the install spec read this, so the script releases the repo the
    # checkout actually points at rather than a literal that goes stale the
    # day the project moves (#259).
    slug: str = ""


@dataclass
class Step:
    key: str
    name: str
    irreversible: bool
    run: Callable[[Context], None]
    # A check can still stop the release. Everything after the tag exists
    # cannot, whatever it finds, so it is not one -- and the ordering
    # invariant (no irreversible step before a check) is asserted over these.
    is_check: bool = True


def step_preconditions(ctx: Context) -> None:
    ctx.sha = check_clean_checkout(ctx.repo)
    ctx.runner.note(f"dev is clean at origin/dev: {ctx.sha}")

    check_version_matches(ctx.repo, ctx.version)
    ctx.runner.note(f"pyproject.toml version == {ctx.version}")

    check_changelog(ctx.repo, ctx.version)
    ctx.runner.note(f"CHANGELOG has `## [{ctx.version}] - {date.today().isoformat()}` with content")

    # Print the notes here rather than in step (d): by step (d) the next thing
    # to happen is the push, and "the release notes are wrong" is a reason to
    # stop. Fragments make this the only place the assembled section can be
    # read before the tag -- CHANGELOG.md does not contain it yet.
    fragments = collect_fragments(ctx.repo)
    ctx.runner.note(
        f"{len(fragments)} changelog fragment(s) under {FRAGMENT_DIR_NAME}/"
        if fragments
        else f"no fragments under {FRAGMENT_DIR_NAME}/; the notes are the section as written"
    )
    ctx.runner.note(f"release notes for v{ctx.version} (the section plus the fragments):")
    print()
    for line in release_notes_for(ctx.repo, ctx.version).rstrip().splitlines():
        print(f"      {line}" if line else "")
    print()

    # Resolve the repository here, inside the step, so a bad `origin` reads as
    # `FAIL at preconditions` with the header above it rather than as a raw
    # git error printed before the run has announced itself.
    ctx.slug = resolve_repo_slug(ctx.repo)
    check_remote_is_the_release_repo(ctx.repo, ctx.slug)
    ctx.runner.note(f"origin is {ctx.slug}, the repo this releases")

    check_tag_is_free(ctx.repo, ctx.version, ctx.slug)
    ctx.runner.note(f"v{ctx.version} exists neither locally, on origin, nor as a release")

    check_main_can_fast_forward(ctx.repo, ctx.sha)
    ctx.runner.note(f"origin/main is an ancestor of {ctx.sha[:12]}: step d can fast-forward")

    # `--repo` on every gh call: it otherwise infers the repo from the cwd,
    # which is not necessarily the repo being released.
    labels = _gh_json(
        ["gh", "label", "list", "--repo", ctx.slug, "--json", "name", "--limit", "200"]
    )
    known = {entry.get("name") for entry in labels} if isinstance(labels, list) else set()
    if BLOCKER_LABEL not in known:
        # Hard stop, not a note. Skipping the blocker query when the label is
        # missing makes the precondition inert exactly when it has never been
        # set up -- the first release, which is the one that most needs it.
        # This script does not create labels; creating one silently would let a
        # release invent its own permission to proceed.
        raise ReleaseError(
            f"the {BLOCKER_LABEL!r} label does not exist on {ctx.slug}, so the "
            "blocker check cannot be evaluated and the release will not guess. "
            "It is a one-time prerequisite -- create it and run this again:\n"
            f"    gh label create {BLOCKER_LABEL} --repo {ctx.slug} "
            "--description 'Must not ship in the next release' --color B60205"
        )
    blockers = _gh_json(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            ctx.slug,
            "--state",
            "open",
            "--label",
            BLOCKER_LABEL,
            "--json",
            "number,title",
        ]
    )
    check_no_release_blockers(blockers if isinstance(blockers, list) else [])
    ctx.runner.note(f"no open PRs labelled {BLOCKER_LABEL}")


def _checks_for(sha: str, slug: str) -> list[dict]:
    """The check runs GitHub reports for a commit, by name.

    Check runs, not workflow runs: what a release waits on is the named check
    `gate`, which is a job inside ci.yml, and `gh run list` only knows about
    the workflow as a whole.
    """
    try:
        payload = _gh_json(["gh", "api", f"repos/{slug}/commits/{sha}/check-runs"])
    except ReleaseError as exc:
        # The likeliest real failure is a commit that was never pushed, and the
        # API answers 422 "No commit found". That is exactly CI_MISSING -- the
        # caller's advice ("push it to dev and let CI run") is what you need.
        # Anything else (auth, network) is a genuine error and must surface.
        if "No commit found" in str(exc):
            return []
        raise
    if isinstance(payload, dict):
        runs = payload.get("check_runs", [])
        return runs if isinstance(runs, list) else []
    return []


def _describe(checks: list[dict], required: tuple[str, ...]) -> str:
    relevant = [c for c in checks if any(_matches(c.get("name", ""), r) for r in required)]
    return (
        ", ".join(f"{c.get('name')}={c.get('conclusion') or c.get('status')}" for c in relevant)
        or "none"
    )


def step_ci(ctx: Context) -> None:
    required = tuple(ctx.required_checks)
    deadline = time.monotonic() + ctx.wait_ci_minutes * 60
    while True:
        checks = _checks_for(ctx.sha, ctx.slug)
        verdict = ci_decision(checks, required=required)
        if verdict == CI_PASSED:
            ctx.runner.note(
                f"required check(s) green on {ctx.sha[:12]}: {_describe(checks, required)}"
            )
            other = [
                c.get("name")
                for c in checks
                if not any(_matches(c.get("name", ""), r) for r in required)
                and c.get("conclusion") not in ("success", "skipped", None)
            ]
            if other:
                ctx.runner.note(
                    f"(advisory, not blocking: {', '.join(other)} -- not in "
                    "--require-check, so it does not hold the release)"
                )
            return
        if verdict == CI_FAILED:
            raise ReleaseError(
                f"a required check is not green on {ctx.sha[:12]}: "
                f"{_describe(checks, required)}. Fix it on dev; the tagged "
                "commit must be one CI passed."
            )
        if verdict == CI_MISSING:
            raise ReleaseError(
                f"no run of {', '.join(required)} exists for {ctx.sha[:12]}. Push the "
                "commit to dev and let CI run -- this script never starts one, because "
                "a run it started is not the run the merge gate saw."
            )
        # pending
        now = time.monotonic()
        if now >= deadline:
            raise ReleaseError(
                f"a required check is still running on {ctx.sha[:12]} after "
                f"{ctx.wait_ci_minutes} min: {_describe(checks, required)}. "
                "Re-run with a longer --wait-ci, or wait and try again."
            )
        # Never sleep past the deadline: a fixed 30s against 5s remaining
        # wastes 25s and reports the timeout later than it was actually reached.
        remaining = deadline - now
        nap = min(30.0, remaining)
        ctx.runner.note(f"required check(s) still running on {ctx.sha[:12]}; waiting {nap:.0f}s")
        time.sleep(nap)


def step_release_layer(ctx: Context) -> None:
    """What a user actually gets, verified before the tag exists (ADR-0032 §3a).

    Installs from the SHA -- not the tag, which does not exist yet -- into a
    throwaway venv, checks `pyrite --version`, and runs the getting-started
    tutorial against that install.

    Nothing here is irreversible: it writes only to a temp directory. It is
    still minutes of network and CPU, so a dry run prints the commands instead
    of running them; `--install-check` rehearses it for real without
    `--execute`, which is how you test this step before release day.
    """
    if ctx.skip_install_check:
        ctx.runner.note("SKIPPED (--skip-install-check): nothing verified about the install")
        return

    if not (ctx.runner.execute or ctx.rehearse_install_check):
        spec = f"pyrite[{INSTALL_CHECK_EXTRAS}] @ git+https://github.com/{ctx.slug}@{ctx.sha}"
        print("    WOULD RUN: uv venv <tmp>")
        print(f'    WOULD RUN: uv pip install --python <tmp>/bin/python "{spec}"')
        print(f"    WOULD RUN: <tmp>/bin/pyrite --version    (must contain {ctx.version})")
        print("    WOULD RUN: PYRITE_TUTORIAL_VENV=<tmp> scripts/run_tutorial.sh")
        print(
            f"    WOULD RUN: PYRITE_TUTORIAL_VENV=<tmp> scripts/run_tutorial.sh {TUTORIAL_20_MIN}"
        )
        if ctx.docker_check:
            print(f"    WOULD RUN: docker build -t pyrite:{ctx.version} .")
        else:
            print("    (docker build skipped; pass --docker-check to build it)")
        ctx.runner.note(
            "pass --install-check to actually run this step in a dry run "
            "(minutes: a real install from GitHub plus the tutorial)"
        )
        return

    if not shutil.which("uv"):
        raise ReleaseError(
            "uv is not on PATH; the install check needs it "
            "(https://docs.astral.sh/uv/). Install it, or pass "
            "--skip-install-check and do the runbook's clean-venv check by hand."
        )

    venv = Path(tempfile.mkdtemp(prefix="pyrite-release-venv-"))
    spec = f"pyrite[{INSTALL_CHECK_EXTRAS}] @ git+https://github.com/{ctx.slug}@{ctx.sha}"
    try:
        ctx.runner.note(f"temp venv: {venv}")
        print(f"    RUN: uv venv {venv}")
        _check_output(["uv", "venv", str(venv)])
        print(f'    RUN: uv pip install --python {venv}/bin/python "{spec}"')
        _check_output(["uv", "pip", "install", "--python", str(venv / "bin" / "python"), spec])

        reported = _check_output([str(venv / "bin" / "pyrite"), "--version"]).strip()
        if ctx.version not in reported:
            raise ReleaseError(
                f"the install from {ctx.sha[:12]} reports {reported!r}, which does "
                f"not contain {ctx.version}. A user installing the tag would get "
                "the wrong version."
            )
        ctx.runner.note(f"`pyrite --version` from the install: {reported}")

        env = dict(os.environ)
        env["PYRITE_TUTORIAL_VENV"] = str(venv)
        print(
            f"    RUN: PYRITE_TUTORIAL_VENV={venv} scripts/run_tutorial.sh"
            "   (docs/getting-started.md against the install)"
        )
        _check_output([str(ctx.repo / "scripts" / "run_tutorial.sh")], cwd=ctx.repo, env=env)
        ctx.runner.note("getting-started tutorial ran clean against the install")
        print(
            f"    RUN: PYRITE_TUTORIAL_VENV={venv} scripts/run_tutorial.sh {TUTORIAL_20_MIN}"
            "   (the 20-minute tutorial against the install)"
        )
        _check_output(
            [str(ctx.repo / "scripts" / "run_tutorial.sh"), TUTORIAL_20_MIN],
            cwd=ctx.repo,
            env=env,
        )
        ctx.runner.note("Pyrite-in-20-minutes tutorial ran clean against the install")
    finally:
        shutil.rmtree(venv, ignore_errors=True)

    if not ctx.docker_check:
        ctx.runner.note(
            "docker build not run (pass --docker-check to build it). Nothing "
            "publishes the image: neither CI nor this script pushes to a "
            "registry, so the build verified an artifact that never left the "
            "machine -- while being able to fail a release, which it did twice "
            "on 0.24.3."
        )
        return

    dockerfile = ctx.repo / "Dockerfile"
    if not shutil.which("docker"):
        ctx.runner.note(
            "!! DOCKER NOT VERIFIED: `docker` is not on PATH, so the image in "
            "the release notes is unproven on this machine. Say so in the notes, "
            "or run the build somewhere with docker."
        )
    elif not dockerfile.exists():
        ctx.runner.note(f"!! DOCKER NOT VERIFIED: no Dockerfile at {dockerfile}")
    else:
        print(f"    RUN: docker build -t pyrite:{ctx.version} .")
        _check_output(["docker", "build", "-t", f"pyrite:{ctx.version}", "."], cwd=ctx.repo)
        ctx.runner.note(f"docker image pyrite:{ctx.version} built")


def _contributor_logins(since_tag: str | None, slug: str) -> list[str]:
    if not since_tag:
        return []
    merged_at = _check_output(
        [
            "gh",
            "api",
            f"repos/{slug}/releases/tags/{since_tag}",
            "--jq",
            ".published_at",
        ]
    ).strip()
    if not merged_at:
        return []
    prs = _gh_json(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            slug,
            "--state",
            "merged",
            "--base",
            "dev",
            "--search",
            # Keep the time component. `merged:>2026-09-18` means "after that
            # day ENDS", so truncating a published_at of 09-18T09:22Z to a bare
            # date silently drops every PR merged earlier that same day -- two
            # of them, when 0.24.3 was cut.
            f"merged:>{merged_at}",
            "--limit",
            "200",
            "--json",
            "author",
        ]
    )
    if not isinstance(prs, list):
        return []
    logins = [pr.get("author", {}).get("login", "") for pr in prs]
    return logins + _co_author_logins_since(since_tag)


def _co_author_logins_since(since_tag: str) -> list[str]:
    """Co-authors named in the commits this release ships.

    A PR author is not the only person whose work is in a release (#248). This
    reads `Co-authored-by:` trailers over `<since_tag>..HEAD` -- the form
    CONTRIBUTING asks for, and the only one that survives the merge queue's
    squash. Failure here must not block a release: a missing credit is a thing
    a human can add to the notes, and `git log` failing is not a reason to stop
    shipping.
    """
    try:
        out = _check_output(
            [
                "git",
                "log",
                f"{since_tag}..HEAD",
                "--format=%(trailers:key=Co-authored-by,valueonly)",
            ]
        )
    except ReleaseError:
        return []
    return co_author_logins([line for line in out.splitlines() if line.strip()])


def _tag_is_released(repo: Path, tag: str) -> bool:
    """Did `main` actually move to this tag?

    Not "does a GitHub release exist": the accidental `v0.24.2` had one, empty
    and unnamed. What makes a tag a release in this project is step (d) --
    `main` fast-forwards to the commit. A tag `main` never reached names a
    commit nobody was ever shipped.
    """
    try:
        sha = _check_output(["git", "-C", str(repo), "rev-list", "-n1", tag]).strip()
        if not sha:
            return False
        _check_output(["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, "origin/main"])
        return True
    except ReleaseError:
        return False


def _previous_tag(repo: Path, exclude: str | None = None) -> str | None:
    """The newest tag `main` actually moved to, for the contributor window.

    `git describe --tags --abbrev=0` returns the newest *tag*, which is not
    the same thing. Cutting 0.24.3 it returned `v0.24.2` -- a tag pointing at
    a mid-development commit whose own pyproject said 0.24.1, published with
    an empty release by accident. An accidental tag is not a release
    boundary, and anchoring the window to one moves it by an arbitrary amount.
    """
    try:
        tags = _check_output(
            ["git", "-C", str(repo), "tag", "--list", "v*", "--sort=-v:refname"]
        ).splitlines()
    except ReleaseError:
        return None
    for tag in (t.strip() for t in tags):
        if not tag or tag == exclude:
            continue
        if _tag_is_released(repo, tag):
            return tag
    return None


def step_publish(ctx: Context) -> None:
    """IRREVERSIBLE. Fast-forward main, tag, push the tag, cut the release.

    `git push origin <sha>:refs/heads/main` is a fast-forward-only push: the
    ruleset on main refuses anything else, which is the safety net. No force,
    no lease, no delete.
    """
    tag = f"v{ctx.version}"
    logins = _contributor_logins(_previous_tag(ctx.repo, exclude=tag), ctx.slug)
    ctx.notes = compose_notes(ctx.repo, ctx.version, logins)
    line = contributors_line(logins)
    ctx.runner.note(
        "release notes: the CHANGELOG section and the assembled fragments (printed in step a)"
        + (f" + {line}" if line else " (no outside contributors this cycle)")
    )
    unresolved = unresolved_contributors(logins)
    if unresolved:
        ctx.runner.note(
            "NOT credited (co-author with no GitHub login; emails are never published): "
            + ", ".join(unresolved)
            + " -- credit by hand if they are not already named above"
        )

    ctx.runner.run_write(
        ["git", "-C", str(ctx.repo), "push", "origin", f"{ctx.sha}:refs/heads/main"]
    )
    ctx.runner.run_write(
        [
            "git",
            "-C",
            str(ctx.repo),
            "tag",
            "-a",
            tag,
            "-m",
            f"{tag}: see CHANGELOG.md",
            ctx.sha,
        ]
    )
    ctx.runner.run_write(["git", "-C", str(ctx.repo), "push", "origin", tag])

    # A context-managed temp dir, and written only under --execute: a dry run
    # must change nothing on disk, and the old code wrote a notes file into the
    # system temp dir outside the Runner choke point and never removed it.
    with tempfile.TemporaryDirectory(prefix="pyrite-release-notes-") as tmpdir:
        notes_file = Path(tmpdir) / "notes.md"
        if ctx.runner.execute:
            notes_file.write_text(ctx.notes)
            ctx.runner.note(f"(notes written to {notes_file})")
        else:
            ctx.runner.note(f"(would write the notes to {notes_file})")
        # --repo explicitly: `gh` otherwise infers it from the cwd, which is not
        # necessarily the repo being released, and a release cut against the wrong
        # repo is not undoable.
        ctx.runner.run_write(
            [
                "gh",
                "release",
                "create",
                tag,
                "--repo",
                ctx.slug,
                "--title",
                tag,
                "--notes-file",
                str(notes_file),
            ]
        )


def _inline_fragments(text: str, version: str, assembled: str) -> str:
    """The CHANGELOG with the assembled fragments written under `## [version]`.

    The fragments are the release's entries: once the tag carries them in its
    notes, the file has to carry them too, or CHANGELOG.md documents every
    release except the ones cut since fragments existed. They go under the
    *released* heading, not under the reopened `[Unreleased]` -- they shipped.
    """
    heading, end = _section_span(text, version)
    written = text[heading.end() : end].strip()
    body = "\n\n".join(part for part in (written, assembled.strip()) if part)
    return text[: heading.end()] + "\n\n" + body + "\n\n" + text[end:]


def step_post_release(ctx: Context) -> None:
    """IRREVERSIBLE (a commit, on a fresh branch). Consume the fragments that
    shipped, and reopen `[Unreleased]`.

    Two edits to one file, so one commit:

    * The fragments assembled into the released section. Leaving them under
      `changelog.d/` would republish every entry in the *next* release, and
      leaving CHANGELOG.md without them would mean the file documents every
      release except this one.
    * A fresh `## [Unreleased]` heading, when the file does not already carry
      one -- a second empty one would only be noise.

    A NO-OP when there is neither work to do: nothing to consume and the
    heading already there.

    The commit goes on `release/reopen-unreleased-<version>`, cut from the
    released commit, never on local `dev`. `dev` takes pull requests only (the
    ruleset refuses a direct push), so a commit on the local branch could not
    be pushed anyway -- and it would leave the checkout ahead of `origin/dev`,
    which is exactly the state step a refuses on the next release.

    pyproject.toml is deliberately NOT bumped to a `.dev0`: `pyrite.__version__`
    reads it and `tests/test_version_consistency.py` pins it, so dev between
    releases reports the last released version -- the runbook's long-standing
    behaviour. The next release's bump is part of its own release commit.
    """
    changelog = ctx.repo / "CHANGELOG.md"
    text = _read_changelog(ctx.repo)

    consumed = fragment_paths(ctx.repo)
    assembled = assemble_fragments(ctx.repo)
    has_unreleased = bool(re.search(r"^##\s*\[Unreleased\]", mask_fenced_blocks(text), re.M))

    if not consumed and has_unreleased:
        ctx.runner.note(
            "CHANGELOG already has an `[Unreleased]` section and there are no "
            "fragments to consume; nothing to do"
        )
        return

    updated = text
    if assembled:
        updated = _inline_fragments(updated, ctx.version, assembled)
    if not has_unreleased:
        marker = re.search(
            rf"^##\s*\[{re.escape(ctx.version)}\]", mask_fenced_blocks(updated), re.M
        )
        if not marker:
            raise ReleaseError("cannot reopen [Unreleased]: the released section vanished")
        updated = updated[: marker.start()] + "## [Unreleased]\n\n" + updated[marker.start() :]

    branch = f"release/reopen-unreleased-{ctx.version}"
    ctx.runner.run_write(["git", "-C", str(ctx.repo), "checkout", "-b", branch, ctx.sha])

    described = []
    if assembled:
        described.append(f"{len(consumed)} fragment(s) assembled into `## [{ctx.version}]`")
    if not has_unreleased:
        described.append("a fresh `## [Unreleased]`")
    what = " and ".join(described)
    if ctx.runner.execute:
        changelog.write_text(updated)
        print(f"    RUN: write CHANGELOG.md with {what}")
    else:
        print(f"    WOULD RUN: write CHANGELOG.md with {what}")

    # Removing the files is a write like writing the CHANGELOG above, and is
    # printed the same way: a dry run removes nothing. Plain `unlink` rather
    # than `git rm`, because the commit below names `changelog.d/` in its
    # pathspec and `git commit -- <path>` stages the deletions it finds there
    # -- while `git rm` would fail outright on a fragment that was never added
    # to the index, in the middle of an irreversible step.
    for path in consumed:
        rendered = f"remove {FRAGMENT_DIR_NAME}/{path.name} (assembled above)"
        if ctx.runner.execute:
            path.unlink(missing_ok=True)
            print(f"    RUN: {rendered}")
        else:
            print(f"    WOULD RUN: {rendered}")

    paths = ["CHANGELOG.md"]
    if consumed:
        paths.append(f"{FRAGMENT_DIR_NAME}/")
    ctx.runner.run_write(
        [
            "git",
            "-C",
            str(ctx.repo),
            "commit",
            "-m",
            f"chore: consume changelog fragments and open [Unreleased] after v{ctx.version}",
            "--",
            *paths,
        ]
    )
    ctx.runner.note(
        f"local dev is untouched; the commit is on {branch}. Open it as a PR like any other change:"
    )
    ctx.runner.note(f"  git push -u origin {branch}")
    ctx.runner.note("  gh pr create --base dev --fill")


def step_handoff(ctx: Context) -> None:
    """What the release does NOT do, said out loud while it is still in mind.

    Changes nothing and can stop nothing -- by the time it runs, the tag
    exists. It is here because the steps this script cannot automate are the
    ones that get forgotten, and a release is not finished when the tag is
    pushed.

    pyrite.wiki is the case that motivated it: the marketing site lives
    outside this repo, and it carries version-specific claims (the current
    version, tool counts, test counts) that go stale silently the moment a
    release lands. Nothing in this path can update it.
    """
    tag = f"v{ctx.version}"
    if ctx.runner.execute:
        print("    The tag is cut. These are yours -- nothing here is automated:")
    else:
        print("    After --execute, these would be yours -- nothing here is automated:")
    print()
    print(f"    1. pyrite.wiki -- the site lives OUTSIDE this repo. Update it for {tag}:")
    print("         - the version it names, and any install command pinned to a tag")
    print("         - the counts it quotes (MCP tools, tests, ADRs); they drift every")
    print("           release and the top GitHub referrer is chatgpt.com, so these are")
    print("           what gets quoted to prospective users")
    print(f"         - whatever {tag} added that the site's feature list should say")
    print()
    print("    2. Deploys -- the tag does not deploy itself (site mapping, runbook):")
    print(f"         ./pyrite_deployments/deploy.sh ink {tag}")
    print("         ./pyrite_deployments/deploy.sh cascade        # --reseed if KB data changed")
    print("       (demo.pyrite.wiki follows dev HEAD automatically; nothing to do)")
    print()
    print(
        f"    3. The [Unreleased] commit from step e is on "
        f"release/reopen-unreleased-{ctx.version} and still needs a PR to dev."
    )
    print()
    print(f"    4. Announce {tag} wherever the release is announced.")


STEPS: list[Step] = [
    Step("preconditions", "a. preconditions", False, step_preconditions),
    Step("ci", "b. CI is green on this SHA", False, step_ci),
    Step("release_layer", "c. release layer: install, tutorial, docker", False, step_release_layer),
    Step("publish", "d. main, tag, GitHub release", True, step_publish, is_check=False),
    Step("post_release", "e. reopen [Unreleased]", True, step_post_release, is_check=False),
    Step(
        "handoff",
        "f. what is still yours to do",
        False,
        step_handoff,
        is_check=False,
    ),
]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripts/release.py",
        description="Cut a Pyrite release from a CI-verified dev commit. "
        "Dry run by default: nothing is written without --execute.",
    )
    parser.add_argument("version", help="the version to release, e.g. 0.24.2 (no 'v')")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--execute",
        action="store_true",
        help="actually do it. Without this, every irreversible command is printed only.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="the default; accepted so you can say it out loud.",
    )
    parser.add_argument(
        "--require-check",
        action="append",
        metavar="NAME",
        help="a check that must be green before the tag, by name. Repeatable. "
        f"Default: {', '.join(DEFAULT_REQUIRED_CHECKS)}. Checks NOT named here are "
        "advisory and never block the release -- that is deliberate for the breadth "
        "jobs that run after the merge gate.",
    )
    parser.add_argument(
        "--wait-ci",
        type=int,
        default=DEFAULT_WAIT_CI_MINUTES,
        metavar="MINUTES",
        help=f"wait up to this many minutes for a pending required check "
        f"(default: {DEFAULT_WAIT_CI_MINUTES}; 0 to fail immediately). The "
        "release never starts a run -- it only waits for the one the merge gate saw.",
    )
    parser.add_argument(
        "--skip-install-check",
        action="store_true",
        help="skip step c (the temp-venv install and tutorial run) entirely. Only "
        "when you have done the runbook's clean-venv check by hand.",
    )
    parser.add_argument(
        "--docker-check",
        action="store_true",
        help="also build the Docker image in step c. Off by default: nothing "
        "publishes the image, so the build gates a release on an artifact that "
        "is never shipped. Turn it back on when images are published.",
    )
    parser.add_argument(
        "--install-check",
        action="store_true",
        help="in a dry run, actually perform step c instead of printing it. Takes "
        "minutes (a real install from GitHub plus the tutorial); harmless.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=REPO,
        help=argparse.SUPPRESS,  # tests only
    )
    args = parser.parse_args(argv)
    # `action="append"` starts at None rather than the default, and giving it a
    # list default would append to it instead of replacing it.
    if not args.require_check:
        args.require_check = list(DEFAULT_REQUIRED_CHECKS)
    return args


def run_release(args: argparse.Namespace) -> tuple[int, Runner]:
    """Walk the steps. Returns the exit code and the Runner, whose `planned`
    list is every write the run printed or performed -- what the safety tests
    inspect."""
    runner = Runner(execute=args.execute)

    try:
        version = normalize_version(args.version)
    except ReleaseError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1, runner

    ctx = Context(
        repo=args.repo,
        version=version,
        runner=runner,
        wait_ci_minutes=args.wait_ci,
        required_checks=tuple(args.require_check),
        skip_install_check=args.skip_install_check,
        rehearse_install_check=args.install_check,
        docker_check=args.docker_check,
    )

    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"pyrite release {version} [{mode}]")
    if not args.execute:
        print(
            "  Dry run: every irreversible command is printed, not run. "
            "Re-run with --execute to cut the release."
        )
    print()

    for step in STEPS:
        marker = " (IRREVERSIBLE)" if step.irreversible else ""
        print(f"  {step.name}{marker}")
        try:
            step.run(ctx)
        except ReleaseError as exc:
            # Piped, stdout block-buffers and stderr does not: without this the
            # failure prints above the header and the step it failed in, which
            # is how it reads in a log or a CI transcript.
            sys.stdout.flush()
            print(f"\nFAIL at {step.key}: {exc}", file=sys.stderr)
            if runner.performed:
                # `main` may already have moved. Saying "nothing further was
                # attempted" would be true and useless: what the maintainer
                # needs is which irreversible commands already succeeded, so
                # they know what state the repo is in before they retry.
                print(
                    "\nThese commands ALREADY RAN and their effects stand:",
                    file=sys.stderr,
                )
                for cmd in runner.performed:
                    print(f"    {shlex.join(cmd)}", file=sys.stderr)
                print(
                    "Nothing after them was attempted. Reconcile that state before re-running.",
                    file=sys.stderr,
                )
            else:
                print("Nothing further was attempted.", file=sys.stderr)
            sys.stderr.flush()
            return 1, runner
        print()

    if args.execute:
        print(f"Released v{version}. Deploy with pyrite_deployments/deploy.sh.")
    else:
        print(f"Dry run complete: v{version} looks releasable. Re-run with --execute.")
    return 0, runner


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv) if argv is not None else sys.argv[1:])
    code, _runner = run_release(args)
    return code


if __name__ == "__main__":
    sys.exit(main())

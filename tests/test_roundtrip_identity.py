"""The round-trip identity gate (quality, retro 2).

Loading every entry in a KB and saving it back with no edit should change
nothing on disk. Before PR #69 this rewrote 768 of 768 real `kb/` files;
#69 brought that down, and this test measured the remainder directly: 52 of
770 files, split across two distinct causes (see `KNOWN_RESIDUAL_IDS` below --
each group is a separate, precisely-identified finding, not one blob). 8
remain, all trailing-blank-line normalization: the 46 bare-string `links:`
files round-trip since #557. Two more groups are gone: the block-indented `links:` set
(11 ids, issue #148 -- `pyrite/utils/yaml.py` now reads a source document's own
sequence indentation out of the parsed tree's line/column records and hands the
emitter those numbers, so a no-op save leaves those files byte-identical) and
the `body:` fold set (6 ids, issue #150 -- those files were re-saved through
the repository, which drops the surviving `body:`/`file_path:` keys).

Every write-path bug in the run-up to this test -- #46 (an incidental
`--tags` update rewriting the whole file), #86 (`pyrite create` leaking
`importance`/`rank` into files that never had them), #87 (`link`/`update`
folding the body into a `body:` frontmatter scalar) -- was a special case of
"save wrote something load did not read". This test makes that class of bug
fail the suite instead of waiting for a human to notice a huge diff in
review.

Design under test (see `pyrite/models/base.py`, #69's final design, read
before touching this file):
  - `Entry.__setattr__` tracks `_absent_default_keys` -- a default-valued key
    the source file lacked stays absent on save, UNLESS something assigns to
    the attribute (which clears it from the set). So this gate loads and
    saves WITHOUT touching any attribute in between: an incidental `setattr`
    in a helper would make the gate fail for a reason that has nothing to do
    with the write path.
  - `_pristine_frontmatter(cls)` is an `@cache`d per-class probe, so it is
    SHARED state across the whole walk of `kb/`. `test_pristine_probe_does_
    not_leak_between_entries` pins that two entries of the same type with
    different absent-key sets do not contaminate each other, mirroring the
    exact bug #69's last commit fixed.

Safety: this NEVER loads or saves inside the real `kb/`. Both the full-corpus
walk and the fixture walk copy their source into a `tmp_path` first and
operate only on the copy, so an agent running this file cannot corrupt the
checked-in KB no matter what the write path does.
"""

from __future__ import annotations

import difflib
import shutil
import time
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from pyrite.storage.repository import KBRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_KB_DIR = REPO_ROOT / "kb"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "roundtrip"

# The exact, fully-classified set of real-kb/ ids that currently fail a
# no-op load -> save, split by root cause. Each group traces to a specific
# finding from this theme's investigation (quality/roundtrip-identity-gate,
# PR #146) -- NOT a blanket "known flaky" bucket. Every id here MUST be
# tracked by a GitHub issue (or, for the residual named in #146's own spec,
# fixed or explicitly deferred there) so the set only shrinks on purpose.
#
# 1. FIXED (#557): BARE-STRING LINKS. A bare YAML string under `links:`
#    parses into `Link(target=..., relation="related")`, and `Link.to_dict()`
#    always emits a mapping, so a no-op save rewrote 46 files' `links:`
#    shorthand into dicts. #557 made the write path keep any key the model
#    cannot reproduce as the file had it, until the field itself changes
#    (`capture_extra_frontmatter` records it; `_frontmatter_for_file` puts
#    the source node back), so this group is empty. Kept as a named set so
#    the count test still records which class of finding it was.
LINKS_BARE_STRING_IDS: frozenset[str] = frozenset()

# 3. FIXED (issue #149): `GenericEntry` (pyrite/models/generic.py, backs
#    `type: design`/`note`/any kb.yaml custom type) promoted undeclared
#    frontmatter keys to `self.metadata`, while `Entry._base_frontmatter()`
#    also re-serialized that whole mapping as a nested `metadata:` block --
#    the same keys twice. `GenericEntry.to_frontmatter` now nests only keys
#    that came from an explicit `metadata:` block and promotes the rest, so
#    this group is empty. Kept as a named set so the union's shape and the
#    count test still record which class of finding it was.
GENERIC_METADATA_DUP_IDS: frozenset[str] = frozenset()

# 5. Daily notes with more than one trailing blank line in their body.
#    `Entry.to_markdown` always writes exactly one trailing newline after
#    the body (a deliberate rule -- see that method's docstring; pre-commit
#    end-of-file hook requires it, and no file in kb/ lacks a FINAL newline,
#    only some have extra blank ones before it). This is idempotent
#    normalization, not a write-path bug: saving twice produces identical
#    bytes to saving once (see TestAdversarialFixtures.
#    test_trailing_newline_shapes_load_and_normalize_idempotently for the
#    equivalent one-shot case). Tracked here, not as an issue, because
#    there is nothing to fix -- to_markdown is doing exactly what its
#    docstring says.
TRAILING_BLANK_LINE_NORMALIZE_IDS: frozenset[str] = frozenset(
    {
        "daily-2026-02-24",
        "daily-2026-03-01",
        "daily-2026-03-02",
        "daily-2026-03-03",
        "daily-2026-03-04",
        "daily-2026-03-05",
        # These two also changed `links:` shape, which hid this cause until
        # #557 fixed that one.
        "epic-task-dag-queries-and-orchestrator-support",
        "epic-task-system-integration-with-qa-agent",
    }
)

# The union is what today's gate must xfail(strict=True) on, named exactly,
# so the set can only shrink on purpose (strict=True fails the test the
# moment any id here stops failing, forcing this list to be updated in the
# same commit as whatever fixed it).
KNOWN_RESIDUAL_IDS: frozenset[str] = (
    LINKS_BARE_STRING_IDS | GENERIC_METADATA_DUP_IDS | TRAILING_BLANK_LINE_NORMALIZE_IDS
)


def _copy_kb_to(tmp_path: Path, source: Path) -> Path:
    dest = tmp_path / "kb_copy"
    shutil.copytree(source, dest)
    return dest


def _repo_for(path: Path) -> KBRepository:
    return KBRepository(KBConfig(name="roundtrip-test", path=path, kb_type="generic"))


def _load_save_diffs(
    repo: KBRepository, *, skip_ids: frozenset[str] = frozenset()
) -> list[tuple[str, Path, str, str]]:
    """Load every entry through ``repo``, save it back untouched, and collect

    diffs. Returns a list of (entry_id, file_path, before_text, after_text)
    for every file whose bytes changed. Entries whose id is in ``skip_ids``
    are loaded and saved (so a regression there is still visible if someone
    widens the skip list) but never appear in the returned diff list -- they
    are asserted on separately, by the caller, as xfail.
    """
    diffs: list[tuple[str, Path, str, str]] = []
    for entry, file_path in repo.list_entries():
        before = file_path.read_text(encoding="utf-8")
        # No attribute assignment between load and save -- see module
        # docstring. `entry.save()` re-reads only what from_markdown already
        # set on the instance.
        entry.save(file_path)
        after = file_path.read_text(encoding="utf-8")
        if before != after and entry.id not in skip_ids:
            diffs.append((entry.id, file_path, before, after))
    return diffs


def _format_diffs(diffs: list[tuple[str, Path, str, str]], limit: int = 10) -> str:
    chunks = []
    for entry_id, file_path, before, after in diffs[:limit]:
        unified = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"{file_path.name} (before)",
                tofile=f"{file_path.name} (after)",
            )
        )
        chunks.append(f"--- entry {entry_id} ---\n{unified}")
    more = len(diffs) - limit
    header = f"{len(diffs)} file(s) changed on a no-op load->save"
    if more > 0:
        header += f" (showing first {limit}, {more} more not shown)"
    return header + "\n\n" + "\n".join(chunks)


@pytest.fixture(scope="module")
def real_kb_walk(tmp_path_factory):
    """Walk the real `kb/`, on a temp copy, load->save->compare -- ONCE per

    test session, in module scope. Every test below that needs the outcome
    of that walk (the identity assertion, the exact-set check, the 70
    per-id xfail cases) reads this fixture's result instead of re-copying
    and re-walking 770 files itself: at ~0.1s/file for load+parse+save,
    walking once and parametrizing 70 assertions over its already-computed
    result is the difference between this file running in a couple of
    seconds and running for minutes. Returns (elapsed_seconds, all_diffs)
    where all_diffs is the full, UNFILTERED diff list (skip_ids=frozenset())
    so every test below can apply its own filtering to the same data.
    """
    tmp_path = tmp_path_factory.mktemp("real_kb_walk")
    kb_copy = _copy_kb_to(tmp_path, REAL_KB_DIR)
    repo = _repo_for(kb_copy)

    start = time.monotonic()
    all_diffs = _load_save_diffs(repo, skip_ids=frozenset())
    elapsed = time.monotonic() - start

    return elapsed, all_diffs


class TestRealKBRoundTrip:
    """The gate itself: walk the real `kb/`, on a temp copy, load->save->compare."""

    def test_real_kb_is_byte_identical_after_noop_roundtrip(self, real_kb_walk):
        _elapsed, all_diffs = real_kb_walk
        diffs = [d for d in all_diffs if d[0] not in KNOWN_RESIDUAL_IDS]

        assert not diffs, _format_diffs(diffs)

    def test_walk_is_fast(self, real_kb_walk):
        """The default suite must stay fast: this file's real-corpus walk

        well under a minute. Measured directly (not asserted against a fixed
        file count) so it stays honest if the corpus grows. Shares the single
        walk in `real_kb_walk` with every other test in this class, so this
        measures the actual cost the default suite pays, not an inflated
        number from walking the corpus once per test.

        The budget is deliberately loose. The walk takes ~2s on an idle
        machine, but under `-n auto` next to two other full suites it took
        over 10s and a 10s budget failed on load alone (CLAUDE.md: a fixed
        wall-clock timeout that fails only under load is a bug in the test).
        What this guards against is the naive one-walk-per-case shape, which
        takes minutes; 60s catches that with room for a busy runner.
        """
        elapsed, _all_diffs = real_kb_walk

        assert elapsed < 60.0, (
            f"load->save walk of the real kb/ took {elapsed:.2f}s, over the "
            "60s budget for the default suite -- sample deterministically "
            "instead of walking the full corpus if this regresses"
        )

    def test_known_residual_is_exactly_this_set_no_more_no_less(self, real_kb_walk):
        """Not xfail -- a normal, passing assertion that the excluded set in

        `test_real_kb_is_byte_identical_after_noop_roundtrip` (via
        `KNOWN_RESIDUAL_IDS`) is exactly right: every id in
        `KNOWN_RESIDUAL_IDS` still fails a no-op round trip (so the skip is
        not hiding a stale entry that got fixed and forgotten), and nothing
        outside that set fails either (so a NEW regression cannot hide
        behind the skip list). Whichever direction breaks, this test fails
        with the two sets' difference, in the ordinary way -- no xfail
        involved, because there is nothing here that is expected to be
        broken; the brokenness lives in the 70 real files, and this test's
        job is to keep the list of them honest.

        The actual `xfail(strict=True)` cases per #146's acceptance
        criterion 4 are the per-id cases below
        (`test_known_residual_id_fails_roundtrip`) and the fixture-level
        ones in TestAdversarialFixtures. (`created_as_date.md` /
        `created_as_string.md` were pinned here as sentinels for #151's
        drop bug while it was open; with #151 fixed they are back in the
        blanket byte-identity assertion.)
        """
        _elapsed, all_diffs = real_kb_walk
        residual_ids = {entry_id for entry_id, *_ in all_diffs}

        missing_from_reality = KNOWN_RESIDUAL_IDS - residual_ids
        assert not missing_from_reality, (
            f"these ids no longer fail a no-op round trip: {sorted(missing_from_reality)} "
            "-- remove them from the relevant *_IDS set above (and close the "
            "matching issue, if any)"
        )
        new_or_unclassified = residual_ids - KNOWN_RESIDUAL_IDS
        assert not new_or_unclassified, (
            f"these ids fail a no-op round trip but are not in any known-residual "
            f"group: {sorted(new_or_unclassified)} -- this is a NEW regression, "
            "classify and fix it, or add it to the right *_IDS set with a reason "
            "and a GitHub issue"
        )

    @pytest.mark.control(
        reason="bookkeeping: counts this module's own residual sets, so it "
        "passes on any code; renamed from _is_52 when #557 emptied the links group"
    )
    def test_known_residual_count_is_8(self):
        """The count, recorded here per #146 acceptance criterion 4 ("the

        count in the test's docstring and in the report") as well as in the
        module docstring: 8 ids as of this branch, all trailing-blank-line
        normalization (two of them hidden behind the links group until it was
        fixed). The bare-string `links:` group is empty (fixed in
        #557), the `GenericEntry` metadata duplication group is empty (fixed
        in #149), and the block-indented `links:` group (fixed in #148) and
        the `body:` fold group (cleaned in #150) are gone.
        """
        assert len(LINKS_BARE_STRING_IDS) == 0
        assert len(GENERIC_METADATA_DUP_IDS) == 0
        assert len(TRAILING_BLANK_LINE_NORMALIZE_IDS) == 8
        assert len(KNOWN_RESIDUAL_IDS) == 8

    # #146 acceptance criterion 4: "xfail(strict=True) on exactly the failing
    # ids with the count in the test's docstring and in the report, so the
    # next tick can see it shrink". One parametrized xfail per id, at
    # per-file granularity (reading the SAME shared `real_kb_walk` result
    # rather than re-walking per id -- 70 separate full-corpus walks made
    # this file take minutes instead of seconds), so fixing group 3's single
    # id (search-failure-modes-and-agent-interface, issue #149) flips
    # exactly that one case to XPASS(strict), failing loudly and
    # specifically, rather than needing every id in the same group fixed
    # before the suite notices progress.
    @pytest.mark.parametrize("entry_id", sorted(KNOWN_RESIDUAL_IDS))
    @pytest.mark.xfail(strict=True, reason="see KNOWN_RESIDUAL_IDS's grouped comments above")
    def test_known_residual_id_fails_roundtrip(self, real_kb_walk, entry_id):
        _elapsed, all_diffs = real_kb_walk
        diff_ids = {d[0] for d in all_diffs}

        assert entry_id not in diff_ids, f"{entry_id} still fails its no-op round trip"


class TestAdversarialFixtures:
    """The smaller, hand-built adversarial shapes under tests/fixtures/roundtrip/.

    Each file is its own parametrized case rather than one walk, so a
    failure names the exact fixture and shape at fault instead of "some file
    in kb/ changed".
    """

    # Fixtures with their own dedicated test below, not the blanket identity
    # assertion:
    #   - bare_string_links.md used to reproduce the links residual (group 1
    #     above); since #557 it is part of the blanket byte-identity assertion.
    #   - anchors_and_merge_keys.md: no real kb/ file uses YAML anchors or
    #     merge keys (checked). Round-tripping a merge key through a
    #     dict-based from_frontmatter/to_frontmatter pipeline resolves it
    #     permanently -- inherent to that approach, not a Pyrite regression
    #     matching #46/#86/#87's pattern -- so it's asserted on its actual,
    #     documented (non-identical) behavior instead.
    #   - created_as_date.md / created_as_string.md: `created_at`/
    #     `updated_at` used to be read from frontmatter but never written
    #     back (issue #151) -- a real, separate bug this fixture set
    #     caught. The fix keeps them and preserves the source text, so
    #     both fixtures are now part of the blanket byte-identity
    #     assertion below rather than carrying dedicated tests.
    #
    # The "missing trailing newline" / "several trailing blank lines" cases
    # are NOT committed files under tests/fixtures/roundtrip/: this repo's
    # own pre-commit end-of-file-fixer hook normalizes exactly that shape on
    # every commit (it did, the first time these were committed as files --
    # see git history on this branch), which would silently defeat the two
    # adversarial fixtures meant to test it. They are built as bytes at test
    # time instead, in test_trailing_newline_shapes_load_and_normalize_
    # idempotently below.
    _DEDICATED_TEST_FIXTURES = frozenset(
        {
            "anchors_and_merge_keys.md",
        }
    )

    @pytest.mark.parametrize(
        "fixture_name",
        sorted(p.name for p in FIXTURES_DIR.glob("*.md")),
    )
    def test_fixture_is_byte_identical_after_noop_roundtrip(self, tmp_path, fixture_name):
        if fixture_name in self._DEDICATED_TEST_FIXTURES:
            pytest.skip("covered by its own dedicated test in this file, see the class docstring")

        src = FIXTURES_DIR / fixture_name
        dest = tmp_path / fixture_name
        shutil.copyfile(src, dest)
        repo = _repo_for(tmp_path)

        entry = repo.load_entry_from_file(dest)
        before = dest.read_text(encoding="utf-8")
        entry.save(dest)
        after = dest.read_text(encoding="utf-8")

        assert before == after, _format_diffs([(entry.id, dest, before, after)])

    _TRAILING_NEWLINE_FRONTMATTER = (
        "---\n"
        "id: trailing-newline-shape\n"
        "title: Trailing newline shape probe\n"
        "type: backlog_item\n"
        "tags:\n"
        "- roundtrip\n"
        "kind: feature\n"
        "priority: low\n"
        "effort: S\n"
        "status: planned\n"
        "---\n\n"
    )

    @pytest.mark.parametrize(
        "body_suffix",
        [
            pytest.param("Body text with no trailing newline.", id="no_trailing_newline"),
            pytest.param(
                "Body text followed by several blank lines.\n\n\n",
                id="many_trailing_blank_lines",
            ),
        ],
    )
    def test_trailing_newline_shapes_load_and_normalize_idempotently(self, tmp_path, body_suffix):
        """Neither shape is part of the byte-identity assertion, and neither

        is a committed fixture FILE: this repo's own pre-commit
        end-of-file-fixer hook normalizes exactly "missing trailing
        newline" / "extra trailing blank lines" on every commit, which would
        silently defeat a fixture meant to test that normalization. Built as
        bytes here instead, at test time.

        `Entry.to_markdown` always writes exactly one trailing newline after
        the body (see its docstring -- pre-commit's end-of-file hook
        requires this, and no file in the real `kb/` lacks a final newline).
        What the gate DOES assert: the load succeeds, and the normalization
        is idempotent -- saving twice produces the same bytes as saving
        once, in both directions (zero trailing newlines, or several
        trailing blank lines, both collapse to exactly one and stay there).
        """
        dest = tmp_path / "trailing_newline_shape.md"
        dest.write_text(self._TRAILING_NEWLINE_FRONTMATTER + body_suffix, encoding="utf-8")
        repo = _repo_for(tmp_path)

        entry = repo.load_entry_from_file(dest)
        entry.save(dest)
        once = dest.read_text(encoding="utf-8")

        entry2 = repo.load_entry_from_file(dest)
        entry2.save(dest)
        twice = dest.read_text(encoding="utf-8")

        assert once == twice
        assert once.endswith("\n") and not once.endswith("\n\n")

    def test_anchors_and_merge_keys_fixture_documents_the_limitation(self, tmp_path):
        """No real kb/ file uses a YAML anchor or merge key (checked directly

        against the corpus). Resolving `<<: *defaults` into its constituent
        keys on load, and not being able to re-collapse them into a merge
        key on save, is inherent to reading frontmatter into a plain dict of
        typed fields -- every loader in this codebase does that -- not a
        Pyrite write-path regression in the #46/#86/#87 family. This test
        documents the actual, current behavior so a future change to how
        frontmatter is parsed either preserves merge keys (test starts
        failing here, update it) or the limitation remains understood and
        deliberate.
        """
        src = FIXTURES_DIR / "anchors_and_merge_keys.md"
        dest = tmp_path / "anchors_and_merge_keys.md"
        shutil.copyfile(src, dest)
        repo = _repo_for(tmp_path)

        entry = repo.load_entry_from_file(dest)
        before = dest.read_text(encoding="utf-8")
        entry.save(dest)
        after = dest.read_text(encoding="utf-8")

        assert before != after
        assert "<<: *defaults" in before
        assert "<<:" not in after
        # The merged keys survive under their own names even though the
        # merge-key syntax itself does not.
        assert "kind: feature" in after
        assert "priority: low" in after


class TestPristineProbeIsolation:
    """Pins the exact bug #69's last commit fixed: `_pristine_frontmatter`

    is an `@cache`d-per-class probe, so it is SHARED across every entry of
    that class loaded during one walk. If the write path ever read or wrote
    through that shared dict instead of only comparing against it, one
    entry's absent-key decisions would leak into another entry of the same
    type.
    """

    def test_pristine_probe_does_not_leak_between_entries_of_same_type(self, tmp_path):
        # Two backlog_item entries, same type, different absent-key sets:
        # one has no `status:` at all, the other sets status explicitly.
        # Loading both and saving both must not let the first entry's
        # "status was absent" fact bleed into the second, or vice versa.
        no_status = tmp_path / "no_status.md"
        no_status.write_text(
            "---\n"
            "id: probe-no-status\n"
            "title: Probe without status\n"
            "type: backlog_item\n"
            "kind: feature\n"
            "priority: low\n"
            "effort: S\n"
            "---\n\n"
            "Body.\n",
            encoding="utf-8",
        )
        with_status = tmp_path / "with_status.md"
        with_status.write_text(
            "---\n"
            "id: probe-with-status\n"
            "title: Probe with status\n"
            "type: backlog_item\n"
            "kind: feature\n"
            "priority: low\n"
            "effort: S\n"
            "status: done\n"
            "---\n\n"
            "Body.\n",
            encoding="utf-8",
        )

        repo = _repo_for(tmp_path)

        # Load BOTH before saving either, so any leak through the shared
        # cached probe has the chance to happen between the two loads.
        entry_no_status = repo.load_entry_from_file(no_status)
        entry_with_status = repo.load_entry_from_file(with_status)

        entry_no_status.save(no_status)
        entry_with_status.save(with_status)

        assert "status:" not in no_status.read_text(encoding="utf-8")
        assert "status: done" in with_status.read_text(encoding="utf-8")

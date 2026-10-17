"""The round-trip identity gate (quality, retro 2), on the one write path.

Writing an entry's own values back should change nothing on disk. Before
PR #69 a no-op load -> ``Entry.save`` rewrote 768 of 768 real ``kb/`` files;
#69, #557, #148, #150 and #151 brought the residual down to 8, all trailing
blank lines the model's emitter normalised. Since #955 (B6 P4) the model has
no emitter: every write is operations applied to the file's own bytes
(ADR-0042 decisions 1 and 3, ``file_operations.apply``), published by
``storage/entry_writer.py``. So the gate is now an **echo**: for every file,
one ``Set`` per frontmatter key with the value the loader read, and a
``ReplaceBody`` with the body as read, through ``KBService.apply_operations``.
Decision 3 says equal values write nothing; this test measures that over the
whole corpus instead of trusting it.

Every write-path bug in the run-up to the original gate -- #46 (an
incidental ``--tags`` update rewriting the whole file), #86 (``create``
leaking ``importance``/``rank`` into files that never had them), #87
(``link``/``update`` folding the body into a ``body:`` scalar) -- was a
special case of "the write put something the read did not take". An echo
that changes bytes is that class of bug; it fails here.

What is not echoed: ``id`` and ``type`` (identity, never set by an update)
and the managed lists ``links`` and ``sources`` (an update takes items of
them, never the whole list; the walking test covers those). A file the
write refuses (its own schema, #955's validation of the result) writes
nothing, which is the same property; the refusals are named in
``KNOWN_REFUSED`` so they only change on purpose.

Safety: this NEVER writes inside the real ``kb/``. Both the full-corpus
walk and the fixture walk copy their source into a ``tmp_path`` first and
operate only on the copy, so an agent running this file cannot corrupt the
checked-in KB no matter what the write path does.
"""

from __future__ import annotations

import difflib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.file_operations import ReplaceBody, Set
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository
from pyrite.utils.frontmatter import Frontmatter, split_frontmatter
from pyrite.utils.yaml import load_yaml

pytestmark = pytest.mark.control(
    reason="the gate pins a property the base already had (an echo through apply writes nothing); it is rewritten here because its old form used Entry.save, gone since #955 P4"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_KB_DIR = REPO_ROOT / "kb"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "roundtrip"

#: Keys an echo does not send: identity (an update never sets them) and the
#: managed lists (an update takes one item of them, never the list).
NOT_ECHOED = frozenset({"id", "type", "links", "sources"})

#: Real kb/ ids whose echo the write refuses today, with the reason's text:
#: the file itself violates its type's schema, so the validated result is
#: refused and nothing is written (the property still holds). Named exactly,
#: so the set changes only on purpose; fix the entry and remove it here.
KNOWN_REFUSED: dict[str, str] = {
    "adr-0003": "superseded_by: required_when_superseded",
}


def _copy_kb_to(tmp_path: Path, source: Path) -> Path:
    dest = tmp_path / "kb_copy"
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns(".git"))
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True, capture_output=True, timeout=30)
    return dest


def _service_for(tmp_path: Path, kb_path: Path, kb_type: str = "generic"):
    """A service over ``kb_path``, indexed; the caller closes ``db``."""
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="roundtrip-test", path=kb_path, kb_type=kb_type)],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    IndexManager(db, config).index_all()
    return KBService(config, db), db, KBRepository(config.knowledge_bases[0])


def _echo_ops(text: str) -> list[object] | None:
    """The echo of ``text``: one ``Set`` per frontmatter key with the value as
    loaded, and the body as read; None for a file with no frontmatter."""
    split = split_frontmatter(text)
    if not isinstance(split, Frontmatter):
        return None
    values = load_yaml(split.text)
    if not isinstance(values, dict):
        return None
    ops: list[object] = [Set(k, v) for k, v in values.items() if k not in NOT_ECHOED]
    ops.append(ReplaceBody(split.body))
    return ops


def _echo(svc: KBService, repo: KBRepository, path: Path) -> tuple[str, str, str | None]:
    """Echo one file; returns ``(before, after, refusal)``."""
    before = path.read_text(encoding="utf-8")
    ops = _echo_ops(before)
    refusal = None
    if ops is not None:
        try:
            svc.apply_operations(repo.id_of_file(path), repo.name, ops)
        except Exception as e:  # noqa: BLE001 - the refusal is the finding
            refusal = f"{type(e).__name__}: {e}"
    return before, path.read_text(encoding="utf-8"), refusal


def _echo_diffs(
    svc: KBService, repo: KBRepository
) -> tuple[list[tuple[str, Path, str, str]], dict[str, str]]:
    """Echo every file through the service: the files whose bytes changed
    (entry_id, path, before, after) and the refusals by id."""
    diffs: list[tuple[str, Path, str, str]] = []
    refused: dict[str, str] = {}
    for path in sorted(repo.list_files()):
        entry_id = repo.id_of_file(path)
        before, after, refusal = _echo(svc, repo, path)
        if refusal:
            refused[entry_id] = refusal
        if before != after:
            diffs.append((entry_id, path, before, after))
    return diffs, refused


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
    header = f"{len(diffs)} file(s) changed on an echo of their own values"
    if more > 0:
        header += f" (showing first {limit}, {more} more not shown)"
    return header + "\n\n" + "\n".join(chunks)


@pytest.fixture(scope="module", autouse=True)
def real_kb_walk_calls():
    """Count actual walks of real-corpus copies, including calls outside the
    shared fixture. Check at module teardown so a later test cannot bypass
    the guard by walking again after the assertion test has already passed.
    Smaller synthetic KBs are not part of this shared-corpus budget.
    """
    real_copies: set[Path] = set()
    calls: list[Path] = []
    original_copy = _copy_kb_to
    original_walk = _echo_diffs

    def tracked_copy(tmp_path, source):
        copied = original_copy(tmp_path, source)
        if source.resolve() == REAL_KB_DIR.resolve():
            real_copies.add(copied.resolve())
        return copied

    def tracked_walk(svc, repo):
        if repo.path.resolve() in real_copies:
            calls.append(repo.path.resolve())
        return original_walk(svc, repo)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys.modules[__name__], "_copy_kb_to", tracked_copy)
        patch.setattr(sys.modules[__name__], "_echo_diffs", tracked_walk)
        yield calls
        if real_copies:
            assert len(calls) == 1, (
                f"real kb/ was walked {len(calls)} times in this module; "
                "reuse real_kb_walk instead of walking the corpus per test"
            )


@pytest.fixture(scope="module")
def real_kb_walk(tmp_path_factory, real_kb_walk_calls):
    """Echo a temp copy of the real ``kb/`` once per module, as the software
    KB it is (its plugin types validate the result, as in production)."""
    pytest.importorskip("pyrite_software_kb.plugin")
    from pyrite.plugins.registry import PluginRegistry
    from pyrite_software_kb.plugin import SoftwareKBPlugin

    PluginRegistry().register(SoftwareKBPlugin())
    tmp_path = tmp_path_factory.mktemp("real_kb_walk")
    kb_copy = _copy_kb_to(tmp_path, REAL_KB_DIR)
    svc, db, repo = _service_for(tmp_path, kb_copy, kb_type="software")
    try:
        return _echo_diffs(svc, repo)
    finally:
        db.close()


class TestRealKBRoundTrip:
    """The full corpus: every file under the real ``kb/`` (a copy)."""

    def test_real_kb_is_byte_identical_after_an_echo(self, real_kb_walk):
        diffs, _refused = real_kb_walk
        assert not diffs, _format_diffs(diffs)

    def test_real_kb_walk_is_shared(self, real_kb_walk, real_kb_walk_calls):
        assert len(real_kb_walk_calls) == 1

    def test_the_refused_echoes_are_exactly_the_known_set(self, real_kb_walk):
        """A refusal writes nothing, which is the property; the set is named so
        a new refusal (a schema the write now checks, a file that stopped
        loading) is a finding, and a fixed entry leaves here on purpose."""
        _diffs, refused = real_kb_walk
        assert set(refused) == set(KNOWN_REFUSED), refused
        for entry_id, reason in KNOWN_REFUSED.items():
            assert reason in refused[entry_id], refused[entry_id]


class TestAdversarialFixtures:
    """The smaller, hand-built adversarial shapes under tests/fixtures/roundtrip/.

    Each file is its own parametrized case rather than one walk, so a
    failure names the exact fixture and shape at fault instead of "some file
    in kb/ changed".
    """

    @staticmethod
    def _kb_with(tmp_path: Path, name: str, text: str | None = None) -> tuple[Path, object]:
        kb = tmp_path / "kb"
        kb.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=kb, check=True, capture_output=True, timeout=30)
        dest = kb / name
        if text is None:
            shutil.copyfile(FIXTURES_DIR / name, dest)
        else:
            dest.write_text(text, encoding="utf-8")
        return dest, _service_for(tmp_path, kb)

    # anchors_and_merge_keys.md has its own test below: `apply` refuses a
    # file whose frontmatter defines or uses an anchor, so its echo is a
    # refusal (nothing written), not a byte-identical write.
    _DEDICATED_TEST_FIXTURES = frozenset({"anchors_and_merge_keys.md"})

    @pytest.mark.parametrize(
        "fixture_name",
        sorted(p.name for p in FIXTURES_DIR.glob("*.md")),
    )
    def test_fixture_is_byte_identical_after_an_echo(self, tmp_path, fixture_name):
        if fixture_name in self._DEDICATED_TEST_FIXTURES:
            pytest.skip("covered by its own dedicated test in this file, see the class comment")
        dest, (svc, db, repo) = self._kb_with(tmp_path, fixture_name)
        try:
            before, after, refusal = _echo(svc, repo, dest)
        finally:
            db.close()

        assert refusal is None, refusal
        assert before == after, _format_diffs([(fixture_name, dest, before, after)])

    def test_anchors_and_merge_keys_fixture_is_refused_and_left_as_written(self, tmp_path):
        """No real kb/ file uses a YAML anchor or merge key (checked directly
        against the corpus). The model's emitter used to resolve
        ``<<: *defaults`` into its constituent keys on a no-op save; ``apply``
        refuses to edit a frontmatter that defines or uses an anchor (it
        cannot say which bytes a merged key's value owns), names the anchor,
        and writes nothing: the file keeps its merge key.
        """
        dest, (svc, db, repo) = self._kb_with(tmp_path, "anchors_and_merge_keys.md")
        try:
            before, after, refusal = _echo(svc, repo, dest)
        finally:
            db.close()

        assert refusal is not None and "anchor" in refusal, refusal
        assert before == after
        assert "<<: *defaults" in after

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
    def test_trailing_newline_shapes_are_left_as_written(self, tmp_path, body_suffix):
        """Neither shape is a committed fixture FILE: this repo's own pre-commit
        end-of-file-fixer hook normalizes exactly "missing trailing newline" /
        "extra trailing blank lines" on every commit. Built as bytes here
        instead. The model's emitter used to normalise both to one newline;
        an echo through ``apply`` writes nothing, so the file keeps its shape
        (decision 3: a request that changes nothing writes nothing).
        """
        text = self._TRAILING_NEWLINE_FRONTMATTER + body_suffix
        dest, (svc, db, repo) = self._kb_with(tmp_path, "trailing_newline_shape.md", text)
        try:
            before, after, refusal = _echo(svc, repo, dest)
            assert refusal is None, refusal
            assert after == before == text
            _before, twice, refusal = _echo(svc, repo, dest)
        finally:
            db.close()

        assert refusal is None, refusal
        assert twice == text


class TestPristineProbeIsolation:
    """Pins the exact bug #69's last commit fixed: ``_pristine_frontmatter``
    is an ``@cache``d-per-class probe, so it is SHARED across every entry of
    that class loaded during one walk. If the write path ever read or wrote
    through that shared dict instead of only comparing against it, one
    entry's absent-key decisions would leak into another entry of the same
    type. With ``apply`` the write names only the keys it is given, so the
    probe cannot reach the file at all; the pin stays as the guard.
    """

    def test_pristine_probe_does_not_leak_between_entries_of_same_type(self, tmp_path):
        # Two backlog_item entries, same type, different absent-key sets:
        # one has no `status:` at all, the other sets status explicitly.
        # Loading both and echoing both must not let the first entry's
        # "status was absent" fact bleed into the second, or vice versa.
        kb = tmp_path / "kb"
        kb.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=kb, check=True, capture_output=True, timeout=30)
        no_status = kb / "no_status.md"
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
        with_status = kb / "with_status.md"
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
        svc, db, repo = _service_for(tmp_path, kb)
        try:
            # Load BOTH before writing either, so any leak through the shared
            # cached probe has the chance to happen between the two loads.
            repo.load_entry_from_file(no_status)
            repo.load_entry_from_file(with_status)
            for path in (no_status, with_status):
                _before, _after, refusal = _echo(svc, repo, path)
                assert refusal is None, refusal
        finally:
            db.close()

        assert "status:" not in no_status.read_text(encoding="utf-8")
        assert "status: done" in with_status.read_text(encoding="utf-8")

"""Regression tests for #46: `pyrite update` rewriting frontmatter it was not asked to touch.

An update of a single field (`--tags`, `--title`, `-b`) must leave every other
top-level frontmatter key exactly as it was on disk. Before the fix, a
load -> setattr -> save round trip through KBService.update_entry ADDED
`body:` (the whole body re-serialized as a YAML string) and `file_path:`
(an absolute path) to the file, because KBRepository._load_entry injects
those two model internals into the frontmatter dict it hands to
entry_from_frontmatter, and capture_extra_frontmatter then records them as
"unknown frontmatter the class did not emit" and writes them back on save.

Six KB items were corrupted this way in one conductor loop. An item whose
frontmatter grows a `body:` key is silently wrong everywhere that reads
frontmatter, and the file doubles in size on every update.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.utils.yaml import load_yaml

# Keys that are model internals, never frontmatter. If any of these appear in
# a saved file's frontmatter, the write path leaked an attribute.
NEVER_IN_FRONTMATTER = ("body", "file_path", "kb_name", "extra_frontmatter")


def _read_frontmatter_text(text):
    """Parse the YAML frontmatter block out of a markdown string."""
    assert text.startswith("---\n"), f"no frontmatter fence in {text[:40]!r}"
    end = text.index("\n---", 3)
    return load_yaml(text[3:end])


def _read_frontmatter(path):
    """Parse the YAML frontmatter block of a KB markdown file."""
    return _read_frontmatter_text(path.read_text(encoding="utf-8"))


BACKLOG_ITEM = """---
id: sample-backlog-item
type: backlog_item
title: A backlog item with declared and undeclared keys
kind: tech_debt
status: proposed
priority: high
effort: XS
created: "2026-07-03"
milestone: "0.24.2"
github_issue: 46
tags: [testing, quality]
links:
- target: some-other-entry
  relation: related
  kb: swkb
---

## Problem

The body of the entry. It has a `---` divider below to be adversarial.

---

And a second paragraph after it.
"""

NOTE_ENTRY = """---
id: sample-note
type: note
title: A core-type note with an undeclared key
created: "2026-07-03"
custom_field: keep-me
tags: [alpha]
---

Note body.
"""

# The fixture the first two passes of this fix were missing. BACKLOG_ITEM above
# already carries `status:` and `priority:`, so no test on it could ever catch a
# write path that INVENTS those keys -- which is exactly how the `priority`
# regression reached review: `rank` was guarded, `priority` three lines above it
# was not, and every test still passed. A file holding nothing but the three
# keys every entry must have is the only shape that can fail.
MINIMAL_BACKLOG_ITEM = """---
id: minimal-backlog-item
type: backlog_item
title: A backlog item with no status, priority, rank or importance
---

Minimal body.
"""

MINIMAL_NOTE = """---
id: minimal-note
type: note
title: A note with no importance key
---

Minimal body.
"""


@pytest.fixture
def swkb_env(tmp_path):
    """A software KB holding one backlog_item and one note, plus a KBService."""
    # The software-kb extension registers `backlog_item`; importing the plugin
    # module is what puts the type in the registry for entry_from_frontmatter.
    pytest.importorskip("pyrite_software_kb.entry_types")
    from pyrite.plugins.registry import PluginRegistry
    from pyrite_software_kb.plugin import SoftwareKBPlugin

    PluginRegistry().register(SoftwareKBPlugin())

    kb_path = tmp_path / "swkb"
    (kb_path / "backlog").mkdir(parents=True)
    (kb_path / "notes").mkdir(parents=True)
    (kb_path / "backlog" / "sample-backlog-item.md").write_text(BACKLOG_ITEM, encoding="utf-8")
    (kb_path / "notes" / "sample-note.md").write_text(NOTE_ENTRY, encoding="utf-8")
    (kb_path / "backlog" / "minimal-backlog-item.md").write_text(
        MINIMAL_BACKLOG_ITEM, encoding="utf-8"
    )
    (kb_path / "notes" / "minimal-note.md").write_text(MINIMAL_NOTE, encoding="utf-8")

    kb = KBConfig(name="swkb", path=kb_path, kb_type="software", description="test software KB")
    config = PyriteConfig(knowledge_bases=[kb], settings=Settings(index_path=tmp_path / "index.db"))
    db = PyriteDB(config.settings.index_path)
    yield {
        "config": config,
        "db": db,
        "service": KBService(config, db),
        "backlog_file": kb_path / "backlog" / "sample-backlog-item.md",
        "note_file": kb_path / "notes" / "sample-note.md",
        "minimal_backlog_file": kb_path / "backlog" / "minimal-backlog-item.md",
        "minimal_note_file": kb_path / "notes" / "minimal-note.md",
    }
    db.close()


class TestUpdateLeavesUntouchedFrontmatterAlone:
    """The core of #46: an update of one field changes exactly that field."""

    def test_tags_update_changes_only_tags_on_extension_type(self, swkb_env):
        """`update --tags` on a backlog_item (software-kb extension type).

        This is the exact shape of the reported corruption.
        """
        path = swkb_env["backlog_file"]
        before = _read_frontmatter(path)

        swkb_env["service"].update_entry(
            "sample-backlog-item", "swkb", tags=["testing", "quality", "audit"]
        )

        after = _read_frontmatter(path)
        assert after["tags"] == ["testing", "quality", "audit"]
        # Every other key is byte-for-byte what it was.
        before.pop("tags")
        after.pop("tags")
        assert after == before

    def test_tags_update_never_writes_model_internals(self, swkb_env):
        """`body:`, `file_path:` are attributes of the model, not frontmatter."""
        swkb_env["service"].update_entry("sample-backlog-item", "swkb", tags=["x"])

        after = _read_frontmatter(swkb_env["backlog_file"])
        for key in NEVER_IN_FRONTMATTER:
            assert key not in after, f"{key!r} leaked into frontmatter"

    def test_declared_fields_survive_a_tags_update(self, swkb_env):
        """kind/status/priority/effort are what `pyrite sw backlog` reads."""
        swkb_env["service"].update_entry("sample-backlog-item", "swkb", tags=["x"])

        after = _read_frontmatter(swkb_env["backlog_file"])
        assert after["kind"] == "tech_debt"
        assert after["status"] == "proposed"
        assert after["priority"] == "high"
        assert after["effort"] == "XS"

    def test_undeclared_keys_survive_a_tags_update(self, swkb_env):
        """The PR #35 guarantee: keys the class does not know are preserved."""
        swkb_env["service"].update_entry("sample-backlog-item", "swkb", tags=["x"])

        after = _read_frontmatter(swkb_env["backlog_file"])
        assert after["created"] == "2026-07-03"
        assert after["milestone"] == "0.24.2"
        assert after["github_issue"] == 46

    def test_title_update_changes_only_title(self, swkb_env):
        """`update --title` goes through the same load/save path as --tags."""
        path = swkb_env["backlog_file"]
        before = _read_frontmatter(path)

        swkb_env["service"].update_entry("sample-backlog-item", "swkb", title="A new title")

        after = _read_frontmatter(path)
        assert after["title"] == "A new title"
        before.pop("title")
        after.pop("title")
        assert after == before
        for key in NEVER_IN_FRONTMATTER:
            assert key not in after

    def test_body_update_changes_no_frontmatter_at_all(self, swkb_env):
        """`update -b` rewrites the body; frontmatter must be untouched.

        In particular the new body must NOT appear as a `body:` frontmatter key.
        """
        path = swkb_env["backlog_file"]
        before = _read_frontmatter(path)

        swkb_env["service"].update_entry("sample-backlog-item", "swkb", body="Replaced body.")

        after = _read_frontmatter(path)
        assert after == before
        assert "Replaced body." in path.read_text(encoding="utf-8").split("\n---\n", 1)[1]
        for key in NEVER_IN_FRONTMATTER:
            assert key not in after

    def test_core_type_note_is_protected_too(self, swkb_env):
        """Not an extension-type-only bug: the leak is in the shared load path."""
        path = swkb_env["note_file"]
        before = _read_frontmatter(path)

        swkb_env["service"].update_entry("sample-note", "swkb", tags=["beta"])

        after = _read_frontmatter(path)
        assert after["tags"] == ["beta"]
        assert after["custom_field"] == "keep-me"
        before.pop("tags")
        after.pop("tags")
        assert after == before
        for key in NEVER_IN_FRONTMATTER:
            assert key not in after

    def test_repeated_updates_do_not_grow_the_file(self, swkb_env):
        """The corruption compounded: each update re-embedded the body."""
        path = swkb_env["backlog_file"]
        swkb_env["service"].update_entry("sample-backlog-item", "swkb", tags=["a"])
        after_first = path.stat().st_size
        swkb_env["service"].update_entry("sample-backlog-item", "swkb", tags=["b"])
        after_second = path.stat().st_size

        assert after_second <= after_first + 8


class TestUpdateProducesAMinimalTextDiff:
    """Not just semantically equal -- the same bytes.

    A KB is a git repository. An update that reorders every key and rewrites
    `tags: [a, b]` as a block list produces a 20-line diff for a one-word
    change, which is what made the #46 corruption invisible in review: the
    real damage was buried in reformatting noise.
    """

    def test_tags_update_rewrites_only_the_tags_lines(self, swkb_env):
        path = swkb_env["backlog_file"]
        before = path.read_text(encoding="utf-8").splitlines()

        swkb_env["service"].update_entry(
            "sample-backlog-item", "swkb", tags=["testing", "quality", "audit"]
        )

        after = path.read_text(encoding="utf-8").splitlines()
        changed_before = [l for l in before if l not in after]
        changed_after = [l for l in after if l not in before]
        assert changed_before == ["tags: [testing, quality]"]
        assert changed_after == ["tags: [testing, quality, audit]"]

    def test_key_order_is_preserved(self, swkb_env):
        path = swkb_env["backlog_file"]
        before = list(_read_frontmatter(path).keys())

        swkb_env["service"].update_entry("sample-backlog-item", "swkb", tags=["x"])

        assert list(_read_frontmatter(path).keys()) == before

    def test_title_update_rewrites_only_the_title_line(self, swkb_env):
        path = swkb_env["backlog_file"]
        before = path.read_text(encoding="utf-8").splitlines()

        swkb_env["service"].update_entry("sample-backlog-item", "swkb", title="A new title")

        after = path.read_text(encoding="utf-8").splitlines()
        assert [l for l in before if l not in after] == [
            "title: A backlog item with declared and undeclared keys"
        ]
        assert [l for l in after if l not in before] == ["title: A new title"]


class TestAlwaysWrittenDefaultsStillWork:
    """The narrowing must not undo commit 7783335.

    `importance: 5` and `rank: 0` are written even at their default, because
    the default is a choice a user can make deliberately and dropping it was
    itself a silent-data-loss bug. The #46 fix only stops those keys being
    INVENTED on a file that never had them.
    """

    def test_new_entry_still_writes_default_importance(self):
        from pyrite.models.core_types import NoteEntry

        assert NoteEntry(id="t", title="T").to_frontmatter()["importance"] == 5

    def test_new_backlog_item_still_writes_zero_rank(self):
        pytest.importorskip("pyrite_software_kb.entry_types")
        from pyrite_software_kb.entry_types import BacklogItemEntry

        assert BacklogItemEntry(id="t", title="T", rank=0).to_frontmatter()["rank"] == 0

    def test_setting_importance_to_the_default_is_not_a_silent_no_op(self, swkb_env):
        """The case the #46 narrowing must not swallow.

        A file with no `importance:` key, and a user who explicitly asks for
        `importance = 5`. "Absent at load" must not be read as "the user does
        not want this written" once the user has assigned it -- that is the
        data loss commit 7783335 fixed, arriving through a new door.

        The mirror image of #46 itself: there, a write changed something nobody
        asked for; here, a write silently does not change something that was
        explicitly asked for. Both report success.

        Asserted together with the untouched sibling, deliberately. The "is
        written" half alone passes on the merge base too -- the base writes
        every default unconditionally -- so on its own it does not test this
        change at all. The pair does: the assigned key must appear AND the
        keys nobody touched must not. Run on a backlog item rather than a note
        because a note has only the one suppressible key, so on a note
        "assigned" and "untouched" cannot be told apart.
        """
        path = swkb_env["minimal_backlog_file"]
        assert set(_read_frontmatter(path)) == {"id", "type", "title"}

        swkb_env["service"].update_entry("minimal-backlog-item", "swkb", importance=5)

        after = _read_frontmatter(path)
        expected = {"id", "type", "title", "importance"}
        assert after.get("importance") == 5
        assert set(after) == expected, f"the update also invented {sorted(set(after) - expected)}"

    def test_setting_a_non_default_importance_still_works(self, swkb_env):
        """The control: this path never broke and must keep working."""
        swkb_env["service"].update_entry("sample-note", "swkb", importance=8)

        assert _read_frontmatter(swkb_env["note_file"])["importance"] == 8

    def test_setting_rank_to_zero_is_not_a_silent_no_op(self, swkb_env):
        """Same bug on the extension type. `sw reorder` legitimately computes
        rank=0 for the first item, through this same update_entry path.

        Paired with the untouched siblings for the same reason as the
        importance case above, and on the minimal item so the siblings exist
        to be got wrong: on the merge base this same call also writes
        `status:`, `priority:` and `importance:` onto a file that had none of
        them, which is the #46 corruption itself.
        """
        path = swkb_env["minimal_backlog_file"]
        assert set(_read_frontmatter(path)) == {"id", "type", "title"}

        swkb_env["service"].update_entry("minimal-backlog-item", "swkb", rank=0)

        after = _read_frontmatter(path)
        expected = {"id", "type", "title", "rank"}
        assert after.get("rank") == 0
        assert set(after) == expected, f"the update also invented {sorted(set(after) - expected)}"

    def test_assigning_the_attribute_directly_also_counts_as_explicit(self):
        """update_entry is not the only caller: REST, MCP and the software-kb
        reorder reach the model differently. The signal has to live on the
        model, not in one service method."""
        from pyrite.models.core_types import NoteEntry

        note = NoteEntry(id="t", title="T")
        note._absent_default_keys = frozenset({"importance"})  # as a load would
        # Checked at the file boundary: `to_frontmatter` deliberately keeps
        # reporting the real value so the index can see it; only the file drops
        # a default the source never carried.
        assert "importance" not in _read_frontmatter_text(note.to_markdown())

        note.importance = 5  # explicit assignment, value equal to the default

        assert _read_frontmatter_text(note.to_markdown()).get("importance") == 5

    def test_an_untouched_default_key_is_still_not_invented(self, swkb_env):
        """The #46 guarantee restated: assigning ANOTHER field must not drag
        `importance:` into a file that never carried it."""
        swkb_env["service"].update_entry("sample-note", "swkb", tags=["q"])

        assert "importance" not in _read_frontmatter(swkb_env["note_file"])

    def test_the_key_is_kept_where_the_file_has_it_and_not_added_where_it_does_not(self, swkb_env):
        """Both directions of the same rule, in one update, so it can fail.

        The "kept" half on its own passes on the merge base -- the base writes
        those keys whatever the file said -- so it proves nothing about this
        change. What distinguishes the two trees is that the SAME `--tags`
        update must add the keys to neither file, while keeping them on the
        one that has them.
        """
        path = swkb_env["backlog_file"]
        text = path.read_text(encoding="utf-8").replace(
            "effort: XS", "effort: XS\nimportance: 5\nrank: 0"
        )
        path.write_text(text, encoding="utf-8")
        minimal = swkb_env["minimal_backlog_file"]

        swkb_env["service"].update_entry("sample-backlog-item", "swkb", tags=["x"])
        swkb_env["service"].update_entry("minimal-backlog-item", "swkb", tags=["x"])

        assert set(_read_frontmatter(minimal)) == {"id", "type", "title", "tags"}
        after = _read_frontmatter(path)
        assert after["importance"] == 5
        assert after["rank"] == 0


ANCHOR_NOTE = """---
id: anchor-note
type: note
title: A note whose frontmatter uses YAML anchors
metadata:
  base: &anch
    x: 1
  derived:
    <<: *anch
    y: 2
tags: [a]
---

Body.
"""


class TestStructuralYamlSurvivesAWrite:
    """Carrying style from the source mapping must not mangle its structure.

    The restyle exists to keep a one-field update to a one-line diff. Copying
    the source node graph to get that also drags YAML anchors, aliases and
    merge keys along -- and a deep copy of a ruamel CommentedMap resolves a
    `<<:` merge into a literal key whose value is the merged mapping, emitting
    the merged keys twice.

    These three DO NOT fail on the merge base, and are not expected to. What
    they guard is a defect *inside* `_restyle_like_source`, which the merge
    base does not have: reverting the implementation removes the mechanism and
    its bug together, so the base writes a plain dict and trivially passes.
    They were written in response to a cold read that found the deepcopy
    destroying `&anch`, and their job is to catch its return in a future
    refactor of that function -- a forward guard, not a regression proof.
    Verified by reintroducing the deepcopy locally: the two that read the
    emitted TEXT fail, and the meaning-preserving one does not, because a file
    with a literal `<<:` and a duplicate key still parses back to the same
    mapping. That asymmetry is why the structural assertions inspect the text.
    Do not read a green run here as evidence about #46; the tests that
    carry that are in TestNoRegisteredTypeInventsFrontmatter and
    TestAlwaysWrittenDefaultsStillWork, and those are red at the base.
    """

    @pytest.fixture
    def anchor_env(self, swkb_env):
        path = swkb_env["note_file"].parent / "anchor-note.md"
        path.write_text(ANCHOR_NOTE, encoding="utf-8")
        return {**swkb_env, "anchor_file": path}

    def test_merge_key_is_not_expanded_into_a_literal_key(self, anchor_env):
        anchor_env["service"].update_entry("anchor-note", "swkb", tags=["z"])

        text = anchor_env["anchor_file"].read_text(encoding="utf-8")
        derived = _read_frontmatter(anchor_env["anchor_file"])["metadata"]["derived"]
        assert dict(derived) == {"x": 1, "y": 2}, text
        # `<<:` must still be a merge, not a mapping-valued key of its own.
        assert "<<:\n" not in text, text

    def test_no_duplicate_key_is_emitted(self, anchor_env):
        anchor_env["service"].update_entry("anchor-note", "swkb", tags=["z"])

        text = anchor_env["anchor_file"].read_text(encoding="utf-8")
        derived_block = text.split("derived:", 1)[1]
        assert derived_block.count("x: 1") <= 1, text

    def test_the_written_file_still_means_what_the_source_meant(self, anchor_env):
        """The meaning survives two writes -- checked against the SOURCE.

        A weaker guarantee than the two above, and deliberately kept separate
        from them. It does NOT fail on the merge-expanding deepcopy: a file
        carrying a literal `<<:` key and a duplicate `x: 1` still parses back
        to the same mapping, because the loader resolves the damage away. That
        is exactly why the two structural assertions above read the TEXT. This
        one guards the weaker property they do not -- that nothing we emit
        changes the entry's meaning -- and is named for what it checks rather
        than for the defect, so it is not mistaken for a guard it is not.
        """
        source = _read_frontmatter_text(ANCHOR_NOTE)
        anchor_env["service"].update_entry("anchor-note", "swkb", tags=["z"])
        first = _read_frontmatter(anchor_env["anchor_file"])

        anchor_env["service"].update_entry("anchor-note", "swkb", tags=["z2"])
        second = _read_frontmatter(anchor_env["anchor_file"])

        expected = {k: dict(v) for k, v in source["metadata"].items()}
        assert {k: dict(v) for k, v in first["metadata"].items()} == expected
        assert {k: dict(v) for k, v in second["metadata"].items()} == expected


class TestValuesAreComparedByTypeNotTruthiness:
    """`1 == True` and `0 == False` in Python. Comparing a new value to the
    source value with plain `==` therefore treats a bool set over an int (or
    vice versa) as 'unchanged' and silently keeps the old one -- the same
    'write reports success and does nothing' shape as the importance bug.

    Like the anchor class, this does not fail on the merge base, because the
    comparison it guards is part of the restyle this branch introduces -- there
    is nothing there to get wrong. It is a real guard nonetheless: deleting the
    bool tagging from `_plain` makes it fail with `assert 1 is True`, verified
    rather than assumed."""

    def test_bool_replacing_an_equal_int_is_written(self, swkb_env):
        path = swkb_env["note_file"]
        text = path.read_text(encoding="utf-8").replace("tags: [alpha]", "flag: 1\ntags: [alpha]")
        path.write_text(text, encoding="utf-8")

        entry = swkb_env["service"].update_entry("sample-note", "swkb", tags=["t"])
        entry.extra_frontmatter["flag"] = True
        entry.save()

        assert _read_frontmatter(path)["flag"] is True


class TestNoRegisteredTypeInventsFrontmatter:
    """The guarantee stated once, over every type, instead of per class.

    #46 was fixed on `importance` and `rank` by guarding two call sites. That
    left the same defect in 32 of the 48 registered entry types -- `ADREntry`
    inventing `adr_number: 0` and `status:`, `ZettelEntry` inventing
    `maturity:`, `QAAssessmentEntry` inventing four keys -- because every one
    of them writes some field unconditionally at its default.

    Per-type tests could not catch that: a new plugin type ships with no test
    here at all. So the assertion is made over the registry, and a type added
    next week is covered the day it registers.
    """

    @staticmethod
    def _registered_types():
        from pyrite.models.core_types import ENTRY_TYPE_REGISTRY
        from pyrite.plugins import get_registry

        types = dict(ENTRY_TYPE_REGISTRY)
        types.update(get_registry().get_all_entry_types())
        return types

    def test_the_registry_is_actually_populated(self, swkb_env):
        """Guard the guard: an empty registry would make the sweep vacuous."""
        types = self._registered_types()
        assert len(types) > 20, f"only {len(types)} types registered; the sweep proves nothing"
        assert "backlog_item" in types and "adr" in types

    def test_a_minimal_entry_of_any_type_round_trips_without_gaining_keys(self, swkb_env):
        """Load a file holding only id/title/type, save it, gain nothing.

        Driven through `to_markdown` -- the real file-write path -- rather than
        `to_frontmatter`, because those two deliberately differ: the index and
        the renderers must still see the entry's real field values, and only
        the file must be kept as the author wrote it.
        """
        from pyrite.models.base import capture_extra_frontmatter

        offenders = {}
        for type_name, cls in sorted(self._registered_types().items()):
            meta = {"id": "probe", "title": "Probe", "type": type_name}
            try:
                entry = cls.from_frontmatter(dict(meta), body="Body.")
                capture_extra_frontmatter(entry, dict(meta))
                written = _read_frontmatter_text(entry.to_markdown())
            except Exception as exc:  # a type that cannot round trip at all
                offenders[type_name] = f"raised {type(exc).__name__}: {exc}"
                continue
            invented = sorted(set(written) - set(meta))
            if invented:
                offenders[type_name] = invented

        assert not offenders, (
            f"{len(offenders)} entry type(s) add frontmatter keys the source file "
            f"never had -- the #46 corruption: {offenders}"
        )

    def test_the_pristine_probe_is_not_shared_into_any_entry(self, swkb_env):
        """The cached probe is per-class and shared; nothing may mutate it.

        `_pristine_frontmatter` is `@cache`d, so one dict backs every entry of
        a type. The write path only compares against it -- but if a value from
        it were ever carried into an entry's frontmatter, a mutation anywhere
        would corrupt every subsequent save of that type. Pinned here because
        the failure would be silent and global.
        """
        from pyrite.models.base import _pristine_frontmatter, capture_extra_frontmatter
        from pyrite.models.core_types import get_entry_class

        cls = get_entry_class("backlog_item")
        probe = _pristine_frontmatter(cls)
        snapshot = dict(probe)

        meta = {"id": "probe", "title": "Probe", "type": "backlog_item"}
        entry = cls.from_frontmatter(dict(meta), body="b")
        capture_extra_frontmatter(entry, dict(meta))
        entry.to_markdown()
        entry.status = "done"
        entry.to_markdown()

        assert _pristine_frontmatter(cls) == snapshot
        # And no value object is shared between the probe and a live entry.
        emitted = entry.to_frontmatter()
        for key in set(probe) & set(emitted):
            if isinstance(probe[key], (dict, list)):
                assert probe[key] is not emitted[key], f"{key} shares a mutable with the probe"

    def test_a_minimal_backlog_item_survives_a_real_cli_shaped_update(self, swkb_env):
        """The end-to-end shape of the reported bug, on a file that can fail.

        `pyrite update <id> --tags x` on a backlog item whose file carries no
        `status:`, `priority:` or `rank:` must not add any of them.
        """
        path = swkb_env["minimal_backlog_file"]
        before = _read_frontmatter(path)
        assert set(before) == {"id", "type", "title"}, before

        swkb_env["service"].update_entry("minimal-backlog-item", "swkb", tags=["x"])

        after = _read_frontmatter(path)
        assert set(after) == {"id", "type", "title", "tags"}, (
            f"update invented {sorted(set(after) - set(before) - {'tags'})}"
        )

    def test_the_index_still_sees_the_fields_the_file_omits(self, swkb_env):
        """The other half of the trade, stated so it cannot be quietly lost.

        Suppression is a property of the FILE, not of the entry. `sw backlog`
        filters on the `status` and `priority` the index holds, so an entry
        loaded from a file without those keys must still report its defaults
        to `to_frontmatter` -- only `to_markdown` drops them.
        """
        from pyrite.models.base import capture_extra_frontmatter
        from pyrite.models.core_types import get_entry_class

        meta = {"id": "probe", "title": "Probe", "type": "backlog_item"}
        cls = get_entry_class("backlog_item")
        entry = cls.from_frontmatter(dict(meta), body="b")
        capture_extra_frontmatter(entry, dict(meta))

        assert entry.to_frontmatter()["status"] == "proposed"
        assert entry.to_frontmatter()["priority"] == "medium"
        assert "status" not in _read_frontmatter_text(entry.to_markdown())
        assert "priority" not in _read_frontmatter_text(entry.to_markdown())

    def test_an_explicit_assignment_still_reaches_the_file(self, swkb_env):
        """The `__setattr__` rule, checked on a type other than the two the
        original fix guarded by hand -- the suppression is now central, so the
        escape hatch has to be central too."""
        from pyrite.models.base import capture_extra_frontmatter
        from pyrite.models.core_types import get_entry_class

        meta = {"id": "probe", "title": "Probe", "type": "adr"}
        cls = get_entry_class("adr")
        entry = cls.from_frontmatter(dict(meta), body="b")
        capture_extra_frontmatter(entry, dict(meta))
        assert "status" not in _read_frontmatter_text(entry.to_markdown())

        entry.status = "proposed"  # explicit, and equal to the default

        assert _read_frontmatter_text(entry.to_markdown())["status"] == "proposed"


class TestLoadDoesNotCaptureInternalsAsExtras:
    """Root cause, tested directly at the layer where it happens."""

    def test_loaded_entry_has_no_internals_in_extra_frontmatter(self, swkb_env):
        from pyrite.storage.repository import KBRepository

        repo = KBRepository(swkb_env["config"].get_kb("swkb"))
        entry = repo.load("sample-backlog-item")

        assert entry is not None
        for key in NEVER_IN_FRONTMATTER:
            assert key not in entry.extra_frontmatter, (
                f"{key!r} captured as undeclared frontmatter; "
                "_load_entry injected a model internal into the meta dict"
            )

    def test_loaded_entry_still_captures_real_extras(self, swkb_env):
        """The fix must not throw away the protection it is narrowing."""
        from pyrite.storage.repository import KBRepository

        repo = KBRepository(swkb_env["config"].get_kb("swkb"))
        entry = repo.load("sample-backlog-item")

        assert entry.extra_frontmatter["milestone"] == "0.24.2"
        assert entry.extra_frontmatter["github_issue"] == 46

    def test_to_frontmatter_of_a_loaded_entry_emits_no_internals(self, swkb_env):
        from pyrite.storage.repository import KBRepository

        repo = KBRepository(swkb_env["config"].get_kb("swkb"))
        entry = repo.load("sample-backlog-item")

        meta = entry.to_frontmatter()
        for key in NEVER_IN_FRONTMATTER:
            assert key not in meta


KB_NO_TIMESTAMPS = """---
id: no-ts
type: note
title: No timestamps
---

Body.
"""

KB_WITH_TIMESTAMPS = """---
id: with-ts
type: note
title: Has timestamps
created_at: 2020-01-01T00:00:00+00:00
updated_at: 2020-01-02T00:00:00+00:00
---

Body.
"""

KB_ONLY_CREATED_AT = """---
id: created-only
type: note
title: Only one timestamp
created_at: 2020-01-01T00:00:00+00:00
---

Body.
"""

KB_NUMERIC_TITLE = """---
id: numeric-title
type: note
title: "2026-01-15"
---

Body.
"""


class TestRepositorySaveDoesNotInventTimestamps:
    """#151, repository path: ``KBRepository.save()`` stamps ``updated_at`` on
    every write (and ``KBService.update_entry`` / ``sw link`` do the same).

    That stamp is internal bookkeeping, not a user editing the field, so it
    must not turn a file that never carried the key into one that does -- the
    #46 corruption arriving through the repository instead of KBService. A
    file that *does* carry the key keeps it (and its original style).

    ``KBRepository.save()`` infers a subdirectory from the entry and returns
    the path it actually wrote, so every assertion here runs against that
    path. The first version of these tests asserted on the source file, which
    ``save()`` had not touched -- they passed with the fix reverted.
    """

    def _repo(self, tmp_path, name="repo151"):
        from pyrite.storage.repository import KBRepository

        kb_path = tmp_path / name
        kb_path.mkdir(parents=True, exist_ok=True)
        return KBRepository(KBConfig(name=name, path=kb_path)), kb_path

    def test_a_file_without_timestamps_does_not_gain_them_on_save(self, tmp_path):
        repo, kb_path = self._repo(tmp_path)
        path = kb_path / "no-ts.md"
        path.write_text(KB_NO_TIMESTAMPS, encoding="utf-8")
        before = path.read_text(encoding="utf-8")

        entry = repo.load_entry_from_file(path)
        assert entry.updated_at is not None  # stamped in memory regardless
        out = repo.save(entry)

        after = out.read_text(encoding="utf-8")
        assert after == before, "the repository save rewrote a file it had no reason to change"
        assert set(_read_frontmatter(out)) == {"id", "type", "title"}, (
            f"the repository save invented {sorted(set(_read_frontmatter(out)) - {'id', 'type', 'title'})}"
        )

    def test_a_file_with_timestamps_is_still_written_back(self, tmp_path):
        repo, kb_path = self._repo(tmp_path, "repo151b")
        path = kb_path / "with-ts.md"
        path.write_text(KB_WITH_TIMESTAMPS, encoding="utf-8")

        entry = repo.load_entry_from_file(path)
        out = repo.save(entry)

        after = _read_frontmatter(out)
        assert "updated_at" in after, "a key the file carried was dropped"
        assert after["created_at"] == datetime(2020, 1, 1, tzinfo=UTC), (
            "KeyError on dev: the created_at the file carried was dropped"
        )

    def test_a_file_with_only_created_at_does_not_grow_updated_at(self, tmp_path):
        repo, kb_path = self._repo(tmp_path, "repo151c")
        path = kb_path / "created-only.md"
        path.write_text(KB_ONLY_CREATED_AT, encoding="utf-8")

        entry = repo.load_entry_from_file(path)
        out = repo.save(entry)

        after = _read_frontmatter(out)
        assert after["created_at"] == datetime(2020, 1, 1, tzinfo=UTC)
        assert "updated_at" not in after, "a half-stamped file grew the key it never had"


def test_no_module_assigns_entry_updated_at_directly():
    """`Entry.touch_updated_at()` is the only stamping path.

    A plain `entry.updated_at = …` counts as an explicit user edit for
    `__setattr__`, clears the key from `_absent_default_keys`, and makes the
    write path grow `updated_at` on a file that never had it -- exactly the
    regression #151's fix closes. Model-layer assignments read as
    `entry.`/`self.`/`e.`, which is what this scans for; the storage layer
    assigns ORM rows (`existing.updated_at = …`), a different object, and is
    out of scope.
    """
    import pyrite as _pyrite

    root = Path(_pyrite.__file__).resolve().parent
    pattern = re.compile(r"\b(?:entry|self|e)\.updated_at\s*=[^=]")
    offenders = [
        f"{path.relative_to(root)}:{lineno}: {line.strip()}"
        for path in sorted(root.rglob("*.py"))
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, (
        "assign updated_at through Entry.touch_updated_at(), not directly (see #151): "
        + "; ".join(offenders)
    )


def test_a_timestamp_looking_title_edit_reaches_the_file(tmp_path):
    """#173 review: the same-instant comparison must only run for timestamps.

    Applied to every key it kept the old node when a title was edited from
    `"2026-01-15"` to `"2026-01-15T00:00:00Z"` -- the CLI reported success and
    the file never changed, so the next index sync silently reverted the edit.
    """
    from pyrite.storage.repository import KBRepository

    kb_path = tmp_path / "repo151d"
    kb_path.mkdir(parents=True)
    path = kb_path / "numeric-title.md"
    path.write_text(KB_NUMERIC_TITLE, encoding="utf-8")
    repo = KBRepository(KBConfig(name="repo151d", path=kb_path))

    entry = repo.load_entry_from_file(path)
    entry.title = "2026-01-15T00:00:00Z"
    out = repo.save(entry)

    assert "2026-01-15T00:00:00Z" in out.read_text(encoding="utf-8"), (
        "the title edit was swallowed -- the write path kept the old node"
    )


def test_an_explicit_updated_at_update_keeps_the_callers_value(swkb_env):
    """#173 review: `update_entry` must not stamp over a caller-supplied
    `updated_at` -- before the guard it printed "Updated:" and wrote "now".

    The third review's case: REST PATCH and the CLI's `--field` hand the value
    in as a *string*. Assigned as-is it was written to the file as a quoted
    string and then raised inside `IndexManager._entry_to_dict` (a `str` has no
    `.isoformat`), so the file was written and the index was not.
    """
    path = swkb_env["note_file"]
    explicit = datetime(2001, 2, 3, 4, 5, 6, tzinfo=UTC)

    swkb_env["service"].update_entry("sample-note", "swkb", updated_at=explicit)

    after = _read_frontmatter(path)
    assert after["updated_at"] == explicit

    entry = swkb_env["service"].update_entry(
        "sample-note", "swkb", updated_at="2002-03-04T05:06:07+00:00"
    )

    assert entry.updated_at == datetime(2002, 3, 4, 5, 6, 7, tzinfo=UTC)
    assert _read_frontmatter(path)["updated_at"] == datetime(2002, 3, 4, 5, 6, 7, tzinfo=UTC)


def test_a_string_created_at_is_coerced_like_updated_at(swkb_env):
    """The same `.isoformat()` at `index.py:149` makes `created_at` reachable
    exactly like `updated_at`; both are coerced at the service boundary so
    neither can leave the file written and the index stale."""
    path = swkb_env["note_file"]

    entry = swkb_env["service"].update_entry(
        "sample-note", "swkb", created_at="2001-02-03T04:05:06+00:00"
    )

    assert entry.created_at == datetime(2001, 2, 3, 4, 5, 6, tzinfo=UTC)
    assert _read_frontmatter(path)["created_at"] == datetime(2001, 2, 3, 4, 5, 6, tzinfo=UTC)


def test_a_refreshed_updated_at_is_written_unquoted_without_microseconds(swkb_env):
    """#173 review: pin the file shape of the refreshed stamp.

    The value has to reach ruamel as a `datetime` so it is written as a plain
    YAML timestamp -- `updated_at: 2026-… 05:06:07+00:00` -- rather than a
    quoted `isoformat()` string carrying microseconds. Nothing pinned that
    shape before this test.
    """
    path = swkb_env["note_file"]
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "tags: [alpha]",
            "tags: [alpha]\nupdated_at: 2020-01-02T00:00:00+00:00",
        ),
        encoding="utf-8",
    )

    swkb_env["service"].update_entry("sample-note", "swkb", tags=["beta"])

    text = path.read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if l.startswith("updated_at:"))
    assert re.fullmatch(r"updated_at: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\+00:00", line), (
        f"refreshed updated_at has the wrong shape: {line!r}"
    )


ADR_WITH_BARE_DATE = """---
id: sample-adr-with-bare-date
type: adr
title: An ADR whose date is a bare YAML date
adr_number: 1
status: accepted
date: 2025-06-01
---

Decision body.
"""


def test_a_timestamp_looking_title_edit_through_update_entry_reaches_the_file(swkb_env):
    """#173 review: the same-instant shortcut must never run for a title.

    The reviewer's case, on the service path the CLI, REST and MCP all take:
    a title edited from `"2026-01-15"` to `"2026-01-15T00:00:00Z"` reported
    success and left the old node, so the next index sync silently reverted
    the edit.
    """
    path = swkb_env["note_file"]
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "title: A core-type note with an undeclared key",
            'title: "2026-01-15"',
        ),
        encoding="utf-8",
    )

    swkb_env["service"].update_entry("sample-note", "swkb", title="2026-01-15T00:00:00Z")

    assert "2026-01-15T00:00:00Z" in path.read_text(encoding="utf-8"), (
        "the title edit was swallowed -- the write path kept the old node"
    )


def test_a_date_edit_through_update_entry_reaches_the_file(swkb_env):
    """#173 review: editing an ADR's `date` to the SAME instant, in a fuller
    representation, must reach disk.

    `date: 2025-06-01` loads as midnight UTC and the edit supplies the same
    instant as a datetime. The unscoped same-instant check kept the old node
    -- the CLI reported success, the file never changed and the index then
    flipped back to the file's value. Scoped to the timestamp keys, `date` is
    written like any other changed field.
    """
    path = swkb_env["note_file"].parent / "sample-adr-with-bare-date.md"
    path.write_text(ADR_WITH_BARE_DATE, encoding="utf-8")

    swkb_env["service"].update_entry(
        "sample-adr-with-bare-date", "swkb", date=datetime(2025, 6, 1, tzinfo=UTC)
    )

    after = path.read_text(encoding="utf-8")
    assert "\ndate: 2025-06-01\n" not in after, (
        "the date edit was swallowed -- the write path kept the bare-date node"
    )
    assert "date: 2025-06-01 00:00:00+00:00" in after


UNPARSEABLE_CREATED_AT_NODES = {
    "null": "created_at:\n",
    "empty-string": "created_at: ''\n",
    "non-iso-string": "created_at: Jan 15 2026\n",
}


@pytest.mark.parametrize("shape", sorted(UNPARSEABLE_CREATED_AT_NODES))
def test_a_created_at_that_does_not_parse_is_kept_verbatim(tmp_path, shape):
    """#173 review: `created_at:` null / `''` / `Jan 15 2026` survive a no-op
    round trip byte for byte.

    `parse_datetime` falls back to "now" for all three, and the write path
    emitted that fallback -- replacing a value the file already had with a
    wrong one, which is worse than the drop this branch fixes. The rule is: a
    source node that did not parse is kept verbatim.
    """
    from pyrite.storage.repository import KBRepository

    kb_path = tmp_path / f"unparseable-created-at-{shape}"
    kb_path.mkdir(parents=True)
    path = kb_path / "created-at.md"
    text = (
        "---\n"
        "id: unparseable-created-at\n"
        "type: note\n"
        "title: A created_at the loader cannot parse\n"
        f"{UNPARSEABLE_CREATED_AT_NODES[shape]}"
        "---\n"
        "\n"
        "Body.\n"
    )
    path.write_text(text, encoding="utf-8")
    repo = KBRepository(KBConfig(name=f"unparseable-created-at-{shape}", path=kb_path))

    entry = repo.load_entry_from_file(path)
    assert entry.created_at is not None  # the in-memory fallback still happens
    out = repo.save(entry)

    assert out.read_text(encoding="utf-8") == text, (
        "the write path replaced an unparseable created_at node with its parse fallback"
    )


def test_an_explicit_created_at_assignment_still_reaches_the_file(tmp_path):
    """The escape hatch for the verbatim rule: an edit is not a no-op.

    A key kept verbatim must still be writable -- assigning `created_at`
    clears the verbatim flag, exactly as an assignment clears
    `_absent_default_keys`.
    """
    from pyrite.storage.repository import KBRepository

    kb_path = tmp_path / "unparseable-created-at-edit"
    kb_path.mkdir(parents=True)
    path = kb_path / "created-at.md"
    path.write_text(
        "---\n"
        "id: unparseable-created-at\n"
        "type: note\n"
        "title: A created_at the loader cannot parse\n"
        "created_at:\n"
        "---\n"
        "\n"
        "Body.\n",
        encoding="utf-8",
    )
    repo = KBRepository(KBConfig(name="unparseable-created-at-edit", path=kb_path))

    entry = repo.load_entry_from_file(path)
    entry.created_at = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
    out = repo.save(entry)

    assert "2030-01-02 03:04:05+00:00" in out.read_text(encoding="utf-8"), (
        "an explicit created_at assignment was swallowed by the verbatim rule"
    )


def test_repository_save_preserves_body_line_matching_frontmatter_key(tmp_path):
    """A body line can look like frontmatter without being duplicate metadata."""
    from pyrite.storage.repository import KBRepository

    kb_path = tmp_path / "body-line"
    events_path = kb_path / "events"
    events_path.mkdir(parents=True)
    body = "title: An intentional first body line\nThis text must survive a save."
    path = events_path / "body-first-line.md"
    path.write_text(
        "---\nid: body-first-line\ntype: event\ntitle: Meeting\n---\n\n" + body + "\n",
        encoding="utf-8",
    )
    repo = KBRepository(KBConfig(name="body-line", path=kb_path))

    entry = repo.load_entry_from_file(path)
    saved_path = repo.save(entry, keep_filename=True)
    saved_entry = repo.load_entry_from_file(saved_path)

    assert saved_entry.body == body

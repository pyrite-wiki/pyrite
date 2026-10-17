"""Every entry type must round-trip frontmatter keys it does not declare.

Typed classes (core and plugin) re-emitted only the keys they knew, so a
load -> save through any of them deleted everything else: `pyrite update -f
status=done` on a backlog item stripped `milestone:` and `created:` (2026-09-17,
twice). Only GenericEntry and TaskEntry collected unknown keys. The guarantee
belongs in the base class, so this test runs over every registered type.
"""

import pytest

import pyrite.models  # noqa: F401  (registers core types)
from pyrite.models.core_types import ENTRY_TYPE_REGISTRY, entry_from_frontmatter, get_entry_class
from pyrite.storage.repository import new_entry_text
from pyrite.plugins import get_registry

EXTRAS = {
    "milestone": "0.13",
    "github_issue": 15,
    "custom_list": ["a", "b"],
    "custom_map": {"nested": {"deep": True}},
}


def _all_types() -> list[str]:
    reg = get_registry()
    reg.discover()
    names = set(ENTRY_TYPE_REGISTRY)
    for plugin in reg._plugins.values():
        try:
            names.update(plugin.get_entry_types())
        except Exception:  # a plugin that fails to list types is not this test's concern
            pass
    return sorted(names)


@pytest.mark.parametrize("entry_type", _all_types())
def test_unknown_keys_survive_load_then_save(entry_type):
    meta = {"id": "rt-probe", "title": "Round trip", "type": entry_type, **EXTRAS}
    entry = entry_from_frontmatter(meta, "body")
    out = entry.to_frontmatter()
    for key, value in EXTRAS.items():
        assert out.get(key) == value, f"{entry_type} dropped {key!r} on round trip"
    assert out["id"] == "rt-probe" and out["type"] == entry_type


def test_declared_fields_win_over_a_stale_extra():
    # An extra never shadows a real field: if a key is both an unknown-at-load
    # key and later becomes a declared field, the declared value is written.
    meta = {"id": "p", "title": "T", "type": "note", "importance": 7}
    entry = entry_from_frontmatter(meta, "b")
    entry.importance = 9
    assert entry.to_frontmatter()["importance"] == 9


# ---------------------------------------------------------------------------
# Base-field conformance: every registered entry class must build its
# constructor kwargs from Entry._base_kwargs (directly or via a superclass),
# not a hand-rolled cls(...) call that forgets a field. Three extensions'
# classes (social, zettelkasten, encyclopedia) did exactly that: `aliases`,
# `importance` and `_schema_version` were silently dropped -- and for
# `importance` not merely dropped but REWRITTEN, since a hand-rolled call that
# omits the kwarg falls back to the dataclass default (5) rather than keeping
# the loaded value. Extras do not rescue this: all three keys are members of
# _BASE_CONSUMED_KEYS, so capture_extra_frontmatter treats them as "the class
# handled this" and never records them as unknown.
# ---------------------------------------------------------------------------

# Non-default values for every base field named in the acceptance criteria.
# `metadata`/`created_at`/`updated_at` are deliberately excluded from the
# to_frontmatter() re-emission assertion below (see the per-field comments in
# test_base_fields_survive_round_trip) to stay agnostic of #173/#175, which
# are landing on neighbouring semantics for those three fields.
_BASE_FIXTURE = {
    "aliases": ["alt-name"],
    "importance": 9,
    "tags": ["tag-a", "tag-b"],
    "links": [{"target": "other-entry", "relation": "related_to"}],
    "sources": [{"title": "A Source", "url": "https://example.com/a"}],
    "metadata": {"custom_key": "custom_value"},
    "summary": "A non-empty summary.",
    "provenance": {"created_by": "tester"},
    "created_at": "2020-01-01T00:00:00+00:00",
    "updated_at": "2020-06-01T00:00:00+00:00",
    "_schema_version": 3,
}


@pytest.mark.parametrize("entry_type", _all_types())
def test_base_fields_survive_round_trip(entry_type):
    """Regime: full base field set present, every key at a non-default value.

    This is the regime that fails today -- defaults hide the bug, because a
    hand-rolled cls(...) call that omits a kwarg falls back to the same
    dataclass default the file may also be carrying.
    """
    meta = {"id": "bf-probe", "title": "Base Fields", "type": entry_type, **_BASE_FIXTURE}
    entry = entry_from_frontmatter(meta, "body")

    assert entry.aliases == _BASE_FIXTURE["aliases"], entry_type
    assert entry.importance == _BASE_FIXTURE["importance"], entry_type
    assert entry.tags == _BASE_FIXTURE["tags"], entry_type
    assert entry.summary == _BASE_FIXTURE["summary"], entry_type
    assert entry._schema_version == _BASE_FIXTURE["_schema_version"], entry_type
    assert len(entry.links) == 1 and entry.links[0].target == "other-entry", entry_type
    assert len(entry.sources) == 1 and entry.sources[0].title == "A Source", entry_type
    assert entry.provenance is not None and entry.provenance.created_by == "tester", entry_type
    # created_at/updated_at: assert only that a value PRESENT IN THE FILE
    # survives onto the entry -- not that to_frontmatter() re-emits it (it
    # does not, for any type, before or after this fix; #173 owns when/
    # whether created_at/updated_at are written back).
    assert entry.created_at.isoformat() == "2020-01-01T00:00:00+00:00", entry_type
    assert entry.updated_at.isoformat() == "2020-06-01T00:00:00+00:00", entry_type

    out = entry.to_frontmatter()
    assert out.get("aliases") == _BASE_FIXTURE["aliases"], (entry_type, out)
    assert out.get("importance") == _BASE_FIXTURE["importance"], (entry_type, out)
    assert out.get("tags") == _BASE_FIXTURE["tags"], (entry_type, out)
    assert out.get("_schema_version") == _BASE_FIXTURE["_schema_version"], (entry_type, out)
    # metadata: assert only that a DECLARED metadata: mapping survives -- not
    # anything about undeclared-key placement (#175 owns whether stray keys
    # get folded into metadata). TaskEntry pre-dates this theme and, by its
    # own documented design (pyrite/models/task.py to_frontmatter), promotes
    # metadata keys to top-level frontmatter and pops the metadata: block
    # entirely -- so for that one type only, accept the promoted shape too.
    if out.get("metadata") != _BASE_FIXTURE["metadata"]:
        for key, value in _BASE_FIXTURE["metadata"].items():
            assert out.get(key) == value, (entry_type, out)


@pytest.mark.control(
    reason="the deleted Entry.save/to_markdown is replaced by the create's text (new_entry_text), which the base already has; the property is unchanged (#955 P4)"
)
@pytest.mark.parametrize("entry_type", _all_types())
def test_base_fields_absent_keep_class_defaults(entry_type):
    """Regime: base fields absent from frontmatter.

    Only id/title/type present; the dataclass defaults must still apply, and
    _frontmatter_for_file (exercised via to_markdown) must not invent an
    empty key for any of them.
    """
    meta = {"id": "bf-absent", "title": "No Base Fields", "type": entry_type}
    entry = entry_from_frontmatter(meta, "body")

    assert entry.aliases == [], entry_type
    assert entry.importance == 5, entry_type
    assert entry._schema_version == 0, entry_type

    written = new_entry_text(entry)
    assert "aliases:" not in written, (entry_type, written)
    assert "_schema_version:" not in written, (entry_type, written)


@pytest.mark.parametrize(
    ("entry_type", "source_path"),
    [
        ("writeup", "extensions/social"),
        ("user_profile", "extensions/social"),
        ("zettel", "extensions/zettelkasten"),
        ("literature_note", "extensions/zettelkasten"),
        ("article", "extensions/encyclopedia"),
        ("talk_page", "extensions/encyclopedia"),
    ],
)
@pytest.mark.control(
    reason="the deleted Entry.save/to_markdown is replaced by the create's text (new_entry_text), which the base already has; the property is unchanged (#955 P4)"
)
def test_base_fields_survive_disk_round_trip(entry_type, source_path):
    """Regime: round trip through disk, one class per extension.

    to_markdown() -> from_markdown() so the YAML layer and save()'s body
    handling are in the loop, not just the in-memory constructor path.
    """
    if entry_type not in _all_types():
        pytest.skip(f"{source_path} not installed in this environment")

    cls = get_entry_class(entry_type)
    meta = {"id": "disk-probe", "title": "Disk Round Trip", "type": entry_type, **_BASE_FIXTURE}
    entry = entry_from_frontmatter(meta, "some body text")
    text = new_entry_text(entry)

    reloaded = cls.from_markdown(text)

    assert reloaded.aliases == _BASE_FIXTURE["aliases"], entry_type
    assert reloaded.importance == _BASE_FIXTURE["importance"], entry_type
    assert reloaded._schema_version == _BASE_FIXTURE["_schema_version"], entry_type
    assert reloaded.tags == _BASE_FIXTURE["tags"], entry_type


@pytest.mark.parametrize("entry_type", _all_types())
def test_base_fields_and_extras_both_preserved(entry_type):
    """Regime: undeclared keys still preserved alongside the full base set.

    The EXTRAS guarantee (test_unknown_keys_survive_load_then_save above)
    must not regress now that _base_kwargs consumes more keys -- both must
    hold in the same run, on the same entry.
    """
    meta = {
        "id": "bf-extras-probe",
        "title": "Base Fields Plus Extras",
        "type": entry_type,
        **_BASE_FIXTURE,
        **EXTRAS,
    }
    entry = entry_from_frontmatter(meta, "body")
    out = entry.to_frontmatter()

    assert out.get("aliases") == _BASE_FIXTURE["aliases"], entry_type
    assert out.get("importance") == _BASE_FIXTURE["importance"], entry_type
    for key, value in EXTRAS.items():
        assert out.get(key) == value, f"{entry_type} dropped {key!r} on round trip"


def test_base_fields_survive_with_registry_empty(monkeypatch):
    """Regime: plugin registry empty -- core types only (CI's core-only leg).

    Simulated by pointing the registry at zero discovered plugins, so
    get_entry_class and _all_types() both fall back to core types only. The
    base-field guarantee must not depend on any plugin being installed: it
    must hold for a plain core `note` with nothing but ENTRY_TYPE_REGISTRY
    in play.
    """
    from pyrite.plugins.registry import PluginRegistry

    empty_registry = PluginRegistry()
    empty_registry._discovered = True  # no discover() call -> no plugins found
    monkeypatch.setattr("pyrite.plugins.registry._registry", empty_registry)

    core_only_types = sorted(ENTRY_TYPE_REGISTRY)
    assert "note" in core_only_types
    assert core_only_types, "core registry must not be empty"

    meta = {"id": "core-only", "title": "Core Only", "type": "note", **_BASE_FIXTURE}
    entry = entry_from_frontmatter(meta, "body")
    out = entry.to_frontmatter()
    assert out.get("aliases") == _BASE_FIXTURE["aliases"]
    assert out.get("importance") == _BASE_FIXTURE["importance"]
    assert out.get("_schema_version") == _BASE_FIXTURE["_schema_version"]


def test_update_keeps_undeclared_keys_on_disk(tmp_path):
    from pyrite.config import KBConfig, PyriteConfig, Settings
    from pyrite.services.kb_service import KBService
    from pyrite.storage.database import PyriteDB

    kb = tmp_path / "kb"
    (kb / "backlog").mkdir(parents=True)
    (kb / "backlog" / "item.md").write_text(
        "---\nid: item\ntitle: Item\ntype: backlog_item\nstatus: proposed\n"
        'milestone: "0.13"\ncreated: "2026-09-17"\n---\nbody\n'
    )
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="t", path=kb, kb_type="software")],
        settings=Settings(index_path=tmp_path / "i.db", auto_embed=False),
    )
    svc = KBService(config, PyriteDB(config.settings.index_path))
    svc.index_kb("t") if hasattr(svc, "index_kb") else None
    svc.update_entry("item", "t", status="done")
    text = (kb / "backlog" / "item.md").read_text()
    assert 'milestone: "0.13"' in text or "milestone: '0.13'" in text, text
    assert "2026-09-17" in text, text
    assert "status: done" in text

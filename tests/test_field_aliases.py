"""#697 -- a type's field aliases are schema data, resolved in one place.

ADR-0045 decision 8: a type's schema names each alias and its target
(``participants: actors``); the platform resolves an alias. ``field_aliases``
is that one place. These tests pin:

* the resolver's answers for the in-tree types (core schema + plugin metadata);
* the ``kb.yaml`` spelling ``field_aliases: {participants: actors}``;
* parity: the declared target is what the class actually reads;
* conformance: a class cannot keep an alias the schema does not name a
  target for (the check ADR-0045 calls "every alias names a target");
* ``pyrite schema validate`` reports a malformed declaration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from pyrite.models.base import Entry
from pyrite.models.core_types import ENTRY_TYPE_REGISTRY, EventEntry, RelationshipEntry
from pyrite.plugins import get_registry
from pyrite.plugins.registry import PluginRegistry
from pyrite.schema.field_aliases import (
    field_aliases,
    field_aliases_findings,
    undeclared_class_aliases,
)
from pyrite.schema.kb_schema import KBSchema


def _all_entry_classes() -> dict[str, type[Entry]]:
    registry = get_registry()
    registry.discover()
    classes = dict(ENTRY_TYPE_REGISTRY)
    classes.update(registry.get_all_entry_types())
    return classes


class TestResolverAnswers:
    def test_core_event_and_relationship(self):
        assert field_aliases("event") == {"participants": "actors"}
        assert field_aliases("relationship") == {
            "source": "source_entity",
            "target": "target_entity",
        }

    def test_a_type_without_aliases_has_none(self):
        assert field_aliases("note") == {}
        assert field_aliases("no_such_type") == {}

    def test_cascade_and_journalism_event_types(self):
        assert field_aliases("cascade_event") == {
            "event_date": "date",
            "participants": "actors",
        }
        for t in (
            "timeline_event",
            "solidarity_event",
            "investigation_event",
            "transaction",
            "legal_action",
        ):
            assert field_aliases(t) == {"participants": "actors"}, t

    def test_answer_is_a_copy(self):
        field_aliases("event")["x"] = "y"
        assert field_aliases("event") == {"participants": "actors"}


class TestKbYaml:
    def test_kb_yaml_type_declares_the_map(self):
        schema = KBSchema.from_dict(
            {"types": {"lead": {"fields": {}, "field_aliases": {"participants": "actors"}}}}
        )
        assert schema.types["lead"].field_aliases == {"participants": "actors"}
        assert field_aliases("lead", schema) == {"participants": "actors"}

    def test_kb_yaml_wins_per_key_over_core(self):
        schema = KBSchema.from_dict(
            {"types": {"event": {"field_aliases": {"participants": "attendees", "who": "actors"}}}}
        )
        assert field_aliases("event", schema) == {"participants": "attendees", "who": "actors"}

    def test_kb_with_no_types(self):
        assert field_aliases("event", KBSchema.from_dict({})) == {"participants": "actors"}

    @pytest.mark.parametrize("bad", ["participants", ["a"], 3, {"a": 5}, {"a": ""}, {1: "b"}])
    def test_malformed_map_never_crashes_load_or_resolve(self, bad):
        schema = KBSchema.from_dict({"types": {"lead": {"field_aliases": bad}}})
        assert field_aliases("lead", schema) == {}
        findings = field_aliases_findings(schema)
        assert findings and all("lead" in f["message"] for f in findings)


class TestPluginLayer:
    def _registry_with(self, meta_fn):
        class P:
            name = "p"

            def get_type_metadata(self):
                return meta_fn()

        reg = PluginRegistry()
        reg._discovered = True
        reg.register(P())
        return reg

    def test_registry_keeps_field_aliases_from_plugin_metadata(self):
        reg = self._registry_with(lambda: {"widget": {"field_aliases": {"old": "new"}}})
        assert reg.get_all_type_metadata()["widget"]["field_aliases"] == {"old": "new"}

    def test_registry_no_longer_drops_the_other_resolved_keys(self):
        reg = self._registry_with(
            lambda: {"widget": {"protocols": ["temporal"], "guidelines": "g", "goals": "x"}}
        )
        meta = reg.get_all_type_metadata()["widget"]
        assert meta["protocols"] == ["temporal"]
        assert meta["guidelines"] == "g"
        assert meta["goals"] == "x"

    def test_registry_copies_list_values(self):
        source = ["temporal"]
        reg = self._registry_with(lambda: {"widget": {"protocols": source}})
        reg.get_all_type_metadata()["widget"]["protocols"].append("x")
        assert source == ["temporal"]

    def test_two_plugins_disagreeing_on_a_keys_shape_keep_the_first_and_warn(self, caplog):
        class A:
            name = "a"

            def get_type_metadata(self):
                return {"widget": {"goals": "first", "field_aliases": {"old": "new"}}}

        class B:
            name = "b"

            def get_type_metadata(self):
                return {"widget": {"goals": {"not": "a string"}, "protocols": ["temporal"]}}

        reg = PluginRegistry()
        reg._discovered = True
        reg.register(A())
        reg.register(B())
        with caplog.at_level(logging.WARNING, logger="pyrite.plugins.registry"):
            meta = reg.get_all_type_metadata()["widget"]
        assert meta["goals"] == "first"
        assert meta["protocols"] == ["temporal"]  # the second plugin's other keys survive
        assert meta["field_aliases"] == {"old": "new"}
        assert any("widget.goals" in r.getMessage() for r in caplog.records)

    def test_plugin_alias_resolves_and_kb_yaml_overrides_it(self, monkeypatch):
        reg = self._registry_with(lambda: {"widget": {"field_aliases": {"old": "new"}}})
        monkeypatch.setattr("pyrite.plugins.get_registry", lambda: reg)
        assert field_aliases("widget") == {"old": "new"}
        schema = KBSchema.from_dict({"types": {"widget": {"field_aliases": {"old": "newer"}}}})
        assert field_aliases("widget", schema) == {"old": "newer"}

    def test_a_plugin_whose_metadata_raises_is_skipped(self, monkeypatch):
        def boom():
            raise RuntimeError("nope")

        reg = self._registry_with(boom)
        monkeypatch.setattr("pyrite.plugins.get_registry", lambda: reg)
        assert field_aliases("event") == {"participants": "actors"}

    def test_no_plugins_installed_core_layer_only(self, monkeypatch):
        reg = PluginRegistry()
        reg._discovered = True
        monkeypatch.setattr("pyrite.plugins.get_registry", lambda: reg)
        assert field_aliases("event") == {"participants": "actors"}
        assert field_aliases("cascade_event") == {}


def _parity_cases():
    classes = _all_entry_classes()
    cases = []
    for entry_type, cls in sorted(classes.items()):
        for alias, target in sorted(field_aliases(entry_type).items()):
            cases.append(pytest.param(cls, entry_type, alias, target, id=f"{entry_type}.{alias}"))
    return cases


class TestParity:
    """The declared target is what the class reads (the groom's riskiest assumption)."""

    def test_the_cases_are_not_empty(self):
        assert len(_parity_cases()) >= 9

    @pytest.mark.parametrize(("cls", "entry_type", "alias", "target"), _parity_cases())
    def test_alias_and_target_load_the_same(self, cls, entry_type, alias, target):
        value = "2024-01-02" if target == "date" else "x" if "entity" in target else ["x"]

        def load(key):
            meta = {"id": "i", "title": "T", "type": entry_type, key: value}
            return cls.from_frontmatter(meta, "").to_frontmatter()

        via_alias, via_target = load(alias), load(target)
        assert via_alias == via_target
        # Both empty would also be "equal": the value must reach the file shape.
        assert "x" in str(via_target) or "2024-01-02" in str(via_target)


# Third-party shapes: a subclass of a class that had aliases, with its own
# `entry_type` and no schema declaration. Before #697 it inherited
# FRONTMATTER_ALIASES; it must read the alias the same way now.
@dataclass
class Meeting(EventEntry):
    @property
    def entry_type(self) -> str:
        return "meeting"


@dataclass
class Standup(Meeting):
    @property
    def entry_type(self) -> str:
        return "standup"


@dataclass
class Bond(RelationshipEntry):
    @property
    def entry_type(self) -> str:
        return "bond"


#: Dev's aliases, class by class, as literals: the names are the
#: `FRONTMATTER_ALIASES` declared on dev (c93387b7) -- on `EventEntry` and
#: `RelationshipEntry`, re-declared on six extension classes, inherited by a
#: subclass -- and each target is what that class's `from_frontmatter` reads.
#: Not derived from `field_aliases` or `_type_names_of`: a dropped inherited
#: alias must fail here, not shrink its own expectation.
LEGACY_ALIASES: dict[str, dict[str, str]] = {
    "event": {"participants": "actors"},
    "relationship": {"source": "source_entity", "target": "target_entity"},
    "cascade_event": {"event_date": "date", "participants": "actors"},
    "timeline_event": {"participants": "actors"},
    "solidarity_event": {"participants": "actors"},
    "investigation_event": {"participants": "actors"},
    "transaction": {"participants": "actors"},
    "legal_action": {"participants": "actors"},
    # third-party shapes (the fixtures above): inherited, no declaration of their own
    "meeting": {"participants": "actors"},
    "standup": {"participants": "actors"},
    "bond": {"source": "source_entity", "target": "target_entity"},
}


def _class_for(entry_type: str) -> type[Entry]:
    fixtures = {"meeting": Meeting, "standup": Standup, "bond": Bond}
    return fixtures.get(entry_type) or _all_entry_classes()[entry_type]


def _load_path_cases():
    return [
        pytest.param(_class_for(t), t, aliases, id=t)
        for t, aliases in sorted(LEGACY_ALIASES.items())
    ]


class TestLoadPathIsUnchanged:
    """Through from_markdown -> to_markdown (which runs capture_extra_frontmatter).

    A file that loaded and wrote one way before #697 loads and writes the same
    way after, for every class that had an alias: the alias key is consumed
    (not an extra, not "unrepresented"), the file comes back respelled with the
    target key once, and an unrelated key survives. The table above is dev's;
    `test_files_match_what_dev_wrote` pins four whole files, captured by running
    the same load -> write on dev (c93387b7) before the change.
    """

    def test_every_registered_aliased_class_is_in_the_table(self):
        # A class that is an EventEntry/RelationshipEntry, or whose type has
        # aliases now, needs a row: adding one without a load-path case fails.
        unlisted = sorted(
            t
            for t, cls in _all_entry_classes().items()
            if (issubclass(cls, (EventEntry, RelationshipEntry)) or field_aliases(t))
            and t not in LEGACY_ALIASES
        )
        assert unlisted == []

    @pytest.mark.parametrize(("cls", "entry_type", "aliases"), _load_path_cases())
    def test_the_resolved_aliases_are_dev_s(self, cls, entry_type, aliases):
        from pyrite.schema.field_aliases import class_field_aliases

        assert class_field_aliases(cls, entry_type) == aliases

    @pytest.mark.parametrize(("cls", "entry_type", "aliases"), _load_path_cases())
    def test_alias_key_is_consumed_and_the_file_respelled_once(self, cls, entry_type, aliases):
        assert aliases, f"{entry_type} has no aliases: the case does not belong here"
        for alias, target in aliases.items():
            value = {"date": "2024-01-02"}.get(target, "x" if "entity" in target else "[a, b]")
            text = (
                f"---\nid: x\ntitle: T\ntype: {entry_type}\n{alias}: {value}\nother: 1\n---\nbody\n"
            )
            entry = cls.from_markdown(text)
            assert alias not in (entry.extra_frontmatter or {}), alias
            assert alias not in (entry._unrepresented_keys or {}), alias
            assert (entry.extra_frontmatter or {}).get("other") == 1
            written = entry.to_markdown()
            keys = [line.split(":")[0] for line in written.splitlines() if ":" in line]
            assert alias not in keys, written
            assert keys.count(target) == 1, written
            assert "&id" not in written and "*id" not in written, written

    @pytest.mark.parametrize(
        ("entry_type", "front", "written"),
        [
            (
                "event",
                "participants: [a, b]\nevent_date: 2024-01-02\n",
                "event_date: 2024-01-02\nother: 1\nactors: [a, b]\n",
            ),
            (
                "meeting",
                "participants: [a, b]\nevent_date: 2024-01-02\n",
                "event_date: 2024-01-02\nother: 1\nactors: [a, b]\n",
            ),
            (
                "relationship",
                "source: a\ntarget: b\n",
                "other: 1\nsource_entity: a\ntarget_entity: b\n",
            ),
            (
                "cascade_event",
                "participants: [a, b]\nevent_date: 2024-01-02\n",
                "other: 1\ndate: '2024-01-02'\nactors: [a, b]\n",
            ),
        ],
    )
    def test_files_match_what_dev_wrote(self, entry_type, front, written):
        cls = _class_for(entry_type)
        source = f"---\nid: x\ntitle: T\ntype: {entry_type}\n{front}other: 1\n---\nbody\n"
        expected = f"---\nid: x\ntitle: T\ntype: {entry_type}\n{written}---\n\nbody\n"
        assert cls.from_markdown(source).to_markdown() == expected

    def test_event_date_stays_an_extra_where_it_was_never_an_alias(self):
        # dev: only cascade_event aliased event_date; on `event` it is an unknown key.
        entry = EventEntry.from_markdown(
            "---\nid: x\ntitle: T\ntype: event\nevent_date: 2024-01-02\n---\n"
        )
        assert "event_date" in entry.extra_frontmatter

    def test_a_subclass_with_its_own_entry_type_inherits_its_base_types_aliases(self):
        entry = Meeting.from_markdown(
            "---\nid: m\ntitle: M\ntype: meeting\nparticipants: [a, b]\n---\n"
        )
        assert not entry.extra_frontmatter
        assert "participants" not in entry.to_markdown()
        assert entry.participants == ["a", "b"]


class TestCliKeepsListTyping:
    def test_create_f_participants_and_actors_both_parse_as_lists(self):
        from pyrite.cli.entry_commands import _field_type_for, _parse_field_value
        from pyrite.config import PyriteConfig, Settings

        config = PyriteConfig(knowledge_bases=[], settings=Settings())
        for name in ("participants", "actors"):
            field_type = _field_type_for(config, "none", "event", name)
            assert _parse_field_value("a,b", field_type) == ["a", "b"], name


class TestConformance:
    def test_every_registered_class_alias_names_a_target(self):
        offenders = {
            entry_type: sorted(undeclared_class_aliases(cls, entry_type))
            for entry_type, cls in _all_entry_classes().items()
            if undeclared_class_aliases(cls, entry_type)
        }
        assert offenders == {}

    def test_a_class_alias_with_no_declaration_fails_the_check(self):
        @dataclass
        class Fixture(Entry):
            FRONTMATTER_ALIASES = frozenset({"foo"})

            @property
            def entry_type(self) -> str:
                return "fixture_type_without_declaration"

        assert undeclared_class_aliases(Fixture, "fixture_type_without_declaration") == {"foo"}
        schema = KBSchema.from_dict(
            {"types": {"fixture_type_without_declaration": {"field_aliases": {"foo": "bar"}}}}
        )
        assert (
            undeclared_class_aliases(Fixture, "fixture_type_without_declaration", schema) == set()
        )

    def test_the_shim_warns_once_per_class_naming_the_alias(self, caplog):
        from pyrite.schema import field_aliases as mod

        @dataclass
        class Legacy(EventEntry):
            FRONTMATTER_ALIASES = frozenset({"foo"})

            @property
            def entry_type(self) -> str:
                return "legacy_shim_type"

        mod._WARNED_CLASSES.discard(Legacy)
        with caplog.at_level(logging.WARNING, logger="pyrite.schema.field_aliases"):
            e = Legacy(id="i", title="t")
            assert "foo" in mod.entry_alias_names(e)
            mod.entry_alias_names(e)
        warnings = [r for r in caplog.records if "foo" in r.getMessage()]
        assert len(warnings) == 1
        assert "Legacy" in warnings[0].getMessage()

    def test_in_tree_classes_declare_no_class_attribute(self):
        # The data moved to the schema; a class that still keeps the attribute
        # is the shim path and would warn on every load.
        still = {t for t, c in _all_entry_classes().items() if c.FRONTMATTER_ALIASES}
        assert still == set()


class TestValidateFindings:
    def _findings(self, aliases, fields=None):
        schema = KBSchema.from_dict(
            {"types": {"lead": {"fields": fields or {}, "field_aliases": aliases}}}
        )
        return field_aliases_findings(schema)

    def test_clean_declaration_has_no_findings(self):
        assert self._findings({"participants": "actors"}, fields={"actors": {"type": "list"}}) == []

    def test_target_that_is_not_a_declared_field(self):
        (f,) = self._findings({"participants": "actors"})
        assert "not a declared field" in f["message"] and "lead" in f["message"]

    def test_core_field_list_names_file_keys_not_old_spellings(self):
        from pyrite.schema.core_types import CORE_TYPES

        for type_name in ("event", "relationship"):
            declared = set(CORE_TYPES[type_name]["fields"])
            for alias, target in field_aliases(type_name).items():
                assert alias not in declared, (type_name, alias)
                assert target in declared, (type_name, target)

    def test_a_target_the_class_reads_counts_as_declared(self):
        # `actors` is not a dataclass field of InvestigationEventEntry (the
        # attribute is `participants`) but the plugin names it as an alias target.
        schema = KBSchema.from_dict(
            {"types": {"investigation_event": {"field_aliases": {"who": "actors"}}}}
        )
        assert field_aliases_findings(schema) == []

    def test_a_dataclass_field_counts_as_declared(self):
        schema = KBSchema.from_dict({"types": {"event": {"field_aliases": {"when": "date"}}}})
        assert field_aliases_findings(schema) == []

    def test_a_kb_yaml_override_of_a_core_type_is_clean(self):
        schema = KBSchema.from_dict(
            {"types": {"event": {"field_aliases": {"participants": "actors"}}}}
        )
        assert field_aliases_findings(schema) == []

    def test_empty_target(self):
        (f,) = self._findings({"a": ""})
        assert "lead" in f["message"] and "'a'" in f["message"]

    def test_alias_equal_to_target(self):
        (f,) = self._findings({"a": "a"})
        assert "lead" in f["message"]

    def test_chain(self):
        findings = self._findings({"a": "b", "b": "c"})
        assert any("chain" in f["message"] for f in findings)

    def test_reserved_alias(self):
        findings = self._findings({"title": "name"})
        assert any("reserved" in f["message"] for f in findings)

    def test_reserved_target(self):
        findings = self._findings({"a": "title"})
        assert any("reserved" in f["message"] for f in findings)

    def test_alias_that_is_a_declared_field(self):
        findings = self._findings({"a": "b"}, fields={"a": {"type": "text"}})
        assert any("also a declared field" in f["message"] for f in findings)

    def test_core_event_declaration_is_clean(self):
        # `participants` is a declared core field of `event`; the core layer is
        # not a kb.yaml declaration, so only the operator's own fields count.
        assert field_aliases_findings(KBSchema.from_dict({})) == []

    def test_schema_validate_reports_it(self, tmp_path):
        from unittest.mock import patch

        from typer.testing import CliRunner

        from pyrite.cli import app
        from pyrite.config import KBConfig, PyriteConfig, Settings

        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "kb.yaml").write_text(
            "name: t\nkb_type: generic\ntypes:\n  lead:\n    field_aliases:\n      a: a\n"
        )
        entry = kb / "x.md"
        entry.write_text("---\nid: x\ntitle: X\ntype: note\n---\nbody\n")
        config = PyriteConfig(knowledge_bases=[], settings=Settings(index_path=tmp_path / "i.db"))
        config._db_kb_cache["t"] = KBConfig(name="t", path=kb, kb_type="generic")
        with patch("pyrite.config.load_config", return_value=config):
            result = CliRunner().invoke(app, ["schema", "validate", str(entry)])
        assert "lead" in result.output and "field_aliases" in result.output, result.output

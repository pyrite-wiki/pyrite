"""
Tests for entry models.
"""

import re
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from pyrite.exceptions import FrontmatterError
from pyrite.models import EventEntry
from pyrite.models.base import parse_datetime
from pyrite.models.core_types import (
    OrganizationEntry,
    PersonEntry,
    QAAssessmentEntry,
    RelationshipEntry,
)
from pyrite.models.generic import GenericEntry
from pyrite.schema import EventStatus, ResearchStatus


class TestEventEntry:
    """Tests for EventEntry model."""

    def test_create_event(self):
        """Test creating an event entry."""
        event = EventEntry.create(
            date="2025-01-20",
            title="Executive Orders Blitz",
            body="The administration signed 47 executive orders on day one.",
            importance=9,
            participants=["Stephen Miller", "Donald Trump"],
            tags=["executive-orders", "day-one"],
        )
        assert event.date == "2025-01-20"
        assert event.id == "2025-01-20--executive-orders-blitz"
        assert event.importance == 9

    def test_event_to_markdown(self):
        """Test converting event to markdown."""
        event = EventEntry(
            id="2025-01-06--minneapolis-raids",
            title="ICE Raids Minneapolis",
            date="2025-01-06",
            importance=8,
            body="Federal agents conducted sweeping raids.",
        )
        md = event.to_markdown()
        assert "---" in md
        assert "date: '2025-01-06'" in md or "date: 2025-01-06" in md
        assert "importance: 8" in md
        assert "Federal agents" in md

    def test_to_frontmatter_writes_actors_not_participants(self):
        """EventEntry must serialize the people/orgs field as `actors`, the
        cascade-timeline convention used by ~4,500 existing files, not
        `participants`. The internal property stays `participants`; only the
        emitted frontmatter key changes (event-write-actors-not-participants).
        """
        event = EventEntry(
            id="2025-01-06--raid",
            title="Raid",
            date="2025-01-06",
            participants=["Alice", "DOJ"],
        )
        fm = event.to_frontmatter()
        assert fm.get("actors") == ["Alice", "DOJ"]
        assert "participants" not in fm

    def test_actors_round_trips_under_actors_key(self):
        """A file written with `actors:` reads back and re-serializes as
        `actors:` — no silent rename on round-trip."""
        loaded = EventEntry.from_frontmatter(
            {"id": "x", "title": "T", "date": "2026-01-01", "actors": ["Bob", "FBI"]},
            "body",
        )
        assert loaded.participants == ["Bob", "FBI"]  # internal name unchanged
        assert loaded.to_frontmatter().get("actors") == ["Bob", "FBI"]

    def test_legacy_participants_key_still_read(self):
        """Existing files using the legacy `participants:` key must still load."""
        loaded = EventEntry.from_frontmatter(
            {"id": "y", "title": "T", "participants": ["Carol"]}, "body"
        )
        assert loaded.participants == ["Carol"]

    def test_event_from_markdown(self):
        """Test parsing event from markdown."""
        md = """---
id: 2025-01-20--test-event
date: '2025-01-20'
importance: 7
title: Test Event
status: confirmed
participants:
  - Person One
  - Person Two
tags:
  - test
  - example
---

This is the event body.
"""
        event = EventEntry.from_markdown(md)
        assert event.id == "2025-01-20--test-event"
        assert event.date == "2025-01-20"
        assert event.importance == 7
        assert event.status == EventStatus.CONFIRMED
        assert len(event.participants) == 2
        assert "This is the event body" in event.body

    def test_from_markdown_missing_frontmatter_raises_frontmatter_error(self):
        """A file with no YAML frontmatter fence raises FrontmatterError rather
        than a bare ValueError, so the entry loader can handle it as a content
        problem instead of crashing."""
        with pytest.raises(FrontmatterError):
            EventEntry.from_markdown("Just body text, no frontmatter fence.\n")

    def test_from_markdown_rejects_body_triple_dash_as_frontmatter(self):
        """A file that lacks a frontmatter fence at the top but contains
        `---` dividers in the body must NOT have the body content parsed as
        YAML. Regression for Tier A 1030
        (bug-from-markdown-splits-on-body-triple-dash-parsing-prose-as-
        frontmatter-yaml). The conductor hit this on cascade-research
        drafts where '**Whipple Building**' in body prose parsed as the
        YAML alias '*Whipple' and triggered a ruamel ComposerError.
        """
        # The file starts with body text. The `---` on its own line is a
        # markdown thematic-break / divider, NOT a frontmatter delimiter.
        # Pre-fix, re.split caught these and parsed paragraph 2 as YAML.
        body_with_divider = (
            "Some introductory paragraph that happens to be the first line.\n"
            "\n"
            "---\n"
            "\n"
            "**Whipple Building**: a body-prose mention that pre-fix parsed\n"
            "as the YAML alias `*Whipple` and crashed ruamel.\n"
            "\n"
            "---\n"
        )
        with pytest.raises(FrontmatterError):
            EventEntry.from_markdown(body_with_divider)

    def test_from_markdown_does_not_treat_body_yaml_as_frontmatter(self):
        """The dangerous silent-corruption case: the body between two `---`
        dividers happens to be valid YAML. Pre-fix, re.split would parse
        it as frontmatter and build an entry from BODY metadata —
        silently corrupting the loader. Post-fix the file must be
        rejected as missing frontmatter."""
        # No fence at line 1. Body-between-dividers parses as valid YAML
        # (`foo: bar`) but is plain text in the source file.
        body_yaml_lookalike = (
            "Introductory paragraph.\n"
            "\n"
            "---\n"
            "foo: bar\n"
            "title: Body-yaml-not-frontmatter\n"
            "---\n"
            "\n"
            "More body.\n"
        )
        with pytest.raises(FrontmatterError):
            EventEntry.from_markdown(body_yaml_lookalike)

    def test_from_markdown_requires_opening_fence_at_line_one(self):
        """The opening `---` must be the first line that has text. Text before the
        fence means the file lacks a frontmatter block. Blank lines and a BOM before
        it are ignored, as Hugo ignores them (tests/test_frontmatter_splitter.py)."""
        leading_blank = "\n---\nid: x\ntitle: X\n---\n\nBody\n"
        assert EventEntry.from_markdown(leading_blank).title == "X"

        # Leading prose — fence not at top.
        leading_text = "preamble line\n---\nid: x\ntitle: X\n---\n\nBody\n"
        with pytest.raises(FrontmatterError):
            EventEntry.from_markdown(leading_text)

    def test_event_validation(self):
        """Test event validation."""
        # Valid event
        event = EventEntry(
            id="2025-01-20--valid", title="Valid Event", date="2025-01-20", importance=5
        )
        errors = event.validate()
        assert len(errors) == 0

        # Missing date
        bad_event = EventEntry(id="missing-date", title="Bad Event", date="", importance=5)
        errors = bad_event.validate()
        assert any("date" in e.lower() for e in errors)

        # Invalid importance
        bad_event2 = EventEntry(
            id="2025-01-20--bad", title="Bad Event", date="2025-01-20", importance=15
        )
        errors = bad_event2.validate()
        assert any("importance" in e.lower() for e in errors)

    def test_event_status_always_written_to_frontmatter(self):
        """Status should always appear in frontmatter, even when 'confirmed' (the default)."""
        event = EventEntry(
            id="2025-01-20--test",
            title="Test",
            date="2025-01-20",
            status=EventStatus.CONFIRMED,
        )
        fm = event.to_frontmatter()
        assert "status" in fm, "status=confirmed should still be written to frontmatter"
        assert fm["status"] == "confirmed"

    def test_event_draft_status_round_trip(self):
        """An event created with status='draft' should round-trip through frontmatter."""
        event = EventEntry.from_frontmatter(
            {
                "id": "2025-01-20--draft",
                "title": "Draft Event",
                "date": "2025-01-20",
                "status": "draft",
            },
            body="",
        )
        assert event.status == EventStatus.DRAFT
        fm = event.to_frontmatter()
        assert fm["status"] == "draft"

    def test_event_status_all_values_round_trip(self):
        """Every EventStatus value should survive from_frontmatter -> to_frontmatter."""
        for es in EventStatus:
            event = EventEntry.from_frontmatter(
                {
                    "id": f"2025-01-20--{es.value}",
                    "title": es.value,
                    "date": "2025-01-20",
                    "status": es.value,
                },
                body="",
            )
            assert event.status == es
            fm = event.to_frontmatter()
            assert fm["status"] == es.value


class TestPersonEntry:
    """Tests for PersonEntry model."""

    def test_create_person(self):
        """Test creating a person entry."""
        name = "Stephen Miller"
        entry_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        actor = PersonEntry(
            id=entry_id,
            title=name,
            role="architect",
            importance=10,
            tags=["immigration", "schedule-f"],
        )
        assert actor.entry_type == "person"
        assert actor.title == "Stephen Miller"
        assert actor.role == "architect"
        assert actor.importance == 10

    def test_person_create_classmethod(self):
        """Test PersonEntry.create() class method."""
        actor = PersonEntry.create(
            name="Stephen Miller",
            role="architect",
            importance=10,
        )
        assert actor.entry_type == "person"
        assert actor.title == "Stephen Miller"
        assert actor.role == "architect"

    def test_person_to_markdown(self):
        """Test converting person entry to markdown."""
        entry = PersonEntry(
            id="miller-stephen",
            title="Stephen Miller",
            role="architect",
            importance=10,
            body="## Quick Facts\n- Primary immigration architect",
        )
        md = entry.to_markdown()
        assert "---" in md
        assert "type: person" in md
        assert "role: architect" in md
        assert "Quick Facts" in md

    def test_person_from_markdown(self):
        """Test parsing person entry from markdown."""
        md = """---
id: miller-stephen
title: Stephen Miller
type: person
role: architect
importance: 10
tags:
  - immigration
---

Key immigration policy architect.
"""
        entry = PersonEntry.from_markdown(md)
        assert entry.title == "Stephen Miller"
        assert entry.entry_type == "person"
        assert entry.role == "architect"
        assert entry.importance == 10
        assert "immigration policy architect" in entry.body

    def test_person_with_sources(self):
        """Test person entry with sources."""
        entry = PersonEntry(id="test-person", title="Test Person")
        entry.add_source(
            title="New York Times Article",
            url="https://nytimes.com/article",
            outlet="New York Times",
            verified=True,
        )
        assert len(entry.sources) == 1
        assert entry.sources[0].verified is True


class TestOrganizationEntry:
    """Tests for OrganizationEntry model."""

    def test_create_organization(self):
        """Test creating an organization entry."""
        org = OrganizationEntry.create(
            name="Heritage Foundation",
            founded="1973",
            jurisdiction="US",
        )
        assert org.entry_type == "organization"
        assert org.id == "heritage-foundation"
        assert org.founded == "1973"

    def test_organization_from_markdown(self):
        """Test parsing organization entry from markdown."""
        md = """---
title: "Heritage Foundation"
type: organization
importance: 9
founded: '1973'
jurisdiction: US
tags:
  - think-tank
  - project-2025
research_status: complete
---

## Overview

The Heritage Foundation is a conservative think tank...
"""
        entry = OrganizationEntry.from_markdown(md)
        assert entry.title == "Heritage Foundation"
        assert entry.entry_type == "organization"
        assert entry.importance == 9
        assert entry.founded == "1973"
        assert entry.research_status == ResearchStatus.COMPLETE
        assert "conservative think tank" in entry.body


class TestPersonResearchStatus:
    """Tests that PersonEntry always writes research_status to frontmatter."""

    def test_person_research_status_stub_always_written(self):
        """research_status=stub (the default) should still appear in frontmatter."""
        person = PersonEntry(
            id="test-person",
            title="Test Person",
            research_status=ResearchStatus.STUB,
        )
        fm = person.to_frontmatter()
        assert "research_status" in fm, "research_status=stub should still be written"
        assert fm["research_status"] == "stub"

    def test_person_research_status_all_values_round_trip(self):
        """Every ResearchStatus value should survive from_frontmatter -> to_frontmatter."""
        for rs in ResearchStatus:
            person = PersonEntry.from_frontmatter(
                {"id": f"test-{rs.value}", "title": rs.value, "research_status": rs.value},
                body="",
            )
            assert person.research_status == rs
            fm = person.to_frontmatter()
            assert fm["research_status"] == rs.value


class TestOrganizationResearchStatus:
    """Tests that OrganizationEntry always writes research_status to frontmatter."""

    def test_org_research_status_stub_always_written(self):
        """research_status=stub (the default) should still appear in frontmatter."""
        org = OrganizationEntry(
            id="test-org",
            title="Test Org",
            research_status=ResearchStatus.STUB,
        )
        fm = org.to_frontmatter()
        assert "research_status" in fm, "research_status=stub should still be written"
        assert fm["research_status"] == "stub"

    def test_org_research_status_all_values_round_trip(self):
        """Every ResearchStatus value should survive from_frontmatter -> to_frontmatter."""
        for rs in ResearchStatus:
            org = OrganizationEntry.from_frontmatter(
                {"id": f"test-{rs.value}", "title": rs.value, "research_status": rs.value},
                body="",
            )
            assert org.research_status == rs
            fm = org.to_frontmatter()
            assert fm["research_status"] == rs.value


class TestRelationshipEntry:
    """Tests that RelationshipEntry always writes identity fields."""

    def test_source_target_always_written_even_when_empty(self):
        """source_entity and target_entity should always appear in frontmatter."""
        rel = RelationshipEntry(
            id="test-rel",
            title="Test Relationship",
            source_entity="",
            target_entity="",
        )
        fm = rel.to_frontmatter()
        assert "source_entity" in fm, "source_entity='' should still be written"
        assert "target_entity" in fm, "target_entity='' should still be written"

    def test_source_target_round_trip(self):
        """source_entity and target_entity should survive round-trip."""
        rel = RelationshipEntry.from_frontmatter(
            {
                "id": "test-rel",
                "title": "Test",
                "source_entity": "person-a",
                "target_entity": "person-b",
            },
            body="",
        )
        fm = rel.to_frontmatter()
        assert fm["source_entity"] == "person-a"
        assert fm["target_entity"] == "person-b"

    def test_empty_source_target_round_trip(self):
        """Empty source_entity/target_entity should round-trip without being dropped."""
        rel = RelationshipEntry.from_frontmatter(
            {"id": "test-rel", "title": "Test", "source_entity": "", "target_entity": ""},
            body="",
        )
        fm = rel.to_frontmatter()
        assert "source_entity" in fm
        assert "target_entity" in fm


class TestQAAssessmentEntry:
    """Tests that QAAssessmentEntry always writes tier, qa_status, issues_found, issues_resolved."""

    def test_defaults_always_written(self):
        """Default values (tier=1, qa_status=pass, issues_found=0, issues_resolved=0) should appear."""
        qa = QAAssessmentEntry(
            id="test-qa",
            title="Test QA",
        )
        fm = qa.to_frontmatter()
        assert "tier" in fm, "tier=1 (default) should still be written"
        assert fm["tier"] == 1
        assert "qa_status" in fm, "qa_status=pass (default) should still be written"
        assert fm["qa_status"] == "pass"
        assert "issues_found" in fm, "issues_found=0 should still be written"
        assert fm["issues_found"] == 0
        assert "issues_resolved" in fm, "issues_resolved=0 should still be written"
        assert fm["issues_resolved"] == 0

    def test_tier_zero_written(self):
        """tier=0 should not be suppressed by a falsy guard."""
        qa = QAAssessmentEntry(id="test-qa", title="Test QA", tier=0)
        fm = qa.to_frontmatter()
        assert fm["tier"] == 0

    def test_qa_status_pass_round_trip(self):
        """qa_status=pass should survive from_frontmatter -> to_frontmatter."""
        qa = QAAssessmentEntry.from_frontmatter(
            {
                "id": "test-qa",
                "title": "Test QA",
                "qa_status": "pass",
                "tier": 1,
                "issues_found": 0,
                "issues_resolved": 0,
            },
            body="",
        )
        fm = qa.to_frontmatter()
        assert fm["qa_status"] == "pass"
        assert fm["tier"] == 1
        assert fm["issues_found"] == 0
        assert fm["issues_resolved"] == 0

    def test_nondefault_values_round_trip(self):
        """Non-default values should also round-trip correctly."""
        qa = QAAssessmentEntry.from_frontmatter(
            {
                "id": "test-qa",
                "title": "Test QA",
                "qa_status": "fail",
                "tier": 3,
                "issues_found": 5,
                "issues_resolved": 2,
            },
            body="",
        )
        fm = qa.to_frontmatter()
        assert fm["qa_status"] == "fail"
        assert fm["tier"] == 3
        assert fm["issues_found"] == 5
        assert fm["issues_resolved"] == 2


class TestEntryRoundtrip:
    """Test roundtrip serialization."""

    def test_event_roundtrip(self):
        """Test event save and load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test-event.md"

            original = EventEntry.create(
                date="2025-01-20",
                title="Test Event",
                body="Test body content.",
                importance=8,
                participants=["Actor One"],
                tags=["test"],
            )
            original.add_source(title="Source", url="https://example.com")

            original.save(path)
            loaded = EventEntry.load(path)

            assert loaded.id == original.id
            assert loaded.date == original.date
            assert loaded.importance == original.importance
            assert len(loaded.sources) == 1

    def test_person_roundtrip(self):
        """Test person entry save and load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test-person.md"

            original = PersonEntry.create(name="John Smith", role="operative", importance=6)
            original.body = "## Background\n\nJohn Smith is..."
            original.tags = ["test", "actor"]
            original.research_status = ResearchStatus.DRAFT

            original.save(path)
            loaded = PersonEntry.load(path)

            assert loaded.id == original.id
            assert loaded.role == "operative"
            assert loaded.research_status == ResearchStatus.DRAFT
            assert "John Smith is" in loaded.body


class TestLoadExistingKBs:
    """Tests that load actual entries from existing KBs.

    These tests verify compatibility with existing data formats.
    """

    @pytest.fixture
    def cascade_series_path(self):
        """Path to CascadeSeries if available."""
        path = Path.home() / "CascadeSeries"
        if path.exists():
            return path
        pytest.skip("CascadeSeries not found")

    def test_load_timeline_event(self, cascade_series_path):
        """Test loading an actual timeline event."""
        events_dir = cascade_series_path / "timeline" / "hugo-site" / "content" / "events"
        if not events_dir.exists():
            pytest.skip("Timeline events not found")

        # Find an event file
        event_files = list(events_dir.glob("*.md"))
        if not event_files:
            pytest.skip("No event files found")

        event_file = event_files[0]
        event = EventEntry.load(event_file)

        assert event.id is not None
        assert event.title is not None
        assert event.date is not None

    def test_load_research_actor(self, cascade_series_path):
        """Test loading an actual research actor."""
        actors_dir = cascade_series_path / "research-kb" / "actors"
        if not actors_dir.exists():
            pytest.skip("Research actors not found")

        # Find an actor file
        actor_files = list(actors_dir.glob("*.md"))
        if not actor_files:
            pytest.skip("No actor files found")

        actor_file = actor_files[0]
        actor = PersonEntry.load(actor_file)

        assert actor.id is not None
        assert actor.title is not None
        assert actor.entry_type == "person"


class TestGenericEntryFrontmatterRoundTrip:
    """`GenericEntry` must not duplicate undeclared keys into `metadata:` (#149)."""

    def test_undeclared_keys_are_promoted_not_duplicated(self):
        meta = {
            "id": "design-one",
            "type": "design",
            "title": "A design",
            "status": "draft",
            "author": "someone",
        }

        out = GenericEntry.from_frontmatter(meta, "body").to_frontmatter()

        assert out["status"] == "draft"
        assert out["author"] == "someone"
        assert "metadata" not in out, out

    def test_explicit_metadata_block_stays_nested(self):
        meta = {
            "id": "design-two",
            "type": "design",
            "title": "A design",
            "status": "draft",
            "metadata": {"owner": "team"},
        }

        out = GenericEntry.from_frontmatter(meta, "body").to_frontmatter()

        assert out["status"] == "draft"
        assert out["metadata"] == {"owner": "team"}
        assert "owner" not in out, out

    def test_no_op_load_save_is_stable(self):
        meta = {"id": "design-three", "type": "design", "title": "T", "status": "draft"}

        first = GenericEntry.from_frontmatter(meta, "body").to_frontmatter()
        second = GenericEntry.from_frontmatter(first, "body").to_frontmatter()

        assert first == second
        assert "metadata" not in first

    @pytest.mark.parametrize("value", [None, "owner", ["owner"], 7, True])
    def test_non_mapping_metadata_is_kept_verbatim(self, value):
        """A non-mapping `metadata:` is kept and written back (review of #175).

        On `dev` a null `metadata:` raised out of the merge, so the loader fell
        back to another class and the file was saved back as `type: event`; a
        string/list/number took the same path. Treating the value as empty for
        `self.metadata` must not delete it from the file: it round-trips
        verbatim through `_raw_metadata`.
        """
        meta = {"id": "design-four", "type": "design", "title": "T", "metadata": value}

        entry = GenericEntry.from_frontmatter(meta, "body")
        out = entry.to_frontmatter()

        assert entry.entry_type == "design"
        assert entry.metadata == {}
        assert out["metadata"] == value, out

    def test_metadata_key_colliding_with_a_base_key_is_kept_nested(self):
        """A metadata key that collides with a base key must not vanish (#149).

        Built in memory, `_nested_metadata_keys` is empty, so the promotion path
        used to run for every key and dropped the ones `_base_frontmatter`
        already emitted. `title` here must survive somewhere rather than being
        silently discarded.
        """
        entry = GenericEntry(
            id="s1",
            title="T",
            body="b",
            metadata={"title": "shadow", "status": "draft"},
        )

        out = entry.to_frontmatter()

        assert out["title"] == "T"
        assert out["status"] == "draft"
        assert out["metadata"] == {"title": "shadow"}, out

    def test_empty_metadata_mapping_is_treated_as_empty(self):
        meta = {"id": "design-five", "type": "design", "title": "T", "metadata": {}}

        out = GenericEntry.from_frontmatter(meta, "body").to_frontmatter()

        assert "metadata" not in out, out


class TestParseDatetime:
    """#151: bare YAML dates must not read back as "now", and naive strings
    must carry a timezone so comparisons against ``_utcnow()`` are safe."""

    def test_bare_date_is_anchored_to_midnight_utc(self):
        assert parse_datetime(date(2026, 1, 15)) == datetime(2026, 1, 15, tzinfo=UTC)

    def test_naive_datetime_string_is_anchored_to_utc(self):
        assert parse_datetime("2026-01-15T09:00:00") == datetime(2026, 1, 15, 9, 0, tzinfo=UTC)

    def test_naive_date_string_is_anchored_to_utc(self):
        assert parse_datetime("2026-01-15") == datetime(2026, 1, 15, tzinfo=UTC)

    def test_trailing_z_is_utc(self):
        assert parse_datetime("2026-01-15T09:00:00Z") == datetime(2026, 1, 15, 9, 0, tzinfo=UTC)

    def test_explicit_offset_is_kept(self):
        parsed = parse_datetime("2026-01-15T09:00:00+02:00")
        assert parsed.utcoffset() == timedelta(hours=2)
        assert parsed.hour == 9  # not converted, only anchored when naive

    def test_datetime_passes_through_unchanged(self):
        aware = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
        assert parse_datetime(aware) is aware

    def test_naive_datetime_is_anchored_to_utc(self):
        naive = datetime(2026, 1, 15, 9, 0)
        parsed = parse_datetime(naive)
        assert parsed == datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
        assert parsed.tzinfo is not None

    def test_loader_hands_back_aware_values(self):
        """#171 review: exercise the *loader* path, not just constructed values.

        An unquoted timestamp is not a str by the time it reaches us -- ruamel
        hands back a naive ``TimeStamp`` (or ``datetime``), which is why the
        constructed-value tests alone missed the most common on-disk form.
        """
        from pyrite.models.core_types import get_entry_class

        cls = get_entry_class("note")
        for raw in ("2026-01-15T09:00:00", "2026-01-15 09:00:00"):
            text = f"---\nid: t\ntitle: T\ntype: note\ncreated_at: {raw}\n---\n\nbody\n"
            entry = cls.from_markdown(text)
            assert entry.created_at is not None, raw
            assert entry.created_at.tzinfo is not None, raw
            assert entry.created_at == datetime(2026, 1, 15, 9, 0, tzinfo=UTC), raw

    def test_missing_or_empty_falls_back_to_now_utc(self):
        for value in (None, ""):
            parsed = parse_datetime(value)
            assert parsed.tzinfo is not None
            assert abs((datetime.now(UTC) - parsed).total_seconds()) < 60

    def test_unparseable_falls_back_to_now_utc(self):
        parsed = parse_datetime("not-a-date")
        assert parsed.tzinfo is not None
        assert abs((datetime.now(UTC) - parsed).total_seconds()) < 60


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

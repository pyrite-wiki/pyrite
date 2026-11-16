"""Tests for file_pattern support in TypeSchema and KBRepository."""

import pytest

from pyrite.schema.field_schema import TypeSchema


class TestTypeSchemaFilePattern:
    """Test TypeSchema.resolve_filename with file_pattern."""

    def test_no_pattern_returns_none(self):
        schema = TypeSchema(name="event")
        from pyrite.models.factory import build_entry

        entry = build_entry("event", title="Test Event", body="", date="2026-03-30")
        assert schema.resolve_filename(entry) is None

    def test_date_slug_pattern(self):
        schema = TypeSchema(name="event", file_pattern="{date}--{slug}.md")
        from pyrite.models.factory import build_entry

        entry = build_entry("event", title="ICE Raid Downtown", body="", date="2026-03-30")
        result = schema.resolve_filename(entry)
        assert result == "2026-03-30--ice-raid-downtown.md"

    def test_date_only_pattern(self):
        schema = TypeSchema(name="event", file_pattern="{date}.md")
        from pyrite.models.factory import build_entry

        entry = build_entry("event", title="Something", body="", date="2026-01-15")
        result = schema.resolve_filename(entry)
        assert result == "2026-01-15.md"

    def test_id_pattern(self):
        schema = TypeSchema(name="note", file_pattern="{id}.md")
        from pyrite.models.factory import build_entry

        entry = build_entry("note", entry_id="my-note", title="My Note", body="")
        result = schema.resolve_filename(entry)
        assert result == "my-note.md"

    def test_type_in_pattern(self):
        schema = TypeSchema(name="event", file_pattern="{type}--{date}--{slug}.md")
        from pyrite.models.factory import build_entry

        entry = build_entry("event", title="Test", body="", date="2026-03-30")
        result = schema.resolve_filename(entry)
        assert result == "event--2026-03-30--test.md"

    def test_missing_date_falls_back_to_the_default_filename(self):
        schema = TypeSchema(name="note", file_pattern="{date}--{slug}.md")
        from pyrite.models.factory import build_entry

        entry = build_entry("note", title="No Date Note", body="")
        assert schema.resolve_filename(entry) is None

    def test_date_placeholder_reads_metadata_for_generic_types(self):
        # A kb.yaml-declared type (GenericEntry) keeps `date` in metadata,
        # not on the entry; `{date}` must resolve from there instead of
        # formatting an empty string into the name (#1467).
        schema = TypeSchema(name="timeline_event", file_pattern="{date}--{slug}.md")
        from pyrite.models.factory import build_entry

        entry = build_entry("timeline_event", title="Upgrade Notes", body="", date="2026-11-12")
        result = schema.resolve_filename(entry)
        assert result == "2026-11-12--upgrade-notes.md"

    def test_generic_entry_without_any_date_falls_back_to_the_id_name(self):
        # The other half of #1467: a kb.yaml-only type whose metadata has no
        # `date` either must fall back to the plain `<id>.md` name (the None
        # return), not format an empty `{date}` into `--<slug>.md`.
        schema = TypeSchema(name="timeline_event", file_pattern="{date}--{slug}.md")
        from pyrite.models.factory import build_entry

        entry = build_entry("timeline_event", title="Undated", body="")
        assert schema.resolve_filename(entry) is None

    def test_to_dict_includes_file_pattern(self):
        schema = TypeSchema(name="event", file_pattern="{date}--{slug}.md")
        d = schema.to_dict()
        assert d["file_pattern"] == "{date}--{slug}.md"

    def test_to_dict_omits_empty_file_pattern(self):
        schema = TypeSchema(name="event")
        d = schema.to_dict()
        assert "file_pattern" not in d


class TestKBSchemaLoadsFilePattern:
    """Test that file_pattern is loaded from kb.yaml-style dicts."""

    def test_loads_file_pattern(self):
        from pyrite.schema.kb_schema import KBSchema

        schema = KBSchema.from_dict(
            {
                "name": "test",
                "types": {
                    "event": {
                        "description": "An event",
                        "file_pattern": "{date}--{slug}.md",
                    }
                },
            }
        )
        ts = schema.get_type_schema("event")
        assert ts is not None
        assert ts.file_pattern == "{date}--{slug}.md"


class TestResolveFilenameEntryFieldPlaceholders:
    """#391: `resolve_filename` gains the entry's OWN fields as placeholders
    (not just the fixed id/slug/date/title/type set), with Python format
    specs, e.g. ``{adr_number:04d}-{title}.md`` -- what the software-kb
    `adr` type declares to keep its ``NNNN-slug.md`` convention through
    `KBService.create`.
    """

    def _adr_entry(self, **kwargs):
        from pyrite_software_kb.entry_types import ADREntry

        defaults = {"id": "adr-0007", "title": "Use PostgreSQL"}
        defaults.update(kwargs)
        return ADREntry(**defaults)

    def test_entry_field_placeholder_with_format_spec(self):
        schema = TypeSchema(name="adr", file_pattern="{adr_number:04d}-{title}.md")
        entry = self._adr_entry(adr_number=7)
        assert schema.resolve_filename(entry) == "0007-use-postgresql.md"

    def test_entry_field_placeholder_without_format_spec(self):
        schema = TypeSchema(name="adr", file_pattern="adr-{adr_number}.md")
        entry = self._adr_entry(adr_number=7)
        assert schema.resolve_filename(entry) == "adr-7.md"

    @pytest.mark.control(
        reason="dev's pre-#391 resolve_filename already returned None for an "
        "unknown placeholder (a bare `except (KeyError, IndexError): return "
        "None` around `.format()`, since {does_not_exist} was never in its "
        "fixed replacements dict) -- same externally observable result, "
        "reached deliberately now instead of by accident. Pins the recorded "
        "#391 acceptance (fall back, don't refuse), not a change from dev."
    )
    def test_missing_field_falls_back_to_the_default_filename(self):
        """A placeholder naming a field the entry does not have falls back
        to the default filename (returns None) -- the recorded #391
        acceptance -- rather than refusing the whole create."""
        schema = TypeSchema(name="adr", file_pattern="{does_not_exist:04d}-{title}.md")
        entry = self._adr_entry(adr_number=7)
        assert schema.resolve_filename(entry) is None

    @pytest.mark.control(
        reason="same as test_missing_field_falls_back_to_the_default_filename: "
        "{superseded_by} was not in dev's fixed placeholder set either, so "
        "dev already returned None here regardless of the field's value"
    )
    def test_none_value_falls_back_to_the_default_filename(self):
        schema = TypeSchema(name="adr", file_pattern="{superseded_by}.md")
        entry = self._adr_entry(superseded_by=None)
        assert schema.resolve_filename(entry) is None

    @pytest.mark.control(reason="same as test_missing_field_falls_back_to_the_default_filename")
    def test_empty_string_value_falls_back_to_the_default_filename(self):
        schema = TypeSchema(name="adr", file_pattern="{superseded_by}.md")
        entry = self._adr_entry(superseded_by="   ")
        assert schema.resolve_filename(entry) is None

    @pytest.mark.control(reason="same as test_missing_field_falls_back_to_the_default_filename")
    def test_leading_dot_value_falls_back_to_the_default_filename(self):
        """A value starting with `.` (e.g. a raw, unslugified traversal
        attempt like `../../etc/passwd`) is treated as missing -- it falls
        back to the safe default filename instead of being substituted in."""
        schema = TypeSchema(name="adr", file_pattern="{superseded_by}.md")
        entry = self._adr_entry(superseded_by="../../etc/passwd")
        assert schema.resolve_filename(entry) is None

    def test_traversal_attempt_not_starting_with_dot_is_refused(self):
        """A field value containing a path separator that does NOT start
        with `.` (so it is not caught by the missing-value fallback above)
        must still be refused -- it would let entry data escape the type's
        folder. `superseded_by` is a plain free-text field on ADREntry,
        unlike `{title}`/`{slug}`, which are already slugified."""
        from pyrite.exceptions import ValidationError

        schema = TypeSchema(name="adr", file_pattern="{superseded_by}.md")
        entry = self._adr_entry(superseded_by="sub/escape")
        with pytest.raises(ValidationError, match="separator"):
            schema.resolve_filename(entry)

    def test_a_resolved_filename_of_exactly_dotdot_is_refused(self):
        """`{slug}` is `entry.id` verbatim (not slugified again). A pattern
        with no literal suffix at all can resolve to exactly `..` -- refused
        as a `..` path component, not by the separator check (there is no
        separator here)."""
        from pyrite.exceptions import ValidationError

        schema = TypeSchema(name="adr", file_pattern="{slug}")
        entry = self._adr_entry(id="..")
        with pytest.raises(ValidationError, match="path component"):
            schema.resolve_filename(entry)

    def test_a_real_filename_containing_dotdot_is_not_refused(self):
        """#391 cold read item 5: the traversal check must look at PATH
        COMPONENTS, not do a substring match -- `v1..2` is a legitimate
        filename fragment and must not be refused."""
        schema = TypeSchema(name="adr", file_pattern="{superseded_by}.md")
        entry = self._adr_entry(superseded_by="v1..2")
        assert schema.resolve_filename(entry) == "v1..2.md"

    def test_format_spec_mismatch_is_a_validation_error(self):
        """#391 cold read item 4: a format-spec mismatch (a non-empty string
        under `:04d`) must become a ValidationError, not a raw ValueError/
        TypeError bubbling out of `str.format`."""
        from pyrite.exceptions import ValidationError

        schema = TypeSchema(name="adr", file_pattern="{superseded_by:04d}.md")
        entry = self._adr_entry(superseded_by="not-a-number")
        with pytest.raises(ValidationError):
            schema.resolve_filename(entry)

    def test_positional_placeholder_is_refused(self):
        """A positional `{0}` placeholder is refused with a ValidationError,
        not a raw IndexError/KeyError from `str.format`."""
        from pyrite.exceptions import ValidationError

        schema = TypeSchema(name="adr", file_pattern="{0}.md")
        entry = self._adr_entry()
        with pytest.raises(ValidationError):
            schema.resolve_filename(entry)

    def test_auto_numbering_placeholder_is_refused(self):
        from pyrite.exceptions import ValidationError

        schema = TypeSchema(name="adr", file_pattern="{}.md")
        entry = self._adr_entry()
        with pytest.raises(ValidationError):
            schema.resolve_filename(entry)

    def test_attribute_access_placeholder_is_refused(self):
        """#391 cold read item 4: `{title.upper}` currently puts a repr in
        the filename instead of being refused -- only a plain field name is
        a valid placeholder, no attribute access."""
        from pyrite.exceptions import ValidationError

        schema = TypeSchema(name="adr", file_pattern="{title.upper}.md")
        entry = self._adr_entry()
        with pytest.raises(ValidationError):
            schema.resolve_filename(entry)

    @pytest.mark.control(
        reason="the refusal is unchanged; the fixture's method moved from the deleted Entry.save to Entry.load (#955 P4)"
    )
    def test_non_data_attribute_placeholder_is_refused(self):
        """#391 cold read item 4: `{load}` (a method, not a data field)
        currently puts a bound-method repr in the filename -- refused.
        (It was `{save}` until #955 B6 P4 deleted `Entry.save`.)"""
        from pyrite.exceptions import ValidationError

        schema = TypeSchema(name="adr", file_pattern="{load}.md")
        entry = self._adr_entry()
        with pytest.raises(ValidationError):
            schema.resolve_filename(entry)

    def test_nul_byte_in_resolved_filename_is_refused(self):
        from pyrite.exceptions import ValidationError

        schema = TypeSchema(name="adr", file_pattern="{superseded_by}.md")
        entry = self._adr_entry(superseded_by="a\x00b")
        with pytest.raises(ValidationError):
            schema.resolve_filename(entry)

    def test_over_long_resolved_filename_is_refused(self):
        from pyrite.exceptions import ValidationError

        schema = TypeSchema(name="adr", file_pattern="{superseded_by}.md")
        entry = self._adr_entry(superseded_by="x" * 300)
        with pytest.raises(ValidationError):
            schema.resolve_filename(entry)

    @pytest.mark.control(
        reason="the fixed id/slug/date/title/type placeholders already resolved "
        "before this change; pins they still do, not the new entry-field support"
    )
    def test_existing_placeholders_keep_working(self):
        """id/slug/date/title/type must still resolve after adding entry-field
        placeholder support -- this is a regression guard, not new behaviour."""
        from pyrite.models.factory import build_entry

        schema = TypeSchema(name="event", file_pattern="{date}--{slug}.md")
        entry = build_entry("event", title="ICE Raid Downtown", body="", date="2026-03-30")
        assert schema.resolve_filename(entry) == "2026-03-30--ice-raid-downtown.md"


class TestCreateEntryMedium:
    """The #1467 repro end to end through `KBService.create_entry`, the
    pipeline `pyrite create -f` and MCP `kb_create` share: a kb.yaml-declared
    `timeline_event` with `file_pattern: "{date}--{slug}.md"` must land as
    `2026-11-12--test-d.md`, not `--test-d.md`."""

    KB_YAML = """\
name: tl
types:
  timeline_event:
    subdirectory: ""
    file_pattern: "{date}--{slug}.md"
    required: [title, date]
    fields:
      date: {type: date}
"""

    def _make_kb(self, tmp_path):
        from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
        from pyrite.storage.database import PyriteDB

        kb_path = tmp_path / "tl"
        kb_path.mkdir()
        (kb_path / "kb.yaml").write_text(self.KB_YAML, encoding="utf-8")
        db_path = tmp_path / "index.db"
        PyriteDB(db_path).close()
        kb_config = KBConfig(name="tl", path=kb_path, kb_type=KBType.GENERIC)
        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=db_path, auto_embed=False),
        )
        return config, kb_path

    def test_create_writes_the_dated_name(self, tmp_path):
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB

        config, kb_path = self._make_kb(tmp_path)
        db = PyriteDB(config.settings.index_path)
        svc = KBService(config, db)
        try:
            svc.create_entry("tl", "test-d", "Test D", "timeline_event", "", date="2026-11-12")
        finally:
            db.close()
        assert (kb_path / "2026-11-12--test-d.md").exists(), sorted(
            p.name for p in kb_path.iterdir()
        )
        assert not (kb_path / "--test-d.md").exists(), "the empty-date name must not exist"

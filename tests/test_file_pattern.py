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
        reason="the refusal is unchanged; the fixture's method moved from the deleted Entry.save "
        "and Entry.load to Entry.add_link (#955 P4)"
    )
    def test_non_data_attribute_placeholder_is_refused(self):
        """#391 cold read item 4: `{add_link}` (a method, not a data field)
        currently puts a bound-method repr in the filename -- refused.
        (It was `{save}` until #955 B6 P4 deleted `Entry.save`, then `{load}`
        until `Entry.load`, a raw reader of any path, was deleted.)"""
        from pyrite.exceptions import ValidationError

        schema = TypeSchema(name="adr", file_pattern="{add_link}.md")
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

    def test_create_refuses_private_field_placeholder(self, tmp_path):
        """#1639: an entry write under a pattern naming a private field raises
        ValidationError (VALIDATION_FAILED)."""
        from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
        from pyrite.exceptions import ValidationError
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB

        kb_yaml = """\
name: tl
types:
  timeline_event:
    subdirectory: ""
    file_pattern: "{_schema_version}.md"
"""
        kb_path = tmp_path / "tl_priv"
        kb_path.mkdir()
        (kb_path / "kb.yaml").write_text(kb_yaml, encoding="utf-8")
        db_path = tmp_path / "index_priv.db"
        PyriteDB(db_path).close()

        kb_config = KBConfig(name="tl", path=kb_path, kb_type=KBType.GENERIC)
        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=db_path, auto_embed=False),
        )
        db = PyriteDB(config.settings.index_path)
        svc = KBService(config, db)
        try:
            with pytest.raises(ValidationError) as exc_info:
                svc.create_entry("tl", "test-priv", "Test Priv", "timeline_event", "")
            assert exc_info.value.error_code == "VALIDATION_FAILED"
            assert "private field" in str(exc_info.value)
        finally:
            db.close()


def _collect_entry_private_field_pairs():
    import dataclasses

    from pyrite.models.base import Entry
    import pyrite.models.core_types  # noqa: F401
    import pyrite.models.generic  # noqa: F401
    import pyrite.models.task  # noqa: F401

    try:
        import pyrite_software_kb.entry_types  # noqa: F401
    except ImportError:
        pass

    def _subclasses(c):
        subs = set(c.__subclasses__())
        for s in list(subs):
            subs.update(_subclasses(s))
        return subs

    all_classes = {Entry} | _subclasses(Entry)
    pairs = []
    for cls in sorted(all_classes, key=lambda c: c.__name__):
        if not dataclasses.is_dataclass(cls):
            continue
        for f in dataclasses.fields(cls):
            if f.name.startswith("_"):
                pairs.append((cls, f.name))
    return pairs


def _build_test_entry(cls):
    import dataclasses
    import inspect

    target_cls = cls
    if inspect.isabstract(cls):

        class Concrete(cls):
            @property
            def entry_type(self):
                return "entry"

            @classmethod
            def from_frontmatter(cls, *a, **k):
                pass

            def to_frontmatter(self):
                return ""

        Concrete.__name__ = f"Concrete{cls.__name__}"
        target_cls = Concrete

    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:
            continue
        if f.name == "id":
            kwargs["id"] = "test-id"
        elif f.name == "title":
            kwargs["title"] = "Test Title"
        elif f.name == "_entry_type":
            kwargs["_entry_type"] = "note"
        elif f.name == "entry_type":
            kwargs["entry_type"] = "note"
        else:
            kwargs[f.name] = "dummy"
    return target_cls(**kwargs)


_ENTRY_PRIVATE_FIELD_PAIRS = _collect_entry_private_field_pairs()
assert len(_ENTRY_PRIVATE_FIELD_PAIRS) > 0, "collected entry private field pairs must not be empty"


@pytest.mark.parametrize(
    ("cls", "field_name"),
    _ENTRY_PRIVATE_FIELD_PAIRS,
    ids=[f"{cls.__name__}-{field_name}" for cls, field_name in _ENTRY_PRIVATE_FIELD_PAIRS],
)
def test_private_field_placeholder_is_refused(cls, field_name):
    """#1639: placeholders naming private fields of the entry (e.g.
    _schema_version, _create_fields, _entry_type) must be refused with
    ValidationError rather than resolved into file names."""
    from pyrite.exceptions import ValidationError

    inst = _build_test_entry(cls)
    schema = TypeSchema(name="t", file_pattern=f"{{{field_name}}}.md")
    with pytest.raises(ValidationError, match="which is a private field of entry"):
        schema.resolve_filename(inst)


@pytest.mark.control(reason="public data fields continue to resolve into filenames")
def test_public_field_placeholder_still_resolves():
    from pyrite_software_kb.entry_types import ADREntry

    entry = ADREntry(id="adr-0007", title="Use PostgreSQL", adr_number=7)
    schema = TypeSchema(name="adr", file_pattern="adr-{adr_number}.md")
    assert schema.resolve_filename(entry) == "adr-7.md"


@pytest.mark.control(
    reason="kb.yaml-declared types resolve _-prefixed metadata keys like _draft from metadata"
)
def test_private_prefixed_metadata_key_on_generic_entry_still_resolves():
    from pyrite.models.generic import GenericEntry

    generic = GenericEntry(
        id="draft-1",
        title="Draft Note",
        _entry_type="note",
        metadata={"_draft": "true"},
    )
    schema = TypeSchema(name="note", file_pattern="{_draft}--{slug}.md")
    assert schema.resolve_filename(generic) == "true--draft-1.md"


@pytest.mark.control(
    reason="absent _-prefixed names fall back to default filename rather than refusing"
)
def test_absent_private_prefixed_placeholder_falls_back_to_none():
    from pyrite_software_kb.entry_types import ADREntry

    entry = ADREntry(id="adr-0007", title="Use PostgreSQL")
    schema = TypeSchema(name="adr", file_pattern="{_absent_name}.md")
    assert schema.resolve_filename(entry) is None


@pytest.mark.control(reason="{date}--{slug}.md pattern resolution is unchanged")
def test_date_slug_pattern_is_unchanged():
    from pyrite.models.factory import build_entry

    event = build_entry("event", title="ICE Raid Downtown", body="", date="2026-03-30")
    schema = TypeSchema(name="event", file_pattern="{date}--{slug}.md")
    assert schema.resolve_filename(event) == "2026-03-30--ice-raid-downtown.md"

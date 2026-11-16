"""Import-contract tests for pyrite.schema package.

Verifies that all public symbols are importable from pyrite.schema
after the module-to-package refactor.
"""


def test_all_public_symbols_importable():
    """All 17+ public symbols should be importable from pyrite.schema."""
    from pyrite.schema import (
        CORE_TYPE_METADATA,
        CORE_TYPES,
        RELATIONSHIP_TYPES,
        EventStatus,
        FieldSchema,
        KBSchema,
        Link,
        Provenance,
        ResearchStatus,
        Source,
        TypeSchema,
        VerificationStatus,
        _validate_field_value,
        generate_entry_id,
        generate_event_id,
        get_all_relationship_types,
        get_inverse_relation,
        resolve_type_metadata,
        validate_date,
        validate_event_id,
        validate_importance,
    )

    # Verify they are not None
    assert CORE_TYPES is not None
    assert CORE_TYPE_METADATA is not None
    assert RELATIONSHIP_TYPES is not None
    assert KBSchema is not None
    assert Link is not None
    assert Source is not None
    assert Provenance is not None
    assert FieldSchema is not None
    assert TypeSchema is not None
    assert VerificationStatus is not None
    assert EventStatus is not None
    assert ResearchStatus is not None
    assert validate_date is not None
    assert validate_importance is not None
    assert validate_event_id is not None
    assert generate_event_id is not None
    assert generate_entry_id is not None
    assert get_all_relationship_types is not None
    assert get_inverse_relation is not None
    assert resolve_type_metadata is not None
    assert _validate_field_value is not None


def test_generate_entry_id():
    """generate_entry_id should slugify a title."""
    from pyrite.schema import generate_entry_id

    assert generate_entry_id("Hello World") == "hello-world"
    assert generate_entry_id("Some  Complex!! Title") == "some-complex-title"


def test_validate_date():
    """validate_date should accept valid YYYY-MM-DD dates."""
    from pyrite.schema import validate_date

    assert validate_date("2024-01-15") is True
    assert validate_date("not-a-date") is False
    assert validate_date("") is False


def test_verification_status_enum():
    """VerificationStatus enum values should match expected strings."""
    from pyrite.schema import VerificationStatus

    assert VerificationStatus.VERIFIED == "verified"
    assert VerificationStatus.UNVERIFIED == "unverified"
    assert VerificationStatus.DISPUTED == "disputed"


def test_event_status_enum():
    """EventStatus enum values should match expected strings."""
    from pyrite.schema import EventStatus

    assert EventStatus.CONFIRMED == "confirmed"
    assert EventStatus.ALLEGED == "alleged"


def test_research_status_enum():
    """ResearchStatus enum values should match expected strings."""
    from pyrite.schema import ResearchStatus

    assert ResearchStatus.STUB == "stub"
    assert ResearchStatus.PUBLISHED == "published"


def test_core_types_dict():
    """CORE_TYPES should contain expected entry types."""
    from pyrite.schema import CORE_TYPES

    assert "note" in CORE_TYPES
    assert "person" in CORE_TYPES
    assert "event" in CORE_TYPES
    assert "organization" in CORE_TYPES
    assert "document" in CORE_TYPES


def test_relationship_types_dict():
    """RELATIONSHIP_TYPES should contain expected relationship types."""
    from pyrite.schema import RELATIONSHIP_TYPES

    assert "owns" in RELATIONSHIP_TYPES
    assert RELATIONSHIP_TYPES["owns"]["inverse"] == "owned_by"


def test_link_from_dict():
    """Link.from_dict should parse a link dictionary."""
    from pyrite.schema import Link

    link = Link.from_dict({"target": "entry-1", "relation": "supports"})
    assert link.target == "entry-1"
    assert link.relation == "supports"


def test_source_round_trip():
    """Source should round-trip through to_dict/from_dict."""
    from pyrite.schema import Source

    src = Source(title="Test", url="https://example.com")
    data = src.to_dict()
    restored = Source.from_dict(data)
    assert restored.title == "Test"
    assert restored.url == "https://example.com"


def test_provenance_from_dict_parses_mapping():
    """Provenance.from_dict should read fields from a mapping."""
    from pyrite.schema import Provenance

    prov = Provenance.from_dict({"created_by": "mark", "agent_confidence": 0.7})
    assert prov.created_by == "mark"
    assert prov.agent_confidence == 0.7


def test_provenance_from_dict_tolerates_non_mapping():
    """Malformed provenance (a bare string/list) should not crash the loader.

    Some hand-authored frontmatter writes ``provenance:`` as free text rather
    than a mapping. from_dict must degrade to a default Provenance instead of
    raising AttributeError, otherwise the entry loader crashes on real KBs.
    """
    from pyrite.schema import Provenance

    for bad in ("free text note", ["a", "b"], 42, None):
        prov = Provenance.from_dict(bad)
        assert isinstance(prov, Provenance)
        assert prov.created_by == ""


def test_dunder_all_completeness():
    """__all__ should list all expected public symbols."""
    import pyrite.schema as schema_mod

    expected = {
        "CORE_TYPE_METADATA",
        "CORE_TYPES",
        "EndpointSpec",
        "EventStatus",
        "FieldSchema",
        "KBSchema",
        "Link",
        "Provenance",
        "RELATIONSHIP_TYPES",
        "RESERVED_FIELD_NAMES",
        "ResearchStatus",
        "Source",
        "TypeSchema",
        "VerificationStatus",
        "_validate_field_value",
        "expand_subdirectory_template",
        "filter_updated_since",
        "generate_entry_id",
        "generate_event_id",
        "get_all_relationship_types",
        "get_inverse_relation",
        "parse_since",
        "resolve_type_metadata",
        "validate_date",
        "validate_event_id",
        "validate_importance",
    }
    assert expected == set(schema_mod.__all__)
